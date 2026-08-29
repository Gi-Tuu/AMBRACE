"""AI 主动提通知：手机后台服务定时上报通知缓存 → 服务器对比新增 → 节流触发 AI 主动消息
- 首次上报只建立基线（不打扰），之后有"新通知"且距上次提及 >=30 分钟才触发
- 北京时间 23:00-08:00 免打扰时段跳过；用户说过睡觉跳过
"""
import hashlib
import json
from datetime import datetime, timezone, timedelta

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.phone_auto_state import PhoneAutoState
from app.models.character import AICharacter
from app.utils.logger import get_logger

_logger = get_logger("services.phone_auto_notify_service")

MIN_TRIGGER_INTERVAL_MINUTES = 30
QUIET_START_HOUR = 23
QUIET_END_HOUR = 8
MAX_MENTION_ITEMS = 3


def _fingerprint(item: dict) -> str:
    raw = "|".join([
        str(item.get("package") or ""),
        str(item.get("title") or ""),
        str(item.get("text") or ""),
    ])
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _in_quiet_hours() -> bool:
    now_cn = datetime.now(timezone(timedelta(hours=8)))
    return now_cn.hour >= QUIET_START_HOUR or now_cn.hour < QUIET_END_HOUR


async def _load_or_create_state(user_id: int) -> tuple[PhoneAutoState | None, bool]:
    async with async_session_factory() as db:
        result = await db.execute(select(PhoneAutoState).where(PhoneAutoState.user_id == user_id))
        state = result.scalar_one_or_none()
        if state is None:
            state = PhoneAutoState(user_id=user_id)
            db.add(state)
            await db.commit()
            await db.refresh(state)
        return state, True


async def _save_state(state: PhoneAutoState, fingerprints: list[str], triggered: bool):
    async with async_session_factory() as db:
        db_state = await db.get(PhoneAutoState, state.id)
        if db_state is None:
            return
        db_state.fingerprints = json.dumps(fingerprints, ensure_ascii=False)
        db_state.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if triggered:
            db_state.last_trigger_at = db_state.updated_at
        await db.commit()


async def _select_character(user_id: int) -> tuple | None:
    """选该用户最近有会话的活跃角色。返回 (char, session, session_id) 或 None。"""
    from app.models.chat_session import ChatSession
    async with async_session_factory() as db:
        result = await db.execute(
            select(AICharacter).where(
                AICharacter.user_id == user_id,
                AICharacter.is_active == True,
            )
        )
        chars = result.scalars().all()
    if not chars:
        return None
    char_ids = [c.id for c in chars]
    async with async_session_factory() as db:
        result = await db.execute(
            select(ChatSession)
            .where(ChatSession.user_id == user_id, ChatSession.is_active == True, ChatSession.character_id.in_(char_ids))
            .order_by(ChatSession.updated_at.desc())
            .limit(1)
        )
        session = result.scalar_one_or_none()
    if session is None:
        return None  # 统一返回 None
    char = next((c for c in chars if c.id == session.character_id), None)
    if char is None:
        return None
    return char, session, session.id


async def _generate_mention(char, items: list[dict]) -> str:
    from app.agent.llm_client import chat_completion
    lines = []
    for it in items:
        app = it.get("app") or it.get("package") or "应用"
        title = (it.get("title") or "").strip()
        text = (it.get("text") or "").strip()
        body = "：".join(x for x in [title, text] if x)
        lines.append(f"- {app}：{body[:60]}")
    prompt = (
        f"你是{char.name}，性格{char.personality or '友善'}，聊天风格{char.chat_style or '自然'}。你通过用户授权感知到，"
        "用户手机最近收到了这些通知：\n" + "\n".join(lines) + "\n\n"
        "请像朋友一样自然地提起其中最值得聊的一条（1-2句话），带点关心或调侃，口语化，"
        "语气符合你的性格和聊天风格。"
        "不要罗列所有通知，不要提'通知监听''手机感知'等技术词，就当你在关心TA手机上的事。直接输出内容。"
    )
    try:
        response = await chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8, max_tokens=120, task="message",
        )
        text = (response or "").strip().strip('"').strip("'").strip("「").strip("」")
        return text if len(text) >= 4 else ""
    except Exception as e:
        _logger.warning("Notification mention LLM failed: %s", e)
        return ""


