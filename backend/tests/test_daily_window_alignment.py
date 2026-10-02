"""每日上限「当日已发」日界口径统一测试（派单 2026-09-29：arbiter → 应用时区）。

钉住四件事：
1. **默认 APP_TZ_OFFSET_HOURS=8 时与旧北京口径同值**（arbiter 的三处当日已发日界，
   含真实时钟下的等价）⇒ 改动不改变现网行为；
2. 偏移改成 +9 / -5 后，``arbiter.get_daily_sent_count`` / ``get_session_daily_sent_count``
   与 ``triggers.get_daily_count`` **同取应用日界**（#7 报出的分叉不再出现）；
3. 跨日边界：记录落在应用日界前 1 秒 / 恰在日界 / 后 1 秒 ⇒ 计数分明；
4. 空库、scalar 返回 None、脏入参不抛。

（项目未装 pytest-asyncio，统一 asyncio.run；全部打桩 DB，不触碰 backend/data）
"""
import asyncio
from datetime import datetime, timedelta, timezone

import app.config as cfg
import app.utils.timeutil as tu
from app.scheduling import arbiter, gates, triggers
from app.scheduling.executors import outreach as exec_outreach

_BJ = timezone(timedelta(hours=8))
_SENTINEL = datetime(2001, 7, 4, 9, 8, 7)


def _local(utc_naive: datetime, offset: int) -> datetime:
    return utc_naive.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=offset)))


def _legacy_day_start(utc_naive: datetime) -> datetime:
    """改动前 arbiter 用的北京口径日界（beijing_day_start_utc 的等价实现）。"""
    bj = _local(utc_naive, 8)
    return datetime(bj.year, bj.month, bj.day, tzinfo=_BJ).astimezone(
        timezone.utc
    ).replace(tzinfo=None)


def _app_day_start(utc_naive: datetime, offset: int) -> datetime:
    """应用口径日界的独立算式参照（与被测实现不同源，用于交叉验证）。"""
    loc = _local(utc_naive, offset)
    tz = timezone(timedelta(hours=offset))
    return datetime(loc.year, loc.month, loc.day, tzinfo=tz).astimezone(
        timezone.utc
    ).replace(tzinfo=None)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value

    def first(self):
        return None


class _FakeSession:
    """假会话：捕获查询实际绑定的「日界」；按「记录 >= 日界」模拟已发送计数。"""

    def __init__(self, sink, records):
        self._sink = sink
        self._records = records

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, *a, **k):
        start = next(
            (v for v in stmt.compile().params.values() if isinstance(v, datetime)), None
        )
        self._sink.append(start)
        return _FakeResult(
            sum(1 for r in self._records if start is not None and r >= start)
        )


def _pin(monkeypatch, utc_now: datetime, offset: int, records=()):
    """钉住「现在」= utc_now（UTC naive）、应用偏移 = offset；arbiter/triggers 共用假会话。"""
    local_now = _local(utc_now, offset)
    monkeypatch.setattr(cfg.settings, "app_tz_offset_hours", offset)
    monkeypatch.setattr(tu, "app_local_now", lambda: local_now)
    monkeypatch.setattr(triggers, "app_local_now", lambda: local_now)
    return _wire(monkeypatch, records)


def _wire(monkeypatch, records=()):
    """只打桩 DB（不碰时钟）：返回日界捕获列表。"""
    sink = []
    session = lambda: _FakeSession(sink, list(records))  # noqa: E731
    # A20 批 1b：get_daily_sent_count / get_session_daily_sent_count 已下沉到 gates，
    # 函数体在 gates 命名空间解析 async_session_factory；arbiter 侧只剩具名重导出，
    # 只打 arbiter 会被绕过（桩失效 ⇒ 真查库）。两边同打同一个桩对象。
    monkeypatch.setattr(arbiter, "async_session_factory", session)
    monkeypatch.setattr(gates, "async_session_factory", session)
    monkeypatch.setattr(triggers, "async_session_factory", session)
    return sink


def _all_three(monkeypatch, now, offset, records=()):
    """跑遍三处「当日已发」查询，返回 (日界捕获列表)。"""
    sink = _pin(monkeypatch, now, offset, records)
    assert asyncio.run(arbiter.get_daily_sent_count(1, "storyline")) == len(records)
    assert asyncio.run(arbiter.get_session_daily_sent_count(1, 7)) == len(records)
    assert asyncio.run(triggers.get_daily_count(1)) == len(records)
    return sink


# ---------------- ① 默认 +8：与旧北京口径同值（行为不变的钉） ----------------

