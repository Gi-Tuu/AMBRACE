# -*- coding: utf-8 -*-
"""RC2 G2：记忆派生关系只读接口（GET /api/v1/memories/{id}/derivations）合同。

全部合成：dependency override 假用户 + 临时 SQLite（_dbclone），不连真实库/网络。
重点：直接边、精确整数成员、授权+同角色过滤、坏 JSON/类型保守不完整、分页/游标、404/401。
"""
import asyncio
import json

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.api import memories as memories_api
from app.auth.deps import get_current_user_id
from app.db import database as db_mod
from app.models.character import AICharacter
from app.models.memory import Memory
from app.models.user import User

BASE = "/api/v1/memories"


@pytest.fixture()
def der_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "g2.db")
    engine = clone_engine(db_path)
    factory = make_session_factory(engine)
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _client(uid: int) -> TestClient:
    app = FastAPI()
    app.include_router(memories_api.router)
    if uid:
        app.dependency_overrides[get_current_user_id] = lambda: uid
    return TestClient(app)


async def _seed(factory):
    async with factory() as db:
        db.add_all([
            User(id=1, username="g2_u1", nickname="甲"),
            User(id=2, username="g2_u2", nickname="乙", parent_id=1),
            User(id=3, username="g2_u3", nickname="丙"),
            AICharacter(id=10, user_id=1, name="c10"),
            AICharacter(id=11, user_id=1, name="c11"),
            AICharacter(id=12, user_id=2, name="c12"),
            AICharacter(id=13, user_id=3, name="c13"),
        ])
        await db.commit()


async def _add_mem(factory, mid, *, user_id=1, character_id=10, derived="[]",
                   status="active", archived=False, content=None, title=None):
    async with factory() as db:
        db.add(Memory(
            id=mid, user_id=user_id, character_id=character_id,
            memory_type="event", content=content or f"正文{mid}",
            title=title, derived_from_ids=derived, status=status, is_archived=archived,
        ))
        await db.commit()


def _get(client, mid, **params):
    return client.get(f"{BASE}/{mid}/derivations", params=params)


def test_双方向直接边_去重_自环_不可见引用保守不完整(der_db):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 10, derived="[11, 12, 11, 999]")
        await _add_mem(der_db, 11)
        await _add_mem(der_db, 12, user_id=2)          # 同家庭共享可见
        await _add_mem(der_db, 13, user_id=2, derived="[10]")
        await _add_mem(der_db, 14, user_id=1, character_id=11, derived="[10]")  # 同用户异角色
        await _add_mem(der_db, 15, user_id=3, character_id=13, derived="[10]")  # 跨家庭
        await _add_mem(der_db, 16)
    asyncio.run(prep())
    body = _get(_client(1), 10).json()

    assert body["status"] == "ok" and body["memory_id"] == 10
    parents = body["parents"]
    assert [i["id"] for i in parents["items"]] == [11, 12]      # 去重、升序、无越权
    assert parents["data_incomplete"] is True                   # 999 悬空 → 不完整但不泄露
    assert set(parents) == {"items", "has_more", "next_cursor", "data_incomplete"}
    assert set(parents["items"][0]) == {"id", "content", "title", "memory_type",
                                        "status", "is_archived"}
    assert "999" not in str(parents["items"])
    children = body["children"]
    # 13（同家庭、同角色）可见；14（异角色）、15（跨家庭）不返回
    assert [i["id"] for i in children["items"]] == [13]
    assert children["data_incomplete"] is True                  # 14 存在但异角色 → 保守不完整


def test_坏JSON_空串_None_旧格式与合法空数组区分(der_db):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 20, derived="not-json")
        await _add_mem(der_db, 21, derived="")
        # 说明：derived_from_ids 列 NOT NULL，真实库不会存 NULL；NULL 分支用纯函数单测覆盖。
        await _add_mem(der_db, 23, derived="{}")
        await _add_mem(der_db, 24, derived="[]")
    asyncio.run(prep())
    client = _client(1)
    for mid in (20, 21, 23):
        parents = _get(client, mid).json()["parents"]
        assert parents["items"] == []
        assert parents["data_incomplete"] is True, f"{mid} 坏格式不能当确认无关联"
    ok = _get(client, 24).json()["parents"]
    assert ok["items"] == [] and ok["data_incomplete"] is False
    from app.memory.derivations import parse_forward_ids
    assert parse_forward_ids(None) == ([], True)
    assert parse_forward_ids("") == ([], True)
    assert parse_forward_ids("[]") == ([], False)
    assert parse_forward_ids("[1, \"2\", true, 3.0]") == ([1], True)


