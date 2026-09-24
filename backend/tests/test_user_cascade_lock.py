# -*- coding: utf-8 -*-
"""删号 cascade **自动发现**的测试锁（控制台删号·第一期第一批，2026-09-24 派单第 4 项）。

锁的是什么（方案 v2 §二.4 / §3.2）
----------------------------------
① 任何带**用户族归属列**的表，必须被 :mod:`app.application.user_cascade` 分类成
   「删除候选」或「例外（带理由）」——不许出现第三种情况（漏表＝静默孤儿）；
② 任何按 **``character_id``／外键 → ``ai_characters``** 归属的表同样必须被分类
   （v2 实测：只扫用户族会漏 24 张表，删 ``ai_characters`` 时直接外键错）。

两条锁都是「**普查**（扫实际库结构）vs **计划回包**（候选 ∪ 例外）」的集合差 ——
判据来自 ``PRAGMA``，不来自文档、也不来自 ORM 模型清单，所以：
- 插件表（``douyin_*`` / ``wechat_ilink_*``）即使主 ``Base.metadata`` 没注册也被发现；
- 用例**不硬编码生产表数**：沙箱里显式建一张归属列起名 ``member_user`` 的表，
  哨兵 :func:`user_cascade.unknown_ownership_columns` 必须点亮（证明锁不是空转）。

口径：临时库走 ``tests/_dbclone`` 克隆（``with_plugins`` 带插件表），绝不碰 backend/data。
"""
import asyncio
import sqlite3
from pathlib import Path

import pytest

from _dbclone import clone_engine, make_session_factory
from app.application import user_cascade as uc

# 快测档：每例一次克隆 + 少量裸 INSERT（毫秒级），与 test_admin_console_p2 同档。
pytestmark = pytest.mark.slow

ROOT_UID, SUB_UID, OTHER_UID = 1, 2, 3
CLASH_CHAR, OWN_CHAR_A, OWN_CHAR_B, FOREIGN_CHAR = 1, 11, 12, 13  # CLASH_CHAR.id == ROOT_UID


def _hash(pw: str) -> str:
    import bcrypt
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def _fill_ins(cur, table: str, cols: list[str], rows: list[tuple]):
    """裸 INSERT：缺失的 NOT NULL 列按类型补默认值（省掉逐表核对建表约束）。"""
    info = {r[1]: r for r in cur.execute(f'PRAGMA table_info("{table}")')}
    if not info:
        raise AssertionError(f"沙箱库里没有表 {table}（建库/插件未装载？）")
    extra = [c for c, r in info.items()
             if c not in cols and r[3] and r[4] is None and not r[5]]
    allcols = list(cols) + extra
    ph = ",".join("?" * len(allcols))
    for row in rows:
        pad = tuple(
            0 if "INT" in info[c][2].upper() or info[c][2].upper() in ("INTEGER", "BIGINT")
            else ("2026-01-01 00:00:00" if ("DATE" in info[c][2].upper() or "TIME" in info[c][2].upper()) else "")
            for c in extra
        )
        cur.execute(f'INSERT INTO "{table}" ({",".join(allcols)}) VALUES ({ph})', tuple(row) + pad)


