"""主动消息纯函数助手 — 消息分段 / 分隔符校验 / 自然度评分 / 复读与重叠判定。

本模块自 ``scheduling/message_generator.py`` 逐字节搬入（A22 ③a，2026-10-02）。
边界＝**消息分段/分隔符校验/自然度评分/复读与重叠判定等纯函数**：不做 IO、不调 LLM、
不读库；Feature Flag 读取沿用原实现，在函数体内惰性 ``from app.agent import loop``。
message_generator 侧靠具名重导出保留原调用点，本模块**绝不**反向 import message_generator。
"""
import re


# 2026-09-13：主动消息可见内容判定（只看数字/拉丁字母/汉字，标点与省略号不算内容）
_VISIBLE_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")


def _has_visible_content(segments: list[str]) -> bool:
    """主动消息是否有可读内容：纯标点/省略号/空白视为无内容（2026-09-13）。"""
    return bool(_VISIBLE_RE.search("".join(segments or [])))


# ── 批次四（2026-09-16）：主动消息分块护栏（flag proactive_segment_guard，默认关）的常量表──
# 关＝逐字节现状；开＝①过滤残句/空块并合并碎片段 ②未闭合括号/引号不落刀
# ③生成前注入当前场景事实 + 生成后轻量现实校验（家庭场景词 vs 住校场景）。
# （段数上限 _SEGMENT_MAX 与自然度阈值 NATURALNESS_* 仍留在 message_generator。）
_SEGMENT_MIN_LEN = 4   # 碎片段阈值（字符数，可配）：低于此长度视为残句，优先并入相邻段
# 家庭场景词（常量词表，可扩展）：用户住校时命中即判现实冲突（宿舍无厨房）
_FAMILY_SCENE_WORDS = (
    "锅里", "留饭", "给你留着饭", "留了饭", "回家吃饭", "回来吃饭", "在家等你",
    "等你回家", "家里的饭", "饭菜给你", "给你留了饭",
)
# 住校场景判定词：出现在「当前场景事实」文本里即视为用户在住校/校园场景
_SCHOOL_SCENE_WORDS = ("宿舍", "教学楼", "食堂", "住校", "寝室", "校区", "学校")
# 未闭合引号对（只认成对的中文/全角引号，避免英文撇号误判导致过度合并）
_QUOTE_PAIRS = {"“": "”", "「": "」", "『": "』", "‘": "’", "【": "】"}
_QUOTE_CLOSES = set(_QUOTE_PAIRS.values())


def _segment_guard_on() -> bool:
    """Feature Flag：主动消息分块护栏（默认关；关=逐字节现状）。"""
    try:
        from app.agent import loop as _loop
        return bool(_loop.AGENT_FLAGS.get("proactive_segment_guard", False))
    except Exception:
        return False


def _quote_unbalanced(text: str) -> bool:
    """成对中文/全角引号是否未闭合（只统计声明过的引号对，普通英文引号不参与）。"""
    opens = sum((text or "").count(ch) for ch in _QUOTE_PAIRS)
    closes = sum((text or "").count(ch) for ch in _QUOTE_CLOSES)
    return opens > closes


def _has_unclosed_delimiter(text: str) -> bool:
    """文本是否停在未闭合的括号/引号里（落刀点判定，2026-09-16）。

    复用 response_parser 既有的括号深度思路（`_bracket_depth`），再补一层引号配对；
    为 True 时不得在此处切段，须并入下一段。
    """
    if not text:
        return False
    try:
        from app.agent.response_parser import _bracket_depth
        if _bracket_depth(0, text) > 0:
            return True
    except Exception:
        pass
    return _quote_unbalanced(text)


def _split_response_lines(response: str) -> list[str]:
    """开＝按行切段；未闭合括号/引号不落刀（把后续行并进当前段）。"""
    out: list[str] = []
    for ln in (response or "").splitlines():
        s = ln.strip().strip('"').strip("'")
        if not s:
            continue
        if out and _has_unclosed_delimiter(out[-1]):
            out[-1] = out[-1] + s
        else:
            out.append(s)
    return out


