# -*- coding: utf-8 -*-
"""PWA M1a-2 六模块只读工作台 · **真实路由契约**（沙箱集成，2026-10-16）。

口径：用**真实** auth / characters / memories / diary / moments / user_content 路由
+ 真实 SQLAlchemy 会话（conftest 已把 DATABASE_URL / 向量目录 / 认证密钥指向本会话
临时沙箱），合成【两家庭、各两角色、六模块数据】，逐域核对本单消费的 GET 端点的
封套字段名/类型、分页口径、过滤行为与跨家庭 404。**不 import app.main**（单实例锁），
照 test_pwa_same_origin.py / test_chat_stream.py 的「独立 FastAPI 实例」策略。

造数约定（防踩坑）：
- 记忆的 ``last_reinforce_at`` 统一设到未来（2099），跳过读列表的惰性衰减展示
  （``retention_pct`` 会改写展示用 importance），让断言与造数逐字节对应；
- 到期行（``delete_at``）的「物理删除 + 清向量」是**默认读**的既有政策；只读面显式走
  ``read_only=true``（无维护读取，2026-10-16 返工新增），本文件同时覆盖该模式的
  封套/过滤/可见性逐字段一致。**「有无副作用」的对照证据**在
  ``test_pwa_reader_isolation.py``（只读模式不删行 / 默认路径仍删）；
- ``diary_date`` 是纯字符串，含 2024-02-29 闰日，断言原样返回、零时区换算。
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import bcrypt
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api.characters import router as characters_router
from app.api.diary import router as diary_router
from app.api.memories import router as memories_router
from app.api.moments import router as moments_router
from app.api.user_content import router as user_content_router
from app.auth.router import router as auth_router
from app.db.database import async_session_factory
from app.models.character import AICharacter
from app.models.life import AIDiary, AIMoment, MomentComment, UserDiary, UserMemo
from app.models.memory import Memory
from app.models.user import User
from app.utils.errors import register_exception_handlers

USER_A = "pwa_rdc_a"
USER_B = "pwa_rdc_b"
PASSWORD = "PwaReader123"
FAR_FUTURE = datetime(2099, 1, 1)  # 跳过惰性衰减（见文件头约定）

_IDS: dict[str, int] = {}


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.include_router(characters_router)
    app.include_router(memories_router)
    app.include_router(diary_router)
    app.include_router(moments_router)
    app.include_router(user_content_router)
    return app


def _mk_memory(**kw) -> Memory:
    kw.setdefault("last_reinforce_at", FAR_FUTURE)
    kw.setdefault("source", "chat")
    kw.setdefault("status", "active")
    return Memory(**kw)


async def _seed() -> None:
    async with async_session_factory() as db:
        if (await db.execute(select(User).where(User.username == USER_A))).scalar_one_or_none():
            return
        ua = User(username=USER_A, nickname="阿测A",
                  password_hash=bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode())
        ub = User(username=USER_B, nickname="阿测B",
                  password_hash=bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode())
        db.add_all([ua, ub])
        await db.flush()
        ca1 = AICharacter(user_id=ua.id, name="Alpha", bio="阿尔法简介", personality="沉稳",
                          timezone_offset=9, gender="female", birthday="2000-01-01",
                          height=165, weight=50, appearance="长发",
                          self_statement="我是Alpha", current_status="测试中",
                          talkativeness=60, relationship_summary="好友")
        ca2 = AICharacter(user_id=ua.id, name="Beta")
        ca3 = AICharacter(user_id=ua.id, name="Ghost", is_active=False)  # 停用角色：动态应被跳过
        cb1 = AICharacter(user_id=ub.id, name="Gamma")
        db.add_all([ca1, ca2, ca3, cb1])
        await db.flush()
        _IDS.update(ua=ua.id, ub=ub.id, ca1=ca1.id, ca2=ca2.id, ca3=ca3.id, cb1=cb1.id)

        # ── 记忆：charA1 四条（含一条双节点链）+ 一条 working_state（应被列表排除）──
        m1 = _mk_memory(user_id=ua.id, character_id=ca1.id, memory_type="user_info",
                        content="MEM-A1-用户叫小测", importance=60,
                        created_at=datetime(2026, 3, 1, 10))
        m2 = _mk_memory(user_id=ua.id, character_id=ca1.id, memory_type="preference",
                        content="MEM-A1-喜欢雨天", importance=40,
                        created_at=datetime(2026, 3, 1, 11))
        m3 = _mk_memory(user_id=ua.id, character_id=ca1.id, memory_type="event",
                        content="MEM-A1-上周去爬山", importance=80, source="moment",
                        chain_id="chain-rdc-1", node_type="root",
                        created_at=datetime(2026, 3, 1, 12))
        db.add_all([m1, m2, m3])
        await db.flush()
        m4 = _mk_memory(user_id=ua.id, character_id=ca1.id, memory_type="event",
                        content="MEM-A1-山顶看日出", importance=40, source="moment",
                        chain_id="chain-rdc-1", node_type="leaf", parent_id=m3.id,
                        created_at=datetime(2026, 3, 1, 13))
        m_ws = _mk_memory(user_id=ua.id, character_id=ca1.id, memory_type="working_state",
                          content="MEM-A1-工作状态行", importance=50)
        # charA2：普通一条 + 一对取代链（历史端点正例）
        m_a2 = _mk_memory(user_id=ua.id, character_id=ca2.id, memory_type="user_info",
                          content="MEM-A2-贝塔专属", importance=50)
        m_new = _mk_memory(user_id=ua.id, character_id=ca2.id, memory_type="user_info",
                           content="MEM-A2-NEW-用户新地址", importance=50)
        db.add_all([m4, m_ws, m_a2, m_new])
        await db.flush()
        m_old = _mk_memory(user_id=ua.id, character_id=ca2.id, memory_type="user_info",
                           content="MEM-A2-OLD-用户旧地址", importance=50,
                           status="stale", superseded_by=m_new.id,
                           valid_to=datetime(2026, 3, 5))
        # charB1：跨家庭哨兵
        m_b1 = _mk_memory(user_id=ub.id, character_id=cb1.id, memory_type="user_info",
                          content="ZXQB哨兵记忆", importance=50)
        db.add_all([m_old, m_b1])
        await db.flush()
        _IDS.update(m1=m1.id, m2=m2.id, m3=m3.id, m4=m4.id, m_ws=m_ws.id,
                    m_a2=m_a2.id, m_new=m_new.id, m_old=m_old.id, m_b1=m_b1.id)

        # ── 角色日记：charA1 三条（含闰日）；charB1 哨兵一条 ──
        db.add_all([
            AIDiary(character_id=ca1.id, diary_date="2026-02-28", content="DIARY-A1-二月底"),
            AIDiary(character_id=ca1.id, diary_date="2026-03-01", content="DIARY-A1-三月一"),
            AIDiary(character_id=ca1.id, diary_date="2024-02-29", content="DIARY-A1-闰日"),
            AIDiary(character_id=cb1.id, diary_date="2026-01-05", content="ZXQB哨兵日记"),
        ])

        # ── 动态：A 家 AI+用户 各一条；A 家停用角色一条（应跳过）；B 家哨兵一条 ──
        mo_ai = AIMoment(sender_type="ai", character_id=ca1.id, user_id=None,
                         content="MOM-A-AI-今天的云很好看", image_desc="一张云的描述",
                         created_at=datetime(2026, 3, 10, 10))
        mo_user = AIMoment(sender_type="user", character_id=None, user_id=ua.id,
                           content="MOM-A-USER-我的动态", created_at=datetime(2026, 3, 10, 11))
        mo_ghost = AIMoment(sender_type="ai", character_id=ca3.id, user_id=None,
                            content="MOM-A-GHOST-停用角色动态", created_at=datetime(2026, 3, 10, 12))
        mo_b = AIMoment(sender_type="ai", character_id=cb1.id, user_id=None,
                        content="ZXQB哨兵动态", created_at=datetime(2026, 3, 10, 13))
        db.add_all([mo_ai, mo_user, mo_ghost, mo_b])
        await db.flush()
        _IDS.update(mo_ai=mo_ai.id, mo_user=mo_user.id, mo_ghost=mo_ghost.id, mo_b=mo_b.id)
        c_root = MomentComment(moment_id=mo_ai.id, parent_id=None, sender_type="user",
                               sender_id=ua.id, sender_name="阿测A", user_id=ua.id,
                               content="CMT-A1-好美", created_at=datetime(2026, 3, 10, 10, 5))
        db.add(c_root)
        await db.flush()
        db.add(MomentComment(moment_id=mo_ai.id, parent_id=c_root.id, sender_type="ai",
                             sender_id=ca1.id, sender_name="Alpha", user_id=None,
                             content="CMT-A2-谢谢", created_at=datetime(2026, 3, 10, 10, 6)))

        # ── 个人备忘 / 个人日记：A 各两条，B 各一条哨兵 ──
        db.add_all([
            UserMemo(user_id=ua.id, title="MEMO-A-购物单", content="MEMO-A-牛奶鸡蛋",
                     updated_at=datetime(2026, 3, 2, 10)),
            UserMemo(user_id=ua.id, title=None, content="MEMO-A-无题备忘",
                     updated_at=datetime(2026, 3, 2, 11)),
            UserMemo(user_id=ub.id, title="ZXQB哨兵备忘", content="ZXQB哨兵备忘正文",
                     updated_at=datetime(2026, 3, 2, 12)),
            UserDiary(user_id=ua.id, diary_date="2026-03-02", content="UDIARY-A-三月二"),
            UserDiary(user_id=ua.id, diary_date="2026-03-03", content="UDIARY-A-三月三"),
            UserDiary(user_id=ub.id, diary_date="2026-03-04", content="ZXQB哨兵用户日记"),
        ])
        await db.commit()


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    asyncio.run(_seed())
    return _IDS


@pytest.fixture(scope="module")
def client():
    return TestClient(_app())


@pytest.fixture
def _llm_network_guard(monkeypatch):
    """生成/LLM 入口钉成「调用即失败」；本单全部是只读 GET，一次都不该触发。"""
    calls = {"n": 0}

    def _boom(*_a, **_kw):
        calls["n"] += 1
        raise AssertionError("本流程不得触发 LLM 网络调用")

    import app.agent.llm_client as llm_client
    import app.agent.nodes as nodes

    for mod, name in ((llm_client, "chat_completion"),
                      (llm_client, "chat_completion_stream"),
                      (nodes, "chat_completion"),
                      (nodes, "chat_completion_stream")):
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, _boom, raising=False)
    return calls


def _login(client: TestClient, username: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ── 1. 角色详情 ─────────────────────────────────────────────────────────────
def test_角色详情契约与跨家庭404(client, _seeded, _llm_network_guard):
    auth_a = _login(client, USER_A)
    r = client.get(f"/api/v1/characters/{_seeded['ca1']}", headers=auth_a)
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("id", "user_id", "name", "bio", "self_statement", "personality",
                "relationship_summary", "current_status", "gender", "birthday",
                "height", "weight", "appearance", "talkativeness", "timezone_offset",
                "is_active", "created_at", "updated_at"):
        assert key in body, f"角色详情缺字段 {key}"
    assert body["name"] == "Alpha"
    assert body["bio"] == "阿尔法简介"
    assert body["timezone_offset"] == 9
    assert body["height"] == 165 and body["weight"] == 50
    # 跨家庭：A 的 token 看 B 的角色 → 404（与「不存在」同观感）
    assert client.get(f"/api/v1/characters/{_seeded['cb1']}", headers=auth_a).status_code == 404
    assert _llm_network_guard["n"] == 0


# ── 2. 记忆列表 ─────────────────────────────────────────────────────────────
def test_记忆列表封套分页与类型过滤(client, _seeded, _llm_network_guard):
    auth_a = _login(client, USER_A)

    r = client.get(f"/api/v1/memories?character_id={_seeded['ca1']}", headers=auth_a)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"memories", "total"}
    assert body["total"] == 4  # working_state 行被排除
    contents = [m["content"] for m in body["memories"]]
    assert "MEM-A1-工作状态行" not in contents
    # 排序：置顶→星级→created_at 倒序（造数无置顶：m3(4星) → m1(3星) → m4/m2(2星, m4 新)）
    assert contents == ["MEM-A1-上周去爬山", "MEM-A1-用户叫小测", "MEM-A1-山顶看日出", "MEM-A1-喜欢雨天"]
    item = body["memories"][0]
    for key in ("id", "user_id", "character_id", "memory_type", "sub_type", "source",
                "source_label", "source_icon", "epistemic_status", "speaker_type",
                "title", "content", "importance", "importance_pct", "is_archived",
                "is_pinned", "is_locked", "chain_id", "parent_id", "node_type",
                "why_it_matters", "strength_days", "review_count", "created_at", "updated_at"):
        assert key in item, f"记忆列表项缺字段 {key}"
    assert item["importance"] == 4 and item["importance_pct"] == 80.0
    assert isinstance(item["source_label"], str) and item["source_label"]

    # skip/limit 真分页：total 恒为全量条数
    p1 = client.get(f"/api/v1/memories?character_id={_seeded['ca1']}&skip=0&limit=2", headers=auth_a).json()
    p2 = client.get(f"/api/v1/memories?character_id={_seeded['ca1']}&skip=2&limit=2", headers=auth_a).json()
    p3 = client.get(f"/api/v1/memories?character_id={_seeded['ca1']}&skip=4&limit=2", headers=auth_a).json()
    assert len(p1["memories"]) == 2 and len(p2["memories"]) == 2 and p3["memories"] == []
    assert p1["total"] == p2["total"] == p3["total"] == 4
    assert {m["id"] for m in p1["memories"]}.isdisjoint({m["id"] for m in p2["memories"]})

    # memory_type 透传不校验：合法过滤 1 条；非法值 = 空结果（不是 422）
    pref = client.get(f"/api/v1/memories?character_id={_seeded['ca1']}&memory_type=preference", headers=auth_a).json()
    assert [m["content"] for m in pref["memories"]] == ["MEM-A1-喜欢雨天"]
    bogus = client.get(f"/api/v1/memories?character_id={_seeded['ca1']}&memory_type=not_a_type", headers=auth_a).json()
    assert bogus["memories"] == [] and bogus["total"] == 0

    # character_id 不做归属预检：跨家庭角色只得到空列表（不是 404、不是哨兵）
    cross = client.get(f"/api/v1/memories?character_id={_seeded['cb1']}", headers=auth_a).json()
    assert cross["memories"] == [] and cross["total"] == 0

    # read_only=true（只读面真实消费的模式）：封套/条数/排序/过滤与默认路径逐字段一致
    ro = client.get(f"/api/v1/memories?character_id={_seeded['ca1']}&read_only=true", headers=auth_a)
    assert ro.status_code == 200, ro.text
    rob = ro.json()
    assert set(rob.keys()) == {"memories", "total"}
    assert rob["total"] == body["total"]
    assert [m["id"] for m in rob["memories"]] == [m["id"] for m in body["memories"]]
    ro_p1 = client.get(f"/api/v1/memories?character_id={_seeded['ca1']}&skip=0&limit=2&read_only=true",
                       headers=auth_a).json()
    assert [m["id"] for m in ro_p1["memories"]] == [m["id"] for m in p1["memories"]]
    assert ro_p1["total"] == p1["total"]
    ro_cross = client.get(f"/api/v1/memories?character_id={_seeded['cb1']}&read_only=true",
                          headers=auth_a).json()
    assert ro_cross["memories"] == [] and ro_cross["total"] == 0
    assert _llm_network_guard["n"] == 0


# ── 3. 记忆单条 / 链条 / 事实历史 ───────────────────────────────────────────
def test_记忆单条链条与历史契约(client, _seeded, _llm_network_guard):
    auth_a = _login(client, USER_A)

    one = client.get(f"/api/v1/memories/{_seeded['m1']}", headers=auth_a)
    assert one.status_code == 200, one.text
    assert one.json()["content"] == "MEM-A1-用户叫小测"
    assert one.json()["importance"] == 3

    chain = client.get(f"/api/v1/memories/{_seeded['m4']}/chain", headers=auth_a)
    assert chain.status_code == 200, chain.text
    cbody = chain.json()
    assert cbody["status"] == "ok"
    assert [n["id"] for n in cbody["chain"]] == [_seeded["m3"], _seeded["m4"]]  # 时间升序
    cur = [n for n in cbody["chain"] if n["is_current"]]
    assert len(cur) == 1 and cur[0]["id"] == _seeded["m4"]
    node = cbody["chain"][0]
    for key in ("id", "content", "memory_type", "sub_type", "node_type", "parent_id",
                "chain_id", "created_at", "is_current", "is_archived"):
        assert key in node, f"链条节点缺字段 {key}"
    assert node["parent_id"] is None and cbody["chain"][1]["parent_id"] == _seeded["m3"]

    # 事实历史：取代链正例（old→new）
    hist = client.get(f"/api/v1/memories/{_seeded['m_new']}/history", headers=auth_a)
    assert hist.status_code == 200, hist.text
    hbody = hist.json()
    assert hbody["status"] == "ok" and hbody["memory_id"] == _seeded["m_new"]
    for key in ("id", "content", "status", "source", "sub_type", "epistemic_status",
                "speaker_type", "version", "valid_from", "valid_to"):
        assert key in hbody["fact"], f"history.fact 缺字段 {key}"
    assert hbody["truncated"] is False
    assert len(hbody["corrections"]) == 1
    corr = hbody["corrections"][0]
    assert corr["old_id"] == _seeded["m_old"] and corr["new_id"] == _seeded["m_new"]
    assert corr["old_value"] == "MEM-A2-OLD-用户旧地址"
    assert corr["new_value"] == "MEM-A2-NEW-用户新地址"
    assert corr["changed_at"] is not None and corr["changed_at_from"] == "valid_to"
    assert corr["actor"] is None and corr["seq"] == 1
    for key in ("old_source", "new_source", "reason"):
        assert key in corr, f"correction 缺字段 {key}"

    # 无历史 → corrections=[]
    plain = client.get(f"/api/v1/memories/{_seeded['m1']}/history", headers=auth_a).json()
    assert plain["corrections"] == []

    # 跨家庭：单条/链条/历史一律 404
    for suffix in ("", "/chain", "/history"):
        assert client.get(f"/api/v1/memories/{_seeded['m_b1']}{suffix}", headers=auth_a).status_code == 404
    assert _llm_network_guard["n"] == 0


# ── 4. 角色日记 ─────────────────────────────────────────────────────────────
def test_角色日记契约(client, _seeded, _llm_network_guard):
    auth_a = _login(client, USER_A)

    r = client.get(f"/api/v1/diary/{_seeded['ca1']}", headers=auth_a)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"entries", "total"}
    assert body["total"] == 3
    # diary_date 倒序（纯字符串，零时区换算）
    assert [e["diary_date"] for e in body["entries"]] == ["2026-03-01", "2026-02-28", "2024-02-29"]
    entry = body["entries"][0]
    for key in ("id", "character_id", "diary_date", "content", "created_at"):
        assert key in entry, f"日记条目缺字段 {key}"

    # 单日详情：闰日原样返回
    leap = client.get(f"/api/v1/diary/{_seeded['ca1']}/date/2024-02-29", headers=auth_a)
    assert leap.status_code == 200, leap.text
    assert leap.json()["diary_date"] == "2024-02-29"
    assert leap.json()["content"] == "DIARY-A1-闰日"
    # 无当天 → 404
    assert client.get(f"/api/v1/diary/{_seeded['ca1']}/date/2026-01-01", headers=auth_a).status_code == 404

    # dates?month=YYYY-MM：只回日期；格式不符 → 422
    dates = client.get(f"/api/v1/diary/{_seeded['ca1']}/dates?month=2026-02", headers=auth_a)
    assert dates.status_code == 200, dates.text
    assert dates.json() == {"dates": ["2026-02-28"]}
    assert client.get(f"/api/v1/diary/{_seeded['ca1']}/dates?month=2026-3", headers=auth_a).status_code == 422

    # 跨家庭角色 → 404（三端点同口径）
    assert client.get(f"/api/v1/diary/{_seeded['cb1']}", headers=auth_a).status_code == 404
    assert client.get(f"/api/v1/diary/{_seeded['cb1']}/dates?month=2026-01", headers=auth_a).status_code == 404
    assert _llm_network_guard["n"] == 0


# ── 5. 动态 + 评论树 ────────────────────────────────────────────────────────
def test_动态列表与评论树契约(client, _seeded, _llm_network_guard):
    auth_a = _login(client, USER_A)

    r = client.get("/api/v1/moments", headers=auth_a)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"moments", "total"}
    # total＝本页条数（非全量）；A 可见 2 条（B 家哨兵与停用角色动态都不可见）
    assert body["total"] == len(body["moments"]) == 2
    # created_at 倒序：用户动态(11:00) 在 AI 动态(10:00) 前
    assert [m["content"] for m in body["moments"]] == ["MOM-A-USER-我的动态", "MOM-A-AI-今天的云很好看"]

    user_m, ai_m = body["moments"]
    for key in ("id", "character_id", "character_name", "avatar_url", "user_id",
                "sender_type", "content", "image_url", "image_desc", "likes_count",
                "is_active", "created_at", "author_tz_offset", "liked_by_me",
                "likers", "comments"):
        assert key in user_m, f"动态条目缺字段 {key}"
    # 作者标识：user → 昵称 + 默认时区 8；ai → 角色名 + 角色时区 9
    assert user_m["sender_type"] == "user" and user_m["character_name"] == "阿测A"
    assert user_m["author_tz_offset"] == 8 and user_m["character_id"] == 0
    assert ai_m["sender_type"] == "ai" and ai_m["character_name"] == "Alpha"
    assert ai_m["author_tz_offset"] == 9 and ai_m["image_desc"] == "一张云的描述"
    assert user_m["liked_by_me"] is False and user_m["likers"] == []

    # 列表项内嵌评论树：根 1 条 + 回复 1 条
    tree = ai_m["comments"]
    assert len(tree) == 1 and tree[0]["content"] == "CMT-A1-好美"
    assert tree[0]["sender_type"] == "user" and tree[0]["sender_name"] == "阿测A"
    assert len(tree[0]["replies"]) == 1 and tree[0]["replies"][0]["content"] == "CMT-A2-谢谢"
    assert tree[0]["replies"][0]["parent_id"] == tree[0]["id"]
    for key in ("id", "moment_id", "parent_id", "sender_type", "sender_id",
                "sender_name", "content", "created_at", "replies"):
        assert key in tree[0], f"评论节点缺字段 {key}"
    assert user_m["comments"] == []

    # 分页语义（实测钉死）：DB 先按 skip/limit 取页，**再在 Python 层按角色活性过滤**——
    # 停用角色的动态仍占 DB 页槽位（本用例最新一条即它）⇒ limit=1 第一页被过滤成空页。
    # 客户端续页判据「本页返回数==limit」在此口径下会提前停止，属真实契约、如实记录。
    pg1 = client.get("/api/v1/moments?skip=0&limit=1", headers=auth_a).json()
    assert pg1["moments"] == [] and pg1["total"] == 0
    pg3 = client.get("/api/v1/moments?skip=0&limit=3", headers=auth_a).json()
    assert [m["id"] for m in pg3["moments"]] == [user_m["id"], ai_m["id"]]
    assert pg3["total"] == 2
    # 不存在 character_id 过滤参数：传了也不生效（不得伪造过滤成功）
    forged = client.get(f"/api/v1/moments?character_id={_seeded['ca2']}", headers=auth_a).json()
    assert forged["total"] == 2

    # 评论端点：total＝扁平总数，comments＝树根；跨家庭动态 → 404
    cmts = client.get(f"/api/v1/moments/{_seeded['mo_ai']}/comments", headers=auth_a)
    assert cmts.status_code == 200, cmts.text
    assert cmts.json()["total"] == 2 and len(cmts.json()["comments"]) == 1
    assert client.get(f"/api/v1/moments/{_seeded['mo_b']}/comments", headers=auth_a).status_code == 404
    assert _llm_network_guard["n"] == 0


# ── 6. 个人备忘 / 个人日记 ──────────────────────────────────────────────────
def test_个人备忘与日记契约(client, _seeded, _llm_network_guard):
    auth_a = _login(client, USER_A)

    memos = client.get("/api/v1/user/memos", headers=auth_a)
    assert memos.status_code == 200, memos.text
    mbody = memos.json()
    assert set(mbody.keys()) == {"memos", "total"}
    assert mbody["total"] == 2  # 无分页；B 家哨兵不可见
    # updated_at 倒序：无题备忘(11:00) 在购物单(10:00) 前
    assert [m["content"] for m in mbody["memos"]] == ["MEMO-A-无题备忘", "MEMO-A-牛奶鸡蛋"]
    for key in ("id", "title", "content", "created_at", "updated_at"):
        assert key in mbody["memos"][0], f"备忘条目缺字段 {key}"
    assert mbody["memos"][0]["title"] is None  # 可空字段如实为 null
    assert mbody["memos"][1]["title"] == "MEMO-A-购物单"

    diaries = client.get("/api/v1/user/diaries", headers=auth_a)
    assert diaries.status_code == 200, diaries.text
    dbody = diaries.json()
    assert set(dbody.keys()) == {"diaries", "total"}
    assert dbody["total"] == 2
    assert [d["diary_date"] for d in dbody["diaries"]] == ["2026-03-03", "2026-03-02"]
    for key in ("id", "diary_date", "content", "created_at", "updated_at"):
        assert key in dbody["diaries"][0], f"用户日记条目缺字段 {key}"

    one = client.get("/api/v1/user/diaries/2026-03-02", headers=auth_a)
    assert one.status_code == 200, one.text
    assert one.json()["content"] == "UDIARY-A-三月二"
    assert client.get("/api/v1/user/diaries/2026-01-01", headers=auth_a).status_code == 404
    # 日期格式非法 → 400（不是 422、不是 500）
    assert client.get("/api/v1/user/diaries/2026-13-99", headers=auth_a).status_code == 400
    assert _llm_network_guard["n"] == 0


# ── 7. 认证边界：六域 GET 未带 token 一律 401 ────────────────────────────────
def test_未认证六域一律401(client, _seeded):
    paths = [
        f"/api/v1/characters/{_seeded['ca1']}",
        "/api/v1/memories",
        f"/api/v1/memories/{_seeded['m1']}",
        f"/api/v1/memories/{_seeded['m1']}/chain",
        f"/api/v1/memories/{_seeded['m1']}/history",
        f"/api/v1/diary/{_seeded['ca1']}",
        f"/api/v1/diary/{_seeded['ca1']}/dates?month=2026-02",
        "/api/v1/moments",
        f"/api/v1/moments/{_seeded['mo_ai']}/comments",
        "/api/v1/user/memos",
        "/api/v1/user/diaries",
        "/api/v1/user/diaries/2026-03-02",
    ]
    for p in paths:
        assert client.get(p).status_code == 401, p
