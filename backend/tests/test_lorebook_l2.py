# -*- coding: utf-8 -*-
"""Lorebook 触发式注入进阶（L2 核心版）单测：正则/概率/Inclusion Group/粘性/冷却/向后兼容。

备注：sticky 语义为「每次续命刷新窗口」（sticky 生效即顺延），非固定 N 轮；cooldown 为固定窗口。
"""
from app.memory.lorebook import (
    _dedup_by_group, _keyword_matches, _roll_probability, match_lorebook_entries,
)
from app.models.memory import LorebookEntry


def _mk(**kw):
    base = dict(user_id=1, character_id=1, title="t", content="c",
                keywords='["记事"]', exclude_keywords="[]", active=True)
    base.update(kw)
    return LorebookEntry(**base)


def test_backward_compat_default():
    e = _mk(keywords='["摄影"]')
    assert len(match_lorebook_entries("我喜欢摄影", [e], round_no=1, rng=lambda: 0.0, character_id=1)) == 1
    assert match_lorebook_entries("我喜欢画画", [e], round_no=1, rng=lambda: 0.0, character_id=1) == []


def test_regex_keyword_match():
    e1 = _mk(keywords='["/照片/"]', is_regex=True)
    assert _keyword_matches("他发了一张照片", e1)
    e2 = _mk(keywords='["/照片|相机/"]', is_regex=True)
    assert _keyword_matches("箱子里有一台相机", e2)
    e3 = _mk(keywords='["/[/"]', is_regex=True)
    assert not _keyword_matches("x", e3)


def test_probability_roll():
    assert _roll_probability(100, lambda: 0.0) is True
    assert _roll_probability(0, lambda: 0.0) is False
    assert _roll_probability(50, lambda: 0.4) is True
    assert _roll_probability(50, lambda: 0.6) is False
    assert _roll_probability(None, lambda: 0.0) is True


def test_inclusion_group_dedup():
    import datetime
    e1 = _mk(title="a", inclusion_group="天气", updated_at=datetime.datetime(2026, 1, 1))
    e2 = _mk(title="b", inclusion_group="天气", updated_at=datetime.datetime(2026, 2, 1))
    e3 = _mk(title="c", inclusion_group="")
    out = _dedup_by_group([e1, e2, e3])
    assert {x.title for x in out} == {"b", "c"}


def test_sticky():
    e = _mk(keywords='["记事"]', sticky_rounds=2, cooldown_rounds=0)
    state = {}
    assert len(match_lorebook_entries("记事", [e], round_no=1, state=state, character_id=1)) == 1
    assert len(match_lorebook_entries("随便说", [e], round_no=2, state=state, character_id=1)) == 1
    assert len(match_lorebook_entries("随便说", [e], round_no=3, state=state, character_id=1)) == 1
    assert len(match_lorebook_entries("随便说", [e], round_no=4, state=state, character_id=1)) == 1


def test_cooldown_blocks_after_trigger():
    e = _mk(keywords='["记事"]', cooldown_rounds=2, sticky_rounds=0)
    state = {}
    assert len(match_lorebook_entries("记事", [e], round_no=1, state=state, character_id=1)) == 1
    assert match_lorebook_entries("记事", [e], round_no=2, state=state, character_id=1) == []
    assert match_lorebook_entries("记事", [e], round_no=3, state=state, character_id=1) == []
    assert len(match_lorebook_entries("记事", [e], round_no=4, state=state, character_id=1)) == 1
