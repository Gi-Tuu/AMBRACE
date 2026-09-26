# -*- coding: utf-8 -*-
"""批 G4（2026-09-26）：小手机备注「标记完成」标记（[CAL_DONE]/[MEMO_DONE]）接线测试。

覆盖：解析层（parse/strip + 与 [MEMO]/[CAL_NOTE] 不互相误吞）、主链路接线（_save_phone_desktop_notes）、
工具登记与真执行（note_done → 按关键词改 status）、未闭合标记剥离兜底。
真库用例走 _dbclone.clone_engine（当前 ORM 全量 schema，含 status 列），不跑 alembic。
"""
import asyncio

import pytest

from _dbclone import clone_engine, make_session_factory

import app.db.database as dbmod
import app.application.chat.tools as chat_tools
from app.agent import loop
from app.agent import tools as agent_tools
from app.agent import actions as A
from app.agent.response_parser import strip_unclosed_markers
from app.application import chat_service
from app.models.character import AICharacter
from app.models.device import CalendarNote, MemoNote
from app.models.user import User


# ── 解析层 ──

def test_parse_actions_识别两条完成标记():
    text = "嗯，这事办完了。[CAL_DONE]面试新生[/CAL_DONE][MEMO_DONE]明早热月饼[/MEMO_DONE]"
    acts = A.parse_actions(text)
    done = [a for a in acts if a.action_type == A.NOTE_DONE]
    assert len(done) == 2
    assert done[0].payload == {"type": "calendar", "match": "面试新生"}
    assert done[1].payload == {"type": "memo", "match": "明早热月饼"}


def test_strip_actions_剥净完成标记():
    text = "办完了[CAL_DONE]面试新生[/CAL_DONE]，[MEMO_DONE]明早热月饼[/MEMO_DONE]我记下了"
    out = A.strip_actions(text)
    assert "CAL_DONE" not in out and "MEMO_DONE" not in out
    assert out == "办完了，我记下了"


def test_完成标记不会被当成新增备忘_或日历():
    """回归护栏：[MEMO_DONE]/[CAL_DONE] 绝不能被 _MEMO_RE/_CAL_NOTE_RE 误吞成「新记一条」。"""
    assert A.extract_memo("[MEMO_DONE]明早热月饼[/MEMO_DONE]") is None
    assert A.extract_cal_note("[CAL_DONE]面试新生[/CAL_DONE]") is None
    # 正常标记仍照旧可用（防改坏既有能力）
    assert A.extract_memo("[MEMO]明早热月饼[/MEMO]") == "明早热月饼"
    assert A.extract_cal_done("[CAL_DONE]面试新生[/CAL_DONE]") == "面试新生"
    assert A.extract_memo_done("[MEMO_DONE]明早热月饼[/MEMO_DONE]") == "明早热月饼"


def test_未闭合完成标记也被剥净():
    """漏标记的两种形态都不留残片：

    ①开标签完整但漏写闭合 → strip_actions 剥离到行尾（与 CAL_NOTE/MEMO 同构）；
    ②开标签本身被截断（[CAL_DONE）→ response_parser.strip_unclosed_markers 兜底。
    """
    assert A.strip_actions("办完了[CAL_DONE]面试新生").strip() == "办完了"
    assert A.strip_actions("办完了[MEMO_DONE]明早热月饼").strip() == "办完了"
    assert strip_unclosed_markers("办完了[CAL_DONE").strip() == "办完了"
    assert strip_unclosed_markers("办完了[MEMO_DONE").strip() == "办完了"


# ── 主链路接线 ──

def test_save_notes_runtime开走note_done(monkeypatch):
    loop.AGENT_FLAGS["agent_loop_chat"] = True
    calls = []

    async def _fake_exec(tool, payload, cid):
        calls.append((tool, payload, cid))

    monkeypatch.setattr(chat_tools, "_execute_note_tool", _fake_exec)
    try:
        asyncio.run(chat_service._save_phone_desktop_notes(
            11, "办完了[CAL_DONE]面试新生[/CAL_DONE][MEMO_DONE]明早热月饼[/MEMO_DONE]",
        ))
    finally:
        loop.AGENT_FLAGS["agent_loop_chat"] = True
    assert [c[0] for c in calls] == ["note_done", "note_done"]
    assert calls[0][1]["type"] == "calendar" and calls[0][1]["match"] == "面试新生"
    assert calls[1][1]["type"] == "memo" and calls[1][1]["match"] == "明早热月饼"
    assert calls[0][1]["status"] == "done" and calls[0][1]["character_id"] == 11


