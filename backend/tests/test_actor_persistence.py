# -*- coding: utf-8 -*-
"""P0 第 4 步 · Actor 归属持久化（范围＝只动「感知条」这一支）——写入侧 / 读侧 / 两个脚本。

覆盖（派单 §要求 1~7 逐条对齐）
────────────────────────────────────────────────────────
- 要求 1 写入侧：感知条落库 ``speaker_type='perception'``；**非感知条逐字节不变**（参数化对照）；
- 要求 2 常开：「感知条不套默认 user 兜底」不再受 ``perception_source_tag`` 门控；
  其它来源的既有兜底一字未动；
- 要求 3 盘点脚本 ``actor_audit.py``：默认 dry-run 不改库、``--apply`` 才写且幂等、孤儿跳过、
  三项输出（NULL 条数 / 其中 source=perception / ai↔character 混用分布）；
- 要求 4 双跑脚本 ``actor_scope_dualrun.py``：矩阵 PASS；``NULL→perception`` 归档正确；
  **构造「非感知条被改」反例必须 FAIL**；感知条越界差异也必须 FAIL；
- 要求 5 只读追溯 ``trace_actor_for_message``：消息 → 事件 → 记忆 source_id 最小链；
  id 为空 / 查不到 / 会话异常一律不抛；
- 要求 6 读侧容错：``memory/format.py`` 与 ``agent/context/section_memories.py`` 对未知
  ``speaker_type`` 不抛错、不把原始枚举显示进提示词，单行异常不拖垮整区。

纪律：临时库走 ``tests/_dbclone``（禁止连生产库）；嵌入/向量查重/后台任务全部打桩；
项目未装 pytest-asyncio，统一 ``asyncio.run`` 同步执行。
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
from app.agent.context import section_memories as _sm
from app.memory import write as _write
from app.memory.format import format_memory_line, normalize_speaker_type, speaker_tag
from app.memory.perception_tier import PERCEPTION_SOURCE

pytestmark = pytest.mark.slow

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "memory"

SNAP = "开会迟到五分钟被主管说了一顿"
MEM_HIT = "他今天开会迟到五分钟被主管说了一顿"
MEM_MISS = "用户喜欢在阳台养几盆多肉"


def _load_script(stem: str):
    """按文件路径加载 backend/scripts/memory/<stem>.py（脚本不是包，照既有测试口径）。"""
    spec = importlib.util.spec_from_file_location(stem, SCRIPTS / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ─────────────────────────── 写入侧临时库环境 ───────────────────────────

@pytest.fixture()
def clean_flags():
    def _reset():
        _loop.AGENT_FLAGS["perception_source_tag"] = False
        _loop.AGENT_FLAGS["memory_admission_gate"] = False
        _loop.AGENT_FLAGS["memory_write_receipt"] = False
    _reset()
    yield
    _reset()


@pytest.fixture()
def env(clean_flags, monkeypatch, tmp_path):
    """临时库 + 打桩（嵌入/向量查重/后台任务/晋升/回执），只观察落库值与留痕。"""
    engine = clone_engine(tmp_path / "t.db")
    factory = make_session_factory(engine)

    async def _seed_parents():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="actor_u1", nickname="归属用户"))
            db.add(AICharacter(id=101, user_id=1, name="归属角色101"))
            await db.commit()

    asyncio.run(_seed_parents())

    import app.memory.service as svc

    async def _embed(_text):
        return [0.0] * 8

    async def _none(*_a, **_kw):
        return None

    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "find_similar_memory", _none)
    monkeypatch.setattr(svc, "add_memory", _none)
    monkeypatch.setattr(svc, "bm25_invalidate", lambda *_a, **_kw: None)

    import app.memory.dedup as dd
    monkeypatch.setattr(dd, "_schedule_dedup", _none)

    import app.utils.async_tasks as at
    monkeypatch.setattr(at, "spawn_background", lambda coro, **_kw: coro.close())

    receipts = []

    def _rec(character_id, memory_id, action, *, reason="", detail=None):
        receipts.append({"memory_id": memory_id, "action": action, "reason": reason, "detail": detail})

    monkeypatch.setattr("app.memory.receipt.emit_memory_receipt", _rec)
    monkeypatch.setattr("app.memory.core.maybe_promote_core", _none)
    monkeypatch.setattr("app.events.publish", lambda *_a, **_kw: None)
    monkeypatch.setattr("app.plugins.registry.run_hook", _none)
    monkeypatch.setattr("app.memory.meaning.maybe_extract_meaning", lambda *_a, **_kw: _none())

    yield {"factory": factory, "receipts": receipts}
    asyncio.run(engine.dispose())


def _tag(on: bool) -> None:
    _loop.AGENT_FLAGS["perception_source_tag"] = on


def _add_snapshot(factory, content, *, user_id=1):
    from app.models.device import PhoneSnapshot
    from app.utils.timeutil import now_naive_utc

    async def _run():
        async with factory() as db:
            db.add(PhoneSnapshot(user_id=user_id, source="accessibility", content=content,
                                 created_at=now_naive_utc() - timedelta(minutes=1)))
            await db.commit()

    asyncio.run(_run())


def _save(**kw):
    kw.setdefault("user_id", 1)
    kw.setdefault("character_id", 101)
    kw.setdefault("memory_type", "event")
    kw.setdefault("importance", 3)
    kw.setdefault("source", "chat")
    return asyncio.run(_write.save_memory(**kw))


def _row(factory, memory_id):
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            return await db.get(Memory, memory_id)

    return asyncio.run(_run())


# ─────────────────────── 要求 1/2：感知条落 perception、其它不动 ───────────────────────

def test_打标命中_感知条落库perception(env):
    """本步主断言：判定结果真写进列（此前只留痕在准入归属，列是 NULL ⇒ 不可 SQL 查询）。"""
    _tag(True)
    _add_snapshot(env["factory"], SNAP)
    m = _save(content=MEM_HIT)
    assert m.source == PERCEPTION_SOURCE
    assert m.speaker_type == "perception"
    assert m.speaker_id is None                      # 感知没有说话人 id
    assert m.epistemic_status == "INFERRED"
    row = _row(env["factory"], m.id)
    assert (row.speaker_type, row.speaker_id) == ("perception", None)


def test_打标命中_perception可被SQL查询(env):
    """「可 SQL 查询」是本步的存在理由：按列过滤必须能捞出这条感知记忆。"""
    _tag(True)
    _add_snapshot(env["factory"], SNAP)
    m = _save(content=MEM_HIT)

    from app.models.memory import Memory

    async def _run():
        async with env["factory"]() as db:
            return (await db.execute(
                select(Memory.id).where(Memory.speaker_type == "perception"))).scalars().all()

    assert m.id in asyncio.run(_run())


def test_直接写perception来源_闸开时落perception(env):
    """未走打标（调用方直接给 source=perception）：闸开 ⇒ 不兜底 user、列写 perception。"""
    _tag(True)
    m = _save(content=MEM_MISS, source=PERCEPTION_SOURCE, epistemic_status="INFERRED")
    assert (m.speaker_type, m.speaker_id) == ("perception", None)


def test_直接写perception来源_闸关也落perception_常开(env):
    """要求 2：感知条例外从 flag 门控升级为**常开**——闸关也不再兜底成 user。"""
    _tag(False)
    m = _save(content=MEM_MISS, source=PERCEPTION_SOURCE, epistemic_status="INFERRED")
    assert m.speaker_type == "perception"
    assert m.speaker_id is None


def test_感知条不覆盖调用方显式归属(env):
    """只在判定为空时补 perception：显式给的归属原样落库（否则「调用方说的话」凭空消失）。"""
    _tag(True)
    m = _save(content=MEM_MISS, source=PERCEPTION_SOURCE, speaker_type="user", speaker_id=1)
    assert (m.speaker_type, m.speaker_id) == ("user", 1)


@pytest.mark.parametrize("source,speaker_type,speaker_id,exp", [
    ("chat", "user", 1, ("user", 1)),               # 提取器常态：用户亲口陈述
    ("chat", None, None, ("user", 1)),               # 默认 user 兜底（非感知条照旧）
    ("chat", None, 7, (None, 7)),                    # 只给 id 不兜底（照旧留空）
    ("diary", None, None, ("user", 1)),              # 落库列的兜底与准入判定是两件事，此处照旧
    ("bio", "character", 101, ("character", 101)),
    ("moment", "system", None, ("system", None)),
    ("mcp_tools", "tool", None, ("tool", None)),
    ("life", "ai", 101, ("ai", 101)),                # 历史脏值写法本轮不洗
    (None, None, None, ("user", 1)),
    ("", None, None, ("user", 1)),
])
def test_非感知条落库值逐字节不变(env, source, speaker_type, speaker_id, exp):
    """要求 1 的参数化对照：非感知条 ``(speaker_type, speaker_id)`` 与改动前完全一致。"""
    _tag(True)
    _add_snapshot(env["factory"], SNAP)             # 语料齐备；这些正文都不命中标签
    m = _save(content=MEM_MISS + str(source), source=source,
              speaker_type=speaker_type, speaker_id=speaker_id)
    assert (m.speaker_type, m.speaker_id) == exp


def test_failopen_归属回到调用方入参(env, monkeypatch):
    """打标过程抛异常 ⇒ 来源/归属同批撤销（不会留下「来源旧、列新」的半改状态）。"""
    _tag(True)
    _add_snapshot(env["factory"], SNAP)

    async def _boom(*_a, **_kw):
        raise RuntimeError("snapshot query failed")

    monkeypatch.setattr(_write, "_recent_snapshots", _boom)
    m = _save(content=MEM_HIT, speaker_type="user", speaker_id=1)
    assert m.source == "chat"
    assert (m.speaker_type, m.speaker_id) == ("user", 1)


def test_打标回执与落库值一致(env):
    """留痕不许说一套写一套：update 回继承认改写后的列值，from_speaker_type 保留原入参。"""
    _tag(True)
    _add_snapshot(env["factory"], SNAP)
    m = _save(content=MEM_HIT, speaker_type="user", speaker_id=1)
    upd = [r for r in env["receipts"] if r["action"] == "update"]
    assert len(upd) == 1
    assert upd[0]["detail"]["speaker"] == "perception"
    assert upd[0]["detail"]["speaker_type"] == m.speaker_type == "perception"
    assert upd[0]["detail"]["from_speaker_type"] == "user"
    assert upd[0]["detail"]["to_source"] == PERCEPTION_SOURCE
    cre = [r for r in env["receipts"] if r["action"] == "create"]
    assert cre and cre[-1]["detail"]["provenance"]["actor"] == "perception"


def test_perception取值不越列宽():
    """memories.speaker_type 是 String(10)：取值变长必须先被拦住。"""
    assert _write.PERCEPTION_SENDER == "perception"
    assert len(_write.PERCEPTION_SENDER) <= 10


# ─────────────────────── 要求 1 纯函数：perception_actor_column ───────────────────────

@pytest.mark.parametrize("spk,source,exp", [
    (None, "perception", "perception"),
    (None, " Perception ", "perception"),
    (None, "PERCEPTION", "perception"),
    (None, "chat", None),
    (None, "diary", None),
    (None, None, None),
    (None, "", None),
    (None, "   ", None),
    ("user", "perception", "user"),
    ("character", "perception", "character"),
    ("", "perception", ""),                          # 空串不是 None，不擅自动它
    (None, 123, None),                               # 脏来源：非字符串一律不是感知条
    (None, [], None),
    (None, None, None),
])
def test_perception_actor_column_参数化(spk, source, exp):
    assert _write.perception_actor_column(spk, source) == exp


def test_perception_actor_column_对非感知来源是恒等():
    """对非感知条这个函数必须恒等——写入侧「逐字节不变」的函数级证明。"""
    for src in ("chat", "diary", "bio", "life", "moment", "mcp_tools", None, "", "Perception-ish"):
        for val in (None, "user", "character", "system", "tool", "ai", ""):
            assert _write.perception_actor_column(val, src) == val


# ─────────────────────────── 要求 6：读侧容错 ───────────────────────────

_BASE_MEM = {"content": "喜欢喝美式", "created_at": "2026-09-29 10:00:00", "epistemic_status": "FACT"}


@pytest.mark.parametrize("value", ["ai", "bot", "unknown_enum", "PERCEPTIO", "用户", 123, ["user"], {"a": 1}, None, ""])
def test_format_未知speaker不抛错也不显示原始枚举(value):
    """脏值/未登记枚举：既不能抛，也不能把原始值写进提示词冒充某种归属。"""
    m = dict(_BASE_MEM, speaker_type=value)
    line = format_memory_line(m, include_speaker=True)
    assert line.endswith("喜欢喝美式")
    assert "ai" not in line.replace("喜欢喝美式", "")
    assert "unknown_enum" not in line and "用户" not in line


@pytest.mark.parametrize("value,exp", [
    ("user", "[你说的] "),
    ("character", "[TA说的] "),
    ("system", "[系统说的] "),
    ("perception", ""),
    ("ai", ""),
    (None, ""),
])
def test_speaker_tag_表驱动(value, exp):
    assert speaker_tag(value) == exp


def test_format_已知三档输出逐字节不变():
    """读侧改造不许动既有三档标注：与旧 if/elif 的产物完全一致。"""
    for value, tag in (("user", "[你说的] "), ("character", "[TA说的] "), ("system", "[系统说的] ")):
        m = dict(_BASE_MEM, speaker_type=value)
        old = f"- [记录于 2026-09-29] {tag}喜欢喝美式"
        assert format_memory_line(m, include_speaker=True) == old


def test_normalize_speaker_type_已知与未知():
    assert normalize_speaker_type(" User ") == "user"
    assert normalize_speaker_type("perception") == "perception"
    assert normalize_speaker_type("ai") is None
    assert normalize_speaker_type(123) is None
    assert normalize_speaker_type(None) is None


def test_section_memories_未知值走兜底且正常行字节不变():
    """检索区：user 档输出与旧实现逐字节一致；未知值/感知档都不显示原始枚举。"""
    lines = _sm._build_retrieved_memory_lines(900001, [
        {"id": 11, **_BASE_MEM, "speaker_type": "user"},
        {"id": 12, **_BASE_MEM, "speaker_type": "ai"},
        {"id": 13, **_BASE_MEM, "speaker_type": "perception"},
    ])
    assert lines == [
        "- [记录于 2026-09-29] [你说的] 喜欢喝美式",
        "- [记录于 2026-09-29] 喜欢喝美式",
        "- [记录于 2026-09-29] 喜欢喝美式",
    ]
    assert "ai" not in "".join(lines).replace("喜欢喝美式", "")


def test_section_memories_单行异常不拖垮整区():
    """要求 7 异常隔离：坏行只丢这一行，同批正常行照常注入。"""
    class _Broken:  # 不是 dict、也没有 .get ⇒ format_memory_line 会抛
        def __str__(self):
            return "broken"

    lines = _sm._build_retrieved_memory_lines(900002, [
        {"id": 21, **_BASE_MEM, "speaker_type": "user"},
        _Broken(),
        {"id": 22, **_BASE_MEM, "speaker_type": "perception"},
    ])
    assert len(lines) == 2
    assert any("[你说的]" in ln for ln in lines)


def test_section_memories_观测函数对脏输入不抛():
    assert _sm._observe_unknown_speaker(object()) is None
    assert _sm._observe_unknown_speaker({"id": 1, "speaker_type": "ai"}) is None
    assert _sm._observe_unknown_speaker({"id": 2, "speaker_type": "user"}) is None
    assert _sm._observe_unknown_speaker(None) is None


# ─────────────────────────── 要求 3：存量盘点脚本 ───────────────────────────

def _make_audit_db(path: Path) -> None:
    """最小库：memories + users + ai_characters，覆盖 NULL/感知/混用/孤儿四种形态。"""
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE memories (id INTEGER PRIMARY KEY, user_id INT, character_id INT,"
        " source TEXT, speaker_type TEXT, speaker_id INT);"
        "CREATE TABLE users (id INTEGER PRIMARY KEY);"
        "CREATE TABLE ai_characters (id INTEGER PRIMARY KEY);"
        "INSERT INTO users VALUES (1),(2); INSERT INTO ai_characters VALUES (101);"
    )
    rows = [
        (1, 1, 101, "perception", None, None),        # 感知条：本步要补的对象
        (2, 1, 101, "perception", None, 7),           # 感知条：给了 speaker_id 也仍为空
        (3, 1, 101, "perception", "perception", None),  # 已回填 ⇒ 不是候选（幂等）
        (4, 1, 101, "chat", "user", 1),
        (5, 1, 101, "chat", "ai", 1),                 # 混用脏值
        (6, 1, 101, "diary", "character", 101),
        (7, 1, 101, "bio", "", None),                 # 空串也算 NULL 面
        (8, 99, 101, "perception", None, None),       # 孤儿：user_id=99 不在 users
    ]
    conn.executemany("INSERT INTO memories VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


@pytest.fixture()
def audit_mod(tmp_path):
    mod = _load_script("actor_audit")
    db = tmp_path / "audit.db"
    _make_audit_db(db)
    yield mod, db


def test_audit_默认dryrun_不改库(audit_mod):
    mod, db = audit_mod
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    data = mod.collect(db)                      # 缺省 apply=False
    assert data["ok"] is True
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert data["written"] == 0 and data["applied"] is False


def test_audit_三项计数(audit_mod):
    mod, db = audit_mod
    data = mod.collect(db)
    assert data["total"] == 8
    assert data["null_speaker_type"] == 4       # id 1,2,7,8（空串计入 NULL 面）
    assert data["null_perception"] == 3         # 其中 source=perception：id 1,2,8
    assert data["perception_total"] == 4
    assert data["perception_filled"] == 1
    assert data["ai_count"] == 1 and data["character_count"] == 1


def test_audit_混用分布与未登记值(audit_mod):
    mod, db = audit_mod
    dist = mod.collect(db)["distribution"]
    assert dist["(NULL)"] == 4 and dist["user"] == 1 and dist["ai"] == 1
    assert dist["perception"] == 1 and dist["character"] == 1
    assert mod.collect(db)["unknown_values"] == {"ai": 1}


def test_audit_孤儿跳过(audit_mod):
    mod, db = audit_mod
    plan = mod.backfill_plan(mod._connect(db))   # 只读连接也算计划，不写
    assert plan["candidate_ids"] == [1, 2]
    assert plan["orphan_ids"] == [8]


def test_audit_apply_写库且幂等(audit_mod):
    mod, db = audit_mod
    data = mod.collect(db, apply=True)
    assert data["written"] == 2
    assert data["idempotent"] is True
    conn = sqlite3.connect(db)
    got = dict(conn.execute("SELECT id, speaker_type FROM memories").fetchall())
    conn.close()
    assert got[1] == "perception" and got[2] == "perception"
    assert got[8] is None                        # 孤儿没被动
    assert got[4] == "user" and got[5] == "ai"   # 非感知条没被动
    again = mod.collect(db, apply=True)
    assert again["written"] == 0                 # 第二次跑候选归零 ⇒ 幂等


def test_audit_apply_生产库安全闸(audit_mod, monkeypatch, capsys):
    mod, db = audit_mod
    monkeypatch.setattr(mod, "DEFAULT_APP_DB", db)   # 把默认库指到临时库＝模拟「指向生产」
    assert mod.main(["--app-db", str(db), "--apply"]) == 2
    assert "拒绝" in capsys.readouterr().err
    assert mod.collect(db)["perception_filled"] == 1        # 没写：只有 id=3 已填
    assert mod.main(["--app-db", str(db), "--apply", "--force-production"]) == 0
    after = mod.collect(db)
    assert after["perception_filled"] == 3                  # 写了 id=1、id=2
    assert after["null_perception"] == 1                    # 孤儿 id=8 仍为空


def test_audit_缺表不炸(tmp_path, capsys):
    mod = _load_script("actor_audit")
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert mod.main(["--app-db", str(empty)]) == 1
    assert "无从盘点" in capsys.readouterr().out


def test_audit_缺库文件(tmp_path):
    mod = _load_script("actor_audit")
    data = mod.collect(tmp_path / "nope.db")
    assert data["ok"] is False and "不存在" in data["note"]


# ─────────────────────────── 要求 4：双跑校验脚本 ───────────────────────────

@pytest.fixture(scope="module")
def dualrun_mod():
    return _load_script("actor_scope_dualrun")


def test_双跑_矩阵PASS(dualrun_mod):
    rep = dualrun_mod.evaluate(dualrun_mod.matrix_cases())
    assert rep["passed"] is True
    assert rep["nonperception_changed"] == 0
    assert rep["unexplained"] == 0
    assert rep["null_to_perception"] > 0          # 确实跑出了本步预期的差异，不是空转
    assert dualrun_mod.main([]) == 0


def test_双跑_非感知条零差异逐条核(dualrun_mod):
    for case in dualrun_mod.matrix_cases():
        if case["source"] == PERCEPTION_SOURCE or (case["source"] or "").strip().lower() == PERCEPTION_SOURCE:
            continue
        assert dualrun_mod.classify(
            case, dualrun_mod.old_branch(case), dualrun_mod.new_branch(case)) == dualrun_mod.DIFF_SAME


def test_双跑_NULL到perception归档(dualrun_mod):
    case = {"source": "perception", "speaker_type": None, "speaker_id": None, "user_id": 1, "tag_on": True}
    assert dualrun_mod.classify(case, dualrun_mod.old_branch(case), dualrun_mod.new_branch(case)) \
        == dualrun_mod.DIFF_NULL_FILL


def test_双跑_非感知条被改_必须FAIL(dualrun_mod):
    """反例（派单点名要求）：把 chat 条的归属也改成 perception ⇒ 判据必须报 FAIL。"""
    def _tampered(case):
        return {"speaker_type": "perception" if case["source"] == "chat" else case["speaker_type"],
                "speaker_id": case["speaker_id"], "actor": case["speaker_type"]}

    cases = [{"source": "chat", "speaker_type": None, "speaker_id": None, "user_id": 1, "tag_on": True}]
    rep = dualrun_mod.evaluate(cases, new_fn=_tampered)
    assert rep["passed"] is False
    assert rep["nonperception_changed"] == 1
    assert rep["unexplained"] == 1


def test_双跑_感知条越界差异也FAIL(dualrun_mod):
    """感知条只允许两档差异：把已显式给的 user 归属覆盖掉 ⇒ 越界。"""
    def _override(case):
        return {"speaker_type": "perception", "speaker_id": case["speaker_id"], "actor": "perception"}

    case = {"source": "perception", "speaker_type": "user", "speaker_id": 1, "user_id": 1, "tag_on": True}
    rep = dualrun_mod.evaluate([case], new_fn=_override)
    assert rep["passed"] is False and rep["unexplained"] == 1


def test_双跑_旧侧确实是闸控的旧行为(dualrun_mod):
    """冻结复刻的旧侧要能自证：关闸时感知条掉进 user 兜底、闸开时列留空只留痕。"""
    case = {"source": "perception", "speaker_type": None, "speaker_id": None, "user_id": 1, "tag_on": False}
    assert dualrun_mod.old_branch(case) == {"speaker_type": "user", "speaker_id": 1, "actor": "user"}
    case["tag_on"] = True
    assert dualrun_mod.old_branch(case) == {"speaker_type": None, "speaker_id": None, "actor": "perception"}


def test_双跑_新侧调用的是生产函数(dualrun_mod):
    """新侧不许另抄一份判据：直接拿生产 ``perception_actor_column`` 的结果比对。"""
    case = {"source": "perception", "speaker_type": None, "speaker_id": None, "user_id": 1, "tag_on": True}
    assert dualrun_mod.new_branch(case)["speaker_type"] == _write.perception_actor_column(None, case["source"])


def test_双跑_存量投影只读(dualrun_mod, tmp_path):
    db = tmp_path / "proj.db"
    _make_audit_db(db)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    cases = dualrun_mod.cases_from_db(db)
    assert cases and all(c["tag_on"] is True for c in cases)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before   # 打开方式必须是只读
    assert dualrun_mod.main(["--db", str(db)]) == 0


# ─────────────────────────── 要求 5：只读追溯函数 ───────────────────────────

def _seed_chain(factory, *, msg_id=501, sender="user", mem_source="chat", mem_speaker="user"):
    from app.models.chat import ChatMessage, ChatSession
    from app.models.domain_event import DomainEvent
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            if await db.get(ChatSession, 1) is None:      # chat_messages.session_id 有外键约束
                db.add(ChatSession(id=1, user_id=1, character_id=101, title="追溯会话"))
            db.add(ChatMessage(id=msg_id, session_id=1, sender_type=sender, content="我今天迟到了"))
            db.add(DomainEvent(aggregate_type="chat_session", aggregate_id=1, entity_type="chat_message",
                               entity_id=msg_id, event_type="chat.message.created", actor_type=sender,
                               payload_json="{}", idempotency_key=f"trace-{msg_id}"))
            db.add(Memory(user_id=1, character_id=101, memory_type="event", content="用户今天迟到",
                          source=mem_source, source_id=msg_id, speaker_type=mem_speaker,
                          importance=40.0))
            await db.commit()

    asyncio.run(_run())


def test_追溯_用户消息链(env):
    _seed_chain(env["factory"])
    got = asyncio.run(_write.trace_actor_for_message(501))
    assert got["error"] is None and got["found"] is True
    assert got["message_sender_type"] == "user"
    assert got["event_actors"] == ["user"]
    assert got["memory_rows"] == 1 and got["memory_speaker_types"] == ["user"]
    assert got["actor"] == "user" and got["consistent"] is True


def test_追溯_感知条链归perception(env):
    _seed_chain(env["factory"], msg_id=502, sender="user", mem_source=PERCEPTION_SOURCE,
                mem_speaker="perception")
    got = asyncio.run(_write.trace_actor_for_message(502))
    assert got["actor"] == "perception"
    assert got["consistent"] is True               # 列值与统一判定对上——本步之前不可能出现


def test_追溯_列与判定不符时consistent为假(env):
    _seed_chain(env["factory"], msg_id=503, sender="ai", mem_source="chat", mem_speaker="user")
    got = asyncio.run(_write.trace_actor_for_message(503))
    assert got["actor"] == "character" and got["consistent"] is False


def test_追溯_会话不可用也不抛(env, monkeypatch):
    """取会话就失败 ⇒ 结构化 error 返回，绝不把异常传到调用方（读侧异常隔离）。"""
    import app.memory.service as svc

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(svc, "async_session_factory", _boom)
    got = asyncio.run(_write.trace_actor_for_message(1))
    assert "RuntimeError" in (got["error"] or "")
    assert got["memory_rows"] == 0


def test_追溯_脏id不抛(env):
    for bad in (None, "abc", [], {}):
        got = asyncio.run(_write.trace_actor_for_message(bad))
        assert isinstance(got, dict)               # 一律结构化返回，不抛


def test_追溯_复用调用方会话不替其关闭(env):
    """传入 db 时只读不关：trace 之后同一个会话还能继续查询。"""
    from app.models.memory import Memory

    async def _run():
        async with env["factory"]() as db:
            got = await _write.trace_actor_for_message(777, db=db)
            rows = (await db.execute(select(Memory.id).limit(1))).all()
            return got, rows

    got, rows = asyncio.run(_run())
    assert got["basis"] == "not_found" and got["error"] is None
    assert isinstance(rows, list)
