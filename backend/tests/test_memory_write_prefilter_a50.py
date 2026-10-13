# -*- coding: utf-8 -*-
"""A50 批 6：``save_memory`` 写入前**过滤**段（``_prefilter_memory_write``）守卫（2026-10-12）。

被测切口：把 ``save_memory`` 里紧跟 ``async with`` 的**连续三条语句**（元信息闸门 If ／
``_src_msg_sender = None`` Assign ／``if source == "chat":`` 整块，含原 try/except，55 行 / CC 19），
机械抽成同文件模块级私有协程 ``_prefilter_memory_write``；调用点仍在感知打标之前。
**判据与三条拒收零行为变化**，唯一改写是三处 ``return None`` → 返回 ``(keep, _src_msg_sender)``
的运输契约（批 5 地图给的早退写法有误，本文件按订正后的契约钉死）：

- 元信息那条早退发生在 ``_src_msg_sender = None`` **之前**，故返回 ``(False, None)``，
  **不得**引用未绑定名（那会 UnboundLocalError）；
- 后两条拒收返回 ``(False, _src_msg_sender)``，末尾放行返回 ``(True, _src_msg_sender)``；
- ``keep`` 是明确布尔：sender 为 None 并不代表拒收；放行路径照旧去新建并**返回 Memory**，
  不能用空 sender 代替 keep 判定。

守卫分两层：
- 结构冻结（纯 AST，零 IO、不依赖 git／docs）：helper 形态与九个 keyword-only 形参、语句序列
  恰为 ``[docstring, 原块 3 条, 运输 Return]``、四个运输 Return 的形状、两条块内局部 import 随块、
  四个模块全局依赖仍晚绑定（未被提成形参或模块级缓存）、块内仍只有 2 个 await、仍无
  ``db.add/flush/commit/refresh``、调用点是「await + 二元解包 + 全 keyword」且位置未漂移、
  ``save_memory`` 公共签名二十个形参逐字未变；
- 行为冻结（临时库真跑 ``save_memory``，合成数据）：三条拒收各自的 reason 与 payload 逐字段、
  拒收不落库不进打标与准备段、非 chat 放行、无 source_id 不查库、长正文不查消息、AI 与 user 的
  sender 缓存、标记路径取同会话紧随 AI 且升序取第一条、不同会话不参与比对、``sub_type`` 非 None
  不取下一条、查消息/取下一条异常 fail-open 且保留已取 sender、放行时 sender 跨 helper 送进准入
  且不重复查库、**放行且 sender 为 None 时新建返回 Memory**、meta/raw 的回执异常照旧上抛而
  verbatim 的被 try 吞后放行。

纪律：临时库走 pytest ``tmp_path``（``_dbclone`` 克隆模板库），绝不连生产库；全文只用合成数据，
不引用任何真实用户内容、作者机器路径或内部文档；嵌入／向量查重／向量写入全部打桩，不发生真实推理。
"""
import ast
import asyncio
import io
import os

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
import app.memory.service as svc

WRITE_PY = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "app", "memory")) + os.sep + "write.py"

HELPER = "_prefilter_memory_write"
# 九个依赖一律 keyword-only：块内读的 7 个 ＋ 由调用点顶 import 传入的发射口与常量
PARAMS = ["db", "content", "character_id", "memory_type", "sub_type", "source", "source_id",
          "emit_memory_receipt", "ACTION_REJECT"]
# 必须继续按模块全局晚绑定（monkeypatch 接缝在模块属性上）
DEFERRED = ["_admission_gate_on", "_is_meta_noise", "select", "_logger"]
# save_memory 的公共签名（二十个形参，逐字冻结）
SAVE_SIG = ["user_id", "character_id", "memory_type", "content", "title", "importance",
            "sub_type", "source", "related_memory_id", "source_id", "group_id", "scope",
            "skip_dedup", "speaker_id", "speaker_type", "epistemic_status", "reliability",
            "chain_id", "parent_id", "node_type", "derived_from_ids"]

