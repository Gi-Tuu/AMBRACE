"""插件管理 API：列表 / 启停 / 配置 / zip 安装 + chat 型插件通用对话（48c）+ 页面托管/卸载（48a）"""
import os
import shutil
import time as _time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Header
from fastapi.responses import Response

from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.plugins import registry
from app.plugins.manifest import (
    MAX_PAGE_FILE_BYTES,
    PAGE_EXT_BLOCKLIST,
    PAGE_EXT_WHITELIST,
)
from app.plugins.zip_safety import validate_zip_bytes, ZipSafetyError
from app.utils.logger import get_logger

_logger = get_logger("api.plugins")

router = APIRouter(prefix="/api/v1/plugins", tags=["Plugins"])

# ---- 48a：页面资源 Content-Type 映射（页面托管端点用）----
_PAGE_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}

# ---- 48c：chat 型插件对话限额（进程内滑动窗口；每用户 20 次/分、500 次/天，重启清零可接受）----
_PLUGIN_CHAT_RATE_MIN = 20
_PLUGIN_CHAT_RATE_DAY = 500
_PLUGIN_CHAT_WINDOW_SEC = 60.0
# user_id -> 分钟窗口时间戳 deque（monotonic）
_chat_hits_min: dict[int, deque] = defaultdict(deque)
# user_id -> [日期串(北京时间), 当日计数]
_chat_hits_day: dict[int, list] = defaultdict(lambda: [None, 0])


def _plugin_chat_rate_check(user_id: int) -> tuple[bool, int]:
    """进程内限额：返回 (是否放行, 429 重试秒数)；放行时记录本次调用（纯逻辑，可单测）"""
    now = _time.monotonic()
    dq = _chat_hits_min[user_id]
    while dq and now - dq[0] > _PLUGIN_CHAT_WINDOW_SEC:
        dq.popleft()
    if len(dq) >= _PLUGIN_CHAT_RATE_MIN:
        wait = int(_PLUGIN_CHAT_WINDOW_SEC - (now - dq[0])) + 1
        return False, max(1, wait)
    day_key = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    rec = _chat_hits_day[user_id]
    if rec[0] != day_key:
        rec[0], rec[1] = day_key, 0
    if rec[1] >= _PLUGIN_CHAT_RATE_DAY:
        return False, 60
    dq.append(now)
    rec[1] += 1
    return True, 0


def _reset_plugin_chat_rate() -> None:
    """清空限额状态（测试用）"""
    _chat_hits_min.clear()
    _chat_hits_day.clear()


def build_plugin_chat_messages(persona: str, user_input: str, history: object | None) -> list[dict]:
    """组装 chat 型对话消息（纯函数可测）：persona 作 system prompt + history(≤20 条、role 白名单) + 当前输入"""
    messages: list[dict] = [{"role": "system", "content": persona}]
    if isinstance(history, list):
        for h in history[:20]:
            if not isinstance(h, dict):
                continue
            role = str(h.get("role") or "")
            content = str(h.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content[:2000]})
    messages.append({"role": "user", "content": user_input})
    return messages


async def _is_owner(user_id: int) -> bool:
    from app.services.permission_service import is_admin_user
    return await is_admin_user(user_id)


async def _validate_douyin_binding(user_id: int, config: dict, lang: str) -> None:
    """#68 P5：douyin_mcp 组级唯一绑定校验（配置变更时）。

    - 调用者须为独立主账号（parent_id IS NULL），子账号 → 403；
    - allowed_character_ids 收窄为单选：>1 → 400；空数组 = 未绑定（允许）；
    - 所选角色 user_id 必须属于调用者家庭（跨家庭 → 403）；
    - 全组唯一：同一家庭内已有其他角色绑定 douyin → 400（先解绑/转移）。
    校验通过后把 allowed_character_ids 归一化为逗号分隔字符串（douyin 插件按逗号读取）。
    """
    if "allowed_character_ids" not in config:
        return
    import json
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.character import AICharacter
    from app.models.plugin import Plugin
    from app.services.family_service import get_family_member_ids, is_sub_account

    raw = config.get("allowed_character_ids")
    if isinstance(raw, list):
        ids = [int(x) for x in raw if str(x).strip().isdigit()]
    else:
        ids = [int(x) for x in str(raw or "").split(",") if x.strip().isdigit()]
    if len(ids) > 1:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "douyin_bind_multi"))

    async with async_session_factory() as db:
        if await is_sub_account(db, user_id):
            raise HTTPException(status_code=403, detail=tr_lang(lang, "douyin_bind_main_only"))
        if ids:
            char = await db.get(AICharacter, ids[0])
            family_ids = await get_family_member_ids(db, user_id)
            if char is None or char.user_id not in family_ids:
                raise HTTPException(status_code=403, detail=tr_lang(lang, "douyin_bind_cross_family"))
            # 全组唯一：同一家庭内已有其他角色绑定 douyin（本身份除外）
            row = (await db.execute(select(Plugin).where(Plugin.name == "douyin_mcp"))).scalar_one_or_none()
            existing = {}
            if row is not None:
                try:
                    existing = json.loads(row.config_json or "{}")
                except Exception:
                    existing = {}
            existing_ids = [int(x) for x in str(existing.get("allowed_character_ids", "") or "").split(",") if x.strip().isdigit()]
            existing_owner: dict[int, int] = {}
            if existing_ids:
                existing_chars = (await db.execute(
                    select(AICharacter).where(AICharacter.id.in_(existing_ids))
                )).scalars().all()
                existing_owner = {c.id: c.user_id for c in existing_chars}
            if any(existing_owner.get(eid) in family_ids and eid != ids[0] for eid in existing_ids):
                raise HTTPException(status_code=400, detail=tr_lang(lang, "douyin_bind_occupied"))
    # 归一化存储为逗号分隔字符串（保持 douyin 插件读取语义不变）
    config["allowed_character_ids"] = ",".join(str(x) for x in ids) if ids else ""


