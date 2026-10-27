# -*- coding: utf-8 -*-
"""P0-1b 测试：内部 AI 行为统一执行（登记 + internal_runner + 高频接入点）"""
import asyncio

from app.agent import internal_runner
from app.agent import tools


def test_内部工具登记_scope无门禁():
    for n in ("memory_extract", "memory_fact_check", "emotion_care", "weave_card", "memory_summary"):
        s = tools.get_tool(n)
        assert s is not None, n
        assert s.scope is None  # 内部行为无权限门禁
        assert s.provenance == n


def test_run_internal_未登记返回错误():
    out = asyncio.run(internal_runner.run_internal("不存在工具", {}))
    assert out["status"] == "error"
    assert "not registered" in out["error"]


def test_run_internal_走统一执行入口(monkeypatch):
    seen = {}

    async def _fake_execute(spec, payload, **kw):
        seen["spec"] = spec
        seen["payload"] = payload
        return {"status": "ok", "result": {"ok": True}}

    monkeypatch.setattr("app.agent.tool_runner.execute_tool", _fake_execute)
    out = asyncio.run(internal_runner.run_internal(
        "memory_extract",
        {"session_id": 9, "character_id": 11, "user_id": 4, "user_msg": "x", "ai_msg": "y"},
        character_id=11, user_id=4,
    ))
    assert seen["spec"].name == "memory_extract"
    assert seen["spec"].scope is None
    assert seen["payload"]["character_id"] == 11
    assert out["status"] == "ok"


def test_run_internal_异常隔离(monkeypatch):
    async def _boom(spec, payload, **kw):
        raise RuntimeError("内部失败")

    monkeypatch.setattr("app.agent.tool_runner.execute_tool", _boom)
    out = asyncio.run(internal_runner.run_internal("memory_extract", {}))
    assert out["status"] == "error"
    assert "内部失败" in out["error"]


def test_extractor_批量经内部入口(monkeypatch, tmp_path):
    """批量入口必须是真实链路：会话/角色/账号从临时库核验、source_id 属于该会话，
    成功判据是 ProcessedExtraction 落库（`run_internal` 只返回 status=ok 不够）。
    身份核验不许 mock——这正是 10-06 之后 `process_session` 补上的门禁。"""
    from _dbclone import clone_engine, make_session_factory
    from app.memory import extractor as ex
    from app.memory import extract_queue
    from app.models.chat import ChatMessage, ChatSession
    from app.models.character import AICharacter
    from app.models.memory import ProcessedExtraction
    from app.models.user import User
    from app.scheduling import periodic_state as pst
    from sqlalchemy import select

    sid = 999901

    async def _seed():
        engine = clone_engine(tmp_path / "internal_batch.db")
        factory = make_session_factory(engine)
        async with factory() as db:
            db.add(User(id=4, username="ib_u4", nickname="合成用户4"))
            await db.flush()
            db.add(AICharacter(id=11, user_id=4, name="合成角色11"))
            await db.flush()
            db.add(ChatSession(id=sid, user_id=4, character_id=11, is_active=True))
            await db.flush()
            for mid in (1, 2, 3, 4):
                db.add(ChatMessage(id=mid, session_id=sid, sender_type="user", content=f"m{mid}"))
            await db.commit()
        return engine, factory

    engine, factory = asyncio.run(_seed())
    # A41：队列与节流判据都已落盘（extractor._pending / _last_batch_at 均迁出）⇒ 指向临时文件
    monkeypatch.setattr(ex, "async_session_factory", factory)
    monkeypatch.setattr(extract_queue, "_QUEUE_FILE", tmp_path / "extract_queue.json")
    monkeypatch.setattr(pst, "_STATE_FILE", tmp_path / "periodic_state.json")
    monkeypatch.setattr(pst, "_LOCAL_STAMPS", {})

    calls = []

    async def _fake_internal(tool, payload, **kw):
        calls.append((tool, payload))
        # 内部工具的真实回执：把 ProcessedExtraction 落库；否则 worker 判 extraction_not_committed。
        await ex._mark_processed(payload["source_id"])
        return {"status": "ok"}

    try:
        monkeypatch.setattr("app.agent.internal_runner.run_internal", _fake_internal)
        # 前 3 条累积（BATCH_SIZE=4），第 4 条触发 process_session 一次性 drain 4 对
        for i in range(1, 5):
            asyncio.run(ex.add_chat_memory_extraction(sid, 11, 4, f"m{i}", f"r{i}", source_id=i))

        assert [t for t, _ in calls] == ["memory_extract"] * 4
        for i, (_, payload) in enumerate(calls, start=1):
            assert payload["session_id"] == sid
            assert payload["character_id"] == 11
            assert payload["user_id"] == 4
            assert payload["source_id"] == i
            assert payload["user_msg"] == f"m{i}"
            assert payload["ai_msg"] == f"r{i}"

        async def _processed_ids():
            async with factory() as db:
                rows = (await db.execute(select(ProcessedExtraction.user_message_id))).scalars().all()
            return set(rows)

        # 成功判据在库不在返回值：4 条 ProcessedExtraction 必须真落库
        assert asyncio.run(_processed_ids()) == {1, 2, 3, 4}
    finally:
        asyncio.run(engine.dispose())


def test_fact_check_经内部入口(monkeypatch):
    from app.memory import fact_check
    calls = []

    async def _fake_internal(tool, payload, **kw):
        calls.append((tool, payload))

    monkeypatch.setattr("app.agent.internal_runner.run_internal", _fake_internal)

    async def _main():
        fact_check.schedule_fact_check(11, 4, "用户消息", "AI 回复")
        await asyncio.sleep(0.02)  # 让 fire-and-forget task 在同一 loop 执行

    asyncio.run(_main())
    assert calls and calls[0][0] == "memory_fact_check"
    assert calls[0][1]["character_id"] == 11
