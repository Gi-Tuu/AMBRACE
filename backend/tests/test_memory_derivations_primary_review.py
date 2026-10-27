import asyncio
import json
import pytest
from test_memory_derivations_api import der_db as der_db, _seed, _add_mem, _client, _get

@pytest.mark.parametrize('raw', ['1', '{"x":1}', '{"ids":1}'])
def test_review_reverse_rejects_nonarray_evidence(der_db, raw):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 1)
        await _add_mem(der_db, 20, derived=raw)
    asyncio.run(prep())
    response = _get(_client(1), 1)
    assert response.status_code == 200
    children = response.json()['children']
    assert children['items'] == [], children
    assert children['data_incomplete'] is True

def test_review_reverse_deduplicates_before_paging(der_db):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 1)
        await _add_mem(der_db, 20, derived='[1,1,1]')
        await _add_mem(der_db, 21, derived='[1]')
    asyncio.run(prep())
    body = _get(_client(1), 1, limit=2).json()['children']
    assert [row['id'] for row in body['items']] == [20,21], body
    assert body['has_more'] is False

@pytest.mark.parametrize('cursor', [str(2**80), '²'])
def test_review_invalid_cursor_returns_4xx_not_crash(der_db, cursor):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 1)
    asyncio.run(prep())
    response = _get(_client(1), 1, children_cursor=cursor)
    assert 400 <= response.status_code < 500

def test_review_permission_filter_precedes_forward_cap(der_db):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 1, derived=json.dumps(list(range(10000,10200))+[3]))
        await _add_mem(der_db, 3)
    asyncio.run(prep())
    body = _get(_client(1), 1).json()['parents']
    assert [row['id'] for row in body['items']] == [3], body
