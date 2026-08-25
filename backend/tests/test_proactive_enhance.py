# -*- coding: utf-8 -*-
"""#28 主动消息增强低优先项（2026-08-24）：
①自然度评分简化版（纯规则）②用户作息学习（user_rhythm 活跃时段推断）③手动触发测试接口（鉴权+返回）。
"""
import asyncio
import os
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import scheduler as scheduler_api
from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.scheduler import message_generator as mg
from app.scheduler import user_rhythm as urm
from app.scheduler.message_generator import (
    NATURALNESS_RETRY_THRESHOLD,
    NATURALNESS_SKIP_THRESHOLD,
    score_naturalness,
)

ADMIN = 1
OTHER = 200


# ---------------- ① 自然度评分（纯函数） ----------------

def test_naturalness_natural_message_high():
    # 自然、像真人的消息 → 高于重试阈值（不触发重试/降级）
    s = score_naturalness(["今天天气不错，我们下午去公园走走吧，顺便拍照。"])
    assert s >= NATURALNESS_RETRY_THRESHOLD


def test_naturalness_repetitive_low():
    # 复读/刷屏 → 低于重试阈值（触发一次重试）
    assert score_naturalness(["哈哈哈哈哈哈"]) < NATURALNESS_RETRY_THRESHOLD


def test_naturalness_very_short_capped():
    # 过短（单个语气词/“在吗”）对主动长文类消息不自然 → 封顶封底判定
    assert score_naturalness(["在吗"]) <= 0.30


def test_naturalness_empty_zero():
    assert score_naturalness([]) == 0.0
    assert score_naturalness("") == 0.0


def test_naturalness_accepts_str_or_list():
    assert score_naturalness("你好呀！") == score_naturalness(["你好呀！"])


def test_naturalness_skip_threshold_gt_retry():
    # 语义护栏：跳过阈值应低于重试阈值（先重试，仍差再降级跳过）
    assert NATURALNESS_SKIP_THRESHOLD < NATURALNESS_RETRY_THRESHOLD


def test_naturalness_flag_toggle():
    from app.agent.loop import AGENT_FLAGS
    saved = AGENT_FLAGS.get("proactive_naturalness_score")
    try:
        AGENT_FLAGS["proactive_naturalness_score"] = True
        assert mg._naturalness_flag() is True
        AGENT_FLAGS["proactive_naturalness_score"] = False
        assert mg._naturalness_flag() is False
    finally:
        AGENT_FLAGS["proactive_naturalness_score"] = saved


# ---------------- ② 用户作息推断（纯函数） ----------------

def test_infer_active_hours_basic():
    counts = {8: 5, 9: 8, 10: 7, 20: 6, 21: 9, 22: 4}
    assert urm.infer_active_hours(counts) == [[8, 11], [20, 23]]


def test_infer_active_hours_empty():
    assert urm.infer_active_hours({}) == []


def test_infer_active_hours_single():
    assert urm.infer_active_hours({3: 1}) == [[3, 4]]


def test_hourly_weight_in_range():
    assert urm.hourly_rhythm_weight(9, [[8, 11]]) == 1.0


def test_hourly_weight_out_of_range():
    assert urm.hourly_rhythm_weight(15, [[8, 11]]) == 0.0


def test_hourly_weight_unknown_is_neutral():
    # 未学习（空时段）→ 1.0（不影响现状）
    assert urm.hourly_rhythm_weight(15, []) == 1.0
    assert urm.hourly_rhythm_weight(15, None) == 1.0


def test_hourly_weight_wrap_around():
    # 跨天时段 22:00-02:00
    assert urm.hourly_rhythm_weight(23, [[22, 2]]) == 1.0
    assert urm.hourly_rhythm_weight(1, [[22, 2]]) == 1.0
    assert urm.hourly_rhythm_weight(12, [[22, 2]]) == 0.0


# ---------------- ② 用户作息学习（落库） ----------------

@pytest.fixture()
def rhythm_db(monkeypatch):
    """临时 SQLite 文件库：patch app.db.database.async_session_factory（不触碰 backend/data）。"""
    tmp = tempfile.mkdtemp(prefix="rhythm_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    yield factory
    engine.sync_engine.dispose()


def _seed_chat(factory):
    """塞用户+角色+会话+跨小时用户消息（北京时间 8/9/10 与 20/21/22），user_id=1。"""
    from app.models.user import User
    from app.models.character import AICharacter
    from app.models.chat_session import ChatSession
    from app.models.chat_message import ChatMessage

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None, minute=30, second=0, microsecond=0)

    async def _s():
        async with factory() as db:
            db.add_all([
                User(id=1, username="u1", nickname="用户1"),
                AICharacter(id=1, user_id=1, name="小爱", personality="友善"),
                ChatSession(id=1, user_id=1, character_id=1),
            ])
            await db.flush()
            msgs = []
            for cn_h, cnt in [(8, 2), (9, 3), (10, 2), (20, 2), (21, 3), (22, 2)]:
                utc_h = (cn_h - 8) % 24
                for _ in range(cnt):
                    msgs.append(ChatMessage(
                        session_id=1, sender_type="user", content="hi",
                        created_at=now_naive.replace(hour=utc_h),
                    ))
            db.add_all(msgs)
            await db.commit()

    asyncio.run(_s())


