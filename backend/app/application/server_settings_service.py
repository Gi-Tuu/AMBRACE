# -*- coding: utf-8 -*-
"""服务器级字符串配置服务（server_settings KV，账号独立 P2，2026-09-19）。

口径（契约 §2）：
- 字符串/枚举型服务器配置（本轮＝注册策略）只落 ``server_settings``，**禁止塞进 AGENT_FLAGS**
  ——该字典是 bool 开关注册表（flag_service 的类型防护会拒绝非 bool 键热切，混入字符串会污染
  开关页展示与热更新链路）；
- 缺行 = 默认值：注册策略默认 ``open``（与现状一致），故上线不改变任何现有注册行为；
- 读失败 fail-open 到默认值，不让配置表缺失把注册链路打挂。
"""
from sqlalchemy import select

from app.models.config import ServerSetting
from app.utils.logger import get_logger

_logger = get_logger("server_settings")

# ── 注册策略（契约 §1.4）──────────────────────────────────────────────────────
REGISTRATION_MODE_KEY = "registration_mode"
REGISTRATION_MODE_OPEN = "open"
REGISTRATION_MODE_INVITE_ONLY = "invite_only"
REGISTRATION_MODE_CLOSED = "closed"
REGISTRATION_MODES = (
    REGISTRATION_MODE_OPEN,
    REGISTRATION_MODE_INVITE_ONLY,
    REGISTRATION_MODE_CLOSED,
)
DEFAULT_REGISTRATION_MODE = REGISTRATION_MODE_OPEN

# ── 服务器身份固定灰度档位（批 0-3 M0-a，2026-09-27）───────────────────────────
# off＝签发但不校验（默认，既有响应逐字节不变）；shadow＝出签但缺签不拒、只记日志；
# enforce＝出签，且「带了 nonce 却出不了签」的请求才被拒（详见 app/server_identity.py）。
IDENTITY_ENFORCE_MODE_KEY = "identity_enforce_mode"
IDENTITY_ENFORCE_MODES = ("off", "shadow", "enforce")
DEFAULT_IDENTITY_ENFORCE_MODE = "off"


async def get_setting(db, key: str, default: str | None = None) -> str | None:
    """读一条字符串配置；缺行/空值 → default。"""
    row = (
        await db.execute(select(ServerSetting).where(ServerSetting.key == key))
    ).scalar_one_or_none()
    if row is None or row.value is None:
        return default
    return row.value


async def set_setting(db, key: str, value: str | None) -> None:
    """写一条字符串配置（缺行新建）；不 commit，由调用方提交（与审计同一事务）。"""
    row = (
        await db.execute(select(ServerSetting).where(ServerSetting.key == key))
    ).scalar_one_or_none()
    if row is None:
        db.add(ServerSetting(key=key, value=value))
    else:
        row.value = value
    await db.flush()


async def get_registration_mode(db) -> str:
    """注册策略：缺行/非法值/读失败 → open（与现状一致，fail-open）。"""
    try:
        mode = await get_setting(db, REGISTRATION_MODE_KEY, DEFAULT_REGISTRATION_MODE)
    except Exception as e:  # noqa: BLE001 —— 配置表缺失/被锁不阻塞注册链路
        _logger.warning("registration mode read failed: %s", e)
        return DEFAULT_REGISTRATION_MODE
    mode = str(mode or "").strip().lower()
    return mode if mode in REGISTRATION_MODES else DEFAULT_REGISTRATION_MODE


async def set_registration_mode(db, mode: str) -> str:
    """写注册策略（非法值抛 ValueError，由路由层转 400）；不 commit。"""
    m = str(mode or "").strip().lower()
    if m not in REGISTRATION_MODES:
        raise ValueError("invalid registration mode: " + str(mode))
    await set_setting(db, REGISTRATION_MODE_KEY, m)
    return m


async def get_identity_enforce_mode(db) -> str:
    """身份校验灰度档位：缺行/脏值/读失败 → ``off``（默认零行为变化，与注册策略同风格）。"""
    try:
        mode = await get_setting(db, IDENTITY_ENFORCE_MODE_KEY, DEFAULT_IDENTITY_ENFORCE_MODE)
    except Exception as e:  # noqa: BLE001 —— 配置表缺失/被锁不打挂被签名的端点
        _logger.warning("identity enforce mode read failed: %s", e)
        return DEFAULT_IDENTITY_ENFORCE_MODE
    mode = str(mode or "").strip().lower()
    return mode if mode in IDENTITY_ENFORCE_MODES else DEFAULT_IDENTITY_ENFORCE_MODE
