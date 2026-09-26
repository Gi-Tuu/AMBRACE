# -*- coding: utf-8 -*-
"""现状 trace 构造（two-pass POC，2026-09-23）：主动消息生成前的「当前现状速读」临时重读指引。

为什么**只取 active 且置信达标**（硬口径，禁止放宽）：
- 雷达 Random Trace 警示——错误现状会被生成端**系统性放大**（14.2 < 29.2：带错现状比不带现状更糟）。
  所以 ``world_facts`` 只允许 ``status == "active"`` 且 ``confidence >= TRACE_CONFIDENCE_MIN`` 的行进
  trace；**expired / superseded 一律不进**，即便它 ``updated_at`` 更晚、看起来更“新”。

口径与边界（方案书 output/AMBRACE_two-pass_POC方案_20260923.md，本地 output 目录不入公开仓）：
- trace 只作**临时重读指引**：本次生成用完即弃——**不落库、不写记忆、不上屏、不调 LLM**
  （纯规则拼装，额外成本≈0，只增加 prompt 长度）；
- 三个分区固定顺序：现状事实 → 用户槽值 → 未完成计划；任一分区为空则整段省略（不输出空标记）；
  全空返回**空串**；
- 每行 ≤ ``TRACE_LINE_CHARS`` 字符、总长硬上限 ``TRACE_TOTAL_CHARS`` 字符；
- 任何异常一律收敛（返回空串 + WARNING），绝不让主链路崩；
- 退出条件：人工抽查 10 条「现状前置正确且未放大错误现状」≥ 8/10 才进灰度，否则关 flag 回滚
  （POC 期跑完即出结论，不做长期灰度）。
"""
from __future__ import annotations

import re

from sqlalchemy import select

from app.utils.logger import get_logger

_logger = get_logger("scheduling.state_trace")

# 置信地板：现行写入链路（chat_status 0.9 / life_activity 0.85 / 权威与编纂事实 1.0）全部远高于此值，
# 取 0.6 只挡明显推测/降级的行，不误伤真实现状（本模块要的是「不误删」也「不放大旧现状」）。
TRACE_CONFIDENCE_MIN = 0.6
TRACE_LINE_CHARS = 200    # 单行截断
TRACE_TOTAL_CHARS = 1200  # 总长硬上限

# Q1（2026-09-25）：同一类近况把「现状事实」段占满 ⇒ 按谓词收敛名额。
# 依据（生产库 char13 实测）：active 且置信达标的 71 条里 curated 68 条、status/activity/setting
# 各 1 条；旧 trace 8 条中 7 条是 curated（关系亲述 4 条 + 腰伤护理 3 条）——**重复只集中在
# curated**，所以只对 curated 设上限，其它谓词不限（它们本来就各只有一条，设限只会误伤）。
# 判重口径见 _texts_duplicate（D1 起：复用近况同义合并判据，不再只用「规范化后完全相同」）。
TRACE_MAX_PER_PREDICATE = {"curated": 4}
# 先多取再收敛：被跳过（重复 / 超名额）的行不该白占 prompt 名额，多取一批让别的谓词补进来；
# 乘数 3 ≈ 覆盖 curated 的重复密度（68/71），硬上限 30 兜住查询与内存成本（不做无界多取）。
TRACE_FACT_FETCH_MULTIPLIER = 3
TRACE_FACT_FETCH_CAP = 30

_HEADER = "【当前现状速读】（只用于你落笔前对齐此刻的现实，不要逐条复述、不要当成必须完成的任务）"
_SEC_FACTS = "· 现状事实"
_SEC_SLOTS = "· 用户近况"
_SEC_INTENTS = "· 未完成计划"
_SEC_HEADERS = (_SEC_FACTS, _SEC_SLOTS, _SEC_INTENTS)

# world_facts.predicate → 中文标签（未知谓词原样输出，宁漏不编）
# C7（2026-09-24）：补 curated 的中文标签——此前会输出英文「- curated：…」夹在中文 prompt 里。
_PREDICATE_LABEL = {"status": "状态", "activity": "正在做", "location": "位置", "mood": "心情",
                    "curated": "近况"}