# 合成语料（不对应任何真实用户内容）
BODY = "阳台上的薄荷又长高了一截"
BODY2 = "厨房的水槽通了"
RAW_LINE = "（摸了摸她的头）别难过"
META_LINE = "我是你的 Agent 助手，请按流程执行这段记忆重构"
AI_LINE = "今晚的月色真好，我们去天台看看吧"
AI_PREFIX = "今晚的月色真好"
AI_OTHER = "先把这批实验数据核对一遍"
CROSS_LINE = "阳台的风有点凉"

# 会话/消息布局（写死 id，方便断言「同会话 + id 更大 + 升序 + limit 1」这四条判据）：
#   会话一：1=用户「今天做了什么」  3=AI AI_LINE（逐字命中）      4=AI AI_OTHER（不命中）
#   会话二：2=AI CROSS_LINE（命中，但**不同会话** ⇒ 不得参与比对）
SEED = [(1, 1, "user", "今天做了什么"), (3, 1, "ai", AI_LINE), (4, 1, "ai", AI_OTHER),
        (2, 2, "ai", CROSS_LINE)]


# ───────────────────────── 结构冻结（纯 AST，零 IO） ─────────────────────────

def _tree():
    return ast.parse(io.open(WRITE_PY, encoding="utf-8").read())


def _fn(tree, name):
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    raise AssertionError("write.py 里找不到 %s" % name)


def _flat(fn):
    """摊平 save_memory 的 async with 一层，让调用点与原块在同一层比。"""
    out = []
    for s in fn.body:
        if isinstance(s, ast.AsyncWith):
            out.extend(s.body)
        else:
            out.append(s)
    return out


def _call_index(tree):
    flat = _flat(_fn(tree, "save_memory"))
    for i, s in enumerate(flat):
        if isinstance(s, ast.Assign) and HELPER in ast.unparse(s):
            return i
    raise AssertionError("save_memory 里找不到 %s 的调用点" % HELPER)


def test_helper是模块级私有协程且九个形参固定():
    h = _fn(_tree(), HELPER)
    assert isinstance(h, ast.AsyncFunctionDef), "必须 async（块内有两个 await）"
    assert h.name.startswith("_"), "私有协程，名字带下划线前缀"
    assert h.args.args == [] and h.args.kwonlyargs, "九个依赖一律 keyword-only"
    assert [a.arg for a in h.args.kwonlyargs] == PARAMS
    assert h.args.vararg is None and h.args.kwarg is None
    assert not h.decorator_list
    assert isinstance(h.returns, ast.Subscript), "返回契约写死成 tuple[...]，不靠口头约定"


def test_helper体语句序列是docstring_原块3条_运输Return():
    h = _fn(_tree(), HELPER)
    body = list(h.body)
    kinds = [type(s).__name__ for s in body]
    assert kinds[0] == "Expr" and isinstance(body[0].value, ast.Constant), "首条是 docstring"
    assert kinds[1:] == ["If", "Assign", "If", "Return"], kinds
    assert len(body) == 5, "docstring + 原块 3 条 + 运输 Return，共 %d" % len(body)
    assert ast.unparse(body[2]) == "_src_msg_sender = None"
    assert ast.unparse(body[1]).startswith("if _admission_gate_on() and _is_meta_noise(content):")
    assert ast.unparse(body[3]).startswith("if source == 'chat':")


def test_四个运输Return形状订正():
    """批 5 地图按原样写会在第一条炸 UnboundLocalError：它的早退早于 sender 初始化。"""
    h = _fn(_tree(), HELPER)
    rets = [n for s in h.body for n in ast.walk(s)
            if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple)]
    shapes = [tuple(e.id if isinstance(e, ast.Name) else ast.unparse(e) for e in r.value.elts)
              for r in rets]
    assert shapes == [("False", "None"), ("False", "_src_msg_sender"),
                      ("False", "_src_msg_sender"), ("True", "_src_msg_sender")], shapes
    # 第一条绝不许引用 sender（它此刻尚未绑定）；其余三条必须带
    first = ast.unparse(h.body[1].body[-1])
    assert first == "return (False, None)", first
    last = ast.unparse(h.body[-1])
    assert last == "return (True, _src_msg_sender)", last


def test_段内局部import两条随块():
    """不得上提：测试 patch 的正是 app.memory.dialogue_filter 与 app.models.chat。"""
    h = _fn(_tree(), HELPER)
    txt = "\n".join(ast.unparse(s) for s in h.body[1:-1])
    for frag in ("from app.memory.dialogue_filter import looks_like_raw_dialogue",
                 "from app.models.chat import ChatMessage"):
        assert txt.count(frag) == 1, "helper 内该局部 import 恰好一处：%s" % frag


