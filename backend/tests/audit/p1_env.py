import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base
from app.models.user import User
from app.models.character import AICharacter
from app.memory import bm25_index as bm
from app.memory import service as svc
from app.memory import supersede as ss
from app.memory import write as wr
from app.db import database as dbmod
from app.db import vector_store as vs
from app.flags.agent_flags import AGENT_FLAGS
from app.memory import extractor as ex
from app.memory import extract_queue as queue


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / 'p1.sqlite'
    engine = create_async_engine('sqlite+aiosqlite:///' + path.as_posix(), poolclass=NullPool)

    @event.listens_for(engine.sync_engine, 'connect')
    def pragmas(conn, record):
        conn.execute('PRAGMA foreign_keys=ON')

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add_all([
                User(id=901, username='synthetic-a', password_hash='unused', nickname='synthetic'),
                User(id=902, username='synthetic-b', password_hash='unused', nickname='synthetic'),
            ])
            await db.flush()
            db.add_all([
                AICharacter(id=901, user_id=901, name='synthetic-a'),
                AICharacter(id=902, user_id=902, name='synthetic-b'),
            ])
            await db.commit()

    asyncio.run(setup())
    for module in [svc, ss, dbmod]:
        monkeypatch.setattr(module, 'async_session_factory', factory)
    for key in AGENT_FLAGS:
        if isinstance(AGENT_FLAGS[key], bool):
            monkeypatch.setitem(AGENT_FLAGS, key, False)
    monkeypatch.setitem(AGENT_FLAGS, 'memory_supersede', True)
    monkeypatch.setattr(bm, '_persist_root', tmp_path / 'bm25')
    monkeypatch.setattr(bm, '_BM25_PERSIST_ENABLED', True)
    bm._cache.clear()
    monkeypatch.setattr(svc, 'text_embedding', AsyncMock(return_value=[1.0, 0.0]))
    import app.memory.embedding as emb
    monkeypatch.setattr(emb, 'text_embedding', AsyncMock(return_value=[1.0, 0.0]))
    monkeypatch.setattr(wr, '_emit_created_memory_receipts', lambda **kw: None)
    monkeypatch.setattr(wr, '_run_created_memory_hooks', AsyncMock(side_effect=lambda **kw: bm.invalidate(kw['character_id'])))
    import app.memory.core as core
    monkeypatch.setattr(core, 'maybe_promote_core', AsyncMock())
    store = {}

    async def upsert(**kw):
        store[kw['memory_id']] = {
            'content': kw['content'], 'character_id': kw['character_id'],
            'status': kw.get('status', 'active'),
        }

    async def remove(mid):
        store.pop(mid, None)

    async def mark(mid, status):
        if mid in store:
            store[mid]['status'] = status

    monkeypatch.setattr(vs, 'upsert_memory_vector', upsert)
    monkeypatch.setattr(vs, 'mark_memory_vector_status', mark)
    monkeypatch.setattr(vs, 'delete_memory_vector', remove)
    monkeypatch.setattr(svc, 'add_memory', upsert)
    monkeypatch.setattr(svc, 'delete_memory_vector', remove)
    yield SimpleNamespace(factory=factory, path=path, store=store, tmp=tmp_path, upsert=upsert)
    bm._cache.clear()
    asyncio.run(engine.dispose())


@pytest.fixture
def world(env, monkeypatch):
    for name, mod in list(sys.modules.items()):
        if name.startswith('app.') and mod is not None and 'async_session_factory' in vars(mod):
            monkeypatch.setattr(mod, 'async_session_factory', env.factory)
    monkeypatch.setitem(AGENT_FLAGS, 'memory_source_proof_v1', True)
    monkeypatch.setattr(queue, '_QUEUE_FILE', env.tmp / 'queue.json')
    monkeypatch.setattr(ex, '_mark_batch_taken', lambda cid: None)
    monkeypatch.setattr(ex, 'BATCH_SIZE', 99)
    monkeypatch.setattr(ex, '_throttle_open', lambda cid: True)
    llm = AsyncMock(return_value='EVENTS: 合成散步发现蓝水晶 | 3\nSTAGE: 合成舞台收藏蓝水晶 | 3\n')
    monkeypatch.setattr(ex, 'llm_call', llm)

    async def initial():
        async with env.factory() as db:
            c = await db.get(AICharacter, 901)
            return c.self_statement, c.current_status, c.relationship_summary

    return SimpleNamespace(**vars(env), llm=llm, initial_character=asyncio.run(initial()))
