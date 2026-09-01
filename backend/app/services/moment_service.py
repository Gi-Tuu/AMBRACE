"""朋友圈统一服务层：发布 + 评论 + 点赞 + 归档 + 清理"""
import random
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func
from app.models.life import AIMoment, MomentAILike, MomentComment
from app.db.database import async_session_factory
from app.models.character import AICharacter
from app.models.chat import ChatSession
from app.models.chat import ChatMessage
from app.models.character import ProactiveSettings
from app.agent.llm_client import chat_completion
from app.agent.user_profile import build_user_profile_text
from app.utils.logger import get_logger

_logger = get_logger("services.moment")


from app.utils.timeutil import beijing_day_start_utc as _beijing_day_start_utc


async def build_moment_prompt(char, extra_hint: str = "") -> str:
    """构建带上下文的朋友圈生成提示词（当前状态 + 最近私聊 + 上一条朋友圈 + 北京时间）

    extra_hint: 状态触发等场景注入的额外情绪提示（如怒气状态描述），为空则不影响原逻辑。
    """
    recent_chat = ""
    async with async_session_factory() as db:
        from app.services.chat_service import get_latest_session_id
        session_id = await get_latest_session_id(char.user_id, char.id)
        session = await db.get(ChatSession, session_id) if session_id else None
        if session:
            msg_result = await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session.id)
                .order_by(ChatMessage.created_at.desc())
                .limit(6)
            )
            msgs = list(reversed(msg_result.scalars().all()))
            lines = []
            for m in msgs:
                sender = "用户" if m.sender_type == "user" else char.name
                lines.append(f"{sender}: {(m.content or '')[:80]}")
            recent_chat = "\n".join(lines)

    last_moment = ""
    async with async_session_factory() as db:
        last_result = await db.execute(
            select(AIMoment)
            .where(AIMoment.character_id == char.id, AIMoment.sender_type == "ai")
            .order_by(AIMoment.id.desc())
            .limit(1)
        )
        lm = last_result.scalar_one_or_none()
        if lm:
            last_moment = lm.content[:100]

    now = datetime.now(timezone(timedelta(hours=8)))
    time_str = f"{now.year}\u5e74{now.month}\u6708{now.day}\u65e5 {now.hour}:{now.minute:02d}"
    status = char.current_status or "正在和用户聊天"

    # 宠物动态（Phase 2，方案 A）：角色发朋友圈时注入宠物信息，可自然提到宠物
    pets_line = ""
    try:
        from app.models.pet import Pet
        from app.services.pet_service import SPECIES_META
        async with async_session_factory() as db:
            pr = await db.execute(
                select(Pet).where(Pet.user_id == char.user_id, Pet.owner_type.is_(None)).limit(2)
            )
            pets = pr.scalars().all()
        if pets:
            parts = []
            for p in pets:
                label = SPECIES_META.get((p.species or "").strip().lower(), {}).get("label", "小动物")
                parts.append(f"{p.name}（{label}，{p.status_text or '精神满满'}）")
            pets_line = "你养的宠物：" + "、".join(parts) + "\n"
    except Exception:
        pets_line = ""

    try:
        from app.agent.user_profile import build_user_profile_text, build_relation_line
        user_profile = await build_user_profile_text(char.user_id or 1)
        relation_line = await build_relation_line(char)
    except Exception:
        user_profile = ""
        relation_line = ""

    # 天气注入（发朋友圈可自然提及当地天气）
    weather_line = ""
    try:
        from app.services.weather_service import get_user_weather_line
        weather_line = await get_user_weather_line(char.user_id or 1)
    except Exception:
        weather_line = ""

    return (
        f"你是{char.name}，请在朋友圈发一条文字动态。\n"
        f"你的性格：{char.personality or ''}\n"
        f"你的自我介绍：{char.bio or ''}\n"
        f"当前时间：{time_str}（北京时间）\n"
        + (f"{weather_line}\n" if weather_line else "")
        + f"你当前的状态：{status}\n"
        f"你和用户的关系：{relation_line or '普通朋友'}\n"
        f"用户画像（不要混淆你和用户的身份）：{user_profile or '用户昵称: 用户'}\n"
        f"最近和用户的聊天：\n{recent_chat or '（暂无）'}\n"
        f"你上一条朋友圈：{last_moment or '（暂无）'}\n"
        f"{pets_line}"
        "\n"
        + (f"本次情绪提示：{extra_hint}\n\n" if extra_hint else "")
        + "动态要求：\n"
        "- 像真人发朋友圈一样，分享此刻的想法、心情或日常\n"
        "- 内容必须和你当前的状态、最近的经历一致，不要写与当前状态矛盾的事（例如状态是'准备出门'就不要写'刚爬完山回来'）\n"
        "- 只分享真实发生过的事或此刻的感受；计划/猜测的事用'打算/可能'表达，不要编造没发生的事\n"
        "- 不要重复上一条朋友圈的主题或内容\n"
        "- 可以是有趣的事、感慨、吐槽、或者一句有意义的话\n"
        "- 不要带话题标签，不要用「分享」这类词开头\n"
        "- 不要用'今天/昨天/最近'等相对时间词；若涉及时间写具体日期（当前时间已在上面给出）\n"
        "- 20-80字左右\n"
        "- 直接输出内容，不要加引号"
    )


_USER_REPLY_BLOCKED_KEYWORDS = ["我无法", "我不能", "没有权限", "我不确定", "我不清楚", "我不知道"]

# ── 发布 ──

