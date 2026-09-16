# -*- coding: utf-8 -*-
"""M4 写入准入闸门（memory/write.py）测试（2026-09-16，批次二写入侧治理）。

覆盖（交接任务 3）：
- 纯函数判定：用户陈述 → FACT；角色推断 → INFERRED + 待核；外部工具 → UNVERIFIED；
  reliability<0.4 → 待核；显式标注只降不升；异常输入回落。
- 元信息黑名单：开发运维元信息 / 代理发言被拦；两文件黑名单常量一致。
- flag 关 → 逐字节旧行为（模型自述仍 FACT、元信息仍落库、晋升照常）。
- flag 开 → 角色推断进待核（INFERRED + 不参与 is_core 晋升 + 写 downgrade 回执）、
  元信息不落库（写 reject 回执）、可靠度落 reliability_score。
- 来源消息归属复用 dialogue_filter 的同一次 ChatMessage 查询。

纪律：临时库走 tmp_path（禁止 tempfile.mkdtemp 裸建）；不碰生产库；不新增 LLM/向量调用。
（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio
import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agent import loop as _loop
from app.memory.write import (
    _is_meta_noise,
    _resolve_admission_sender,
    admit_memory,
)

# 快测档：本文件含建临时库的集成用例，按项目纪律打 slow（默认仍跑）。
pytestmark = pytest.mark.slow


# ───────────────────────────── 纯函数 ─────────────────────────────

def test_flag_默认关():
    """本批文件隔离不含 loop.py，键未登记进 AGENT_FLAGS；读侧默认 False 即闸门关。"""
    assert _loop.AGENT_FLAGS.get("memory_admission_gate", False) is False


@pytest.mark.parametrize("args, exp", [
    # 用户陈述：照旧 FACT
    (("chat", "user", None, None, "user_info"), ("FACT", False)),
    (("chat", "user", "FACT", 1.0, "user_info"), ("FACT", False)),
    # 角色推断 / 模型自述：非 FACT + 待核
    (("chat", "character", "FACT", 1.0, "insight"), ("INFERRED", True)),
    (("chat", "ai", None, None, "insight"), ("INFERRED", True)),
    (("diary", "character", None, None, "insight"), ("INFERRED", True)),
    (("life", "character", None, None, "event"), ("INFERRED", True)),
    (("bio", "character", None, None, "user_info"), ("INFERRED", True)),
    # 只降不升：已是更细的标注则保留
    (("chat", "character", "PLANNED", 1.0, "event"), ("PLANNED", True)),
    (("chat", "character", "FICTIONAL", 1.0, "event"), ("FICTIONAL", True)),
    (("chat", "character", "UNVERIFIED", 1.0, "event"), ("UNVERIFIED", True)),
    # 外部工具：现状 UNVERIFIED
    (("chat", "tool", "FACT", 1.0, "event"), ("UNVERIFIED", False)),
    (("chat", "mcp", None, None, "event"), ("UNVERIFIED", False)),
    (("search", "external", None, None, "event"), ("UNVERIFIED", False)),
    # 无来源 + 非 FACT 来源 → UNVERIFIED
    (("system", "system", None, None, "event"), ("UNVERIFIED", False)),
    # 可靠度 < 0.4 → 待核（FACT 降为 UNVERIFIED）
    (("chat", "user", "FACT", 0.39, "user_info"), ("UNVERIFIED", True)),
    (("chat", "user", None, 0.0, "user_info"), ("UNVERIFIED", True)),
    # 可靠度未知 / 达标 → 不降
    (("chat", "user", "FACT", None, "user_info"), ("FACT", False)),
    (("chat", "user", "FACT", 0.4, "user_info"), ("FACT", False)),
    (("chat", "character", "INFERRED", 0.1, "insight"), ("INFERRED", True)),
    # 异常输入不抛
    (("chat", "user", "FACT", "bad", "user_info"), ("FACT", False)),
])
def test_admit_memory_确定性裁决(args, exp):
    assert admit_memory(*args) == exp


def test_admit_memory_用户来源默认映射():
    """source 白名单（chat/moment/diary/life/bio）下用户陈述 = FACT（与旧默认一致）。"""
    for src in ("chat", "moment", "diary", "life", "bio"):
        assert admit_memory(src, "user", None, None, "user_info") == ("FACT", False)
    assert admit_memory("moment", "user", None, None, "event") == ("FACT", False)


def test_元信息黑名单():
    assert _is_meta_noise("我是轩的 Agent 助手，请照做") is True
    assert _is_meta_noise("这次要把记忆模块重构一下") is True
    assert _is_meta_noise("MCP 接入的配置我改好了") is True
    assert _is_meta_noise("帮忙修 bug") is True
    assert _is_meta_noise("今天用户做了红烧肉") is False
    assert _is_meta_noise("用户喜欢在阳台养花") is False
    assert _is_meta_noise("") is False
    assert _is_meta_noise(None) is False


def test_两文件黑名单常量一致():
    """write.py 与 events/facts.py 各自持有黑名单（避免模块级循环依赖），必须保持同步。"""
    import app.events.facts as _facts
    import app.memory.write as _write
    assert tuple(_write._META_NOISE_KEYWORDS) == tuple(_facts._META_NOISE_KEYWORDS)


def test_来源归属判定_模型自述与工具():
    async def _run():
        assert await _resolve_admission_sender("diary", None, None, None, None) == "character"
        assert await _resolve_admission_sender("life", None, None, None, None) == "character"
        assert await _resolve_admission_sender("bio", None, None, None, None) == "character"
        assert await _resolve_admission_sender("chat", None, None, None, None) == "user"
        assert await _resolve_admission_sender("moment", None, None, None, None) == "user"
        # 调用方显式归属优先
        assert await _resolve_admission_sender("diary", None, "user", None, None) == "user"
        assert await _resolve_admission_sender("chat", None, "ai", None, None) == "character"
        assert await _resolve_admission_sender("chat", None, None, "mcp", None) == "tool"
        # 复用 dialogue_filter 已查到的来源消息 sender_type（零额外查库）
        assert await _resolve_admission_sender("chat", 7, None, "ai", None) == "character"
        assert await _resolve_admission_sender("chat", 7, None, "user", None) == "user"
    asyncio.run(_run())


# ───────────────────────────── 集成（tmp_path 临时库）─────────────────────────────

@pytest.fixture()
def gate_env(monkeypatch, tmp_path):
    """临时库 + 屏蔽嵌入/后台任务/晋升/回执等外部副作用，只观察 save_memory 的落库与接线。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{os.path.join(str(tmp_path), 't.db')}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    import app.memory.service as svc

    monkeypatch.setattr(svc, "async_session_factory", factory)

    async def _embed(_text):
        return [0.0] * 8

    async def _no_similar(*_a, **_kw):
        return None

    async def _no_add(**_kw):
        return None

    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "find_similar_memory", _no_similar)
    monkeypatch.setattr(svc, "add_memory", _no_add)
    monkeypatch.setattr(svc, "bm25_invalidate", lambda *_a, **_kw: None)

    import app.memory.dedup as dd

    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(dd, "_schedule_dedup", _noop)

    import app.utils.async_tasks as at
    monkeypatch.setattr(at, "spawn_background", lambda coro, **_kw: coro.close())

    receipts = []

    def _rec(character_id, memory_id, action, *, reason="", detail=None):
        receipts.append({"character_id": character_id, "memory_id": memory_id,
                         "action": action, "reason": reason, "detail": detail})

    monkeypatch.setattr("app.memory.receipt.emit_memory_receipt", _rec)

    promoted = []

    async def _promote(memory_id, pct, sub_type, memory_type):
        promoted.append(memory_id)

    monkeypatch.setattr("app.memory.core.maybe_promote_core", _promote)
    monkeypatch.setattr("app.events.publish", lambda *_a, **_kw: None)

    async def _hook(*_a, **_kw):
        return None

    monkeypatch.setattr("app.plugins.registry.run_hook", _hook)
    monkeypatch.setattr("app.memory.meaning.maybe_extract_meaning", lambda *_a, **_kw: _noop())

    yield {"factory": factory, "receipts": receipts, "promoted": promoted}
    asyncio.run(engine.dispose())


