# -*- coding: utf-8 -*-
"""A4 批 5 / T6 M2 渠道归因（2026-09-27）——口径钉桩测试（只测不改产品代码）。

被测接线：
- 承载层 ``app/utils/llm_channel.py``（contextvar + 三值词表 + (unknown) 哨兵）
- 唯一读取点 ``app/agent/llm_client._record_usage_async``（spawn 前取值 + 落库带 channel）
- 派生边界 ``app/utils/async_tasks.spawn_background``（后台协程一律切成 server）
- 入口侧 ``app/api/chat.py``（App 主链路标 app）/ ``chat_service.send_and_receive``（微信桥带
  channel 且 try/finally 必清）
- 读端 ``app/application/system.py`` 的 by_channel（App 用量页 get_llm_usage + 控制台 usage_report）
- 迁移 ``b4c5d6e7f8a9``（llm_usage.channel 可空列）与 ``_CURRENT_SCHEMA_SENTINELS`` 登记

覆盖派单七条：
1. 常量 / set-get-reset 往返 / 空值归一为 None（+ 超长截断到列宽 30）；
2. **硬验收**：已 set app 的请求上下文里经 spawn_background 派生的后台调用必须落 **server**；
3. 前台真值：set wechat_ilink 的上下文里落库行 channel == wechat_ilink（钉住「取值发生在
   spawn 之前」——若把读取推迟进落库协程，这一条会被派生边界改写成 server）；
4. 入口接线：源码级断言 App 主链路入口确实 set app（含 SSE 的 set 必须早于 create_task），
   以及 send_and_receive 微信那条路把 channel 透传进上下文并在收尾清回父上下文；
5. by_channel 聚合：不同 channel（含 NULL / 空串）分桶正确、NULL 归 (unknown)、既有字段不减少；
6. 哨兵 ("llm_usage","channel") 已登记进 _CURRENT_SCHEMA_SENTINELS；
7. 迁移幂等与可逆（**方式＝真跑 alembic 编程接口** command.upgrade / command.downgrade 对临时
   文件库，含「版本号退回上一节但保留列」的重放，以及历史行不回填）。

口径与纪律：临时库一律 pytest ``tmp_path`` 私有 SQLite 文件（``_dbclone`` 页级克隆；跑迁移那条
不用克隆、自建空库跑整链——克隆模板是 create_all 出的当前 schema，测不到迁移路径会假绿），
全程不碰 backend/data 生产库。渠道 set 只发生在协程（Task 的上下文副本）里 ⇒ 用例之间不互相
残留，见 _no_channel_leak 守卫。
"""
import asyncio
import inspect
import os
import re
import sqlite3
import sys
import time

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import llm_client
from app.utils import llm_channel as lc
from app.utils.async_tasks import await_all, spawn_background

# 快测档：本文件每条用例起临时库（含一条真跑整链 alembic），属重量级集成用例。
pytestmark = pytest.mark.slow

ROOT_UID = 1
PREV_REV = "a9c1e3f5b7d9"      # b4c5d6e7f8a9 的 down_revision（本迁移的上一节）
NEW_REV = "b4c5d6e7f8a9"


# ---------------------------------------------------------------- 临时库 / 通用 helper

def _patch_session_factories(monkeypatch, factory) -> None:
    """把私有临时库接到所有 app.* 模块的 async_session_factory 名上（含 import 期早绑定）。

    与 test_t6_usage_report 同法：chat_service / permission_service 等在 import 期就
    ``from ... import`` 绑定了引用，只换 app.db.database 这一个接缝不够。
    """
    import app.db.database as db_mod
    import app.db.session as session_mod

    original = db_mod.async_session_factory
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(session_mod, "async_session_factory", factory, raising=False)
    for name, mod in list(sys.modules.items()):
        if not (name == "app" or name.startswith("app.")):
            continue
        try:
            if getattr(mod, "async_session_factory", None) is original:
                monkeypatch.setattr(mod, "async_session_factory", factory)
        except Exception:
            continue


@pytest.fixture()
def m2_db(monkeypatch, tmp_path):
    """私有临时库（全表、空用量）。落库 / 读端用例都基于它。"""
    engine = clone_engine(tmp_path / "m2.db")
    factory = make_session_factory(engine)
    _patch_session_factories(monkeypatch, factory)
    yield factory
    engine.sync_engine.dispose()