def _normalize_segments(segments: list[str], min_len: int = _SEGMENT_MIN_LEN) -> list[str]:
    """开＝过滤空/纯标点段 + 合并长度不足阈值的碎片段（优先并入相邻段，无法合并则丢弃）。

    - 纯空白/纯标点/纯省略号段直接丢弃（禁止空块）；
    - 长度 < min_len 的碎片段并入前一段；前面无可并入段时暂挂，遇到下一段并入；
      若整条都是碎片、前后都无可并段，则丢弃（宁可不发残句）。
    """
    cleaned: list[str] = []
    for s in segments or []:
        s2 = (s or "").strip()
        if not s2 or not _has_visible_content([s2]):
            continue
        cleaned.append(s2)
    merged: list[str] = []
    pending = ""  # 前导碎片：暂无前段可并，等待并入其后第一段
    for s in cleaned:
        if len(s) < min_len:
            if merged:
                merged[-1] += s
            else:
                pending += s
            continue
        merged.append(pending + s)
        pending = ""
    if pending:  # 全是碎片（无完整段可并）：若能拼出达阈值的一段则合并成单段，否则丢弃
        merged = [pending] if len(pending) >= min_len else []
    return merged


def _scene_is_school(scene_text: str) -> bool:
    """当前场景是否为住校/校园（命中宿舍/教学楼/食堂… 任一即真）。"""
    return any(w in (scene_text or "") for w in _SCHOOL_SCENE_WORDS)


def _conflicting_segment_indexes(segments: list[str], scene_text: str) -> list[int]:
    """现实冲突段下标：住校场景下命中家庭场景词（锅里/留饭/回家吃饭…）的段。"""
    if not _scene_is_school(scene_text):
        return []
    return [i for i, s in enumerate(segments or []) if any(w in (s or "") for w in _FAMILY_SCENE_WORDS)]


def _apply_segment_guard(
    segments: list[str],
    scene_text: str = "",
    *,
    drop_conflicts: bool = False,
) -> tuple[list[str], list[int]]:
    """开＝残句/空块过滤 + 现实约束校验；返回 (清洗后段列表, 冲突段下标)。

    drop_conflicts=False：只报告冲突段下标（调用方决定重试）；
    drop_conflicts=True：重试后仍冲突 → 直接丢弃冲突段（不发送穿帮内容）。
    """
    kept = _normalize_segments(segments)
    conflicts = _conflicting_segment_indexes(kept, scene_text)
    if drop_conflicts and conflicts:
        drop = set(conflicts)
        kept = [s for i, s in enumerate(kept) if i not in drop]
    return kept, conflicts


# 生成后规则校验（2026-08-12 → 2026-08-18 G-P2-3）：仅「身份暴露类」完整词组硬拦截，
# 去掉裸词 AI/模型/算法（避免误伤「AI 绘画」「这个模型跑得慢」等生活化表达）
_BANNED_WORDS = ("我是AI", "我是人工智能", "我是模型", "我是大模型", "我是一个AI", "我是一个模型",
                 "作为AI", "作为人工智能", "作为模型", "作为大模型",
                 "AI助手", "AI 助手", "AI模型", "AI 模型", "人工智能助手",
                 "根据系统", "系统判断", "系统提示", "系统指令")
_MAX_SEGMENT_LEN = 80


# ── 自然度评分（#28 ①，2026-08-24）词表；阈值 NATURALNESS_RETRY/SKIP_THRESHOLD 留在 message_generator ──
# 突兀开口词（仅判定消息开头前两字；命中即压分）——包含单人语气词/口头禅，防止生硬开口
_ABRUPT_OPENING_WORDS = ("哈", "啊", "呢", "哦", "嗯", "诶", "咦", "那个", "就是说", "然后啊")
# 模板/客套句式（命中越多越像模板；每命中一个 -0.35）
_TEMPLATE_PHRASES = (
    "今天天气", "跟你说个", "跟你说", "你知道吗", "我想你",
    "你在干嘛", "在忙吗", "最近怎么样", "分享一下", "记得吗", "猜猜",
)


# 轻量"抛回问题"后检（方案 §5.3d）：中文问号/疑问助词判定是否留了话头
_INVITATION_RE = re.compile(r"(吗|嘛|呢|怎么样|如何|要不要|是不是|有没有|哪个|还是)")


def _has_invitation(text: str) -> bool:
    """是否在消息里留了可接的话头/问题（纯函数，可单测）。

    命中任意一个中文问号/英文问号，或含常见疑问助词（吗/嘛/呢/怎么样…）即判定有邀请；
    纯陈述句返回 False。
    """
    if not text:
        return False
    if "？" in text or "?" in text:
        return True
    return bool(_INVITATION_RE.search(text))


def _naturalness_flag() -> bool:
    """Feature Flag：低优先主动消息自然度评分（默认开；可经运行时开关改 False 回退为纯现状）。"""
    try:
        from app.agent import loop as _loop
        return bool(_loop.AGENT_FLAGS.get("proactive_naturalness_score", True))
    except Exception:
        return True


