# -*- coding: utf-8 -*-
"""A50 迁移守卫（文本/主题语义）：相似合并已被「证据作用域写入重放键」取代。

返工2（2026-10-25）：按主审 `primary_review_rework1/REVIEW.md` 修正——**行为守卫只认真实新建路径产生的带键行**。
旧写法先用 `_seed_legacy` 插 `write_key=NULL` 的旧行，旧行不参与键匹配，所以即使身份维度丢失，守卫也不会红（主审外置
探针三例 DID NOT RAISE）。现在每例都：真实 `save_memory` 先建 A（带重放键）→ 再建 B（**只差一个身份维度**）→
断言两行独立、归属正确、A 行逐字段未变。变异测试在建 A **之前**替换 `write_identity.replay_key`（内存副本，磁盘字节不动），
然后跑**完全同一 oracle**，必须收到 AssertionError；未变异时同一 oracle 绿。

保留的旧行兼容例：`test_旧行无重放键不参与新证据键匹配`（旧行为兼容，不冒充新证据）。
"""
import ast
import asyncio
import os
from datetime import timedelta

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

import app.memory.service as svc
import app.memory.write_identity as wid

WRITE_PY = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "app", "memory")) + os.sep + "write.py"

SAVE_SIG = ["user_id", "character_id", "memory_type", "content", "title", "importance",
            "sub_type", "source", "related_memory_id", "source_id", "group_id", "scope",
            "skip_dedup", "speaker_id", "speaker_type", "epistemic_status", "reliability",
            "chain_id", "parent_id", "node_type", "derived_from_ids"]
PREFILTER = "_prefilter_memory_write"
BODY = "阳台上的薄荷又长高了一截"
SIMILAR = "阳台上的薄荷又长高了一大截"          # 高相似、但不是同一证据


def _tree():
    with open(WRITE_PY, encoding="utf-8") as fh:
        return ast.parse(fh.read())


def _fn(tree, name):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError("write.py 里找不到 %s" % name)


def _mutant_key_factory(drop):
    """真实 replay_key 的内存副本：只去掉给定字段，其余编码逐字一致（供变异探针复用）。"""
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
        self.promote = []
        self.hooks = []


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env()
    engine = clone_engine(tmp_path / "a50_identity.db")
    factory = make_session_factory(engine)

    async def _seed_base():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add_all([User(id=1, username="a50_r2_u1", nickname="A50 返工2 用户1"),
                        User(id=2, username="a50_r2_u2", nickname="A50 返工2 用户2")])
            await db.commit()
        async with factory() as db:
            db.add_all([AICharacter(id=101, user_id=1, name="A50 角色101"),
                        AICharacter(id=102, user_id=1, name="A50 角色102"),
                        AICharacter(id=201, user_id=2, name="A50 角色201")])
            await db.commit()
    asyncio.run(_seed_base())

    async def _embed(text):
        e.embeds.append(text)
        return [0.0] * 8

    async def _add(**kw):
        e.add_calls.append(kw)

    def _receipt(character_id, memory_id, action, *, reason="", detail=None):
        e.receipts.append({"character_id": character_id, "memory_id": memory_id,
                           "action": action, "reason": reason, "detail": detail})

    async def _hook(**_kw):
        e.hooks.append(_kw)

    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "add_memory", _add)
    monkeypatch.setattr(svc, "bm25_invalidate", lambda *_a, **_kw: None)

    from app.memory import core as core_mod
    from app.memory import receipt as receipt_mod
    from app.memory import write as write_mod
    monkeypatch.setattr(receipt_mod, "emit_memory_receipt", _receipt)
    monkeypatch.setattr(write_mod, "_run_created_memory_hooks", _hook)
    monkeypatch.setattr(core_mod, "maybe_promote_core", lambda *a, **k: e.promote.append(a) or None)

    e.engine, e.factory, e.db_path = engine, factory, tmp_path / "a50_identity.db"
    yield e
    asyncio.run(engine.dispose())