@pytest.fixture(autouse=True)
def _no_channel_leak():
    """守卫：渠道 set 只应发生在协程（Task 上下文副本）里 ⇒ 用例跑完外层上下文原样返回。

    哪天有人在同步路径上 set_channel 而不清，这里就会红（跨用例/跨请求污染）。
    """
    before = lc.get_channel()
    yield
    assert lc.get_channel() == before


async def _usage_rows(factory) -> list[tuple]:
    from app.models.agent import LlmUsage

    async with factory() as db:
        rows = (await db.execute(
            select(LlmUsage.id, LlmUsage.channel, LlmUsage.task, LlmUsage.total_tokens)
            .order_by(LlmUsage.id)
        )).all()
    return [(r.id, r.channel, r.task, r.total_tokens) for r in rows]


async def _wait_rows(factory, expect: int = 1, timeout: float = 10.0) -> list[tuple]:
    """等 fire-and-forget 的落库任务真正写出 ``expect`` 行（多行用例靠它定住插入顺序）。"""
    deadline = time.monotonic() + timeout
    rows: list[tuple] = []
    while time.monotonic() < deadline:
        await await_all(timeout=0.2)
        await asyncio.sleep(0.01)
        rows = await _usage_rows(factory)
        if len(rows) >= expect:
            return rows
    return rows


def _buckets(bucket: list[dict], key: str) -> dict[str, dict]:
    return {b[key]: b for b in bucket}


# ---------------------------------------------------------------- 1. 常量 / 往返 / 空值归一

def test_三值词表与列宽常量正确():
    assert lc.CHANNEL_APP == "app"
    assert lc.CHANNEL_WECHAT_ILINK == "wechat_ilink"
    assert lc.CHANNEL_SERVER == "server"
    # 读端哨兵：无归因不与真实取值混桶（沿用 M0 by_task 的 (untagged) 思路）
    assert lc.CHANNEL_UNKNOWN == "(unknown)"
    assert lc.CHANNEL_MAX_LEN == 30          # 与 llm_usage.channel String(30) 同档


def test_set_get_reset往返与空值归一():
    async def _main():
        lc.set_channel(None)                  # 基线（防外层上下文有残留时误判）
        assert lc.get_channel() is None

        t_app = lc.set_channel(lc.CHANNEL_APP)
        assert lc.get_channel() == "app"

        t_wx = lc.set_channel(lc.CHANNEL_WECHAT_ILINK)
        assert lc.get_channel() == "wechat_ilink"
        lc.reset_channel(t_wx)                # reset 回上一值，不是回 None
        assert lc.get_channel() == "app"

        assert lc.set_channel("") is not None
        assert lc.get_channel() is None       # 空串归一为 None（不造出空桶）
        lc.set_channel(None)
        assert lc.get_channel() is None       # None 同样归一

        long_token = lc.set_channel("x" * 40)
        assert lc.get_channel() == "x" * 30   # 超长按列宽截断，落库不炸
        lc.reset_channel(long_token)
        assert lc.get_channel() is None

        lc.reset_channel(t_app)
        assert lc.get_channel() is None
        lc.reset_channel(None)                # token 为空＝什么都不做，不抛
        return lc.get_channel()

    assert asyncio.run(_main()) is None


# ---------------------------------------------------------------- 2. 硬验收：派生后台必须落 server

