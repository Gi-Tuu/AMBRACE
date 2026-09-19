# -*- coding: utf-8 -*-
"""通用槽值写入闸（记忆时态缺陷族·第三批，2026-09-17）。

生产实证（``user_facts`` 只读扫描，user_id=3 共 6 行）：四个非位置槽已被「整句聊天」污染——
``health=用户出门去提前占个好位置``、``living=用户买了校园网，覆盖全校教学区和宿舍``、
``goal_state=用户参加的比赛快截止了，用户要赶出作品来``、``job=用户今晚有课，时间赶``。
位置槽当日已人工写回权威值，且位置有写入闸（``location_guard.location_write_ok``）；
其余槽没有 → 今天修好明天还会被同样覆盖。本模块把位置闸抽象成**通用槽值校验**：
槽值必须是短语/近况词，不能是整句聊天行。

三组零依赖纯函数（不读 DB、不调 LLM）：

1. ``slot_value_reject_reason``：返回拒写原因码（``None`` = 放行）——写侧 warning 日志与
   只读扫描脚本按同一判据出裁决清单；
2. ``generic_slot_value_ok`` / ``generic_slot_value_reject_reason``：槽无关的通用判据
   （长度、句中句末标点、转述句、对话残留、逗号堆叠、逗号分句过长；结尾句末标点先剥再判）；
3. ``slot_value_write_ok``：布尔封装，叠加槽特异规则（``location`` 沿用
   ``location_guard.location_write_ok`` 的位置证据锚点，**不改变既有位置行为**）。

口径（对齐交接文件）：
- **fail-closed**：校验不过就不写该槽（调用方仍可落普通 memories，不污染槽位）；
- ``relationship``/``health`` 仍须**显式开启**（红线在 ``user_facts.user_fact_slot_enabled``；
  本模块只管「值像不像槽值」，不参与槽开关，绝不把这两槽带出去）；
- 被拒写入不得覆盖 ``previous_value``：``upsert_user_fact`` 校验不过即 return None，不触碰 DB。

阈值/锚点表的校准理由见各常量注释；锚点表独立一份，避免与 ``user_facts.MUTABLE_SLOTS``
形成 import 环（同 ``location_guard`` 与 reflection 的处理）。
"""
from __future__ import annotations

# 通用长度上限（字）：四个生产污染样本 10~20 字，正常槽值为 2~12 字短语；
# 取 30 既挡住整段聊天行，又给「身份证式长值」留余量。
GENERIC_VALUE_MAX_LEN = 30
# 长值（> ANCHOR_MIN_LEN 字）必须含该槽语义锚点：短 token（「程序员」「已婚」「生病住院」
# 「大二在读」「有课，时间紧」）一律放行——宁松勿误伤既有调用点与既有用例。
ANCHOR_MIN_LEN = 6
# 句末标点：「整句聊天」的强信号，但只在**句中**算叙述证据；结尾标点属书写习惯，
# 判前先剥（2026-09-19 修复误拒：合法槽值只因结尾一个句号就整条被拒，权威槽冻结约 2 天）。
_SENTENCE_END_PUNCT_CHARS = "。！？；!?;…～~"
# 换行/回车：防多行粘贴，出现在值里任何位置都拒，不因剥结尾标点而放松。
_LINE_BREAK_CHARS = "\n\r"
# 对话残留：成对引号/书名号（槽值里出现即是从聊天行抄来）。
_DIALOGUE_RESIDUE_CHARS = "\"'“”‘’「」『』《》〈〉"
# 转述主语：槽值应是短语，不该以「用户/我/你/他…」开头（与位置闸 _PRONOUN_PREFIXES 同款口径）。
_NARRATION_PREFIXES = ("用户", "我们", "你们", "他们", "我", "你", "他", "她", "它")
# 转述谓语：兜住不以主语开头的报告式整句（「……，用户表示……」）。
_NARRATION_MARKERS = (
    "用户说", "用户表示", "用户称", "用户提到", "用户觉得", "用户认为",
    "用户已经", "用户正在", "用户打算", "用户准备", "用户想",
)
# 逗号堆叠：单个逗号是「短语并置」（「有课，时间紧」），≥2 个即聊天行。
_MAX_COMMAS = 1
_COMMA_CHARS = "，,"
# 短语并置的长度：逗号两侧每段 ≤ COMMA_SEGMENT_MAX_LEN 字（「有课，时间紧」「慢性胃炎，忌辛辣」）；
# 任一段更长 → 聊天行分句（生产实证「买了校园网，覆盖全校教学区和宿舍」，防 LLM 去掉「用户」前缀后漏网）。
COMMA_SEGMENT_MAX_LEN = 6

