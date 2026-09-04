# -*- coding: utf-8 -*-
"""#72 PR-B 迁移可逆 + 单头无分叉测试（group_memories）。

- upgrade head → 出现 group_memories 表与 4 个索引；脚本目录单头（无分叉）；
- downgrade 回父修订 e5f6a7b8c9d0 → 表与索引干净删除。
"""
import os
import tempfile

import pytest
from sqlalchemy import create_engine, inspect


@pytest.fixture()
def mig_db(monkeypatch):
    """临时库：把 settings.database_url 指向临时 DB，跑真实 alembic 迁移链。"""
    import app.config as cfg
    tmp = tempfile.mkdtemp(prefix="group_mem_mig_")
    db_path = os.path.join(tmp, "mig.db")
    monkeypatch.setattr(cfg.settings, "database_url", "sqlite+aiosqlite:///" + db_path)
    yield db_path


def _alembic_cfg():
    from alembic.config import Config
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(backend, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend, "alembic"))
    return cfg


def _heads():
    from alembic.script import ScriptDirectory
    return set(ScriptDirectory.from_config(_alembic_cfg()).get_heads())


def test_migration_upgrade_downgrade(mig_db):
    from alembic import command
    db_path = mig_db
    cfg = _alembic_cfg()

    # 升级到 head：group_memories 出现，脚本单头无分叉
    command.upgrade(cfg, "head")
    assert _heads() == {"f3a4b5c6d7e8"}, f"期望单头 f3a4b5c6d7e8，实际 {_heads()}"

    eng = create_engine("sqlite:///" + db_path)
    insp = inspect(eng)
    assert insp.has_table("group_memories"), "upgrade 后应有 group_memories 表"
    cols = {c["name"] for c in insp.get_columns("group_memories")}
    for c in ("id", "group_id", "user_id", "round_id", "speaker_type",
              "speaker_id", "content", "epistemic_status", "importance", "created_at"):
        assert c in cols, f"group_memories 缺列 {c}"
    idx = {i["name"] for i in insp.get_indexes("group_memories")}
    for name in ("idx_group_mem_group_created", "ix_group_memories_group_id",
                 "ix_group_memories_user_id", "ix_group_memories_round_id"):
        assert name in idx, f"group_memories 缺索引 {name}"
    eng.dispose()

    # 回退父修订：表与索引干净删除
    command.downgrade(cfg, "e5f6a7b8c9d0")
    eng2 = create_engine("sqlite:///" + db_path)
    insp2 = inspect(eng2)
    assert not insp2.has_table("group_memories"), "downgrade 后 group_memories 应删除"
    eng2.dispose()
