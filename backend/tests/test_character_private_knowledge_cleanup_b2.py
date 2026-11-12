# -*- coding: utf-8 -*-
"""B2a：角色私有设定（world_facts / lorebook_entries）删除闭环与 ID 复用回归。

合成用户 A/B、A 的目标/保留角色、B 的保留角色；验证：
1. 删除只清目标角色的两表记录，保留角色与未知脏数据不动；
2. 旧 ROWID 表结构下正常新建确实复用旧 ID，但旧设定不可被同账号或另一账号的新角色读到/注入；
   当前 AUTOINCREMENT 结构下正常新建不复用旧 ID，且不继承旧设定；
3. 两表清理失败向上抛，SQL 事务回滚后角色与两表数据由新会话读回完整；
4. 创建幂等收据保留（FK SET NULL），不被级联误删。

所有数据库均在 pytest tmp_path 的模板克隆中；不触生产库/真实向量库。
"""
import asyncio
import sqlite3

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from _dbclone import clone_engine, make_session_factory

UID_A = 9701
UID_B = 9702
CHAR_KEEP_A = 9711
CHAR_KEEP_B = 9712
CHAR_TARGET = 9713  # 当前最大角色 ID；仅旧 ROWID 结构会在删除后自动复用
ORPHAN_CHAR_ID = 9999

LOREBOOK_TARGET = "A-秘密设定"
LOREBOOK_KEEP_A = "A-保留设定"
LOREBOOK_KEEP_B = "B-保留设定"


async def _noop(*_args, **_kwargs):
    return None


@pytest.fixture()
def knowledge_env(tmp_path, monkeypatch):
    db_path = tmp_path / "knowledge.db"
    engine = clone_engine(str(db_path))
    factory = make_session_factory(engine)

    import app.db.database as db_mod
    import app.events.facts as facts_mod
    import app.memory.lorebook as lorebook_mod

    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(facts_mod, "async_session_factory", factory)
    monkeypatch.setattr(lorebook_mod, "async_session_factory", factory)
    monkeypatch.setattr("app.db.vector_store.delete_memory_vectors_by_character", _noop)
    monkeypatch.setattr("app.memory.save_memory", _noop)
    yield factory, str(db_path)
    engine.sync_engine.dispose()


@pytest.fixture()
def legacy_knowledge_env(knowledge_env):
    """Preserve the original cleanup regression under the pre-AUTOINCREMENT schema."""
    from sqlalchemy import MetaData
    from sqlalchemy.dialects import sqlite
    from sqlalchemy.schema import CreateTable
    from app.db.character_id_guard import CHECK_NAME
    from app.models.character import AICharacter

    metadata = MetaData()
    for table in AICharacter.metadata.tables.values():
        table.to_metadata(metadata)
    legacy = metadata.tables[AICharacter.__tablename__]
    legacy.dialect_options["sqlite"]["autoincrement"] = False
    for constraint in list(legacy.constraints):
        if constraint.name == CHECK_NAME:
            legacy.constraints.remove(constraint)
    legacy_ddl = str(CreateTable(legacy).compile(dialect=sqlite.dialect()))
    factory, db_path = knowledge_env
    with sqlite3.connect(db_path) as raw:
        assert raw.execute("SELECT COUNT(*) FROM ai_characters").fetchone()[0] == 0
        objects = raw.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name='ai_characters' "
            "AND type IN ('index','trigger') AND sql IS NOT NULL ORDER BY type,name"
        ).fetchall()
        raw.execute("PRAGMA foreign_keys=OFF")
        raw.execute("DROP TABLE ai_characters")
        raw.execute(legacy_ddl)
        for (sql,) in objects:
            raw.execute(sql)
        raw.commit()
        raw.execute("PRAGMA foreign_keys=ON")
        assert raw.execute("PRAGMA foreign_key_check").fetchall() == []
        assert "AUTOINCREMENT" not in raw.execute(
            "SELECT sql FROM sqlite_master WHERE name='ai_characters'"
        ).fetchone()[0].upper()
    return factory, db_path


