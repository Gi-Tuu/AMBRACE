"""内置工具注册包（AMBRACE 重构步骤 8）。

把 4 个内置工具（search / image_gen / note_calendar / note_memo）的执行入口依次登记到
ToolRegistry。模块 import 无副作用；注册经 register_all() 显式触发（幂等覆盖，可重入）。
"""
from app.tools.builtin import calendar_tool, image_tool, memo_tool, search_tool


def register_all() -> int:
    """依次调用 4 个内置工具的 register()（幂等覆盖同名条目，无副作用）。返回登记数。"""
    calendar_tool.register()
    memo_tool.register()
    search_tool.register()
    image_tool.register()
    return 4


__all__ = ["register_all"]
