"""聊天 API（HTTP + WebSocket）"""
from datetime import timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Header
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

import asyncio
import os
import random
from app.db.database import get_db, async_session_factory
from app.models.chat_session import ChatSession
from app.models.chat_message import ChatMessage
from app.schemas.chat import SendMessageRequest, ChatMessageResponse, ChatHistoryResponse
from app.services.chat_service import (
    create_session, send_and_receive, send_and_receive_chunked, continue_chat,
    get_owned_session, get_unread_counts as service_unread_counts,
    mark_session_read as service_mark_read,
)
from app.auth.deps import get_current_user_id
from app.ws.connection_manager import connected_clients

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])

# ── 图片上传（统一走 services/upload_service，见 app/services/upload_service.py）──
from app.services.upload_service import save_image, save_file, save_voice, UPLOAD_DIR  # noqa: F401


async def save_upload_image(session_id: int, file: UploadFile, lang: str = "zh") -> str:
    """保存上传图片到 uploads/{session_id}/，返回 /uploads/ 相对路径"""
    return await save_image(file, str(session_id), lang)


@router.websocket("/ws/{session_id}")
async def websocket_chat(websocket: WebSocket, session_id: int):
    """WebSocket 实时聊天（?token= 鉴权 + 会话归属校验）"""
    from jose import jwt, JWTError
    from app.auth.config import auth_settings as _as
    from app.db.database import async_session_factory as _dbf

    token = websocket.query_params.get("token", "")
    try:
        payload = jwt.decode(token, _as.secret_key, algorithms=[_as.algorithm])
        ws_user_id = payload.get("user_id")
    except JWTError:
        ws_user_id = None
    if ws_user_id is None:
        await websocket.close(code=4401)
        return
    async with _dbf() as db:
        session = await get_owned_session(db, session_id, ws_user_id)
    if session is None:
        await websocket.close(code=4403)
        return

    await websocket.accept()
    connected_clients[session_id] = websocket
    # P0-5 修复（2026-08-16）：客户端传的 character_id 必须与会话角色一致，防跨用户越权
    _sid_char = session.character_id

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "chat_message")
            lang = str(data.get("lang", "zh"))

            if msg_type == "chat_message":
                character_id = data["character_id"]
                if character_id != _sid_char:
                    await websocket.send_json({"type": "error", "message": "character mismatch"})
                    continue
                content = str(data.get("content", ""))
                if len(content) > 4000:
                    content = content[:4000]
                # 完整引用消息 v2.0.0：WS 通道接收可选 quote（{message_id, sender, content}）
                quote = data.get("quote")
                if not isinstance(quote, dict):
                    quote = None

                # 发送"正在输入"指示
                await websocket.send_json({
                    "type": "typing",
                    "is_typing": True,
                })

                # Agent 处理 + 拆分回复
                result = await send_and_receive_chunked(
                    session_id=session_id,
                    user_id=ws_user_id,
                    character_id=character_id,
                    content=content,
                    lang=lang,
                    quote=quote,
                )

                # 停止"正在输入"
                await websocket.send_json({
                    "type": "typing",
                    "is_typing": False,
                })

                # 推送用户消息正式落库（前端替换本地临时 id，保证删除可用；2026-08-15）
                _um = result.get("user_message")
                if _um:
                    await websocket.send_json({"type": "user_message", "data": _um})

                # 冷战拦截（v3）：角色生气冷战期不回复，向前端发提示事件
                if result.get("cold_war"):
                    await websocket.send_json({
                        "type": "cold_war",
                        "message": "TA 还在生闷气，暂时没理你……说点软话哄哄 TA 吧",
                    })
                    continue

                # 逐块发送 AI 回复（模拟人类打字间隔）
                chunks = result["chunks"]
                for i, chunk_data in enumerate(chunks):
                    is_last = (i == len(chunks) - 1)
                    await websocket.send_json({
                        "type": "ai_response",
                        "data": chunk_data,
                        "memories_updated": result["memories_updated"] and is_last,
                        "is_chunk": not is_last,
                    })
                    if not is_last and len(chunks) > 1:
                        await asyncio.sleep(random.uniform(0.5, 1.5))

            elif msg_type == "continue_chat":
                character_id = data["character_id"]
                if character_id != _sid_char:
                    await websocket.send_json({"type": "error", "message": "character mismatch"})
                    continue
                last_message_id = data.get("last_message_id", 0)
                await websocket.send_json({"type": "typing", "is_typing": True})
                cont_result = await continue_chat(
                    session_id=session_id, user_id=ws_user_id,
                    character_id=character_id, last_message_id=last_message_id,
                    lang=lang,
                )
                await websocket.send_json({"type": "typing", "is_typing": False})
                if cont_result.get("cold_war"):
                    await websocket.send_json({
                        "type": "cold_war",
                        "message": "TA 还在生闷气，暂时没理你……说点软话哄哄 TA 吧",
                    })
                    continue
                await websocket.send_json({
                    "type": "ai_response", "data": cont_result,
                    "memories_updated": False, "is_continue": True,
                })

            elif msg_type == "batch_messages":
                character_id = data["character_id"]
                if character_id != _sid_char:
                    await websocket.send_json({"type": "error", "message": "character mismatch"})
                    continue
                messages_list = (data.get("messages") or [])[:50]
                messages_list = [str(m)[:4000] for m in messages_list]

                await websocket.send_json({"type": "typing", "is_typing": True})

                from app.models.chat_message import ChatMessage as ChatMsg
                async with async_session_factory() as db:
                    batch_infos = []
                    for msg_text in messages_list:
                        um = ChatMsg(session_id=session_id, sender_type="user", content=msg_text)
                        db.add(um)
                        await db.flush()
                        batch_infos.append({
                            "id": um.id, "session_id": session_id, "sender_type": "user",
                            "content": um.content, "created_at": um.created_at.isoformat(),
                        })
                    await db.commit()
                # 批量用户消息正式 id 回传（前端替换本地临时 id，保证删除可用；2026-08-15）
                for _bi in batch_infos:
                    await websocket.send_json({"type": "user_message", "data": _bi})

                combined = " ".join(messages_list)
                result = await send_and_receive_chunked(
                    session_id=session_id, user_id=ws_user_id,
                    character_id=character_id, content=combined,
                    save_user_message=False, lang=lang,
                )

                await websocket.send_json({"type": "typing", "is_typing": False})
                if result.get("cold_war"):
                    await websocket.send_json({
                        "type": "cold_war",
                        "message": "TA 还在生闷气，暂时没理你……说点软话哄哄 TA 吧",
                    })
                    continue
                chunks = result["chunks"]
                for i, chunk_data in enumerate(chunks):
                    is_last = (i == len(chunks) - 1)
                    await websocket.send_json({
                        "type": "ai_response", "data": chunk_data,
                        "memories_updated": result["memories_updated"] and is_last,
                        "is_chunk": not is_last,
                    })
                    if not is_last and len(chunks) > 1:
                        await asyncio.sleep(random.uniform(0.5, 1.5))
    except WebSocketDisconnect:
        connected_clients.pop(session_id, None)
    except Exception as e:
        connected_clients.pop(session_id, None)
        try:
            await websocket.send_json({"type": "error", "detail": str(e)})
        except Exception:
            pass


