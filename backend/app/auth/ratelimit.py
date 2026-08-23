"""登录/注册失败限流（进程内存级，单实例适用）

规则：同一 key（IP+用户名）60 秒内失败 5 次 → 锁定 15 分钟；锁定期间直接拒绝，
不再执行 bcrypt 校验（防止撞库 CPU 消耗）。登录成功清除该 key 计数。
"""
import time
import threading

_MAX_FAILS = 5
_WINDOW_SEC = 60
_LOCK_SEC = 15 * 60

_lock = threading.Lock()
# key -> (fail_count, window_start)
_failures: dict[str, list[float]] = {}
# key -> locked_until_ts
_locked: dict[str, float] = {}


def _now() -> float:
    return time.time()


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
    now = _now()
    with _lock:
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
