"""聊天服务：管理消息收发，集成 Agent"""
from app.utils.logger import get_logger
from app.utils.errors import friendly_llm_error
from sqlalchemy import select, func, and_, or_, delete
from app.db.database import async_session_factory
import asyncio
import json
import re
import time
from datetime import datetime, timezone

from app.agent import actions as _agent_actions

_logger = get_logger("services.chat")
from app.models.chat_session import ChatSession
from app.models.chat_message import ChatMessage
from app.models.character import AICharacter
from app.agent.graph import agent
from app.memory import add_chat_memory_extraction
from app.memory.extractor import SELF_STATEMENT_MAX_LEN


async def get_latest_session_id(user_id: int | None, character_id: int) -> int | None:
    """选择该角色真正活跃的会话：按最新一条消息时间排序（无消息回退创建时间/ID）。

    历史坑：mark_session_read 联动标记已读曾触发 ORM onupdate 污染 updated_at，
    同角色多会话 updated_at 相同时按 updated_at 排序会选错会话（表现为聊天记录"只剩早期"）。
    现在统一以消息时间为准。
    """
    conds = [
        ChatSession.character_id == character_id,
        ChatSession.is_active == True,
    ]
    if user_id is not None:
        conds.append(ChatSession.user_id == user_id)
    async with async_session_factory() as db:
        result = await db.execute(
            select(ChatSession.id, func.max(ChatMessage.created_at).label("last_msg_at"))
            .outerjoin(ChatMessage, ChatMessage.session_id == ChatSession.id)
            .where(*conds)
            .group_by(ChatSession.id)
            .order_by(
                func.coalesce(func.max(ChatMessage.created_at), ChatSession.created_at).desc(),
                ChatSession.id.desc(),
            )
            .limit(1)
        )
        row = result.first()
        return row[0] if row else None


async def create_session(user_id: int, character_id: int) -> dict:
    """获取或创建聊天会话，优先复用最新活跃会话（按最新消息时间选会话）"""
    existing_id = await get_latest_session_id(user_id, character_id)
    if existing_id:
        _logger.debug("Reusing existing session: id=%d", existing_id)
        return {"id": existing_id, "character_id": character_id, "greeting": None}

    async with async_session_factory() as db:

        session = ChatSession(user_id=user_id, character_id=character_id)
        db.add(session)
        await db.flush()
        await db.refresh(session)

        result = await db.execute(
            select(AICharacter).where(AICharacter.id == character_id)
        )
        char = result.scalar_one_or_none()
        greeting = char.greeting_message if char and char.greeting_message else ""

        if greeting:
            greeting_msg = ChatMessage(
                session_id=session.id, sender_type="ai", content=greeting,
            )
            db.add(greeting_msg)
            await db.flush()
            await db.commit()
        await db.commit()
        _logger.info("New session created: id=%d char=%d greeting=%s", session.id, character_id, bool(greeting))
        return {"id": session.id, "character_id": character_id, "greeting": greeting}


async def _save_bio_update(character_id: int, bio_text: str, user_id: int):
    """保存自述更新到角色表（写入独立自述字段，不覆盖用户提供的背景信息 bio）"""
    if not bio_text:
        return
    try:
        async with async_session_factory() as db:
            result = await db.execute(select(AICharacter).where(AICharacter.id == character_id))
            char = result.scalar_one_or_none()
            if char:
                char.self_statement = bio_text[:SELF_STATEMENT_MAX_LEN]
                await db.flush()
                await db.commit()
                _logger.info("Bio updated for character %d: %.60s", character_id, bio_text)
    except Exception as e:
        _logger.warning("Bio update failed: %s", e)


# 进程内去重：已生成过初始自述的角色不再重复触发
_initial_bio_done: set[int] = set()


async def _generate_initial_bio(character_id: int, user_id: int) -> None:
    """角色无自述时，用 LLM 依据人格/风格/关系生成初始自述（异步、失败静默）"""
    if character_id in _initial_bio_done:
        return
    try:
        from app.agent.llm_client import chat_completion, get_user_llm_config
        async with async_session_factory() as db:
            result = await db.execute(select(AICharacter).where(AICharacter.id == character_id))
            char = result.scalar_one_or_none()
            if not char or (char.self_statement and char.self_statement.strip()):
                _initial_bio_done.add(character_id)
                return
            cfg = await get_user_llm_config(user_id)
            prompt = (
                "你是角色设定撰写助手。请以第一人称写一段简短自述（60 字以内），"
                "说明这个角色是谁、性格特点、与用户的关系。只输出自述正文，不要任何前缀或解释。\n"
                f"角色名：{char.name or 'AI'}\n"
                f"人格：{char.personality or '暂无'}\n"
                f"聊天风格：{char.chat_style or '暂无'}\n"
                f"与用户的关系：{char.relationship_summary or '普通朋友'}（类型：{char.relation_type or '朋友'}）"
            )
            text = (await chat_completion(
                [{"role": "user", "content": prompt}],
                max_tokens=200, task="card", **(cfg or {}),
            ) or "").strip().strip('\"「」”')
            if text:
                char.self_statement = text[:SELF_STATEMENT_MAX_LEN]
                await db.flush()
                await db.commit()
                _logger.info("Initial bio generated for character %d: %.60s", character_id, text)
            _initial_bio_done.add(character_id)
    except Exception as e:
        _logger.warning("Initial bio generation failed char=%d: %s", character_id, e)


async def _save_status_update(character_id: int, status_text: str, user_id: int):
    """保存当前状态更新到角色表，同时存入记忆库"""
    if not status_text:
        return
    try:
        async with async_session_factory() as db:
            result = await db.execute(select(AICharacter).where(AICharacter.id == character_id))
            char = result.scalar_one_or_none()
            if char:
                char.current_status = status_text[:200]
                await db.flush()
                await db.commit()
                _logger.info("Status updated for character %d: %.60s", character_id, status_text)
        # 同时存入记忆
        try:
            from app.memory import save_memory
            await save_memory(
                user_id=user_id,
                character_id=character_id,
                memory_type="insight",
                content=f"状态更新: {status_text[:200]}",
                importance=2,
                sub_type="status",
                source="status",
                speaker_type="character", speaker_id=character_id,
                epistemic_status="FACT",
            )
        except Exception as e:
            _logger.warning("Failed to save status as memory: %s", e)
        # 世界状态折叠（P4）：状态更新 → 当前世界事实（失败静默）
        try:
            from app.events.facts import fold_status_update
            await fold_status_update(character_id, user_id, status_text)
        except Exception as e:
            _logger.warning("World fact fold status failed: %s", e)
    except Exception as e:
        _logger.warning("Status update failed: %s", e)


def _trigger_state_eval(character_id: int, user_id: int, user_msg: str, ai_response: str, status_update):
    """异步触发八维状态评估（fire-and-forget，失败不影响聊天）"""
    try:
        from app.services.character_state_service import update_character_states
        asyncio.ensure_future(update_character_states(
            character_id, user_id,
            user_msg or "", ai_response or "",
            status_update or "",
        ))
    except Exception as e:
                _logger.warning("State eval trigger failed: %s", e)


