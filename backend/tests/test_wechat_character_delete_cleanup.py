# -*- coding: utf-8 -*-
"""删角色 × 渠道插件自有数据清理（2026-09-26 批 E）测试。

缺口（已核实）：插件自有表 wechat_ilink_bindings / wechat_ilink_messages 都带 character_id，
但清理只挂在「内核 channel_bindings 增删」上（on_binding_saved / on_binding_removed）——
**删角色这条路径不触发任何插件清理**，会留下指向已删角色的绑定行（含凭据）与消息历史。

本批口径（用户拍板）：内核加通用扩展点 ``notify_character_deleted``（逐渠道 SAVEPOINT 隔离，
只转调、绝不碰插件表），微信插件实现 ``on_character_deleted``——绑定行**停用 + 清凭据
（保留行留痕）**，消息历史**物理删除**。

覆盖：
1. 主路径（端到端走 ``delete_character``）：该角色绑定行仍在但 enabled=0 且凭据/游标/地址清空、
   消息 0 行；另一角色的行一条没动；
2. 幂等 + 接线：``main.py`` 确实注册了该回调；再调一次 disabled=0 / messages_deleted=0 且不报错；
3. 内核容错：某渠道回调抛异常 → 只回滚它自己（SAVEPOINT）、同批另一渠道仍被调用、删角色仍成功。

夹具口径抄 tests/test_wechat_unbind_sync.py（插件加载 + with_plugins 临时库）与
tests/test_character_delete_cleanup.py（删除链路全局副作用打桩）。
（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio
import pathlib

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

# 快测档：每例起一次临时库（含插件表），打 slow 标记，口径同 test_wechat_unbind_sync.py
pytestmark = pytest.mark.slow

_USER = 1
_CHAR = 101    # 被删角色
_OTHER = 103   # 对照角色（一条都不许动）
_TEST_SOURCE = "batch_e_test"
_PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "wechat_ilink"


async def _noop(*_a, **_k):
    return None


def _plugin_models():
    from app.plugins import registry

    return registry._loaded["wechat_ilink"]["module"].models


@pytest.fixture()
def ws_env(tmp_path, monkeypatch):
    """加载微信插件 + 含插件表的临时库 + 种子（两个角色各有绑定行与 2 条消息）。

    teardown 把插件装载与测试渠道注册一并归位，避免 on_character_deleted 外泄给同进程其它文件。
    """
    import sys as _sys

    from app.plugins import registry
    from app.providers import registry as prov_reg

    if str(_PLUGIN_DIR) not in _sys.path:
        _sys.path.insert(0, str(_PLUGIN_DIR))
    if not registry.load_plugin_dir(_PLUGIN_DIR):
        raise RuntimeError("wechat_ilink plugin failed to load")

    monkeypatch.setenv("AMBRACE_SECRET_KEY", "wechat-batch-e-test-secret-0000000000000")
    engine = clone_engine(tmp_path / "t.db", with_plugins="wechat_ilink")
    factory = make_session_factory(engine)

    import app.db.database as db_mod

    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    # 删除链路上的全局副作用（向量库、「离开记忆」）不参与本文件断言
    monkeypatch.setattr("app.db.vector_store.delete_memory_vectors_by_character", _noop)
    monkeypatch.setattr("app.memory.save_memory", _noop)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.user import User

        M = _plugin_models()
        async with factory() as db:
            db.add(User(id=_USER, username="batche", nickname="批E", is_admin=True))
            db.add(AICharacter(id=_CHAR, user_id=_USER, name="待删"))
            db.add(AICharacter(id=_OTHER, user_id=_USER, name="对照"))
            for cid in (_CHAR, _OTHER):
                db.add(M.WeChatILinkBinding(
                    user_id=_USER, tenant_id=_USER, bot_account_id=f"bot{cid}",
                    character_id=cid, ilink_user_id=f"wx{cid}", ilink_bot_id=f"ilink{cid}",
                    bot_token_enc=f"enc-{cid}", baseurl="https://ilinkai.weixin.qq.com",
                    poll_buf=f"buf-{cid}", enabled=True))
                for i in range(2):
                    db.add(M.WeChatILinkMessage(
                        binding_id=cid, character_id=cid, ilink_msg_id=f"m{cid}-{i}",
                        direction="in", content="你好"))
            await db.commit()

    asyncio.run(_seed())
    yield factory
    asyncio.run(engine.dispose())
    prov_reg.unregister_providers_for_source("wechat_ilink")
    prov_reg.unregister_providers_for_source(_TEST_SOURCE)
    registry._loaded.pop("wechat_ilink", None)
    registry._db_config.pop("wechat_ilink", None)
    registry._enabled.pop("wechat_ilink", None)


# ------------------------------------------------------------------ 读回断言用的取数 helper


def _binding_rows(factory) -> dict[int, object]:
    M = _plugin_models()

    async def _run():
        async with factory() as db:
            rows = (await db.execute(select(M.WeChatILinkBinding))).scalars().all()
            return {int(r.character_id): r for r in rows}

    return asyncio.run(_run())


def _message_counts(factory) -> dict[int, int]:
    M = _plugin_models()

    async def _run():
        async with factory() as db:
            cids = (await db.execute(select(M.WeChatILinkMessage.character_id))).scalars().all()
        counts: dict[int, int] = {}
        for cid in cids:
            counts[int(cid)] = counts.get(int(cid), 0) + 1
        return counts

    return asyncio.run(_run())


def _char_exists(factory, cid: int) -> bool:
    from app.models.character import AICharacter

    async def _run():
        async with factory() as db:
            return await db.get(AICharacter, cid) is not None

    return asyncio.run(_run())


def _delete_character(factory, cid: int) -> None:
    from app.application.characters import delete_character

    async def _run():
        async with factory() as db:
            await delete_character(db, cid, _USER, "zh")
            await db.commit()

    asyncio.run(_run())


def _notify(factory, cid: int, *, user_id: int | None = _USER) -> dict:
    from app.providers.channel import notify_character_deleted

    async def _run():
        async with factory() as db:
            out = await notify_character_deleted(db, cid, user_id=user_id)
            await db.commit()
            return out

    return asyncio.run(_run())


def _call_handler(factory, handler, cid: int) -> dict:
    async def _run():
        async with factory() as db:
            out = await handler(db, cid, user_id=_USER)
            await db.commit()
            return out

    return asyncio.run(_run())


# ------------------------------------------------------------------ 1. 主路径（端到端删角色）


def test_删角色_微信绑定停用清凭据_消息物理删除_对照角色不动(ws_env):
    factory = ws_env
    assert _message_counts(factory) == {_CHAR: 2, _OTHER: 2}
    assert _binding_rows(factory)[_CHAR].enabled is True

    _delete_character(factory, _CHAR)

    assert _char_exists(factory, _CHAR) is False, "角色行本身应已硬删"
    rows = _binding_rows(factory)
    assert _CHAR in rows, "绑定行须保留（留痕），不是物理删"
    dead = rows[_CHAR]
    assert dead.enabled is False
    assert dead.bot_token_enc == "" and dead.ilink_bot_id == ""
    assert dead.baseurl == "" and dead.poll_buf == ""
    # ②消息历史物理删除；③对照角色一条没动（含凭据/游标/微信身份/消息）
    assert _message_counts(factory) == {_OTHER: 2}
    alive = rows[_OTHER]
    assert alive.enabled is True
    assert alive.bot_token_enc == f"enc-{_OTHER}"
    assert alive.ilink_bot_id == f"ilink{_OTHER}"
    assert alive.baseurl == "https://ilinkai.weixin.qq.com"
    assert alive.poll_buf == f"buf-{_OTHER}"
    assert alive.ilink_user_id == f"wx{_OTHER}"


# ------------------------------------------------------------------ 2. 接线 + 幂等


def test_插件入口已注册回调_重复清理幂等不报错(ws_env):
    factory = ws_env
    from app.providers.channel import _channel_binding_hooks

    handler = _channel_binding_hooks("wechat").get("on_character_deleted")
    assert handler is not None, "main.py 未注册 on_character_deleted＝内核转调不到，清理缺口仍在"

    first = _call_handler(factory, handler, _CHAR)
    assert first == {"disabled": 1, "messages_deleted": 2}
    second = _call_handler(factory, handler, _CHAR)
    assert second == {"disabled": 0, "messages_deleted": 0}, "幂等：已停用/已清空的行不得重复计数"
    # 经内核转调再来一次同样不报错，且不牵连其它渠道
    result = _notify(factory, _CHAR)
    assert "wechat" in result["channels"] and result["failed"] == []
    assert _binding_rows(factory)[_OTHER].enabled is True


# ------------------------------------------------------------------ 3. 内核容错（单渠道失败隔离）


def test_内核容错_单渠道失败只回滚自己且不阻断删除(ws_env):
    from app.providers.channel import register_channel, set_channel_binding_hooks

    factory = ws_env
    M = _plugin_models()
    calls: list[tuple[int, int | None]] = []

    async def _boom(db, character_id, *, user_id=None):
        row = (await db.execute(select(M.WeChatILinkBinding).where(
            M.WeChatILinkBinding.character_id == _OTHER))).scalars().first()
        row.poll_buf = "POISONED"
        await db.flush()  # 真写进 SAVEPOINT，验证失败渠道的改动被回滚
        raise RuntimeError("渠道清理失败（测试注入）")

    async def _ok(db, character_id, *, user_id=None):
        calls.append((int(character_id), user_id))
        return {"ok": True}

    register_channel("batch_e_boom", object(), meta={"label": "测试-必炸"}, source=_TEST_SOURCE)
    set_channel_binding_hooks("batch_e_boom", {"on_character_deleted": _boom})
    register_channel("batch_e_ok", object(), meta={"label": "测试-正常"}, source=_TEST_SOURCE)
    set_channel_binding_hooks("batch_e_ok", {"on_character_deleted": _ok})

    result = _notify(factory, _CHAR)
    assert "batch_e_boom" in result["failed"]
    assert "batch_e_ok" in result["channels"]
    assert calls == [(_CHAR, _USER)], "失败渠道不得影响同批其它渠道的调用"
    assert _binding_rows(factory)[_OTHER].poll_buf == f"buf-{_OTHER}", "失败渠道的写入须被 SAVEPOINT 回滚"

    _delete_character(factory, _CHAR)
    assert _char_exists(factory, _CHAR) is False, "渠道清理失败不得阻断删角色"
    assert _binding_rows(factory)[_OTHER].poll_buf == f"buf-{_OTHER}"