# user_facts.slot → 中文标签
_SLOT_LABEL = {
    "location": "位置/城市",
    "job": "工作/学业",
    "relationship": "感情状态",
    "living": "居住情况",
    "goal_state": "进行中计划状态",
    "health": "身体状态",
}


def _get(row, key, default=None):
    """ORM 行 / dict 通用取值（渲染函数因此可脱离数据库直接测）。"""
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _one_line(text, limit: int = TRACE_LINE_CHARS) -> str:
    """压成单行并截断（禁止多行原文塞进 trace 打乱分区结构）。"""
    return " ".join(str(text or "").split())[:limit]


def fact_line(row) -> str:
    """world_facts 行 → trace 行（谓词标签 + 事实值）；无值返回空串。"""
    value = _one_line(_get(row, "object_value"))
    if not value:
        return ""
    predicate = str(_get(row, "predicate") or "").strip()
    label = _PREDICATE_LABEL.get(predicate, predicate)
    return f"- {label}：{value}" if label else f"- {value}"


def _merge_status_row(rows: list, status_row) -> list:
    """C7 保底（2026-09-24）：rows 里没有 predicate=="status" 的行时，把 status_row 插到最前。

    背景：事实按 updated_at desc 取 limit_facts（默认 8）条，char13 的最新若干条几乎
    全是 curated（腰伤/关系），真正的当前 status（例如「正在给轩做腰部护理」）会被挤出；
    这里保证它一定出现在 trace 里。已有 status 行或 status_row 为 None 时原样返回
    （纯函数，便于单测；_get 同时支持 ORM 行与 dict）。
    """
    if any(str(_get(r, "predicate") or "").strip() == "status" for r in rows):
        return rows
    if status_row is None:
        return rows
    return [status_row] + list(rows)


def _norm_fact_text(text) -> str:
    """判重用的规范化：去空白、去中英文标点/符号、转小写（只用于比较，不改原值）。"""
    return re.sub(r"[\W_]+", "", str(text or ""), flags=re.UNICODE).lower()


# ── D1（2026-09-26）同义判重：复用「近况同义合并」既有判据（app.events.facts /
#    app.scheduling.prospective_intent），只在拼装层收敛，不改底层表、不调 LLM。
#    与旧「规范化后完全相同」相比，本判据多拦两类同族重复（生产库 char13 实测）：
#    ①「同一件事换措辞 + 各自补充」（关系亲述族：核心相同、后缀不同）；
#    ②「同一句话里换了可变槽位」——人名（sam）与相对/绝对时间（明早 / 9月27日），
#      这些槽位不改变「说的是同一件事」，故比较前先剥掉，再交给既有判据。
#    剥离槽位后仍走「保守子集」：仅「短串前缀包含」+「近乎逐字重复（0.9）」两条，
#    刻意不含 facts 的「共享核心前缀（C13）」——那会误并「喜欢喝美式咖啡 / 喜欢喝拿铁咖啡」
#    这类同模板不同宾语者；实测本判据对 coffee/不同腰伤等 KEEP 全部不误并。
_ASCII_NAME_RE = re.compile(r"[a-z]+", re.IGNORECASE)   # 拉丁人名 / 句柄（sam、AI 等）
_DATE_ABS_RE = re.compile(r"\d{4}年\d{1,2}月\d{1,2}[日号]|\d{1,2}月\d{1,2}[日号]|\d{4}年")
_TIME_REL_RE = re.compile(
    r"(?:大前天|前天|昨天|昨晚|今晚|今晨|今早|今天|明早|明晚|明天|后天|大后天|"
    r"上午|中午|下午|晚上|早上|凌晨|傍晚|午夜)")