@router.get("")
async def list_plugins(user_id: int = Depends(get_current_user_id)):
    """插件列表（含启用状态与配置）"""
    items = registry.list_plugins()
    return {"items": items, "total": len(items)}


@router.put("/{name}")
async def update_plugin(
    name: str,
    body: dict,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """启用/禁用/更新配置（仅主账号可写；douyin_mcp 走组级唯一绑定校验）"""
    plugin = registry.get_plugin(name)
    if plugin is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "plugin_not_found"))
    enabled = body.get("enabled")
    config = body.get("config")
    if config is not None and not isinstance(config, dict):
        raise HTTPException(status_code=400, detail=tr_lang(lang, "config_invalid"))
    if name == "douyin_mcp" and config is not None:
        # #68 P5：配置变更时校验组级唯一绑定（子账号 403 / 多角色 400 / 跨家庭 403 / 占用 400）
        await _validate_douyin_binding(user_id, config, lang)
    if not await _is_owner(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "main_account_manage_only"))
    if enabled is not None and not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail=tr_lang(lang, "enabled_invalid"))
    updated = await registry.set_plugin_state(name, enabled=enabled, config=config)
    return updated


@router.post("/install")
async def install_plugin(
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """zip 安装插件：校验 manifest + 安全解压到 backend/data/plugins/"""
    if not await _is_owner(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "main_account_install_only"))
    data = await file.read()
    try:
        manifest, names = validate_zip_bytes(data)
    except ZipSafetyError as e:
        raise HTTPException(status_code=400, detail=tr_lang(lang, e.key, **e.kwargs))
    name = manifest["name"]

    # 解压到 backend/data/plugins/<name>/
    from app.plugins.zip_safety import extract_zip_bytes
    target = registry.USER_DIR / name
    if not target.resolve().is_relative_to(registry.USER_DIR.resolve()):
        raise HTTPException(status_code=400, detail="invalid plugin name (path traversal blocked)")
    if target.exists():
        shutil.rmtree(target)
    os.makedirs(target, exist_ok=True)
    try:
        extract_zip_bytes(data, names, target)
    except Exception as e:
        shutil.rmtree(target, ignore_errors=True)
        raise HTTPException(status_code=500, detail=tr_lang(lang, "install_failed", err=str(e)[:200]))
    _logger.info("插件 %s 安装到 %s", name, target)

    # 重新扫描加载
    await registry.sync_plugins_db()
    plugin = registry.get_plugin(name)
    if plugin is None:
        raise HTTPException(status_code=500, detail=tr_lang(lang, "plugin_load_failed"))
    return plugin


@router.post("/{name}/chat")
async def plugin_chat(
    name: str,
    body: dict,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """chat 型插件通用对话（48c 独立闭环，不依赖 48a 桥/48b 角色 API）：
    config.chat.persona 作 system prompt + llm_client（用户 BYOK > 服务器级 DB > .env 三级回退）
    + 进程内限额（20/分、500/天）+ 不写记忆/不建会话 + 输出剥离动作标记。"""
    plugin = registry.get_plugin(name)
    if plugin is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "plugin_not_found"))
    if plugin.get("type") != "chat":
        raise HTTPException(status_code=400, detail=tr_lang(lang, "plugin_chat_not_chat_type", name=name))
    chat_cfg = (plugin.get("config") or {}).get("chat") or {}
    persona = str(chat_cfg.get("persona") or "").strip()
    if not persona:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "plugin_chat_no_persona"))
    user_input = str(body.get("input") or "").strip()
    if not user_input:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "plugin_chat_input_empty"))
    if len(user_input) > 4000:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "plugin_chat_input_too_long"))
    ok, retry_after = _plugin_chat_rate_check(user_id)
    if not ok:
        raise HTTPException(
            status_code=429, detail=tr_lang(lang, "plugin_chat_rate_limited"),
            headers={"Retry-After": str(retry_after)},
        )
    try:
        max_tokens = max(1, min(2000, int(body.get("maxTokens") or 800)))
        temperature = max(0.0, min(1.5, float(body.get("temperature") or 0.8)))
    except Exception:
        max_tokens, temperature = 800, 0.8
    messages = build_plugin_chat_messages(persona, user_input, body.get("history"))
    try:
        from app.agent.llm_client import chat_completion, get_user_llm_config
        cfg = await get_user_llm_config(user_id)  # 用户级 BYOK；None 时回退服务器级 DB → .env
        reply = await chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            task="plugin_ai",  # 记账归因（复用 llm_usage.task）
            **(cfg or {}),
        )
    except Exception as e:
        _logger.warning("plugin chat llm failed %s: %s", name, e)
        raise HTTPException(status_code=500, detail=tr_lang(lang, "plugin_llm_failed", err=str(e)[:200]))
    # 输出剥离动作标记（[SEARCH]/[GEN_IMAGE]/[CAL_NOTE]/[MEMO]/[timer]/【状态更新】等）
    from app.agent.actions import strip_actions, strip_status_update
    cleaned = strip_status_update(strip_actions(reply)).strip()
    if not cleaned:
        cleaned = reply.strip()
    return {
        "reply": cleaned,
        "plugin": {"name": name, "type": "chat", "display_name": chat_cfg.get("name") or name},
    }


