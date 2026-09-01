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


def register_game(game_type: str, engine_cls, meta: dict) -> None:
    """X1（2026-08-31）：注册游戏扩展包（仅插件 main.py 加载期可调）。

    - 与内置游戏走同一注册口 app.games.registry.register_game_type（source=本插件名）；
    - 插件停用后其游戏自动从 engine_for/list_games 隐藏（来源启用过滤）；
    - 插件目录被移除后 sync_plugins_db 清理残留注册；
    - 内核保留不变量：房间/回合/主持/游戏记忆隔离，扩展包只提供规则引擎。
    """
    source = registry.current_plugin_name()
    if source is None:
        raise RuntimeError("sdk.register_game 只能在插件 main.py 加载时调用")
    from app.games.registry import register_game_type
    register_game_type(game_type, engine_cls, meta or {}, source=source)


def register_provider(kind: str, name: str, factory, meta: dict | None = None) -> None:
    """X3（2026-08-31）：注册 Provider 扩展（仅插件 main.py 加载期可调）。

    - 与内置实现走同一注册口 app.providers.registry.register_provider（source=本插件名）；
    - kind ∈ llm/tts/asr/vision/image/push；factory(config) 契约见注册表 docstring
      （每次请求调用，密钥只经运行时 config 下发，禁止打进扩展包）；
    - 选中方式：配置的 provider 字段（api_configs.provider / speech_configs.provider）
      与注册名精确匹配；未匹配时内置实现兜底；插件停用后不可选；
    - 插件目录被移除后 sync_plugins_db 清理残留注册。
    """
    source = registry.current_plugin_name()
    if source is None:
        raise RuntimeError("sdk.register_provider 只能在插件 main.py 加载时调用")
    from app.providers.registry import register_provider as _reg
    _reg(kind, name, factory, meta or {}, source=source)


# ── X4（2026-08-31）：只读端口——受控只读访问内核数据，替代"import 内部模块"（重构即碎）──
# 每个端口都要求 manifest 显式声明对应只读权限；返回脱敏快照，不暴露 ORM 对象与内部字段。


async def get_persona(character_id: int) -> dict:
    """只读人格公开字段（需 persona:read）：name/personality/self_statement。"""
    require_permission("persona:read")
    from sqlalchemy import select as _select
    from app.db.database import async_session_factory
    from app.models.character import AICharacter

    async with async_session_factory() as db:
        row = (await _db_execute(db, _select(AICharacter).where(AICharacter.id == int(character_id)))).scalar_one_or_none()
    if row is None:
        return {}
    return {
        "name": row.name,
        "personality": (row.personality or "")[:500],
        "self_statement": (row.self_statement or "")[:500],
    }


async def search_memory(character_id: int, query: str, *, limit: int = 5, types: list[str] | None = None) -> list[dict]:
    """受控记忆检索（需 memory:read）：走内核多路召回+重排，含权限过滤与类型筛选。"""
    require_permission("memory:read")
    from app.memory.service import search_memories as _search

    results = await _search(character_id=int(character_id), query=str(query or ""), limit=max(1, min(int(limit), 10)))
    out = []
    for r in results:
        if types and r.get("type") not in types:
            continue
        out.append({
            "id": r.get("id"), "type": r.get("type"),
            "content": (r.get("content") or "")[:200],  # 脱敏：截断正文
            "importance": r.get("importance"),
        })
        if len(out) >= max(1, min(int(limit), 10)):
            break
    return out


async def get_relationship(character_id: int) -> dict:
    """只读关系快照（需 relationship:read）：信任/亲密度/好奇度（0-100 标量）。"""
    require_permission("relationship:read")
    from app.services.character_state_service import get_character_states

    st = await get_character_states(int(character_id))
    return {
        "trust": int(st.get("trust", 50)),
        "attachment": int(st.get("attachment", 50)),
        "curiosity": int(st.get("curiosity", 50)),
    }


async def get_life_state(character_id: int) -> dict:
    """只读当前状态快照（需 life:read）：八维中的体感/情绪维度（脱敏数值）。"""
    require_permission("life:read")
    from app.services.character_state_service import get_character_states

    st = await get_character_states(int(character_id))
    keys = ("mood", "body_temp", "desire", "possessiveness", "fatigue", "sensitivity", "comfort", "anger")
    return {k: int(st.get(k, 50)) for k in keys}


async def emit(event_type: str, payload: dict) -> None:
    """统一定向事件发布（需 send_message 之外无新增权限；领域事件走总线，不直接改库）。

    事件类型须带插件前缀（如 "my_plugin.thing_done"），防止伪造内核域事件。
    """
    name = registry.current_plugin_name()
    if name is None:
        raise RuntimeError("sdk.emit 只能在插件上下文中调用")
    et = str(event_type or "")
    if not et.startswith(f"{name}."):
        raise ValueError(f"事件类型须以插件名为前缀：{name}.*")
    from app.events.bus import event_bus
    event_bus.publish_async(et, {"source": name, **(payload or {})})


async def _db_execute(db, q):
    return await db.execute(q)
