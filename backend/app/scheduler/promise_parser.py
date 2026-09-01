"""定时承诺解析器 — 从 LLM 回复/用户消息中提取时间承诺（[timer:xx] 标签 + 正则兜底）"""
import re
from datetime import datetime, timedelta, timezone

# [timer:20m] / [timer:30分钟] / 【计时器:1h】/ [timer:45s]
_TIMER_TAG = re.compile(
    r"[\[【]\s*(?:timer|计时器)\s*[:：]\s*(\d+)\s*(h|小时|m|min|分钟|s|秒)?\s*[\]】]",
    re.IGNORECASE,
)

# 中文数字映射（"二十"=20、"两"=2、"半"=半小时）
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_to_int(text: str) -> float | None:
    """中文/阿拉伯数字串 → 数值。支持：2 / 二十 / 十几(≈15) / 两 / 半(→0.5)。"""
    t = (text or "").strip()
    if not t:
        return None
    if t.isdigit():
        return int(t)
    if t == "半":
        return 0.5
    if t == "十几":
        return 15.0
    if t == "几":
        return 5.0
    if t == "十":
        return 10.0
    if t.startswith("十") and len(t) > 1:
        tail = t[1:]
        return (10 + _CN_DIGITS[tail]) if tail in _CN_DIGITS else 10.0
    if t.endswith("十") and len(t) > 1:
        head = t[:-1]
        return (_CN_DIGITS[head] * 10) if head in _CN_DIGITS else None
    return _CN_DIGITS.get(t)


# 数字（阿拉伯 + 中文）与时间单位
_NUM = r"(?:[一二两三四五六七八九十半几\d]+)"
_UNIT = r"(?:分钟|min|m|小时|h|时)"

# 正则兜底：(数字)(单位)(去向动词)，覆盖 AI/用户常见的口头承诺句式（2026-08-14 扩充分词）
_PATTERNS = [
    (r"洗\s*(" + _NUM + r")\s*(" + _UNIT + r")", "shower"),
    (r"睡\s*(" + _NUM + r")\s*(" + _UNIT + r")", "sleep"),
    (r"(?:眯|休息|歇|补个觉)\s*(" + _NUM + r")\s*(" + _UNIT + r")", "sleep"),
    (r"吃(?:个|顿)?饭.*?(" + _NUM + r")\s*(" + _UNIT + r")", "meal"),
    # "20分钟到家 / 半小时后回来 / 十几分钟到 / 等我10分钟 / 2h后再联系你"
    (r"(" + _NUM + r")\s*(" + _UNIT + r")\s*(?:左右|之后|以后|后)?\s*(?:回来|回|到家|到|到你家|找你|回来找你|联系你|见你|过去找你)", "back"),
    (r"(?:等我|等我回来)\s*(" + _NUM + r")\s*(" + _UNIT + r")", "back"),
    # "差不多/大概/约 20 分钟到家"
    (r"(?:差不多|大概|约|大约)\s*(" + _NUM + r")\s*(" + _UNIT + r")\s*(?:左右|之后|后)?\s*(?:回来|回|到家|到|找你)", "back"),
    # "五分钟后就能吃上饭了 / 几分钟就弄好 / 半小时就能完成 / 20分钟搞定"（完成类，到点 AI 主动问）
    (r"(" + _NUM + r")\s*(" + _UNIT + r")\s*(?:左右|之后|以后|后)?\s*(?:就|就能|就|就可以|能|可以|差不多就|马上就|应该就)?\s*(?:吃上|做好|弄好|搞定|完成|结束|好了|好|出锅|上桌|到)", "ready"),
    # "二十分钟吧，好了我叫你 / 20分钟后好了叫我 / 半小时后叫你起床"（到点叫你/喊你）
    (r"(" + _NUM + r")\s*(" + _UNIT + r")\s*(?:吧|左右|之后|以后|后)?\s*.*?(?:好了|做好|弄好|搞定|好|到点)?\s*(?:我)?(?:叫你|来叫你|叫你起来|叫你起床|喊你|喊你起来)", "ready"),
    (r"(" + _NUM + r")\s*(" + _UNIT + r")\s*(?:吧|左右|之后|以后|后)?\s*.*?(?:好了|做好|弄好|搞定|好|到点)?\s*(?:叫我|来叫我|喊我|叫我起来|叫我起床)", "ready"),
]

