# -*- coding: utf-8 -*-
"""X6-c（2026-09-17）motivation / unfinished_topic 外放 + 素材 key 扩展 单测。

覆盖（交接文档任务 5 五条 + 内核 prepare/配额归属/rhythm 去重口径）：
1. 新 key 白名单内可取（含 quota 需类别）、白名单外不可取、未传 character_id 的角色维度 key 不下发；
2. 两个类别 flag 开时「包产出 + 内核让位 + 配额/去重/关系门/免打扰在内核 prepare」；
3. flag 关 / 包未启用 → motivation、unfinished_topic 两源逐字节旧行为；
4. 类别注册冲突仍被拒；端口异常不阻塞主链路；
5. rhythm 当日去重改用触发日志（原按 message_type 查 storyline 会空转）。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；库为 conftest 会话级沙箱，不碰生产库。）
"""
import asyncio
import datetime
import json

import pytest

from app.agent import loop as agent_loop
from app.plugins import manifest, registry, sdk
from app.scheduling.sources import SourceContext
from app.scheduling.sources import strategy as strategy_mod
from app.scheduling.sources import unfinished_topic as ut_src
from app.scheduling.sources.motivation import MotivationSource
from app.scheduling.sources.plugin import PluginSource
from app.scheduling.sources.unfinished_topic import UnfinishedTopicSource

PACK_MOTIVATION = "proactive_strategy_motivation"
PACK_UNFINISHED = "proactive_strategy_unfinished_topic"
FLAG = "proactive_strategy_plugins"

CHAR_ID = 3
USER_ID = 1
SESSION_ID = 42


# ---------------------------------------------------------------- 夹具

@pytest.fixture
def clean_registry():
    """用例结束清空类别登记（防跨用例污染）。"""
    yield
    strategy_mod.reset_registrations()


@pytest.fixture
def pack_motivation():
    registry.load_plugin_dir(registry.EXAMPLE_DIR / PACK_MOTIVATION)
    registry._enabled[PACK_MOTIVATION] = True
    yield PACK_MOTIVATION
    registry._enabled.pop(PACK_MOTIVATION, None)
    registry._loaded.pop(PACK_MOTIVATION, None)
    strategy_mod.reset_registrations()


@pytest.fixture
def pack_unfinished():
    registry.load_plugin_dir(registry.EXAMPLE_DIR / PACK_UNFINISHED)
    registry._enabled[PACK_UNFINISHED] = True
    yield PACK_UNFINISHED
    registry._enabled.pop(PACK_UNFINISHED, None)
    registry._loaded.pop(PACK_UNFINISHED, None)
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


def _stub_context(monkeypatch, payload: dict, *, boom: bool = False):
    """替换内核素材端口：只返回 payload 里被请求的 key（验证白名单过滤）。"""
    seen = {}

    async def _fake(keys, character_id=None, category=None, **kw):
        if boom:
            raise RuntimeError("context boom")
        seen["keys"] = list(keys or [])
        seen["character_id"] = character_id
        seen["category"] = category
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