def test_四个全局依赖仍晚绑定且未提形参():
    tree = _tree()
    h = _fn(tree, HELPER)
    used = {n.id for n in ast.walk(h) if isinstance(n, ast.Name)}
    assert set(DEFERRED) <= used, "helper 里少了这些依赖：%s" % sorted(set(DEFERRED) - used)
    assert not (set(DEFERRED) & set(PARAMS)), "这些依赖不得变成形参（会提前绑定）"
    cached = {t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets
              if isinstance(t, ast.Name)}
    assert not (cached & set(DEFERRED)), "模块顶层不许缓存：%s" % sorted(cached & set(DEFERRED))


def test_块内挂起点与事务时点不变():
    h = _fn(_tree(), HELPER)
    txt = "\n".join(ast.unparse(s) for s in h.body[1:-1])
    awaits = [ast.unparse(n.value) for s in h.body[1:-1] for n in ast.walk(s)
              if isinstance(n, ast.Await)]
    assert len(awaits) == 2, awaits
    for banned in ("db.add(", "db.flush(", "db.commit(", "db.refresh("):
        assert banned not in txt, "块内不许出现 %s（事务时点必须留在调用方）" % banned
    # 拒收路径仍是「不写库不 commit」，fail-open 的 except 仍只包逐字比对那一段
    assert txt.count("except Exception:") == 1 and txt.count("pass") == 1


def test_调用点是await二元解包且位置未漂移():
    tree = _tree()
    flat = _flat(_fn(tree, "save_memory"))
    i = _call_index(tree)
    call = flat[i]
    assert isinstance(call.value, ast.Await), "调用点必须 await"
    inner = call.value.value
    assert isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
    assert inner.func.id == HELPER
    assert inner.args == [] and [k.arg for k in inner.keywords] == PARAMS, \
        "九个依赖一律 keyword 传入、不留位置参数"
    assert [e.id for e in call.targets[0].elts] == ["keep", "_src_msg_sender"]
    # 位置：紧跟函数顶第三条 import（emit/ACTION_MERGE/ACTION_REJECT 仍原位解析）
    before = flat[i - 1]
    assert isinstance(before, ast.ImportFrom) and "ACTION_REJECT" in ast.unparse(before)
    # 位置：其后紧跟 `if not keep: return None`，再往后才是批 3 的打标快照
    assert ast.unparse(flat[i + 1]) == "if not keep:\n    return None", ast.unparse(flat[i + 1])
    assert ast.unparse(flat[i + 2]).startswith("_perception_tagged = False"), ast.unparse(flat[i + 2])


def test_save_memory公共签名逐字未变():
    sm = _fn(_tree(), "save_memory")
    assert [a.arg for a in sm.args.args] == SAVE_SIG
    assert sm.args.vararg is None and sm.args.kwarg is None


# ───────────────────────── 行为冻结（临时库真跑 save_memory） ─────────────────────────

class Env:
    def __init__(self):
        self.calls = []        # _prepare_created_memory 的 kwargs（out 挂在同一条）
        self.tags = []         # _tag_perception_source 的 kwargs
        self.receipts = []
        self.gets = []         # db.get(ChatMessage, id)
        self.queries = []      # db.execute(...) 里与 chat_messages 有关的
        self.embeddings = []   # text_embedding 的入参
        self.added = []        # db.add 的实参

    @property
    def prep(self):
        return self.calls[-1]

    @property
    def rejects(self):
        return [r for r in self.receipts if r["action"] == "reject"]

    @property
    def chat_gets(self):
        return len(self.gets)

    @property
    def chat_queries(self):
        return len(self.queries)


# 会话探针的故障注入开关（用例内临时开，fixture 前后复位）
FAULT = {"get": False, "execute": False}


