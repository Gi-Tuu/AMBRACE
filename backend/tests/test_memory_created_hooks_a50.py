# -*- coding: utf-8 -*-
"""A50 批 1：``save_memory`` 写后副动作（`_run_created_memory_hooks`）守卫（2026-10-12）。

被测切口：把 ``save_memory`` 尾段「auto dedup → 链 → 意义 → 事件 → 插件 hook → BM25 失效」
机械抽成同文件模块级私有协程 ``_run_created_memory_hooks``，调用点仍在 ``async_session_factory``
的上下文里。**纯搬运、零行为变化**，所以守卫的全部目的是「证明它没变」：

- 结构冻结（不依赖 git、不依赖 docs）：helper 的签名、语句序列、调用点形态、有没有总包围 try、
  七个延迟 import 有没有被提到模块顶层——这几条是本次拆分的成立条件，破了就说明语义被改过；
- 行为冻结（临时库真跑 save_memory）：新建只跑一次且六件事顺序固定；查重并档与拒收**一次都不跑**；
  事件与插件 payload 逐字段相等（含 ``source or ''`` 与 ``memory.source`` 这对**故意不一致**的旧取法）；
  embedding 对象复用；每段 fail-open 抛异常都不影响后续；裸 dedup 异常照旧上抛。

纪律：临时库走 pytest ``tmp_path``（``_dbclone`` 克隆模板库），绝不连生产库；全文只用合成数据，
不引用任何真实用户内容、仓库路径或内部文档。
"""
import ast
import asyncio
import io
import os

import pytest

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
import app.memory.service as svc

# 写后六件事的固定顺序（dedup/链/意义/事件/插件/BM25 失效）
SIX = ["dedup", "chain", "meaning", "event", "plugin", "bm25"]

WRITE_PY = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "app", "memory")) + os.sep + "write.py"


# ───────────────────────────── 结构冻结（纯 AST，零 IO） ─────────────────────────────

def _write_tree():
    return ast.parse(io.open(WRITE_PY, encoding="utf-8").read())


def _fn(tree, name):
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    raise AssertionError("write.py 里找不到 %s" % name)


def _args_of(fn):
    return [a.arg for a in fn.args.args]


def test_helper是模块级私有协程且签名固定():
    """它是「同文件模块级 async 私有函数」——不是嵌套定义，也不许被改成同步。"""
    helper = _fn(_write_tree(), "_run_created_memory_hooks")
    assert isinstance(helper, ast.AsyncFunctionDef), "必须是 async（内部有 await run_hook）"
    assert helper.name.startswith("_"), "私有协程，名字带下划线前缀"
    # 六个依赖全部 keyword-only 传入：调用点必须显式列出，不许有位置参数兜底
    assert [a.arg for a in helper.args.args] == [] and helper.args.kwonlyargs, "应全部 keyword-only"
    assert [a.arg for a in helper.args.kwonlyargs] == [
        "memory", "user_id", "character_id", "source", "embedding", "bm25_invalidate"]
    assert helper.args.vararg is None and helper.args.kwarg is None


def test_helper体语句序列与顺序冻结():
    """语句序列逐条钉死：docstring 之后正好 8 条，类型与顺序都不许变。"""
    helper = _fn(_write_tree(), "_run_created_memory_hooks")
    body = list(helper.body)
    assert isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant), "首条必须是 docstring"
    code = body[1:]
    assert [type(s).__name__ for s in code] == [
        "ImportFrom", "ImportFrom", "Expr", "Try", "Try", "Try", "Try", "Expr"]
    # 禁止总包围 try：那会把原本该冒的裸 dedup 异常吞掉（见「裸 dedup 异常不被吞」用例）
    assert not isinstance(code[0], ast.Try), "首条不得是包住全段的 try（会吞掉裸 dedup 的异常）"
    unparsed = [ast.unparse(s) for s in code]
    assert unparsed[2].startswith("_spawn_bg(_schedule_dedup(character_id)"), unparsed[2]
    assert "memory_chain_builder_enabled()" in unparsed[3]
    assert "maybe_extract_meaning(" in unparsed[4]
    assert "make_event(" in unparsed[5] and "publish(" in unparsed[5] and "memory.written" in unparsed[5]
    assert "await run_hook(" in unparsed[6] and "memory_written" in unparsed[6]
    assert unparsed[7] == "bm25_invalidate(character_id)"