def _stub_kernel_common(monkeypatch, *, dnd=False, unreplied=False, last_msg_hours=10):
    """两个类别共用的内核依赖：选人 / 免打扰 / 未回复冷却 / 会话与最后消息时间。"""
    from app.scheduling import arbiter
    from app.scheduling import triggers as trig

    async def _chars():
        return [_char_info()]

    async def _dnd(cid, now):
        return dnd

    async def _unreplied(cid, uid):
        return unreplied

    async def _sess(cid, uid):
        return {"id": SESSION_ID, "updated_at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)}

    last_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(hours=last_msg_hours)

    async def _last(sid):
        return last_at

    monkeypatch.setattr(arbiter, "get_active_characters", _chars)
    monkeypatch.setattr(arbiter, "is_dnd_now", _dnd)
    monkeypatch.setattr(arbiter, "unreplied_cooldown_active", _unreplied)
    monkeypatch.setattr(arbiter, "_session_last_message_at", _last)
    monkeypatch.setattr(trig, "get_latest_session", _sess)


def _stub_quota(monkeypatch, used_6h=0, used_today=0, limit_6h=1, limit_day=2):
    async def _fake(character_id, category, *, message_type=None):
        return {"used_6h": used_6h, "used_today": used_today,
                "limit_6h": limit_6h, "limit_day": limit_day}

    monkeypatch.setattr(strategy_mod, "quota_used", _fake)


def _stub_no_dedup(monkeypatch):
    async def _not_sent(character_id, category, message_type):
        return False

    monkeypatch.setattr(strategy_mod, "sent_recently", _not_sent)


# ---------------------------------------------------------------- 1. 新素材 key

def test_两个新包_manifest与类别登记(pack_motivation, pack_unfinished):
    m = manifest.load_manifest(str(registry.EXAMPLE_DIR / PACK_MOTIVATION / "manifest.json"))
    assert m["config"]["strategy_category"] == "motivation"
    assert m["permissions"] == ["proactive:read"]
    assert set(m["context_keys"]) == {"relationship", "user_rhythm", "quota", "character_state"}
    m2 = manifest.load_manifest(str(registry.EXAMPLE_DIR / PACK_UNFINISHED / "manifest.json"))
    assert m2["config"]["strategy_category"] == "unfinished_topic"
    assert set(m2["context_keys"]) == {"open_topics", "recent_intents"}

    reg = strategy_mod.strategy_registry()
    assert reg["motivation"]["message_types"] == ("motivation",)
    assert reg["unfinished_topic"]["message_types"] == ("unfinished_topic",)
    # 执行路由与内核 prepare 均已登记
    assert strategy_mod.CATEGORY_EXEC_ROUTE["motivation"] == "candidate"
    assert strategy_mod.CATEGORY_EXEC_ROUTE["unfinished_topic"] == "candidate"
    assert strategy_mod.CATEGORY_KERNEL_PREP["motivation"] == "motivation"
    assert strategy_mod.CATEGORY_KERNEL_PREP["unfinished_topic"] == "unfinished_topic"


def test_manifest_新key校验(tmp_path):
    """新 key 合法；白名单外的 key 仍在装包校验阶段被拒（走真实文件，tmp_path 不落项目目录）。"""
    ok = {
        "name": "ctx_x6c", "version": "1.0.0", "description": "d",
        "permissions": ["proactive:read"],
        "context_keys": ["relationship", "user_rhythm", "quota", "open_topics"],
    }
    assert manifest.validate_manifest(ok) is None
    assert manifest.validate_manifest({**ok, "context_keys": ["unknown_key"]}) is not None

    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(ok, ensure_ascii=False), encoding="utf-8")
    assert manifest.load_manifest(str(p))["name"] == "ctx_x6c"
    p.write_text(json.dumps({**ok, "context_keys": ["nope"]}, ensure_ascii=False), encoding="utf-8")
    assert manifest.load_manifest(str(p)) is None


def test_新key_未传character_id则不下发(clean_registry):
    """四个新 key 全是角色维度：不给 character_id → 一个都不下发（防跨角色取数）。"""
    out = asyncio.run(strategy_mod.build_proactive_context(
        ["relationship", "user_rhythm", "quota", "open_topics", "time_ctx"],
    ))
    assert set(out) == {"time_ctx"}


def test_新key_白名单内可取_白名单外不可取(clean_registry):
    out = asyncio.run(strategy_mod.build_proactive_context(
        ["relationship", "user_rhythm", "open_topics", "not_a_key"],
        character_id=CHAR_ID,
    ))
    assert "not_a_key" not in out
    assert set(out["relationship"]) == {"trust", "attachment", "curiosity"}
    assert set(out["user_rhythm"]) == {
        "hours_since_last_user_message", "active_hours", "weight", "learned",
    }
    assert out["open_topics"] == []          # 沙箱库无话题
    assert out["user_rhythm"]["weight"] == 1.0    # 未学到作息 → 不挡


def test_quota_需要类别参数(clean_registry):
    """quota 不知道统计哪个类别 → 返回空（策略包应显式传或由 sdk 按登记推导）。"""
    assert asyncio.run(strategy_mod.build_proactive_context(
        ["quota"], character_id=CHAR_ID,
    )).get("quota") == {}
    q = asyncio.run(strategy_mod.build_proactive_context(
        ["quota"], character_id=CHAR_ID, category="motivation",
    ))["quota"]
    assert q["used_6h"] == 0 and q["used_today"] == 0
    assert q["limit_6h"] == 1 and q["limit_day"] == 2      # 想念通道独立配额


