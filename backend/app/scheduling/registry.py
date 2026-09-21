"""调度器任务基类：BaseTask（life_tick 等定时任务复用）"""
from abc import ABC, abstractmethod

from app.utils.timeutil import now_naive_utc


class BaseTask(ABC):
    def __init__(self, name, interval, active_hours=None):
        self.name = name
        self.interval = interval
        self.active_hours = active_hours
        self._last_run = 0.0

    def should_run(self, elapsed):
        if elapsed < self.interval:
            return False
        if self.active_hours:
            local_hour = (now_naive_utc().hour + 8) % 24
            start, end = self.active_hours
            if not (start <= local_hour < end):
                return False
        return True

    @abstractmethod
    async def execute(self):
        ...

    def mark_run(self, elapsed):
        self._last_run = elapsed