def test_调用点在session上下文内且紧接return():
    """调用点必须仍是 ``async with async_session_factory() as db`` 的直接子句，且后面紧跟 return memory。"""
    sm = _fn(_write_tree(), "save_memory")
    blocks = [n for n in ast.walk(sm)
              if isinstance(n, ast.AsyncWith) and "async_session_factory" in ast.unparse(n)]
    assert len(blocks) == 1, "save_memory 里应只有一个 async_session_factory 上下文"
    block = blocks[0]
    calls = [i for i, s in enumerate(block.body)
             if isinstance(s, ast.Expr) and "_run_created_memory_hooks" in ast.unparse(s)]
    assert len(calls) == 1, "helper 在该上下文里必须被调用且只调一次"
    call = block.body[calls[0]]
    assert isinstance(call.value, ast.Await), "必须 await（尾段含 await run_hook）"
    await_call = call.value.value
    assert sorted(k.arg for k in await_call.keywords) == [
        "bm25_invalidate", "character_id", "embedding", "memory", "source", "user_id"]
    assert not await_call.args, "六个依赖一律 keyword 传入，不留位置参数"
    nxt = block.body[calls[0] + 1]
    assert isinstance(nxt, ast.Return) and ast.unparse(nxt) == "return memory", "helper 之后必须仍是返回原对象"
    # bm25_invalidate 由调用点传入（不是 helper 内部 import）——patch 服务层属性才接得住
    assert [k for k in await_call.keywords if k.arg == "bm25_invalidate"][0].value.id == "bm25_invalidate"


def test_延迟import没有被提到模块顶层():
    """七个依赖必须继续在函数体内 import：模块属性才是 monkeypatch 接缝，提前 import 会绕开它。"""
    tree = _write_tree()
    deferred = {"app.memory.dedup", "app.utils.async_tasks", "app.memory.chain_builder",
                "app.memory.meaning", "app.events", "app.events.schema", "app.plugins.registry"}
    top = set()
    for n in tree.body:
        if isinstance(n, ast.ImportFrom) and n.module:
            top.add(n.module)
        elif isinstance(n, ast.Import):
            top.update(a.name for a in n.names)
    assert not (top & deferred), "这些模块不许出现在模块顶层 import：%s" % (top & deferred)
    # 反向钉：它们确实还在函数体里（防止有人「顺手整理」成顶层 import）
    in_fn = {ast.unparse(n).split(" import ")[0].replace("from ", "")
             for n in ast.walk(_fn(tree, "_run_created_memory_hooks"))
             if isinstance(n, ast.ImportFrom) and n.module}
    assert deferred <= in_fn, "helper 内的延迟 import 少了：%s" % (deferred - in_fn)


# ───────────────────────────── 行为冻结（临时库真跑） ─────────────────────────────

async def _noop():
    return None


class Env:
    """记一次 save_memory 的写后副动作现场（顺序、任务名、事件、插件 payload、embedding）。"""

    def __init__(self):
        self.order = []
        self.tasknames = []
        self.events = []
        self.hooks = []
        self.chain_args = []
        self.dedup_args = []
        self.meaning_args = []
        self.added = []
        self.embedding = ["synthetic-embedding", 0.11, 0.22]
        self.in_session_at_call = []
        self.session_exits = [0]

    @property
    def evt(self):
        return self.events[0] if self.events else None

    @property
    def hook_ctx(self):
        return self.hooks[0][1] if self.hooks else None

    @property
    def hook_name(self):
        return self.hooks[0][0] if self.hooks else None