def test_quota_走真实库_两个类别都不抛错(clean_registry):
    """沙箱库真查一遍（含 proactive_trigger_logs）：想念走触发日志口径，未收尾走消息日志。"""
    m = asyncio.run(strategy_mod.quota_used(CHAR_ID, "motivation"))
    u = asyncio.run(strategy_mod.quota_used(CHAR_ID, "unfinished_topic"))
    assert m["used_6h"] == 0 and m["used_today"] == 0 and m["limit_day"] == 2
    assert u["used_today"] == 0 and u["limit_day"] == 1
    assert strategy_mod.category_quota_limits("unknown_cat") == {}


def test_quota_类别由sdk按登记自动推导(monkeypatch, pack_motivation):
    """策略包不传 category 时，sdk 用本包登记的类别推导（category_of_source）。"""
    captured = {}

    async def _fake(keys, character_id=None, category=None, **kw):
        captured["keys"] = list(keys or [])
        captured["category"] = category
        return {"quota": {"used_6h": 0, "used_today": 0, "limit_6h": 1, "limit_day": 2}}

    monkeypatch.setattr(strategy_mod, "build_proactive_context", _fake)

    async def _run():
        # A2 M4（2026-09-20）：_sdk_ctx 改 ContextVar，用 registry.sdk_context 设置插件身份
        with registry.sdk_context(PACK_MOTIVATION):
            return await sdk.get_proactive_context(["quota", "roster"], character_id=CHAR_ID)

    out = asyncio.run(_run())
    assert captured["keys"] == ["quota"]          # roster 未声明 → 已被白名单过滤
    assert captured["category"] == "motivation"   # 按本包登记推导
    assert set(out) == {"quota"}


def test_素材端口_新key逐key_fail_open(monkeypatch, clean_registry):
    """单个新 key 构造抛错 → 只丢该 key，其余照常返回。"""
    async def _boom(cid):
        raise RuntimeError("relationship boom")

    monkeypatch.setitem(strategy_mod._CONTEXT_BUILDERS, "relationship", _boom)
    out = asyncio.run(strategy_mod.build_proactive_context(
        ["relationship", "open_topics"], character_id=CHAR_ID,
    ))
    assert set(out) == {"open_topics"}


# ---------------------------------------------------------------- 2. motivation 迁移

def _motivation_ctx():
    return {
        "quota": {"used_6h": 0, "used_today": 0, "limit_6h": 1, "limit_day": 2},
        "relationship": {"trust": 70, "attachment": 80, "curiosity": 60},
        "user_rhythm": {"hours_since_last_user_message": 6.0, "active_hours": [[19, 23]],
                        "weight": 1.0, "learned": True},
        "character_state": {"desire": 70, "fatigue": 10, "mood": 60},
    }


def _stub_motivation_kernel(monkeypatch, score=0.9):
    from app.scheduling import arbiter
    from app.scheduling import triggers as trig

    async def _score(cid):
        return score

    async def _msgs(sid, limit=10):
        return "用户: 你好"

    monkeypatch.setattr(arbiter, "_compute_motivation", _score)
    monkeypatch.setattr(trig, "get_last_messages", _msgs)


def test_flag开_motivation_包产出且内核让位(monkeypatch, pack_motivation):
    _flag(monkeypatch, True)
    _stub_roster(monkeypatch, [_roster_entry()])
    _stub_kernel_common(monkeypatch)
    _stub_motivation_kernel(monkeypatch, score=0.9)
    _stub_quota(monkeypatch)
    _stub_no_dedup(monkeypatch)
    seen = _stub_context(monkeypatch, _motivation_ctx())

    items = asyncio.run(PluginSource().collect(SourceContext()))
    assert seen["keys"] == ["quota", "relationship", "user_rhythm", "character_state"]
    assert len(items) == 1
    d = items[0].to_dict()
    assert d["type"] == "motivation"                 # 走内核想念通道（独立配额）
    cand = d["candidate"]
    assert cand["strategy"] == "motivation"
    assert cand["message_type"] == "motivation"
    assert cand["behavior"] == "motivation"
    # 素材装配归内核：会话/最近语境/闲置时长/人格
    assert cand["session_id"] == SESSION_ID
    assert cand["last_context"] == "用户: 你好"
    assert cand["character_name"] == "小A" and cand["character_personality"] == "温柔"
    assert isinstance(cand["idle_minutes"], int)
    assert cand["motivation"] == 0.9                 # 关系门算出的渴望度由内核写入
    # 内核让位：motivation 源不再产出
    assert asyncio.run(MotivationSource().collect(SourceContext())) == []


