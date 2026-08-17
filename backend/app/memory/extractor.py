import asyncio
import time
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from app.db.database import async_session_factory
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession
from app.models.character import AICharacter
from app.models.processed_extraction import ProcessedExtraction
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.agent.llm_client import chat_completion as llm_call
from app.events.schema import EPISTEMIC_FACT, EPISTEMIC_INFERRED
from app.utils.logger import get_logger

_logger = get_logger("memory.extractor")
BATCH_SIZE = 2
EXTRACT_MAX_TOKENS = 512  # v4-flash 是推理模型：200 会被 reasoning 全部吃掉导致空输出
_pending = {}
_catchup_lock = asyncio.Lock()
_pending_ids: set[int] = set()  # 队列中待提取的源消息 id（catchup 跳过防重复）
EXTRACT_THROTTLE_SECONDS = 1800  # 2026-08-08：同角色 30 分钟最多提取一批
MAX_PENDING_PAIRS = 10         # 队列上限：达到强制提取（防节流期间无限累积）
MAX_PENDING_AGE = 600          # 队列滞留超时（秒）：凑不满批次时超时强制提取（2026-08-16 审计，防 source_id 永久占位）
_last_batch_at: dict[int, float] = {}  # character_id -> 上次批量提取时间

EXTRACT_PROMPT = """今天是{today}。从以下对话中提取值得记住的信息。用AI第一人称视角描述。

对话：
{conversation}

输出格式（每行一个，没有则写“无”）：
USER_INFO: 内容 | 重要程度(1-5)
EVENTS: 内容 | 重要程度(1-5)
PREFERENCES: 内容 | 重要程度(1-5)
BIO: 内容 | 重要程度(1-5)
STATUS: 内容 | 重要程度(1-5)
RELATIONSHIP: 内容 | 重要程度(1-5)
STAGE: 内容 | 重要程度(1-5)

重要程度：1=无关紧要 2=普通 3=重要 4=很重要 5=极其重要
每条控制在75字以内。
时间规则（2026-08-17）：用户说的"今天/昨天/前天/上周/最近"等相对时间，提取时写成具体日期（如 2026年8月16日）；不确定具体日期的用"前几天/前段时间"等中性表述，禁止原样保留相对时间词。
只提取对话中真实出现的事实信息点（用户喜好/经历/关系变化/重要事件/角色新设定），禁止抄录对话原文、角色扮演台词、动作描写（如“（笑了笑）”“（凑近）”）。输出的是提炼后的记忆，不是原文引用。如果对话重申、纠正了之前提过的同一信息，仍按最新表述输出即可（系统会自动合并更新，不要为了显得不同而改写意思）。
STAGE（舞台）专用于记录“非真实/临时性”内容，仅两类：
1) 小游戏内容：玩游戏（真心话大冒险、你问我答、互动小游戏、剧情游戏等）时发生的临时设定、游戏剧情、游戏内承诺与惩罚（如“学猫叫”“输了答应一个要求”“这轮算你赢”），只要对话在玩游戏，相关内容就归 STAGE；
2) AI 判别出的假话/虚构：对话中明显是玩笑、反话、虚构情节或用户明确说假的内容（如“我失忆了”但明显是玩笑）。
除此之外的日常对话（包括拥抱、照顾、一起洗澡、亲昵等角色互动）都是真实发生的，按常规类别正常记录，不要归入 STAGE。没有上述两类内容则写“无”。
用户性别与关系以对话开头的用户画像为准：用户为男性或关系未明确时，禁止用“她”指代用户，一律用“他”或用户昵称。
额外要求（2026-08-08 收紧）：同一信息在对话中出现多次时只提取一次；只提取用户明确说出或明确发生的事实，禁止推测、脑补或把AI猜测当事实（AI回复中提到的内容不代表用户喜欢/拥有它）；信息不明确时写"无"。
PREFERENCES 归属（2026-08-16 修复）：必须带主语——用户说出的偏好以"用户"开头（如：用户喜欢喝美式咖啡），你自己（角色）的偏好以"我"开头（如：我喜欢吃辣）；禁止无主语。"""

from app.memory.dialogue_filter import looks_like_raw_dialogue

def _get_val(response, key):
    for line in response.split("\n"):
        if line.strip().startswith(key + ":"):
            raw = line.strip()[len(key)+1:].strip()
            if " | " in raw:
                parts = raw.rsplit(" | ", 1)
                return parts[0].strip()
            return raw
    return ""

