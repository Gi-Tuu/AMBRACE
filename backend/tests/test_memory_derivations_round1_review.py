import asyncio
import pytest
from test_memory_derivations_api import der_db as der_db, _seed, _add_mem, _client, _get

@pytest.mark.parametrize('raw', ['[0]', '[-1]', '[1208925819614629174706176]'])
def test_review_invalid_integer_array_does_not_claim_complete(der_db, raw):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 1)
        await _add_mem(der_db, 20, derived=raw)
    asyncio.run(prep())
    response = _get(_client(1), 1)
    assert response.status_code == 200
    children = response.json()['children']
    assert children['items'] == []
    assert children['data_incomplete'] is True, children