# ── HTTP 端点 ──

@router.post("/sessions", status_code=201)
async def create_chat_session(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """创建新聊天会话（归属当前登录用户）"""
    from app.models.character import AICharacter as _AC
    cresult = await db.execute(select(_AC).where(_AC.id == character_id, _AC.user_id == user_id))
    if cresult.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
    result = await create_session(user_id=user_id, character_id=character_id)
    return result


@router.get("/sessions")
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """获取当前用户的聊天会话列表"""
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.user_id == user_id,
            ChatSession.is_active == True,
        )
    )
    sessions = result.scalars().all()
    return {"sessions": sessions, "total": len(sessions)}


import traceback
from app.i18n import tr_lang
from app.utils.logger import get_logger

_logger = get_logger("api.chat")


@router.post("/send")
async def send_message(
    data: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """发送消息（HTTP 模式，返回 AI 回复）"""
    # 获取 session 信息（归属校验）
    session = await get_owned_session(db, data.session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "session_not_found"))

    try:
        result_data = await send_and_receive(
            session_id=data.session_id,
            user_id=session.user_id,
            character_id=session.character_id,
            content=data.content,
            lang=lang,
            quote=data.quote,
        )
        return result_data
    except Exception as e:
        _logger.error("send_message failed: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail="internal error")


@router.get("/sessions/{session_id}/messages", response_model=ChatHistoryResponse)
async def get_chat_history(
    session_id: int,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """获取聊天历史"""
    if await get_owned_session(db, session_id, user_id) is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "session_not_found"))
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .offset(skip)
        .limit(limit)
    )
    messages = list(reversed(result.scalars().all()))

    total_result = await db.execute(
        select(func.count()).where(ChatMessage.session_id == session_id)
    )
    total = total_result.scalar()

    return ChatHistoryResponse(
        session_id=session_id,
        messages=[ChatMessageResponse.model_validate(m) for m in messages],
        total=total,
    )