def _extract_epistemic(user_msg, ai_msg, user_id, character_id):
    """对话记忆提取的认知状态标注（World & Cognition P3）。
    - 用户消息在场 → 内容出自用户（FACT，speaker=user）
    - 仅 AI 单方内容 → AI 的表述（INFERRED，speaker=character）
    """
    if (user_msg or "").strip():
        return "user", user_id, EPISTEMIC_FACT
    return "character", character_id, EPISTEMIC_INFERRED


def _resolve_speaker(content, user_msg, ai_msg, user_id, character_id):
    """逐条解析记忆归属（2026-08-16 修复：批级判断把角色/用户偏好搞混）：
    - 内容以「我」开头 → 角色自己的表述（character, INFERRED）
    - 内容以「用户/对方/他/她」开头或前 8 字含「用户」 → 用户（user, FACT）
    - 无法判断 → 回退批级（用户消息在场=user，否则 character）
    """
    text = (content or "").strip()
    if text.startswith("我"):
        return "character", character_id, EPISTEMIC_INFERRED
    if text.startswith(("用户", "对方", "他", "她")) or "用户" in text[:8]:
        return "user", user_id, EPISTEMIC_FACT
    if (user_msg or "").strip():
        return "user", user_id, EPISTEMIC_FACT
    return "character", character_id, EPISTEMIC_INFERRED


def _is_empty_val(val: str) -> bool:
    """提取结果为空值标记（LLM 未输出有效内容：无/（无）/（空）等）"""
    return (val or "").strip() in {"无", "（无）", "(无)", "空", "（空）", "(空)", "无。", "（无）。", "空。"}


def _get_imp(response, key):
    """从 KEY: 内容 | N 格式中提取重要程度"""
    for line in response.split("\n"):
        if line.strip().startswith(key + ":"):
            raw = line.strip()[len(key)+1:].strip()
            if " | " in raw:
                parts = raw.rsplit(" | ", 1)
                try:
                    val = int(parts[1].strip())
                    return max(1, min(5, val))
                except:
                    pass
            return 2
    return 2

