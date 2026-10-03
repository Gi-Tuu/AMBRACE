"""插件注册表：扫描目录 → 校验 manifest → 动态加载 main.py → hook 分发

- 插件目录：项目根 plugins/examples/（内置示例，入库/进开源包）
           + backend/data/plugins/（用户安装，不入库、不进开源包）
- 开关/配置持久化在 plugins 表；运行时缓存启用集合，hook 分发零 DB 开销
- 单个插件异常完全隔离，不影响核心链路
"""
import asyncio
import contextvars
import inspect
import json
import time as _time
from contextlib import contextmanager
from app.utils.logger import get_logger
from pathlib import Path

_logger = get_logger("plugins")

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # backend/app/plugins -> 项目根
EXAMPLE_DIR = PROJECT_ROOT / "plugins" / "examples"
USER_DIR = PROJECT_ROOT / "backend" / "data" / "plugins"

# name -> {"info": {...}, "module": module, "hooks": {hook: [func, ...]}, "actions": {action: func}}
_loaded: dict[str, dict] = {}
# name -> enabled（内存缓存，load 时从 DB 同步）
_enabled: dict[str, bool] = {}
# name -> config dict（内存缓存）
_db_config: dict[str, dict] = {}
# name -> 安装来源/同意元数据缓存（source/source_url/sha256/consented_permissions/consented_at；3.9）
_db_prov: dict[str, dict] = {}
# 当前插件执行上下文（供 sdk 定位）：A2 M4（2026-09-20）由进程级单键 dict 改为 ContextVar。
# 值形如 {"current": 插件名, "user_id": 调用者账号, "tenant_id": 调用者家庭根}；不在插件上下文时为 None。
# 历史坑（full-project-audit P1）：进程级 dict 在「set → await 插件 hook → pop」期间被并发协程覆盖，
# 会让 sdk.get_config()/require_permission 解析到别的插件身份 —— ContextVar 保证各协程互不串身份。
# 本改动 always-on（纯并发正确性修复，单账号行为不变）。
_sdk_ctx: contextvars.ContextVar = contextvars.ContextVar("plugin_sdk_ctx", default=None)


def push_sdk_context(plugin: str, *, user_id: int | None = None,
                     tenant_id: int | None = None) -> contextvars.Token:
    """进入插件上下文，返回 token（必须与 :func:`reset_sdk_context` 成对使用）。"""
    return _sdk_ctx.set({"current": plugin, "user_id": user_id, "tenant_id": tenant_id})


def reset_sdk_context(token: contextvars.Token) -> None:
    """退出插件上下文（reset 回 set 之前的值；token 失效等异常静默，不影响主链路）。"""
    try:
        _sdk_ctx.reset(token)
    except Exception:
        pass


@contextmanager
def sdk_context(plugin: str, *, user_id: int | None = None, tenant_id: int | None = None):
    """with 形态的插件上下文（测试/一次性调用用；与 push/reset 同语义）。"""
    token = push_sdk_context(plugin, user_id=user_id, tenant_id=tenant_id)
    try:
        yield
    finally:
        reset_sdk_context(token)


def current_sdk_context() -> dict:
    """当前插件上下文快照（不在插件上下文时为空 dict；供 sdk 归属断言读取 caller）。"""
    return dict(_sdk_ctx.get() or {})


# ── A22 ④a（2026-10-03）：目录扫描/动态加载/库表同步/安装溯源已下沉 plugin_store ──────
# 具名重导出（外请与 tests 一律继续从 app.plugins.registry 取这些名字，桩锚点零迁移）：
# plugin_store 内部反向经 _reg.<name> 现取 registry 留原地的锚点，故顶层不得在此之上绑定。
from app.plugins.plugin_store import (  # noqa: F401
    _discard_partial_load,
    _ensure_plugin_tables_sync,
    _py_default_literal,
    _reconcile_plugin_schema,
    _row_prov,
    _scan_dir,
    ensure_plugin_tables,
    get_plugin_provenance,
    load_plugin_dir,
    record_install_provenance,
    resolve_plugin_dir,
    sync_plugins_db,
)


