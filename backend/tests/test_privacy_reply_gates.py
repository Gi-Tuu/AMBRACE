# -*- coding: utf-8 -*-
"""断点 #8′ E13（2026-09-29）：privacy_reply 私聊直发补最小防线（**不补频控**）。

口径来源：output/AMBRACE_断点8prime_其他直发点核实_20260929.md §2 直发点表 + 本单任务书。
原状：``app/api/privacy.py`` 的 ``_send_chat_followup``（申请结果落库后直发 1 条私信并弹推送）
零道内核判定；冷却/锁屏判据只活在读端点 ``get_privacy_status``。
本单补：① 资格（复用 ``arbiter.get_active_characters``）② 发送侧复检读端点同款冷却/锁屏
（共用 ``_lock_view`` 一个口径，按本次申请行 id 排除自己）③ 每次降级/拦截打 INFO 痕迹。
间隔 / 每小时 / 每日上限一律**不加**（频控会伤「回应」语义）。
全部用例走 monkeypatch（假 session + 假 arbiter 闸 + 假 LLM/发送出口），
**不连生产库、不建表、不打 LLM**。每道闸一条「会被拦」，另有「不该拦」用例守住只减不发。
"""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.api import privacy as pv
from app.scheduling import arbiter
from app.utils.timeutil import now_naive_utc

CID, UID = 12, 4
_FUTURE = now_naive_utc() + timedelta(hours=2)


# ───────────────────────── 桩件 ─────────────────────────
def _const(value):
    async def _f(*_a, **_k):
        return value
    return _f


def _boom(*_a, **_k):
    async def _f():
        raise RuntimeError("闸取数失败")
    return _f()


class _LogSpy:
    """接管模块 logger：降级/拦截必须留下可读痕迹（本单口径＝INFO 日志）。"""

    def __init__(self):
        self.infos = []
        self.warns = []

    def info(self, msg, *a):
        self.infos.append(str(msg) % a if a else str(msg))

    def warning(self, msg, *a):
        self.warns.append(str(msg) % a if a else str(msg))

    def hit(self, gate):
        return [x for x in self.infos if f"gate={gate}" in x and "降级" in x]


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    """零 SQL 假会话：查询恒返回 trust，flush 时给新行补自增 id。"""

    def __init__(self, trust=50, row_id=77):
        self.trust = trust
        self.row_id = row_id
        self.added = []

    async def execute(self, *_a, **_k):
        return _Result(self.trust)

    def add(self, obj, *_a, **_k):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self.row_id

    async def commit(self):
        return None


@pytest.fixture
def env(monkeypatch):
    """闸默认全放行（角色在内核名单里 + 锁着 + 冷却 0），逐用例只拨需要的那一道。"""
    sent, calls, log = [], {}, _LogSpy()
    monkeypatch.setattr(pv, "_logger", log)

    async def _active(*_a, **_k):
        return [{"character_id": CID}]
    monkeypatch.setattr(arbiter, "get_active_characters", _active)

    async def _settings(db, character_id):
        return SimpleNamespace(privacy_lock_enabled=True)

    async def _unlock(db, character_id, user_id, target, now, exclude_id=None):
        calls["unlock_exclude_id"] = exclude_id
        return None

    async def _cool(db, character_id, user_id, target, now, exclude_id=None):
        calls["cooldown_exclude_id"] = exclude_id
        return 0

    monkeypatch.setattr(pv, "_get_settings", _settings)
    monkeypatch.setattr(pv, "_active_unlock", _unlock)
    monkeypatch.setattr(pv, "_cooldown_remaining", _cool)

    async def _send(session_id, character_id, user_id, content, message_type="state_trigger"):
        sent.append((session_id, character_id, user_id, content, message_type))
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _send)

    async def _sid(user_id, character_id):
        return 9
    monkeypatch.setattr("app.application.chat_service.get_latest_session_id", _sid)

    async def _llm(*_a, **_k):
        calls["llm"] = calls.get("llm", 0) + 1
        return "看就看呗，别笑我写得乱。"
    monkeypatch.setattr("app.agent.llm_client.chat_completion", _llm)

    return SimpleNamespace(sent=sent, calls=calls, log=log, monkeypatch=monkeypatch)


def _gate(env, request_id=None):
    return asyncio.run(pv._send_gate_reason(_FakeDB(), CID, UID, "diary", request_id))


