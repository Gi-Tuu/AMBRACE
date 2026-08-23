# -*- coding: utf-8 -*-
"""G-P2-1（WS chunked 可靠度/事实核查接入）+ X-4（记忆疲劳：热度裁剪 + 会话内 N 轮去重）测试（2026-08-18）

- G-P2-1：chunked 主链路（App 主路径）在 AI 回复生成后以 reliability=True 走与 HTTP 同一公共收尾入口，
  调用同一可靠度信号/事实核查函数且参数一致（mock 断言）；
- X-4①：核心记忆/关系锚点注入上限按角色热度裁剪（高频 10/5 全量、低频 3/2），
  并经 _inject_core_anchors_loops 透传到 get_core_memories/get_relationship_anchors 的 limit；
- X-4②：会话内「N 轮内不重复注入」轻量去重——同一记忆最近 5 轮内不重复进入检索区、第 6 轮恢复；
  仅影响「和你相关的记忆」检索区行，核心记忆/锚点等长期画像分区不受限（避免丢画像）。
（项目未装 pytest-asyncio，统一 asyncio.run 同步执行）
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from app.agent import context_builder as cb
from app.services import chat_service


async def _async_noop(*a, **k):
    return None


class _FakeDB:
    """chunk 落库循环替身：add/flush/refresh/commit 无真实 DB"""

    def __init__(self):
        self._n = 0

    def add(self, obj):
        pass

    async def flush(self):
        pass

    async def refresh(self, obj):
        self._n += 1
        obj.id = self._n
        obj.created_at = datetime.now(timezone.utc).replace(tzinfo=None)

    async def commit(self):
        pass


class _FakeSessionFactory:
    """async_session_factory 替身：async with factory() as db 可用"""

    def __init__(self, db):
        self._db = db

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


# ---------------- G-P2-1：WS chunked 可靠度/事实核查（与 HTTP 同一函数同一参数） ----------------

def test_公共收尾_reliability_调用与HTTP相同函数(monkeypatch):
    """_run_post_processing(reliability=True) 走与 HTTP 路径相同的可靠度信号/事实核查函数，参数一致"""
    calls = []
    monkeypatch.setattr("app.memory.reliability.schedule_feedback_processing",
                        lambda *a, **k: calls.append(("feedback", a, k)))
    monkeypatch.setattr("app.memory.fact_check.schedule_fact_check",
                        lambda *a, **k: calls.append(("fact_check", a, k)))
    # 拦截公共收尾中的 fire-and-forget DB 写路径，保持纯逻辑验证
    monkeypatch.setattr(chat_service, "add_chat_memory_extraction", _async_noop)
    monkeypatch.setattr(chat_service, "_generate_initial_bio", _async_noop)
    monkeypatch.setattr(chat_service, "_bump_relationship", _async_noop)
    monkeypatch.setattr(chat_service, "_trigger_state_eval", lambda *a, **k: None)
    monkeypatch.setattr("app.agent.topic_tracker.maybe_extract_topics", _async_noop)
    monkeypatch.setattr("app.agent.topic_tracker.update_topic_resolution", _async_noop)

    async def _main():
        await chat_service._run_post_processing(
            9, 4, 11, "用户消息内容", {}, "AI 回复文本", 1001, 1002,
            reliability=True,
        )

    asyncio.run(_main())
    assert ("feedback", (11, 4, "用户消息内容", "AI 回复文本"), {}) in calls
    assert ("fact_check", (11, 4, "用户消息内容", "AI 回复文本"), {}) in calls


def test_chunked_回复生成后以reliability调用公共收尾(monkeypatch):
    """chunked 主链路（App 主路径）AI 回复生成后以 reliability=True 调用与 HTTP 相同的公共收尾入口"""
    seen = {}

    async def _fake_persist(*a, **k):
        return (5001, {"id": 5001, "session_id": 9, "sender_type": "user",
                       "content": "hi", "created_at": "2026-08-18T00:00:00", "extra_meta": None})

    async def _fake_core(*a, **k):
        return {
            "final_state": {"emotional_state": "", "status_update": None, "reasoning": "r",
                            "tools_used": [], "should_update_memory": False},
            "final_text": "第一段。第二段。",
            "gen_prompt": None, "img_text": None, "cal_note_text": None, "memo_text": None,
        }

    async def _fake_post(*a, **k):
        seen["kwargs"] = k

    monkeypatch.setattr(chat_service, "_persist_user_message", _fake_persist)
    monkeypatch.setattr(chat_service, "_run_agent_core", _fake_core)
    monkeypatch.setattr(chat_service, "_run_post_processing", _fake_post)
    monkeypatch.setattr("app.agent.nodes.split_response", lambda text, emo: ["第一段。", "第二段。"])
    monkeypatch.setattr(chat_service, "async_session_factory", _FakeSessionFactory(_FakeDB()))

    out = asyncio.run(chat_service.send_and_receive_chunked(9, 4, 11, "hi"))
    assert seen["kwargs"].get("reliability") is True   # 与 HTTP 路径同一入口、同一开关
    assert seen["kwargs"].get("gen_prompt") is None
    assert len(out["chunks"]) == 2
    assert out["chunks"][0]["content"] == "第一段。"


# ---------------- X-4①：核心记忆/关系锚点注入上限按热度裁剪 ----------------

def test_trim_limits_高频核心锚点保持全量():
    d = cb._trim_limits(hot=True)
    assert d["core_limit"] == 10
    assert d["anchor_limit"] == 5


def test_trim_limits_低频核心锚点降低():
    d = cb._trim_limits(hot=False)
    assert d["core_limit"] == cb.LOW_FREQ_CORE_LIMIT == 3
    assert d["anchor_limit"] == cb.LOW_FREQ_ANCHOR_LIMIT == 2
    assert d["core_limit"] < 10 and d["anchor_limit"] < 5


def test_核心锚点注入上限按热度透传(monkeypatch):
    """_inject_core_anchors_loops 把热度裁剪后的上限透传给 get_core_memories/get_relationship_anchors"""
    seen = {}

    async def _fake_core(cid, limit=10):
        seen["core"] = (cid, limit)
        return []

    async def _fake_anchor(cid, uid, limit=5):
        seen["anchor"] = (cid, uid, limit)
        return []

    async def _fake_loops(cid, uid, limit=10):
        return []

    monkeypatch.setattr("app.memory.core.get_core_memories", _fake_core)
    monkeypatch.setattr("app.memory.core.get_relationship_anchors", _fake_anchor)
    monkeypatch.setattr("app.memory.core.get_open_loops", _fake_loops)

    asyncio.run(cb._inject_core_anchors_loops(11, 4, cb._trim_limits(hot=True)))
    assert seen == {"core": (11, 10), "anchor": (11, 4, 5)}
    asyncio.run(cb._inject_core_anchors_loops(11, 4, cb._trim_limits(hot=False)))
    assert seen == {"core": (11, 3), "anchor": (11, 4, 2)}


def test_核心锚点注入_失败静默缺省无(monkeypatch):
    """注入异常/无角色时静默返回「无」，不阻断主链路"""
    async def _boom(cid, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.memory.core.get_core_memories", _boom)
    core_text, anchors_text, loops_text = asyncio.run(
        cb._inject_core_anchors_loops(11, 4, cb._trim_limits(hot=True))
    )
    assert core_text == "无" and anchors_text == "无" and loops_text == "无"
    # cid 为空：不查询、直接「无」
    core_text, anchors_text, loops_text = asyncio.run(
        cb._inject_core_anchors_loops(None, 4, cb._trim_limits(hot=True))
    )
    assert core_text == "无" and anchors_text == "无"


# ---------------- X-4②：会话内「N 轮内不重复注入」轻量去重（仅检索区） ----------------

def test_检索区同一记忆5轮内不重复第6轮恢复():
    cb._memory_char_rounds.clear()
    cb._memory_inject_rounds.clear()
    mems = [{"id": 1, "content": "用户喜欢喝美式咖啡", "created_at": "2026-08-18"}]
    cb._bump_memory_round(11)                                  # 第 1 轮
    lines = cb._build_retrieved_memory_lines(11, mems)
    assert len(lines) == 1 and "用户喜欢喝美式咖啡" in lines[0]
    for _ in range(4):                                         # 第 2~5 轮：不重复注入
        cb._bump_memory_round(11)
        assert cb._build_retrieved_memory_lines(11, mems) == []
    cb._bump_memory_round(11)                                  # 第 6 轮：恢复
    assert len(cb._build_retrieved_memory_lines(11, mems)) == 1


def test_去重仅跳过注入过的记忆():
    cb._memory_char_rounds.clear()
    cb._memory_inject_rounds.clear()
    cb._bump_memory_round(11)
    cb._build_retrieved_memory_lines(11, [{"id": 1, "content": "A", "created_at": "2026-08-18"}])
    cb._bump_memory_round(11)
    lines = cb._build_retrieved_memory_lines(11, [
        {"id": 1, "content": "A", "created_at": "2026-08-18"},  # 最近注入过 → 跳过
        {"id": 2, "content": "B", "created_at": "2026-08-18"},  # 未注入过 → 保留
    ])
    assert len(lines) == 1 and "B" in lines[0]


def test_去重兼容dict与ORM对象():
    cb._memory_char_rounds.clear()
    cb._memory_inject_rounds.clear()
    cb._bump_memory_round(11)
    cb._mark_memories_injected(11, [{"id": 5}])
    out1 = cb._filter_recently_injected(11, [{"id": 5}, {"id": 6}])
    assert [m["id"] for m in out1] == [6]
    cb._bump_memory_round(11)
    out2 = cb._filter_recently_injected(11, [SimpleNamespace(id=5), SimpleNamespace(id=6)])
    assert [m.id for m in out2] == [6]


def test_无id记忆不参与去重():
    cb._memory_char_rounds.clear()
    cb._memory_inject_rounds.clear()
    cb._bump_memory_round(11)
    cb._mark_memories_injected(11, [{"id": 1}])
    out = cb._filter_recently_injected(11, [{"content": "无id"}, {"id": 1, "content": "有id"}])
    assert [m.get("content") for m in out] == ["无id"]


def test_去重按角色隔离():
    cb._memory_char_rounds.clear()
    cb._memory_inject_rounds.clear()
    cb._bump_memory_round(11)
    cb._build_retrieved_memory_lines(11, [{"id": 1, "content": "A", "created_at": "2026-08-18"}])
    # 角色 12 不受角色 11 的注入状态影响
    cb._bump_memory_round(12)
    assert len(cb._build_retrieved_memory_lines(12, [{"id": 1, "content": "A", "created_at": "2026-08-18"}])) == 1


def test_去重容量裁剪():
    cb._memory_char_rounds.clear()
    cb._memory_inject_rounds.clear()
    cb._bump_memory_round(11)
    mems = [{"id": i, "content": f"m{i}", "created_at": "2026-08-18"}
            for i in range(cb.MEMORY_DEDUP_MAX_PER_CHAR + 50)]
    cb._build_retrieved_memory_lines(11, mems)
    assert sum(1 for k in cb._memory_inject_rounds if k[0] == 11) <= cb.MEMORY_DEDUP_MAX_PER_CHAR


def test_长期画像分区不受N轮去重限制(monkeypatch):
    """核心记忆/锚点注入不经过检索区 N 轮去重（避免丢画像）"""
    cb._memory_char_rounds.clear()
    cb._memory_inject_rounds.clear()
    cb._bump_memory_round(11)
    cb._mark_memories_injected(11, [{"id": 77}])   # 检索区刚注入过 id=77

    async def _fake_core(cid, limit=10):
        return [SimpleNamespace(id=77, created_at=datetime(2026, 8, 18),
                                content="用户是程序员", core_category="identity")]

    async def _fake_anchor(cid, uid, limit=5):
        return [SimpleNamespace(id=77, created_at=datetime(2026, 8, 18),
                                content="一起爬过山", core_category=None)]

    async def _fake_loops(cid, uid, limit=10):
        return []

    monkeypatch.setattr("app.memory.core.get_core_memories", _fake_core)
    monkeypatch.setattr("app.memory.core.get_relationship_anchors", _fake_anchor)
    monkeypatch.setattr("app.memory.core.get_open_loops", _fake_loops)

    core_text, anchors_text, _ = asyncio.run(
        cb._inject_core_anchors_loops(11, 4, cb._trim_limits(hot=True))
    )
    assert "用户是程序员" in core_text     # 即使检索区最近注入过，核心记忆照常注入
    assert "一起爬过山" in anchors_text
