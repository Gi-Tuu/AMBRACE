# -*- coding: utf-8 -*-
"""B2b/B2c 候选（副本正式接入；主仓库仍未接入）：把 ai_characters.id 重建为 SQLITE AUTOINCREMENT。

本文件位于副本 backend/alembic/versions，是 B2b 正式接入候选；主仓库/正式链尚未接入，
仍不得放入主仓库，直到主审接收并安排部署窗口。

关键点：
- SQLite 的 AUTOINCREMENT 必须写成内联 ``INTEGER PRIMARY KEY AUTOINCREMENT``；
  表级 ``PRIMARY KEY (id)`` + ``autoincrement=True`` 不会生成它。
- 采用 SQLite 官方 12 步表重建：foreign_keys 在事务外关闭，复制行，DROP 旧父表，
  RENAME 新表；子表 FK 仍按名字引用 ai_characters，重建后继续有效。
- **高水位预检在第一次写/禁 FK 之前完成**：只统计有证据的角色引用列；真实 SQLite
  typeof 必须是 integer；正整数有效、0/NULL 为合法哨兵；负数/REAL/TEXT/超 int64/
  超客户端安全上限/无下一可用 ID 一律拒绝；未知语义的同名列若出现非零值也拒绝并报告。
- 本迁移 import app.db.migrate 的高水位/序列 helper：这是当前单一真源取舍，历史重放
  兼容责任见 CORRECTIONS/DECISION——未来改 helper 语义必须同步评估历史迁移重放。
- B2c：重建表带 CHECK ``ck_ai_characters_client_safe_id``；已 AUTOINCREMENT 的早退分支
  与正常重建末尾都调用 character_id_guard 安装/核验 AFTER 守卫（BEFORE 时 NEW.id 不可靠）。
- 历史已删除且无任何存活引用的最大 id 无法从现库恢复；候选不声称“历史上从未复用”。
- downgrade 拒绝：降级会重新引入 ID 复用，恢复路径是迁移前备份。
"""
import re

from alembic import op

from app.db.character_id_guard import (
    CHECK_NAME,
    CLIENT_SAFE_MAX_ID,
    ensure_client_safe_id_guard,
)
from app.db.migrate import repair_ai_character_sequence, scan_character_id_watermark

revision = "b2a0c1d2e3f4"
down_revision = "e2f4a6b8c0d2"
branch_labels = None
depends_on = None

TABLE = "ai_characters"
NEW_TABLE = "ai_characters_b2a_new"


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _table_sql(conn, name: str) -> str | None:
    row = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row[0] if row else None


def _tables(conn) -> list[str]:
    return [
        r[0]
        for r in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
    ]


