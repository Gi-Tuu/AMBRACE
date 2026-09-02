"""LLM 错误友好化：把底层 OpenAI/供应商原始异常转为用户可读的中文提示。

供 SSE 流式端点（chat_service.send_and_receive_stream / api.chat.stream_message）
与 /send 端点等调用方共用；与 /send 现有处理保持一致：
  - "api key" / "authentication" / "invalid" / "未配置" → "LLM API Key 无效或未配置：{msg}"
另补充 timeout 与 rate limit(429) 的友好文案。
"""

import traceback as _traceback
import uuid as _uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.utils.logger import get_logger

_log = get_logger("app.error")


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


# ── 全局异常处理：统一错误响应体 + 不向客户端泄漏堆栈（3.3）────────────────────────────────
# 约定：业务 HTTPException 维持原状态码与 detail 语义（401/403/404/422 等不变）；
# 未捕获异常仅记日志（含完整堆栈 + trace id），对外只返回 trace id，避免暴露内部实现。


def _error_body(code: str, message: str, detail: Any = None) -> dict:
    """统一错误体：{ok, error{code,message,detail}}，并把 detail 放到顶层兼容位。

    顶层 detail 保留给仍按「读取 detail 字段」实现的旧前端/测试；error.detail 为新结构位。
    """
    return {
        "ok": False,
        "error": {"code": code, "message": message, "detail": detail},
        "detail": detail,
    }


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器；在 main.py 创建 app 后调用一次。"""

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request: Request, exc: StarletteHTTPException):
        # 401/403/404/422 等沿用状态码，但统一外层结构；detail 同时放 error.detail 与顶层兼容位
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(f"HTTP_{exc.status_code}", str(exc.detail), exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request: Request, exc: RequestValidationError):
        # 422：参数校验失败；errors() 列表放 error.detail 与顶层 detail
        return JSONResponse(
            status_code=422,
            content=_error_body("VALIDATION_ERROR", "参数校验失败", exc.errors()),
        )

    @app.exception_handler(Exception)
    async def _unhandled_exc(request: Request, exc: Exception):
        # 关键：完整堆栈只进日志，对外只给 trace id，便于排查且不泄漏内部细节
        tid = _uuid.uuid4().hex[:12]
        _log.error(
            "unhandled [%s] %s %s\n%s",
            tid, request.method, request.url.path, _traceback.format_exc(),
        )
        return JSONResponse(
            status_code=500,
            content=_error_body("INTERNAL_ERROR", "服务器内部错误", {"trace": tid}),
        )
