# -*- coding: utf-8 -*-
"""M1-S11 记忆可观测埋点测试：obs_event 门控/字段、quota 裁剪埋点、marker_truncated 判定"""
import pytest

from app.memory import observability as obs


@pytest.fixture()
def captured(monkeypatch):
    """捕获 enqueue_task_log 调用（obs_event 内部动态 import，须 patch 源头）"""
    calls = []

    def _fake_enqueue(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("app.agent.trace.enqueue_task_log", _fake_enqueue)
    return calls


def test_obs_event_flag_on_writes(captured, monkeypatch):
    monkeypatch.setattr(obs, "_flag_on", lambda: True)
    obs.obs_event(7, "quota_clipped_sections", {"total_removed": 120}, kind="x")
    assert len(captured) == 1
    row = captured[0]
    assert row["trigger"] == "memory_obs"
    assert row["route"] == "quota_clipped_sections"
    assert row["character_id"] == 7
    assert '"kind": "x"' in row["steps_json"] or '"kind":"x"' in row["steps_json"]


def test_obs_event_flag_off_silent(captured, monkeypatch):
    monkeypatch.setattr(obs, "_flag_on", lambda: False)
    obs.obs_event(7, "dual_write_dup_merge", {"hit_id": 1}, kind="merge")
    assert captured == []


def test_obs_event_failure_silent(monkeypatch):
    """enqueue 抛错不得外溢（fail-open 主链路）"""
    monkeypatch.setattr(obs, "_flag_on", lambda: True)

    def _boom(**kwargs):
        raise RuntimeError("no loop")

    monkeypatch.setattr("app.agent.trace.enqueue_task_log", _boom)
    obs.obs_event(None, "marker_truncated", {"tail": "x"})  # 不应抛出


def test_marker_truncation_detected(captured, monkeypatch):
    monkeypatch.setattr(obs, "_flag_on", lambda: True)
    obs.note_marker_truncation("今天天气不错。对了我想起来了【记忆：用户喜欢", 5)
    assert len(captured) == 1
    assert captured[0]["route"] == "marker_truncated"
    assert "记忆" in captured[0]["steps_json"]


def test_marker_truncation_clean_text_silent(captured, monkeypatch):
    monkeypatch.setattr(obs, "_flag_on", lambda: True)
    obs.note_marker_truncation("正常回复，标记已闭合【记忆：测试】。", 5)
    obs.note_marker_truncation("", 5)
    obs.note_marker_truncation(None, 5)
    assert captured == []


def test_quota_clip_emits_event(monkeypatch):
    """超预算裁剪写 quota_clipped_sections；配额内零埋点（行为与埋点双零变化）"""
    from app.agent import context_builder as cb

    events = []
    monkeypatch.setattr("app.memory.observability.obs_event",
                        lambda cid, metric, detail, kind=None: events.append((cid, metric, detail)))

    big = "x" * (cb.TOTAL_SYSTEM_QUOTA_TOKENS * cb._EST_CHARS_PER_TOKEN)  # 单块即超顶
    msgs = [{"role": "system", "content": "核心：" + big},
            {"role": "system", "content": "追加块A：" + "y" * 500},
            {"role": "user", "content": "hi"}]
    cb._apply_system_total_quota(msgs, character_id=9)
    assert len(events) == 1
    cid, metric, detail = events[0]
    assert cid == 9 and metric == "quota_clipped_sections"
    assert detail["total_removed"] > 0 and len(detail["blocks"]) >= 1
    # user 消息不裁剪
    assert msgs[2]["content"] == "hi"

    # 配额内：零裁剪零埋点
    events.clear()
    small = [{"role": "system", "content": "短"}, {"role": "user", "content": "hi"}]
    cb._apply_system_total_quota(small, character_id=9)
    assert events == []
    assert small[0]["content"] == "短"
