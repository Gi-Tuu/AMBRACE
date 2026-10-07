"""生日/节日/每日上限触发器的「过一天」日界口径测试（P3-2 同源尾活，2026-09-29）。

钉住两件事：
1. **默认 APP_TZ_OFFSET_HOURS=8 时行为与旧「北京 +8」口径逐字节相同**
   （生日防重复、节日防重复、每日上限统计三处日界）；
2. 应用时区改成 +9 / -5 后，「今天」与防重复日界同取应用时区 ⇒ 旧「今天走应用时区、
   日界走北京」混用的两类错不再出现：**应用已跨天而北京未跨天 ⇒ 窗口虚宽（昨日记录压制今天，
   整日漏发）**；**北京已跨天而应用未跨天 ⇒ 今日记录查不到（同日重发）**。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；全部打桩 DB，不触碰 backend/data）
"""
import asyncio
from datetime import datetime, timedelta, timezone

import app.config as cfg
import app.utils.timeutil as tu
from app.scheduling import triggers

_BJ = timezone(timedelta(hours=8))
_SENTINEL = datetime(2001, 7, 4, 9, 8, 7)


def _local(utc_naive: datetime, offset: int) -> datetime:
    return utc_naive.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=offset)))


def _legacy_day_start(utc_naive: datetime) -> datetime:
    """旧口径日界（修复前 _beijing_day_start_utc 的等价实现）：北京当天 00:00 对应 UTC naive。"""
    bj = _local(utc_naive, 8)
    return datetime(bj.year, bj.month, bj.day, tzinfo=_BJ).astimezone(
        timezone.utc
    ).replace(tzinfo=None)


class _FakeScalars:
    """A37 批 1 之后谓词走 `.scalars().all()`（要按行内容分「真发过／失败留痕」）。

    本夹具考的是**日界**，不是失败前缀 ⇒ 给的那一行始终算"真发出去了"。
    """

    def __init__(self, hit: bool):
        self._hit = hit

    def all(self):
        from types import SimpleNamespace
        return [SimpleNamespace(id=1, content="生日快乐")] if self._hit else []


class _FakeResult:
    def __init__(self, hit: bool):
        self._hit = hit

    def first(self):
        return datetime(2026, 1, 1) if self._hit else None

    def scalar(self):
        return 1 if self._hit else 0

    def scalars(self):
        return _FakeScalars(self._hit)


class _FakeSession:
    """假会话：捕获查询实际绑定的「日界」；已发记录落在日界之后才算命中。"""

    def __init__(self, captured, record_at):
        self._captured = captured
        self._record_at = record_at

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, *a, **k):
        start = next(
            (v for v in stmt.compile().params.values() if isinstance(v, datetime)), None
        )
        self._captured.append(start)
        hit = self._record_at is not None and start is not None and self._record_at >= start
        return _FakeResult(hit)


class _RecordingLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *a):
        self.warnings.append(msg % a if a else str(msg))


def _pin(monkeypatch, utc_now, offset, *, record_at=None, birthday=None):
    """钉住「现在」= utc_now（UTC naive）、应用时区偏移 = offset；返回 (日界捕获, 假日志)。

    默认让角色生日 = 应用本地今天的 MM-DD，使候选路径只由「日界口径」决定。
    """
    local_now = _local(utc_now, offset)
    monkeypatch.setattr(cfg.settings, "app_tz_offset_hours", offset)
    monkeypatch.setattr(tu, "app_local_now", lambda: local_now)
    monkeypatch.setattr(triggers, "app_local_now", lambda: local_now)

    async def _chars():
        return [{
            "character_id": 1,
            "user_id": 2,
            "nickname": "u",
            "birthday": birthday or local_now.strftime("%m-%d"),
            "birthday_enabled": True,
            "holiday_enabled": True,
            "max_daily_proactive": 5,
        }]

    async def _latest(character_id, user_id):
        return {"id": 7, "updated_at": utc_now}

    captured = []
    monkeypatch.setattr(triggers, "async_session_factory", lambda: _FakeSession(captured, record_at))
    monkeypatch.setattr(triggers, "get_active_characters", _chars)
    monkeypatch.setattr(triggers, "get_latest_session", _latest)
    log = _RecordingLogger()
    monkeypatch.setattr(triggers, "_logger", log)
    return captured, log