def test_成员类型严格_正整数去自环(der_db):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 30, derived='[11, "12", 13.5, true, false, -1, 0, {"x": 1}, [11], 11]')
        await _add_mem(der_db, 11)
        await _add_mem(der_db, 12)
        await _add_mem(der_db, 13)
        await _add_mem(der_db, 14)
        await _add_mem(der_db, 34, derived="[34]")
    asyncio.run(prep())
    body = _get(_client(1), 30).json()["parents"]
    assert [i["id"] for i in body["items"]] == [11]
    assert body["data_incomplete"] is True
    self_body = _get(_client(1), 34).json()["parents"]
    assert self_body["items"] == [] and self_body["data_incomplete"] is False


def test_反向精确匹配_ID1不误中11_字符串浮点布尔不误中(der_db):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 1)
        await _add_mem(der_db, 11, derived="[1]")
        await _add_mem(der_db, 18, derived="[11]")
        await _add_mem(der_db, 19, derived='["1"]')
        await _add_mem(der_db, 21, derived="[1.0]")
        await _add_mem(der_db, 22, derived="[true]")
        await _add_mem(der_db, 23, derived="[ 1 ]")
    asyncio.run(prep())
    children = _get(_client(1), 1).json()["children"]
    ids = [i["id"] for i in children["items"]]
    assert 11 in ids and 23 in ids            # 合法整数成员（含空白）命中
    assert 18 not in ids                      # 11 不等于 1
    assert 19 not in ids and 21 not in ids and 22 not in ids
    assert children["data_incomplete"] is True  # 存在坏成员类型 → 保守不完整


def test_环与自环_一跳不递归_状态与归档照真展示(der_db):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 40)
        await _add_mem(der_db, 41, derived="[40]")
        await _add_mem(der_db, 42, derived="[41]")
        await _add_mem(der_db, 43, derived="[42]")
        await _add_mem(der_db, 44, derived="[44]")   # 自环
        await _add_mem(der_db, 45, status="stale", derived="[40]")
        await _add_mem(der_db, 46, archived=True, derived="[40]")
    asyncio.run(prep())
    client = _client(1)
    body = _get(client, 40).json()["children"]
    by_id = {i["id"]: i for i in body["items"]}
    assert set(by_id) == {41, 45, 46}          # 只一跳，不把 42/43 展开
    assert by_id[45]["status"] == "stale"
    assert by_id[46]["is_archived"] is True
    assert _get(client, 44).json()["children"]["items"] == []


def test_租户与角色边界_家庭共享_user模式_跨家庭404_异角色过滤(der_db):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 50, derived="[51]")
        await _add_mem(der_db, 51)
        await _add_mem(der_db, 52, user_id=2, derived="[50]")            # 家庭内共享
        await _add_mem(der_db, 53, user_id=1, character_id=11, derived="[50]")  # 异角色
        await _add_mem(der_db, 54, user_id=3, character_id=13, derived="[50]")  # 跨家庭
        await _add_mem(der_db, 55, user_id=3, character_id=13)

    asyncio.run(prep())
    fam = _get(_client(2), 50)   # 子账号看家庭内根账号记忆
    assert fam.status_code == 200
    assert [i["id"] for i in fam.json()["parents"]["items"]] == [51]
    assert 52 in [i["id"] for i in fam.json()["children"]["items"]]

    cross = _get(_client(3), 50)
    assert cross.status_code == 404
    missing = _get(_client(3), 55)
    assert missing.status_code == 200
    assert _get(_client(3), 9999).status_code == 404
    assert cross.json()["detail"] == _get(_client(3), 9999).json()["detail"]

    # 同用户异角色：53 能读（家庭），但 50 的派生边不包含它
    other_char = _get(_client(1), 53).json()
    assert other_char["parents"]["items"] == []
    assert other_char["parents"]["data_incomplete"] is True

    # user 模式：子账号看不到主账号记忆
    from app.application import tenant_service
    import unittest.mock as mock
    with mock.patch.object(tenant_service, "_mode_override", "user"):
        assert _get(_client(2), 50).status_code == 404
        # 但可读自己的 52（其 parents 50 属主账号 → 过滤 + 不完整）
        own = _get(_client(2), 52)
        assert own.status_code == 200
        assert own.json()["parents"]["items"] == []
        assert own.json()["parents"]["data_incomplete"] is True


