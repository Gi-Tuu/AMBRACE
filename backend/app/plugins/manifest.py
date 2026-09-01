"""manifest.json 校验"""
import json

REQUIRED = ("name", "version", "description")
VALID_CATEGORIES = ("plugin", "mcp")
VALID_TYPES = ("http", "prompt", "chat", "workflow", "hybrid", "content")  # 48c：插件类型（缺省 http）；X2（2026-08-31）+content 内容包（零代码声明型）
# X0（2026-08-31）：纳入 tool_runner 已在派发的 6 个工具生命周期钩子（此前"内核在喊、插件听不到"）；
# 语义均为 notify（只观察，不可改写工具调用与结果），契约见 docs/extension-contract.md
VALID_HOOKS = (
    "context_inject", "before_generate", "after_generate",
    "memory_written", "memory_search", "proactive_candidate",
    "schedule_tick", "http_router",
    "tool_call_requested", "tool_permission_checked", "tool_execution_started",
    "tool_result", "tool_finished", "tool_error",
)
VALID_PERMISSIONS = (
    "write_memory", "send_message",
    # X4（2026-08-31）：只读权限组——SDK 只读端口（get_persona/search_memory/get_relationship/get_life_state）
    "persona:read", "memory:read", "life:read", "relationship:read",
)

# 48a：插件页面资源扩展名白名单（页面托管端点 GET /{name}/page/{filepath} 只放行这些扩展名）
PAGE_EXT_WHITELIST = (
    ".html", ".htm", ".css", ".js", ".mjs", ".json",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".woff", ".woff2", ".ttf", ".ico", ".txt", ".md",
)
# 48a：显式拒绝服务的可执行扩展名（即使误入白名单也不放行，双保险）
PAGE_EXT_BLOCKLIST = (".py", ".pyc", ".pyd", ".so", ".dll", ".exe")
# 48a：单文件大小上限（页面托管）
MAX_PAGE_FILE_BYTES = 5 * 1024 * 1024
# 48a：manifest.icon 字段长度上限
MAX_ICON_CHARS = 32

# ---- 48c 配置型契约上限（plan_48 §5.2；防超大 config 拖垮注入/导入）----
MAX_PROMPT_TRIGGERS = 20          # trigger 词数 ≤20
MAX_TRIGGER_WORD_CHARS = 20       # 每个 trigger ≤20 字
MAX_SYSTEM_PROMPT_CHARS = 8000    # systemPrompt ≤8000 字符
MAX_CHAT_NAME_CHARS = 50          # chat.name ≤50
MAX_PERSONA_CHARS = 8000          # chat.persona ≤8000 字符
MAX_GREETING_CHARS = 500          # chat.greeting ≤500 字符
MAX_DESC_CHARS = 200              # description ≤200（prompt/chat/workflow 通用）
MAX_WF_TEMPLATES = 10             # workflow.templates 1-10 个
MAX_WF_NODES = 50                 # 模板 nodes ≤50
MAX_WF_EDGES = 100                # 模板 edges ≤100


def _is_nonempty_str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def validate_prompt_config(cfg: dict) -> str | None:
    """校验 config.prompt（type=prompt）：trigger 非空 string 数组（每词 ≤20 字、≤20 个）；systemPrompt 非空 ≤8000；description ≤200"""
    if not isinstance(cfg, dict):
        return "config.prompt 必须是对象"
    trigger = cfg.get("trigger")
    if not isinstance(trigger, list) or not trigger:
        return "config.prompt.trigger 必须是非空数组"
    if len(trigger) > MAX_PROMPT_TRIGGERS:
        return f"config.prompt.trigger 最多 {MAX_PROMPT_TRIGGERS} 个"
    for w in trigger:
        if not _is_nonempty_str(w):
            return "config.prompt.trigger 每项必须是非空字符串"
        if len(w.strip()) > MAX_TRIGGER_WORD_CHARS:
            return f"config.prompt.trigger 每词最多 {MAX_TRIGGER_WORD_CHARS} 字"
    sp = cfg.get("systemPrompt")
    if not _is_nonempty_str(sp):
        return "config.prompt.systemPrompt 必须是非空字符串"
    if len(sp.strip()) > MAX_SYSTEM_PROMPT_CHARS:
        return f"config.prompt.systemPrompt 最多 {MAX_SYSTEM_PROMPT_CHARS} 字符"
    desc = cfg.get("description")
    if desc is not None and (not isinstance(desc, str) or len(desc) > MAX_DESC_CHARS):
        return f"config.prompt.description 最多 {MAX_DESC_CHARS} 字符"
    return None