def _run(coro):
    return asyncio.run(coro)


def _seed(factory, *, with_receipt: bool = True) -> None:
    from app.models.character import AICharacter
    from app.models.character.creation_request import CharacterCreationRequest
    from app.models.memory import LorebookEntry, WorldFact
    from app.models.user import User

    async def _do():
        async with factory() as db:
            db.add_all([
                User(id=UID_A, username="b2a_a", nickname="B2A A"),
                User(id=UID_B, username="b2a_b", nickname="B2A B"),
            ])
            await db.flush()
            db.add_all([
                AICharacter(id=CHAR_KEEP_A, user_id=UID_A, name="A 保留角色"),
                AICharacter(id=CHAR_KEEP_B, user_id=UID_B, name="B 保留角色"),
                AICharacter(id=CHAR_TARGET, user_id=UID_A, name="A 待删角色"),
            ])
            await db.flush()
            db.add_all([
                LorebookEntry(
                    user_id=UID_A, character_id=CHAR_TARGET, title=LOREBOOK_TARGET,
                    content="只有 A 目标角色知道", keywords='["暗号A"]', active=True,
                ),
                LorebookEntry(
                    user_id=UID_A, character_id=CHAR_KEEP_A, title=LOREBOOK_KEEP_A,
                    content="A 保留哨兵", keywords='["保留A"]', active=True,
                ),
                LorebookEntry(
                    user_id=UID_B, character_id=CHAR_KEEP_B, title=LOREBOOK_KEEP_B,
                    content="B 保留哨兵", keywords='["保留B"]', active=True,
                ),
            ])
            facts = [
                # 目标角色的瞬时状态（active + 历史 superseded）
                dict(user_id=UID_A, character_id=CHAR_TARGET, subject_type="character",
                     subject_id=CHAR_TARGET, predicate="status", object_value="A目标开心",
                     status="active", kind="status", audience='["public"]'),
                dict(user_id=UID_A, character_id=CHAR_TARGET, subject_type="character",
                     subject_id=CHAR_TARGET, predicate="status", object_value="A目标旧状态",
                     status="superseded", kind="status", audience='["public"]'),
                # 目标角色的长期 curated 事实
                dict(user_id=UID_A, character_id=CHAR_TARGET, subject_type="character",
                     subject_id=CHAR_TARGET, predicate="preference", object_value="A目标喜欢雨天",
                     status="active", kind="fact", audience='["public"]', is_authoritative=True),
                dict(user_id=UID_A, character_id=CHAR_TARGET, subject_type="character",
                     subject_id=CHAR_TARGET, predicate="constraint", object_value="A目标不能提猫",
                     status="active", kind="constraint", audience='["public"]', is_authoritative=True),
                # 保留角色的哨兵事实
                dict(user_id=UID_A, character_id=CHAR_KEEP_A, subject_type="character",
                     subject_id=CHAR_KEEP_A, predicate="status", object_value="A保留事实",
                     status="active", kind="status", audience='["public"]'),
                dict(user_id=UID_B, character_id=CHAR_KEEP_B, subject_type="character",
                     subject_id=CHAR_KEEP_B, predicate="status", object_value="B保留事实",
                     status="active", kind="status", audience='["public"]'),
                # 未知脏数据：没有对应角色行；不得按目标删除，也不得重绑
                dict(user_id=UID_A, character_id=ORPHAN_CHAR_ID, subject_type="system",
                     subject_id=ORPHAN_CHAR_ID, predicate="status", object_value="未知脏数据",
                     status="active", kind="status", audience='["public"]'),
            ]
            for row in facts:
                db.add(WorldFact(**row))
            if with_receipt:
                db.add(CharacterCreationRequest(
                    user_id=UID_A, idempotency_key="b2a-key",
                    payload_digest="a" * 64, character_id=CHAR_TARGET, state="active",
                ))
            await db.commit()

    _run(_do())


