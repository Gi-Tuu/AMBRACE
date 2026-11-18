# -*- coding: utf-8 -*-
"""配置表哨兵 0 号：拆掉 task_llm_configs 的 users 外键 + 给老库预置哨兵行（2026-11-18）。

## 背景（真机现场）

服务器级配置统一用 ``user_id = 0`` 当"全局哨兵"。``api_configs`` / ``vlm_configs`` /
``speech_configs`` / ``multimodal_configs`` / ``image_gen_configs`` 五张表在
``c9d0e1f2a3b4`` 里已统一拆掉 ``users`` 外键（该迁移 docstring 明写"0/-1 哨兵对 FK 违约"），
**但 ``task_llm_configs`` 漏在清单外**，仍带 ``FOREIGN KEY(user_id) REFERENCES users(id)``；
而 ``app/db/engine.py`` 对每条连接强制 ``PRAGMA foreign_keys=ON``。

于是：``users`` 表没有 id=0 的行 ⇒ 服务器级"任务模型"首次保存是 INSERT ⇒ 外键违约 ⇒ HTTP 500。
用户现场表现为"语音/识图/生图/任务 保存全报 500，只有服务器级 LLM 能存"——因为那五张表里
**早就有 0 号行**（保存走 UPDATE，外键不校验被改列），而任务表是空表（走 INSERT）。

## 本迁移做两件事（都幂等）

1. **拆 ``task_llm_configs.user_id`` 的外键**：SQLite 只能重建表 ⇒
   ``PRAGMA foreign_keys=OFF`` + ``batch_alter_table(..., recreate="always",
   copy_from=<ORM 表，只保留 DB 已存在的列>)``（照抄 b3c4d5e6f7a8 / c9d0e1f2a3b4 的手法）。
   重建以 ORM metadata 为单一事实源，模型侧已同时去掉 ``ForeignKey`` ⇒ 新表无该外键。
2. **给五张"每用户唯一"的配置表预置 user_id=0 的空行**（``INSERT OR IGNORE``）：
   这是给**老库**的兜底——即便某张表在升级时仍带着历史外键，只要 0 号行存在，
   后续保存就走 UPDATE、不再触发外键校验，功能立刻可用（表结构由第 1 步与 c9d0e1f2a3b4 负责根治）。
   ``task_llm_configs`` 按 task 分行、没有"每用户唯一"的哨兵语义，故只重建、不预置。

## 形态与守卫

* 表名一律写字面量（``app/db/migrate.py`` 靠正则收集版本链涉及的表名）。
* 重建后 ``ensure_indexes(op, Base.metadata)`` 补齐隐式索引（迁移规范）。
* 幂等：``task_llm_configs`` 已无 user_id 外键则跳过重建；已有 0 号行则不插。
* downgrade：把外键加回去（同样 recreate）；**不删任何哨兵行**——生产里那行可能已带用户配置，
  删了会丢数据，宁可留着（外键加回后在 SQLite 里也只校验后续写入）。
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
revision: str = "c6f8b0d2e4a7"
down_revision: Union[str, None] = "c5e7a9b1d3f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None

# 需要预置 user_id=0 哨兵行的表（每用户唯一一行的配置表）
_SENTINEL_TABLES = (
    "api_configs",
    "vlm_configs",
    "speech_configs",
    "multimodal_configs",
    "image_gen_configs",
)
# 需要保证"user_id 不挂外键"的表（本次修的是第一张；其余仅回验）
_NO_FK_TABLES = ("task_llm_configs",)


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


def _fk_columns(bind, table: str) -> list[set[str]]:
    """该表所有外键涉及的本地列集合（空 = 没有任何外键）。"""
    if not _has_table(bind, table):
        return []
    try:
        return [{c for c in fk.get("constrained_columns") or []}
                for fk in _insp(bind).get_foreign_keys(table)]
    except Exception:
        return []


def _has_user_id_fk(bind, table: str) -> bool:
    return any({"user_id"} & cols for cols in _fk_columns(bind, table))


def _rebuild_copy_table(meta, bind, tname: str):
    """batch recreate 的 copy_from：以 ORM metadata 为事实源，只保留 DB 已存在的列。"""
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
            _Idx(idx.name, *[new_t.c[c.name] for c in idx.columns], unique=idx.unique)
    return new_t


def _ensure_indexes(bind) -> list[str]:
    """补齐缺失索引（复用 alembic/_helpers.ensure_indexes）。"""
    path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "_helpers.py")
    spec = _ilu.spec_from_file_location("_ambrace_alembic_helpers", path)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.ensure_indexes(op, _base_metadata())


def _has_sentinel_row(bind, table: str) -> bool:
    try:
        return bind.execute(sa.text(f'SELECT 1 FROM "{table}" WHERE user_id = 0 LIMIT 1')).first() is not None
    except Exception:
        return True  # 查不了就当作有，宁可少做也不炸


def upgrade() -> None:
    bind = op.get_bind()
    meta = _base_metadata()
    rebuilt: list[str] = []

    op.execute("PRAGMA foreign_keys=OFF")
    try:
        # ① 拆掉 task_llm_configs 的 users 外键（本迁移的主因）
        if _has_user_id_fk(bind, "task_llm_configs"):
            with op.batch_alter_table(
                "task_llm_configs",
                recreate="always",
                copy_from=_rebuild_copy_table(meta, bind, "task_llm_configs"),
            ):
                pass  # 目的只是"按新 metadata 重建"——新模型的 user_id 已无 ForeignKey
            rebuilt.append("task_llm_configs")

        # ② 给五张每用户唯一的配置表预置 0 号哨兵行（老库兜底：让保存走 UPDATE 而非 INSERT）
        for t in _SENTINEL_TABLES:
            if not _has_table(bind, t) or _has_sentinel_row(bind, t):
                continue
            cols = {c["name"] for c in _insp(bind).get_columns(t)}
            if "enabled" not in cols:
                continue
            if t == "image_gen_configs" and "daily_limit" in cols:
                bind.execute(sa.text(
                    f'INSERT INTO "{t}" (user_id, enabled, daily_limit) VALUES (0, 0, 0)'))
            else:
                bind.execute(sa.text(f'INSERT INTO "{t}" (user_id, enabled) VALUES (0, 0)'))
    finally:
        op.execute("PRAGMA foreign_keys=ON")

    if rebuilt:
        _ensure_indexes(bind)

    # ③ 回验：重建过就必须真的没有 user_id 外键了（防静默漂移）
    for t in _NO_FK_TABLES:
        if _has_table(bind, t) and _has_user_id_fk(bind, t):
            raise RuntimeError(
                f"[{revision}] {t}.user_id 仍挂 users 外键——服务器级配置的首次保存会 500，迁移中止")


def downgrade() -> None:
    """把外键加回去（可逆）。**不删哨兵行**：生产里那行可能已带用户配置，删了会丢数据。"""
    bind = op.get_bind()
    if not _has_table(bind, "task_llm_configs") or _has_user_id_fk(bind, "task_llm_configs"):
        return
    meta = _MD()
    cols = [c for c in _insp(bind).get_columns("task_llm_configs")]
    t = _Tbl("task_llm_configs", meta,
             *[sa.Column(c["name"], c["type"], primary_key=bool(c.get("primary_key")))
               for c in cols])
    t.append_constraint(_FK(["user_id"], ["users.id"]))
    op.execute("PRAGMA foreign_keys=OFF")
    try:
        with op.batch_alter_table("task_llm_configs", recreate="always", copy_from=t):
            pass
    finally:
        op.execute("PRAGMA foreign_keys=ON")
    _ensure_indexes(bind)
