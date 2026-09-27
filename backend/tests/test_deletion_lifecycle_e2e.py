# -*- coding: utf-8 -*-
"""删除生命周期端到端（专项派单 2026-09-27；D-1…D-5 修复后转为回归套件）。

矩阵：行 = 删除路径（删角色 / 删号 / 删群 / 遗弃宠物），列 = 关联面
（a 前瞻意图 PIS、b 渠道绑定、c 共享记忆、d 对局记录）。每个用例的 docstring
第一行标注它钉的是哪一格（如「格 ①a」），报告按此逐格给结论。

口径：
- 一律走**真实服务函数**：``application.characters.delete_character``、
  ``application.character_cascade.cascade_delete_character``、
  ``application.account_deletion.mark_deleted/restore``、
  ``application.account_purge.purge_account``、``application.chat_groups.delete_group``、
  ``application.pet_service.abandon_pet``、``application.channel_binding_service``、
  ``scheduling.prospective_intent``（采集/认领两侧）、``scheduling.pet_care``（采集侧）；
- 库：pytest ``tmp_path`` + ``tests/_dbclone`` 模板克隆（每用例私有 SQLite，
  ``PRAGMA foreign_keys=ON``，与生产同口径）；**绝不碰 backend/data 生产库**；
- 外部依赖桩（只桩这三类，其余全真跑）：
  ① 向量层 ``delete_memory_vectors_by_character`` / ``delete_memory_vectors_by_user``
     —— 真 Chroma 落盘与 SQL 生命周期无关，桩成「记录调用」；
  ② ``app.memory.save_memory`` —— 记忆写入含抽取/向量双写，桩成「记录参数」，
     既能验证「删角色给其他角色补【xxx离开了】记忆」的调用面，又不拉起 LLM；
  ③ 清除器前置备份 ``_run_backup`` 与文件目录 ``_data_dir`` —— 真 ``do_backup()`` 会拷
     生产库与源码，测试里换成 tmp 下的假 zip；
- 「渠道插件侧清理」用**合成渠道 + on_character_deleted 回调**验内核分发与 SAVEPOINT 隔离
  （真微信插件的自有表清理由既有 tests/test_wechat_character_delete_cleanup.py 覆盖，报告引用）。

断言纪律：本文件由「专项验证」转为**删除链路回归套件**（2026-09-27 修复派单）——验证当时
记录的 5 处缺陷 D-1…D-5 已全部落地修复，原按「现状」断言并以 ``DEFECT(D-n)`` 标记的用例已
改成正向断言（断言**期望**行为）。任一修复被回退即红灯，用例 docstring 标注对应缺陷号与修法
位置，供回溯。
"""
import asyncio
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from _dbclone import clone_engine, make_session_factory
from test_account_deletion import _hash, _patch_session_factories

from app.application import account_deletion, account_purge, pet_service
from app.application.channel_binding_service import remove_binding, resolve_character, upsert_binding
from app.application.chat_groups import delete_group
from app.application.character_cascade import cascade_delete_character
from app.application.characters import delete_character
from app.models.channel import ChannelBinding
from app.models.character import AICharacter
from app.models.chat import ChatGroup, ChatGroupMember, ChatGroupMessage, ChatSession, GroupMemory
from app.models.game import GameEvent, GameMemory, GamePlayer, GameSession
from app.models.memory import Memory, ProspectiveIntent, SharedEvent
from app.models.pet import Pet
from app.models.user import GlobalUserFact, User
from app.db import vector_store
from app.memory import bm25_index
from app.providers import registry as provider_registry
from app.providers.channel import register_channel, set_channel_binding_hooks
from app.scheduling import pet_care
from app.scheduling.prospective_intent import (
    _now_local_naive,
    claim_intent_for_fire,
    collect_due_promises,
)

pytestmark = pytest.mark.slow

# ── 种子主键（一次性分配，避免各用例互相撞号）────────────────────────────────
ACTOR_UID = 2       # 邻居账号，同时是 server_admin（删号发起方）
ROOT_UID = 1        # 被测账号（独立主账号）：删角色 / 被删号的目标
CHA, CHB = 11, 12   # ROOT 名下两个角色
NEIGH_CID = 21      # ACTOR 名下角色（一切删除都必须动不到它）
SESS_A, SESS_B, SESS_N = 101, 102, 103
OK_GID, BOOM_GID = 7777, 8888   # 仅代打「渠道自有数据」的宿主群行（见 notify 用例注释）
PW = "rootpass123"
CHANNEL = "douyin"  # 未注册渠道 → _binding_mode 回落 family_single，bot 恒 'default'


def _rows(dst, table, **where):
    """裸 SQL 读回（绕开 ORM 身份映射，保证断言打在磁盘上）。"""
    for ident in (table, *where):
        assert ident.isidentifier(), f"非法标识符: {ident}"
    con = sqlite3.connect(str(dst))
    try:
        cols = list(where)
        sql = f'SELECT * FROM "{table}"'
        if cols:
            sql += " WHERE " + " AND ".join(f'"{c}" = ?' for c in cols)
        cur = con.execute(sql, tuple(where[c] for c in cols))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]
    finally:
        con.close()


def _n(dst, table, **where) -> int:
    return len(_rows(dst, table, **where))


def _fk_orphans(dst):
    con = sqlite3.connect(str(dst))
    try:
        con.execute("PRAGMA foreign_keys=ON")
        return con.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        con.close()


