# -*- coding: utf-8 -*-
"""X6-b（2026-09-17）策略包素材端口 + 类别注册 + rhythm/memory_review 迁移 单测。

覆盖（交接文档任务 4 六条 + 执行路由/注册冲突/素材白名单）：
1. 白名单外 key 取不到（manifest context_keys 过滤 + 内核未知 key 不返回）；
2. pull 端口异常不阻塞主链路（逐 key fail-open + hook 隔离）；
3. 类别注册冲突被拒（同名后加载者 / 内置类别 / 非法名 / 空白名单）；
4. 两个新类别 flag 开时由策略包产出、内核让位、同日去重不双发；
5. flag 关 / 包未启用 → rhythm、memory_review 两源逐字节旧行为；
6. 新类别不注册时完全不影响内核（不让位、不去重、落 hint 路径）；
7. manifest context_keys / 权限校验；exec 路由与内核保留闸（节律日上限/剧情线互斥）。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio
import datetime
import json

import pytest

from app.agent import loop as agent_loop
from app.plugins import manifest, registry, sdk
from app.scheduling.sources import SourceContext
from app.scheduling.sources import strategy as strategy_mod
from app.scheduling.sources.memory_review import MemoryReviewSource
from app.scheduling.sources.plugin import PluginSource
from app.scheduling.sources.rhythm import RhythmSource

PACK_RHYTHM = "proactive_strategy_rhythm"
PACK_REVIEW = "proactive_strategy_memory_review"
FLAG = "proactive_strategy_plugins"

CHAR_ID = 3
USER_ID = 1
SESSION_ID = 42

# rhythm 包注册的行为白名单（与包内 BEHAVIORS 一致）
RHYTHM_BEHAVIORS = (
    "greeting", "status_update", "proactive_chat", "goodnight",
    "moment_publish", "moment_comment",
)


# ---------------------------------------------------------------- 夹具

@pytest.fixture
def clean_registry():
    """用例结束清空类别登记（防跨用例污染）。"""
    yield
    strategy_mod.reset_registrations()


@pytest.fixture
def pack_rhythm():
    registry.load_plugin_dir(registry.EXAMPLE_DIR / PACK_RHYTHM)
    registry._enabled[PACK_RHYTHM] = True
    yield PACK_RHYTHM
    registry._enabled.pop(PACK_RHYTHM, None)
    registry._loaded.pop(PACK_RHYTHM, None)
    strategy_mod.reset_registrations()


@pytest.fixture
def pack_review():
    registry.load_plugin_dir(registry.EXAMPLE_DIR / PACK_REVIEW)
    registry._enabled[PACK_REVIEW] = True
    yield PACK_REVIEW
    registry._enabled.pop(PACK_REVIEW, None)
    registry._loaded.pop(PACK_REVIEW, None)
    strategy_mod.reset_registrations()


def _flag(monkeypatch, on: bool):
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, FLAG, on)


def _roster_entry():
    return {
        "character_id": CHAR_ID,
        "user_id": USER_ID,
        "session_id": SESSION_ID,
        "character_name": "小A",
        "nickname": "小明",
        "birthday": None,
        "birthday_enabled": True,
        "holiday_enabled": True,
        "first_session_at": None,
    }


def _stub_roster(monkeypatch, roster):
    async def _fake_roster():
        return list(roster)

    monkeypatch.setattr(strategy_mod, "build_roster", _fake_roster)


def _stub_dedup(monkeypatch, sent: bool):
    async def _fake_sent_today(character_id, message_type):
        return sent

    monkeypatch.setattr(strategy_mod, "sent_today", _fake_sent_today)


def _stub_context(monkeypatch, payload: dict, *, boom: bool = False):
    """替换内核素材端口：只返回 payload 里被请求的 key（验证白名单过滤）。"""
    seen = {}

    async def _fake(keys, character_id=None, **kw):
        if boom:
            raise RuntimeError("context boom")
        seen["keys"] = list(keys or [])
        seen["character_id"] = character_id
        return {k: v for k, v in (payload or {}).items() if k in (keys or [])}

    monkeypatch.setattr(strategy_mod, "build_proactive_context", _fake)
    return seen


def _char_info():
    return {
        "character_id": CHAR_ID, "user_id": USER_ID, "character_name": "小A",
        "character_bio": "简介", "character_personality": "温柔",
        "current_status": "在看书", "relationship_summary": "关系很好",
        "username": "u1", "nickname": "小明", "frequency": "medium",
        "max_daily_proactive": 5, "idle_threshold_minutes": 120,
    }


def _stub_rhythm_kernel(monkeypatch, *, timer=False, storyline=False, daily=0):
    """内核节律的频控闸与素材装配依赖（prepare_strategy_candidate / 旧路径共用）。"""
    from app.scheduling import arbiter, life_rhythm
    from app.scheduling import triggers as trig

    async def _no_timer(cid):
        return timer

    async def _no_story(cid):
        return storyline

    async def _chars():
        return [_char_info()]

    async def _daily(cid):
        return daily

    async def _sess(cid, uid):
        return {"id": SESSION_ID, "updated_at": datetime.datetime(2026, 9, 17, 2, 0, 0)}

    async def _msgs(sid, limit=10):
        return "用户: 你好"

    async def _last(sid):
        return datetime.datetime(2026, 9, 17, 1, 0, 0)

    monkeypatch.setattr(arbiter, "has_pending_timer", _no_timer)
    monkeypatch.setattr(arbiter, "has_pending_storyline", _no_story)
    monkeypatch.setattr(arbiter, "get_active_characters", _chars)
    monkeypatch.setattr(arbiter, "_session_last_message_at", _last)
    monkeypatch.setattr(trig, "get_daily_count", _daily)
    monkeypatch.setattr(trig, "get_latest_session", _sess)
    monkeypatch.setattr(trig, "get_last_messages", _msgs)
    monkeypatch.setattr(life_rhythm, "get_time_window", lambda now=None: {
        "name": "晚间", "start": 19 * 60, "end": 22 * 60,
        "tendencies": ["proactive_chat", "moment_comment"],
    })


# ---------------------------------------------------------------- 1. 两个新包本体

def test_两个新策略包_manifest与类别登记(pack_rhythm, pack_review):
    for pack, cat, mts in (
        (PACK_RHYTHM, "rhythm", RHYTHM_BEHAVIORS),
        (PACK_REVIEW, "memory_review", ("memory_review",)),
    ):
        m = manifest.load_manifest(str(registry.EXAMPLE_DIR / pack / "manifest.json"))
        assert m["name"] == pack
        assert m["hooks"] == ["proactive_candidate"]
        assert m["config"]["strategy_category"] == cat
        assert m["permissions"] == ["proactive:read"]      # 只读素材，不自己发消息
        assert "time_ctx" in m["context_keys"] or cat == "memory_review"
        assert "proactive_candidate" in registry._loaded[pack]["hooks"]

    reg = strategy_mod.strategy_registry()
    assert set(reg) == {"rhythm", "memory_review"}
    assert reg["rhythm"]["message_types"] == RHYTHM_BEHAVIORS
    assert reg["memory_review"]["message_types"] == ("memory_review",)


def test_manifest_context_keys与权限校验(tmp_path):
    """context_keys 白名单：未知 key / 非数组 / 超量 → 拒绝；proactive:read 权限合法。"""
    ok = {
        "name": "ctx_ok", "version": "1.0.0", "description": "d",
        "permissions": ["proactive:read"], "context_keys": ["time_ctx", "due_reviews"],
    }
    assert manifest.validate_manifest(ok) is None
    assert manifest.validate_manifest({**ok, "context_keys": ["unknown_key"]}) is not None
    assert manifest.validate_manifest({**ok, "context_keys": "time_ctx"}) is not None
    assert manifest.validate_manifest({**ok, "context_keys": ["time_ctx"] * 9}) is not None
    assert manifest.validate_manifest({**ok, "permissions": ["proactive:write"]}) is not None

    # 走真实文件加载路径（tmp_path，不落项目目录）
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(ok, ensure_ascii=False), encoding="utf-8")
    assert manifest.load_manifest(str(p))["name"] == "ctx_ok"
    p.write_text(json.dumps({**ok, "context_keys": ["nope"]}, ensure_ascii=False), encoding="utf-8")
    assert manifest.load_manifest(str(p)) is None


# ---------------------------------------------------------------- 2. 素材端口白名单

def test_白名单外key取不到(monkeypatch, pack_review):
    """本包只声明 due_reviews：请求 roster/time_ctx 也被过滤掉（白名单在 sdk 侧生效）。"""
    captured = {}

    async def _fake(keys, character_id=None, **kw):
        captured["keys"] = list(keys or [])
        return {"due_reviews": [{"id": 7, "summary": "s", "importance": 60, "due_at": None}]}

    monkeypatch.setattr(strategy_mod, "build_proactive_context", _fake)

    async def _run():
        registry._sdk_ctx["current"] = PACK_REVIEW
        try:
            return await sdk.get_proactive_context(
                ["due_reviews", "time_ctx", "roster"], character_id=CHAR_ID,
            )
        finally:
            registry._sdk_ctx.pop("current", None)

    out = asyncio.run(_run())
    assert captured["keys"] == ["due_reviews"]        # 未声明的两个 key 已被过滤
    assert set(out) == {"due_reviews"}


def test_内核侧未知key不返回(clean_registry):
    """内核实现层再兜一道：白名单外的 key 一律不返回。"""
    out = asyncio.run(strategy_mod.build_proactive_context(["time_ctx", "not_a_key"]))
    assert set(out) == {"time_ctx"}
    assert out["time_ctx"]["date"] == datetime.date.today().strftime("%Y-%m-%d")
    assert isinstance(out["time_ctx"]["hour"], int)
    assert "window" in out["time_ctx"]


def test_perchar_key无character_id则不下发(clean_registry):
    """character_state / due_reviews / recent_intents 必须带 character_id（防跨角色取数）。"""
    out = asyncio.run(strategy_mod.build_proactive_context(
        ["character_state", "due_reviews", "recent_intents", "time_ctx"],
    ))
    assert set(out) == {"time_ctx"}


def test_素材端口逐key_fail_open(monkeypatch, clean_registry):
    """单个 key 构造抛错 → 只丢该 key，其余照常返回（不阻塞主链路）。"""
    async def _boom(cid):
        raise RuntimeError("state boom")

    monkeypatch.setitem(strategy_mod._CONTEXT_BUILDERS, "character_state", _boom)
    out = asyncio.run(strategy_mod.build_proactive_context(
        ["time_ctx", "character_state"], character_id=CHAR_ID,
    ))
    assert set(out) == {"time_ctx"}


def test_素材端口异常_主链路照旧(monkeypatch, pack_review):
    """端口整体抛错 → 策略包 hook 返回空，plugin 源不抛错（hook 隔离）。"""
    _flag(monkeypatch, True)
    _stub_roster(monkeypatch, [_roster_entry()])
    _stub_context(monkeypatch, {}, boom=True)
    assert asyncio.run(PluginSource().collect(SourceContext())) == []


def test_素材端口_体量上限(monkeypatch, clean_registry):
    """超量列表会被逐条瘦身（防插件把上下文吃爆）。"""
    async def _big_roster(cid):
        return [{"character_id": i, "pad": "x" * 500} for i in range(20)]

    monkeypatch.setitem(strategy_mod._CONTEXT_BUILDERS, "roster", _big_roster)
    monkeypatch.setattr(strategy_mod, "MAX_CONTEXT_CHARS", 2000)
    out = asyncio.run(strategy_mod.build_proactive_context(["roster"]))
    assert len(json.dumps(out, ensure_ascii=False)) <= 2000
    assert len(out["roster"]) < 20


def test_真实链路_空库不抛错(monkeypatch, clean_registry):
    """两个包都启用 + flag 开，走真实 build_roster（沙箱库无角色）：不抛错、零候选。"""
    registry.load_plugin_dir(registry.EXAMPLE_DIR / PACK_RHYTHM)
    registry.load_plugin_dir(registry.EXAMPLE_DIR / PACK_REVIEW)
    registry._enabled[PACK_RHYTHM] = True
    registry._enabled[PACK_REVIEW] = True
    _flag(monkeypatch, True)
    try:
        assert asyncio.run(PluginSource().collect(SourceContext())) == []
    finally:
        for p in (PACK_RHYTHM, PACK_REVIEW):
            registry._enabled.pop(p, None)
            registry._loaded.pop(p, None)
        strategy_mod.reset_registrations()


def test_真实端口全key不抛错(clean_registry):
    """空沙箱库下真实走一遍全部 key（含 SQL 查询）：不抛错、类型正确、逐 key 限量。"""
    out = asyncio.run(strategy_mod.build_proactive_context(
        ["roster", "time_ctx", "character_state", "due_reviews", "recent_intents"],
        character_id=CHAR_ID,
    ))
    assert isinstance(out.get("roster"), list)
    assert out["due_reviews"] == [] and out["recent_intents"] == []
    assert set(out["character_state"]) == {
        "mood", "body_temp", "desire", "possessiveness",
        "fatigue", "sensitivity", "comfort", "anger",
    }
    assert out["time_ctx"]["date"]


# ---------------------------------------------------------------- 3. 类别注册冲突

def test_类别注册_冲突与非法被拒(clean_registry):
    assert strategy_mod.register_strategy("rhythm", ["greeting"], "pack_a") is True
    # 后加载者被拒（先到先得）
    assert strategy_mod.register_strategy("rhythm", ["goodnight"], "pack_b") is False
    assert strategy_mod.strategy_registry()["rhythm"] == {
        "source": "pack_a", "message_types": ("greeting",),
    }
    # 同一插件重载 = 覆盖，不算冲突
    assert strategy_mod.register_strategy("rhythm", ["goodnight", "greeting"], "pack_a") is True
    # 内置类别冲突 / 类别名非法 / 空白名单 / 命名非法 → 拒绝
    assert strategy_mod.register_strategy("special", ["birthday"], "pack_c") is False
    assert strategy_mod.register_strategy("Rhythm", ["greeting"], "pack_d") is False
    assert strategy_mod.register_strategy("bad-name", ["greeting"], "pack_d") is False
    assert strategy_mod.register_strategy("rhythm2", [], "pack_d") is False
    assert strategy_mod.register_strategy("rhythm2", ["Bad Type"], "pack_d") is False
    assert strategy_mod.register_strategy("rhythm2", [f"t{i}" for i in range(9)], "pack_d") is False
    # 告警有留痕
    warns = strategy_mod.strategy_warnings()
    assert any("已被插件 pack_a 注册" in w for w in warns)
    assert any("内置类别冲突" in w for w in warns)
    assert any("类别名非法" in w for w in warns)


def test_注册口只能在插件加载期调用(clean_registry):
    with pytest.raises(RuntimeError):
        sdk.register_proactive_strategy("rhythm", ["greeting"])
    # 素材端口：不在插件上下文 → 权限校验先拦（PermissionError 是 RuntimeError 的语义外异常，单独断言）
    with pytest.raises(PermissionError):
        asyncio.run(sdk.get_proactive_context(["time_ctx"]))


def test_未启用插件的登记不算接管(monkeypatch, clean_registry):
    """登记存在但插件被停用 / 已卸载 → 不让位（fail-back 内核旧行为）。"""
    registry.load_plugin_dir(registry.EXAMPLE_DIR / PACK_REVIEW)
    registry._enabled[PACK_REVIEW] = False
    _flag(monkeypatch, True)
    try:
        assert strategy_mod.claimed_categories() == set()
        assert strategy_mod.category_yielded("memory_review") is False
    finally:
        registry._loaded.pop(PACK_REVIEW, None)
        strategy_mod.reset_registrations()


# ---------------------------------------------------------------- 4. memory_review 迁移

def test_flag开_memory_review_策略包产出且内核让位(monkeypatch, pack_review):
    _flag(monkeypatch, True)
    _stub_roster(monkeypatch, [_roster_entry()])
    _stub_dedup(monkeypatch, sent=False)
    _stub_context(monkeypatch, {"due_reviews": [
        {"id": 555, "summary": "一起看过的展", "memory_type": "event",
         "importance": 60.0, "due_at": "2026-09-17T02:00:00"},
    ]})

    items = asyncio.run(PluginSource().collect(SourceContext()))
    assert len(items) == 1
    d = items[0].to_dict()
    assert d["type"] == "memory_review"            # 内核既定执行路由（run_memory_review）
    assert d["candidate"]["memory_id"] == 555
    assert d["candidate"]["message_type"] == "memory_review"   # 沿用既定口径
    assert d["candidate"]["strategy"] == "memory_review"
    assert d["candidate"]["plugin"] == PACK_REVIEW
    # 内核让位：同名源不再产出
    assert asyncio.run(MemoryReviewSource().collect(SourceContext())) == []


def test_flag开_memory_review_同日去重不双发(monkeypatch, pack_review):
    _flag(monkeypatch, True)
    _stub_roster(monkeypatch, [_roster_entry()])
    _stub_context(monkeypatch, {"due_reviews": [
        {"id": 555, "summary": "s", "memory_type": "event", "importance": 60.0, "due_at": None},
    ]})
    state = {"sent": False}

    async def _fake_sent_today(character_id, message_type):
        return state["sent"]

    monkeypatch.setattr(strategy_mod, "sent_today", _fake_sent_today)

    assert len(asyncio.run(PluginSource().collect(SourceContext()))) == 1
    state["sent"] = True                             # 内核已发过 → 下一 tick 不再投
    assert asyncio.run(PluginSource().collect(SourceContext())) == []


def test_flag关_memory_review_逐字节旧行为(monkeypatch, pack_review):
    _flag(monkeypatch, False)
    expected = [{"type": "memory_review", "priority": 1,
                 "candidate": {"character_id": CHAR_ID, "user_id": USER_ID, "memory_id": 9}}]

    from app.scheduling import memory_review as mr

    async def _fake():
        return list(expected)

    monkeypatch.setattr(mr, "collect_review_events", _fake)
    assert [i.to_dict() for i in asyncio.run(MemoryReviewSource().collect(SourceContext()))] == expected


def test_flag开但包未启用_memory_review行为不变(monkeypatch):
    """flag 开但无人接管 → 内核源照旧产出。"""
    _flag(monkeypatch, True)
    expected = [{"type": "memory_review", "priority": 1,
                 "candidate": {"character_id": CHAR_ID, "user_id": USER_ID, "memory_id": 9}}]

    from app.scheduling import memory_review as mr

    async def _fake():
        return list(expected)

    monkeypatch.setattr(mr, "collect_review_events", _fake)
    assert [i.to_dict() for i in asyncio.run(MemoryReviewSource().collect(SourceContext()))] == expected


# ---------------------------------------------------------------- 5. rhythm 迁移

def test_flag开_rhythm_策略包产出且内核让位(monkeypatch, pack_rhythm):
    _flag(monkeypatch, True)
    _stub_roster(monkeypatch, [_roster_entry()])
    _stub_dedup(monkeypatch, sent=False)
    _stub_rhythm_kernel(monkeypatch)
    monkeypatch.setitem(registry._db_config, PACK_RHYTHM, {
        "probability": 1.0, "windows": {"晚间": 1.0}, "behaviors": {"晚间": ["proactive_chat"]},
    })
    _stub_context(monkeypatch, {
        "time_ctx": {"date": "2026-09-17", "hour": 20, "minute": 0, "weekday": 3,
                     "is_weekend": False, "window": "晚间",
                     "tendencies": ["proactive_chat", "moment_comment"]},
        "character_state": {"mood": 50, "fatigue": 10},
    })

    items = asyncio.run(PluginSource().collect(SourceContext()))
    assert len(items) == 1
    d = items[0].to_dict()
    assert d["type"] == "proactive_chat"             # 内核按既定执行链（剧情线）执行
    cand = d["candidate"]
    assert cand["message_type"] == "proactive_chat"
    assert cand["strategy"] == "rhythm"
    assert cand["behavior"] == "proactive_chat"
    # 素材装配归内核：人格/现状/最近消息/闲置时长都由内核补齐
    assert cand["character_name"] == "小A" and cand["character_personality"] == "温柔"
    assert cand["session_id"] == SESSION_ID and cand["last_context"] == "用户: 你好"
    assert isinstance(cand["idle_minutes"], int)
    # 内核让位：rhythm 源不再产出
    assert asyncio.run(RhythmSource().collect(SourceContext())) == []


def test_rhythm_内核保留闸_pending剧情线(monkeypatch, pack_rhythm):
    """内核闸：有未发完剧情线 → 策略候选被丢弃（频控归内核，策略包绕不开）。"""
    _flag(monkeypatch, True)
    _stub_roster(monkeypatch, [_roster_entry()])
    _stub_dedup(monkeypatch, sent=False)
    _stub_rhythm_kernel(monkeypatch, storyline=True)
    monkeypatch.setitem(registry._db_config, PACK_RHYTHM, {
        "probability": 1.0, "windows": {"晚间": 1.0}, "behaviors": {"晚间": ["proactive_chat"]},
    })
    _stub_context(monkeypatch, {
        "time_ctx": {"window": "晚间", "hour": 20, "tendencies": ["proactive_chat"]},
        "character_state": {},
    })
    assert asyncio.run(PluginSource().collect(SourceContext())) == []


def test_rhythm_内核保留闸_每日上限(monkeypatch, pack_rhythm):
    _flag(monkeypatch, True)
    _stub_roster(monkeypatch, [_roster_entry()])
    _stub_dedup(monkeypatch, sent=False)
    _stub_rhythm_kernel(monkeypatch, daily=5)        # 已达 max_daily_proactive
    monkeypatch.setitem(registry._db_config, PACK_RHYTHM, {
        "probability": 1.0, "windows": {"晚间": 1.0}, "behaviors": {"晚间": ["proactive_chat"]},
    })
    _stub_context(monkeypatch, {
        "time_ctx": {"window": "晚间", "hour": 20, "tendencies": ["proactive_chat"]},
        "character_state": {},
    })
    assert asyncio.run(PluginSource().collect(SourceContext())) == []


def test_rhythm_非活跃时段不产出(monkeypatch, pack_rhythm):
    """time_ctx 无时段（凌晨）→ 策略包零输出。"""
    _flag(monkeypatch, True)
    _stub_roster(monkeypatch, [_roster_entry()])
    _stub_rhythm_kernel(monkeypatch)
    _stub_context(monkeypatch, {"time_ctx": {"window": "", "hour": 3, "tendencies": []}})
    assert asyncio.run(PluginSource().collect(SourceContext())) == []


def test_flag关_rhythm_逐字节旧行为(monkeypatch, pack_rhythm):
    _flag(monkeypatch, False)
    _stub_rhythm_kernel(monkeypatch)
    from app.scheduling import life_rhythm

    monkeypatch.setattr(life_rhythm, "sample_should_trigger", lambda freq, w: True)
    monkeypatch.setattr(life_rhythm, "pick_behavior", lambda w, override=None: "proactive_chat")

    async def _last(sid):
        return datetime.datetime(2026, 9, 17, 2, 0, 0)

    monkeypatch.setattr("app.scheduling.arbiter._session_last_message_at", _last)
    out = [i.to_dict() for i in asyncio.run(RhythmSource().collect(SourceContext()))]
    assert len(out) == 1
    assert out[0]["type"] == "proactive_chat"
    assert out[0]["candidate"]["session_id"] == SESSION_ID
    assert out[0]["candidate"]["idle_minutes"] == 0
    assert out[0]["candidate"]["behavior"] == "proactive_chat"
    assert out[0]["candidate"]["character_name"] == "小A"
    assert out[0]["candidate"]["window"]["name"] == "晚间"
    assert out[0]["candidate"]["last_context"] == "用户: 你好"


def test_flag开但包未启用_rhythm行为不变(monkeypatch):
    _flag(monkeypatch, True)
    _stub_rhythm_kernel(monkeypatch)
    from app.scheduling import life_rhythm

    monkeypatch.setattr(life_rhythm, "sample_should_trigger", lambda freq, w: True)
    monkeypatch.setattr(life_rhythm, "pick_behavior", lambda w, override=None: "proactive_chat")
    out = [i.to_dict() for i in asyncio.run(RhythmSource().collect(SourceContext()))]
    assert len(out) == 1 and out[0]["type"] == "proactive_chat"


# ---------------------------------------------------------------- 6. 未注册类别不影响内核

def test_新类别未注册_完全不影响内核(monkeypatch, clean_registry):
    """类别没登记 → 不让位、不去重、候选走 hint 路径（type=plugin）。"""
    _flag(monkeypatch, True)
    assert strategy_mod.claimed_categories() == set()
    assert strategy_mod.category_yielded("memory_review") is False
    assert strategy_mod.message_type_of(
        {"strategy": "memory_review", "message_type": "memory_review"}) is None
    assert strategy_mod.exec_type_of(
        {"strategy": "memory_review", "message_type": "memory_review"}) == "plugin"

    _stub_roster(monkeypatch, [_roster_entry()])

    async def _fake_collect(hook_name, ctx, timeout=None):
        return [{"plugin": "x", "result": {
            "character_id": CHAR_ID, "user_id": USER_ID, "session_id": SESSION_ID,
            "strategy": "memory_review", "message_type": "memory_review", "memory_id": 1,
            "hint": "未注册类别的候选",
        }}]

    monkeypatch.setattr(registry, "run_hook_collect", _fake_collect)
    items = asyncio.run(PluginSource().collect(SourceContext()))
    assert len(items) == 1
    assert items[0].to_dict()["type"] == "plugin"      # 未登记 → hint 生成路径，零变化


def test_登记后_执行路由与去重生效(monkeypatch, clean_registry):
    """登记 rhythm 后：message_type 进白名单、执行路由切成 candidate 口径。"""
    assert strategy_mod.register_strategy(
        "rhythm", ["proactive_chat", "goodnight"], "pack_a") is True
    cand = {"strategy": "rhythm", "message_type": "goodnight"}
    assert strategy_mod.message_type_of(cand) == "goodnight"
    assert strategy_mod.exec_type_of(cand) == "goodnight"
    # 白名单外的 message_type 不采信（防伪造落库口径）
    assert strategy_mod.message_type_of({"strategy": "rhythm", "message_type": "birthday"}) is None
    assert strategy_mod.exec_type_of({"strategy": "rhythm", "message_type": "birthday"}) == "plugin"


def test_reset_registrations_清空登记表(clean_registry):
    strategy_mod.register_strategy("rhythm", ["greeting"], "pack_a")
    assert strategy_mod.strategy_registry()
    strategy_mod.reset_registrations()
    assert strategy_mod.strategy_registry() == {}
    assert strategy_mod.strategy_warnings() == []