def test_motivation_内核闸_关系门(monkeypatch, pack_motivation):
    """渴望度未达阈值 → 内核 prepare 丢弃（策略包打分再高也没用）。"""
    _flag(monkeypatch, True)
    _stub_roster(monkeypatch, [_roster_entry()])
    _stub_kernel_common(monkeypatch)
    _stub_motivation_kernel(monkeypatch, score=0.1)
    _stub_quota(monkeypatch)
    _stub_context(monkeypatch, _motivation_ctx())
    assert asyncio.run(PluginSource().collect(SourceContext())) == []


def test_motivation_内核闸_独立配额(monkeypatch, pack_motivation):
    """6h / 当日配额满 → 内核丢弃（配额归内核，策略包不记数）。"""
    for used in ({"used_6h": 1}, {"used_today": 2}):
        _flag(monkeypatch, True)
        _stub_roster(monkeypatch, [_roster_entry()])
        _stub_kernel_common(monkeypatch)
        _stub_motivation_kernel(monkeypatch, score=0.9)
        _stub_quota(monkeypatch, **used)
        _stub_context(monkeypatch, _motivation_ctx())
        assert asyncio.run(PluginSource().collect(SourceContext())) == []


def test_motivation_内核闸_免打扰(monkeypatch, pack_motivation):
    _flag(monkeypatch, True)
    _stub_roster(monkeypatch, [_roster_entry()])
    _stub_kernel_common(monkeypatch, dnd=True)
    _stub_motivation_kernel(monkeypatch, score=0.9)
    _stub_quota(monkeypatch)
    _stub_context(monkeypatch, _motivation_ctx())
    assert asyncio.run(PluginSource().collect(SourceContext())) == []


def test_flag关_motivation_逐字节旧行为(monkeypatch, pack_motivation):
    _flag(monkeypatch, False)
    _stub_kernel_common(monkeypatch)
    _stub_motivation_kernel(monkeypatch, score=0.9)
    from app.application import chat_service
    from app.scheduling import triggers as trig

    async def _sid(uid, cid):
        return SESSION_ID

    async def _msgs(sid, limit=10):
        return "用户: 你好"

    monkeypatch.setattr(chat_service, "get_latest_session_id", _sid)
    monkeypatch.setattr(trig, "get_last_messages", _msgs)
    out = [i.to_dict() for i in asyncio.run(MotivationSource().collect(SourceContext()))]
    assert len(out) == 1
    assert out[0]["type"] == "motivation"
    assert out[0]["candidate"]["session_id"] == SESSION_ID
    assert out[0]["candidate"]["last_context"] == "用户: 你好"
    assert out[0]["motivation"] == 0.9


# ---------------------------------------------------------------- 3. unfinished_topic 迁移

def _unfinished_ctx():
    return {
        "open_topics": [
            {"id": 88, "topic": "周末去爬山", "importance": 0.8, "goal": False, "hours_since": 6.0},
        ],
        "recent_intents": [],
    }


def test_flag开_unfinished_包产出且内核让位(monkeypatch, pack_unfinished):
    _flag(monkeypatch, True)
    _stub_roster(monkeypatch, [_roster_entry()])
    _stub_kernel_common(monkeypatch, last_msg_hours=10)
    _stub_quota(monkeypatch, used_today=0, limit_day=1)
    _stub_no_dedup(monkeypatch)

    async def _resolve(candidate, char_id):
        return "周末去爬山"

    monkeypatch.setattr(ut_src, "_resolve_content", _resolve)
    _stub_context(monkeypatch, _unfinished_ctx())
    items = asyncio.run(PluginSource().collect(SourceContext()))
    assert len(items) == 1
    d = items[0].to_dict()
    assert d["type"] == "unfinished_topic"              # 走内核 run_unfinished_topic
    cand = d["candidate"]
    assert cand["message_type"] == "unfinished_topic"   # 落库与去重用既定口径
    assert cand["topic_id"] == 88                       # 包只给 id
    assert cand["unfinished_content"] == "周末去爬山"     # 正文由内核装配
    assert cand["session_id"] == SESSION_ID
    # 内核让位：unfinished_topic 源不再产出
    assert asyncio.run(UnfinishedTopicSource().collect(SourceContext())) == []


