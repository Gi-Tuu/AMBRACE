# -*- coding: utf-8 -*-
"""A34 尾（2026-10-13）：group_active 双角色分支「只请求发起者 1 条、预算 192」回归测试。

背景：A34 批 3 只在**落库侧**加了 ``MAX_LANDS_PER_GROUP_TICK=1``，生成侧仍要求模型输出 2-4 轮 JSON、
预算 ``max_tokens=512``，落库只用第一句 ⇒ 生成侧的轮次与预算都是浪费。本批**只改 prompt 文字与那一个数字**，
协议（JSON schema / 解析 / valid 遍历 / allowed / 截断日志 / commit / 闸③）逐字不动。

钉住四件事：
① 双角色分支：恰好 1 次 ``chat_completion``、``max_tokens=192``、prompt 只要 1 条且角色只提示发起者
   （搭档仍作背景出现），``user_id``/``task``/``temperature`` 透传不变；
② 无搭档分支逐字不变：``max_tokens=128``、纯文本 system 提示、单句冒泡照旧落 1 条；
③ 兼容路径：旧模型返回多条 JSON 时仍只落 1 条 + INFO 留痕；非法/空 JSON 不落库；
   闸③命中不落库也不写 ProactiveMessageLog；两个分支的闸③调用点仍在（源码计数 2 处）；
④ 两处**隔离进程动态 compile 变异**（把 prompt 恢复 2-4 轮 / 把预算恢复 512）必须让 ① 的守卫真变红，
   证明 ① 咬的是真实源码而不是手写反例；变异体是 exec 出来的独立模块对象，**不写生产源**。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时库一律 tmp_path，不连生产库。）
"""
import asyncio
import json
import logging
import os
import types
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.chat import ChatGroup, ChatGroupMember, ChatGroupMessage
from app.models.character import ProactiveMessageLog

OWNER = 1
GROUP = 1
SPEAKER = 1
PARTNER = 2

_SRC = None


def _source() -> str:
    global _SRC
    if _SRC is None:
        from app.scheduling import group_active as ga
        _SRC = Path(ga.__file__).read_text(encoding="utf-8")
    return _SRC


def _factory(tmp_path, tag: str):
    db_path = os.path.join(str(tmp_path), f"ga_a34_{tag}.db").replace("\\", "/")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _create_all():
        import app.models  # noqa: F401  确保全部 ORM 进入 metadata
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())
    return factory


def _seed(factory):
    """1 用户 + 2 角色同群（角色 1 发起者，角色 2 搭档）。"""
    from app.models.character import AICharacter
    from app.models.user import User

    async def _do():
        async with factory() as db:
            db.add_all([
                User(id=OWNER, username="u1", nickname="用户1"),
                AICharacter(id=SPEAKER, user_id=OWNER, name="小爱"),
                AICharacter(id=PARTNER, user_id=OWNER, name="小和"),
                ChatGroup(id=GROUP, user_id=OWNER),
                ChatGroupMember(group_id=GROUP, character_id=SPEAKER),
                ChatGroupMember(group_id=GROUP, character_id=PARTNER),
            ])
            await db.commit()

    asyncio.run(_do())


def _dump(factory, model):
    async def _do():
        async with factory() as db:
            return (await db.execute(select(model).order_by(model.id))).scalars().all()

    return asyncio.run(_do())


def _run(ga, monkeypatch, tmp_path, tag, reply, with_id):
    """在给定模块（真实模块或变异体）上真跑一次 run_group_active，返回 (ok, calls, 群消息, 日志)。"""
    factory = _factory(tmp_path, tag)
    calls = []

    async def fake_chat_completion(**kwargs):
        calls.append(kwargs)
        return reply

    try:
        _seed(factory)
        monkeypatch.setattr(ga, "async_session_factory", factory)
        monkeypatch.setattr("app.agent.llm_client.chat_completion", fake_chat_completion)
        ok = asyncio.run(ga.run_group_active(SPEAKER, GROUP, OWNER, with_id))
        return ok, calls, _dump(factory, ChatGroupMessage), _dump(factory, ProactiveMessageLog)
    finally:
        asyncio.run(factory.kw["bind"].dispose())


def _dual_reply(n: int = 1) -> str:
    people = [(SPEAKER, "最近天冷，大家记得添衣"), (PARTNER, "是呀，今天出门多穿点了吗"),
              (SPEAKER, "穿了呢，早上还叮嘱他带伞")]
    return json.dumps({"messages": [{"character_id": c, "content": t} for c, t in people[:n]]},
                      ensure_ascii=False)


