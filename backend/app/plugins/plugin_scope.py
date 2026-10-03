"""插件租户可见性与运行时作用域（A22 第九刀 · ④c，2026-10-03 自 `plugins/registry.py` 逐字节搬入）。

边界：本模块只管「某个插件对某个家庭根是否可见 / 能否进入本次运行面」——flag 读取口
（`plugin_user_scope_enabled` / `plugin_runtime_scope_enabled` / `plugin_disabled_route_gate_enabled`）、
可见性谓词、30s 进程内缓存的读写逻辑、hook 分发范围解析、插件自定义 REST 的统一依赖闸。
**插件扫描/加载/库表同步与安装溯源在 `plugin_store.py`（④a）**；**同意与能力判定在
`plugin_consent.py`（④b）**；`_loaded` / `_enabled` / `_db_prov` / `_logger` 与三个缓存本体
（`_visible_names_cache` / `_caller_tenant_cache` / `_warned_no_caller`）及 `_RUNTIME_SCOPE_TTL`
**仍留在 `registry.py`**（tests 直接读 `registry._warned_no_caller`、在 registry 上打桩 ⇒ 状态本体不许搬家）。

搬家口径（与 A20／A22 前八刀一致，由 `tests/test_arbiter_seam.py` 的接缝守卫钉住）：
- 函数体**逐字节照搬**：不改签名、不合并相似逻辑（三个 `plugin_*_enabled` flag 读取口键名不同、
  异常兜底同为 False，禁止顺手合并成一个参数化函数）；
- **R2**：引用仍留在 registry 的名字一律在**函数体内** `from app.plugins import registry as _reg`
  后写 `_reg.<name>`；三个缓存本体也经 `_reg.` 就地读写（保持「跨模块同一份 dict/set」语义）；
- **R3**：**同批搬走**的函数互相调用**同样**走 `_reg.<name>`——`setattr(registry,
  "plugin_disabled_route_gate_enabled", …)`（test_mount_plugin_routers_enabled_gate.py:44）
  与 `setattr(registry, "clear_runtime_scope_caches", …)`（test_arbiter_seam.py:3438）打在 registry 上，
  写成裸名就会解析到本模块自己那份 ⇒ 桩静默失效；
- registry 侧**具名重导出**本模块 15 个名字，`registry.<name>` 的既有调用点
  （`api/plugins.py` / `api/marketplace.py` / `api/plugin_bridge.py` / `agent/tools.py` /
  `plugins/sdk.py` / `plugins/config_hooks.py` / `plugin_store.py` 的 `_reg.` 回指）一律不变。
"""
import time as _time   # 标准库、非打桩面：与 ④b 在顶层 import json 同法


def plugin_user_scope_enabled() -> bool:
    """A2 M3（2026-09-20）：读 ``plugin_user_scope`` flag（默认关；延迟 import + 异常兜底 False）。

    flag 关 = 插件列表不做任何归属过滤（逐字节旧行为）。本函数是「可见性过滤是否生效」的
    唯一判定口：registry 与 api 层共用同一处判断，避免两处各读一次 flag 造成口径漂移。
    """
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("plugin_user_scope", False))
    except Exception:
        return False


def plugin_visible_to_tenant(*, source: str | None, owner_user_id: int | None,
                             owner_tenant_id: int | None,
                             viewer_tenant_id: int | None) -> bool:
    """A2 M3 纯谓词：插件对「某家庭根账号」是否可见（调用方负责 flag 门控）。

    可见 = ``source == "builtin"`` ∪ ``owner_tenant_id == 调用者家庭根``
          ∪ ``owner_user_id IS NULL``（存量/服务级；内置行 owner 两列也为 NULL）。

    ``viewer_tenant_id is None``（调用者家庭根解析失败 / 拿不到）→ **fail-closed**：
    只保留 ``builtin ∪ owner_user_id IS NULL`` 的「最保守集合」，绝不因为拿不到租户就全量放行。
    异常（脏数据/比较失败）同样 fail-closed 返回 False。
    """
    try:
        if str(source or "builtin") == "builtin":
            return True
        if owner_user_id is None:
            return True
        if viewer_tenant_id is None:
            return False
        return owner_tenant_id is not None and int(owner_tenant_id) == int(viewer_tenant_id)
    except (TypeError, ValueError):
        return False


def plugin_runtime_scope_enabled() -> bool:
    """A2 M4：读 ``plugin_runtime_scope`` flag（默认关；延迟 import + 异常兜底 False）。

    flag 关 = 运行面逐字节旧行为（hook 全量分发、工具全量登记、无 sdk 归属断言、桥/页面无归属闸）。
    """
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("plugin_runtime_scope", False))
    except Exception:
        return False


def plugin_is_builtin(name: str) -> bool:
    """插件是否内置（source=builtin；无来源记录的行按内置处理，与 M3 谓词口径一致）。"""
    from app.plugins import registry as _reg   # A22 ④c：留原模块与同批搬走的名字一律调用时刻现取
    return str((_reg._db_prov.get(name) or {}).get("source") or "builtin") == "builtin"


