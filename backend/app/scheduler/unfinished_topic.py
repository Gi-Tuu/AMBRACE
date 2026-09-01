"""对话未收尾跟进：识别「下次/改天/有空」等未完成信号，AI 自然捡起话题（2026-08-12）

- 扫描各角色最新会话：用户最后一条消息含未收尾信号词 → 产出跟进候选
- 排除告别场景（下次聊/下次再见等）与 AI 已跟进的情况
- 每角色每日最多 1 次，距上次对话至少 MIN_GAP_MINUTES
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.database import async_session_factory
from app.models.chat_message import ChatMessage
from app.models.proactive_settings import ProactiveMessageLog
from app.scheduler.triggers import get_active_characters, get_latest_session
from app.utils.logger import get_logger

_logger = get_logger("scheduler.unfinished_topic")

# 未收尾信号词：用户消息中出现视为抛了话头
UNFINISHED_KEYWORDS = ("下次", "以后", "改天", "有空", "回头", "到时候", "再聊", "晚点", "找时间")
# 告别/结束语组合：命中则不视为未收尾（避免把「下次聊」当话头）
EXCLUDE_PATTERNS = (
    "下次聊", "下次再聊", "下次见", "下次再说", "下次说", "下次找", "下次一定",
    "以后再说", "以后聊", "以后见", "改天聊", "改天再说", "改天见",
    "有空再聊", "有空聊", "回头聊", "回头再说", "回头见", "晚点再说",
)
# 距上次对话至少间隔（分钟）：给用户留缓冲，避免刚说完就追问
MIN_GAP_MINUTES = 120
# 每角色每日最多跟进次数
MAX_DAILY = 1


async def _used_today(character_id: int) -> bool:
    """今天（北京时间）该角色是否已跟进过未收尾话题"""
    now_cn = datetime.now(timezone(timedelta(hours=8)))
    start_cn = now_cn.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_cn.astimezone(timezone.utc).replace(tzinfo=None)
    async with async_session_factory() as db:
        n = (
            await db.execute(
                select(func.count()).where(
                    ProactiveMessageLog.character_id == character_id,
                    ProactiveMessageLog.message_type == "unfinished_topic",
                    ProactiveMessageLog.created_at >= start_utc,
                )
            )
        ).scalar() or 0
    return n >= MAX_DAILY


async def collect_unfinished_events() -> list[dict]:
    """扫描各角色最新会话：用户最后一条消息含未收尾信号 → 产出跟进候选"""
    events = []
    chars = await get_active_characters()
    for c in chars:
        try:
            char_id = c["character_id"]
            user_id = c["user_id"]
            if await _used_today(char_id):
                continue
            session = await get_latest_session(char_id, user_id)
            if not session:
                continue
            async with async_session_factory() as db:
                last_user = (
                    await db.execute(
                        select(ChatMessage.content, ChatMessage.created_at)
                        .where(
                            ChatMessage.session_id == session["id"],
                            ChatMessage.sender_type == "user",
                        )
                        .order_by(ChatMessage.created_at.desc())
                        .limit(1)
                    )
                ).first()
                last_msg_at = (
                    await db.execute(
                        select(func.max(ChatMessage.created_at)).where(
                            ChatMessage.session_id == session["id"]
                        )
                    )
                ).scalar()
            if not last_user or not last_msg_at:
                continue
            content = (last_user[0] or "").strip()
            if len(content) < 4:
                continue
            if any(kw in content for kw in EXCLUDE_PATTERNS):
                continue
            if not any(kw in content for kw in UNFINISHED_KEYWORDS):
                continue
            # 距最后一条消息足够久（用户已离开对话）
            if isinstance(last_msg_at, datetime):
                t = last_msg_at if last_msg_at.tzinfo else last_msg_at.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - t < timedelta(minutes=MIN_GAP_MINUTES):
                    continue
            events.append({
                "type": "unfinished_topic",
                "priority": 3,
                "candidate": {
                    "character_id": char_id,
                    "user_id": user_id,
                    "session_id": session["id"],
                    "character_name": c["character_name"],
                    "character_personality": c["character_personality"],
                    "nickname": c["nickname"] or c["username"],
                    "unfinished_content": content[:120],
                },
            })
        except Exception as e:
            _logger.warning("unfinished collect failed char=%s: %s", c.get("character_id"), e)
    return events


async def run_unfinished_topic(candidate: dict) -> bool:
    """生成「自然捡起话题」消息并发送；失败静默"""
    char_id = candidate["character_id"]
    session_id = candidate["session_id"]
    user_id = candidate["user_id"]
    topic = candidate.get("unfinished_content", "")
    try:
        from app.agent.llm_client import chat_completion
        from app.models.character import AICharacter
        from app.agent.user_profile import build_role_prompt_block
        from app.scheduler import scheduler as engine
        async with async_session_factory() as db:
            char = await db.get(AICharacter, char_id)
        char_name = char.name if char else "我"
        identity = ""
        if char:
            try:
                identity = await build_role_prompt_block(char, user_id) + "\n"
            except Exception:
                identity = f"你是{char_name}，性格{char.personality or '友善'}。\n"
        hint = (
            f"{identity}"
            f"你是{char_name}，用户之前说过：「{topic}」，像是在约下次或留了话头。"
            "请自然地捡起这个话题（例如'对了，你上次说的那个……'），1-2 句话，"
            "按你的性格和聊天风格来说，不要太刻意，不要提'AI'，不要加引号标注。"
        )
        msg = await chat_completion(
            messages=[
                {"role": "system", "content": "直接输出内容，不要加引号和标注。"},
                {"role": "user", "content": hint},
            ],
            temperature=0.85,
            max_tokens=256,
            task="message",
        )
        msg = (msg or "").strip().strip('"').strip("'")
        if len(msg) < 2:
            return False
        await engine.send_to_session(
            session_id, char_id, user_id, msg, message_type="unfinished_topic",
        )
        _logger.info("Unfinished topic followed up char=%d", char_id)
        return True
    except Exception as e:
        _logger.warning("unfinished run failed char=%s: %s", char_id, e)
        return False
