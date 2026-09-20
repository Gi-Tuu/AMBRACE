# -*- coding: utf-8 -*-
"""#72 PR-C P3+P4 群聊认知升级（逐角色认知生成 + 私有注入 + 预算 + 两级闸 + 观测）。

全部用 pytest tmp_path 临时库（项目 2026-09-09 铁律），不碰真实库、不改 flag 默认值。
覆盖：私有/公开不串、预算封顶与跨群计数、幂等、DM 过滤、默认关零影响、LLM 失败静默、
两级闸（群列）、配额挤压实测、注入观测 trace。
项目未装 pytest-asyncio，统一 asyncio.run 同步执行。
"""
import asyncio
import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401  注册全部模型（含 chat / agent）
from app.models.base import Base
from app.models.chat import (
    ChatGroup, ChatGroupMember, GroupCharCognition, GroupMemory,
)
from app.models.character import AICharacter
from app.models.user import User
from app.memory import group_memory as gm
from app.agent.context import section_overlay as overlay

pytestmark = pytest.mark.slow


USER = 1
CHAR_A = 11
CHAR_B = 12
G1 = 1   # cognition_enabled = True
G2 = 2   # cognition_enabled = True
G3 = 3   # cognition_enabled = False（两级闸群列关）
G4 = 4   # cognition_enabled = True（用于跨群预算压测）


