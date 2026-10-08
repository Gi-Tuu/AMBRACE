# -*- coding: utf-8 -*-
"""B15 可测性守卫：两档释放必须留痕（`route=relational_drive_release`），且**纯留痕零行为变化**。

为什么补这条：B15 的判据是「发送后 level 下降、回复后清零」，但
① `relational_drives.level` 是**懒结算的连续值**（释放后任何一次 settle 都会把增量加回去），
② 现网 `app.log` 里 "release" 命中 **0 条**、全表只留 2 个 `last_released_at` ＋ 1 个 ratio，
⇒ 事后既读不到"下降"也读不到"清零"，这条观察窗口**根本没牙**（同族教训：灰度对象产不出被测现象）。
本守卫钉的是"释放瞬间的 before/after 被记下来了"，不是"释放对不对"（那是 drives 纯函数的既有守卫管的）。

纪律：全桩（不建库、不连生产、不调模型）；`enqueue_task_log` 用猴子补丁收集。
"""
import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import app.agent.trace as trace_mod
from app.application import relational_drive_service as rds

NOW = datetime(2026, 10, 8, 5, 0, 0)


class _FakeDB:
    def __init__(self):
        self.flushed = 0

    async def flush(self):
        self.flushed += 1


def _row(level=20.0, released_at=None, ratio=0.0, drive="longing"):
    return SimpleNamespace(level=level, last_released_at=released_at,
                           last_released_ratio=ratio, drive_key=drive)


def _capture(monkeypatch):
    got = []

    def _fake(**kw):
        got.append(kw)

    monkeypatch.setattr(trace_mod, "enqueue_task_log", _fake)
    return got


def _stubs(monkeypatch, row, *, enabled=True, msg=None, shadow=True):
    async def _settle(db, cid, uid, now=None):
        return {}

    async def _fetch(db, cid, uid, key):
        return row

    async def _recent(db, cid, sid):
        return msg

    monkeypatch.setattr(rds, "settle", _settle)
    monkeypatch.setattr(rds, "_fetch_row", _fetch)
    monkeypatch.setattr(rds, "_recent_sent_outreach", _recent)
    monkeypatch.setattr(rds, "release_enabled", lambda kind, character_id=None: enabled)
    monkeypatch.setattr(rds, "shadow_enabled", lambda: shadow)


# ── 一、开口释放：一条 trace，带 before/after/ratio ─────────────────────────
def test_开口释放落恰好一条留痕(monkeypatch):
    got = _capture(monkeypatch)
    row = _row(level=20.0)
    _stubs(monkeypatch, row)
    res = asyncio.run(rds.apply_open_release(_FakeDB(), 13, 3, "check_in", now=NOW))
    assert res and res["drive"] == "longing"
    assert len(got) == 1, got
    rec = got[0]
    assert rec["route"] == rds.RELEASE_TRACE_ROUTE
    assert rec["trigger"] == "outreach_open"
    payload = json.loads(rec["steps_json"])[0]
    assert payload["kind"] == "open" and payload["level_before"] == 20.0
    assert payload["level_after"] < payload["level_before"], "留痕里的 after 必须真的低于 before（＝能看出'下降'）"
    assert payload["ratio"] > 0


def test_全额释放落留痕且after为零(monkeypatch):
    got = _capture(monkeypatch)
    row = _row(level=17.5)
    msg = SimpleNamespace(id=9001, created_at=NOW - timedelta(hours=1),
                          extra_meta=json.dumps({"intent": "check_in"}))
    _stubs(monkeypatch, row, msg=msg)
    res = asyncio.run(rds.apply_reply_release(_FakeDB(), 13, 3, 11, now=NOW))
    assert res is not None, res
    assert len(got) == 1, got
    payload = json.loads(got[0]["steps_json"])[0]
    assert payload["kind"] == "full" and payload["level_after"] == 0.0
    assert payload["level_before"] > 0.0, "before 必须留底——'回复后清零'要有清零前的参照才叫证据"
    assert payload["attributed_msg_id"] == 9001, "要能反查是哪条主动消息被回应"
    assert got[0]["trigger"] == "outreach_full"


# ── 二、闸关 / 无对象 ⇒ 零留痕（逐字节旧行为） ─────────────────────────────
def test_闸关时不留痕(monkeypatch):
    got = _capture(monkeypatch)
    _stubs(monkeypatch, _row(), enabled=False)
    assert asyncio.run(rds.apply_open_release(_FakeDB(), 13, 3, "check_in", now=NOW)) is None
    assert asyncio.run(rds.apply_reply_release(_FakeDB(), 13, 3, 11, now=NOW)) is None
    assert got == [], f"闸关却留了痕：{got}"


def test_没有释放发生就不留痕(monkeypatch):
    got = _capture(monkeypatch)
    _stubs(monkeypatch, _row(), msg=None)          # 没有可归属的主动消息
    assert asyncio.run(rds.apply_reply_release(_FakeDB(), 13, 3, 11, now=NOW)) is None
    # 反查不到 drive 的 intent 同样不落
    _stubs(monkeypatch, _row())
    assert asyncio.run(rds.apply_open_release(_FakeDB(), 13, 3, "不存在的意图", now=NOW)) is None
    assert got == []


def test_留痕失败不影响主链路(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("trace down")

    monkeypatch.setattr(trace_mod, "enqueue_task_log", _boom)
    _stubs(monkeypatch, _row(level=20.0))
    res = asyncio.run(rds.apply_open_release(_FakeDB(), 13, 3, "check_in", now=NOW))
    assert res and res["level_before"] == 20.0, "trace 炸了也必须照常返回（水位已改＝主链路不受影响）"


# ── 三、列宽与唯一写入点（防"第二条路径漏留/重复留"） ─────────────────────
def test_route_与_trigger_不超列宽():
    assert len(rds.RELEASE_TRACE_ROUTE) <= 30
    assert len("outreach_open") <= 20 and len("outreach_full") <= 20


def test_留痕写入点全仓唯一():
    from pathlib import Path
    app = Path(rds.__file__).resolve().parent.parent
    users = [p.name for p in app.rglob("*.py")
             if "relational_drive_release" in p.read_text(encoding="utf-8")
             and p.name != "relational_drive_service.py"]
    assert users == [], f"第二个写入点会让 B15 计数翻倍：{users}"
    src = Path(rds.__file__).read_text(encoding="utf-8")
    assert src.count("_trace_release(") == 3, "一处定义 ＋ 两处调用（open／full），多一处就得重查口径"
