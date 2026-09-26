# -*- coding: utf-8 -*-
"""P3-1：七处凭据列 VARCHAR(n) → TEXT（2026-09-26 全量审查批 B）。

背景：A8 方案 B 之后凭据列存的是密文 ``enc:v1:<b64 nonce>:<b64 ct+tag>``，字节数约为明文的
4/3 + 40。SQLite 不校验 ``VARCHAR(n)`` 长度（超长静默存入），但 MySQL/PostgreSQL 严格模式会
截断或直接报错 ⇒ 「按明文长度设列宽」这个假设本身不成立。ORM 侧已改用 ``EncryptedText``
（DDL = TEXT，见 ``app/utils/credential_crypto.py``），本迁移把**已有库**的 7 张表对齐到 TEXT。

涉及表（列均为 ``api_key``）：``api_configs`` / ``vlm_configs`` / ``speech_configs`` /
``multimodal_configs`` / ``image_gen_configs``（原 VARCHAR(255)）、``user_llm_configs`` /
``task_llm_configs``（原 VARCHAR(500)）。

形态（照抄先例 c9d0e1f2a3b4 / e8f9a0b1c2d3）：
  * SQLite 改列类型只能重建表 ⇒ ``PRAGMA foreign_keys=OFF`` +
    ``batch_alter_table(<表>, recreate="always", copy_from=<ORM 表，只保留 DB 已存在的列>)``；
    alembic 的 recreate 走「建 ``_alembic_tmp_<表>`` → INSERT … SELECT → DROP 旧表 →
    临时表改名回原名」，**旧表名全程不变**，故 ``ai_characters.user_llm_config_id`` 这类
    外部 FK 指向的名字不会被改写成临时表名（迁移末尾仍显式回验）。
  * 数据搬运是 Core 层 ``INSERT … SELECT``，不经过 ``EncryptedText`` 的 bind/result 处理器
    ⇒ 既有密文原样搬迁，既不会二次加密，也不会把明文写回去。
  * 凡 ``recreate="always"`` 重建表，upgrade 末尾必须 ``ensure_indexes(op, Base.metadata)``
    （``alembic/_helpers.py``，迁移规范：隐式单列索引不随 copy_from 还原）。
  * 幂等：逐表读实际类型，已是 TEXT（或表/列不存在）即跳过该表——``scripts/migrate_drill.py``
    会把整链重放一遍，新库由 ``create_all`` 直接建成 TEXT，命中守卫零操作。
  * 升级后回验：7 张表 ``api_key`` 必须全为 TEXT，缺任一 raise 中止（防静默漂移）；同时回验
    ``ix_user_llm_configs_user_id`` 与 ``ai_characters → user_llm_configs`` 外键仍在。
  * downgrade 可逆：改回 VARCHAR（``user_llm_configs`` / ``task_llm_configs`` 回 500，其余回
    255），同样 ``recreate="always"`` + 末尾 ``ensure_indexes`` + 同样回验。
  * 表名在 alter 调用里一律写**字面量**（``app/db/migrate.py`` 靠正则收集版本链涉及的表名），
    故 7 张表逐张显式书写，不做表名参数化。
"""
import importlib.util as _ilu
import os as _os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import ForeignKeyConstraint as _FK
from sqlalchemy import Index as _Idx
from sqlalchemy import MetaData as _MD
from sqlalchemy import Table as _Tbl

# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None

COLUMN = "api_key"
# 回验清单（字面量表名 + 回退时的 VARCHAR 长度）
_CREDENTIAL_TABLES = (
    "api_configs",
    "vlm_configs",
    "speech_configs",
    "multimodal_configs",
    "image_gen_configs",
    "user_llm_configs",
    "task_llm_configs",
)
_VARCHAR_LEN = {"user_llm_configs": 500, "task_llm_configs": 500}

# alembic/_helpers.py 的绝对路径（version 模块由 alembic 按文件加载，没有包上下文）。
_HELPERS_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "_helpers.py"
)


def _ensure_indexes_fn():
    """取 ``alembic/_helpers.py::ensure_indexes``（同 e1b2c3d4e5f6/e8f9a0b1c2d3 的加载方式）。"""
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


