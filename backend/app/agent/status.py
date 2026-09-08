"""执行状态口径统一（2026-08-23）：agent_task_logs / agent_tasks 的 status 语义归一化。

各写入点历史状态值混用 ok / error / blocked / done / failed / partial。本模块提供唯一的
语义分类函数，供成功率统计 / 前端展示 / 行为基准脚本一致解释（统一口径，不改任何写入逻辑）：

- success：执行成功（ok / done / success / succeeded）
- failed：执行失败（error / failed / failure）
- partial：部分步骤未全成功（不按整体失败计）
- blocked：被权限/限额/开关真正拦截（未实际执行，不是失败，不计入成功率分母）
- skipped：本轮未触发 / 未尝试（中性，既非失败也非拦截；R2 2026-09-09 从 blocked 中拆出）
"""
from typing import Optional

_SUCCESS = {"ok", "done", "success", "succeeded"}
_FAILED = {"error", "failed", "failure"}
_PARTIAL = {"partial", "partial_success", "partially_done"}
# R2（2026-09-09，工具轨迹治理 §4.2.1）：skipped 独立成「中性未触发」桶，不再并进 blocked。
# 历史库里已写入的旧值仍为 blocked，classify 后归 blocked（不回填、不删数据）。
_SKIPPED = {"skipped", "not_attempted"}
_BLOCKED = {"blocked", "intercepted"}

SUCCESS = "success"
FAILED = "failed"
PARTIAL = "partial"
BLOCKED = "blocked"
SKIPPED = "skipped"


def classify(status: Optional[str]) -> str:
    """把杂化 status 归一化为语义桶：success / failed / partial / skipped / blocked / unknown"""
    s = (status or "").strip().lower()
    if s in _SUCCESS:
        return SUCCESS
    if s in _FAILED:
        return FAILED
    if s in _PARTIAL:
        return PARTIAL
    if s in _SKIPPED:
        return SKIPPED
    if s in _BLOCKED:
        return BLOCKED
    return "unknown"


def is_success(status: Optional[str]) -> bool:
    return classify(status) == SUCCESS


def is_failed(status: Optional[str]) -> bool:
    return classify(status) == FAILED
