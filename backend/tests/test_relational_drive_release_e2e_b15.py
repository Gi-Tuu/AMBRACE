# -*- coding: utf-8 -*-
"""B15 留痕**端到端**守卫：`_trace_release` → `enqueue_task_log` → `spawn_background` → `write_task_log`
必须在真实表 `agent_task_logs` 里落一行。

为什么单独一份：`test_relational_drive_release_trace_b15.py` 把 `enqueue_task_log` **整个打了桩**，
钉的是"调用发生了、payload 长对了"，但**从没证明这行真能进库**——而 `trace.py` 全程 fail-open
（列名不匹配／列宽超／没有运行中的事件循环都只写一条 WARNING 就吞掉）。B15 现在生产读数＝0 条，
"0 条"既可能是"确实没释放"，也可能是"通道坏了"，**这两者在现有守卫下区分不出来**（同族教训：
看不见的那一档就是下次栽的地方）。本文件把整条链在临时库里跑通，让"到点仍 0 条才回头查接线"
这条判据真的有资格被使用。

纪律：临时库用 `_dbclone`（页级克隆、NullPool、生产同款 PRAGMA），**绝不连生产库**、不调模型。
"""
import asyncio
import json
from types import SimpleNamespace

from _dbclone import clone_engine, make_session_factory

import app.agent.trace as trace_mod
import app.db.database as db_mod
from app.application import relational_drive_service as rds


def _now():
    import datetime as _dt
    return _dt.datetime(2026, 10, 8, 5, 0, 0)


class _FakeDB:
    """被测编排只把 db 透给 `settle`/`_fetch_row`（两者都打桩），自己只调 `flush`。"""

    def __init__(self):
        self.flushed = 0

    async def flush(self):
        self.flushed += 1


def _row(level=20.0):
    return SimpleNamespace(level=level, last_released_at=None,
                           last_released_ratio=0.0, drive_key="longing")


def _stub_orchestration(monkeypatch, row):
    async def _settle(db, cid, uid, now=None):
        return {}

    async def _fetch(db, cid, uid, key):
        return row

    monkeypatch.setattr(rds, "settle", _settle)
    monkeypatch.setattr(rds, "_fetch_row", _fetch)
    monkeypatch.setattr(rds, "release_enabled", lambda kind, character_id=None: True)
    monkeypatch.setattr(rds, "shadow_enabled", lambda: True)


def test_开口释放的留痕真能落到_agent_task_logs(monkeypatch, tmp_path):
    """端到端：跑真编排（stub 只替换 DB 读写的水位部分），trace 走**未打桩**的完整链路。"""
    engine = clone_engine(str(tmp_path / "e2e.db"))
    factory = make_session_factory(engine)
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    assert trace_mod.enqueue_task_log.__module__ == "app.agent.trace"  # 本例不许打桩 trace

    _stub_orchestration(monkeypatch, _row(level=20.0))

    async def _count_rows():
        async with factory() as db:
            from sqlalchemy import text
            return int((await db.execute(
                text("SELECT COUNT(*) FROM agent_task_logs WHERE route='relational_drive_release'")
            )).scalar())

    async def _main():
        res = await rds.apply_open_release(_FakeDB(), 13, 3, "check_in",
                                           now=_now())
        # fire-and-forget：给事件循环把后台任务跑完的机会（轮询而不是固定 sleep）
        for _ in range(80):
            if await _count_rows():
                break
            await asyncio.sleep(0.02)
        rows = []
        async with factory() as db:
            from sqlalchemy import text
            rows = list((await db.execute(text(
                "SELECT character_id, user_id, trigger, route, steps_json, status "
                "FROM agent_task_logs WHERE route='relational_drive_release'"))).mappings())
        return res, rows

    try:
        res, rows = asyncio.run(_main())
    finally:
        asyncio.run(engine.dispose())

    assert res is not None and res["kind"] == "open", "编排本身没释放 ⇒ 本例前提失效（不是留痕坏了）"
    assert len(rows) == 1, f"留痕应当恰好落 1 行，实际 {len(rows)} 行：{rows}"
    rec = rows[0]
    assert rec["trigger"] == "outreach_open"
    assert rec["status"] == "ok"
    assert rec["character_id"] == 13 and rec["user_id"] == 3
    payload = json.loads(rec["steps_json"])[0]
    assert payload["level_after"] < payload["level_before"], "落库的必须能看出'下降'"


def test_全额释放的留痕带得上归属消息号(monkeypatch, tmp_path):
    engine = clone_engine(str(tmp_path / "e2e_full.db"))
    factory = make_session_factory(engine)
    monkeypatch.setattr(db_mod, "async_session_factory", factory)

    row = _row(level=17.5)
    msg = SimpleNamespace(id=9001, created_at=_now(), extra_meta=json.dumps({"intent": "check_in"}))

    async def _recent(db, cid, sid):
        return msg

    _stub_orchestration(monkeypatch, row)
    monkeypatch.setattr(rds, "_recent_sent_outreach", _recent)

    async def _main():
        res = await rds.apply_reply_release(_FakeDB(), 13, 3, 11, now=_now())
        for _ in range(80):
            async with factory() as db:
                from sqlalchemy import text
                n = int((await db.execute(text(
                    "SELECT COUNT(*) FROM agent_task_logs WHERE trigger='outreach_full'")
                )).scalar())
            if n:
                break
            await asyncio.sleep(0.02)
        async with factory() as db:
            from sqlalchemy import text
            rows = list((await db.execute(text(
                "SELECT steps_json FROM agent_task_logs WHERE trigger='outreach_full'")
            )).mappings())
        return res, rows

    try:
        res, rows = asyncio.run(_main())
    finally:
        asyncio.run(engine.dispose())

    assert res is not None, "全额释放编排返回 None ⇒ 前提失效"
    assert len(rows) == 1, rows
    payload = json.loads(rows[0]["steps_json"])[0]
    assert payload["kind"] == "full" and payload["level_after"] == 0.0
    assert payload["attributed_msg_id"] == 9001, "归属消息号必须真进库（判效要靠它反查）"


def test_牙在_字段名对不上时确实落不进行(tmp_path):
    """反向自证：`write_task_log` 全程 fail-open，所以若 kwargs 与表结构不匹配，
    上面两例会归零而不是假绿——这一例就是证明"它真的会归零"。"""
    engine = clone_engine(str(tmp_path / "e2e_teeth.db"))
    factory = make_session_factory(engine)

    async def _main():
        await trace_mod.write_task_log(
            character_id=13, user_id=3, session_id=None, trigger="outreach_open",
            route="relational_drive_release", steps_json="[]", status="ok",
            not_a_real_column=1,               # 故意塞一个模型上没有的字段
        )
        await asyncio.sleep(0.05)
        async with factory() as db:
            from sqlalchemy import text
            return int((await db.execute(text(
                "SELECT COUNT(*) FROM agent_task_logs WHERE route='relational_drive_release'")
            )).scalar())

    try:
        n = asyncio.run(_main())
    finally:
        asyncio.run(engine.dispose())

    assert n == 0, "字段对不上却仍落行 ⇒ 本文件的'落 1 行'没有牙"
