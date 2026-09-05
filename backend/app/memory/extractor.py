import asyncio
import time
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from app.db.database import async_session_factory
from app.models.chat import ChatMessage
from app.models.chat import ChatSession
from app.models.character import AICharacter
from app.models.memory import ProcessedExtraction
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.agent.llm_client import chat_completion as llm_call, TASK_MEMORY
from app.events.schema import EPISTEMIC_FACT, EPISTEMIC_INFERRED
from app.utils.logger import get_logger
from app.memory.speaker import resolve_speaker_from_content  # X-2（2026-08-18）：统一归属判定公共函数

_logger = get_logger("memory.extractor")
BATCH_SIZE = 4  # C1（2026-08-18 降本）：2->4 条/批，调用次数 -30~50%，30min 节流不变
EXTRACT_MAX_TOKENS = 512  # v4-flash 是推理模型：200 会被 reasoning 全部吃掉导致空输出
_pending = {}
_catchup_lock = asyncio.Lock()
_pending_ids: set[int] = set()  # 队列中待提取的源消息 id（catchup 跳过防重复）
EXTRACT_THROTTLE_SECONDS = 1800  # 2026-08-08：同角色 30 分钟最多提取一批
MAX_PENDING_PAIRS = 10         # 队列上限：达到强制提取（防节流期间无限累积）
MAX_PENDING_AGE = 600          # 队列滞留超时（秒）：凑不满批次时超时强制提取（2026-08-16 审计，防 source_id 永久占位）
_last_batch_at: dict[int, float] = {}  # character_id -> 上次批量提取时间
SELF_STATEMENT_MAX_LEN = 200  # 自述（self_statement）正文长度上限（2026-08-23：控制篇幅，正文 ≤200 字，分段保留）

EXTRACT_PROMPT = """今天是{today}。从以下对话中提取值得记住的信息。描述视角：角色自己的内容用「我」第一人称，关于用户的信息必须用「用户」作主语（主语规则见末段）。

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
CURATED: 长期稳定、几乎不会变的信息（用户硬档案/长期偏好/你们关系的稳定基线/你必须遵守的铁律）| 类别(fact/preference_profile/relationship_baseline/constraint) | 重要程度(1-5)
INTENT: 面向未来的承诺/约定/到某个线索出现时要做的事（如"下周末带你去吃火锅""樱花开了提醒我拍照"）；纯当下事实不要写 | 类型(promise/cue) | 时间窗(YYYY-MM-DD~YYYY-MM-DD，不确定写无) | 线索词(逗号分隔，cue 必填，promise 可写无)

重要程度：1=无关紧要 2=普通 3=重要 4=很重要 5=极其重要
每条控制在75字以内。
BIO/RELATIONSHIP 规则（2026-08-18）：BIO 输出角色自述的完整最新表述（包含此前已确立的所有要点，不要只写本轮新出现的；若本轮有变化，在原基础上补充而非替换）；RELATIONSHIP 输出关系摘要的完整最新表述（合并此前关系要点，不要只写本轮内容）。BIO 与 RELATIONSHIP 各控制在 200 字以内，内容多时提炼精要（2026-08-23：控制自述与关系摘要篇幅）。
时间规则（2026-08-17）：用户说的"今天/昨天/前天/上周/最近"等相对时间，提取时写成具体日期（如 2026年8月16日）；不确定具体日期的用"前几天/前段时间"等中性表述，禁止原样保留相对时间词。
只提取对话中真实出现的事实信息点（用户喜好/经历/关系变化/重要事件/角色新设定），禁止抄录对话原文、角色扮演台词、动作描写（如“（笑了笑）”“（凑近）”）。输出的是提炼后的记忆，不是原文引用。如果对话重申、纠正了之前提过的同一信息，仍按最新表述输出即可（系统会自动合并更新，不要为了显得不同而改写意思）。
STAGE（舞台）专用于记录“非真实/临时性”内容，仅两类：
1) 小游戏内容：玩游戏（真心话大冒险、你问我答、互动小游戏、剧情游戏等）时发生的临时设定、游戏剧情、游戏内承诺与惩罚（如“学猫叫”“输了答应一个要求”“这轮算你赢”），只要对话在玩游戏，相关内容就归 STAGE；
2) AI 判别出的假话/虚构：对话中明显是玩笑、反话、虚构情节或用户明确说假的内容（如“我失忆了”但明显是玩笑）。
除此之外的日常对话（包括拥抱、照顾、一起洗澡、亲昵等角色互动）都是真实发生的，按常规类别正常记录，不要归入 STAGE。没有上述两类内容则写“无”。
用户性别与关系以对话开头的用户画像为准：用户为男性或关系未明确时，禁止用“她”指代用户，一律用“他”或用户昵称。
额外要求（2026-08-08 收紧）：同一信息在对话中出现多次时只提取一次；只提取用户明确说出或明确发生的事实，禁止推测、脑补或把AI猜测当事实（AI回复中提到的内容不代表用户喜欢/拥有它）；信息不明确时写"无"。
主语规则（2026-08-18 强化）：USER_INFO/EVENTS/PREFERENCES 条目中，关于用户的信息必须以「用户」开头或明确写出主语（如「用户喜欢喝美式咖啡」「用户说下周出差」「用户上周去了海边」），禁止用裸「我」开头描述用户的事（如「我注意到用户…」「我发现你…」应改写为「用户…」）；角色自己的设定与偏好用「我」开头（如「我喜欢吃辣」）；「我们一起/我们」类共同事件（如「我们一起看了海」）按对话归属：对话中有用户发言 → 归用户（写「用户」主语），仅 AI 单方内容 → 归角色。禁止无主语。
CURATED 只收「长期稳定/可编纂」的信息，一次性情绪、临时状态、短期事件不要写 CURATED；没有写"无"。
INTENT 只在用户明确表达了"未来要兑现的承诺/约定"或"某线索出现时提醒/做某事"时写一条；没有写"无"。拿不准时间就把时间窗写"无"、并给出≥2个线索词；既无时间又无线索、或只是随口一说的，不要写。"""

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
    """逐条解析记忆归属（2026-08-16 修复：批级判断把角色/用户偏好搞混）。
    X-2（2026-08-18）：判定逻辑收敛至 app.memory.speaker.resolve_speaker_from_content
    （推断词优先 → 「我」前缀 → 用户指代前缀 → 批级回退），本函数保留为薄封装以兼容既有调用与测试。
    """
    return resolve_speaker_from_content(content, user_msg, ai_msg, user_id, character_id)


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
                except ValueError:
                    pass
            return 2
    return 2