async def _bump_relationship(character_id: int, user_id: int) -> None:
    """认知循环 v2.1：用户主动发消息 → 关系标量小幅上升（信任+2、依恋+1，封顶 100；失败静默）"""
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.character_state import CharacterState
        async with async_session_factory() as db:
            st = (await db.execute(
                select(CharacterState).where(CharacterState.character_id == character_id)
            )).scalar_one_or_none()
            if st is None:
                return
            changed = False
            if int(st.trust or 50) < 100:
                st.trust = min(100, int(st.trust or 50) + 2)
                changed = True
            if int(st.attachment or 50) < 100:
                st.attachment = min(100, int(st.attachment or 50) + 1)
                changed = True
            if changed:
                await db.commit()
                _logger.info("Relationship bump char=%d trust=%d attachment=%d",
                             character_id, st.trust, st.attachment)
    except Exception as e:
        _logger.warning("Relationship bump failed char=%d: %s", character_id, e)


def _extract_gen_image(text: str) -> tuple[str, str | None, str | None]:
    """提取生图标记，返回 (清理后的文本, 画面描述或None, 图片消息文案或None)。

    支持 [IMG_TEXT]...[/IMG_TEXT]（角色随回复一起生成图片消息文案，符合人设）；
    未输出文案时返回 None，由调用方用兜底文案。
    实现已收敛到统一解析层 app.agent.actions（Phase A，行为零变化）。
    """
    return _agent_actions.extract_gen_image(text)


# 进程级节流：每用户 60 秒最多触发 1 次搜索（防滥用，LLM 一轮最多 1 次）
_SEARCH_THROTTLE: dict[int, float] = {}


def _extract_search(text: str) -> tuple[str, str | None]:
    """提取自主搜索标记，返回 (清理后文本, 查询词或None)。

    实现已收敛到统一解析层 app.agent.actions（Phase A，行为零变化）。
    """
    return _agent_actions.extract_search(text)


def _search_throttle(user_id: int, seconds: float = 60.0) -> bool:
    import time as _time
    now = _time.time()
    last = _SEARCH_THROTTLE.get(user_id, 0.0)
    if now - last < seconds:
        return False
    _SEARCH_THROTTLE[user_id] = now
    return True


def _search_inject_enabled() -> bool:
    """browser_mcp 插件「自主搜索注入」开关（config.search_inject，默认开）。
    关：LLM 输出 [SEARCH] 仅视为内心活动/剧情，剥离标记但不搜索不注入（2026-08-16 用户拍板）。"""
    try:
        from app.plugins.registry import get_plugin
        plugin = get_plugin("browser_mcp")
        if plugin is None:
            return False
        return bool(plugin.get("config", {}).get("search_inject", True))
    except Exception:
        return True


