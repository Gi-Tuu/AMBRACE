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
import re
import time as _time

from fastapi import WebSocket

from app.agent.llm_client import chat_completion_stream, TASK_CHAT
from app.application import tts_service
from app.utils.logger import get_logger

_logger = get_logger("voice.gateway")

TTS_DIR = tts_service.TTS_DIR
THINKING_TEXT = "嗯…"


def clean_voice_text(text: str, char_name: str = "") -> str:
    # 剥离 LLM 输出中的说话人/神态前缀，避免 TTS 把前缀念出来。
    if not text:
        return ""
    cleaned = text.strip()
    name = (char_name or "").strip()
    if name:
        _n = re.escape(name)
        cleaned = re.sub(_n + r"\s*[:：]\s*", "", cleaned, count=1)
        cleaned = re.sub(_n + r"\s*[（(][^）)]{1,12}[）)]\s*[:：]\s*", "", cleaned, count=1)
        cleaned = re.sub(_n + r"\s*语音说\s*[:：]?\s*", "", cleaned, count=1)
    # 兜底：任意短名字 + （神态）：前缀（锚定行首，避免误删句中片段）
    cleaned = re.sub(r"^\s*[\u4e00-\u9fa5A-Za-z0-9_]{1,12}\s*[（(][^）)]{1,12}[）)]\s*[:：]\s*", "", cleaned, count=1)
    # 正文残留：开头【标签】与行尾神态/动作提示（保留真实口白括号）
    cleaned = re.sub(r"^\s*[【\[][^】\]]{1,12}[】\]]\s*", "", cleaned)
    _STAGE_TAIL = re.compile(r"\s*[（(](?:[^）)]{0,10}?(?:笑|叹|顿|喘|咳嗽|轻声|停顿|沉默|吸气|呼气|哽咽|嘀咕|嘟囔)[^）)]{0,10}?)[）)]\s*$")
    cleaned = _STAGE_TAIL.sub("", cleaned)
    cleaned = re.sub(r"\s*[【\[][^】\]]{1,12}[】\]]\s*$", "", cleaned)
    return cleaned.strip()


async def _asr_text(audio_bytes: bytes) -> str | None:
    """音频字节（wav/m4a）→ ASR provider 转写（默认本地 faster-whisper 整段；失败返回 None）

    Phase 1：经 get_asr_provider() 分发——流式 ASR 配置+启用+协议已确认才走流式，
    否则回退 LocalWhisperProvider（与旧行为完全一致）。asr_final 事件契约不变。
    """
    from app.voice.asr_provider import get_asr_provider

    provider = get_asr_provider()
    return await provider.transcribe(audio_bytes)


