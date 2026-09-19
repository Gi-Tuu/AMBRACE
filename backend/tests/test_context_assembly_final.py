# -*- coding: utf-8 -*-
"""「最终装配」级测试：append 分区真的进了 prompt + 结构性护栏（2026-09-18）。

背景：5 个 append 分区（``current_state_anchor`` / ``user_now`` / ``working_state`` /
``recall_capability`` / ``group_char_cognition``）此前「注册了但从未挂载」——builder 每轮
都跑、结果无人消费，等于永不注入（见 docs/context-order-convention.md §2.4）。本文件用
**真正的装配函数** ``build_context_legacy`` 断言这些块的文本确实出现在 ``context_messages``
里，并加一条**结构性护栏**：任何新增的 append 分区若忘了在 ``legacy.py`` 挂载，本文件直接变红。

覆盖：
1. 5 个分区的挂载点断言（块文本真的进了 system 消息）+ 闸门关/无数据时的零行为断言；
2. 挂载相对顺序断言（位置即语义）+ user 消息仍是最后一条（红线②）；
3. 真实 builder 端到端：``recall_capability``（闸门开/关）、``working_state``（有行/无行）；
4. 结构性护栏：所有 enabled 的 append 分区必须「已挂载」或「在显式豁免清单里」；
5. ``world_facts`` 槽位在 ``chat_history`` 之前（模板级 + 装配产物级）。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio
import json
import re
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.agent.context as _ctx  # noqa: F401  触发所有 section_*.py 注册
from app.agent.context import legacy as legacy_mod
from app.agent.context.sections import TARGET_APPEND, TARGET_TEMPLATE, get_sections

LEGACY_PY = Path(__file__).resolve().parents[1] / "app" / "agent" / "context" / "legacy.py"
CONTEXT_BUILDER_PY = Path(__file__).resolve().parents[1] / "app" / "agent" / "context_builder.py"

# 本批挂载的 5 个分区（顺序 = legacy.py 挂载链中的先后，用于相对顺序断言）
MOUNTED_KEYS = (
    "current_state_anchor",
    "user_now",
    "working_state",
    "recall_capability",
    "group_char_cognition",
)

# 显式豁免清单（走「工具/资源声明」专用通道，不是 ``_sv[key]`` 直挂，属"已挂载"）：
# mcp_tools / mcp_resources 由 legacy.py 用 ``_section_values.get("mcp_*")`` 取出后经
# ``mcp_*_blocks`` 变量在主模板块之后统一 append（承载流式不注入 / 配额 / flag-off 内联兜底
# 等特殊逻辑），因此不出现在 ``if _sv and "<key>" in _sv`` 链里。
EXEMPT_APPEND_KEYS = {"mcp_tools", "mcp_resources"}


# ────────────────────────────────────────────────────────────── 结构性护栏用的源码解析


def _append_mount_keys() -> set[str]:
    """从 legacy.py 源码抽取「主模板块之后」被消费的分区 key（= 真实 append 挂载点）。

    与 scripts/audit_context_order.py 的口径一致（落位由 legacy.py 源码先后决定，
    registry order 不算），但更严格：只认主模板块之后的消费点。
    """
    lines = LEGACY_PY.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if 'state["context_messages"] = [' in ln)
    pats = (
        re.compile(r'"([a-z_]+)"\s+in\s+_sv'),
        re.compile(r'_section_values\.get\("([a-z_]+)"'),
    )
    keys: set[str] = set()
    for ln in lines[start:]:
        for pat in pats:
            keys.update(pat.findall(ln))
    return keys


# ────────────────────────────────────────────────────────────── 装配夹具


@pytest.fixture(scope="session")
def asm_db(tmp_path_factory):
    """会话级临时库（pytest 托管 tmp_path_factory）：User + AICharacter(13) 一行就够。

    装配函数只查这两张表就能走到 append 链；其余分区走 try/except 内联兜底（空库返回空）。
    """
    db_file = (tmp_path_factory.mktemp("ctx") / "ctx.db").as_posix()
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        from app.models.character import AICharacter
        from app.models.user import User

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add(User(id=1, username="u1", nickname="用户"))
            db.add(AICharacter(
                id=13, user_id=1, name="酱", personality="温柔",
                chat_style="口语化", relation_type="朋友", is_active=True,
            ))
            await db.commit()

    asyncio.run(_init())
    old = legacy_mod.async_session_factory
    legacy_mod.async_session_factory = factory
    yield factory
    legacy_mod.async_session_factory = old
    asyncio.run(engine.dispose())


def _assemble(sv: dict) -> list[dict]:
    """跑真正的装配函数，返回 state["context_messages"]。

    ``relationship`` 总是带上（注册表路径必然有该键）→ 跳过 assemble_persona_context；
    ``_trim`` 显式传入 → 跳过 _is_hot_character；两者都只是为了去掉与挂载无关的 DB 查询。
    """
    from app.agent.context_builder import _trim_limits

    state = {
        "user_message": "在吗",
        "character_id": 13,
        "user_id": 1,
        "session_id": 1,
        "intent": "",
        "retrieved_memories": [],
        "context_messages": [],
        "character_info": {},
        "ai_response": "",
        "should_update_memory": False,
        "new_memories": [],
        "emotional_state": "",
        "bio_update": None,
        "status_update": None,
        "lang": "zh",
    }
    out = asyncio.run(legacy_mod.build_context_legacy(
        state, _section_values={"relationship": "", **sv}, _trim=_trim_limits(True),
    ))
    return out["context_messages"]


def _system_texts(messages: list[dict]) -> list[str]:
    return [m["content"] for m in messages if m["role"] == "system"]


# ────────────────────────────────────────────────────────────── 1. 挂载点断言


@pytest.mark.parametrize("key", MOUNTED_KEYS)
def test_mounted_append_block_reaches_prompt(asm_db, key):
    """闸门开/有数据：builder 返回的块文本必须真的出现在 context_messages 的 system 消息里。"""
    marker = f"@@{key}-BLOCK@@"
    texts = _system_texts(_assemble({key: [marker]}))
    assert any(marker in t for t in texts), f"{key} 未进入 context_messages（挂载缺失）"


@pytest.mark.parametrize("key", MOUNTED_KEYS)
def test_append_block_absent_when_empty_or_missing(asm_db, key):
    """闸门关/无数据（空列表）或键缺失（纯 legacy 路径）→ 不追加任何块（零行为变化）。"""
    baseline = len(_system_texts(_assemble({})))
    assert len(_system_texts(_assemble({key: []}))) == baseline, f"{key} 空列表仍追加了块"
    assert len(_system_texts(_assemble({}))) == baseline


def test_mounted_blocks_keep_relative_order_and_user_last(asm_db):
    """位置即语义：5 块按挂载链先后出现；user 消息仍是最后一条（红线②）。"""
    messages = _assemble({k: [f"@@{k}@@"] for k in MOUNTED_KEYS})
    joined = "\n".join(_system_texts(messages))
    positions = [joined.index(f"@@{k}@@") for k in MOUNTED_KEYS]
    assert positions == sorted(positions), f"挂载顺序错乱: {list(zip(MOUNTED_KEYS, positions))}"
    assert messages[-1]["role"] == "user", "user 消息必须是最后一条"


def test_recall_capability_mounted_between_search_and_group(asm_db):
    """recall_capability 紧随 search_capability、在 group_dynamics 之前。"""
    joined = "\n".join(_system_texts(_assemble({
        "search_capability": ["@@SC@@"],
        "recall_capability": ["@@RC@@"],
        "group_dynamics": ["@@GD@@"],
    })))
    assert joined.index("@@SC@@") < joined.index("@@RC@@") < joined.index("@@GD@@")


# ────────────────────────────────────────────────────────────── 2. 真实 builder 端到端


def test_recall_capability_real_builder_injected_when_flag_on(asm_db, monkeypatch):
    """真实 builder + 真实装配：flag memory_recall_second_hop 开 → 规则声明进 prompt。"""
    from app.agent.context import section_memories as smem
    from app.agent.loop import AGENT_FLAGS

    monkeypatch.setitem(AGENT_FLAGS, "memory_recall_second_hop", True)
    blocks = asyncio.run(smem.recall_capability_section({"character_id": 13, "user_id": 1}, {}))
    assert len(blocks) == 1 and "【记忆调取规则】" in blocks[0]

    joined = "\n".join(_system_texts(_assemble({
        "search_capability": ["@@SC@@"],
        "recall_capability": blocks,
        "group_dynamics": ["@@GD@@"],
    })))
    assert "【记忆调取规则】" in joined
    assert joined.index("@@SC@@") < joined.index("【记忆调取规则】") < joined.index("@@GD@@")


def test_recall_capability_real_builder_absent_when_flag_off(asm_db, monkeypatch):
    """flag 关（默认）→ builder 返空 → 不追加任何块（与修复前逐字节一致）。"""
    from app.agent.context import section_memories as smem
    from app.agent.loop import AGENT_FLAGS

    monkeypatch.setitem(AGENT_FLAGS, "memory_recall_second_hop", False)
    blocks = asyncio.run(smem.recall_capability_section({"character_id": 13, "user_id": 1}, {}))
    assert blocks == []
    baseline = len(_system_texts(_assemble({})))
    out = _assemble({"recall_capability": blocks})
    assert len(_system_texts(out)) == baseline
    assert not any("【记忆调取规则】" in t for t in _system_texts(out))


def test_working_state_real_builder_injected(asm_db, monkeypatch):
    """真实 builder + 真实装配：灰度角色 char13 有 working_state 行 → 【工作记忆】进 prompt。

    section 内部是函数内 ``from app.db.database import async_session_factory``，故此处
    再打一次桩（函数级 monkeypatch 自动还原），让它与本文件的临时库同源。
    """
    from app.agent.context import section_working_state as sws
    from app.db import database as db_mod
    from app.models.memory import Memory

    monkeypatch.setattr(db_mod, "async_session_factory", asm_db)

    async def _seed():
        async with asm_db() as db:
            db.add(Memory(
                user_id=1, character_id=13, memory_type="working_state",
                content=json.dumps({
                    "version": 1,
                    "ongoing": [{"topic": "准备考试", "detail": "下周", "evidence_ids": [1]}],
                    "open_questions": [], "relationship_notes": [],
                }, ensure_ascii=False),
                scope="private", source="system", importance=60.0,
            ))
            await db.commit()

    asyncio.run(_seed())
    blocks = asyncio.run(sws.working_state_section(
        {"character_id": 13, "user_id": 1, "session_id": 1}, {},
    ))
    assert len(blocks) == 1 and "【工作记忆" in blocks[0]

    hit = [m for m in _assemble({"working_state": blocks})
           if m["role"] == "system" and "【工作记忆" in m["content"]]
    assert len(hit) == 1
    assert "正在进行：准备考试（下周）" in hit[0]["content"]

    # 清理种子行，避免污染同库其它用例
    async def _cleanup():
        async with asm_db() as db:
            for row in (await db.execute(
                select(Memory).where(Memory.character_id == 13)
            )).scalars().all():
                await db.delete(row)
            await db.commit()

    asyncio.run(_cleanup())


# ────────────────────────────────────────────────────────────── 3. 结构性护栏


def test_every_enabled_append_section_is_mounted_or_exempt():
    """护栏：新增 append 分区却忘了在 legacy.py 挂载 → 本条直接变红。

    判定 = ``get_sections()`` 里 target=append 且 enabled 的 key，必须落在
    legacy.py 主模板块之后的消费点里，或在显式豁免清单里（见 EXEMPT_APPEND_KEYS）。
    """
    mounts = _append_mount_keys()
    missing = [
        s.key for s in get_sections()
        if s.target == TARGET_APPEND and s.enabled
        and s.key not in mounts and s.key not in EXEMPT_APPEND_KEYS
    ]
    assert not missing, (
        f"以下 append 分区注册了但 legacy.py 从不消费（算了就丢），请在 legacy.py 挂载或加入豁免清单: {missing}"
    )


def test_guardrail_is_not_vacuous():
    """护栏自检：本批 5 个 key 必须来自真实挂载点（防止靠"全豁免"把护栏架空）。"""
    mounts = _append_mount_keys()
    assert mounts, "legacy.py 挂载点解析为空（解析逻辑失效）"
    for key in MOUNTED_KEYS:
        assert key in mounts, f"{key} 不在 legacy.py 挂载点里"


def test_exempt_keys_really_consumed_by_legacy():
    """豁免清单复核：mcp_tools / mcp_resources 确实走工具通道被消费（不是漏挂）。"""
    src = LEGACY_PY.read_text(encoding="utf-8")
    for key in EXEMPT_APPEND_KEYS:
        assert f'_section_values.get("{key}"' in src, f"{key} 已不在 legacy 取值点（豁免需复核）"
        assert f"for _mcp_b in {key}_blocks:" in src, f"{key} 未走 _blocks 通道 append（豁免需复核）"


# ────────────────────────────────────────────────────────────── 4. world_facts 位置


def test_world_facts_slot_precedes_chat_history_in_template():
    """模板级：{world_facts} 必须排在 {chat_history} 之前（状态/条件前置）。"""
    from app.agent.context_builder import SYSTEM_PROMPT_TEMPLATE

    assert SYSTEM_PROMPT_TEMPLATE.index("{world_facts}") < SYSTEM_PROMPT_TEMPLATE.index("{chat_history}")


def test_world_facts_block_precedes_chat_history_in_assembled_prompt(asm_db):
    """装配产物级：主模板块里「当前世界状态」整块在「最近的对话上下文」之前。"""
    first = _system_texts(_assemble({"world_facts": "@@WF@@", "chat_history": "@@CH@@"}))[0]
    assert "## 当前世界状态" in first and "## 最近的对话上下文" in first
    assert first.index("@@WF@@") < first.index("@@CH@@")


# ──────────────────────────────────────────── 3. 落位取舍固化（2026-09-18 拍板）


def test_trio_sits_after_mcp_before_materials(asm_db):
    """现状三连落位取舍固化（2026-09-18 拍板：采用 Codex 推荐）。

    固定次序：MCP 能力声明 → 现状三连（anchor → user_now → working_state）→ 素材块（织库/设定…）
    → …… → user 消息恒为最后一条。

    理由：①「MCP 最前」是既有约定（能力声明＝条件类）；②三连属状态类，必须排在**全部素材块之前**；
    ③三连变化频率低于每轮，靠前不显著伤缓存前缀（见 docs/context-order-convention.md §0.1）。
    本用例把该取舍从「口头约定」变成「会变红的不变式」，防日后同类漂移。
    """
    keys = ("mcp_tools", "current_state_anchor", "user_now", "working_state", "weave_full", "lorebook")
    sv = {k: [f"@@{k.upper()}@@"] for k in keys}
    messages = _assemble(sv)
    joined = "\n".join(_system_texts(messages))
    pos = [joined.index(f"@@{k.upper()}@@") for k in keys]
    assert pos == sorted(pos), f"落位漂移（期望 MCP→三连→素材）: {list(zip(keys, pos))}"
    assert messages[-1]["role"] == "user", "user 消息必须是最后一条"


# ──────────────────────────────── 4. 插件 hook 越位治理（方案 C，2026-09-18）


def _fake_plugin(name: str, hooks: dict):
    from app.plugins import registry

    registry._loaded[name] = {"info": {"name": name}, "hooks": hooks, "actions": {}, "router": None}
    registry._enabled[name] = True


def _drop_plugin(name: str):
    from app.plugins import registry

    registry._loaded.pop(name, None)
    registry._enabled.pop(name, None)


def test_context_inject_block_lands_before_user(asm_db):
    """方案 C 主断言：插件 context_inject 追加的块必须落在宿主 user **之前**（红线②）。

    dsh 设计 §4.3 A 组：位移后插件块落在 continue_payload 之前；并做「非空跑自检」——
    假插件 hook 必须真的被调用过，否则「user 最后」是空断言（原盲区）。
    """
    calls = {"n": 0}

    async def _hook(ctx):
        calls["n"] += 1
        ctx["context_messages"].append({"role": "system", "content": "@@PLUGIN@@"})

    _fake_plugin("_ctx_place", {"context_inject": [_hook]})
    try:
        msgs = _assemble({"continue_payload": ["@@CONTINUE@@"], "weave_full": ["@@WEAVE@@"]})
    finally:
        _drop_plugin("_ctx_place")

    assert calls["n"] == 1, "假插件 hook 未被调用 → 断言空跑"
    joined = "\n".join(m["content"] for m in msgs if m.get("role") == "system")
    assert "@@PLUGIN@@" in joined
    plugin_idx = next(i for i, m in enumerate(msgs) if "@@PLUGIN@@" in (m.get("content") or ""))
    last_user = max(i for i, m in enumerate(msgs) if m.get("role") == "user")
    assert msgs[-1]["role"] == "user", "宿主 user 必须是最后一条"
    assert plugin_idx < last_user, "插件块落在 user 之后（越位未修）"
    assert joined.index("@@PLUGIN@@") < joined.index("@@CONTINUE@@"), "插件块必须在【系统指令】之前"


def test_enforce_user_message_last_moves_strays(asm_db):
    """护栏纯函数：越位块按原相对顺序归位到宿主 user 之前；已归位时任调零移动（幂等）。"""
    from app.agent.context_builder import _enforce_user_message_last

    msgs = [
        {"role": "system", "content": "s0"},
        {"role": "user", "content": "host"},
        {"role": "system", "content": "stray1"},
        {"role": "system", "content": "stray2"},
    ]
    assert _enforce_user_message_last(msgs, user_index=1) == 2
    assert [m["content"] for m in msgs] == ["s0", "stray1", "stray2", "host"]
    assert _enforce_user_message_last(msgs, user_index=1) == 0


def test_enforce_user_message_last_ignores_forged_user(asm_db):
    """锚点用宿主下标而非「最后一条 role=user」：插件伪造的 user 消息只被移位、不被当锚点。"""
    from app.agent.context_builder import _enforce_user_message_last

    host = {"role": "user", "content": "host"}
    msgs = [
        {"role": "system", "content": "s0"},
        host,
        {"role": "system", "content": "stray"},
        {"role": "user", "content": "FORGED"},
    ]
    assert _enforce_user_message_last(msgs, user_index=1) == 2
    assert msgs[-1] is host, "宿主的 user 消息必须回到最后"
    assert msgs[2]["content"] == "FORGED", "伪造 user 只移位、不删改"


def test_enforce_user_message_last_noop_cases(asm_db):
    """零行为变化面：空列表 / 无 user / user 已在末尾 → 全部零移动。"""
    from app.agent.context_builder import _enforce_user_message_last

    assert _enforce_user_message_last([], user_index=None) == 0
    assert _enforce_user_message_last([{"role": "system", "content": "s"}]) == 0
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    assert _enforce_user_message_last(msgs, user_index=1) == 0
    assert [m["content"] for m in msgs] == ["s", "u"]


def test_source_order_hooks_before_user_guard_after():
    """源码级回归护栏：hook 调用必须在 user append **之前**；护栏在 user 之后、配额之前。"""
    lines = LEGACY_PY.read_text(encoding="utf-8").splitlines()

    def _first(pred):
        for _i, _ln in enumerate(lines, 1):
            if pred(_ln):
                return _i
        return None

    ci = _first(lambda ln: 'run_hook("context_inject"' in ln)
    ips = _first(lambda ln: "inject_prompt_skill(state)" in ln)
    user_ln = _first(lambda ln: '"role": "user"' in ln)
    enf = _first(lambda ln: "_enforce_user_message_last(" in ln)
    quota = _first(lambda ln: "_apply_system_total_quota(state[" in ln)
    assert None not in (ci, ips, user_ln, enf, quota), (ci, ips, user_ln, enf, quota)
    assert ci < user_ln and ips < user_ln, "插件 hook 调用必须早于 user append"
    assert user_ln < enf < quota, "护栏必须在 user append 之后、配额裁剪之前"


# ────────────────────────────────────────────────────────────── 6. template 槽位护栏（2026-09-19）
# 与 §3 的 append 护栏同族：template 分区的 slot 若未出现在 SYSTEM_PROMPT_TEMPLATE 里，
# ``str.format(**kwargs)`` 会**静默丢弃**该值（算了就丢）。append 侧护栏只扫 TARGET_APPEND，
# 结构上盖不住 template 槽（2026-09-19 只读审计发现，见 output/AMBRACE_上下文分区顺序审计_dsh_20260919.md）。

# 棘轮清单（只减不增）：注册为 template 槽但模板无占位。当前已清空——
# 2026-09-19 storyline_status 已落位（模板「你们最近的剧情」段新增 {storyline_status}，
# legacy.py 同步做哨兵「无」归一），清单清空；此后新增 template 槽缺占位直接判红。
EXEMPT_TEMPLATE_SLOTS = set()


def _template_placeholders() -> set[str]:
    """从 context_builder.py 的 SYSTEM_PROMPT_TEMPLATE 字面量抽取 ``{槽}`` 占位名。"""
    src = CONTEXT_BUILDER_PY.read_text(encoding="utf-8")
    m = re.search(r'SYSTEM_PROMPT_TEMPLATE\s*=\s*r?(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')', src, re.S)
    assert m, "未能定位 SYSTEM_PROMPT_TEMPLATE 字面量（解析逻辑失效）"
    body = m.group(1) if m.group(1) is not None else (m.group(2) or "")
    return set(re.findall(r"\{([a-z_][a-z0-9_]*)\}", body))


def test_every_template_section_has_placeholder_or_exempt():
    """护栏：enabled 的 template 分区，其 slot 必须在 SYSTEM_PROMPT_TEMPLATE 里有占位。

    否则 ``.format()`` 静默丢弃该分区产出 —— 与 09-18 append 侧「算了就丢」同类缺陷。
    """
    placeholders = _template_placeholders()
    assert placeholders, "模板占位解析为空（解析逻辑失效）"
    missing = [
        s.key for s in get_sections()
        if s.target == TARGET_TEMPLATE and s.enabled and s.slot
        and s.slot not in placeholders and s.slot not in EXEMPT_TEMPLATE_SLOTS
    ]
    assert not missing, (
        "以下 template 分区注册了 slot 但 SYSTEM_PROMPT_TEMPLATE 无对应占位"
        f"（.format() 会静默丢弃，算了就丢）: {missing}"
    )


def test_template_slot_guard_is_not_vacuous():
    """护栏自检：占位解析必须含已知真实槽；棘轮清单不得残留已注销的 slot。"""
    placeholders = _template_placeholders()
    for known in ("memories", "chat_history", "world_facts", "current_time"):
        assert known in placeholders, f"模板占位解析漏掉了已知槽 {known}"
    registered = {s.slot for s in get_sections() if s.target == TARGET_TEMPLATE}
    assert EXEMPT_TEMPLATE_SLOTS <= registered, (
        f"棘轮清单里的 slot 已不存在，请清理: {EXEMPT_TEMPLATE_SLOTS - registered}"
    )
