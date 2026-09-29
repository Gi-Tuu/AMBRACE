"""认识纪念日（anniversary）时区口径测试（P3-2）。

钉住两件事：
1. **默认 APP_TZ_OFFSET_HOURS=8 时行为与旧「北京 +8」口径逐字节相同**（首日/今天/防重复日界三处）；
2. 应用时区改成 +9 / -5 后，首日、今天、防重复日界同取应用时区 ⇒ 口径自洽，
   旧「首日北京 + 今天应用时区」导致的天数差 1（提前/漏发一天）不再出现。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；全部打桩 DB，不触碰 backend/data）
"""
import asyncio
from datetime import datetime, timedelta, timezone

import app.config as cfg
import app.utils.timeutil as tu
from app.scheduling import triggers

_BJ = timezone(timedelta(hours=8))


def _legacy_days(created_at: datetime, local_today) -> int:
    """旧口径（P3-2 修复前）：首日固定北京 +8，今天取应用时区日期。"""
    first_bj = (created_at + timedelta(hours=8)).date()
    return (local_today - first_bj).days + 1


def _legacy_day_start(local_now: datetime) -> datetime:
    """旧口径的防重复日界：北京当天 00:00 对应的 UTC naive。"""
    bj = local_now.astimezone(_BJ)
    return datetime(bj.year, bj.month, bj.day, tzinfo=_BJ).astimezone(timezone.utc).replace(tzinfo=None)


class _FakeResult:
    def __init__(self, row=None):
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    """假会话：记录防重复查询实际绑定的「日界」参数，默认查不到已发记录"""

    def __init__(self, captured, dup=False):
        self._captured = captured
        self._row = datetime(2026, 1, 1) if dup else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, *a, **k):
        params = stmt.compile().params
        self._captured.append(next((v for v in params.values() if isinstance(v, datetime)), None))
        return _FakeResult(self._row)


class _RecordingLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *a):
        self.warnings.append(msg % a if a else str(msg))


def _setup(monkeypatch, created_at, local_now, offset, dup=False):
    """钉住「今天」= local_now、应用时区偏移 = offset、最早会话 = created_at；返回 (日界捕获, 日志)"""
    monkeypatch.setattr(cfg.settings, "app_tz_offset_hours", offset)
    monkeypatch.setattr(tu, "app_local_now", lambda: local_now)
    monkeypatch.setattr(triggers, "app_local_now", lambda: local_now)

    async def _chars():
        return [{"character_id": 1, "user_id": 2, "nickname": "u", "birthday": "01-01"}]

    async def _first(character_id, user_id):
        return {"id": 7, "created_at": created_at}

    monkeypatch.setattr(triggers, "get_active_characters", _chars)
    monkeypatch.setattr(triggers, "get_first_session", _first)
    captured = []
    monkeypatch.setattr(triggers, "async_session_factory", lambda: _FakeSession(captured, dup=dup))
    log = _RecordingLogger()
    monkeypatch.setattr(triggers, "_logger", log)
    return captured, log


def _run():
    return asyncio.run(triggers.get_anniversary_candidates())


# ---------------- 硬要求：默认 +8 下新旧结果相同 ----------------

def test_anniversary_默认偏移8_天数与旧北京口径逐小时相同(monkeypatch):
    """UTC 一天 24 个钟点各造一例：默认 +8 时 anniversary_days 与旧算式完全一致"""
    now = datetime(2026, 5, 20, 12, 0, tzinfo=_BJ)  # 应用本地今天 = 2026-05-20
    fired = 0
    for hour in range(24):
        created = datetime(2026, 5, 13, hour, 30)
        captured, log = _setup(monkeypatch, created, now, 8)
        expect = _legacy_days(created, now.date())
        got = _run()
        assert not log.warnings, log.warnings
        if expect in triggers._ANNIVERSARY_MILESTONES:
            fired += 1
            assert len(got) == 1 and got[0]["anniversary_days"] == expect
        else:
            assert got == []
    assert fired > 0, "网格应至少命中一个里程碑（否则用例形同空跑）"


