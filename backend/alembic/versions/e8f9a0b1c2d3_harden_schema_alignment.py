# -*- coding: utf-8 -*-
"""硬化批 schema 对齐——P3-2 外键 / P3-3 NOT NULL 漂移 / P3-4 索引（2026-09-17）。

Revision ID: e8f9a0b1c2d3
Revises: b2c3d4e5f6a7
Create Date: 2026-09-17

背景：全量检查报告 §4 P3-2/3/4——``alembic`` 整链重放出来的库与 ``Base.metadata.create_all``
建出的库存在三处 schema 漂移（生产本机库走 init_db/create_all，故漂移只命中「整链重放」路径）。
本迁移把**迁移侧**向 ORM（单一事实源）收敛；已对齐库/新链路 0 操作，可重复执行。

1) **P3-2 外键补齐**：``ai_characters.user_llm_config_id`` 由 c8d9d0e1f2a3 的裸 ``Integer``
   （SQLite ``add_column`` 无法带外键）补成 ``ForeignKey("user_llm_configs.id", ondelete="SET NULL")``
   ——与 ORM ``AiCharacter.user_llm_config_id``（本次同步补 ``ondelete``）及
   ``llm_config_service.delete_config`` 的「删配置自动清角色引用」语义一致。
   开外键前先只读查「悬挂引用」（非 NULL 且指向不存在的 user_llm_configs.id）；
   若有悬挂则打印告警并**跳过**，不盲目落外键（需先清数据）。
   （报告原文写作 ``user_llm_configs.character_id``，实测该列不存在：真实漂移列是
   ``ai_characters.user_llm_config_id``，指向 ``user_llm_configs.id``。）

2) **P3-3 NOT NULL 漂移（12 列，以 ORM 为准）**：
   - ``channel_bindings``：bot_account_id / bot_label / created_at / enabled / extra_json / updated_at
   - ``domain_events``：created_at
   - ``prospective_intents``：created_at / cue_terms_json / updated_at
   - ``user_facts``：updated_at / valid_from
   batch recreate 统一收紧为 NOT NULL，沿用 ORM 已有的 server_default（不新增 ORM 没有的默认）。
   收紧前先只读确认存量无 NULL；发现 NULL 则打印告警并**跳过该表**（不硬改、不静默回填）。
   SQLite 改列约束需重建表 → ``recreate="always"`` + ``copy_from=ORM 表``（同 c9d0e1f2a3b4 口径）。

3) **P3-4 索引漂移（ORM 补 ``index=True``，本迁移幂等补建）**：
   - ``ix_user_llm_configs_user_id``（c8d9 只在「本迁移建表」分支里建过，create_all 路径缺）；
   - ``ix_user_device_tokens_user_id``（d9e0 只在「本迁移建表」分支里建过）；
   - ``ix_user_device_tokens_push_token``：迁移 d9e0 把 push_token 的索引**命名错位**成了
     ``ix_user_device_tokens_token``；本次 ORM 补 ``index=True`` 后按列名生成正确名
     ``ix_user_device_tokens_push_token``，upgrade 里删掉错名旧索引、再由 ``ensure_indexes``
     补齐正确名 —— 使「整链重放库」的索引集合与 ``create_all`` 库完全一致。
     反向（downgrade）只把 d9e0 的旧索引名**加回**（不删任何索引），否则 d9e0.downgrade 的
     ``drop_index('ix_user_device_tokens_token')`` 会抛 ``no such index``、整链 ``downgrade base`` 断掉
     （本迁移开发时实测复现）。
   末尾统一复用 ``alembic/_helpers.ensure_indexes``（迁移规范：``recreate="always"`` 后必须补隐式索引）。

幂等：``has_table`` / ``has_column`` / FK 存在性 / 列可空性 / 存量 NULL 计数全守卫，重放零副作用。
downgrade 有意 no-op：本迁移是「让迁移库向 ORM 单向收敛」——NOT NULL 收紧与 FK 落库在 SQLite 需
再次整表重建（含全量数据拷贝），回退无收益且有数据风险；补索引属「补索引类」沿用 no-op 口径
（不 DROP 任何既有索引）。唯一例外是**恢复** d9e0 的旧索引名（纯新增、幂等），只为保住
``downgrade base`` 可走通；整链 ``downgrade base`` 时这些表由上游迁移（e5f6a7b8c9d0 /
c1e2f3a4b5c6 / a7b8c9d0e1f2 / d9e0f1a2b3c4 / 1d19fa0a34c9）的 downgrade 连带删除。
"""
import importlib.util as _ilu
import os as _os

