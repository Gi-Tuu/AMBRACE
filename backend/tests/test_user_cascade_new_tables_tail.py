# -*- coding: utf-8 -*-
"""删号 cascade：**新增表**的覆盖防御（audit-tail，2026-10-14，只加测试、零业务语义改动）。

已有锁（:mod:`tests.test_user_cascade_lock`）证的是「普查集合 ⊆ 候选 ∪ 例外」——那是
**分类**层面的覆盖。本文件补的是另一件事：**真把 DELETE 跑下去**之后，
「将来新加的一张表」到底会不会被带走、别人的行会不会被多带走。口径：

1. schema 来自**真跑 Alembic 整链 ``upgrade head``** 的临时库（``tmp_path_factory`` 建一次模板、
   每条用例复制到 ``tmp_path`` 私有库），**不用 ``_dbclone`` 模板克隆代替**——新增迁移的形态
   （新表 + 物理外键 ``ON DELETE RESTRICT``）必须在真实 DDL 上核验；
2. 「新增表」用**真实 ``CREATE TABLE`` DDL** 现场建出来（模拟下一版迁移落进来的形态），
   四张合成表：
   - ``tail_owner_note``（用户族：``user_id``，FK→users **RESTRICT**）
   - ``tail_char_artifact``（角色族：``character_id``，FK→ai_characters **RESTRICT**）
   - ``tail_probe_member_user``（归属列起名 ``member_user``：8 列口径看不见它 → 只有哨兵会红）
   - ``tail_invisible_badge``（列名 ``badge``：既不属于 8 列也不属于 4 类哨兵 → 真·未知表）
   另用一张 0 行的 ``tail_empty_note`` 验「新表当前没数据也在清除面上」。
   异名角色外键用例另建 ``tail_role_links``，验证单列识别、双列并集去重与他人行保留。
3. 断言全部调用**生产函数**：``user_cascade.discover_purge_plan`` /
   ``account_purge._delete_specs`` / ``account_purge._delete_order`` /
   ``account_purge._purge_table`` / ``account_purge._delete_root``，本文件不复制任何判定或拼 SQL 算法；
4. ``RESTRICT`` 是既有设计：本文件**验证它仍然生效**（别人的子行挡住父行删除），不改判据、
   不改成 CASCADE；
5. 已验表清单见上面 4 张合成表 + 真实迁移表 ``users`` / ``ai_characters`` / ``chat_groups`` /
   ``admin_audit_log`` / ``account_purge_jobs``。**本文件不代表全库 124 张表都已端到端删除**
   （全库分类覆盖在 test_user_cascade_lock 的锁 ①/②）。

数据全为合成（账号 id 101/102/103、角色 id 201/202，标签是占位字符串），不含任何真实聊天/昵称/记忆。
"""
import asyncio
import shutil
import sqlite3
from pathlib import Path

import pytest
from pytest import MonkeyPatch
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.application import account_purge as ap
from app.application import user_cascade as uc
from app.config import settings
from test_user_cascade_lock import _fill_ins

pytestmark = pytest.mark.slow

TGT_UID, OTH_UID, SUB_UID = 101, 102, 103      # 目标 / 旁观账号 / 目标的子账号
TGT_CID, OTH_CID = 201, 202                    # 目标名下角色 / 别人名下角色

#: 本文件现场建出来的「下一版迁移会新增」的表（真实 DDL，非模板库自带）
NEW_TABLE_DDL = (
    ("tail_owner_note", 'CREATE TABLE tail_owner_note (id INTEGER PRIMARY KEY, '
                        'user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT, '
                        'label TEXT)'),
    ("tail_char_artifact", 'CREATE TABLE tail_char_artifact (id INTEGER PRIMARY KEY, '
                           'character_id INTEGER NOT NULL REFERENCES ai_characters(id) '
                           'ON DELETE RESTRICT, note TEXT)'),
    ("tail_probe_member_user", 'CREATE TABLE tail_probe_member_user (id INTEGER PRIMARY KEY, '
                               'member_user INTEGER)'),
    ("tail_invisible_badge", 'CREATE TABLE tail_invisible_badge (id INTEGER PRIMARY KEY, '
                             'badge INTEGER REFERENCES users(id) ON DELETE RESTRICT)'),
    ("tail_empty_note", 'CREATE TABLE tail_empty_note (id INTEGER PRIMARY KEY, '
                        'user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT)'),
)