@pytest.fixture(autouse=True)
def clean_flags():
    def _reset():
        FAULT["get"] = FAULT["execute"] = False
        for k in ("memory_admission_gate", "perception_source_tag", "memory_write_receipt",
                  "review_plan_validity_extract", "actor_semantics_shadow",
                  "write_dedup_active_only"):
            _loop.AGENT_FLAGS[k] = False
    _reset()
    yield
    _reset()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """临时库（_dbclone 克隆模板库到 tmp_path）＋ 记录式替身；预置两段会话与四条消息。"""
    e = Env()
    engine = clone_engine(tmp_path / "prefilter.db")
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.chat import ChatMessage, ChatSession
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="a50b6_u1", nickname="A50 批6 合成用户"))
            db.add(AICharacter(id=101, user_id=1, name="A50 批6 合成角色"))
            db.add(ChatSession(id=1, user_id=1, character_id=101, title="合成会话一"))
            db.add(ChatSession(id=2, user_id=1, character_id=101, title="合成会话二"))
            for mid, sid, sender, text in SEED:
                db.add(ChatMessage(id=mid, session_id=sid, sender_type=sender, content=text))
            await db.commit()
    asyncio.run(_seed())

    # —— 服务层四件一律打桩：不真推理、不真写向量 ——
    async def _embed(text):
        e.embeddings.append(text)
        return [0.0] * 8

    async def _no_similar(*_a, **_kw):
        return None

    async def _add(**_kw):
        return None

    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "find_similar_memory", _no_similar)
    monkeypatch.setattr(svc, "add_memory", _add)
    monkeypatch.setattr(svc, "bm25_invalidate", lambda *_a, **_kw: None)

    # —— 与本切口无关的副作用全静默（后台任务/链/意义/事件/插件/晋升）——
    import app.memory.dedup as dd
    import app.utils.async_tasks as at
    import app.memory.chain_builder as cb
    import app.memory.meaning as meaning_mod
    import app.memory.core as core_mod
    import app.events as events_mod
    import app.plugins.registry as registry
    from app.memory import receipt as receipt_mod

    def _noop(*_a, **_kw):
        return None

    def _spawn(coro, **_kw):
        if hasattr(coro, "close"):
            coro.close()
        return None

    def _rec(character_id, memory_id, action, *, reason="", detail=None):
        e.receipts.append({"character_id": character_id, "memory_id": memory_id,
                           "action": action, "reason": reason, "detail": detail})

    monkeypatch.setattr(dd, "_schedule_dedup", _noop)
    monkeypatch.setattr(at, "spawn_background", _spawn)
    monkeypatch.setattr(cb, "memory_chain_builder_enabled", lambda: False)
    monkeypatch.setattr(cb, "link_new_memory", _noop)
    monkeypatch.setattr(meaning_mod, "maybe_extract_meaning", _noop)
    monkeypatch.setattr(events_mod, "publish", _noop)
    monkeypatch.setattr(registry, "run_hook", _noop)
    monkeypatch.setattr(core_mod, "maybe_promote_core", _noop)
    monkeypatch.setattr(receipt_mod, "emit_memory_receipt", _rec)

    # —— 两个已验收 helper 的观测点（模块属性＝既有 monkeypatch 接缝）——
    from app.memory import write as write_mod

    real_prep = write_mod._prepare_created_memory

    async def _probe_prep(**kw):
        out = await real_prep(**kw)
        e.calls.append(dict(kw, out=out))
        return out

    monkeypatch.setattr(write_mod, "_prepare_created_memory", _probe_prep)

    real_tag = write_mod._tag_perception_source

    async def _probe_tag(**kw):
        out = await real_tag(**kw)
        e.tags.append(dict(kw, out=out))
        return out

    monkeypatch.setattr(write_mod, "_tag_perception_source", _probe_tag)

    # —— 会话探针：记 db.get(ChatMessage,…) 与涉及 chat_messages 的 db.execute，并可注入故障 ——
    from app.models.chat import ChatMessage

    class _TracedSession:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def get(self, model, ident):
            if model is ChatMessage:
                e.gets.append(ident)
                if FAULT["get"]:
                    raise RuntimeError("get-boom")
            return await self._inner.get(model, ident)

        async def execute(self, stmt, *a, **kw):
            if "chat_messages" in str(stmt).lower():
                e.queries.append(str(stmt))
                if FAULT["execute"]:
                    raise RuntimeError("execute-boom")
            return await self._inner.execute(stmt, *a, **kw)

        def add(self, obj):
            e.added.append(obj)
            return self._inner.add(obj)

        async def __aenter__(self):
            await self._inner.__aenter__()
            return self

        async def __aexit__(self, *exc):
            return await self._inner.__aexit__(*exc)

    monkeypatch.setattr(svc, "async_session_factory", lambda: _TracedSession(factory()))
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
    kw.setdefault("importance", 4)
    kw.setdefault("content", BODY)
    kw.setdefault("source", "chat")
    return asyncio.run(save_memory(**kw))


