# -*- coding: utf-8 -*-
"""A50 迁移守卫（向量语义）：向量相似度只作检索证据，永不授权合并/强化、永不阻止写入。

返工2 修正（同文本侧）：行为守卫一律**真实 `save_memory` 先建 A（带重放键）再建 B**，各次只改一个身份维度；
变异在**建 A 之前**注入（只改内存副本），同一 oracle 必须判红；未变异时同一 oracle 绿。
"""
import ast
import asyncio
import os

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

import app.memory.service as svc
import app.memory.write_identity as wid

WRITE_PY = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "app", "memory")) + os.sep + "write.py"
BODY = "厨房窗台的罗勒该浇水了"
SIMILAR = "厨房窗台的罗勒该浇水啦"      # 高相似但不是同一证据
SENTINEL = object()


def _tree():
    with open(WRITE_PY, encoding="utf-8") as fh:
        return ast.parse(fh.read())


def _mutant_key_factory(drop):
    import hashlib
    import json

    def _key(memory):
        fields = ("user_id", "character_id", "source", "source_id", "group_id", "scope",
                  "speaker_type", "speaker_id", "epistemic_status", "memory_type", "sub_type",
                  "content", "node_type", "parent_id", "chain_id", "related_memory_id")
        ident = {n: getattr(memory, n, None) for n in fields if n not in drop}
        ident["derived_from_ids"] = sorted(set(json.loads(memory.derived_from_ids or "[]")))
        enc = json.dumps(ident, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(("memory-evidence-v1:" + enc).encode()).hexdigest()

    return _key


class Env:
    def __init__(self):
        self.add_calls = []
        self.embeds = []
        self.receipts = []


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env()
    engine = clone_engine(tmp_path / "a50_vector.db")
    factory = make_session_factory(engine)

    async def _seed_base():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="a50_r2v_u1", nickname="A50 返工2 向量用户"))
            await db.commit()
        async with factory() as db:
            db.add(AICharacter(id=101, user_id=1, name="A50 向量角色"))
            await db.commit()
    asyncio.run(_seed_base())

    async def _embed(text):
        e.embeds.append(text)
        return SENTINEL

    async def _add(**kw):
        e.add_calls.append(kw)

    def _receipt(character_id, memory_id, action, *, reason="", detail=None):
        e.receipts.append({"character_id": character_id, "memory_id": memory_id, "action": action})

    async def _hook(**_kw):
        return None

    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "add_memory", _add)
    monkeypatch.setattr(svc, "bm25_invalidate", lambda *_a, **_kw: None)

    from app.memory import core as core_mod
    from app.memory import receipt as receipt_mod
    from app.memory import write as write_mod
    monkeypatch.setattr(receipt_mod, "emit_memory_receipt", _receipt)
    monkeypatch.setattr(core_mod, "maybe_promote_core", lambda *a, **k: None)
    monkeypatch.setattr(write_mod, "_run_created_memory_hooks", _hook)

    e.engine, e.factory = engine, factory
    yield e
    asyncio.run(engine.dispose())


def _rows():
    from app.models.memory import Memory

    async def _run():
        async with svc.async_session_factory() as db:
            return (await db.execute(select(Memory).order_by(Memory.id))).scalars().all()
    return asyncio.run(_run())


def _save(**kw):
    from app.memory.write import save_memory
    kw.setdefault("user_id", 1)
    kw.setdefault("character_id", 101)
    kw.setdefault("memory_type", "event")
    kw.setdefault("importance", 3)
    kw.setdefault("content", BODY)
    kw.setdefault("source", "chat")
    kw.setdefault("source_id", 900)
    return asyncio.run(save_memory(**kw))


def _oracle_two_rows(a_kw, b_kw):
    a = _save(**a_kw)
    snap = (a.id, a.content, a.importance)
    b = _save(**b_kw)
    rows = _rows()
    assert len(rows) == 2, f"必须独立成两行，实际 {len(rows)}"
    assert b.id != a.id
    by_id = {r.id: r for r in rows}
    assert by_id[snap[0]].content == snap[1]
    assert by_id[snap[0]].importance == snap[2], "A 行不得被相似写入改写/强化"
    return a, b


# ------------------------------------------------------------------ 防回潮（结构）
def test_save_memory体内不得再出现相似查重调用():
    tree = _tree()
    fn = next(n for n in tree.body
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "save_memory")
    body = ast.unparse(fn)
    for banned in ("find_similar_memory", "similar_memory", "_merge_derived"):
        assert banned not in body, f"相似合并/派生合并已被正式禁止，save_memory 不得再引用 {banned}"


# ------------------------------------------------------------------ 行为
def test_相似行存在时仍独立成行(env, monkeypatch):
    """向量相似检索即使返回旧行，也不得授权改写；drop=("content",) 变异必须让本守卫判红。"""
    first = _save(content=BODY)
    second = _save(content=SIMILAR, source_id=901)
    assert second.id != first.id and len(_rows()) == 2


def test_同正文不同memory_type独立(env):
    """drop=("memory_type",) 变异必须让本守卫判红。"""
    _oracle_two_rows({"memory_type": "event"}, {"memory_type": "insight"})


def test_嵌入至多一次且对象原样运到向量写入(env):
    out = _save(content=BODY)
    assert len(env.embeds) == 1, "新建路径只算一次嵌入"
    assert env.add_calls and env.add_calls[0]["memory_id"] == out.id
    assert env.add_calls[0]["embedding"] is SENTINEL, "必须原样复用同一嵌入对象，不得重算"


def test_重放零嵌入零向量零回执(env):
    first = _save(content=BODY)
    env.embeds.clear(); env.add_calls.clear(); env.receipts.clear()
    again = _save(content=BODY)
    assert again.id == first.id
    assert env.embeds == [] and env.add_calls == [] and env.receipts == [], "重放必须零副作用"


def test_嵌入失败仍落库(env, monkeypatch):
    async def _boom(_text):
        raise RuntimeError("embed-boom")

    monkeypatch.setattr(svc, "text_embedding", _boom)
    out = _save(content=BODY)
    assert out is not None and len(_rows()) == 1, "嵌入失败不得阻断落库"
    assert env.add_calls == [], "嵌入失败时不写向量"


def test_向量写入失败仍落库(env, monkeypatch):
    async def _boom(**_kw):
        raise RuntimeError("chroma-boom")

    monkeypatch.setattr(svc, "add_memory", _boom)
    out = _save(content=BODY)
    assert out is not None and len(_rows()) == 1, "向量写入失败只告警，不阻断"
    assert len(env.embeds) == 1


def test_向量写入携带落库id与importance(env):
    out = _save(content=BODY, importance=7)
    assert env.add_calls[0]["memory_id"] == out.id
    assert env.add_calls[0]["importance"] == 7


# ------------------------------------------------------------------ 行为变异（同一 oracle 必须红）
def _assert_oracle_red(monkeypatch, drop, a_kw, b_kw):
    monkeypatch.setattr(wid, "replay_key", _mutant_key_factory(drop))
    with pytest.raises(AssertionError):
        _oracle_two_rows(a_kw, b_kw)


def test_变异_丢正文维度会让相似守卫判红(env, monkeypatch):
    _assert_oracle_red(monkeypatch, ("content",), {"content": BODY}, {"content": SIMILAR})


def test_变异_丢类型维度会让类型守卫判红(env, monkeypatch):
    _assert_oracle_red(monkeypatch, ("memory_type",),
                       {"memory_type": "event"}, {"memory_type": "insight"})
