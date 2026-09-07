# -*- coding: utf-8 -*-
"""G 前瞻 extractor 放宽口径 + 置信标记（2026-09-07，A 方案）。

覆盖：
- _parse_intent_line 解析：旧 4 段格式默认 confidence=medium（向后兼容）；
- _parse_intent_line 解析：新 5 段格式正确捕获 confidence；
- upsert_intent 把 confidence 写进 cue_terms_json 的 dict 包装（`{confidence, terms}`）；
- upsert_intent 非法 confidence 回落 medium；
- match_cue_intents 向后兼容旧 list 格式 + 解析新 dict 格式；
- extractor 便车：低置信 INTENT 写出（放宽口径）；观测事件带 confidence；旧格式也仍写出。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio
import json
import os
import tempfile

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.memory import extractor
from app.models.memory import ProspectiveIntent


@pytest.fixture()
def pi_db(monkeypatch):
    """临时库：create_all + 把 database / prospective_intent / extractor / facts 的工厂指向临时工厂。"""
    tmp = tempfile.mkdtemp(prefix="ariadne_g_relax_")
    engine = create_async_engine(f"sqlite+aiosqlite:///{os.path.join(tmp, 't.db')}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        from app.models.character import AICharacter
        from app.models.user import User
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add(User(id=1, username="u1", nickname="用户"))
            db.add(AICharacter(id=11, user_id=1, name="酱", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_init())
    import app.db.database as db_mod
    import app.scheduling.prospective_intent as pi
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(pi, "async_session_factory", factory)
    monkeypatch.setattr(extractor, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _rows_of(factory):
    async def _run():
        async with factory() as db:
            rows = (await db.execute(select(ProspectiveIntent).order_by(ProspectiveIntent.id))).scalars().all()
            return [(r.id, r.content, r.kind, r.status, r.cue_terms_json) for r in rows]
    return asyncio.run(_run())


# ────── _parse_intent_line 纯函数解析（不依赖 DB）────────────────────────


def test_parse_intent_line_old_4_fields_default_medium():
    """旧 4 段格式（无第 5 段）默认 confidence=medium，向后兼容。"""
    raw = "INTENT: 下周末带你去吃火锅 | promise | 2026-09-12~2026-09-13 | 火锅,周末"
    pi = extractor._parse_intent_line(raw)
    assert pi is not None
    assert pi["content"] == "下周末带你去吃火锅"
    assert pi["kind"] == "promise"
    assert pi["cue_terms"] == ["火锅", "周末"]
    assert pi["confidence"] == "medium"  # 旧格式默认


def test_parse_intent_line_new_5_fields_with_confidence():
    """新 5 段格式正确捕获 confidence（low/medium/high）。"""
    raw = "INTENT: 樱花开了提醒用户拍照 | cue | 无 | 樱花,拍照 | low"
    pi = extractor._parse_intent_line(raw)
    assert pi is not None
    assert pi["confidence"] == "low"

    raw = "INTENT: 周一开会 | promise | 2026-09-08~2026-09-08 | 无 | high"
    pi = extractor._parse_intent_line(raw)
    assert pi is not None
    assert pi["confidence"] == "high"


def test_parse_intent_line_invalid_confidence_falls_back_to_medium():
    """非法置信（不在白名单）回落 medium，不抛错。"""
    raw = "INTENT: 啥时候 | promise | 无 | 无 | banana"
    pi = extractor._parse_intent_line(raw)
    assert pi is not None
    assert pi["confidence"] == "medium"  # 非法回落


# ────── upsert_intent 把 confidence 写进 cue_terms_json dict 包装 ─────────


def test_upsert_intent_confidence_written_in_dict(pi_db):
    """confidence 写入 cue_terms_json 的 dict 包装（`{confidence, terms}`）。"""
    from app.scheduling.prospective_intent import upsert_intent

    pid = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content="下周末吃火锅",
        kind="promise", due_end=None,  # 没时间
        cue_terms=["火锅", "周末"], source_message_id=900,
        confidence="high",
    ))
    assert pid is not None
    rows = _rows_of(pi_db)
    assert len(rows) == 1
    cid, content, kind, status, cue_json = rows[0]
    payload = json.loads(cue_json)
    assert payload == {"confidence": "high", "terms": ["火锅", "周末"]}


def test_upsert_intent_invalid_confidence_falls_back_to_medium(pi_db):
    """upsert_intent 非法 confidence 回落 medium。"""
    from app.scheduling.prospective_intent import upsert_intent

    pid = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content="啥时候都行",
        kind="promise", due_end=None,
        cue_terms=["啥时候"], source_message_id=901,
        confidence="banana",
    ))
    assert pid is not None
    rows = _rows_of(pi_db)
    payload = json.loads(rows[0][4])
    assert payload["confidence"] == "medium"  # 回落


def test_upsert_intent_default_medium_when_no_confidence_arg(pi_db):
    """upsert_intent 不传 confidence 时默认 medium（向后兼容旧调用方）。"""
    from app.scheduling.prospective_intent import upsert_intent

    pid = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content="改天吧",
        kind="promise", due_end=None,
        cue_terms=["改天"], source_message_id=902,
    ))
    assert pid is not None
    rows = _rows_of(pi_db)
    payload = json.loads(rows[0][4])
    assert payload["confidence"] == "medium"
    assert payload["terms"] == ["改天"]


# ────── match_cue_intents 向后兼容旧 list + 解析新 dict ──────────────────


def test_match_cue_intents_backward_compat_old_list(pi_db, monkeypatch):
    """match_cue_intents 旧 list 格式 cue_terms_json 仍能匹配（向后兼容）。"""
    from app.models.memory import ProspectiveIntent as PI
    from app.scheduling.prospective_intent import match_cue_intents

    async def _seed():
        async with pi_db() as db:
            db.add(PI(user_id=1, character_id=11, content="樱花开了叫我",
                      kind="cue", cue_terms_json=json.dumps(["樱花", "开花"], ensure_ascii=False),
                      status="pending", source_message_id=910))
            await db.commit()
    asyncio.run(_seed())

    # 拦截 LLM（fail-open 兜底）
    async def _boom(*a, **k):
        raise AssertionError("match_cue_intents 不应调用 LLM")
    monkeypatch.setattr("app.agent.llm_client.chat_completion", _boom)

    hits = asyncio.run(match_cue_intents(11, "我看见樱花开了"))
    assert len(hits) == 1


def test_match_cue_intents_parses_new_dict(pi_db, monkeypatch):
    """match_cue_intents 新 dict 格式 cue_terms_json 正确提取 terms。"""
    from app.models.memory import ProspectiveIntent as PI
    from app.scheduling.prospective_intent import _loads_cue_terms
    from app.scheduling.prospective_intent import match_cue_intents

    payload = json.dumps({"confidence": "low", "terms": ["桂花", "秋天"]}, ensure_ascii=False)

    async def _seed():
        async with pi_db() as db:
            db.add(PI(user_id=1, character_id=11, content="秋天桂花开了提醒我",
                      kind="cue", cue_terms_json=payload,
                      status="pending", source_message_id=911))
            await db.commit()
    asyncio.run(_seed())

    async def _boom(*a, **k):
        raise AssertionError("match_cue_intents 不应调用 LLM")
    monkeypatch.setattr("app.agent.llm_client.chat_completion", _boom)

    # 直接验 _loads_cue_terms 解析 dict → list
    assert _loads_cue_terms(payload) == ["桂花", "秋天"]

    hits = asyncio.run(match_cue_intents(11, "秋天桂花开了"))
    assert len(hits) == 1


# ────── extractor 便车：低置信 INTENT 也落库 + 观测带 confidence ─────────


def test_extractor_low_confidence_writes_and_observes(pi_db, monkeypatch):
    """放宽口径：低置信 INTENT 也写；观测事件 detail 含 confidence。"""
    events = []

    async def _fake_llm(**kw):
        # 5 段格式 + low 置信（放宽口径模拟 LLM 输出）
        return (
            "USER_INFO: 无\nEVENTS: 无\nPREFERENCES: 无\nBIO: 无\nSTATUS: 无\n"
            "RELATIONSHIP: 无\nSTAGE: 无\nCURATED: 无\n"
            "INTENT: 改天有空再说 | promise | 无 | 改天,回头 | low"
        )

    async def _fake_save(**kw):
        return None

    def _fake_obs(cid, metric, detail, kind=None):
        events.append((cid, metric, detail))

    monkeypatch.setattr(extractor, "llm_call", _fake_llm)
    # save_memory 在 extract_single 内部 from app.memory import，必须打在源模块上
    monkeypatch.setattr("app.memory.save_memory", _fake_save, raising=False)
    monkeypatch.setattr("app.memory.observability.obs_event", _fake_obs)
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "prospective_intent_enabled", True)

    asyncio.run(extractor.extract_single(7, 11, 1, "改天有空再说吧", "嗯嗯", source_id=920))

    # INTENT 落库（放宽口径下"改天"类低置信也写）
    rows = _rows_of(pi_db)
    assert len(rows) == 1
    payload = json.loads(rows[0][4])
    assert payload["confidence"] == "low"
    assert payload["terms"] == ["改天", "回头"]

    # 观测事件带 confidence
    hit = [e for e in events if e[1] == "prospective_intent_extract"]
    assert len(hit) == 1
    assert hit[0][2]["written"] is True
    assert hit[0][2]["confidence"] == "low"
    assert hit[0][2]["kind"] == "promise"


def test_extractor_old_4_fields_still_parses_with_default_medium(pi_db, monkeypatch):
    """向后兼容：旧 4 段格式 INTENT 仍写出，默认 confidence=medium。"""
    async def _fake_llm(**kw):
        return (
            "USER_INFO: 无\nEVENTS: 无\nPREFERENCES: 无\nBIO: 无\nSTATUS: 无\n"
            "RELATIONSHIP: 无\nSTAGE: 无\nCURATED: 无\n"
            "INTENT: 下周带你吃火锅 | promise | 2026-09-13~2026-09-13 | 火锅,周末"
            # 旧 4 段格式：缺第 5 段（confidence）→ 默认 medium
        )

    async def _fake_save(**kw):
        return None

    monkeypatch.setattr(extractor, "llm_call", _fake_llm)
    # save_memory 在 extract_single 内部 from app.memory import，必须打在源模块上
    monkeypatch.setattr("app.memory.save_memory", _fake_save, raising=False)
    monkeypatch.setattr("app.memory.observability.obs_event", lambda *a, **k: None)
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "prospective_intent_enabled", True)

    asyncio.run(extractor.extract_single(7, 11, 1, "下周带你吃火锅", "好呀", source_id=921))

    rows = _rows_of(pi_db)
    assert len(rows) == 1
    payload = json.loads(rows[0][4])
    assert payload == {"confidence": "medium", "terms": ["火锅", "周末"]}