# ── 三条拒收：reason 与 payload 逐字段 ──────────────────────────────────────

@pytest.mark.slow
def test_元信息拒收且第一条早退不炸未绑定sender(env):
    """订正后的契约第一处：sender 尚未初始化，返回 (False, None) 而非引用它。"""
    _loop.AGENT_FLAGS["memory_admission_gate"] = True
    m = _save(content=META_LINE)
    assert m is None
    assert len(env.rejects) == 1, env.receipts
    r = env.rejects[0]
    assert r["reason"] == "meta-noise/agent-speak blocked"
    assert r["character_id"] == 101 and r["memory_id"] is None
    assert r["detail"] == {"memory_type": "event", "sub_type": None, "source": "chat"}
    assert env.calls == [] and env.tags == [], "拒收不进打标与准备段"
    assert _rows() == [] and env.embeddings == [], "拒收不落库、不算向量"


@pytest.mark.slow
def test_台词原文拒收(env):
    m = _save(content=RAW_LINE)
    assert m is None
    r = env.rejects[0]
    assert r["reason"] == "raw dialogue dropped"
    assert r["detail"] == {"memory_type": "event", "sub_type": None, "source": "chat"}
    assert env.calls == [] and env.chat_gets == 0, "台词拦截先于来源消息查询"
    assert _rows() == []


@pytest.mark.slow
def test_逐字命中AI台词拒收(env):
    """来源消息就是 AI 回复（sender_type='ai'）且正文逐字一致 ⇒ 拒收，且不再取下一条。"""
    m = _save(content=AI_LINE, source_id=3)
    assert m is None
    r = env.rejects[0]
    assert r["reason"] == "verbatim AI line dropped"
    assert r["memory_id"] is None and r["character_id"] == 101
    assert env.chat_gets == 1 and env.chat_queries == 0, "AI 消息直接进候选"
    assert env.calls == [] and _rows() == []


@pytest.mark.slow
def test_标记路径取同会话第一条AI且按升序(env):
    """source_id 指向用户消息（sub_type=None）⇒ 取同会话 id 更大、sender_type=ai 的**第一条**。

    会话一里 id=3 命中、id=4 不命中：若排序被改成降序／取最后一条，这条就会漏检。
    """
    m = _save(content=AI_PREFIX, source_id=1)
    assert m is None and env.rejects[0]["reason"] == "verbatim AI line dropped"
    assert env.chat_gets == 1 and env.chat_queries == 1
    q = env.queries[0].lower()
    assert "chat_messages.session_id" in q and "chat_messages.id >" in q \
        and "sender_type" in q and "asc" in q and "limit" in q, q


@pytest.mark.slow
def test_不同会话的AI消息不参与比对(env):
    """id=2 那条逐字命中但在会话二：同会话判据没被拆松 ⇒ 不得因此拒收。"""
    m = _save(content=CROSS_LINE, source_id=1)
    assert m is not None, "跨会话的命中不参与比对"
    assert env.rejects == [], env.receipts
    assert env.chat_queries == 1, "取下一条照旧发生（同会话 id=3 不匹配）"
    assert env.prep["_src_msg_sender"] == "user"
    assert _rows()[0].id == m.id


@pytest.mark.slow
def test_sub_type非None时不取下一条(env):
    """显式 sub_type 的路径不查下一条 ⇒ 同会话紧随的逐字 AI 台词也不拒。"""
    m = _save(content=AI_PREFIX, source_id=1, sub_type="status")
    assert m is not None
    assert env.rejects == [], "sub_type 非 None ⇒ 不进标记路径的比对"
    assert env.chat_queries == 0
    assert env.prep["_src_msg_sender"] == "user"


# ── 放行与 sender 运输 ──────────────────────────────────────────────────────

