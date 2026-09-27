"""LLM 用量渠道归因承载（A4 批 5 / T6 M2，2026-09-27）。

llm_usage 原本只有 task（用途）维度，摊不开「哪条渠道吃掉多少 token」。勘察结论：全仓记账
只有 `llm_client._record_usage_async` 一个写入咽喉，因此渠道取值也在那一处收口，本模块只负责
「当前渠道」的承载与词表，不做任何落库。

三值口径（与派单一致）：
- ``app``          App 主链路（WS / SSE / HTTP send 及图片/文件/语音/表情散点端点）
- ``wechat_ilink`` 微信桥（``send_and_receive(channel=...)`` 带进来的值）
- ``server``       后台任务（APScheduler/事件处理器，以及**请求内派生**的后台协程）

取不到渠道就留 NULL，读端归 ``(unknown)`` 桶（沿用 M0 by_task 的 ``(untagged)`` 思路：
无归因不与真实取值混桶）。历史行不回填。

全链路 fail-open：归因是观测能力，任何异常只记 WARNING，绝不让 LLM 调用失败。
"""
from contextvars import ContextVar, Token

from app.utils.logger import get_logger

_logger = get_logger("llm_channel")

CHANNEL_APP = "app"
CHANNEL_WECHAT_ILINK = "wechat_ilink"
CHANNEL_SERVER = "server"
# 读端哨兵：迁移上线前的历史行（NULL）与「入口漏设」的行都归这里
CHANNEL_UNKNOWN = "(unknown)"

# 列宽 String(30)：与 llm_usage.task 同档，写入侧统一截断
CHANNEL_MAX_LEN = 30

_channel_var: ContextVar[str | None] = ContextVar("llm_usage_channel", default=None)


def set_channel(channel: str | None) -> Token | None:
    """标记当前执行上下文的渠道，返回 token 供 `reset_channel` 复原。

    空值归一为 None（不写入空串，避免读端冒出一个空桶）。失败返回 None 并告警。
    """
    try:
        return _channel_var.set((channel or "")[:CHANNEL_MAX_LEN] or None)
    except Exception as e:  # 理论上不会发生（contextvar 在未运行上下文中才会抛）
        _logger.warning("llm channel set failed channel=%s: %s", channel, e)
        return None


def get_channel() -> str | None:
    """当前上下文的渠道；异常一律回退 None（宁可少归因，不影响调用）。"""
    try:
        return _channel_var.get()
    except Exception as e:
        _logger.warning("llm channel read failed: %s", e)
        return None


def reset_channel(token: Token | None) -> None:
    """复原到 `set_channel` 之前（token 为空则什么都不做）。"""
    if token is None:
        return
    try:
        _channel_var.reset(token)
    except Exception as e:  # 跨上下文 reset 会抛 ValueError，只告警
        _logger.warning("llm channel reset failed: %s", e)