@pytest.fixture()
def cascade_db(tmp_path):
    """临时库：root(1) 有 2 角色 / sub(2) / other root(3) 有 1 个 id 与 root 撞号的角色。"""
    dst: Path = tmp_path / "cascade.db"
    engine = clone_engine(dst, with_plugins=("douyin_mcp", "wechat_ilink"))
    factory = make_session_factory(engine)

    async def _seed_users():
        from app.models.user import User
        async with factory() as db:
            db.add_all([
                User(id=ROOT_UID, username="root", nickname="根", is_admin=True,
                     server_admin=True, password_hash=_hash("rootpass123")),
                User(id=SUB_UID, username="sub", nickname="子", parent_id=ROOT_UID,
                     password_hash=_hash("subpass123")),
                User(id=OTHER_UID, username="other", nickname="别根", is_admin=True,
                     password_hash=_hash("rootpass123")),
            ])
            await db.commit()

    asyncio.run(_seed_users())
    con = sqlite3.connect(str(dst))
    try:
        cur = con.cursor()
        # 角色：OWN_* 归 root；CLASH_CHAR 归别人且 id 正好等于 ROOT_UID（双语义撞号）
        _fill_ins(cur, "ai_characters", ["id", "user_id", "name"],
                  [(CLASH_CHAR, OTHER_UID, "撞号角色"), (OWN_CHAR_A, ROOT_UID, "甲"),
                   (OWN_CHAR_B, ROOT_UID, "乙"), (FOREIGN_CHAR, OTHER_UID, "别家角色")])
        # memories：speaker_id 三种取值各一行（本人=SUB_UID 明确 / 自家角色 / 撞号值）
        _fill_ins(cur, "memories",
                  ["id", "user_id", "character_id", "content", "importance", "speaker_id", "speaker_type"],
                  [(1, ROOT_UID, OWN_CHAR_A, "m", 50, CLASH_CHAR, "character"),   # 判定不出
                   (2, ROOT_UID, OWN_CHAR_A, "m", 50, OWN_CHAR_A, "character"),    # 自家角色
                   (3, ROOT_UID, OWN_CHAR_A, "m", 50, SUB_UID, "user"),            # 本人发言
                   (4, SUB_UID, OWN_CHAR_A, "m", 50, SUB_UID, "user")])            # 子账号自己的
        _fill_ins(cur, "chat_sessions", ["id", "user_id", "character_id", "title"],
                  [(1, ROOT_UID, OWN_CHAR_A, "t"), (2, SUB_UID, OWN_CHAR_A, "t")])
        # 家庭根归属表（整户共享）：删子账号不该带走
        _fill_ins(cur, "chat_groups", ["id", "user_id", "name"], [(1, ROOT_UID, "家庭群")])
        _fill_ins(cur, "chat_group_members", ["id", "group_id", "character_id"],
                  [(1, 1, OWN_CHAR_A), (2, 1, OWN_CHAR_B)])
        _fill_ins(cur, "chat_group_messages", ["id", "group_id", "character_id", "content", "notify_user"],
                  [(1, 1, OWN_CHAR_A, "hi", 0)])
        _fill_ins(cur, "plugin_consents", ["plugin_name", "tenant_id", "permissions_json", "consented_at"],
                  [("x", ROOT_UID, "[]", "2026-01-01")])
        _fill_ins(cur, "channel_bindings", ["id", "channel", "tenant_id", "owner_user_id",
                                            "character_id", "enabled"],
                  [(1, "webhook", ROOT_UID, ROOT_UID, OWN_CHAR_A, 1)])
        _fill_ins(cur, "device_action_targets", ["id", "tenant_id", "target"], [(1, ROOT_UID, "t")])
        _fill_ins(cur, "shared_events", ["id", "user_id", "character_id", "event_type", "title"],
                  [(1, ROOT_UID, OWN_CHAR_A, "anniversary", "e")])
        # llm_usage：group_owner_id 是家庭根归因列（列级例外）
        _fill_ins(cur, "llm_usage", ["id", "user_id", "model", "prompt_tokens",
                                     "completion_tokens", "total_tokens", "group_owner_id"],
                  [(1, ROOT_UID, "m", 1, 1, 2, ROOT_UID), (2, SUB_UID, "m", 1, 1, 2, ROOT_UID)])
        # 「最后编辑者」标记：服务器默认额度全库只有 id=1 一行，updated_by 记的是改过它的管理员
        _fill_ins(cur, "llm_usage_limits", ["id", "total_limit", "updated_by"], [(1, 100, ROOT_UID)])
        # append-only 合规记录（永久保留）
        _fill_ins(cur, "admin_audit_log", ["actor_user_id", "action"], [(ROOT_UID, "a"), (SUB_UID, "b")])
        _fill_ins(cur, "domain_events", ["aggregate_type", "aggregate_id", "event_type",
                                         "actor_type", "actor_id", "payload_json"],
                  [("memory", "1", "created", "user", ROOT_UID, "{}")])
        con.commit()
    finally:
        con.close()
    yield factory
    engine.sync_engine.dispose()


def _plan(factory, uid: int, scope: str) -> dict:
    async def _run():
        async with factory() as db:
            return await uc.discover_purge_plan(db, user_id=uid, scope=scope)
    return asyncio.run(_run())


def _entry(plan: dict, table: str, key: str):
    return next((t for t in plan[key] if t["table"] == table), None)


def _census_diff(factory, uid: int, scope: str, family: str) -> list[str]:
    """普查表 − （该族候选 ∪ 生效例外表）＝ 逃逸分类的表（必须为空）。"""
    async def _run():
        async with factory() as db:
            plan = await uc.discover_purge_plan(db, user_id=uid, scope=scope)
            census = (await uc.user_family_census(db) if family == "user"
                      else await uc.character_family_census(db))
            return plan, census
    plan, census = asyncio.run(_run())
    covered = {t["table"] for t in plan[family + "_family"]} | {e["table"] for e in plan["exceptions"]}
    if family == "character" and not plan["character_ids"]:
        return []  # 名下无角色时角色族片段天然匹配 0 行，计划里不出现这些表是正确行为
    return [c["table"] for c in census if c["table"] not in covered]


