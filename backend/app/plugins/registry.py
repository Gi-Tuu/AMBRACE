"""插件注册表：扫描目录 → 校验 manifest → 动态加载 main.py → hook 分发

- 插件目录：项目根 plugins/examples/（内置示例，入库/进开源包）
           + backend/data/plugins/（用户安装，不入库、不进开源包）
- 开关/配置持久化在 plugins 表；运行时缓存启用集合，hook 分发零 DB 开销
- 单个插件异常完全隔离，不影响核心链路
"""
import asyncio
import importlib.util
import inspect
import json
import time as _time
from app.utils.logger import get_logger
import sys
from pathlib import Path

from app.plugins.manifest import load_manifest

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
# 当前正在执行 hook 的插件名（供 sdk 定位）
_sdk_ctx: dict = {}


def _scan_dir(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    return [p for p in base.iterdir() if p.is_dir() and (p / "manifest.json").is_file()]


def resolve_plugin_dir(name: str) -> Path | None:
    """在 EXAMPLE_DIR 或 USER_DIR 中查找插件目录（含 manifest.json），返回 Path 或 None（48a 页面托管/卸载用）"""
    for base in (EXAMPLE_DIR, USER_DIR):
        p = base / str(name or "")
        if p.is_dir() and (p / "manifest.json").is_file():
            return p
    return None


def load_plugin_dir(path: Path) -> dict | None:
    """加载单个插件目录，返回 info dict；失败返回 None"""
    try:
        manifest = load_manifest(str(path / "manifest.json"))
        if manifest is None:
            _logger.warning("插件 %s manifest 无效，跳过", path.name)
            return None
        name = manifest["name"]
        plugin_type = str(manifest.get("type", "http") or "http").strip()  # 48c：缺省 http
        main_py = path / "main.py"
        module = None
        if main_py.is_file():
            module_name = f"ai_plugin_{name}"
            spec = importlib.util.spec_from_file_location(module_name, str(main_py))
            if spec is None or spec.loader is None:
                _logger.warning("插件 %s 无法创建 spec，跳过", name)
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            # 先占位再执行 main.py（其中 sdk.hook 注册到本插件名下，避免 KeyError）
            _loaded[name] = {"info": {}, "module": None, "hooks": {}, "actions": {}, "router": None}
            _sdk_ctx["current"] = name
            try:
                spec.loader.exec_module(module)
            finally:
                _sdk_ctx.pop("current", None)
        else:
            # 48c：config-only 加载——无 main.py 但 type ∈ prompt/chat/workflow/hybrid/content 时仍加载 info
            #      （hybrid 页面插件（48a）亦无需 main.py，page 资源由页面托管端点直读磁盘；
            #       X2：content 内容包零代码，数据来自 manifest.content，经 validate_manifest 校验）
            if plugin_type not in ("prompt", "chat", "workflow", "hybrid", "content"):
                _logger.warning("插件 %s 缺 main.py 且非配置型(type=%s)，跳过", name, plugin_type)
                return None
            _loaded[name] = {"info": {}, "module": None, "hooks": {}, "actions": {}, "router": None}
        page = str(manifest.get("page", "") or "").strip()
        info = {
            "name": name,
            "version": str(manifest.get("version", "0.0.1")),
            "description": str(manifest.get("description", "")),
            "author": str(manifest.get("author", "")),
            "category": str(manifest.get("category", "plugin")),
            "type": plugin_type,  # 48c：插件类型（http/prompt/chat/workflow/hybrid）
            "icon": str(manifest.get("icon", "") or ""),  # 48a：图标名（≤32 字符）
            "page": page,  # 48a：页面入口相对路径（可选）
            "has_page": bool(page) and (path / page).is_file(),  # 48a：page 非空且入口文件存在
            "hooks": list(manifest.get("hooks", [])),
            "permissions": list(manifest.get("permissions", [])),
            "config": dict(manifest.get("config", {})),
            "usage": str(manifest.get("usage", "") or ""),  # 使用教程（前端扩展页展示；可选）
            "hook_timeout": manifest.get("hook_timeout"),  # per-plugin hook 超时（秒，可选；2026-08-16 审计修复）
            "content": dict(manifest.get("content") or {}) if plugin_type == "content" else {},  # X2：内容包数据（已过 schema 校验）
            "path": str(path),
        }
        _loaded[name] = {"info": info, "module": module, "hooks": _loaded[name].get("hooks", {}), "actions": _loaded[name].get("actions", {}), "router": _loaded[name].get("router")}
        return info
    except Exception as e:
        _logger.warning("插件 %s 加载失败: %s", path.name, e)
        return None


async def sync_plugins_db() -> None:
    """扫描插件目录并同步到 plugins 表（幂等 upsert）；刷新启用/配置缓存"""
    _loaded.clear()
    # X1（2026-08-31）：重扫前清空全部插件来源的游戏注册（main.py 重载时会重新注册；
    # 目录已被删除的插件其注册在此一并清理，防残留幽灵游戏）
    try:
        from app.games.registry import unregister_games_not_in
        unregister_games_not_in(set())
    except Exception:
        pass
    # X3（2026-08-31）：provider 注册同规则清理（重扫前清空插件来源注册，重载时重新注册）
    try:
        from app.providers.registry import unregister_providers_not_in
        unregister_providers_not_in(set())
    except Exception:
        pass
    _enabled.clear()
    _db_config.clear()
    _db_prov.clear()
    # (name, source) 对：示例目录=builtin，用户目录=local（新建行以此落 source；存量行保留已记录来源）
    seen: list[tuple[str, str]] = []
    for _base, _src in ((EXAMPLE_DIR, "builtin"), (USER_DIR, "local")):
        for d in _scan_dir(_base):
            info = load_plugin_dir(d)
            if info:
                seen.append((info["name"], _src))
    if not seen:
        return
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.plugin import Plugin
    async with async_session_factory() as db:
        rows = (await db.execute(select(Plugin))).scalars().all()
        by_name = {r.name: r for r in rows}
        for name, _src in seen:
            info = _loaded[name]["info"]
            row = by_name.get(name)
            if row is None:
                db.add(Plugin(
                    name=name, version=info["version"], description=info["description"],
                    author=info["author"], category=info["category"],
                    type=info.get("type", "http"), enabled=False,
                    config_json=json.dumps(info["config"], ensure_ascii=False),
                    source=_src,
                ))
            else:
                row.version = info["version"]
                row.description = info["description"]
                row.author = info["author"]
                row.category = info["category"]
                row.type = info.get("type", "http")  # 48c：sync 时从 manifest 回填 type
        await db.commit()
        # 刷新内存缓存
        rows2 = (await db.execute(select(Plugin))).scalars().all()
        for r in rows2:
            _enabled[r.name] = bool(r.enabled)
            try:
                _db_config[r.name] = json.loads(r.config_json or "{}")
            except Exception:
                _db_config[r.name] = {}
            _db_prov[r.name] = _row_prov(r)
    _logger.info("插件扫描完成：%d 个插件", len(seen))


def _row_prov(row) -> dict:
    """从 Plugin ORM 行提取来源/同意元数据（3.9），供缓存与接口输出用。"""
    try:
        _con = json.loads(row.consented_permissions or "[]")
        if not isinstance(_con, list):
            _con = []
    except Exception:
        _con = []
    return {
        "source": str(getattr(row, "source", "builtin") or "builtin"),
        "source_url": getattr(row, "source_url", None),
        "sha256": getattr(row, "sha256", None),
        "consented_permissions": _con,
        "consented_at": row.consented_at.isoformat() if getattr(row, "consented_at", None) else None,
    }


async def get_plugin_provenance(name: str) -> dict:
    """读取插件来源/同意元数据（DB 为准；无行返回内置默认）。"""
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.plugin import Plugin
    async with async_session_factory() as db:
        row = (await db.execute(select(Plugin).where(Plugin.name == name))).scalar_one_or_none()
        if row is None:
            return {"source": "builtin", "source_url": None, "sha256": None,
                    "consented_permissions": [], "consented_at": None}
        return _row_prov(row)


async def get_plugin_consented_permissions(name: str) -> list[str]:
    """读取已同意权限集（3.9）。"""
    return list((await get_plugin_provenance(name)).get("consented_permissions", []))


async def record_install_provenance(name: str, *, source: str, source_url: str | None = None, sha256: str | None = None) -> None:
    """安装/升级成功后记录来源（remote/local/builtin）+ 来源 url + sha256 实际计算值（3.9）。"""
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.plugin import Plugin
    async with async_session_factory() as db:
        row = (await db.execute(select(Plugin).where(Plugin.name == name))).scalar_one_or_none()
        if row is None:
            row = Plugin(name=name, version="0.0.1", description="", source=source)
            db.add(row)
            await db.flush()
        if source is not None:
            row.source = source
        if source_url is not None:
            row.source_url = source_url
        if sha256 is not None:
            row.sha256 = sha256
        await db.commit()
    _db_prov[name] = await get_plugin_provenance(name)


async def grant_plugin_consent(name: str, permissions: list[str]) -> None:
    """持久化同意：权限并入已同意集（保序去重）+ 更新同意时间（3.9）。"""
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.plugin import Plugin
    async with async_session_factory() as db:
        row = (await db.execute(select(Plugin).where(Plugin.name == name))).scalar_one_or_none()
        if row is None:
            row = Plugin(name=name, version="0.0.1", description="")
            db.add(row)
            await db.flush()
        try:
            _existing = json.loads(row.consented_permissions or "[]")
            if not isinstance(_existing, list):
                _existing = []
        except Exception:
            _existing = []
        union = list(dict.fromkeys(_existing + list(permissions or [])))  # 保序去重
        row.consented_permissions = json.dumps(union, ensure_ascii=False)
        row.consented_at = datetime.now(timezone.utc).replace(tzinfo=None)  # 与库一致的 naive UTC
        await db.commit()
    _db_prov[name] = await get_plugin_provenance(name)


def consent_state(manifest_permissions: list[str], stored_permissions: list[str]) -> tuple[str, list[str]]:
    """纯函数：判定同意是否需要。返回 ('empty'|'auto'|'required', needed_list)。
    - empty：manifest 未声明权限 → 无需同意；
    - auto：声明权限 ⊆ 已同意集（升级未新增）→ 自动放行；
    - required：声明了未同意过的权限 → 需用户显式同意（needed 为完整清单）。
    """
    need = sorted(set(manifest_permissions or []))
    if not need:
        return "empty", []
    if set(need) <= set(stored_permissions or []):
        return "auto", []
    return "required", need


def consent_matches(manifest_permissions: list[str], consent: bool, provided_permissions: list[str]) -> bool:
    """同意请求的权限必须与 manifest 实际声明完全一致，否则视为未同意（3.9）。"""
    need = sorted(set(manifest_permissions or []))
    return bool(consent) and sorted(set(provided_permissions or [])) == need


async def require_plugin_consent(name: str, manifest_permissions: list[str], lang: str,
                                 *, consent: bool = False, provided_permissions: list[str] | None = None) -> None:
    """安装/升级执行前的「权限同意」闸（3.9，只设在安装/升级入口，不破坏启动重扫）。

    无权限声明/升级未新增权限 → 直接放行；否则需请求携带 consent=true 且 permissions 与
    manifest 完全一致（不一致视为未同意）→ 记录并持久化同意；否则抛 HTTPException(400)
    返回所需权限清单，供前端弹确认框。
    """
    from fastapi import HTTPException
    from app.i18n import tr_lang
    _state, _needed = consent_state(manifest_permissions, await get_plugin_consented_permissions(name))
    if _state in ("empty", "auto"):
        return
    if consent_matches(manifest_permissions, consent, provided_permissions):
        await grant_plugin_consent(name, _needed)
        return
    raise HTTPException(status_code=400, detail=tr_lang(lang, "plugin_consent_required", perms=", ".join(_needed)))


def verify_plugin_signature(manifest: dict, payload: bytes, signature: str | None = None) -> bool:
    """预留：插件签名校验接口（AMBRACE 3.9 插件安全闸）。

    当前未接入签名/公钥体系，恒返回 True（不强制启用）。这是安装/加载校验层的扩展点：
    未来接入插件签名（如对 zip 的 signature 字段做公钥验签）后在此实现，校验失败返回 False，
    调用方据此拒绝安装/加载。文档见 docs/plugin-development.md「安全模型」。
    """
    return True


def list_plugins() -> list[dict]:
    """合并 manifest 信息 + DB 状态（enabled/config）+ 来源/同意元数据（3.9），按名称排序"""
    out = []
    for name, entry in _loaded.items():
        info = dict(entry["info"])
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
        coro = asyncio.get_running_loop().run_in_executor(None, fn, ctx)
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


async def run_hook_collect(hook_name: str, ctx: dict, timeout: float | None = None) -> list[dict]:
    """分发 hook 并收集各插件返回值（异常隔离 + 超时门禁，不阻断主链路）；用于 proactive_candidate 等候选收集"""
    results: list[dict] = []
    if not _loaded:
        return results
    explicit_timeout = timeout  # 保留调用方显式传入（per-plugin 判断用，2026-08-16 修复死代码）
    timeout = _hook_timeout(timeout)
    for name, entry in list(_loaded.items()):
        funcs = entry.get("hooks", {}).get(hook_name)
        if not funcs or not _enabled.get(name, False):
            continue
        # per-plugin 超时（manifest.hook_timeout）：调用方未显式传时用插件自身配置，否则全局默认
        t = explicit_timeout if explicit_timeout is not None else _hook_timeout(entry.get("info", {}).get("hook_timeout"))
        for fn in funcs:
            _sdk_ctx["current"] = name
            try:
                result = await _call_hook_bounded(fn, ctx, t, plugin=name, hook_name=hook_name)
                if result is not None:
                    results.append({"plugin": name, "result": result})
            except Exception as e:
                _logger.warning("插件 %s hook %s 异常: %s", name, hook_name, e)
            finally:
                _sdk_ctx.pop("current", None)
    return results


async def run_hook(hook_name: str, ctx: dict, timeout: float | None = None) -> None:
    """分发 hook 到所有启用且注册了该 hook 的插件（异常隔离 + 超时门禁，不阻断主链路）"""
    if not _loaded:
        return
    explicit_timeout = timeout  # 保留调用方显式传入（per-plugin 判断用，2026-08-16 修复死代码）
    timeout = _hook_timeout(timeout)
    for name, entry in list(_loaded.items()):
        funcs = entry.get("hooks", {}).get(hook_name)
        if not funcs or not _enabled.get(name, False):
            continue
        # per-plugin 超时（manifest.hook_timeout）：调用方未显式传时用插件自身配置，否则全局默认
        t = explicit_timeout if explicit_timeout is not None else _hook_timeout(entry.get("info", {}).get("hook_timeout"))
        for fn in funcs:
            _sdk_ctx["current"] = name
            try:
                await _call_hook_bounded(fn, ctx, t, plugin=name, hook_name=hook_name)
            except Exception as e:
                _logger.warning("插件 %s hook %s 异常: %s", name, hook_name, e)
            finally:
                _sdk_ctx.pop("current", None)


def current_plugin_name() -> str | None:
    return _sdk_ctx.get("current")


def mount_plugin_routers(app) -> None:
    """挂载各插件的 http_router 到 FastAPI app（lifespan 启动时 sync_plugins_db 后调用）"""
    for name, entry in list(_loaded.items()):
        r = entry.get("router")
        if r is not None:
            try:
                app.include_router(r)
                _logger.info("插件路由已挂载: /api/v1/plugins/%s", name)
            except Exception as e:
                _logger.warning("插件 %s 路由挂载失败: %s", name, e)

def preload_channels() -> int:
    """X5（2026-09-01）：仅加载 manifest 声明 channel 的渠道插件（main.py lifespan 在 init_db
    之前调用——渠道自有 ORM 模型随 main.py 加载注册进 Base.metadata，create_all 建表齐全）。
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
    _sdk_ctx["current"] = plugin
    try:
        result = fn(payload)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)
    except Exception as e:
        _logger.warning("插件 %s action %s 异常: %s", plugin, action, e)
        return False
    finally:
        _sdk_ctx.pop("current", None)