_CLOCK_RE = re.compile(r"[一二三四五六七八九十两\d]{1,3}点(?:半|钟|过|多)?")
_TRACE_MIN_CORE = 6     # 前缀包含最短核心（对齐 facts._MIN_CORE_LEN）
# 近似阈值分档（批 B，2026-09-26）：误并的代价是丢约定/丢频次，宁少合；
# 中长键写死 0.95（等长句差 2 字就可能是不同宾语：快递/外卖、美式/拿铁），
# 短键放宽到 0.9（差一个字＝多加代词/助词，如「甲说晚点再说」vs「甲说他晚点再说」）。
_TRACE_SIMILARITY = 0.95
_TRACE_SIMILARITY_SHORT = 0.9
_SHORT_KEY_LEN = 10    # ≤ 此长度算「短键」：差一个字仍视为近乎逐字重复
# 等长句只差 k 字时 ratio = 1 - k/n，n≥40 时 k=2 已能撞上 0.95，故长句直接不走相似度路径。
_LONG_KEY_LEN = 40


def _dedup_key(text) -> str:
    """同义判重键：先剥离 ASCII 人名与相对/绝对时间槽位，再规范化。仅用于比较，不改原值。"""
    s = _DATE_ABS_RE.sub("", str(text or ""))
    s = _TIME_REL_RE.sub("", s)
    s = _CLOCK_RE.sub("", s)
    s = _ASCII_NAME_RE.sub("", s)
    return _norm_fact_text(s)


def _dedup_slots(text) -> tuple[frozenset[str], frozenset[str]]:
    """取出会被 _dedup_key 剥掉的「区分性槽位」：ASCII 人名集合 与 时间词集合（小写化）。"""
    s = str(text or "")
    names = frozenset(m.group(0).lower() for m in _ASCII_NAME_RE.finditer(s))
    dates = frozenset(
        [m.group(0) for m in _DATE_ABS_RE.finditer(s)]
        + [m.group(0) for m in _TIME_REL_RE.finditer(s)]
        + [m.group(0) for m in _CLOCK_RE.finditer(s)]
    )
    return names, dates


def _slots_conflict(x: frozenset, y: frozenset) -> bool:
    """区分性槽位是否「明确冲突」：**两侧都非空**且不相等。

    一侧为空不算冲突 —— 缺人名/缺时间只是写法省略（「我是bo，甲的伴侣」vs「我是甲的伴侣」
    仍是同一件事），不构成「不是同一件事」的证据；两侧都有且不同才是硬证据。
    """
    return bool(x) and bool(y) and x != y


def _texts_duplicate(a, b, *, ignore_dates: bool = False) -> bool:
    """两条【原值】是否「同一件事」（确定性、零 LLM）：区分性槽位先比、再比键。

    批 B（2026-09-26，中-3）：**先比区分性槽位**——人名两侧都有且不同（Sam vs Leo）⇒ 不同对象；
    时间两侧都有且不同（昨天 vs 今天）⇒ 不同事件；两者都不能并成一条。
    ``ignore_dates=True`` 供**计划段**使用：未完成计划里时间是「约定措辞」而非事件标识
    （「我答应明早给甲带饭」与「我答应9月27日给甲带饭吃」是同一个约定），故不拿它判冲突。
    槽位一致时才在剥离槽位后的键上走三条保守判据（任一即视为重复）：
    1. 键完全相同（含原「规范化后完全相同」）；
    2. 短键 ≥ 6 字且是长键的前缀（核心相同、各自补充，如「我是用户的老公」⊂「…会照顾…」）；
    3. SequenceMatcher 比值 ≥ 阈值（短键 ≤ 10 字取 0.9、其余取 0.95）且两侧键都 < 40 字
       （长句只差 2 字也能撞上高阈值，故长句只认全等/前缀）。
    刻意不用 C13 共享核心前缀（会误并同模板不同宾语）。任一异常一律保守判「不重复」。
    """
    na, da = _dedup_slots(a)
    nb, db = _dedup_slots(b)
    if _slots_conflict(na, nb):
        return False          # 人名两侧都有且不同：不同对象，绝不合并
    if not ignore_dates and _slots_conflict(da, db):
        return False          # 时间两侧都有且不同：不同事件（计划段按措辞处理，不判冲突）
    ka, kb = _dedup_key(a), _dedup_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    short, long_ = (ka, kb) if len(ka) <= len(kb) else (kb, ka)
    if len(short) >= _TRACE_MIN_CORE and long_.startswith(short):
        return True
    if max(len(ka), len(kb)) >= _LONG_KEY_LEN:   # 长句：只认全等/前缀，不做相似度
        return False
    sim = _TRACE_SIMILARITY
    if max(len(ka), len(kb)) <= _SHORT_KEY_LEN:  # 短句差一个字＝近乎逐字重复
        sim = _TRACE_SIMILARITY_SHORT
    try:
        from app.scheduling.prospective_intent import similar_intent_text
        return similar_intent_text(ka, kb, sim)
    except Exception:
        return False