def _assert_dual_single_request(ok, calls, msgs, logs, ga):
    """① 双角色分支的守卫：1 次调用 / 192 预算 / 只要 1 条 / 只提示发起者 / 透传不变 / 落 1 条。"""
    assert ok is True
    assert len(calls) == 1, f"应恰好 1 次 LLM 调用，实得 {len(calls)}"
    call = calls[0]
    assert call["max_tokens"] == 192, f"双角色预算应 192，实得 {call['max_tokens']}"
    assert call["task"] == "message" and call["user_id"] == OWNER
    assert call["temperature"] == 0.9
    system = call["messages"][0]["content"]
    prompt = call["messages"][1]["content"]
    assert system == "你是输出 JSON 的助手，直接输出 JSON，不要多余文字。"
    # 只请求 1 条：旧的「2-4 轮 / 交替发言 / 最多 4 条」口径必须消失
    assert "只放 1 条" in prompt, "prompt 未要求只放 1 条"
    assert "2-4 轮" not in prompt, "prompt 仍残留旧的 2-4 轮要求"
    assert "交替发言" not in prompt and "最多 4 条" not in prompt
    assert "不要替对方说话" in prompt
    # 角色只提示发起者；搭档仍作背景出现（不删上下文）
    assert f"character_id 填 {SPEAKER}（你）" in prompt
    assert f"character_id 只能是 {SPEAKER}" not in prompt
    assert "小和" in prompt and "也在群里" in prompt
    # JSON schema 仍是旧的 messages 列表
    assert '"messages": [{"character_id": 1, "content": "..."}]' in prompt
    # 落地与记账不变：1 条群消息 + 1 条日志（发起者），同批 commit
    assert len(msgs) == 1 and len(logs) == 1
    assert msgs[0].character_id == SPEAKER and logs[0].character_id == SPEAKER
    assert logs[0].message_type == "group_active" and logs[0].session_id is None
    assert logs[0].content == msgs[0].content
    assert json.loads(logs[0].extra_meta) == {"group_id": GROUP, "with_id": PARTNER}


def _mutant(name: str, old: str, new: str):
    """把真实源码做一处精确变异后 exec 成独立模块对象（不写生产源、不污染真实模块）。"""
    src = _source()
    assert old in src, "变异锚点不在源码里（源码已变？）"
    mutated = src.replace(old, new, 1)
    assert mutated != src
    from app.scheduling import group_active as ga
    mod = types.ModuleType(name)
    mod.__file__ = ga.__file__
    mod.__package__ = ga.__package__
    exec(compile(mutated, f"<mutant:{name}>", "exec"), mod.__dict__)
    return mod


# ---------------------------------------------------------------- ① 双角色分支


def test_dual_branch_requests_single_message_with_budget_192(tmp_path, monkeypatch):
    """① 双角色：只请求发起者 1 条、预算 192、恰好 1 次调用、透传不变。"""
    from app.scheduling import group_active as ga
    ok, calls, msgs, logs = _run(ga, monkeypatch, tmp_path, "dual1", _dual_reply(1), PARTNER)
    _assert_dual_single_request(ok, calls, msgs, logs, ga)


def test_dual_branch_prompt_has_no_multi_round_wording(tmp_path, monkeypatch):
    """①' 源码层面：双角色 prompt 里不再出现 2-4 轮口径，且没有引入新的预算常量。"""
    src = _source()
    assert "2-4 轮来回" not in src
    assert "MAX_TOKENS" not in src, "不得新增预算常量（预算应就地写在调用点）"
    assert src.count("max_tokens=192") == 1
    assert src.count("max_tokens=128") == 1, "无搭档分支的 128 不得被改"


# ---------------------------------------------------------------- ② 无搭档分支逐字不变


def test_no_partner_branch_unchanged_128_plain_text(tmp_path, monkeypatch):
    """② 无搭档：纯文本 system 提示 + max_tokens=128 + 单句冒泡落 1 条（原文案仍在）。"""
    from app.scheduling import group_active as ga
    ok, calls, msgs, logs = _run(ga, monkeypatch, tmp_path, "solo", "大家最近怎么样呀", None)
    assert ok is True and len(calls) == 1
    assert calls[0]["max_tokens"] == 128
    assert calls[0]["messages"][0]["content"] == "直接输出要说的话，不要加引号和标注。"
    prompt = calls[0]["messages"][1]["content"]
    assert "主动在群里冒个泡说一句话（20-40 字" in prompt
    assert "messages" not in prompt, "无搭档分支不该出现 JSON schema 要求"
    assert len(msgs) == 1 and len(logs) == 1
    assert msgs[0].character_id == SPEAKER and msgs[0].content == "大家最近怎么样呀"
    assert "with_id" not in json.loads(logs[0].extra_meta)


# ---------------------------------------------------------------- ③ 兼容与闸


