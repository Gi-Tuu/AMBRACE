# -*- coding: utf-8 -*-
"""用户位置一致性纯规则（记忆时态缺陷族·第二批任务2，2026-09-17）。

生产现象：user_facts.location 权威值被无关聊天行覆盖（id=5 现行值
「用户说芒芒已经被照顾好了，外卖到了会叫我。」，真实权威「常驻湛江市·…」被挤进
previous_value），而 AI 生活/朋友圈/主动消息又在把旧「长沙」当现状写回，形成自激回声腔。

本模块提供三组零依赖纯函数（不读 DB、不调 LLM）：

1. ``looks_like_location_value`` / ``strong_location_value``：位置槽值的**证据锚点**
   （写侧拒绝垃圾值、读侧识别可共享的权威值）；
2. ``resolve_location_value``：现行值不达标时回退 ``previous_value``（仅接受强锚点，
   防止把「长沙」这种旧值当权威复活）——放在 ``app/memory/user_facts.py`` 调用；
3. ``location_conflict``：拟写内容与权威位置冲突的**保守**判定
   （同一句里「地点前有位置介词」才算冲突；「长沙特产」这类名词性提及不判）。

与 ``app/agent/reflection._STALE_LOCATION_WORDS`` 同源（该处为反思校验用），
此处独立一份并在城市表上扩容，避免 import 环与跨模块耦合。
"""
from __future__ import annotations

import re

# 常见城市/地名表（用于「值是否像位置」与「内容是否在说另一个城市」两处判定）
USER_LOCATION_CITIES: tuple[str, ...] = (
    "湛江", "长沙", "广州", "深圳", "东莞", "佛山", "珠海", "中山", "惠州", "汕头",
    "茂名", "阳江", "韶关", "清远", "肇庆", "江门", "河源", "梅州", "汕尾", "潮州",
    "揭阳", "云浮", "北京", "上海", "天津", "重庆", "杭州", "南京", "武汉", "成都",
    "西安", "苏州", "青岛", "沈阳", "哈尔滨", "大连", "厦门", "福州", "济南", "郑州",
    "昆明", "贵阳", "南宁", "海口", "三亚", "兰州", "太原", "石家庄", "合肥", "南昌",
    "长春", "无锡", "宁波", "温州", "徐州", "常州", "南通", "扬州", "嘉兴", "绍兴",
    "台州", "洛阳", "烟台", "潍坊", "唐山", "保定", "桂林", "柳州", "遵义", "常德",
    "岳阳", "株洲", "湘潭", "衡阳", "郴州", "香港", "澳门", "台北",
)

# 位置谓语的稳定标记：命中即视为「常驻类」权威值（比单点城市更可信）
_STRONG_MARKERS = ("常驻", "常住", "定居", "现居", "长期在", "老家", "家乡")
# 位置值的通用锚点词（学校/居住场景），弱于强锚点
_PLACE_ANCHOR_WORDS = (
    "住在", "居住", "住在", "宿舍", "校区", "学校", "大学", "学院", "小区",
    "公寓", "搬到", "搬回", "公司", "城市", "位置",
)
_PLACE_SUFFIX_RE = re.compile(r"[\u4e00-\u9fa5]{2,6}(?:市|省|区|县|镇|州|盟|旗)")
# 位置介词（冲突判定窗口内需要有它，避免名词性「长沙特产」误判）。
# 只认「当前位置」类介词（在/待/呆/住）：生产回声腔的形态是「轩在长沙」「在长沙顶着大太阳」；
# 「去/回/到长沙」多为出行/往事叙事，宁可不判也不误伤正常文案（如「你上次说要去长沙」）。
_CUES = ("在", "待", "呆", "住")
_CUE_WINDOW = 4


def has_city(text: str) -> bool:
    """文本是否含已知城市名。"""
    return any(c in (text or "") for c in USER_LOCATION_CITIES)


def looks_like_location_value(text: str) -> bool:
    """位置槽值的通用证据锚点：城市名 / 稳定标记 / 地点词 / 「XX市（区/县…）」形态。

    用于写侧拒绝垃圾值（生产实证：无关聊天行「…外卖到了会叫我。」曾覆盖 location 槽），
    以及读侧判断权威值是否可信。
    """
    t = (text or "").strip()
    if not t or len(t) > 200:
        return False
    if has_city(t):
        return True
    if any(w in t for w in _STRONG_MARKERS):
        return True
    if any(w in t for w in _PLACE_ANCHOR_WORDS):
        return True
    if _PLACE_SUFFIX_RE.search(t):
        return True
    return False


