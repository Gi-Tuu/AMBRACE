# -*- coding: utf-8 -*-
"""批 4 M2-b1（2026-10-01）：念头池素材真进 prompt——flag ＋ 注入分区 ＋ 取一条 ＋ 三档释放接线。

派单：``output/AMBRACE_批4M2b1_注入生效_派单_转发用_20261001.md``
设计稿：``output/AMBRACE_批4_念头池T2_详细设计_v1_20260929.md`` §2.6 / §3 / §4 / §7 M2 行

钉住四件（改任一即红）：
1. **flag 关＝逐字节旧 prompt 且不查库**（设计 §4「关＝逐字节旧行为」四条硬保证之首）：
   ``thought_pool_v1`` 关或角色未命中灰度 ⇒ 取一条/释放/注入每个入口**一次 SQL 都不发**；
   ``generate_proactive_event`` 的 ``thought`` 缺省 None ⇒ prompt 与改前逐字节相同；
2. **取一条**（§2.6）：flag 开 ∧ 白名单角色 ⇒ 从池取**最咸**的一条活跃念头（spent/faded 不取）；
3. **三档释放**（§2.3）：``settle_release`` 用 M2-a 的 ``apply_release`` 写回，spent/told_flat/never_told
   三档正确，且 ``spent_at`` 非空幂等跳过；
4. **注入分区**（§2.6）：``thought_pool`` append 分区注册（order 70，保留空档段），注入体**不写元叙述**；
   聊天侧块插在 continue_payload（【系统指令】）之前（红线②：诉求/指令恒最后）。

口径与纪律：临时库一律 pytest ``tmp_path`` 私有 SQLite（``_dbclone`` 克隆模板库）；全程不碰
backend/data 生产库、不调模型、不走网络。LLM 调用用 monkeypatch 桩替身（只捕获 prompt，不发请求）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import select

from app.application import thought_pool_service as svc
from app.application.flag_catalog import FLAG_CATALOG
from app.domain.thought import dynamics as dyn
from app.flags.agent_flags import AGENT_FLAGS

_NOW = datetime(2026, 10, 1, 3, 0)          # naive UTC ＝北京 2026-10-01 11:00
_CHAR, _USER = 13, 1                         # char 13 在灰度白名单内
_OUT_CHAR = 999                              # 白名单外角色


# ══════════════════════════════════════════════ 1. flag 登记（行为端 + 展示端）

def test_v1_flag_registered_default_false():
    """``thought_pool_v1`` 在 AGENT_FLAGS 登记且默认 False（未登记 ⇒ runtime 开了也不生效）。"""
    assert svc.V1_FLAG_KEY == "thought_pool_v1"
    assert svc.V1_FLAG_KEY in AGENT_FLAGS, "新 flag 未登记进 AGENT_FLAGS（flag_service 只合并已登记键）"
    assert AGENT_FLAGS[svc.V1_FLAG_KEY] is False, "thought_pool_v1 默认必须 False（关＝逐字节旧行为）"
    assert svc.v1_enabled() is False


def test_v1_flag_catalog_metadata():
    """展示端登记：组 outreach_natural、order 15、visible=False、文案含「默认关闭」且无实现术语。"""
    assert svc.V1_FLAG_KEY in FLAG_CATALOG, "新 flag 未登记进 flag_catalog（App 里看不到＝守护测试红）"
    row = FLAG_CATALOG[svc.V1_FLAG_KEY]
    assert row["group"] == "outreach_natural"
    assert row["order"] == 15
    assert row["visible"] is False
    assert "默认关闭" in row["desc_zh"]
    # 文案禁出现实现术语（test_flag_catalog_metadata.py:71,82 同款口径）
    for term in ("flag", "prompt", "DB", "thought_pool", "念头池", "SQL"):
        assert term not in row["desc_zh"] and term not in row["title_zh"], (
            f"flag 文案泄漏实现术语 {term}（设计 §8 不做清单第 7 条）"
        )


# ══════════════════════════════════════════════ 2. 灰度白名单（照 pacing 范式）

def test_gray_whitelist_is_two_chars_including_13():
    """灰度＝白名单 2 角色（设计 §4 建议含 char 13，与既有节律/驱力灰度同角色便于对照）。"""
    assert svc.THOUGHT_POOL_GRAY_CHARS == frozenset({13, 14})
    assert len(svc.THOUGHT_POOL_GRAY_CHARS) == 2
    assert 13 in svc.THOUGHT_POOL_GRAY_CHARS


@pytest.mark.parametrize("cid,expect", [
    (13, True), (14, True),          # 白名单内
    (999, False), (1, False),        # 白名单外
    (None, False), ("x", False),     # 非法 ⇒ fail-closed
])
def test_gray_hit(cid, expect):
    assert svc.thought_pool_gray_hit(cid) is expect


def test_v1_allowed_requires_flag_and_gray(monkeypatch):
    """准入＝flag 开 ∧ 灰度命中；任一不满足 ⇒ False（且该判定**不查库**）。"""
    assert svc.thought_pool_v1_allowed(_CHAR) is False           # flag 关
    monkeypatch.setitem(AGENT_FLAGS, svc.V1_FLAG_KEY, True)
    assert svc.thought_pool_v1_allowed(_CHAR) is True            # flag 开 + 白名单内
    assert svc.thought_pool_v1_allowed(_OUT_CHAR) is False       # flag 开 + 白名单外


# ══════════════════════════════════════════════ 3. flag 关＝一次 SQL 都不发

class _NoSQLDB:
    """假 session：任何 execute 都算「发了 SQL」⇒ 直接失败（用于钉「关＝不查库」）。"""

    def __init__(self):
        self.executed = 0

    async def execute(self, *a, **k):
        self.executed += 1
        raise AssertionError("flag 关时不应发任何 SQL")

    def add(self, obj):
        raise AssertionError("flag 关时不应写库")

    async def flush(self, *a, **k):
        raise AssertionError("flag 关时不应 flush")


def test_fetch_one_thought_no_sql_when_flag_off():
    """flag 关 ⇒ fetch_one_thought 首行即返回 None，一次 SQL 都不发（设计 §4 硬保证 1）。"""
    db = _NoSQLDB()
    out = asyncio.run(svc.fetch_one_thought(db, _CHAR, _USER))
    assert out is None
    assert db.executed == 0


def test_fetch_one_thought_no_sql_when_char_not_gray(monkeypatch):
    """flag 开但角色未命中灰度 ⇒ 同样一次 SQL 都不发（先判 flag／灰度再查库）。"""
    monkeypatch.setitem(AGENT_FLAGS, svc.V1_FLAG_KEY, True)
    db = _NoSQLDB()
    out = asyncio.run(svc.fetch_one_thought(db, _OUT_CHAR, _USER))
    assert out is None
    assert db.executed == 0


def test_settle_release_no_sql_when_flag_off():
    """flag 关 ⇒ settle_release 首行即返回 {}，一次 SQL 都不发。"""
    db = _NoSQLDB()
    out = asyncio.run(svc.settle_release(db, 1, sent_ok=True, replied_within_window=True))
    assert out == {}
    assert db.executed == 0


def test_section_no_sql_when_flag_off():
    """flag 关 ⇒ thought_pool_section 返回空串且不查库（缓存进 state 亦为空）。"""
    from app.agent.context.sections import thought_pool_section, _THOUGHT_POOL_STATE_KEY
    state = {"character_id": _CHAR, "user_id": _USER}
    text = asyncio.run(thought_pool_section(state, {}))
    assert text == ""
    assert state[_THOUGHT_POOL_STATE_KEY] == ""


def test_inject_block_noop_when_flag_off():
    """flag 关 ⇒ _inject_thought_pool_block 直接返回，消息结构逐字不变。"""
    from app.agent.context_builder import _inject_thought_pool_block
    msgs = [{"role": "system", "content": "旧块"}, {"role": "user", "content": "在吗"}]
    state = {"character_id": _CHAR, "user_id": _USER, "context_messages": msgs}
    before = [dict(m) for m in msgs]
    asyncio.run(_inject_thought_pool_block(state))
    assert state["context_messages"] == before, "flag 关时消息结构必须逐字不变"


# ══════════════════════════════════════════════ 4. flag 关＝逐字节旧 prompt（主动侧）

def _capture_prompt(monkeypatch):
    """monkeypatch 掉 LLM 调用，捕获 generate_proactive_event 实际拼出的 prompt。"""
    from app.scheduling import message_generator as mg
    captured: dict = {}

    async def _fake_gen(messages, character_id, user_id, **kw):
        captured["messages"] = messages
        # 返回一条合规的可见文本，让生成链正常收尾（不发网络请求）
        return "今天把阳台的茉莉换了盆，土是新的，叶子精神了不少。", ""

    monkeypatch.setattr(mg, "_gen_with_reasoning", _fake_gen)
    return captured


def test_proactive_prompt_byte_identical_when_thought_absent(monkeypatch):
    """★ 核心等价断言：``thought`` 缺省（None）⇒ prompt 与显式不传逐字节相同，且不含念头素材块。"""
    from app.scheduling.message_generator import generate_proactive_event
    cap = _capture_prompt(monkeypatch)

    common = dict(
        character_name="小暖", character_bio="温柔", character_personality="体贴",
        character_id=_CHAR, user_id=_USER, current_status="在阳台", user_name="主人",
        last_context="上次聊到养花", previous_messages="", idle_minutes=120,
        behavior="status_update", return_reasoning=False,
    )
    asyncio.run(generate_proactive_event(**common))                 # 不传 thought（旧调用点）
    prompt_absent = cap["messages"][1]["content"]
    asyncio.run(generate_proactive_event(**common, thought=None))   # 显式 None（flag 关时 arbiter 传的值）
    prompt_none = cap["messages"][1]["content"]

    assert prompt_absent == prompt_none, "thought 缺省与显式 None 必须逐字节相同"
    # flag 关 ⇒ arbiter 恒传 None ⇒ 素材块绝不出现
    assert "可自然聊起的一件事" not in prompt_absent
    assert "惦记" not in prompt_absent


def test_proactive_prompt_splices_thought_when_present(monkeypatch):
    """flag 开（arbiter 取到念头）⇒ thought 作为**素材**拼进 prompt（与 outreach 同性质）。"""
    from app.scheduling.message_generator import generate_proactive_event
    cap = _capture_prompt(monkeypatch)
    asyncio.run(generate_proactive_event(
        character_name="小暖", character_bio="温柔", character_personality="体贴",
        character_id=_CHAR, user_id=_USER, current_status="在阳台", user_name="主人",
        last_context="上次聊到养花", previous_messages="", idle_minutes=120,
        behavior="status_update", return_reasoning=False,
        thought="今天把阳台的茉莉换了盆",
    ))
    prompt = cap["messages"][1]["content"]
    assert "今天把阳台的茉莉换了盆" in prompt, "念头素材必须进 prompt"
    assert "可自然聊起的一件事" in prompt or "惦记" in prompt
    # 不写元叙述（设计 §8 不做清单第 7 条）
    assert "条念头" not in prompt and "念头池" not in prompt


# ══════════════════════════════════════════════ 5. 取一条 + 三档释放（真库读写，_dbclone）

@pytest.fixture()
def pool_env(tmp_path):
    from _dbclone import clone_engine, make_session_factory
    from app.models.character import AICharacter
    from app.models.user import User

    engine = clone_engine(tmp_path / "pool_m2b.db")
    factory = make_session_factory(engine)

    async def _init():
        async with factory() as db:
            db.add(User(id=_USER, username="tp_m2b", nickname="主人"))
            db.add(AICharacter(id=_CHAR, user_id=_USER, name="小暖", is_active=True))
            await db.commit()

    asyncio.run(_init())
    yield factory
    engine.sync_engine.dispose()


def _seed_thought(factory, *, rid=None, text="换了盆的茉莉", status=dyn.STATUS_SPARK,
                  salt=1.0, tell_count=0, spent_at=None, char=_CHAR, user=_USER):
    from app.models.character import ThoughtPool

    async def _run():
        async with factory() as db:
            row = ThoughtPool(
                character_id=char, user_id=user, thought_kind="", text=text,
                source_type="activity", source_ref=str(rid or hash(text) % 10000),
                text_hash="h" + str(abs(hash(text)) % 10 ** 8), status=status,
                salt=salt, novelty=0.5, hit_sources='["activity"]', tell_count=tell_count,
                created_at=_NOW, last_hit_at=_NOW, spent_at=spent_at,
            )
            if rid is not None:
                row.id = rid
            db.add(row)
            await db.commit()
            return row.id
    return asyncio.run(_run())


def test_fetch_one_returns_saltiest_active(pool_env, monkeypatch):
    """flag 开 ∧ 白名单 ⇒ 取**最咸**的一条活跃念头（salt 降序）。"""
    monkeypatch.setitem(AGENT_FLAGS, svc.V1_FLAG_KEY, True)
    _seed_thought(pool_env, rid=1, text="淡的", salt=0.5)
    _seed_thought(pool_env, rid=2, text="咸的", salt=3.0)

    async def _run():
        async with pool_env() as db:
            return await svc.fetch_one_thought(db, _CHAR, _USER)
    got = asyncio.run(_run())
    assert got is not None and got["text"] == "咸的" and got["id"] == 2


def test_fetch_one_skips_spent_and_faded(pool_env, monkeypatch):
    """终态（spent/faded）不参与选择——只取 ACTIVE_STATUSES。"""
    monkeypatch.setitem(AGENT_FLAGS, svc.V1_FLAG_KEY, True)
    _seed_thought(pool_env, rid=1, text="已释放", status=dyn.STATUS_SPENT, salt=9.0, spent_at=_NOW)
    _seed_thought(pool_env, rid=2, text="已淡去", status=dyn.STATUS_FADED, salt=8.0)
    _seed_thought(pool_env, rid=3, text="活跃", status=dyn.STATUS_SPARK, salt=1.0)

    async def _run():
        async with pool_env() as db:
            return await svc.fetch_one_thought(db, _CHAR, _USER)
    got = asyncio.run(_run())
    assert got is not None and got["text"] == "活跃", "spent/faded 即便更咸也不取"


def test_fetch_one_returns_none_when_pool_empty(pool_env, monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, svc.V1_FLAG_KEY, True)

    async def _run():
        async with pool_env() as db:
            return await svc.fetch_one_thought(db, _CHAR, _USER)
    assert asyncio.run(_run()) is None


def test_settle_release_spent(pool_env, monkeypatch):
    """发送成功 ∧ 60 分钟内有回 ⇒ spent：status=spent、记 spent_at、salt 冻结。"""
    monkeypatch.setitem(AGENT_FLAGS, svc.V1_FLAG_KEY, True)
    rid = _seed_thought(pool_env, rid=1, salt=2.0)

    async def _run():
        async with pool_env() as db:
            out = await svc.settle_release(db, rid, sent_ok=True, replied_within_window=True, now=_NOW)
            await db.commit()
            return out
    out = asyncio.run(_run())
    assert out["release"] == dyn.STATUS_SPENT and out["written"] is True
    assert out["status"] == dyn.STATUS_SPENT and out["salt"] == 2.0  # 冻结不打折

    from app.models.character import ThoughtPool
    async def _read():
        async with pool_env() as db:
            return (await db.execute(select(ThoughtPool).where(ThoughtPool.id == rid))).scalars().first()
    row = asyncio.run(_read())
    assert row.status == dyn.STATUS_SPENT and row.spent_at is not None


def test_settle_release_told_flat(pool_env, monkeypatch):
    """发出去但没人接 ⇒ told_flat：salt *= 0.35、tell_count += 1、仍活跃。"""
    monkeypatch.setitem(AGENT_FLAGS, svc.V1_FLAG_KEY, True)
    rid = _seed_thought(pool_env, rid=1, salt=2.0, tell_count=0)

    async def _run():
        async with pool_env() as db:
            out = await svc.settle_release(db, rid, sent_ok=True, replied_within_window=False, now=_NOW)
            await db.commit()
            return out
    out = asyncio.run(_run())
    assert out["release"] == dyn.STATUS_TOLD_FLAT and out["written"] is True
    assert out["tell_count"] == 1
    assert abs(out["salt"] - round(2.0 * dyn.SALT_TOLD_FLAT_RATIO, 6)) < 1e-9


def test_settle_release_told_flat_twice_forces_faded(pool_env, monkeypatch):
    """tell_count ≥ 2 ⇒ 强制 faded（沉默 ≠ 可无限重试，设计 §2.3）。"""
    monkeypatch.setitem(AGENT_FLAGS, svc.V1_FLAG_KEY, True)
    rid = _seed_thought(pool_env, rid=1, salt=2.0, tell_count=1)  # 已说过一次

    async def _run():
        async with pool_env() as db:
            out = await svc.settle_release(db, rid, sent_ok=True, replied_within_window=False, now=_NOW)
            await db.commit()
            return out
    out = asyncio.run(_run())
    assert out["status"] == dyn.STATUS_FADED and out["tell_count"] == 2


def test_settle_release_never_told_no_penalty(pool_env, monkeypatch):
    """没发出去 ⇒ never_told：不惩罚、不写库（设计 §2.3：未用的东西不该被惩罚）。"""
    monkeypatch.setitem(AGENT_FLAGS, svc.V1_FLAG_KEY, True)
    rid = _seed_thought(pool_env, rid=1, salt=2.0, tell_count=0)

    async def _run():
        async with pool_env() as db:
            out = await svc.settle_release(db, rid, sent_ok=False, replied_within_window=False, now=_NOW)
            return out
    out = asyncio.run(_run())
    assert out["release"] == "never_told" and out.get("written") is False

    from app.models.character import ThoughtPool
    async def _read():
        async with pool_env() as db:
            return (await db.execute(select(ThoughtPool).where(ThoughtPool.id == rid))).scalars().first()
    row = asyncio.run(_read())
    assert row.salt == 2.0 and row.tell_count == 0 and row.status == dyn.STATUS_SPARK, "never_told 不得改行"


def test_settle_release_idempotent_on_spent(pool_env, monkeypatch):
    """幂等：spent_at 非空的行不再改（设计 §2.3 / §6 R7）。"""
    monkeypatch.setitem(AGENT_FLAGS, svc.V1_FLAG_KEY, True)
    rid = _seed_thought(pool_env, rid=1, status=dyn.STATUS_SPENT, salt=2.0, spent_at=_NOW)

    async def _run():
        async with pool_env() as db:
            return await svc.settle_release(db, rid, sent_ok=True, replied_within_window=False, now=_NOW)
    out = asyncio.run(_run())
    assert out.get("idempotent") is True and out["release"] == dyn.STATUS_SPENT


def test_settle_release_no_thought_id(pool_env, monkeypatch):
    """thought_id 为空 ⇒ {}（没绑定念头就无从结算）。"""
    monkeypatch.setitem(AGENT_FLAGS, svc.V1_FLAG_KEY, True)
    async def _run():
        async with pool_env() as db:
            return await svc.settle_release(db, None, sent_ok=True, replied_within_window=True)
    assert asyncio.run(_run()) == {}


# ══════════════════════════════════════════════ 6. 注入分区注册 + 注入文本口径

def test_section_registered_as_append_order_70():
    """``thought_pool`` 分区已注册为 TARGET_APPEND、order 70（保留空档段，不撞既有号）。"""
    from app.agent.context.sections import get_sections, TARGET_APPEND
    sec = next((s for s in get_sections() if s.key == "thought_pool"), None)
    assert sec is not None, "thought_pool 分区未注册"
    assert sec.target == TARGET_APPEND
    assert sec.order == 70
    # 不撞既有 order（红线③：不得复用已占用 order）
    others = [s.order for s in get_sections() if s.key != "thought_pool"]
    assert 70 not in others, "order 70 与既有分区撞号"


def test_build_injection_text_no_meta_narrative():
    """注入体只写那一件事本身，**不写元叙述**（无「N 条念头」「念头池」「执念」类实现概念）。"""
    text = svc.build_injection_text({"text": "今天把阳台的茉莉换了盆"})
    assert "今天把阳台的茉莉换了盆" in text
    for banned in ("条念头", "念头池", "执念", "闪念", "status", "salt", "source"):
        assert banned not in text, f"注入体泄漏实现概念 {banned}"


def test_build_injection_text_empty_for_none():
    assert svc.build_injection_text(None) == ""
    assert svc.build_injection_text({"text": "  "}) == ""


# ══════════════════════════════════════════════ 7. 聊天侧块落位（红线②）

def test_insert_thought_block_before_continue_payload():
    """素材块必须插在 continue_payload（【系统指令】）之前——诉求/指令恒最后（红线②）。"""
    from app.agent.context_builder import _insert_thought_block
    msgs = [
        {"role": "system", "content": "素材块"},
        {"role": "system", "content": "【系统指令】继续"},
        {"role": "user", "content": "在吗"},
    ]
    state = {"_host_user_msg_index": 2}
    _insert_thought_block(msgs, state, "【可自然提起的一件事】换了盆的茉莉")
    contents = [m["content"] for m in msgs]
    thought_idx = next(i for i, c in enumerate(contents) if "换了盆的茉莉" in c)
    cont_idx = next(i for i, c in enumerate(contents) if c.startswith("【系统指令】"))
    user_idx = next(i for i, c in enumerate(contents) if msgs[i]["role"] == "user")
    assert thought_idx < cont_idx < user_idx, "念头块须在 continue_payload 与 user 之前"


def test_insert_thought_block_falls_back_before_user():
    """无 continue_payload 锚点 ⇒ 退化到宿主 user 之前（user 恒为最后一条）。"""
    from app.agent.context_builder import _insert_thought_block
    msgs = [{"role": "system", "content": "素材块"}, {"role": "user", "content": "在吗"}]
    state = {"_host_user_msg_index": 1}
    _insert_thought_block(msgs, state, "【可自然提起的一件事】换了盆的茉莉")
    assert msgs[-1]["role"] == "user", "user 必须仍是最后一条"
    assert "换了盆的茉莉" in msgs[-2]["content"]