# ------------------------------------------------------------------ schema：真跑迁移

@pytest.fixture(scope="module")
def migrated_schema(tmp_path_factory) -> Path:
    """会话模板：临时库上跑完整 Alembic 链到 head（新增迁移形态只有在真链上才算核验）。"""
    from alembic import command
    from alembic.script import ScriptDirectory
    from app.db.migrate import _alembic_config

    root = tmp_path_factory.mktemp("tail_cascade_schema")
    base = root / "schema_head.db"
    mp = MonkeyPatch()
    try:
        mp.setattr(settings, "database_url", f"sqlite+aiosqlite:///{base.as_posix()}", raising=False)
        command.upgrade(_alembic_config(), "head")
        cfg = _alembic_config()
        head = ScriptDirectory.from_config(cfg).get_current_head()
        con = sqlite3.connect(str(base))
        try:
            assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == head
        finally:
            con.close()
    finally:
        mp.undo()
    return base


@pytest.fixture()
def tail_db(migrated_schema, tmp_path):
    """把模板 schema 复制成用例私有库 → 现场补建「新增表」→ 造合成数据 → 给生产函数用同一份库。"""
    db = tmp_path / "tail_cascade.db"
    shutil.copyfile(migrated_schema, db)

    con = sqlite3.connect(str(db))
    try:
        con.execute("PRAGMA foreign_keys=OFF")   # 建表与造数阶段：按依赖顺序自插
        for _name, ddl in NEW_TABLE_DDL:
            con.execute(ddl)
        cur = con.cursor()
        for uid, username in ((TGT_UID, "tail_tgt"), (OTH_UID, "tail_oth"), (SUB_UID, "tail_sub")):
            _fill_ins(cur, "users", ["id", "username", "nickname", "password_hash"],
                      [(uid, username, f"标签{uid}", "x")])
        # 子账号挂在目标名下（家庭根 scope 的语义来源）
        con.execute("UPDATE users SET parent_id = ? WHERE id = ?", (TGT_UID, SUB_UID))
        _fill_ins(cur, "ai_characters", ["id", "user_id", "name"],
                  [(TGT_CID, TGT_UID, "角色甲"), (OTH_CID, OTH_UID, "角色乙")])
        _fill_ins(cur, "tail_owner_note", ["id", "user_id", "label"],
                  [(1, TGT_UID, "目标的行"), (2, OTH_UID, "别人的行"), (3, SUB_UID, "子账号的行")])
        _fill_ins(cur, "tail_char_artifact", ["id", "character_id", "note"],
                  [(1, TGT_CID, "目标角色的"), (2, OTH_CID, "别人角色的")])
        _fill_ins(cur, "tail_probe_member_user", ["id", "member_user"],
                  [(1, TGT_UID), (2, OTH_UID)])
        _fill_ins(cur, "tail_invisible_badge", ["id", "badge"], [(1, TGT_UID)])
        # 家庭根共享表 + 永久保留表：删除语义上都不该被子账号 scope 带走
        _fill_ins(cur, "chat_groups", ["id", "user_id", "name"], [(1, TGT_UID, "家庭群")])
        _fill_ins(cur, "admin_audit_log", ["actor_user_id", "action"],
                  [(TGT_UID, "tail.a"), (SUB_UID, "tail.b")])
        _fill_ins(cur, "account_purge_jobs", ["user_id", "status"], [(TGT_UID, "done")])
        con.commit()
    finally:
        con.close()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db.as_posix()}", poolclass=NullPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record):     # 与 app/db/engine.py 同口径：引擎强制外键（RESTRICT 才生效）
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA foreign_keys=ON")
        finally:
            cur.close()

    yield db, async_sessionmaker(engine, expire_on_commit=False)
    engine.sync_engine.dispose()


# ------------------------------------------------------------------ 只调生产函数