# 槽语义锚点（正向放过面）：4 个非位置槽各一份语义词表，来源 = ``user_facts.MUTABLE_SLOTS``
# 关键词 + 生产常见近况词扩容。``location`` 不在此表（改走 location_guard 的位置证据锚点）。
SLOT_ANCHORS: dict[str, tuple[str, ...]] = {
    "job": (
        "上班", "工作", "公司", "单位", "入职", "离职", "辞职", "跳槽", "转行", "岗位",
        "学校", "上课", "有课", "课程", "专业", "学业", "在读", "大学", "学院", "学生",
        "毕业", "考研", "备考", "实习", "兼职", "就业", "求职", "面试", "工作室",
        "程序员", "工程师", "开发", "设计", "教师", "医生", "护士", "司机", "销售",
        "运营", "会计", "律师", "公务员", "创业", "个体", "读研", "读博", "高三", "大一",
        "大二", "大三", "大四", "研一", "研二", "研三",
    ),
    "relationship": (
        "单身", "恋爱", "在一起", "异地", "复合", "分手", "结婚", "已婚", "订婚",
        "离婚", "脱单", "对象", "伴侣", "老公", "老婆", "男友", "女友", "爱人",
    ),
    "living": (
        "搬家", "搬到", "搬回", "租房", "宿舍", "合租", "独居", "同居", "家里住",
        "住在", "居住", "校区", "公寓", "小区", "老家", "寝室", "住处", "学生宿舍",
    ),
    "goal_state": (
        "准备", "打算", "计划", "备考", "考研", "在考", "项目", "面试", "筹备",
        "争取", "冲刺", "截止", "进行中", "赶工", "报名", "论文", "答辩", "比赛",
        "作品", "初试", "复试", "考试", "毕设", "毕业设计", "开题", "实习",
    ),
    "health": (
        "生病", "住院", "出院", "康复", "手术", "怀孕", "体检", "吃药", "发烧",
        "感冒", "受伤", "过敏", "失眠", "血压", "血糖", "胃病", "慢性", "咳嗽",
        "头疼", "头痛", "腰疼", "腰痛", "颈椎", "贫血", "熬夜", "服药", "门诊", "复诊",
    ),
}


# ── 通用判据（槽无关）──────────────────────────────────────────────────────

def _strip_trailing_sentence_punct(text: str) -> str:
    """剥掉结尾的句末标点（可连续多个、可夹空白）；句中标点不动。"""
    t = text.rstrip()
    while t and t[-1] in _SENTENCE_END_PUNCT_CHARS:
        t = t[:-1].rstrip()
    return t


def generic_slot_value_reject_reason(value: str) -> str | None:
    """槽无关通用判据：放行返回 None，拒写返回原因码。

    句末标点按「剥掉结尾再判」处理：只有句中仍残留句末标点才视为整句叙述（``sentence_end_punct``）；
    换行/回车任何位置出现一律拒（``line_break``）。
    """
    t = (value or "").strip()
    if not t:
        return "empty"
    if any(ch in t for ch in _LINE_BREAK_CHARS):
        return "line_break"
    t = _strip_trailing_sentence_punct(t)
    if not t:
        return "empty"
    if any(ch in t for ch in _SENTENCE_END_PUNCT_CHARS):
        return "sentence_end_punct"
    if any(t.startswith(p) for p in _NARRATION_PREFIXES):
        return "narration_prefix"
    if any(w in t for w in _NARRATION_MARKERS):
        return "narration_reported"
    if any(ch in t for ch in _DIALOGUE_RESIDUE_CHARS):
        return "dialogue_residue"
    if sum(t.count(c) for c in _COMMA_CHARS) > _MAX_COMMAS:
        return "comma_stacking"
    if any(c in t for c in _COMMA_CHARS):
        segs = t.replace(_COMMA_CHARS[1], _COMMA_CHARS[0]).split(_COMMA_CHARS[0])
        if any(len(seg.strip()) > COMMA_SEGMENT_MAX_LEN for seg in segs):
            return "long_clause"
    return None