def _db_type(bind, table: str) -> str:
    """``api_key`` 的 DDL 类型原文（幂等判定/回验报错用）；拿不到返回 ``"?"``。"""
    try:
        for col in _insp(bind).get_columns(table):
            if col["name"] == COLUMN:
                return str(col["type"].compile(dialect=bind.dialect))
    except Exception:
        pass
    return "?"


def _is_text(bind, table: str) -> bool:
    """库里 ``api_key`` 实际是否已是 TEXT（表/列不存在一律 False）。"""
    return isinstance(
        next((c["type"] for c in _insp(bind).get_columns(table) if c["name"] == COLUMN), None),
        sa.Text,
    )


def _need_rebuild(bind, table: str) -> bool:
    """需要动手的判据：表在、列在、且当前不是 TEXT（幂等守卫）。"""
    if not _has_table(bind, table) or not _has_column(bind, table, COLUMN):
        return False
    return not _is_text(bind, table)


def _is_text_now(bind, table: str) -> bool:
    """downgrade 的守卫：表/列存在且当前是 TEXT 才需要退回 VARCHAR。"""
    if not _has_table(bind, table) or not _has_column(bind, table, COLUMN):
        return False
    return _is_text(bind, table)


def _rebuild_copy_table(meta, bind, tname: str):
    """batch recreate 的 copy_from：以当前 ORM metadata 为单一事实源（深拷贝列/列级外键/
    表级约束/索引），但只保留『DB 已存在』的列——避免「未来迁移新增的列」被提前 SELECT，
    导致从基线整链重放 ``upgrade head`` 失败（同 c9d0e1f2a3b4 / e8f9a0b1c2d3 口径）。
    """
    model_table = meta.tables[tname]
    existing = {c["name"] for c in _insp(bind).get_columns(tname)}
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


def _verify(bind, want_text: bool) -> None:
    """回验 7 张表 api_key 类型与预期一致（TEXT，或 VARCHAR(255)/VARCHAR(500)），不一致则 raise。"""
    bad = []
    for tname in _CREDENTIAL_TABLES:
        if not _has_column(bind, tname, COLUMN):
            continue  # 这张表不在该库里（交由建表路径按当前模型建）
        actual = _db_type(bind, tname)
        if want_text:
            if actual.upper() != "TEXT":
                bad.append(f"{tname}={actual}")
        elif actual.upper() != "VARCHAR(%d)" % _VARCHAR_LEN.get(tname, 255):
            bad.append(f"{tname}={actual}")
    if bad:
        raise RuntimeError(
            f"[{revision}] api_key 类型回验失败（期望"
            f"{' TEXT' if want_text else ' VARCHAR'}）: {bad} —— 中止，防止静默漂移"
        )


def _fk_sig(fk: dict) -> tuple:
    """外键签名：(本地列, 目标表, 目标列)——用于「一条不少」比对。"""
    return (
        tuple(fk.get("constrained_columns") or []),
        fk.get("referred_table"),
        tuple(fk.get("referred_columns") or []),
    )


def _snapshot_kept_objects(bind) -> dict:
    """迁移前快照：**当时实际存在**的索引与 ai_characters 外键。

    口径（2026-09-26 全量测试抓到的缺陷修正）：老库形态各异 —— 例如
    `tests/test_game_session_user_fk.py` 的 OLD_REV 形态里，`ai_characters` 根本没有
    `user_llm_config_id` 外键、`user_llm_configs` 也还没建那个索引。故回验只能是
    「迁移前有的，迁移后一个不少」（超集），**不能**要求这些对象必须存在。
    """
    insp = _insp(bind)
    idx: dict[str, set] = {}
    for t in _CREDENTIAL_TABLES:
        if _has_table(bind, t):
            idx[t] = {ix["name"] for ix in insp.get_indexes(t)}
    fks: set = set()
    if _has_table(bind, "ai_characters"):
        for fk in insp.get_foreign_keys("ai_characters"):
            fks.add(_fk_sig(fk))
    return {"idx": idx, "fks": fks}