def _raw_val(response, key):
    """取 KEY: 整行值（不剥最后一列；INTENT 线索词是末列，不能走 _get_val 的剥离）。无则空串。"""
    for line in response.split("\n"):
        if line.strip().startswith(key + ":"):
            return line.strip()[len(key)+1:].strip()
    return ""


# ── Ariadne 模块F/G：extractor 便车解析（2026-09-04，行协议新增两行输出）──
def _parse_curated_line(response: str):
    """CURATED: 内容 | 类别 | N → (content, kind, imp)；无则 None。

    类别白名单：fact/preference_profile/relationship_baseline/constraint（非法回落 fact）。
    """
    raw = _get_val(response, "CURATED")
    if not raw or _is_empty_val(raw):
        return None
    parts = [p.strip() for p in raw.split("|")]
    # _get_val 已剥掉重要度，这里形如 "内容 | 类别"
    content = parts[0]
    kind = parts[1] if len(parts) > 1 else "fact"
    imp = _get_imp(response, "CURATED")
    if kind not in ("fact", "preference_profile", "relationship_baseline", "constraint"):
        kind = "fact"
    return content, kind, imp


def _parse_intent_line(response: str):
    """INTENT: 内容 | 类型 | 时间窗 | 线索 → dict / None。时间窗解析失败返回 None 时间。

    用 _raw_val 取整行（线索词是末列，_get_val 会误剥），四种字段独立解析。
    """
    raw = _raw_val(response, "INTENT")
    if not raw or _is_empty_val(raw):
        return None
    parts = [p.strip() for p in raw.split("|")]
    content = parts[0]
    if not content or len(content) < 3:
        return None
    kind = parts[1] if len(parts) > 1 and parts[1] in ("promise", "cue") else "promise"
    win = parts[2] if len(parts) > 2 else "无"
    cues_raw = parts[3] if len(parts) > 3 else "无"
    due_start = due_end = None
    if win and win != "无" and "~" in win:
        try:
            a, b = [x.strip() for x in win.split("~", 1)]
            due_start = datetime.strptime(a, "%Y-%m-%d")
            due_end = datetime.strptime(b, "%Y-%m-%d").replace(hour=23, minute=59)
        except Exception:
            due_start = due_end = None
    cues = []
    if cues_raw and cues_raw != "无":
        cues = [c.strip() for c in cues_raw.replace("，", ",").split(",") if c.strip()]
    return {"content": content, "kind": kind, "due_start": due_start, "due_end": due_end, "cue_terms": cues}


def _truncate_sample(text: str, head: int = 100, tail: int = 100) -> str:
    """长文本头尾采样（M-P2-4）：超过 head+tail 字时保留头部 head 字与尾部 tail 字，
    中间用省略号连接，避免长倾诉消息的关键尾部信息被 150 字截断丢掉。
    """
    t = text or ""
    if len(t) <= head + tail:
        return t
    return t[:head] + "…" + t[-tail:]