# A22 ④b（2026-10-03）：插件同意与能力判定下沉到 plugin_consent，此处**具名重导出**。
# ⚠ 一个名字都不许删：tests 里 setattr(registry, "get_tenant_consented_permissions"|
#   "backfill_plugin_consents_once", …)（test_plugin_capability_notes_m0.py:285 /
#   test_arbiter_seam.py 的 test_A22第八刀a_5_*）与 plugin_store（④a）、api/marketplace.py、api/plugins.py
#   全部按 registry.<name> 取用；删名字＝桩静默失效＝守卫变绿却扫真库。
from app.plugins.plugin_consent import (  # noqa: F401
    _parse_perms,
    _upsert_plugin_consent,
    backfill_plugin_consents_once,
    consent_matches,
    consent_state,
    get_plugin_consented_permissions,
    get_tenant_consented_permissions,
    grant_plugin_consent,
    has_capability_permission,
    require_plugin_consent,
    resolve_tenant_for_user,
    verify_plugin_signature,
)


# ── A2 M4（2026-09-20）：运行面按账号过滤（flag plugin_runtime_scope，默认关）──────────────
# 运行面（hook 分发 / 工具登记 / prompt 注入 / 桥与页面 / sdk 归属断言）共用 M3 的可见性谓词
# 与 30s 进程内缓存（仿 app/mcp/ownership.py 的 _CACHE 写法）；缓存**按调用者家庭根**失效。
_RUNTIME_SCOPE_TTL = 30.0
# viewer_tenant_id -> (monotonic_ts, frozenset[插件名])
_visible_names_cache: dict[int | None, tuple[float, frozenset[str]]] = {}
# user_id -> (monotonic_ts, 家庭根 | None)
_caller_tenant_cache: dict[int, tuple[float, int | None]] = {}
# 已告警过的「拿不到 caller」调用点（hook@callsite），避免刷屏
_warned_no_caller: set[str] = set()

# A22 ④c（2026-10-03）：租户可见性与运行时作用域下沉到 plugin_scope，此处**具名重导出**。
# ⚠ 一个名字都不许删：`plugin_disabled_route_gate_enabled` / `clear_runtime_scope_caches`
#   是 tests 的直接打桩目标（test_mount_plugin_routers_enabled_gate.py:44 /
#   test_arbiter_seam.py 的 test_A22第八刀a_5_*），`_warned_no_caller` 等三个缓存本体与 `_RUNTIME_SCOPE_TTL`
#   仍留在上方（test_plugin_runtime_scope_m4.py:240 直接读 registry._warned_no_caller）；
#   api/plugins.py、api/marketplace.py、api/plugin_bridge.py、agent/tools.py、plugins/sdk.py、
#   plugins/config_hooks.py、plugin_store.py 全部按 registry.<name> 取用。
from app.plugins.plugin_scope import (  # noqa: F401
    plugin_user_scope_enabled,
    plugin_visible_to_tenant,
    plugin_runtime_scope_enabled,
    plugin_is_builtin,
    clear_runtime_scope_caches,
    resolve_caller_tenant_cached,
    _visible_plugin_names,
    _runtime_scope_viewer,
    plugin_in_runtime_scope,
    plugin_visible_for_caller,
    plugin_disabled_route_gate_enabled,
    plugin_http_gate,
    _warn_no_caller_once,
    _resolve_hook_scope,
    resolve_viewer_tenant,
)