def test_anniversary_默认偏移8_防重复日界与北京日界相同(monkeypatch):
    created = datetime(2026, 5, 13, 17, 0)  # 北京 +8 ⇒ 首日 2026-05-14，到 05-20 为第 7 天
    now = datetime(2026, 5, 20, 12, 0, tzinfo=_BJ)
    captured, log = _setup(monkeypatch, created, now, 8)
    got = _run()
    assert not log.warnings, log.warnings
    assert len(got) == 1 and got[0]["anniversary_days"] == 7
    assert captured == [_legacy_day_start(now)]  # 与旧 _beijing_day_start_utc 同值
    assert captured[0] == datetime(2026, 5, 19, 16, 0)


# ---------------- 非 +8：口径自洽、旧差 1 bug 不再出现 ----------------

def test_anniversary_偏移9_第7天触发_旧口径会漏发一天(monkeypatch):
    """应用本地 01-02 凌晨：首日按 +9 是 12-27 ⇒ 第 7 天；旧口径按北京算成第 8 天而漏发"""
    created = datetime(2025, 12, 26, 15, 30)
    now = datetime(2026, 1, 1, 15, 30, tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=9)))  # 本地 01-02 00:30
    captured, log = _setup(monkeypatch, created, now, 9)
    got = _run()
    assert not log.warnings, log.warnings
    assert _legacy_days(created, now.date()) == 8  # 旧口径落在里程碑之外
    assert len(got) == 1 and got[0]["anniversary_days"] == 7
    assert captured[0] == datetime(2026, 1, 1, 15, 0)  # 本地 01-02 00:00+09


def test_anniversary_偏移9_旧口径会提前一天_现不再提前(monkeypatch):
    """旧口径在这天算成第 7 天（提前触发）；统一口径后是第 6 天，不产出候选"""
    created = datetime(2026, 1, 1, 15, 30)  # 本地(+9) 首日 = 01-02
    now = datetime(2026, 1, 7, 10, 0, tzinfo=timezone(timedelta(hours=9)))  # 本地今天 01-07
    captured, log = _setup(monkeypatch, created, now, 9)
    got = _run()
    assert not log.warnings, log.warnings
    assert _legacy_days(created, now.date()) == 7  # 旧口径会提前发
    assert got == []
    assert captured == []  # 未到里程碑，不该发起防重复查询


def test_anniversary_偏移负5_第7天按应用日界触发(monkeypatch):
    """纽约口径（-5）：首日 UTC 01-02 04:00 → 本地 01-01 ⇒ 本地 01-07 为第 7 天；旧口径算成第 6 天"""
    created = datetime(2026, 1, 2, 4, 0)
    now = datetime(2026, 1, 7, 22, 0, tzinfo=timezone(timedelta(hours=-5)))  # 本地 01-07 17:00
    captured, log = _setup(monkeypatch, created, now, -5)
    got = _run()
    assert not log.warnings, log.warnings
    assert _legacy_days(created, now.date()) == 6
    assert len(got) == 1 and got[0]["anniversary_days"] == 7
    assert captured[0] == datetime(2026, 1, 7, 5, 0)  # 本地 01-07 00:00-05


def test_anniversary_偏移负5_旧口径会提前一天_现不再提前(monkeypatch):
    created = datetime(2026, 1, 1, 4, 0)  # 本地(-5) 首日 = 2025-12-31
    now = datetime(2026, 1, 7, 22, 0, tzinfo=timezone(timedelta(hours=-5)))
    captured, log = _setup(monkeypatch, created, now, -5)
    got = _run()
    assert not log.warnings, log.warnings
    assert _legacy_days(created, now.date()) == 7  # 旧口径会提前发
    assert got == []


def test_anniversary_今天已发过则不重复候选(monkeypatch):
    """防重复查询命中记录时不产出候选（日界口径改动后该闸仍生效）"""
    created = datetime(2026, 5, 13, 17, 0)
    now = datetime(2026, 5, 20, 12, 0, tzinfo=_BJ)
    captured, log = _setup(monkeypatch, created, now, 8, dup=True)
    got = _run()
    assert not log.warnings, log.warnings
    assert got == []
    assert captured == [_legacy_day_start(now)]