def strong_location_value(text: str) -> bool:
    """是否为「强锚点」位置值（常驻/定居/老家，或城市 + 学校/校区/宿舍/生活场景）。

    仅强锚点允许作为 ``previous_value`` 回退（防止旧「长沙」被当权威复活）。
    """
    t = (text or "").strip()
    if not t:
        return False
    if any(w in t for w in _STRONG_MARKERS):
        return True
    return has_city(t) and any(
        w in t for w in ("大学", "学院", "学校", "校区", "宿舍", "小区", "公寓", "在读")
    )


# 写侧宽松闸：句读/引号/代词开头一票否决；长文本另需位置证据锚点
_BAD_VALUE_CHARS = "，。！？；、,.!?;\"'「」"
_PRONOUN_PREFIXES = ("用户", "我们", "你们", "他们", "我", "你", "他", "她", "它")
# 语句功能词：短 token 含这些即视为句子片段，不是地点名
_STOP_MARKERS = ("了", "的", "吗", "吧", "呢", "会", "要", "想", "说", "吃", "喝", "买")
_SHORT_TOKEN_MAX = 6


def location_write_ok(text: str) -> bool:
    """位置槽写入闸（宁松勿误伤）：句读/代词开头直接否决；其余按「锚点或短 token」放行。

    生产实证的污染源是**整句聊天行**（「用户说芒芒已经被照顾好了，外卖到了会叫我。」）：
    含句读、以代词开头即否决；长文本（>6 字）必须含城市名 / 常驻地标记 / 地点词 / 「XX市」形态；
    短 token（「广州」「示例城」「a」）无语句功能词时放行，避免误伤既有调用点与占位值。
    """
    t = (text or "").strip()
    if not t:
        return False
    if any(ch in t for ch in _BAD_VALUE_CHARS):
        return False
    if any(t.startswith(p) for p in _PRONOUN_PREFIXES):
        return False
    if looks_like_location_value(t):
        return True
    return len(t) <= _SHORT_TOKEN_MAX and not any(m in t for m in _STOP_MARKERS)


def authoritative_city(text: str) -> str | None:
    """从权威位置文本里取城市名（城市表命中优先，其次「XX市」形态）。无则 None。"""
    t = (text or "").strip()
    if not t:
        return None
    for c in USER_LOCATION_CITIES:
        if c in t:
            return c
    m = _PLACE_SUFFIX_RE.search(t)
    return m.group(1) if m else None


def _city_mentioned_as_current(text: str, city: str) -> bool:
    """城市名是否在「位置介词窗口」内出现（如「在长沙」「回长沙」「到了长沙」）。

    仅名词性提及（「长沙特产」「想念长沙」）不算——宁可不判，也不误伤正常文案。
    """
    start = 0
    while True:
        i = text.find(city, start)
        if i < 0:
            return False
        window = text[max(0, i - _CUE_WINDOW):i]
        if any(ch in window for ch in _CUES):
            return True
        start = i + len(city)


def location_conflict(text: str, authoritative: str) -> str | None:
    """拟写内容与权威位置冲突 → 返回冲突城市名；无冲突/无法判定返回 None。

    保守口径（只拦「把用户说成此刻在别的城市」）：
    - 权威值里识别不出城市 → 不判；
    - 文本本身就带着权威城市（可能在说出行）→ 不判；
    - 异城必须出现在「当前位置介词」窗口内（在/待/呆/住），「去/回/到长沙」等出行叙事不判。
    """
    if not text or not authoritative:
        return None
    auth = authoritative_city(authoritative)
    if not auth:
        return None
    if auth in text:  # 文本提到了权威城市（哪怕在讲出行）→ 保守放行
        return None
    for c in USER_LOCATION_CITIES:
        if c == auth or c in auth or auth in c:
            continue
        if c in text and _city_mentioned_as_current(text, c):
            return c
    return None


__all__ = [
    "USER_LOCATION_CITIES",
    "has_city",
    "looks_like_location_value",
    "strong_location_value",
    "location_write_ok",
    "authoritative_city",
    "location_conflict",
]