def list_plugins(viewer_user_id: int | None = None, *,
                 viewer_tenant_id: int | None = None) -> list[dict]:
    """合并 manifest 信息 + DB 状态（enabled/config）+ 来源/同意元数据（3.9），按名称排序。

    A2 M3（2026-09-20）可选可见性过滤（plugin_user_scope flag 门控）：
    - ``viewer_user_id is None``（默认，既有调用方）→ **零过滤，逐字节旧行为**；
    - ``viewer_user_id`` 非空且 flag 开 → 只返回该账号可见的插件（谓词见
      :func:`plugin_visible_to_tenant`）；
    - flag 关 → 无论传不传 viewer 都逐字节旧行为（全量列表）；
    - ``viewer_tenant_id`` = 调用者家庭根，由异步入口层 ``await resolve_viewer_tenant(user_id)``
      解析后传入；缺省 None 表示解析失败/未知 → fail-closed 到「内置 ∪ 服务级」。
    """
    _scope_on = viewer_user_id is not None and plugin_user_scope_enabled()
    out = []
    for name, entry in _loaded.items():
        _raw_info = entry.get("info") or {}
        if not _raw_info.get("name"):
            # 加载中断残留的占位条目：跳过并告警，绝不让它拖垮整份列表（防御纵深，见 _discard_partial_load）
            _logger.warning("插件 %s 注册表条目缺失 info（加载中断残留），已跳过", name)
            continue
        info = dict(_raw_info)
        info["enabled"] = bool(_enabled.get(name, False))
        saved = _db_config.get(name, {})
        merged = dict(info.get("config", {}))
        merged.update(saved)
        info["config"] = merged
        prov = _db_prov.get(name, {})
        info["source"] = prov.get("source", info.get("source", "builtin"))
        info["source_url"] = prov.get("source_url")
        info["sha256"] = prov.get("sha256")
        info["consented_permissions"] = prov.get("consented_permissions", [])
        info["consented_at"] = prov.get("consented_at")
        if _scope_on and not plugin_visible_to_tenant(
            source=info["source"],
            owner_user_id=prov.get("owner_user_id"),
            owner_tenant_id=prov.get("owner_tenant_id"),
            viewer_tenant_id=viewer_tenant_id,
        ):
            continue  # A2 M3：非本家庭安装的非内置插件 → 不可见
        out.append(info)
    out.sort(key=lambda x: x["name"])
    return out


def get_plugin(name: str) -> dict | None:
    for p in list_plugins():
        if p["name"] == name:
            return p
    return None


async def set_plugin_state(name: str, enabled: bool | None = None, config: dict | None = None) -> dict | None:
    """更新插件启用状态/配置（DB + 内存缓存）"""
    if name not in _loaded:
        return None
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.plugin import Plugin
    async with async_session_factory() as db:
        row = (await db.execute(select(Plugin).where(Plugin.name == name))).scalar_one_or_none()
        if row is None:
            info = _loaded[name]["info"]
            row = Plugin(name=name, version=info["version"], description=info["description"],
                         author=info["author"], category=info["category"], enabled=False,
                         config_json="{}")
            db.add(row)
            await db.flush()
        if enabled is not None:
            row.enabled = enabled
        if config is not None:
            saved = {}
            try:
                saved = json.loads(row.config_json or "{}")
            except Exception:
                saved = {}
            saved.update(config)
            row.config_json = json.dumps(saved, ensure_ascii=False)
        await db.commit()
        await db.refresh(row)
        _enabled[name] = bool(row.enabled)
        try:
            _db_config[name] = json.loads(row.config_json or "{}")
        except Exception:
            _db_config[name] = {}
    return get_plugin(name)


def _hook_timeout(timeout: float | None) -> float:
    """解析 hook 超时：显式传入直接用；默认取配置 plugin_hook_timeout（1-60s 收敛）"""
    if timeout is None:
        try:
            from app.config import settings
            timeout = float(getattr(settings, "plugin_hook_timeout", 10.0))
        except Exception:
            timeout = 10.0
        timeout = max(1.0, min(60.0, timeout))
    return timeout


