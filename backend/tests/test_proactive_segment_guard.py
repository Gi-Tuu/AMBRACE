# -*- coding: utf-8 -*-
"""批次四任务 1：主动消息分块护栏（flag proactive_segment_guard，默认关）。

覆盖交接要求 5 点：
- 残句被合并/丢弃（碎片段并入相邻段；全碎片且拼不出阈值 → 丢弃）；空/纯标点段被丢；
- 未闭合括号/引号不落刀（按行切 + 单段按句兜底切两处切点）；
- 家庭场景词 + 住校场景 → 拦截重试一次，仍冲突则丢弃该段；
- flag 关 = 逐字节旧行为（残句不合并、不校验现实约束）。
集成用例只 patch 前置查询与 LLM，不触生产库；DB 用例统一 tmp_path。
"""
import asyncio
import os

import pytest

from _dbclone import clone_engine, make_session_factory

from app.scheduling import message_generator as mg

pytestmark = pytest.mark.slow

_SCHOOL_ANCHOR = "\nTA 当前已知现状（以此为准，旧记忆不得与此矛盾）：位置：宿舍。\n"
_FAMILY_RESP = "我把菜都热好了。\n锅里给你留着。"
_SAFE_RESP = "我把菜都热好了。\n你那边忙完记得吃点东西。"


# ────────────────────────── 纯函数：残句/空块 ──────────────────────────

def test_空段与纯标点段被丢弃():
    assert mg._normalize_segments(["……", "刚到家了", "。。。", "   ", "吃了吗"]) == ["刚到家了吃了吗"]


def test_碎片段并入相邻段():
    # 后置碎片并入前一段；前置碎片并入其后第一段
    assert mg._normalize_segments(["我把菜都热好了。", "饭还热"]) == ["我把菜都热好了。饭还热"]
    assert mg._normalize_segments(["饭", "我把菜都热好了。"]) == ["饭我把菜都热好了。"]


def test_全碎片拼得出阈值则合并单段_拼不出则丢弃():
    assert mg._normalize_segments(["刚到家", "饭", "吃了吗"]) == ["刚到家饭吃了吗"]
    assert mg._normalize_segments(["饭"]) == []
    assert mg._normalize_segments(["……", "。。。"]) == []


# ────────────────────────── 纯函数：未闭合不落刀 ──────────────────────────

def test_未闭合括号与引号判定():
    assert mg._has_unclosed_delimiter("他还没说完（其实") is True
    assert mg._has_unclosed_delimiter("他说「明天见") is True
    assert mg._has_unclosed_delimiter("他说「明天见」。") is False
    assert mg._has_unclosed_delimiter("普通一句，没有括号。") is False


def test_按行切分_未闭合括号不落刀():
    # 括号跨行：开=并入一段；关=按行切成两段（旧行为）
    raw = "（低头笑了一下\n把手里的碗放好。）"
    assert mg._split_response_lines(raw) == ["（低头笑了一下把手里的碗放好。）"]


# ────────────────────────── 纯函数：现实约束校验 ──────────────────────────

def test_住校场景命中家庭场景词判冲突():
    assert mg._scene_is_school("TA 当前已知现状：位置：宿舍") is True
    assert mg._scene_is_school("位置：示例市") is False
    assert mg._conflicting_segment_indexes(["锅里给你留着。", "我把汤盛出来。"], "宿舍") == [0]
    # 非住校场景：不校验（宁可漏拦也不误伤）
    assert mg._conflicting_segment_indexes(["锅里给你留着。"], "位置：示例市") == []


def test_护栏应用_丢弃开关():
    segs = ["我把汤盛出来。", "锅里给你留着。"]
    kept, conf = mg._apply_segment_guard(segs, "宿舍")
    assert kept == segs and conf == [1]
    dropped, conf2 = mg._apply_segment_guard(segs, "宿舍", drop_conflicts=True)
    assert dropped == ["我把汤盛出来。"] and conf2 == [1]


def test_flag_默认关():
    from app.agent.loop import AGENT_FLAGS
    assert AGENT_FLAGS.get("proactive_segment_guard") is False
    assert mg._segment_guard_on() is False


