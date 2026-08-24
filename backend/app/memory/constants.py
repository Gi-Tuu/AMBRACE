"""记忆领域常量（衰减/查重/摘要/去重）"""
# 艾宾浩斯遗忘曲线（2026-08-05）：保留率 R=exp(-Δt/S)，importance=R*120
DECAY_THRESHOLD_PCT = 20.0  # 保留率低于 20% 进入删除倒计时
DECAY_MAX_PCT = 120.0
DECAY_COUNTDOWN_DAYS = 3
S_DEFAULT = 7.0  # 记忆强度默认（天）
S_MIN_DAYS = 3.0
S_MAX_DAYS = 60.0  # 复习间隔递增上限（2026-08-08 调低：180 天衰减不可感知）
S_BY_TYPE = {"user_info": 14.0, "preference": 10.0, "insight": 7.0, "event": 3.0}  # 新记忆初始 S
REINFORCE_FACTOR_WRITE = 1.6  # 写入查重命中：S ×1.6（2026-08-08 调低防 S 膨胀）
REINFORCE_FACTOR_RETRIEVE = 1.3  # 检索命中：S ×1.3（2026-08-08 调低防 S 膨胀）
REINFORCE_DEBOUNCE_HOURS = 24  # 检索命中强化防抖（同一记忆 24h 内多次命中只强化 1 次）
MIGRATE_S_FROM_PCT = 90.0  # 存量迁移：S = clamp(importance/120*90, 3, 180)
# 主动到期复习（P1）
REVIEW_MIN_IMPORTANCE = 40.0  # 只复习重要性≥40 的记忆
REVIEW_MAX_PER_DAY = 3        # 每角色每天最多主动复习次数
REVIEW_RETRY_DAYS = 3         # 发出后未获回应，3 天后重试
REVIEW_SUCCESS_WINDOW_HOURS = 24  # 用户回复成功判定窗口
REVIEW_MIN_INTERVAL_MINUTES = 90  # 同角色两条复习消息最小间隔（与主动消息一致，防存量到期扎堆连发）
# AI 自主评星（P2）
AI_RATING_MAX_PER_CHAR = 10   # 每角色每天最多复评条数
AI_RATING_BATCH = 10          # 单次 LLM 调用批量评星条数
VECTOR_DEDUP_THRESHOLD = 0.86  # 2026-08-08 调低：语义更接近即视为重复，减少重复条目
SUMMARY_TTL_HOURS = 6
DEDUP_MIN_INTERVAL = 15 * 60  # 全量去重节流（O(n^2) 开销，保守 15 分钟；提前去重由写前查重/24h 合并承担）
_TYPE_CN = {
    "user_info": "印象",
    "preference": "偏好",
    "event": "近期事件",
    "insight": "洞察",
}

MERGE_MIN_SIMILARITY = 0.85
MERGE_MAX_CLUSTER_SIZE = 6