def _make_db(tmp_path):
    db_path = os.path.join(str(tmp_path), "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def _seed():
        async with factory() as db:
            db.add(User(id=USER, username="u", nickname="用户"))
            db.add(AICharacter(id=CHAR_A, user_id=USER, name="小阳"))
            db.add(AICharacter(id=CHAR_B, user_id=USER, name="小冰"))
            db.add(ChatGroup(id=G1, user_id=USER, name="测试群1", cognition_enabled=True))
            db.add(ChatGroup(id=G2, user_id=USER, name="测试群2", cognition_enabled=True))
            db.add(ChatGroup(id=G3, user_id=USER, name="测试群3", cognition_enabled=False))
            db.add(ChatGroup(id=G4, user_id=USER, name="测试群4", cognition_enabled=True))
            for gid in (G1, G3):
                db.add(ChatGroupMember(group_id=gid, character_id=CHAR_A))
            db.add(ChatGroupMember(group_id=G1, character_id=CHAR_B))
            db.add(ChatGroupMember(group_id=G2, character_id=CHAR_A))
            await db.commit()

    asyncio.run(_init())
    asyncio.run(_seed())
    return engine, factory


@pytest.fixture()
def dbsetup(tmp_path, monkeypatch):
    """临时库 + 把 group_memory 的会话工厂指向临时库 + flag 默认关。"""
    engine, factory = _make_db(tmp_path)
    monkeypatch.setattr(gm, "async_session_factory", factory)
    # LLM / trace 默认 stub（各例按需开启）；chat_completion 在 group_memory 内惰性导入自 llm_client
    from app.agent import llm_client
    async def _no_llm(*a, **k):
        return "我觉得这件事挺有意思的。"
    monkeypatch.setattr(llm_client, "chat_completion", _no_llm)
    traces = []
    monkeypatch.setattr(gm, "_trace_group_cognition", lambda route, cid, detail: traces.append((route, cid, detail)))
    yield factory, traces
    engine.sync_engine.dispose()


def _enable_flag(monkeypatch):
    import app.agent.loop as loop_mod
    monkeypatch.setitem(loop_mod.AGENT_FLAGS, "group_cognition_v2", True)


async def _count_rows(factory, model, **filters):
    async with factory() as db:
        stmt = select(model)
        for k, v in filters.items():
            stmt = stmt.where(getattr(model, k) == v)
        return len((await db.execute(stmt)).scalars().all())


# ───────────────────────── 默认关零影响 ─────────────────────────

def test_默认关_生成不写库(dbsetup, monkeypatch):
    factory, _ = dbsetup
    # flag 默认关（未 enable）
    asyncio.run(gm.generate_char_cognitions(
        group_id=G1, user_id=USER, user_content="hi",
        replies=[{"character_id": CHAR_A, "content": "你好"}], name_map={CHAR_A: "小阳"},
    ))
    assert asyncio.run(_count_rows(factory, GroupCharCognition, group_id=G1)) == 0


def test_默认关_注入section返回空(dbsetup, monkeypatch):
    factory, _ = dbsetup
    out = asyncio.run(overlay.group_char_cognition_section(
        {"character_id": CHAR_A, "group_id": G1, "user_id": USER}, {}))
    assert out == []


def test_默认关_scene_filter原样(dbsetup):
    from app.memory.retrieve import _scene_filter
    rows = [{"source": "group_cognition", "sub_type": "char_stance", "group_id": G1}]
    assert _scene_filter(rows, None, None, None) == rows


# ───────────────────────── 两级闸（群列） ─────────────────────────

def test_群列关_即使全局开也不生成(dbsetup, monkeypatch):
    factory, _ = dbsetup
    _enable_flag(monkeypatch)
    # G3 的 cognition_enabled = False
    asyncio.run(gm.generate_char_cognitions(
        group_id=G3, user_id=USER, user_content="hi",
        replies=[{"character_id": CHAR_A, "content": "你好"}], name_map={CHAR_A: "小阳"},
    ))
    assert asyncio.run(_count_rows(factory, GroupCharCognition, group_id=G3)) == 0


def test_群列关_注入section返回空(dbsetup, monkeypatch):
    factory, _ = dbsetup
    _enable_flag(monkeypatch)
    out = asyncio.run(overlay.group_char_cognition_section(
        {"character_id": CHAR_A, "group_id": G3, "user_id": USER}, {}))
    assert out == []


# ───────────────────────── 私有/公开不串 ─────────────────────────

def test_私有认知仅owner可见_不跨角色(dbsetup, monkeypatch):
    factory, _ = dbsetup
    _enable_flag(monkeypatch)
    # 直接落库：小阳在 G1 的认知、小冰在 G1 的认知
    async def _seed():
        async with factory() as db:
            db.add(GroupCharCognition(group_id=G1, user_id=USER, character_id=CHAR_A,
                                      content="小阳认为应该去爬山", cognition_type="stance"))
            db.add(GroupCharCognition(group_id=G1, user_id=USER, character_id=CHAR_B,
                                      content="小冰觉得在家吃饭更好", cognition_type="stance"))
            await db.commit()
    asyncio.run(_seed())
    a_cog = asyncio.run(gm.recall_char_cognition(CHAR_A, G1))
    b_cog = asyncio.run(gm.recall_char_cognition(CHAR_B, G1))
    assert a_cog == ["小阳认为应该去爬山"]
    assert b_cog == ["小冰觉得在家吃饭更好"]
    # section 注入只含 owner 自己的认知
    out_a = asyncio.run(overlay.group_char_cognition_section(
        {"character_id": CHAR_A, "group_id": G1, "user_id": USER}, {}))
    out_b = asyncio.run(overlay.group_char_cognition_section(
        {"character_id": CHAR_B, "group_id": G1, "user_id": USER}, {}))
    assert "小阳认为应该去爬山" in out_a[0]
    assert "小冰觉得在家吃饭更好" not in out_a[0]
    assert "小冰觉得在家吃饭更好" in out_b[0]
    assert "小阳认为应该去爬山" not in out_b[0]


def test_共享记忆与逐角色认知不互串(dbsetup, monkeypatch):
    factory, _ = dbsetup
    _enable_flag(monkeypatch)
    async def _seed():
        async with factory() as db:
            db.add(GroupMemory(group_id=G1, user_id=USER, speaker_type="system",
                               content="用户说周末要去爬山", epistemic_status="FACT"))
            db.add(GroupCharCognition(group_id=G1, user_id=USER, character_id=CHAR_A,
                                      content="小阳的个人看法：爬山挺累的", cognition_type="stance"))
            await db.commit()
    asyncio.run(_seed())
    shared = asyncio.run(gm.recall_group_longterm(G1))
    cogs = asyncio.run(gm.recall_char_cognition(CHAR_A, G1))
    assert any("周末要去爬山" in s for s in shared)
    assert all("小阳的个人看法" not in s for s in shared)   # 共享记忆不含角色认知
    assert cogs == ["小阳的个人看法：爬山挺累的"]
    assert "周末要去爬山" not in cogs                       # 角色认知不含共享事实正文


# ───────────────────────── 预算封顶 + 跨群 ─────────────────────────

def test_单群预算封顶_第5次跳过(dbsetup, monkeypatch):
    factory, traces = dbsetup
    _enable_flag(monkeypatch)
    # 同角色同群、不同话题（user_content 不同 → topic_key 不同 → 不幂等去重），连发 5 轮
    for i in range(5):
        asyncio.run(gm.generate_char_cognitions(
            group_id=G1, user_id=USER, user_content=f"话题{i}",
            replies=[{"character_id": CHAR_A, "content": f"回应{i}"}], name_map={CHAR_A: "小阳"},
        ))
    rows = asyncio.run(_count_rows(factory, GroupCharCognition, group_id=G1, character_id=CHAR_A))
    assert rows == gm._CHAR_COG_PER_GROUP_DAILY  # 恰好 4 条
    # 第 5 次应记 budget_hit trace（per_group_cap）
    gen_traces = [t for t in traces if t[0] == "group_cognition_gen" and t[2].get("hit_budget")]
    assert any(t[2].get("source") == "per_group_cap" for t in gen_traces)


def test_跨群预算封顶_第11次跳过(dbsetup, monkeypatch):
    factory, traces = dbsetup
    _enable_flag(monkeypatch)
    # 单角色跨群：G1 写 4（单群上限）+ G2 写 4 + G4 写 2 = 10 条（跨群上限）；
    # G4 第 3 次（全局第 11 次）应被 cross_group_cap 拦截。
    for i in range(4):
        asyncio.run(gm.generate_char_cognitions(
            group_id=G1, user_id=USER, user_content=f"g1话题{i}",
            replies=[{"character_id": CHAR_A, "content": f"r{i}"}], name_map={CHAR_A: "小阳"}))
    for i in range(4):
        asyncio.run(gm.generate_char_cognitions(
            group_id=G2, user_id=USER, user_content=f"g2话题{i}",
            replies=[{"character_id": CHAR_A, "content": f"r{i}"}], name_map={CHAR_A: "小阳"}))
    for i in range(3):  # 第 3 次在 G4 应被跨群上限拦截
        asyncio.run(gm.generate_char_cognitions(
            group_id=G4, user_id=USER, user_content=f"g4话题{i}",
            replies=[{"character_id": CHAR_A, "content": f"r{i}"}], name_map={CHAR_A: "小阳"}))
    g1 = asyncio.run(_count_rows(factory, GroupCharCognition, group_id=G1, character_id=CHAR_A))
    g2 = asyncio.run(_count_rows(factory, GroupCharCognition, group_id=G2, character_id=CHAR_A))
    g4 = asyncio.run(_count_rows(factory, GroupCharCognition, group_id=G4, character_id=CHAR_A))
    assert g1 == 4 and g2 == 4 and g4 == 2  # 合计 10，第 11 次被跨群拦截
    assert any(t[2].get("source") == "cross_group_cap" for t in traces
               if t[0] == "group_cognition_gen" and t[2].get("hit_budget"))


# ───────────────────────── 幂等 ─────────────────────────

def test_同轮幂等_重复调度只写一条(dbsetup, monkeypatch):
    factory, _ = dbsetup
    _enable_flag(monkeypatch)
    kw = dict(group_id=G1, user_id=USER, user_content="同一个话题",
              replies=[{"character_id": CHAR_A, "content": "回应"}], name_map={CHAR_A: "小阳"},
              round_id="R1", topic_key="R1")
    asyncio.run(gm.generate_char_cognitions(**kw))
    asyncio.run(gm.generate_char_cognitions(**kw))  # 重复调度
    rows = asyncio.run(_count_rows(factory, GroupCharCognition, group_id=G1, character_id=CHAR_A, round_id="R1"))
    assert rows == 1  # 幂等：仅 1 条


def test_不同角色同轮各写各的(dbsetup, monkeypatch):
    factory, _ = dbsetup
    _enable_flag(monkeypatch)
    kw = dict(group_id=G1, user_id=USER, user_content="同一个话题",
              replies=[{"character_id": CHAR_A, "content": "A回应"}, {"character_id": CHAR_B, "content": "B回应"}],
              name_map={CHAR_A: "小阳", CHAR_B: "小冰"}, round_id="R2", topic_key="R2")
    asyncio.run(gm.generate_char_cognitions(**kw))
    a = asyncio.run(_count_rows(factory, GroupCharCognition, group_id=G1, character_id=CHAR_A))
    b = asyncio.run(_count_rows(factory, GroupCharCognition, group_id=G1, character_id=CHAR_B))
    assert a == 1 and b == 1


# ───────────────────────── LLM 失败静默 ─────────────────────────

def test_LLM失败静默_不写库不抛(dbsetup, monkeypatch):
    factory, _ = dbsetup
    _enable_flag(monkeypatch)
    async def _boom(*a, **k):
        raise RuntimeError("llm down")
    from app.agent import llm_client
    monkeypatch.setattr(llm_client, "chat_completion", _boom)
    # 不应抛异常
    asyncio.run(gm.generate_char_cognitions(
        group_id=G1, user_id=USER, user_content="hi",
        replies=[{"character_id": CHAR_A, "content": "回应"}], name_map={CHAR_A: "小阳"}))
    assert asyncio.run(_count_rows(factory, GroupCharCognition, group_id=G1)) == 0


# ───────────────────────── _scene_filter DM 过滤（纯函数表驱动） ─────────────────────────

def test_scene_filter_group_cognition_dm排除_group保留():
    from app.memory.retrieve import _scene_filter
    rows = [
        {"source": "group_cognition", "sub_type": "char_stance", "group_id": G1},
        {"source": "group", "sub_type": "group_summary", "group_id": G1},
        {"source": "group", "sub_type": "event", "group_id": G1},
        {"source": "memory", "sub_type": "x", "group_id": G1},
    ]
    # DM：group_cognition 被排除；group_summary 保留；其它保留
    dm = _scene_filter(rows, "dm", None, G1)
    assert all(r["source"] != "group_cognition" for r in dm)
    assert any(r["sub_type"] == "group_summary" for r in dm)
    # group 场景：group_cognition 不受影响（保留）
    grp = _scene_filter(rows, "group", None, G1)
    assert any(r["source"] == "group_cognition" for r in grp)
    # scene=None 原样返回
    assert _scene_filter(rows, None, None, None) == rows
    # exclude_sources 命中即剔除
    ex = _scene_filter(rows, None, {"group_cognition"}, None)
    assert all(r["source"] != "group_cognition" for r in ex)


# ───────────────────────── 注入观测 trace ─────────────────────────

def test_注入section_emit_inject_trace(dbsetup, monkeypatch):
    factory, traces = dbsetup
    _enable_flag(monkeypatch)
    async def _seed():
        async with factory() as db:
            db.add(GroupCharCognition(group_id=G1, user_id=USER, character_id=CHAR_A,
                                      content="小阳的看法", cognition_type="stance"))
            await db.commit()
    asyncio.run(_seed())
    asyncio.run(overlay.group_char_cognition_section(
        {"character_id": CHAR_A, "group_id": G1, "user_id": USER, "group_shared_fact": True}, {}))
    inj = [t for t in traces if t[0] == "group_cognition_inject"]
    assert inj and inj[0][2]["has_stance"] is True and inj[0][2]["has_shared"] is True


# ───────────────────────── 配额挤压实测（§9-2 未验证项） ─────────────────────────

def test_配额挤压_认知块单独budget且优先保低优先级块():
    """实测：逐角色认知注入占用 _SECTION_QUOTA_TOKENS["group_char_cognition"]（≈600 字），
    且其块优先级=3（默认），超出系统总硬顶时先裁剪低优先级块（群聊动态/共同经历等 priority=4），
    不会挤压主模板/高优先级块——即「知识不串线」的核心块不被牺牲。"""
    from app.agent.context_builder import (
        _apply_system_total_quota, _SECTION_QUOTA_TOKENS, _EST_CHARS_PER_TOKEN,
    )
    q_tokens = _SECTION_QUOTA_TOKENS["group_char_cognition"]
    q_chars = q_tokens * _EST_CHARS_PER_TOKEN
    # 构造一个超出系统硬顶的 system 多块（每块用可识别标记以便优先级判定）
    low_block = "【群聊动态】" + "x" * 6000          # priority 4 → 先裁
    low_block2 = "【共同经历】" + "x" * 6000         # priority 4 → 先裁
    main_block = "【系统指令】" + "m" * 6000         # priority 1 → 最后裁
    cog_block = "【我的群聊看法】" + "c" * q_chars    # 注入的认知块（priority 3）
    messages = [
        {"role": "system", "content": main_block},
        {"role": "system", "content": low_block},
        {"role": "system", "content": low_block2},
        {"role": "system", "content": cog_block},
        {"role": "user", "content": "hi"},
    ]
    before = sum(len(m["content"]) for m in messages if m["role"] == "system")
    _apply_system_total_quota(messages, character_id=CHAR_A)
    after = sum(len(m["content"]) for m in messages if m["role"] == "system")
    budget_chars = 9000 * _EST_CHARS_PER_TOKEN
    # 1) 总量被压到硬顶内
    assert after <= budget_chars, f"after={after} 应 <= {budget_chars}"
    # 2) 认知块（priority 3）在超额时不被牺牲——保留原样（单独 budget 由 section 侧裁剪，见另测）
    cog_now = next((m["content"] for m in messages if m["content"].startswith("【我的群聊看法】")), "")
    assert len(cog_now) == len(cog_block), "认知块（中优先级）超额时不应被系统总配额裁剪"
    # 3) 主模板（priority 1）不被牺牲
    main_now = next((m["content"] for m in messages if m["content"].startswith("【系统指令】")), "")
    assert len(main_now) == len(main_block), "主模板（高优先级）在超额时应保留"
    # 4) 低优先级块被裁剪（证明认知块挤压的是低优先级块而非核心块）
    low_now = next((m["content"] for m in messages if m["content"].startswith("【共同经历】")), "")
    assert len(low_now) < len(low_block2), "低优先级共同经历块应被超额裁剪"
    # 实测结论数值（用于回报，不强制）
    print(f"\n[配额实测] 认知块预算={q_tokens}token/{q_chars}字；"
          f"system 总字符 before={before} after={after} 硬顶={budget_chars}；"
          f"主模板保留={len(main_now)==len(main_block)} 低优先级被裁={len(low_now)<len(low_block2)}")


def test_配额_认知块多召回被单独裁剪():
    """recall 返回多条长认知时，section 输出被 _SECTION_QUOTA_TOKENS 裁剪到单块上限。"""
    from app.agent.context_builder import (
        _clip_text_to_quota, _SECTION_QUOTA_TOKENS, _EST_CHARS_PER_TOKEN,
    )
    q_chars = _SECTION_QUOTA_TOKENS["group_char_cognition"] * _EST_CHARS_PER_TOKEN
    many = ["长认知内容" * 50 for _ in range(6)]  # 6 条约 3600 字
    header = "【我的群聊看法】以下是我个人的看法，可能与群里的既定事实不同（以群共同记忆的 FACT 为准，不要与之矛盾）："
    text = _clip_text_to_quota(header + "\n" + "\n".join(f"- {c}" for c in many),
                               _SECTION_QUOTA_TOKENS["group_char_cognition"])
    assert len(text) <= q_chars