def _table_cols(dst, table):
    con = sqlite3.connect(str(dst))
    try:
        return {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
    finally:
        con.close()


def _seed_base(factory):
    """两账号 / ROOT 两角色 / 各一条私聊会话（所有用例的共同底座）。"""
    async def _run():
        async with factory() as db:
            db.add_all([
                User(id=ROOT_UID, username="root", nickname="户主", is_admin=True,
                     server_admin=True, password_hash=_hash(PW)),
                User(id=ACTOR_UID, username="actor", nickname="邻居", is_admin=True,
                     server_admin=True, password_hash=_hash(PW)),
            ])
            await db.flush()
            db.add_all([
                AICharacter(id=CHA, user_id=ROOT_UID, name="小甲", is_active=True),
                AICharacter(id=CHB, user_id=ROOT_UID, name="小乙", is_active=True),
                AICharacter(id=NEIGH_CID, user_id=ACTOR_UID, name="邻家", is_active=True),
            ])
            await db.flush()
            db.add_all([
                ChatSession(id=SESS_A, user_id=ROOT_UID, character_id=CHA),
                ChatSession(id=SESS_B, user_id=ROOT_UID, character_id=CHB),
                ChatSession(id=SESS_N, user_id=ACTOR_UID, character_id=NEIGH_CID),
            ])
            await db.commit()
    asyncio.run(_run())


def _stub_kernel_boundaries(monkeypatch, calls):
    """桩①②：向量层 + save_memory（只记录，不真跑 Chroma / 抽取链路）。"""
    async def _noop_vec_char(character_id, *a, **k):
        calls["vector_char"].append(int(character_id))
        return None

    async def _noop_vec_user(user_id, memory_ids=None, *a, **k):
        calls["vector_user"].append((int(user_id), [int(m) for m in (memory_ids or [])]))
        return {"by_user": 0, "by_memory": len(memory_ids or []), "unresolved": 0}

    async def _spy_save_memory(**kw):
        calls["save_memory"].append(kw)
        return None

    monkeypatch.setattr(vector_store, "delete_memory_vectors_by_character", _noop_vec_char)
    monkeypatch.setattr(vector_store, "delete_memory_vectors_by_user", _noop_vec_user)
    monkeypatch.setattr("app.memory.save_memory", _spy_save_memory)


@pytest.fixture()
def lc_env(tmp_path, monkeypatch):
    """删角色 / 删群 / 遗弃宠物三行共用的底座（ROOT 账号 + 两角色 + 会话）。"""
    dst = tmp_path / "lifecycle.db"
    engine = clone_engine(dst)
    factory = make_session_factory(engine)
    _patch_session_factories(monkeypatch, factory)
    calls = {"vector_char": [], "vector_user": [], "save_memory": []}
    _stub_kernel_boundaries(monkeypatch, calls)
    _seed_base(factory)
    yield SimpleNamespace(dst=dst, factory=factory, calls=calls, tmp_path=tmp_path)
    asyncio.run(engine.dispose())


@pytest.fixture()
def acct_env(tmp_path, monkeypatch):
    """删号行专用：额外把清除器的备份 / 文件目录 / BM25 落盘根挪进 tmp。"""
    dst = tmp_path / "acct.db"
    engine = clone_engine(dst)
    factory = make_session_factory(engine)
    _patch_session_factories(monkeypatch, factory)
    calls = {"vector_char": [], "vector_user": [], "save_memory": [], "backup": 0}
    _stub_kernel_boundaries(monkeypatch, calls)
    _seed_base(factory)

    data = tmp_path / "data"
    (data / "uploads").mkdir(parents=True)
    monkeypatch.setattr(account_purge, "_data_dir", lambda: data)

    def _fake_backup() -> str:
        zip_path = tmp_path / "backups" / "20260927.zip"
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        zip_path.write_text("x", encoding="utf-8")
        calls["backup"] += 1
        return str(zip_path)

    monkeypatch.setattr(account_purge, "_run_backup", _fake_backup)
    bm25_root = tmp_path / "bm25_cache"
    bm25_root.mkdir()
    monkeypatch.setattr(bm25_index, "_persist_root", bm25_root)

    perm_mod = _import_permission_service()
    perm_mod._admin_cache.clear()
    perm_mod._server_admin_cache.clear()
    perm_mod._account_state_cache.clear()
    yield SimpleNamespace(dst=dst, factory=factory, calls=calls, tmp_path=tmp_path, data=data)
    perm_mod._admin_cache.clear()
    perm_mod._server_admin_cache.clear()
    perm_mod._account_state_cache.clear()
    asyncio.run(engine.dispose())


def _import_permission_service():
    from app.application import permission_service
    return permission_service


def _seed_pis(factory, rows):
    """rows: [(id, character_id, status, kind)]，due 窗口按「北京 naive」写入（与提取器同口径）。"""
    async def _run():
        async with factory() as db:
            local_now = _now_local_naive()
            for cid, char_id, status, kind in rows:
                db.add(ProspectiveIntent(
                    id=cid, user_id=ROOT_UID, character_id=char_id,
                    content=f"承诺{cid}", kind=kind,
                    due_start=local_now - timedelta(hours=1), due_end=local_now - timedelta(minutes=10),
                    status=status, chat_session_id=SESS_A if char_id == CHA else SESS_B,
                ))
            await db.commit()
    asyncio.run(_run())


def _seed_memories(factory, rows):
    """rows: [(id, character_id, content)]；用于「他角色记得被删角色」的共享记忆面。"""
    async def _run():
        async with factory() as db:
            for mid, char_id, content in rows:
                db.add(Memory(id=mid, user_id=ROOT_UID, character_id=char_id,
                              memory_type="event", content=content, importance=50))
            await db.commit()
    asyncio.run(_run())


def _act_delete_character(factory, character_id=CHA, user_id=ROOT_UID):
    async def _run():
        async with factory() as db:
            await delete_character(db, character_id, user_id, "zh")
            await db.commit()
    asyncio.run(_run())


# ══════════════════════════ 格 ①a：删角色 × 前瞻意图 PIS ══════════════════════════

def test_delete_character_cancels_pending_and_matched_pis_without_deleting_rows(lc_env):
    """格 ①a（通过）：pending/matched → cancelled（终态、不删行）；已兑现/作废行留痕不受影响。

    证据点：
    - 行数不变（4 条仍 4 条）＝「置 cancelled 而非删行」；
    - 主动提起扫描谓词（prospective_intent.py:504 ``status == "pending"``）命中数归 0
      ＝「不再白烧 LLM」的结构性判据。
    """
    _seed_pis(lc_env.factory, [
        (9001, CHA, "pending", "promise"),
        (9002, CHA, "matched", "promise"),
        (9003, CHA, "discharged", "promise"),
        (9004, CHA, "cancelled", "promise"),
        (9011, CHB, "pending", "promise"),   # 同账号另一角色：必须不受影响
    ])
    _act_delete_character(lc_env.factory)

    mine = {r["id"]: r["status"] for r in _rows(lc_env.dst, "prospective_intents", character_id=CHA)}
    assert len(mine) == 4, f"PIS 行被删了（期望终态留痕不删行）: {mine}"
    assert mine[9001] == "cancelled" and mine[9002] == "cancelled", f"未触发态未取消: {mine}"
    assert mine[9003] == "discharged" and mine[9004] == "cancelled", f"终态被改写: {mine}"
    # 另一角色的 pending 不受牵连
    assert _rows(lc_env.dst, "prospective_intents", id=9011)[0]["status"] == "pending"
    # 扫描谓词命中数（可提起候选的 WHERE 口径）
    assert _n(lc_env.dst, "prospective_intents", character_id=CHA, status="pending") == 0
    assert _n(lc_env.dst, "prospective_intents", character_id=CHA, status="matched") == 0


def test_cascade_only_cancels_pis_and_leaves_row_deletion_to_plan(lc_env):
    """格 ①a 补充（通过）：单独调 cascade 入口同样只取消不删行（级联清单刻意不含 PIS）。

    放大判据：级联的 UPDATE 谓词只覆盖 pending/matched，已终态（expired）的行必须原样留着
    —— 证明「取消」是**按生命周期状态精确改写**，不是「该角色的一律抹成 cancelled」。
    """
    _seed_pis(lc_env.factory, [(9101, CHA, "pending", "promise"), (9102, CHA, "expired", "cue")])

    async def _run():
        async with lc_env.factory() as db:
            stats = await cascade_delete_character(db, CHA)
            await db.commit()
        return stats

    stats = asyncio.run(_run())
    assert stats.get("Memory", 0) >= 0                      # cascade 返回逐表计数（可观测）
    got = {r["id"]: r["status"] for r in _rows(lc_env.dst, "prospective_intents", character_id=CHA)}
    assert got == {9101: "cancelled", 9102: "expired"}, got
    # 级联只清从属数据，角色行由调用方（characters.delete_character）自己删 → 这里必须还在
    assert _n(lc_env.dst, "ai_characters", id=CHA) == 1


# ══════════════════════════ 格 ①b：删角色 × 渠道绑定 ══════════════════════════

def test_delete_character_blocked_by_channel_binding_then_deletable_after_unbind(lc_env):
    """格 ①b（通过）：有绑定 → 409 且零写入；解绑后可删，绑定行随之消失。"""
    _seed_memories(lc_env.factory, [(9201, CHA, "我自己的记忆")])

    async def _bind():
        async with lc_env.factory() as db:
            row = await upsert_binding(db, ROOT_UID, CHANNEL, CHA, bot_label="小甲的号")
            await db.commit()
            return int(row.id)

    bind_id = asyncio.run(_bind())
    assert _n(lc_env.dst, "channel_bindings", id=bind_id) == 1

    with pytest.raises(HTTPException) as ei:
        _act_delete_character(lc_env.factory)
    assert ei.value.status_code == 409
    assert "解绑" in ei.value.detail, f"409 文案未指引解绑: {ei.value.detail}"
    # 拦截必须是「零写入」：角色 / 记忆 / 绑定都还在
    assert _n(lc_env.dst, "ai_characters", id=CHA) == 1
    assert _n(lc_env.dst, "memories", id=9201) == 1
    assert _n(lc_env.dst, "channel_bindings", character_id=CHA) == 1

    async def _unbind():
        async with lc_env.factory() as db:
            assert await remove_binding(db, ROOT_UID, CHANNEL) is True
            await db.commit()

    asyncio.run(_unbind())
    assert _n(lc_env.dst, "channel_bindings", character_id=CHA) == 0
    _act_delete_character(lc_env.factory)
    assert _n(lc_env.dst, "ai_characters", id=CHA) == 0
    assert _n(lc_env.dst, "channel_bindings", character_id=CHA) == 0, "删角色后仍有指向死角色的绑定"
    assert _n(lc_env.dst, "memories", id=9201) == 0


def test_delete_character_notifies_all_channels_and_isolates_failing_channel(lc_env, monkeypatch):
    """格 ①b（通过）：删除后转调各渠道 on_character_deleted；单渠道失败只回滚它自己。

    mock 面：合成两个渠道（不依赖真微信/抖音插件），回调里写真表行以观测 SAVEPOINT 语义。
    """
    snapshot = dict(provider_registry._ENTRIES)
    seen = []

    async def _ok_hook(db, character_id, *, user_id=None):
        seen.append(("ok", character_id, user_id))
        db.add(GroupMemory(group_id=OK_GID, user_id=ROOT_UID, speaker_type="system",
                           content=f"plugin-cleanup char={character_id}"))

    async def _boom_hook(db, character_id, *, user_id=None):
        seen.append(("boom", character_id, user_id))
        db.add(GroupMemory(group_id=BOOM_GID, user_id=ROOT_UID, speaker_type="system",
                           content="rolled-back"))
        raise RuntimeError("插件清理炸了")

    # 渠道自有表在测试里用两张「只属于渠道回调」的群行代打（group_memories.group_id 有外键，
    # 必须先有群行；级联清单从不删 chat_groups，所以这两行不会被删除路径牵动）。
    async def _seed_scratch():
        async with lc_env.factory() as db:
            db.add_all([ChatGroup(id=OK_GID, user_id=ROOT_UID, name="渠道自有-A"),
                        ChatGroup(id=BOOM_GID, user_id=ROOT_UID, name="渠道自有-B")])
            await db.commit()

    asyncio.run(_seed_scratch())
    try:
        register_channel("e2e_ok", object(), {"plugin": "e2e_ok"}, source="builtin")
        register_channel("e2e_boom", object(), {"plugin": "e2e_boom"}, source="builtin")
        set_channel_binding_hooks("e2e_ok", {"on_character_deleted": _ok_hook})
        set_channel_binding_hooks("e2e_boom", {"on_character_deleted": _boom_hook})

        _act_delete_character(lc_env.factory)

        assert {s[0] for s in seen} == {"ok", "boom"}, f"渠道未被全部通知: {seen}"
        assert all(s[1] == CHA and s[2] == ROOT_UID for s in seen), seen
        assert _n(lc_env.dst, "ai_characters", id=CHA) == 0, "单渠道失败拖垮了删角色"
        assert _n(lc_env.dst, "group_memories", group_id=OK_GID) == 1, "成功渠道的清理没落库"
        assert _n(lc_env.dst, "group_memories", group_id=BOOM_GID) == 0, "失败渠道的写没被 SAVEPOINT 回滚"
    finally:
        provider_registry._ENTRIES.clear()
        provider_registry._ENTRIES.update(snapshot)


# ══════════════════════════ 格 ①c：删角色 × 共享记忆 ══════════════════════════

def test_delete_character_keeps_user_facts_and_other_characters_memories(lc_env):
    """格 ①c（通过）：账号级用户事实不随角色消失；他角色对被删角色的记忆保留 + 离开标记。"""
    async def _seed():
        async with lc_env.factory() as db:
            db.add(GlobalUserFact(user_id=ROOT_UID, slot="job", value="程序员", source="chat"))
            db.add(Memory(id=9301, user_id=ROOT_UID, character_id=CHA,
                          memory_type="event", content="我自己的私密记忆", importance=50))
            db.add(Memory(id=9302, user_id=ROOT_UID, character_id=CHB,
                          memory_type="event", content="小甲喜欢手冲咖啡", importance=60,
                          speaker_type="character", speaker_id=CHA))
            await db.commit()

    asyncio.run(_seed())
    _act_delete_character(lc_env.factory)

    # 用户事实是账号维度（user_facts 无 character_id 列）→ 必须活着
    assert _n(lc_env.dst, "user_facts", user_id=ROOT_UID) == 1
    # 被删角色自己的记忆清空
    assert _n(lc_env.dst, "memories", character_id=CHA) == 0
    # 他角色的记忆保留且打上离开标记（名字#id，重名可分辨）
    kept = _rows(lc_env.dst, "memories", id=9302)
    assert len(kept) == 1, "其他角色对被删角色的记忆被误删（会造成关系割裂）"
    assert "小甲#11" in (kept[0]["departed_names"] or ""), f"离开标记缺失: {kept[0]}"
    # 删除提交后为相关角色补【xxx离开了】记忆（save_memory 为记录型桩）
    dep = [c for c in lc_env.calls["save_memory"] if c.get("sub_type") == "departure"]
    assert [d["character_id"] for d in dep] == [CHB], f"离开记忆没只补给命中的角色: {dep}"
    assert "离开了" in dep[0]["content"]
    # 向量层按角色删被调用（Chroma 侧不残留该角色向量）
    assert lc_env.calls["vector_char"] == [CHA]


def test_delete_character_clears_shared_events_of_dead_character(lc_env):
    """格 ①c（D-1 已修）：删角色清掉该角色的 shared_events；同账号他角色 / 他账号的行原样。

    修法：``CHARACTER_DELETE_SPECS`` 增 ``(SharedEvent, "character_id")``
    （character_cascade.py，判据见该模块 docstring）。语义判断：一行恒为「某用户 × 某角色」的
    成对经历（两列均 NOT NULL、无失效标记列 ⇒ 无法「只清角色那一侧」），且**所有**读路径都要
    user_id + character_id 同时命中（memory/shared_events.py:87-92），角色没了这行永久读不到
    —— 属死引用而非用户侧数据。用户侧数据另有归属：账号级用户事实在 user_facts（无角色列）。
    """
    async def _seed():
        async with lc_env.factory() as db:
            db.add(SharedEvent(id=9401, user_id=ROOT_UID, character_id=CHA, title="第一次见面",
                               is_anniversary=True))
            db.add(SharedEvent(id=9402, user_id=ROOT_UID, character_id=CHB, title="小乙的事件"))
            db.add(SharedEvent(id=9403, user_id=ACTOR_UID, character_id=NEIGH_CID,
                               title="邻居的事件", is_anniversary=True))
            await db.commit()

    asyncio.run(_seed())
    _act_delete_character(lc_env.factory)

    assert _rows(lc_env.dst, "shared_events", character_id=CHA) == [], \
        "删角色仍留引用死角色的共同经历（纪念日每日扫描空转）"
    assert _n(lc_env.dst, "shared_events", is_anniversary=1, character_id=CHA) == 0
    # 同账号另一角色 / 另一账号的共享事件必须原样（谓词只按被删角色取行）
    assert _n(lc_env.dst, "shared_events", id=9402) == 1, "误删同账号另一角色的共同经历"
    assert _n(lc_env.dst, "shared_events", id=9403) == 1, "误删其他账号的共同经历"
    assert _n(lc_env.dst, "shared_events") == 2
    # 结构层面没有任何保护（这就是「外键自检查不出」的证据，只能靠级联清单）
    assert _fk_orphans(lc_env.dst) == []
    assert "character_id" in _table_cols(lc_env.dst, "shared_events")


# ══════════════════════════ 格 ①d：删角色 × 对局记录 ══════════════════════════

def test_delete_character_keeps_live_games_and_sweeps_emptied_session(lc_env):
    """格 ①d（通过）：仍有人在场的局整局保留；空局清事件与游戏记忆，局行留档。"""
    async def _seed():
        async with lc_env.factory() as db:
            db.add(GameSession(id=61, user_id=ROOT_UID, game_type="undercover", player_mode="multi"))
            db.add(GameSession(id=62, user_id=ROOT_UID, game_type="undercover", player_mode="single"))
            db.add(GameSession(id=63, user_id=ACTOR_UID, game_type="werewolf", player_mode="single"))
            await db.flush()
            db.add_all([
                GamePlayer(session_id=61, player_type="ai", character_id=CHA),
                GamePlayer(session_id=61, player_type="ai", character_id=CHB),
                GamePlayer(session_id=62, player_type="ai", character_id=CHA),
                GamePlayer(session_id=63, player_type="ai", character_id=NEIGH_CID),
            ])
            await db.flush()
            db.add_all([
                GameEvent(session_id=61, event_type="deal"), GameEvent(session_id=61, event_type="vote"),
                GameEvent(session_id=62, event_type="deal"), GameEvent(session_id=63, event_type="deal"),
            ])
            await db.flush()
            db.add_all([
                GameMemory(session_id=61, character_id=CHA, summary="甲的视角"),
                GameMemory(session_id=61, character_id=CHB, summary="乙的视角"),
                GameMemory(session_id=62, character_id=CHA, summary="甲的孤局"),
                GameMemory(session_id=63, character_id=NEIGH_CID, summary="邻居的局"),
            ])
            await db.commit()

    asyncio.run(_seed())
    _act_delete_character(lc_env.factory)

    # 玩家身份按角色清掉
    assert _n(lc_env.dst, "game_players", character_id=CHA) == 0
    # 61 还有小乙在场 → 局与事件、乙的视角都保留；甲的私有摘要随行消失（FK CASCADE）
    assert _n(lc_env.dst, "game_sessions", id=61) == 1
    assert _n(lc_env.dst, "game_events", session_id=61) == 2
    assert {r["character_id"] for r in _rows(lc_env.dst, "game_memories", session_id=61)} == {CHB}
    # 62 变成空局：事件/记忆收摊，局行按设计留档（无角色列，不构成孤儿）
    assert _n(lc_env.dst, "game_events", session_id=62) == 0
    assert _n(lc_env.dst, "game_memories", session_id=62) == 0
    assert _n(lc_env.dst, "game_sessions", id=62) == 1, "空局整局被删（与「历史对局可追溯」设计不符）"
    assert _n(lc_env.dst, "game_players", session_id=62) == 0
    # 邻居账号的对局完全不动
    assert _n(lc_env.dst, "game_sessions", id=63) == 1
    assert _n(lc_env.dst, "game_events", session_id=63) == 1
    assert _n(lc_env.dst, "game_memories", session_id=63) == 1
    assert _fk_orphans(lc_env.dst) == []


def test_delete_character_abandons_ai_pet_and_stops_care_candidates(lc_env):
    """附加发现 D-2（已修）：删角色按既有遗弃语义软删该角色的 AI 宠物，采集端不再产出死角色候选。

    修法两层：主修＝级联里遗弃 AI 宠物（character_cascade.py：置 abandoned_at、保留行，
    复用 pet_service.abandon_pet 的软删语义，不新造状态）；兜底＝collect_ai_care_events 的
    owner 存在性探针（pet_care.py）。本用例走真实删角色路径，验证两层协同：
    行不丢（活动/外键不悬空）、候选归零、外键自检干净。
    """
    async def _seed():
        async with lc_env.factory() as db:
            db.add(Pet(id=81, user_id=ROOT_UID, name="球球", species="cat",
                       owner_type="ai", owner_id=CHA, hunger=5, cleanliness=5, mood=5, energy=5))
            await db.commit()

    asyncio.run(_seed())

    async def _collect():
        return await pet_care.collect_ai_care_events()

    # 基线：角色存活时确实产出候选（否则「归零」无意义）
    before = [c for c in asyncio.run(_collect()) if c["candidate"]["character_id"] == CHA]
    assert len(before) == 1, f"基线就没产出候选（用例失效）: {before}"

    _act_delete_character(lc_env.factory)

    orphan = _rows(lc_env.dst, "pets", id=81)
    assert len(orphan) == 1, "AI 宠物行被物理删除（应为软删：置 abandoned_at 保留行）"
    assert orphan[0]["abandoned_at"], f"级联未遗弃该角色的 AI 宠物: {orphan[0]}"
    assert orphan[0]["owner_type"] == "ai" and orphan[0]["owner_id"] == CHA
    assert _n(lc_env.dst, "ai_characters", id=CHA) == 0
    assert _fk_orphans(lc_env.dst) == [], "外键自检发现不了（owner_id 无 FK）"

    fired = [c for c in asyncio.run(_collect()) if c["candidate"]["character_id"] == CHA]
    assert fired == [], f"删角色后采集端仍产出死角色候选: {fired}"


# ══════════════════════════ 格 ②a/②b/②c/②d：删号 ══════════════════════════

def _act_mark_deleted(factory, target=ROOT_UID, actor=ACTOR_UID, body=None):
    async def _run():
        async with factory() as db:
            return await account_deletion.mark_deleted(
                db, actor_user_id=actor, target_user_id=target,
                body=body if body is not None else {"confirm_username": "root"})
    return asyncio.run(_run())


def _act_restore(factory, target=ROOT_UID, actor=ACTOR_UID):
    async def _run():
        async with factory() as db:
            return await account_deletion.restore(
                db, actor_user_id=actor, target_user_id=target)
    return asyncio.run(_run())


def _act_purge(factory, target=ROOT_UID, actor=ACTOR_UID, *, force=True):
    async def _run():
        async with factory() as db:
            return await account_purge.purge_account(
                db, actor_user_id=actor, target_user_id=target,
                body={"confirm_username": "root", "force": force})
    return asyncio.run(_run())


def test_mark_deleted_enters_recycle_bin_and_restore_leaves_it(acct_env):
    """格 ②（前置，通过）：标记进回收站写三字段 + 宽限期 7 天；重复标记拒绝；恢复清空。"""
    rep = _act_mark_deleted(acct_env.factory)
    assert rep["mode"] == "delete_family_root", rep
    assert rep["purge_now"] is False
    row = _rows(acct_env.dst, "users", id=ROOT_UID)[0]
    assert row["deleted_at"] and row["disabled_at"], f"回收站/门禁字段缺失: {row}"
    delta = (datetime.fromisoformat(row["purge_after"])
             - datetime.fromisoformat(row["deleted_at"])).days
    assert delta == account_deletion.GRACE_DAYS == 7, delta

    with pytest.raises(HTTPException) as ei:
        _act_mark_deleted(acct_env.factory)
    assert ei.value.status_code == 400, "重复标记会悄悄把宽限期再推 7 天"

    # confirm_username 必须逐字符相等（防手滑）：拿另一账号试一次错拼
    async def _bad_confirm():
        async with acct_env.factory() as db:
            await account_deletion.mark_deleted(
                db, actor_user_id=ROOT_UID, target_user_id=ACTOR_UID,
                body={"confirm_username": "rooot"})
    with pytest.raises(HTTPException) as ei2:
        asyncio.run(_bad_confirm())
    assert ei2.value.status_code == 400
    assert _rows(acct_env.dst, "users", id=ACTOR_UID)[0]["deleted_at"] is None

    rpr = _act_restore(acct_env.factory)
    assert rpr["restored"] is True
    back = _rows(acct_env.dst, "users", id=ROOT_UID)[0]
    assert not back["deleted_at"] and not back["disabled_at"] and not back["purge_after"], back


def test_purge_aborts_on_backup_failure_and_deletes_nothing(acct_env, monkeypatch):
    """格 ②（前置，通过）：清除器 fail-closed —— 备份拿不到就中止，一行都没删。"""
    _act_mark_deleted(acct_env.factory, body={"confirm_username": "root", "purge_now": True})

    async def _seed():
        async with acct_env.factory() as db:
            db.add(Memory(id=9501, user_id=ROOT_UID, character_id=CHA,
                          memory_type="event", content="m", importance=50))
            await db.commit()

    asyncio.run(_seed())

    def _boom():
        raise RuntimeError("备份炸了")

    monkeypatch.setattr(account_purge, "_run_backup", _boom)
    with pytest.raises(Exception) as ei:
        _act_purge(acct_env.factory)
    assert "备份炸了" in str(ei.value)
    # 零删除
    assert _n(acct_env.dst, "users", id=ROOT_UID) == 1
    assert _n(acct_env.dst, "ai_characters", id=CHA) == 1
    assert _n(acct_env.dst, "memories", id=9501) == 1
    # 也没碰向量层
    assert acct_env.calls["vector_user"] == []
    job = _rows(acct_env.dst, "account_purge_jobs", user_id=ROOT_UID)
    assert job and job[0]["status"] == "failed", f"账本没记下失败: {job}"


def test_purge_removes_account_every_surface_and_survives_rerun(acct_env):
    """格 ②a+②b+②c+②d（通过）：宽限到期物理清除把四张关联面一并带走，邻居零污染，重跑幂等。"""
    async def _seed():
        async with acct_env.factory() as db:
            db.add(ChannelBinding(channel=CHANNEL, tenant_id=ROOT_UID, owner_user_id=ROOT_UID,
                                  bot_account_id="default", character_id=CHA, enabled=True))
            db.add(GlobalUserFact(user_id=ROOT_UID, slot="job", value="程序员"))
            db.add(SharedEvent(id=9601, user_id=ROOT_UID, character_id=CHA, title="第一次见面"))
            db.add(Memory(id=9611, user_id=ROOT_UID, character_id=CHA, memory_type="event",
                          content="m", importance=50, group_id=None))
            db.add(Memory(id=9612, user_id=ACTOR_UID, character_id=NEIGH_CID,
                          memory_type="event", content="邻居的记忆", importance=50))
            db.add(Pet(id=86, user_id=ROOT_UID, name="咪咪", species="cat"))
            db.add(GameSession(id=66, user_id=ROOT_UID, game_type="twenty_q", player_mode="single"))
            await db.commit()

    asyncio.run(_seed())
    _seed_pis(acct_env.factory, [(9621, CHA, "pending", "promise")])
    assert _n(acct_env.dst, "channel_bindings", character_id=CHA) == 1

    _act_mark_deleted(acct_env.factory, body={"confirm_username": "root", "purge_now": True})
    rep = _act_purge(acct_env.factory)

    assert rep["status"] == "done", rep
    assert rep["backup_zip"].endswith(".zip")
    assert acct_env.calls["backup"] == 1
    # 四张关联面全部清零
    for table, where in (
        ("users", {"id": ROOT_UID}),
        ("ai_characters", {"user_id": ROOT_UID}),
        ("chat_sessions", {"user_id": ROOT_UID}),
        ("memories", {"user_id": ROOT_UID}),
        ("prospective_intents", {"user_id": ROOT_UID}),          # a 面
        ("channel_bindings", {"tenant_id": ROOT_UID}),           # b 面
        ("user_facts", {"user_id": ROOT_UID}),                   # c 面（账号级用户事实）
        ("shared_events", {"user_id": ROOT_UID}),                # c 面（跨角色共享事件）
        ("game_sessions", {"user_id": ROOT_UID}),                # d 面（对局）
        ("pets", {"user_id": ROOT_UID}),
    ):
        assert _n(acct_env.dst, table, **where) == 0, f"{table} 残留: {where}"
    # 向量层收到过被删记忆 id（先向量后行）
    flat = [m for _u, ids in acct_env.calls["vector_user"] for m in ids]
    assert 9611 in flat, f"向量层没按固化 memory id 清理: {acct_env.calls['vector_user']}"
    # 邻居账号一切照旧
    for table, where in (
        ("users", {"id": ACTOR_UID}), ("ai_characters", {"id": NEIGH_CID}),
        ("memories", {"id": 9612}), ("chat_sessions", {"id": SESS_N}),
    ):
        assert _n(acct_env.dst, table, **where) == 1, f"{table} 被误删"
    assert _fk_orphans(acct_env.dst) == []

    again = _act_purge(acct_env.factory)
    assert again["already_done"] is True and again["status"] == "done"
    assert acct_env.calls["backup"] == 1, "幂等重跑又备份了一次"


def test_recycle_bin_account_cancels_prospective_intent_on_mark_deleted(acct_env):
    """格 ②a（D-3 已修）：标记删除即取消该账号 pending/matched PIS，宽限期内不再白烧 LLM。

    修法：``mark_deleted`` 在标记三字段之后、同一事务内，按 ``user_id == 目标账号`` 把
    pending/matched 置 cancelled（与删角色级联同语义、同谓词；character_cascade.py:204-211）。
    证据：① 本账号承诺行→cancelled（不删行，留痕）；② 采集端不再命中、认领端返回 False；
    ③ 邻居账号的 pending PIS 分毫不动（归属键只用 user_id，绝不用 get_family_member_ids）。
    """
    _seed_pis(acct_env.factory, [
        (9701, CHA, "pending", "promise"),
        (9702, CHA, "matched", "promise"),
    ])

    async def _seed_neighbour():
        async with acct_env.factory() as db:
            db.add(ProspectiveIntent(id=9711, user_id=ACTOR_UID, character_id=NEIGH_CID,
                                     content="邻居的承诺", kind="promise", status="pending",
                                     chat_session_id=SESS_N))
            await db.commit()

    asyncio.run(_seed_neighbour())
    _act_mark_deleted(acct_env.factory)

    mine = {r["id"]: r["status"] for r in _rows(acct_env.dst, "prospective_intents", user_id=ROOT_UID)}
    assert mine == {9701: "cancelled", 9702: "cancelled"}, f"标记删除未取消 PIS: {mine}"
    assert _n(acct_env.dst, "prospective_intents", user_id=ROOT_UID) == 2, "PIS 行被删（应置终态留痕）"
    # 另一账号的 pending 不受牵连（归属键=user_id，不误伤其它账号）
    assert _rows(acct_env.dst, "prospective_intents", id=9711)[0]["status"] == "pending"

    async def _collect():
        return await collect_due_promises()

    due = asyncio.run(_collect())
    assert [c for c in due if c["pis_id"] in (9701, 9702)] == [], f"采集端仍命中已取消承诺: {due}"

    async def _claim():
        return await claim_intent_for_fire(9701)

    assert asyncio.run(_claim()) is False, "取消后仍可认领＝仍会白烧 LLM"


def test_recycle_bin_account_binding_disabled_on_mark_deleted_and_restored(acct_env):
    """格 ②b（D-4 已修）：标记删除即停用该账号名下角色的绑定，restore 复通；他租户不受影响。

    修法：``mark_deleted`` / ``restore`` 各加一条与 ``disabled_at`` 同事务的 UPDATE
    （account_deletion.py）。归属键＝**角色归属**（character_id ∈ ai_characters.user_id==目标），
    不是 tenant_id——子账号的绑定行 tenant_id 是家庭根，按租户停用会误伤父号/兄弟账号。
    复用既有 ``enabled`` 列（解绑才是删行），保留行留痕。
    与 D-3 刻意不同口径：PIS 的 cancelled 是终态、restore 不复活；渠道绑定是**可恢复**的。
    """
    async def _seed():
        async with acct_env.factory() as db:
            await upsert_binding(db, ROOT_UID, CHANNEL, CHA, bot_label="小甲的号")
            # 另一租户的绑定（直接建行，绕开 family_single/物理单实例裁决）：必须分毫不动
            db.add(ChannelBinding(channel=CHANNEL, tenant_id=ACTOR_UID, owner_user_id=ACTOR_UID,
                                  bot_account_id="default", character_id=NEIGH_CID, enabled=True))
            await db.commit()

    asyncio.run(_seed())

    async def _resolve(tenant=ROOT_UID):
        async with acct_env.factory() as db:
            return await resolve_character(db, CHANNEL, tenant)

    assert asyncio.run(_resolve()) is not None, "基线就没绑定（用例失效）"

    _act_mark_deleted(acct_env.factory)

    rows = _rows(acct_env.dst, "channel_bindings", character_id=CHA)
    assert len(rows) == 1, "宽限期是**停用**不是删行（行丢了就无从留痕/恢复）"
    assert rows[0]["enabled"] == 0, f"标记删除未停用绑定: {rows[0]}"
    assert asyncio.run(_resolve()) is None, "入站消息仍路由到待注销账号"
    # 另一租户的绑定不受牵连（归属键=角色归属，不是 tenant_id）
    assert _rows(acct_env.dst, "channel_bindings", character_id=NEIGH_CID)[0]["enabled"] == 1
    assert asyncio.run(_resolve(ACTOR_UID)) is not None

    rpr = _act_restore(acct_env.factory)
    assert rpr["restored"] is True
    assert _rows(acct_env.dst, "channel_bindings", character_id=CHA)[0]["enabled"] == 1, \
        "出回收站未复通渠道（用户以为恢复了，外部却静默失联）"
    assert asyncio.run(_resolve()) is not None


# ══════════════════════════ 格 ③：删群 ══════════════════════════

def _seed_group(factory, *, gid=51, mem_ids=(9801,)):
    async def _run():
        async with factory() as db:
            db.add(ChatGroup(id=gid, user_id=ROOT_UID, name="家庭群"))
            await db.flush()
            db.add_all([
                ChatGroupMember(group_id=gid, character_id=CHA),
                ChatGroupMember(group_id=gid, character_id=CHB),
            ])
            await db.flush()
            db.add_all([
                ChatGroupMessage(group_id=gid, content="大家好", sender_type="user", character_id=None),
                ChatGroupMessage(group_id=gid, content="在的", sender_type="ai", character_id=CHA),
                ChatGroupMessage(group_id=gid, content="我来啦", sender_type="ai", character_id=CHB),
            ])
            db.add_all([
                GroupMemory(group_id=gid, user_id=ROOT_UID, speaker_type="system",
                            content="一轮群聊聚合事件"),
                GroupMemory(group_id=gid, user_id=ROOT_UID, speaker_type="ai",
                            speaker_id=CHA, content="小甲：我说过一句话"),
            ])
            # 群内沉淀到个人记忆的行（memories.group_id 无外键，仅做节流标记）
            for mid in mem_ids:
                db.add(Memory(id=mid, user_id=ROOT_UID, character_id=CHA, memory_type="event",
                              content="群里聊到的事", importance=50, group_id=gid))
            # 群对局：一局在小乙还在场，一局只有小甲
            db.add(GameSession(id=71, user_id=ROOT_UID, group_id=gid,
                               game_type="undercover", player_mode="multi"))
            db.add(GameSession(id=72, user_id=ROOT_UID, group_id=gid,
                               game_type="turtle_soup", player_mode="single"))
            await db.flush()
            db.add_all([
                GamePlayer(session_id=71, player_type="ai", character_id=CHA),
                GamePlayer(session_id=71, player_type="ai", character_id=CHB),
                GamePlayer(session_id=72, player_type="ai", character_id=CHA),
            ])
            await db.commit()

    asyncio.run(_run())


def _act_delete_group(factory, gid=51):
    async def _run():
        async with factory() as db:
            return await delete_group(db, gid, ROOT_UID, "zh")

    return asyncio.run(_run())


def test_delete_group_detaches_game_sessions_and_keeps_them_as_history(lc_env):
    """格 ③a+③b+③d（通过）：成员/消息/群记忆随群消失，群对局置空 group_id 后保留为历史。"""
    _seed_group(lc_env.factory)
    assert _act_delete_group(lc_env.factory)["status"] == "ok"

    assert _n(lc_env.dst, "chat_groups", id=51) == 0
    assert _n(lc_env.dst, "chat_group_members", group_id=51) == 0
    assert _n(lc_env.dst, "chat_group_messages", group_id=51) == 0
    assert _n(lc_env.dst, "group_memories", group_id=51) == 0
    # 对局：局行保留（历史可追溯），只脱离群
    for gid in (71, 72):
        rows = _rows(lc_env.dst, "game_sessions", id=gid)
        assert len(rows) == 1, f"对局被连带删除: {gid}"
        assert rows[0]["group_id"] is None, f"群没了但局还挂着 group_id: {rows[0]}"
    assert _n(lc_env.dst, "game_players", session_id=71) == 2
    # a/b 两面在该路径上不存在关联面（列都不存在，见 test_not_applicable_cells_*）
    assert _n(lc_env.dst, "prospective_intents") == 0
    assert _n(lc_env.dst, "channel_bindings") == 0
    assert _fk_orphans(lc_env.dst) == []


def test_delete_group_detaches_personal_memories_and_keeps_them(lc_env):
    """格 ③c（D-5 已修）：删群把群内沉淀的个人记忆 group_id 置 NULL，记忆本体一行不删。

    修法：``delete_group`` 第 3 步（chat_groups.py）── 与同函数第 2 步对 GameSession 的处理
    同范式。语义：``memories.group_id`` 是**无外键**的节流标记列，NULL 正是它文档化的
    「非群聊」取值（models/memory/__init__.py:30）；记忆属于角色/用户，群只是标签 ⇒ 不删行。
    误伤面：另一群的指针、本就无群的记忆都必须原样。
    """
    _seed_group(lc_env.factory, mem_ids=(9801, 9802))

    async def _seed_neighbours():
        async with lc_env.factory() as db:
            db.add(ChatGroup(id=52, user_id=ROOT_UID, name="另一个群"))
            await db.flush()
            db.add(Memory(id=9803, user_id=ROOT_UID, character_id=CHA, memory_type="event",
                          content="另一个群里聊到的事", importance=50, group_id=52))
            db.add(Memory(id=9804, user_id=ROOT_UID, character_id=CHA, memory_type="event",
                          content="私聊里聊到的事", importance=50, group_id=None))
            await db.commit()

    asyncio.run(_seed_neighbours())
    assert _n(lc_env.dst, "memories", group_id=51) == 2
    _act_delete_group(lc_env.factory)

    kept = _rows(lc_env.dst, "memories", id=9801) + _rows(lc_env.dst, "memories", id=9802)
    assert len(kept) == 2, "群内沉淀的个人记忆被连带删除（记忆属于角色/用户，群只是标签）"
    assert all(r["group_id"] is None for r in kept), f"悬空 group_id 未清理: {kept}"
    assert _n(lc_env.dst, "memories", group_id=51) == 0
    assert {r["character_id"] for r in kept} == {CHA}, "置空指针时改动了记忆归属"
    # 不误伤：另一群的指针、本就无群的记忆原样
    assert _rows(lc_env.dst, "memories", id=9803)[0]["group_id"] == 52
    assert _rows(lc_env.dst, "memories", id=9804)[0]["group_id"] is None
    assert _fk_orphans(lc_env.dst) == [], "memories.group_id 无外键 → 这类悬空只能靠级联清单"


def test_delete_group_keeps_other_group_and_other_account_intact(lc_env):
    """格 ③ 反向保护（通过）：只删目标群，邻居账号与另一群不受影响。"""
    _seed_group(lc_env.factory)

    async def _seed_other():
        async with lc_env.factory() as db:
            db.add(ChatGroup(id=52, user_id=ROOT_UID, name="另一个群"))
            db.add(ChatGroup(id=53, user_id=ACTOR_UID, name="邻居群"))
            await db.flush()
            db.add_all([
                GroupMemory(group_id=52, user_id=ROOT_UID, speaker_type="system", content="另一群的事件"),
                GroupMemory(group_id=53, user_id=ACTOR_UID, speaker_type="system", content="邻居群的事件"),
                ChatGroupMessage(group_id=53, content="邻居发言", sender_type="user", character_id=None),
            ])
            await db.commit()

    asyncio.run(_seed_other())
    _act_delete_group(lc_env.factory)

    assert _n(lc_env.dst, "chat_groups", id=52) == 1
    assert _n(lc_env.dst, "group_memories", group_id=52) == 1
    assert _n(lc_env.dst, "chat_groups", id=53) == 1
    assert _n(lc_env.dst, "group_memories", group_id=53) == 1
    assert _n(lc_env.dst, "chat_group_messages", group_id=53) == 1


# ══════════════════════════ 格 ④：遗弃宠物 ══════════════════════════

def _seed_pets(factory, rows):
    """rows: [(id, abandoned, hunger)] —— 低值才会进提醒候选。"""
    async def _run():
        async with factory() as db:
            for pid, abandoned, hunger in rows:
                db.add(Pet(id=pid, user_id=ROOT_UID, name=f"宠物{pid}", species="cat",
                           hunger=hunger, cleanliness=hunger, mood=hunger, energy=hunger,
                           abandoned_at=(datetime(2026, 9, 1) if abandoned else None)))
            await db.commit()

    asyncio.run(_run())


def test_abandon_pet_is_soft_delete_and_stops_proactive_candidates(lc_env):
    """格 ④a+④c（通过）：软删保留行与活动，提醒采集立刻不再产出该宠候选；重复遗弃幂等。"""
    _seed_pets(lc_env.factory, [(91, False, 5), (92, True, 5)])

    async def _collect_before():
        return await pet_care.collect_pet_events()

    before = asyncio.run(_collect_before())
    assert [c["candidate"]["pet_id"] for c in before] == [91], f"基线就不对: {before}"

    async def _abandon():
        return await pet_service.abandon_pet(91, ROOT_UID)

    assert asyncio.run(_abandon()) is True
    rows = _rows(lc_env.dst, "pets", id=91)
    assert len(rows) == 1 and rows[0]["abandoned_at"], "不是软删（行丢了会导致活动/记忆外键悬空）"
    assert _n(lc_env.dst, "pet_activities", pet_id=91, action="abandon") == 1
    # 采集端过滤（pet_care.py:124 三处采集入口同口径）
    after = asyncio.run(_collect_before())
    assert [c["candidate"]["pet_id"] for c in after] == [], f"已遗弃的宠物仍在产生提醒候选: {after}"
    # 幂等
    assert asyncio.run(_abandon()) is False
    assert _n(lc_env.dst, "pet_activities", pet_id=91, action="abandon") == 1, "重复落了一条遗弃活动"
    # 共享记忆面：遗弃不牵连任何记忆/群/对局行，外键自检干净
    assert _n(lc_env.dst, "group_memories") == 0
    assert _n(lc_env.dst, "game_sessions") == 0
    assert _fk_orphans(lc_env.dst) == []


def test_not_applicable_cells_have_no_schema_surface(lc_env):
    """格 ③a / ③b / ④b / ④d ＝ 不适用：按磁盘 schema 给出「该路径不存在此关联面」的证据。"""
    pis_cols = _table_cols(lc_env.dst, "prospective_intents")
    bind_cols = _table_cols(lc_env.dst, "channel_bindings")
    pet_cols = _table_cols(lc_env.dst, "pets")
    mem_cols = _table_cols(lc_env.dst, "memories")

    # ③a 删群 × PIS：PIS 没有群维度列（只有 character_id / chat_session_id）
    assert not ({"group_id", "chat_group_id"} & pis_cols), pis_cols
    # ③b 删群 × 渠道绑定：绑定按 (channel,tenant,bot)→character，无群维度
    assert not ({"group_id", "chat_group_id"} & bind_cols), bind_cols
    # ④b 遗弃宠物 × 渠道绑定 / ④d 遗弃宠物 × 对局：pets 不持有任何绑定或对局外键
    assert not ({"channel", "tenant_id", "group_id", "session_id", "game_id"} & pet_cols), pet_cols
    # PIS 也不看宠物（④a 走的是 pet_care 采集闸，已在上一个用例里验过）
    assert not ({"pet_id"} & pis_cols) and not ({"pet_id"} & mem_cols)
    # 绑定唯一入口对宠物零引用（代码面佐证：模块内不 import Pet / pet_service）
    src = (Path(__file__).resolve().parents[1] / "app" / "application"
           / "channel_binding_service.py").read_text(encoding="utf-8")
    assert "Pet" not in src and "pet_service" not in src