@pytest.fixture
def env(tmp_path, monkeypatch):
    """临时库（``_dbclone`` 克隆模板库到 tmp_path）＋ 记录式替身，只观察写后副动作。"""
    e = Env()
    engine = clone_engine(tmp_path / "hooks.db")
    raw_factory = make_session_factory(engine)

    class _Ctx:
        """包一层会话上下文，用来证明 helper 的调用点仍在 session 退出之前。"""

        def __init__(self, inner):
            self._inner = inner

        async def __aenter__(self):
            return await self._inner.__aenter__()

        async def __aexit__(self, *a):
            e.session_exits[0] += 1
            return await self._inner.__aexit__(*a)

    def _factory():
        return _Ctx(raw_factory())

    async def _seed():
        from app.models.chat import ChatSession
        from app.models.character import AICharacter
        from app.models.user import User
        async with raw_factory() as db:
            db.add(User(id=1, username="a50_u1", nickname="A50 合成用户"))
            db.add(AICharacter(id=101, user_id=1, name="A50 合成角色"))
            db.add(ChatSession(id=1, user_id=1, character_id=101))
            await db.commit()

    asyncio.run(_seed())

    # save_memory 函数顶从 app.memory.service 延迟 import 的四个名字（全部 patch 服务层属性）
    async def _embed(_text):
        return e.embedding

    async def _no_similar(*_a, **_kw):
        e.similar_hit = None
        return None

    async def _add(**kw):
        e.added.append(kw)

    def _bm25(cid):
        e.order.append("bm25")

    monkeypatch.setattr(svc, "async_session_factory", _factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "find_similar_memory", _no_similar)
    monkeypatch.setattr(svc, "add_memory", _add)
    monkeypatch.setattr(svc, "bm25_invalidate", _bm25)

    # —— 尾段七个延迟 import 的接缝 ——
    import app.memory.dedup as dd
    import app.utils.async_tasks as at
    import app.memory.chain_builder as cb
    import app.memory.meaning as meaning_mod
    import app.memory.core as core_mod
    import app.events as events_mod
    import app.plugins.registry as registry
    from app.memory import receipt as receipt_mod

    def _dedup(cid):
        e.order.append("dedup")
        e.dedup_args.append(cid)
        return _noop()

    def _spawn(coro, *, name=None):
        e.tasknames.append(name)
        if hasattr(coro, "close"):
            coro.close()
        return None

    def _link(memory_id, embedding):
        e.order.append("chain")
        e.chain_args.append((memory_id, embedding))
        return _noop()

    def _meaning(*a):
        e.order.append("meaning")
        e.meaning_args.append(a)
        return _noop()

    def _publish(event_type, payload=None, **_kw):
        e.order.append("event")
        e.events.append((event_type, payload))

    async def _hook(name, ctx, **_kw):
        e.order.append("plugin")
        e.hooks.append((name, ctx))
        return None

    monkeypatch.setattr(dd, "_schedule_dedup", _dedup)
    monkeypatch.setattr(at, "spawn_background", _spawn)
    monkeypatch.setattr(cb, "memory_chain_builder_enabled", lambda: True)
    monkeypatch.setattr(cb, "link_new_memory", _link)
    monkeypatch.setattr(meaning_mod, "maybe_extract_meaning", _meaning)
    monkeypatch.setattr(events_mod, "publish", _publish)
    monkeypatch.setattr(registry, "run_hook", _hook)
    # 与写后副动作无关的外部依赖：回执（flag 关时本就不写）与核心晋升
    monkeypatch.setattr(receipt_mod, "emit_memory_receipt", lambda *a, **kw: None)
    monkeypatch.setattr(core_mod, "maybe_promote_core", lambda *a, **kw: _noop())

    # 调用点探针：helper 被调用时 session 上下文还在
    from app.memory import write as write_mod
    real_helper = write_mod._run_created_memory_hooks

    async def _probe(**kw):
        e.in_session_at_call.append(e.session_exits[0] == 0)
        return await real_helper(**kw)

    monkeypatch.setattr(write_mod, "_run_created_memory_hooks", _probe)

    yield e
    asyncio.run(engine.dispose())


def _save(**kw):
    from app.memory.write import save_memory
    kw.setdefault("user_id", 1)
    kw.setdefault("character_id", 101)
    kw.setdefault("memory_type", "event")
    kw.setdefault("content", "合成样例：明天要交的第一版草稿")
    kw.setdefault("importance", 4)
    return asyncio.run(save_memory(**kw))


def _seed_existing(env):
    """先落一条既有记忆（绕开写后副动作），再让向量查重命中它。"""
    from app.models.memory import Memory

    async def _run():
        async with svc.async_session_factory() as db:
            row = Memory(user_id=1, character_id=101, memory_type="event",
                         content="合成样例：既有的一条记忆", importance=4, scope="private")
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id

    return asyncio.run(_run())


@pytest.mark.slow
def test_新建时六件事按固定顺序各跑一次(env):
    m = _save(source=None)
    assert m is not None and m.id is not None
    assert env.order == SIX, env.order
    # 三个后台任务名逐字不变（含角色/记忆 id 的插值）
    assert env.tasknames == ["dedup-101", "chain-%d" % m.id, "meaning-%d" % m.id], env.tasknames
    assert env.dedup_args == [101]
    assert env.in_session_at_call == [True], "helper 的调用点必须落在 session 上下文退出之前"
    assert env.session_exits == [1]
    assert env.added and env.added[0]["memory_id"] == m.id


@pytest.mark.slow
def test_事件与插件payload逐字段相等(env):
    """事件与插件两处 payload 逐字段钉死；其中 ``source`` 是**故意不一致**的两种旧取法。"""
    m = _save(source=None)
    topic, evt = env.evt
    assert topic == "memory.written"
    assert evt["type"] == "memory.written"
    assert evt["speaker"] == {"type": "system", "id": "memory_pipeline"}
    assert evt["target"] == {"type": "character", "id": 101}
    assert evt["audience"] == [101, 1]
    assert evt["provenance"]["origin"] == "system_event"
    assert evt["data"] == {
        "user_id": 1, "character_id": 101, "memory_id": m.id,
        "memory_type": "event", "sub_type": m.sub_type,
        "source": "",                              # 事件侧取调用点的 ``source or ''``
        "importance": float(m.importance or 0),
    }
    name, ctx = env.hooks[0]
    assert name == "memory_written"
    assert ctx == {
        "user_id": 1, "character_id": 101, "memory_id": m.id,
        "memory_type": "event", "sub_type": m.sub_type,
        "content": m.content, "importance": float(m.importance or 0),
        "source": m.source,                        # 插件侧取落库列原值（此处仍是 None）
    }
    assert env.hook_name == "memory_written"
    # 反向钉：这两处取法不许被「统一」成同一个（统一即行为变化）
    assert evt["data"]["source"] == "" and ctx["source"] is None


