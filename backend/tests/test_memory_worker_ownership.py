# -*- coding: utf-8 -*-
"""提取工人归属与账号门禁的合成库测试。

所有用例均使用 _dbclone 克隆的独立临时 SQLite 库与本用例独占的队列文件，不连
生产库、不读/写生产队列、不 mock 掉新增的授权判据（_load_identity /
_identity_disposition / _source_belongs_to_session 均走真实数据库）。

覆盖：
- 主链路 add_chat_memory_extraction / process_session：禁用/已删除账号不提取；
- 错调用方传入的 user_id/character_id 不覆盖会话真实值；
- 缺源 / AI 源 / 异会话源不调模型、不写 ProcessedExtraction、明确入死；
- 家庭共享角色的合法 session 不强制 char.user_id == session.user_id；
- 临时禁用后恢复 active 可继续提取；
- catchup_extract_all 不绕过账号门禁（跳过禁用/已删除账号的会话）。
"""
import asyncio
import sqlite3
from datetime import datetime

from app.memory import extract_queue as q
from app.memory import extraction_worker as worker
from app.utils.timeutil import now_naive_utc


def task_rows() -> list[dict]:
    with sqlite3.connect(q.database_path()) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM extraction_tasks ORDER BY id")]


def _setup(tmp_path, monkeypatch):
    """建独立临时库 + 独占队列，拦截 extractor DB 工厂与节流标记，返回 (engine, factory)。"""
    from _dbclone import clone_engine, make_session_factory
    from app.memory import extractor as ex

    engine = clone_engine(tmp_path / "ownership.db")
    factory = make_session_factory(engine)
    monkeypatch.setattr(q, "_QUEUE_FILE", tmp_path / "queue.json")
    monkeypatch.setattr(ex, "async_session_factory", factory)
    monkeypatch.setattr(ex, "_mark_batch_taken", lambda *args: None)
    return engine, factory


async def _seed(factory, users, chars, sessions, messages):
    from app.models.user import User
    from app.models.character import AICharacter
    from app.models.chat import ChatSession, ChatMessage

    async with factory() as db:
        for u in users:
            db.add(User(**u))
        await db.flush()
        for c in chars:
            db.add(AICharacter(**c))
        await db.flush()
        for s in sessions:
            db.add(ChatSession(**s))
        await db.flush()
        for m in messages:
            db.add(ChatMessage(**m))
        await db.commit()


def _make_runner(monkeypatch, ex):
    """把 run_internal 换成记账假件；提取成功后按真实行为写入 ProcessedExtraction 标记。"""
    calls: list[dict] = []

    async def run(tool, payload, **kwargs):
        calls.append(payload)
        await ex._mark_processed(payload["source_id"])
        return {"status": "ok"}

    monkeypatch.setattr("app.agent.internal_runner.run_internal", run)
    return calls


# ── 主链路归属 ──────────────────────────────────────────────────────────

def test_主链路_临时禁用账号不提取且保留载荷(monkeypatch, tmp_path):
    """disabled_at 账号：不调模型、不写标记、队列载荷保留 pending（可恢复）。"""
    engine, factory = _setup(tmp_path, monkeypatch)
    from app.memory import extractor as ex
    calls = _make_runner(monkeypatch, ex)
    try:
        asyncio.run(_seed(factory,
            users=[{"id": 7101, "username": "own_disable", "nickname": "禁用用户", "disabled_at": datetime(2026, 1, 1)}],
            chars=[{"id": 7201, "user_id": 7101, "name": "禁用角色", "is_active": True}],
            sessions=[{"id": 7301, "user_id": 7101, "character_id": 7201, "is_active": True}],
            messages=[{"id": 7401, "session_id": 7301, "sender_type": "user", "content": "禁用账号的消息"}]))
        q.add(7301, {"source_id": 7401, "ts": 0, "user_message": "禁用账号的消息", "ai_response": "回复"})
        asyncio.run(worker.drain_due())
        assert calls == [], "禁用账号不得提取"
        assert q.peek(7301) == [{"source_id": 7401, "ts": 0, "user_message": "禁用账号的消息", "ai_response": "回复"}], "载荷保留 pending，不入死队"
        assert task_rows()[0]["state"] == "pending", "临时禁用保留状态，不判成功"
    finally:
        asyncio.run(engine.dispose())