def score_naturalness(segments: "list[str] | str") -> float:
    """纯规则自然度评分（0..1，越高越自然）。不调 LLM。

    低优先级主动消息（motivation/渴望唤醒 等）生成后据此判断是否需要重试/降级。
    基于规则的简化版：长度分档 / 连续复读密度 / 突兀开口 / 模板句式（近似等权）。
    """
    if isinstance(segments, str):
        segments = [segments]
    text = "".join(s or "" for s in segments).strip()
    if not text:
        return 0.0
    n = len(text)

    # 1) 长度分档 0..1：过短/过长都不自然
    if n < 4:
        length = 0.0
    elif n < 10:
        length = 0.4
    elif n <= 160:
        length = 1.0
    elif n <= 220:
        length = 0.7
    elif n <= 300:
        length = 0.4
    else:
        length = 0.2

    # 2) 连续复读密度 0..1：最长连续相同字符段越短越自然（复读/刷屏压分）
    max_run = 1
    cur = 1
    prev_ch = text[0]
    for ch in text[1:]:
        if ch == prev_ch:
            cur += 1
            if cur > max_run:
                max_run = cur
        else:
            cur = 1
        prev_ch = ch
    density = min(1.0, (max_run - 1) / 3.0)
    repeat = 1.0 - density

    # 3) 突兀开头 0..1：命中突兀开口词 → 0
    head = text[:2]
    opening = 0.0 if any(head.startswith(w) for w in _ABRUPT_OPENING_WORDS) else 1.0

    # 4) 模板句式 0..1：命中越少越自然
    template_hits = sum(1 for p in _TEMPLATE_PHRASES if p in text)
    template = max(0.0, 1.0 - 0.35 * template_hits)

    # 加权合并（复读密度权重最高，最影响"像不像真人说话"）
    score = round(0.25 * length + 0.45 * repeat + 0.15 * opening + 0.15 * template, 4)
    # 过短消息（如单个语气词/“在吗”）对主动长文类消息不自然：直接封顶
    if n < 8:
        score = min(score, 0.30)
    return score


def _validate_segments(segments: list[str]) -> tuple[bool, list[str]]:
    """校验主动消息：含禁用词整段剔除、超长截断。返回 (是否一次通过, 清洗后列表)"""
    cleaned: list[str] = []
    for s in segments:
        s2 = (s or "").strip().strip('"').strip("'")
        if not s2:
            continue
        if any(bw in s2 for bw in _BANNED_WORDS):
            continue
        cleaned.append(s2[:_MAX_SEGMENT_LEN])
    ok = len(cleaned) == len(segments) and all(len(s) <= _MAX_SEGMENT_LEN for s in segments)
    return ok, cleaned


# A-C（2026-09-01）：字面重合守卫（防「复述上一条」兜底，真机 sam 案——LLM 逐句照抄最近 AI 对话回复）。
_PARROT_OVERLAP_THRESHOLD = 0.7  # 去空白后字符级重合率阈值（>70% 判定复述）
_PARROT_MIN_CHARS = 30           # 守卫适用最小有效字符数（主动消息 3~5 句，短于此不适用防误伤）


def _context_overlap_ratio(text: str, context: str) -> float:
    """segments 文本相对 last_context 的字面重合率（纯函数，便于测试）。

    归一化=去除全部空白字符；分母只取生成文本长度（difflib.SequenceMatcher 匹配块合计），
    衡量「生成文本有多大比例是照抄上下文」——context 更长不会稀释比例。
    """
    import re as _re
    from difflib import SequenceMatcher as _SM
    a = _re.sub(r"\s+", "", text or "")
    b = _re.sub(r"\s+", "", context or "")
    if not a or not b:
        return 0.0
    matched = sum(bl.size for bl in _SM(None, a, b).get_matching_blocks())
    return matched / len(a)


def _parrot_blocked(segments: list[str], last_context: str) -> tuple[bool, float]:
    """字面重合守卫（纯函数）：重合率超阈值且有效字符数达标 → 判定复述上一条。

    返回 (是否拦截, 重合率)；last_context 为空或生成文本有效字符不足 → (False, 0.0)。
    """
    joined = "\n".join(segments or [])
    if not (last_context or "").strip():
        return False, 0.0
    ratio = _context_overlap_ratio(joined, last_context)
    import re as _re
    _chars = len(_re.sub(r"\s+", "", joined))
    if _chars < _PARROT_MIN_CHARS:
        return False, ratio
    return ratio > _PARROT_OVERLAP_THRESHOLD, ratio