def generic_slot_value_ok(value: str) -> bool:
    """通用判据布尔封装（不含长度上限：长度按槽在 ``slot_value_reject_reason`` 里判）。"""
    return generic_slot_value_reject_reason(value) is None


# ── 槽值写入闸（通用 + 槽特异叠加）────────────────────────────────────────

def slot_value_reject_reason(slot: str, value: str) -> str | None:
    """槽值写入闸：放行返回 None，拒写返回原因码（供 warning 日志 / 只读扫描裁决）。

    判定顺序：通用判据 → 槽特异规则。
    - ``location`` 沿用 ``location_guard.location_write_ok``（句读/代词开头一票否决 +
      城市/常驻地/地点词证据锚点，短 token 放行）；长度上限保留该闸的 200——GPS 反查地名
      常长于 30 字，收紧会改变既有位置行为（交接允许「保留专有规则并叠加通用规则」）。
    - 其余槽：通用长度上限 ``GENERIC_VALUE_MAX_LEN``；长值须含该槽语义锚点
      （``SLOT_ANCHORS`` 未收录的槽不做锚点要求，避免误伤未知槽位）。
    """
    t = (value or "").strip()
    reason = generic_slot_value_reject_reason(t)
    if reason:
        return reason
    slot = (slot or "").strip()
    if slot == "location":
        from app.memory.location_guard import location_write_ok
        return None if location_write_ok(t) else "location_anchor"
    if len(t) > GENERIC_VALUE_MAX_LEN:
        return "too_long"
    anchors = SLOT_ANCHORS.get(slot)
    if anchors and len(t) > ANCHOR_MIN_LEN and not any(a in t for a in anchors):
        return "no_slot_anchor"
    return None


def slot_value_write_ok(slot: str, value: str) -> bool:
    """槽值是否允许写入（``upsert_user_fact`` 的唯一判据入口）。"""
    return slot_value_reject_reason(slot, value) is None


# ── 槽值规范化 L1（2026-09-19：本地确定性，不动上方任何判据）──────────────────
#
# 背景：``extractor`` 把 LLM 抽出的 USER_INFO 正文整句（「用户与sam是伴侣关系，…」）直接
# 当槽值传入，主语闸 + 逗号分句把几乎全部候选拒掉 → ``user_facts`` 自 09-17 起只拒不写。
# 本函数只做「形态归一」（去转述主语、剥结尾标点、按锚点取分句），**不改变任何放行判据**：
# 规范化后仍由 ``slot_value_reject_reason`` 裁决，不合格就返回 None（调用方原样传 val，
# 槽闸照旧拒写，fail-closed 口径与修复前一致）。

# 转述主语前缀最多剥几轮（「用户说」「他们说」…剥完可能露出下一层主语）。
_NORMALIZE_PREFIX_ROUNDS = 2
# 主语后面的转述谓语（与 ``_NARRATION_MARKERS`` 不同口径：这里只吃紧跟主语的动词）。
_REPORT_VERBS = ("表示", "觉得", "认为", "希望", "透露", "说")
# 连接词起首的分句＝叙述尾巴（「因为他赢了比赛」「所以很累」）：从整句里切出来也不是槽值 → 放弃。
_CONNECTIVE_PREFIXES = ("因为", "所以", "然后", "而且", "但是", "不过", "可是", "结果", "由于", "因此", "另外", "反正", "虽然", "如果", "要是")
# 分句切分符：逗号系（全/半角、顿号）+ 分号系。
_VALUE_DELIMITER_CHARS = "，,、;；"


def _strip_one_narration_prefix(text: str) -> str:
    """剥一层「转述主语（+的）+ 转述谓语（+逗号空白）」；不吃到内容词为止。"""
    for p in sorted(_NARRATION_PREFIXES, key=len, reverse=True):
        if not text.startswith(p):
            continue
        rest = text[len(p):]
        if rest.startswith("的"):
            rest = rest[1:]
        for verb in _REPORT_VERBS:
            if rest.startswith(verb):
                rest = rest[len(verb):].lstrip(" \t" + _VALUE_DELIMITER_CHARS)
                break
        return rest
    return text


def _value_segments(text: str) -> list[str]:
    """按逗号/顿号/分号切段（统一成全角逗号后 split），返回非空段。"""
    norm = text
    for ch in _VALUE_DELIMITER_CHARS[1:]:
        norm = norm.replace(ch, _VALUE_DELIMITER_CHARS[0])
    return [s for s in (seg.strip() for seg in norm.split(_VALUE_DELIMITER_CHARS[0])) if s]


