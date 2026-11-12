# -*- coding: utf-8 -*-
"""Contract-fix regressions: world `author` fallback + stable invalid-key code."""
import asyncio

import pytest

from test_garden_portable_user_api import (  # noqa: E402,F401
    BASE, C, _asm_messages, _client, wired,
)

U = 98401


class _WorldFeed:
    """Actual world item: `author` plus optional `author_display`."""

    def __init__(self, base, key="", name="", timeout=0):
        self.base = base

    def user_whoami(self, user_key):
        if user_key != "UK_GOOD":
            from garden_forum_sdk import GardenError
            raise GardenError("invalid_user_key", "synthetic")
        return {"ok": True, "active": True, "scope": "readonly",
                "role_id": "rl_0123456789ab", "character_id": "sam",
                "agent": "p_synthetic"}

    def user_feed(self, user_key, circle=None, limit=20, offset=0):
        return {"ok": True, "items": [
            {"id": 1, "title": "无显示名", "body": "正文", "author": "作者A",
             "circle": "花园", "created_at": "2026-10-31"},
            {"id": 2, "title": "旧字段优先", "body": "正文", "author": "内部作者",
             "author_display": "显示作者", "circle": "花园", "created_at": "2026-10-31"},
            {"id": 3, "title": "超长", "body": "正文", "author": "A" * 200,
             "circle": "花园", "created_at": "2026-10-31"},
            {"id": 4, "title": "无作者", "body": "正文",
             "circle": "花园", "created_at": "2026-10-31"},
        ], "total": 3, "scope": "readonly"}


class _BadKey:
    def __init__(self, base, key="", name="", timeout=0):
        self.base = base

    def user_whoami(self, user_key):
        from garden_forum_sdk import GardenError
        raise GardenError("invalid_user_key", "synthetic")

    def user_feed(self, user_key, circle=None, limit=20, offset=0):
        raise AssertionError("must not read feed with invalid key")


def _bind(mod, key="UK_GOOD"):
    return asyncio.run(mod.bind_user_account(C, U, key, BASE))


def test_author_falls_back_to_world_field_and_preserves_old_field(wired):  # noqa: F811
    mod, _registry = wired
    mod.GardenForum = _WorldFeed
    assert _bind(mod)["ok"] is True
    r = asyncio.run(mod.run_user_readonly(U, C, observe=False))
    assert r["ok"] is True
    items = r["items"]
    assert [(i["id"], i["author_display"]) for i in items] == [
        (1, "作者A"), (2, "显示作者"), (3, "A" * 80), (4, "")]
    assert "author" not in items[0]  # 不透传内部字段

    # observe=true 走同一条 out 映射；确认进入上下文的是作者显示名。
    observed = asyncio.run(mod.run_user_readonly(U, C, observe=True))
    assert observed.get("observed") is True
    msgs = _asm_messages()
    joined = "\n".join(str(m.get("content") or "") for m in msgs)
    assert "作者A" in joined


def test_invalid_key_stable_code_in_bind_and_read(wired):  # noqa: F811
    mod, _registry = wired
    mod.GardenForum = _BadKey
    assert _bind(mod, "UK_BAD")["code"] == "user_key_invalid"

    # 先以正常 world 绑定，再把远端换成 invalid，验证只读路径同码。
    mod.GardenForum = _WorldFeed
    assert _bind(mod)["ok"] is True
    mod.GardenForum = _BadKey
    r = asyncio.run(mod.run_user_readonly(U, C, observe=False))
    assert r["code"] == "user_key_invalid"


@pytest.mark.parametrize("code", ["user_key_revoked", "user_key_expired", "unreachable", "timeout"])
def test_read_error_codes_preserved(wired, code):  # noqa: F811
    mod, _registry = wired
    mod.GardenForum = _WorldFeed
    assert _bind(mod)["ok"] is True
    class _CodeGF:
        def __init__(self, base, key="", name="", timeout=0):
            self.base = base

        def user_whoami(self, _key):
            from garden_forum_sdk import GardenError
            raise GardenError(code, "synthetic")

        def user_feed(self, *_a, **_k):
            raise AssertionError("unexpected feed")

    mod.GardenForum = _CodeGF
    r = asyncio.run(mod.run_user_readonly(U, C, observe=False))
    assert r["code"] == code
