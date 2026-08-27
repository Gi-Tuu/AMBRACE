"""SSE/TTS/chunk 流式层：真流式生成 + 语义块落库 + 逐句/批量 TTS。

AMBRACE 重构步骤 3：从 chat_service 拆出流式/TTS/chunk 相关函数到本模块。设计约定：
- 对 service 层函数（_run_agent_core/_run_post_processing/send_and_receive_chunked/
  _persist_user_message）统一用函数内（懒）import（from app.services.chat_service import ...），
  避免 streaming↔service 包内循环；
- 消息 IO（_push_user_notify）取自无副作用依赖的 chat/io.py；
- 纯搬移，不改业务逻辑/签名/默认参数/错误文案，SSE/TTS 事件序列（delta/block/done/
  reset_blocks）与拆分前完全一致。
"""
from sqlalchemy import delete, select

from app.db.database import async_session_factory
from app.models.chat_message import ChatMessage
from app.services.chat.io import _push_user_notify
from app.utils.errors import friendly_llm_error
from app.utils.logger import get_logger

_logger = get_logger("services.chat")


def _assemble_chunk_meta(
    idx: int, total: int, final_state: dict,
    gen_prompt: str | None, extra_capabilities: list[str] | None,
    cal_note_text: str | None, memo_text: str | None,
    tts_url: str | None = None, tts: bool = False,
) -> dict | None:
    """按 _persist_ai_chunks 的规则组装单个语义块的 extra_meta。

    - 首块（idx==0）：reasoning（若有）+ tools（final_state.tools_used + 生图 + 语音回复 + extra_capabilities）；
    - tts_url 非空：附加 tts.url（语音逐句合成时每块自带音频）；
    - 末块（idx==total-1）：status_update + cal_note + memo（若有）。
    """
    _meta: dict = {}
    if idx == 0:
        _reasoning = (final_state.get("reasoning") or "").strip()
        if _reasoning:
            _meta["reasoning"] = _reasoning
        _tools = list(final_state.get("tools_used") or [])
        if gen_prompt:
            _tools.append("生图")
        if tts:
            _tools.append("语音回复")
        for _cap in (extra_capabilities or []):
            if _cap not in _tools:
                _tools.append(_cap)
        if _tools:
            _meta["tools"] = _tools
    if tts_url:
        _meta["tts"] = {"url": tts_url}
    if idx == total - 1:
        _st = (final_state.get("status_update") or "").strip()
        if _st:
            _meta["status_update"] = _st
        if cal_note_text:
            _meta["cal_note"] = cal_note_text
        if memo_text:
            _meta["memo"] = memo_text
    return _meta or None


async def _update_chunk_meta(message_id: int, meta: dict | None) -> None:
    """更新已落库块的 extra_meta（用于流式逐句路径生成后回填首/末块）。"""
    import json as _json
    async with async_session_factory() as db:
        m = (await db.execute(
            select(ChatMessage).where(ChatMessage.id == message_id)
        )).scalar_one_or_none()
        if m is not None:
            m.extra_meta = _json.dumps(meta, ensure_ascii=False) if meta else None
            await db.commit()


async def _delete_chunks(message_ids: list[int]) -> None:
    """删除已落库的块（流式异常回退非流式前清理半截块，避免重复落库）。"""
    if not message_ids:
        return
    async with async_session_factory() as db:
        await db.execute(delete(ChatMessage).where(ChatMessage.id.in_(message_ids)))
        await db.commit()