@pytest.mark.slow
def test_非chat来源放行且不查消息(env):
    m = _save(source="diary")
    assert m is not None, "放行路径照旧新建并返回 Memory（不是 None）"
    assert env.chat_gets == 0 and env.chat_queries == 0
    assert env.prep["_src_msg_sender"] is None, "非 chat ⇒ sender 恒 None（None≠拒收）"
    assert _rows()[0].id == m.id


@pytest.mark.slow
def test_无source_id不查消息(env):
    m = _save()
    assert m is not None
    assert env.chat_gets == 0 and env.chat_queries == 0
    assert env.prep["_src_msg_sender"] is None


@pytest.mark.slow
def test_长正文不查消息(env):
    """长度 60 判据照旧：超长正文根本不查来源消息（逐字比对只对短正文有意义）。"""
    long_body = BODY * 6
    assert len(long_body) > 60
    m = _save(content=long_body, source_id=3)
    assert m is not None
    assert env.chat_gets == 0 and env.chat_queries == 0
    assert env.prep["_src_msg_sender"] is None


@pytest.mark.slow
def test_放行时sender跨helper送进准入且不重复查库(env):
    """用户消息的 sender_type 被缓存复用：准入解析拿到它，且 ChatMessage 只查一次。"""
    _loop.AGENT_FLAGS["memory_admission_gate"] = True
    m = _save(content=BODY2, source_id=1)
    assert m is not None
    assert env.prep["_src_msg_sender"] == "user"
    assert env.chat_gets == 1, "准入闸门复用同一次查询，不得再查一遍"
    assert _rows()[0].speaker_type == "user"


@pytest.mark.slow
def test_AI来源消息的sender同样被缓存(env):
    m = _save(content=BODY2, source_id=3)
    assert m is not None
    assert env.prep["_src_msg_sender"] == "ai"
    assert env.chat_queries == 0, "AI 消息不再取下一条"


# ── 异常边界（fail-open 与上抛都照旧） ───────────────────────────────────────

@pytest.mark.slow
def test_查消息异常fail_open放行且sender为None(env):
    FAULT["get"] = True
    m = _save(content=BODY2, source_id=1)
    assert m is not None, "查消息失败 ⇒ fail-open 放行"
    assert env.prep["_src_msg_sender"] is None, "get 没拿到东西，sender 仍是 None"
    assert _rows()[0].id == m.id


@pytest.mark.slow
def test_取下一条异常fail_open且保留已取sender(env):
    FAULT["execute"] = True
    m = _save(content=BODY2, source_id=1)
    assert m is not None, "取下一条失败 ⇒ 照旧放行"
    assert env.prep["_src_msg_sender"] == "user", "已取到的 sender 必须保留"
    assert _rows()[0].id == m.id


@pytest.mark.slow
def test_元信息与台词原文的回执异常照旧上抛(env, monkeypatch):
    """这两条不在 try 内：回执抛错原样上抛（不得新包 try 把它吞成放行）。"""
    from app.memory import receipt as receipt_mod

    def _boom(*_a, **_kw):
        raise RuntimeError("receipt-boom")

    monkeypatch.setattr(receipt_mod, "emit_memory_receipt", _boom)
    _loop.AGENT_FLAGS["memory_admission_gate"] = True
    with pytest.raises(RuntimeError):
        _save(content=META_LINE)
    _loop.AGENT_FLAGS["memory_admission_gate"] = False
    with pytest.raises(RuntimeError):
        _save(content=RAW_LINE)


@pytest.mark.slow
def test_逐字那条的回执异常被try吞后放行(env, monkeypatch):
    """逐字那条在 try 内：回执抛错被 except 吞掉 ⇒ 本次不拒收、照旧落库（既有行为）。"""
    from app.memory import receipt as receipt_mod

    def _boom(*_a, **_kw):
        raise RuntimeError("receipt-boom")

    monkeypatch.setattr(receipt_mod, "emit_memory_receipt", _boom)
    m = _save(content=AI_LINE, source_id=3)
    assert m is not None, "回执抛错不得把这条丢掉（既有 fail-open 语义）"
    assert _rows()[0].id == m.id
    assert env.prep["_src_msg_sender"] == "ai"
