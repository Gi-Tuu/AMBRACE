# -*- coding: utf-8 -*-
"""主动消息防重复修复（2026-09-01）：
- C：字面重合守卫 _parrot_blocked / _context_overlap_ratio 纯函数（拦截逐字照抄、换措辞放行、空 context 放行、阈值边界、短文本豁免）；
- A：get_recent_proactive_messages 并入最近 24h AI 对话回复（真实临时库）。
"""
import asyncio
import os
import tempfile

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.scheduling.message_generator import (
    _PARROT_OVERLAP_THRESHOLD,
    _context_overlap_ratio,
    _parrot_blocked,
)


# ────────────────────────── C：字面重合守卫（纯函数） ──────────────────────────

_OLD_REPLY = (
    "尾巴的工作量确实挺大的，长毛猫每天都要梳个十来分钟才行。"
    "而且换毛季节掉毛更厉害，梳毛频率还得往上加。"
    "你要是觉得忙不过来，可以考虑短毛的品种，打理起来轻松很多。"
)


def test_parrot_逐字照抄被拦截():
    """last_context 含旧 AI 回复 + segments 逐字相同 → 拦截（真机 sam 案复现）"""
    segs = [s for s in _OLD_REPLY.split("。") if s]
    blocked, ratio = _parrot_blocked(segs, f"用户: 尾巴工作量大\nAI: {_OLD_REPLY}")
    assert blocked is True
    assert ratio > _PARROT_OVERLAP_THRESHOLD


def test_parrot_换措辞放行():
    """承接同一话题但用自己的话重新表达 → 放行"""
    segs = [
        "说到猫毛，我昨天还给芝麻梳了好久呢。",
        "你家要真嫌麻烦，短毛的确实省心一些。",
        "不过长毛猫撸起来的手感也是真的香。",
    ]
    blocked, ratio = _parrot_blocked(segs, f"用户: 尾巴工作量大\nAI: {_OLD_REPLY}")
    assert blocked is False
    assert ratio <= _PARROT_OVERLAP_THRESHOLD


def test_parrot_空context放行():
    """last_context 为空 → 跳过守卫"""
    blocked, ratio = _parrot_blocked(["随便一句正常的话，内容完全独立。"], "")
    assert blocked is False
    assert ratio == 0.0
    assert _parrot_blocked(["另一条"], "   ") == (False, 0.0)


def test_parrot_阈值边界():
    """重合率略高于 0.7 拦截、略低放行（构造 100 字符生成文本，70/69 字符来自 context）"""
    ctx = "甲" * 200
    seg_hit = "甲" * 71 + "乙" * 29      # 71% 来自 context → 拦截
    seg_pass = "甲" * 69 + "乙" * 31    # 69% 来自 context → 放行
    assert _parrot_blocked([seg_hit], ctx)[0] is True
    assert _parrot_blocked([seg_pass], ctx)[0] is False
    assert abs(_context_overlap_ratio(seg_hit, ctx) - 0.71) < 1e-9


def test_parrot_短文本豁免():
    """有效字符不足 _PARROT_MIN_CHARS 时守卫不适用（防短消息误伤）"""
    from app.scheduling.message_generator import _PARROT_MIN_CHARS
    short = "好" * (_PARROT_MIN_CHARS - 1)
    blocked, ratio = _parrot_blocked([short], short + "后缀内容")
    assert blocked is False
    assert ratio > _PARROT_OVERLAP_THRESHOLD  # 重合率超阈但被豁免


def test_parrot_重合率不受context长度稀释():
    """context 远长于生成文本时，分母仍取生成文本（覆盖度语义）"""
    seg = "这段话被完整包含在很长的上下文里。"
    ctx = "前缀" * 300 + seg + "后缀" * 300
    assert _context_overlap_ratio(seg, ctx) > 0.99


# ────────────────────────── A：防重复数据源并入 AI 对话回复（真实临时库） ──────────────────────────

@pytest.fixture()
def proac_db(monkeypatch):
    """临时 SQLite 文件库：patch app.db.database.async_session_factory（不触碰 backend/data）。"""
    tmp = tempfile.mkdtemp(prefix="parrot_")
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
    # arbiter 顶部 from-import 捕获了绑定——须同时 patch 其自身引用（否则依赖测试导入顺序）
    from app.scheduling import arbiter as _arb
    monkeypatch.setattr(_arb, "async_session_factory", factory)
    yield factory
    engine.sync_engine.dispose()


def test_recent_proactive_并入ai对话回复(proac_db):
    """主动消息日志 + 最近 24h AI 对话回复合并去重、按时间倒序、整体截断 400"""
    from datetime import datetime, timedelta
    from app.models.character import AICharacter, ProactiveMessageLog
    from app.models.chat import ChatMessage, ChatSession
    from app.scheduling.arbiter import get_recent_proactive_messages

    async def _seed():
        async with proac_db() as db:
            db.add(AICharacter(id=13, user_id=1, name="sam"))
            db.add(ChatSession(id=11, user_id=1, character_id=13))
            await db.commit()
            now = datetime.utcnow()
            # 两条 AI 对话回复（较新）+ 一条主动消息日志（更旧）
            db.add(ChatMessage(session_id=11, sender_type="ai", content="对话回复甲，最新。",
                               created_at=now - timedelta(minutes=5)))
            db.add(ChatMessage(session_id=11, sender_type="ai", content="对话回复乙，次新。",
                               created_at=now - timedelta(minutes=10)))
            # 24h 之外的 AI 回复：不应并入
            db.add(ChatMessage(session_id=11, sender_type="ai", content="超 24h 的旧回复不应出现",
                               created_at=now - timedelta(hours=30)))
            # 用户消息：不并入
            db.add(ChatMessage(session_id=11, sender_type="user", content="用户消息不并入",
                               created_at=now - timedelta(minutes=3)))
            db.add(ProactiveMessageLog(character_id=13, session_id=11, message_type="storyline",
                                       content="主动消息丙，最旧。", created_at=now - timedelta(hours=1)))
            await db.commit()

    asyncio.run(_seed())
    out = asyncio.run(get_recent_proactive_messages(13, 2))
    assert "对话回复甲" in out and "对话回复乙" in out and "主动消息丙" in out
    assert "超 24h" not in out and "用户消息不并入" not in out
    # 倒序：最新在前
    assert out.index("对话回复甲") < out.index("对话回复乙") < out.index("主动消息丙")


def test_recent_proactive_对话回复查询失败不影响主动日志(proac_db, monkeypatch):
    """A 的 fail-open：对话回复段异常时仍返回主动消息日志内容（不影响主动链路）"""
    from datetime import datetime
    from app.models.character import AICharacter, ProactiveMessageLog
    from app.scheduling.arbiter import get_recent_proactive_messages

    async def _seed():
        async with proac_db() as db:
            db.add(AICharacter(id=14, user_id=1, name="t"))
            db.add(ProactiveMessageLog(character_id=14, session_id=None, message_type="storyline",
                                       content="只有主动日志。", created_at=datetime.utcnow()))
            await db.commit()

    def _boom(*a, **k):
        raise RuntimeError("boom")

    asyncio.run(_seed())
    # 只炸第二段（AI 对话回复）的时间来源；主动日志段（第一段查询）不受影响
    monkeypatch.setattr("app.utils.timeutil.now_naive_utc", _boom)
    out = asyncio.run(get_recent_proactive_messages(14, 2))
    assert "只有主动日志。" in out