async def publish_moment(character_id: int, skip_interval: bool = False, extra_hint: str = "") -> dict | None:
    """为角色发布一条朋友圈，返回动态数据或 None（已达上限）

    Args:
        character_id: 角色ID
        skip_interval: True 跳过时间间隔检查（手动发布）
        extra_hint: 额外情绪提示（状态触发场景注入，透传 build_moment_prompt）
    """
    async with async_session_factory() as db:
        char_result = await db.execute(select(AICharacter).where(AICharacter.id == character_id, AICharacter.is_active == True))
        char = char_result.scalar_one_or_none()
        if not char:
            _logger.warning("Publish failed: char %d not found or inactive", character_id)
            return None

        # 检查每日上限（3条）
        day_start = _beijing_day_start_utc()
        count_result = await db.execute(
            select(func.count()).where(
                AIMoment.character_id == character_id,
                AIMoment.sender_type == "ai",
                AIMoment.created_at >= day_start,
            )
        )
        daily_count = count_result.scalar() or 0
        if daily_count >= 3:
            _logger.info("Publish skipped: char %d daily limit reached (3)", character_id)
            return None

        # 检查时间间隔（手动发布跳过）
        if not skip_interval:
            last_result = await db.execute(
                select(AIMoment)
                .where(AIMoment.character_id == character_id, AIMoment.sender_type == "ai")
                .order_by(AIMoment.created_at.desc())
                .limit(1)
            )
            last = last_result.scalar_one_or_none()
            if last:
                last_ts = last.created_at
                if last_ts is not None and last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds()
                if elapsed < 7200:  # 2小时
                    _logger.debug("Publish skipped: interval < 2h for char %d", character_id)
                    return None

    # 生成内容
    content = await _generate_moment_content(await build_moment_prompt(char, extra_hint), char.name)
    if not content or len(content) < 5:
        return None

    async with async_session_factory() as db:
        moment = AIMoment(
            character_id=character_id,
            user_id=char.user_id or 1,  # 审计第三批 P2-05：防角色归属缺失写 NULL（与事件发布同源兜底）
            sender_type="ai",
            content=content,
        )
        db.add(moment)
        await db.commit()
        await db.refresh(moment)
        _logger.info("Moment published: char=%d content=%.40s", character_id, content)
        # 事件发布（2026-08-14 演进规划 v2 Phase A）：朋友圈发布成功广播 life.moment_published
        try:
            from app.events import publish
            from app.events.schema import make_event
            _evt = make_event(
                "life.moment_published",
                speaker={"type": "character", "id": character_id},
                target={"type": "user", "id": char.user_id or 1},
                audience=["public"],  # 朋友圈 = 公开事件
                provenance={"origin": "social_event"},
                data={
                    "user_id": char.user_id or 1,
                    "character_id": character_id,
                    "moment_id": moment.id,
                    "content": (content or "")[:200],
                },
            )
            publish("life.moment_published", _evt)
        except Exception:
            pass

    # 自动存入记忆
    try:
        from app.memory import save_memory
        await save_memory(
            user_id=char.user_id or 1,
            character_id=character_id,
            memory_type="insight",
            content=f"发了一条朋友圈（{datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')}）: {content[:100]}",
            importance=1,
            sub_type="moment",
            source="moment",
            speaker_id=character_id, speaker_type="character", epistemic_status="FACT",
        )
    except Exception as e:
        _logger.warning("Failed to save moment as memory: %s", e)

    return {
        "id": moment.id,
        "character_id": character_id,
        "character_name": char.name,
        "content": content,
        "created_at": moment.created_at.isoformat(),
    }


