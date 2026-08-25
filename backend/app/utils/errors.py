"""LLM 错误友好化：把底层 OpenAI/供应商原始异常转为用户可读的中文提示。

供 SSE 流式端点（chat_service.send_and_receive_stream / api.chat.stream_message）
与 /send 端点等调用方共用；与 /send 现有处理保持一致：
  - "api key" / "authentication" / "invalid" / "未配置" → "LLM API Key 无效或未配置：{msg}"
另补充 timeout 与 rate limit(429) 的友好文案。
"""


def friendly_llm_error(e: Exception) -> str:
    """把 LLM 调用异常转为用户友好的中文描述（无匹配时返回原始 str）。"""
    msg = str(e)
    lower = msg.lower()
    if "api key" in lower or "未配置" in msg or "authentication" in lower or "invalid" in lower:
        return f"LLM API Key 无效或未配置：{msg}"
    if "timeout" in lower or "timed out" in lower:
        return "LLM 请求超时，请稍后重试或检查网络"
    if "rate limit" in lower or "429" in msg:
        return "LLM 请求频率超限，请稍后重试"
    return msg
