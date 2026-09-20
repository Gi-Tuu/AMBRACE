# -*- coding: utf-8 -*-
"""X6（2026-09-16）主动内容策略包外放 —— 内核让位 / 防双发 / 回归保护 单测。

覆盖（交接文档任务 4 四条 + 去重口径）：
1. 示例策略包 manifest/hook 合法、不自带写权限；
2. 策略包投候选 → 内核 plugin 源正常产出 TriggerItem（含 strategy/message_type/hint）；
3. 策略包抛错 → 主链路不受影响（既有 hook 隔离 + plugin 源 try/except）；
4. flag 关 / 未装包 → 内核 special 源行为不变（回归）、hook ctx 不带 roster；
5. 装了包 + flag 开 → 不双发（special 让位 + 内核按 message_type 当日去重双保险）；
6. sent_today 按北京日界去重（真实沙箱库，不写生产库）。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio
import datetime

import pytest

from app.agent import loop as agent_loop
from app.plugins import manifest, registry, sdk
from app.scheduling import arbiter
from app.scheduling.sources import SourceContext, strategy as strategy_mod
from app.scheduling.sources.plugin import PluginSource
from app.scheduling.sources.special import SpecialSource

PACK = "proactive_strategy_special"
FLAG = "proactive_strategy_plugins"

CHAR_ID = 3
USER_ID = 1
SESSION_ID = 42


# ---------------------------------------------------------------- 夹具

@pytest.fixture
def pack_enabled():
    """加载并启用示例策略包（用例结束还原注册表）。"""
    registry.load_plugin_dir(registry.EXAMPLE_DIR / PACK)
    registry._enabled[PACK] = True
    yield
    registry._enabled.pop(PACK, None)
    registry._loaded.pop(PACK, None)


def _flag(monkeypatch, on: bool):
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, FLAG, on)


def _roster_entry(birthday=None, *, holiday_enabled=True, first_session_at=None,
                  birthday_enabled=True):
    return {
        "character_id": CHAR_ID,
        "user_id": USER_ID,
        "session_id": SESSION_ID,
        "character_name": "小A",
        "nickname": "小明",
        "birthday": birthday,
        "birthday_enabled": birthday_enabled,
        "holiday_enabled": holiday_enabled,
        "first_session_at": first_session_at,
    }


def _stub_roster(monkeypatch, roster):
    async def _fake_roster():
        return list(roster)

    monkeypatch.setattr(strategy_mod, "build_roster", _fake_roster)


def _stub_dedup(monkeypatch, sent: bool):
    async def _fake_sent_today(character_id, message_type):
        return sent

    monkeypatch.setattr(strategy_mod, "sent_today", _fake_sent_today)


def _stub_special_triggers(monkeypatch):
    """给 special 源喂固定候选（flag 关时应当原样产出）。"""
    import app.scheduling.triggers as trig

    async def _bday():
        return [{"character_id": CHAR_ID, "user_id": USER_ID, "session_id": SESSION_ID}]

    async def _holiday():
        return [{"character_id": CHAR_ID, "user_id": USER_ID, "session_id": SESSION_ID,
                 "holiday_name": "测试节"}]

    async def _anniv():
        return [{"character_id": CHAR_ID, "user_id": USER_ID, "session_id": SESSION_ID,
                 "anniversary_days": 100}]

    monkeypatch.setattr(trig, "get_birthday_candidates", _bday)
    monkeypatch.setattr(trig, "get_holiday_candidates", _holiday)
    monkeypatch.setattr(trig, "get_anniversary_candidates", _anniv)
    return [
        {"type": "birthday", "priority": 3,
         "candidate": {"character_id": CHAR_ID, "user_id": USER_ID, "session_id": SESSION_ID}},
        {"type": "holiday", "priority": 3,
         "candidate": {"character_id": CHAR_ID, "user_id": USER_ID, "session_id": SESSION_ID,
                       "holiday_name": "测试节"}},
        {"type": "anniversary", "priority": 3,
         "candidate": {"character_id": CHAR_ID, "user_id": USER_ID, "session_id": SESSION_ID,
                       "anniversary_days": 100}},
    ]


# ---------------------------------------------------------------- 1. 策略包本体

def test_示例策略包_manifest与hook注册(pack_enabled):
    m = manifest.load_manifest(str(registry.EXAMPLE_DIR / PACK / "manifest.json"))
    assert m["name"] == PACK
    assert m["hooks"] == ["proactive_candidate"]
    assert m["config"]["strategy_category"] == "special"
    # 策略包不自己发消息（发送归内核）
    assert m["permissions"] == []
    assert "proactive_candidate" in registry._loaded[PACK]["hooks"]


def test_策略包_命中日期才投候选(pack_enabled):
    """纯策略判定：今天生日 → 投 birthday 候选；不是生日且不配节日 → 不投。"""
    today = datetime.date.today().strftime("%m-%d")

    async def _collect(roster, kinds):
        monkeypatched = {"kinds": kinds, "festivals": {}, "templates": {}}
        registry._db_config[PACK] = monkeypatched
        try:
            return await registry.run_hook_collect(
                "proactive_candidate",
                {"strategy_categories": ["special"], "roster": roster},
            )
        finally:
            registry._db_config.pop(PACK, None)

    hit = asyncio.run(_collect([_roster_entry(today)], ["birthday"]))
    assert hit and hit[0]["plugin"] == PACK
    cand = hit[0]["result"][0]
    assert cand["strategy"] == "special"
    assert cand["message_type"] == "birthday"
    assert cand["session_id"] == SESSION_ID and cand["hint"]

    miss = asyncio.run(_collect([_roster_entry("01-02" if today != "01-02" else "01-03")],
                                ["birthday"]))
    assert miss == []


# ---------------------------------------------------------------- 2. flag 关 = 逐字节旧行为

def test_flag关_hook不下发roster(monkeypatch, pack_enabled):
    """flag 关 → ctx 为空 dict（策略包返回空），与策略包落地前完全一致。"""
    _flag(monkeypatch, False)
    seen = {}

    async def _fake_collect(hook_name, ctx, timeout=None, **kw):
        seen["hook"] = hook_name
        seen["ctx"] = dict(ctx)
        return []

    monkeypatch.setattr(registry, "run_hook_collect", _fake_collect)
    assert asyncio.run(PluginSource().collect(SourceContext())) == []
    assert seen["hook"] == "proactive_candidate"
    assert seen["ctx"] == {}


def test_flag关_special源行为不变(monkeypatch, pack_enabled):
    """回归：装了包但 flag 关 → special 源照旧产出全部候选。"""
    _flag(monkeypatch, False)
    expected = _stub_special_triggers(monkeypatch)
    out = [i.to_dict() for i in asyncio.run(SpecialSource().collect(SourceContext()))]
    assert out == expected


def test_flag开但包未启用_special源行为不变(monkeypatch):
    """回归：flag 开但策略包未启用 → 无人接管，special 源照旧。"""
    _flag(monkeypatch, True)
    expected = _stub_special_triggers(monkeypatch)
    out = [i.to_dict() for i in asyncio.run(SpecialSource().collect(SourceContext()))]
    assert out == expected


# ---------------------------------------------------------------- 3. 策略包投候选 → 内核产出

def test_flag开_下发roster并产出策略候选(monkeypatch, pack_enabled):
    _flag(monkeypatch, True)
    monkeypatch.setitem(registry._db_config, PACK, {"kinds": ["birthday"], "festivals": {}})
    _stub_roster(monkeypatch, [_roster_entry(datetime.date.today().strftime("%m-%d"))])
    _stub_dedup(monkeypatch, sent=False)

    items = asyncio.run(PluginSource().collect(SourceContext()))
    assert len(items) == 1
    d = items[0].to_dict()
    assert d["type"] == "plugin" and d["priority"] == 1
    assert d["candidate"]["character_id"] == CHAR_ID
    assert d["candidate"]["session_id"] == SESSION_ID
    assert d["candidate"]["strategy"] == "special"
    assert d["candidate"]["message_type"] == "birthday"
    assert d["candidate"]["plugin"] == PACK


def test_flag开_节日候选带holiday_name(monkeypatch, pack_enabled):
    today = datetime.date.today().strftime("%m-%d")
    _flag(monkeypatch, True)
    monkeypatch.setitem(
        registry._db_config, PACK,
        {"kinds": ["holiday"], "festivals": {today: "测试节"}},
    )
    _stub_roster(monkeypatch, [_roster_entry(None)])
    _stub_dedup(monkeypatch, sent=False)

    items = asyncio.run(PluginSource().collect(SourceContext()))
    assert len(items) == 1
    cand = items[0].to_dict()["candidate"]
    assert cand["message_type"] == "holiday"
    assert cand["holiday_name"] == "测试节"


# ---------------------------------------------------------------- 4. 异常隔离

def test_策略包抛错_主链路不受影响(monkeypatch, pack_enabled):
    """hook 抛错 / 收集失败 → plugin 源返回空，不抛给 arbiter。"""
    _flag(monkeypatch, True)

    async def _boom(hook_name, ctx, timeout=None, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(registry, "run_hook_collect", _boom)
    assert asyncio.run(PluginSource().collect(SourceContext())) == []


def test_策略包内部异常被hook隔离(monkeypatch, pack_enabled):
    """策略包自身抛错（如读配置失败）→ run_hook_collect 单插件隔离 → 源返回空。"""
    _flag(monkeypatch, True)
    _stub_roster(monkeypatch, [_roster_entry(datetime.date.today().strftime("%m-%d"))])

    def _boom_config():
        raise RuntimeError("config boom")

    monkeypatch.setattr(sdk, "get_config", _boom_config)
    assert asyncio.run(PluginSource().collect(SourceContext())) == []


# ---------------------------------------------------------------- 5. 防双发

def test_flag开_special源让位(monkeypatch, pack_enabled):
    """装包 + flag 开 → special 源整体让位（返回空），交给策略包独占产出。"""
    _flag(monkeypatch, True)
    _stub_special_triggers(monkeypatch)
    assert asyncio.run(SpecialSource().collect(SourceContext())) == []


def test_装包且flag开_同一触发日只发一次(monkeypatch, pack_enabled):
    """双保险：special 让位 → 只剩插件候选；已发送（北京日界内）→ 插件源也不再产出。"""
    _flag(monkeypatch, True)
    _stub_special_triggers(monkeypatch)          # 内核本来会产 3 条
    monkeypatch.setitem(registry._db_config, PACK, {"kinds": ["birthday"], "festivals": {}})
    _stub_roster(monkeypatch, [_roster_entry(datetime.date.today().strftime("%m-%d"))])

    state = {"sent": False}

    async def _fake_sent_today(character_id, message_type):
        return state["sent"]

    monkeypatch.setattr(strategy_mod, "sent_today", _fake_sent_today)

    special_items = asyncio.run(SpecialSource().collect(SourceContext()))
    plugin_items = asyncio.run(PluginSource().collect(SourceContext()))
    assert special_items == []                    # 让位
    assert len(plugin_items) == 1                 # 只有策略包一条
    assert len(special_items) + len(plugin_items) == 1

    state["sent"] = True                          # 内核已发送 → 下一 tick 不再投
    assert asyncio.run(PluginSource().collect(SourceContext())) == []


def test_未声明message_type的策略候选不去重也不让位(monkeypatch, pack_enabled):
    """普通插件候选（无 strategy 键）不受策略逻辑影响，照旧产出。"""
    _flag(monkeypatch, True)

    async def _fake_collect(hook_name, ctx, timeout=None, **kw):
        assert ctx and ctx.get("roster")           # flag 开 → roster 已下发
        return [{"plugin": "x", "result": {
            "character_id": CHAR_ID, "user_id": USER_ID, "session_id": SESSION_ID,
            "hint": "普通插件提示",
        }}]

    monkeypatch.setattr(registry, "run_hook_collect", _fake_collect)
    _stub_roster(monkeypatch, [_roster_entry(None)])
    items = asyncio.run(PluginSource().collect(SourceContext()))
    assert len(items) == 1
    assert "strategy" not in items[0].to_dict()["candidate"]


# ---------------------------------------------------------------- 6. 去重口径（真实沙箱库）

def test_sent_today_按北京日界去重():
    """同一角色同一 message_type 当天已发 → True；换类型 / 换角色 → False。"""
    from app.db.database import async_session_factory
    from app.models.character import AICharacter, ProactiveMessageLog
    from app.models.user import User

    uid, cid, other = 8901, 8902, 8903

    async def _seed():
        async with async_session_factory() as db:
            db.add(User(id=uid, username="x6_user", nickname="小明"))
            db.add(AICharacter(id=cid, user_id=uid, name="小A"))
            db.add(AICharacter(id=other, user_id=uid, name="小B"))
            await db.flush()
            db.add(ProactiveMessageLog(
                character_id=cid, message_type="birthday", content="生日快乐",
            ))
            await db.commit()

    asyncio.run(_seed())
    assert asyncio.run(strategy_mod.sent_today(cid, "birthday")) is True
    assert asyncio.run(strategy_mod.sent_today(cid, "holiday")) is False
    assert asyncio.run(strategy_mod.sent_today(other, "birthday")) is False


# ---------------------------------------------------------------- 7. arbiter 落库口径

def test_arbiter_策略候选按声明口径落库(monkeypatch):
    """策略候选 → send_to_session 用声明的 message_type（供内核去重/统计）；普通候选仍是 plugin。"""
    from app.agent import runtime as runtime_mod

    seen = {}

    async def _fake_runtime(**kw):
        seen["extra"] = kw.get("extra_system") or []
        return {"status": "ok", "text": "生日快乐呀！", "steps": []}

    async def _fake_send(session_id, char_id, user_id, content, message_type="", **kw):
        seen.update({"mtype": message_type, "holiday_name": kw.get("holiday_name")})

    monkeypatch.setattr(runtime_mod, "run_social_reply", _fake_runtime)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _fake_send)

    ok = asyncio.run(arbiter._plugin_proactive_runtime(
        CHAR_ID,
        {"user_id": USER_ID, "strategy": "special", "message_type": "holiday",
         "holiday_name": "测试节"},
        SESSION_ID, "今天是测试节",
    ))
    assert ok is True
    assert seen["mtype"] == "holiday"
    assert seen["holiday_name"] == "测试节"
    assert "【今日提醒】" in seen["extra"][0]["content"]   # 策略候选不再套「外部平台动态」

    # 普通插件候选：逐字节旧行为
    ok2 = asyncio.run(arbiter._plugin_proactive_runtime(
        CHAR_ID, {"user_id": USER_ID, "plugin": "x"}, SESSION_ID, "有人评论了你的视频",
    ))
    assert ok2 is True
    assert seen["mtype"] == "plugin"
    assert seen["holiday_name"] is None
    assert "【外部动态】你在外部平台看到一条新动态：" in seen["extra"][0]["content"]