def test_默认偏移8_arbiter当日日界与旧北京算式逐小时同值(monkeypatch):
    """UTC 一天 24 钟点：三处日界绑定的值必须与改动前的北京口径算式完全一致"""
    for hour in range(24):
        now = datetime(2026, 8, 12, hour, 15)
        legacy = _legacy_day_start(now)
        sink = _all_three(monkeypatch, now, 8, records=(now,))
        assert sink == [legacy, legacy, legacy], (now, sink)
        assert sink[0].microsecond == 0                    # 与旧 beijing_day_start_utc 逐字节一致


def test_默认偏移8_真实时钟下与北京日界同值(monkeypatch):
    """不打桩时钟：默认 +8 时 arbiter 取的日界 == beijing_day_start_utc()（现网行为不变）"""
    monkeypatch.setattr(cfg.settings, "app_tz_offset_hours", 8)
    # 2026-10-02 修：原用「现在 - 5 分钟」造记录，跑在北京 00:00–00:05 之间时那条会落到
    # 前一天日界之前 ⇒ 当日已发计数变 0，用例假红（10-01 23:57 起的全量跑正好跨零点被抓到）。
    # 改成「当日日界 + 1 分钟」，仍然锚在真实时钟上，但不依赖「现在是几点几分」。
    _day_start = tu.beijing_day_start_utc()
    sink = _wire(monkeypatch, records=(_day_start + timedelta(minutes=1),))
    assert asyncio.run(arbiter.get_daily_sent_count(1, "ai_care")) == 1
    assert abs(sink[0] - tu.beijing_day_start_utc()) < timedelta(seconds=5)


# ---------------- ② 非 +8：arbiter 与 triggers 不再分叉 ----------------

def test_偏移9_arbiter与triggers同取应用日界(monkeypatch):
    now = datetime(2026, 8, 11, 15, 30)          # 本地(+9) = 08-12 00:30，北京仍在 08-11 23:30
    app_start = _app_day_start(now, 9)           # 08-11 15:00 UTC
    assert app_start != _legacy_day_start(now)   # 与北京口径已分道
    sink = _all_three(monkeypatch, now, 9, records=(datetime(2026, 8, 11, 15, 20),))
    assert sink == [app_start] * 3, sink


def test_偏移负5_arbiter与triggers同取应用日界(monkeypatch):
    now = datetime(2026, 8, 12, 20, 0)           # 本地(-5) = 08-12 15:00，北京已跨到 08-13
    app_start = _app_day_start(now, -5)          # 08-12 05:00 UTC
    legacy = _legacy_day_start(now)              # 08-12 16:00 UTC
    assert app_start != legacy
    # 今天凌晨那条：旧北京口径查不到（同日重发），应用口径查得到 ⇒ 三处结果一致
    assert datetime(2026, 8, 12, 8, 0) < legacy
    sink = _all_three(monkeypatch, now, -5, records=(datetime(2026, 8, 12, 8, 0),))
    assert sink == [app_start] * 3, sink


# ---------------- ③ 跨日边界 ----------------

def test_跨应用日界前后计数分明(monkeypatch):
    """记录落在应用日界前 1 秒 / 恰在日界 / 后 1 秒：0 / 1 / 1（前 1 秒属昨天，不得占今日额度）"""
    now = datetime(2026, 8, 12, 15, 30)          # 本地(+9) = 08-13 00:30
    app_start = _app_day_start(now, 9)
    for delta, expect in ((timedelta(seconds=-1), 0), (timedelta(0), 1), (timedelta(seconds=1), 1)):
        sink = _pin(monkeypatch, now, 9, records=(app_start + delta,))
        got = [
            asyncio.run(arbiter.get_daily_sent_count(1, "greeting")),
            asyncio.run(arbiter.get_session_daily_sent_count(1, 7)),
            asyncio.run(triggers.get_daily_count(1)),
        ]
        assert got == [expect] * 3, (delta, got)
        assert sink == [app_start] * 3


def test_偏移9_昨日记录不再吃掉今日额度(monkeypatch):
    """本地(+9) 已跨天而北京仍在昨天：旧北京日界停在昨天 ⇒ 把昨天上午的记录算成「今天已发」，
    今日额度被昨日记录吃掉；新口径按应用日界正确清零。"""
    now = datetime(2026, 8, 12, 15, 30)          # 本地(+9) = 08-13 00:30，北京 = 08-12 23:30
    app_start = _app_day_start(now, 9)           # 08-12 15:00 UTC
    record = datetime(2026, 8, 12, 2, 0)         # 本地(+9) = 08-12 11:00（昨天上午）
    assert record >= _legacy_day_start(now), "旧口径把它算进今天 ⇒ 额度被昨日记录吃掉"
    assert record < app_start
    sink = _pin(monkeypatch, now, 9, records=(record,))
    assert asyncio.run(arbiter.get_daily_sent_count(1, "storyline")) == 0
    assert asyncio.run(arbiter.get_session_daily_sent_count(1, 7)) == 0
    assert asyncio.run(triggers.get_daily_count(1)) == 0
    assert sink == [app_start] * 3