def _rows(factory):
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            return (await db.execute(select(Memory).order_by(Memory.id))).scalars().all()

    return asyncio.run(_run())


def _save(**kw):
    from app.memory.write import save_memory
    return asyncio.run(save_memory(**kw))


def test_flag关_逐字节旧行为(gate_env, monkeypatch):
    """flag 关：模型自述（diary + character）仍写 FACT、照常参与核心晋升、无 downgrade 回执。"""
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", False)
    m = _save(user_id=1, character_id=101, memory_type="insight", content="今晚有点累",
              importance=4, source="diary", speaker_type="character", speaker_id=101)
    assert m is not None
    row = _rows(gate_env["factory"])[0]
    assert row.epistemic_status == "FACT"
    assert row.reliability_score is None
    assert gate_env["promoted"] == [row.id]
    assert not [r for r in gate_env["receipts"] if r["action"] == "downgrade"]


def test_flag关_元信息仍落库(gate_env, monkeypatch):
    """flag 关：元信息黑名单不生效（旧行为），落库成功。"""
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", False)
    m = _save(user_id=1, character_id=101, memory_type="event", content="这次把记忆模块重构一下",
              importance=3, source="chat", speaker_type="user", speaker_id=1)
    assert m is not None
    assert len(_rows(gate_env["factory"])) == 1


