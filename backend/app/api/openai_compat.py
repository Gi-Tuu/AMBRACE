# -*- coding: utf-8 -*-
"""批 8 块 A / M1（2026-10-01）：角色级 OpenAI 兼容端点——一层「形状壳」，内核不换。

设计稿：``AMBRACE_批8_接口面与形态护栏_详细设计_v1_20260929.md`` §1.1 / §2.1 / §7 M1「A」行。

两条路由（前缀 ``/v1``，全仓唯一非 ``/api/v1`` 先例是 ``/channel-login``，故有先例可循）：
  - ``GET  /v1/models``           → 列**本账号**角色（A 严格 owner），OpenAI ``list`` 形状；
  - ``POST /v1/chat/completions`` → 标准 ``chat.completion`` 形状，内核复用既有旁路
    ``application/character_chat_api.chat_with_character``（不落库 / 不建会话 / 不写记忆 / 不触发 hook）。

硬约束（派单 + 设计 §2.1，越界即返工）：
  - **flag 关 ⇒ 两个路由一律 404**：``openai_compat_endpoint`` 关时由**路由级依赖**首关拦截
    （排在鉴权之前 ⇒ 未登录探测也得到 404 而非 401，对外表现为「路由不存在」），不查库、不进内核；
  - **归属口径＝A 严格 owner**（设计 §8 待拍板 1 已拍板 A）：与 ``/api/v1/ai/*`` 同口径——
    角色不存在 ⇒ 404、存在但不属于调用账号 ⇒ 403；``GET /v1/models`` 只列 ``user_id == 调用账号``
    的角色（``list_characters`` 既有谓词），与 ``POST`` 的 ``chat_with_character`` 归属校验**同口径**；
  - **凭据本阶段仍用 JWT**（``Authorization: Bearer``，走既有 ``get_current_user_id``）；API key 属 M2，
    本单不做、不建表；
  - **显式拒绝**（400，复用 M0 ``domain/compat_shape.validate_compat_request`` 文案）：``stream=true`` /
    ``tools`` / ``response_format`` / ``n>1`` / ``messages[role=system]``——静默忽略会让接错的人以为成功；
  - **不新增第二条对话链路**：唯一内核＝``chat_with_character``；本模块只做形状出入参 + 归因；
  - **渠道归因**：入口 ``set_channel(openai_compat)``（``utils/llm_channel.py`` 词表），``task`` 沿用内核
    既有的 ``plugin_ai``；渠道在 ``chat_with_character``（内部 spawn 记账）**之前**设、之后复原。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.application import character_chat_api
from app.auth.deps import get_current_user_id
from app.domain.compat_shape import (
    MODEL_PREFIX,
    clamp_max_tokens,
    clamp_temperature,
    validate_compat_request,
)
from app.flags.agent_flags import AGENT_FLAGS
from app.i18n import lang_of
from app.utils.llm_channel import CHANNEL_OPENAI_COMPAT, reset_channel, set_channel

router = APIRouter(prefix="/v1", tags=["OpenAI Compat"])

FLAG_KEY = "openai_compat_endpoint"


def _compat_enabled() -> bool:
    """兼容端点总闸（缺省关；读 flag 失败也按关——门面层不得因读开关异常而误放行）。"""
    try:
        return bool(AGENT_FLAGS.get(FLAG_KEY, False))
    except Exception:
        return False


def _require_compat_enabled() -> None:
    """路由级依赖：flag 关 ⇒ 404（排在 ``get_current_user_id`` 之前 ⇒ 未登录探测也得 404）。

    对外表现为「路由不存在」（通用 Not Found，不泄漏端点是否存在），与设计 §7 M1「flag 关即 404」一致。
    """
    if not _compat_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


def _parse_ai_id(model: str) -> int:
    """从 ``ambrace:<ai_id>`` 取角色 id（``validate_compat_request`` 已保证前缀与数字合法）。"""
    return int(model[len(MODEL_PREFIX):].strip())


def _split_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """OpenAI ``messages`` → 内核 ``(input_text, history)``。

    OpenAI 约定末条为当前 user 输入 ⇒ 末条 content 作 ``input_text``，其余作 ``history``
    （``chat_with_character.build_api_messages`` 会把 history 铺在前、input 作为最后一条 user）。
    只取 role/content 两键（system 已被 ``validate_compat_request`` 拒掉）。
    """
    last = messages[-1]
    input_text = str(last.get("content") or "")
    history = [
        {"role": m.get("role"), "content": m.get("content")}
        for m in messages[:-1]
    ]
    return input_text, history


def _estimate_tokens(text: str) -> int:
    """token 估算：2 字符 ≈ 1 token（与 context_builder / compat_shape 既有估算口径一致）。"""
    return max(0, len(text or "") // 2)


@router.get("/models", dependencies=[Depends(_require_compat_enabled)])
async def list_models(
    request: Request,
    user_id: int = Depends(get_current_user_id),
):
    """``GET /v1/models``：列**本账号**角色（A 严格 owner），OpenAI ``list`` 形状。

    只出 ``id`` + ``owned_by``（角色名），**不回显**人设 / bio / self_statement 等字段
    （设计 §2.1：``GET /v1/models`` 只出 id + 名称）。归属口径与 ``POST /v1/chat/completions``
    一致——都只认 ``user_id == 调用账号``（``list_characters`` 既有谓词），不走家庭租户范围。
    """
    result = await character_chat_api.list_characters(user_id)
    created = int(time.time())
    data = [
        {
            "id": f"{MODEL_PREFIX}{item['id']}",
            "object": "model",
            "created": created,
            "owned_by": item.get("name") or "",
        }
        for item in result.get("items", [])
    ]
    return {"object": "list", "data": data}


@router.post("/chat/completions", dependencies=[Depends(_require_compat_enabled)])
async def chat_completions(
    request: Request,
    body: dict = Body(...),
    user_id: int = Depends(get_current_user_id),
):
    """``POST /v1/chat/completions``：标准 chat.completion 形状，内核复用 ``chat_with_character``。

    流程：形状校验（M0 文案，400）→ 解析 ``ambrace:<ai_id>`` → 渠道归因 → 调内核旁路
    （归属 404/403、限额 429、BYOK 400 全部由内核既有口径抛出，本壳不另设）→ 映射回 OpenAI 形状。
    ``truncated`` ⇒ ``finish_reason="length"``（标准壳与私有壳唯一必须做的语义翻译，设计 §2.1(1)）。
    """
    # 1. 形状校验（显式拒绝 stream/tools/response_format/n>1/system；复用 M0 纯函数文案）
    err = validate_compat_request(body)
    if err:
        raise HTTPException(status_code=400, detail=err)

    ai_id = _parse_ai_id(str(body.get("model") or ""))
    messages = body.get("messages") or []
    input_text, history = _split_messages(messages)
    max_tokens = clamp_max_tokens(body.get("max_tokens"))
    temperature = clamp_temperature(body.get("temperature"))
    lang = lang_of(request)

    # 2. 渠道归因：在内核（内部 spawn 记账）之前设，调用结束复原（设计 §2.1(1) 归因 + spawn 前读约束）
    token = set_channel(CHANNEL_OPENAI_COMPAT)
    try:
        result = await character_chat_api.chat_with_character(
            ai_id=ai_id,
            user_id=user_id,
            input_text=input_text,
            history=history or None,
            max_tokens=max_tokens,
            temperature=temperature,
            lang=lang,
        )
    finally:
        reset_channel(token)

    # 3. 内核返回 → OpenAI chat.completion 形状
    reply = str(result.get("reply") or "")
    truncated = bool(result.get("truncated"))
    created = int(time.time())
    prompt_tokens = sum(_estimate_tokens(str(m.get("content") or "")) for m in messages)
    completion_tokens = _estimate_tokens(reply)
    return {
        "id": f"chatcmpl-ambrace-{created}-{ai_id}",
        "object": "chat.completion",
        "created": created,
        "model": f"{MODEL_PREFIX}{ai_id}",
        "choices": [
            {
                "index": 0,
                "finish_reason": "length" if truncated else "stop",
                "message": {"role": "assistant", "content": reply},
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