def _columns(conn, table: str) -> list[str]:
    return [r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({_q(table)})").fetchall()]


def _build_new_ddl(old_sql: str) -> str:
    if not old_sql or "ai_characters" not in old_sql.lower():
        raise RuntimeError("B2a: ai_characters DDL not found")
    if "AUTOINCREMENT" in old_sql.upper():
        return ""
    new_sql = re.sub(
        r'CREATE\s+TABLE\s+"?ai_characters"?',
        f'CREATE TABLE "{NEW_TABLE}"',
        old_sql,
        count=1,
        flags=re.I,
    )
    new_sql, n_id = re.subn(
        r'(\bid\s+INTEGER\s+NOT\s+NULL)\s*,',
        r"\1 PRIMARY KEY AUTOINCREMENT,",
        new_sql,
        count=1,
        flags=re.I,
    )
    new_sql, n_pk = re.subn(
        r",\s*\n\s*PRIMARY\s+KEY\s*\(\s*id\s*\)",
        "",
        new_sql,
        count=1,
        flags=re.I,
    )
    if n_id != 1 or n_pk != 1:
        raise RuntimeError(
            "B2a: ai_characters DDL shape not recognized "
            f"(id_match={n_id}, pk_match={n_pk}); refusing to rebuild"
        )
    if CHECK_NAME not in new_sql and f"id <= {CLIENT_SAFE_MAX_ID}" not in new_sql:
        new_sql = new_sql.rstrip()
        if not new_sql.endswith(")"):
            raise RuntimeError("B2a: rebuilt DDL shape not recognized")
        new_sql = (
            new_sql[:-1].rstrip().rstrip(",")
            + f',\n\tCONSTRAINT "{CHECK_NAME}" CHECK (id <= {CLIENT_SAFE_MAX_ID})\n)'
        )
    return new_sql


def upgrade() -> None:
    conn = op.get_bind()
    old_sql = _table_sql(conn, TABLE)
    if old_sql is None:
        raise RuntimeError("B2a: ai_characters table missing")
    if "AUTOINCREMENT" in old_sql.upper():
        # 已有目标约束（例如旧链 harden 迁移按新模型重建过）：不能静默早退，
        # 仍要按存活引用修正序列；类型/范围/未知语义错误在写前抛出。
        fixed = repair_ai_character_sequence(conn)
        if fixed["changed"]:
            print(
                "B2b: ai_characters AUTOINCREMENT already present; "
                f"sequence repaired {fixed['before']}->{fixed['after']}"
            )
        ensure_client_safe_id_guard(conn)
        return
    if _table_sql(conn, NEW_TABLE) is not None:
        raise RuntimeError(
            "B2a: leftover ai_characters_b2a_new exists; refusing to overwrite. "
            "Restore the pre-migration backup and investigate."
        )

    # R3：任何写/禁 FK/DDL 之前完成类型、范围、未知语义与高水位预检。
    scan = scan_character_id_watermark(conn)
    high = scan["high"]
    if scan["unknown_columns"]:
        print(
            "B2b high-water: unclassified reference columns recorded (zero/NULL only, "
            f"not used for high water): {scan['unknown_columns']}"
        )

    old_columns = _columns(conn, TABLE)
    objects = conn.exec_driver_sql(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL "
        "ORDER BY type, name",
        (TABLE,),
    ).fetchall()
    new_sql = _build_new_ddl(old_sql)
    if not new_sql:
        return

    ctx = op.get_context()
    # PRAGMA foreign_keys 在事务内是 no-op；必须在事务外关闭，避免 DROP 父表触发
    # 子表 ON DELETE CASCADE/SET NULL。
    with ctx.autocommit_block():
        op.execute("PRAGMA foreign_keys=OFF")

    conn.exec_driver_sql(new_sql)
    # B2A_FAIL_AFTER_CREATE: 新表已创建、尚未复制
    cols = ", ".join(_q(c) for c in old_columns)
    conn.exec_driver_sql(
        f'INSERT INTO {_q(NEW_TABLE)} ({cols}) SELECT {cols} FROM {_q(TABLE)}'
    )
    # B2A_FAIL_AFTER_COPY: 新表已复制、尚未 DROP
    conn.exec_driver_sql(f"DROP TABLE {_q(TABLE)}")
    # B2A_FAIL_AFTER_DROP: 旧父表已 DROP、尚未 RENAME
    conn.exec_driver_sql(
        f"ALTER TABLE {_q(NEW_TABLE)} RENAME TO {_q(TABLE)}"
    )
    # B2A_FAIL_AFTER_RENAME: 新表已改名、尚未重建索引/触发器与序列
    for _type, _name, sql in objects:
        conn.exec_driver_sql(sql)

    has_seq = conn.exec_driver_sql(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
    ).scalar()
    if not has_seq:
        raise RuntimeError("B2a: sqlite_sequence missing after AUTOINCREMENT rebuild")
    conn.exec_driver_sql(
        "DELETE FROM sqlite_sequence WHERE name IN (?, ?)", (TABLE, NEW_TABLE)
    )
    conn.exec_driver_sql(
        "INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)", (TABLE, high)
    )
    # B2A_FAIL_AFTER_SEQUENCE: 序列已设置、尚未最终 FK 校验

    bad = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if bad:
        raise RuntimeError(f"B2a: foreign_key_check failed after rebuild: {bad[:10]}")
    # B2A_FAIL_AFTER_CHECK: FK 校验已过、尚未重新开启 foreign_keys

    with ctx.autocommit_block():
        op.execute("PRAGMA foreign_keys=ON")
    ensure_client_safe_id_guard(conn)


def downgrade() -> None:
    raise RuntimeError(
        "B2a downgrade refused: reverting to the old ai_characters DDL would reintroduce "
        "SQLite id reuse. Restore the pre-migration database backup instead."
    )
