"""X7-M4a 行动能力 API（裁决侧，**零真实执行**）。

三个行动端点全部要求登录（``get_current_user_id``），并且**一律 200 + allowed/reason**——被拒不抛
4xx，便于插件侧直接读 ``reason`` 判断被哪一层闸门挡住（限流/熔断/未授权/目标不在白名单…）。

- ``POST /api/v1/device/actions``：**内置通道**提交一次行动意图 → 裁决。身份由本端点固定为内置
  （``builtin``），**不接受请求体自称**——请求体带非空 ``plugin`` 一律拒
  ``invalid_intent:plugin_not_allowed``（M4c-1 收口：堵掉「自称内置即绕过插件授权闸门」）。
  本批执行体由 App 自己承担，批准时下发 ``action_token``（干跑为 ``dry_run``）；
  **插件提交不走这里**，走 ``POST /api/v1/plugins/{name}/bridge`` 的 ``device_action`` api
  （身份取路径名 ``{name}``，见 :mod:`app.application.plugin_bridge_service`）；
- ``GET /api/v1/device/actions/pending``：App 取当前账号的待执行动作；
- ``POST /api/v1/device/actions/{token}/result``：App 回报执行结果（只能回报自己的）。

M4b-1 追加两个**目标白名单管理**端点（闸门④ 的配置入口，挂 ``require_server_admin``，非管理员 403）；
M4c-3 起两份名单**落库持久**（``device_action_targets`` / ``device_action_plugins``，重启不再清零）：

- ``GET  /api/v1/device/actions/targets``：当前管理员所属家庭的白名单（排序输出）；
- ``POST /api/v1/device/actions/targets``：放开一个目标包名（校验失败/超容量同样 200 + ``reason``；
  单租户上限按**库里的现有条数**判定，重复添加幂等、不占新额度）；
- ``GET  /api/v1/device/actions/plugins``：被灰度放开行动能力的插件（库里 ∪ 编译期常量，排序输出）；
- ``POST /api/v1/device/actions/plugins``：放开一个插件（同上口径，全局上限 50 个）。

租户口径：``tenant_id`` 由当前账号解析为家庭根（``family_service.get_family_root_id``，与 A2/M6
插件归户同口径），解析不到即 ``None`` —— 插件级闸门与目标白名单都按 ``None`` fail-closed 拒绝；
白名单管理端点在解析不到时直接拒绝写入（``tenant_unresolved``）。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from pydantic import ValidationError

from app.auth.deps import get_current_user_id, require_server_admin
from app.db import database
from app.device.actions import (
    BUILTIN_CALLER,
    REASON_INVALID_INTENT,
    REASON_PLUGIN_NOT_ALLOWED,
    STATUS_DENIED,
    ActionIntent,
    allow_plugin_actions,
    allow_target,
    configured_plugins,
    configured_targets,
    decide_action,
    invalid_intent_reason,
    report_result,
    take_pending,
)
from app.utils.logger import get_logger

router = APIRouter(prefix="/api/v1/device", tags=["Device Actions"])

_logger = get_logger("api.device_actions")

# ── 目标白名单入参约束（M4b-1）──
# 包名形态：首段字母开头 + 至少一个点 + 各段 [A-Za-z0-9_]（含空格/分号/斜杠的一律判非法）
TARGET_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
TARGET_MAX_LEN = 128
MAX_TARGETS_PER_TENANT = 20      # 单租户白名单容量上限（超出即拒，不写入）

# ── 插件灰度名单入参约束（M4c-3）──
# 插件名与 manifest 里的 name 同形（目录名口径：字母数字起头，可含 _ . -），≤64（与列宽一致）
PLUGIN_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MAX_PLUGINS_GRAYLISTED = 50      # 灰度名单容量上限（含编译期常量，超出即拒，不写入）


async def _resolve_tenant_id(user_id: int) -> int | None:
    """当前账号的家庭根（= 租户）；解析失败返回 None（调用方据此 fail-closed 拒绝）。"""
    from app.application.family_service import get_family_root_id

    try:
        async with database.async_session_factory() as db:
            return await get_family_root_id(db, user_id)
    except Exception as e:  # 读库失败不得放行行动裁决的租户判定
        _logger.warning("行动裁决解析租户失败 user=%s: %s", user_id, e)
        return None


@router.post("/actions")
async def submit_action(
    payload: dict,
    user_id: int = Depends(get_current_user_id),
):
    """提交行动意图 → 闸门裁决（不执行）。被拒同样 200，理由在 ``reason``。

    **身份固定为内置**（:data:`~app.device.actions.BUILTIN_CALLER`），不由请求体决定：本端点是
    App 自己的通道，插件提交走 ``/api/v1/plugins/{name}/bridge`` 的 ``device_action`` api。
    请求体带非空 ``plugin`` 即拒 ``invalid_intent:plugin_not_allowed``（不接受自称）；``plugin``
    为空或缺省视同「未声明身份」，按内置处理。
    """
    payload = payload or {}
    claimed = str(payload.get("plugin") or "").strip()
    body = {k: v for k, v in payload.items() if k != "plugin"}
    if claimed:
        _logger.info("action submit denied (self-declared identity) user=%s plugin=%s",
                     user_id, claimed)
        return {"allowed": False,
                "reason": f"{REASON_INVALID_INTENT}:{REASON_PLUGIN_NOT_ALLOWED}",
                "dry_run": bool(body.get("dry_run") or False), "status": STATUS_DENIED}
    try:
        intent = ActionIntent.model_validate(body)
    except ValidationError as e:
        reason = invalid_intent_reason(e)
        _logger.info("action submit rejected user=%s reason=%s", user_id, reason)
        return {"allowed": False, "reason": reason, "dry_run": bool(body.get("dry_run") or False),
                "status": STATUS_DENIED}

    tenant_id = await _resolve_tenant_id(user_id)
    decision = await decide_action(user_id=user_id, tenant_id=tenant_id,
                                   plugin_name=BUILTIN_CALLER, intent=intent)
    resp = {"allowed": decision.allowed, "reason": decision.reason,
            "dry_run": decision.dry_run, "status": decision.status}
    if decision.action_token:
        resp["action_token"] = decision.action_token
    return resp


@router.get("/actions/pending")
async def get_pending_actions(user_id: int = Depends(get_current_user_id)) -> list[dict]:
    """App 取当前账号的待执行动作（跨账号取不到：队列按 user_id 过滤）。"""
    return take_pending(user_id)


@router.post("/actions/{token}/result")
async def post_action_result(
    token: str,
    payload: dict,
    user_id: int = Depends(get_current_user_id),
):
    """App 回报执行结果（本批无执行体，回报只用于打通链路 + 驱动熔断计数）。"""
    ok = bool(payload.get("ok"))
    detail = str(payload.get("detail") or "")
    found = report_result(token, ok, detail, user_id=user_id)
    return {"ok": found}


def _valid_target(target: str) -> bool:
    """目标包名是否合形（非空、≤``TARGET_MAX_LEN``、整串匹配包名形态）。

    用 ``fullmatch`` 而不是 ``match``：Python 的 ``$`` 允许「串尾换行之前」成立，
    ``match`` 会放过带尾巴的脏输入；本函数收到的字符串已由调用方 strip，但形态校验不依赖那个前提。
    """
    return (len(target) <= TARGET_MAX_LEN
            and TARGET_PATTERN.fullmatch(target) is not None)


@router.get("/actions/targets")
async def list_action_targets(admin_user_id: int = Depends(require_server_admin)) -> dict:
    """当前管理员所属家庭的目标白名单（排序输出；落库持久，重启不丢）。

    租户解析不到时返回空列表（与闸门④ 的 fail-closed 同口径：拿不到租户＝全拒）。
    """
    tenant_id = await _resolve_tenant_id(admin_user_id)
    return {"targets": sorted(await configured_targets(tenant_id))}


@router.post("/actions/targets")
async def allow_action_target(
    payload: dict,
    admin_user_id: int = Depends(require_server_admin),
) -> dict:
    """放开一个行动目标（闸门④ 默认无行＝全拒，运维据此逐条放行）。

    口径与本文件其它端点一致：**校验失败同样 200 + 机器可读 ``reason``**，不抛 4xx。
    写入的是 ``device_action_targets``（M4c-3 起落库，重启不清零）；单租户最多
    :data:`MAX_TARGETS_PER_TENANT` 个目标（按**库里现有条数**判定），超出即拒且不写入
    （重复添加已有目标幂等、不占新额度）。
    """
    tenant_id = await _resolve_tenant_id(admin_user_id)
    if tenant_id is None:
        _logger.warning("目标白名单写入被拒：租户解析失败 admin=%s", admin_user_id)
        return {"ok": False, "reason": "tenant_unresolved"}

    target = str(payload.get("target") or "").strip()
    current = sorted(await configured_targets(tenant_id))
    if not _valid_target(target):
        _logger.info("目标白名单入参非法 admin=%s target=%r", admin_user_id, target)
        return {"ok": False, "reason": "invalid_target", "targets": current}
    if len(set(current) | {target}) > MAX_TARGETS_PER_TENANT:
        _logger.warning("目标白名单超容量 admin=%s tenant=%s limit=%d",
                        admin_user_id, tenant_id, MAX_TARGETS_PER_TENANT)
        return {"ok": False, "reason": "too_many_targets", "targets": current}

    if not await allow_target(tenant_id, target):
        _logger.warning("目标白名单写库失败 admin=%s tenant=%s target=%s",
                        admin_user_id, tenant_id, target)
        return {"ok": False, "reason": "store_unavailable", "targets": current}
    targets = sorted(await configured_targets(tenant_id))
    _logger.info("目标白名单更新 admin=%s tenant=%s target=%s total=%d",
                 admin_user_id, tenant_id, target, len(targets))
    return {"ok": True, "targets": targets}


@router.get("/actions/plugins")
async def list_graylisted_plugins(admin_user_id: int = Depends(require_server_admin)) -> dict:
    """被灰度放开行动能力的插件全集（库里 ∪ 编译期常量 ``PLUGIN_ACTION_ENABLED_PLUGINS``，排序）。

    闸门 ③a 的总闸（``device_actions_plugin_enabled``）另有其行，本端点只列「哪些插件在名单里」。
    """
    return {"plugins": sorted(await configured_plugins())}


@router.post("/actions/plugins")
async def allow_plugin_graylist(
    payload: dict,
    admin_user_id: int = Depends(require_server_admin),
) -> dict:
    """把一个插件放进行动灰度名单（闸门 ③a 的逐插件放开入口，全局不分租户）。

    与目标白名单同一套口径：**校验失败同样 200 + 机器可读 ``reason``**。名单落
    ``device_action_plugins``（重启不丢）；总量上限 :data:`MAX_PLUGINS_GRAYLISTED`，超出即拒不写入；
    重复添加幂等。**这只决定「哪个插件在灰度范围内」**——总闸关着仍然一条都不放。
    """
    name = str(payload.get("plugin") or "").strip()
    current = sorted(await configured_plugins())
    if not PLUGIN_NAME_PATTERN.fullmatch(name):
        _logger.info("插件灰度入参非法 admin=%s plugin=%r", admin_user_id, name)
        return {"ok": False, "reason": "invalid_plugin", "plugins": current}
    if len(set(current) | {name}) > MAX_PLUGINS_GRAYLISTED:
        _logger.warning("插件灰度名单超容量 admin=%s limit=%d", admin_user_id, MAX_PLUGINS_GRAYLISTED)
        return {"ok": False, "reason": "too_many_plugins", "plugins": current}

    if not await allow_plugin_actions(name):
        _logger.warning("插件灰度名单写库失败 admin=%s plugin=%s", admin_user_id, name)
        return {"ok": False, "reason": "store_unavailable", "plugins": current}
    plugins = sorted(await configured_plugins())
    _logger.info("插件灰度名单更新 admin=%s plugin=%s total=%d", admin_user_id, name, len(plugins))
    return {"ok": True, "plugins": plugins}