@router.post("/sessions/{session_id}/image")
async def upload_chat_image(
    session_id: int,
    file: UploadFile = File(...),
    caption: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """上传图片消息：保存图片 → 本地图片理解（OCR）→ 生成 AI 回复。
    硬约束：图片文件/二进制绝不传入 deepseek；理解结果以文本形式进上下文。
    content 存用户配文（用户可见），extra_meta 存图片描述（仅 AI 上下文使用）。"""
    # 校验会话归属
    session = await get_owned_session(db, session_id, user_id)
    if session is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "session_not_found"))
    character_id = session.character_id

    image_url = await save_upload_image(session_id, file, lang)

    # 本地图片理解（OCR）→ 文字描述
    from app.services.image_understanding_service import describe_image
    abs_path = str(UPLOAD_DIR / image_url.removeprefix("/uploads/"))
    desc = ""
    try:
        desc = await describe_image(abs_path, user_id=user_id)
    except Exception as e:
        _logger.warning("Image describe failed: %s", e)

    import json as _json
    caption = (caption or "").strip()[:500]
    image_desc = (desc or "").strip()[:500]
    # AI 上下文文本（图片内容 + 用户配文，仅注入 LLM，不写入用户可见消息）
    llm_parts = []
    if image_desc:
        llm_parts.append(f"用户发来一张图片，图片内容：{image_desc}")
    if caption:
        llm_parts.append(f"用户补充说明：{caption}")
    llm_content = "\n".join(llm_parts) or "[图片]"

    # 保存图片消息：content=配文（用户可见），extra_meta=图片描述（AI 用，兼容未来 VLM）
    async with async_session_factory() as db:
        img_msg = ChatMessage(
            session_id=session_id, sender_type="user",
            content=caption, image_url=image_url,
            extra_meta=_json.dumps({
                "image_desc": {"kind": "auto", "text": image_desc},
            }, ensure_ascii=False),
        )
        db.add(img_msg)
        await db.flush()
        await db.commit()
        await db.refresh(img_msg)

    # 触发 agent 生成回复（user_message = 图片描述 + 配文文本，图片本体不进 LLM）
    result = await send_and_receive_chunked(
        session_id=session_id,
        user_id=user_id,
        character_id=character_id,
        content=llm_content,
        save_user_message=False, lang=lang,
        extra_capabilities=["识图"],
    )

    return {
        "image_message": {
            "id": img_msg.id, "session_id": session_id,
            "sender_type": "user", "content": img_msg.content,
            "image_url": img_msg.image_url,
            "created_at": img_msg.created_at.isoformat(),
        },
        "chunks": result["chunks"],
    }


@router.get("/sessions/{session_id}/archive")
async def get_chat_archive(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """获取会话消息归档（按日期分组）"""
    if await get_owned_session(db, session_id, user_id) is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "session_not_found"))
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    )
    messages = result.scalars().all()

    from collections import defaultdict
    days = defaultdict(list)
    for msg in messages:
        beijing_tz = timezone(timedelta(hours=8))
        beijing_time = msg.created_at.replace(tzinfo=timezone.utc).astimezone(beijing_tz)
        day_key = beijing_time.strftime("%Y-%m-%d")
        days[day_key].append({
            "id": msg.id,
            "sender_type": msg.sender_type,
            "content": msg.content,
            "image_url": msg.image_url,
            "created_at": msg.created_at.isoformat(),
        })

    result_list = []
    for date_str in sorted(days.keys(), reverse=True):
        result_list.append({
            "date": date_str,
            "messages": days[date_str],
            "count": len(days[date_str]),
        })

    return {"session_id": session_id, "days": result_list, "total_days": len(result_list)}


@router.get("/messages/{message_id}")
async def get_message(
    message_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """获取单条消息"""
    result = await db.execute(select(ChatMessage).where(ChatMessage.id == message_id))
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "message_not_found"))
    if await get_owned_session(db, msg.session_id, user_id) is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "message_not_found"))
    return {
        "id": msg.id, "session_id": msg.session_id,
        "sender_type": msg.sender_type, "content": msg.content,
        "image_url": msg.image_url,
        "created_at": msg.created_at.isoformat(),
    }


