"""本地感知层（零 LLM）：用户意图/情绪/话题粗分类，认知循环 v2.1 Perception 模块。

规则优先、零 LLM 调用，输出结构化感知结果：
{intent, emotion, emotion_label, topic, length_hint}
供 context_builder 注入与 planning/reflection 使用。
"""
import re

from app.utils.emotion import detect_user_emotion

# ── 意图五类 ──
INTENT_QUERY = "query"            # 信息查询
INTENT_EMOTION = "emotion"        # 情绪倾诉
INTENT_SMALLTALK = "smalltalk"    # 日常闲聊
INTENT_COMMAND = "command"        # 指令任务
INTENT_DEEP = "deep"              # 深层交流

_INTENT_CN = {
    INTENT_QUERY: "信息查询",
    INTENT_EMOTION: "情绪倾诉",
    INTENT_SMALLTALK: "日常闲聊",
    INTENT_COMMAND: "指令任务",
    INTENT_DEEP: "深层交流",
}

_INTENT_GUIDE = {
    INTENT_QUERY: "先直接回答，再视情况补充",
    INTENT_EMOTION: "优先共情陪伴，少讲道理",
    INTENT_SMALLTALK: "自然交流，别太正式",
    INTENT_COMMAND: "确认任务并给出明确回应",
    INTENT_DEEP: "认真接住，给足情绪价值与陪伴",
}

# 深层交流：存在主义/自我/人生意义（权重最高，优先级第一）
_DEEP_KEYWORDS = (
    "人生意义", "人生的意义", "生命的意义", "活着的意义", "活着有什么意义", "为什么活着", "为什么要活着",
    "活着好累", "活得太累", "不想活", "活不下去", "撑不下去",
    "没意思", "没意义", "没什么意思", "活着没意思", "空虚", "迷茫", "孤独", "自我价值",
    "找不到自己", "存在的价值", "我是谁", "想不开", "看不开", "很痛苦", "好痛苦",
    "太痛苦", "好绝望", "一无是处", "空壳", "格格不入",
)

# 指令任务：明确动词指令（强指令才命中，避免误伤倾诉）
_COMMAND_KEYWORDS = (
    "提醒我", "帮我查", "帮我找", "帮我写", "帮我算", "帮我设置", "帮我订",
    "查一下", "查查", "设置闹钟", "定个闹钟", "发给我", "保存到", "记录下来", "记录一下",
    "写一篇", "生成", "给我画", "给我下载", "帮我下载", "帮我安装", "翻译",
    "帮我整理", "给我推荐", "帮我看看", "帮我配", "帮我预约",
)

# 信息查询特征
_QUERY_HINTS = (
    "怎么", "如何", "为什么", "为何", "是什么", "什么是", "多少", "几点",
    "哪里", "哪家", "行不行", "可不可以", "能不能", "会不会", "教我",
    "教一下", "有什么", "哪些", "区别", "好不好", "要不要",
)
_QUERY_RE = re.compile(r"[?？]{1,}")

# 话题粗分类：关键词命中计数取最高分；无命中 → other
_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "work": ("工作", "上班", "加班", "老板", "同事", "项目", "会议", "会议纪要", "周报", "报告",
             "简历", "面试", "offer", "代码", "bug", "需求", "甲方", "离职", "打工", "创业",
             "领导", "备忘录", "定时任务", "文件", "电脑", "配置", "计算器", "软件", "安装", "ComfyUI"),
    "study": ("学习", "考试", "作业", "论文", "课程", "老师", "学校", "大学", "考研",
              "英语", "复习", "笔记", "图书馆", "上课", "成绩", "期末", "量子力学",
              "翻译", "短文", "文章", "单词"),
    "relationship": ("喜欢", "恋爱", "分手", "对象", "男朋友", "女朋友", "相亲", "表白",
                     "吵架", "老公", "老婆", "暧昧", "心动", "吃醋", "前任", "我妈",
                     "妈妈", "家人", "室友"),
    "life": ("吃饭", "睡觉", "睡醒", "买菜", "做饭", "天气", "感冒", "生病", "家务", "快递",
             "外卖", "好累", "累死", "起床", "下班了", "洗衣机", "好吃的", "电影", "地铁",
             "机场", "番茄", "衣服", "新衣服", "火锅", "食堂", "公园", "发型", "闹钟",
             "咖啡", "小说"),
    "pet": ("宠物", "猫", "狗", "仓鼠", "喂", "铲屎", "阿帕次", "团团", "遛", "猫粮"),
    "game": ("游戏", "打游戏", "王者", "原神", "上分", "排位", "副本", "手游", "开黑"),
    "health": ("健身", "跑步", "减肥", "锻炼", "体检", "医院", "吃药", "熬夜", "失眠",
               "运动", "体重", "肌肉", "药", "长胖"),
    "money": ("钱", "工资", "花呗", "信用卡", "攒钱", "理财", "股票", "基金", "房贷", "房租",
              "NFT", "钱包"),
}