def test_已设app的请求上下文里派生的后台调用落server(m2_db):
    """防的就是「后台任务被请求上下文误归属」。

    链路：请求上下文 app → spawn_background（派生边界切成 server）→ 后台 LLM 调用记账
    （_record_usage_async 在该子 Task 的上下文里取渠道）→ 落库行 channel == server。
    """
    seen = {}

    async def _bg():
        seen["channel_in_bg"] = lc.get_channel()
        llm_client._record_usage_async("deepseek", "v4-flash", 30, 12, 0, task="memory")
        return "bg-returned"

    async def _request():
        outer = lc.get_channel()                # 进入前的外层基线（应为 None）
        token = lc.set_channel(lc.CHANNEL_APP)  # 模拟 App 主链路入口
        task = spawn_background(_bg())
        seen["task_result"] = await task
        seen["channel_after_spawn"] = lc.get_channel()   # 子任务不得回灌父上下文
        rows = await _wait_rows(m2_db, 1)
        lc.reset_channel(token)
        return outer, rows

    outer, rows = asyncio.run(_request())

    assert seen["channel_in_bg"] == "server", seen      # 派生边界已切断
    assert seen["channel_after_spawn"] == "app"         # 父请求仍是 app
    assert seen["task_result"] == "bg-returned"         # 包装没吞掉返回值
    assert len(rows) == 1, rows
    _id, channel, task, total = rows[0]
    assert task == "memory" and total == 42
    assert channel == "server", f"后台调用被误归属：{channel!r}"
    assert channel != "app", "后台任务被请求上下文误归属"
    assert outer is None                                # 跨请求零残留


def test_无请求上下文时后台任务也归server(m2_db):
    """调度器/事件处理器里的后台任务（从未设过渠道）经同一条派生边界 → server，而非 NULL。"""
    async def _bg():
        llm_client._record_usage_async("deepseek", "v4-flash", 5, 5, 0, task="reflection")
        return lc.get_channel()

    async def _main():
        seen = await spawn_background(_bg())
        rows = await _wait_rows(m2_db, 1)
        return seen, rows

    seen, rows = asyncio.run(_main())
    assert seen == "server"
    assert len(rows) == 1 and rows[0][1] == "server", rows


# ---------------------------------------------------------------- 3. 前台真值 / 显式传参 / fail-open

def test_前台wechat_ilink上下文的用量行落wechat_ilink(m2_db):
    """钉住「取值发生在 spawn 之前」：若把读取推迟进落库协程，这里会被派生边界改写成 server。"""
    async def _request():
        token = lc.set_channel(lc.CHANNEL_WECHAT_ILINK)
        llm_client._record_usage_async("deepseek", "v4-flash", 100, 40, 3, task="chat")
        rows = await _wait_rows(m2_db, 1)
        lc.reset_channel(token)
        return rows

    rows = asyncio.run(_request())
    assert len(rows) == 1, rows
    _id, channel, task, total = rows[0]
    assert channel == "wechat_ilink", rows
    assert task == "chat"
    assert total == 140                        # total = prompt + completion（既有口径未动）


def test_显式channel参数优先于上下文(m2_db):
    """渠道名已在调用方手里的（插件/开放 API 入口）走显式传参，不被上下文覆盖。"""
    async def _request():
        token = lc.set_channel(lc.CHANNEL_APP)
        llm_client._record_usage_async("p", "m", 1, 1, 0, task="chat", channel="wechat_ilink")
        first = await _wait_rows(m2_db, 1)
        llm_client._record_usage_async("p", "m", 1, 1, 0, task="chat")   # 未显式传 → 用上下文
        rows = await _wait_rows(m2_db, 2)
        lc.reset_channel(token)
        return first, rows

    first, rows = asyncio.run(_request())
    assert len(rows) == 2, rows
    assert [r[1] for r in rows] == ["wechat_ilink", "app"], rows


def test_渠道超长在写入侧被截断到列宽(m2_db):
    async def _request():
        token = lc.set_channel("y" * 40)
        llm_client._record_usage_async("p", "m", 1, 1, 0, task="chat")
        rows = await _wait_rows(m2_db, 1)
        lc.reset_channel(token)
        return rows

    rows = asyncio.run(_request())
    assert len(rows) == 1, rows
    assert rows[0][1] == "y" * lc.CHANNEL_MAX_LEN


def test_渠道取值异常时fail_open留NULL不影响记账(m2_db, monkeypatch):
    """归因是观测能力：上下文读取炸掉也只记 WARNING，用量行照写、channel 留 NULL。"""
    def _boom():
        raise RuntimeError("注入：contextvar 不可读")

    monkeypatch.setattr(llm_client, "get_channel", _boom)

    async def _request():
        llm_client._record_usage_async("p", "m", 7, 1, 0, task="chat")
        first = await _wait_rows(m2_db, 1)
        llm_client._record_usage_async("p", "m", 2, 1, 0, task="chat", channel="")  # 空串同样 NULL
        rows = await _wait_rows(m2_db, 2)
        return first, rows

    first, rows = asyncio.run(_request())
    assert [r[1] for r in rows] == [None, None], rows
    assert [r[3] for r in rows] == [8, 3], rows     # 记账本身一字未受影响