def _pick_anchor_segment(slot: str, text: str, max_len: int | None = None) -> str | None:
    """取含该槽锚点词的分句：**锚点命中数最多者胜，并列取最左**；连接词起首的段一律跳过。

    合格＝含该槽锚点词、长度≤``max_len``、且**不以连接词起首**（「因为他赢了比赛」这类
    叙述尾巴不能当槽值）。锚点表未收录的槽（``location`` / 未知槽）一律返回 None。
    **09-19 Codex 复核三次定稿**：①原「取最长段」会选进叙述尾巴；②「取最左」会把过时状态选进来
    （「单身多年，最近脱单有了对象」→ 选到「单身多年」，与真实现状相反）；③「取最右」又会把并列
    复述的后半句顶掉（「与sam是伴侣关系，sam是用户的老公」）。最终＝**锚点密度优先 + 并列取左**。
    """
    anchors = SLOT_ANCHORS.get((slot or "").strip())
    if not anchors:
        return None
    best: str | None = None
    best_hits = 0
    for seg in _value_segments(text):
        if max_len is not None and len(seg) > max_len:
            continue
        if seg.startswith(_CONNECTIVE_PREFIXES):
            continue
        hits = sum(1 for a in anchors if a in seg)
        if hits > best_hits:          # 严格大于 → 并列时保留更靠左的那段
            best, best_hits = seg, hits
    return best


def normalize_slot_value(slot: str, raw: str) -> str | None:
    """把 LLM 抽出的 USER_INFO 整句规范成候选槽值；无法规范化返回 ``None``。

    纯函数、零依赖、可单测；只做形态归一（判据不变）：
    a) 去首尾空白后为空 → None；
    b) 去转述主语前缀（最多 ``_NORMALIZE_PREFIX_ROUNDS`` 轮）与其后的转述谓语；
    c) 剥结尾句末标点（复用 ``_strip_trailing_sentence_punct``）；
    d) 含逗号/分号且**至少一段含该槽锚点词** → 取含锚点的那一段（多段含锚点取最长）；
       无锚点段不裁剪、保留整串；
    e) 长度收敛：裁剪在选段时即限制 ``GENERIC_VALUE_MAX_LEN``（超长段不作为候选）；
       归一后仍超长 → None（禁止硬截断词语）；
    f) 结尾再 strip + 剥一次标点，空 → None。
    """
    t = (raw or "").strip()
    if not t:
        return None
    for _ in range(_NORMALIZE_PREFIX_ROUNDS):
        nxt = _strip_one_narration_prefix(t)
        if nxt == t:
            break
        t = nxt
    t = _strip_trailing_sentence_punct(t)
    # 叙述尾巴先砍（09-19 Codex 复核收紧）：以连接词起首的**收尾分句**（「…，因为…」「…，所以…」）
    # 整段丢掉——它是叙述、不是槽值；只砍尾部，不动句中分句。
    segs = _value_segments(t)
    if len(segs) >= 2 and segs[-1].startswith(_CONNECTIVE_PREFIXES):
        cut = max(t.rfind(ch) for ch in _VALUE_DELIMITER_CHARS)
        if cut > 0:
            t = _strip_trailing_sentence_punct(t[:cut].strip())
    if not t:
        return None
    # 规则 d 前置：**整串优先**——去掉主语/结尾标点后若整串本身已能过闸，就直接用它，
    # 避免「有课，时间赶」被裁成「有课」这种并置信息丢失。
    if slot_value_reject_reason(slot, t) is None:
        return t
    # 规则 d + e：整串不过闸时才取分句（从右往左的第一段合格分句）。
    picked = _pick_anchor_segment(slot, t, max_len=GENERIC_VALUE_MAX_LEN)
    if picked:
        t = picked
    if len(t) > GENERIC_VALUE_MAX_LEN:
        return None
    t = _strip_trailing_sentence_punct(t.strip())
    return t or None


__all__ = [
    "GENERIC_VALUE_MAX_LEN",
    "ANCHOR_MIN_LEN",
    "COMMA_SEGMENT_MAX_LEN",
    "SLOT_ANCHORS",
    "generic_slot_value_reject_reason",
    "generic_slot_value_ok",
    "slot_value_reject_reason",
    "slot_value_write_ok",
    "normalize_slot_value",
]