async def extract_single(session_id, character_id, user_id, user_msg, ai_msg, source_id=None):
    conv = f"用户: {user_msg[:150]}\nAI: {ai_msg[:150]}"
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.user import User
        from app.models.character import AICharacter
        from app.agent.user_profile import gender_cn
        async with async_session_factory() as db:
            user = await db.get(User, user_id)
            ch = await db.get(AICharacter, character_id)
            partners = (
                await db.execute(select(AICharacter).where(
                    AICharacter.user_id == user_id, AICharacter.is_partner == True
                ))
            ).scalars().all()
        parts = []
        if user:
            parts.append(f"用户性别: {gender_cn(user.gender)}")
        if partners:
            pp = partners[0]
            parts.append(f"用户的对象: {pp.name}（{gender_cn(pp.gender)}），对象不是当前AI")
        if ch:
            rt = ch.relation_type or "朋友"
            parts.append(f"当前AI角色({ch.name})与用户的关系: {rt}")
        if parts:
            conv = f"用户画像(仅用于区分身份；提取记忆时不要把其他角色的事记到当前AI头上): {'；'.join(parts)}\n{conv}"
    except Exception:
        pass
    _bj = datetime.now(timezone(timedelta(hours=8)))
    _today_str = f"{_bj.year}年{_bj.month}月{_bj.day}日"
    prompt = EXTRACT_PROMPT.format(conversation=conv, today=_today_str)
    response = await llm_call(messages=[{"role":"user","content":prompt}], temperature=0.1, max_tokens=EXTRACT_MAX_TOKENS)
    _logger.info("Raw: %.120s", response)
    if not response: return 0

    saved = 0

    # 舞台/扮演记忆：分流到 stage_memories（不进入常规记忆库，防止游戏/假话内容污染真实记忆）
    import re as _re
    _game_text = f"{user_msg or ''} {ai_msg or ''}"
    _game_convo = bool(_re.search(r"(真心话|大冒险|你问我答|互动小游戏|扮演游戏|玩.{0,8}(?:游戏|大冒险))", _game_text))
    def _is_game_content(val: str) -> bool:
        return bool(_re.search(r"(真心话|大冒险|你问我答|这轮|这局|游戏|惩罚|承诺|扮演|输了|赢了)", val or ""))
    stage_val = _get_val(response, "STAGE")
    if stage_val and stage_val != "无" and len(stage_val) >= 2 and not looks_like_raw_dialogue(stage_val):
        stage_imp = _get_imp(response, "STAGE")
        try:
            from app.models.stage_memory import StageMemory
            async with async_session_factory() as db:
                db.add(StageMemory(
                    user_id=user_id, character_id=character_id, session_id=session_id,
                    content=stage_val[:200], stage_kind="roleplay",
                    importance=stage_imp, source_id=source_id,
                ))
                await db.commit()
            saved += 1
            _logger.info("Stage saved char=%d: %.60s", character_id, stage_val)
        except Exception as e:
            _logger.warning("Stage save failed char=%d: %s", character_id, e)

    for key, mtype in [("USER_INFO","user_info"),("EVENTS","event"),("PREFERENCES","preference")]:
        val = _get_val(response, key)
        if val and len(val) >= 2 and not looks_like_raw_dialogue(val) and not _is_empty_val(val):
            imp = _get_imp(response, key)
            # 游戏轮兜底：LLM 未归 STAGE 但明确是小游戏相关内容时强制转舞台
            if _game_convo and _is_game_content(val):
                try:
                    from app.models.stage_memory import StageMemory
                    async with async_session_factory() as db:
                        db.add(StageMemory(
                            user_id=user_id, character_id=character_id, session_id=session_id,
                            content=val[:200], stage_kind="game",
                            importance=imp, source_id=source_id,
                        ))
                        await db.commit()
                    saved += 1
                    _logger.info("Stage(game) saved char=%d: %.60s", character_id, val)
                except Exception as e:
                    _logger.warning("Stage(game) save failed char=%d: %s", character_id, e)
                continue
            from app.memory import save_memory
            _spk_type, _spk_id, _epi = _resolve_speaker(val, user_msg, ai_msg, user_id, character_id)
            await save_memory(user_id=user_id,character_id=character_id,memory_type=mtype,content=val[:100],importance=imp,source="chat",sub_type="extracted",source_id=source_id,
                              speaker_type=_spk_type, speaker_id=_spk_id, epistemic_status=_epi)
            saved += 1

    # BIO 只更新角色自述 self_statement（不写记忆、不覆盖用户背景信息 bio，2026-08-14 拍板）；STATUS 只更新 current_status；RELATIONSHIP 存为 insight（同时更新关系描述）
    bio_val = _get_val(response, "BIO")
    if bio_val and len(bio_val) >= 2 and not looks_like_raw_dialogue(bio_val):
        from app.models.character import AICharacter
        async with async_session_factory() as db:
            c = (await db.execute(select(AICharacter).where(AICharacter.id == character_id))).scalar_one_or_none()
            if c: c.self_statement = bio_val[:500]; await db.commit()
        saved += 1

    status_val = _get_val(response, "STATUS")
    if status_val and len(status_val) >= 2:
        from app.models.character import AICharacter
        async with async_session_factory() as db:
            c = (await db.execute(select(AICharacter).where(AICharacter.id == character_id))).scalar_one_or_none()
            if c:
                c.current_status = status_val[:200]
                await db.commit()
        saved += 1


    rel = _get_val(response, "RELATIONSHIP")
    if rel and rel != "无" and len(rel) >= 2 and not looks_like_raw_dialogue(rel):
        imp = _get_imp(response, "RELATIONSHIP")
        from app.memory import save_memory
        _spk_type, _spk_id, _epi = _resolve_speaker(rel, user_msg, ai_msg, user_id, character_id)
        await save_memory(user_id=user_id,character_id=character_id,memory_type="insight",content="关系: "+rel[:100],importance=imp,source="chat",sub_type="relationship",source_id=source_id,
                          speaker_type=_spk_type, speaker_id=_spk_id, epistemic_status=_epi)
        from app.models.character import AICharacter
        async with async_session_factory() as db:
            c = (await db.execute(select(AICharacter).where(AICharacter.id == character_id))).scalar_one_or_none()
            if c: c.relationship_summary = rel[:200]; await db.commit()
        saved += 1

    if saved: _logger.info("Extracted %d for char=%d", saved, character_id)
    # 无论是否有值得记的信息，只要 LLM 返回了有效文本就标记已处理（避免 catchup 重复提取）
    if source_id is not None:
        await _mark_processed(source_id)
    return saved

def _pending_remove_uid(session_id: int, uid: int) -> None:
    """从主链路队列移除已提取的源消息（catchup 提取后调用，防主链路重复提取）"""
    if session_id in _pending:
        _pending[session_id] = [p for p in _pending[session_id] if p.get("source_id") != uid]