def _plan(db_path, uid: int, scope: str) -> dict:
    """跑生产 :func:`discover_purge_plan`（AsyncSession 入口，与清除器同一个对象类型）。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}", poolclass=NullPool)

    async def _run():
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            return await uc.discover_purge_plan(db, user_id=uid, scope=scope)
    try:
        return asyncio.run(_run())
    finally:
        engine.sync_engine.dispose()


def _specs(plan) -> dict[str, str]:
    """生产清除器固化的「表 → WHERE 片段」（不是本文件自己拼的删除语句）。"""
    return {t: w for t, w in ap._delete_specs(plan)}


def _run_deletes(db_path, specs: dict[str, str], *, uid: int, cids: list[int]) -> dict[str, int]:
    """按生产清除器的真实顺序把 DELETE 跑一遍（``_delete_order`` + ``_purge_table``）。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}", poolclass=NullPool)

    async def _run():
        deleted: dict[str, int] = {}
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            order, _cyclic = await ap._delete_order(db, sorted(specs))
            for table in order:
                if table == ap.CHARACTER_ROOT:
                    continue      # 根表殿后，单独一条用例验 RESTRICT 挡住它
                deleted[table] = await ap._purge_table(db, table, specs[table], uid=uid, cids=cids)
        return order, deleted
    try:
        order, deleted = asyncio.run(_run())
    finally:
        engine.sync_engine.dispose()
    return deleted


def _count(db_path: Path, sql: str) -> int:
    con = sqlite3.connect(str(db_path))
    try:
        return int(con.execute(sql).fetchone()[0])
    finally:
        con.close()


# ═══════════════════════════════════════════════════════════════════════════════

def test_新增用户族表_真删除且别人的行保留(tail_db):
    """① 新表带 ``user_id``：不只是「被普查分类到」，DELETE 真的带它走——只带走目标账号的行。"""
    db_path, _factory = tail_db
    plan = _plan(db_path, TGT_UID, uc.SCOPE_DELETE_FAMILY_ROOT)
    specs = _specs(plan)
    assert "tail_owner_note" in specs, f"新表没进清除清单：{sorted(specs)[:5]}…"
    assert "tail_empty_note" in specs, "0 行的新表也必须进清除面（阈值口径按表计，不按当前行数）"

    deleted = _run_deletes(db_path, specs, uid=TGT_UID, cids=plan["character_ids"])
    # 归属判据是**按账号**（user_id = 目标本人）：家庭根 scope 只把「家庭共享表」纳入候选，
    # 不会把子账号的行一起吞掉（子账号另有自己的清除入口）→ 新表也只该带走目标本人那 1 行。
    assert deleted["tail_owner_note"] == 1, f"只该删掉目标账号自己的行：{deleted['tail_owner_note']}"
    assert deleted["tail_empty_note"] == 0
    assert _count(db_path, "SELECT COUNT(*) FROM tail_owner_note") == 2, "跨账号数据被多带走了"
    assert _count(db_path, "SELECT COUNT(*) FROM tail_owner_note WHERE user_id = 102") == 1
    assert _count(db_path, "SELECT COUNT(*) FROM tail_owner_note WHERE user_id = 103") == 1, \
        "家庭根清除顺手删掉了子账号的行（删除语义被动过）"
    # 别人的角色行与别人的账号都还在（跨账号保留）
    assert deleted["tail_char_artifact"] == 1, "角色族新表只按该账号名下角色集合删"
    assert _count(db_path, "SELECT COUNT(*) FROM tail_char_artifact WHERE character_id = 202") == 1


def test_新增角色族外键表_按角色归属删除并排在根表之前(tail_db):
    """② 新表靠 ``character_id`` 归属：计划认得它，且物理外键排序把它排在 ai_characters 前面。"""
    db_path, factory = tail_db
    plan = _plan(db_path, TGT_UID, uc.SCOPE_DELETE_FAMILY_ROOT)
    specs = _specs(plan)
    assert "tail_char_artifact" in specs

    async def _run():
        async with factory() as db:
            order, _cyclic = await ap._delete_order(db, sorted(specs))
            await ap._purge_table(db, "tail_char_artifact", specs["tail_char_artifact"],
                                  uid=TGT_UID, cids=plan["character_ids"])
            return order
    order = asyncio.run(_run())
    assert order.index("tail_char_artifact") < order.index(ap.CHARACTER_ROOT), \
        f"删序把 FK 子表放到父表之后就必炸：{order[:6]}…"
    assert order[-1] == ap.CHARACTER_ROOT, "角色根表殿后（既有设计）"
    assert _count(db_path, "SELECT COUNT(*) FROM tail_char_artifact") == 1
    assert _count(db_path, "SELECT character_id FROM tail_char_artifact") == OTH_CID