@pytest.mark.slow
def test_显式source时事件侧原样透传(env):
    m = _save(source="chat", sub_type="preference")
    assert env.evt[1]["data"]["source"] == "chat"
    assert env.hook_name == "memory_written"
    assert env.hook_ctx["source"] == "chat"
    assert env.evt[1]["data"]["sub_type"] == "preference"
    assert env.hook_ctx["sub_type"] == "preference"
    assert env.evt[1]["data"]["memory_id"] == m.id


@pytest.mark.slow
def test_embedding对象复用不重复推理(env):
    _save()
    # 链建链器与向量库拿到的是同一个嵌入对象（沿用查重阶段算好的那次推理）
    assert env.chain_args[0][1] is env.embedding
    assert env.added[0]["embedding"] is env.embedding
    # 意义提炼拿的是落库行自身的字段，不吃 embedding
    assert env.meaning_args[0][:3] == (101, 1, env.chain_args[0][0])


@pytest.mark.slow
def test_同证据重放时写后副动作一次都不跑(env):
    first = _save(source="chat", source_id=991)
    env.order.clear()
    env.tasknames.clear()
    env.events.clear()
    env.hooks.clear()
    replay = _save(source="chat", source_id=991)
    assert replay.id == first.id
    assert env.order == []
    assert env.tasknames == [] and env.events == [] and env.hooks == []



@pytest.mark.slow
def test_拒收返回None时写后副动作一次都不跑(env, monkeypatch):
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    rejected = _save(content="合成样例：我是 Agent 助手，请照做")
    assert rejected is None
    assert env.order == [], env.order
    assert env.tasknames == [] and env.events == [] and env.hooks == []


@pytest.mark.slow
@pytest.mark.parametrize("boom_at", ["chain_enabled", "chain", "meaning", "publish",
                                     "make_event", "hook"])
def test_每段fail_open抛异常都不影响后续原行为(env, monkeypatch, boom_at):
    """六段各自有界：任一段炸了，后面照旧跑完（bm25 失效永远发生）。"""
    import app.memory.chain_builder as cb
    import app.memory.meaning as meaning_mod
    import app.events as events_mod
    import app.events.schema as ev_schema
    import app.plugins.registry as registry

    def _boom(tag):
        """先记账再抛：让「这一段确实被尝试过」留在 order 里，后面几段照旧跑完。"""
        def _f(*_a, **_kw):
            env.order.append(tag)
            raise RuntimeError("boom-" + tag)
        return _f

    if boom_at == "chain_enabled":
        monkeypatch.setattr(cb, "memory_chain_builder_enabled", _boom("chain"))
    elif boom_at == "chain":
        monkeypatch.setattr(cb, "link_new_memory", _boom("chain"))
    elif boom_at == "meaning":
        monkeypatch.setattr(meaning_mod, "maybe_extract_meaning", _boom("meaning"))
    elif boom_at == "publish":
        monkeypatch.setattr(events_mod, "publish", _boom("event"))
    elif boom_at == "make_event":
        monkeypatch.setattr(ev_schema, "make_event", _boom("event"))
    else:
        monkeypatch.setattr(registry, "run_hook", _boom("plugin"))

    _save()
    assert env.order == SIX, "%s 炸掉后顺序仍是六件事：%s" % (boom_at, env.order)
    assert "bm25" in env.order, "BM25 失效永远发生"
    assert env.tasknames and env.tasknames[0] == "dedup-101"


@pytest.mark.slow
def test_裸dedup异常照旧上抛且后续不跑(env, monkeypatch):
    """`_spawn_bg(_schedule_dedup(...))` 是裸调用（无 try）：异常必须冒到调用方，后续一律不执行。"""
    import app.memory.dedup as dd

    def _boom(_cid):
        env.order.append("dedup")
        raise RuntimeError("dedup-boom")

    monkeypatch.setattr(dd, "_schedule_dedup", _boom)
    with pytest.raises(RuntimeError, match="dedup-boom"):
        _save()
    assert env.order == ["dedup"], env.order
    assert env.tasknames == [] and env.events == [] and env.hooks == []