import sqlalchemy as sa
from alembic import op
from sqlalchemy import ForeignKeyConstraint as _FK
from sqlalchemy import Index as _Idx
from sqlalchemy import MetaData as _MD
from sqlalchemy import Table as _Tbl

# revision identifiers, used by Alembic.
revision = "e8f9a0b1c2d3"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None

# alembic/_helpers.py 的绝对路径（version 模块由 alembic 按文件加载，没有包上下文）。
_HELPERS_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "_helpers.py"
)

# P3-3：ORM 侧 NOT NULL、迁移侧此前可空的列（表 → 列名）。
_NOT_NULL_DRIFT = {
    "channel_bindings": [
        "bot_account_id", "bot_label", "created_at", "enabled", "extra_json", "updated_at",
    ],
    "domain_events": ["created_at"],
    "prospective_intents": ["created_at", "cue_terms_json", "updated_at"],
    "user_facts": ["updated_at", "valid_from"],
}


def _ensure_indexes_fn():
    """取 ``alembic/_helpers.py::ensure_indexes``（同 e1b2c3d4e5f6/b2c3d4e5f6a7 的加载方式）。"""
    spec = _ilu.spec_from_file_location("ambrace_alembic_helpers", _HELPERS_PATH)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ensure_indexes


def _base_metadata():
    """当前完整 ORM metadata（导入全部模型后取 Base.metadata）。"""
    import app.models  # noqa: F401  # 确保所有模型注册到 Base.metadata
    from app.models.base import Base

    return Base.metadata


def _insp(bind):
    """取 inspector 并清缓存——本迁移会重建表，缓存反射会读到重建前的旧结构。"""
    insp = sa.inspect(bind)
    try:
        insp.clear_cache()
    except Exception:
        pass
    return insp


def _has_table(bind, table: str) -> bool:
    try:
        return _insp(bind).has_table(table)
    except Exception:
        return False


def _has_column(bind, table: str, column: str) -> bool:
    try:
        return column in {c["name"] for c in _insp(bind).get_columns(table)}
    except Exception:
        return False


def _db_nullable(bind, table: str) -> dict:
    """DB 侧列可空性（列名 → nullable）；表不存在/反射失败返回空 dict。"""
    try:
        return {c["name"]: bool(c["nullable"]) for c in _insp(bind).get_columns(table)}
    except Exception:
        return {}


def _count(bind, sql: str) -> int:
    try:
        return int(bind.execute(sa.text(sql)).scalar() or 0)
    except Exception:
        return 0


def _rebuild_copy_table(meta, conn, tname: str):
    """batch recreate 的 copy_from：以当前 ORM metadata 为单一事实源（深拷贝列/表级约束/索引），
    但只保留『DB 已存在』的列——避免「未来迁移新增的列」被提前 SELECT 导致重放失败。

    与 c9d0e1f2a3b4::_rebuild_copy_table 同构（本仓 recreate 迁移的既定口径）。
    """
    model_table = meta.tables[tname]
    existing = {c["name"] for c in _insp(conn).get_columns(tname)}
    src = model_table.to_metadata(_MD())
    new_t = _Tbl(src.name, _MD(), **src.kwargs)
    for col in src.columns:
        if col.name in existing:
            new_t.append_column(col._copy())
    for const in src.constraints:
        if isinstance(const, _FK):
            if {c.name for c in const.columns} <= existing:
                new_t.append_constraint(const._copy(target_table=new_t))
        elif not getattr(const, "_column_flag", False):
            if {c.name for c in const.columns} <= existing:
                new_t.append_constraint(const._copy(target_table=new_t))
    for idx in src.indexes:
        if {c.name for c in idx.columns} <= existing:
            _Idx(
                idx.name,
                unique=idx.unique,
                *[new_t.c[c.name] for c in idx.columns],
                **idx.kwargs,
            )
    return new_t


def _recreate(bind, meta, tname: str) -> None:
    """按 ORM 定义重建单表（SQLite 改约束/加外键的唯一途径）。

    FK 必须关：ai_characters 被大量 CASCADE 子表引用，开着外键 DROP 父表会连带删子表数据。
    """
    copy_t = _rebuild_copy_table(meta, bind, tname)
    op.execute("PRAGMA foreign_keys=OFF")
    try:
        with op.batch_alter_table(tname, recreate="always", copy_from=copy_t):
            pass
    finally:
        op.execute("PRAGMA foreign_keys=ON")
    _insp(bind)  # 丢弃重建前的反射缓存