def test_主链路_已删除账号明确入死不判成功(monkeypatch, tmp_path):
    """deleted_at 账号：明确入死，不吞成功、不写 ProcessedExtraction、不清生产数据。"""
    engine, factory = _setup(tmp_path, monkeypatch)
    from app.memory import extractor as ex
    calls = _make_runner(monkeypatch, ex)
    try:
        asyncio.run(_seed(factory,
            users=[{"id": 7102, "username": "own_deleted", "nickname": "已删用户", "deleted_at": datetime(2026, 1, 1)}],
            chars=[{"id": 7202, "user_id": 7102, "name": "已删角色", "is_active": True}],
            sessions=[{"id": 7302, "user_id": 7102, "character_id": 7202, "is_active": True}],
            messages=[{"id": 7402, "session_id": 7302, "sender_type": "user", "content": "已删账号的消息"}]))
        q.add(7302, {"source_id": 7402, "ts": 0, "user_message": "已删账号的消息", "ai_response": "回复"})
        asyncio.run(worker.drain_due())
        assert calls == [], "已删除账号不得提取"
        assert task_rows()[0]["state"] == "dead", "错源/已删除应明确入死"
        assert task_rows()[0]["last_error"] == "deleted_user"
    finally:
        asyncio.run(engine.dispose())


def test_主链路_错调用方参数以会话真实值为准(monkeypatch, tmp_path):
    """process_session 调用参数 user_id/character_id 须匹配会话真实值，以数据库行为准。"""
    engine, factory = _setup(tmp_path, monkeypatch)
    from app.memory import extractor as ex
    calls = _make_runner(monkeypatch, ex)
    try:
        asyncio.run(_seed(factory,
            users=[{"id": 7103, "username": "own_real", "nickname": "真实用户"}],
            chars=[{"id": 7203, "user_id": 7103, "name": "真实角色"}],
            sessions=[{"id": 7303, "user_id": 7103, "character_id": 7203}],
            messages=[{"id": 7403, "session_id": 7303, "sender_type": "user", "content": "真实会话的消息"}]))
        q.add(7303, {"source_id": 7403, "ts": 0, "user_message": "真实会话的消息", "ai_response": "回复"})
        # 故意传错 caller 字段，期望工人仍以会话数据库真实值调用模型。
        asyncio.run(worker.process_session(7303, 9999, 8888))
        assert len(calls) == 1
        assert calls[0]["user_id"] == 7103, "user_id 必须取自会话真实值"
        assert calls[0]["character_id"] == 7203, "character_id 必须取自会话真实值"
        assert calls[0]["session_id"] == 7303
        assert task_rows()[0]["state"] == "succeeded"
    finally:
        asyncio.run(engine.dispose())


def test_缺源不调模型_明确入死(monkeypatch, tmp_path):
    """source_id 在数据库中不存在：不调模型、不写标记、入死。"""
    engine, factory = _setup(tmp_path, monkeypatch)
    from app.memory import extractor as ex
    calls = _make_runner(monkeypatch, ex)
    try:
        asyncio.run(_seed(factory,
            users=[{"id": 7104, "username": "own_miss", "nickname": "缺源用户"}],
            chars=[{"id": 7204, "user_id": 7104, "name": "缺源角色"}],
            sessions=[{"id": 7304, "user_id": 7104, "character_id": 7204}],
            messages=[{"id": 7404, "session_id": 7304, "sender_type": "user", "content": "正常消息"}]))
        q.add(7304, {"source_id": 999999, "ts": 0, "user_message": "缺源消息", "ai_response": "回复"})
        asyncio.run(worker.drain_due())
        assert calls == [], "缺源不得调模型"
        assert task_rows()[0]["state"] == "dead"
        assert task_rows()[0]["last_error"] == "invalid_source"
    finally:
        asyncio.run(engine.dispose())