def _rows():
    from app.models.memory import Memory

    async def _run():
        async with svc.async_session_factory() as db:
            return (await db.execute(select(Memory).order_by(Memory.id))).scalars().all()
    return asyncio.run(_run())


def _seed_legacy(mid, **kw):
    """旧行兼容例用：显式 write_key=NULL 的历史行（不参与新证据键匹配）。"""
    from app.models.memory import Memory
    from app.memory.service import _now_naive

    base = dict(user_id=1, character_id=101, memory_type="event", content=BODY, source="chat",
                source_id=900, status="active", importance=40.0, strength_days=1.0,
                review_count=0, derived_from_ids="[]", write_key=None,
                created_at=_now_naive() - timedelta(hours=2))
    base.update(kw)

    async def _run():
        async with svc.async_session_factory() as db:
            db.add(Memory(id=mid, **base))
            await db.commit()
    asyncio.run(_run())


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


def _oracle_two_rows(a_kw, b_kw, *, expect_user=1, expect_character=101):
    """同一 oracle：A、B 必须独立成两行，归属正确，且 A 行内容/重要度逐字未变。"""
    a = _save(**a_kw)
    snapshot = (a.id, a.content, a.importance)
    b = _save(**b_kw)
    rows = _rows()
    assert len(rows) == 2, f"必须独立成两行，实际 {len(rows)}"
    assert b.id != a.id, "必须新建一条，而不是复用 A"
    by_id = {r.id: r for r in rows}
    assert by_id[snapshot[0]].content == snapshot[1]
    assert by_id[snapshot[0]].importance == snapshot[2], "A 行不得被 B 强化/改写"
    assert by_id[b.id].user_id == expect_user
    assert by_id[b.id].character_id == expect_character
    return a, b


# ------------------------------------------------------------------ 结构不变量
def test_save_memory公共签名二十一个形参逐字未变():
    names = [a.arg for a in _fn(_tree(), "save_memory").args.args]
    assert names == SAVE_SIG, names


def test_prefilter仍是模块级私有协程且形参集合固定():
    fn = _fn(_tree(), PREFILTER)
    assert isinstance(fn, ast.AsyncFunctionDef)
    assert [a.arg for a in fn.args.args] == [], "prefilter 必须全 keyword-only"
    assert sorted(a.arg for a in fn.args.kwonlyargs) == sorted(
        ["db", "content", "character_id", "memory_type", "sub_type", "source", "source_id",
         "emit_memory_receipt", "ACTION_REJECT"])


def test_prefilter内零commit且不写库():
    src = ast.unparse(_fn(_tree(), PREFILTER))
    for forbidden in (".commit(", "db.add(", ".flush("):
        assert forbidden not in src, f"过滤阶段不得出现 {forbidden}"


def test_块内局部import仍随块未上提():
    body = ast.unparse(_fn(_tree(), PREFILTER))
    assert "dialogue_filter" in body or "looks_like_raw_dialogue" in body


# ------------------------------------------------------------------ 真实新建路径的行为守卫
def test_高相似正文不再被吞并而独立成行(env):
    """drop=("content",) 的变异必须让本守卫判红（主审外置探针复用此 nodeid）。"""
    _oracle_two_rows({"content": BODY}, {"content": SIMILAR})


def test_同正文不同来源不吞并(env):
    """drop=("source","source_id") 的变异必须让本守卫判红（memo/manual 同为 UNVERIFIED，仅来源名不同）。"""
    _oracle_two_rows({"source": "memo", "source_id": 900},
                     {"source": "manual", "source_id": 900})


def test_同正文同来源不同事件不吞并(env):
    _oracle_two_rows({"source": "chat", "source_id": 900},
                     {"source": "chat", "source_id": 901})


def test_跨角色同正文不吞并(env):
    """drop=("character_id",) 的变异必须让本守卫判红。"""
    _oracle_two_rows({"character_id": 101}, {"character_id": 102}, expect_character=102)