def clear_runtime_scope_caches() -> None:
    """清空 M4 运行面缓存（插件重扫 / 测试隔离用；sdk 侧字符归属缓存由 sdk 自清）。"""
    from app.plugins import registry as _reg   # A22 ④c：留原模块与同批搬走的名字一律调用时刻现取
    _reg._visible_names_cache.clear()
    _reg._caller_tenant_cache.clear()
    _reg._warned_no_caller.clear()


async def resolve_caller_tenant_cached(user_id: int | None) -> int | None:
    """解析调用者家庭根（30s 进程内缓存；无 user_id / 解析失败 → None）。"""
    from app.plugins import registry as _reg   # A22 ④c：留原模块与同批搬走的名字一律调用时刻现取
    if not user_id:
        return None
    uid = int(user_id)
    now = _time.monotonic()
    hit = _reg._caller_tenant_cache.get(uid)
    if hit is not None and (now - hit[0]) < _reg._RUNTIME_SCOPE_TTL:
        return hit[1]
    tid = await _reg.resolve_tenant_for_user(uid)
    _reg._caller_tenant_cache[uid] = (now, tid)
    return tid


def _visible_plugin_names(viewer_tenant_id: int | None) -> frozenset[str]:
    """某家庭根可见的插件名集合（30s 缓存；复用 M3 纯谓词 :func:`plugin_visible_to_tenant`）。"""
    from app.plugins import registry as _reg   # A22 ④c：留原模块与同批搬走的名字一律调用时刻现取
    now = _time.monotonic()
    hit = _reg._visible_names_cache.get(viewer_tenant_id)
    if hit is not None and (now - hit[0]) < _reg._RUNTIME_SCOPE_TTL:
        return hit[1]
    names = frozenset(
        n for n in list(_reg._loaded)
        if _reg.plugin_visible_to_tenant(
            source=(_reg._db_prov.get(n) or {}).get("source", "builtin"),
            owner_user_id=(_reg._db_prov.get(n) or {}).get("owner_user_id"),
            owner_tenant_id=(_reg._db_prov.get(n) or {}).get("owner_tenant_id"),
            viewer_tenant_id=viewer_tenant_id,
        )
    )
    _reg._visible_names_cache[viewer_tenant_id] = (now, names)
    return names


async def _runtime_scope_viewer(user_id: int | None, tenant_id: int | None) -> int | None:
    """本次运行面的调用者家庭根：显式 tenant_id 优先，否则按 user_id 走缓存解析。"""
    from app.plugins import registry as _reg   # A22 ④c：留原模块与同批搬走的名字一律调用时刻现取
    if tenant_id is not None:
        return int(tenant_id)
    return await _reg.resolve_caller_tenant_cached(user_id)


async def plugin_in_runtime_scope(name: str, *, user_id: int | None = None,
                                  tenant_id: int | None = None) -> bool:
    """flag 门控：该插件是否允许进入本次运行面（= 对该调用者可见）。

    - flag 关 → 恒 True（逐字节旧行为）；
    - flag 开 + 拿不到调用者家庭根 → **fail-closed**：只放行内置插件（绝不全放）；
    - flag 开 + 有家庭根 → 走 M3 谓词（内置 ∪ 本家庭安装 ∪ 服务级 owner 为空）。
    """
    from app.plugins import registry as _reg   # A22 ④c：留原模块与同批搬走的名字一律调用时刻现取
    if not _reg.plugin_runtime_scope_enabled():
        return True
    viewer = await _reg._runtime_scope_viewer(user_id, tenant_id)
    if viewer is None:
        return _reg.plugin_is_builtin(name)
    return name in _reg._visible_plugin_names(viewer)


async def plugin_visible_for_caller(name: str, user_id: int | None) -> bool:
    """flag 门控：桥/页面端点用——该插件对调用者是否可见（flag 关 → 恒 True = 旧行为）。"""
    from app.plugins import registry as _reg   # A22 ④c：留原模块与同批搬走的名字一律调用时刻现取
    return await _reg.plugin_in_runtime_scope(name, user_id=user_id)


def plugin_disabled_route_gate_enabled() -> bool:
    """A2 M0-3：读 ``plugin_disabled_route_gate`` flag（默认关；延迟 import + 异常兜底 False）。

    与 ``app/api/plugin_bridge.py`` 的 ``_plugin_disabled_gate`` 同一 flag 名、同一语义
    （「插件停用后彻底不可访问」）：桥 / chat / 页面托管 / 插件自定义 REST 共用一处判定口径。
    """
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("plugin_disabled_route_gate", False))
    except Exception:
        return False