async def _synthesize_chunks_tts(
    chunk_texts: list[str], session_id: int, character_id: int, user_id: int,
) -> list[str | None]:
    """逐句合成语音（复用 tts_service.synthesize，百炼优先/edge 兜底），返回与块一一对应的 URL 或 None。

    用于流式异常/深度思考回退非流式时，仍按每块逐句合成（与实时逐句路径行为一致）。
    """
    try:
        from app.voice.voice_mode import load_character_voice_params
        params = await load_character_voice_params(character_id)
    except Exception as e:
        _logger.warning("Load voice params failed: %s", e)
        params = {}
    from app.services.tts_service import synthesize
    urls: list[str | None] = []
    for text in chunk_texts:
        try:
            url = await synthesize(
                text, subdir=str(session_id),
                gender=params.get("gender"), voice=params.get("voice"),
                voice_rate=params.get("voice_rate"), voice_pitch=params.get("voice_pitch"),
                user_id=user_id,
            )
        except Exception as e:
            _logger.warning("Chunk TTS failed: %s", e)
            url = None
        urls.append(url)
    return urls


async def _backfill_stream_tts_meta(
    saved: list[dict], final_state: dict, gen_prompt: str | None,
    extra_capabilities: list[str] | None, cal_note_text: str | None, memo_text: str | None,
) -> list[dict]:
    """流式逐句路径生成后，按 _persist_ai_chunks 规则回填首块（reasoning/tools）与末块（status/cal/memo）meta。

    实时逐句落库时只带每块 tts.url；生成后补齐首/末块的 extra_meta，保证与批量落库一致。
    """
    import json as _json
    total = len(saved)
    if total == 0:
        return saved
    first = saved[0]
    meta0 = _assemble_chunk_meta(
        0, total, final_state, gen_prompt, extra_capabilities,
        cal_note_text, memo_text, first.get("tts_url"), tts=True,
    )
    await _update_chunk_meta(first["id"], meta0)
    first["extra_meta"] = _json.dumps(meta0, ensure_ascii=False) if meta0 else None
    if total > 1:
        last = saved[total - 1]
        meta_n = _assemble_chunk_meta(
            total - 1, total, final_state, gen_prompt, extra_capabilities,
            cal_note_text, memo_text, last.get("tts_url"), tts=True,
        )
        await _update_chunk_meta(last["id"], meta_n)
        last["extra_meta"] = _json.dumps(meta_n, ensure_ascii=False) if meta_n else None
    return saved


async def _persist_ai_chunks(
    session_id: int, final_state: dict,
    chunks: list[str],
    gen_prompt: str | None,
    cal_note_text: str | None, memo_text: str | None,
    extra_capabilities: list[str] | None = None,
    tts_urls: list[str | None] | None = None,
    tts: bool = False,
) -> list[dict]:
    """把语义块按 chunked 的 extra_meta 规则落库（首块：reasoning/tools；末块：status/cal/memo）。

    tts_urls/tts 用于语音逐句合成：每个块额外携带自身 tts.url（首块 tools 追加「语音回复」）。
    返回已落库的块列表（含 id/created_at/extra_meta），供流式端点逐块推送。
    """
    import json as _json
    saved: list[dict] = []
    _total = len(chunks)
    async with async_session_factory() as db:
        for idx, chunk in enumerate(chunks):
            _tts_url = tts_urls[idx] if tts_urls else None
            meta = _assemble_chunk_meta(
                idx, _total, final_state, gen_prompt, extra_capabilities,
                cal_note_text, memo_text, _tts_url, tts,
            )
            m = ChatMessage(
                session_id=session_id, sender_type="ai", content=chunk,
                extra_meta=_json.dumps(meta, ensure_ascii=False) if meta else None,
            )
            db.add(m)
            await db.flush()
            await db.refresh(m)
            item = {
                "id": m.id, "session_id": session_id, "sender_type": "ai",
                "content": m.content, "created_at": m.created_at.isoformat(),
                "extra_meta": m.extra_meta,
            }
            if _tts_url:
                item["tts_url"] = _tts_url
            saved.append(item)
        await db.commit()
    return saved


