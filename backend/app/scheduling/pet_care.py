"""宠物关怀（2026-08-06 Phase 2）：宠物饿了/脏了 → AI 角色主动提醒用户。

数据流：
- collect_pet_events：arbiter tick 扫描宠物属性低值（hunger<30 或 cleanliness<30）
  且 6h 内未提醒（pets.last_remind_at）→ 每宠物 1 条候选事件（priority=1）
- run_pet_remind：限额/免打扰/会话检查 → LLM 生成提醒消息 → send_to_session
  （message_type="pet_remind"）→ 更新 pets.last_remind_at
- 护栏：每角色每日 <=2 条宠物提醒、免打扰不发、无活跃会话跳过、
  深夜静默与用户"说睡觉"拦截由 arbiter 统一层处理
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func, or_

from app.db.database import async_session_factory
from app.models.pet import Pet
from app.models.character import AICharacter
from app.models.character import ProactiveMessageLog
from app.utils.logger import get_logger
from app.agent.llm_client import chat_completion, load_character_reasoning_level
from app.utils.dnd import user_in_dnd_period as _user_in_dnd_period

_logger = get_logger("scheduler.pet_care")

PET_EVENT_TYPE = "pet_remind"
REMIND_INTERVAL_HOURS = 6   # 同一宠物两次提醒最小间隔
LOW_THRESHOLD = 30          # 饥饿/脏阈值
MAX_PER_DAY = 2             # 每角色每日宠物提醒上限

_SPECIES_CN = {
    "cat": "猫咪", "dog": "狗狗", "parrot": "鹦鹉",
    "rabbit": "兔子", "hamster": "仓鼠", "snake": "蛇", "gecko": "守宫",
}


def _species_cn(species: str | None) -> str:
    return _SPECIES_CN.get((species or "").strip().lower(), "小动物")


async def _pet_llm(messages: list[dict], char_id: int | None,
                   temperature: float = 0.9, max_tokens: int = 256) -> tuple[str, str]:
    """宠物通道 LLM 调用（2026-08-15；D2-E 2026-08-18 关闭深度思考）。

    返回 (content, reasoning)；reasoning 恒为空串（挡位 2 不再走 include_reasoning，与 0/1 同走普通生成）。"""
    level = await load_character_reasoning_level(char_id)
    # D2-E（2026-08-18）：宠物通道关闭深度思考（level==2 分支删除）——统一走挡位 1/0 逻辑；
    # 挡位 1 保留「先在心里简短想一下」prompt 引导；reasoning 恒为空串（extra_meta 不再带 reasoning）
    if level == 1:
        msgs = [
            {"role": "system", "content": "先在心里简短想一下怎么说合适（思考不外显），然后直接输出要说的话，不要加引号和标注。"},
            *messages,
        ]
        return await chat_completion(messages=msgs, temperature=temperature, max_tokens=max_tokens, task="message"), ""
    return await chat_completion(messages=messages, temperature=temperature, max_tokens=max_tokens, task="message"), "" 


async def _daily_count(db, character_id: int) -> int:
    cn_tz = timezone(timedelta(hours=8))
    today_start = datetime.now(cn_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = today_start.astimezone(timezone.utc).replace(tzinfo=None)
    return (await db.execute(
        select(func.count(ProactiveMessageLog.id)).where(
            ProactiveMessageLog.character_id == character_id,
            ProactiveMessageLog.message_type == PET_EVENT_TYPE,
            ProactiveMessageLog.created_at >= today_start,
        )
    )).scalar() or 0


async def _pick_reminder_character(user_id: int):
    """选提醒角色：用户对象（is_partner）优先，否则第一个活跃角色；关闭主动互动的角色不参与提醒。"""
    from app.scheduling.triggers import proactive_enabled
    async with async_session_factory() as db:
        partner = (await db.execute(
            select(AICharacter).where(
                AICharacter.user_id == user_id,
                AICharacter.is_partner == True,
                AICharacter.is_active == True,
            ).limit(1)
        )).scalar_one_or_none()
        if partner is not None and await proactive_enabled(partner.id):
            return partner
        rows = (await db.execute(
            select(AICharacter).where(
                AICharacter.user_id == user_id,
                AICharacter.is_active == True,
            ).order_by(AICharacter.id.asc())
        )).scalars().all()
        for c in rows:
            if await proactive_enabled(c.id):
                return c
        return None


async def collect_pet_events() -> list[dict]:
    """扫描需要提醒的宠物 → 每宠物一条候选事件（绑定提醒角色）。"""
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    remind_before = now_naive - timedelta(hours=REMIND_INTERVAL_HOURS)
    async with async_session_factory() as db:
        result = await db.execute(
            select(Pet).where(or_(Pet.owner_type.is_(None), Pet.owner_type == "user"))  # 仅用户宠物（AI 养宠 Phase 3 预留）
        )
        pets = result.scalars().all()
    items = []
    for pet in pets:
        if pet.hunger >= LOW_THRESHOLD and pet.cleanliness >= LOW_THRESHOLD:
            continue
        if pet.last_remind_at is not None and pet.last_remind_at > remind_before:
            continue
        char = await _pick_reminder_character(pet.user_id)
        if char is None:
            continue
        items.append({
            "type": PET_EVENT_TYPE, "priority": 1,
            "candidate": {"character_id": char.id, "user_id": pet.user_id, "pet_id": pet.id},
        })
    if items:
        _logger.info("Pet remind candidates: %d", len(items))
    return items


async def run_pet_remind(char_id: int, user_id: int, pet_id: int) -> bool:
    """执行一次宠物提醒：限额/免打扰 → LLM 生成 → 发送 → 更新提醒时间。"""
    from app.scheduling.scheduler import send_to_session
    from app.application.chat_service import get_latest_session_id

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session_factory() as db:
        if await _user_in_dnd_period(db, user_id):
            return False
        if await _daily_count(db, char_id) >= MAX_PER_DAY:
            _logger.info("Pet remind char=%d skipped: daily limit", char_id)
            return False
        pet = await db.get(Pet, pet_id)
        if pet is None or pet.user_id != user_id:
            return False
        # 6h 最小提醒间隔（collect_pet_events 已过滤，此处双保险防同 tick 重复执行）
        remind_before = now_naive - timedelta(hours=REMIND_INTERVAL_HOURS)
        if pet.last_remind_at is not None and pet.last_remind_at > remind_before:
            return False
        # 提醒后用户已互动（属性回升）→ 取消本次提醒
        if pet.hunger >= LOW_THRESHOLD and pet.cleanliness >= LOW_THRESHOLD:
            return False
        char = await db.get(AICharacter, char_id)
        pet_name = pet.name
        pet_species = _species_cn(pet.species)
        need_food = pet.hunger < LOW_THRESHOLD
        need_clean = pet.cleanliness < LOW_THRESHOLD
        if need_food and need_clean:
            state_text = "又饿又脏，蔫蔫的"
        elif need_food:
            state_text = "饿了"
        else:
            state_text = "身上脏了"
        # 归属标签：AI 自己的宠物 vs 用户家的宠物（AI 养宠 Phase 3 预留）
        if pet.owner_type == "ai" and pet.owner_id == char_id:
            owner_line = f"你养了一只{pet_species}，名字叫{pet_name}。"
        else:
            owner_line = f"用户家养了一只{pet_species}，名字叫{pet_name}，你在帮用户一起照顾它。"

    session_id = await get_latest_session_id(user_id, char_id)
    if session_id is None:
        return False

    char_name = char.name if char else "我"
    personality = (char.personality or "友善")[:100] if char else "友善"
    try:
        from app.agent.user_profile import build_role_prompt_block
        identity = ""
        try:
            identity = await build_role_prompt_block(char, user_id)
        except Exception:
            identity = f"你是{char_name}，性格{personality}。"
        # 认知循环 v2.1：主动通道 persona 统一层（关系温度/剧情状态/进行中话题；开关关=空串）
        active_persona = ""
        try:
            from app.agent.persona import build_active_channel_persona
            active_persona = await build_active_channel_persona(char_id, user_id)
        except Exception:
            active_persona = ""
        persona_block = f"{active_persona}\n" if active_persona else ""
        # 宠物习性（饮食/照顾要点），防止提醒时说错食物
        pet_fact = ""
        try:
            from app.application.pet_service import species_fact
            pet_fact = species_fact(pet.species)
        except Exception:
            pet_fact = ""
        fact_line = f"它的习性：{pet_fact}。" if pet_fact else ""
        hint = (
            f"{identity}\n"
            f"{persona_block}"
            f"{owner_line}\n"
            f"{fact_line}"
            f"它现在的状态：{state_text}。\n"
            "请你主动提醒用户照顾它：1-2 句话，口语化、自然，像真的在关心宠物；\n"
            "语气按上面你的性格和聊天风格来（例如话少的人就别腻歪、冷漠的人别撒娇），\n"
            "不要出现'检测''系统通知''提醒你'这类字眼。"
        )
        text = await _pet_llm(
            [
                {"role": "system", "content": "直接输出要说的话，不要加引号和标注。"},
                {"role": "user", "content": hint},
            ],
            char_id=char_id,
            temperature=0.9,
            max_tokens=256,
        )
        text, remind_reasoning = text
        text = (text or "").strip().strip('"').strip("'")
        if not text or len(text) < 2:
            return False
    except Exception as e:
        _logger.warning("Pet remind LLM failed char=%d: %s", char_id, e)
        return False

    _remind_extra = None
    if remind_reasoning:
        import json as _json
        _remind_extra = _json.dumps({"reasoning": remind_reasoning}, ensure_ascii=False)
    await send_to_session(
        session_id=session_id, character_id=char_id, user_id=user_id,
        content=text[:500], message_type=PET_EVENT_TYPE,
        extra_meta=_remind_extra,
    )
    async with async_session_factory() as db:
        pet2 = await db.get(Pet, pet_id)
        if pet2:
            pet2.last_remind_at = now_naive
            await db.commit()
    # 互动展示区：记录 AI 角色提醒照顾（短时去重由 log_activity 处理）
    try:
        from app.application.pet_service import log_activity
        await log_activity(pet_id, user_id, "remind", f"AI 提醒用户照顾{pet_name}（{pet_species}）")
    except Exception as e:
        _logger.warning("Pet remind activity log failed: %s", e)
    _logger.info("Pet remind sent char=%d pet=%d", char_id, pet_id)
    return True

# ── Phase 3：AI 自主养宠物（领养 / 照顾 / 来访） ──

AI_ADOPT_TYPE = "ai_adopt"      # AI 领养告知消息（独立计数）
AI_CARE_TYPE = "ai_care"        # AI 照顾宠物消息（独立限额每日 <=1）
PET_VISIT_TYPE = "pet_visit"    # AI 宠物来访（只写记录不发消息）
AI_ADOPT_DAILY_LIMIT = 1        # 每角色每日 AI 领养消息上限
AI_CARE_DAILY_LIMIT = 1         # 每角色每日 AI 照顾消息上限（已拍板：独立限额，不占 pet_remind）
PET_VISIT_DAILY_LIMIT = 1       # 每用户每日 AI 宠物来访上限
AI_ADOPT_PROBABILITY = 0.002    # 30s tick 下 AI "心血来潮"领养概率（每日上限兜底）
PET_VISIT_PROBABILITY = 0.05    # 30s tick 下 AI 宠物来访概率（每日上限兜底）
AI_CARE_INTERVAL_HOURS = 6      # 同一 AI 宠物两次照顾最小间隔


async def _daily_msg_count(char_id: int, msg_type: str) -> int:
    """某角色某消息类型今日条数（北京时间）"""
    cn_tz = timezone(timedelta(hours=8))
    today_start = datetime.now(cn_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = today_start.astimezone(timezone.utc).replace(tzinfo=None)
    async with async_session_factory() as db:
        return (await db.execute(
            select(func.count(ProactiveMessageLog.id)).where(
                ProactiveMessageLog.character_id == char_id,
                ProactiveMessageLog.message_type == msg_type,
                ProactiveMessageLog.created_at >= today_start,
            )
        )).scalar() or 0


async def collect_ai_adopt_events() -> list[dict]:
    """AI 心血来潮领养宠物（低频）：活跃角色且尚无 AI 宠物 → 概率 + 每日限额 → 候选"""
    import random
    async with async_session_factory() as db:
        chars = (await db.execute(
            select(AICharacter).where(AICharacter.is_active == True)  # noqa: E712
        )).scalars().all()
        pet_rows = (await db.execute(select(Pet).where(Pet.owner_type == "ai"))).scalars().all()
    has_pet = {(p.owner_id, p.user_id) for p in pet_rows}
    events = []
    for c in chars:
        if (c.id, c.user_id) in has_pet:
            continue
        if await _daily_msg_count(c.id, AI_ADOPT_TYPE) >= AI_ADOPT_DAILY_LIMIT:
            continue
        if random.random() > AI_ADOPT_PROBABILITY:
            continue
        events.append({
            "type": AI_ADOPT_TYPE, "priority": 0.6,
            "candidate": {"character_id": c.id, "user_id": c.user_id},
        })
    if events:
        _logger.info("AI adopt candidates: %d", len(events))
    return events


async def _gen_pet_name(char: AICharacter) -> str:
    """LLM 生成宠物名（<=5 字）；失败返回空串由调用方兜底"""
    try:
        identity = ""
        try:
            from app.agent.user_profile import build_role_prompt_block
            identity = await build_role_prompt_block(char, char.user_id)
        except Exception:
            identity = ""
        text = await chat_completion(
            messages=[{"role": "system", "content": "只输出宠物名字，不要引号和其他文字。"},
                      {"role": "user", "content": f"{identity}\n你是{char.name}（性格：{char.personality or '友善'}），心血来潮想养一只宠物。"
                                                  f"按你的性格和语气给它起个贴切的小名（2-5 个字，中文）。"}],
            temperature=0.9, max_tokens=32, task="message",
        )
        name = (text or "").strip().strip('"').strip("'")
        if name and len(name) <= 5 and any("一" <= ch <= "鿿" for ch in name):
            return name
    except Exception as e:
        _logger.warning("AI pet name LLM failed char=%d: %s", char.id, e)
    return ""


async def _gen_adopt_message(char: AICharacter, pet) -> tuple[str, str]:
    """生成 1 条领养告知（口语化，注入人设/关系/进行中话题，与聊天同人格）。

    返回 (text, reasoning)；思考用于气泡折叠展示。"""
    try:
        # 统一身份块（性别/关系/用户对象），与宠物提醒通道一致
        identity = ""
        try:
            from app.agent.user_profile import build_role_prompt_block
            identity = await build_role_prompt_block(char, char.user_id)
        except Exception:
            identity = ""
        # 主动通道 persona 统一层（关系温度/剧情状态/进行中话题；认知开关关=空串）
        persona_block = ""
        try:
            from app.agent.persona import build_active_channel_persona
            persona_block = await build_active_channel_persona(char.id, char.user_id)
        except Exception:
            persona_block = ""
        persona_block = f"{persona_block}\n" if persona_block else ""
        text, adopt_reasoning = await _pet_llm(
            messages=[{"role": "system", "content": "直接输出要说的话，不要加引号和标注。"},
                      {"role": "user", "content": f"{identity}\n你是{char.name}。\n"
                                                  f"背景：{char.bio or '无'}\n"
                                                  f"{persona_block}"
                                                  f"你刚领养了一只{_species_cn(pet.species)}取名叫{pet.name}。"
                                                  f"按你一贯的口吻和语气，自然地告诉用户这件事（1-2 句话，像朋友分享近况，不要出现'领养系统'之类字眼）。"}],
            char_id=char.id, temperature=0.9, max_tokens=128,
        )
        text = (text or "").strip().strip('"').strip("'")
        return (text, adopt_reasoning) if len(text) >= 2 else ("", "")
    except Exception as e:
        _logger.warning("AI adopt message LLM failed: %s", e)
        return ("", "")


async def run_ai_adopt(char_id: int, user_id: int) -> bool:
    """AI 自主领养：起名 → 创建 AI 宠物（owner_type='ai'）→ 记忆/活动 → 1 条主动消息告知"""
    import random
    from app.scheduling.scheduler import send_to_session
    from app.application.chat_service import get_latest_session_id
    from app.application import pet_service

    async with async_session_factory() as db:
        char = await db.get(AICharacter, char_id)
    if char is None:
        return False
    if await _daily_msg_count(char_id, AI_ADOPT_TYPE) >= AI_ADOPT_DAILY_LIMIT:
        return False
    species = random.choice(["cat", "dog", "parrot", "rabbit", "hamster"])
    name = await _gen_pet_name(char)
    if not name:
        name = f"{_species_cn(species)}{random.randint(1, 99)}"
    try:
        pet = await pet_service.ai_adopt(char_id, user_id, species, name)
    except Exception as e:
        _logger.warning("AI adopt failed char=%d: %s", char_id, e)
        return False
    session_id = await get_latest_session_id(user_id, char_id)
    if session_id is None:
        _logger.info("AI adopt done char=%d pet=%d (no session)", char_id, pet.id)
        return True
    text, adopt_reasoning = await _gen_adopt_message(char, pet)
    if text:
        _adopt_extra = None
        if adopt_reasoning:
            import json as _json
            _adopt_extra = _json.dumps({"reasoning": adopt_reasoning}, ensure_ascii=False)
        await send_to_session(session_id, char_id, user_id, text[:500], message_type=AI_ADOPT_TYPE,
                              extra_meta=_adopt_extra)
    _logger.info("AI adopt sent char=%d pet=%d", char_id, pet.id)
    return True


async def _recent_care(pet_id: int, since) -> bool:
    """该宠物最近是否有互动（照顾间隔判断）"""
    from app.models.pet import PetActivity
    async with async_session_factory() as db:
        cnt = (await db.execute(
            select(func.count(PetActivity.id)).where(
                PetActivity.pet_id == pet_id,
                PetActivity.action.in_(["feed", "play", "clean"]),
                PetActivity.created_at >= since,
            )
        )).scalar() or 0
    return cnt > 0


async def collect_ai_care_events() -> list[dict]:
    """AI 照顾自己的宠物：AI 宠物低值且 6h 内未照顾 → 候选（priority=1，与 pet_remind 同级）"""
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    care_before = now_naive - timedelta(hours=AI_CARE_INTERVAL_HOURS)
    async with async_session_factory() as db:
        pets = (await db.execute(select(Pet).where(Pet.owner_type == "ai"))).scalars().all()
    items = []
    for pet in pets:
        if pet.hunger >= 30 and pet.cleanliness >= 30 and pet.mood >= 30 and pet.energy >= 30:
            continue
        if await _recent_care(pet.id, care_before):
            continue
        items.append({
            "type": AI_CARE_TYPE, "priority": 1,
            "candidate": {"character_id": pet.owner_id, "user_id": pet.user_id, "pet_id": pet.id},
        })
    if items:
        _logger.info("AI care candidates: %d", len(items))
    return items


async def _gen_care_message(char: AICharacter, pet) -> tuple[str, str]:
    """生成 1 条 AI 照顾宠物后的口语消息（注入人设，与聊天同人格）。

    返回 (text, reasoning)；思考用于气泡折叠展示。"""
    try:
        identity = ""
        try:
            from app.agent.user_profile import build_role_prompt_block
            identity = await build_role_prompt_block(char, char.user_id)
        except Exception:
            identity = ""
        persona_block = ""
        try:
            from app.agent.persona import build_active_channel_persona
            persona_block = await build_active_channel_persona(char.id, char.user_id)
        except Exception:
            persona_block = ""
        persona_block = f"{persona_block}\n" if persona_block else ""
        text, care_reasoning = await _pet_llm(
            messages=[{"role": "system", "content": "直接输出要说的话，不要加引号和标注。"},
                      {"role": "user", "content": f"{identity}\n你是{char.name}。\n"
                                                  f"背景：{char.bio or '无'}\n"
                                                  f"{persona_block}"
                                                  f"你刚照顾完自己的宠物{pet.name}（{_species_cn(pet.species)}）。"
                                                  f"按你一贯的口吻跟用户提一句（1 句话，像分享日常，不要出现'照顾''喂食'这类系统感强的字眼，可以说'我刚给{pet.name}……'）。"}],
            char_id=char.id, temperature=0.9, max_tokens=128,
        )
        text = (text or "").strip().strip('"').strip("'")
        return (text, care_reasoning) if len(text) >= 2 else ("", "")
    except Exception as e:
        _logger.warning("AI care message LLM failed: %s", e)
        return ("", "")


async def run_ai_care(char_id: int, user_id: int, pet_id: int) -> bool:
    """AI 照顾自己的宠物：按低值选动作互动（属性+活动+记忆）→ 照顾消息独立限额每日 <=1"""
    from app.scheduling.scheduler import send_to_session
    from app.application.chat_service import get_latest_session_id
    from app.application import pet_service

    async with async_session_factory() as db:
        pet = await db.get(Pet, pet_id)
        if pet is None or pet.owner_type != "ai" or pet.owner_id != char_id:
            return False
        char = await db.get(AICharacter, char_id)
    action = None
    if pet.hunger < 30:
        action = "feed"
    elif pet.cleanliness < 30:
        action = "clean"
    elif pet.mood < 30 or pet.energy < 30:
        action = "play"
    if action is None:
        return False
    char_name = char.name if char else "AI"
    try:
        async with async_session_factory() as db:
            pet2 = await db.get(Pet, pet_id)
            await pet_service.interact_by(db, pet2, action, user_id, actor="ai", owner_char_name=char_name)
    except Exception as e:
        _logger.warning("AI care interact failed char=%d pet=%d: %s", char_id, pet_id, e)
        return False
    # 照顾消息：独立限额每日 <=1（不占 pet_remind 的 2 条）
    if await _daily_msg_count(char_id, AI_CARE_TYPE) >= AI_CARE_DAILY_LIMIT:
        return True
    session_id = await get_latest_session_id(user_id, char_id)
    if session_id is None:
        return True
    text, care_reasoning = await _gen_care_message(char, pet) if char else ("", "")
    if text:
        _care_extra = None
        if care_reasoning:
            import json as _json
            _care_extra = _json.dumps({"reasoning": care_reasoning}, ensure_ascii=False)
        await send_to_session(session_id, char_id, user_id, text[:500], message_type=AI_CARE_TYPE,
                              extra_meta=_care_extra)
    _logger.info("AI care sent char=%d pet=%d", char_id, pet_id)
    return True


async def _pet_visit_daily_count(user_id: int) -> int:
    """今日该用户已收到 AI 宠物来访次数（按 PetActivity action=visit 计数）"""
    from app.models.pet import PetActivity
    cn_tz = timezone(timedelta(hours=8))
    today_start = datetime.now(cn_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = today_start.astimezone(timezone.utc).replace(tzinfo=None)
    async with async_session_factory() as db:
        return (await db.execute(
            select(func.count(PetActivity.id)).where(
                PetActivity.user_id == user_id,
                PetActivity.action == "visit",
                PetActivity.created_at >= today_start,
            )
        )).scalar() or 0


async def collect_pet_visit_events() -> list[dict]:
    """AI 宠物来访（低频）：用户有宠物且有角色养了 AI 宠物 → 概率 + 每日 <=1 → 候选"""
    import random
    async with async_session_factory() as db:
        user_pets = (await db.execute(
            select(Pet).where(or_(Pet.owner_type.is_(None), Pet.owner_type == "user"))
        )).scalars().all()
        ai_pets = (await db.execute(select(Pet).where(Pet.owner_type == "ai"))).scalars().all()
    by_user = {p.user_id for p in user_pets}
    events = []
    for ai_pet in ai_pets:
        uid = ai_pet.user_id
        if uid not in by_user:
            continue
        if await _pet_visit_daily_count(uid) >= PET_VISIT_DAILY_LIMIT:
            continue
        if random.random() > PET_VISIT_PROBABILITY:
            continue
        events.append({
            "type": PET_VISIT_TYPE, "priority": 0.5,
            "candidate": {"character_id": ai_pet.owner_id, "user_id": uid, "ai_pet_id": ai_pet.id},
        })
    if events:
        _logger.info("Pet visit candidates: %d", len(events))
    return events


async def run_pet_visit(char_id: int, user_id: int, ai_pet_id: int) -> bool:
    """AI 宠物来访：写互动展示区记录 + 记忆（不发消息，低打扰）"""
    from app.application import pet_service

    async with async_session_factory() as db:
        ai_pet = await db.get(Pet, ai_pet_id)
        if ai_pet is None or ai_pet.owner_type != "ai" or ai_pet.owner_id != char_id:
            return False
        char = await db.get(AICharacter, char_id)
        user_pet = (await db.execute(
            select(Pet).where(
                Pet.user_id == user_id,
                or_(Pet.owner_type.is_(None), Pet.owner_type == "user"),
            ).order_by(Pet.id.asc()).limit(1)
        )).scalar_one_or_none()
    if user_pet is None:
        return False
    char_name = char.name if char else "AI"
    content = f"{char_name}的宠物{ai_pet.name}（{_species_cn(ai_pet.species)}）来家里拜访啦"
    await pet_service.log_activity(user_pet.id, user_id, "visit", content)
    try:
        from app.memory import save_memory
        # 拜访事件只写入宠物主人角色（这是我的宠物去你家了），不扩散到其他角色，避免归属串扰
        await save_memory(
            user_id=user_id, character_id=char_id, memory_type="event",
            content=f"{char_name}的宠物{ai_pet.name}来用户家拜访了",
            importance=1, source="pet", skip_dedup=True,
            speaker_id=char_id, speaker_type="character", epistemic_status="FACT",
        )
    except Exception as e:
        _logger.warning("Pet visit memory failed: %s", e)
    _logger.info("Pet visit recorded user=%d char=%d ai_pet=%d", user_id, char_id, ai_pet_id)
    return True
