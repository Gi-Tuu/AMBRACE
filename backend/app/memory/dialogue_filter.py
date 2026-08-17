"""聊天记忆"原文拦截"过滤器。

提取器（extractor）与 LLM 输出标记（【记忆：...】）都可能把对话台词原样写进记忆，
本模块提供启发式判定：命中即视为"误抄台词原文"，聊天来源的记忆不落库。

只使用"强信号"避免误伤合法记忆（如「用户爱喊我"老公"」「用户喜欢被照顾（揉头、搂抱）」）：
1) 省略号开头（台词延续，如“……怕什么怕”）
2) 含"叙事括号"：括号内 ≥4 字且带 了/着/你/的/地 等叙事粒子的动作描写
   （如（移开视线，声音闷闷的）（放下筷子，看你一眼）），或短动作词
   （大喘气/哽咽/哈欠/泪花等）
3) 口语复述痕迹（哈哈/嘿嘿 且长度>20）
"""
import re

_LEAD_ELLIPSIS = ("……", "…", "...", "。。。")
# 动作/状态动词（用于叙事括号判定）
_ACTION_VERBS = (
    "移开", "放下", "松开", "揉", "别过脸", "低下头", "抬头", "转开", "嘀咕", "嘟囔",
    "撇嘴", "翻白眼", "瞪", "皱眉", "凑近", "搂", "揽", "戳", "捏", "摸", "拍", "瞥",
    "侧头", "偏头", "垂眼", "抿嘴", "咬唇", "攥", "环", "抱", "牵", "拉", "捂", "掩",
    "哼", "叹气", "笑了笑", "笑道", "看了看", "看你一眼", "看你", "跺", "踢", "伸", "缩",
    "歪头", "眯眼", "红了脸", "耳根", "打了个哈欠", "切", "吆喝", "端", "递", "拿", "接",
    "站", "坐", "躺", "靠", "走", "推门", "进", "出", "穿", "系", "咬", "嚼", "咽",
    "夹", "盛", "喝", "倒", "抬头看", "瞥了", "看了", "盯着", "望着", "摸了摸", "拍了拍",
    "揉了揉", "捏了捏", "低下头", "顿住", "回过神", "转身", "回过身", "扬起", "勾起",
)
# 短动作词（括号内容短但明显是动作/状态）
_SHORT_ACTION = ("大喘气", "喘气", "哽咽", "哈欠", "哭腔", "泪花", "眼角", "顿住", "顿了一下")

_PAREN_RE = re.compile(r"[（(]([^（()）]{1,40})[）)]")


def _paren_is_action(inner: str) -> bool:
    """括号内容是否为"动作/情绪描写"（角色扮演台词特征）。"""
    if any(q in inner for q in _SHORT_ACTION):
        return True
    if len(inner) < 4:
        return False
    has_particle = ("了" in inner) or ("着" in inner) or ("你" in inner) or inner.endswith(("的", "地"))
    if not has_particle:
        return False
    return any(v in inner for v in _ACTION_VERBS) or inner.endswith(("的", "地"))


def looks_like_raw_dialogue(val) -> bool:
    """识别误抄的对话原文/角色扮演台词/动作描写（强信号，任一命中即判定）。"""
    v = (val or "").strip()
    if not v or len(v) < 2:
        return False
    if v.startswith(_LEAD_ELLIPSIS):
        return True
    if _PAREN_RE.search(v) and any(_paren_is_action(m.group(1)) for m in _PAREN_RE.finditer(v)):
        return True
    if ("哈哈" in v or "嘿嘿" in v) and len(v) > 20:
        return True
    return False