@pytest.mark.parametrize("dual_fk", [False, True], ids=["renamed-column", "two-columns"])
def test_异名角色外键_按并集清除且重叠只计一次(tail_db, dual_fk):
    """靠物理外键识别异名列；双列任一命中即删除，双方都命中仍只算一行。"""
    db_path, factory = tail_db
    table = "tail_role_links"
    columns = ["owner_character"] + (["peer_character"] if dual_fk else [])
    ddl = ", ".join(
        f"{column} INTEGER REFERENCES ai_characters(id) ON DELETE RESTRICT"
        for column in columns
    )
    rows = ([(1, TGT_CID, OTH_CID), (2, OTH_CID, TGT_CID),
             (3, TGT_CID, TGT_CID), (4, OTH_CID, OTH_CID)] if dual_fk
            else [(1, TGT_CID), (4, OTH_CID)])
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, {ddl})")
        placeholders = ", ".join("?" for _ in range(len(columns) + 1))
        con.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
        con.commit()
    finally:
        con.close()

    plan = _plan(db_path, TGT_UID, uc.SCOPE_DELETE_FAMILY_ROOT)
    entry = next(item for item in plan["character_family"] if item["table"] == table)
    assert {item["column"] for item in entry["columns"]} == set(columns)
    expected = 3 if dual_fk else 1
    assert entry["rows"] == expected, "双列命中的同一行不可重复计入清除预览"
    specs = _specs(plan)

    async def _run():
        async with factory() as db:
            order, _cyclic = await ap._delete_order(db, sorted(specs))
            assert order.index(table) < order.index(ap.CHARACTER_ROOT)
            return await ap._purge_table(db, table, specs[table],
                                         uid=TGT_UID, cids=plan["character_ids"])

    assert asyncio.run(_run()) == expected
    assert _count(db_path, f"SELECT COUNT(*) FROM {table}") == 1
    assert _count(db_path, f"SELECT id FROM {table}") == 4, "仅引用他人角色的行应原样保留"


def test_RESTRICT仍然生效_不改成CASCADE(tail_db):
    """③ 既有设计：物理外键是 RESTRICT——子行**挡住**父行删除，而不是被级联静默带走。

    钉住两件事：a) 新增表的 FK 是 RESTRICT 语义（改 CASCADE 就静默丢数据）；
    b) 清除器靠**顺序**（子表先删）而不是靠改 FK 语义来通过这道闸。
    """
    db_path, factory = tail_db
    plan = _plan(db_path, TGT_UID, uc.SCOPE_DELETE_FAMILY_ROOT)
    specs = _specs(plan)

    async def _try_delete_parent():
        async with factory() as db:
            return await ap._purge_table(db, ap.CHARACTER_ROOT,
                                         specs[ap.CHARACTER_ROOT], uid=TGT_UID,
                                         cids=plan["character_ids"])
    # 先删父表（刻意不按生产顺序）：新表的 RESTRICT 子行把它挡住，整条语句回滚
    with pytest.raises(Exception) as exc:
        asyncio.run(_try_delete_parent())
    assert "FOREIGN KEY" in str(exc.value).upper(), exc.value
    assert _count(db_path, "SELECT COUNT(*) FROM tail_char_artifact") == 2, \
        "级联把子行带走了 → RESTRICT 语义被动过"
    assert _count(db_path, "SELECT COUNT(*) FROM ai_characters") == 2, "失败的一趟删除留下半删状态"

    # 按生产清除器的真实顺序（子表先删）重跑：删得动，且只带走目标名下那个角色
    deleted = _run_deletes(db_path, specs, uid=TGT_UID, cids=plan["character_ids"])

    async def _delete_root_after_children():
        async with factory() as db:
            return await ap._delete_root(db, ap.CHARACTER_ROOT, TGT_CID)
    assert asyncio.run(_delete_root_after_children()) == 1
    assert deleted["tail_char_artifact"] == 1
    assert _count(db_path, "SELECT COUNT(*) FROM tail_char_artifact") == 1
    assert _count(db_path, "SELECT COUNT(*) FROM ai_characters WHERE id = 202") == 1, \
        "别人的角色被带走（归属判据越界）"