async def add_chat_memory_extraction(session_id, character_id, user_id, user_msg, ai_msg, source_id=None):
    if session_id not in _pending: _pending[session_id] = []
    _now = time.time()
    _pending[session_id].append({"user_message":user_msg,"ai_response":ai_msg,"source_id":source_id, "ts": _now})
    if source_id is not None:
        _pending_ids.add(source_id)
    _q = _pending[session_id]
    _stale = any(_now - float(p.get("ts") or _now) > MAX_PENDING_AGE for p in _q)  # 2026-08-16 审计：滞留超时强制提取
    if len(_q) < BATCH_SIZE and not _stale:
        return
    # 2026-08-08 节流：同角色 30 分钟最多提取一批；节流中继续累积，达到上限/滞留超时强制提取
    if _now - _last_batch_at.get(character_id, 0) < EXTRACT_THROTTLE_SECONDS and len(_q) < MAX_PENDING_PAIRS and not _stale:
        return
    pairs = _pending.pop(session_id)
    _last_batch_at[character_id] = time.time()
    _logger.info("Batch: session=%d count=%d", session_id, len(pairs))
    for p in pairs:
        try:
            # P0-1b（2026-08-16）：记忆提炼经内部统一工具入口（生命周期/事件/异常隔离）
            from app.agent.internal_runner import run_internal
            await run_internal("memory_extract", {
                "session_id": session_id, "character_id": character_id, "user_id": user_id,
                "user_msg": p["user_message"], "ai_msg": p["ai_response"],
                "source_id": p.get("source_id"),
            }, character_id=character_id, user_id=user_id)
        finally:
            if p.get("source_id") is not None:
                _pending_ids.discard(p["source_id"])
        await asyncio.sleep(0.3)

async def _mark_processed(source_id: int):
    """记录该用户消息已完成记忆提取（幂等）"""
    try:
        async with async_session_factory() as db:
            stmt = sqlite_insert(ProcessedExtraction).values(user_message_id=source_id).prefix_with("OR IGNORE")
            await db.execute(stmt)
            await db.commit()
    except Exception as e:
        _logger.warning("Mark processed failed id=%s: %s", source_id, e)


async def _load_processed_ids() -> set[int]:
    async with async_session_factory() as db:
        rows = (await db.execute(select(ProcessedExtraction.user_message_id))).scalars().all()
    return set(rows)


async def catchup_extract_all():
    """补采最近 2 小时未提取的消息对；带防重入 + 已处理去重（修复每 15 分钟重复扫描同一批消息的问题）"""
    if _catchup_lock.locked():
        _logger.info("Catchup skipped: previous run still active")
        return
    async with _catchup_lock:
        _logger.info("Catchup")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        processed = await _load_processed_ids()
        async with async_session_factory() as db:
            # 只补采仍处于活跃状态的角色的会话（过滤已删除角色，避免给残留角色写记忆）
            sessions = (await db.execute(
                select(ChatSession)
                .join(AICharacter, AICharacter.id == ChatSession.character_id)
                .where(ChatSession.is_active == True, AICharacter.is_active == True)
            )).scalars().all()
        for s in sessions:
            try:
                # 2026-08-08 节流：与主链路同节奏，同角色 30 分钟最多补采一批
                if time.time() - _last_batch_at.get(s.character_id, 0) < EXTRACT_THROTTLE_SECONDS:
                    continue
                async with async_session_factory() as db:
                    msgs = (await db.execute(select(ChatMessage).where(ChatMessage.session_id == s.id, ChatMessage.created_at >= cutoff).order_by(ChatMessage.created_at.asc()))).scalars().all()
                new_pairs = 0
                for i in range(len(msgs)-1):
                    if msgs[i].sender_type=="user" and msgs[i+1].sender_type=="ai":
                        uid = msgs[i].id
                        if uid in processed or uid in _pending_ids:
                            continue
                        await extract_single(s.id, s.character_id, s.user_id, msgs[i].content, msgs[i+1].content, source_id=uid)
                        processed.add(uid)
                        new_pairs += 1
                        _pending_remove_uid(s.id, uid)
                        await asyncio.sleep(0.3)
                if new_pairs:
                    _last_batch_at[s.character_id] = time.time()
                    _logger.info("Catchup session=%d new_pairs=%d", s.id, new_pairs)
            except Exception as e:
                _logger.warning("Catchup s=%d: %s", s.id, e)
        _logger.info("Catchup done")