def test_learn_user_rhythm_persists(rhythm_db):
    _seed_chat(rhythm_db)
    active = asyncio.run(urm.learn_user_rhythm(1))
    assert active == [[8, 11], [20, 23]]

    from sqlalchemy import select
    from app.models.user_rhythm import UserRhythm
    async def _read():
        async with rhythm_db() as db:
            row = (await db.execute(select(UserRhythm).where(UserRhythm.user_id == 1))).scalar_one_or_none()
            return row
    row = asyncio.run(_read())
    assert row is not None
    assert asyncio.run(urm._load_hourly_counts(1)) == {8: 2, 9: 3, 10: 2, 20: 2, 21: 3, 22: 2}


# ---------------- ③ 手动触发测试接口（鉴权+返回） ----------------

class _FakeSession:
    def __init__(self, char):
        self._char = char

    async def get(self, model, pk, **kw):
        if self._char is None:
            return None
        return self._char if pk == self._char.id else None


def _char(char_id=1):
    return SimpleNamespace(
        id=char_id, user_id=1, name="小爱", bio="",
        personality="友善", current_status="在家", relationship_summary="普通朋友",
    )


def _make_app(char, user_id):
    app = FastAPI()
    app.include_router(scheduler_api.router)
    app.include_router(scheduler_api.proactive_router)

    async def _fake_db():
        yield _FakeSession(char)

    app.dependency_overrides[get_db] = _fake_db
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return app


def _patch_api_deps(monkeypatch):
    async def _is_admin(uid):
        return uid == ADMIN

    async def _gen(**kw):
        return (["你好呀！", "今天天气不错。"], "")

    async def _sess(char_id, user_id):
        return {"id": 1}

    async def _last(sid, limit=10):
        return "用户: hi"

    monkeypatch.setattr("app.services.permission_service.is_admin_user", _is_admin)
    monkeypatch.setattr("app.scheduler.message_generator.generate_proactive_event", _gen)
    monkeypatch.setattr("app.scheduler.triggers.get_latest_session", _sess)
    monkeypatch.setattr("app.scheduler.triggers.get_last_messages", _last)


def test_trigger_test_forbidden_non_admin(monkeypatch):
    _patch_api_deps(monkeypatch)
    client = TestClient(_make_app(_char(), OTHER))
    r = client.post("/api/v1/proactive/trigger/test", json={"character_id": 1})
    assert r.status_code == 403


def test_trigger_test_ok(monkeypatch):
    _patch_api_deps(monkeypatch)
    client = TestClient(_make_app(_char(), ADMIN))
    r = client.post("/api/v1/proactive/trigger/test", json={"character_id": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["character_id"] == 1
    assert body["trigger_type"] == "motivation"
    assert body["content"] == "你好呀！\n今天天气不错。"
    assert body["segments"] == ["你好呀！", "今天天气不错。"]
    assert body["sent"] is False
    assert body["naturalness_score"] == score_naturalness(["你好呀！", "今天天气不错。"])


def test_trigger_test_explicit_type(monkeypatch):
    _patch_api_deps(monkeypatch)
    client = TestClient(_make_app(_char(), ADMIN))
    r = client.post("/api/v1/proactive/trigger/test",
                    json={"character_id": 1, "trigger_type": "greeting"})
    assert r.status_code == 200
    assert r.json()["trigger_type"] == "greeting"


def test_trigger_test_missing_character(monkeypatch):
    _patch_api_deps(monkeypatch)
    client = TestClient(_make_app(_char(), ADMIN))
    r = client.post("/api/v1/proactive/trigger/test", json={})
    assert r.status_code == 400


def test_trigger_test_invalid_type(monkeypatch):
    _patch_api_deps(monkeypatch)
    client = TestClient(_make_app(_char(), ADMIN))
    r = client.post("/api/v1/proactive/trigger/test",
                    json={"character_id": 1, "trigger_type": "nope"})
    assert r.status_code == 400


def test_trigger_test_character_not_found(monkeypatch):
    _patch_api_deps(monkeypatch)
    client = TestClient(_make_app(None, ADMIN))
    r = client.post("/api/v1/proactive/trigger/test", json={"character_id": 999})
    assert r.status_code == 404