# ═══════════════════════════════════════════════════════════════════════════════
# 锁 ① / 锁 ②
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("scope", uc.SCOPES)
def test_lock1_user_family_fully_classified(cascade_db, scope):
    """锁 ①：带用户族归属列的表 100% 落进「候选 ∪ 例外」，且模块自检为空。"""
    diff = _census_diff(cascade_db, ROOT_UID, scope, "user")
    assert diff == [], f"这些表既没进候选也没拿到例外理由：{diff}"
    assert asyncio.run(_coro(uc.unhandled_user_family_tables, cascade_db, scope)) == []
    plan = _plan(cascade_db, ROOT_UID, scope)
    assert len(plan["user_family"]) >= 40, "普查规模异常（沙箱建库没建全，锁会空转）"


@pytest.mark.parametrize("scope", uc.SCOPES)
def test_lock2_character_family_fully_classified(cascade_db, scope):
    """锁 ②：按 character_id/外键归属的表同样 100% 被分类（v2 漏掉的 24 张就在这族里）。"""
    diff = _census_diff(cascade_db, ROOT_UID, scope, "character")
    assert diff == [], f"这些角色族表被漏掉：{diff}"
    assert asyncio.run(_coro(uc.unhandled_character_family_tables, cascade_db, scope)) == []
    plan = _plan(cascade_db, ROOT_UID, scope)
    assert plan["character_ids"] == [OWN_CHAR_A, OWN_CHAR_B]  # 撞号角色不属于它
    assert len(plan["character_family"]) >= 20, "角色族覆盖异常（外键扫描失效会让这族塌成 0）"


def test_character_only_tables_are_not_missed(cascade_db):
    """v2 关键数字：存在「只有角色归属、没有任何用户归属列」的表，且它们全在计划里。"""
    async def _run():
        async with cascade_db() as db:
            return await uc.user_family_census(db), await uc.character_family_census(db)
    uf, cf = asyncio.run(_run())
    user_tables = {c["table"] for c in uf}
    char_only = [c["table"] for c in cf if c["table"] not in user_tables]
    assert char_only, "沙箱里至少应有 chat_group_members 这类只按角色归属的表"
    plan = _plan(cascade_db, ROOT_UID, uc.SCOPE_DELETE_FAMILY_ROOT)
    covered = {t["table"] for t in plan["character_family"]} | {e["table"] for e in plan["exceptions"]}
    assert set(char_only) <= covered, f"只按角色归属的表被漏：{set(char_only) - covered}"


def test_plugin_tables_are_discovered_from_db_structure(cascade_db):
    """插件表不在主 ``Base.metadata``：判据必须是**实际库结构**（含 ``douyin_*`` / ``wechat_ilink_*``）。"""
    async def _run():
        async with cascade_db() as db:
            return await uc.user_family_census(db), await uc.character_family_census(db)
    uf, cf = asyncio.run(_run())
    names = {c["table"] for c in uf} | {c["table"] for c in cf}
    plugin_named = [n for n in names if n.startswith("douyin_") or n.startswith("wechat_ilink_")]
    assert plugin_named, f"插件表没被发现（自动发现退化成只看 ORM 模型）：{sorted(names)[:5]}…"


def test_tripwire_lights_on_unknown_ownership_column(cascade_db, tmp_path):
    """哨兵非空洞证明：显式造一张归属列起名 ``member_user`` 的表 → 必须点亮。

    派单口径「不许硬编码生产表数」——这里不比对表数量级，只验证「新增一列命名不规范就红灯」。
    """
    con = sqlite3.connect(str(tmp_path / "cascade.db"))
    con.execute("CREATE TABLE probe_owned (id INTEGER PRIMARY KEY, member_user INTEGER)")
    con.execute("INSERT INTO probe_owned (id, member_user) VALUES (1, 2)")
    con.commit()
    con.close()

    async def _run():
        async with cascade_db() as db:
            return await uc.unknown_ownership_columns(db)
    hits = asyncio.run(_run())
    assert {"table": "probe_owned", "column": "member_user"} in hits, hits
    # 锁 ①/② 仍为空：它没有 8 列口径内的列，也没有角色列 → 本来就不属于两族
    assert asyncio.run(_coro(uc.unhandled_user_family_tables, cascade_db,
                             uc.SCOPE_DELETE_SUB_ACCOUNT)) == []


async def _coro(fn, factory, scope):
    async with factory() as db:
        return await fn(db, scope=scope)


# ═══════════════════════════════════════════════════════════════════════════════
# 例外语义
# ═══════════════════════════════════════════════════════════════════════════════