# ---------------- 硬要求：默认 +8 下新旧结果逐字节相同 ----------------

def test_默认偏移8_生日候选与旧北京口径逐小时相同(monkeypatch):
    """UTC 一天 24 钟点 × 记录相对旧日界 7 个偏移：默认 +8 时新口径与旧算式结果完全一致"""
    fired = suppressed = 0
    for hour in range(24):
        utc_now = datetime(2026, 8, 12, hour, 15)
        legacy = _legacy_day_start(utc_now)
        for delta_min in (-720, -60, -1, 0, 1, 60, 720):
            record = legacy + timedelta(minutes=delta_min)
            captured, log = _pin(monkeypatch, utc_now, 8, record_at=record)
            got = asyncio.run(triggers.get_birthday_candidates())
            assert not log.warnings, log.warnings
            assert captured == [legacy], (utc_now, delta_min)  # 日界与旧算式同值
            expect_fire = record < legacy                      # 旧口径的压制判据
            assert (len(got) == 1) is expect_fire, (utc_now, delta_min)
            fired += expect_fire
            suppressed += not expect_fire
    assert fired > 0 and suppressed > 0, "网格须同时覆盖「发」与「压制」两类（否则形同空跑）"


def test_默认偏移8_节日防重复与每日上限日界与旧口径相同(monkeypatch):
    for hour in range(24):
        utc_now = datetime(2026, 3, 9, hour, 45)
        legacy = _legacy_day_start(utc_now)
        captured, log = _pin(monkeypatch, utc_now, 8)
        assert asyncio.run(triggers.was_holiday_sent_today(1)) is False
        assert asyncio.run(triggers.get_daily_count(1)) == 0
        assert not log.warnings, log.warnings
        assert captured == [legacy, legacy]                    # 两处日界同旧值
        assert captured[0].microsecond == 0                    # 与旧 beijing_day_start_utc 逐字节一致


# ---------------- 非 +8：日界随应用时区，旧混用的两类错不再出现 ----------------

def test_偏移9_昨日记录不再压制今天漏发(monkeypatch):
    """本地(+9) 刚跨天（00:30）：记录属于本地昨天上午。旧北京日界此刻还停在昨天 ⇒ 窗口宽 23h，
    把昨天上午的记录当「今天已发」→ 生日祝福整日漏发；新口径按应用日界正确放行。"""
    utc_now = datetime(2026, 8, 12, 15, 30)      # 本地(+9) = 08-13 00:30，北京 = 08-12 23:30
    record = datetime(2026, 8, 12, 2, 0)         # 本地(+9) = 08-12 11:00（昨天上午）
    captured, log = _pin(monkeypatch, utc_now, 9, record_at=record, birthday="08-13")
    assert _legacy_day_start(utc_now) == datetime(2026, 8, 11, 16, 0)   # 北京 08-12 00:00
    assert record >= _legacy_day_start(utc_now), "旧口径误判为今天 ⇒ 整日漏发"

    got = asyncio.run(triggers.get_birthday_candidates())
    assert not log.warnings, log.warnings
    assert captured == [datetime(2026, 8, 12, 15, 0)]          # 本地 08-13 00:00+09
    assert len(got) == 1 and got[0]["birthday"] == "08-13"


def test_偏移负5_今天凌晨已发不再同日重发(monkeypatch):
    """北京已跨天而本地(-5) 仍在今天：旧北京日界晚于应用日界 11 小时 → 今天凌晨那条查不到 ⇒ 同日重发。"""
    utc_now = datetime(2026, 8, 12, 20, 0)       # 本地(-5) = 08-12 15:00，北京 = 08-13 04:00
    record = datetime(2026, 8, 12, 8, 0)         # 本地(-5) = 08-12 03:00（今天凌晨已送出）
    captured, log = _pin(monkeypatch, utc_now, -5, record_at=record, birthday="08-12")
    assert _legacy_day_start(utc_now) == datetime(2026, 8, 12, 16, 0)   # 北京 08-13 00:00
    assert record < _legacy_day_start(utc_now), "旧口径查不到 ⇒ 同日重发"

    got = asyncio.run(triggers.get_birthday_candidates())
    assert not log.warnings, log.warnings
    assert captured == [datetime(2026, 8, 12, 5, 0)]           # 本地 08-12 00:00-05
    assert got == []                                            # 新口径正确压制