def test_save_notes_flag关直改状态(monkeypatch):
    loop.AGENT_FLAGS["agent_loop_chat"] = False
    seen = []

    async def _fake_mark(cid, note_type, match_text, status="done"):
        seen.append((cid, note_type, match_text, status))
        return {"ok": True, "message": "ok"}

    monkeypatch.setattr(chat_tools, "_mark_note_status", _fake_mark)
    try:
        asyncio.run(chat_service._save_phone_desktop_notes(11, "[CAL_DONE]面试新生[/CAL_DONE]"))
    finally:
        loop.AGENT_FLAGS["agent_loop_chat"] = True
    assert seen == [(11, "calendar", "面试新生", "done")]


def test_无标记不触发完成动作(monkeypatch):
    calls = []

    async def _fake_exec(tool, payload, cid):
        calls.append(tool)

    monkeypatch.setattr(chat_tools, "_execute_note_tool", _fake_exec)
    try:
        asyncio.run(chat_service._save_phone_desktop_notes(11, "纯文本无标记"))
    finally:
        loop.AGENT_FLAGS["agent_loop_chat"] = True
    assert calls == []


# ── 工具登记 + 真执行 ──

def test_note_done_工具登记_本地能力无权限门禁():
    spec = agent_tools.get_tool("note_done")
    assert spec is not None
    assert spec.action_type == "NOTE_DONE"
    assert spec.scope is None
    assert spec.ask_auto_allow is False
    assert spec.execute is not None
    assert agent_tools.get_tool_by_action("NOTE_DONE").name == "note_done"


@pytest.mark.slow
def test_note_done_真库按关键词改状态(monkeypatch, tmp_path):
    """真执行：命中关键词 → 该行 status=done；命中不到 → ok=False 且状态不变。"""
    db_path = tmp_path / "g4.db"
    engine = clone_engine(db_path)
    fac = make_session_factory(engine)
    monkeypatch.setattr(dbmod, "async_session_factory", fac)
    monkeypatch.setattr(chat_tools, "async_session_factory", fac)
    from app.agent.tool_runner import execute_tool

    async def _seed():
        async with fac() as db:
            db.add(User(id=9, username="g4u9", nickname="g4"))
            await db.flush()
            ch = AICharacter(user_id=9, name="小艾")
            db.add(ch)
            await db.flush()
            db.add(CalendarNote(character_id=ch.id, note_date="2026-09-24",
                                note_text="轩明天面试新生", author="我"))
            await db.commit()
            return ch.id

    cid = asyncio.run(_seed())
    spec = agent_tools.get_tool("note_done")

    async def _status():
        async with fac() as db:
            row = (await db.execute(
                __import__("sqlalchemy").select(CalendarNote).where(CalendarNote.character_id == cid)
            )).scalars().all()
            return [r.status for r in row]

    assert asyncio.run(_status()) == ["active"]
    # 命中：关键词片段匹配 → done
    out = asyncio.run(execute_tool(spec, {"character_id": cid, "type": "calendar",
                                          "match": "面试", "status": "done"},
                                 user_id=9, character_id=cid))
    assert out["status"] == "ok"
    assert out["result"]["ok"] is True
    assert asyncio.run(_status()) == ["done"]
    # 命中不到：明确失败、状态不变
    miss = asyncio.run(execute_tool(spec, {"character_id": cid, "type": "calendar",
                                            "match": "不存在的条目", "status": "done"},
                                   user_id=9, character_id=cid))
    assert miss["status"] == "ok"
    assert miss["result"]["ok"] is False
    assert asyncio.run(_status()) == ["done"]
    # 参数不合法（type 错）：同样明确失败
    bad = asyncio.run(execute_tool(spec, {"character_id": cid, "type": "x",
                                           "match": "面试", "status": "done"},
                                  user_id=9, character_id=cid))
    assert bad["result"]["ok"] is False
    # 备忘侧：同样可标记
    async def _seed_memo():
        async with fac() as db:
            db.add(MemoNote(character_id=cid, text="明早热月饼", author="我"))
            await db.commit()

    asyncio.run(_seed_memo())
    ok = asyncio.run(execute_tool(spec, {"character_id": cid, "type": "memo",
                                        "match": "热月饼", "status": "done"},
                               user_id=9, character_id=cid))
    assert ok["result"]["ok"] is True

    async def _memo_status():
        from sqlalchemy import select as _sel
        async with fac() as db:
            row = (await db.execute(_sel(MemoNote).where(MemoNote.character_id == cid))).scalars().all()
            return [r.status for r in row]

    assert asyncio.run(_memo_status()) == ["done"]
    engine.sync_engine.dispose()