def _polish_search_query(query: str) -> str:
    """查询词轻量改写（2026-08-16）：去首尾语气词/口语词、压缩空白，不花 LLM token。"""
    q = re.sub(r"^(帮我|请问|麻烦|能不能|可以帮我|帮我一下|你帮我|给我|帮我查查|帮我搜)[：:，,。\s]*", "", (query or "").strip())
    q = re.sub(r"[吧呗呀呢啊哦哈]+$", "", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q[:80] or (query or "").strip()[:80]


async def _run_web_search(query: str, timeout: float = 20.0) -> str:
    """AI 自主搜索：browser_mcp 打开 Bing 抓取结果（中文拆词自动过滤+DDG 补充），返回可读摘要；失败/插件未加载返回空串。"""
    try:
        query = _polish_search_query(query)
        import sys as _sys
        mod = _sys.modules.get("ai_plugin_browser_mcp")
        if mod is None or not hasattr(mod, "search_web"):
            return ""
        res = await asyncio.wait_for(mod.search_web(query), timeout=timeout)
        if not res or not res.get("ok"):
            return ""
        lines = []
        for r in (res.get("results") or [])[:5]:
            title = (r.get("title") or "").strip()
            snippet = (r.get("snippet") or "").strip()
            if title or snippet:
                lines.append(f"- {title}：{snippet}" if title and snippet else (title or snippet))
        if not lines:
            _logger.info("Web search empty query=%s", query[:60])
            return ""
        _logger.info("Web search ok query=%s results=%d", query[:60], len(lines))
        return "\n".join(lines)[:1500]
    except Exception as e:
        _logger.warning("Web search failed query=%s: %s", query[:60], e)
        return ""


def _extract_cal_note(text: str) -> tuple[str, str] | None:
    """提取日历备注标记，返回 (YYYY-MM-DD, 内容)；无标记返回 None。日期省略=今天（北京时间）。

    实现已收敛到统一解析层 app.agent.actions（Phase A，行为零变化）。
    """
    return _agent_actions.extract_cal_note(text)


def _extract_memo(text: str) -> str | None:
    """提取备忘录标记，返回内容（≤80 字）；无标记返回 None。

    实现已收敛到统一解析层 app.agent.actions（Phase A，行为零变化）。
    """
    return _agent_actions.extract_memo(text)


async def _save_calendar_note(character_id: int, note_date: str, note_text: str, note_author: str = "") -> bool:
    """日历备注落库（去重防重复；署名角色名；失败静默）。Phase F 拆出的纯落库函数。"""
    try:
        from app.models.phone_desktop import CalendarNote
        from sqlalchemy import select as _sa_select
        from app.models.phone_desktop import CalendarNote as _CalNote
        async with async_session_factory() as db:
            dup = (await db.execute(_sa_select(_CalNote).where(
                _CalNote.character_id == character_id,
                _CalNote.note_date == note_date,
                _CalNote.note_text == note_text,
            ))).scalar_one_or_none()
            if dup is None:
                db.add(CalendarNote(character_id=character_id, note_date=note_date, note_text=note_text, author=note_author))
                await db.commit()
                _logger.info("AI calendar note saved: char=%d date=%s", character_id, note_date)
                return True
        return False
    except Exception as e:
        _logger.warning("Calendar note save failed: %s", e)
        return False


async def _save_memo_note(character_id: int, memo_text: str, note_author: str = "") -> bool:
    """备忘录落库（去重防重复；署名角色名；失败静默）。Phase F 拆出的纯落库函数。"""
    try:
        from sqlalchemy import select as _sa_select2
        from app.models.phone_desktop import MemoNote as _MemoNote2
        async with async_session_factory() as db:
            dup = (await db.execute(_sa_select2(_MemoNote2).where(
                _MemoNote2.character_id == character_id,
                _MemoNote2.text == memo_text,
            ))).scalar_one_or_none()
            if dup is None:
                db.add(_MemoNote2(character_id=character_id, text=memo_text, author=note_author))
                await db.commit()
                _logger.info("AI memo saved: char=%d", character_id)
                return True
        return False
    except Exception as e:
        _logger.warning("Memo save failed: %s", e)
        return False


async def _execute_note_tool(tool_name: str, payload: dict, character_id: int) -> None:
    """经统一工具执行入口执行本地小手机工具（Phase F：note_calendar/note_memo）。

    本地能力 scope=None 直接执行（无权限门禁）；仍触发工具生命周期钩子与异常隔离。
    """
    from app.agent import tools as _tools
    from app.agent.tool_runner import execute_tool
    _spec = _tools.get_tool(tool_name)
    if _spec is None:
        return
    if tool_name == "note_calendar":
        async def _run(p: dict):
            return await _save_calendar_note(
                int(p.get("character_id") or 0), p.get("date", ""), p.get("text", ""), p.get("author", ""),
            )
    else:
        async def _run(p: dict):
            return await _save_memo_note(
                int(p.get("character_id") or 0), p.get("text", ""), p.get("author", ""),
            )
    _exec_spec = _tools.ToolSpec(
        name=_spec.name,
        description=_spec.description,
        action_type=_spec.action_type,
        risk_level=_spec.risk_level,
        rate_limit=_spec.rate_limit,
        idempotent=_spec.idempotent,
        scope=_spec.scope,
        ask_auto_allow=_spec.ask_auto_allow,
        execute=_run,
    )
    await execute_tool(_exec_spec, payload, user_id=None, character_id=character_id)


async def _save_phone_desktop_notes(character_id: int, full_text: str) -> None:
    """小手机日历/备忘录标记落库（[CAL_NOTE]/[MEMO]）。

    主聊天 /send 统一接入（Phase F：默认经统一执行入口 execute_tool；
    Feature Flag agent_loop_chat 关闭时直接落库=旧链路，可回退对比）；失败静默。
    """
    try:
        from app.models.character import AICharacter
        from sqlalchemy import select
        _note_author = ""
        async with async_session_factory() as _db:
            _nm = (await _db.execute(select(AICharacter.name).where(AICharacter.id == character_id))).scalar_one_or_none()
        if _nm:
            _note_author = _nm
    except Exception:
        _note_author = ""
    from app.agent import loop as _loop
    _use_runtime = _loop.AGENT_FLAGS.get("agent_loop_chat", True)
    # 日历备注 [CAL_NOTE]...[/CAL_NOTE]
    try:
        _cal = _extract_cal_note(full_text or "")
        if _cal:
            _note_date, _note_text = _cal
            if _use_runtime:
                await _execute_note_tool("note_calendar", {
                    "character_id": character_id, "date": _note_date, "text": _note_text, "author": _note_author,
                }, character_id)
            else:
                await _save_calendar_note(character_id, _note_date, _note_text, _note_author)
    except Exception as e:
        _logger.warning("Calendar note save failed: %s", e)
    # 备忘录 [MEMO]...[/MEMO]
    try:
        _memo_text = _extract_memo(full_text or "")
        if _memo_text:
            if _use_runtime:
                await _execute_note_tool("note_memo", {
                    "character_id": character_id, "text": _memo_text, "author": _note_author,
                }, character_id)
            else:
                await _save_memo_note(character_id, _memo_text, _note_author)
    except Exception as e:
        _logger.warning("Memo save failed: %s", e)


async def _gen_image_flow(user_id: int, character_id: int, session_id: int, prompt: str, img_text: str | None = None) -> None:
    """聊天内AI发图：异步生图并追加 AI 图片消息（成功带图 / 失败带说明），并 WS 实时上屏"""
    # AI 能力权限（2026-08-12）：forbid 跳过并提示；ask 挂起动作等用户确认
    try:
        from app.services import permission_service
        _mode = await permission_service.check_mode(user_id, permission_service.SCOPE_IMAGE_GEN)
        if _mode == "forbid":
            await _append_ai_text_message(session_id, "（已按你的权限设置，这次不生成图片了）")
            return
        if _mode == "ask":
            _row = await permission_service.create_pending_action(
                user_id, session_id, character_id,
                permission_service.SCOPE_IMAGE_GEN,
                {"prompt": prompt, "img_text": img_text},
            )
            try:
                from app.ws.connection_manager import push_to_session
                await push_to_session(session_id, {
                    "type": "permission_request",
                    "data": {
                        "action_id": _row.id,
                        "scope": permission_service.SCOPE_IMAGE_GEN,
                        "scope_label": permission_service.SCOPE_LABELS[permission_service.SCOPE_IMAGE_GEN],
                        "prompt": prompt[:200],
                        "character_id": character_id,
                    },
                })
            except Exception:
                pass
            return
    except Exception as _e:
        _logger.warning("permission check failed char=%d: %s", character_id, _e)
    try:
        from app.services.image_gen_service import (
            create_image_gen_task, run_image_gen_task, check_daily_limit,
        )
        if await check_daily_limit(user_id):
            await _append_ai_text_message(session_id, "（今天的生图额度用完啦，明天再试吧～）")
            return
        # Task Trace（Phase A）：真实生图调用处写 trace（先只写不读；失败静默）
        _img_t0 = time.monotonic()
        task = await create_image_gen_task(user_id, prompt, character_id, session_id)
        image_url = await run_image_gen_task(task.id)
        try:
            from app.agent import trace as _trace
            _trace.enqueue_task_log(
                task_id=_trace.new_task_id(),
                character_id=character_id,
                user_id=user_id,
                session_id=session_id,
                trigger="image_gen",
                route="image_gen",
                steps_json=json.dumps([{"action": "GEN_IMAGE", "prompt": prompt[:80]}], ensure_ascii=False),
                llm_calls=0,
                tool_calls=1,
                latency_ms=int((time.monotonic() - _img_t0) * 1000),
                status="ok" if image_url else "error",
                error=None if image_url else "image gen returned empty",
            )
        except Exception as _te:
            _logger.warning("Task trace failed: %s", _te)
        if image_url:
            await _append_ai_image_message(session_id, image_url, prompt, content=img_text)
        else:
            await _append_ai_text_message(session_id, "（抱歉，图片没生成成功，稍后再让我试试？）")
    except Exception as e:
        _logger.warning("gen image flow failed char=%d: %s", character_id, e)


async def _append_ai_image_message(session_id: int, image_url: str, prompt: str, content: str | None = None) -> None:
    async with async_session_factory() as db:
        msg = ChatMessage(
            session_id=session_id, sender_type="ai",
            content=(content or "给你画好啦～")[:60],
            image_url=image_url,
            extra_meta=json.dumps({"gen_image": True, "prompt": prompt}, ensure_ascii=False),
        )
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
    await _push_ws_ai_message(session_id, msg)


async def _append_ai_text_message(session_id: int, content: str) -> None:
    async with async_session_factory() as db:
        msg = ChatMessage(session_id=session_id, sender_type="ai", content=content)
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
    await _push_ws_ai_message(session_id, msg)


async def _push_ws_ai_message(session_id: int, msg: ChatMessage) -> None:
    """通过 WS 向该会话推送 ai_response（生图消息实时上屏，离线则静默）"""
    try:
        from app.ws.connection_manager import push_to_session
        await push_to_session(session_id, {
            "type": "ai_response",
            "data": {
                "id": msg.id,
                "session_id": session_id,
                "sender_type": "ai",
                "content": msg.content,
                "image_url": msg.image_url,
                "created_at": msg.created_at.isoformat(),
            },
        })
    except Exception as e:
        _logger.warning("WS push ai message failed session=%d: %s", session_id, e)


async def _push_user_notify(user_id: int, session_id: int, character_id: int, content: str) -> None:
    """#55 App 后台保活：把新 AI 消息实时推送到用户级通知连接池（后台 isolate 弹系统通知）。"""
    try:
        from app.ws.notify_manager import push_to_user
        await push_to_user(user_id, {
            "type": "ai_response",
            "data": {
                "session_id": session_id,
                "character_id": character_id,
                "sender_type": "ai",
                "content": content,
            },
            "is_proactive": False,
        })
    except Exception as e:
        _logger.warning("Push user notify failed user=%d: %s", user_id, e)


async def _load_reasoning_level(character_id: int) -> int:
    """读取角色「思考过程」挡位：0=关闭 / 1=简单思考 / 2=深度思考"""
    try:
        from app.models.proactive_settings import ProactiveSettings
        async with async_session_factory() as db:
            row = (await db.execute(
                select(ProactiveSettings.reasoning_level)
                .where(ProactiveSettings.character_id == character_id)
            )).scalar_one_or_none()
        return int(row or 0)
    except Exception as e:
        _logger.warning("Load reasoning_level failed char=%d: %s", character_id, e)
        return 0


async def _cold_war_block(character_id: int, user_id: int, user_msg: str) -> bool:
    """冷战拦截（v3）：角色生气冷战期，用户发消息不回复；哄好关键词可提前解除。

    返回 True = 拦截（不生成 AI 回复）；False = 正常回复（无冷战或已哄好/自动恢复）。
    """
    try:
        # v5-B：吃醋/疲惫剧情线的用户哄/安慰通道（落和好/恢复节点，不拦截回复）
        from app.scheduler.storyline_engine import maybe_resolve_storyline_by_message
        await maybe_resolve_storyline_by_message(character_id, user_id, user_msg)
        from app.scheduler.state_triggers import check_cold_war, resolve_cold_war_by_message
        if await check_cold_war(character_id, user_id):
            _r = await resolve_cold_war_by_message(character_id, user_id, user_msg)
            if _r == 1:
                _logger.info("Cold war resolved char=%d by user message, replying normally", character_id)
                return False
            # v5 增强 ①：敷衍道歉 → 角色更冷回应（每剧情线一次，防重）
            if _r == 4:
                from app.scheduler.storyline_engine import run_dismissive_cold_reply
                try:
                    await run_dismissive_cold_reply(character_id, user_id)
                except Exception as _e:
                    _logger.warning("Dismissive cold reply call failed char=%d: %s", character_id, _e)
            # v5 增强 ③：关系恶化支线——用户持续敷衍（>=2 次）或冷战超长（>=6h）且占有维高
            try:
                from app.scheduler.storyline_engine import run_deteriorate_arc
                from app.scheduler.state_triggers import cold_war_deteriorate_triggered
                if await cold_war_deteriorate_triggered(character_id, user_id):
                    await run_deteriorate_arc(character_id, user_id)
            except Exception as _e2:
                _logger.warning("Deteriorate arc call failed char=%d: %s", character_id, _e2)
            _logger.info("Cold war block char=%d (no reply, level=%d)", character_id, _r)
            return True
    except Exception as e:
        _logger.warning("Cold war check failed char=%d: %s", character_id, e)
    return False


async def _persist_user_message(
    session_id: int, user_id: int, character_id: int, content: str,
    quote: dict | None = None, save_user_message: bool = True,
    shared_memory: bool = False,
) -> tuple[int | None, dict | None]:
    """用户消息落库，返回 (user_msg_id, user_msg_info)。

    save_user_message=False 跳过落库；shared_memory=True 触发 Shared Memory 标记（chunked）。
    """
    user_msg_id = None
    user_msg_info = None
    if not save_user_message:
        return user_msg_id, user_msg_info
    async with async_session_factory() as db:
        _q = None
        if quote and isinstance(quote, dict):
            _q = json.dumps({"quote": quote}, ensure_ascii=False)
        um = ChatMessage(session_id=session_id, sender_type="user", content=content,
                         extra_meta=_q)
        db.add(um)
        await db.flush()
        user_msg_id = um.id
        await db.commit()
        user_msg_info = {
            "id": um.id, "session_id": session_id, "sender_type": "user",
            "content": um.content, "created_at": um.created_at.isoformat(),
            "extra_meta": um.extra_meta,
        }
    # Shared Memory（Phase C，2026-08-14）：用户消息含“记住/第一次/纪念日”等标记意图 → 异步创建共同经历
    if shared_memory:
        try:
            import asyncio as _aio

            async def _mark_shared():
                try:
                    from app.memory.shared_events import maybe_create_shared_event
                    async with async_session_factory() as _db2:
                        await maybe_create_shared_event(_db2, user_id, character_id, content)
                except Exception as _e:
                    _logger.warning("shared event mark failed: %s", _e)

            _aio.ensure_future(_mark_shared())
        except Exception:
            pass
    return user_msg_id, user_msg_info


async def _run_agent_core(
    session_id: int, user_id: int, character_id: int, content: str,
    lang: str, user_msg_id: int | None,
    *,
    user_timer: bool = False,
    search_loop: bool = False,
    run_chat_task: bool = False,
    trace_route: str = "chunked",
    stream_sink=None,
    tts: bool = False,
    stream_tts_ctx: dict | None = None,
) -> dict | None:
    """公共 Agent 主流程（双路径收敛 #45，以 chunked 版逻辑为基准）。

    stream_sink 非空时注入 LangGraph state 走真流式生成（见 nodes.generate_response 的
    stream 分支）；流式块/增量经 sink 推给调用方。返回 None 表示冷战拦截；成功返回
    final_state/final_text/gen_prompt/img_text/cal_note_text/memo_text（+ streamed/stream_blocks）。
    """
    # 冷战拦截（v3）：不生成回复
    if await _cold_war_block(character_id, user_id, content):
        return None

    # 主动到期复习成功判定（P1）：用户回复与 24h 内复习消息弱相关 → 强化（异步不阻塞）
    try:
        from app.scheduler.memory_review import maybe_review_success
        import asyncio as _aio
        _aio.ensure_future(maybe_review_success(user_id, character_id, content))
    except Exception:
        pass

    # AI 情绪关怀：检测用户低落情绪 → 登记延迟主动关心任务（异步不阻塞）
    try:
        from app.utils.emotion import detect_user_emotion
        if "低落" in detect_user_emotion(content):
            from app.scheduler.emotion_care import register_care_task
            asyncio.ensure_future(register_care_task(user_id, character_id, content))
    except Exception:
        pass

    # 用户时间承诺解析（用户说"我去洗澡20分钟回来"等 → 创建定时事件，到点 AI 主动问；2026-08-14 修复）
    if user_timer:
        try:
            from app.scheduler.promise_parser import extract_timer
            from app.scheduler.promise_service import create_event
            user_timer_info = extract_timer(
                content, user_id=user_id, character_id=character_id,
                session_id=session_id, source_message_id=user_msg_id, sender="user",
            )
            if user_timer_info:
                await create_event(user_timer_info)
        except Exception as e:
            _logger.warning("User timer parse failed: %s", e)

    initial_state = {
        "user_message": content, "character_id": character_id,
        "user_id": user_id, "session_id": session_id, "intent": "",
        "retrieved_memories": [], "context_messages": [],
        "character_info": {}, "ai_response": "",
        "should_update_memory": False, "new_memories": [], "emotional_state": "",
        "bio_update": None, "status_update": None,
        "source_id": user_msg_id,
        "lang": lang,
        "reasoning_level": await _load_reasoning_level(character_id),
        "tools_used": [],
        "stream_sink": stream_sink,
        "tts": tts,
        "voice_params": (stream_tts_ctx or {}).get("voice_params", {}) if stream_tts_ctx else {},
        "tts_subdir": (stream_tts_ctx or {}).get("tts_subdir") if stream_tts_ctx else None,
        "block_sink": (stream_tts_ctx or {}).get("block_sink") if stream_tts_ctx else None,
    }

    _t0 = time.monotonic()
    final_state = await agent.ainvoke(initial_state)

    # 真流式标记 / 标记元数据源文本（流式时原始响应含全部标记，用于 trace/生图/日历/备忘提取）
    _is_stream = bool(final_state.get("streamed"))
    _source_text = final_state.get("raw_response") if _is_stream else (final_state.get("ai_response") or "")

    # Task Trace（Phase A，2026-08-16）：快照原始动作（标记未剥离前），流程结束统一落库（先只写不读）
    _trace_actions = _agent_actions.parse_actions(_source_text)
    _trace_llm_calls = 1
    _trace_searched = False

    full_text = final_state.get("ai_response") or ""
    _loop_steps: list[dict] = []

    if search_loop:
        # AI 自主搜索（Phase B，2026-08-16）：受控 Loop（decide→execute→observe→条件再决策；最多 2 次搜索/3 次 LLM）
        try:
            from app.agent import loop as _agent_loop

            async def _save_browser_history(_char_id: int, _query: str) -> None:
                """搜索成功落小手机浏览记录（角色记得自己搜过；同词刷新时间）"""
                from app.models.phone_desktop import BrowserHistory
                async with async_session_factory() as _db:
                    _ex = (await _db.execute(
                        select(BrowserHistory).where(
                            BrowserHistory.character_id == _char_id,
                            BrowserHistory.query == _query[:200],
                        )
                    )).scalar_one_or_none()
                    if _ex is not None:
                        _ex.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    else:
                        _db.add(BrowserHistory(character_id=_char_id, query=_query[:200]))
                    await _db.commit()

            final_state, _loop_steps = await _agent_loop.run_search_loop(
                final_state,
                user_id=user_id,
                character_id=character_id,
                run_search=_run_web_search,
                throttle=_search_throttle,
                inject_enabled=_search_inject_enabled,
                save_history=_save_browser_history,
            )
            _trace_searched = bool(_loop_steps)
            if _loop_steps:
                # 只有搜索成功才触发再决策（regen），失败轮次不计入 LLM 调用
                _trace_llm_calls = 1 + sum(1 for _s in _loop_steps if _s.get("ok"))
        except Exception as e:
            _logger.warning("AI web search loop failed: %s", e)
            try:
                final_state["ai_response"] = _extract_search(final_state.get("ai_response", ""))[0]
            except Exception:
                pass
        full_text = final_state.get("ai_response") or ""
    else:
        # AI 自主搜索（流式路径暂不触发，仅剥离标记兜底，避免打断流式输出）
        try:
            full_text = _extract_search(full_text)[0]
        except Exception:
            pass

    # MCP 工具执行（Phase 2）：LLM 输出 mcp.* 标记 → ToolRunner.execute + 再决策。
    # 仅在 context_builder 注入过 MCP 工具声明后才可能触发（默认无 mcp.* 标记 → 零行为变化）。
    # Phase 3（2026-08-27）评估：SSE 真流式路径仍不触发 run_mcp_tool_stage（与搜索一致），
    # 仅兜底剥离标记。原因：流式输出已推送给客户端（stream_blocks/stream_saved 已定型），
    # 此时再跑 MCP 工具 + _regen2 再决策会产出第二条回复但原 SSE 连接已结束，需额外推送通道，
    # 且会改变严格断言流式片段的现有测试契约。留待 Phase 4 单独接入后推送通道。
    # Phase 4（2026-08-28）评估结论：仍延期。真流式路径（_is_stream）若要执行 MCP 工具循环，
    # 需要 (a) 从 raw_response（非已剥离的 ai_response）解析 mcp.* 标记；
    # (b) 再决策 _regen2 会再次走流式（state 仍带 stream_sink），导致 delta 二次推送、
    #    stream_blocks/raw_response 被第二条回复覆盖，需额外拼接两段块/原始文本；
    # (c) tts 路径（block_sink 实时落库 + 逐句 TTS）在首条回复时已消费完毕，第二条回复需
    #    重开 TTS 流水线并再次落库，改动显著；且会改变 test_chat_stream 严格断言的事件序列。
    # 结论：维持现状（仅剥离 mcp.* 标记、不触发循环），待引入独立的流尾推送通道后再接入。
    if not _is_stream:
        try:
            from app.agent.mcp_tools import run_mcp_tool_stage
            _mcp_src = full_text or (final_state.get("ai_response") or "")
            _has_mcp = any(
                a.action_type.startswith("mcp.")
                for a in _agent_actions.parse_actions(_mcp_src)
            )
            if _has_mcp and await run_mcp_tool_stage(
                final_state, _loop_steps,
                user_id=user_id, character_id=character_id, session_id=session_id,
            ):
                from app.agent.nodes import generate_response as _regen2
                final_state = await _regen2(final_state)
                full_text = final_state.get("ai_response") or ""
                # 再决策输出如仍含 mcp.* 标记（内部调用标签）则剥离，避免泄漏到展示正文
                from app.agent.actions import _MCP_TOOL_RE as _mcp_re
                full_text = _mcp_re.sub("", full_text).strip()
        except Exception as e:
            _logger.warning("MCP tool stage failed: %s", e)
            try:
                from app.agent.actions import strip_actions as _strip_mcp
                full_text = _strip_mcp(full_text or "")
            except Exception:
                pass

    # 生图标记提取（聊天内AI发图）：清理标记后落库，异步生图
    # 提取源：流式用原始响应（展示文本已剥全标记）/ 非流式沿用 full_text（与旧行为一致），
    # 仅非流式回写 full_text，且从「已处理展示文本」剥离（避免经源文本回写时把已剥离的 SEARCH 等
    # 标记重新带回来）
    _marker_src = _source_text if _is_stream else full_text
    clean_text, gen_prompt, img_text = _extract_gen_image(_marker_src)
    if gen_prompt and not _is_stream:
        full_text = _extract_gen_image(full_text)[0]

    # Task Trace（Phase A）：写 trace（先只写不读；失败静默）
    try:
        from app.agent import trace as _trace
        _trace.enqueue_task_log(
            task_id=_trace.new_task_id(),
            character_id=character_id,
            user_id=user_id,
            session_id=session_id,
            trigger="chat",
            route=("search_loop" if _trace_searched else "direct") if search_loop else trace_route,
            steps_json=json.dumps(_agent_actions.actions_to_steps(_trace_actions) + _loop_steps, ensure_ascii=False),
            llm_calls=_trace_llm_calls,
            tool_calls=len(_trace_actions) + len(_loop_steps),
            latency_ms=int((time.monotonic() - _t0) * 1000),
            status="ok",
        )
    except Exception as _te:
        _logger.warning("Task trace failed: %s", _te)

    # Phase H：工具轮次任务化（≥1 个明确工具/备忘动作 → agent_tasks 任务记录；只记不改行为，失败静默；仅 HTTP 路径）
    if run_chat_task:
        try:
            from app.agent.task_engine import run_chat_task as _run_chat_task
            _all_steps = _agent_actions.actions_to_steps(_trace_actions) + _loop_steps
            if len(_all_steps) >= 1:
                asyncio.ensure_future(_run_chat_task(
                    character_id, user_id, session_id, content,
                    _all_steps, full_text, True,
                ))
        except Exception:
            pass

    # 日历/备忘录标记提取（Phase F 收口，2026-08-16）：提取结果供 chunked extra_meta 前端小字展示；
    # 落库统一走 _save_phone_desktop_notes（→ execute_tool，去重/署名），文本用原始含标记版本
    _cal_note_text = None
    _memo_text = None
    _marker_src2 = _source_text if _is_stream else full_text
    try:
        _cal = _extract_cal_note(_marker_src2)
        if _cal:
            _cal_note_text = _cal[1]
    except Exception:
        pass
    try:
        _memo_text = _extract_memo(_marker_src2)
    except Exception:
        _memo_text = None
    try:
        asyncio.ensure_future(_save_phone_desktop_notes(character_id, _marker_src2))
    except Exception:
        pass

    # 定时承诺解析（AI 侧）：检测 [timer:xx] 或"洗n分钟澡"等时间承诺 → 创建定时事件；
    # HTTP 路径额外剥离全部动作标记（记忆/自述/状态等），流式路径仅剥离定时标签
    try:
        from app.scheduler.promise_parser import extract_timer, strip_timer_tag
        from app.scheduler.promise_service import create_event
        timer_info = extract_timer(
            (_source_text if _is_stream else full_text),
            user_id=user_id, character_id=character_id,
            session_id=session_id, source_message_id=user_msg_id, sender="ai",
        )
        if search_loop:
            from app.agent.actions import strip_actions as _strip_actions
            full_text = strip_timer_tag(_strip_actions(full_text) or "") or ""
        else:
            full_text = strip_timer_tag(full_text)
        if timer_info:
            await create_event(timer_info)
    except Exception as e:
        _logger.warning("AI tag strip failed: %s", e)
    final_state["ai_response"] = full_text

    return {
        "final_state": final_state,
        "final_text": full_text,
        "gen_prompt": gen_prompt,
        "img_text": img_text,
        "cal_note_text": _cal_note_text,
        "memo_text": _memo_text,
        "streamed": _is_stream,
        "stream_blocks": final_state.get("stream_blocks") or [],
        "stream_saved": final_state.get("stream_saved") or [],
    }


def _schedule_reliability_fact_check(character_id: int, user_id: int, content: str, final_text: str) -> None:
    """P5：记忆可靠度信号（确认/纠正，$0 规则）+ 异步事实核查（节流，失败静默）。

    G-P2-1（2026-08-18）：HTTP 与 WS chunked 双路径共用的公共调用（纯异步 fire-and-forget，
    不阻塞推送；可靠性/事实核查结果只写元数据，不影响已推送文本）。
    """
    try:
        from app.memory.reliability import schedule_feedback_processing
        schedule_feedback_processing(character_id, user_id, content, final_text)
    except Exception:
        pass
    try:
        from app.memory.fact_check import schedule_fact_check
        schedule_fact_check(character_id, user_id, content, final_text)
    except Exception:
        pass


async def _run_post_processing(
    session_id: int, user_id: int, character_id: int, content: str,
    final_state: dict, final_text: str, ai_message_id: int | None,
    user_msg_id: int | None,
    *,
    reliability: bool = False,
    gen_prompt: str | None = None,
    img_text: str | None = None,
) -> None:
    """AI 回复落库后的公共收尾（反思/自述/状态/记忆/话题/复习/关系/状态评估/可靠度/生图；失败静默）。"""
    # 认知循环 v2.1：反思结果落库（节点已计算，拿第一条 AI 消息 id 记录；失败静默）
    try:
        from app.agent.reflection import persist_reflection
        if final_state.get("reflection_result") and ai_message_id:
            await persist_reflection(character_id, user_id, ai_message_id, final_state["reflection_result"])
    except Exception as e:
        _logger.warning("Reflection persist failed: %s", e)

    await _save_bio_update(character_id, final_state.get("bio_update"), user_id)
    await _save_status_update(character_id, final_state.get("status_update"), user_id)
    asyncio.ensure_future(_generate_initial_bio(character_id, user_id))

    # 触发记忆提取
    asyncio.ensure_future(add_chat_memory_extraction(
        session_id, character_id, user_id, content, final_text,
        source_id=user_msg_id,
    ))

    # 认知循环 v2.1：对话话题追踪（本地提取+节流；失败静默）
    try:
        from app.agent.topic_tracker import maybe_extract_topics
        asyncio.ensure_future(maybe_extract_topics(
            character_id, user_id, content, final_text,
            perception=final_state.get("perception"),
        ))
    except Exception:
        pass

    # 记忆架构 v2.1 Phase 4b：情境驱动复习——感知 deep/emotion 或命中进行中目标 → 入队候选（异步，失败静默）
    try:
        from app.scheduler.memory_review import queue_contextual_review_for
        asyncio.ensure_future(queue_contextual_review_for(
            character_id, user_id, content, final_state.get("perception"),
        ))
    except Exception:
        pass

    # 认知循环 v2.1：用户发消息 → 关系标量互动加分（bump，失败静默）
    asyncio.ensure_future(_bump_relationship(character_id, user_id))

    # 认知循环 v2.1：话题完成/搁置自动切换（本地零 LLM，失败静默）
    try:
        from app.agent.topic_tracker import update_topic_resolution
        asyncio.ensure_future(update_topic_resolution(character_id, user_id, content))
    except Exception:
        pass

    # 异步评估八维可视化状态（10 分钟节流，不阻塞回复）
    _trigger_state_eval(character_id, user_id, content, final_text, final_state.get("status_update"))

    # P5：记忆可靠度信号（确认/纠正，$0 规则）+ 异步事实核查（节流，失败静默；G-P2-1：HTTP 与 chunked 共用）
    if reliability:
        _schedule_reliability_fact_check(character_id, user_id, content, final_text)

    # 生图：用户要求画图时异步生成并追加图片消息（开关在 context 注入层控制标记输出）
    if gen_prompt:
        asyncio.ensure_future(_gen_image_flow(user_id, character_id, session_id, gen_prompt, img_text))


async def send_and_receive(
    session_id: int, user_id: int, character_id: int, content: str,
    lang: str = "zh", quote: dict | None = None,
) -> dict:
    """发送用户消息 → Agent 处理 → 返回 AI 回复"""
    # 用户消息落库（HTTP 路径：无开关，始终落库；无 user_msg_info/Shared Memory）
    user_msg_id, _ = await _persist_user_message(
        session_id, user_id, character_id, content,
        quote=quote, save_user_message=True, shared_memory=False,
    )

    # 公共 Agent 主流程（HTTP 专属：用户定时承诺 / 自主搜索 Loop / 多工具任务化）
    core = await _run_agent_core(
        session_id, user_id, character_id, content, lang, user_msg_id,
        user_timer=True, search_loop=True, run_chat_task=True,
    )
    if core is None:
        return {"ai_message": None, "memories_updated": False, "cold_war": True}

    final_state = core["final_state"]
    final_text = core["final_text"]
    gen_prompt = core["gen_prompt"]
    img_text = core["img_text"]

    # 组装 AI 消息 extra_meta：思考过程 + 调用能力（生图/扩展等）
    _meta = {}
    _reasoning = (final_state.get("reasoning") or "").strip()
    if _reasoning:
        _meta["reasoning"] = _reasoning
    _tools = list(final_state.get("tools_used") or [])
    if gen_prompt:
        _tools.append("生图")
    if _tools:
        _meta["tools"] = _tools
    # 状态更新附到气泡（前端小字显示，2026-08-14）
    _st = (final_state.get("status_update") or "").strip()
    if _st:
        _meta["status_update"] = _st
    _ai_meta = json.dumps(_meta, ensure_ascii=False) if _meta else None

    # AI 回复落库（HTTP 路径：单条 ChatMessage）
    async with async_session_factory() as db:
        ai_msg = ChatMessage(
            session_id=session_id, sender_type="ai", content=final_text,
            extra_meta=_ai_meta,
        )
        db.add(ai_msg)
        await db.flush()
        await db.commit()
        await db.refresh(ai_msg)

    await _push_user_notify(user_id, session_id, character_id, ai_msg.content)

    # 公共收尾（HTTP 专属：可靠度信号 + 异步事实核查）
    await _run_post_processing(
        session_id, user_id, character_id, content,
        final_state, final_text, ai_msg.id, user_msg_id,
        reliability=True,
        gen_prompt=gen_prompt, img_text=img_text,
    )

    _logger.info("AI response saved: msg_id=%d len=%d", ai_msg.id, len(ai_msg.content))
    return {
        "ai_message": {
            "id": ai_msg.id, "session_id": session_id, "sender_type": "ai",
            "content": ai_msg.content, "created_at": ai_msg.created_at.isoformat(),
            "extra_meta": ai_msg.extra_meta,
        },
        "memories_updated": final_state.get("should_update_memory", False),
    }


async def send_and_receive_chunked(
    session_id: int, user_id: int, character_id: int, content: str,
    save_user_message: bool = True, lang: str = "zh", tts: bool = False,
    quote: dict | None = None,
    extra_capabilities: list[str] | None = None,
) -> dict:
    """发送用户消息 -> Agent处理 -> 拆分回复 -> 保存每条块

    extra_capabilities: 外部链路标记的能力（如识图/文档问答），合并进 AI 回复的调用能力列表
    """
    # 用户消息落库（chunked 路径：save_user_message 开关 + user_msg_info 回传 + Shared Memory）
    user_msg_id, user_msg_info = await _persist_user_message(
        session_id, user_id, character_id, content,
        quote=quote, save_user_message=save_user_message, shared_memory=True,
    )

    # 公共 Agent 主流程（流式路径：不触发搜索，仅剥离标记兜底）
    core = await _run_agent_core(
        session_id, user_id, character_id, content, lang, user_msg_id,
    )
    if core is None:
        return {"chunks": [], "memories_updated": False, "cold_war": True}

    final_state = core["final_state"]
    full_text = core["final_text"]
    gen_prompt = core["gen_prompt"]
    img_text = core["img_text"]
    _cal_note_text = core["cal_note_text"]
    _memo_text = core["memo_text"]

    from app.agent.nodes import split_response
    chunks = split_response(full_text, final_state.get("emotional_state", ""))

    # AI 语音回复（TTS，仅语音对话场景）：edge-tts 云端免费，失败静默降级为纯文字
    tts_url = None
    if tts and full_text and chunks:
        try:
            gender = None
            voice = None
            voice_rate = None
            voice_pitch = None
            async with async_session_factory() as db:
                row = (await db.execute(
                    select(AICharacter.gender, AICharacter.voice, AICharacter.voice_rate, AICharacter.voice_pitch)
                    .where(AICharacter.id == character_id)
                )).first()
                if row:
                    gender, voice, voice_rate, voice_pitch = row
            from app.services.tts_service import synthesize
            tts_url = await synthesize(
                full_text, str(session_id),
                gender=gender, voice=voice, voice_rate=voice_rate, voice_pitch=voice_pitch,
                user_id=user_id,
            )
        except Exception as e:
            _logger.warning("TTS synthesis failed: %s", e)
            tts_url = None

    import json as _json
    # 首条块携带思考过程与调用能力（识图/文档/生图/语音回复/扩展）
    _meta = {}
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
    saved_chunks = []
    async with async_session_factory() as db:
        for idx, chunk in enumerate(chunks):
            meta = dict(_meta) if idx == 0 else None
            if idx == 0 and tts_url:
                meta = meta or {}
                meta["tts"] = {"url": tts_url}
            # 状态更新/日历备注/备忘附到最后一个气泡（前端小字显示，2026-08-14）
            if idx == len(chunks) - 1:
                _st = (final_state.get("status_update") or "").strip()
                if _st:
                    meta = meta or {}
                    meta["status_update"] = _st
                if _cal_note_text:
                    meta = meta or {}
                    meta["cal_note"] = _cal_note_text
                if _memo_text:
                    meta = meta or {}
                    meta["memo"] = _memo_text
            m = ChatMessage(session_id=session_id, sender_type="ai", content=chunk,
                            extra_meta=_json.dumps(meta, ensure_ascii=False) if meta else None)
            db.add(m)
            await db.flush()
            await db.refresh(m)
            chunk_item = {
                "id": m.id, "session_id": session_id, "sender_type": "ai",
                "content": m.content, "created_at": m.created_at.isoformat(),
                "extra_meta": m.extra_meta,
            }
            if idx == 0 and tts_url:
                chunk_item["tts_url"] = tts_url
            saved_chunks.append(chunk_item)
        await db.commit()

    await _push_user_notify(user_id, session_id, character_id, full_text)

    # 公共收尾（流式路径：G-P2-1，可靠度/事实核查与 HTTP 同一入口、同一参数；纯异步调度不阻塞推送）
    await _run_post_processing(
        session_id, user_id, character_id, content,
        final_state, full_text, saved_chunks[0]["id"] if saved_chunks else None, user_msg_id,
        reliability=True,
        gen_prompt=gen_prompt, img_text=img_text,
    )

    _logger.info("Chunked: %d chunks from %d chars", len(saved_chunks), len(full_text))
    return {"chunks": saved_chunks, "memories_updated": final_state.get("should_update_memory", False),
            "user_message": user_msg_info}


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
            stream_sink=sink, tts=tts, stream_tts_ctx=tts_ctx,
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


async def continue_chat(
    session_id: int, user_id: int, character_id: int, last_message_id: int,
    lang: str = "zh",
) -> dict:
    # 冷战拦截（v3）：继续对话也属于"发消息"，冷战期不回复
    if await _cold_war_block(character_id, user_id, ""):
        return {"chunks": [], "memories_updated": False, "cold_war": True}
    """AI 连续回复：复用完整 Agent 流程（角色卡/记忆/朋友圈注入），以角色身份延续上一句话"""
    import re as _re

    # 取上一条 AI 消息内容（优先 last_message_id，回退会话最后一条 AI 消息），供检索与延续指令使用
    last_ai_content = ""
    try:
        async with async_session_factory() as db:
            msg_row = None
            if last_message_id and last_message_id > 0:
                result = await db.execute(
                    select(ChatMessage).where(
                        ChatMessage.id == last_message_id,
                        ChatMessage.session_id == session_id,
                    )
                )
                msg_row = result.scalar_one_or_none()
            if msg_row is None or not (msg_row.content or "").strip():
                result = await db.execute(
                    select(ChatMessage)
                    .where(
                        ChatMessage.session_id == session_id,
                        ChatMessage.sender_type == "ai",
                    )
                    .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                    .limit(1)
                )
                msg_row = result.scalar_one_or_none()
            if msg_row and (msg_row.content or "").strip():
                last_ai_content = msg_row.content[:500]
    except Exception as _e:
        _logger.warning("Continue load last ai message failed: %s", _e)

    initial_state = {
        # 用户位只放占位（无新输入）；真正的继续指令由 context_builder 注入 system 区
        "user_message": "（用户没有说话，等你继续）",
        "continue_payload": {
            "last_ai_content": last_ai_content,
        },
        "character_id": character_id,
        "user_id": user_id,
        "session_id": session_id,
        "intent": "",
        "retrieved_memories": [],
        "context_messages": [],
        "character_info": {},
        "ai_response": "",
        "should_update_memory": False,
        "new_memories": [],
        "emotional_state": "",
        "bio_update": None,
        "status_update": None,
        "source_id": None,
        "lang": lang,
        "reasoning_level": await _load_reasoning_level(character_id),
        "tools_used": [],
    }
    final_state = await agent.ainvoke(initial_state)
    full_text = (final_state.get("ai_response") or "").strip()
    if not full_text:
        full_text = "……"

    # 清理可能残留的标记（记忆/自述/状态），避免出现在聊天内容里
    full_text = _re.sub(
        r"\s*[\[【]\s*(?:记忆|自述更新|自述删除|状态更新)\s*[：:].*?[\]】]\s*",
        "", full_text,
    ).strip()

    # 定时承诺解析（继续时若承诺了时间，同样生效）
    try:
        from app.scheduler.promise_parser import extract_timer, strip_timer_tag
        timer_info = extract_timer(
            full_text, user_id=user_id, character_id=character_id,
            session_id=session_id, source_message_id=None, sender="ai",
        )
        full_text = strip_timer_tag(full_text)
        if timer_info:
            from app.scheduler.promise_service import create_event
            await create_event(timer_info)
    except Exception as e:
        _logger.warning("Continue timer parse failed: %s", e)

    # 自述/状态更新落库
    try:
        await _save_bio_update(character_id, final_state.get("bio_update"), user_id)
        await _save_status_update(character_id, final_state.get("status_update"), user_id)
    except Exception as e:
        _logger.warning("Continue bio/status update failed: %s", e)

    # 保存消息 + 更新会话时间戳（携带思考过程/调用能力）
    _cmeta = {}
    _creasoning = (final_state.get("reasoning") or "").strip()
    if _creasoning:
        _cmeta["reasoning"] = _creasoning
    _ctools = list(final_state.get("tools_used") or [])
    if _ctools:
        _cmeta["tools"] = _ctools
    _cai_meta = json.dumps(_cmeta, ensure_ascii=False) if _cmeta else None
    async with async_session_factory() as db:
        ai_msg = ChatMessage(session_id=session_id, sender_type="ai", content=full_text,
                             extra_meta=_cai_meta)
        db.add(ai_msg)
        await db.flush()
        result = await db.execute(
            select(ChatSession).where(ChatSession.id == session_id)
        )
        session = result.scalar_one_or_none()
        if session:
            session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)  # 2026-08-16 审计：与库内 naive UTC 一致
        await db.commit()
        await db.refresh(ai_msg)

    _logger.info("Continue chat: session=%d msg_id=%d", session_id, ai_msg.id)
    return {
        "id": ai_msg.id, "session_id": session_id, "sender_type": "ai",
        "content": ai_msg.content, "created_at": ai_msg.created_at.isoformat(),
        "extra_meta": ai_msg.extra_meta,
    }