# ────────────────────────── 集成：flag 关 = 逐字节旧行为 ──────────────────────────

class _FakeResult:
    def scalar_one_or_none(self):
        return None


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, *_a, **_k):
        return _FakeResult()


def _fake_session_factory():
    def _factory():
        return _FakeSession()
    return _factory


def _patch_all(monkeypatch, responses, anchor=""):
    """patch 前置查询 + LLM；返回 captured（记录每次 LLM 返回与 prompt）。"""
    calls: list[str] = []
    captured: dict = {"calls": calls}

    async def _noop(*_a, **_k):
        return ""

    async def _noop_list(*_a, **_k):
        return []

    async def _persona(*_a, **_k):
        return {"cognitive": True, "relationship_state": "", "active_topics": "", "storyline_status": "无"}

    async def _anchor(**_kw):
        return anchor

    seq = list(responses)

    async def _fake_gen(*_a, **_k):
        calls.append("call")
        idx = min(len(calls) - 1, len(seq) - 1)
        return seq[idx], ""

    monkeypatch.setattr("app.agent.user_profile.build_user_profile_text", _noop)
    monkeypatch.setattr("app.agent.persona.assemble_persona_context", _persona)
    monkeypatch.setattr("app.application.weather_service.get_user_weather_line", _noop)
    monkeypatch.setattr("app.db.database.async_session_factory", _fake_session_factory)
    monkeypatch.setattr("app.memory.search_memories", _noop_list)
    monkeypatch.setattr(mg, "_load_recent_reflection", _noop)
    monkeypatch.setattr("app.memory.current_state.current_user_state_anchor", _anchor)
    monkeypatch.setattr(mg, "_gen_with_reasoning", _fake_gen)
    return captured


def _run(**kw):
    gen_kw = dict(
        character_name="小爱", character_bio="", character_personality="友善",
        character_id=1, user_id=1, current_status="在家",
        last_context="用户: 今天好累\n你: 早点休息",
    )
    gen_kw.update(kw)
    return asyncio.run(mg.generate_proactive_event(**gen_kw))


def test_flag关_残句不合并且不做现实校验(monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "proactive_segment_guard", False)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_naturalness_score", False)
    _patch_all(monkeypatch, [_FAMILY_RESP], anchor=_SCHOOL_ANCHOR)
    segs = _run()
    # 旧行为：按行切、「饭还热」不合并、住校也照发家庭场景
    assert segs == ["我把菜都热好了。", "锅里给你留着。"]


def test_flag开_残句合并(monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "proactive_segment_guard", True)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_naturalness_score", False)
    _patch_all(monkeypatch, ["我把菜都热好了。\n饭还热"], anchor="")
    assert _run() == ["我把菜都热好了。饭还热"]


def test_flag开_单段按句兜底切时未闭合括号不落刀(monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "proactive_naturalness_score", False)
    raw = (
        "（我心里还惦记着他今天说的那句话好像有点别的意思。"
        "不过他要是真不想说就算了）。我先去把汤盛出来。你说呢？"
    )
    _patch_all(monkeypatch, [raw], anchor="")
    monkeypatch.setitem(AGENT_FLAGS, "proactive_segment_guard", False)
    off = _run()
    assert len(off) == 3 and "）" not in off[0]          # 旧行为：切在未闭合括号里
    _patch_all(monkeypatch, [raw], anchor="")
    monkeypatch.setitem(AGENT_FLAGS, "proactive_segment_guard", True)
    on = _run()
    assert len(on) == 2 and on[0].endswith("）。")        # 开：括号闭合后才落刀


def test_flag开_住校命中家庭场景词_重试一次(monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "proactive_segment_guard", True)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_naturalness_score", False)
    captured = _patch_all(monkeypatch, [_FAMILY_RESP, _SAFE_RESP], anchor=_SCHOOL_ANCHOR)
    segs = _run()
    assert segs == ["我把菜都热好了。", "你那边忙完记得吃点东西。"]
    assert len(captured["calls"]) == 2                    # 冲突 → 重试一次