def test_归属列命名不规范_只有哨兵会红(tail_db):
    """④ 新表把归属列起名叫 ``member_user``：8 列口径扫不到它（计划里就是没有），哨兵必须点亮。

    这正是「普查集合覆盖」骗人的地方——分类锁 ① 恒为空不等于新表被覆盖了。
    """
    db_path, factory = tail_db
    plan = _plan(db_path, TGT_UID, uc.SCOPE_DELETE_FAMILY_ROOT)
    assert "tail_probe_member_user" not in _specs(plan), "8 列口径本就该看不见它（别把断言写成反向）"

    async def _run():
        async with factory() as db:
            return (await uc.unknown_ownership_columns(db),
                    await uc.unhandled_user_family_tables(db, scope=uc.SCOPE_DELETE_SUB_ACCOUNT))
    unknown, unhandled = asyncio.run(_run())
    assert {"table": "tail_probe_member_user", "column": "member_user"} in unknown, \
        "新增归属列命名不规范却不被哨兵抓到 → 漏表会静默发生"
    assert unhandled == []   # 分类锁依然为空：说明哨兵是唯一防线，两者不能互相替代


def test_真未知表_删账号根表必须失败且不留半删(tail_db):
    """⑤ 完全没有归属信号、只有 ``badge`` 列外键指向 users 的表：``users`` 删不动时必须抛错中止。

    既有设计是「抛 :class:`PurgeFKError` + 中止」而不是「静默跳过」或「改成 CASCADE 一了百了」：
    账本里拿到的是 **正在删的表名 + 语句原文 + 异常原文** 三件套，账号行与未知表行都还在。
    如实登记一个可定位性缺口（报告 §7）：SQLite 的约束错文本只有 ``FOREIGN KEY constraint
    failed``，**不含阻塞表名**，而 ``PRAGMA foreign_key_check`` 只报「父行已缺失」的孤儿行
    （这里父行还在，返回空）——所以「是哪张未知表挡住了删号」在当前回包/审计里读不出来。
    """
    db_path, factory = tail_db

    async def _run():
        async with factory() as db:
            return await ap._delete_root(db, ap.ACCOUNT_ROOT, TGT_UID)
    with pytest.raises(ap.PurgeFKError) as exc:
        asyncio.run(_run())
    assert exc.value.table == ap.ACCOUNT_ROOT
    assert 'DELETE FROM "users"' in exc.value.statement, exc.value.statement
    assert "FOREIGN KEY" in exc.value.cause.upper(), exc.value.cause
    # 中止而非半删：账号行仍在，未知表行也仍在（既不静默删、也不静默跳过）
    assert _count(db_path, "SELECT COUNT(*) FROM users WHERE id = 101") == 1
    assert _count(db_path, "SELECT COUNT(*) FROM tail_invisible_badge") == 1
    # 别的账号完全没被波及（越界删除的反证）
    assert _count(db_path, "SELECT COUNT(*) FROM users WHERE id IN (102, 103)") == 2


def test_保留表与家庭根表_跑完真实删除仍在库里(tail_db):
    """⑥ 删完之后按同一把尺子再数一次：永久保留表、子账号 scope 下的家庭根共享表原样还在。"""
    db_path, _factory = tail_db
    plan = _plan(db_path, SUB_UID, uc.SCOPE_DELETE_SUB_ACCOUNT)
    specs = _specs(plan)
    for kept in ("admin_audit_log", "account_purge_jobs", "chat_groups"):
        assert kept not in specs, f"{kept} 不该出现在子账号清除清单里"
    assert "tail_owner_note" in specs   # 子账号自己的新表行照走

    _run_deletes(db_path, specs, uid=SUB_UID, cids=plan["character_ids"])
    assert _count(db_path, "SELECT COUNT(*) FROM tail_owner_note WHERE id = 3") == 0
    assert _count(db_path, "SELECT COUNT(*) FROM admin_audit_log") == 2, "append-only 合规记录被删了"
    assert _count(db_path, "SELECT COUNT(*) FROM account_purge_jobs") == 1
    assert _count(db_path, "SELECT COUNT(*) FROM chat_groups") == 1, "删子账号带走了整户群聊"
    assert _count(db_path, "SELECT COUNT(*) FROM users WHERE id = 101") == 1