async def get_owned_session(db, session_id: int, user_id: int):
    """按用户归属获取会话，不存在或非本人返回 None（api/chat.py 公共依赖）"""
    result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session or session.user_id != user_id or not session.is_active:
        return None
    return session


async def get_unread_counts(db, user_id: int) -> list[dict]:
    """每个角色的未读 AI 消息数（一条 GROUP BY 查询，替代逐会话 COUNT 的 N+1）"""
    result = await db.execute(
        select(
            ChatSession.id,
            ChatSession.character_id,
            func.count(ChatMessage.id).label("cnt"),
        )
        .outerjoin(
            ChatMessage,
            and_(
                ChatMessage.session_id == ChatSession.id,
                ChatMessage.sender_type == "ai",
                or_(
                    ChatSession.last_read_at.is_(None),
                    ChatMessage.created_at > ChatSession.last_read_at,
                ),
            ),
        )
        .where(ChatSession.user_id == user_id, ChatSession.is_active == True)
        .group_by(ChatSession.id, ChatSession.character_id)
    )
    rows = result.all()
    return [
        {"session_id": r.id, "character_id": r.character_id, "count": r.cnt}
        for r in rows
        if r.cnt > 0
    ]


async def mark_session_read(db, session_id: int, user_id: int) -> bool:
    """标记会话已读（联动同角色其他活跃会话，避免残留未读）；会话不存在返回 False。

    历史遗留：同一角色可能残留多个活跃会话，点进聊天页时一并标记已读。
    用原生 SQL 更新，避免 SQLAlchemy ORM 会话自动附加 updated_at=onupdate（CURRENT_TIMESTAMP）
    污染 updated_at，导致"按最新会话复用"选错（聊天记录只剩早期的问题）。
    """
    from sqlalchemy import text

    session = await get_owned_session(db, session_id, user_id)
    if session is None:
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # 库内统一 naive UTC
    await db.execute(
        text(
            "UPDATE chat_sessions SET last_read_at = :t "
            "WHERE user_id = :u AND character_id = :c AND is_active = 1"
        ),
        {"t": now, "u": session.user_id, "c": session.character_id},
    )
    await db.commit()
    return True