def test_append_only_records_retained_in_both_scopes(cascade_db):
    """① 永久保留：``admin_audit_log`` 两 scope 都不进候选（带理由），``domain_events`` 同。"""
    for scope in uc.SCOPES:
        plan = _plan(cascade_db, ROOT_UID, scope)
        assert _entry(plan, "admin_audit_log", "user_family") is None
        assert _entry(plan, "admin_audit_log", "character_family") is None
        exc = {e["table"]: e["reason"] for e in plan["exceptions"]}
        assert "admin_audit_log" in exc and "合规" in exc["admin_audit_log"]
        assert "domain_events" in uc.RETAINED_TABLES  # 它用 actor_id，天然不在 8 列里


def test_family_root_tables_switch_with_scope(cascade_db):
    """② 家庭根归属：删子账号时是例外，删家庭根时进候选（整户一起走）。"""
    sub = _plan(cascade_db, SUB_UID, uc.SCOPE_DELETE_SUB_ACCOUNT)
    exc_tables = {e["table"] for e in sub["exceptions"]}
    for table in ("chat_groups", "shared_events", "plugin_consents", "channel_bindings",
                  "device_action_targets"):
        assert table in exc_tables, table
        assert _entry(sub, table, "user_family") is None, table
    # 列级例外：整张 llm_usage 该删，group_owner_id 这一列不该动
    assert {"table": "llm_usage", "column": "group_owner_id"} in [
        {k: e.get(k) for k in ("table", "column")} for e in sub["exceptions"]]
    assert _entry(sub, "llm_usage", "user_family") is not None

    root = _plan(cascade_db, ROOT_UID, uc.SCOPE_DELETE_FAMILY_ROOT)
    for table in ("chat_groups", "shared_events", "plugin_consents", "channel_bindings"):
        entry = _entry(root, table, "user_family")
        assert entry is not None and entry["rows"] >= 1, table
    assert "llm_usage" not in {e["table"] for e in root["exceptions"] if not e["column"]}
    # v2 护栏：家庭根即使名下无人，也要显式提示它带走家庭共享数据
    assert {w["table"] for w in root["warnings"]} >= {"chat_groups", "plugin_consents"}