def test_跨用户同正文不吞并且归属正确(env):
    _oracle_two_rows({"user_id": 1, "character_id": 101},
                     {"user_id": 2, "character_id": 201}, expect_user=2, expect_character=201)


def test_完全相同证据重放返回同id且零副作用(env):
    first = _save(content=BODY, source="chat", source_id=900, importance=40)
    env.receipts.clear(); env.add_calls.clear(); env.embeds.clear(); env.promote.clear(); env.hooks.clear()
    again = _save(content=BODY, source="chat", source_id=900, importance=3)
    rows = _rows()
    assert again.id == first.id and len(rows) == 1, "同证据重放必须命中同一行"
    assert rows[0].importance == 40.0, "重放不得强化/抬重要度"
    assert rows[0].derived_from_ids in (None, "", "[]"), "重放不得合并 derived"
    assert env.receipts == [] and env.add_calls == [] and env.embeds == []
    assert env.promote == [] and env.hooks == []


def test_skip_dedup为真时同证据也新建(env):
    a = _save(content=BODY)
    b = _save(content=BODY, skip_dedup=True)
    assert b.id != a.id and len(_rows()) == 2


def test_缺source或source_id时不得伪造重放(env):
    """两点都缺 ⇒ replay_key 为 None ⇒ 每次都是新建；两条相同正文也必须是两行。"""
    a = _save(content=BODY, source=None, source_id=None)
    b = _save(content=BODY, source=None, source_id=None)
    assert a.id != b.id
    assert len(_rows()) == 2, "没有证据身份就不得判定为重放"
    assert a.write_key is None and b.write_key is None


@pytest.mark.parametrize("field", ["is_pinned", "is_locked", "is_archived"])
def test_受保护行不被相似新写改动(env, field):
    _seed_legacy(1, **{field: True})
    _save(content=SIMILAR, source_id=901)
    rows = _rows()
    old = [r for r in rows if r.id == 1][0]
    assert len(rows) == 2
    assert old.importance == 40.0 and getattr(old, field) is True


def test_旧行无重放键不参与新证据键匹配(env):
    """旧行兼容：write_key=NULL 的历史行不会被当作重放目标（保留旧数据不变更）。"""
    _seed_legacy(1)
    new = _save(content=BODY, source="chat", source_id=900)
    assert new.id != 1 and len(_rows()) == 2
    assert _rows()[0].write_key is None


def test_新建路径回执按动作类型各一次且指向新行(env):
    out = _save(content=BODY)
    actions = [r["action"] for r in env.receipts]
    assert actions.count("create") == 1, f"新建必须恰有一条 create 回执，实际 {actions}"
    for action in set(actions):
        assert actions.count(action) <= 1, f"同动作不得重复：{action}×{actions.count(action)}"
    assert all(r["memory_id"] == out.id for r in env.receipts)
    assert all(r["character_id"] == 101 for r in env.receipts)
    assert actions, "新建必须至少发一条回执"


def test_新建路径嵌入对象被复用未重算(env):
    out = _save(content=BODY)
    assert len(env.embeds) == 1
    assert env.add_calls and env.add_calls[0]["memory_id"] == out.id
    assert env.add_calls[0]["embedding"] is not None


# ------------------------------------------------------------------ 行为变异（同一 oracle 必须红）
def _assert_oracle_red(monkeypatch, drop, a_kw, b_kw, **oracle_kw):
    """在建 A **之前**注入键变异，再跑与未变异完全相同的 oracle，必须收到 AssertionError。"""
    monkeypatch.setattr(wid, "replay_key", _mutant_key_factory(drop))
    with pytest.raises(AssertionError):
        _oracle_two_rows(a_kw, b_kw, **oracle_kw)


def test_变异_丢来源维度会让跨源守卫判红(env, monkeypatch):
    _assert_oracle_red(monkeypatch, ("source", "source_id"),
                       {"source": "memo", "source_id": 900},
                       {"source": "manual", "source_id": 900})