def validate_chat_config(cfg: dict) -> str | None:
    """校验 config.chat（type=chat）：name ≤50；persona 非空 ≤8000；greeting ≤500；description ≤200"""
    if not isinstance(cfg, dict):
        return "config.chat 必须是对象"
    name = cfg.get("name")
    if name is not None and (not isinstance(name, str) or len(name) > MAX_CHAT_NAME_CHARS):
        return f"config.chat.name 最多 {MAX_CHAT_NAME_CHARS} 字符"
    persona = cfg.get("persona")
    if not _is_nonempty_str(persona):
        return "config.chat.persona 必须是非空字符串"
    if len(persona.strip()) > MAX_PERSONA_CHARS:
        return f"config.chat.persona 最多 {MAX_PERSONA_CHARS} 字符"
    greeting = cfg.get("greeting")
    if greeting is not None and (not isinstance(greeting, str) or len(greeting) > MAX_GREETING_CHARS):
        return f"config.chat.greeting 最多 {MAX_GREETING_CHARS} 字符"
    desc = cfg.get("description")
    if desc is not None and (not isinstance(desc, str) or len(desc) > MAX_DESC_CHARS):
        return f"config.chat.description 最多 {MAX_DESC_CHARS} 字符"
    return None


def validate_workflow_config(cfg: dict) -> str | None:
    """校验 config.workflow（type=workflow）：templates 1-10、id 唯一、nodes ≤50、edges ≤100、引用完整"""
    if not isinstance(cfg, dict):
        return "config.workflow 必须是对象"
    templates = cfg.get("templates")
    if not isinstance(templates, list) or not templates:
        return "config.workflow.templates 必须是非空数组"
    if len(templates) > MAX_WF_TEMPLATES:
        return f"config.workflow.templates 最多 {MAX_WF_TEMPLATES} 个"
    seen_tpl_ids: set[str] = set()
    for t in templates:
        if not isinstance(t, dict):
            return "config.workflow.templates 每项必须是对象"
        tid = t.get("id")
        if not _is_nonempty_str(tid):
            return "config.workflow.templates 每项必须有不空的 id"
        tid = str(tid).strip()
        if tid in seen_tpl_ids:
            return f"config.workflow 模板 id 重复: {tid}"
        seen_tpl_ids.add(tid)
        template = t.get("template")
        if not isinstance(template, dict):
            return f"模板 {tid} 缺少 template 对象"
        nodes = template.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            return f"模板 {tid} 的 nodes 必须是非空数组"
        if len(nodes) > MAX_WF_NODES:
            return f"模板 {tid} 的 nodes 最多 {MAX_WF_NODES} 个"
        node_ids: set[str] = set()
        for n in nodes:
            if not isinstance(n, dict):
                return f"模板 {tid} 的 nodes 每项必须是对象"
            nid = n.get("id")
            if not _is_nonempty_str(nid):
                return f"模板 {tid} 存在缺少 id 的节点"
            nid = str(nid).strip()
            if nid in node_ids:
                return f"模板 {tid} 节点 id 重复: {nid}"
            node_ids.add(nid)
            node_type = n.get("type") or n.get("action")
            if not _is_nonempty_str(node_type):
                return f"模板 {tid} 节点 {nid} 必须声明 type 或 action"
            if "config" in n and not isinstance(n["config"], dict):
                return f"模板 {tid} 节点 {nid} 的 config 必须是对象"
        edges = template.get("edges") or []
        if not isinstance(edges, list):
            return f"模板 {tid} 的 edges 必须是数组"
        if len(edges) > MAX_WF_EDGES:
            return f"模板 {tid} 的 edges 最多 {MAX_WF_EDGES} 个"
        for e in edges:
            if not isinstance(e, dict):
                return f"模板 {tid} 的 edges 每项必须是对象"
            frm = e.get("from")
            to = e.get("to")
            if str(frm) not in node_ids or str(to) not in node_ids:
                return f"模板 {tid} 存在引用不存在节点的连线"
            if str(frm) == str(to):
                return f"模板 {tid} 存在自环连线"
    return None