def test_分页_两方向独立_游标前进_上限与非法游标(der_db):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 500, derived="[" + ",".join(str(i) for i in range(501, 561)) + "]")
        for i in range(501, 561):
            await _add_mem(der_db, i)
        await _add_mem(der_db, 600)
        for i in range(601, 661):
            await _add_mem(der_db, i, derived="[600]")

    asyncio.run(prep())
    client = _client(1)

    p1 = _get(client, 500, limit=50).json()["parents"]
    assert len(p1["items"]) == 50 and p1["has_more"] is True
    assert p1["next_cursor"] == "550"
    p2 = _get(client, 500, limit=50, parents_cursor="550").json()["parents"]
    assert [i["id"] for i in p2["items"]] == list(range(551, 561))
    assert p2["has_more"] is False and p2["next_cursor"] is None
    ids = [i["id"] for i in p1["items"]] + [i["id"] for i in p2["items"]]
    assert len(ids) == len(set(ids)) == 60

    c1 = _get(client, 600, limit=50).json()["children"]
    assert len(c1["items"]) == 50 and c1["has_more"] is True
    c2 = _get(client, 600, limit=50, children_cursor=c1["next_cursor"]).json()["children"]
    assert [i["id"] for i in c2["items"]] == list(range(651, 661))
    assert c2["has_more"] is False

    # 两方向独立：只给 parents_cursor 不影响 children 第一页
    mixed = _get(client, 500, limit=50, parents_cursor="520").json()
    assert mixed["children"]["items"] == [] and mixed["children"]["has_more"] is False
    assert mixed["parents"]["items"][0]["id"] == 521

    # 越界游标 = 空页且无更多（不是"确认无关联"，由列表状态区分）
    tail = _get(client, 600, children_cursor="999").json()["children"]
    assert tail["items"] == [] and tail["has_more"] is False

    # 上限与非法游标
    for bad_limit in (0, 51, -3):
        assert _get(client, 500, limit=bad_limit).status_code == 400
    for bad_cursor in ("abc", "-1", "1.5", "0", " ", "1e3"):
        assert _get(client, 500, parents_cursor=bad_cursor).status_code == 400
    assert _get(client, 500, limit=1).status_code == 200


def test_401未登录与客户端伪造owner参数被忽略(der_db):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 70, derived="[71]")
        await _add_mem(der_db, 71)
        await _add_mem(der_db, 72, user_id=3, character_id=13)

    asyncio.run(prep())
    # 无认证依赖 override → 401
    assert _get(_client(0), 70).status_code == 401
    # 客户端传 owner/character/user 参数不得改变结果（身份只来自认证）
    body = _client(1).get(f"{BASE}/70/derivations",
                          params={"user_id": 3, "character_id": 13, "owner": 3}).json()
    assert [i["id"] for i in body["parents"]["items"]] == [71]
    # 跨家庭主体：带任何参数仍 404
    assert _client(1).get(f"{BASE}/72/derivations", params={"user_id": 3}).status_code == 404

@pytest.mark.parametrize("raw", ["1", '{"x":1}', '{"ids":1}', "{}", "true", "null", "3.5", '"1"'])
def test_R1_反向非数组证据一律不成立且不完整(der_db, raw):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 1)
        await _add_mem(der_db, 20, derived=raw)
    asyncio.run(prep())
    body = _get(_client(1), 1).json()["children"]
    assert body["items"] == [], (raw, body)
    assert body["data_incomplete"] is True


def test_R1_反向成员负0嵌套与超大数不误中不500(der_db):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 1)
        await _add_mem(der_db, 20, derived="[0]")
        await _add_mem(der_db, 21, derived="[-1]")
        await _add_mem(der_db, 22, derived="[[1]]")
        await _add_mem(der_db, 23, derived="[{\"id\":1}]")
        await _add_mem(der_db, 24, derived="[1208925819614629174706176]")  # > int64
        await _add_mem(der_db, 25, derived='["1"]')
        await _add_mem(der_db, 26, derived="[1.0]")
        await _add_mem(der_db, 27, derived="[true]")
    asyncio.run(prep())
    resp = _get(_client(1), 1)
    assert resp.status_code == 200
    body = resp.json()["children"]
    assert body["items"] == []
    assert body["data_incomplete"] is True