def test_flag开_仍冲突则丢弃该段(monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "proactive_segment_guard", True)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_naturalness_score", False)
    captured = _patch_all(monkeypatch, [_FAMILY_RESP], anchor=_SCHOOL_ANCHOR)
    segs = _run()
    assert segs == ["我把菜都热好了。"]                    # 冲突段被丢
    assert len(captured["calls"]) == 2


def test_flag开_非住校不拦家庭场景词(monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "proactive_segment_guard", True)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_naturalness_score", False)
    captured = _patch_all(monkeypatch, [_FAMILY_RESP], anchor="位置：示例市")
    assert _run() == ["我把菜都热好了。", "锅里给你留着。"]
    assert len(captured["calls"]) == 1


def test_flag开_段数上限保持4(monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "proactive_segment_guard", True)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_naturalness_score", False)
    raw = "\n".join([
        "今天食堂的菜还不错。", "我泡了杯茶。", "你那边忙完了吗？", "刚下课回来。", "一整天没见你说话了。",
    ])
    _patch_all(monkeypatch, [raw], anchor="")
    segs = _run()
    assert len(segs) == 4                       # 合并溢出段，上限仍是 4
    assert segs[:3] == ["今天食堂的菜还不错。", "我泡了杯茶。", "你那边忙完了吗？"]
    assert segs[3] == "刚下课回来。一整天没见你说话了。"


def test_flag开_全部段冲突则整条不发(monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "proactive_segment_guard", True)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_naturalness_score", False)
    # 两段都命中家庭场景词：重试后仍冲突 → 全部丢弃 → 无可见内容 → 整条不发（宁可不发也不穿帮）
    captured = _patch_all(monkeypatch, ["锅里给你留着。\n等你回家。"], anchor=_SCHOOL_ANCHOR)
    assert _run() == []
    assert len(captured["calls"]) == 2          # 冲突重试一次


# ────────────────────────── 场景事实读取（tmp_path 临时库） ──────────────────────────

@pytest.fixture()
def scene_db(monkeypatch, tmp_path):
    """临时库（模板库克隆，见 tests/_dbclone.py）：patch async_session_factory（不触碰生产库）。"""
    db_path = os.path.join(str(tmp_path), "scene.db")
    engine = clone_engine(db_path)
    factory = make_session_factory(engine)

    async def _init():
        import app.models  # noqa: F401
        from app.models.user import User
        # 克隆库默认开 FK（生产同款 PRAGMA）：用例体插 user_facts(user_id=1) 需 users 父行
        async with factory() as db:
            db.add(User(id=1, username="sg_u1", nickname="主人"))
            await db.commit()

    asyncio.run(_init())
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    # user_facts 在模块顶部 from-import 捕获了绑定——须同时 patch 其自身引用
    import app.memory.user_facts as _uf
    monkeypatch.setattr(_uf, "async_session_factory", factory)
    from app.scheduling import user_rhythm as _ur
    _ur._active_hours_cache.pop(1, None)
    yield factory
    engine.sync_engine.dispose()


def test_scene_facts_读取位置槽与作息(scene_db, monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    from app.models.user import GlobalUserFact
    from app.models.life import UserRhythm
    monkeypatch.setitem(AGENT_FLAGS, "proactive_segment_guard", True)
    monkeypatch.setitem(AGENT_FLAGS, "user_fact_location", True)

    async def _seed():
        async with scene_db() as db:
            db.add(GlobalUserFact(user_id=1, slot="location", value="示例市·某大学宿舍"))
            db.add(UserRhythm(user_id=1, active_hours="[[8, 11], [20, 23]]"))
            await db.commit()

    asyncio.run(_seed())
    out = asyncio.run(mg._load_scene_facts(1))
    assert "示例市·某大学宿舍" in out
    assert "8点-11点" in out and "20点-23点" in out
    assert mg._scene_is_school(out) is True


def test_scene_facts_flag关返回空且不查库(scene_db, monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "proactive_segment_guard", False)
    assert asyncio.run(mg._load_scene_facts(1)) == ""
