"""proactivity 睡眠静默策略常量（F2-b）：21 点后用户说过睡觉 → 当晚主动交流关闭。

判定函数 has_user_said_sleep（含 DB 查询）留在 arbiter；本模块只放策略常量。
"""
# 北京时间 21 点后，用户说过"睡觉"则当天主动交流提前关闭
SLEEP_HOUR = 21
# 仅明确"要去睡/已睡"意图才触发当晚静默（去掉"困了/休息了/困死"等易误伤的非入睡表达）
SLEEP_KEYWORDS = ("睡觉", "睡了", "晚安", "要睡了", "先睡了", "去睡了", "睡啦", "睡觉了", "睡了哦", "睡吧", "去睡觉", "我先睡", "睡觉去", "睡了哈")

# 会推送到用户端的主动消息类型：21 点后用户说过睡觉 → 当晚这些类型不再发
# 后台类型 timer / ai_social / group_active / pet_visit 故意不在内
# （timer 是定时承诺，睡了也必须兑现；其余三类不向用户露面）
SLEEP_SILENCED_TYPES = frozenset({
    "birthday", "anniversary", "holiday", "greeting", "proactive_chat",
    "goodnight", "status_update", "state_trigger", "memory_review",
    "memory_review_contextual", "emotion_care", "pet_remind", "ai_care",
    "ai_adopt", "plugin", "motivation", "prospective_intent",
    "life_regression", "unfinished_topic",
})
