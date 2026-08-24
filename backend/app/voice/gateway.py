"""语音实时链路（Phase A+B）：WS 会话编排（轻量实时优先）

流程：客户端录音（wav/m4a 字节）→ 整段 ASR → voice_mode 上下文 → LLM 流式
→ 句子切分（流水线）→ 逐句 TTS（百炼/edge 兜底）→ 二进制帧下发 → 客户端边收边播。
打断：客户端发 barge_in（或新一轮语音）→ 取消当前回合 → ai_interrupted，
被打断的半截回复记入会话状态，下一轮注入「你没说完」提示（Phase B）。
体验增强（Phase B）：
- TTS 流水线：LLM producer 与 TTS consumer 并行，LLM 不被逐句合成阻塞；
- 思考音预热：session_start 时按角色音色预生成「嗯…」，回复前下发 ai_thinking（has_audio）+ 音频；
- 首字耗时日志：ASR 完成 → 首句音频下发耗时。
尚未做（依赖流式 ASR/客户端 VAD，Phase C）：投机生成、附和声、Silero VAD。
"""
import asyncio
import json
import tempfile
import time as _time
import uuid
from pathlib import Path

from fastapi import WebSocket

from app.agent.llm_client import chat_completion_stream, TASK_CHAT
from app.services import speech_service, tts_service
from app.utils.logger import get_logger

_logger = get_logger("voice.gateway")

TTS_DIR = tts_service.TTS_DIR
THINKING_TEXT = "嗯…"


async def _asr_text(audio_bytes: bytes) -> str | None:
    """音频字节（wav/m4a）→ 临时文件 → faster-whisper 整段转写（失败返回 None）"""
    tmp = Path(tempfile.gettempdir()) / f"voice_asr_{uuid.uuid4().hex[:8]}.m4a"
    tmp.write_bytes(audio_bytes)
    try:
        return await speech_service.transcribe(str(tmp), language="zh")
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


async def _synthesize_sentence(text: str, params: dict, subdir: str) -> str | None:
    """逐句合成 → 音频 URL（/uploads/tts/...，复用 tts_service：百炼优先，edge-tts 兜底）。

    2026-08-12 协议调整：服务端不再推二进制帧，改发 URL 由客户端 UrlSource 播放
    （BytesSource 在 Android 大 wav 帧播放不可靠，真机实测只听到思考音、回复帧无声）。
    """
    return await tts_service.synthesize(
        text,
        subdir=subdir,
        gender=params.get("gender"),
        voice=params.get("voice"),
        voice_rate=params.get("voice_rate"),
        voice_pitch=params.get("voice_pitch"),
    )


async def _warmup_thinking_audio(state: dict) -> None:
    """预热：按角色音色预生成思考音（嗯…）缓存，供 ai_thinking 下发；失败静默"""
    try:
        url = await _synthesize_sentence(
            THINKING_TEXT, state.get("voice_params") or {},
            f"thinking_{state.get('character_id')}",
        )
        if url:
            state["thinking_audio_url"] = url
            _logger.info("Voice thinking audio warmup ok: %s", url)
    except Exception as e:
        _logger.warning("Voice thinking audio warmup failed: %s", e)


async def _init_session(state: dict, session_id, character_id, user_id: int) -> bool:
    """校验会话归属与角色一致，并加载角色语音参数；成功后预热思考音（不阻塞）"""
    if not session_id or not character_id:
        return False
    from app.db.database import async_session_factory
    from app.services.chat_service import get_owned_session

    async with async_session_factory() as db:
        session = await get_owned_session(db, int(session_id), user_id)
        if session is None or session.character_id != int(character_id):
            return False
    state["session_id"] = int(session_id)
    state["character_id"] = int(character_id)
    from app.voice.voice_mode import load_character_voice_params
    state["voice_params"] = await load_character_voice_params(int(character_id))
    asyncio.create_task(_warmup_thinking_audio(state))
    return True


def _record_interrupted(state: dict) -> None:
    """记录被打断的半截回复（供下一轮注入「你没说完」提示）"""
    t = (state.get("sent_text") or "").strip()
    if t:
        state["last_interrupted"] = t[-200:]


