# -*- coding: utf-8 -*-
"""跨天补跑回归（2026-09-28）：每日任务错过一天后，任何时刻都能补跑。

起因：09-27 主循环因 LLM 长调用被 supervisor 判 stalled 并取消重建，23:00 的日记窗口整段不可用；
旧判据要求「在本地 [min_hour,24) 窗口内」才跑，于是要再等近 24 小时才补，期间该天内容一直缺。
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.scheduling import periodic_state as ps


@pytest.fixture()
def _state(tmp_path, monkeypatch):
    f = tmp_path / "periodic_state.json"
    monkeypatch.setattr(ps, "_STATE_FILE", f)
    return f


def _write(path, key, last_success):
    path.write_text(json.dumps({key: {"last_success": last_success}}), encoding="utf-8")


def test_窗口外_未错过一天_不跑(_state):
    # 上次成功＝今天，窗口外（本地 10 点 < 23）⇒ 不跑
    now = datetime(2026, 9, 27, 2, 0)  # UTC 02:00 = 北京 10:00
    _write(_state, "diary", "2026-09-27 02:00:00")
    assert ps.is_daily_due("diary", 23, now=now) is False


def test_错过整整一天_窗口外也补跑(_state):
    # 上次成功＝前天（本地 09-25），今天本地 09-27 上午 10 点 ⇒ 补跑
    now = datetime(2026, 9, 27, 2, 0)  # 北京 09-27 10:00
    _write(_state, "diary", "2026-09-25 15:00:18")
    assert ps.is_daily_due("diary", 23, now=now) is True


def test_窗口内_昨天成功过_仍然跑(_state):
    # 上次成功＝昨天，今天 23:00 窗口内 ⇒ 照常跑（原行为不变）
    now = datetime(2026, 9, 27, 15, 0)  # 北京 09-27 23:00
    _write(_state, "diary", "2026-09-26 15:00:18")
    assert ps.is_daily_due("diary", 23, now=now) is True


def test_今天已成功_不再跑(_state):
    now = datetime(2026, 9, 27, 16, 0)  # 北京 09-28 00:00
    _write(_state, "diary", "2026-09-27 15:10:00")
    assert ps.is_daily_due("diary", 23, now=now) is False


def test_从未跑过_仍等窗口_不在窗口外抢跑(_state):
    now = datetime(2026, 9, 27, 2, 0)  # 北京 10:00
    assert ps.is_daily_due("diary", 23, now=now) is False
