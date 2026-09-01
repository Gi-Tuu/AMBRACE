"""MCP 生命周期清理（F1 拆分，2026-08-31）：异常退出时同步取消 stdio worker（atexit 防御纵深）。

自 manager.py 原样搬移；app.mcp.manager 保留同名重导出（兼容面不变）。
"""
import atexit

from app.utils.logger import get_logger

_logger = get_logger("mcp.lifecycle")


# ------------------------------------------------------------------ 异常退出清理（P3-B，2026-08-29）
def _emergency_cleanup() -> None:
    """SIGKILL/崩溃时同步清理 stdio 子进程（atexit 中不能 await）。

    尽力而为：取消所有仍在运行的 worker 任务 —— mcp SDK transport.__aexit__ 会终止子进程。
    正常关闭仍走 FastAPI lifespan 的 mcp_manager.shutdown()；atexit 只是防御纵深。
    注意：
    - mcp_manager 是模块单例，这里全程容错（任何异常不得打断解释器退出）；
    - atexit 不可 await，只能同步 cancel；若解释器退出时事件循环已关闭，cancel 可能不生效
      —— 这是该方案的上限（要可靠还需记录子进程 PID 后 SIGTERM，但 mcp SDK stdio 传输不暴露
      子进程句柄）。SIGKILL 本身不可捕获、atexit 不执行，只能靠容器/进程树托管等外部手段。
    """
    try:
        from app.mcp.manager import mcp_manager  # 延迟导入防循环
        for conn in list(getattr(mcp_manager, "_conns", {}).values()):
            worker = getattr(conn, "_worker", None)
            if worker is not None and not worker.done():
                try:
                    worker.cancel()
                except Exception:
                    pass
    except Exception:
        pass


atexit.register(_emergency_cleanup)
