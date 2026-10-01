# -*- coding: utf-8 -*-
"""A14 M0（2026-10-01）：AI 评星「多轮共识写回」**干跑**测试。

口径（小方案 v1 §2/§4）：
  1. flag `ai_rating_vote3` 默认关、已登记目录；关 ⇒ 逐字节旧行为（一次都不多调）；
  2. 干跑闸＝flag ∧ 角色命中既有灰度白名单（char13），**fail-closed**；
  3. 干跑只多算 N-1 轮并把 rounds/consensus 写进留痕，**写回仍用第 1 轮**；
  4. 共识纯函数：多数星（严格过半）；无多数取中位数四舍五入。
"""
from __future__ import annotations

import pathlib

import pytest

from app.flags.agent_flags import AGENT_FLAGS
from app.memory import ai_rating

_FLAG = ai_rating.VOTE3_FLAG_KEY


@pytest.fixture(autouse=True)
def _restore_flag():
    saved = AGENT_FLAGS.get(_FLAG)
    AGENT_FLAGS[_FLAG] = False
    yield
    if saved is None:
        AGENT_FLAGS.pop(_FLAG, None)
    else:
        AGENT_FLAGS[_FLAG] = saved


def test_flag默认关且已登记():
    assert _FLAG == "ai_rating_vote3"
    assert AGENT_FLAGS[_FLAG] is False
    assert ai_rating.VOTE3_ROUNDS == 3


def test_干跑闸必须flag开且角色在白名单():
    assert ai_rating._vote3_enabled(13) is False       # flag 关
    AGENT_FLAGS[_FLAG] = True
    assert ai_rating._vote3_enabled(13) is True        # 白名单内
    assert ai_rating._vote3_enabled(2) is False        # 白名单外
    assert ai_rating._vote3_enabled(None) is False     # 缺角色 ⇒ fail-closed


def test_共识_多数星优先():
    rounds = [
        [{"id": 1, "star": 3}, {"id": 2, "star": 2}],
        [{"id": 1, "star": 3}, {"id": 2, "star": 4}],
        [{"id": 1, "star": 4}, {"id": 2, "star": 4}],
    ]
    cons = ai_rating._consensus_stars(rounds)
    assert cons[1] == 3.0 and cons[2] == 4.0


def test_共识_无多数取中位数():
    rounds = [
        [{"id": 7, "star": 5}],
        [{"id": 7, "star": 2}],
        [{"id": 7, "star": 3}],
    ]
    assert ai_rating._consensus_stars(rounds)[7] == 3.0   # 中位数 3


def test_共识_缺轮与空轮不炸():
    rounds = [
        [{"id": 9, "star": 4}],
        [],
        [{"id": 9, "star": 4}],
    ]
    assert ai_rating._consensus_stars(rounds)[9] == 4.0
    assert ai_rating._consensus_stars([]) == {}
    assert ai_rating._consensus_stars([[{"id": "脏", "star": "x"}]]) == {}


def test_M0干跑不改写回口径_源码级锚定():
    """写回仍取第 1 轮 results（by_id 由 results 构造），干跑块只写留痕且受 _vote3_enabled 门控。"""
    src = pathlib.Path(ai_rating.__file__).read_text(encoding="utf-8")
    assert 'obs["consensus"] = {str(k): v for k, v in _consensus_stars(_rounds).items()}' in src
    assert 'obs["rounds"] = [{str(r["id"]): r["star"] for r in rd} for rd in _rounds]' in src
    assert "if _vote3_enabled(char.id):" in src
    # 写回源的锚：by_id 仍来自 results（第 1 轮）
    i_dry = src.index("if _vote3_enabled(char.id):")
    i_by_id = src.index('by_id = {r["id"]: r["star"] for r in results}')
    assert i_by_id > i_dry, "写回源必须仍在干跑块之后、且用第 1 轮 results"
    assert "_rounds" not in src[i_by_id:i_by_id + 80]
