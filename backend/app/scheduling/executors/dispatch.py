"""etype 分派入口 — dispatch（A20 批 4a，方案 §2.3）

``arbiter._execute`` 在 ``pre_gates`` 之后调用本函数：命中注册表 ⇒ 透传 handler 的 ``bool``；
未接管 ⇒ 返回 ``None``，arbiter 继续走自己剩下的梯子（outreach 五类 / plugin，批 4b 再搬）。
「未命中返回 ``None``」与旧梯子末尾的 ``return False`` 不等价，所以**未接管必须由 ``None`` 显式表达**，
调用方用 ``is not None`` 判定，别写成真值判断。

签名与 handler 一致（``item`` / ``candidate`` / ``char_id`` / ``g``），保证批 4b 搬剩余分支时
只需加一个 ``@handler``，不必再动 arbiter。
"""
from app.scheduling.executors.context import GateBundle
from app.scheduling.executors.registry import HANDLERS

# 以下五个 import **只为触发各模块的 @handler 注册**（删掉任何一行，对应 etype 会静默回落到
# arbiter 梯子——守卫测试 ⑨-1 钉住键集覆盖）。批 4b 搬 outreach / plugin 时在此追加。
from app.scheduling.executors import moment, memory, social, special, story  # noqa: F401


async def dispatch(item: dict, etype: str, candidate: dict, char_id: int, g: GateBundle) -> bool | None:
    """按 etype 查表执行；未接管返回 ``None``（调用方继续走自己的分支）。"""
    fn = HANDLERS.get(etype)
    if fn is None:
        return None          # 未接管（outreach 五类 / plugin 仍由 arbiter 处理）
    return await fn(item, candidate, char_id, g)