async def _generate_moment_content(prompt: str, char_name: str, max_retries: int = 2) -> str:
    """调用 LLM 生成朋友圈内容（prompt 由 build_moment_prompt 构建）"""
    for attempt in range(max_retries):
        try:
            response = await chat_completion(
                messages=[
                    {"role": "system", "content": f"你是{char_name}，正在发朋友圈。直接输出动态内容，不要说'我发了一条朋友圈'这类话。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.9,
                max_tokens=400,  # v4-flash 推理模型留配额
                task="message",
            )
            content = response.strip().strip('"').strip("'").strip()
            if len(content) >= 5 and len(content) <= 1000:
                return content
            _logger.warning("Moment content invalid (len=%d), retrying...", len(content))
        except Exception as e:
            _logger.warning("Moment gen failed attempt %d: %s", attempt, e)
    return ""


# ── 评论 ──

async def generate_comments_for_moment(moment_id: int):
    """为一个朋友圈动态生成AI评论和回复（多用户隔离：只允许动态归属用户自己的 AI 角色评论）"""
    async with async_session_factory() as db:
        moment_result = await db.execute(select(AIMoment).where(AIMoment.id == moment_id))
        moment = moment_result.scalar_one_or_none()
        if not moment:
            return

        # 确定动态归属用户：AI 动态取角色所属用户，用户动态取发布者
        owner_user_id = moment.user_id
        if moment.character_id:
            owner_char = await db.get(AICharacter, moment.character_id)
            if owner_char:
                owner_user_id = owner_char.user_id
        if not owner_user_id:
            return

        settings_result = await db.execute(
            select(ProactiveSettings).where(
                ProactiveSettings.moments_enabled == True,
                ProactiveSettings.moments_comment_enabled == True,
            )
        )
        settings_list = settings_result.scalars().all()

        ai_chars = {}
        for s in settings_list:
            char_result = await db.execute(select(AICharacter).where(AICharacter.id == s.character_id))
            char = char_result.scalar_one_or_none()
            # 只允许归属用户自己的 AI 角色评论（跨用户评论隔离）
            if char and char.is_active and char.user_id == owner_user_id and char.id != moment.character_id:
                ai_chars[char.id] = char

        if not ai_chars:
            return

        existing_result = await db.execute(
            select(MomentComment).where(MomentComment.moment_id == moment_id).order_by(MomentComment.created_at.asc())
        )
        existing_comments = existing_result.scalars().all()

    # 动态作者名
    author_name = await _resolve_author_name(moment)
    author_gender = await _resolve_author_gender(moment)
    try:
        user_profile = await build_user_profile_text(moment.user_id or 1)
    except Exception:
        user_profile = ""

    # 隔离加固（跨用户评论防护）：评论/回复目标只允许归属动态用户自己的 AI 角色与用户本人
    allowed_ai_ids = set(ai_chars.keys())
    if moment.character_id:
        allowed_ai_ids.add(moment.character_id)
    existing_top_comments = [
        c for c in existing_comments if c.parent_id is None
        and (c.sender_type != "ai" or c.sender_id in allowed_ai_ids)
    ]
    existing_user_comments = [
        c for c in existing_comments
        if c.sender_type == "user" and c.user_id == owner_user_id
    ]  # 含用户回复 AI 的子评论（回复评论功能）

    daily_limit = await _get_daily_comment_limit(owner_user_id)
    image_desc = (moment.image_desc or "").strip()[:200]

    # 动态作者角色（若有）：其本人回复用户评论走"作者视角"，移出循环避免重复处理
    author_char = None
    if moment.character_id:
        try:
            async with async_session_factory() as db:
                ar = await db.execute(select(AICharacter).where(AICharacter.id == moment.character_id))
                ac = ar.scalar_one_or_none()
                if ac and ac.is_active:
                    author_char = ac
        except Exception:
            author_char = None

    # P1-1 AI 点赞：其他 AI 角色按性格概率点赞（纯规则，零 LLM，每日每角色上限）
    try:
        await maybe_ai_likes(moment_id, ai_chars)
    except Exception as e:
        _logger.warning("AI likes failed moment=%d: %s", moment_id, e)

    for char_id, char in ai_chars.items():
        try:
            daily_count = await _get_today_ai_comment_count_for_char(char_id)
            await _generate_top_and_reply_comments(
                char_id, char, moment_id, existing_top_comments,
                existing_comments, daily_limit, daily_count, image_desc,
                moment_content=moment.content or "", author_name=author_name,
                user_profile=user_profile, author_gender=author_gender,
            )
            # 回复用户评论（不计上限，旁观视角）
            await _reply_user_comments(char_id, char, moment_id, existing_comments, existing_user_comments, image_desc,
                                       moment_content=moment.content or "", author_name=author_name,
                                       user_profile=user_profile, author_gender=author_gender)
        except Exception as e:
            _logger.warning("Comment gen failed char=%d moment=%d: %s", char_id, moment_id, e)

    # P1-2a 第一轮互评：同批生成的顶级评论之间互相回复（查最新状态，幂等）
    try:
        await _first_round_ai_replies(
            moment_id, ai_chars, daily_limit,
            moment_content=moment.content or "", author_name=author_name,
            user_profile=user_profile, author_gender=author_gender, image_desc=image_desc,
            owner_char_ids=allowed_ai_ids,
        )
    except Exception as e:
        _logger.warning("First-round replies failed moment=%d: %s", moment_id, e)

    # P1-2 多轮互评：第一轮 AI 互评后，被回复方 50% 概率再回一句（限 1 个来回 + 每日上限复用）
    try:
        await _second_round_ai_replies(
            moment_id, ai_chars, daily_limit,
            moment_content=moment.content or "", author_name=author_name,
            user_profile=user_profile, author_gender=author_gender, image_desc=image_desc,
            owner_char_ids=allowed_ai_ids,
        )
    except Exception as e:
        _logger.warning("Second-round replies failed moment=%d: %s", moment_id, e)

    # 动态作者本人回复用户评论（作者视角：动态里的经历是自己的，体现与用户的关系）
    if author_char is not None:
        try:
            async with async_session_factory() as adb:
                ar = await adb.execute(
                    select(ProactiveSettings).where(ProactiveSettings.character_id == author_char.id)
                )
                author_ps = ar.scalar_one_or_none()
            author_comment_ok = author_ps is None or bool(getattr(author_ps, "moments_comment_enabled", True))
        except Exception:
            author_comment_ok = True
        if author_comment_ok:
            try:
                await _reply_user_comments(author_char.id, author_char, moment_id, existing_comments, existing_user_comments, image_desc,
                                           moment_content=moment.content or "", author_name=author_name,
                                           user_profile=user_profile, author_gender=author_gender,
                                           is_author=True)
            except Exception as e:
                _logger.warning("Author reply failed char=%d moment=%d: %s", author_char.id, moment_id, e)


async def _resolve_author_name(moment) -> str:
    """获取动态作者名"""
    if moment.character_id:
        async with async_session_factory() as db:
            ar = await db.execute(select(AICharacter).where(AICharacter.id == moment.character_id))
            ac = ar.scalar_one_or_none()
            return ac.name if ac else ""


async def _resolve_author_gender(moment) -> str:
    """获取动态作者性别（male/female/空）"""
    if moment.character_id:
        async with async_session_factory() as db:
            ar = await db.execute(select(AICharacter).where(AICharacter.id == moment.character_id))
            ac = ar.scalar_one_or_none()
            if ac and ac.gender:
                g = (ac.gender or "").strip().lower()
                if g in ("男", "male"):
                    return "male"
                if g in ("女", "female"):
                    return "female"
            return ""
    return ""
    return "\u7528\u6237" if moment.sender_type == "user" else "\u672a\u77e5"
async def _get_daily_comment_limit(user_id: int) -> int:
    """计算某用户每日评论上限：该用户活跃AI数*2 + 该用户今日朋友圈数（按用户隔离）"""
    start = _beijing_day_start_utc()
    async with async_session_factory() as db:
        # 该用户开启了朋友圈的 AI 角色数
        settings_result = await db.execute(
            select(ProactiveSettings)
            .join(AICharacter, AICharacter.id == ProactiveSettings.character_id)
            .where(
                ProactiveSettings.moments_enabled == True,
                ProactiveSettings.moments_comment_enabled == True,
                AICharacter.user_id == user_id,
            )
        )
        ai_count = len(settings_result.scalars().all())
        user_count_result = await db.execute(
            select(func.count()).where(
                AIMoment.sender_type == "user", AIMoment.is_active == True,
                AIMoment.user_id == user_id,
                AIMoment.created_at >= start,
            )
        )
        user_count = user_count_result.scalar() or 0
    return max(ai_count * 2 + user_count, 1)


async def _get_today_ai_comment_count_for_char(char_id: int) -> int:
    """获取某AI今日已发评论数（不含回复用户的）"""
    start = _beijing_day_start_utc()
    async with async_session_factory() as db:
        result = await db.execute(
            select(MomentComment).where(
                MomentComment.sender_id == char_id,
                MomentComment.sender_type == "ai",
                MomentComment.created_at >= start,
            )
        )
        all_c = result.scalars().all()
    count = 0
    for c in all_c:
        if c.parent_id is None:
            count += 1
        else:
            async with async_session_factory() as db:
                p = await db.execute(select(MomentComment).where(MomentComment.id == c.parent_id))
                parent = p.scalar_one_or_none()
            if parent and parent.sender_type == "ai":
                count += 1
    return count


async def _identity_block(char) -> str:
    """角色身份块：性别/用户性别/对象/关系，防止"对象是谁/谁是谁的谁"混淆。失败返回空串。"""
    try:
        from app.agent.user_profile import build_role_prompt_block
        return await build_role_prompt_block(char, char.user_id or 1)
    except Exception:
        return ""


async def _generate_comment_text(char_name: str, personality: str, prompt: str, max_tok: int = 400) -> str:
    """调用 LLM 生成单条评论"""
    messages = [
        {"role": "system", "content": f"\u4f60\u662f{char_name}\uff0c\u6027\u683c{personality or '\u53cb\u5584'}\u3002\u4f60\u6b63\u5728\u670b\u53cb\u5708\u770b\u5230\u4e00\u6761\u52a8\u6001\uff0c\u8bc4\u8bba\u5fc5\u987b\u56f4\u7ed5\u8fd9\u6761\u52a8\u6001\u7684\u5177\u4f53\u5185\u5bb9\uff0c\u4e0d\u80fd\u5199\u4e0e\u5185\u5bb9\u65e0\u5173\u7684\u4e07\u80fd\u5957\u8bdd\u3002\u6ce8\u610f\u5206\u6e05\u52a8\u4f5c\u4e3b\u4f53\uff08\u8c01\u53d1\u5e03\u7684\u52a8\u6001\u3001\u8c01\u5728\u505a\u4e8b\uff09\u3002\u53ea\u8f93\u51fa\u8bc4\u8bba\u5185\u5bb9\u672c\u8eab\uff0c\u4e0d\u8981\u52a0\u5f15\u53f7\u3002"},
        {"role": "user", "content": prompt},
    ]
    response = await chat_completion(messages=messages, temperature=0.8, max_tokens=max_tok, task="message")
    return response.strip().strip('"').strip("'").strip("\u300c").strip("\u300d").strip("\u3010").strip("\u3011")


def _is_valid_content(content: str) -> bool:
    return len(content) >= 2


def _emotion_hint_for(content: str) -> str:
    """动态/评论情感倾向提示（复用 utils/emotion.py，零 LLM）。返回空串表示无情绪。"""
    try:
        from app.domain.emotion.model import detect_user_emotion
        hint = detect_user_emotion(content or "")
    except Exception:
        hint = ""
    if not hint:
        return ""
    if "低落" in hint or "倾诉" in hint:
        return "\n这条动态/评论情绪偏低落或倾诉，请先接住情绪（表示关心/共情），再自然回应，不要说风凉话或万能套话。"
    if "不耐烦" in hint or "激动" in hint:
        return "\n这条动态/评论情绪偏负面（不耐烦/激动），回复要温和体谅，别火上浇油。"
    if "心情不错" in hint:
        return "\n这条动态/评论情绪不错，可以跟着轻松活泼地回应。"
    return ""


def _comment_too_similar(content: str, existing_texts: list, threshold: float = 0.7) -> bool:
    """新评论与同角色已有评论字符相似度过高 → 视为重复（复用记忆去重思路）。"""
    from difflib import SequenceMatcher
    a = (content or "").strip()[:60]
    if len(a) < 4:
        return False
    for b in existing_texts or []:
        b = (b or "").strip()[:60]
        if len(b) < 4:
            continue
        if SequenceMatcher(None, a, b).ratio() >= threshold:
            return True
    return False



# ── P1-1 AI 点赞（纯规则） ──

_LIKE_HIGH_KEYWORDS = ("热情", "活泼", "暖", "开朗", "外向", "阳光", "爱笑", "亲切", "暖心", "粘人", "乐天")
_LIKE_LOW_KEYWORDS = ("高冷", "毒舌", "冷淡", "傲娇", "慢热", "安静", "沉稳", "理性", "冷漠", "神秘", "寡言")


def _like_probability(personality: str) -> float:
    """按性格关键词给点赞概率：热情类高、高冷类低、默认中等"""
    if not personality:
        return 0.55
    if any(k in personality for k in _LIKE_HIGH_KEYWORDS):
        return 0.85
    if any(k in personality for k in _LIKE_LOW_KEYWORDS):
        return 0.25
    return 0.55


async def maybe_ai_likes(moment_id: int, ai_chars: dict) -> list[str]:
    """其他 AI 角色按性格概率点赞该动态（每日每角色 ≤3 个赞），返回本次点赞的角色名列表。

    ai_chars: 已做过用户隔离的 {char_id: AICharacter}，不含动态作者本人。
    纯规则零 LLM；重复点赞由 unique(moment_id, character_id) + 查重兜底。
    """
    liked_names: list[str] = []
    day_start = _beijing_day_start_utc()
    async with async_session_factory() as db:
        for char_id, char in ai_chars.items():
            # 当日点赞数（每日上限 3）
            cnt_result = await db.execute(
                select(func.count()).where(
                    MomentAILike.character_id == char_id,
                    MomentAILike.created_at >= day_start,
                )
            )
            if cnt_result.scalar_one() >= 3:
                continue
            # 是否已点过该动态
            dup_result = await db.execute(
                select(MomentAILike).where(
                    MomentAILike.moment_id == moment_id,
                    MomentAILike.character_id == char_id,
                )
            )
            if dup_result.scalar_one_or_none() is not None:
                continue
            if random.random() >= _like_probability(char.personality or ""):
                continue
            db.add(MomentAILike(moment_id=moment_id, character_id=char_id))
            liked_names.append(char.name)
        if liked_names:
            await db.commit()
            _logger.info("AI likes on moment=%d: %s", moment_id, ",".join(liked_names))
    return liked_names


async def _first_round_ai_replies(moment_id: int, ai_chars: dict, daily_limit: int,
                                moment_content: str = "", author_name: str = "",
                                user_profile: str = "", author_gender: str = "",
                                image_desc: str = "", owner_char_ids: set[int] | None = None):
    """第一轮 AI 互评（统一处理）：同批生成的顶级评论之间互相回复。

    查询最新评论状态，对每个 AI 角色找 1 条未回复过的其他 AI 顶级评论生成回复；
    幂等（已回复的跳过），并复用每日评论上限。
    """
    async with async_session_factory() as db:
        existing_result = await db.execute(
            select(MomentComment).where(MomentComment.moment_id == moment_id)
        )
        existing_comments = list(existing_result.scalars().all())
    if len(existing_comments) < 2:
        return

    ai_top = [
        c for c in existing_comments if c.parent_id is None and c.sender_type == "ai"
        and (owner_char_ids is None or c.sender_id in owner_char_ids)
    ]
    if len(ai_top) < 2:
        return

    gender_note = ""
    if author_gender == "male":
        gender_note = "（作者是男性，提到作者时要用“他”，绝不能用“她/姐”等女性称谓）"
    elif author_gender == "female":
        gender_note = "（作者是女性，提到作者时用“她”）"
    role_block = (
        "角色约束：你是看这条动态的评论者/回复者，不是动态里经历的主人；"
        f"作者的照片、宠物、做的事都属于{author_name or '朋友'}本人，绝不能把作者的经历当成你自己的来写。\n"
        f"{gender_note}"
        + (f"\n用户背景（供了解朋友圈成员，回复用户评论时用）：\n{user_profile}" if user_profile else "")
    )
    image_hint = f"\n这条动态配了一张图片，图片内容：{image_desc}" if image_desc else ""
    dynamic_text = (moment_content or "").strip()[:60]

    for char_id, char in ai_chars.items():
        async with async_session_factory() as db:
            today_cnt = await db.execute(
                select(func.count()).where(
                    MomentComment.sender_type == "ai",
                    MomentComment.sender_id == char_id,
                    MomentComment.created_at >= _beijing_day_start_utc(),
                )
            )
            if today_cnt.scalar_one() >= daily_limit:
                continue
        # 该角色已回复过的 AI 顶级评论
        replied_ids = set()
        my_top_ids = set()
        for c in existing_comments:
            if c.sender_id == char_id and c.sender_type == "ai":
                if c.parent_id is None:
                    my_top_ids.add(c.id)
                else:
                    p = next((ec for ec in existing_comments if ec.id == c.parent_id), None)
                    if p and p.sender_type == "ai" and p.parent_id is None:
                        replied_ids.add(p.id)
        unreplied = [c for c in ai_top if c.id not in replied_ids and c.id not in my_top_ids]
        if not unreplied:
            continue
        target = random.choice(unreplied)
        identity_block = await _identity_block(char)
        identity_hint = (f"\n你的身份（重要，防止把动态作者的经历当成自己的、防止用错称谓/混淆关系）：\n{identity_block}\n"
                         if identity_block else "")
        prompt_text = (
            f"你是{char.name}，性格{char.personality or '友善'}。"
            f"动态原文：\"{dynamic_text or '（无文字）'}\"\n"
            f"你在回复{target.sender_name}的评论，说\"你\"时指的是{target.sender_name}，不要把{author_name}做的事安到{target.sender_name}头上。\n"
            f"{role_block}\n"
            f"{identity_hint}"
            f"在这条动态下，{target.sender_name}评论说：\"{target.content[:60]}...\"\n"
            f"请回复ta的评论，结合动态内容，像朋友间互动一样（答10-30字）。直接输出内容。"
            + image_hint
        )
        content = await _generate_comment_text(char.name, char.personality or "友善", prompt_text)
        if not _is_valid_content(content):
            continue
        async with async_session_factory() as db:
            reply = MomentComment(
                moment_id=moment_id, parent_id=target.id,
                sender_type="ai", sender_id=char_id, sender_name=char.name, content=content,
            )
            db.add(reply)
            await db.commit()
        _logger.info("AI first-round reply: char=%d -> comment=%d: %.40s", char_id, target.id, content)


async def _second_round_ai_replies(moment_id: int, ai_chars: dict, daily_limit: int,
                                   moment_content: str = "", author_name: str = "",
                                   user_profile: str = "", author_gender: str = "",
                                   image_desc: str = "", owner_char_ids: set[int] | None = None):
    """多轮互评（限 1 个来回）：第一轮 AI 互评后，被回复方 50% 概率再回一句。

    只扫描 parent 为 AI 顶级评论的回复（第一轮互评），第二轮回复不再被回复，杜绝无限聊；
    每日评论上限复用 daily_limit；身份约束与首轮一致（旁观视角）。
    """
    async with async_session_factory() as db:
        existing_result = await db.execute(
            select(MomentComment).where(MomentComment.moment_id == moment_id)
        )
        existing_comments = list(existing_result.scalars().all())
    if len(existing_comments) < 2:
        return

    # 收集第一轮互评：(回复, 被回复的顶级评论)
    first_round: list[tuple] = []
    for c in existing_comments:
        if c.parent_id is None or c.sender_type != "ai":
            continue
        p = next((ec for ec in existing_comments if ec.id == c.parent_id), None)
        if p and p.sender_type == "ai" and p.parent_id is None:
            if owner_char_ids is not None and (c.sender_id not in owner_char_ids or p.sender_id not in owner_char_ids):
                continue
            first_round.append((c, p))
    if not first_round:
        return

    gender_note = ""
    if author_gender == "male":
        gender_note = "（作者是男性，提到作者时要用“他”，绝不能用“她/姐”等女性称谓）"
    elif author_gender == "female":
        gender_note = "（作者是女性，提到作者时用“她”）"
    role_block = (
        "角色约束：你是看这条动态的评论者/回复者，不是动态里经历的主人；"
        f"作者的照片、宠物、做的事都属于{author_name or '朋友'}本人，绝不能把作者的经历当成你自己的来写。\n"
        f"{gender_note}"
        + (f"\n用户背景（供了解朋友圈成员，回复用户评论时用）：\n{user_profile}" if user_profile else "")
    )
    image_hint = f"\n这条动态配了一张图片，图片内容：{image_desc}" if image_desc else ""

    for reply_c, top_c in first_round:
        replied_id = top_c.sender_id     # 被回复者 A
        if replied_id not in ai_chars:
            continue
        # A 已回过 B 的这条回复 → 不重复
        if any(ec.sender_id == replied_id and ec.parent_id == reply_c.id for ec in existing_comments):
            continue
        # 50% 概率再回一句
        if random.random() >= 0.5:
            continue
        char = ai_chars[replied_id]
        async with async_session_factory() as db:
            today_cnt = await db.execute(
                select(func.count()).where(
                    MomentComment.sender_type == "ai",
                    MomentComment.sender_id == replied_id,
                    MomentComment.created_at >= _beijing_day_start_utc(),
                )
            )
            if today_cnt.scalar_one() >= daily_limit:
                continue
        identity_block = await _identity_block(char)
        identity_hint = (f"\n你的身份（重要，防止把动态作者的经历当成自己的、防止用错称谓/混淆关系）：\n{identity_block}\n"
                         if identity_block else "")
        dynamic_text = (moment_content or "").strip()[:60]
        prompt_text = (
            f"你是{char.name}，性格{char.personality or '友善'}。"
            f"动态原文：\"{dynamic_text or '（无文字）'}\"\n"
            f"你在和{reply_c.sender_name}在评论区聊天。你之前评论说：\"{top_c.content[:60]}...\"，"
            f"{reply_c.sender_name}回复你说：\"{reply_c.content[:60]}...\"\n"
            f"{role_block}\n"
            f"{identity_hint}"
            f"请自然地再回{reply_c.sender_name}一句，像真人评论区聊天一样接住 ta 的话（答10-25字），不要重复你之前说过的话。直接输出内容。"
            + image_hint
        )
        content = await _generate_comment_text(char.name, char.personality or "友善", prompt_text)
        if not _is_valid_content(content):
            continue
        async with async_session_factory() as db:
            reply = MomentComment(
                moment_id=moment_id, parent_id=reply_c.id,
                sender_type="ai", sender_id=replied_id, sender_name=char.name, content=content,
            )
            db.add(reply)
            await db.commit()
        _logger.info("AI second-round reply: char=%d -> comment=%d: %.40s", replied_id, reply_c.id, content)



async def _generate_top_and_reply_comments(char_id, char, moment_id, existing_top_comments, existing_comments, daily_limit, daily_count, image_desc: str = "", moment_content: str = "", author_name: str = "", user_profile: str = "", author_gender: str = ""):
    """生成顶级评论 + 回复其他AI评论"""
    added = 0
    image_hint = f"\n这条动态配了一张图片，图片内容：{image_desc}" if image_desc else ""
    gender_note = ""
    if author_gender == "male":
        gender_note = "（作者是男性，提到作者时要用“他”，绝不能用“她/姐”等女性称谓）"
    elif author_gender == "female":
        gender_note = "（作者是女性，提到作者时用“她”）"
    role_block = (
        f"角色约束：你是看这条动态的评论者/回复者，不是动态里经历的主人；作者的照片、宠物、做的事都属于{author_name or '朋友'}本人，绝不能把作者的经历当成你自己的来写。\n"
        f"{gender_note}"
        + (f"\n用户背景（供了解朋友圈成员，回复用户评论时用）：\n{user_profile}" if user_profile else "")
    )

    # 我的已有顶级评论
    my_top_comments = [c for c in existing_top_comments if c.sender_id == char_id and c.sender_type == "ai"]
    identity_block = await _identity_block(char)
    identity_hint = (f"\n你的身份（重要，防止把动态作者的经历当成自己的、防止用错称谓/混淆关系）：\n{identity_block}\n"
                     if identity_block else "")

    # === 1. 顶级评论（最多1条）===
    if not my_top_comments and daily_count + added < daily_limit:
        dynamic_text = (moment_content or "").strip()[:80]
        prompt_text = (
            f"你是{char.name}，性格{char.personality or '友善'}。"
            f"你在朋友圈看到{author_name or '朋友'}发了一条动态：\"{dynamic_text or '（无文字）'}\"\n"
            f"注意：这条动态是{author_name or '朋友'}本人的经历，动态里提到的宠物、照片、动作的主人都{author_name or '朋友'}（比如照片里的猫是{author_name or '朋友'}的猫，摸猫/拍照的人是{author_name or '朋友'}）。\n"
            f"{role_block}\n"
            f"{identity_hint}"
            "请针对这条动态的具体内容评论（答10-25字），像朋友之间的互动，不要评论动态本身的形式。直接输出内容。"
            + _emotion_hint_for(moment_content)
            + image_hint
        )
        content = await _generate_comment_text(char.name, char.personality or "友善", prompt_text)
        # P2-2 防重复：与同角色已有评论相似度过高 → 重试一次
        _my_texts = [c.content for c in existing_comments if c.sender_id == char_id and c.sender_type == "ai"]
        if _is_valid_content(content) and _comment_too_similar(content, _my_texts):
            content = await _generate_comment_text(char.name, char.personality or "友善", prompt_text)
        if _is_valid_content(content):
            async with async_session_factory() as db:
                comment = MomentComment(
                    moment_id=moment_id, sender_type="ai",
                    sender_id=char_id, sender_name=char.name, content=content,
                )
                db.add(comment)
                await db.commit()
            _logger.info("AI top comment: char=%d on moment=%d: %.40s", char_id, moment_id, content)
            added += 1
        else:
            _logger.warning("Char=%d generated invalid top comment", char_id)

    my_top_comments = [c for c in existing_top_comments if c.sender_id == char_id and c.sender_type == "ai"]

    # === 2. 回复其他AI评论（随机选1条未回复的）===
    if daily_count + added < daily_limit:
        other_ai = [c for c in existing_top_comments if c.sender_id != char_id and c.sender_type == "ai"]
        already_replied = set()
        for c in existing_comments:
            if c.sender_id == char_id and c.sender_type == "ai" and c.parent_id is not None:
                p = next((ec for ec in existing_comments if ec.id == c.parent_id), None)
                if p and p.sender_type == "ai":
                    already_replied.add(p.id)

        unreplied = [c for c in other_ai if c.id not in already_replied]
        if unreplied:
            target = random.choice(unreplied)
            dynamic_text = (moment_content or "").strip()[:60]
            prompt_text = (
                f"你是{char.name}，性格{char.personality or '友善'}。"
                f"动态原文：\"{dynamic_text or '（无文字）'}\"\n"
                f"你在回复{target.sender_name}的评论，说\"你\"时指的是{target.sender_name}，不要把{author_name}做的事安到{target.sender_name}头上。\n"
                f"{role_block}\n"
                f"{identity_hint}"
                f"在这条动态下，{target.sender_name}评论说：\"{target.content[:60]}...\"\n"
                f"请回复ta的评论，结合动态内容，像朋友间互动一样（答10-30字）。直接输出内容。"
            )
            content = await _generate_comment_text(char.name, char.personality or "友善", prompt_text)
            if _is_valid_content(content):
                async with async_session_factory() as db:
                    reply = MomentComment(
                        moment_id=moment_id, parent_id=target.id,
                        sender_type="ai", sender_id=char_id, sender_name=char.name, content=content,
                    )
                    db.add(reply)
                    await db.commit()
                _logger.info("AI->AI reply: char=%d replied to comment=%d: %.40s", char_id, target.id, content)
                added += 1

    return added


async def _reply_user_comments(char_id, char, moment_id, existing_comments, existing_user_comments, image_desc: str = "", moment_content: str = "", author_name: str = "", user_profile: str = "", author_gender: str = "", is_author: bool = False):
    """回复所有未回复的用户评论——不计入每日上限

    is_author=True：动态作者本人回复（作者视角，动态里的经历是自己的）；
    is_author=False：其他 AI 评论者回复（旁观视角）。
    """
    image_hint = f"\n这条动态配了一张图片，图片内容：{image_desc}" if image_desc else ""
    gender_note = ""
    if author_gender == "male":
        gender_note = "（动态作者是男性，提到作者时要用“他”，绝不能用“她/姐”等女性称谓）"
    elif author_gender == "female":
        gender_note = "（动态作者是女性，提到作者时用“她”）"
    if is_author:
        # 作者本人：动态里的经历就是自己的，不存在"作者是别人"
        role_block = (
            f"你是这条动态的作者{author_name or '你自己'}，动态里的经历（做饭/宠物/照片/动作/说的话）都是你自己的事。\n"
            f"{gender_note}"
            + (f"\n用户背景：\n{user_profile}" if user_profile else "")
        )
    else:
        role_block = (
            f"角色约束：你是看这条动态的评论者/回复者，不是动态里经历的主人；作者的照片、宠物、做的事都属于{author_name or '朋友'}本人，绝不能把作者的经历当成你自己的来写。\n"
            f"{gender_note}"
            + (f"\n用户背景：\n{user_profile}" if user_profile else "")
        )
    identity_block = await _identity_block(char)
    identity_hint = (f"\n你的身份（重要，防止把动态作者的经历当成自己的、防止用错称谓/混淆关系）：\n{identity_block}\n"
                     if identity_block else "")
    # 评论者（用户）昵称：从用户画像解析，供作者视角回复时称呼
    _user_nickname = ""
    for _line in (user_profile or "").splitlines():
        if _line.startswith("用户昵称:"):
            _user_nickname = _line.split(":", 1)[1].strip()
            break
    replied_ids = set()
    for c in existing_comments:
        if c.sender_id == char_id and c.sender_type == "ai" and c.parent_id is not None:
            p = next((ec for ec in existing_comments if ec.id == c.parent_id), None)
            if p and p.sender_type == "user":
                replied_ids.add(p.id)

    for uc in existing_user_comments:
        if uc.id in replied_ids:
            continue
        # 用户子评论（回复某 AI 评论）语境：告知 AI 用户在回复谁，避免答非所问
        parent_note = ""
        if uc.parent_id:
            parent = next((ec for ec in existing_comments if ec.id == uc.parent_id), None)
            if parent is not None:
                parent_note = f"（用户这条是在回复{parent.sender_name}的评论「{parent.content[:40]}」）"
        dynamic_text = (moment_content or "").strip()[:80]
        if is_author:
            # 作者本人回复：以"我/我们"口吻，动态经历是自己的，体现与用户的关系（对象/伴侣/朋友）
            prompt_text = (
                f"你是{char.name}，性格{char.personality or '友善'}。"
                f"这条动态是你自己发的：\"{dynamic_text or '（无文字）'}\"，动态里的经历都是你的。\n"
                f"{role_block}\n"
                f"{identity_hint}"
                f"用户（{_user_nickname or '你的爱人/朋友'}）在你这条动态下评论说：\"{uc.content[:80]}...\"{parent_note}\n"
                f"请以你自己的口吻（用“我/我们”）回复用户这条评论，像恋人/家人/朋友间的日常互动，回应ta评论的具体内容（答10-30字）。直接输出内容。"
                + image_hint
            )
        else:
            prompt_text = (
                f"你是{char.name}，性格{char.personality or '友善'}。"
                f"动态原文：\"{dynamic_text or '（无文字）'}\"\n"
                f"你在回复用户的评论，说\"你\"时指的是用户，不要把{author_name}做的事安到用户头上。\n"
                f"{role_block}\n"
                f"{identity_hint}"
                f"用户在这条动态下评论说：\"{uc.content[:80]}...\"{parent_note}\n"
                f"请结合动态内容回复用户这条评论（答10-30字），语气友好自然。直接输出内容。"
                + _emotion_hint_for(uc.content)
                + image_hint
            )
        content = await _generate_comment_text(char.name, char.personality or "友善", prompt_text)
        # P2-2 防重复：与同角色已有评论相似度过高 → 重试一次
        _my_texts = [c.content for c in existing_comments if c.sender_id == char_id and c.sender_type == "ai"]
        if _is_valid_content(content) and _comment_too_similar(content, _my_texts):
            content = await _generate_comment_text(char.name, char.personality or "友善", prompt_text)
        if not _is_valid_content(content):
            continue
        async with async_session_factory() as db:
            reply = MomentComment(
                moment_id=moment_id, parent_id=uc.id,
                sender_type="ai", sender_id=char_id, sender_name=char.name, content=content,
            )
            db.add(reply)
            await db.commit()
        _logger.info("AI->User reply: char=%d replied to user comment=%d: %.40s", char_id, uc.id, content)


# ── 清理 ──

async def cleanup_deleted_character(character_id: int):
    """角色删除后清理朋友圈数据"""
    async with async_session_factory() as db:
        # 删除评论
        await db.execute(
            select(MomentComment).where(MomentComment.sender_id == character_id, MomentComment.sender_type == "ai")
        ).delete(synchronize_session=False)
        # 删除 AI 点赞（角色删除后不再出现在"谁赞了"）
        await db.execute(
            select(MomentAILike).where(MomentAILike.character_id == character_id)
        ).delete(synchronize_session=False)
        await db.commit()
    # 标记动态为不活跃（保留历史，但不再显示）并清理图片文件
    from app.services.upload_service import delete_image_file
    async with async_session_factory() as db:
        moment_ids = await db.execute(
            select(AIMoment.id).where(AIMoment.character_id == character_id)
        )
        for mid in moment_ids.scalars().all():
            m = await db.get(AIMoment, mid)
            if m:
                delete_image_file(m.image_url)
                m.is_active = False
        await db.commit()
    _logger.info("Cleaned up moments for deleted character %d", character_id)


# ── 归档 ──

async def generate_pending_comments():
    """评论兜底提频（P0-2）：只补「无 AI 顶级评论 且 发布 <6h」的动态，优先用户动态，每批 ≤10 条。

    用户/手动/状态触发发布已走即时评论链路；这里兜底覆盖即时链路失败或角色当时不在线的情况。
    成本受控：6h 窗口 + 0 评论过滤，已有评论的动态不重复调用。
    """
    from datetime import timedelta
    from app.models.life import AIMoment, MomentComment
    from sqlalchemy import select
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=6)
    async with async_session_factory() as db:
        # 有 AI 顶级评论的动态 ID（子查询）
        ai_comment_ids = select(MomentComment.moment_id).where(
            MomentComment.sender_type == "ai", MomentComment.parent_id.is_(None)
        )
        result = await db.execute(
            select(AIMoment)
            .where(
                AIMoment.is_active == True,
                AIMoment.created_at >= cutoff,
                ~AIMoment.id.in_(ai_comment_ids),
            )
            .order_by((AIMoment.sender_type == "ai").asc(), AIMoment.created_at.desc())
            .limit(10)
        )
        moments = result.scalars().all()
    for moment in moments:
        try:
            await generate_comments_for_moment(moment.id)
        except Exception as e:
            _logger.warning("generate_pending_comments failed moment=%d: %s", moment.id, e)


__all__ = [
    "publish_moment", "generate_comments_for_moment",
    "cleanup_deleted_character", "_generate_moment_content", "generate_pending_comments",
]