def test_unfinished_内核闸_日配额(monkeypatch, pack_unfinished):
    _flag(monkeypatch, True)
    _stub_roster(monkeypatch, [_roster_entry()])
    _stub_kernel_common(monkeypatch)
    _stub_quota(monkeypatch, used_today=1, limit_day=1)      # 今日已发过 1 条
    _stub_context(monkeypatch, _unfinished_ctx())
    assert asyncio.run(PluginSource().collect(SourceContext())) == []


def test_unfinished_内核闸_最小间隔(monkeypatch, pack_unfinished):
    """距会话最后一条消息不足 MIN_GAP_MINUTES → 内核丢弃。"""
    _flag(monkeypatch, True)
    _stub_roster(monkeypatch, [_roster_entry()])
    _stub_kernel_common(monkeypatch, last_msg_hours=-1)      # 1 小时前（<2h）
    _stub_quota(monkeypatch, used_today=0, limit_day=1)
    _stub_context(monkeypatch, _unfinished_ctx())
    assert asyncio.run(PluginSource().collect(SourceContext())) == []


def test_unfinished_内核闸_免打扰与未回复冷却(monkeypatch, pack_unfinished):
    for kw in ({"dnd": True}, {"unreplied": True}):
        _flag(monkeypatch, True)
        _stub_roster(monkeypatch, [_roster_entry()])
        _stub_kernel_common(monkeypatch, **kw)
        _stub_quota(monkeypatch, used_today=0, limit_day=1)
        _stub_context(monkeypatch, _unfinished_ctx())
        assert asyncio.run(PluginSource().collect(SourceContext())) == []


def test_flag关_unfinished_逐字节旧行为(monkeypatch, pack_unfinished):
    _flag(monkeypatch, False)
    from app.scheduling import unfinished_topic as ut_mod

    expected = [{"type": "unfinished_topic", "priority": 3,
                 "candidate": {"character_id": CHAR_ID, "user_id": USER_ID,
                               "session_id": SESSION_ID, "unfinished_content": "改天一起吃饭"}}]

    async def _fake():
        return list(expected)

    monkeypatch.setattr(ut_mod, "collect_unfinished_events", _fake)
    assert [i.to_dict() for i in asyncio.run(UnfinishedTopicSource().collect(SourceContext()))] == expected


def test_flag开但包未启用_两个类别行为不变(monkeypatch):
    """flag 开但无人接管 → 两个内核源都照旧产出。"""
    _flag(monkeypatch, True)
    _stub_kernel_common(monkeypatch)
    _stub_motivation_kernel(monkeypatch, score=0.9)
    from app.application import chat_service
    from app.scheduling import triggers as trig
    from app.scheduling import unfinished_topic as ut_mod

    async def _sid(uid, cid):
        return SESSION_ID

    async def _msgs(sid, limit=10):
        return "用户: 你好"

    async def _events():
        return [{"type": "unfinished_topic", "priority": 3,
                 "candidate": {"character_id": CHAR_ID, "user_id": USER_ID,
                               "session_id": SESSION_ID, "unfinished_content": "改天聊"}}]

    monkeypatch.setattr(chat_service, "get_latest_session_id", _sid)
    monkeypatch.setattr(trig, "get_last_messages", _msgs)
    monkeypatch.setattr(ut_mod, "collect_unfinished_events", _events)
    assert len(asyncio.run(MotivationSource().collect(SourceContext()))) == 1
    assert len(asyncio.run(UnfinishedTopicSource().collect(SourceContext()))) == 1


# ---------------------------------------------------------------- 4. 注册冲突 / 端口异常