@router.delete("/messages/{message_id}")
async def delete_message(
    message_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """删除指定聊天消息（同步删除关联记忆）"""
    from app.models.chat_message import ChatMessage
    from app.models.memory import Memory
    from sqlalchemy import select

    result = await db.execute(select(ChatMessage).where(ChatMessage.id == message_id))
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "message_not_found"))
    if await get_owned_session(db, msg.session_id, user_id) is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "message_not_found"))

    # 删除关联记忆（来自该消息触发的记忆）
    try:
        mem_result = await db.execute(
            select(Memory).where(Memory.source_id == message_id)
        )
        for mem in mem_result.scalars().all():
            try:
                from app.db.vector_store import delete_memory_vector
                await delete_memory_vector(mem.id)
            except Exception:
                pass
            await db.delete(mem)
        _logger.info("Deleted %d memories linked to message %d", mem_result.scalars().all().__len__(), message_id)
    except Exception as e:
        _logger.warning("Failed to delete memories for msg %d: %s", message_id, e)

    await db.delete(msg)
    await db.commit()
    return {"status": "ok", "message": "消息及关联记忆已删除"}


@router.get("/unread")
async def get_unread_counts(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """获取当前用户每个角色的未读消息数（聚合逻辑在 services/chat_service）"""
    return {"unread": await service_unread_counts(db, user_id)}


@router.post("/sessions/{session_id}/read")
async def mark_session_read(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """标记会话已读（联动同角色其他活跃会话，逻辑在 services/chat_service）"""
    if not await service_mark_read(db, session_id, user_id):
        raise HTTPException(status_code=404, detail=tr_lang(lang, "session_not_found"))
    return {"status": "ok"}
# ── 文件上传（私聊文件：白名单 + 大小上限 + AI 摘要注入）──
@router.post("/sessions/{session_id}/file")
async def upload_chat_file(
    session_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """上传文件消息：保存文件 → 文本类提取摘要（非文本仅元数据）→ 生成 AI 回复。
    文件二进制不进入 LLM；摘要/元数据以文本进上下文。content 存文件名（用户可见）。"""
    session = await get_owned_session(db, session_id, user_id)
    if session is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "session_not_found"))
    character_id = session.character_id
    fname = (file.filename or "文件").strip()[:120]
    file_url = await save_file(file, str(session_id), lang)

    # 文本类文件取前 N 字作摘要；二进制仅元数据
    import json as _json
    ext = os.path.splitext(fname)[1].lower()
    summary = ""
    try:
        if ext in (".txt", ".md", ".json", ".csv", ".log"):
            abs_path = str(UPLOAD_DIR / file_url.removeprefix("/uploads/"))
            raw = open(abs_path, "rb").read(8192)
            try:
                # 文档问答：保存全文（截断 6000 字符），注入上下文供上传后持续追问
                summary = raw.decode("utf-8", errors="ignore").strip()[:6000]
            except Exception:
                summary = ""
        fsize = os.path.getsize(str(UPLOAD_DIR / file_url.removeprefix("/uploads/")))
    except Exception:
        fsize = 0
    size_str = f"{fsize / 1024 / 1024:.1f}MB" if fsize >= 1024 * 1024 else f"{fsize / 1024:.0f}KB"

    llm_parts = [f"用户发来一个文件《{fname}》"]
    if summary:
        llm_parts.append(f"文件内容摘要：{summary}")
    else:
        llm_parts.append(f"（{ext.lstrip('.')} 文件，{size_str}）")
    llm_content = "\n".join(llm_parts)

    async with async_session_factory() as db:
        file_msg = ChatMessage(
            session_id=session_id, sender_type="user",
            content=fname, image_url=None,
            extra_meta=_json.dumps({
                "file": {
                    "name": fname, "url": file_url, "size": size_str,
                    "type": ext.lstrip(".") or "file", "summary": summary,
                },
            }, ensure_ascii=False),
        )
        db.add(file_msg)
        await db.commit()
        await db.refresh(file_msg)

    result = await send_and_receive_chunked(
        session_id=session_id, user_id=user_id, character_id=character_id,
        content=llm_content, save_user_message=False, lang=lang,
        extra_capabilities=["文档问答"],
    )
    return {
        "file_message": {
            "id": file_msg.id, "session_id": session_id,
            "sender_type": "user", "content": file_msg.content,
            "file_url": file_url, "created_at": file_msg.created_at.isoformat(),
        },
        "chunks": result["chunks"],
    }


# ── 语音消息上传（本地 ASR 转写，转写文本进 AI 上下文与消息内容）──
@router.post("/sessions/{session_id}/voice")
async def upload_chat_voice(
    session_id: int,
    file: UploadFile = File(...),
    duration: float = Form(0),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """上传语音消息：保存音频 → faster-whisper 本地转写 → 生成 AI 回复。
    音频不进 LLM；转写文本以消息内容与上下文注入。转写失败降级为"[语音消息]"。"""
    session = await get_owned_session(db, session_id, user_id)
    if session is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "session_not_found"))
    character_id = session.character_id
    voice_url = await save_voice(file, str(session_id), lang)

    # 本地 ASR 转写
    from app.services.speech_service import transcribe
    abs_path = str(UPLOAD_DIR / voice_url.removeprefix("/uploads/"))
    transcript = ""
    try:
        transcript = (await transcribe(abs_path, user_id=user_id) or "").strip()[:500]
    except Exception as e:
        _logger = __import__("app.utils.logger", fromlist=["get_logger"]).get_logger("api.chat")
        _logger.warning("Voice transcribe failed: %s", e)
        transcript = ""

    import json as _json
    dur = max(0, int(float(duration or 0)))
    content = transcript or "[语音消息]"
    llm_content = f"用户发来一条语音消息，用户说：{transcript}" if transcript else "用户发来一条语音消息（内容暂时无法转写）"

    async with async_session_factory() as db:
        voice_msg = ChatMessage(
            session_id=session_id, sender_type="user",
            content=content, image_url=None,
            extra_meta=_json.dumps({
                "voice": {"url": voice_url, "duration": dur, "transcript": transcript},
            }, ensure_ascii=False),
        )
        db.add(voice_msg)
        await db.commit()
        await db.refresh(voice_msg)

    result = await send_and_receive_chunked(
        session_id=session_id, user_id=user_id, character_id=character_id,
        content=llm_content, save_user_message=False, lang=lang, tts=True,
    )
    return {
        "voice_message": {
            "id": voice_msg.id, "session_id": session_id,
            "sender_type": "user", "content": voice_msg.content,
            "voice_url": voice_url, "duration": dur, "transcript": transcript,
            "extra_meta": _json.dumps({
                "voice": {"url": voice_url, "duration": dur, "transcript": transcript},
            }, ensure_ascii=False),
            "created_at": voice_msg.created_at.isoformat(),
        },
        "chunks": result["chunks"],
    }