def _delete_target(factory, *, user_id: int = UID_A) -> None:
    from app.application.characters import delete_character

    async def _do():
        async with factory() as db:
            await delete_character(db, CHAR_TARGET, user_id, "zh")
            await db.commit()

    _run(_do())


def _new_character(factory, *, user_id: int, name: str) -> int:
    from app.models.character import AICharacter

    async def _do():
        async with factory() as db:
            row = AICharacter(user_id=user_id, name=name)
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id

    return _run(_do())


def _count(factory, model, **filters) -> int:
    async def _do():
        async with factory() as db:
            stmt = select(func.count()).select_from(model)
            for key, value in filters.items():
                stmt = stmt.where(getattr(model, key) == value)
            return int((await db.execute(stmt)).scalar() or 0)

    return _run(_do())


def _lorebook_titles(factory, character_id: int, text: str) -> list[str]:
    from app.memory.lorebook import load_matching_entries

    async def _do():
        rows = await load_matching_entries(character_id, text, round_no=0)
        return [r.title for r in rows]

    return _run(_do())


def _character_view(factory, character_id: int, user_id: int) -> str:
    from app.events.facts import get_character_view

    async def _do():
        return await get_character_view(character_id, user_id)

    return _run(_do())


def _receipt_state(factory) -> tuple[int | None, str]:
    from app.models.character.creation_request import CharacterCreationRequest

    async def _do():
        async with factory() as db:
            row = (await db.execute(
                select(CharacterCreationRequest).where(
                    CharacterCreationRequest.user_id == UID_A,
                    CharacterCreationRequest.idempotency_key == "b2a-key",
                )
            )).scalar_one()
            return row.character_id, row.state

    return _run(_do())


def test_unauthorized_delete_does_not_touch_target(knowledge_env):
    factory, _db_path = knowledge_env
    _seed(factory)
    from app.models.character import AICharacter
    from app.models.memory import LorebookEntry, WorldFact

    with pytest.raises(HTTPException) as exc:
        _delete_target(factory, user_id=UID_B)
    assert exc.value.status_code == 404
    assert _count(factory, AICharacter, id=CHAR_TARGET) == 1
    assert _count(factory, LorebookEntry, character_id=CHAR_TARGET) == 1
    assert _count(factory, WorldFact, character_id=CHAR_TARGET) == 4


def test_delete_target_removes_only_target_private_knowledge(knowledge_env):
    factory, _db_path = knowledge_env
    _seed(factory)
    from app.models.character import AICharacter
    from app.models.memory import LorebookEntry, WorldFact

    # 先证明读路径确实能读到目标旧设定（避免“本来就是空”的假绿）
    assert _lorebook_titles(factory, CHAR_TARGET, "暗号A") == [LOREBOOK_TARGET]
    assert "A目标开心" in _character_view(factory, CHAR_TARGET, UID_A)

    _delete_target(factory)

    assert _count(factory, AICharacter, id=CHAR_TARGET) == 0
    assert _count(factory, LorebookEntry, character_id=CHAR_TARGET) == 0
    assert _count(factory, WorldFact, character_id=CHAR_TARGET) == 0
    assert _count(factory, LorebookEntry, character_id=CHAR_KEEP_A) == 1
    assert _count(factory, LorebookEntry, character_id=CHAR_KEEP_B) == 1
    assert _count(factory, WorldFact, character_id=CHAR_KEEP_A) == 1
    assert _count(factory, WorldFact, character_id=CHAR_KEEP_B) == 1
    assert _count(factory, WorldFact, character_id=ORPHAN_CHAR_ID) == 1, "未知脏数据不得擅自清理"
    assert _lorebook_titles(factory, CHAR_TARGET, "暗号A") == []
    assert _character_view(factory, CHAR_TARGET, UID_A) == ""
    # 幂等收据保留、目标置 NULL（FK SET NULL），供既有“已删除则拒绝”契约使用
    assert _receipt_state(factory) == (None, "active")


