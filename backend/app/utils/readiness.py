"""进程内组件就绪登记（AMBRACE 3.5，2026-09-02）：关键组件未就绪 => /ready 503，可选组件降级仅登记可见。

设计边界（铁律保持不变）：
- 只治理「启动期组件」的可见性（main.py lifespan 播种），绝不扩大到业务链路——
  陪伴主回复链路上的异步任务失败仍保持「静默、不阻塞回复」（见 docs/dev-changelog 2026-08-16 定调）。
- 本例是进程内单例登记表（module-level dict），非持久化、无跨进程协议：/ready 据此返回
  200/503，供 watchdog/部署探针判断「启动是否完整」，不代替运行期依赖探活（/health）。

参考：D:\\Users\\sheng\\Downloads\\AMBRACE_两份外部审查_核验汇总与修复方案_20260902.md 3.5 节。
"""
import threading

# 组件名 -> {"ok": bool, "critical": bool, "msg": str}
_STATE: dict[str, dict] = {}
_LOCK = threading.Lock()


def reset() -> None:
    """清空就绪登记。

    - 应用启动前调用（main.py lifespan 入口），保证只反映本次启动。
    - 测试夹具在每用例前后调用，避免跨用例污染进程级登记表。
    """
    with _LOCK:
        _STATE.clear()


def mark(name: str, ok: bool, critical: bool = False, msg: str = "") -> None:
    """登记一个启动组件的就绪状态（幂等覆盖，同名后写覆盖先写）。

    - name：组件名（如 database / alembic / scheduler / plugins / embedding ...）。
    - ok：组件是否就绪。
    - critical：是否为关键路径组件——任一 critical 且 ok=False 时 /ready 返回 503 并列入 blocking。
    - msg：失败/降级说明（可选，暴露到 /ready 的 components[].msg 供前端诊断）。
    """
    with _LOCK:
        _STATE[name] = {"ok": bool(ok), "critical": bool(critical), "msg": msg}


def snapshot() -> dict:
    """返回就绪快照。

    - ready：所有 critical 组件均 ok 才为 True。
    - blocking：未就绪的 critical 组件名列表（任一非空则 ready=False）。
    - components：全部已登记组件（含可选组件的降级状态），供前端诊断并展示细粒度状态。

    components 返回的是深拷贝，调用方修改不会影响进程内登记表。
    """
    with _LOCK:
        components = {k: dict(v) for k, v in _STATE.items()}
    blocking = [k for k, v in components.items() if v["critical"] and not v["ok"]]
    return {"ready": not blocking, "components": components, "blocking": blocking}


def get(name: str) -> dict | None:
    """按组件名取单个登记状态（未登记返回 None）。诊断/测试用，不改变语义。"""
    with _LOCK:
        entry = _STATE.get(name)
        return dict(entry) if entry is not None else None