def validate_type_config(plugin_type: str, config: dict) -> str | None:
    """按插件类型校验 config 对应块（纯函数可测）：
    - prompt 要求 config.prompt；chat 要求 config.chat；workflow 要求 config.workflow；
    - hybrid 可带 prompt/chat（有则校验）；http 无 schema 要求（兼容旧插件）。
    """
    if plugin_type == "prompt":
        if "prompt" not in config:
            return "type=prompt 必须提供 config.prompt"
        return validate_prompt_config(config.get("prompt"))
    if plugin_type == "chat":
        if "chat" not in config:
            return "type=chat 必须提供 config.chat"
        return validate_chat_config(config.get("chat"))
    if plugin_type == "workflow":
        if "workflow" not in config:
            return "type=workflow 必须提供 config.workflow"
        return validate_workflow_config(config.get("workflow"))
    if plugin_type == "hybrid":
        if "prompt" in config:
            err = validate_prompt_config(config.get("prompt"))
            if err:
                return err
        if "chat" in config:
            err = validate_chat_config(config.get("chat"))
            if err:
                return err
    return None


def _is_safe_relative_path(v: str) -> bool:
    """页面相对路径安全校验：非空、非绝对路径、路径段不含 .. / . / 空段、首段不含盘符"""
    if not isinstance(v, str) or not v.strip():
        return False
    norm = v.strip().replace("\\", "/")
    if norm.startswith("/") or norm.startswith("\\"):
        return False
    parts = norm.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False
    if ":" in parts[0]:
        return False
    return True


def validate_page_field(page) -> str | None:
    """校验 manifest.page（48a）：可选；非空字符串、相对路径、不含 .. 与绝对路径；扩展名须在白名单"""
    if page is None:
        return None
    if not isinstance(page, str) or not page.strip():
        return "page 必须是非空字符串"
    page = page.strip()
    if not _is_safe_relative_path(page):
        return "page 必须是相对路径且不能包含 .. 或绝对路径"
    ext = "." + page.rsplit(".", 1)[-1].lower() if "." in page else ""
    if ext not in PAGE_EXT_WHITELIST:
        return f"page 扩展名不在白名单: {ext or '(无扩展名)'}"
    return None


def validate_icon_field(icon) -> str | None:
    """校验 manifest.icon（48a）：可选字符串，长度 ≤32"""
    if icon is None:
        return None
    if not isinstance(icon, str) or not icon.strip():
        return "icon 必须是非空字符串"
    if len(icon.strip()) > MAX_ICON_CHARS:
        return f"icon 最长 {MAX_ICON_CHARS} 字符"
    return None


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
    plugin_type = str(data.get("type", "http") or "http").strip()
    if plugin_type not in VALID_TYPES:
        return f"type 必须是 {VALID_TYPES}"
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
    # X5：声明 channel 的插件可自带 <渠道名>_ 前缀的自有权限（内核不枚举具体渠道权限名）
    channel = str(data.get("channel") or "").strip()
    for p in perms:
        if p in VALID_PERMISSIONS:
            continue
        if channel and p.startswith(f"{channel}_"):
            continue
        return f"未知权限: {p}"
    config = data.get("config", {})
    if not isinstance(config, dict):
        return "config 必须是对象"
    # 48c：按类型校验 config 对应块（非法安装被拒）
    type_err = validate_type_config(plugin_type, config)
    if type_err:
        return type_err
    # X2（2026-08-31）：content 内容包——content 块必填且过 schema 校验；非 content 类型带 content 块拒绝
    if plugin_type == "content":
        from app.plugins.content_schema import validate_content_payload
        cerr = validate_content_payload(data.get("content"))
        if cerr:
            return cerr
    elif data.get("content") is not None:
        return "content 块仅 type=content 内容包可用"
    # 48a：page / icon 字段校验（页面插件）
    page_err = validate_page_field(data.get("page"))
    if page_err:
        return page_err
    icon_err = validate_icon_field(data.get("icon"))
    if icon_err:
        return icon_err
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