# ---------------- 48a：页面托管 ----------------

@router.get("/{name}/page/{filepath:path}")
async def plugin_page(
    name: str,
    filepath: str,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """页面托管（48a）：插件包内相对路径静态资源。

    - resolve_plugin_dir → (dir/filepath).resolve() 必须 startswith(dir.resolve())，否则 404；
    - 扩展名白名单（PAGE_EXT_WHITELIST）+ 显式拒绝 .py/.pyc/.pyd/.so/.dll/.exe 与 __pycache__；
    - 单文件 ≤5MB；未安装/越界/非法统一 404（不泄露目录结构）；登录态 get_current_user_id。
    """
    # plugin-bridge.js：桥 SDK 特殊返回（不读磁盘；Content-Type application/javascript）
    if filepath == "plugin-bridge.js":
        from app.api.plugin_bridge import PLUGIN_BRIDGE_JS
        return Response(content=PLUGIN_BRIDGE_JS, media_type="application/javascript; charset=utf-8")
    plugin_dir = registry.resolve_plugin_dir(name)
    if plugin_dir is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "plugin_page_not_found"))
    # 路径安全（双保险：先拒绝明显非法，再 resolve 越界检查）
    norm = filepath.replace("\\", "/")
    parts = norm.split("/")
    if not norm or norm.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise HTTPException(status_code=404, detail=tr_lang(lang, "plugin_page_not_found"))
    if "__pycache__" in parts:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "plugin_page_not_found"))
    try:
        target = (plugin_dir / filepath).resolve()
    except OSError:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "plugin_page_not_found"))
    root = plugin_dir.resolve()
    if not str(target).startswith(str(root)):
        raise HTTPException(status_code=404, detail=tr_lang(lang, "plugin_page_not_found"))
    if not target.is_file():
        raise HTTPException(status_code=404, detail=tr_lang(lang, "plugin_page_not_found"))
    ext = target.suffix.lower()
    if ext not in PAGE_EXT_WHITELIST or ext in PAGE_EXT_BLOCKLIST:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "plugin_page_not_found"))
    try:
        data = target.read_bytes()
    except OSError:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "plugin_page_not_found"))
    if len(data) > MAX_PAGE_FILE_BYTES:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "plugin_page_too_large"))
    return Response(content=data, media_type=_PAGE_CONTENT_TYPES.get(ext, "application/octet-stream"))


@router.delete("/{name}")
async def uninstall_plugin(
    name: str,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """卸载插件（48a，仅主账号）：删 USER_DIR 插件目录 + 清空 plugin_stores 行 + 禁用（删除 plugins 行）"""
    if not await _is_owner(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "main_account_manage_only"))
    plugin_dir = registry.resolve_plugin_dir(name)
    if plugin_dir is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "plugin_not_found"))
    user_dir = registry.USER_DIR.resolve()
    # 内置插件（仅存在于 EXAMPLE_DIR，无 USER_DIR 副本）不可卸载
    if not plugin_dir.resolve().is_relative_to(user_dir):
        raise HTTPException(status_code=400, detail=tr_lang(lang, "plugin_uninstall_builtin"))
    try:
        shutil.rmtree(plugin_dir, ignore_errors=True)
        from sqlalchemy import delete as sa_delete, select as sa_select
        from app.db.database import async_session_factory
        from app.models.plugin import Plugin
        from app.models.plugin_store import PluginStore
        async with async_session_factory() as db:
            await db.execute(sa_delete(PluginStore).where(PluginStore.plugin_name == name))
            row = (await db.execute(sa_select(Plugin).where(Plugin.name == name))).scalar_one_or_none()
            if row is not None:
                await db.delete(row)
            await db.commit()
        # 重新扫描（内存加载 / 启用 / 配置缓存同步）
        await registry.sync_plugins_db()
    except Exception as e:
        _logger.warning("插件 %s 卸载失败: %s", name, e)
        raise HTTPException(status_code=500, detail=tr_lang(lang, "plugin_uninstall_failed", err=str(e)[:200]))
    _logger.info("插件 %s 已卸载", name)
    return {"uninstalled": True, "name": name}
