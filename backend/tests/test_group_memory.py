# -*- coding: utf-8 -*-
"""群聊记忆 speaker 归属测试（2026-08-18 修复）：按发言者拆分、speaker 正确、存给所有成员"""
import asyncio

from app.api.chat_groups import build_group_memory_entries


# ---------------- 纯函数：build_group_memory_entries ----------------

def test_entries_用户发言加多角色回应各拆一条():
    name_map = {11: "小阳", 12: "小冰"}
    entries = build_group_memory_entries(
        "@小阳 明天一起做饭好不好？",
        [
            {"character_id": 11, "content": "好呀好呀！我来打下手！"},
            {"character_id": 12, "content": "可以，我带点蔬菜。"},
        ],
        name_map,
    )
    assert len(entries) == 3
    assert entries[0] == {"speaker_type": "user", "speaker_id": 0,
                          "content": "用户在群里说：@小阳 明天一起做饭好不好？"}
    assert entries[1]["speaker_type"] == "character" and entries[1]["speaker_id"] == 11
    assert entries[1]["content"] == "小阳在群里说：好呀好呀！我来打下手！"
    assert entries[2]["speaker_type"] == "character" and entries[2]["speaker_id"] == 12
    assert entries[2]["content"] == "小冰在群里说：可以，我带点蔬菜。"


def test_entries_无回应时用户发言speaker为user():
    entries = build_group_memory_entries("大哥二哥你们辛苦了", [], {})
    assert len(entries) == 1
    assert entries[0]["speaker_type"] == "user"
    assert entries[0]["content"] == "用户在群里说：大哥二哥你们辛苦了"


def test_entries_空用户消息与空回应不产生条目():
    entries = build_group_memory_entries("", [], {})
    assert entries == []
    entries = build_group_memory_entries("你好", [{"character_id": None, "content": "x"}], {})
    assert len(entries) == 1 and entries[0]["speaker_type"] == "user"


def test_entries_未知角色名回退为角色字样():
    entries = build_group_memory_entries("hi", [{"character_id": 99, "content": "hello"}], {})
    assert entries[1]["content"] == "角色在群里说：hello"
    assert entries[1]["speaker_id"] == 99


def test_entries_截断上限():
    entries = build_group_memory_entries("长" * 200, [], {})
    assert len(entries[0]["content"]) <= 100 + len("用户在群里说：")
    entries = build_group_memory_entries("hi", [{"character_id": 1, "content": "长" * 200}], {1: "A"})
    assert len(entries[1]["content"]) <= 80 + len("A在群里说：")


# ---------------- 集成：_save_group_memory 调用参数（mock DB 与 save_memory） ----------------

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows
    def scalars(self):
        return self
    def all(self):
        return self._rows
    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    def __init__(self, members, reply_rows, recent=None):
        self._members = members
        self._reply_rows = reply_rows
        self._recent = recent
        self.calls = []
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def execute(self, stmt, *a):
        self.calls.append(stmt)
        text = str(stmt)
        if "chat_group_members" in text:
            return _FakeResult(self._members)
        if "memories" in text:
            return _FakeResult([self._recent] if self._recent else [])
        if "ai_characters" in text:
            return _FakeResult(self._reply_rows)
        return _FakeResult([])


def test_save_group_memory_每个成员每发言者一条且speaker正确(monkeypatch):
    from app.api import chat_groups as cg
    saved = []
    async def fake_save(**kw):
        saved.append(kw)
    monkeypatch.setattr(cg, "async_session_factory", lambda: _FakeDB(
        members=[11, 12],
        reply_rows=[(11, "小阳"), (12, "小冰")],
        recent=None,
    ))
    import app.memory.service as memsvc
    monkeypatch.setattr(memsvc, "save_memory", fake_save)
    asyncio.run(cg._save_group_memory(
        group_id=1, user_id=4,
        user_content="@小阳 明天一起做饭好不好？",
        replies=[{"character_id": 11, "content": "好呀！"}, {"character_id": 12, "content": "可以。"}],
    ))
    # 2 成员 × 3 发言者 = 6 条
    assert len(saved) == 6
    by_member = {}
    for s in saved:
        by_member.setdefault(s["character_id"], []).append(s)
    assert set(by_member) == {11, 12}
    for member in (11, 12):
        sps = sorted((s["speaker_type"], s["speaker_id"]) for s in by_member[member])
        assert sps == [("character", 11), ("character", 12), ("user", 4)]
        assert all(s["source"] == "group" and s["sub_type"] == "group" for s in by_member[member])
        assert all(s["skip_dedup"] is True for s in by_member[member])


def test_save_group_memory_节流命中不重复写(monkeypatch):
    from app.api import chat_groups as cg
    saved = []
    async def fake_save(**kw):
        saved.append(kw)
    monkeypatch.setattr(cg, "async_session_factory", lambda: _FakeDB(
        members=[11], reply_rows=[], recent=12345,
    ))
    import app.memory.service as memsvc
    monkeypatch.setattr(memsvc, "save_memory", fake_save)
    asyncio.run(cg._save_group_memory(group_id=1, user_id=4, user_content="hi", replies=[]))
    assert saved == []


def test_save_group_memory_节流按群过滤且写group_id(monkeypatch):
    """P3-3：节流查询按 group_id 过滤（含旧数据 IS NULL 兼容），落库时写 group_id。"""
    from app.api import chat_groups as cg
    captured = {}

    class _SqlDB(_FakeDB):
        async def execute(self, stmt, *a):
            self.calls.append(stmt)
            text = str(stmt)
            if "chat_group_members" in text:
                return _FakeResult([11])
            if "memories" in text:
                captured["sql"] = text
                return _FakeResult([])  # 无近期同群记忆 → 不节流
            if "ai_characters" in text:
                return _FakeResult([])
            return _FakeResult([])

    saved = []

    async def fake_save(**kw):
        saved.append(kw)

    monkeypatch.setattr(cg, "async_session_factory", lambda: _SqlDB([11], [], None))
    import app.memory.service as memsvc
    monkeypatch.setattr(memsvc, "save_memory", fake_save)
    asyncio.run(cg._save_group_memory(group_id=7, user_id=4, user_content="hi", replies=[]))
    sql = captured.get("sql", "")
    # 节流查询同时含「同一群」等值与「旧数据 NULL 兼容」条件
    assert "memories.group_id" in sql
    assert "IS NULL" in sql
    # 落库记忆带 group_id
    assert saved and all(s.get("group_id") == 7 for s in saved)