def _merge_profile_text(current: str | None, new: str, max_len: int) -> str:
    """BIO/RELATIONSHIP 保守合并（M-P1-3，2026-08-18）：
    - 现值缺失 → 直接用新值（截断到上限）；
    - 新值长度 >= 现值长度 → 视为 LLM 输出了完整最新表述，替换现值；
    - 新值更短 → 可能只写了本轮新增/单侧面，保留现值并追加新值（防多面信息丢失）。
    追加用空行「\n\n」分段（自述/关系描述多段更可读），整体截断到 max_len。
    """
    cur = (current or "").strip()
    new = (new or "").strip()
    if not cur:
        return new[:max_len]
    if not new:
        return cur[:max_len]
    if len(new) >= len(cur):
        return new[:max_len]
    return (cur + "\n\n" + new)[:max_len]


async def extract_single(session_id, character_id, user_id, user_msg, ai_msg, source_id=None):
    conv = f"用户: {_truncate_sample(user_msg)}\nAI: {_truncate_sample(ai_msg)}"
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
    # §20（2026-09-04）：开 global_user_facts 时，借用同一次提取让 LLM 多吐一个 SLOT 归槽字段
    # （不新增 LLM 调用）；关=原 prompt 逐字节一致（零行为变化）。
    try:
        from app.agent.loop import AGENT_FLAGS as _flags_now
        if bool(_flags_now.get("global_user_facts", False)):
            prompt += ("\n额外要求：若上方 USER_INFO 属于用户可变近况（所在城市/工作学业/感情状态/"
                       "居住情况/进行中计划/身体状态），另输出一行：SLOT: location|job|relationship|"
                       "living|goal_state|health；否则输出：SLOT: 无。")
    except Exception:
        pass
    response = await llm_call(messages=[{"role":"user","content":prompt}], temperature=0.1, max_tokens=EXTRACT_MAX_TOKENS, task=TASK_MEMORY)
    _logger.info("Raw: %.120s", response)
    if not response: return 0

    saved = 0

    # Ariadne 模块F/G（2026-09-04）：flag 预取（关=零行为变化）+ curated 提前解析（供 PREFERENCES 互斥）。
    from app.agent.loop import AGENT_FLAGS as _flags
    _curated_enabled = bool(_flags.get("curated_knowledge", False))
    _intent_enabled = bool(_flags.get("prospective_intent_enabled", False))
    _curated_added: set[str] = set()   # 已被 assert_curated 收录的 content（PREFERENCES 互斥用）
    _curated_item = None               # (content, kind, imp)
    if _curated_enabled:
        try:
            _cu = _parse_curated_line(response)
            if _cu and not looks_like_raw_dialogue(_cu[0]):
                _content, _kind, _imp = _cu
                if _imp >= 4:
                    _curated_item = (_content, _kind, _imp)
                    _curated_added.add((_content or "").strip())
        except Exception as e:
            _logger.warning("Curated parse failed char=%d: %s", character_id, e)

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
            from app.models.memory import StageMemory
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
                    from app.models.memory import StageMemory
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
            # Ariadne 模块F（2026-09-04）：被升级为 curated 的长期偏好不再重复进衰减记忆
            # （一期只对 PREFERENCES 互斥，USER_INFO 保留双写观察后再收紧；flag 关时恒 False 零行为）
            if key == "PREFERENCES" and _curated_enabled and any(
                c and (c in val or val in c) for c in _curated_added
            ):
                continue
            from app.memory import save_memory
            _spk_type, _spk_id, _epi = _resolve_speaker(val, user_msg, ai_msg, user_id, character_id)
            # §20（2026-09-04）：global_user_facts 开且为 USER_INFO → 归槽 upsert 用户级事实 + 旧值失效；
            # 关=原 sub_type="extracted" 路径（逐字节一致，零行为变化）。
            if mtype == "user_info" and bool(_flags.get("global_user_facts", False)):
                from app.memory.user_facts import MUTABLE_SLOTS, classify_slot, upsert_user_fact
                slot_raw = (_get_val(response, "SLOT") or "").strip()
                slot = slot_raw if slot_raw in MUTABLE_SLOTS else classify_slot(val)
                if slot:
                    change = await upsert_user_fact(user_id, slot, val, source="chat")
                    # 旧值失效放「新记忆写入前」：避免 sub_type/文本命中到刚写入的新值记忆误标 stale
                    if change is not None:
                        from app.memory.cross_char_sync import stale_character_slot_memory
                        await stale_character_slot_memory(character_id, slot, change[0])
                    await save_memory(user_id=user_id,character_id=character_id,memory_type=mtype,content=val[:100],importance=imp,source="chat",sub_type=slot,source_id=source_id,
                                      speaker_type=_spk_type, speaker_id=_spk_id, epistemic_status=_epi)
                    saved += 1
                    continue
            await save_memory(user_id=user_id,character_id=character_id,memory_type=mtype,content=val[:100],importance=imp,source="chat",sub_type="extracted",source_id=source_id,
                              speaker_type=_spk_type, speaker_id=_spk_id, epistemic_status=_epi)
            saved += 1

    # BIO 只更新角色自述 self_statement（不写记忆、不覆盖用户背景信息 bio，2026-08-14 拍板）；STATUS 只更新 current_status；RELATIONSHIP 存为 insight（同时更新关系描述）
    bio_val = _get_val(response, "BIO")
    if bio_val and len(bio_val) >= 2 and not looks_like_raw_dialogue(bio_val):
        from app.models.character import AICharacter
        async with async_session_factory() as db:
            c = (await db.execute(select(AICharacter).where(AICharacter.id == character_id))).scalar_one_or_none()
            if c:
                # M-P1-3：覆盖写 → 保守合并（新值长度 >= 现值时替换，否则保留现值 + 追加新值），
                # 防单批只输出单侧面时把角色既有自述多面信息覆盖丢失。
                c.self_statement = _merge_profile_text(c.self_statement, bio_val, SELF_STATEMENT_MAX_LEN)
                await db.commit()
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
            if c:
                # M-P1-3：同上保守合并，防关系摘要多面信息被单行覆盖丢失。
                c.relationship_summary = _merge_profile_text(c.relationship_summary, rel, 200)
                await db.commit()
        saved += 1

    # ── Ariadne 模块F：长期编纂知识便车（flag 关=零行为）──
    if _curated_enabled and _curated_item is not None:
        try:
            content, kind, imp = _curated_item
            from app.events.facts import assert_curated
            async with async_session_factory() as db:
                await assert_curated(
                    db, character_id=character_id, user_id=user_id, kind=kind,
                    object_value=content, predicate="curated",
                    source="extractor", source_event_id=str(source_id) if source_id else None,
                    sources=[{"src": "chat_extract", "message_id": source_id, "imp": imp}],
                )
                await db.commit()
            saved += 1
        except Exception as e:
            _logger.warning("Curated extract failed char=%d: %s", character_id, e)

    # ── Ariadne 模块G：前瞻意图便车（零新增 LLM；写入 flag 关=零行为）──
    if _intent_enabled:
        try:
            pi = _parse_intent_line(response)
            # 落地审查派工（2026-09-06）：INTENT 输出观测——Raw 日志仅前 120 字符看不到尾部
            # INTENT 行，trigger 段拍板（G 前瞻触发段 3-7 天观察）需要「输出率/写出率」数据。
            # 观测自带内层兜底：obs 异常绝不影响 upsert 主链路（fail-open）。
            try:
                from app.memory.observability import obs_event
                obs_event(character_id, "prospective_intent_extract",
                          {"written": bool(pi and pi.get("content")), "kind": (pi or {}).get("kind"),
                           "content": ((pi or {}).get("content") or "")[:60]})
            except Exception:
                pass
            if pi and not looks_like_raw_dialogue(pi["content"]):
                from app.scheduling.prospective_intent import upsert_intent
                await upsert_intent(
                    user_id=user_id, character_id=character_id, content=pi["content"],
                    kind=pi["kind"], cue_terms=pi["cue_terms"],
                    due_start=pi["due_start"], due_end=pi["due_end"],
                    source_message_id=source_id, chat_session_id=session_id,
                )
        except Exception as e:
            _logger.warning("Prospective intent extract failed char=%d: %s", character_id, e)

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


def _pair_user_ai(msgs) -> list[tuple]:
    """把消息序列配对为「用户消息, 其后第一条 AI 消息」对（M-P2-4）：
    跳过中间连续的用户消息，保证 [U1, U2, A1] 时 U1 也能与 A1 配对提取。
    """
    pairs: list[tuple] = []
    for i, m in enumerate(msgs):
        if getattr(m, "sender_type", None) != "user":
            continue
        ai = next((nxt for nxt in msgs[i + 1:] if getattr(nxt, "sender_type", None) == "ai"), None)
        if ai is not None:
            pairs.append((m, ai))
    return pairs


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
                for um, am in _pair_user_ai(msgs):
                    uid = um.id
                    if uid in processed or uid in _pending_ids:
                        continue
                    await extract_single(s.id, s.character_id, s.user_id, um.content, am.content, source_id=uid)
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
