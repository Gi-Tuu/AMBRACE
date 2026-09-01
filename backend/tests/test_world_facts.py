"""World State 查询层测试（World & Cognition P4，2026-08-15）

覆盖：audience 可见性纯函数 / 事实文本标注 / 模型字段与上限常量 / status 新鲜度窗口 / fold TTL。
DB 级 assert_fact/supersede 行为通过真实链路冒烟验证（见 changelog）。
"""
import asyncio
from datetime import datetime, timedelta

from app.events.facts import (
    audience_list, audience_visible, fact_text,
    PUBLIC_AUDIENCE, MAX_FACTS_PER_CHAR, STATUS_FRESH_HOURS,
    _status_fresh, _latest_facts_by_predicate, fold_status_update,
)
from app.events.schema import EPISTEMIC_FACT, EPISTEMIC_PLANNED
from app.models.memory import WorldFact


def test_audience_list_formats_types():
    assert audience_list([("user", 4), ("char", 11)]) == '["user:4", "char:11"]'
    assert audience_list(["public"]) == '["public"]'
    assert audience_list([]) == "[]"


def test_audience_visible():
    private = audience_list([("user", 4), ("char", 11)])
    assert audience_visible(private, "character", 11) is True
    assert audience_visible(private, "char", 11) is True
    assert audience_visible(private, "user", 4) is True
    assert audience_visible(private, "character", 12) is False  # B 看不到 A 的私有事实
    assert audience_visible('["public"]', "character", 99) is True  # public 所有人可见
    assert audience_visible(None, "character", 11) is False
    assert audience_visible("bad-json", "character", 11) is False


def test_fact_text_prefix():
    f1 = WorldFact(id=1, object_value="正在和用户一起吃晚饭", epistemic_status=EPISTEMIC_FACT)
    assert fact_text(f1) == "- 正在和用户一起吃晚饭"
    f2 = WorldFact(id=2, object_value="用户可能因为工作压力", epistemic_status=EPISTEMIC_PLANNED)
    assert fact_text(f2) == "- [PLANNED] 用户可能因为工作压力"


def test_status_fresh_窗口():
    now = datetime(2026, 8, 16, 6, 0, 0)
    fresh = now - timedelta(hours=5)
    stale = now - timedelta(hours=13)
    assert _status_fresh(fresh, now) is True
    assert _status_fresh(stale, now) is False
    assert _status_fresh(None, now) is False  # 缺失视为不新鲜，保守不注入


def test_fold_status_update_带ttl(monkeypatch):
    captured = {}

    async def _fake_assert(**kw):
        captured.update(kw)

    monkeypatch.setattr("app.events.facts.assert_fact", _fake_assert)
    asyncio.run(fold_status_update(13, 3, "两人在吃饭"))
    assert captured.get("predicate") == "status"
    assert captured.get("ttl_minutes") == STATUS_FRESH_HOURS * 60  # 瞬时状态 12h 过期


def test_latest_facts_by_predicate_矛盾只取最新():
    from datetime import datetime, timedelta
    now = datetime(2026, 8, 16, 12, 0, 0)
    f_old = WorldFact(id=1, predicate="status", object_value="两人在床上准备入睡", asserted_at=now - timedelta(hours=3))
    f_new = WorldFact(id=2, predicate="status", object_value="两人在吃饭", asserted_at=now - timedelta(hours=1))
    f_act = WorldFact(id=3, predicate="activity", object_value="完成了创作", asserted_at=now)
    out = _latest_facts_by_predicate([f_new, f_old, f_act], {"status", "activity"})
    preds = [f.predicate for f in out]
    assert preds == ["status", "activity"]  # status 只留最新 1 条 + activity 1 条
    assert out[0].id == 2  # 保留最新「在吃饭」，丢弃旧「准备入睡」


def test_model_and_constants():
    assert MAX_FACTS_PER_CHAR == 12
    f = WorldFact(
        user_id=4, character_id=11, subject_type="character", subject_id=11,
        predicate="status", object_value="正在做饭", audience='["user:4", "char:11"]',
        status="active", epistemic_status=EPISTEMIC_FACT,
    )
    assert f.predicate == "status"
    assert f.status == "active"
    assert f.epistemic_status == "FACT"
    assert PUBLIC_AUDIENCE == "public"
