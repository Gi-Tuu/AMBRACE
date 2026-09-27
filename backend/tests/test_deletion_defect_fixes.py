# -*- coding: utf-8 -*-
"""删除链路缺陷修复的行为断言（2026-09-27 派单：D-3 + D-2，同批追加 D-1 / D-4 / D-5）。

与 test_deletion_lifecycle_e2e.py 里被改成正向断言的 DEFECT 用例互补，本文件只放
**修复单新增**的行为断言（选择「新建独立文件」而非写进 e2e 文件，见交付说明）：

- D-3（mark_deleted 取消 PIS）：
  ① 归属键＝user_id，删号只动本账号、绝不误伤同一家庭里的其它账号（子账号删除不动父账号）；
  ② 取消即终态——restore 从回收站恢复不把已 cancelled 的承诺复活。
- D-2（删角色清 AI 宠物）：
  ① 主修——级联只遗弃**本角色**的 AI 宠物（软删置 abandoned_at、保留行），不牵连同账号他角色；
  ② 兜底——collect_ai_care_events 的 owner 存在性探针**独立于级联**：老数据 / 直接改库留下的
     「abandoned_at 为空但 owner 角色已不存在」的候选也一律短路，且不误杀存活的 AI 宠物。
- D-1（删角色清 shared_events）：级联入口单跑同样按角色清行并计入 stats（e2e 走的是全链路）。
- D-4（宽限期停用渠道绑定）：
  ① 归属键＝**角色归属**而非 tenant_id——删子账号只停它的角色，父号名下另一角色的绑定原样；
  ② 与 D-3 **口径相反**：restore 复通绑定，但**不**复活已 cancelled 的 PIS（两种语义同框钉住）。
- D-5（删群置空 memories.group_id）：跨账号 / 跨群 / 无群三类指针一律不误伤。

库与桩面复用 e2e 文件的种子工具（_dbclone 模板克隆 + tmp_path + _patch_session_factories +
_seed_base）：每用例私有 SQLite，异步 session 工厂打桩到临时引擎，**绝不碰 backend/data 生产库**。
本文件自建 fix_env fixture（只 import 纯函数工具，不跨模块导入 fixture，以免形参名遮蔽导入名）。
"""
import asyncio
from types import SimpleNamespace

import pytest

from _dbclone import clone_engine, make_session_factory
from test_account_deletion import _hash, _patch_session_factories
from test_deletion_lifecycle_e2e import (
    ACTOR_UID,
    CHA,
    CHB,
    NEIGH_CID,
    PW,
    ROOT_UID,
    _act_delete_group,
    _act_mark_deleted,
    _act_restore,
    _n,
    _rows,
    _seed_base,
    _seed_group,
    _seed_pis,
)

from app.application.character_cascade import cascade_delete_character
from app.models.channel import ChannelBinding
from app.models.chat import ChatGroup, ChatSession
from app.models.character import AICharacter
from app.models.memory import Memory, ProspectiveIntent, SharedEvent
from app.models.pet import Pet
from app.models.user import User
from app.scheduling import pet_care

pytestmark = pytest.mark.slow

SUB_UID = 3     # ACTOR 名下的子账号（叶子：可被标记删除）
SUB_CID = 31    # 子账号名下角色


@pytest.fixture()
def fix_env(tmp_path, monkeypatch):
    """本文件自建的临时库底座（复用 e2e 的 _seed_base，不跨模块导入 fixture 以免遮蔽形参名）。

    覆盖：ROOT 两角色 + 邻居一角色 + 各自会话；异步 session 工厂打桩到临时引擎，
    删角色级联 / collect_* 采集 / mark_deleted / restore 都走这套库。
    """
    dst = tmp_path / "fix.db"
    engine = clone_engine(dst)
    factory = make_session_factory(engine)
    _patch_session_factories(monkeypatch, factory)
    _seed_base(factory)
    yield SimpleNamespace(dst=dst, factory=factory)
    asyncio.run(engine.dispose())


# ══════════════════════════ D-3：mark_deleted 取消 PIS ══════════════════════════