async def _call_hook_bounded(fn, ctx: dict, timeout: float, plugin: str = "", hook_name: str = ""):
    """执行单个 hook（超时门禁，2026-08-16 Phase A）：
    - 异步 hook：asyncio.wait_for 超时中断（任务取消）；
    - 同步 hook：丢默认线程池执行 + wait_for 超时（线程无法强杀，但主流程不再等待）；
    超时统一忽略返回值返回 None；非超时异常原样上抛（由调用方隔离）。"""
    _t0 = _time.monotonic()
    if inspect.iscoroutinefunction(fn):
        coro = fn(ctx)
    else:
        # A2 M4：同步 hook 在默认线程池执行——把当前 contextvars 一并带进线程，否则线程里的
        # sdk.get_config()/require_permission 读不到插件身份（等价于旧进程级 dict 的可见性）。
        _cctx = contextvars.copy_context()
        coro = asyncio.get_running_loop().run_in_executor(None, _cctx.run, fn, ctx)
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        _logger.warning(
            "插件 %s hook %s 超时（已耗时 %.1fs，限制 %ss）已中断并忽略返回值",
            plugin, hook_name, _time.monotonic() - _t0, timeout,
        )
        return None
    if inspect.isawaitable(result):
        try:
            return await asyncio.wait_for(result, timeout=timeout)
        except asyncio.TimeoutError:
            _logger.warning(
                "插件 %s hook %s 返回协程超时（已耗时 %.1fs，限制 %ss）已中断并忽略返回值",
                plugin, hook_name, _time.monotonic() - _t0, timeout,
            )
            return None
    return result


async def run_hook_collect(hook_name: str, ctx: dict, timeout: float | None = None, *,
                           user_id: int | None = None, tenant_id: int | None = None,
                           callsite: str | None = None) -> list[dict]:
    """分发 hook 并收集各插件返回值（异常隔离 + 超时门禁，不阻断主链路）；用于 proactive_candidate 等候选收集

    A2 M4（2026-09-20）：新增 caller 形参（``user_id`` / ``tenant_id``，默认 None = 该调用点拿不到调用者）。
    flag ``plugin_runtime_scope`` 开时只向「对该调用者可见」的插件分发（M3 可见性谓词 + 30s 缓存）；
    flag 开且拿不到 caller → **fail-closed**：只向内置插件分发，并按调用点告警一次。flag 关 = 逐字节旧行为。
    同时把 caller 写进插件上下文（``_sdk_ctx`` 的 user_id/tenant_id），供 sdk 归属断言读取。
    """
    results: list[dict] = []
    if not _loaded:
        return results
    allowed, viewer = await _resolve_hook_scope(
        hook_name, user_id=user_id, tenant_id=tenant_id, callsite=callsite or hook_name,
    )
    explicit_timeout = timeout  # 保留调用方显式传入（per-plugin 判断用，2026-08-16 修复死代码）
    timeout = _hook_timeout(timeout)
    for name, entry in list(_loaded.items()):
        funcs = entry.get("hooks", {}).get(hook_name)
        if not funcs or not _enabled.get(name, False):
            continue
        if allowed is not None and name not in allowed:
            continue  # A2 M4：对本调用者不可见 → 不进入本次运行面
        # per-plugin 超时（manifest.hook_timeout）：调用方未显式传时用插件自身配置，否则全局默认
        t = explicit_timeout if explicit_timeout is not None else _hook_timeout(entry.get("info", {}).get("hook_timeout"))
        for fn in funcs:
            _token = push_sdk_context(name, user_id=user_id, tenant_id=viewer)
            try:
                result = await _call_hook_bounded(fn, ctx, t, plugin=name, hook_name=hook_name)
                if result is not None:
                    results.append({"plugin": name, "result": result})
            except Exception as e:
                _logger.warning("插件 %s hook %s 异常: %s", name, hook_name, e)
            finally:
                reset_sdk_context(_token)
    return results


async def run_hook(hook_name: str, ctx: dict, timeout: float | None = None, *,
                   user_id: int | None = None, tenant_id: int | None = None,
                   callsite: str | None = None) -> None:
    """分发 hook 到所有启用且注册了该 hook 的插件（异常隔离 + 超时门禁，不阻断主链路）

    A2 M4：caller 形参与可见性过滤口径同 :func:`run_hook_collect`（flag 门控，默认关 = 旧行为）。
    """
    if not _loaded:
        return
    allowed, viewer = await _resolve_hook_scope(
        hook_name, user_id=user_id, tenant_id=tenant_id, callsite=callsite or hook_name,
    )
    explicit_timeout = timeout  # 保留调用方显式传入（per-plugin 判断用，2026-08-16 修复死代码）
    timeout = _hook_timeout(timeout)
    for name, entry in list(_loaded.items()):
        funcs = entry.get("hooks", {}).get(hook_name)
        if not funcs or not _enabled.get(name, False):
            continue
        if allowed is not None and name not in allowed:
            continue  # A2 M4：对本调用者不可见 → 不进入本次运行面
        # per-plugin 超时（manifest.hook_timeout）：调用方未显式传时用插件自身配置，否则全局默认
        t = explicit_timeout if explicit_timeout is not None else _hook_timeout(entry.get("info", {}).get("hook_timeout"))
        for fn in funcs:
            _token = push_sdk_context(name, user_id=user_id, tenant_id=viewer)
            try:
                await _call_hook_bounded(fn, ctx, t, plugin=name, hook_name=hook_name)
            except Exception as e:
                _logger.warning("插件 %s hook %s 异常: %s", name, hook_name, e)
            finally:
                reset_sdk_context(_token)


