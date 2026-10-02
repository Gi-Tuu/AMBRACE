"""行为执行器（A20 批 3a 起）— 前置闸 + 执行上下文 + etype 执行器

目录定位：``arbiter._execute`` 的执行侧逻辑按「闸判定 → etype 分支」分层落在这里。
批 3a 落 ``guards``（跨类型前置闸）与 ``context``（GateBundle）；批 3b 落 ``timer``
（定时承诺兑现）；批 4a 落 ``registry`` + ``dispatch``（分派表）与 ``social`` / ``memory`` /
``story`` / ``special`` / ``moment`` 五组 etype 执行器；批 4b 落 ``outreach``（主动搭话五类）
与 ``plugin``（插件/渠道主动候选），至此 ``_execute`` 只剩 timer 薄调用 → 前置闸 → 分派 → 兜底。

⚠ 本包**不得** import 节流闸函数（is_dnd_now / has_user_said_sleep / is_user_active / …）
与会话工厂 ``async_session_factory``：调用点一旦落到本包，tests/ 打在 arbiter 命名空间上的
monkeypatch 桩就会静默失效。闸函数与会话工厂由 arbiter 的 ``_gates()`` 在调用时刻现取、经
GateBundle 注入。

⚠ ``dispatch`` 必须在包导入期被拉起（它 import 各 handler 模块触发 ``@handler`` 注册），
否则 ``HANDLERS`` 是空表、分派静默失效。
"""
from app.scheduling.executors.context import GateBundle, agent_flag_on
from app.scheduling.executors.guards import pre_gates
from app.scheduling.executors.registry import BACKGROUND_TYPES, HANDLERS, handler
from app.scheduling.executors.timer import run_timer
# ⚠ 这里绑定的必须是**子模块本身**：写成 ``from …dispatch import dispatch`` 会把包属性
#   ``executors.dispatch`` 遮蔽成函数，tests/ 与 arbiter 按模块路径取名的地方都会拿错对象。
from app.scheduling.executors import dispatch  # noqa: F401
# A20 批 4b：outreach / plugin 两模块的 @handler 登记同样靠这两行触发（dispatch 只 import 批 4a 那五组）
from app.scheduling.executors import outreach, plugin  # noqa: F401

__all__ = [
    "BACKGROUND_TYPES", "GateBundle", "HANDLERS", "agent_flag_on", "dispatch",
    "handler", "outreach", "plugin", "pre_gates", "run_timer",
]
