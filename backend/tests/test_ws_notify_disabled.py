# -*- coding: utf-8 -*-
"""P2-7 回归（2026-09-28 修复）：通知长连接要拒绝禁用 / 已删账号。

修复前 /api/v1/system/notifications/ws 只验 JWT 的 user_id，不查 users.disabled_at
⇒ 被控制台禁用（或已进回收站）的账号仍能建连并持续收推送（HTTP 侧是会被拒的）。
"""
import asyncio
from datetime import datetime, timezone


from app.api.system import notifications_ws
from app.auth.config import create_token
from app.db.database import async_session_factory
from app.models.user import User
from _dbclone import run_unit_of_work

ACTIVE_ID = 991
DISABLED_ID = 992


class FakeWS:
    """最小替身：记录 close / accept，receive_json 抛错以结束循环。"""
    def __init__(self, token):
        self.query_params = {"token": token}
        self.closed = None
        self.accepted = False
        self.sent = []

    async def close(self, code=1000):
        self.closed = code

    async def accept(self):
        self.accepted = True

    async def send_json(self, payload):
        self.sent.append(payload)

    async def receive_json(self):
        raise RuntimeError("client gone")


async def _seed(uid, disabled):
    """种账号。A36（2026-10-07）：写单元走 ``run_unit_of_work``——本函数正是 A21 锁族
    10-07 的实测现场（``db.commit()`` 撞锁被包装成 PendingRollbackError）；单元先查后写＝幂等，
    撞锁那次已被整体回滚，换干净会话重放不会写两遍。
    """
    from sqlalchemy import select

    async def _unit(db):
        row = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
        dt = datetime.now(timezone.utc).replace(tzinfo=None) if disabled else None
        if row is None:
            db.add(User(id=uid, username="p27_%d" % uid, nickname="p27_%d" % uid,
                        password_hash="x", disabled_at=dt))
        else:
            row.disabled_at = dt
        await db.commit()

    await run_unit_of_work(async_session_factory, _unit)


def test_禁用账号被拒且不建连():
    asyncio.run(_seed(DISABLED_ID, True))
    ws = FakeWS(create_token(DISABLED_ID))
    asyncio.run(notifications_ws(ws))
    assert ws.closed == 4403, "禁用账号应以 4403 关闭"
    assert ws.accepted is False, "禁用账号不应被 accept"


def test_正常账号可建立连接():
    asyncio.run(_seed(ACTIVE_ID, False))
    ws = FakeWS(create_token(ACTIVE_ID))
    asyncio.run(notifications_ws(ws))
    assert ws.accepted is True, "正常账号应被 accept"
    assert ws.closed is None
    assert ws.sent and ws.sent[0].get("type") == "connected"


def test_无效token_仍按_4401_拒绝():
    ws = FakeWS("not-a-jwt")
    asyncio.run(notifications_ws(ws))
    assert ws.closed == 4401
    assert ws.accepted is False
