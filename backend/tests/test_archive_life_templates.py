# -*- coding: utf-8 -*-
"""生活模板归档工具 ``scripts/archive_life_templates.py`` 的沙箱验证（2026-10-14 派单）。

schema 真源：每例从**整链 alembic upgrade head** 产出的模板库文件复制而来（真迁移物理结构，
不用 ``tests/_dbclone.py`` 的 create_all 克隆捷径），库文件一律 pytest ``tmp_path``。

覆盖：干跑零写、选择三重闸门（非 life 来源不入）、保护矩阵（含反向引用/链兄弟/未知指针列）、
apply 只改归档位＋在线备份、漂移/新增引用拒绝、备份失败零写入、确认串闸门、
restore 原值撤销与后续改动/状态冲突拒绝、召回面确实退出、仓库外输出目录守卫。

测试正文全部为**合成**模板句（假角色名「小测」），不含任何真实用户内容。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest
from alembic import command

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
pytestmark = pytest.mark.slow

_spec = importlib.util.spec_from_file_location(
    "archive_life_templates", BACKEND_ROOT / "scripts" / "archive_life_templates.py")
alt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(alt)

FRAME = "小测学了「概率论」，把之前模糊的地方理清了，有点收获。"
FRAME2 = "小测学了「线性代数」，整理了一遍要点，比昨天清楚一些。"
GENERIC = "小测学了一会儿新东西，感觉有收获。"

_BASE = {
    "user_id": 4, "character_id": 11, "memory_type": "event", "sub_type": "life_event",
    "source": "life", "importance": 40.0, "is_archived": 0, "is_pinned": 0, "is_core": 0,
    "is_locked": 0, "review_count": 0, "status": "active", "title": None, "chain_id": None,
    "parent_id": None, "node_type": None, "version": 0, "delete_at": None, "next_review_at": None,
    "derived_from_ids": "[]", "epistemic_status": "FACT", "speaker_type": "character",
    "why_it_matters": None,
    "created_at": "2026-10-01 00:00:00", "updated_at": "2026-10-01 00:00:00",
}


_FILL = {"TEXT": "", "INTEGER": 0, "REAL": 0.0, "FLOAT": 0.0, "BOOLEAN": 0, "BLOB": None}


def mk(con: sqlite3.Connection, content: str = FRAME, **kw) -> int:
    """按**迁移后物理 schema** 造行：语义列走 _BASE，其余 NOT NULL 无默认值的列按类型补零。"""
    row = {**_BASE, **kw, "content": content}
    cols: list[str] = []
    for _cid, name, type_name, notnull, dflt, _pk in con.execute('PRAGMA table_info("memories")').fetchall():
        if name == "id":
            continue
        if name not in row and notnull and dflt is None:
            row[name] = _FILL.get((type_name or "TEXT").upper().split("(")[0], "")
        if name in row:
            cols.append(name)
    ph = ",".join("?" * len(cols))
    cur = con.execute(
        f"INSERT INTO memories ({','.join(cols)}) VALUES ({ph})", tuple(row[c] for c in cols))
    con.commit()
    return int(cur.lastrowid)


def full_rows(db: Path) -> dict[int, dict]:
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        return {r["id"]: dict(r) for r in con.execute("SELECT * FROM memories ORDER BY id")}
    finally:
        con.close()


def plan_of(db: Path) -> dict:
    con = alt.open_ro(db)
    try:
        return alt.build_plan(con)
    finally:
        con.close()


class GuardedConn:
    """确定性「读取失败」注入：只对**整句精确匹配**的目标语句抛 ``sqlite3.OperationalError``。

    PRAGMA / schema / 候选计数等其余语句原样转发真连接，所以被测分支确定是
    「表在、列在、只有这一句读失败」，不再受 SQLite 版本对 fts5 损坏
    「在 schema 阶段还是读取阶段失败」的差异影响（py3.13/Linux 属于前者）。
    不额外持有连接，句柄仍由真连接统一关闭。
    """

    def __init__(self, con: sqlite3.Connection, failing: dict[str, str]):
        self._con = con
        self._failing = dict(failing)
        self.fired: list[str] = []

    def execute(self, sql, parameters=()):
        why = self._failing.get(sql)
        if why is not None:
            self.fired.append(sql)
            raise sqlite3.OperationalError(why)
        return self._con.execute(sql, parameters)

    def __getattr__(self, name):
        return getattr(self._con, name)


def guarded_plan(db: Path, failing: dict[str, str]) -> tuple[dict, set[str]]:
    """注入后跑一次干跑，返回 (plan, 实际被拦截的语句集合)。"""
    con = alt.open_ro(db)
    try:
        wrap = GuardedConn(con, failing)
        plan = alt.build_plan(wrap)
        return plan, set(wrap.fired)
    finally:
        con.close()


def digest(db: Path) -> str:
    return hashlib.sha256(Path(db).read_bytes()).hexdigest()


def reasons_for(plan: dict, rid: int) -> list[str]:
    for c in plan["candidates"]:
        if c["id"] == rid:
            return c["protected"]
    return []


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory) -> Path:
    """整链重放到 head 的模板库（物理 schema 权威形态；只建一次，按例复制）。"""
    from app.config import settings
    from app.db.migrate import _alembic_config

    db = tmp_path_factory.mktemp("alt_schema") / "migrated.db"
    settings.database_url = f"sqlite+aiosqlite:///{db.as_posix()}"
    command.upgrade(_alembic_config(), "head")
    assert db.exists()
    return db


@pytest.fixture()
def db(migrated_template, tmp_path) -> Path:
    out = tmp_path / "app.db"
    shutil.copy(migrated_template, out)
    return out


@pytest.fixture()
def out_dir() -> Path:
    """工具的输出闸门要求「仓库外」，故用带自动清理的系统临时目录（项目测试卫生允许的形式）。"""
    with tempfile.TemporaryDirectory(prefix="ambrace_alt_out_") as td:
        yield Path(td)


# ── 1 干跑零写 ──

def test_dry_run_writes_nothing(db, out_dir):
    con = sqlite3.connect(str(db))
    mk(con, FRAME)
    con.close()
    before = digest(db)
    plan = alt.run_dry(db, out_dir)
    assert digest(db) == before                        # 库逐字节未变
    assert (out_dir / "plan.json").is_file()
    assert plan["db_basename"] == "app.db"             # 只记文件名，不落绝对路径
    blob = json.dumps(plan, ensure_ascii=False)
    assert FRAME not in blob and "小测" not in blob     # 计划里绝无正文


# ── 2 选择三重闸门 ──

def test_selection_requires_source_type_and_full_shape(db):
    con = sqlite3.connect(str(db))
    ok_frame = mk(con, FRAME)
    ok_frame2 = mk(con, FRAME2)
    ok_generic = mk(con, GENERIC)
    other_source = mk(con, FRAME, source="system")                      # 来源不是 life
    other_type = mk(con, FRAME, memory_type="working_state")             # 类型不是 event
    other_subtype = mk(con, FRAME, sub_type=None)
    diary_generic = mk(con, "今天有点收获，顺手记了几笔。", source="diary", sub_type="diary")  # 泛词误杀面
    frame_with_prose = mk(con, FRAME + "另外还写了一大段自己的话")                                 # 非整串
    tail_only = mk(con, "把之前模糊的地方理清了，有点收获。")                                       # 只有收尾语
    con.close()
    plan = plan_of(db)
    ids = {c["id"] for c in plan["candidates"]}
    assert {ok_frame, ok_frame2, ok_generic} <= ids
    assert not ({other_source, other_type, other_subtype, diary_generic,
                 frame_with_prose, tail_only} & ids)
    assert plan["totals"]["shape_frame"] == 2
    assert plan["totals"]["shape_generic"] == 1
    assert plan["totals"]["archivable"] == 3


# ── 3 保护矩阵 ──

@pytest.mark.parametrize("kw,tag", [
    ({"is_archived": 1}, "already_archived"),
    ({"status": "superseded"}, "status_not_active"),
    ({"status": "stale"}, "status_not_active"),
    ({"delete_at": "2026-11-01 00:00:00"}, "delete_pending"),
    ({"is_pinned": 1}, "pinned"),
    ({"is_core": 1}, "core"),
    ({"is_locked": 1}, "locked"),
    ({"review_count": 3}, "reviewed"),
    ({"next_review_at": "2026-10-20 00:00:00"}, "review_scheduled"),
    ({"parent_id": 999}, "child_node"),
    ({"title": "某标题"}, "has_title"),
    ({"why_it_matters": "对关系有影响"}, "has_meaning_layer"),
])
def test_protection_matrix(db, kw, tag):
    con = sqlite3.connect(str(db))
    keep = mk(con, FRAME, **kw)
    free = mk(con, FRAME2, chain_id="c-free")
    con.close()
    plan = plan_of(db)
    assert tag in reasons_for(plan, keep)
    assert keep not in plan["allowed_ids"]
    assert free in plan["allowed_ids"]


def test_reverse_parent_reference_protects_target(db):
    con = sqlite3.connect(str(db))
    parent = mk(con, FRAME, chain_id="c1", node_type="root")
    child = mk(con, FRAME2, chain_id="c1", parent_id=parent, node_type="leaf")
    con.close()
    plan = plan_of(db)
    assert "child_node" in reasons_for(plan, child)
    assert "referenced_as_parent" in reasons_for(plan, parent)
    assert plan["allowed_ids"] == []


def test_chain_sibling_outside_batch_protects(db):
    con = sqlite3.connect(str(db))
    tmpl = mk(con, FRAME, chain_id="c2")
    real = mk(con, "用户真实事件：今天面试通过了", memory_type="event", sub_type="life_event",
              source="chat", chain_id="c2")
    con.close()
    plan = plan_of(db)
    assert "chain_sibling_outside_batch" in reasons_for(plan, tmpl)
    assert plan["allowed_ids"] == []
    assert real not in {c["id"] for c in plan["candidates"]}


def test_derived_from_and_receipt_pointers_protect(db):
    con = sqlite3.connect(str(db))
    src = mk(con, FRAME, chain_id="c3")
    derived = mk(con, FRAME2, derived_from_ids=json.dumps([src]))
    receipt = mk(con, GENERIC)
    con.execute("INSERT INTO memory_write_receipts (memory_id, action, created_at) VALUES (?, 'create', '2026-10-01 00:00:00')", (receipt,))
    con.commit()
    con.close()
    plan = plan_of(db)
    assert "referenced_derived_from" in reasons_for(plan, src)
    assert derived in plan["allowed_ids"]
    assert "referenced_receipt" in reasons_for(plan, receipt)


def test_unknown_pointer_column_is_conservatively_protected(db):
    con = sqlite3.connect(str(db))
    target = mk(con, FRAME)
    con.execute("CREATE TABLE probe_refs (id INTEGER PRIMARY KEY, memory_id INTEGER, "
                "FOREIGN KEY (memory_id) REFERENCES memories(id))")
    con.execute("INSERT INTO probe_refs (memory_id) VALUES (?)", (target,))
    con.commit()
    known, unknown = alt.discover_pointers(con)
    con.close()
    assert ("probe_refs", "memory_id") in unknown
    assert ("probe_refs", "memory_id") not in known
    plan = plan_of(db)
    assert any(t.startswith("unknown_reference") for t in reasons_for(plan, target))
    assert plan["allowed_ids"] == []


def test_pointer_whitelist_covers_migrated_schema(migrated_template):
    """指针白名单必须跟得上真实迁移 schema：出现未知指针列＝本工具判据过期，先拒绝再补。"""
    con = alt.open_ro(migrated_template)
    try:
        _, unknown = alt.discover_pointers(con)
    finally:
        con.close()
    assert unknown == [], f"schema 新增未登记指针列：{unknown}"


# ── 3b 指针列逐值严核（2026-10-14 复核：``IN`` 标量比对会漏掉 JSON 数组引用）──

def test_unknown_json_array_pointer_protects_each_item(db):
    """真漏引用反例：JSON 数组列在 ``WHERE col IN (id)`` 下永远不命中，逐值严核才拦得住。"""
    con = sqlite3.connect(str(db))
    a = mk(con, FRAME, chain_id="ca")
    b = mk(con, FRAME2, chain_id="cb")
    con.execute("CREATE TABLE probe_json (id INTEGER PRIMARY KEY, memory_ids TEXT)")
    con.execute("INSERT INTO probe_json (memory_ids) VALUES (?)", (f"[{a},999999]",))
    con.execute("INSERT INTO probe_json (memory_ids) VALUES (?)", (f'["{b}"]',))   # 字符串元素同样严核
    con.commit()
    con.close()
    plan = plan_of(db)
    assert "unknown_reference:probe_json.memory_ids" in reasons_for(plan, a)
    assert "unknown_reference:probe_json.memory_ids" in reasons_for(plan, b)
    assert plan["allowed_ids"] == []
    assert plan["pointer_unresolved"] == []           # 解释得通，不是「排除不掉」


def test_unknown_pointer_scalar_still_matches_without_int_cast(db):
    """标量形态照旧命中（含 TEXT 存 id），但绝不 int() 强转别人的值。"""
    con = sqlite3.connect(str(db))
    a = mk(con, FRAME, chain_id="ca")
    b = mk(con, FRAME2, chain_id="cb")
    con.execute("CREATE TABLE probe_scalar (id INTEGER PRIMARY KEY, ref_memory_ids TEXT)")
    con.execute("INSERT INTO probe_scalar (ref_memory_ids) VALUES (?)", (str(a),))
    con.execute("INSERT INTO probe_scalar (ref_memory_ids) VALUES (?)", (str(b),))
    con.commit()
    con.close()
    plan = plan_of(db)
    assert plan["allowed_ids"] == []
    assert ("unknown_reference:probe_scalar.ref_memory_ids") in reasons_for(plan, a)


@pytest.mark.parametrize("bad", ["[", "[1,", "1.5", "[1.5]", '{"refs":[1]}',
                                 "[[[[[[1]]]]]]", "3,4", "id:7"])
def test_uninterpretable_pointer_values_protect_every_candidate(db, bad):
    """坏 JSON／非整数／未知对象／嵌套过深／含数字的非数组 ⇒ 全体候选保留并给原因。"""
    con = sqlite3.connect(str(db))
    a = mk(con, FRAME, chain_id="ca")
    b = mk(con, GENERIC, chain_id="cb")
    con.execute("CREATE TABLE probe_bad (id INTEGER PRIMARY KEY, memory_ids TEXT)")
    con.execute("INSERT INTO probe_bad (memory_ids) VALUES (?)", (bad,))
    con.commit()
    con.close()
    plan = plan_of(db)
    assert plan["pointer_unresolved"], "解释不了的列必须登记，不能当成无引用"
    assert plan["allowed_ids"] == []
    for rid in (a, b):
        assert any(t.startswith("unresolved_reference:probe_bad.memory_ids") for t in reasons_for(plan, rid))


def test_derived_from_ids_unsupported_shape_is_fail_closed(db):
    """derived_from_ids 非 list／未知对象：旧写法 continue 当无引用，现在整批保留。"""
    con = sqlite3.connect(str(db))
    a = mk(con, FRAME, chain_id="ca")
    b = mk(con, FRAME2, chain_id="cb")
    con.execute("UPDATE memories SET derived_from_ids=? WHERE id=?", ('{"other":3}', a))
    con.commit()
    con.close()
    plan = plan_of(db)
    assert plan["allowed_ids"] == []
    assert any(t.startswith("unresolved_reference:memories.derived_from_ids") for t in reasons_for(plan, b))


def test_derived_from_ids_supported_shapes_still_resolve(db):
    """收严不许过头：历史 ``{"ids":[...]}`` 与嵌套数组形态照常逐项命中。"""
    con = sqlite3.connect(str(db))
    src = mk(con, FRAME, chain_id="cx")
    legacy = mk(con, FRAME2, chain_id="cy", derived_from_ids=json.dumps({"ids": [src]}))
    nested = mk(con, GENERIC, chain_id="cz", derived_from_ids=json.dumps([[src]]))
    quiet = mk(con, GENERIC, chain_id="cw", derived_from_ids="null")
    con.close()
    plan = plan_of(db)
    assert "referenced_derived_from" in reasons_for(plan, src)
    assert plan["allowed_ids"] == sorted([legacy, nested, quiet])
    assert plan["pointer_unresolved"] == []


def test_legal_pointer_values_do_not_overprotect(db):
    """合法且确无引用：不误伤可归档面（收严≠凑数式全保护）。"""
    con = sqlite3.connect(str(db))
    a = mk(con, FRAME, chain_id="ca")
    b = mk(con, FRAME2, chain_id="cb")
    con.execute("CREATE TABLE probe_legal (id INTEGER PRIMARY KEY, memory_ids TEXT)")
    for v in ("[]", "", None, "[999999]", "null", "note", '{"ids": []}'):
        con.execute("INSERT INTO probe_legal (memory_ids) VALUES (?)", (v,))
    con.commit()
    con.close()
    plan = plan_of(db)
    assert plan["pointer_unresolved"] == []
    assert plan["allowed_ids"] == sorted([a, b])


def test_unreadable_existing_pointer_column_is_not_swallowed(db):
    """列确实存在（PRAGMA 读得出）但查询失败：不可吞成「无引用」→ 全体保留。

    读取失败用 ``GuardedConn`` 确定性注入到真实存在的表上。旧夹具靠 fts5 影子表缺失
    制造真实失败，但那只保证「读不动」，不保证失败发生在**读取**阶段：py3.13/Linux 的
    SQLite 在 schema 阶段就失败（归因变成 ``probe_fts(schema)``），把本分支顶掉。
    """
    table, column = "probe_reads", "memory_ids"
    con = sqlite3.connect(str(db))
    a = mk(con, FRAME, chain_id="ca")
    b = mk(con, GENERIC, chain_id="cb")
    con.execute(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY, {column} TEXT)')
    con.execute(f'INSERT INTO {table} ({column}) VALUES (?)', ("[999999]",))
    con.commit()
    target = f"SELECT DISTINCT {alt._q(column)} FROM {alt._q(table)}"
    assert column in {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    assert con.execute(target).fetchall() == [("[999999]",)]   # 真连接读得动 ⇒ 失败确是注入的
    con.close()
    plan, fired = guarded_plan(db, {target: "夹具注入的指针列读取失败"})
    assert fired == {target}                                   # 只拦这一句，别的一律走真连接
    assert "probe_reads.memory_ids" in plan["pointer_columns_unknown"]   # 列被发现，不是缺席
    assert plan["allowed_ids"] == []
    assert plan["pointer_unresolved"] == [
        "unresolved_reference:probe_reads.memory_ids:读取失败(OperationalError)"]
    for rid in (a, b):
        assert any(t.startswith("unresolved_reference:probe_reads.memory_ids") for t in reasons_for(plan, rid))


def test_unreadable_pointer_table_schema_is_not_treated_as_absent(db):
    """表存在但 PRAGMA 读不动：登记「排除不掉」的 schema 阶段，绝不退化成「这表没有列」。

    这是 py3.13/Linux 上 fts5 损坏实际走到的那条分支，用注入固定住。
    """
    table, column = "probe_schema", "memory_ids"
    con = sqlite3.connect(str(db))
    a = mk(con, FRAME, chain_id="ca")
    b = mk(con, GENERIC, chain_id="cb")
    con.execute(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY, {column} TEXT)')
    con.execute(f'INSERT INTO {table} ({column}) VALUES (?)', ("[999999]",))
    con.commit()
    target = f"PRAGMA table_info({alt._q(table)})"
    assert con.execute(target).fetchall()                      # 真连接 schema 读得出
    con.close()
    plan, fired = guarded_plan(db, {target: "夹具注入的 schema 读取失败"})
    assert fired == {target}
    assert "probe_schema.memory_ids" not in plan["pointer_columns_unknown"]  # schema 读不动 ⇒ 不产指针
    assert plan["allowed_ids"] == []
    assert plan["pointer_unresolved"] == [
        "unresolved_reference:probe_schema(schema):schema读取失败(OperationalError)"]
    for rid in (a, b):
        assert any(t.startswith("unresolved_reference:probe_schema(schema)") for t in reasons_for(plan, rid))


def test_fts_shadow_corruption_rejects_conservatively_on_every_sqlite_version(db):
    """fts5 影子表缺失＝真损坏：无论 SQLite 在 schema 阶段还是读取阶段失败，都必须保留全体。

    本例只锁「保守拒绝」这一版本无关的不变式；具体归因标签随失败阶段而变
    （``probe_fts.memory_ids`` vs ``probe_fts(schema)``），逐值归因由上面两个注入用例分别锁定。
    """
    con = sqlite3.connect(str(db))
    a = mk(con, FRAME, chain_id="ca")
    b = mk(con, GENERIC, chain_id="cb")
    con.execute("CREATE VIRTUAL TABLE probe_fts USING fts5(memory_ids)")
    con.execute("INSERT INTO probe_fts (memory_ids) VALUES ('[1]')")
    con.execute("DROP TABLE probe_fts_data")
    con.commit()
    assert con.execute("PRAGMA table_info(probe_fts)").fetchall()      # schema 里列仍在
    con.close()
    plan = plan_of(db)
    assert plan["allowed_ids"] == []
    assert plan["pointer_unresolved"], "损坏的指针表不许被当成无引用"
    for rid in (a, b):
        assert any(t.startswith("unresolved_reference:probe_fts") for t in reasons_for(plan, rid))


def test_quoted_identifier_pointer_column_not_misread(db):
    """表/列名带双引号：动态 SQL 必须转义，不许因拼错标识符而静默漏引用。"""
    con = sqlite3.connect(str(db))
    a = mk(con, FRAME, chain_id="ca")
    b = mk(con, FRAME2, chain_id="cb")
    con.execute('CREATE TABLE "we""ird_refs" ("pro""be_memory_ids" TEXT)')
    con.execute('INSERT INTO "we""ird_refs" ("pro""be_memory_ids") VALUES (?)', (f"[{a}]",))
    con.commit()
    con.close()
    plan = plan_of(db)
    assert any(t.startswith('unknown_reference:we"ird_refs.pro"be_memory_ids') for t in reasons_for(plan, a))
    assert plan["allowed_ids"] == [b]                                   # 逐项严核：没被引用的行不受牵连
    assert plan["pointer_columns_unknown"] == ['we"ird_refs.pro"be_memory_ids']


# ── 4 apply ──

def _apply(db, out_dir, plan=None, confirm=None):
    plan = plan or alt.run_dry(db, out_dir)
    return alt.apply_plan(db, out_dir, plan, confirm or alt.APPLY_CONFIRM)


def test_apply_flips_only_is_archived_and_backs_up(db, out_dir):
    con = sqlite3.connect(str(db))
    a = mk(con, FRAME, chain_id="ca")
    b = mk(con, GENERIC, chain_id="cb")
    keep = mk(con, FRAME2, chain_id="cc", review_count=2)
    con.close()
    before = full_rows(db)
    audit = _apply(db, out_dir)
    assert audit["result"] == "applied" and audit["written"] == 2
    after = full_rows(db)
    for rid in (a, b):
        assert after[rid]["is_archived"] == 1
        assert {k: v for k, v in after[rid].items() if k != "is_archived"} == \
               {k: v for k, v in before[rid].items() if k != "is_archived"}
    assert after[keep] == before[keep]                 # 受保护行逐字节未动
    tgt = sqlite3.connect(str(out_dir / audit["backup"]["name"]))
    assert tgt.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert tgt.execute("SELECT COUNT(*) FROM memories WHERE is_archived=0").fetchone()[0] == 3
    tgt.close()                                        # 备份是写前状态：三行都还没归档
    assert full_rows(db)[keep]["is_archived"] == 0
    assert audit["rows"][0]["fingerprint_at_plan"] and "小测" not in json.dumps(audit)


def test_apply_rejects_drifted_row_only(db, out_dir):
    con = sqlite3.connect(str(db))
    a = mk(con, FRAME, chain_id="ca")
    b = mk(con, GENERIC, chain_id="cb")
    con.close()
    plan = alt.run_dry(db, out_dir)
    con = sqlite3.connect(str(db))
    con.execute("UPDATE memories SET content=?, importance=61 WHERE id=?", (FRAME2, b))
    con.commit()
    con.close()
    audit = _apply(db, out_dir, plan)
    assert audit["rejects"]["drift"] == [b]
    assert audit["written"] == 1
    assert full_rows(db)[a]["is_archived"] == 1
    assert full_rows(db)[b]["is_archived"] == 0
    assert full_rows(db)[b]["importance"] == 61.0      # 别人的改动原样保留


def test_apply_rejects_newly_referenced_row(db, out_dir):
    con = sqlite3.connect(str(db))
    a = mk(con, FRAME, chain_id="ca")
    b = mk(con, GENERIC, chain_id="cb")
    con.close()
    plan = alt.run_dry(db, out_dir)
    con = sqlite3.connect(str(db))
    mk(con, "用户真实事件：养了一只猫", memory_type="event", sub_type="life_event",
       source="chat", parent_id=a)
    con.close()
    audit = _apply(db, out_dir, plan)
    assert audit["rejects"]["newly_protected"] == [a]
    assert full_rows(db)[a]["is_archived"] == 0
    assert full_rows(db)[b]["is_archived"] == 1


def test_apply_zero_write_when_backup_fails(db, out_dir, monkeypatch):
    con = sqlite3.connect(str(db))
    mk(con, FRAME, chain_id="ca")
    con.close()
    plan = alt.run_dry(db, out_dir)
    before = digest(db)

    def _boom(*_a, **_k):
        raise RuntimeError("模拟备份失败")

    monkeypatch.setattr(alt, "_online_backup", _boom)
    audit = alt.apply_plan(db, out_dir, plan, alt.APPLY_CONFIRM)
    assert audit["result"] == "aborted"
    assert "backup_failed" in audit["rejects"]
    assert audit["written"] == 0
    assert digest(db) == before                        # 备份失败 ⇒ 一行都不写


def test_apply_requires_explicit_confirm(db, out_dir):
    con = sqlite3.connect(str(db))
    mk(con, FRAME, chain_id="ca")
    con.close()
    plan = alt.run_dry(db, out_dir)
    before = digest(db)
    with pytest.raises(SystemExit):
        alt.apply_plan(db, out_dir, plan, "")
    with pytest.raises(SystemExit):
        alt.apply_plan(db, out_dir, plan, "yes")
    assert digest(db) == before


def test_apply_is_idempotent(db, out_dir):
    con = sqlite3.connect(str(db))
    mk(con, FRAME, chain_id="ca")
    con.close()
    first = _apply(db, out_dir)
    assert first["written"] == 1
    before = digest(db)
    second = _apply(db, out_dir)                       # 重跑干跑：已归档 ⇒ 保护，无新写
    assert second["written"] == 0
    assert second["result"] in ("rejected", "noop-empty-plan")
    assert digest(db) == before


# ── 5 restore ──

def test_restore_returns_original_values(db, out_dir):
    con = sqlite3.connect(str(db))
    a = mk(con, FRAME, chain_id="ca")
    b = mk(con, GENERIC, chain_id="cb")
    keep = mk(con, FRAME2, chain_id="cc", review_count=2)
    con.close()
    before = full_rows(db)
    audit = _apply(db, out_dir)
    res = alt.restore_apply(db, out_dir, audit, alt.RESTORE_CONFIRM)
    assert res["result"] == "restored" and res["restored"] == 2
    after = full_rows(db)
    for rid in (a, b, keep):
        assert after[rid] == before[rid]               # 原值逐字段回到归档前


def test_restore_refuses_after_third_party_edit(db, out_dir):
    con = sqlite3.connect(str(db))
    a = mk(con, FRAME, chain_id="ca")
    b = mk(con, GENERIC, chain_id="cb")
    con.close()
    audit = _apply(db, out_dir)
    con = sqlite3.connect(str(db))
    con.execute("UPDATE memories SET importance=88 WHERE id=?", (a,))
    con.commit()
    con.close()
    before = full_rows(db)
    res = alt.restore_apply(db, out_dir, audit, alt.RESTORE_CONFIRM)
    assert res["restored"] == 1                        # 只撤没被人动过的那条
    assert res["conflicts"]["fingerprint_drift"] == [a]
    assert res["result"] == "restored"
    cur = full_rows(db)
    assert before[b]["is_archived"] == 1 and cur[b]["is_archived"] == 0   # 未被动过的行撤回到原值
    assert cur[a]["is_archived"] == 1 and cur[a]["importance"] == 88.0


def test_restore_refuses_when_state_already_changed(db, out_dir):
    con = sqlite3.connect(str(db))
    a = mk(con, FRAME, chain_id="ca")
    con.close()
    audit = _apply(db, out_dir)
    con = sqlite3.connect(str(db))
    con.execute("UPDATE memories SET is_archived=0 WHERE id=?", (a,))   # 别人已自行恢复
    con.commit()
    con.close()
    before = full_rows(db)
    res = alt.restore_apply(db, out_dir, audit, alt.RESTORE_CONFIRM)
    assert res["restored"] == 0
    assert res["conflicts"]["state_not_ours"] == [a]
    assert res["result"] == "rejected"
    assert full_rows(db) == before                     # 整批零写入（回滚，不覆盖别人的恢复）


def test_restore_requires_confirm(db, out_dir):
    con = sqlite3.connect(str(db))
    mk(con, FRAME, chain_id="ca")
    con.close()
    audit = _apply(db, out_dir)
    before = digest(db)
    with pytest.raises(SystemExit):
        alt.restore_apply(db, out_dir, audit, alt.APPLY_CONFIRM)
    assert digest(db) == before


# ── 6 召回面语义 ──

def test_archived_rows_leave_recall_surface(db, out_dir):
    """归档位确实让这批退出召回：生产回填池带 is_archived=0，本例按同谓词实测。"""
    con = sqlite3.connect(str(db))
    a = mk(con, FRAME, chain_id="ca")
    b = mk(con, GENERIC, chain_id="cb")
    keep = mk(con, FRAME2, chain_id="cc", review_count=2)
    con.close()
    _apply(db, out_dir)

    def recall_pool(ids: list[int]) -> set[int]:
        con = sqlite3.connect(str(db))
        ph = ",".join("?" * len(ids))
        try:
            rows = con.execute(
                f"SELECT id FROM memories WHERE id IN ({ph}) AND is_archived = 0 "
                "AND memory_type != 'working_state' "
                "AND (status IS NULL OR status = 'active')", tuple(ids)).fetchall()
        finally:
            con.close()
        return {r[0] for r in rows}

    assert recall_pool([a, b, keep]) == {keep}
    plan = plan_of(db)
    assert plan["totals"]["shape_already_archived"] == 2
    assert plan["allowed_ids"] == []                   # 已归档＋有复习痕迹 ⇒ 无可归档面


def test_recall_semantics_still_filter_archived_in_retrieve_source():
    """护栏：召回出口若不再按 is_archived 过滤，本工具的「单置归档位」前提就失效。"""
    src = (BACKEND_ROOT / "app" / "memory" / "retrieve.py").read_text(encoding="utf-8")
    start = src.index("async def _rerank(")
    block = src[start:start + 3000]
    assert "is_archived == False" in block, "_rerank 回填池不再过滤 is_archived，需重判归档语义"


# ── 7 越界守卫与 CLI ──

def test_out_dir_must_be_outside_repo(db, tmp_path, capsys):
    inside = REPO_ROOT / "backend" / "_scratch_out"
    rc = alt.main(["--db", str(db), "--out", str(inside)])
    assert rc == 2
    assert not inside.exists()
    assert "仓库外" in capsys.readouterr().out


def test_cli_dry_run_reports_aggregates(db, out_dir, capsys):
    con = sqlite3.connect(str(db))
    mk(con, FRAME, chain_id="ca")
    mk(con, FRAME2, chain_id="cc", review_count=1)
    con.close()
    before = digest(db)
    rc = alt.main(["--db", str(db), "--out", str(out_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert digest(db) == before
    assert "小测" not in out and FRAME not in out
    assert "可归档=1" in out