def test_变异_丢事件维度会让不同source_id守卫判红(env, monkeypatch):
    _assert_oracle_red(monkeypatch, ("source_id",),
                       {"source": "chat", "source_id": 900},
                       {"source": "chat", "source_id": 901})


def test_变异_丢角色维度会让跨角色守卫判红(env, monkeypatch):
    _assert_oracle_red(monkeypatch, ("character_id",),
                       {"character_id": 101}, {"character_id": 102}, expect_character=102)


def test_变异_丢正文维度会让相似守卫判红(env, monkeypatch):
    _assert_oracle_red(monkeypatch, ("content",), {"content": BODY}, {"content": SIMILAR})


def _mem(**kw):
    """构造未落库 Memory（仅字段），用于键级变异证明（不冒充端到端）。"""
    from app.models.memory import Memory
    base = dict(user_id=1, character_id=101, memory_type="event", content=BODY, source="chat",
                source_id=900, scope="private", group_id=None, speaker_type=None, speaker_id=1,
                epistemic_status=None, sub_type=None, node_type=None, parent_id=None,
                chain_id=None, related_memory_id=None, derived_from_ids="[]")
    base.update(kw)
    return Memory(**base)


def test_变异_丢用户维度会让键判据判红():
    """端到端跨用户必然同时改变角色归属（合法 FK），故用户维度用键级证明：真键区分、变异键不区分。"""
    a = _mem(user_id=1)
    b = _mem(user_id=2)          # 仅 user_id 不同（同一 character 仅用于键级对照，不落库）
    assert wid.replay_key(a) != wid.replay_key(b), "真键必须区分 user_id"
    mutant = _mutant_key_factory(("user_id",))
    assert mutant(a) == mutant(b), "⇒ 键若丢 user_id，跨用户会被判重放（判据有牙）"


def test_变异_无来源也造键会伪造重放(env, monkeypatch):
    monkeypatch.setattr(wid, "replay_key", lambda memory: _mutant_key_factory(())(memory))
    a = _save(content=BODY, source=None, source_id=None)
    b = _save(content=BODY, source=None, source_id=None)
    assert a.id == b.id, "变异生效：无来源也被判重放"
    assert len(_rows()) == 1, "⇒ 缺证据身份守卫在该变异下会判红（有牙）"


# ------------------------------------------------------------------ 回执边界（commit 先于回执）
def test_新建路径commit先于回执_独立连接可见(env, monkeypatch):
    """在回执被调用的那一刻，用**独立只读连接**查询：行必须已提交可见（commit 先于回执）。"""
    import sqlite3

    from app.memory import receipt as receipt_mod

    visible = []

    def _probe(character_id, memory_id, action, *, reason="", detail=None):
        con = sqlite3.connect(f"file:{env.db_path.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT id FROM memories WHERE id=?", (memory_id,)).fetchone()
        finally:
            con.close()
        visible.append((action, row is not None))

    monkeypatch.setattr(receipt_mod, "emit_memory_receipt", _probe)
    out = _save(content=BODY)
    assert out is not None
    assert visible, "新建路径必须至少在回执阶段被调用一次"
    assert any(action == "create" and ok for action, ok in visible), \
        f"create 回执时该行必须已提交可见，实际 {visible}"


def test_回执抛异常不阻断已提交的新建(env, monkeypatch):
    """新路径实际异常契约：回执异常被逐段静默，已提交行保留、函数正常返回原 memory。"""
    from app.memory import receipt as receipt_mod

    def _boom(*_a, **_kw):
        raise RuntimeError("receipt-boom")

    monkeypatch.setattr(receipt_mod, "emit_memory_receipt", _boom)
    out = _save(content=BODY)
    assert out is not None
    rows = _rows()
    assert len(rows) == 1 and rows[0].id == out.id, "回执异常不得回滚已提交的新建"