def test_两个新类别_注册冲突被拒(clean_registry):
    assert strategy_mod.register_strategy("motivation", ["motivation"], "pack_a") is True
    assert strategy_mod.register_strategy("motivation", ["motivation"], "pack_b") is False
    assert strategy_mod.register_strategy("unfinished_topic", ["unfinished_topic"], "pack_a") is True
    assert strategy_mod.register_strategy("unfinished_topic", ["unfinished_topic"], "pack_c") is False
    # 内置类别 / 非法名仍拒
    assert strategy_mod.register_strategy("special", ["birthday"], "pack_d") is False
    assert strategy_mod.register_strategy("BAD", ["x"], "pack_d") is False
    warns = strategy_mod.strategy_warnings()
    assert any("已被插件 pack_a 注册" in w for w in warns)


def test_素材端口异常_主链路照旧(monkeypatch, pack_motivation, pack_unfinished):
    """端口整体抛错 → 两个包都零输出，plugin 源不抛错（hook 隔离）。"""
    _flag(monkeypatch, True)
    _stub_roster(monkeypatch, [_roster_entry()])
    _stub_context(monkeypatch, {}, boom=True)
    assert asyncio.run(PluginSource().collect(SourceContext())) == []


def test_prepare异常_候选被丢弃(monkeypatch, clean_registry):
    """内核 prepare 抛错 → None（宁可不发，也不绕过内核频控）。"""
    from app.scheduling.sources import motivation as motivation_src

    assert strategy_mod.register_strategy("motivation", ["motivation"], "pack_a") is True

    async def _boom(candidate):
        raise RuntimeError("prep boom")

    monkeypatch.setattr(motivation_src, "prepare_strategy_candidate", _boom)
    assert asyncio.run(strategy_mod.prepare_candidate(
        {"strategy": "motivation", "message_type": "motivation"})) is None


# ---------------------------------------------------------------- 5. 去重口径

def test_去重口径_按类别登记(monkeypatch):
    """rhythm / motivation 走触发日志（storyline 落库查不到），unfinished 走消息日志当日。"""
    assert strategy_mod.CATEGORY_DEDUP["rhythm"] == ("trigger_log", "day")
    assert strategy_mod.CATEGORY_DEDUP["motivation"] == ("trigger_log", "6h")
    assert strategy_mod.CATEGORY_DEDUP["unfinished_topic"] == ("message_log", "day")

    calls = []

    async def _trigger(cid, ttype, since):
        calls.append(("trigger_log", ttype))
        return 1

    async def _sent_today(cid, mtype):
        calls.append(("message_log", mtype))
        return False

    monkeypatch.setattr(strategy_mod, "_count_trigger_log", _trigger)
    monkeypatch.setattr(strategy_mod, "sent_today", _sent_today)

    assert asyncio.run(strategy_mod.sent_recently(CHAR_ID, "rhythm", "proactive_chat")) is True
    assert calls[-1] == ("trigger_log", "proactive_chat")
    assert asyncio.run(strategy_mod.sent_recently(CHAR_ID, "unfinished_topic", "unfinished_topic")) is False
    assert calls[-1] == ("message_log", "unfinished_topic")
    # 未登记类别 → 沿用 sent_today（北京当日 + 消息日志）
    assert asyncio.run(strategy_mod.sent_recently(CHAR_ID, "unknown", "x")) is False
    assert calls[-1] == ("message_log", "x")


def test_去重口径_想念近6h(monkeypatch):
    """motivation 的去重窗口是 6h（日上限 2 条，不能按当日一刀切）。"""
    seen = {}

    async def _trigger(cid, ttype, since):
        seen["since"] = since
        return 1

    monkeypatch.setattr(strategy_mod, "_count_trigger_log", _trigger)
    assert asyncio.run(strategy_mod.sent_recently(CHAR_ID, "motivation", "motivation")) is True
    delta = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - seen["since"]
    assert 5.9 <= delta.total_seconds() / 3600.0 <= 6.1


def test_rhythm_当日去重不再空转(monkeypatch, clean_registry):
    """回归：rhythm 的当日去重此前按 message_type 查 storyline 恒为 0（空转），现走触发日志。"""
    calls = []

    async def _trigger(cid, ttype, since):
        calls.append((cid, ttype))
        return 1

    monkeypatch.setattr(strategy_mod, "_count_trigger_log", _trigger)
    assert asyncio.run(strategy_mod.sent_recently(CHAR_ID, "rhythm", "goodnight")) is True
    assert calls == [(CHAR_ID, "goodnight")]