async def _process_utterance(ws: WebSocket, state: dict, audio_bytes: bytes) -> None:
    """处理一段用户语音：ASR → voice_mode → LLM 流式（流水线）→ 逐句 TTS 下发"""
    subdir = f"voice_{state['session_id']}"
    try:
        # 1) ASR 整段转写
        text = await _asr_text(audio_bytes)
        if state["interrupt"]:
            return
        if not text:
            await ws.send_json({"type": "asr_final", "text": ""})
            await ws.send_json({"type": "error", "code": "asr_empty", "message": "没听清，再说一遍"})
            return
        await ws.send_json({"type": "asr_final", "text": text})
        _t0 = _time.monotonic()

        # 2) 思考音：LLM 处理期间播放（Phase B；URL 播放，客户端按需播放一次）
        thinking_url = state.get("thinking_audio_url")
        await ws.send_json({"type": "ai_thinking", "url": thinking_url or ""})

        # 3) voice_mode 上下文（含上一轮被打断提示）+ 流式 LLM
        from app.voice.sentence_chunker import SentenceChunker
        from app.voice.voice_mode import build_voice_messages

        messages = await build_voice_messages(
            state["user_id"], state["character_id"], state["session_id"], text,
            interrupted_text=state.get("last_interrupted"),
        )
        chunker = SentenceChunker()
        state["sent_text"] = ""
        state["last_interrupted"] = None  # 本轮若说完则清掉提示
        await ws.send_json({"type": "ai_speaking_start"})

        # 流水线：producer 喂句子，consumer 逐句合成下发（LLM 不被 TTS 阻塞）
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        first_audio_sent = False

        async def consumer() -> None:
            nonlocal first_audio_sent
            while True:
                sent = await queue.get()
                if sent is None or state["interrupt"]:
                    return
                state["sent_text"] = (state.get("sent_text") or "") + sent
                await ws.send_json({"type": "llm_sentence", "text": sent})
                url = await _synthesize_sentence(sent, state["voice_params"], subdir)
                if url and not state["interrupt"]:
                    if not first_audio_sent:
                        first_audio_sent = True
                        _logger.info(
                            "Voice first-audio latency: %.0f ms (asr_done -> first tts frame)",
                            (_time.monotonic() - _t0) * 1000,
                        )
                    await ws.send_json({"type": "tts_audio", "url": url})

        consumer_task = asyncio.create_task(consumer())
        _llm_first_delta = False
        try:
            async for delta in chat_completion_stream(
                messages, temperature=0.8, max_tokens=512, thinking=False, task=TASK_CHAT
            ):
                if not _llm_first_delta:
                    _llm_first_delta = True
                    _logger.info("Voice LLM first delta: %.0f ms (asr_done -> first token)", (_time.monotonic() - _t0) * 1000)
                if state["interrupt"]:
                    return
                for sent in chunker.feed(delta):
                    await queue.put(sent)
            for sent in chunker.finish():
                if sent:
                    await queue.put(sent)
        finally:
            await queue.put(None)
            try:
                await consumer_task
            except BaseException:
                pass

        if state["interrupt"]:
            return
        await ws.send_json({"type": "ai_speaking_end"})
    except asyncio.CancelledError:
        raise
    except Exception as e:
        _logger.warning("Voice utterance failed: %s", e)
        try:
            await ws.send_json({"type": "error", "code": "voice_failed", "message": "语音回复失败"})
        except Exception:
            pass
    finally:
        if state.get("interrupt"):
            _record_interrupted(state)


async def _cancel_task(state: dict) -> None:
    task = state.get("task")
    if task and not task.done():
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    state["task"] = None


async def handle_voice_session(ws: WebSocket, user_id: int) -> None:
    """WS 主循环：文本帧（控制）+ 二进制帧（用户语音 wav/m4a）"""
    await ws.accept()
    state: dict = {
        "user_id": user_id,
        "session_id": None,
        "character_id": None,
        "voice_params": {},
        "thinking_audio_url": None,
        "last_interrupted": None,
        "sent_text": "",
        "task": None,
        "interrupt": False,
    }
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg["type"] != "websocket.receive":
                continue
            text = msg.get("text")
            raw = msg.get("bytes")
            if text is not None:
                try:
                    data = json.loads(text)
                except Exception:
                    continue
                t = data.get("type")
                if t == "session_start":
                    ok = await _init_session(
                        state, data.get("session_id"), data.get("character_id"), user_id
                    )
                    await ws.send_json({"type": "ready", "ok": ok})
                elif t == "barge_in":
                    state["interrupt"] = True
                    await _cancel_task(state)
                    _record_interrupted(state)
                    await ws.send_json({"type": "ai_interrupted"})
                elif t == "session_end":
                    break
                elif t == "ping":
                    await ws.send_json({"type": "pong"})
            elif raw is not None:
                if state["character_id"] is None:
                    continue
                # 新一轮语音 = 打断旧回合
                if state["task"] and not state["task"].done():
                    state["interrupt"] = True
                    await _cancel_task(state)
                state["interrupt"] = False
                state["sent_text"] = ""
                state["task"] = asyncio.create_task(
                    _process_utterance(ws, state, bytes(raw))
                )
    finally:
        await _cancel_task(state)
