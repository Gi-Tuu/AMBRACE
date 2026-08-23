"""插件 SDK：插件 main.py 通过本模块注册 hook 与使用运行时能力

用法（插件 main.py）：
    from app.plugins import sdk

    @sdk.hook("context_inject")
    async def inject(ctx):
        ctx["context_messages"].append({"role": "system", "content": "..."})

- hook 装饰器只能在插件 main.py 被加载时调用（registry 会设置当前插件名）。
- save_memory 需要 manifest 声明 permissions: ["write_memory"]，否则抛 PermissionError。
"""
from app.utils.logger import get_logger

from app.plugins import registry

_logger = get_logger("plugins")


def hook(hook_name: str):
    """装饰器：把函数注册为指定 hook 的处理函数（关联到当前加载的插件）"""
    def deco(func):
        current = registry.current_plugin_name()
        if current is None:
            raise RuntimeError("sdk.hook 只能在插件 main.py 加载时调用")
        registry._loaded[current]["hooks"].setdefault(hook_name, []).append(func)
        return func
    return deco


def action(action_name: str):
    """装饰器：注册插件自定义行为（arbiter 通过 registry.run_plugin_action 调用）。

    用于需要插件内部执行逻辑的候选（如抖音评论回复生成入队），
    区别于 hook 的广播分发；actions 由 arbiter 定向调用。
    """
    def deco(func):
        current = registry.current_plugin_name()
        if current is None:
            raise RuntimeError("sdk.action 只能在插件 main.py 加载时调用")
        registry._loaded[current].setdefault("actions", {})[action_name] = func
        return func
    return deco


def log(msg: str, *args) -> None:
    """插件日志（带插件名前缀写入 app.log；支持 %s 格式化）"""
    name = registry.current_plugin_name() or "?"
    try:
        text = msg % args if args else msg
    except (TypeError, ValueError):
        text = msg
    _logger.info("[plugin:%s] %s", name, text)


def get_config() -> dict:
    """读取当前插件配置（manifest 默认值 + DB 覆盖值合并）"""
    name = registry.current_plugin_name()
    if name is None:
        return {}
    entry = registry._loaded.get(name, {})
    info = entry.get("info", {})
    base = dict(info.get("config", {}))
    saved = dict(registry._db_config.get(name, {}))
    base.update(saved)
    return base


def require_permission(perm: str) -> None:
    """校验当前插件是否声明了指定权限，否则抛 PermissionError"""
    name = registry.current_plugin_name()
    if name is None:
        raise PermissionError("未在插件上下文中")
    perms = registry._loaded.get(name, {}).get("info", {}).get("permissions", [])
    if perm not in perms:
        raise PermissionError(f"插件 {name} 未声明权限 {perm}")


async def save_memory(user_id: int, character_id: int, memory_type: str, content: str,
                      importance: int = 2, **kwargs) -> None:
    """写记忆（需 manifest permissions: ["write_memory"]），复用主链路 save_memory"""
    require_permission("write_memory")
    from app.memory import save_memory as _save
    await _save(user_id=user_id, character_id=character_id, memory_type=memory_type,
                content=content, importance=importance, **kwargs)


async def send_message(character_id: int, user_id: int, content: str, message_type: str = "plugin") -> bool:
    """代表角色向用户发送主动消息（需 manifest permissions: ["send_message"]）。

    复用主链路 _send_message（自动取最新会话、走每小时限额、落库 chat 消息）。
    """
    require_permission("send_message")
    from app.scheduler.storyline_engine import _send_message
    return await _send_message(character_id, user_id, content, message_type=message_type)


def router():
    """创建插件专属路由（供 http_router 能力使用）：prefix=/api/v1/plugins/<name>，main.py 里 @router.get(...) 注册后由 registry 启动时挂载"""
    name = registry.current_plugin_name()
    if name is None:
        raise RuntimeError("sdk.router 只能在插件 main.py 加载时调用")
    from fastapi import APIRouter, Depends
    # P0-11 安全加固（2026-08-16）：插件 HTTP 路由统一要求登录态，防局域网匿名调用（SSRF/RCE 面）
    from app.auth.deps import get_current_user_id
    r = APIRouter(
        prefix=f"/api/v1/plugins/{name}",
        tags=[name],
        dependencies=[Depends(get_current_user_id)],
    )
    registry._loaded[name]["router"] = r
    return r