def test_mark_deleted_cancels_only_target_account_pis_in_family(fix_env):
    """D-3 归属键：删子账号只取消它自己的 PIS，父账号/别的账号分毫不动。

    刻意构造家庭关系（SUB.parent_id = ACTOR）：若误用 get_family_member_ids 取「成员集」去取消，
    会把父账号 ACTOR 的 pending 一起抹成 cancelled（误伤）。修法只按 user_id==目标，父号必须原样。
    """
    _seed_pis(fix_env.factory, [(9701, CHA, "pending", "promise")])  # ROOT 的，第三方账号

    async def _seed():
        async with fix_env.factory() as db:
            db.add(User(id=SUB_UID, username="sub", nickname="子号", is_admin=False,
                        parent_id=ACTOR_UID, password_hash=_hash(PW)))
            await db.flush()
            db.add(AICharacter(id=SUB_CID, user_id=SUB_UID, name="子号角色", is_active=True))
            db.add(ChatSession(id=131, user_id=SUB_UID, character_id=SUB_CID))
            await db.flush()
            db.add(ProspectiveIntent(id=9721, user_id=SUB_UID, character_id=SUB_CID,
                                     content="子号的承诺", kind="promise", status="pending",
                                     chat_session_id=131))
            db.add(ProspectiveIntent(id=9722, user_id=ACTOR_UID, character_id=NEIGH_CID,
                                     content="父号的承诺", kind="promise", status="pending"))
            await db.commit()

    asyncio.run(_seed())
    _act_mark_deleted(fix_env.factory, target=SUB_UID, actor=ROOT_UID,
                      body={"confirm_username": "sub"})

    assert _rows(fix_env.dst, "prospective_intents", id=9721)[0]["status"] == "cancelled", "子账号自己的 PIS 未取消"
    assert _rows(fix_env.dst, "prospective_intents", id=9722)[0]["status"] == "pending", "误伤父账号（家庭成员）的 PIS"
    assert _rows(fix_env.dst, "prospective_intents", id=9701)[0]["status"] == "pending", "误伤无关账号的 PIS"
    # 子账号确已进回收站
    assert _rows(fix_env.dst, "users", id=SUB_UID)[0]["deleted_at"]


def test_restore_does_not_resurrect_cancelled_pis(fix_env):
    """D-3 取消即终态：恢复账号不把已 cancelled 的承诺复活（restore 只清 users 三字段）。"""
    _seed_pis(fix_env.factory, [(9731, CHA, "pending", "promise")])
    _act_mark_deleted(fix_env.factory)
    assert _rows(fix_env.dst, "prospective_intents", id=9731)[0]["status"] == "cancelled"

    rpr = _act_restore(fix_env.factory)
    assert rpr["restored"] is True
    assert _rows(fix_env.dst, "users", id=ROOT_UID)[0]["deleted_at"] is None, "账号未从回收站恢复"
    assert _rows(fix_env.dst, "prospective_intents", id=9731)[0]["status"] == "cancelled", \
        "恢复把 cancelled 承诺复活（无法安全区分原 pending/matched，应保持终态）"


# ══════════════════════════ D-2：删角色清 AI 宠物 ══════════════════════════

def test_cascade_abandons_target_ai_pet_only(fix_env):
    """D-2 主修：级联只遗弃本角色的 AI 宠物（置 abandoned_at、保留行），不动同账号他角色的宠物。"""
    async def _seed():
        async with fix_env.factory() as db:
            db.add(Pet(id=81, user_id=ROOT_UID, name="甲宠", species="cat",
                       owner_type="ai", owner_id=CHA, hunger=5, cleanliness=5, mood=5, energy=5))
            db.add(Pet(id=82, user_id=ROOT_UID, name="乙宠", species="dog",
                       owner_type="ai", owner_id=CHB, hunger=5, cleanliness=5, mood=5, energy=5))
            await db.commit()

    asyncio.run(_seed())

    async def _cascade():
        async with fix_env.factory() as db:
            await cascade_delete_character(db, CHA)
            await db.commit()

    asyncio.run(_cascade())

    a = _rows(fix_env.dst, "pets", id=81)[0]
    b = _rows(fix_env.dst, "pets", id=82)[0]
    assert a["abandoned_at"], "本角色的 AI 宠物未被遗弃（软删）"
    assert b["abandoned_at"] is None, "误伤了同账号另一角色的 AI 宠物"
    assert _n(fix_env.dst, "pets", id=81) == 1 and _n(fix_env.dst, "pets", id=82) == 1, \
        "遗弃应是软删（保留行），不得物理删除宠物行"