def plugin_http_gate(name: str):
    """P2-2（2026-09-20）：插件自定义 REST（``sdk.router()``）的统一依赖闸。

    工厂闭包：插件名在 ``sdk.router()`` 创建期即已知，按名生成依赖，运行期不必解析路径。
    补齐桥（``/{name}/bridge``）与页面托管（``/{name}/page/...``）早已具备、唯独自定义
    REST 缺失的两道闸，避免「停用插件的页面 404、但它的 API 仍可调」：

    1. **运行时禁用闸**（flag ``plugin_disabled_route_gate``）：只读内存缓存 ``_enabled``
       （由 ``sync_plugins_db`` / ``set_plugin_state`` 维护），零查库；停用 → 404；
    2. **租户可见性闸**：复用 :func:`plugin_visible_for_caller`（flag
       ``plugin_runtime_scope`` 的判定收在其内部）；对本调用者不可见 → 404；
    3. 过闸后 ``push_sdk_context(name, user_id=调用者)``，响应结束 ``reset_sdk_context``。
       异步生成器依赖与端点在同一协程内执行，故插件 HTTP handler 里的 ``sdk.*``
       能解析到自己的身份（改前恒为 None：REST 路径上 get_config / require_permission /
       save_memory 等全部失效）；同时把 caller 写进上下文，使 M4 归属断言在 REST 路径生效。

    两个 flag 全关（默认）时本依赖只有一次内存读并立即放行，与改动前逐字节一致、不查库。
    404 复用既有 ``plugin_not_found`` 文案（不新增 i18n key、不泄漏插件存在性）。
    """
    from app.plugins import registry as _reg   # A22 ④c：留原模块与同批搬走的名字一律调用时刻现取
    from fastapi import Depends, Header, HTTPException

    from app.auth.deps import get_current_user_id
    from app.i18n import tr_lang

    async def gate(user_id: int = Depends(get_current_user_id),
                   lang: str = Header(default="zh")):
        if _reg.plugin_disabled_route_gate_enabled() and not bool(_reg._enabled.get(name, False)):
            raise HTTPException(status_code=404, detail=tr_lang(lang, "plugin_not_found"))
        if not await _reg.plugin_visible_for_caller(name, user_id):
            raise HTTPException(status_code=404, detail=tr_lang(lang, "plugin_not_found"))
        _token = _reg.push_sdk_context(name, user_id=user_id)
        try:
            yield
        finally:
            _reg.reset_sdk_context(_token)

    return gate


def _warn_no_caller_once(hook_name: str, callsite: str) -> None:
    """「拿不到 caller」告警一次（按 hook@调用点去重，避免 hot path 刷屏）。"""
    from app.plugins import registry as _reg   # A22 ④c：留原模块与同批搬走的名字一律调用时刻现取
    key = f"{hook_name}@{callsite}"
    if key in _reg._warned_no_caller:
        return
    _reg._warned_no_caller.add(key)
    _reg._logger.warning(
        "plugin_runtime_scope 开但 %s 拿不到调用者（callsite=%s）→ 只向内置插件分发 hook（fail-closed）",
        hook_name, callsite,
    )


async def _resolve_hook_scope(hook_name: str, *, user_id: int | None, tenant_id: int | None,
                              callsite: str) -> tuple[frozenset[str] | None, int | None]:
    """解析本次 hook 分发范围，返回 ``(allowed, viewer_tenant_id)``。

    ``allowed is None`` = 不过滤（flag 关，逐字节旧行为）；flag 开时为「对该调用者可见」的插件名集合；
    拿不到调用者 → fail-closed 只留内置插件，并按调用点告警一次。
    """
    from app.plugins import registry as _reg   # A22 ④c：留原模块与同批搬走的名字一律调用时刻现取
    if not _reg.plugin_runtime_scope_enabled():
        return None, None
    viewer = await _reg._runtime_scope_viewer(user_id, tenant_id)
    if viewer is None:
        if any(not _reg.plugin_is_builtin(n) for n in list(_reg._loaded)):
            _reg._warn_no_caller_once(hook_name, callsite)
        return frozenset(n for n in list(_reg._loaded) if _reg.plugin_is_builtin(n)), None
    return _reg._visible_plugin_names(viewer), viewer


async def resolve_viewer_tenant(user_id: int | None) -> int | None:
    """A2 M3：flag 开时解析调用者家庭根，供列表/市场可见性过滤用；flag 关 → 不查库返回 None。

    ``list_plugins`` 是同步函数，无法 await ``get_family_root_id``；家庭根解析必须由异步入口层
    完成，再把结果作为 ``viewer_tenant_id`` 传入。flag 关时直接返回 None（既有行为零额外查库，
    调用方 ``list_plugins`` 也因 flag 关而跳过过滤）。
    """
    from app.plugins import registry as _reg   # A22 ④c：留原模块与同批搬走的名字一律调用时刻现取
    if not _reg.plugin_user_scope_enabled():
        return None
    return await _reg.resolve_tenant_for_user(user_id)