async def send_and_receive_stream(
    session_id: int, user_id: int, character_id: int, content: str,
    lang: str = "zh", quote: dict | None = None,
    sink=None, extra_capabilities: list[str] | None = None,
    tts: bool = False, save_user_message: bool = True,
) -> None:
    """发送用户消息 → 真 SSE 流式生成 → 经 sink 逐事件推送（delta/block/done/user_message/error/cold_war）。

    sink: async callable(event: str, payload: dict)。事件：
    - user_message：用户消息落库回传（前端替换本地临时 id；save_user_message=False 时不发）；
    - delta：增量展示文本（打字机）；
    - block：完整语义块（已落库，含 id/created_at/extra_meta；tts 时逐句携带 tts_url）；
    - done：生成完成（含 message + blocks + memories_updated）；
    - error：流式失败（随后回退非流式 chunked，仍会推 block/done）；
    - cold_war：冷战拦截。
    - reset_blocks：回退路径在重推新 block 前发出，前端据此清除本轮已确认的 AI 正式块，
      避免旧/新块 id 不同导致的重复气泡。两类触发：TTS consumer 中途死亡（_fallback=True，
      reason="tts_consumer_fallback"）；TTS 模式 LLM 流式异常回退 chunked
      （reason="stream_error_fallback"，V2-3）。

    tts=True：语音逐句合成——_stream_generate 边收边切句块时逐句 TTS（与 LLM 流并行），
    实时落库 + 推 block（带 tts_url），前端按块排版顺序播放；非语音（tts=False）行为与现状一致。
    save_user_message=False：语音链路用户消息已单独落库（/voice），SSE 不重复落用户消息。
    """
    # 懒 import：streaming ↔ service 包内循环规避，且保证调用点 monkeypatch 生效
    from app.services.chat_service import (
        _persist_user_message as _persist_user_message,
        _run_agent_core as _run_agent_core,
        _run_post_processing as _run_post_processing,
        send_and_receive_chunked as send_and_receive_chunked,
    )

    if sink is None:
        return
    user_msg_id, user_msg_info = await _persist_user_message(
        session_id, user_id, character_id, content,
        quote=quote, save_user_message=save_user_message, shared_memory=True,
    )
    if user_msg_info:
        await sink("user_message", user_msg_info)

    # 语音逐句 TTS：预加载角色音色参数 + 实时块落库/推送回调（供 _stream_generate 流水线消费）
    tts_ctx = None
    tts_saved: list[dict] = []
    if tts:
        try:
            from app.voice.voice_mode import load_character_voice_params
            voice_params = await load_character_voice_params(character_id)
        except Exception as e:
            _logger.warning("Load voice params failed: %s", e)
            voice_params = {}

        async def _block_sink(index: int, blk_text: str, tts_url: str | None) -> dict:
            # 生成期只带每块 tts.url（首块含「语音回复」工具标注）；生成后由
            # _backfill_stream_tts_meta 补齐首/末块 reasoning/tools/status/cal/memo。
            import json as _json
            meta = _assemble_chunk_meta(
                index, 0, {}, None, extra_capabilities, None, None, tts_url, tts=True,
            )
            async with async_session_factory() as db:
                m = ChatMessage(
                    session_id=session_id, sender_type="ai", content=blk_text,
                    extra_meta=_json.dumps(meta, ensure_ascii=False) if meta else None,
                )
                db.add(m)
                await db.flush()
                await db.refresh(m)
                item = {
                    "id": m.id, "session_id": session_id, "sender_type": "ai",
                    "content": m.content, "created_at": m.created_at.isoformat(),
                    "extra_meta": m.extra_meta,
                }
                if tts_url:
                    item["tts_url"] = tts_url
                await db.commit()
            tts_saved.append(item)
            return item

        tts_ctx = {
            "voice_params": voice_params,
            "tts_subdir": str(session_id),
            "block_sink": _block_sink,
        }

    try:
        core = await _run_agent_core(
            session_id, user_id, character_id, content, lang, user_msg_id,
            stream_sink=sink, tts=tts, stream_tts_ctx=tts_ctx, reply_delay=True,
        )
    except Exception as e:
        _logger.warning("Stream generation failed, fallback chunked: %s", e)
        await sink("error", {"detail": friendly_llm_error(e)})
        # 清理已实时落库的半截块，避免与回退 chunked 全量落库重复。
        if tts_saved:
            await _delete_chunks([c["id"] for c in tts_saved])
            tts_saved.clear()
            # V2-3（2026-08-29）：TTS 模式下 LLM 流式异常回退到 chunked —— 已实时推送的半截块
            # 被删除，chunked 回退将生成全新内容/全新 ID 的块；先发 reset_blocks 让前端清除本轮
            # 已确认的 AI 正式块，避免旧半截块 + 新块重复显示（与 P2-NEW 的 TTS consumer 回退一致）。
            await sink("reset_blocks", {"reason": "stream_error_fallback"})
        core = None

    if core is None:
        # 冷战拦截 or 流式异常：回退非流式 chunked（不重复落用户消息）
        try:
            chunk_res = await send_and_receive_chunked(
                session_id, user_id, character_id, content,
                save_user_message=False, lang=lang, quote=quote,
                extra_capabilities=extra_capabilities, tts=tts,
                # P2-3：流式异常回退时已在 _run_agent_core(reply_delay=True) 或流式路径 sleep 过，
                # 这里跳过自然延迟，避免已 sleep 一次又 sleep 一次（极端最多 2×8s）。
                reply_delay=False,
            )
        except Exception as e:
            # chunked 也失败：error 事件已在上面的 except 发过，这里只记日志不再向上抛，
            # 避免 SSE 端点（chat.py _run）重复发送 error 事件。
            _logger.warning("Chunked fallback also failed, ending stream: %s", e)
            await sink("done", {
                "message": {"content": ""},
                "blocks": [],
                "memories_updated": False,
            })
            return
        if chunk_res.get("cold_war"):
            await sink("cold_war", {"message": "TA 还在生闷气，暂时没理你……说点软话哄哄 TA 吧"})
            return
        for i, c in enumerate(chunk_res["chunks"]):
            await sink("block", {"index": i, **c})
        await sink("done", {
            "message": {"content": chunk_res["chunks"][-1]["content"] if chunk_res["chunks"] else ""},
            "blocks": chunk_res["chunks"],
            "memories_updated": chunk_res.get("memories_updated", False),
        })
        return

    final_state = core["final_state"]
    full_text = core["final_text"]
    gen_prompt = core["gen_prompt"]
    img_text = core["img_text"]
    _cal_note_text = core["cal_note_text"]
    _memo_text = core["memo_text"]

    # A1（#59）流式路径 MCP 工具循环：SSE 真流式下执行 LLM 输出的 mcp.* 标记，工具结果经
    # 独立流尾事件 tool_result 推给前端（前端观察区可折叠展示）。不做二次 LLM 再决策——
    # 流式再决策会再次走流式导致 delta 二次推送/stream_blocks 被覆盖，且 tts 的 block_sink
    # 流水线在首条回复时已消费完毕（评估见 chat_service._run_agent_core 注释）。工具的
    # observation 已注入 final_state.context_messages，供下一轮引用。
    if core.get("streamed") and sink is not None:
        try:
            from app.agent import actions as _mcp_actions
            from app.agent.mcp_tools import run_stream_mcp_tool_stage
            _mcp_src = final_state.get("raw_response") or full_text
            _has_mcp = any(
                getattr(_a, "action_type", "").startswith("mcp.")
                for _a in _mcp_actions.parse_actions(_mcp_src)
            )
            if _has_mcp:
                _mcp_steps: list[dict] = []
                _, _mcp_results = await run_stream_mcp_tool_stage(
                    final_state, _mcp_steps,
                    user_id=user_id, character_id=character_id, session_id=session_id,
                )
                for _r in _mcp_results:
                    await sink("tool_result", _r)
        except Exception as e:
            _logger.warning("Stream MCP tool stage failed: %s", e)

    # P3-5（2026-08-29）：回退批量路径会重推已推送的块，用 done.fallback=true 标给前端，
    # 前端按块 id 去重（_confirmStreamBlock），避免重复气泡。
    _fallback = False

    if tts and core.get("stream_saved"):
        # 实时逐句路径：块已在流中实时落库/推送，生成后仅回填首/末块 meta，不再重推 block。
        # P2-B：consumer 若中途死亡（block_sink DB 异常），saved 只含前 N 块，stream_blocks 中
        # 剩余块将永远不会落库 → 先做完整性检查：不完整则删除半截块并回退下方批量落库，
        # 保证该轮 AI 块数与 stream_blocks 一致、done.blocks 与全文对应（历史不缺失）。
        saved = core["stream_saved"]
        all_blocks = core.get("stream_blocks") or []
        if len(saved) < len(all_blocks):
            _logger.warning(
                "TTS stream partial: %d/%d blocks saved, falling back to batch",
                len(saved), len(all_blocks),
            )
            if saved:
                await _delete_chunks([c["id"] for c in saved])
            # P3-5：回退批量路径会重推已推送的块 → 标记 done.fallback=true（前端按块 id 去重）
            _fallback = True
            # 落到下方非 TTS 批量落库路径（split_response/_persist_ai_chunks 全量落库，
            # 此时 tts_urls 已由 _synthesize_chunks_tts 逐句合成补上，每个块仍带 tts_url）。
        else:
            saved = await _backfill_stream_tts_meta(
                saved, final_state, gen_prompt,
                extra_capabilities, _cal_note_text, _memo_text,
            )
            await _push_user_notify(user_id, session_id, character_id, full_text)
            await _run_post_processing(
                session_id, user_id, character_id, content,
                final_state, full_text, saved[0]["id"] if saved else None, user_msg_id,
                reliability=True, gen_prompt=gen_prompt, img_text=img_text,
            )
            _logger.info("Stream(tts): %d blocks from %d chars", len(saved), len(full_text))
            await sink("done", {
                "message": {"content": full_text},
                "blocks": saved,
                "memories_updated": final_state.get("should_update_memory", False),
            })
            return

    # 流式块：节点边的 IncrementalResponseChunker 收集；未流式（深度思考/after_generate 回退）
    # 时按现有 split_response 伪切块兜底。
    if core.get("streamed") and core.get("stream_blocks"):
        chunk_texts = core["stream_blocks"]
    else:
        from app.agent.nodes import split_response
        chunk_texts = split_response(full_text, final_state.get("emotional_state", ""))
        chunk_texts = chunk_texts or ([full_text] if full_text else [])

    tts_urls = None
    if tts:
        tts_urls = await _synthesize_chunks_tts(chunk_texts, session_id, character_id, user_id)

    # P2-NEW（2026-08-29）：回退批量路径（_fallback=True）会先删旧块再全量新建，新块 ID 与旧块
    # 不同，前端按块 id 去重（_confirmStreamBlock）永远不命中 → 先发 reset_blocks 让前端清除本轮
    # 已确认的 AI 正式块，避免重复气泡。此事件只在回退批量路径发送；正常 TTS 实时路径/普通非流式不发送。
    if _fallback:
        await sink("reset_blocks", {"reason": "tts_consumer_fallback"})

    saved = await _persist_ai_chunks(
        session_id, final_state, chunk_texts, gen_prompt,
        _cal_note_text, _memo_text, extra_capabilities,
        tts_urls=tts_urls, tts=tts,
    )
    await _push_user_notify(user_id, session_id, character_id, full_text)

    await _run_post_processing(
        session_id, user_id, character_id, content,
        final_state, full_text, saved[0]["id"] if saved else None, user_msg_id,
        reliability=True,
        gen_prompt=gen_prompt, img_text=img_text,
    )

    _logger.info("Stream: %d blocks from %d chars", len(saved), len(full_text))
    for i, c in enumerate(saved):
        await sink("block", {"index": i, **c})
    await sink("done", {
        "message": {"content": full_text},
        "blocks": saved,
        "memories_updated": final_state.get("should_update_memory", False),
        # P3-5：回退批量路径重推块时置 true，前端按块 id 去重避免重复气泡
        "fallback": _fallback,
    })