def test_collect_ai_care_skips_dead_owner_independent_of_cascade(fix_env):
    """D-2 兜底：owner 存在性探针独立生效——绕过级联直接留一条「abandoned_at 空、owner 已不存在」的
    AI 宠物，采集端也必须短路它；同时不得误杀 owner 仍存活的候选。"""
    async def _seed():
        async with fix_env.factory() as db:
            # owner=CHA（存活）→ 应产出；owner=9999（无此角色）→ 探针应跳过
            db.add(Pet(id=81, user_id=ROOT_UID, name="活宠", species="cat",
                       owner_type="ai", owner_id=CHA, hunger=5, cleanliness=5, mood=5, energy=5))
            db.add(Pet(id=90, user_id=ROOT_UID, name="死宠", species="cat",
                       owner_type="ai", owner_id=9999, hunger=5, cleanliness=5, mood=5, energy=5))
            await db.commit()

    asyncio.run(_seed())

    async def _collect():
        return await pet_care.collect_ai_care_events()

    owners = {c["candidate"]["character_id"] for c in asyncio.run(_collect())}
    assert CHA in owners, "存活的 AI 宠物被探针误过滤"
    assert 9999 not in owners, "owner 角色不存在的候选未被探针短路（兜底失效）"


# ══════════════════════════ D-1：删角色清 shared_events ══════════════════════════

def test_cascade_deletes_only_target_character_shared_events(fix_env):
    """D-1 级联入口：单跑 cascade（角色行仍在）同样按角色清行，且计数进 stats 可观测。

    e2e 那条走的是 delete_character 全链路；这里只打级联函数，确保「谁调级联都干净」，
    并确认谓词精确到被删角色——同账号另一角色、另一账号的成对经历各留 1 行。
    """
    async def _seed():
        async with fix_env.factory() as db:
            db.add(SharedEvent(id=9401, user_id=ROOT_UID, character_id=CHA, title="甲的共同经历",
                               is_anniversary=True))
            db.add(SharedEvent(id=9402, user_id=ROOT_UID, character_id=CHB, title="乙的共同经历"))
            db.add(SharedEvent(id=9403, user_id=ACTOR_UID, character_id=NEIGH_CID, title="邻居的"))
            await db.commit()

    asyncio.run(_seed())

    async def _cascade():
        async with fix_env.factory() as db:
            stats = await cascade_delete_character(db, CHA)
            await db.commit()
        return stats

    stats = asyncio.run(_cascade())
    assert stats.get("SharedEvent", 0) == 1, f"级联计数异常（未清 / 重复清）: {stats.get('SharedEvent')}"
    assert _n(fix_env.dst, "shared_events", character_id=CHA) == 0
    assert _n(fix_env.dst, "shared_events", id=9402) == 1, "误伤同账号另一角色的成对经历"
    assert _n(fix_env.dst, "shared_events", id=9403) == 1, "误伤其他账号的成对经历"


# ══════════════════════════ D-4：宽限期停用渠道绑定 ══════════════════════════

def test_mark_deleted_disables_bindings_by_character_owner_not_tenant(fix_env):
    """D-4 归属键：删子账号只停**它名下角色**的绑定；同租户里父号角色的绑定原样。

    这是唯一能分辨「character_id ∈ ai_characters.user_id==目标」与「tenant_id==目标」两种取法
    的构造：SUB 的绑定行 tenant_id 是父号 ACTOR，按租户停用会连带抹掉 e2e_sib（父号自己的角色）。
    """
    async def _seed():
        async with fix_env.factory() as db:
            db.add(User(id=SUB_UID, username="sub", nickname="子号", is_admin=False,
                        parent_id=ACTOR_UID, password_hash=_hash(PW)))
            await db.flush()
            db.add(AICharacter(id=SUB_CID, user_id=SUB_UID, name="子号角色", is_active=True))
            await db.flush()
            db.add_all([
                ChannelBinding(channel="e2e_sub", tenant_id=ACTOR_UID, owner_user_id=ACTOR_UID,
                               bot_account_id="default", character_id=SUB_CID, enabled=True),
                ChannelBinding(channel="e2e_sib", tenant_id=ACTOR_UID, owner_user_id=ACTOR_UID,
                               bot_account_id="default", character_id=NEIGH_CID, enabled=True),
                ChannelBinding(channel="e2e_root", tenant_id=ROOT_UID, owner_user_id=ROOT_UID,
                               bot_account_id="default", character_id=CHA, enabled=True),
            ])
            await db.commit()

    asyncio.run(_seed())
    _act_mark_deleted(fix_env.factory, target=SUB_UID, actor=ROOT_UID,
                      body={"confirm_username": "sub"})

    def _binding(channel):
        return _rows(fix_env.dst, "channel_bindings", channel=channel)[0]

    sub = _binding("e2e_sub")
    assert sub["enabled"] == 0, "子账号名下角色的绑定未停用（宽限期内仍会路由真人消息）"
    assert sub["character_id"] == SUB_CID, "宽限期是停用不是删行，绑定行须留痕"
    assert _binding("e2e_sib")["enabled"] == 1, "误伤同租户另一角色的绑定（归属键错用 tenant_id）"
    assert _binding("e2e_root")["enabled"] == 1, "误伤无关账号的绑定"


