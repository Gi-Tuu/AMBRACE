"""内置工具注册入口（AMBRACE 重构步骤 8）。

把 4 个内置工具（search / image_gen / note_calendar / note_memo）的**执行入口**注册到
ToolRegistry（app/agent/tools.py）。原先在 agent/tools.py._register_builtin_tools 里只读登记
（execute=None，执行仍走 chat_service 私有链路），本步骤把 execute 补成真函数，逐步收敛到
统一 tool_runner.execute_tool。

设计约定：
- 4 个工具的执行入口在 app/tools/builtin/*.py，execute 内惰性 import services（避免 import 循环）；
- register_builtin_tools() 幂等可重入（重复登记覆盖同名条目，无副作用），由 main.py 启动时调用；
- timer / status_update / memory_extract 等内部工具保留在 app/agent/tools.py，不进 builtin。
"""
__all__ = ["register_builtin_tools"]


def register_builtin_tools() -> int:
    """注册 4 个内置工具（幂等：重复调用覆盖同名条目，无副作用）。返回登记数。

    惰性 import app.tools.builtin，避免 agent↔tools 包级循环（builtin 内 execute 再惰性 import services）。
    """
    from app.tools.builtin import register_all

    handled = register_all()
    return handled