async def _synthesize_sentence(text: str, params: dict, subdir: str, emotion: str | None = None) -> str | None:
    """逐句合成 → 音频 URL（/uploads/tts/...，复用 tts_service：百炼优先，edge-tts 兜底）。

    2026-08-12 协议调整：服务端不再推二进制帧，改发 URL 由客户端 UrlSource 播放
    （BytesSource 在 Android 大 wav 帧播放不可靠，真机实测只听到思考音、回复帧无声）。

    emotion（Phase 0 P0 / P2-1）：语音回合现在由 _refresh_emotional_state 写入
    state['emotional_state']（角色八维状态规则器，零 LLM），调用方传入实际值；
    None/空串时零行为变化。
    """
    return await tts_service.synthesize(
        text,
        subdir=subdir,
        gender=params.get("gender"),
        voice=params.get("voice"),
        voice_rate=params.get("voice_rate"),
        voice_pitch=params.get("voice_pitch"),
        emotion=emotion,
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
    from app.application.chat_service import get_owned_session

    async with async_session_factory() as db:
        session = await get_owned_session(db, int(session_id), user_id)
        if session is None or session.character_id != int(character_id):
            return False
    state["session_id"] = int(session_id)
    state["character_id"] = int(character_id)
    from app.voice.voice_mode import load_character_voice_params
    state["voice_params"] = await load_character_voice_params(int(character_id))
    await _refresh_emotional_state(state)  # P2-1：会话开始定一次情绪
    asyncio.create_task(_warmup_thinking_audio(state))
    return True


def _record_interrupted(state: dict) -> None:
    """记录被打断的半截回复（供下一轮注入「你没说完」提示）"""
    t = (state.get("sent_text") or "").strip()
    if t:
        state["last_interrupted"] = t[-200:]


async def _refresh_emotional_state(state: dict) -> None:
    """P2-1：取角色八维状态 → 计算 TTS 情感标签写入 state['emotional_state']。

    会话开始/每轮调用；失败/无状态保持空串（零行为变化，不阻塞）。
    """
    try:
        from app.application.character_state_service import get_character_states
        from app.domain.emotion.model import emotion_from_character_states
        _cs = await get_character_states(state.get("character_id"))
        state["emotional_state"] = emotion_from_character_states(_cs) or ""
    except Exception as e:
        _logger.warning("Voice emotion refresh failed char=%s: %s", state.get("character_id"), e)
        state["emotional_state"] = state.get("emotional_state") or ""


async def _process_utterance(ws: WebSocket, state: dict, audio_bytes: bytes) -> None:
    """处理一段用户语音：ASR → voice_mode → LLM 流式（流水线）→ 逐句 TTS 下发"""
    subdir = f"voice_{state['session_id']}"
    _turn = state.get("turn_id", 0)
    try:
        # P2-2 服务端轻量 VAD（#71）：极短帧/全静音帧不触发 ASR（省 faster-whisper）。
        # 判定 False 时回「没听清」并计数，不崩溃；VAD 自身异常 fail-open（走 ASR）。
        try:
            from app.voice.audio_gate import should_transcribe
            if not should_transcribe(audio_bytes):
                state["vad_ignored"] = state.get("vad_ignored", 0) + 1
                _logger.info("Voice VAD gate skip frame (count=%d)", state["vad_ignored"])
                await ws.send_json({"type": "asr_final", "text": ""})
                await ws.send_json({"type": "error", "code": "asr_empty", "message": "没听清，再说一遍"})
                return
        except Exception as e:
            _logger.warning("Voice VAD gate failed, fallback ASR: %s", e)

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

        # P2-1：每轮刷新角色情感标签（缺省安全）；并对用户 ASR 文本做情绪提示
        await _refresh_emotional_state(state)
        _user_emotion = None
        try:
            from app.domain.emotion.model import detect_user_emotion
            _user_emotion = detect_user_emotion(text) or None
        except Exception:
            _user_emotion = None

        # 2) 思考音：LLM 处理期间播放（Phase B；URL 播放，客户端按需播放一次）
        thinking_url = state.get("thinking_audio_url")
        await ws.send_json({"type": "ai_thinking", "url": thinking_url or ""})

        # 3) voice_mode 上下文（含上一轮被打断提示）+ 流式 LLM
        from app.voice.sentence_chunker import SentenceChunker
        from app.voice.voice_mode import build_voice_messages

        messages = await build_voice_messages(
            state["user_id"], state["character_id"], state["session_id"], text,
            interrupted_text=state.get("last_interrupted"),
            user_emotion_hint=_user_emotion,
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
                # P2-2：打断后不再 send llm_sentence/tts_audio（含 turn_id 防抖防御）
                if sent is None or state["interrupt"] or state.get("turn_id", 0) != _turn:
                    return
                # 剥离 LLM 可能带出的说话人/神态前缀，避免 TTS 把前缀念出来
                sent = clean_voice_text(sent, (state.get("voice_params") or {}).get("name") or "")
                if not sent:
                    continue
                state["sent_text"] = (state.get("sent_text") or "") + sent
                await ws.send_json({"type": "llm_sentence", "text": sent})
                # P2-1：语音回合现在有结构化情绪（emotional_state）→ 传给 TTS
                url = await _synthesize_sentence(
                    sent, state["voice_params"], subdir,
                    emotion=state.get("emotional_state"),
                )
                if url and not state["interrupt"] and state.get("turn_id", 0) == _turn:
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
        "emotional_state": "",  # P2-1：当前角色情感标签（TTS 用）
        "turn_id": 0,           # P2-2：回合代次（防抖：打断后旧回合不得再发 tts_audio）
        "vad_ignored": 0,       # P2-2：被 VAD 忽略的帧计数
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
                state["turn_id"] = state.get("turn_id", 0) + 1  # P2-2：新回合代次（防抖）
                state["task"] = asyncio.create_task(
                    _process_utterance(ws, state, bytes(raw))
                )
    finally:
        await _cancel_task(state)