def test_AI_源不调模型_明确入死(monkeypatch, tmp_path):
    """source_id 指向 AI 消息：不属于本会话 user 消息，不得提取，入死。"""
    engine, factory = _setup(tmp_path, monkeypatch)
    from app.memory import extractor as ex
    calls = _make_runner(monkeypatch, ex)
    try:
        asyncio.run(_seed(factory,
            users=[{"id": 7105, "username": "own_ai", "nickname": "AI源用户"}],
            chars=[{"id": 7205, "user_id": 7105, "name": "AI源角色"}],
            sessions=[{"id": 7305, "user_id": 7105, "character_id": 7205}],
            messages=[
                {"id": 7405, "session_id": 7305, "sender_type": "user", "content": "用户消息"},
                {"id": 7505, "session_id": 7305, "sender_type": "ai", "content": "AI 消息作为错误源"},
            ]))
        q.add(7305, {"source_id": 7505, "ts": 0, "user_message": "AI 源消息", "ai_response": "回复"})
        asyncio.run(worker.drain_due())
        assert calls == [], "AI 源不得调模型"
        assert task_rows()[0]["state"] == "dead"
        assert task_rows()[0]["last_error"] == "invalid_source"
    finally:
        asyncio.run(engine.dispose())


def test_异会话源不调模型_明确入死(monkeypatch, tmp_path):
    """source_id 属于另一个会话的 user 消息：不得提取，入死。"""
    engine, factory = _setup(tmp_path, monkeypatch)
    from app.memory import extractor as ex
    calls = _make_runner(monkeypatch, ex)
    try:
        asyncio.run(_seed(factory,
            users=[{"id": 7106, "username": "own_a", "nickname": "用户A"}, {"id": 7107, "username": "own_b", "nickname": "用户B"}],
            chars=[{"id": 7206, "user_id": 7106, "name": "角色A"}, {"id": 7207, "user_id": 7107, "name": "角色B"}],
            sessions=[{"id": 7306, "user_id": 7106, "character_id": 7206}, {"id": 7307, "user_id": 7107, "character_id": 7207}],
            messages=[{"id": 7406, "session_id": 7307, "sender_type": "user", "content": "B 会话的消息"}]))
        q.add(7306, {"source_id": 7406, "ts": 0, "user_message": "异会话源", "ai_response": "回复"})
        asyncio.run(worker.drain_due())
        assert calls == [], "异会话源不得调模型"
        assert task_rows()[0]["state"] == "dead"
        assert task_rows()[0]["last_error"] == "invalid_source"
    finally:
        asyncio.run(engine.dispose())


def test_家庭共享角色会话可提取_不强制角色归属者等于会话持有者(monkeypatch, tmp_path):
    """家庭共享：子账号持有的会话绑定主账号拥有的角色，不应因 char.user_id != session.user_id 拒绝。"""
    engine, factory = _setup(tmp_path, monkeypatch)
    from app.memory import extractor as ex
    calls = _make_runner(monkeypatch, ex)
    try:
        asyncio.run(_seed(factory,
            users=[
                {"id": 7108, "username": "family_root", "nickname": "主账号"},
                {"id": 7109, "username": "family_sub", "nickname": "子账号", "parent_id": 7108},
            ],
            chars=[{"id": 7208, "user_id": 7108, "name": "主账号角色", "is_active": True}],
            sessions=[{"id": 7308, "user_id": 7109, "character_id": 7208, "is_active": True}],
            messages=[{"id": 7408, "session_id": 7308, "sender_type": "user", "content": "家庭共享会话消息"}]))
        q.add(7308, {"source_id": 7408, "ts": 0, "user_message": "家庭共享会话消息", "ai_response": "回复"})
        asyncio.run(worker.drain_due())
        assert len(calls) == 1
        assert calls[0]["user_id"] == 7109
        assert calls[0]["character_id"] == 7208
        assert task_rows()[0]["state"] == "succeeded"
    finally:
        asyncio.run(engine.dispose())


def test_账号恢复_active后可继续提取(monkeypatch, tmp_path):
    """临时禁用后恢复 active（disabled_at 清空），之前保留的载荷可正常继续提取。"""
    engine, factory = _setup(tmp_path, monkeypatch)
    from app.memory import extractor as ex
    from app.models.user import User
    calls = _make_runner(monkeypatch, ex)
    try:
        asyncio.run(_seed(factory,
            users=[{"id": 7110, "username": "own_recover", "nickname": "恢复用户", "disabled_at": datetime(2026, 1, 1)}],
            chars=[{"id": 7210, "user_id": 7110, "name": "恢复角色", "is_active": True}],
            sessions=[{"id": 7310, "user_id": 7110, "character_id": 7210, "is_active": True}],
            messages=[{"id": 7410, "session_id": 7310, "sender_type": "user", "content": "恢复前的消息"}]))
        q.add(7310, {"source_id": 7410, "ts": 0, "user_message": "恢复前的消息", "ai_response": "回复"})

        asyncio.run(worker.drain_due())
        assert calls == [], "禁用期间不提取，载荷保留"
        assert q.peek(7310) != [], "禁用期间队列仍为 pending"

        # 恢复：清空 disabled_at。
        async def _reactivate():
            async with factory() as db:
                user = await db.get(User, 7110)
                user.disabled_at = None
                await db.commit()
        asyncio.run(_reactivate())

        asyncio.run(worker.drain_due())
        assert len(calls) == 1, "恢复后应继续提取"
        assert calls[0]["user_id"] == 7110
        assert task_rows()[0]["state"] == "succeeded"
    finally:
        asyncio.run(engine.dispose())


