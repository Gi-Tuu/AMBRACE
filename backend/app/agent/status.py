"""执行状态口径统一（2026-08-23）：agent_task_logs / agent_tasks 的 status 语义归一化。

各写入点历史状态值混用 ok / error / blocked / done / failed / partial。本模块提供唯一的
语义分类函数，供成功率统计 / 前端展示 / 行为基准脚本一致解释（统一口径，不改任何写入逻辑）：

- success：执行成功（ok / done / success / succeeded）
- failed：执行失败（error / failed / failure）
- partial：部分步骤未全成功（不按整体失败计）
- blocked：被限额/条件拦截（未实际执行，不是失败，不计入成功率分母）
"""
from typing import Optional

_SUCCESS = {"ok", "done", "success", "succeeded"}
_FAILED = {"error", "failed", "failure"}
_PARTIAL = {"partial", "partial_success", "partially_done"}
_BLOCKED = {"blocked", "skipped", "intercepted", "not_attempted"}

SUCCESS = "success"
FAILED = "failed"
PARTIAL = "partial"
BLOCKED = "blocked"


def classify(status: Optional[str]) -> str:
    """把杂化 status 归一化为语义桶：success / failed / partial / blocked / unknown"""
    s = (status or "").strip().lower()
    if s in _SUCCESS:
        return SUCCESS
    if s in _FAILED:
        return FAILED
    if s in _PARTIAL:
        return PARTIAL
    if s in _BLOCKED:
        return BLOCKED
    return "unknown"


def is_success(status: Optional[str]) -> bool:
    return classify(status) == SUCCESS


def is_failed(status: Optional[str]) -> bool:
    return classify(status) == FAILED