def _verify_kept_objects(bind, before: dict) -> None:
    """回验重建没弄丢既有对象：索引与既有外键都必须是「迁移前集合」的超集。"""
    insp = _insp(bind)
    for t, names in before["idx"].items():
        if not _has_table(bind, t):
            raise RuntimeError(f"[{revision}] 表 {t} 在迁移后消失——中止")
        now = {ix["name"] for ix in insp.get_indexes(t)}
        lost = set(names) - now
        if lost:
            raise RuntimeError(f"[{revision}] {t} 丢失索引 {sorted(lost)}（现有 {sorted(now)}）——中止")
    if before["fks"]:
        now_fks = set()
        if _has_table(bind, "ai_characters"):
            now_fks = {_fk_sig(fk) for fk in insp.get_foreign_keys("ai_characters")}
        lost_fk = before["fks"] - now_fks
        if lost_fk:
            raise RuntimeError(
                f"[{revision}] ai_characters 丢失/被改写的既有外键 {sorted(lost_fk)}"
                f"（现有 {sorted(now_fks)}）——中止"
            )


def upgrade() -> None:
    bind = op.get_bind()
    meta = _base_metadata()
    before = _snapshot_kept_objects(bind)  # 迁移前快照：按「不丢」判定，不假定对象一定在
    op.execute("PRAGMA foreign_keys=OFF")
    try:
        if _need_rebuild(bind, "api_configs"):
            with op.batch_alter_table(
                "api_configs",
                recreate="always",
                copy_from=_rebuild_copy_table(meta, bind, "api_configs"),
            ) as batch_op:
                batch_op.alter_column(
                    "api_key", type_=sa.Text(),
                    existing_type=sa.VARCHAR(length=255), existing_nullable=True,
                )
        if _need_rebuild(bind, "vlm_configs"):
            with op.batch_alter_table(
                "vlm_configs",
                recreate="always",
                copy_from=_rebuild_copy_table(meta, bind, "vlm_configs"),
            ) as batch_op:
                batch_op.alter_column(
                    "api_key", type_=sa.Text(),
                    existing_type=sa.VARCHAR(length=255), existing_nullable=True,
                )
        if _need_rebuild(bind, "speech_configs"):
            with op.batch_alter_table(
                "speech_configs",
                recreate="always",
                copy_from=_rebuild_copy_table(meta, bind, "speech_configs"),
            ) as batch_op:
                batch_op.alter_column(
                    "api_key", type_=sa.Text(),
                    existing_type=sa.VARCHAR(length=255), existing_nullable=True,
                )
        if _need_rebuild(bind, "multimodal_configs"):
            with op.batch_alter_table(
                "multimodal_configs",
                recreate="always",
                copy_from=_rebuild_copy_table(meta, bind, "multimodal_configs"),
            ) as batch_op:
                batch_op.alter_column(
                    "api_key", type_=sa.Text(),
                    existing_type=sa.VARCHAR(length=255), existing_nullable=True,
                )
        if _need_rebuild(bind, "image_gen_configs"):
            with op.batch_alter_table(
                "image_gen_configs",
                recreate="always",
                copy_from=_rebuild_copy_table(meta, bind, "image_gen_configs"),
            ) as batch_op:
                batch_op.alter_column(
                    "api_key", type_=sa.Text(),
                    existing_type=sa.VARCHAR(length=255), existing_nullable=True,
                )
        if _need_rebuild(bind, "user_llm_configs"):
            with op.batch_alter_table(
                "user_llm_configs",
                recreate="always",
                copy_from=_rebuild_copy_table(meta, bind, "user_llm_configs"),
            ) as batch_op:
                batch_op.alter_column(
                    "api_key", type_=sa.Text(),
                    existing_type=sa.VARCHAR(length=500), existing_nullable=True,
                )
        if _need_rebuild(bind, "task_llm_configs"):
            with op.batch_alter_table(
                "task_llm_configs",
                recreate="always",
                copy_from=_rebuild_copy_table(meta, bind, "task_llm_configs"),
            ) as batch_op:
                batch_op.alter_column(
                    "api_key", type_=sa.Text(),
                    existing_type=sa.VARCHAR(length=500), existing_nullable=True,
                )
    finally:
        op.execute("PRAGMA foreign_keys=ON")

    _insp(bind)  # 让 ensure_indexes 读到重建后的表结构/索引集合
    created = _ensure_indexes_fn()(op, meta)
    print(f"[{revision}] ensure_indexes created {len(created)}: {created}")

    _verify(bind, want_text=True)
    _verify_kept_objects(bind, before)
    print(f"[{revision}] 7 张表 api_key 均为 TEXT（已是 TEXT 的表命中守卫 0 操作）")