# ---------------- ④ 空库 / None / 脏输入不抛 ----------------

def test_空库返回0不抛(monkeypatch):
    sink = _pin(monkeypatch, datetime(2026, 8, 12, 3, 0), 8, records=())
    assert asyncio.run(arbiter.get_daily_sent_count(1, "storyline")) == 0
    assert asyncio.run(arbiter.get_session_daily_sent_count(1, 7)) == 0
    assert asyncio.run(triggers.get_daily_count(1)) == 0
    assert len(sink) == 3 and all(s is not None for s in sink)


def test_scalar返回None按0处理(monkeypatch):
    """库空时 scalar() 可能给 None：`or 0` 兜住，不得抛 TypeError"""
    sink = _wire(monkeypatch, records=(datetime(2026, 8, 12, 3, 0),))
    monkeypatch.setattr(_FakeResult, "scalar", lambda self: None)
    assert asyncio.run(arbiter.get_daily_sent_count(1, "storyline")) == 0
    assert asyncio.run(arbiter.get_session_daily_sent_count(1, 7)) == 0
    assert len(sink) == 2


def test_脏入参不抛且仍绑定日界(monkeypatch):
    """角色 ID 缺失 / 类型为空串 / 非法类型：查询照常构造，日界仍是应用零点（不静默放大窗口）"""
    now = datetime(2026, 8, 12, 3, 0)
    app_start = _app_day_start(now, 8)
    for bad_id, bad_type in ((None, ""), (0, None), (-1, "storyline"), (1, 12345), (1, "x" * 5000)):
        sink = _pin(monkeypatch, now, 8, records=(now,))
        assert asyncio.run(arbiter.get_daily_sent_count(bad_id, bad_type)) == 1
        assert asyncio.run(arbiter.get_session_daily_sent_count(bad_id, None)) == 1
        assert sink == [app_start, app_start], (bad_id, bad_type, sink)


# ---------------- ⑤ 口径同源（不复制算式、不再混用北京日界） ----------------

def test_三处当日已发只从app_day_start_utc取(monkeypatch):
    """把 arbiter/triggers 的 app_day_start_utc 换成哨兵：绑定值必须原样等于哨兵 ⇒ 同源、未手搓偏移"""
    # A20 批 1b：两处「当日已发」函数体已搬到 gates，在 gates 命名空间解析
    # app_day_start_utc；arbiter 侧只是重导出，只打 arbiter 桩会被绕过。两边同打。
    monkeypatch.setattr(arbiter, "app_day_start_utc", lambda: _SENTINEL)
    monkeypatch.setattr(gates, "app_day_start_utc", lambda: _SENTINEL)
    monkeypatch.setattr(triggers, "app_day_start_utc", lambda: _SENTINEL)
    sink = _wire(monkeypatch, records=(datetime(2026, 8, 12, 0, 30),))
    assert asyncio.run(arbiter.get_daily_sent_count(1, "storyline")) == 1
    assert asyncio.run(arbiter.get_session_daily_sent_count(1, 7)) == 1
    assert asyncio.run(triggers.get_daily_count(1)) == 1
    assert sink == [_SENTINEL, _SENTINEL, _SENTINEL]


def test_arbiter源码_当日已发用应用日界_北京日界只剩反思回溯():
    src = open(arbiter.__file__, encoding="utf-8").read()
    # A20 批 1b：②③ 两处「当日已发」函数体已搬到 gates（arbiter 侧只剩具名重导出，
    # 源码里不再出现这两行），所以锚定改读 gates 源码。
    # A20 批 4b：想念每日配额那行随 outreach 分支搬到 executors/outreach，改经 GateBundle 现取
    # （g.app_day_start()）⇒ 锚点改读该模块源码；arbiter 侧仍留裸名 app_day_start=app_day_start_utc，
    # 所以 setattr(arbiter, "app_day_start_utc", 哨兵) 这条桩依旧穿透（上方 ⑤ 已验）。
    gsrc = open(gates.__file__, encoding="utf-8").read()
    osrc = open(exec_outreach.__file__, encoding="utf-8").read()
    assert gsrc.count("since = app_day_start_utc()") == 2      # ② 类型配比 / ③ 单会话限频
    assert "g.app_day_start()) >= MOTIVATION_MAX_PER_DAY" in osrc   # 想念每日配额
    assert "app_day_start=app_day_start_utc," in src               # arbiter 现取注入（桩仍在 arbiter）
    bj_lines = [ln for ln in src.splitlines() if "beijing_day_start_utc" in ln]
    assert len(bj_lines) == 2, bj_lines                        # 仅剩局部 import + 回溯查询
    assert all(("import" in ln) or ("Memory.created_at" in ln) for ln in bj_lines), bj_lines
