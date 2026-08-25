"""随机节律引擎 — 按时间窗概率采样，产生"AI 自己想做的事" """
import random
from datetime import datetime, timezone

from app.utils.logger import get_logger

_logger = get_logger("scheduler.life_rhythm")

# ── 时间窗定义（分钟制）──
# name: 窗口名, start/end: 分钟数（0:00 起算）, tendencies: 行为倾向（概率偏重，非锁定）
TIME_WINDOWS = [
    {"name": "清晨", "start": 7 * 60,      "end": 9 * 60,       "tendencies": ["greeting", "moment_comment"]},
    {"name": "上午", "start": 9 * 60,      "end": 11 * 60 + 30, "tendencies": ["moment_comment", "moment_publish"]},
    {"name": "午间", "start": 11 * 60 + 30, "end": 13 * 60 + 30, "tendencies": ["status_update", "moment_publish"]},
    {"name": "下午", "start": 13 * 60 + 30, "end": 17 * 60,     "tendencies": ["moment_comment", "proactive_chat"]},
    {"name": "傍晚", "start": 17 * 60,     "end": 19 * 60,      "tendencies": ["status_update", "moment_publish"]},
    {"name": "晚间", "start": 19 * 60,     "end": 22 * 60,      "tendencies": ["proactive_chat", "moment_comment"]},
    {"name": "深夜", "start": 22 * 60,     "end": 24 * 60,      "tendencies": ["goodnight", "moment_comment"]},
]

# 触发概率：high / medium / low
FREQ_PROBABILITY = {"high": 0.7, "medium": 0.5, "low": 0.3}


def get_time_window(now: datetime | None = None) -> dict | None:
    """返回当前时间所在的时间窗（无则 None，如凌晨 0-7 点不活跃）"""
    if now is None:
        now = datetime.now(timezone.utc)
    cn_hour = (now.hour + 8) % 24  # 北京时间
    minutes = cn_hour * 60 + now.minute
    for w in TIME_WINDOWS:
        if w["start"] <= minutes < w["end"]:
            return w
    return None


def get_trigger_probability(frequency: str) -> float:
    """根据角色频率配置返回触发概率"""
    return FREQ_PROBABILITY.get(frequency, 0.5)


def sample_should_trigger(frequency: str, window: dict) -> bool:
    """概率采样：该时间窗是否触发一次行为"""
    prob = get_trigger_probability(frequency)
    return random.random() < prob


def pick_behavior(window: dict, override: str | None = None) -> str:
    """从时间窗倾向中选一个行为；有上下文覆盖（如待回复评论）时优先覆盖"""
    if override:
        return override
    # 倾向列表第一个为最高倾向，加权随机：70% 选第一个，30% 选第二个
    if len(window["tendencies"]) >= 2 and random.random() < 0.3:
        return window["tendencies"][1]
    return window["tendencies"][0]
