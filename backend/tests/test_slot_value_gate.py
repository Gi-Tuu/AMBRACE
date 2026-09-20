# -*- coding: utf-8 -*-
"""通用槽值写入闸（记忆时态缺陷族·第三批任务1–3，2026-09-17）回归用例。

交接口径：把「槽值必须是短语、不能是整句聊天」做成通用闸（``app/memory/slot_guard.py``），
接上 ``upsert_user_fact`` 唯一写入口，fail-closed 拒写但不阻断主链路。

覆盖：
- **负面用例 = 生产实证四条整句**（health/living/goal_state/job，取生产库现行 value 原文）
  + 生产 location 的 previous_value 聊天行 → 必须被拒；
- 正面用例：正常短语（「常驻湛江市」「大二在读」「有课，时间紧」等）→ 必须通过；
- **句末标点误拒修复（2026-09-19）**：结尾句末标点先剥再判（可连续多个），只有**句中**残留
  才判整句叙述；换行/回车任何位置出现一律拒（新增原因码 ``line_break``）；
- 写入路径接线：extractor 槽位落库分支被拒后**仍落普通 memories**（不污染槽位）；
- ``previous_value`` 不因拒绝而丢失/被覆盖；
- **红线回归**：``user_fact_slot_enabled`` 对 relationship/health 仍为「须显式开启」，
  共享读路径也不带出这两槽。

用项目既有「临时 SQLite 文件库 + monkeypatch 异步工厂」夹具法，不触碰 backend/data。
（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio
import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.slow

# 生产库 user_id=3 现行 location 权威值（29 字，须仍可过闸）
_STRONG_LOCATION = "常驻湛江市·广东海洋大学湖光校区·学生宿舍（大二在读学生）"

# ── 生产实证污染（只读扫描 user_facts 得到的四槽现行 value 原文）──────────────
_PRODUCTION_POLLUTION = (
    ("health", "用户出门去提前占个好位置"),
    ("living", "用户买了校园网，覆盖全校教学区和宿舍"),
    ("goal_state", "用户参加的比赛快截止了，用户要赶出作品来"),
    ("job", "用户今晚有课，时间赶"),
)
# 交接文件里 goal_state 的省略号写法（同一行的缩写形态）也必须被拒
_PRODUCTION_POLLUTION_ELIDED = "用户参加的比赛快截止了…"
# 生产实证：曾把 location 权威值挤进 previous_value 的无关聊天行（位置闸既有用例）
_PRODUCTION_LOCATION_JUNK = "用户说芒芒已经被照顾好了，外卖到了会叫我。"

# ── 正面短语（交接指定 + 既有调用点/既有用例在用的值）────────────────────────
_POSITIVE_PHRASES = (
    ("location", "常驻湛江市"),
    ("location", _STRONG_LOCATION),
    ("location", "湛江市"),
    ("location", "示例城"),
    ("location", "a"),          # 既有用例的短 token（宁松勿误伤）
    ("job", "大二在读"),
    ("job", "有课，时间紧"),
    ("job", "程序员"),           # 既有用例
    ("job", "后端开发工程师"),    # 长值 + 槽锚点（开发）
    ("relationship", "已婚"),     # 既有用例
    ("living", "住学校宿舍"),
    ("goal_state", "论文写到第三章"),
    ("health", "生病住院"),       # 既有用例
    ("health", "慢性胃炎，忌辛辣"),
)

_SLOT_FLAGS = (
    "user_fact_location", "user_fact_job", "user_fact_relationship",
    "user_fact_living", "user_fact_goal_state", "user_fact_health",
)


@pytest.fixture()
def sg_db(monkeypatch, tmp_path):
    """临时库：建全模型 + 把 user_facts/extractor/cross_char_sync 的工厂指向临时工厂。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{os.path.join(str(tmp_path), 'sg.db')}", poolclass=NullPool)
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
            db.add(AICharacter(id=1, user_id=1, name="酱", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_init())
    import app.db.database as db_mod
    import app.memory.cross_char_sync as ccs
    import app.memory.extractor as ex
    import app.memory.user_facts as uf
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(uf, "async_session_factory", factory)
    monkeypatch.setattr(ex, "async_session_factory", factory)
    monkeypatch.setattr(ccs, "async_session_factory", factory)

    async def _noop(*a, **k):
        return None
    import app.db.vector_store as vs
    monkeypatch.setattr(vs, "mark_memory_vector_status", _noop)
    yield factory
    asyncio.run(engine.dispose())


def _set_flags(monkeypatch, **kw):
    """显式设置 AGENT_FLAGS（未指定的槽 flag 一律 False，保证用例确定性）。"""
    from app.agent.loop import AGENT_FLAGS as _af
    monkeypatch.setitem(_af, "global_user_facts", bool(kw.get("global_user_facts", False)))
    for k in _SLOT_FLAGS:
        monkeypatch.setitem(_af, k, bool(kw.get(k, False)))


def _rows(factory, user_id=1, slots=None):
    from app.memory.user_facts import get_active_user_facts
    return asyncio.run(get_active_user_facts(user_id, slots=slots))


# ────────────────────────── 任务1：通用判据（纯函数）──────────────────────────

@pytest.mark.parametrize("slot,value", _PRODUCTION_POLLUTION)
def test_production_pollution_sentences_rejected(slot, value):
    """负面用例 = 生产实证四条整句（原文）→ 必须被拒。"""
    from app.memory.slot_guard import slot_value_reject_reason, slot_value_write_ok
    assert slot_value_write_ok(slot, value) is False
    assert slot_value_reject_reason(slot, value) == "narration_prefix"  # 报告式整句（「用户…」开头）


def test_production_pollution_elided_and_location_junk_rejected():
    """省略号形态的整句、以及曾污染 location 的聊天行，同样被拒。"""
    from app.memory.slot_guard import slot_value_reject_reason, slot_value_write_ok
    assert slot_value_write_ok("goal_state", _PRODUCTION_POLLUTION_ELIDED) is False
    # 2026-09-19 改后：结尾省略号先剥，判据落点变成转述主语（拒写结论不变，仅原因码不同）
    assert slot_value_reject_reason("goal_state", _PRODUCTION_POLLUTION_ELIDED) == "narration_prefix"
    # 省略号出现在句中，仍是整句叙述信号
    assert slot_value_reject_reason("goal_state", "比赛快截止了…要赶作品") == "sentence_end_punct"
    assert slot_value_write_ok("location", _PRODUCTION_LOCATION_JUNK) is False


@pytest.mark.parametrize("slot,value", _POSITIVE_PHRASES)
def test_positive_phrases_pass(slot, value):
    """正面用例：正常短语（含交接指定的三条）→ 必须通过。"""
    from app.memory.slot_guard import slot_value_write_ok
    assert slot_value_write_ok(slot, value) is True


def test_reject_reason_codes_document_criteria():
    """判据落点可解释：句中句末标点 / 换行 / 转述句 / 对话残留 / 逗号堆叠 / 逗号分句过长 / 超长 / 缺槽锚点。"""
    from app.memory.slot_guard import slot_value_reject_reason as R
    assert R("health", "吃药了。") is None                      # 结尾标点不再拒（2026-09-19 修复）
    assert R("health", "吃药了。今天有点头疼") == "sentence_end_punct"  # 句中出现仍视为整句叙述
    assert R("health", "吃药了\n头疼") == "line_break"           # 多行粘贴任何时候都拒
    assert R("health", "用户表示自己知道应该照顾好自己的身体") == "narration_prefix"
    assert R("job", "今天有课，明天没课，后天再说") == "comma_stacking"
    # 去掉「用户」前缀的生产污染句仍被拒：逗号右侧是整句分句（纵深防线，不依赖转述主语）
    assert R("living", "买了校园网，覆盖全校教学区和宿舍") == "long_clause"
    assert R("job", "「程序员」") == "dialogue_residue"
    assert R("health", "阿" * 31) == "too_long"          # > 30 字上限
    assert R("health", "最近心情不太好呢") == "no_slot_anchor"  # 长值（>6 字）无健康语义锚点
    assert R("health", "") == "empty"


def test_location_rules_preserved():
    """交接要求：保留 location_guard 专有规则（证据锚点 + 短 token 放行），不被通用闸破坏。"""
    from app.memory.slot_guard import slot_value_reject_reason, slot_value_write_ok
    assert slot_value_write_ok("location", "长沙") is True
    assert slot_value_write_ok("location", _STRONG_LOCATION) is True  # 29 字权威值
    # >6 字且无任何位置证据锚点 → 拒（既有 location_write_ok 口径）
    assert slot_value_write_ok("location", "乱七八糟随便写的内容") is False
    assert slot_value_reject_reason("location", "乱七八糟随便写的内容") == "location_anchor"
    # ≤6 字短 token 放行是既有位置闸的「宁松勿误伤」口径（读侧 resolve_location_value 的
    # looks_like_location_value 仍会挡住它），本次不改动——此处显式断言以防被无意收紧。
    assert slot_value_write_ok("location", "垃圾值") is True


# ─────────── 句末标点误拒修复（2026-09-19）：结尾先剥、句中仍拒、换行仍拒 ───────────

_TRAILING_PUNCT_OK = (
    ("relationship", "与sam是伴侣关系。"),
    ("job", "大二在读。"),
    ("job", "大二在读……"),            # 结尾连续多个标点
    ("job", "大二在读。 。"),           # 结尾标点之间夹空白
    ("health", "吃药了！"),
    ("health", "慢性胃炎，忌辛辣。"),    # 结尾标点不计进逗号分句长度
    ("living", "住学校宿舍～"),
    ("goal_state", "论文写到第三章。"),
)


@pytest.mark.parametrize("slot,value", _TRAILING_PUNCT_OK)
def test_trailing_sentence_punct_stripped_before_judging(slot, value):
    """结尾句末标点属书写习惯：剥掉后是合格短语 → 放行（本次修复的正题）。"""
    from app.memory.slot_guard import slot_value_reject_reason, slot_value_write_ok
    assert slot_value_write_ok(slot, value) is True
    assert slot_value_reject_reason(slot, value) is None


@pytest.mark.parametrize("slot,value,reason", (
    ("health", "吃药了。今天有点头疼", "sentence_end_punct"),
    ("goal_state", "比赛快截止了…要赶作品", "sentence_end_punct"),
    # 分号仍在句末标点表内：句中即拒（本次不放宽，是否再放行留用户拍板）
    ("relationship", "恋爱中；对象sam", "sentence_end_punct"),
    ("job", "大二在读\n住校", "line_break"),
    ("job", "大二在读\r住校", "line_break"),
    ("health", "。", "empty"),         # 全是标点、无内容
    ("health", "！？", "empty"),
))
def test_mid_sentence_punct_and_line_break_still_rejected(slot, value, reason):
    """句中句末标点 = 整句叙述、换行/回车 = 格式不安全：两者照旧拒写。"""
    from app.memory.slot_guard import slot_value_reject_reason, slot_value_write_ok
    assert slot_value_write_ok(slot, value) is False
    assert slot_value_reject_reason(slot, value) == reason


def test_flagship_production_value_still_blocked_by_other_rules():
    """交接点名的「用户与sam是伴侣关系，sam是用户的老公。」：结尾句号已不再触发拒写，
    但仍被「转述主语开头」拦住；去掉主语后又被「逗号分句过长」拦住——那两条规则本次不动。
    """
    from app.memory.slot_guard import slot_value_reject_reason as R
    value = "用户与sam是伴侣关系，sam是用户的老公。"
    assert R("relationship", value) == "narration_prefix"
    assert R("relationship", value.replace("用户与", "与", 1)) == "long_clause"
    # 同一事实写成短语（无主语、逗号两侧都短）→ 放行，这是槽值应有的形态
    assert R("relationship", "与sam是伴侣关系。") is None


# ─────────── 槽值规范化 L1 接线（2026-09-19）：写槽传归一值，普通记忆存原句 ───────────

@pytest.mark.slow
def test_extractor_writes_normalized_slot_value(monkeypatch, sg_db):
    """旗舰用例：LLM 正文是「用户…」整句 → 槽里写的是规范化短值，原始整句仍落普通 memories。"""
    from app.memory.extractor import extract_single
    calls = []

    async def _fake_llm(**kw):
        return "USER_INFO: 用户与sam是伴侣关系，sam是用户的老公。 | 5\nSLOT: relationship | 1\n"

    async def _fake_save_memory(**kw):
        calls.append(kw)
        return None

    monkeypatch.setattr("app.memory.extractor.llm_call", _fake_llm)
    import app.memory as memory_mod
    monkeypatch.setattr(memory_mod, "save_memory", _fake_save_memory)
    _set_flags(monkeypatch, user_fact_relationship=True)

    saved = asyncio.run(extract_single(1, 1, 1, "我和sam在一起两年了", "真好", source_id=1))
    assert saved >= 1
    assert [r.value for r in _rows(sg_db, slots=["relationship"])] == ["与sam是伴侣关系"]
    # 原句不丢：普通记忆内容仍是 LLM 正文原文（那一行调用未被改动）
    assert any(kw.get("content", "").startswith("用户与sam是伴侣关系") for kw in calls)


@pytest.mark.slow
def test_extractor_passes_raw_value_when_normalize_returns_none(monkeypatch, sg_db):
    """归一化返回 None（无锚点超长句）→ 原样传 val，槽闸照旧拒写（fail-closed 不变）。"""
    from app.memory.extractor import extract_single
    raw = "用户说明天要去一个很远很远的地方找一个很久很久不见的人顺便看看沿途的风景啊"
    calls = []

    async def _fake_llm(**kw):
        return f"USER_INFO: {raw} | 5\nSLOT: health | 1\n"

    async def _fake_save_memory(**kw):
        calls.append(kw)
        return None

    monkeypatch.setattr("app.memory.extractor.llm_call", _fake_llm)
    import app.memory as memory_mod
    monkeypatch.setattr(memory_mod, "save_memory", _fake_save_memory)
    _set_flags(monkeypatch, user_fact_health=True)

    asyncio.run(extract_single(1, 1, 1, "明天想去远点的地方", "好呀", source_id=1))
    assert _rows(sg_db, slots=["health"]) == []                 # 槽位没被写
    assert any(kw.get("content", "").startswith("用户说明天要去") for kw in calls)  # 仍落普通记忆


# ────────────────────────── 任务2：接上写入路径（DB）──────────────────────────

@pytest.mark.slow
def test_upsert_rejects_production_pollution(monkeypatch, sg_db):
    """四条生产整句即使对应槽显式开启，也不得写进 user_facts（fail-closed）。"""
    from app.memory.user_facts import upsert_user_fact
    _set_flags(monkeypatch, user_fact_living=True, user_fact_goal_state=True,
               user_fact_job=True, user_fact_health=True)
    for slot, value in _PRODUCTION_POLLUTION:
        assert asyncio.run(upsert_user_fact(1, slot, value, source="chat")) is None
    assert _rows(sg_db, slots=[s for s, _ in _PRODUCTION_POLLUTION]) == []


@pytest.mark.slow
def test_upsert_accepts_positive_phrases(monkeypatch, sg_db):
    """正常短语仍照写（含 location 权威值、多槽并存）。"""
    from app.memory.user_facts import upsert_user_fact
    _set_flags(monkeypatch, user_fact_location=True, user_fact_job=True,
               user_fact_health=True, user_fact_goal_state=True)
    assert asyncio.run(upsert_user_fact(1, "location", _STRONG_LOCATION, source="manual")) is not None
    assert asyncio.run(upsert_user_fact(1, "job", "大二在读", source="chat")) == (None, "大二在读")
    assert asyncio.run(upsert_user_fact(1, "goal_state", "论文写到第三章", source="chat")) is not None
    assert asyncio.run(upsert_user_fact(1, "health", "慢性胃炎，忌辛辣", source="chat")) is not None
    got = {r.slot: r.value for r in _rows(sg_db)}
    assert got == {"location": _STRONG_LOCATION, "job": "大二在读",
                   "goal_state": "论文写到第三章", "health": "慢性胃炎，忌辛辣"}


@pytest.mark.slow
def test_upsert_accepts_value_with_trailing_punct(monkeypatch, sg_db):
    """写路径实证（句末标点误拒修复）：带结尾句号的合格短语能真正落槽，不再整条拒写。"""
    from app.memory.user_facts import upsert_user_fact
    _set_flags(monkeypatch, user_fact_job=True)
    assert asyncio.run(upsert_user_fact(1, "job", "大二在读。", source="chat")) == (None, "大二在读。")
    assert [r.value for r in _rows(sg_db, slots=["job"])] == ["大二在读。"]


@pytest.mark.slow
def test_rejected_write_keeps_previous_value(monkeypatch, sg_db):
    """被拒写入不触碰 DB：现行值与 previous_value 都不变（不得因拒绝而丢失旧值）。"""
    from app.memory.user_facts import upsert_user_fact
    _set_flags(monkeypatch, user_fact_job=True)
    assert asyncio.run(upsert_user_fact(1, "job", "程序员", source="chat")) == (None, "程序员")
    assert asyncio.run(upsert_user_fact(1, "job", "后端开发工程师", source="chat")) \
        == ("程序员", "后端开发工程师")
    # 生产污染整句 → 拒写
    assert asyncio.run(upsert_user_fact(1, "job", "用户今晚有课，时间赶", source="chat")) is None
    rows = _rows(sg_db, slots=["job"])
    assert len(rows) == 1
    assert rows[0].value == "后端开发工程师"
    assert rows[0].previous_value == "程序员"


@pytest.mark.slow
def test_extractor_rejected_slot_still_saved_as_memory(monkeypatch, sg_db):
    """接入 extractor 槽位落库分支：值被闸拒 → 不写槽，但仍落普通 memories（不阻断主链路）。"""
    from app.memory.extractor import extract_single
    calls = []

    async def _fake_llm(**kw):
        return "USER_INFO: 用户出门去提前占个好位置 | 5\nSLOT: health | 1\n"

    async def _fake_save_memory(**kw):
        calls.append(kw)
        return None

    monkeypatch.setattr("app.memory.extractor.llm_call", _fake_llm)
    import app.memory as memory_mod
    monkeypatch.setattr(memory_mod, "save_memory", _fake_save_memory)
    _set_flags(monkeypatch, user_fact_health=True)  # 槽显式开启，仍被值闸拒

    saved = asyncio.run(extract_single(1, 1, 1, "我出门去占位置", "好的", source_id=1))
    assert saved >= 1
    assert _rows(sg_db, slots=["health"]) == []          # 槽位未被污染
    assert any(kw.get("sub_type") == "health" for kw in calls)  # 仍落普通 memories


# ────────────────────────── 红线回归：感情/健康仍须显式开启 ──────────────────

@pytest.mark.slow
def test_red_line_sensitive_slots_remain_opt_in(monkeypatch, sg_db):
    """红线：relationship/health 不吃总闸旁路，须各自显式开启；共享读路径也不带出。"""
    from app.memory.user_facts import (
        enabled_user_fact_slots, get_shared_user_facts, upsert_user_fact,
        user_fact_slot_enabled,
    )
    _set_flags(monkeypatch)
    # 全关 → 六槽皆 False（含两条红线）
    assert user_fact_slot_enabled("relationship") is False
    assert user_fact_slot_enabled("health") is False
    # 只开总闸 → 红线仍 False（不吃旁路）
    _set_flags(monkeypatch, global_user_facts=True)
    assert user_fact_slot_enabled("relationship") is False
    assert user_fact_slot_enabled("health") is False
    assert "relationship" not in enabled_user_fact_slots()
    assert "health" not in enabled_user_fact_slots()
    # 只有显式开启才为 True，且互不影响
    _set_flags(monkeypatch, user_fact_relationship=True)
    assert user_fact_slot_enabled("relationship") is True
    assert user_fact_slot_enabled("health") is False
    # 库里有感情/健康行（值形态合格）也不随共享读路径带出
    _set_flags(monkeypatch)
    asyncio.run(upsert_user_fact(1, "location", "湛江市", source="manual"))
    asyncio.run(upsert_user_fact(1, "relationship", "已婚", source="chat"))
    asyncio.run(upsert_user_fact(1, "health", "生病住院", source="chat"))
    assert asyncio.run(get_shared_user_facts(1)) == {"location": "湛江市"}