# ── catchup 旁路门禁 ────────────────────────────────────────────────────

def test_catchup_跳过禁用与已删除账号的会话(monkeypatch, tmp_path):
    """catchup_extract_all 不得为 disabled_at / deleted_at 账号的会话调用 extract_single。"""
    from app.memory import extractor as ex
    from app.scheduling import periodic_state as pst
    engine, factory = _setup(tmp_path, monkeypatch)
    # 节流台账必须落进本用例临时目录，绝不写生产 data/periodic_state.json。
    monkeypatch.setattr(pst, "_STATE_FILE", tmp_path / "periodic_state.json")
    for name in ("_LOCAL_COUNTERS", "_LOCAL_MARKS", "_LOCAL_STAMPS", "_LOCAL_RETRY_AT"):
        monkeypatch.setattr(pst, name, {})
    now = now_naive_utc()
    try:
        asyncio.run(_seed(factory,
            users=[
                {"id": 7121, "username": "catch_active", "nickname": "活跃用户"},
                {"id": 7122, "username": "catch_disable", "nickname": "禁用用户", "disabled_at": datetime(2026, 1, 1)},
                {"id": 7123, "username": "catch_deleted", "nickname": "已删用户", "deleted_at": datetime(2026, 1, 1)},
            ],
            chars=[
                {"id": 7221, "user_id": 7121, "name": "活跃角色", "is_active": True},
                {"id": 7222, "user_id": 7122, "name": "禁用角色", "is_active": True},
                {"id": 7223, "user_id": 7123, "name": "已删角色", "is_active": True},
            ],
            sessions=[
                {"id": 7321, "user_id": 7121, "character_id": 7221, "is_active": True},
                {"id": 7322, "user_id": 7122, "character_id": 7222, "is_active": True},
                {"id": 7323, "user_id": 7123, "character_id": 7223, "is_active": True},
            ],
            messages=[
                {"id": 7421, "session_id": 7321, "sender_type": "user", "content": "活跃用户消息", "created_at": now},
                {"id": 7422, "session_id": 7321, "sender_type": "ai", "content": "活跃角色回复", "created_at": now},
                {"id": 7423, "session_id": 7322, "sender_type": "user", "content": "禁用用户消息", "created_at": now},
                {"id": 7424, "session_id": 7322, "sender_type": "ai", "content": "禁用角色回复", "created_at": now},
                {"id": 7425, "session_id": 7323, "sender_type": "user", "content": "已删用户消息", "created_at": now},
                {"id": 7426, "session_id": 7323, "sender_type": "ai", "content": "已删角色回复", "created_at": now},
            ]))
        seen: list[tuple[int, int, int]] = []

        async def fake_extract_single(session_id, character_id, user_id, user_msg, ai_msg, source_id=None):
            seen.append((session_id, character_id, user_id))
            return 0

        monkeypatch.setattr(ex, "extract_single", fake_extract_single)
        asyncio.run(ex.catchup_extract_all())
        active = [(s, c, u) for (s, c, u) in seen if s == 7321]
        disabled = [(s, c, u) for (s, c, u) in seen if s == 7322]
        deleted = [(s, c, u) for (s, c, u) in seen if s == 7323]
        assert active == [(7321, 7221, 7121)], f"活跃会话应被补采: {seen}"
        assert disabled == [], f"禁用账号会话不得被补采: {seen}"
        assert deleted == [], f"已删除账号会话不得被补采: {seen}"
    finally:
        asyncio.run(engine.dispose())
