# -*- coding: utf-8 -*-
"""#68 多 LLM 配置（user_llm_configs）P0 测试：
- CRUD（创建/列表/详情/更新/删除）
- is_default 唯一（设新默认自动清其他）
- 删除配置清角色引用（ai_characters.user_llm_config_id 置 NULL）
- 子账号共享只读且不泄露 api_key；共享配置禁止改/删/设默认
- 角色绑定校验（非本人/不可共享配置 403；主账号共享配置可绑定）
- 解析链优先级：角色绑定 > 用户默认 > 主账号共享默认 > 服务器级 > .env
"""
import asyncio
import os
import tempfile

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import llm_configs as llm_configs_api
from app.api import characters as characters_api
from app.auth.deps import get_current_user_id

# 测试用配置 Key（断言用；不会真实外呼）
BOUND_KEY = "sk-bound12345678"
DEFAULT_KEY = "sk-default12345678"
SHARED_KEY = "sk-shared12345678"
SERVER_KEY = "sk-server12345678"
ENV_KEY = "sk-env12345678"


@pytest.fixture()
def llm_db(monkeypatch):
    """临时 SQLite 文件库：patch async_session_factory（不触碰 backend/data）。"""
    tmp = tempfile.mkdtemp(prefix='llmcfg_test_')
    db_path = os.path.join(tmp, 't.db')
    engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}', poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, 'async_session_factory', factory)
    yield factory
    engine.sync_engine.dispose()


