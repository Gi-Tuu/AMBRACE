# -*- coding: utf-8 -*-
"""写侧记忆文本定形（A30 批 1，2026-10-10）：确定性纯函数规范化。

为什么存在：0-4 尺子的生成侧认证要求「三跑逐字一致」，而未认证的题里有一半只差措辞
——同一事实被写成「现住/现居」「接送牌要填/接送牌填」「忌口辣椒（2026-10-05）/
（2026-10-05提到）/（2026-10-05起）」。落库文本的形状由本模块收敛，不靠提示词赌合规。

口径边界（只做下面两件事，其余一律原样）：
1. 剥行尾时点括注；2. 词级同义表统一写法。
**不改**句法结构、不增删信息、不改标点习惯（全/半角、句号都不归一）、不改句首人称词
（`memory/speaker.resolve_speaker_from_content` 用句首「用户/对方/他/她」判 user/FACT、
用「我」判 character/INFERRED，越界即归属静默翻转）。

── ① 支持的「行尾时点括注」形态（逐条枚举，不在名单内的一律不动）──
三条前提：a) 用全角（）或半角()；b) 位于整段文本**末尾**（其后最多再跟一个收尾标点
「。！？.!，、」与空白）；c) 括注内容**整体**只是时点——混入任何非时点字（「（橘猫）」
「（第三针）」「（待用户补充）」）即原样保留。
- A1 绝对日期：（2026-10-05）（2026-10-5）（2026/10/05）（2026.10.05）（2026年10月5日）
  （2026年10月5号）（2026年10月）（2026年）（2026）（10月5日）（10月5号）
- A2 日期＋时点尾词：（2026-10-05提到）（2026-10-05起）…——尾词白名单：
  提到 / 说起 / 说 / 起 / 开始 / 记录 / 当天 / 那天 / 当时
- A3 相对时点词：（今天）（今日）（昨天）（昨日）（前天）（大前天）（刚刚）（刚才）
  （方才）（最近）（近期）
不剥「（明天）」「（下周）」这类**指向未来**的时点：它带真实信息（计划有效期靠它解析），
A3 只登记「记录时点」这一类（历史抖动也全部来自这一类）。
合法性依据：注入层每行已统一打 `[记录于 YYYY-MM-DD]`（`memory/format.py` 的
`format_memory_line`），落库正文自带的日期后缀是纯冗余。
剥完若整段为空（原文整条就是一个时点括注）→ 返回原文，绝不让载荷变空而丢记忆；
连续叠套的时点括注逐层剥净（不设层数上限，否则「剥了三层还剩一层」会破坏幂等）。

── ② 同义表（窄：只登记实测到的抖动，宁少勿滥，拿不准不进表）──
- `现住` → `现居`（j1s02 三跑抖动）
- `要填写` → `填写`、`要填` → `填`（j1f01「接送牌要填/填女儿名字」；长形优先，避免
  「要填写→填写写」）——情态词剥离带守卫：「要」字若是前一个词的词尾（主要/需要/要求/
  必要/不要/只要/就要…）则整条不动，否则「主要填写→主填写」吞信息、「不要填写→不填写」
  还会翻转否定。
  副作用登记：`现住→现居` 命中 `tense._STABLE_LOCATION_MARKERS`，新写入的位置类记忆由
  transient 转 enduring——这是方案 §七 已登记的下游影响，非失控。

超长正文（> `_MAX_LEN`）不参与定形：记忆正文实际都在几十～几百字量级，超长输入
一律原样返回（同 fail-open）。
"""
from __future__ import annotations

import re

from app.utils.logger import get_logger

_logger = get_logger("memory.normalize")

# 超长正文不定形（见模块注释）
_MAX_LEN = 2000

# 日期本体：年-月-日 / 年-月 / 年月日 / 月日 / 年 / 裸年份（顺序＝正则候选顺序，长形优先）
_DATE = (
    r"\d{4}\s*[-/.年]\s*\d{1,2}(?:\s*[-/.月]\s*\d{1,2}\s*[日号]?|\s*月)?"
    r"|\d{1,2}\s*月\s*\d{1,2}\s*[日号]?"
    r"|\d{4}\s*年"
    r"|\d{4}"
)

# 日期后可跟的「时点尾词」（A2）
_TIME_TAILS = ("说起", "提到", "开始", "记录", "当天", "那天", "当时", "说", "起")
# 纯相对时点词（A3，只登记记录时点，不含未来指向词）
_REL_POINTS = ("大前天", "刚刚", "刚才", "方才", "今天", "今日",
               "昨天", "昨日", "前天", "最近", "近期")


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


# 行尾时点括注：括号 + 纯时点内容 + 括号，且后面只能是收尾标点与空白直到文末
_TRAILING_TIME_RE = re.compile(
    r"[（(]\s*(?:(?:" + _DATE + r")(?:\s*(?:" + _alt(_TIME_TAILS) + r"))?|(?:" + _alt(_REL_POINTS) + r"))"
    r"\s*[)）]\s*(?=[。！？.!，、]?\s*$)"
)

# 「要」字作为前词词尾时不得剥离（否定词与构词成分一律放行原文）
_MODAL_BLOCK_BEFORE = "主次需求必紧摘纪提要不没非未只就还更想总都便允许召命愿尽"

# 同义表：(变体, 统一写法, 「不允许出现在变体之前的字符」)——逐条登记、逐条注明来由
_SYNONYM_TABLE = (
    ("现住", "现居", ""),
    ("要填写", "填写", _MODAL_BLOCK_BEFORE),
    ("要填", "填", _MODAL_BLOCK_BEFORE),
)

# 匹配用的正则表（按上表顺序应用：长形在前，保证「要填写」不会被「要填」切成「填写写」）
_SYN_RE_RES = tuple(
    (re.compile(r"(?<![" + blk + r"])" + re.escape(var)) if blk else re.compile(re.escape(var)), rep)
    for var, rep, blk in _SYNONYM_TABLE
)


def _strip_trailing_time(text: str) -> str:
    """逐层剥行尾时点括注，直到不再命中（每轮至少少掉一对括号，必然收敛；幂等要求不留上限）。"""
    out = text
    while True:
        cand = _TRAILING_TIME_RE.sub("", out, count=1)
        if cand == out:
            return out
        out = cand


def _apply_synonyms(text: str) -> str:
    """同义表逐条应用（只做词级替换，不动句法）。"""
    out = text
    for pattern, canonical in _SYN_RE_RES:
        out = pattern.sub(canonical, out)
    return out


def normalize_memory_text(text: str) -> str:
    """记忆正文落库前的确定性规范化（纯函数、幂等、fail-open）。

    任何异常、非字符串、空串、超长输入一律返回原文（只 INFO 留痕，绝不抛、绝不让
    规范化把记忆写丢）。
    """
    if not isinstance(text, str) or not text or len(text) > _MAX_LEN:
        return text
    try:
        stripped = _strip_trailing_time(text)
        changed = stripped != text
        out = _apply_synonyms(stripped)
        if changed:
            out = out.rstrip()
        if not out.strip():
            # 整条正文就是一个时点括注 → 剥完就空了，宁可不剥
            return text
        return out
    except Exception as exc:  # noqa: BLE001 - fail-open：规范化永不影响写入
        _logger.info("[A30 写侧定形] fail-open 原样返回：err=%r text=%.40r", exc, text)
        return text