def test_偏移负5_昨晚记录不再压制今天整日漏发(monkeypatch):
    """记录发于本地(-5) 昨天 15:00：旧北京日界把它算进「今天」→ 漏发一整天；新口径不再压制"""
    utc_now = datetime(2026, 8, 12, 13, 0)       # 本地(-5) = 08-12 08:00
    record = datetime(2026, 8, 11, 20, 0)        # 本地(-5) = 08-11 15:00（昨天）
    captured, log = _pin(monkeypatch, utc_now, -5, record_at=record)
    assert _legacy_day_start(utc_now) == datetime(2026, 8, 11, 16, 0)
    assert record >= _legacy_day_start(utc_now), "旧口径误判为今天 ⇒ 整日漏发"

    got = asyncio.run(triggers.get_birthday_candidates())
    assert not log.warnings, log.warnings
    assert captured == [datetime(2026, 8, 12, 5, 0)]           # 本地 08-12 00:00-05
    assert len(got) == 1 and got[0]["birthday"] == "08-12"


def test_偏移9_节日防重复与每日上限同取应用日界(monkeypatch):
    utc_now = datetime(2026, 8, 11, 15, 30)      # 本地(+9) = 08-12 00:30
    captured, log = _pin(monkeypatch, utc_now, 9, record_at=datetime(2026, 8, 11, 15, 20))
    assert asyncio.run(triggers.was_holiday_sent_today(1)) is True
    assert asyncio.run(triggers.get_daily_count(1)) == 1
    assert not log.warnings, log.warnings
    app_start = datetime(2026, 8, 11, 15, 0)
    assert captured == [app_start, app_start]
    assert app_start != _legacy_day_start(utc_now)             # 非 +8 时与北京日界分道


def test_偏移9_今天口径取应用本地日期(monkeypatch):
    """本地(+9) 已跨天而北京仍在昨天：生日按「应用本地今天」命中，北京今天不命中"""
    utc_now = datetime(2026, 8, 11, 15, 30)      # 本地 08-12 / 北京 08-11
    captured, log = _pin(monkeypatch, utc_now, 9, birthday="08-11")
    assert asyncio.run(triggers.get_birthday_candidates()) == []
    assert not log.warnings, log.warnings
    assert captured == []                                       # 未到生日，不该发起防重复查询

    captured2, log2 = _pin(monkeypatch, utc_now, 9, birthday="08-12")
    got = asyncio.run(triggers.get_birthday_candidates())
    assert not log2.warnings, log2.warnings
    assert len(got) == 1
    assert captured2 == [datetime(2026, 8, 11, 15, 0)]


def test_三处日界只从app_day_start_utc取_不复制算式(monkeypatch):
    """把 app_day_start_utc 换成哨兵：三处查询绑定的日界必须原样等于哨兵 ⇒ 口径同源、未手搓偏移"""
    monkeypatch.setattr(triggers, "app_day_start_utc", lambda: _SENTINEL)
    captured, log = _pin(monkeypatch, datetime(2026, 8, 12, 3, 0), 9,
                         record_at=datetime(2026, 8, 12, 0, 30))
    assert asyncio.run(triggers.get_birthday_candidates()) == []  # 哨兵极早 ⇒ 任何记录都算今天已发
    assert asyncio.run(triggers.was_holiday_sent_today(1)) is True
    assert asyncio.run(triggers.get_daily_count(1)) == 1
    assert not log.warnings, log.warnings
    assert captured == [_SENTINEL, _SENTINEL, _SENTINEL]


def test_模块不再引用北京日界():
    assert not hasattr(triggers, "_beijing_day_start_utc")
    src = open(triggers.__file__, encoding="utf-8").read()
    assert "beijing_day_start" not in src
    assert "timedelta(hours=8)" not in src
    assert src.count("start = app_day_start_utc()") == 3  # 每日上限／节庆（生日＋节日共用一次查询）／纪念日，三处同源
    # A37 批 1：原来生日与节日各自取一次日界（两次 SQL），现合并成 `festival_today_state` 一次查完 ⇒ 4 → 3