def _followup(env, request_id=77):
    return asyncio.run(pv._send_chat_followup(
        _FakeDB(), CID, UID, "小爱", "阿泽", "日记", True, target="diary", request_id=request_id,
    ))


def _assert_downgraded(env, gate):
    """降级＝这条寒暄私信一条不发、且不额外打 LLM，但必须有 INFO 痕迹。"""
    assert env.sent == [], f"闸 {gate} 命中却仍私信"
    assert env.calls.get("llm") is None, "降级不该再生成寒暄内容"
    assert env.log.hit(gate), f"闸 {gate} 命中缺 INFO 痕迹"


# ───────────────────────── ① 资格（内核名单，enable_proactive=0 也算不合格） ─────────────────────────
def test_not_eligible_downgrades(env):
    env.monkeypatch.setattr(arbiter, "get_active_characters", _const([]))
    assert _gate(env) == "not_eligible"
    _followup(env)
    _assert_downgraded(env, "not_eligible")


def test_eligible_character_passes(env):
    """「不该拦」：角色在内核合格名单、锁着、没冷却 → 照常回应。"""
    assert _gate(env) is None
    _followup(env)
    assert len(env.sent) == 1
    assert env.sent[0][4] == "privacy_reply"
    assert env.log.infos == []          # 没降级就不该有降级痕迹


# ───────────────────────── ② 发送侧复检：冷却 / 锁屏（读端点同款口径） ─────────────────────────
def test_cooldown_hit_downgrades(env):
    """并发重复提交：上一次申请还在 2 分钟冷却里 → 不重复私信。"""
    env.monkeypatch.setattr(pv, "_cooldown_remaining", _const(119))
    assert _gate(env) == "cooldown"
    _followup(env)
    _assert_downgraded(env, "cooldown")


def test_cooldown_zero_allows(env):
    """「不该拦」：冷却 0 秒（上一发早已出冷却）→ 仍应发出。"""
    env.monkeypatch.setattr(pv, "_cooldown_remaining", _const(0))
    assert _gate(env) is None
    _followup(env)
    assert len(env.sent) == 1


def test_lock_switch_off_still_sends(env):
    """隐私上锁本就关着（读端点 locked=False）→ 发送侧**不看锁状态**，照常回应。

    Codex 2026-09-29 拍板收窄：E13 只补「资格降级 + 冷却复检」，不把「上锁/已解锁」当发送闸 ——
    高信任用户走「自动同意并自动关闭隐私上锁」时必然 locked=False，据此拦截会静默掐掉这条回应。
    """
    async def _off(db, character_id):
        return SimpleNamespace(privacy_lock_enabled=False)
    env.monkeypatch.setattr(pv, "_get_settings", _off)
    assert _gate(env) is None
    _followup(env)
    assert len(env.sent) == 1


def test_existing_unlock_window_still_sends(env):
    """本次申请之前就已处于解锁有效期内 → 同样**不拦**（发送侧不看锁状态，只判冷却）。"""
    env.monkeypatch.setattr(pv, "_active_unlock", _const(_FUTURE))
    assert _gate(env) is None
    _followup(env)
    assert len(env.sent) == 1


def test_gate_excludes_own_request_row(env):
    """复检必须按本次申请行 id 排除自己（否则刚落库的审批必然命中冷却/解锁）。"""
    _gate(env, request_id=77)
    assert env.calls["cooldown_exclude_id"] == 77
    assert env.calls["unlock_exclude_id"] == 77


def test_read_endpoint_and_send_side_share_one_criteria(env):
    """读端点与发送侧共用同一 ``_lock_view``（不复制第二套阈值）。"""
    seen = []

    async def _spy(db, character_id, user_id, target, now, exclude_request_id=None):
        seen.append(exclude_request_id)
        return {"enabled": True, "locked": True, "unlock_until": None, "cooldown": 0}
    env.monkeypatch.setattr(pv, "_lock_view", _spy)
    env.monkeypatch.setattr(pv, "_check_owned", _const(SimpleNamespace(id=CID, name="小爱")))
    asyncio.run(pv.get_privacy_status(CID, target="diary", db=_FakeDB(), user_id=UID))
    assert seen == [None]
    assert asyncio.run(pv._send_gate_reason(_FakeDB(), CID, UID, "diary")) is None
    assert len(seen) == 2          # 发送侧复检走同一个函数
    import inspect
    src = inspect.getsource(pv._send_gate_reason)
    assert "_lock_view" in src and "COOLDOWN" not in src   # 闸里没有自带阈值
    assert pv.COOLDOWN_SECONDS == 120                      # 阈值全模块只此一处