# ---------------------------------------------------------------- 4. 入口接线

def _src(fn) -> str:
    return inspect.getsource(fn)


def test_app主链路入口逐处标app():
    from app.api import chat as chat_api

    assert "from app.utils.llm_channel import CHANNEL_APP, set_channel" in _src(chat_api)
    # WS / HTTP send / SSE / 图片 / 文件 / 表情——App 侧真打 LLM 的入口一个不漏
    for fn in (chat_api.websocket_chat, chat_api.send_message, chat_api.stream_message,
               chat_api.upload_chat_image, chat_api.upload_chat_file,
               chat_api.send_emoji_message):
        assert "set_channel(CHANNEL_APP)" in _src(fn), fn.__name__

    # SSE 的时序硬要求：Task 创建时复制上下文快照，晚设就赶不上真流式生成
    # （比索引前先剥掉整行注释——那段注释本身就提了 create_task）
    code = "\n".join(ln for ln in _src(chat_api.stream_message).splitlines()
                     if not ln.strip().startswith("#"))
    assert code.index("set_channel(CHANNEL_APP)") < code.index("asyncio.create_task"), \
        "SSE 里 set 必须早于 create_task"

    # 语音端点只做本地 ASR（不调 LLM、不产用量行）→ 不该有渠道语义；钉一句防将来误加/误删
    assert "set_channel" not in _src(chat_api.upload_chat_voice)


def test_微信桥入口源码层透传与必清():
    import app.application.chat_service as cs

    s = _src(cs.send_and_receive)
    assert "_ch_token = set_channel(channel)" in s
    assert re.search(r"finally:\s*\n\s*reset_channel\(_ch_token\)", s), s   # try/finally 必清
    assert s.index("_ch_token = set_channel(channel)") < s.index("_run_agent_core("), \
        "取值必须早于主流程（否则赶不上派生边界的切断）"


def test_微信桥运行期把渠道透传进上下文并清回父上下文(m2_db, monkeypatch):
    """行为级复核：send_and_receive(channel=...) 期间上下文＝wechat_ilink，返回后清回原值。"""
    import app.application.chat_service as cs

    seen = {}

    async def _fake_core(*_a, **_k):
        seen["channel_during_core"] = lc.get_channel()
        seen["channel_hint"] = _k.get("channel_hint")
        return {
            "final_state": {"reasoning": "", "tools_used": [], "status_update": "",
                            "should_update_memory": False},
            "final_text": "回复文本", "gen_prompt": None, "img_text": None,
        }

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(cs, "_run_agent_core", _fake_core)
    monkeypatch.setattr(cs, "_push_user_notify", _noop)
    monkeypatch.setattr(cs, "_run_post_processing", _noop)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.chat import ChatSession
        from app.models.user import User

        async with m2_db() as db:
            db.add(User(id=13, username="m2_u13", nickname="微信桥用例用户"))
            db.add(AICharacter(id=101, user_id=13, name="小慧"))
            db.add(ChatSession(id=999, user_id=13, character_id=101))
            await db.commit()

    async def _request():
        # 微信桥是常驻轮询 task：本轮 set 本轮必清，否则下一轮无关用量也被染成 wechat
        token = lc.set_channel(lc.CHANNEL_APP)          # 外层基线（模拟已有上下文）
        await cs.send_and_receive(999, 13, 101, "你好呀", lang="zh",
                                  reply_delay=False, channel="wechat_ilink")
        after = lc.get_channel()
        lc.reset_channel(token)
        return after

    asyncio.run(_seed())
    after = asyncio.run(_request())

    assert seen["channel_during_core"] == "wechat_ilink"    # 透传到唯一读取点所在上下文
    assert seen["channel_hint"] == "wechat_ilink"           # 既有 channel_hint 语义未被动过
    assert after == "app", "收尾没清回父上下文（常驻 task 会被污染）"