def test_legacy_multi_entry_json_still_lands_one(tmp_path, monkeypatch, caplog):
    """③ 旧模型返回 3 条 JSON：仍只落 1 条 + INFO 留痕（兼容路径，不许被「优化」掉）。"""
    from app.scheduling import group_active as ga
    with caplog.at_level(logging.INFO):
        ok, _calls, msgs, logs = _run(ga, monkeypatch, tmp_path, "legacy3", _dual_reply(3), PARTNER)
    assert ok is True
    assert len(msgs) == 1 and len(logs) == 1
    assert msgs[0].character_id == SPEAKER
    assert any("multi-chat trimmed" in r.message and "drop=2" in r.message
               for r in caplog.records), "丢弃的轮次必须留 INFO 痕迹"


def test_legacy_partner_first_entry_still_lands(tmp_path, monkeypatch):
    """③' 旧语义保留：第一条来自搭档时照旧落那一条（本批不修、不顺手改语义）。"""
    from app.scheduling import group_active as ga
    reply = json.dumps({"messages": [
        {"character_id": PARTNER, "content": "我先冒个泡"},
        {"character_id": SPEAKER, "content": "那我跟一句"},
    ]}, ensure_ascii=False)
    ok, _calls, msgs, _logs = _run(ga, monkeypatch, tmp_path, "pfirst", reply, PARTNER)
    assert ok is True
    assert len(msgs) == 1 and msgs[0].character_id == PARTNER and msgs[0].content == "我先冒个泡"


def test_invalid_json_lands_nothing(tmp_path, monkeypatch):
    """③'' 非法/空输出不落库（解析与校验未放宽）。"""
    from app.scheduling import group_active as ga
    ok, _calls, msgs, logs = _run(ga, monkeypatch, tmp_path, "bad", "这不是 JSON", PARTNER)
    assert ok is False and msgs == [] and logs == []

    ok2, _calls2, msgs2, logs2 = _run(ga, monkeypatch, tmp_path, "empty", '{"messages": []}', PARTNER)
    assert ok2 is False and msgs2 == [] and logs2 == []


def test_conflict_gate_blocks_landing_and_logging(tmp_path, monkeypatch):
    """③''' 闸③命中：不落群消息也不写日志（不变量 I3），两个分支的调用点仍在。"""
    from app.scheduling import group_active as ga
    assert _source().count("await _pre_land_conflict(") == 2, "闸③两个分支的调用点必须都在"

    async def _blocked(group_id, char_id, last_seen_id):
        return "human_spoke"

    monkeypatch.setattr(ga, "_pre_land_conflict", _blocked)
    ok, _calls, msgs, logs = _run(ga, monkeypatch, tmp_path, "gate", _dual_reply(1), PARTNER)
    assert ok is False and msgs == [] and logs == []


# ---------------------------------------------------------------- ④ 隔离变异（动态 compile，不写生产源）


def test_mutant_prompt_restored_to_multi_round_turns_guard_red(tmp_path, monkeypatch):
    """④ 变异 1：把 prompt 恢复成旧的「2-4 轮 / 交替发言」口径 ⇒ ① 的守卫必须真变红。"""
    mutant = _mutant(
        "ga_mut_prompt",
        'f"1. messages 里只放 1 条，就是你（发起者）开口那一句，不要替对方说话；\\n"',
        '"1. 第一条必须是你（发起者）先开口，之后两人交替发言（对方接话，你可再回，最多 4 条）；\\n"',
    )
    ok, calls, msgs, logs = _run(mutant, monkeypatch, tmp_path, "mutp", _dual_reply(1), PARTNER)
    with pytest.raises(AssertionError):
        _assert_dual_single_request(ok, calls, msgs, logs, mutant)


def test_mutant_budget_restored_to_512_turns_guard_red(tmp_path, monkeypatch):
    """④' 变异 2：把预算恢复 512 ⇒ ① 的守卫必须真变红。"""
    mutant = _mutant("ga_mut_budget", "max_tokens=192", "max_tokens=512")
    ok, calls, msgs, logs = _run(mutant, monkeypatch, tmp_path, "mutb", _dual_reply(1), PARTNER)
    with pytest.raises(AssertionError):
        _assert_dual_single_request(ok, calls, msgs, logs, mutant)


def test_mutants_do_not_touch_production_source():
    """④'' 变异全程只改内存副本：真实模块的预算仍是 192（生产源未被写）。"""
    from app.scheduling import group_active as ga
    assert _source().count("max_tokens=192") == 1
    mutant = _mutant("ga_mut_clean", "max_tokens=192", "max_tokens=512")
    assert "max_tokens=192" in _source()
    assert mutant is not ga