# 模糊时长兜底（2026-08-15）：无明确数字但有承诺语义（叫你/喊我/来找你等），按默认分钟计时
_VAGUE_PATTERNS = [
    # "等下饭好了叫你 / 等会弄完找你 / 一会儿好了喊我" → 10 分钟
    (r"(?:等下|等会|等会儿|等一会|等一阵|一会儿|一会|稍等|待会|待会儿)\s*(?:.*?)?\s*(?:我)?(?:叫你|来叫你|叫你起来|叫你起床|喊你|喊你起来|叫我|来叫我|喊我|告诉我|找你|来找你|来找我)", 10, "ready"),
    (r"(?:先|再|去)?\s*(?:忙|弄|干|搞|处理|写|做)\s*(?:会|一下|点)?\s*(?:事|活|东西|文件)?\s*(?:，|,)?\s*(?:完了|弄完|忙完|搞定|之后|等下|等会)\s*(?:再|就)?\s*(?:我)?(?:叫你|来叫你|喊你|叫我|来叫我|喊我|找你|来找你|来找我)", 10, "ready"),
    # "睡醒了喊你 / 眯一会儿叫你 / 补个觉好了叫你" → 30 分钟
    (r"(?:睡醒|睡一觉|眯一会|眯会儿|补个觉|休息一会|歇会儿)\s*(?:.*?)?\s*(?:我)?(?:叫你|来叫你|喊你|叫我|来叫我|喊我|来找你)", 30, "ready"),
    # "看完这集叫你 / 弄完手上的事找你 / 忙完来找你" → 30 分钟
    (r"(?:看完|弄完|做完|忙完|搞定|处理好|收个尾)\s*(?:这集|这部|这个|这些|手上|手头|手里|那点|剩下)?\s*(?:事|活|东西)?\s*(?:我)?(?:叫你|来叫你|叫你起来|喊你|叫我|来叫我|喊我|找你|来找你|来找我)", 30, "ready"),
    # 陪伴主动线（2026-08-30）：无数字日常句式兜底（去开会/去吃饭/等下试/去洗澡），到点 AI 主动关心
    (r"开会去了|去开会|去忙了|去忙会儿", 60, "ready"),
    (r"吃饭去了|去吃饭了|先去吃饭|去吃饭", 40, "ready"),
    (r"(?:等下|等会|等会儿|一会|一会儿|待会|待会儿)\s*试(?:试)?", 10, "ready"),
    (r"洗澡去了|去洗个澡|洗个澡|去洗澡", 30, "ready"),
]

# 最长承诺：24 小时
MAX_MINUTES = 24 * 60


def extract_timer(
    response: str,
    user_id: int,
    character_id: int,
    session_id: int,
    source_message_id: int | None = None,
    sender: str = "ai",
) -> dict | None:
    """从回复/消息中提取定时承诺，返回事件数据或 None

    优先级：显式标签 > 正则兜底；支持中文数字（二十/十几/两/半）。
    sender: ai=AI承诺（到点 AI 说"我回来了"）；user=用户承诺（到点 AI 主动问）。
    """
    minutes: float | None = None
    event_type: str | None = None
    promise_text: str | None = None

    # 1. 显式标签（最可靠）
    tag = _TIMER_TAG.search(response)
    if tag:
        minutes = float(int(tag.group(1)))
        unit = (tag.group(2) or "m").lower()
        if unit in ("h", "小时"):
            minutes *= 60
        elif unit in ("s", "秒"):
            minutes = max(1, round(minutes / 60))
        event_type = "back"
        promise_text = f"{int(minutes)} 分钟后回来"

    # 2. 正则兜底
    if minutes is None:
        for pattern, etype in _PATTERNS:
            m = re.search(pattern, response)
            if not m:
                continue
            n = _cn_to_int(m.group(1))
            if n is None:
                continue
            unit = (m.group(2) or "").lower()
            minutes = n * 60 if unit in ("h", "小时", "时") else n
            event_type = etype
            promise_text = m.group(0)[:120]
            break

    # 3. 模糊时长兜底：无数字但有承诺语义 → 默认分钟（等下=10 / 睡醒=30 / 忙完=30）
    if minutes is None:
        for pattern, default_minutes, etype in _VAGUE_PATTERNS:
            m = re.search(pattern, response)
            if not m:
                continue
            minutes = float(default_minutes)
            event_type = etype
            promise_text = m.group(0)[:120]
            break

    if minutes is None:
        return None

    minutes = int(min(max(minutes, 1), MAX_MINUTES))
    return {
        "user_id": user_id,
        "character_id": character_id,
        "session_id": session_id,
        "trigger_at": datetime.now(timezone.utc) + timedelta(minutes=minutes),
        "event_type": event_type or "back",
        "source_message_id": source_message_id,
        "sender": sender if sender in ("ai", "user") else "ai",
        "promise_text": promise_text,
    }


# ready 承诺的结果关键词组（陪伴主动线 2026-08-30）：按 content_hint 归类选择，命中即视为用户已回报结果
_READY_RESULT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "meeting": ("开完", "开好", "散会", "忙完", "忙好", "结束", "好了"),
    "meal": ("吃完", "吃好", "吃过", "吃饱"),
    "shower": ("洗完", "洗好", "洗过", "洗好了"),
    "try": ("试完", "试好", "试过", "行了", "好了", "搞定"),
}
_READY_RESULT_FALLBACK = ("好了", "搞定", "结束", "完成", "回来了")


def ready_result_seen(recent_user_texts: list[str], hint: str = "") -> bool:
    """判断用户最近消息是否已包含 ready 承诺的结果关键词（用于跳过到点重复询问）。

    依据 hint（ScheduledEvent.content_hint，事件创建时的匹配原文）选择关键词组：
    开会/忙→meeting、吃饭→meal、洗澡→shower、试→try，其余走兜底。
    纯函数零 IO；命中任一关键词即 True，空列表恒 False。
    """
    if not recent_user_texts:
        return False
    h = hint or ""
    if "开会" in h or "忙" in h:
        kws = _READY_RESULT_KEYWORDS["meeting"]
    elif "吃饭" in h:
        kws = _READY_RESULT_KEYWORDS["meal"]
    elif "澡" in h:
        kws = _READY_RESULT_KEYWORDS["shower"]
    elif "试" in h:
        kws = _READY_RESULT_KEYWORDS["try"]
    else:
        kws = _READY_RESULT_FALLBACK
    return any(any(kw in (t or "") for kw in kws) for t in recent_user_texts)


def strip_timer_tag(text: str) -> str:
    """从文本中移除 [timer:xx] 标签，返回清理后的文本"""
    return _TIMER_TAG.sub("", text).strip()
