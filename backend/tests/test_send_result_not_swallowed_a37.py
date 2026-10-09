# -*- coding: utf-8 -*-
"""A37 批 1 守卫：`send_to_session` 的结局**不许被吞**（审计 §1.4 V1-V8 的结构性防线）。

三件事：
① 穷举调用点并强制分类——已消费的、明确留给批 2/批 3 的，两份名单加起来必须等于实测集合
   （多一个新调用点就红，逼后来的人表态；少一个也必须改名单，不许名单烂掉）；
② 批 1 改过的 6 处逐个验"确实读了 `.ok`"（源码标记）＋两处行为回归（被拦 vs 真发）；
③ `SendResult` 自身的形状：非空元组恒真 ⇒ 消费方必须读 `.ok`，抑制分支不许再写裸 `return`。
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import pytest

from app.scheduling.scheduler import SendResult

APP = Path(__file__).resolve().parents[1] / "app"

# 批 1 已消费返回值的调用点（文件 → 该文件里必须出现的消费标记）
CONSUMED = {
    "app/scheduling/scheduler.py": None,                       # 出口本身（单独验形状）
    "app/scheduling/arbiter.py": '_sent = getattr(_res, "ok", None)',
    "app/scheduling/memory_review.py": 'if getattr(_res, "ok", None) is False:',
    "app/scheduling/pet_care.py": 'if getattr(_res, "ok", None) is False:',
    "app/scheduling/life_share.py": 'if getattr(_res, "ok", None) is False:',
    "app/application/emotion_care_ports.py": "return await send_to_session(",
}

# 明确不在批 1 范围（审计 §4 批 2/批 3 的口径），逐条写清"为什么现在可以不消费"
DEFERRED = {
    "app/api/privacy.py": "隐私导出回执，非主动投放通道，不写消费标记",
    "app/application/phone_auto_notify_service.py": "A40（10-10）已消费其返回值：发送前二次校验；此条保留作历史留档",
    "app/scheduling/executors/plugin.py": "批 3；插件通道另有自己的失败留痕",
    "app/scheduling/executors/special.py": "批 1 只改「失败算不算当日已发」（见 triggers 谓词），发送口本身留批 3",
    "app/scheduling/executors/timer.py": "审计 V4：闹钟类先 mark_fired 保证承诺状态流转；主题熔断那一档属 A35/A38 已判可接受",
    "app/scheduling/life_regression.py": "批 3",
    "app/scheduling/state_triggers.py": "批 3",
    "app/scheduling/storyline_engine.py": "批 3（arbiter 侧 flush 已在批 1 收口）",
    "app/scheduling/unfinished_topic.py": "批 3",
    "app/scheduling/prospective_intent.py": "A34 已确立 _revert_to_pending；发送结局的消费留批 3 收口",
}


def _sites():
    """穷举 app/ 下的 send_to_session 调用点（按文件计数，排除定义本身）。"""
    pat = re.compile(r"send_to_session\(")
    out = {}
    for p in APP.rglob("*.py"):
        rel = str(p.relative_to(APP.parent)).replace("\\", "/")
        n = 0
        for line in p.read_text(encoding="utf-8").splitlines():
            if pat.search(line) and "async def send_to_session" not in line:
                n += 1
        if n:
            out[rel] = out.get(rel, 0) + n
    return out


def test_调用点全被分类_新增静默发送口会当场变红():
    got = _sites()
    declared = set(CONSUMED) | set(DEFERRED)
    assert set(got) == declared, (
        "有新的 send_to_session 调用点没被分类，或名单已过期：%s" % {
            "多出来的": sorted(set(got) - declared), "已不存在的": sorted(declared - set(got))})
    # 每文件的出现次数也要对得上（防止"同一文件里只改一处就宣称已消费"）
    counts = {**{k: 1 for k in CONSUMED if k != "app/scheduling/scheduler.py"},
              **{k: v for k, v in (("app/scheduling/pet_care.py", 3),
                                   ("app/scheduling/executors/plugin.py", 2))}}
    for rel, expect in counts.items():
        assert got[rel] >= expect, "%s 调用点数掉到 %d（预期 ≥%d）" % (rel, got[rel], expect)


@pytest.mark.parametrize("rel,marker", sorted(CONSUMED.items()))
def test_批1改过的点确实读了ok(rel, marker):
    if marker is None:
        return
    src = (APP.parent / rel).read_text(encoding="utf-8")
    assert marker in src, f"{rel} 没读到 .ok：结局又被吞了"


def test_出口形状_抑制分支不许再写裸return():
    src = (APP / "scheduling" / "scheduler.py").read_text(encoding="utf-8")
    start = src.index("async def send_to_session(")
    nxt = re.search(r"\n(?:async )?def |\n# ──", src[start + 10:])
    body = src[start:start + 10 + (nxt.start() if nxt else 4000)]
    guard = body[body.index("if _sup:"):]
    first_return = guard[guard.index("return"):].splitlines()[0].strip()
    assert "SendResult(False" in first_return, f"抑制分支返回值改成 {first_return} 了？裸 return 就是静默蒸发"
    assert 'return SendResult(True, "sent")' in body, "出口尾部的 ok=True 结局被删了"
    assert not re.search(r"^\s+return\s*$", body, re.M), "函数里还有裸 return（调用方拿不到任何信息）"


def test_SendResult_恒真陷阱_文档必须提醒():
    """非空元组恒真：`if res:` 等于没读。守卫把这条写进文档，防后来人踩。"""
    r = SendResult(False, "topic_guard")
    assert bool(r) is True and r.ok is False
    assert "恒真" in (SendResult.__doc__ or "")


# ── 行为回归：arbiter flush 的两种结局 ────────────────────────────────────────

CHAR, OWNER, SESSION = 71, 72, 73


def _factory(tmp_path):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    db_path = os.path.join(str(tmp_path), "a37.db").replace("\\", "/")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _create_all():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())
    return factory


def _seed_pending(factory, send_at):
    from app.models.character import ProactiveStorylineItem

    async def _ins():
        async with factory() as db:
            db.add(ProactiveStorylineItem(
                character_id=CHAR, session_id=SESSION, user_id=OWNER,
                group_id="g-a37", seq=0, content="在吗", send_at=send_at, status="pending",
            ))
            await db.commit()

    asyncio.run(_ins())


def _status_of(factory):
    from sqlalchemy import select

    from app.models.character import ProactiveStorylineItem

    async def _q():
        async with factory() as db:
            return (await db.execute(select(ProactiveStorylineItem.status))).scalars().all()

    return asyncio.run(_q())


@pytest.fixture()
def _clock(monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app.scheduling import arbiter

    base = datetime(2026, 10, 8, 4, 0, tzinfo=timezone.utc)   # 北京 12:00，避开夜间闸

    class _FixedDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return base.astimezone(tz) if tz else base.replace(tzinfo=None)

    monkeypatch.setattr(arbiter, "datetime", _FixedDT)
    return base, timedelta


def test_被闸拦下_切片保持pending_不计sent(monkeypatch, tmp_path, _clock):
    from app.scheduling import arbiter

    now, _td = _clock
    factory = _factory(tmp_path)
    monkeypatch.setattr(arbiter, "async_session_factory", factory)
    _seed_pending(factory, now)

    async def _suppressed(*a, **k):
        return SendResult(False, "topic_guard")

    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _suppressed)
    assert asyncio.run(arbiter.flush_storyline_items()) == 0
    assert _status_of(factory) == ["pending"], "被拦的切片被标成 sent 了＝跳过即烧"


def test_真发出去才标sent(monkeypatch, tmp_path, _clock):
    from app.scheduling import arbiter

    now, _td = _clock
    factory = _factory(tmp_path)
    monkeypatch.setattr(arbiter, "async_session_factory", factory)
    _seed_pending(factory, now)

    async def _sent(*a, **k):
        return SendResult(True, "sent")

    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _sent)
    assert asyncio.run(arbiter.flush_storyline_items()) == 1
    assert _status_of(factory) == ["sent"]


def test_替身返回None仍按已发处理_不炸旧测试(monkeypatch, tmp_path, _clock):
    """旧行为兼容：假发送返回 None（没有 .ok）⇒ 不能因为新逻辑把它当成"没发"卡死。"""
    from app.scheduling import arbiter

    now, _td = _clock
    factory = _factory(tmp_path)
    monkeypatch.setattr(arbiter, "async_session_factory", factory)
    _seed_pending(factory, now)

    async def _legacy(*a, **k):
        return None

    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _legacy)
    assert asyncio.run(arbiter.flush_storyline_items()) == 1


# ── 结构回归：顺序即语义的两处 ────────────────────────────────────────────────

def test_life_share_approved必须排在发送之后():
    src = (APP / "scheduling" / "life_share.py").read_text(encoding="utf-8")
    i_send = src.index("await send_to_session(")
    i_ok = src.index("await _log_approved(")
    assert i_send < i_ok, "approved 又跑到发送前面了＝配额按排队计（V8 复发）"
    assert src[i_send:i_ok].count("is False") >= 1, "发送后没判结局就直接记 approved"


def test_memory_review_发出才占3天_没发只占短退避():
    src = (APP / "scheduling" / "memory_review.py").read_text(encoding="utf-8")
    assert "REVIEW_SKIP_BACKOFF_MINUTES = 30" in src
    body = src[src.index("async def run_memory_review"):]
    i_short = body.index("timedelta(minutes=REVIEW_SKIP_BACKOFF_MINUTES)")
    i_long = body.index("days=REVIEW_RETRY_DAYS")
    assert i_short < i_long, "占位顺序倒了：又变回一律 3 天（V1 复发）"
    gate = body[i_long - 900:i_long]
    assert 'getattr(_res, "ok", None) is False' in gate, "3 天窗口没被「真发出去」这个条件守住"


def test_pet_care_两个早退不再占名额():
    src = (APP / "scheduling" / "pet_care.py").read_text(encoding="utf-8")
    body = src[src.index("async def run_ai_care"):src.index("async def _pet_visit_daily_count")]
    quota = body[body.index("AI_CARE_DAILY_LIMIT"):][:160]
    sess = body[body.index("if session_id is None:"):][:120]
    assert "return False" in quota and "return True" not in quota, "限额那条又 return True 占名额了"
    assert "return False" in sess, "无会话那条又 return True 占名额了"


def test_care_取消原因拆两类_且不再写裸cancelled():
    from app.domain.emotion import care

    assert care.STATUS_CANCELLED_QUOTA == "cancelled_quota"
    assert care.STATUS_CANCELLED_STORY == "cancelled_story"
    src = (APP / "domain" / "emotion" / "care.py").read_text(encoding="utf-8")
    assert 'finish_care_task(task_id, "cancelled")' not in src, "还有取消原因没拆（V5 复发）"
    assert src.count("STATUS_CANCELLED_QUOTA") >= 4 and src.count("STATUS_CANCELLED_STORY") >= 2


def test_care_被闸拦下时不写done_取消原因归环境类():
    """care.py 不直接调 send_to_session（经 ports），所以不在调用点名单里，单独钉。"""
    src = (APP / "domain" / "emotion" / "care.py").read_text(encoding="utf-8")
    body = src[src.index("async def run_emotion_care"):]
    i_gate = body.index('getattr(_res, "ok", None) is False')
    i_done = body.index('finish_care_task(task_id, "done")')
    assert i_gate < i_done, "done 又排在结局判定之前＝被拦也算已关怀"
    assert "STATUS_CANCELLED_QUOTA)" in body[i_gate:i_done], "没发出去时没落取消终态"


def test_节庆谓词_失败留痕不算已发():
    """纯判据单测（不连库）：[send_failed] 前缀＝失败，其余＝真发过。"""
    from app.scheduling.triggers import FESTIVAL_FAIL_ATTEMPTS_PER_DAY, _is_send_failed

    class _Row:
        def __init__(self, content):
            self.content = content

    assert _is_send_failed(_Row("[send_failed] boom")) is True
    assert _is_send_failed(_Row("生日快乐")) is False
    assert _is_send_failed(_Row(None)) is False
    assert FESTIVAL_FAIL_ATTEMPTS_PER_DAY >= 1, "有限重试闸被拆了＝08-20 死循环防线回来了"


def test_闸字段默认None_不改任何既有候选():
    from datetime import datetime

    from app.scheduling.sources.base import TriggerItem

    old = TriggerItem(type="x", priority=3)
    assert old.to_dict() == {"type": "x", "priority": 3}, "默认值泄漏进 dict＝既有候选语义变了"
    assert TriggerItem.from_dict({"type": "x", "priority": 3}).snapshot_at is None
    stamped = TriggerItem(type="x", priority=3, snapshot_at=datetime(2026, 10, 8), expects="arrival")
    d = stamped.to_dict()
    assert d["snapshot_at"] == datetime(2026, 10, 8) and d["expects"] == "arrival"
    back = TriggerItem.from_dict(d)
    assert (back.snapshot_at, back.expects) == (datetime(2026, 10, 8), "arrival"), "from_dict 没透传＝闸①的地基断了"
