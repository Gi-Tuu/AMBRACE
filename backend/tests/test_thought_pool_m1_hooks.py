# -*- coding: utf-8 -*-
"""批 4 M1-挂点（2026-10-01）：念头池「抽取」三处搭车挂点的接线断言。

派单：``output/AMBRACE_批4M1挂点_抽取接线_派单_转发用_20261001.md``
设计稿：``output/AMBRACE_批4_念头池T2_详细设计_v1_20260929.md`` §2.1（抽取面 F1–F6 + 抽取挂点唯一三处）

钉住四件（改任一即红）：
1. **flag 关＝零调用零查询**（派单硬约束 + 设计 §4 硬保证 1）：``thought_pool_shadow`` 关 ⇒
   三个挂点各自**首行返回**，既不建 session、也不调 ``supply_thought_pool``（一次 SQL 都不发）；
2. **flag 开＝各面真入池**：F1 事件侧 / F4+F5 回合侧 / F2+F3+F6 周期侧，复用既有纯函数抽取落池行；
3. **幂等**：同一来源重复抽取**不增行**（幂等键＝角色,用户,来源类型,来源主键,规范化文本哈希）；
4. **异常隔离 + 不改发送链**：挂点内部炸只记日志、绝不外抛；服务层仍零发送链路引用。

口径与纪律：临时库一律 pytest ``tmp_path`` 私有 SQLite（``_dbclone`` 克隆模板库）；全程不碰
backend/data 生产库、不调模型、不走网络。flag 用 monkeypatch 临时置开，用例间互不污染。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.application import thought_pool_service as svc
from app.domain.thought import dynamics as dyn
from app.domain.thought import extract as ex
from app.flags.agent_flags import AGENT_FLAGS

_CHAR, _USER = 13, 1
_NOW = datetime(2026, 10, 1, 3, 0)          # naive UTC ＝北京 2026-10-01 11:00
_FLAG = svc.FLAG_KEY                          # thought_pool_shadow


# ══════════════════════════════════════════════ 0. 公共桩：零查询探针 + 真库环境

class _NoSession:
    """假 session 工厂：一旦被调用即记账并报错——用于钉「flag 关＝一次 SQL 都不发」。"""

    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise AssertionError("flag 关时挂点不应建立 session / 发任何 SQL")


class _SupplySpy:
    """透传/拦截 ``supply_thought_pool``：记录调用次数（flag 关时必须 0 次）。"""

    def __init__(self, inner=None):
        self._inner = inner
        self.calls = 0

    async def __call__(self, db, rows_by_face, **kw):
        self.calls += 1
        if self._inner is not None:
            return await self._inner(db, rows_by_face, **kw)
        return {"intake": 0}


@pytest.fixture()
def pool_env(tmp_path):
    """真库环境（_dbclone 克隆模板库）：建好角色/用户，返回 (factory, engine)。"""
    from _dbclone import clone_engine, make_session_factory
    from app.models.character import AICharacter
    from app.models.user import User

    engine = clone_engine(tmp_path / "hooks.db")
    factory = make_session_factory(engine)

    async def _init():
        async with factory() as db:
            db.add(User(id=_USER, username="hk_u1", nickname="主人"))
            db.add(AICharacter(id=_CHAR, user_id=_USER, name="小暖", is_active=True))
            await db.commit()

    asyncio.run(_init())
    yield factory
    engine.sync_engine.dispose()


def _pool_rows(factory) -> list[tuple]:
    from app.models.character import ThoughtPool

    async def _run():
        async with factory() as db:
            got = (await db.execute(select(ThoughtPool).order_by(ThoughtPool.id))).scalars().all()
            return [(r.source_type, r.source_ref, r.status, r.character_id, r.user_id) for r in got]
    return asyncio.run(_run())


def _patch_session(monkeypatch, module_path: str, factory):
    """把挂点模块的 ``async_session_factory`` 换成测试库工厂（或 _NoSession 探针）。"""
    import importlib
    mod = importlib.import_module(module_path)
    monkeypatch.setattr(mod, "async_session_factory", factory)


# ══════════════════════════════════════════════ 1. flag 关＝零调用零查询（三挂点）

def test_event_hook_zero_call_zero_sql_when_flag_off(monkeypatch):
    """事件侧：flag 关 ⇒ _on_thought_pool_activity 首行返回，不建 session、不调 supply。"""
    from app.events import handlers
    assert AGENT_FLAGS[_FLAG] is False                 # 缺省关
    no_sess = _NoSession()
    spy = _SupplySpy()
    _patch_session(monkeypatch, "app.events.handlers", no_sess)
    monkeypatch.setattr(svc, "supply_thought_pool", spy)
    payload = {"data": {"character_id": _CHAR, "user_id": _USER, "activity_type": "create",
                        "artifact_id": 101, "memory_id": 201, "summary": "今天把阳台的茉莉换了盆"}}
    asyncio.run(handlers._on_thought_pool_activity(payload))
    assert no_sess.calls == 0, "flag 关时事件挂点建立了 session（应首行返回）"
    assert spy.calls == 0, "flag 关时事件挂点调了 supply_thought_pool（应首行返回）"


def test_turn_hook_zero_call_zero_sql_when_flag_off(monkeypatch):
    """回合侧：flag 关 ⇒ _settle_thought_pool_turn 首行返回，不建 session、不调 supply。"""
    from app.application import chat_service
    assert AGENT_FLAGS[_FLAG] is False
    no_sess = _NoSession()
    spy = _SupplySpy()
    _patch_session(monkeypatch, "app.application.chat_service", no_sess)
    monkeypatch.setattr(svc, "supply_thought_pool", spy)
    asyncio.run(chat_service._settle_thought_pool_turn(_CHAR, _USER, 1))
    assert no_sess.calls == 0, "flag 关时回合挂点建立了 session（应首行返回）"
    assert spy.calls == 0, "flag 关时回合挂点调了 supply_thought_pool（应首行返回）"


def test_periodic_hook_zero_call_zero_sql_when_flag_off(monkeypatch):
    """周期侧：flag 关 ⇒ _supply_thought_pool_periodic 首行返回，不建 session、不调 supply。"""
    from app.application import character_state_service as css
    assert AGENT_FLAGS[_FLAG] is False
    no_sess = _NoSession()
    spy = _SupplySpy()
    _patch_session(monkeypatch, "app.application.character_state_service", no_sess)
    monkeypatch.setattr(svc, "supply_thought_pool", spy)
    asyncio.run(css._supply_thought_pool_periodic([_CHAR], _NOW))
    assert no_sess.calls == 0, "flag 关时周期挂点建立了 session（应首行返回）"
    assert spy.calls == 0, "flag 关时周期挂点调了 supply_thought_pool（应首行返回）"


def test_periodic_hook_zero_call_when_no_char_ids(monkeypatch):
    """周期侧：char_ids 为空 ⇒ 即便 flag 开也首行返回（无候选角色不查库）。"""
    from app.application import character_state_service as css
    monkeypatch.setitem(AGENT_FLAGS, _FLAG, True)
    no_sess = _NoSession()
    _patch_session(monkeypatch, "app.application.character_state_service", no_sess)
    asyncio.run(css._supply_thought_pool_periodic([], _NOW))
    assert no_sess.calls == 0


# ══════════════════════════════════════════════ 2. flag 开＝各面真入池

def test_event_hook_f1_activity_enters_pool(pool_env, monkeypatch):
    """事件侧 F1：flag 开 ⇒ create 活动以 spark 入池（复用 extract_activity，不重写规则）。"""
    from app.events import handlers
    monkeypatch.setitem(AGENT_FLAGS, _FLAG, True)
    _patch_session(monkeypatch, "app.events.handlers", pool_env)
    payload = {"data": {"character_id": _CHAR, "user_id": _USER, "activity_type": "create",
                        "artifact_id": 101, "memory_id": 201, "summary": "今天把阳台的茉莉换了盆"}}
    asyncio.run(handlers._on_thought_pool_activity(payload))
    rows = _pool_rows(pool_env)
    assert len(rows) == 1
    assert rows[0][0] == ex.SRC_ACTIVITY and rows[0][2] == dyn.STATUS_SPARK
    assert rows[0][3] == _CHAR and rows[0][4] == _USER


def test_event_hook_ignores_non_f1_activity_type(pool_env, monkeypatch):
    """事件侧：activity_type 不在 F1 词表（create/learn/study）⇒ 不入池（extract_activity 判定）。"""
    from app.events import handlers
    monkeypatch.setitem(AGENT_FLAGS, _FLAG, True)
    _patch_session(monkeypatch, "app.events.handlers", pool_env)
    payload = {"data": {"character_id": _CHAR, "user_id": _USER, "activity_type": "rest",
                        "memory_id": 202, "summary": "今天休息了一下"}}
    asyncio.run(handlers._on_thought_pool_activity(payload))
    assert _pool_rows(pool_env) == []


def test_turn_hook_f4_f5_enter_pool(pool_env, monkeypatch):
    """回合侧 F4+F5：进行中话题(≥3天未动) + 新写 INFERRED 事实 ⇒ 各入池一行。"""
    from app.application import chat_service
    from app.models.memory import ConversationTopic, Memory
    monkeypatch.setitem(AGENT_FLAGS, _FLAG, True)
    _patch_session(monkeypatch, "app.application.chat_service", pool_env)

    async def _seed():
        async with pool_env() as db:
            db.add(ConversationTopic(
                character_id=_CHAR, user_id=_USER, topic="他上次说想学摄影",
                status="进行中", last_touched_at=_NOW - timedelta(days=5),
            ))
            db.add(Memory(
                character_id=_CHAR, user_id=_USER, memory_type="event",
                content="他好像最近换了工作", epistemic_status="INFERRED",
                created_at=_NOW - timedelta(hours=1),
            ))
            await db.commit()
    asyncio.run(_seed())

    # 挂点内部用 now_naive_utc() 作「现在」；种子的 last_touched_at/created_at 须相对真实当下，
    # 故这里把时间锚到真实 now，避免 _NOW(2026-10-01) 与运行时 now 不一致导致窗口判定漂移。
    from app.utils.timeutil import now_naive_utc
    real_now = now_naive_utc()
    async def _reseed():
        async with pool_env() as db:
            t = (await db.execute(select(ConversationTopic))).scalars().first()
            t.last_touched_at = real_now - timedelta(days=5)
            m = (await db.execute(select(Memory))).scalars().first()
            m.created_at = real_now - timedelta(hours=1)
            await db.commit()
    asyncio.run(_reseed())

    asyncio.run(chat_service._settle_thought_pool_turn(_CHAR, _USER, 1))
    faces = {r[0] for r in _pool_rows(pool_env)}
    assert ex.SRC_USER_HOOK in faces, "F4 进行中话题未入池"
    assert ex.SRC_FACT in faces, "F5 INFERRED 事实未入池"


def test_periodic_hook_f2_f3_f6_enter_pool(pool_env, monkeypatch):
    """周期侧 F2+F3+F6：复盘 / 冷场朋友圈 / 新增兴趣 ⇒ 各入池。"""
    from app.application import character_state_service as css
    from app.models.memory import Memory
    from app.models.life import AIMoment, LifeInterest
    monkeypatch.setitem(AGENT_FLAGS, _FLAG, True)
    _patch_session(monkeypatch, "app.application.character_state_service", pool_env)

    from app.utils.timeutil import now_naive_utc
    real_now = now_naive_utc()

    async def _seed():
        async with pool_env() as db:
            # F2 复盘（命中触发词「下次/想/试试」）
            db.add(Memory(
                character_id=_CHAR, user_id=_USER, memory_type="ai_reflection",
                content="下次想试试一起做陶艺", sub_type="plan",
                created_at=real_now - timedelta(hours=2),
            ))
            # F3 冷场朋友圈（发布满 7 天、零评论零点赞）
            db.add(AIMoment(
                character_id=_CHAR, user_id=_USER, content="阳台的茉莉开了",
                is_active=True, created_at=real_now - timedelta(days=10),
            ))
            # F6 新增兴趣（近 24h 新建）
            db.add(LifeInterest(
                character_id=_CHAR, name="摄影", level=30,
                created_at=real_now - timedelta(hours=2),
            ))
            await db.commit()
    asyncio.run(_seed())

    asyncio.run(css._supply_thought_pool_periodic([_CHAR], real_now))
    faces = {r[0] for r in _pool_rows(pool_env)}
    assert ex.SRC_REFLECT in faces, "F2 复盘未入池"
    assert ex.SRC_MOMENT in faces, "F3 冷场朋友圈未入池"
    assert ex.SRC_INTEREST in faces, "F6 新增兴趣未入池"


# ══════════════════════════════════════════════ 3. 幂等：重复抽取不增行

def test_event_hook_idempotent_no_new_row(pool_env, monkeypatch):
    """同一活动事件重复投递 ⇒ 幂等键拦住，池里仍只 1 行（设计 §2.5 唯一约束）。"""
    from app.events import handlers
    monkeypatch.setitem(AGENT_FLAGS, _FLAG, True)
    _patch_session(monkeypatch, "app.events.handlers", pool_env)
    payload = {"data": {"character_id": _CHAR, "user_id": _USER, "activity_type": "create",
                        "artifact_id": 101, "memory_id": 201, "summary": "今天把阳台的茉莉换了盆"}}
    asyncio.run(handlers._on_thought_pool_activity(payload))
    asyncio.run(handlers._on_thought_pool_activity(payload))   # 重复投递
    asyncio.run(handlers._on_thought_pool_activity(payload))   # 再投一次
    rows = _pool_rows(pool_env)
    assert len(rows) == 1, f"重复抽取新增了行（幂等键失效）：{rows}"


def test_turn_hook_idempotent_no_new_row(pool_env, monkeypatch):
    """回合侧同话题/同事实重复扫 ⇒ 不增行。"""
    from app.application import chat_service
    from app.models.memory import ConversationTopic
    monkeypatch.setitem(AGENT_FLAGS, _FLAG, True)
    _patch_session(monkeypatch, "app.application.chat_service", pool_env)
    from app.utils.timeutil import now_naive_utc
    real_now = now_naive_utc()

    async def _seed():
        async with pool_env() as db:
            db.add(ConversationTopic(
                character_id=_CHAR, user_id=_USER, topic="他上次说想学摄影",
                status="进行中", last_touched_at=real_now - timedelta(days=5),
            ))
            await db.commit()
    asyncio.run(_seed())

    asyncio.run(chat_service._settle_thought_pool_turn(_CHAR, _USER, 1))
    first = len(_pool_rows(pool_env))
    asyncio.run(chat_service._settle_thought_pool_turn(_CHAR, _USER, 1))   # 重复扫
    assert len(_pool_rows(pool_env)) == first, "回合侧重复扫描新增了行（幂等键失效）"
    assert first >= 1


# ══════════════════════════════════════════════ 4. 异常隔离 + 订阅登记 + 零发送权

def test_event_hook_swallows_supply_failure(monkeypatch):
    """挂点内部炸（supply 抛异常）⇒ 只记 WARNING、绝不外抛（不拖垮活动主链路）。"""
    from app.events import handlers
    monkeypatch.setitem(AGENT_FLAGS, _FLAG, True)

    async def _boom(db, rows_by_face, **kw):
        raise RuntimeError("supply 炸了")
    monkeypatch.setattr(svc, "supply_thought_pool", _boom)
    _patch_session(monkeypatch, "app.events.handlers", lambda: _RaisingCtx())
    payload = {"data": {"character_id": _CHAR, "user_id": _USER, "activity_type": "create",
                        "artifact_id": 101, "summary": "换了盆"}}
    # 不应抛异常
    asyncio.run(handlers._on_thought_pool_activity(payload))


class _RaisingCtx:
    async def __aenter__(self):
        raise RuntimeError("session 炸了")

    async def __aexit__(self, *a):
        return False


def test_turn_hook_swallows_failure(monkeypatch):
    """回合侧挂点内部炸 ⇒ 只记 DEBUG、绝不外抛（不拖垮聊天回合）。"""
    from app.application import chat_service
    monkeypatch.setitem(AGENT_FLAGS, _FLAG, True)
    _patch_session(monkeypatch, "app.application.chat_service", lambda: _RaisingCtx())
    asyncio.run(chat_service._settle_thought_pool_turn(_CHAR, _USER, 1))   # 不应抛


def test_periodic_hook_swallows_failure(monkeypatch):
    """周期侧挂点内部炸 ⇒ 只记 DEBUG、绝不外抛（不拖垮漂移/驱力兜底）。"""
    from app.application import character_state_service as css
    monkeypatch.setitem(AGENT_FLAGS, _FLAG, True)
    _patch_session(monkeypatch, "app.application.character_state_service", lambda: _RaisingCtx())
    asyncio.run(css._supply_thought_pool_periodic([_CHAR], _NOW))   # 不应抛


def test_event_hook_subscribed_to_activity_completed():
    """订阅登记：_on_thought_pool_activity 已挂在 life.activity_completed（与 life_share 同订阅位）。"""
    from app.events import handlers
    from app.events.bus import event_bus
    handlers.register_builtin_handlers()
    subs = event_bus._subscribers.get("life.activity_completed", [])
    assert handlers._on_thought_pool_activity in subs, "事件挂点未订阅 life.activity_completed"
    # 与 life_share 同一订阅位（设计 §2.1：事件侧与 life_share 同订阅位）
    assert handlers._on_life_share in subs


def test_service_layer_still_has_no_send_chain_reference():
    """不改发送链：服务层代码本体仍零引用 arbiter/生成器/调度器/发送口（M1 红线，挂点没把它带偏）。"""
    import ast

    def _code_only(path: str) -> str:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not isinstance(body, list) or not body:
                continue
            first = body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                node.body = body[1:]
        return ast.unparse(tree)

    code = _code_only(svc.__file__)
    for banned in ("arbiter", "message_generator", "send_to_session", "send_message",
                   "proactive_message", "ChatMessage"):
        assert banned not in code, f"服务层出现了发送链路引用 {banned}（念头池不得有发送权）"
