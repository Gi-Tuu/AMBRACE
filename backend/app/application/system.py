"""system 域对外门面（facade）：共享辅助（_require_admin / _require_server_admin /
_cfg_snapshot / _audit）在此，业务实现分居 usage_service / system_config_service /
feature_flag_service / context_budget_service / system_status_service /
system_backup_service，本文件对其做具名重导出。
"""
from fastapi import HTTPException

from app.i18n import tr_lang
from app.application.permission_service import is_admin_user
from app.utils.logger import get_logger

_logger = get_logger("application.system")


# A22 第一刀（2026-10-02）：用量/面板块下沉 usage_service，此处具名重导出。
# ⚠ api/admin.py 与 api/system.py 会经本模块取这些名字（api/system.py 用 _svc.<name> 模块属性）。
# ⚠ tests 在本模块替换 _read_usage_window / _cost_estimate / _TOKEN_PRICE_RANGES，故 usage_service 对这三处保留 _sys 回指。
from app.application.usage_service import (  # noqa: F401
    _USAGE_UNTAGGED, _USAGE_UNKNOWN, _PANEL_DEFAULT_DAYS, _PANEL_MIN_DAYS, _PANEL_MAX_DAYS,
    _CLIP_ROUTE, _CLIP_WINDOW_HOURS, _USAGE_ROUTE, _SECTION_ROUTE,
    BREAKDOWN_DEFAULT_SAMPLES, BREAKDOWN_MAX_SAMPLES, _BREAKDOWN_TURN_SECTIONS,
    _TOKEN_PRICE_RANGES, _PRICE_CURRENCY, _PER_MILLION_TOKENS,
    get_llm_usage, _usage_metrics_blank, _usage_window_bounds, _usage_window_descriptor,
    _read_usage_window, _aggregate_usage_rows, _usage_emit, usage_report,
    _panel_days_or_raise, _panel_estimated_segment, _panel_money_segment,
    _blank_usage_panel, usage_panel, update_llm_usage_limit, _unknown_usage,
    _empty_breakdown, _clamp_breakdown_samples, _aggregate_section_breakdown,
    _price_range_for, _cost_estimate,
)


# A22 第二刀（2026-10-02）：配置/探针块下沉 system_config_service，此处具名重导出。
# ⚠ api/system.py 用 _svc.<name> 模块属性调用；删一行就会断。
from app.application.system_config_service import (  # noqa: F401
    _API_CFG_FIELDS, _VLM_CFG_FIELDS, _IMAGE_CFG_FIELDS, _SPEECH_CFG_FIELDS, _MODALITY_LABELS,
    _task_cfg_payload, _get_task_cfg, get_api_config, update_api_config, get_server_api_config,
    update_server_api_config, get_task_llm_catalog, get_task_api_config,
    update_task_api_config, get_server_task_api_config, update_server_task_api_config,
    test_api_connection, _probe_chat, _probe_models_list, _probe_image, _probe_image_dashscope,
    _probe_speech, _status_code_of, _classify_probe_error, get_image_gen_server_config,
    update_image_gen_server_config, get_vlm_server_config, update_vlm_server_config,
    get_speech_server_config, update_speech_server_config, speech_preview,
)


# A22 第三刀（2026-10-02）：feature flag 与上下文预算各自下沉，此处具名重导出。
# ⚠ api/system.py 用 _svc.<name> 模块属性调用；agent/context_builder.py 函数内 import 本模块名字。
from app.application.feature_flag_service import (  # noqa: F401
    get_feature_flags, update_feature_flag,
)
from app.application.context_budget_service import (  # noqa: F401
    get_context_budget, read_account_context_budget_tier, set_context_budget_tier,
)


# A22 第四刀（2026-10-02）：状态/公告与备份各自下沉，此处具名重导出；本文件自此为薄壳。
# ⚠ api/system.py 用 _svc.<name> 模块属性调用；tests/test_backup_api.py 把 _load_backup_module 的桩打在 system 上。
from app.application.system_status_service import (  # noqa: F401
    _changelog_title, _get_lan_ip, _is_private_ipv4, _parse_changelog, get_updates,
    system_status, system_status_public,
)
from app.application.system_backup_service import (  # noqa: F401
    _backup_info, _load_backup_module, download_backup, trigger_backup,
)


async def _require_admin(user_id: int, lang: str = "zh") -> None:
    if not await is_admin_user(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "admin_config_only"))


async def _require_server_admin(user_id: int, lang: str = "zh") -> None:
    """服务器控制台管理员门禁（账号独立 P2，契约 §3）：写类服务器级端点用。

    ``is_admin``（家庭主账号，家庭内管理）与 ``server_admin``（服务器控制台管理员，跨家庭管
    服务器级配置）分离后，**写类**服务器级端点收紧为 server_admin（非 server_admin → 403）；
    **读类**（get_*）保持既有 _require_admin（is_admin），避免非 server_admin 账号的 App 页面
    因读接口 403 报错。判定走 permission_service.is_server_admin（DB 权威 + 30s 缓存 + env 兜底）。
    """
    from app.application.permission_service import is_server_admin
    if not await is_server_admin(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "admin_config_only"))


def _cfg_snapshot(cfg, fields) -> dict:
    """配置行 → 审计快照（None=无行；api_key 只留是否已配置，脱敏由 admin_audit_service 兜底）。"""
    if cfg is None:
        return {"configured": False}
    out = {"configured": True}
    for f in fields:
        v = getattr(cfg, f, None)
        if f == "api_key":
            v = "***" if v else None
        out[f] = v
    return out


async def _audit(db, actor_user_id, action: str, target: str | None = None,
                 before=None, after=None) -> None:
    """控制台写动作审计（契约 §0/§1.5）；fail-open，见 app/application/admin_audit_service。"""
    from app.application.admin_audit_service import record
    await record(db, actor_user_id, action, target, before, after)