def _align_config_fk(bind, meta) -> None:
    """P3-2：``ai_characters.user_llm_config_id`` 补 ``user_llm_configs.id`` 外键（SET NULL）。"""
    if not (
        _has_table(bind, "ai_characters")
        and _has_table(bind, "user_llm_configs")
        and _has_column(bind, "ai_characters", "user_llm_config_id")
    ):
        print("[harden_schema] ai_characters.user_llm_config_id: table/column absent, skip")
        return
    has_fk = any(
        fk.get("referred_table") == "user_llm_configs"
        and list(fk.get("constrained_columns") or []) == ["user_llm_config_id"]
        for fk in _insp(bind).get_foreign_keys("ai_characters")
    )
    if has_fk:
        print("[harden_schema] ai_characters.user_llm_config_id: FK already present, skip")
        return
    dangling = _count(
        bind,
        "SELECT COUNT(*) FROM ai_characters a WHERE a.user_llm_config_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM user_llm_configs c WHERE c.id = a.user_llm_config_id)",
    )
    if dangling:
        print(
            "[harden_schema] WARNING ai_characters.user_llm_config_id has "
            f"{dangling} dangling refs -> FK NOT added (clean data first, then re-run)"
        )
        return
    _recreate(bind, meta, "ai_characters")
    print(
        "[harden_schema] ai_characters.user_llm_config_id -> "
        "user_llm_configs.id ON DELETE SET NULL"
    )


def _align_not_null(bind, meta) -> None:
    """P3-3：把 12 处迁移侧可空列收紧为 ORM 的 NOT NULL（存量 NULL 则告警跳过）。"""
    for tname, cols in _NOT_NULL_DRIFT.items():
        if not _has_table(bind, tname):
            print(f"[harden_schema] {tname}: table absent, skip")
            continue
        db_null = _db_nullable(bind, tname)
        targets = [c for c in cols if db_null.get(c) is True]
        if not targets:
            print(f"[harden_schema] {tname}: NOT NULL already aligned, skip")
            continue
        nulls = {c: _count(bind, f"SELECT COUNT(*) FROM {tname} WHERE {c} IS NULL") for c in targets}
        dirty = {c: n for c, n in nulls.items() if n}
        if dirty:
            print(
                f"[harden_schema] WARNING {tname}: NULL rows present {dirty} -> "
                "NOT NULL NOT applied (backfill/report first, then re-run)"
            )
            continue
        _recreate(bind, meta, tname)
        print(f"[harden_schema] {tname}: NOT NULL applied -> {targets}")


def _align_indexes(bind, meta) -> None:
    """P3-4：删掉 d9e0 的错名索引，再按 ORM metadata 幂等补齐缺失索引（含正确名 push_token）。"""
    if _has_table(bind, "user_device_tokens"):
        names = {ix["name"] for ix in _insp(bind).get_indexes("user_device_tokens")}
        if "ix_user_device_tokens_token" in names:
            op.drop_index("ix_user_device_tokens_token", table_name="user_device_tokens")
            print(
                "[harden_schema] dropped misnamed index ix_user_device_tokens_token "
                "(column is push_token; correct name ix_user_device_tokens_push_token ensured below)"
            )
    _insp(bind)  # 让 ensure_indexes 读到最新表结构/索引集合
    created = _ensure_indexes_fn()(op, meta)
    print(f"[harden_schema] created {len(created)} indexes: {created}")


def upgrade() -> None:
    bind = op.get_bind()
    meta = _base_metadata()
    _align_config_fk(bind, meta)
    _align_not_null(bind, meta)
    _align_indexes(bind, meta)


def downgrade() -> None:
    """反向：唯一回滚动作是把 d9e0 的旧索引名**加回**（纯新增、幂等），保证整链 downgrade base 可走通。

    其余（NOT NULL 收紧 / 外键落库 / 补索引）有意 no-op——见模块 docstring。
    """
    bind = op.get_bind()
    if not _has_table(bind, "user_device_tokens"):
        return
    names = {ix["name"] for ix in _insp(bind).get_indexes("user_device_tokens")}
    if "ix_user_device_tokens_token" not in names:
        op.create_index("ix_user_device_tokens_token", "user_device_tokens", ["push_token"])
