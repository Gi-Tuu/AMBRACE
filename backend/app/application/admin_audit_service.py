# -*- coding: utf-8 -*-
"""服务器控制台审计服务（账号独立 P2，2026-09-19）。

契约 §0/§1.5：所有写动作必须落 ``admin_audit_log``，``GET /api/v1/admin/server/audit`` 可见。

- ``record``：同一事务内追加一行（调用方随后 commit）；``db=None`` 时自开会话并提交；
- ``list_entries``：按 created_at DESC（同秒用 id DESC 兜底）返回，附 actor_username；
- **fail-open**：审计写入/读取异常只记 _logger.warning，绝不影响业务写入与返回（审计是
  旁路留痕，不能因为审计表缺失/被锁把控制台的写操作整体打挂）；
- 脱敏：``api_key`` / ``password`` / ``token`` / ``secret`` 等键一律不进审计明文。
"""
import json
from typing import Any

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.admin import AdminAuditLog
from app.models.user import User
from app.utils.logger import get_logger

_logger = get_logger("admin_audit")

# 敏感键（递归命中即脱敏为 "***"；原值为空则记 None，保留「是否已配置」语义）
_REDACT_KEYS = frozenset({
    "api_key", "password", "password_hash", "token", "access_token",
    "secret", "auth_secret_key", "client_secret",
})

# 单条快照最大落库长度（TEXT 无硬上限，此处防异常大对象把审计表撑爆）
_MAX_SNAPSHOT = 4000


def redact(value: Any) -> Any:
    """递归脱敏：敏感键替换为 "***"（空值保留 None），其余原样。"""
    if isinstance(value, dict):
        return {
            k: ("***" if v else None) if k in _REDACT_KEYS else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _dump(value: Any) -> str | None:
    """快照 → JSON 字符串（脱敏 + 截断）；不可序列化/为空 → None。"""
    if value is None:
        return None
    try:
        text = json.dumps(redact(value), ensure_ascii=False, default=str)
    except Exception:
        return None
    return text[:_MAX_SNAPSHOT]


def _loads(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return text


async def record(
    db,
    actor_user_id: int | None,
    action: str,
    target: str | None = None,
    before: Any = None,
    after: Any = None,
) -> None:
    """落一条审计（fail-open）。

    ``db`` 非空：加入调用方事务（flush，由调用方 commit，保证「业务提交则审计提交」）；
    ``db`` 为空：自开会话并提交（App 侧服务器配置写接口等无 db 句柄的调用点）。
    """
    try:
        row = AdminAuditLog(
            actor_user_id=int(actor_user_id) if actor_user_id else None,
            action=str(action)[:64],
            target=(str(target)[:128] if target else None),
            before_json=_dump(before),
            after_json=_dump(after),
        )
        if db is not None:
            db.add(row)
            await db.flush()
        else:
            async with async_session_factory() as own:
                own.add(row)
                await own.commit()
    except Exception as e:  # noqa: BLE001 —— 审计失败绝不影响业务
        _logger.warning("audit record failed action=%s target=%s: %s", action, target, e)


async def list_entries(db, limit: int = 100) -> list[dict]:
    """最近审计条目（created_at DESC，id DESC 兜底）：附 actor_username，before/after 解 JSON。"""
    try:
        n = max(1, min(int(limit or 100), 500))
    except (TypeError, ValueError):
        n = 100
    try:
        rows = (
            await db.execute(
                select(AdminAuditLog)
                .order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
                .limit(n)
            )
        ).scalars().all()
        actor_ids = {r.actor_user_id for r in rows if r.actor_user_id}
        name_map: dict[int, str] = {}
        if actor_ids:
            users = (
                await db.execute(select(User.id, User.username).where(User.id.in_(actor_ids)))
            ).all()
            name_map = {u.id: u.username for u in users}
    except Exception as e:  # noqa: BLE001 —— 审计读取失败返回空，不打挂控制台
        _logger.warning("audit list failed: %s", e)
        return []
    return [
        {
            "id": r.id,
            "actor_user_id": r.actor_user_id,
            "actor_username": name_map.get(r.actor_user_id) if r.actor_user_id else None,
            "action": r.action,
            "target": r.target,
            "before": _loads(r.before_json),
            "after": _loads(r.after_json),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