def test_sub_account_scope_does_not_reach_into_the_family(cascade_db):
    """子账号只删自己那部分：家庭根用量行留在库里（``group_owner_id`` 不匹配他人）。"""
    plan = _plan(cascade_db, SUB_UID, uc.SCOPE_DELETE_SUB_ACCOUNT)
    entry = _entry(plan, "llm_usage", "user_family")
    assert entry["rows"] == 1  # 只有 id=4 那一行
    sess = _entry(plan, "chat_sessions", "user_family")
    assert sess["rows"] == 1
    assert plan["character_ids"] == []  # 子账号名下无角色 → 角色族不参与
    # uid=2 没有被任何角色占用 → speaker_id=2 判得明「本人在说话」（含 root 行里引用它的发言者）
    speaker = next(c for c in _entry(plan, "memories", "user_family")["columns"]
                   if c["column"] == "speaker_id")
    assert (speaker["rows_user"], speaker["rows_character"], speaker["rows_undetermined"]) == (2, 0, 0)
    # 但「speaker 是这个子账号」不构成归属：别人的记忆行不会因为它的发言而被删
    assert _entry(plan, "memories", "user_family")["rows"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 列语义（speaker_id 双语义 / updated_by 编辑器标记）
# ═══════════════════════════════════════════════════════════════════════════════

def test_speaker_id_dual_semantic_counts_unattributable_separately(cascade_db):
    """``speaker_id``：值撞别人角色 id 的行**单独计数、不盲删**（v2 §3.2 第 0 步）。

    本表另有更硬的 ``user_id`` → 该列**不作为删除依据**（否则会越界删别人的行），
    但三档归属判定照常做，判定不出的行数必须报出来。
    """
    plan = _plan(cascade_db, ROOT_UID, uc.SCOPE_DELETE_FAMILY_ROOT)
    entry = _entry(plan, "memories", "user_family")
    col = next(c for c in entry["columns"] if c["column"] == "speaker_id")
    assert col["kind"] == uc.KIND_DUAL
    assert col["deletable"] is False and col["rows"] == 0
    # 沙箱里角色 id=1 归别人（OTHER_UID），而目标 uid 也是 1 → speaker_id=1 判不出归属
    assert col["rows_undetermined"] == 1      # memories.id=1
    assert col["rows_character"] == 1         # memories.id=2（speaker_id=11 = 自家角色）
    assert col["rows_user"] == 0              # uid=1 被别家角色占用 → 一行都不敢认成本人发言
    assert plan["undetermined_speaker_rows"] == [
        {"table": "memories", "column": "speaker_id", "rows": 1}]
    assert plan["totals"]["undetermined_rows"] == 1
    # 判定不出的那行仍随该账号自己的 user_id 归属被删（memories.id=1 的 user_id 就是 root）
    assert entry["rows"] == 4  # 1/2/3（user_id=root）+ 4（自家角色 11 的行）


def test_speaker_id_is_the_predicate_when_it_is_the_only_owner_column(cascade_db, tmp_path):
    """反向护栏：若某表**只**靠 ``speaker_id`` 归属（无 user_id/角色列），它就必须是删除依据。

    「不作为删除依据」只适用于「同表有更硬的归属列」，不是把这一列整体作废。
    """
    con = sqlite3.connect(str(tmp_path / "cascade.db"))
    con.execute("CREATE TABLE probe_speaker_only (id INTEGER PRIMARY KEY, speaker_id INTEGER)")
    con.executemany("INSERT INTO probe_speaker_only (id, speaker_id) VALUES (?, ?)",
                    [(1, CLASH_CHAR), (2, OWN_CHAR_A), (3, SUB_UID)])
    con.commit()
    con.close()
    plan = _plan(cascade_db, ROOT_UID, uc.SCOPE_DELETE_FAMILY_ROOT)
    entry = _entry(plan, "probe_speaker_only", "user_family")
    col = next(c for c in entry["columns"] if c["column"] == "speaker_id")
    assert col["deletable"] is True
    assert (col["rows_user"], col["rows_character"], col["rows_undetermined"]) == (0, 1, 1)
    assert col["rows"] == 1          # 只删判得明归属的（speaker=11），speaker=1 那行不盲删
    assert entry["rows"] == 1
    assert {"table": "probe_speaker_only", "column": "speaker_id", "rows": 1} in \
        plan["undetermined_speaker_rows"]


def test_updated_by_is_measured_but_never_a_delete_predicate(cascade_db):
    """``updated_by`` 是「最后编辑者」标记：按它删会清空全局单行配置 → 只登记、不删。"""
    plan = _plan(cascade_db, ROOT_UID, uc.SCOPE_DELETE_FAMILY_ROOT)
    entry = _entry(plan, "llm_usage_limits", "user_family")
    col = next(c for c in entry["columns"] if c["column"] == "updated_by")
    assert col["kind"] == uc.KIND_EDITOR and col["deletable"] is False
    assert col["rows"] == 0 and col["rows_matched"] == 1
    assert plan["totals"]["editor_only_rows"] == 1
    assert entry["rows"] == 0
    # 不计入清除体量：只登记不删的表不能把 totals.tables 撑大（阈值判据会失真）
    assert "llm_usage_limits" not in {t["table"] for t in plan["user_family"] if t["rows"]}


# ═══════════════════════════════════════════════════════════════════════════════
# 纯读 / 失败要响
# ═══════════════════════════════════════════════════════════════════════════════

def test_discovery_is_pure_read_zero_writes(cascade_db, tmp_path):
    """自动发现必须逐表零写入（跑完 dry-run 之后全库行数一字不变）。"""
    dst = tmp_path / "cascade.db"
    before = _all_counts(dst)
    assert sum(before.values()) > 0, "种子没落库（fixture 与用例不在同一个 tmp_path？）"
    for scope in uc.SCOPES:
        _plan(cascade_db, ROOT_UID, scope)
    assert _all_counts(dst) == before


def test_illegal_inputs_raise_readable_error(cascade_db):
    """失败要抛可读错误，不静默跳过（静默跳过正是「漏表」的形态）。"""
    async def _bad_scope():
        async with cascade_db() as db:
            await uc.discover_purge_plan(db, user_id=ROOT_UID, scope="delete_everything")
    with pytest.raises(uc.UserCascadeError, match="scope"):
        asyncio.run(_bad_scope())

    async def _bad_uid():
        async with cascade_db() as db:
            await uc.discover_purge_plan(db, user_id=0, scope=uc.SCOPE_DELETE_SUB_ACCOUNT)
    with pytest.raises(uc.UserCascadeError, match="正整数"):
        asyncio.run(_bad_uid())

    # 表/列名来自 sqlite_master，异常值一律拒绝拼 SQL（PRAGMA 不吃绑定参数 → 白名单是唯一防线）
    with pytest.raises(uc.UserCascadeError, match="标识符"):
        uc._q('bad"name')


def _all_counts(dst: Path) -> dict:
    con = sqlite3.connect(str(dst))
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        con.close()