def select_fact_rows(rows, *, limit: int, max_per_predicate: dict | None = None) -> list:
    """事实行按谓词判重 + 名额收敛（**纯函数，零 DB / 零 LLM**）。

    ``rows`` 由调用方按 ``updated_at desc`` 排好 ⇒ 最先出现的那条＝最新的那条。
    - 判重（D1）：同 predicate 且 ``_texts_duplicate`` 判为同一件事 ⇒ 只保留最先出现的那条
      （先按规范化键 O(1) 命中完全重复，再对已保留行做保守近义比较）；
      **批 B（中-3）**：O(1) 键带区分性槽位、两两比较一律传**原值**（由 ``_texts_duplicate`` 自己
      判槽位）——「昨天 Sam 来聊天」与「今天 Sam 来聊天」时间槽位冲突 ⇒ 两条都留（原口径会被规范化键并掉）；
    - 名额：某 predicate 已选条数达到 ``max_per_predicate[predicate]`` ⇒ 后续该谓词一律跳过
      （表里没有的谓词不限）；
    - 选满 ``limit`` 即停；``limit`` ≤ 0 返回空列表；
    - 脏行（predicate 为 None/空串、object_value 为空）不抛异常，按「谓词组键＝空串」正常参与
      判重与计数；绝不就地修改 ``rows``。
    """
    out: list = []
    if not limit or limit <= 0:
        return out
    quotas = max_per_predicate or {}
    seen_keys: set[tuple] = set()
    kept: list[tuple] = []  # (predicate, 原值)，仅供近义两两比较（|rows|≤30，成本可控）
    counts: dict[str, int] = {}
    for row in rows or []:
        predicate = str(_get(row, "predicate") or "").strip()
        value = _get(row, "object_value")
        key = _dedup_key(value)
        slots = _dedup_slots(value)
        if (predicate, key, slots) in seen_keys:
            continue  # 规范化后完全相同（且槽位一致）：O(1) 命中
        if key and any(predicate == p and _texts_duplicate(value, v) for p, v in kept):
            continue  # 近义重复：复用既有判据（槽位冲突 ⇒ 不并，见 _texts_duplicate）
        cap = quotas.get(predicate)
        if cap is not None and counts.get(predicate, 0) >= cap:
            continue
        seen_keys.add((predicate, key, slots))
        kept.append((predicate, value))
        counts[predicate] = counts.get(predicate, 0) + 1
        out.append(row)
        if len(out) >= limit:
            break
    return out


def select_intent_rows(rows, *, limit: int) -> list:
    """未完成计划行按语义判重收敛（**纯函数，零 DB / 零 LLM**）。

    ``rows`` 由调用方按 ``updated_at desc`` 排好。同义（``_texts_duplicate`` 判为同一件事）
    只保留最先出现（＝最新）的一条；空正文跳过；选满 ``limit`` 即停；``limit`` ≤ 0 返回空。
    比较基准是 ``content`` 原值（``_texts_duplicate(..., ignore_dates=True)`` 自己剥槽位与规范化）。
    **批 B（中-3）口径**：计划段只用人名区分「不同约定」（「明天要和 Sam 去看电影」与
    「明天要和 Leo 去看电影」各占一条），**时间词不判冲突**（「明早」与「9月27日」是同一个约定的
    两种写法，仍要归一）。渲染时 ``intent_line`` 仍按各自原行做绝对化，两者不掺混。
    绝不改动 ``rows``。
    """
    out: list = []
    if not limit or limit <= 0:
        return out
    kept: list[str] = []   # 原值，供近义两两比较（|rows| 有上限，成本可控）
    for row in rows or []:
        content = str(_get(row, "content") or "").strip()
        if not content:
            continue
        key = _dedup_key(content)
        # 计划段口径：时间是「约定措辞」不是事件标识 ⇒ ignore_dates=True（人名仍作区分）
        if key and any(_texts_duplicate(content, k, ignore_dates=True) for k in kept):
            continue
        kept.append(content)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def slot_line(row) -> str:
    """user_facts 行 → trace 行；无值返回空串。"""
    value = _one_line(_get(row, "value"))
    if not value:
        return ""
    slot = str(_get(row, "slot") or "").strip()
    label = _SLOT_LABEL.get(slot, slot)
    return f"- {label}：{value}" if label else f"- {value}"