def test_restore_reenables_bindings_but_not_cancelled_pis(fix_env):
    """D-4 与 D-3 的口径分野（同框钉住）：restore 复通绑定，但**不**复活已 cancelled 的 PIS。"""
    _seed_pis(fix_env.factory, [(9741, CHA, "pending", "promise")])

    async def _seed():
        async with fix_env.factory() as db:
            db.add(ChannelBinding(channel="e2e_r", tenant_id=ROOT_UID, owner_user_id=ROOT_UID,
                                  bot_account_id="default", character_id=CHA, enabled=True))
            await db.commit()

    asyncio.run(_seed())
    _act_mark_deleted(fix_env.factory)
    assert _rows(fix_env.dst, "channel_bindings", channel="e2e_r")[0]["enabled"] == 0
    assert _rows(fix_env.dst, "prospective_intents", id=9741)[0]["status"] == "cancelled"

    _act_restore(fix_env.factory)
    assert _rows(fix_env.dst, "channel_bindings", channel="e2e_r")[0]["enabled"] == 1, \
        "绑定应随恢复复通（停用是标记删除代写的，不是用户自选的终态）"
    assert _rows(fix_env.dst, "prospective_intents", id=9741)[0]["status"] == "cancelled", \
        "PIS 的 cancelled 是状态机终态、恢复不得复活（与 D-4 相反，勿统一两条口径）"

    _act_restore(fix_env.factory)
    assert _rows(fix_env.dst, "channel_bindings", channel="e2e_r")[0]["enabled"] == 1, "重复恢复把绑定改坏了"


# ══════════════════════════ D-5：删群置空 memories.group_id ══════════════════════════

def test_delete_group_nulls_only_target_group_memory_pointer(fix_env):
    """D-5 不误伤：删目标群只置空该群那一组指针——邻居账号的群、本就无群的都是原样。"""
    _seed_group(fix_env.factory, mem_ids=(9801,))

    async def _seed():
        async with fix_env.factory() as db:
            db.add(ChatGroup(id=59, user_id=ACTOR_UID, name="邻居群"))
            await db.flush()
            db.add_all([
                Memory(id=9811, user_id=ACTOR_UID, character_id=NEIGH_CID, memory_type="event",
                       content="邻居群聊到的事", importance=50, group_id=59),
                Memory(id=9812, user_id=ROOT_UID, character_id=CHB, memory_type="event",
                       content="私聊的事", importance=50, group_id=None),
            ])
            await db.commit()

    asyncio.run(_seed())
    _act_delete_group(fix_env.factory, gid=51)

    assert _n(fix_env.dst, "memories", id=9801) == 1, "记忆本体被删（群只是标签，应只置空指针）"
    assert _rows(fix_env.dst, "memories", id=9801)[0]["group_id"] is None, "目标群指针未置空"
    assert _rows(fix_env.dst, "memories", id=9811)[0]["group_id"] == 59, "误清空邻居账号的群指针"
    assert _rows(fix_env.dst, "memories", id=9812)[0]["group_id"] is None
    assert _n(fix_env.dst, "chat_groups", id=59) == 1, "邻居群被连带删除"
