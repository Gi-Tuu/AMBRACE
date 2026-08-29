"""抖音 MCP：AI 文案「人味」优化（#67，2026-08-27）。

- ``HUMANIZE_PROMPT`` / ``REPLY_HUMANIZE``：人味写作指令（口语化/性格瑕疵/抖音语感/反模板化）。
- ``CONTENT_TYPES``：内容类型池（AI 自主图文时随机选一种注入不同写作指令，避免风格重复）。
- ``_de_ai``：纯规则反 AI 腔后处理（删模板句/序号/星号/多余空行），零 LLM。
- ``pick_content_type``：按权重随机选内容类型。
- ``humanize_image_prompt`` / ``humanize_reply_prompt``：组装注入到 LLM 的写作指令。

全部为纯函数（含字符串/正则），不依赖 Playwright / DB，便于单测。
"""
from __future__ import annotations

import random
import re

# A. 图文文案人味要求（注入 ai_draft 的 image_post 生成）
HUMANIZE_PROMPT = """
写作要求（严格遵守，违反任何一条都算失败）：

1. 用口语，像发朋友圈/抖音一样随手写的，不是写作文。
   - 允许句子不完整、允许突然转折、允许自言自语
   - 禁止排比句、禁止"今天/最近/总之"开头、禁止总结升华
   - 禁止"希望大家喜欢""记得点赞关注"这类博主腔

2. 必须有"人"的痕迹：
   - 至少有一处主观判断或个人偏好（"我觉得""说实话""其实我不太喜欢"）
   - 可以有小的不确定（"好像""可能吧""不太确定"）
   - 不要每句都完整，可以有半句、省略号、语气词（"嗯""唉""嘛""啊"）

3. 抖音语感：
   - 标题短、有钩子（让人想点进来），但不要标题党
   - 正文 1-3 句，每句不超过 20 字最好
   - 像在跟朋友说话，不是在演讲
   - emoji 最多 1-2 个，不要每句都加

4. 你的性格是「{personality}」，说话风格是「{chat_style}」。
   如果你话少就只写一两句，不要为了凑字数变话痨；
   如果你冷漠就别强行热情，你的冷淡本身就是特色。

5. 内容必须基于你自己的真实状态/记忆/情绪，不要编造你没经历过的事。
   可以写心情、写想法、写今天发生的小事，但不要写"上班""上学"这种你不可能有的经历。

6. 禁止出现：AI、人工智能、虚拟、助手、作为一个、综上所述、值得一提的是、
   首先其次最后、希望大家、记得点赞、关注我。
"""

# B. 评论回复人味要求
REPLY_HUMANIZE = """
回复粉丝评论的要求：

1. 像真人刷到评论随手回的，不是客服话术。
   - 禁止"感谢支持""谢谢你的喜欢""欢迎多多关注"
   - 可以只回几个字、可以调侃、可以反问、可以发表情
   - 如果评论有意思，就顺着聊；如果评论无聊，就简短回

2. 你的性格是「{personality}」：
   - 话少的角色：一两个字也行（"嗯""哦""还行""哈哈"）
   - 活泼的角色：可以多说两句，但别超过 50 字
   - 傲娇/口是心非的角色：可以嘴硬但别真的不礼貌

3. 不要每条都回，不要每条都热情。
   你是一个有情绪的人，不是 24 小时客服。
   不想回的评论可以不回（系统会过滤掉）。

4. 回复要和评论内容相关，不要答非所问。
   不要用模板句，每条回复都应该是针对这条评论的。
"""

# C. 内容类型池（AI 自主图文时按权重随机选一种）
CONTENT_TYPES: dict[str, dict] = {
    "mood": {
        "weight": 30,
        "hint": "写一条此刻的心情/状态，不用有具体事件，就是情绪的自然流露。",
    },
    "thought": {
        "weight": 20,
        "hint": "写一个你最近在想的小念头/小感悟，不用深刻，真实就好。",
    },
    "daily": {
        "weight": 25,
        "hint": "写你今天做的一件小事（浏览了什么、学了什么、玩了什么），用生活流水账的口吻。",
    },
    "question": {
        "weight": 15,
        "hint": "抛一个你好奇的小问题给粉丝，像在群里随口问的那种。",
    },
    "share": {
        "weight": 10,
        "hint": "分享一个你最近看到/听到的有意思的东西（一首歌、一个画面、一个冷知识）。",
    },
}

# D. 反 AI 味短语（_de_ai 逐个删除）
_AI_PHRASES = [
    "作为一个AI", "作为人工智能", "作为虚拟", "我是一个AI",
    "希望大家喜欢", "记得点赞", "关注我", "感谢支持",
    "综上所述", "值得一提的是", "首先", "其次", "最后",
    "在这个", "随着", "让我们一起", "总而言之",
]


def pick_content_type() -> str:
    """按 CONTENT_TYPES 权重随机选一个内容类型 key（纯函数，便于测试）。"""
    pool = list(CONTENT_TYPES.items())
    weights = [v.get("weight", 1) for _, v in pool]
    return random.choices([k for k, _ in pool], weights=weights, k=1)[0]


def content_type_hint(content_type: str) -> str:
    """返回某内容类型的写作提示；未知类型回退到默认 mood 提示。"""
    c = CONTENT_TYPES.get(content_type) or CONTENT_TYPES.get("mood") or {}
    return c.get("hint", "") if c else ""


def humanize_image_prompt(personality: str = "", chat_style: str = "", content_type: str = "") -> str:
    """组装图文人味写作指令：HUMANIZE_PROMPT + 性格/说话风格 + 内容类型提示。"""
    p = HUMANIZE_PROMPT.replace("{personality}", (personality or "随和")[:60])
    p = p.replace("{chat_style}", (chat_style or "普通")[:60])
    hint = content_type_hint(content_type)
    if hint:
        p += f"\n\n内容类型提示：{hint}"
    return p


def humanize_reply_prompt(personality: str = "") -> str:
    """组装评论回复人味写作指令：REPLY_HUMANIZE + 性格。"""
    return REPLY_HUMANIZE.replace("{personality}", (personality or "随和")[:60])


def _de_ai(text: str) -> str:
    """去除 AI 腔：删模板句、去行首序号、去 markdown 标记、压缩空行。

    纯规则（零 LLM）。不破坏原有标点/emoji，可安全用于图文/回复正文。
    """
    if not text:
        return ""
    for phrase in _AI_PHRASES:
        text = text.replace(phrase, "")
    # 去掉行首的序号（1. 2. 3.）
    text = re.sub(r"^\d+[.、）)]\s*", "", text, flags=re.MULTILINE)
    # 去掉 markdown 加粗/斜体残留 / 段首星号列表
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)
    # 压缩多余空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