def test_same_account_id_reuse_cannot_read_old_knowledge(legacy_knowledge_env):
    factory, _db_path = legacy_knowledge_env
    _seed(factory)
    from app.models.character import AICharacter
    from app.models.memory import LorebookEntry, WorldFact

    _delete_target(factory)
    new_id = _new_character(factory, user_id=UID_A, name="A 新角色")
    assert new_id == CHAR_TARGET, "正常新建应复用被删的最大 ID（旧 DDL 行为）"

    assert _count(factory, AICharacter, id=new_id) == 1
    assert _count(factory, LorebookEntry, character_id=new_id) == 0
    assert _count(factory, WorldFact, character_id=new_id) == 0
    assert _lorebook_titles(factory, new_id, "暗号A") == []
    assert "A目标" not in _character_view(factory, new_id, UID_A)


def test_cross_account_id_reuse_cannot_read_old_lorebook(legacy_knowledge_env):
    factory, _db_path = legacy_knowledge_env
    _seed(factory)
    from app.models.memory import LorebookEntry

    _delete_target(factory)
    new_id = _new_character(factory, user_id=UID_B, name="B 新角色")
    assert new_id == CHAR_TARGET, "跨账号新建同样复用被删的最大 ID"

    assert _count(factory, LorebookEntry, character_id=new_id) == 0
    assert _lorebook_titles(factory, new_id, "暗号A") == [], "旧 A 世界书不得注入 B 的新角色"
    assert _count(factory, LorebookEntry, character_id=CHAR_KEEP_A) == 1
    assert _count(factory, LorebookEntry, character_id=CHAR_KEEP_B) == 1


@pytest.mark.parametrize("new_owner", [UID_A, UID_B])
def test_current_schema_does_not_reuse_deleted_id_or_private_knowledge(knowledge_env, new_owner):
    factory, _db_path = knowledge_env
    _seed(factory)
    from app.models.memory import LorebookEntry, WorldFact

    assert _lorebook_titles(factory, CHAR_TARGET, "暗号A") == [LOREBOOK_TARGET]
    _delete_target(factory)
    new_id = _new_character(factory, user_id=new_owner, name="新角色")
    assert new_id > CHAR_TARGET
    assert _count(factory, LorebookEntry, character_id=CHAR_TARGET) == 0
    assert _count(factory, WorldFact, character_id=CHAR_TARGET) == 0
    assert _lorebook_titles(factory, new_id, "暗号A") == []
    assert "A目标" not in _character_view(factory, new_id, new_owner)


def test_cleanup_failure_rolls_back_private_knowledge(knowledge_env):
    factory, db_path = knowledge_env
    _seed(factory, with_receipt=False)
    from app.application.character_cascade import cascade_delete_character
    from app.models.character import AICharacter
    from app.models.memory import LorebookEntry, WorldFact

    raw = sqlite3.connect(db_path)
    raw.execute(
        "CREATE TRIGGER b2a_fail_lorebook BEFORE DELETE ON lorebook_entries "
        "BEGIN SELECT RAISE(ABORT, 'injected lorebook delete failure'); END"
    )
    raw.commit()
    raw.close()

    async def _flow():
        async with factory() as db:
            with pytest.raises(Exception):
                await cascade_delete_character(db, CHAR_TARGET)
            await db.rollback()

    _run(_flow())

    # 事务回滚后，角色与两表数据由新会话读回完整；不能吞错继续删
    assert _count(factory, AICharacter, id=CHAR_TARGET) == 1
    assert _count(factory, LorebookEntry, character_id=CHAR_TARGET) == 1
    assert _count(factory, WorldFact, character_id=CHAR_TARGET) == 4
    assert _lorebook_titles(factory, CHAR_TARGET, "暗号A") == [LOREBOOK_TARGET]
    assert "A目标开心" in _character_view(factory, CHAR_TARGET, UID_A)
