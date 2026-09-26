# -*- coding: utf-8 -*-
"""长周期记忆维护「按时间戳补齐」的回归（2026-09-25）。

事故背景见 app/memory/maintenance_schedule.py 模块说明：判据原本是 scheduler 的
「6 小时进程内计数」，频繁重启下永远到不了阈值 ⇒ 记忆评星从 09-20 起停摆 5 天。
"""
import asyncio
from datetime import timedelta

import pytest

from app.memory import maintenance_schedule as ms
from app.utils.timeutil import now_naive_utc


@pytest.fixture()
def state_file(tmp_path, monkeypatch):
    path = tmp_path / "last_memory_maintenance"
    monkeypatch.setattr(ms, "_STATE_FILE", path)
    return path


def test_never_run_is_due(state_file):
    assert ms.last_run_at() is None
    assert ms.is_due() is True


def test_recent_run_not_due_old_run_due(state_file):
    now = now_naive_utc()
    ms._write_state(now)
    assert ms.is_due(now=now) is False
    ms._write_state(now - timedelta(hours=7))
    assert ms.is_due(now=now) is True


def test_corrupt_state_treated_as_never(state_file):
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("garbage", encoding="utf-8")
    assert ms.last_run_at() is None
    assert ms.is_due() is True


def test_run_if_due_skips_when_not_due(state_file, monkeypatch):
    import app.memory as memory_pkg
    import app.memory.ai_rating as rating_mod

    ms._write_state(now_naive_utc())
    calls = []

    async def _decay():
        calls.append("decay")

    async def _rate():
        calls.append("rate")
        return 0

    monkeypatch.setattr(memory_pkg, "run_memory_decay", _decay, raising=False)
    monkeypatch.setattr(rating_mod, "run_ai_rating", _rate, raising=False)

    assert asyncio.run(ms.run_if_due()) is False
    assert calls == []


def test_run_if_due_runs_and_stamps(state_file, monkeypatch):
    import app.memory as memory_pkg
    import app.memory.ai_rating as rating_mod

    calls = []

    async def _decay():
        calls.append("decay")

    async def _rate():
        calls.append("rate")
        return 7

    monkeypatch.setattr(memory_pkg, "run_memory_decay", _decay, raising=False)
    monkeypatch.setattr(rating_mod, "run_ai_rating", _rate, raising=False)

    before = now_naive_utc()
    assert asyncio.run(ms.run_if_due(reason="test")) is True
    after = now_naive_utc()
    assert calls == ["decay", "rate"]
    stamped = ms.last_run_at()
    assert stamped is not None
    # 状态文件按秒存储（实现有意丢微秒），比较前先对齐到秒
    assert before.replace(microsecond=0) <= stamped <= after
    assert ms.is_due() is False


def test_failure_shortens_retry_window(state_file, monkeypatch):
    import app.memory as memory_pkg
    import app.memory.ai_rating as rating_mod

    async def _decay():
        raise RuntimeError("boom")

    async def _rate():
        return 0

    monkeypatch.setattr(memory_pkg, "run_memory_decay", _decay, raising=False)
    monkeypatch.setattr(rating_mod, "run_ai_rating", _rate, raising=False)

    assert asyncio.run(ms.run_if_due(reason="test")) is True
    now = now_naive_utc()
    assert ms.is_due(now=now) is False              # 刚失败，先冷却
    assert ms.is_due(now=now + ms.RETRY_AFTER) is True  # 15 分钟后允许重试
