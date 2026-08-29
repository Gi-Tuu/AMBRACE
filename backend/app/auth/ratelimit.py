"""登录/注册失败限流（进程内存级，单实例适用）

规则：同一 key（IP+用户名）60 秒内失败 5 次 → 锁定 15 分钟；锁定期间直接拒绝，
不再执行 bcrypt 校验（防止撞库 CPU 消耗）。登录成功清除该 key 计数。
"""
import time as _time
import threading

_MAX_FAILS = 5
_WINDOW_SEC = 60
_LOCK_SEC = 15 * 60

_lock = threading.Lock()
# key -> (fail_count, window_start)
_failures: dict[str, list[float]] = {}
# key -> locked_until_ts
_locked: dict[str, float] = {}

# 过期键轻量清理：避免大量不同 key 探测时 dict 缓慢无界增长（自托管低风险加固）
_LAST_PRUNE = 0.0
_PRUNE_INTERVAL = 10 * 60  # 最多 10 分钟扫一次


def _prune_locked(now: float) -> None:
    """清理已过锁定期但不再被访问的 _locked 键（_failures 键在窗口期外自然过期）。"""
    stale = [k for k, until in _locked.items() if until <= now]
    for k in stale:
        _locked.pop(k, None)
    stale_f = [k for k, stamps in _failures.items()
               if not stamps or now - max(stamps) > _WINDOW_SEC]
    for k in stale_f:
        _failures.pop(k, None)


def _now() -> float:
    return _time.time()


def is_locked(key: str) -> bool:
    with _lock:
        until = _locked.get(key)
        if until is None:
            return False
        if until <= _now():
            _locked.pop(key, None)
            return False
        return True


def remaining_lock_seconds(key: str) -> int:
    with _lock:
        until = _locked.get(key)
        if until and until > _now():
            return int(until - _now())
        return 0


def record_failure(key: str) -> bool:
    """记录一次失败；返回 True 表示本次已达锁定阈值（需锁定）。"""
    global _LAST_PRUNE
    now = _now()
    with _lock:
        if now - _LAST_PRUNE > _PRUNE_INTERVAL:
            _LAST_PRUNE = now
            _prune_locked(now)
        stamps = [s for s in _failures.get(key, []) if now - s < _WINDOW_SEC]
        stamps.append(now)
        _failures[key] = stamps
        if len(stamps) >= _MAX_FAILS:
            _locked[key] = now + _LOCK_SEC
            _failures.pop(key, None)
            return True
        return False


def record_success(key: str) -> None:
    with _lock:
        _failures.pop(key, None)
        _locked.pop(key, None)