def current_plugin_name() -> str | None:
    return (_sdk_ctx.get() or {}).get("current")


def mount_plugin_routers(app) -> None:
    """挂载各插件的 http_router 到 FastAPI app（lifespan 启动时 sync_plugins_db 后调用）

    C3（2026-09-25）：flag ``plugin_disabled_route_gate`` 开时，已禁用插件（内存缓存
    ``_enabled``，缺键视为禁用）直接**跳过挂载**，与请求级禁用闸同一口径；
    flag 关时逐字旧行为——所有带 router 的插件照常挂载。
    """
    gate = plugin_disabled_route_gate_enabled()
    for name, entry in list(_loaded.items()):
        r = entry.get("router")
        if r is None:
            continue
        if gate:
            try:
                enabled = bool(_enabled.get(name, False))
            except Exception:
                enabled = True  # 判定失败只影响该插件：按旧行为挂载，请求级闸仍兜底
            if not enabled:
                _logger.info("插件路由跳过（插件已禁用）: %s", name)
                continue
        try:
            app.include_router(r)
            _logger.info("插件路由已挂载: /api/v1/plugins/%s", name)
        except Exception as e:
            _logger.warning("插件 %s 路由挂载失败: %s", name, e)

def preload_channels() -> int:
    """X5（2026-09-01）：仅加载 manifest 声明 channel 的渠道插件（main.py lifespan 在 init_db
    之前调用——渠道自有 ORM 模型随 main.py 加载注册进插件独立 plugin_metadata（T5），
    并由 load_plugin_dir 内联的 ensure 幂等建表）。
    正式加载仍由 sync_plugins_db 统一重扫（渠道注册为同源替换语义）。返回预加载数。"""
    count = 0
    for d in _scan_dir(EXAMPLE_DIR) + _scan_dir(USER_DIR):
        try:
            mf = json.loads((d / "manifest.json").read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if not mf.get("channel"):
            continue
        if load_plugin_dir(d):
            count += 1
    return count


async def run_plugin_action(plugin: str, action: str, payload: dict, user_id: int | None = None) -> bool:
    """调用插件注册的 action（arbiter 执行插件自定义行为，如渠道评论回复）。

    异常隔离返回 False；未注册返回 False；payload 为候选 dict（含 social_event 等）。
    user_id 非空时按插件名映射能力 scope 做权限检查（ask/forbid 拒绝执行）。
    """
    if user_id is not None:
        try:
            from app.application import permission_service
            _scope = permission_service._plugin_scope(plugin)
            _mode = await permission_service.check_mode(user_id, _scope)
            if _mode != "allow":
                _logger.info("plugin action blocked plugin=%s scope=%s mode=%s", plugin, _scope, _mode)
                return False
        except Exception:
            pass
    entry = _loaded.get(plugin)
    fn = (entry or {}).get("actions", {}).get(action)
    if fn is None:
        _logger.warning("插件 %s 未注册 action %s", plugin, action)
        return False
    # A2 M4：把 caller 写进插件上下文（sdk 归属断言读它；tenant_id 由 sdk 按 user_id 懒解析）
    _token = push_sdk_context(plugin, user_id=user_id)
    try:
        result = fn(payload)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)
    except Exception as e:
        _logger.warning("插件 %s action %s 异常: %s", plugin, action, e)
        return False
    finally:
        reset_sdk_context(_token)