# ---------------------------------------------------------------- 5. by_channel 聚合

async def _seed_channels(factory) -> None:
    """六行：app×2 / wechat_ilink / server / NULL / 空串（后两条都应归 (unknown)）。"""
    from app.models.agent import LlmUsage
    from app.utils.timeutil import now_naive_utc

    now = now_naive_utc()

    def _u(channel, total):
        return LlmUsage(user_id=ROOT_UID, provider="deepseek", model="v4-flash",
                        prompt_tokens=total, completion_tokens=0, total_tokens=total,
                        reasoning_tokens=0, task="chat", channel=channel, created_at=now)

    async with factory() as db:
        db.add_all([_u("app", 100), _u("app", 60), _u("wechat_ilink", 80),
                    _u("server", 40), _u(None, 7), _u("", 3)])
        await db.commit()


def test_usage_report的by_channel分桶正确且既有字段不减少(m2_db):
    from app.application.system import usage_report

    asyncio.run(_seed_channels(m2_db))
    data = asyncio.run(usage_report(days=7))

    assert {"window", "total", "by_task", "by_day", "by_model", "estimated_calls",
            "by_channel"} <= set(data), sorted(data)          # 既有字段一个不少
    assert data["total"]["calls"] == 6
    assert data["total"]["total_tokens"] == 290

    buckets = _buckets(data["by_channel"], "channel")
    assert set(buckets) == {"app", "wechat_ilink", "server", "(unknown)"}, data["by_channel"]
    assert buckets["app"] == {"channel": "app", "calls": 2, "prompt_tokens": 160,
                              "completion_tokens": 0, "total_tokens": 160, "reasoning_tokens": 0}
    # NULL 与空串都不回填、都归 (unknown)，不与真实取值混读
    assert buckets["(unknown)"]["calls"] == 2
    assert buckets["(unknown)"]["total_tokens"] == 10
    assert buckets["wechat_ilink"]["total_tokens"] == 80
    assert buckets["server"]["total_tokens"] == 40
    # 排序口径与 by_task 一致：用量降序
    assert [b["channel"] for b in data["by_channel"]] == \
        ["app", "wechat_ilink", "server", "(unknown)"]
    # 闭合性：by_channel 与 total 对得上（既有桶同样闭合，未因新增桶而漏计）
    assert sum(b["calls"] for b in data["by_channel"]) == data["total"]["calls"]
    assert sum(b["total_tokens"] for b in data["by_channel"]) == data["total"]["total_tokens"]
    assert sum(b["total_tokens"] for b in data["by_task"]) == data["total"]["total_tokens"]


def test_usage_report空库与读库异常也带by_channel键(m2_db, monkeypatch):
    from app.application.system import usage_report

    data = asyncio.run(usage_report(days=7))
    assert data["by_channel"] == []

    def _boom():
        raise RuntimeError("注入：用量表不可读")

    import app.db.database as db_mod

    monkeypatch.setattr(db_mod, "async_session_factory", _boom)
    data = asyncio.run(usage_report(days=7))          # fail-open：不抛，空结构仍含该键
    assert data["by_channel"] == [] and data["total"]["calls"] == 0


def test_app用量页by_channel同样归桶且只增不减(m2_db):
    from app.application import permission_service as perm
    from app.application.system import get_llm_usage

    async def _seed_user():
        from app.models.user import User

        async with m2_db() as db:
            db.add(User(id=ROOT_UID, username="m2_root", nickname="根", is_admin=True))
            await db.commit()

    for cache in (perm._admin_cache, perm._server_admin_cache, perm._account_state_cache):
        cache.clear()
    asyncio.run(_seed_user())
    asyncio.run(_seed_channels(m2_db))
    data = asyncio.run(get_llm_usage(ROOT_UID))
    for cache in (perm._admin_cache, perm._server_admin_cache, perm._account_state_cache):
        cache.clear()

    assert {"total_limit", "limit_source", "used_total", "remaining", "today", "week",
            "month", "by_model", "by_user", "by_task",
            "can_edit_limit"} <= set(data), sorted(data)
    buckets = _buckets(data["by_channel"], "channel")
    assert set(buckets) == {"app", "wechat_ilink", "server", "(unknown)"}
    assert buckets["app"]["total"] == 160 and buckets["app"]["calls"] == 2
    assert buckets["(unknown)"]["total"] == 10
    assert sum(b["total"] for b in data["by_channel"]) == data["used_total"]