def test_R2_同一行重复成员只算一条边且先实体去重再分页(der_db):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 1)
        await _add_mem(der_db, 20, derived="[1,1,1]")
        await _add_mem(der_db, 21, derived="[1]")
        await _add_mem(der_db, 22, derived="[1]")
    asyncio.run(prep())
    p1 = _get(_client(1), 1, limit=2).json()["children"]
    assert [i["id"] for i in p1["items"]] == [20, 21] and p1["has_more"] is True
    p2 = _get(_client(1), 1, limit=2, children_cursor=p1["next_cursor"]).json()["children"]
    assert [i["id"] for i in p2["items"]] == [22] and p2["has_more"] is False


@pytest.mark.parametrize("cursor", [str(2**80), "\u00b2", "\u0661\u0662", "0", "-1", "1e3",
                                    "9" * 20, "abc", " 1", "1 "])
def test_R3_非法游标一律4xx不崩(der_db, cursor):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 1)
    asyncio.run(prep())
    for kw in ("parents_cursor", "children_cursor"):
        resp = _get(_client(1), 1, **{kw: cursor})
        assert 400 <= resp.status_code < 500, (kw, cursor, resp.status_code)


def test_R3_存量超大派生ID不击穿绑定且不猜合法(der_db):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 1, derived=f"[{2**80}, 3]")
        await _add_mem(der_db, 3)
    asyncio.run(prep())
    body = _get(_client(1), 1).json()["parents"]
    assert [i["id"] for i in body["items"]] == [3]      # 合法 3 仍可见
    assert body["data_incomplete"] is True               # 超大 ID 不猜、不泄露
    assert str(2**80) not in str(body["items"])


def test_R4_前向先授权过滤再分页_大量悬空后合法来源仍可达(der_db):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 1, derived=json.dumps(list(range(9000, 9200)) + [3]))
        await _add_mem(der_db, 3)
    asyncio.run(prep())
    body = _get(_client(1), 1).json()["parents"]
    assert [i["id"] for i in body["items"]] == [3]
    assert body["items"][0]["content"] == "正文3"
    assert body["data_incomplete"] is True


def test_R4_前向可见分页稳定不漏不重(der_db):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 1, derived=json.dumps(list(range(40, 95)) + [3]))
        for i in list(range(40, 95)) + [3]:
            await _add_mem(der_db, i)
    asyncio.run(prep())
    seen, cursor = [], None
    for _ in range(10):
        params = {"limit": 20}
        if cursor:
            params["parents_cursor"] = cursor
        page = _get(_client(1), 1, **params).json()["parents"]
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break
    assert seen == [3] + list(range(40, 95))
    assert len(seen) == len(set(seen))

@pytest.mark.parametrize("raw", ["[0]", "[-1]", "[1208925819614629174706176]",
                                 "[9223372036854775808]", "[\"1\"]", "[1.0]", "[true]"])
def test_R2_单一坏整数成员必须标不完整且不误当证据(der_db, raw):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 1)
        await _add_mem(der_db, 20, derived=raw)   # 库内仅这一条派生数组
    asyncio.run(prep())
    body = _get(_client(1), 1).json()["children"]
    assert body["items"] == [], (raw, body)
    assert body["data_incomplete"] is True, (raw, body)


def test_R2_合法整数与合法空数组不被误标坏(der_db):
    async def prep():
        await _seed(der_db)
        await _add_mem(der_db, 1)
        await _add_mem(der_db, 20, derived="[1]")
        await _add_mem(der_db, 21, derived="[1, 1, 3]")
        await _add_mem(der_db, 22, derived="[]")
        await _add_mem(der_db, 23, derived="[9223372036854775807]")
    asyncio.run(prep())
    body = _get(_client(1), 1).json()["children"]
    assert [i["id"] for i in body["items"]] == [20, 21]
    assert body["data_incomplete"] is False
    empty = _get(_client(1), 22).json()["children"]
    assert empty["items"] == [] and empty["data_incomplete"] is False
