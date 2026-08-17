"""manifest.json 校验"""
import json

REQUIRED = ("name", "version", "description")
VALID_CATEGORIES = ("plugin", "mcp")
VALID_HOOKS = (
    "context_inject", "before_generate", "after_generate",
    "memory_written", "memory_search", "proactive_candidate",
    "schedule_tick", "http_router",
)
VALID_PERMISSIONS = ("write_memory", "send_message", "douyin_publish")


def validate_manifest(data: dict) -> str | None:
    """返回错误信息；None 表示合法"""
    if not isinstance(data, dict):
        return "manifest 必须是 JSON 对象"
    for k in REQUIRED:
        if not data.get(k):
            return f"缺少必填字段 {k}"
    import re as _re
    name = str(data.get("name", "")).strip()
    if not (1 <= len(name) <= 64):
        return "name 长度需在 1-64"
    if not _re.fullmatch(r"[A-Za-z0-9_-]+", name):
        return "name 仅允许字母数字下划线连字符（P0-2 安全加固：防路径穿越）"
    category = data.get("category", "plugin")
    if category not in VALID_CATEGORIES:
        return f"category 必须是 {VALID_CATEGORIES}"
    ht = data.get("hook_timeout")
    if ht is not None and not isinstance(ht, (int, float)):
        return "hook_timeout 必须是数字（秒，1-60 收敛）"
    hooks = data.get("hooks", [])
    if not isinstance(hooks, list):
        return "hooks 必须是数组"
    for h in hooks:
        if h not in VALID_HOOKS:
            return f"未知 hook: {h}"
    perms = data.get("permissions", [])
    if not isinstance(perms, list):
        return "permissions 必须是数组"
    for p in perms:
        if p not in VALID_PERMISSIONS:
            return f"未知权限: {p}"
    if not isinstance(data.get("config", {}), dict):
        return "config 必须是对象"
    return None


def load_manifest(path: str) -> dict | None:
    """读取并校验 manifest.json；失败返回 None"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if validate_manifest(data) is not None:
        return None
    return data