def intent_line(row) -> str:
    """prospective_intents 行 → trace 行；无内容返回空串。

    Q2（2026-09-25）：正文是当初写计划时的话，含「明天/下周三」这类相对时间词——事后注入即失真
    ⇒ 以该行 created_at（缺失则 updated_at）为基准日换成绝对日期；两者都没有或换算失败原样输出。
    """
    content = _one_line(_get(row, "content"))
    if not content:
        return ""
    # 函数内 import 与 try 同包：失败也只可能是「这一行不换算」，绝不冒泡到主链路
    try:
        from app.utils.relative_time import absolutize_relative_dates
        content = absolutize_relative_dates(
            content, _get(row, "created_at") or _get(row, "updated_at")) or content
    except Exception as e:  # 绝不冒泡到主链路
        _logger.warning("state trace intent absolutize failed: %s", e)
    return f"- {content}"


def render_state_trace(fact_lines: list[str] | None = None,
                       slot_lines: list[str] | None = None,
                       intent_lines: list[str] | None = None) -> str:
    """三分区 → 纯文本 trace（**纯函数，不查库**）；全空返回空串；总长硬上限。

    分区顺序固定（现状事实 → 用户槽值 → 未完成计划）；空分区整段省略；预算用尽即停。
    """
    parts: list[str] = []
    for header, lines in ((_SEC_FACTS, fact_lines), (_SEC_SLOTS, slot_lines), (_SEC_INTENTS, intent_lines)):
        items = [_one_line(ln) for ln in (lines or []) if str(ln or "").strip()]
        if items:
            parts.append(header)
            parts.extend(items)
    if not parts:
        return ""
    out = [_HEADER]
    used = len(_HEADER)
    for part in parts:
        if used + 1 + len(part) > TRACE_TOTAL_CHARS:
            break
        out.append(part)
        used += 1 + len(part)
    if len(out) > 1 and out[-1] in _SEC_HEADERS:  # 只有分区头 = 该段没装进任何行，撤掉头
        out.pop()
    if len(out) <= 1:
        return ""
    return "\n".join(out)[:TRACE_TOTAL_CHARS]


async def _readable_slots(user_id) -> list[str]:
    """读取侧槽白名单（红线：敏感槽 relationship/health 未经该账号显式开启则永不带出）。"""
    try:
        from app.memory.user_facts import readable_user_fact_slots_for
        return list(await readable_user_fact_slots_for(user_id) or [])
    except Exception as e:
        _logger.warning("state trace slot gate failed user=%s: %s", user_id, e)
    try:
        from app.memory.user_facts import enabled_user_fact_slots
        return list(enabled_user_fact_slots() or [])  # 按账号解析失败 → 全局口径（同样不旁路敏感槽）
    except Exception:
        return []


