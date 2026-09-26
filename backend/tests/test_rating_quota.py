# -*- coding: utf-8 -*-
"""AI 评星每日额度账本的回归（2026-09-25）。

事故背景见 app/memory/rating_quota.py 模块说明：额度原按 memories.updated_at 近似统计，
被同拍的记忆衰减刷新的 updated_at 污染 ⇒ 评星恒判「今日已评满」而静默停摆 5 天。
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.memory import rating_quota as rq


@pytest.fixture()
def quota_file(tmp_path, monkeypatch):
    path = tmp_path / "ai_rating_quota.json"
    monkeypatch.setattr(rq, "_STATE_FILE", path)
    return path


def test_empty_is_zero(quota_file):
    assert rq.used_today(13) == 0


def test_add_accumulates(quota_file):
    assert rq.add(13, 10) == 10
    assert rq.add(13, 5) == 15
    assert rq.used_today(13) == 15
    assert rq.used_today(11) == 0


def test_day_rollover_resets(quota_file):
    day1 = datetime(2026, 9, 25, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    day2 = day1 + timedelta(days=1)
    rq.add(13, 10, now=day1)
    assert rq.used_today(13, now=day1) == 10
    assert rq.used_today(13, now=day2) == 0
    assert rq.add(13, 3, now=day2) == 3


def test_corrupt_file_is_zero(quota_file):
    quota_file.parent.mkdir(parents=True, exist_ok=True)
    quota_file.write_text("not json", encoding="utf-8")
    assert rq.used_today(13) == 0


def test_daily_rated_reads_ledger(quota_file):
    """评星额度不再看 memories.updated_at（会被衰减污染），只认账本。"""
    from app.memory.ai_rating import _daily_rated

    rq.add(13, 7)
    assert asyncio.run(_daily_rated(None, 13)) == 7
    assert asyncio.run(_daily_rated(None, 99)) == 0
