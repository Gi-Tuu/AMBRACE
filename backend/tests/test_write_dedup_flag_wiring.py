# -*- coding: utf-8 -*-
"""P1-2 接线回归（2026-09-28）：写路径查重口径开关已登记且可被读到。

语义级对比（开关前后 merge 行为差异）属灰度观察项，此处只钉住「开关存在 + helper 读数正确 +
关＝用怀旧面子句」这三点，防止未来被误删或改了键名而静默失效。
"""
from app.agent.loop import AGENT_FLAGS
from app.memory.service import _retrievable_status_clause, current_facts_status_clause
from app.memory.write import _write_dedup_active_only_on


def test_开关已登记且默认关():
    assert "write_dedup_active_only" in AGENT_FLAGS
    assert AGENT_FLAGS["write_dedup_active_only"] is False
    assert _write_dedup_active_only_on() is False


def test_helper_随开关变化():
    import app.agent.loop as loop
    old = loop.AGENT_FLAGS["write_dedup_active_only"]
    try:
        loop.AGENT_FLAGS["write_dedup_active_only"] = True
        assert _write_dedup_active_only_on() is True
    finally:
        loop.AGENT_FLAGS["write_dedup_active_only"] = old


def test_两个状态子句在未来也要保持可区分():
    """关＝怀旧面（当前永真），开＝现状面（恒 active）；两者若变成同一个对象，本修复就失去意义。"""
    old = _retrievable_status_clause()
    new = current_facts_status_clause()
    assert repr(old) != repr(new), "两个子句必须语义不同（否则 write_dedup_active_only 无从生效）"