async def handle_auto_report(user_id: int, notifications: list[dict]) -> dict:
    """处理手机端主动上报的通知缓存。返回 {triggered: bool, new_count: int}"""
    items = [n for n in notifications if (n.get("title") or n.get("text") or "").strip()]
    if not items:
        return {"triggered": False, "new_count": 0}

    fingerprints = [_fingerprint(n) for n in items]
    state, _ = await _load_or_create_state(user_id)
    prev = set()
    if state and state.fingerprints:
        try:
            prev = set(json.loads(state.fingerprints))
        except Exception:
            prev = set()

    new_fps = [f for f in fingerprints if f not in prev]
    new_count = len(new_fps)

    # 首次上报（无基线）只建基线不触发；无新增不触发
    if not prev:
        _logger.info("Phone auto notify baseline built user=%d count=%d", user_id, new_count)
    elif not new_fps:
        _logger.info("Phone auto notify no new user=%d", user_id)
    triggered = False
    if prev and new_fps:
        # 节流 + 时段 + 睡眠检查
        if _in_quiet_hours():
            _logger.info("Phone auto notify skipped: quiet hours user=%d", user_id)
        else:
            if state and state.last_trigger_at:
                last = state.last_trigger_at
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - last < timedelta(minutes=MIN_TRIGGER_INTERVAL_MINUTES):
                    _logger.info("Phone auto notify throttled user=%d", user_id)
                else:
                    triggered = True
            else:
                triggered = True

    if triggered:
        try:
            await _persist_notification_snapshots(user_id, items)
        except Exception as e:
            _logger.warning("Phone auto notify snapshot persist failed: %s", e)
        try:
            await _trigger_mention(user_id, items[:MAX_MENTION_ITEMS])
        except Exception as e:
            _logger.warning("Phone auto notify trigger failed: %s", e)
            triggered = False

    if state:
        await _save_state(state, fingerprints, triggered)
    return {"triggered": triggered, "new_count": new_count}



async def _persist_notification_snapshots(user_id: int, items: list[dict]):
    """触发时把通知写入 phone_snapshots（source=notification），供聊天上下文注入引用"""
    from app.models.phone_snapshot import PhoneSnapshot
    from sqlalchemy import delete as sa_delete
    async with async_session_factory() as db:
        for it in items:
            title = (it.get("title") or "").strip()
            text = (it.get("text") or "").strip()
            body = "：".join(x for x in [title, text] if x)
            if not body:
                continue
            db.add(PhoneSnapshot(user_id=user_id, source="notification", content=body[:500]))
        await db.commit()
        # 每用户只保留最近 20 条
        from sqlalchemy import select as sa_select
        old_ids = (
            await db.execute(
                sa_select(PhoneSnapshot.id)
                .where(PhoneSnapshot.user_id == user_id)
                .order_by(PhoneSnapshot.created_at.desc())
                .offset(20)
            )
        ).scalars().all()
        if old_ids:
            await db.execute(sa_delete(PhoneSnapshot).where(PhoneSnapshot.id.in_(old_ids)))
        await db.commit()

async def _trigger_mention(user_id: int, items: list[dict]):
    picked = await _select_character(user_id)
    if picked is None:
        return
    char, session, session_id = picked

    # 用户说过睡觉 → 不打扰
    try:
        from app.scheduler.arbiter import has_user_said_sleep
        if await has_user_said_sleep(char.id, user_id):
            _logger.info("Phone auto notify skipped: user sleep char=%d", char.id)
            return
    except Exception:
        pass

    content = await _generate_mention(char, items)
    if not content:
        return

    from app.scheduler import scheduler as engine
    await engine.send_to_session(
        session_id, char.id, user_id, content, message_type="notification_mention",
    )
    _logger.info("Phone auto notify triggered: char=%d session=%d user=%d", char.id, session_id, user_id)