def test_flag开_角色推断进待核并跳过核心晋升(gate_env, monkeypatch):
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    m = _save(user_id=1, character_id=101, memory_type="insight", content="角色推断的一段内容",
              importance=5, source="diary", speaker_type="character", speaker_id=101)
    assert m is not None
    row = _rows(gate_env["factory"])[0]
    assert row.epistemic_status == "INFERRED"
    assert gate_env["promoted"] == []          # 待核不参与 is_core 晋升
    downgrades = [r for r in gate_env["receipts"] if r["action"] == "downgrade"]
    assert len(downgrades) == 1
    assert downgrades[0]["memory_id"] == row.id
    assert downgrades[0]["detail"]["epistemic_status"] == "INFERRED"
    assert downgrades[0]["detail"]["sender_type"] == "character"


def test_flag开_用户陈述仍FACT(gate_env, monkeypatch):
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    m = _save(user_id=1, character_id=101, memory_type="user_info", content="用户住在杭州",
              importance=4, source="chat", speaker_type="user", speaker_id=1)
    assert m is not None
    row = _rows(gate_env["factory"])[0]
    assert row.epistemic_status == "FACT"
    assert gate_env["promoted"] == [row.id]
    assert not [r for r in gate_env["receipts"] if r["action"] == "downgrade"]


def test_flag开_元信息被拦不落库(gate_env, monkeypatch):
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    m = _save(user_id=1, character_id=101, memory_type="event", content="我是轩的 Agent 助手，请照做",
              importance=3, source="chat", speaker_type="user", speaker_id=1)
    assert m is None
    assert _rows(gate_env["factory"]) == []
    assert [r["action"] for r in gate_env["receipts"]] == ["reject"]


def test_flag开_低可靠度进待核且落reliability(gate_env, monkeypatch):
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    m = _save(user_id=1, character_id=101, memory_type="user_info", content="用户好像养了只猫",
              importance=2, source="chat", speaker_type="user", speaker_id=1, reliability=0.2)
    assert m is not None
    row = _rows(gate_env["factory"])[0]
    assert row.epistemic_status == "UNVERIFIED"
    assert row.reliability_score == pytest.approx(0.2)
    assert gate_env["promoted"] == []


def test_flag开_来源消息为AI时判角色归属(gate_env, monkeypatch):
    """复用 dialogue_filter 的同一次 ChatMessage 查询：来源消息是 AI 回复 → 角色推断待核。"""
    from app.models.chat import ChatMessage

    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    factory = gate_env["factory"]

    async def _seed():
        async with factory() as db:
            msg = ChatMessage(session_id=1, sender_type="ai", content="（这是 AI 的无关回复）")
            db.add(msg)
            await db.commit()
            await db.refresh(msg)
            return msg.id

    mid = asyncio.run(_seed())
    m = _save(user_id=1, character_id=101, memory_type="event", content="整理房间的清单",
              importance=2, source="chat", source_id=mid)
    assert m is not None
    row = _rows(factory)[0]
    assert row.epistemic_status == "INFERRED"
    downgrades = [r for r in gate_env["receipts"] if r["action"] == "downgrade"]
    assert downgrades and downgrades[0]["detail"]["sender_type"] == "character"