# ───────────────────────── 异常 / 脏身份：一律降级（宁可不发寒暄） ─────────────────────────
def test_gate_error_downgrades(env):
    env.monkeypatch.setattr(arbiter, "get_active_characters", _boom)
    assert _gate(env) == "kernel_gate_error"
    _followup(env)
    assert env.sent == [] and env.log.hit("kernel_gate_error")


def test_lock_view_error_downgrades(env):
    env.monkeypatch.setattr(pv, "_cooldown_remaining", _boom)
    assert _gate(env) == "kernel_gate_error"
    assert env.sent == []


def test_bad_identity_downgrades():
    assert asyncio.run(pv._send_gate_reason(_FakeDB(), 0, UID, "diary")) == "bad_identity"
    assert asyncio.run(pv._send_gate_reason(_FakeDB(), None, UID, "diary")) == "bad_identity"
    assert asyncio.run(pv._send_gate_reason(_FakeDB(), CID, 0, "diary")) == "bad_identity"


# ───────────────────────── 端点：降级绝不吞掉「申请结果」本身 ─────────────────────────
def _endpoint(env, followup_spy=None):
    async def _owned(db, character_id, user_id, lang="zh"):
        return SimpleNamespace(id=character_id, name="小爱")

    async def _rate(db, character_id):
        return 1.0

    env.monkeypatch.setattr(pv, "_check_owned", _owned)
    env.monkeypatch.setattr(pv, "_nickname", _const("阿泽"))
    env.monkeypatch.setattr(pv, "_weighted_approve_rate", _rate)
    env.monkeypatch.setattr(pv, "_gen_reply", _const(("给你看就是了", "开心", 2.0)))
    env.monkeypatch.setattr(pv, "_write_summary_memory", _const(None))
    if followup_spy is not None:
        env.monkeypatch.setattr(pv, "_send_chat_followup", followup_spy)
    db = _FakeDB()
    out = asyncio.run(pv.request_privacy_access(
        CID, pv.RequestIn(target="diary"), db=db, user_id=UID, lang="zh",
    ))
    return out, db


def test_api_still_answers_when_downgraded(env):
    """角色不合格 → 接口照旧回 approved / ai_reply / 解锁截止，只有私信寒暄被减掉。"""
    env.monkeypatch.setattr(arbiter, "get_active_characters", _const([]))
    out, _db = _endpoint(env)
    assert out["approved"] is True
    assert out["ai_reply"] == "给你看就是了"
    assert out["unlock_until"] is not None
    assert env.sent == [] and env.log.hit("not_eligible")


def test_api_passes_own_row_id_to_followup(env):
    got = {}

    async def _spy(db, character_id, user_id, name, nickname, target_cn, approved,
                   target="diary", request_id=None):
        got.update({"target": target, "request_id": request_id})
    out, _db = _endpoint(env, followup_spy=_spy)
    assert out["approved"] is True
    assert got == {"target": "diary", "request_id": 77}


def test_followup_failure_still_never_breaks_request(env):
    """私信链路抛异常（原语义）：申请结果仍返回，且不吞成 500。"""
    async def _raise(*_a, **_k):
        raise RuntimeError("发送炸了")
    env.monkeypatch.setattr(pv, "_send_chat_followup", _raise)
    out, _db = _endpoint(env)
    assert out["approved"] is True and env.sent == []


# ───────────────────────── 复检查询真的排除了自己（SQL 层） ─────────────────────────
def _captured_stmt(**kwargs):
    captured = {}

    class _Db:
        async def execute(self, stmt):
            captured["stmt"] = stmt
            return _Result(None)
    asyncio.run(pv._cooldown_remaining(_Db(), CID, UID, "diary", now_naive_utc(), **kwargs))
    return str(captured["stmt"].compile())


def test_cooldown_query_excludes_own_id():
    """复检查询带上 ``id != 本次申请行``：只数自己之前那次，不被自己刚落库的记录撞掉。"""
    assert "privacy_requests.id != " in _captured_stmt(exclude_id=77)


def test_cooldown_query_keeps_own_id_when_not_excluding():
    """读端点不传 exclude → 查询口径与改动前一致（不扩大拦截）。"""
    assert "privacy_requests.id != " not in _captured_stmt()
