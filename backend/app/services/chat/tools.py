"""AI 工具/标记层：从 chat_service 拆分出的纯工具函数（AMBRACE 重构步骤 2）。

包含标记解析（_extract_*）、搜索（_polish_search_query/_run_web_search/_search_*）、
小手机日历/备忘落库（_save_*_note/_execute_note_tool/_save_phone_desktop_notes）、
生图流程（_gen_image_flow）。

设计约定：需要 services 引用时使用函数内（懒）import，避免包内/模块间循环。
"""
import asyncio
import json
import re
import time

from app.utils.logger import get_logger
from app.db.database import async_session_factory
from app.agent import actions as _agent_actions

_logger = get_logger("services.chat")

# 进程级节流：每用户 60 秒最多触发 1 次搜索（防滥用，LLM 一轮最多 1 次）
_SEARCH_THROTTLE: dict[int, float] = {}


def _extract_gen_image(text: str) -> tuple[str, str | None, str | None]:
    """提取生图标记，返回 (清理后的文本, 画面描述或None, 图片消息文案或None)。

    支持 [IMG_TEXT]...[/IMG_TEXT]（角色随回复一起生成图片消息文案，符合人设）；
    未输出文案时返回 None，由调用方用兜底文案。
    实现已收敛到统一解析层 app.agent.actions（Phase A，行为零变化）。
    """
    return _agent_actions.extract_gen_image(text)


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
        from app.models.device import CalendarNote
        from sqlalchemy import select as _sa_select
        from app.models.device import CalendarNote as _CalNote
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
        from app.models.device import MemoNote as _MemoNote2
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

    AMBRACE 步骤 8：执行入口由 app/tools/builtin 的内置工具提供（execute 惰性 import services 落库），
    这里直接 get_tool + execute_tool，不再自建 _run 闭包；本地能力 scope=None 直接执行
    （无权限门禁）；仍触发工具生命周期钩子与异常隔离。
    """
    from app.agent import tools as _tools
    from app.agent.tool_runner import execute_tool
    _spec = _tools.get_tool(tool_name)
    if _spec is None:
        return
    await execute_tool(_spec, payload, user_id=None, character_id=character_id)


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
    # 消息 IO 层位于聊天的无副作用 io 层（chat/io.py），懒 import 避免循环
    from app.services.chat.io import _append_ai_image_message, _append_ai_text_message
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