def downgrade() -> None:
    """可逆：改回 VARCHAR（``user_llm_configs`` / ``task_llm_configs`` 回 500，其余回 255）。

    回退后长密文在 MySQL/PG 严格模式会重新受长度约束（SQLite 不受），仅供回滚演练使用。
    """
    bind = op.get_bind()
    meta = _base_metadata()
    before = _snapshot_kept_objects(bind)  # 同 upgrade：回退也按「不丢」判定
    op.execute("PRAGMA foreign_keys=OFF")
    try:
        if _is_text_now(bind, "api_configs"):
            with op.batch_alter_table(
                "api_configs",
                recreate="always",
                copy_from=_rebuild_copy_table(meta, bind, "api_configs"),
            ) as batch_op:
                batch_op.alter_column(
                    "api_key", type_=sa.VARCHAR(length=255),
                    existing_type=sa.Text(), existing_nullable=True,
                )
        if _is_text_now(bind, "vlm_configs"):
            with op.batch_alter_table(
                "vlm_configs",
                recreate="always",
                copy_from=_rebuild_copy_table(meta, bind, "vlm_configs"),
            ) as batch_op:
                batch_op.alter_column(
                    "api_key", type_=sa.VARCHAR(length=255),
                    existing_type=sa.Text(), existing_nullable=True,
                )
        if _is_text_now(bind, "speech_configs"):
            with op.batch_alter_table(
                "speech_configs",
                recreate="always",
                copy_from=_rebuild_copy_table(meta, bind, "speech_configs"),
            ) as batch_op:
                batch_op.alter_column(
                    "api_key", type_=sa.VARCHAR(length=255),
                    existing_type=sa.Text(), existing_nullable=True,
                )
        if _is_text_now(bind, "multimodal_configs"):
            with op.batch_alter_table(
                "multimodal_configs",
                recreate="always",
                copy_from=_rebuild_copy_table(meta, bind, "multimodal_configs"),
            ) as batch_op:
                batch_op.alter_column(
                    "api_key", type_=sa.VARCHAR(length=255),
                    existing_type=sa.Text(), existing_nullable=True,
                )
        if _is_text_now(bind, "image_gen_configs"):
            with op.batch_alter_table(
                "image_gen_configs",
                recreate="always",
                copy_from=_rebuild_copy_table(meta, bind, "image_gen_configs"),
            ) as batch_op:
                batch_op.alter_column(
                    "api_key", type_=sa.VARCHAR(length=255),
                    existing_type=sa.Text(), existing_nullable=True,
                )
        if _is_text_now(bind, "user_llm_configs"):
            with op.batch_alter_table(
                "user_llm_configs",
                recreate="always",
                copy_from=_rebuild_copy_table(meta, bind, "user_llm_configs"),
            ) as batch_op:
                batch_op.alter_column(
                    "api_key", type_=sa.VARCHAR(length=500),
                    existing_type=sa.Text(), existing_nullable=True,
                )
        if _is_text_now(bind, "task_llm_configs"):
            with op.batch_alter_table(
                "task_llm_configs",
                recreate="always",
                copy_from=_rebuild_copy_table(meta, bind, "task_llm_configs"),
            ) as batch_op:
                batch_op.alter_column(
                    "api_key", type_=sa.VARCHAR(length=500),
                    existing_type=sa.Text(), existing_nullable=True,
                )
    finally:
        op.execute("PRAGMA foreign_keys=ON")

    _insp(bind)
    created = _ensure_indexes_fn()(op, meta)
    print(f"[{revision}] downgrade ensure_indexes created {len(created)}: {created}")

    _verify(bind, want_text=False)
    _verify_kept_objects(bind, before)