# ── 表情消息（自定义表情：引用已上传图片，AI 经表情名描述理解，不做 OCR）──
@router.post("/sessions/{session_id}/emoji")
async def send_emoji_message(
    session_id: int,
    body: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """发送自定义表情消息：image 消息 + extra_meta.image_desc（表情名），AI 直接理解，零 OCR 成本"""
    emoji_url = str(body.get("emoji_url") or "").strip()
    name = str(body.get("name") or "表情").strip()[:30]
    # 越权防护：仅允许引用本用户自己的自定义表情
    allowed_prefix = f"/uploads/emojis/user/{user_id}/"
    if not emoji_url.startswith(allowed_prefix):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "emoji_source_invalid"))
    session = await get_owned_session(db, session_id, user_id)
    if session is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "session_not_found"))
    character_id = session.character_id

    import json as _json
    desc = f"用户发送了一个表情：{name}"
    async with async_session_factory() as db:
        emoji_msg = ChatMessage(
            session_id=session_id, sender_type="user",
            content=name, image_url=emoji_url,
            extra_meta=_json.dumps({"image_desc": {"text": desc}}, ensure_ascii=False),
        )
        db.add(emoji_msg)
        await db.commit()
        await db.refresh(emoji_msg)

    result = await send_and_receive_chunked(
        session_id=session_id, user_id=user_id, character_id=character_id,
        content=desc, save_user_message=False, lang=lang,
    )
    return {
        "emoji_message": {
            "id": emoji_msg.id, "session_id": session_id,
            "sender_type": "user", "content": emoji_msg.content,
            "image_url": emoji_msg.image_url, "created_at": emoji_msg.created_at.isoformat(),
        },
        "chunks": result["chunks"],
    }