# ---------------------------------------------------------------- 6. 哨兵登记

def test_老库判别哨兵已登记llm_usage_channel():
    from app.db.migrate import _CURRENT_SCHEMA_SENTINELS

    assert ("llm_usage", "channel") in _CURRENT_SCHEMA_SENTINELS, _CURRENT_SCHEMA_SENTINELS

    # 列定义与哨兵/迁移一致：可空 String(30)（老库缺列必须判「落后」走 upgrade，见 migrate.py 注）
    from app.models.agent import LlmUsage

    col = LlmUsage.__table__.c.channel
    assert col.nullable is True
    assert col.type.length == 30


# ---------------------------------------------------------------- 7. 迁移幂等与可逆（真跑 alembic）

def _alembic_cfg():
    from alembic.config import Config

    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(backend, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend, "alembic"))
    return cfg


def _alembic_version(db_path) -> str:
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def test_迁移幂等与可逆(tmp_path, monkeypatch):
    """方式：临时文件库上真跑 alembic 编程接口（command.upgrade / command.downgrade）。

    不用 _dbclone 模板库（那是 create_all 出的当前 schema，跑迁移会假绿）。
    """
    from sqlalchemy import create_engine, inspect

    import app.config as app_cfg

    db_path = tmp_path / "m2_mig.db"
    url_path = str(db_path).replace("\\", "/")
    monkeypatch.setattr(app_cfg.settings, "database_url", "sqlite+aiosqlite:///" + url_path)
    cfg = _alembic_cfg()

    def _cols() -> set:
        eng = create_engine("sqlite:///" + url_path)
        try:
            insp = inspect(eng)
            assert insp.has_table("llm_usage"), "整链跑完 llm_usage 必在（baseline 建表）"
            return {c["name"] for c in insp.get_columns("llm_usage")}
        finally:
            eng.dispose()

    def _channels() -> list:
        with sqlite3.connect(str(db_path)) as conn:
            return [r[0] for r in conn.execute("SELECT channel FROM llm_usage ORDER BY id")]

    from alembic import command

    # ① 老库形态：跑到上一节 → 有表无 channel 列，且已有一条历史行
    command.upgrade(cfg, PREV_REV)
    assert "channel" not in _cols()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO llm_usage (provider, prompt_tokens, completion_tokens, "
                     "total_tokens, reasoning_tokens) VALUES ('deepseek', 1, 0, 1, 0)")
        conn.commit()

    # ② upgrade head → 补列；历史行**不回填**（NULL 与「将来漏传」同归读端 (unknown)）
    command.upgrade(cfg, NEW_REV)
    assert _alembic_version(db_path) == NEW_REV
    assert "channel" in _cols()
    assert _channels() == [None], _channels()

    # ③ 幂等：版本号退回上一节但列还在 → 重放本迁移不报错、不动数据
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE alembic_version SET version_num=?", (PREV_REV,))
        conn.execute("INSERT INTO llm_usage (channel, prompt_tokens, completion_tokens, "
                     "total_tokens, reasoning_tokens) VALUES ('app', 2, 1, 3, 0)")
        conn.commit()
    command.upgrade(cfg, NEW_REV)
    assert _alembic_version(db_path) == NEW_REV
    assert "channel" in _cols()
    assert _channels() == [None, "app"], _channels()

    # ④ 可逆：downgrade 只删本列，表与其余列/行都在
    command.downgrade(cfg, PREV_REV)
    assert _alembic_version(db_path) == PREV_REV
    back = _cols()
    assert "channel" not in back
    assert {"id", "task", "provider", "model", "total_tokens", "created_at"} <= back
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 2

    # ⑤ 再 upgrade：列补回（该列值按设计为 NULL，不随回滚保留）
    command.upgrade(cfg, NEW_REV)
    assert "channel" in _cols()
    assert _channels() == [None, None], _channels()
