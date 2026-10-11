# -*- coding: utf-8 -*-
"""A30 批 A：写侧兜底——模型没落标记时，由系统按**确定性规则**补记。

三条硬约束（不是偏好，是被读数逼出来的形状，改之前先读）：

1. **只在用户亲口下令时动**：本轮必须命中「记/备忘」族或「提醒/安排＋日期」族，否则一律不补。
   兜底买的是「用户明确要记、模型没落」那一格，不是「系统觉得这条有用」——后者误记面无法收敛。
2. **内容不造句**：正文**逐字取自召回到的记忆原文**（只过 `normalize_memory_text`），日期取自本轮短语
   按锚点换算。任何"改写、归纳、补全"都不在这里做，做了就把误记从「选错条目」升级成「编造事实」。
3. **周期约定没有那一个日期**（10-10 批 51 定口径）：命中周期词 ⇒ `date` 为空串，**不许换算成某个具体日子**。
   词表用本模块那一份，尺子（`scripts/diagnostics/memory_action_eval.py`）反过来 import 它——
   同一个事实只许有一套词，抄第二遍迟早自相矛盾。

纯函数、零 IO、零 LLM、幂等；任何异常 ⇒ 返回"什么都不补"（fail-open：兜底永不阻塞主链路，
也永不因为自己的 bug 往库里写脏东西）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

# ── 词表与形状常量（唯一真源）────────────────────────────────────────────
PERIODIC_WORDS = ("每到", "每逢", "每次", "每周", "每月", "每天", "每星期", "以后每")

# 「记/备忘」族：用户明确要求落成备忘/记忆。命中即走 MEMO＋MEMORY，不再产 CAL_NOTE（宁少记不多记）。
MEMO_REQUEST_WORDS = ("记进", "记一下", "记下", "记一笔", "备忘", "记到", "帮我记")

# 「提醒/安排」族：需要与日期或周期词同时命中才产 CAL_NOTE。
CALENDAR_REQUEST_WORDS = ("提醒", "别给我安排", "别安排", "别约", "不要约", "安排", "约我")

# 「新值线索」：命中 ⇒ 用户正在**当场改口**，正文必须取他本轮那句原话，**绝不取召回**
# （召回里躺着的是作废的旧值，取回来就是把旧事实再记一遍——M0 第一次读数就栽在这格上）。
NEW_VALUE_WORDS = ("现在", "已经", "不再", "以后", "改口", "改了", "换成", "搬", "新地址", "最新", "刚")

_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 15}
_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

_MAX_CONTENT = 40          # 正文截断上限（短句化，别把整轮对话抄进日历）
_MAX_RECALL_LEN = 120      # 超长召回条目不当兜底正文（多半是摘要/流水，不是单条事实）

CHANNEL_MEMORY = "MEMORY"
CHANNEL_MEMO = "MEMO"
CHANNEL_CAL = "CAL_NOTE"


@dataclass(frozen=True)
class FallbackAction:
    """与 `app.agent.actions.AgentAction` 同名两属性（判据只读这两个），但**不落库、不执行**。"""
    action_type: str
    payload: dict


def _as_base_date(base_date) -> date:
    """锚点归一：`date` / `YYYY-MM-DD` / 带时分秒的串都可；读不到 ⇒ 今天（保持尺子不跟本机日历走的例外）。"""
    if isinstance(base_date, datetime):
        return base_date.date()
    if isinstance(base_date, date):
        return base_date
    s = str(base_date or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    return datetime.now().date()


def is_periodic(text: str) -> bool:
    t = text or ""
    return any(w in t for w in PERIODIC_WORDS)


def resolve_date_phrase(text: str, base_date=None) -> str:
    """本轮短语 → 绝对日期（`YYYY-MM-DD`）；**周期表达或认不出日期 ⇒ 空串**（空串＝不塞日期）。

    覆盖：今天/明天/后天、过N天（含中文数字）、周X/星期X/下周X、M月D日(号)、下个月D日(号)。
    一周从周一数起（与项目"北京日界"口径一致），"周六"指**本周内的那个周六**，未来才到。
    """
    t = text or ""
    if is_periodic(t):
        return ""
    today = _as_base_date(base_date)

    m = re.search(r"[过](\d+|[一两二三四五六七八九十])\s*(天|周)", t)
    if m:
        n = _cn_int(m.group(1))
        if n:
            return (today + timedelta(days=n if m.group(2) == "天" else n * 7)).isoformat()

    for w, off in (("今天", 0), ("明天", 1), ("后天", 2), ("大后天", 3)):
        if w in t:
            return (today + timedelta(days=off)).isoformat()

    m = re.search(r"(下)?(?:周|星期)(" + "|".join(_WEEKDAY) + r")", t)
    if m:
        target = _WEEKDAY[m.group(2)]
        delta = (target - today.weekday()) % 7
        if m.group(1):
            delta += 7
        if delta == 0:
            delta = 7          # "周六"说出口那天正好周六：指下一个，不是今天
        return (today + timedelta(days=delta)).isoformat()

    m = re.search(r"下个月\s*(\d{1,2})\s*[日号]", t)
    if m:
        day = int(m.group(1))
        y, mo = (today.year + (today.month == 12), 1 if today.month == 12 else today.month + 1)
        try:
            return date(y, mo, day).isoformat()
        except ValueError:
            return ""

    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", t)
    if m:
        try:
            cand = date(today.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return ""
        if cand < today:                        # 已经过去的月日＝明年那个
            try:
                cand = date(today.year + 1, int(m.group(1)), int(m.group(2)))
            except ValueError:
                return ""
        return cand.isoformat()

    return ""


def _cn_int(token: str) -> int:
    if token.isdigit():
        return int(token)
    return _CN_NUM.get(token, 0)


def _bigrams(s: str) -> set:
    s = re.sub(r"\s+", "", s or "")
    return {s[i:i + 2] for i in range(len(s) - 1)}


def pick_recall(user_text: str, recall_texts) -> str:
    """从召回条目里挑一条当正文。

    - 只有**一条**召回 ⇒ 直接用它：这就是人类那种"他让我记娃的名字，而他女儿叫朵朵我知道"。
    - 多条 ⇒ 必须**严格胜出**（与本轮共享二元组最多，且不与第二名并列，得分 >0），否则不补。
      并列或零重合时宁可这轮不记——把另一件事的原文写进日历/备忘，正是用户最不原谅的误记形状。
    """
    cands = []
    for raw in (recall_texts or ()):
        if not isinstance(raw, str):        # 非字符串不当候选：`str(None)`＝"None" 会冒充一条记忆
            continue
        cand = raw.strip()
        if cand and len(cand) <= _MAX_RECALL_LEN and _bigrams(cand):
            cands.append(cand)
    if not cands:
        return ""
    if len(cands) == 1:
        return cands[0]
    shared = _bigrams(user_text)
    scored = sorted((len(shared & _bigrams(c)) / len(_bigrams(c)), i, c)
                    for i, c in enumerate(cands))
    best_score, _, best = scored[-1]
    second = scored[-2][0] if len(scored) > 1 else 0.0
    if best_score <= 0 or best_score == second:
        return ""
    return best


def new_value_clause(text: str) -> str:
    """用户当场改口时，返回他本轮那句原话；不是改口 ⇒ 空串（正文改用召回）。

    判定要**先排除周期**：「以后每到…」是周期约定，不是改口，它的事实本体在召回里（如"周三带课"），
    取本轮从句反而会丢掉那个片段。顺序不能颠倒。
    """
    t = (text or "").strip()
    if not t or is_periodic(t):
        return ""
    if not any(w in t for w in NEW_VALUE_WORDS):
        return ""
    clauses = [c.strip() for c in re.split(r"[，,。；;！!？?\s]+", t) if c.strip()]
    hit = [c for c in clauses
           if any(w in c for w in NEW_VALUE_WORDS) and not any(r in c for r in MEMO_REQUEST_WORDS)]
    return hit[-1] if hit else ""


def trigger_clause(user_text: str) -> str:
    """本轮里**含日期短语的那个从句**（逐字截取，不加工）——日历条目的最后一级来源。

    为什么只给日历用：一条日历写「周六别给我安排太早的事」是有意义的时间块；
    而把「把这个记一下」当成记忆正文存进记忆本，是纯垃圾行。所以记忆类仍然要求
    **改口原话**或**召回原文**这种实名来源，拿不到就不补。
    """
    t = (user_text or "").strip()
    if not t:
        return ""
    for c in re.split(r"[，,。；;！!？?\s]+", t):
        c = c.strip()
        if len(c) >= 4 and resolve_date_phrase(c, None) and not any(w in c for w in MEMO_REQUEST_WORDS):
            return c
    return ""


def _content_body(user_text: str, recall_texts, allow_clause: bool = False) -> tuple[str, str]:
    """正文来源三级：**改口原话 → 召回原文 → （仅日历）本轮含日期的从句**；都拿不到 ⇒ 空串（不补）。"""
    clause = new_value_clause(user_text)
    if clause:
        return clause, "turn"
    body = pick_recall(user_text, recall_texts)
    if body:
        return body, "recall"
    if allow_clause:
        c = trigger_clause(user_text)
        return (c, "clause") if c else ("", "")
    return "", ""


def _clip(text: str) -> str:
    return text[:_MAX_CONTENT].rstrip()


def fallback_actions(user_text: str, *, recall_texts=(), model_channels=(),
                     base_date=None) -> dict:
    """确定性兜底。返回 ``{"actions": [...], "memory_texts": [...], "skipped": {...}}``。

    - ``model_channels``：模型**这一轮已经产出**的通道名集合；命中的通道一律不补（零标记才触发）。
    - 判定顺序：先「记/备忘」族（⇒ MEMO＋MEMORY，不多产日历），再「提醒/安排＋日期或周期」（⇒ CAL_NOTE）。
    - ``skipped`` 记录每个候选通道没补的原因，供影子期读数（不许只数补了几条、把"为什么不补"丢掉）。
    """
    out = {"actions": [], "memory_texts": [], "skipped": {}}
    try:
        t = (user_text or "").strip()
        if not t:
            out["skipped"]["all"] = "本轮没有用户文本"
            return out
        have = {str(c).strip().upper() for c in (model_channels or ())}
        from app.memory.normalize import normalize_memory_text

        wants_memo = any(w in t for w in MEMO_REQUEST_WORDS)
        wants_cal = (not wants_memo) and any(w in t for w in CALENDAR_REQUEST_WORDS)
        periodic = is_periodic(t)
        note_date = "" if periodic else resolve_date_phrase(t, base_date)

        if wants_memo:
            # 一轮只补**一处**：用户明说"备忘"才写小手机，其余"记一下/记一笔"只写记忆通道。
            # 两处都写＝用户看见同一条事实重复出现，那是一种会被当成 bug 的误记。
            body, src = _content_body(t, recall_texts)
            wanted = [CHANNEL_MEMO] if "备忘" in t else [CHANNEL_MEMORY]
            ch = wanted[0]
            if ch in have:
                out["skipped"][ch] = "模型已产出该通道"
                return out
            if not body:
                out["skipped"][ch] = "本轮没陈述句、召回也对不上（宁缺毋滥）"
                return out
            out["content_source"] = src
            text = normalize_memory_text(body)
            if ch == CHANNEL_MEMO:
                out["actions"].append(FallbackAction(CHANNEL_MEMO, {"text": _clip(text)}))
            else:
                out["memory_texts"].append(text)
            return out

        if not (wants_cal or periodic):
            out["skipped"][CHANNEL_CAL] = "本轮没有「记/备忘」也没有「提醒/安排」指令"
            return out
        if CHANNEL_CAL in have:
            out["skipped"][CHANNEL_CAL] = "模型已产出该通道"
            return out
        if not periodic and not note_date:
            out["skipped"][CHANNEL_CAL] = "认不出日期短语（不许瞎猜日子）"
            return out
        body, src = _content_body(t, recall_texts, allow_clause=True)
        if not body:
            out["skipped"][CHANNEL_CAL] = "召回与本轮都给不出条目文本（宁缺毋滥）"
            return out
        out["content_source"] = src
        text = normalize_memory_text(body)
        # 载荷形状只有一套：永远是 {date, text}；周期约定用空 date 表示"没有那一个日期"。
        out["actions"].append(FallbackAction(CHANNEL_CAL, {
            "date": note_date, "text": (_clip(text) if not periodic else "重复约定：" + _clip(text))}))
        return out
    except Exception:
        # fail-open：兜底自己出错时**什么都不补**，绝不半补（半补会留下无法解释的库行）
        return {"actions": [], "memory_texts": [], "skipped": {"all": "兜底内部异常，已放弃本次补记"}}