def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(llm_configs_api.router)
    app.include_router(characters_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


async def _add_user(factory, uid: int, username: str, parent_id: int | None = None):
    async with factory() as db:
        from app.models.user import User
        db.add(User(id=uid, username=username, nickname=f'n{uid}', parent_id=parent_id))
        await db.commit()


async def _add_config(factory, user_id: int, name: str, api_key: str, *,
                      base_url: str = "https://example.com/v1", model: str = "m1",
                      provider: str = "test", enabled: bool = True,
                      is_default: bool = False, shared_with_subs: bool = False) -> int:
    async with factory() as db:
        from app.models.config import UserLlmConfig
        cfg = UserLlmConfig(
            user_id=user_id, name=name, base_url=base_url, api_key=api_key,
            model=model, provider=provider, enabled=enabled,
            is_default=is_default, shared_with_subs=shared_with_subs,
        )
        db.add(cfg)
        await db.commit()
        await db.refresh(cfg)
        return cfg.id


async def _add_character(factory, char_id: int, user_id: int, name: str = "Char",
                         llm_config_id: int | None = None) -> None:
    async with factory() as db:
        from app.models.character import AICharacter
        db.add(AICharacter(id=char_id, user_id=user_id, name=name,
                           user_llm_config_id=llm_config_id))
        await db.commit()


async def _add_server_config(factory, api_key: str) -> None:
    async with factory() as db:
        from app.models.config import ApiConfig
        db.add(ApiConfig(user_id=0, base_url="https://srv.example.com/v1",
                         api_key=api_key, model="srv", enabled=True))
        await db.commit()


# ── CRUD ──

def test_crud_roundtrip(llm_db):
    asyncio.run(_add_user(llm_db, 1, 'alice'))
    client = _make_client(1)

    # create
    r = client.post('/api/v1/llm-configs', json={
        'name': '我的 DeepSeek', 'base_url': 'https://api.deepseek.com/v1',
        'api_key': BOUND_KEY, 'model': 'deepseek-chat', 'provider': 'deepseek',
    })
    assert r.status_code == 201, r.text
    cfg = r.json()
    assert cfg['name'] == '我的 DeepSeek'
    assert cfg['has_api_key'] is True
    # api_key 脱敏：不泄露明文
    assert cfg['api_key'] != BOUND_KEY
    assert '...' in (cfg['api_key'] or '')
    cfg_id = cfg['id']

    # list
    r = client.get('/api/v1/llm-configs')
    assert r.status_code == 200
    items = r.json()['items']
    assert any(i['id'] == cfg_id for i in items)

    # get
    r = client.get(f'/api/v1/llm-configs/{cfg_id}')
    assert r.status_code == 200
    assert r.json()['id'] == cfg_id

    # update
    r = client.put(f'/api/v1/llm-configs/{cfg_id}', json={'name': '新名字'})
    assert r.status_code == 200
    assert r.json()['name'] == '新名字'

    # delete
    r = client.delete(f'/api/v1/llm-configs/{cfg_id}')
    assert r.status_code in (200, 204)
    r = client.get(f'/api/v1/llm-configs/{cfg_id}')
    assert r.status_code == 404


def test_create_duplicate_name_400(llm_db):
    asyncio.run(_add_user(llm_db, 1, 'alice'))
    client = _make_client(1)
    assert client.post('/api/v1/llm-configs', json={'name': 'x', 'api_key': 'k'}).status_code == 201
    r = client.post('/api/v1/llm-configs', json={'name': 'x', 'api_key': 'k2'})
    assert r.status_code == 400


# ── is_default 唯一 ──

def test_is_default_unique(llm_db):
    asyncio.run(_add_user(llm_db, 1, 'alice'))
    cid1 = asyncio.run(_add_config(llm_db, 1, 'a', 'k1', is_default=True))
    cid2 = asyncio.run(_add_config(llm_db, 1, 'b', 'k2', is_default=False))
    client = _make_client(1)
    r = client.post(f'/api/v1/llm-configs/{cid2}/default')
    assert r.status_code == 200
    assert r.json()['is_default'] is True
    # cid1 默认被清
    r1 = client.get(f'/api/v1/llm-configs/{cid1}')
    assert r1.json()['is_default'] is False


# ── 删除清角色引用 ──

def test_delete_clears_character_reference(llm_db):
    asyncio.run(_add_user(llm_db, 1, 'alice'))
    cid = asyncio.run(_add_config(llm_db, 1, 'a', 'k1'))
    asyncio.run(_add_character(llm_db, 100, 1, llm_config_id=cid))
    client = _make_client(1)
    assert client.delete(f'/api/v1/llm-configs/{cid}').status_code in (200, 204)
    from app.db.database import async_session_factory
    async def _check():
        async with async_session_factory() as db:
            from app.models.character import AICharacter
            ch = await db.get(AICharacter, 100)
            return ch.user_llm_config_id
    assert asyncio.run(_check()) is None


# ── 子账号共享只读且不泄露 api_key ──

def test_sub_account_shared_readonly(llm_db):
    asyncio.run(_add_user(llm_db, 1, 'main', parent_id=None))
    asyncio.run(_add_user(llm_db, 2, 'sub', parent_id=1))
    cid = asyncio.run(_add_config(llm_db, 1, '共享', SHARED_KEY, shared_with_subs=True))
    client = _make_client(2)

    # 子账号列表含共享配置，api_key 不泄露
    r = client.get('/api/v1/llm-configs')
    assert r.status_code == 200
    items = r.json()['items']
    shared = [i for i in items if i['is_shared']]
    assert shared, items
    assert shared[0]['id'] == cid
    assert shared[0]['api_key'] == ''  # 共享配置不返回 api_key
    assert shared[0]['has_api_key'] is True  # 但标注已配置（前端显示"已配置"）

    # 子账号禁止修改/删除/设默认（对主账号共享配置）
    assert client.put(f'/api/v1/llm-configs/{cid}', json={'name': 'x'}).status_code == 403
    assert client.delete(f'/api/v1/llm-configs/{cid}').status_code == 403
    assert client.post(f'/api/v1/llm-configs/{cid}/default').status_code == 403
    assert client.post(f'/api/v1/llm-configs/{cid}/share', json={'shared': False}).status_code == 403

    # 主账号仍可编辑
    main_client = _make_client(1)
    assert main_client.put(f'/api/v1/llm-configs/{cid}', json={'name': '共享2'}).status_code == 200


def test_sub_account_cannot_see_other_private_config(llm_db):
    asyncio.run(_add_user(llm_db, 1, 'main', parent_id=None))
    asyncio.run(_add_user(llm_db, 2, 'sub', parent_id=1))
    asyncio.run(_add_config(llm_db, 1, '私有', 'k1', shared_with_subs=False))
    client = _make_client(2)
    r = client.get('/api/v1/llm-configs')
    assert r.status_code == 200
    # 未共享的配置不出现
    assert all(not (i['name'] == '私有' and not i['is_shared']) for i in r.json()['items'])


# ── 角色绑定校验 ──

def test_character_binding_validation(llm_db):
    asyncio.run(_add_user(llm_db, 1, 'main', parent_id=None))
    asyncio.run(_add_user(llm_db, 2, 'sub', parent_id=1))
    own_cid = asyncio.run(_add_config(llm_db, 1, '我的', 'k1'))
    shared_cid = asyncio.run(_add_config(llm_db, 1, '共享', SHARED_KEY, shared_with_subs=True))
    client = _make_client(2)

    # 子账号把角色绑定到主账号共享配置：允许
    r = client.post('/api/v1/characters', json={'name': 'A', 'user_llm_config_id': shared_cid})
    assert r.status_code == 201, r.text
    char_id = r.json()['id']

    # 子账号不能绑定主账号私密（未共享）配置
    r = client.post('/api/v1/characters', json={'name': 'B', 'user_llm_config_id': own_cid})
    assert r.status_code in (400, 403)

    # 绑定不存在的配置 -> 400
    r = client.post('/api/v1/characters', json={'name': 'C', 'user_llm_config_id': 99999})
    assert r.status_code in (400, 403)

    # 更新绑定到非共享配置 -> 403（原绑定保持）
    r = client.put(f'/api/v1/characters/{char_id}', json={'user_llm_config_id': own_cid})
    assert r.status_code in (400, 403)


# ── 解析链优先级 ──

def test_resolution_chain_priority(llm_db, monkeypatch):
    from app.agent.llm_client import _resolve_llm_config

    asyncio.run(_add_user(llm_db, 1, 'main', parent_id=None))
    asyncio.run(_add_user(llm_db, 2, 's1', parent_id=1))
    asyncio.run(_add_user(llm_db, 3, 's2', parent_id=1))
    asyncio.run(_add_user(llm_db, 10, 'standalone', parent_id=None))
    asyncio.run(_add_server_config(llm_db, SERVER_KEY))

    # M 的共享默认
    asyncio.run(_add_config(llm_db, 1, '前端共享', SHARED_KEY,
                            is_default=True, shared_with_subs=True))
    # S1 的角色绑定 + 用户默认
    bound_cid = asyncio.run(_add_config(llm_db, 2, '绑定', BOUND_KEY))
    asyncio.run(_add_config(llm_db, 2, '默认', DEFAULT_KEY, is_default=True))
    asyncio.run(_add_character(llm_db, 200, 2, llm_config_id=bound_cid))

    # 1) 角色绑定 > 用户默认 > 主账号共享 > 服务器级
    cfg = asyncio.run(_resolve_llm_config(user_id=2, character_id=200))
    assert cfg['api_key'] == BOUND_KEY, cfg

    # 2) 用户默认 > 主账号共享 > 服务器级（无角色绑定）
    cfg = asyncio.run(_resolve_llm_config(user_id=2, character_id=None))
    assert cfg['api_key'] == DEFAULT_KEY, cfg

    # 3) 主账号共享默认 > 服务器级（子账号无任何自有配置/绑定）
    cfg = asyncio.run(_resolve_llm_config(user_id=3, character_id=None))
    assert cfg['api_key'] == SHARED_KEY, cfg

    # 4) 服务器级 > .env（独立主账号，无任何配置）
    monkeypatch.setattr('app.config.settings.llm_api_key', 'env-fallback')
    cfg = asyncio.run(_resolve_llm_config(user_id=10, character_id=None))
    assert cfg['api_key'] == SERVER_KEY, cfg


def test_resolution_env_fallback(llm_db, monkeypatch):
    from app.agent.llm_client import _resolve_llm_config
    asyncio.run(_add_user(llm_db, 11, 'only-env', parent_id=None))
    monkeypatch.setattr('app.config.settings.llm_api_key', ENV_KEY)
    cfg = asyncio.run(_resolve_llm_config(user_id=11, character_id=None))
    assert cfg['api_key'] == ENV_KEY, cfg


# ── 服务器级解析（api_configs user_id=0）──

def test_resolution_server_config(llm_db):
    from app.agent.llm_client import _resolve_llm_config
    asyncio.run(_add_user(llm_db, 12, 'srv-user', parent_id=None))
    asyncio.run(_add_server_config(llm_db, SERVER_KEY))
    cfg = asyncio.run(_resolve_llm_config(user_id=12, character_id=None))
    assert cfg['api_key'] == SERVER_KEY, cfg