async def build_state_trace(db, *, character_id=None, user_id=None,
                            limit_facts: int = 8, limit_slots: int = 8,
                            limit_intents: int = 5) -> str:
    """只读拼装现状 trace（零 LLM / 零写入 / 不上屏）；异常收敛为空串 + WARNING。

    ``db`` 由调用方提供（同一会话内复用连接，POC 只读、绝不 commit）。
    """
    try:
        from app.models.memory import ProspectiveIntent, WorldFact
        from app.models.user import GlobalUserFact

        fact_rows: list = []
        if character_id and limit_facts > 0:
            fetch_limit = min(limit_facts * TRACE_FACT_FETCH_MULTIPLIER, TRACE_FACT_FETCH_CAP)
            stmt = select(WorldFact).where(
                WorldFact.character_id == character_id,
                # 硬口径：只取 active + 置信达标；expired / superseded 一律不进
                WorldFact.status == "active",
                WorldFact.confidence >= TRACE_CONFIDENCE_MIN,
            )
            if user_id:
                stmt = stmt.where(WorldFact.user_id == user_id)
            fact_rows = list((await db.execute(
                # Q1：先多取（多取的量在收敛时砍掉），被跳过的重复/超额条目才不至于白占名额
                stmt.order_by(WorldFact.updated_at.desc()).limit(fetch_limit)
            )).scalars().all())
            # Q1（2026-09-25）：判重 + 同谓词名额收敛回 limit_facts 条。
            fact_rows = select_fact_rows(fact_rows, limit=limit_facts,
                                         max_per_predicate=TRACE_MAX_PER_PREDICATE)
            # C7 保底（2026-09-24）：收敛后仍没有 status 行时，补查一条最新 status 置顶。
            # **必须在收敛之后**（顺序反了会出现「保底补上、又被收敛规则挤掉」的自我打架）；
            # 过滤条件与上面逐项一致（只多一个 predicate）。
            if not any(str(_get(r, "predicate") or "").strip() == "status" for r in fact_rows):
                sstmt = select(WorldFact).where(
                    WorldFact.character_id == character_id,
                    WorldFact.status == "active",
                    WorldFact.confidence >= TRACE_CONFIDENCE_MIN,
                    WorldFact.predicate == "status",
                )
                if user_id:
                    sstmt = sstmt.where(WorldFact.user_id == user_id)
                status_row = (await db.execute(
                    sstmt.order_by(WorldFact.updated_at.desc()).limit(1)
                )).scalars().first()
                fact_rows = _merge_status_row(fact_rows, status_row)

        slot_rows: list = []
        if user_id and limit_slots > 0:
            slots = await _readable_slots(user_id)
            if slots:
                slot_rows = list((await db.execute(
                    select(GlobalUserFact).where(
                        GlobalUserFact.user_id == user_id,
                        GlobalUserFact.slot.in_(slots),
                    ).order_by(GlobalUserFact.updated_at.desc()).limit(limit_slots)
                )).scalars().all())

        intent_rows: list = []
        if character_id and limit_intents > 0:
            # 归类（2026-09-26）：本段是「未完成计划」，只收 promise；kind=="cue" 是话题/线索
            # （如「用户说十一点的事记着」），不该混进计划 ⇒ 查询侧直接排除。
            istmt = select(ProspectiveIntent).where(
                ProspectiveIntent.character_id == character_id,
                ProspectiveIntent.status == "pending",  # discharged/matched/stale/expired 不进
                ProspectiveIntent.kind == "promise",
            )
            if user_id:
                istmt = istmt.where(ProspectiveIntent.user_id == user_id)
            raw_intents = list((await db.execute(
                # 同事实侧：先多取（同义收敛后腾出的名额由更旧的计划补上），再判重回 limit
                istmt.order_by(ProspectiveIntent.updated_at.desc()).limit(
                    min(limit_intents * TRACE_FACT_FETCH_MULTIPLIER, TRACE_FACT_FETCH_CAP))
            )).scalars().all())
            intent_rows = select_intent_rows(raw_intents, limit=limit_intents)

        from app.memory.user_facts import fact_is_expired
        return render_state_trace(
            [fact_line(r) for r in fact_rows],
            [slot_line(r) for r in slot_rows if not fact_is_expired(r)],  # TTL 过期槽值同属旧现状
            [intent_line(r) for r in intent_rows],
        )
    except Exception as e:
        _logger.warning("state trace build failed char=%s: %s", character_id, e)
        return ""