_TOPIC_CN = {
    "work": "工作", "study": "学习", "relationship": "感情", "life": "生活",
    "pet": "宠物", "game": "游戏", "health": "健康", "money": "金钱", "other": "其他",
}


def _detect_intent(text: str, emotion_text: str) -> str:
    """意图判定优先级：深层交流 > 指令任务 > 情绪倾诉 > 信息查询 > 日常闲聊"""
    if any(kw in text for kw in _DEEP_KEYWORDS):
        return INTENT_DEEP
    if any(kw in text for kw in _COMMAND_KEYWORDS):
        return INTENT_COMMAND
    if emotion_text and "简短回应" not in emotion_text and any(
        k in emotion_text for k in ("低落", "长篇倾诉", "情绪激动")
    ):
        return INTENT_EMOTION
    if any(h in text for h in _QUERY_HINTS) or _QUERY_RE.search(text):
        return INTENT_QUERY
    return INTENT_SMALLTALK


def _emotion_label(emotion_text: str) -> str:
    """把情绪提示语映射为机器标签（供反思层情绪匹配用）"""
    if "低落" in emotion_text:
        return "sad"
    if "心情不错" in emotion_text:
        return "happy"
    if "情绪激动" in emotion_text:
        return "excited"
    if "困惑" in emotion_text:
        return "confused"
    if "长篇倾诉" in emotion_text:
        return "venting"
    if "简短回应" in emotion_text:
        return "short"
    return ""


def _detect_topic(text: str) -> str:
    scores: dict[str, int] = {}
    for topic, kws in _TOPIC_KEYWORDS.items():
        n = sum(1 for kw in kws if kw in text)
        if n:
            scores[topic] = n
    if not scores:
        return "other"
    return max(scores, key=scores.get)


def _length_hint(intent: str, emotion_label: str) -> str:
    if intent in (INTENT_DEEP, INTENT_EMOTION) or emotion_label in ("sad", "venting", "excited"):
        return "long"
    if intent == INTENT_SMALLTALK or emotion_label == "short":
        return "short"
    return "medium"


def perceive(user_message: str) -> dict:
    """本地感知：返回结构化 {intent, emotion, emotion_label, topic, length_hint}"""
    text = (user_message or "").strip()
    emotion_text = detect_user_emotion(text)
    intent = _detect_intent(text, emotion_text)
    label = _emotion_label(emotion_text)
    return {
        "intent": intent,
        "emotion": emotion_text,
        "emotion_label": label,
        "topic": _detect_topic(text),
        "length_hint": _length_hint(intent, label),
    }


def topic_cn(topic: str) -> str:
    """话题机器标签 -> 中文名"""
    return _TOPIC_CN.get(topic, topic)


def build_perception_section(perception: dict | None) -> str:
    """生成注入 SYSTEM_PROMPT 的感知段落（无感知时返回空串）"""
    if not perception:
        return ""
    intent = perception.get("intent") or INTENT_SMALLTALK
    parts = [f"用户意图：{_INTENT_CN.get(intent, '日常闲聊')}（{_INTENT_GUIDE.get(intent, '自然交流')}）"]
    topic = perception.get("topic")
    if topic and topic != "other":
        parts.append(f"话题方向：{_TOPIC_CN.get(topic, topic)}")
    hint = perception.get("length_hint")
    if hint:
        parts.append(f"建议篇幅：{'较长' if hint == 'long' else ('简短' if hint == 'short' else '适中')}")
    return "；".join(parts) + "。"