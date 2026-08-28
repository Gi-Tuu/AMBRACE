"""后端错误消息双语（多语种 P3）。

- 语言来源：请求头 X-Lang（前端已随请求携带），默认简体中文。
- 用法：raise HTTPException(status_code=400, detail=tr(request, "password_too_short"))
"""

from typing import Any

from fastapi import Request

# key -> (中文, English)
_MESSAGES: dict[str, tuple[str, str]] = {
    # 认证
    "password_too_short": ("密码长度至少 8 位", "Password must be at least 8 characters"),
    "password_too_long": ("密码长度不能超过 64 位", "Password must be at most 64 characters"),
    "password_too_simple": ("密码过于简单，请更换", "Password is too weak, please change it"),
    "password_contains_username": ("密码不能包含用户名", "Password cannot contain the username"),
    "password_need_alpha_digit": ("密码需同时包含字母和数字", "Password must contain both letters and numbers"),
    "username_exists": ("用户名已存在", "Username already exists"),
    "too_many_attempts": ("尝试次数过多，请约 {minutes} 分钟后再试", "Too many attempts, try again in about {minutes} minutes"),
    "wrong_credentials": ("用户名或密码错误", "Incorrect username or password"),
    "user_not_found": ("用户不存在", "User not found"),
    "old_password_wrong": ("旧密码错误", "Old password is incorrect"),
    "please_login": ("请先登录", "Please log in first"),
    "token_invalid": ("Token无效", "Invalid token"),
    "token_expired": ("Token无效或已过期", "Invalid or expired token"),
    # 聊天
    "character_not_found": ("角色不存在", "Character not found"),
    "session_not_found": ("会话不存在", "Conversation not found"),
    "message_not_found": ("消息不存在", "Message not found"),
    "moment_daily_limit_or_not_found": ("今日已达发布上限或角色不存在", "Daily publish limit reached or character not found"),
    "content_empty": ("内容不能为空", "Content cannot be empty"),
    "content_too_long": ("内容不能超过500字", "Content must be 500 characters or fewer"),
    "moment_not_found": ("动态不存在", "Moment not found"),
    "comment_empty": ("评论不能为空", "Comment cannot be empty"),
    "comment_too_long": ("评论不能超过200字", "Comment must be 200 characters or fewer"),
    "replied_comment_not_found": ("回复的评论不存在", "The comment you're replying to does not exist"),
    "delete_own_moment_only": ("只能删除自己的动态", "You can only delete your own moments"),
    "comment_not_found": ("评论不存在", "Comment not found"),
    "delete_own_comment_only": ("只能删除自己的评论", "You can only delete your own comments"),
    "pet_species_unsupported": ("不支持的宠物种类", "Unsupported pet species"),
    "pet_species_coming_soon": ("非常宠物即将开放，敬请期待", "Special pets coming soon, stay tuned"),
    "pet_name_required": ("请给宠物起个名字", "Please give your pet a name"),
    "pet_name_too_long": ("名字太长（最多 {max} 个字）", "Name is too long (max {max} characters)"),
    "pet_limit_max": ("最多只能养 {max} 只宠物", "You can keep up to {max} pets"),
    "pet_not_found": ("宠物不存在", "Pet not found"),
    "ai_pet_rename_forbidden": ("AI 角色的宠物不能由用户改名", "AI pets cannot be renamed by users"),
    "pet_name_empty": ("名字不能为空", "Name cannot be empty"),
    "no_character": ("暂无角色", "No character yet"),
    "unsupported_action": ("不支持的动作", "Unsupported action"),
    "unsupported_file_format": ("不支持的文件格式: {ext}", "Unsupported file format: {ext}"),
    "unknown_ext": ("未知", "unknown"),
    "file_too_large": ("文件不能超过 {mb}MB", "File must be under {mb}MB"),
    # 家庭群聊
    "group_not_found": ("群聊不存在", "Group not found"),
    "group_min_two": ("群聊至少需要 2 个角色", "A group needs at least 2 characters"),
    "group_max_members": ("群聊最多 {max} 人", "Group can have at most {max} members"),
    "chat_group_foreign_char": ("只能拉取自己的角色进群", "Only your own characters can join the group"),
    "group_msg_empty": ("消息内容不能为空", "Message cannot be empty"),
    "group_member_not_found": ("该角色不在群里", "Character is not in the group"),
    "invalid_character_id": ("角色编号无效", "Invalid character ID"),
    # 插件 / 插件市场
    "main_account_install_only": ("仅主账号可安装", "Only the main account can install"),
    "main_account_manage_only": ("仅主账号可管理", "Only the main account can manage"),
    "plugin_not_found": ("插件不存在", "Plugin not found"),
    "enabled_invalid": ("启用状态参数无效", "Invalid enabled value"),
    "config_invalid": ("配置参数无效", "Invalid config"),
    "zip_too_large": ("压缩包超过 20MB 限制", "Zip exceeds 20MB limit"),
    "zip_empty_file": ("压缩包为空", "Empty zip file"),
    "zip_invalid": ("压缩包格式无效", "Invalid zip file"),
    "zip_empty": ("压缩包内没有文件", "Zip contains no files"),
    "zip_illegal_path": ("压缩包含非法路径: {n}", "Zip contains illegal path: {n}"),
    "zip_symlink": ("压缩包含符号链接: {n}", "Zip contains symlink: {n}"),
    "zip_no_manifest": ("压缩包缺少 manifest.json", "Zip missing manifest.json"),
    "manifest_parse_failed": ("manifest 解析失败", "Failed to parse manifest"),
    "manifest_invalid": ("manifest 校验失败: {err}", "Invalid manifest: {err}"),
    "plugin_load_failed": ("插件加载失败", "Plugin failed to load"),
    "market_no_plugin": ("市场中无此插件", "Plugin not found in marketplace"),
    "plugin_dir_not_found": ("插件目录不存在", "Plugin directory not found"),
    "install_failed": ("安装失败: {err}", "Install failed: {err}"),
    # 48c 配置驱动零代码模板：chat 型对话 / workflow 模板导入
    "plugin_chat_input_empty": ("消息内容不能为空", "Message cannot be empty"),
    "plugin_chat_input_too_long": ("消息内容不能超过 4000 字", "Message must be 4000 characters or fewer"),
    "plugin_chat_not_chat_type": ("插件 {name} 不是 chat 型，无法对话", "Plugin {name} is not a chat-type plugin"),
    "plugin_chat_no_persona": ("插件未配置 persona", "Plugin has no persona configured"),
    "plugin_chat_rate_limited": ("对话太频繁，请稍后再试", "Too many requests, please try again later"),
    "plugin_llm_failed": ("AI 服务调用失败: {err}", "AI service call failed: {err}"),
    # 48b 角色开放成 API
    "ai_character_not_found": ("角色不存在", "Character not found"),
    "ai_character_forbidden": ("无权访问该角色", "You do not have access to this character"),
    "ai_character_no_byok": ("未配置 AI 服务（BYOK）", "No AI service configured (BYOK)"),
    "ai_chat_input_empty": ("消息内容不能为空", "Message cannot be empty"),
    "ai_chat_input_too_long": ("消息内容不能超过 4000 字", "Message must be 4000 characters or fewer"),
    "ai_chat_rate_limited": ("对话太频繁，请稍后再试", "Too many requests, please try again later"),
    "ai_llm_failed": ("AI 服务调用失败: {err}", "AI service call failed: {err}"),
    "wf_import_missing_fields": ("缺少 plugin_name 或 template_id", "Missing plugin_name or template_id"),
    "wf_import_not_workflow_type": ("插件 {name} 不是 workflow 型", "Plugin {name} is not a workflow-type plugin"),
    "wf_template_invalid": ("插件模板格式无效", "Invalid workflow template"),
    "wf_template_not_found": ("未找到模板 {id}", "Template {id} not found"),
    "wf_template_nodes_empty": ("模板节点不能为空", "Template nodes cannot be empty"),
    "wf_template_too_many_nodes": ("模板节点数不能超过 {max}", "Template nodes must be at most {max}"),
    "wf_template_too_many_edges": ("模板连线数不能超过 {max}", "Template edges must be at most {max}"),
    "wf_template_bad_node": ("第 {n} 个节点格式无效", "Node #{n} has an invalid format"),
    "wf_template_dup_node_id": ("节点 id 重复: {nid}", "Duplicate node id: {nid}"),
    "wf_template_bad_edge": ("第 {n} 条连线格式无效", "Edge #{n} has an invalid format"),
    # 远程市场
    "market_remote_disabled": ("远程市场未启用", "Remote marketplace is disabled"),
    "market_empty_urls": ("未配置远程市场地址", "No remote marketplace URLs configured"),
    "market_url_invalid": ("无效的市场地址: {url}", "Invalid marketplace URL: {url}"),
    "market_index_invalid": ("市场索引格式无效", "Invalid marketplace index"),
    "market_index_too_large": ("市场索引超过 1MB 限制", "Index exceeds 1MB limit"),
    "market_item_invalid": ("市场条目无效: {name}", "Invalid marketplace item: {name}"),
    "market_download_failed": ("下载失败: {err}", "Download failed: {err}"),
    "market_sha_mismatch": ("校验和不符，内容可能被篡改", "SHA256 mismatch, content may be tampered"),
    "market_size_exceed": ("文件超过大小限制", "File exceeds size limit"),
    "market_host_not_allowed": ("域名不在白名单: {host}", "Host not in whitelist: {host}"),
    "market_need_https": ("仅支持 https 地址（调试可放行 http）", "HTTPS required (http allowed in debug)"),
    "market_refresh_cooldown": ("距上次刷新未到间隔（可传 force=true 强制刷新）", "Refresh interval not reached (use force=true) "),
    # 表情市场（2026-08-23）：远程索引 + 市场表情包
    "emoji_market_no_pack": ("市场中无此表情包", "No such emoji pack in marketplace"),
    "emoji_manifest_invalid": ("表情包清单校验失败: {err}", "Invalid emoji pack manifest: {err}"),
    "emoji_zip_dup_file": ("表情包内文件名重复: {n}", "Duplicate file name in emoji pack: {n}"),
    "emoji_market_not_installed": ("表情包未安装", "Emoji pack not installed"),
    "emoji_market_uninstall_failed": ("卸载失败: {err}", "Uninstall failed: {err}"),
    # 48a 插件页面层：页面托管 / 桥 API / store / http 代理 / 卸载
    "plugin_page_not_found": ("页面不存在", "Page not found"),
    "plugin_page_too_large": ("页面文件过大", "Page file too large"),
    "bridge_api_unknown": ("不支持的桥 API: {api}", "Unsupported bridge API: {api}"),
    "bridge_ai_rate_limited": ("AI 调用太频繁，请稍后再试", "AI calls too frequent, please try again later"),
    "store_key_invalid": ("存储 key 无效", "Invalid store key"),
    "store_value_not_json": ("存储值不是合法 JSON", "Store value is not valid JSON"),
    "store_value_too_large": ("存储值超过 100KB 限制", "Store value exceeds 100KB limit"),
    "http_scheme_not_allowed": ("仅支持 https 请求（调试可放行 http）", "HTTPS required (http allowed in debug)"),
    "http_ssrf_blocked": ("请求地址被安全策略拦截", "Request URL blocked by security policy"),
    "http_failed": ("HTTP 请求失败: {err}", "HTTP request failed: {err}"),
    "plugin_uninstall_failed": ("卸载失败: {err}", "Uninstall failed: {err}"),
    "plugin_uninstall_builtin": ("内置插件不可卸载", "Builtin plugins cannot be uninstalled"),
    # 备份一键导出（#54）
    "backup_failed": ("备份失败，请稍后重试", "Backup failed, please try again later"),
    "backup_not_found": ("未找到备份文件", "No backup file found"),
    # #46 主账号管理（选择型，2026-08-24）
    "admin_enabled_invalid": ("enabled 参数无效", "Invalid enabled parameter"),
    "admin_keep_one": ("至少保留一个主账号", "At least one main account must remain"),
    # #68 修订：主账号管理按家庭范围隔离（2026-08-28）
    "admin_cannot_toggle_self": ("不能修改自己的主账号状态", "You cannot change your own admin status"),
    "admin_target_not_in_family": ("只能管理自己家庭内的账号", "You can only manage accounts in your own family"),
    # #28 ③ 手动触发测试接口（2026-08-24）
    "trigger_test_forbidden": ("仅主账号可执行手动触发测试", "Only main account can run trigger test"),
    "trigger_test_character_required": ("请指定要测试的角色", "Character id is required"),
    "trigger_test_invalid_type": ("不支持的触发类型", "Unsupported trigger type"),
    # MCP 接入（Phase 1-2，2026-08-26）
    "mcp_name_invalid": ("MCP 名称不合法（需字母/数字/_- 且不超过 64 位）", "Invalid MCP name (letters/digits/_- , max 64 chars)"),
    "mcp_command_required": ("stdio 传输必须配置 command", "command is required for stdio transport"),
    "mcp_url_required": ("sse/streamable_http 传输必须配置 url", "url is required for sse/streamable_http transport"),
    "mcp_url_invalid": ("MCP url 不合法或已被安全策略拦截: {err}", "Invalid or blocked MCP url: {err}"),
    "mcp_transport_unsupported": ("不支持的传输类型（仅 stdio/sse/streamable_http）", "Unsupported transport (stdio/sse/streamable_http only)"),
    "mcp_not_found": ("MCP Server 不存在", "MCP server not found"),
    "mcp_name_conflict": ("同名 MCP Server 已存在", "An MCP server with the same name already exists"),
    "mcp_connect_failed": ("连接失败: {err}", "Connect failed: {err}"),
    "mcp_test_failed": ("测试连接失败: {err}", "Test connection failed: {err}"),
    "mcp_update_failed": ("更新失败: {err}", "Update failed: {err}"),
    "mcp_delete_failed": ("删除失败: {err}", "Delete failed: {err}"),
    "mcp_arguments_invalid": ("参数无效", "Invalid arguments"),
    # MCP 工具权限（Phase 3，2026-08-27）
    "mcp_tool_not_found": ("MCP 工具不存在", "MCP tool not found"),
    "mcp_mode_invalid": ("权限等级无效（仅 allow/ask/forbid）", "Invalid permission mode (allow/ask/forbid only)"),
    "mcp_permission_saved": ("权限已保存", "Permission saved"),
}


def lang_of(request: Request | None) -> str:
    if request is None:
        return "zh"
    lang = (request.headers.get("X-Lang") or "zh").strip().lower()
    return "en" if lang.startswith("en") else "zh"


def tr_lang(lang: str, key: str, **kwargs: Any) -> str:
    zh, en = _MESSAGES.get(key, (key, key))
    msg = en if lang.strip().lower().startswith("en") else zh
    for k, v in kwargs.items():
        msg = msg.replace("{" + k + "}", str(v))
    return msg


def tr(request: Request | None, key: str, **kwargs: Any) -> str:
    return tr_lang(lang_of(request), key, **kwargs)
