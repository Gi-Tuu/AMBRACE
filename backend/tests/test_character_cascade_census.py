# -*- coding: utf-8 -*-
"""角色级联覆盖普查回归（P3-3，2026-09-26 批 C）：让「漏表」变成 CI 红灯。

为什么必须量实际库结构：``CHARACTER_DELETE_SPECS`` 是**手写**清单，而「带 character_id 的表」
会随新功能不断长出（历史上正因漏表在只读实库留下 463 条孤儿）。若从 ORM 清单推集合 A，
量到的是「我们想到了哪些表」而不是「库里有哪些表」——那正好复现 bug 的成因。
故 A 一律走 ``sqlite_master`` + ``PRAGMA table_info``（含插件表，与
``app/application/user_cascade.py::_scan_schema`` 同一口径）。

两条判据（①是派单原始判据，②是防假绿的加强判据）：
① ``A − (SPECS ∪ 物理级联) ⊆ 例外清单``；
② ``A − SPECS ⊆ 例外清单 ∪ 仅靠物理级联清单``。
②为什么要有：40 张 SPECS 表里 39 张同时带 ``ON DELETE CASCADE`` 物理外键，只按①判的话
「注释掉一条 SPECS」照样绿（DB 兜底把差集抹平了），自证就失去意义——②让任何一条 SPECS
被摘掉都必须显式登记理由才可能通过。

例外清单每一项都要写理由；同时反向断言不许有僵尸条目（库里已无该表还挂在清单上），
否则清单会慢慢攒出「看起来被审查过」的死条目。

口径边界（写明以免误读）：本普查扫**主 schema**（``clone_engine`` 默认不加载插件表）。
原因不是省事——``CHARACTER_DELETE_SPECS`` 装的是主 ``Base`` 的模型类，插件未装载时那些表
根本不存在，主清单**永远不可能**覆盖插件表；把插件表扫进来只会产出一批「无法修」的红灯。
插件表的角色归属由插件自身生命周期负责（本批开发期临时把模板库换成 ``with_plugins=True``
量过一次：``wechat_ilink_bindings`` / ``wechat_ilink_messages`` 带 character_id 而无归属清理，
属真缺口；但级联模块不在本批改动清单内，已作为待排期遗留项上报）。
"""
import sqlite3

import pytest

from _dbclone import clone_engine

pytestmark = pytest.mark.slow

CHARACTER_ROOT_TABLE = "ai_characters"
FAMILY_COLUMN = "character_id"
# PRAGMA foreign_key_list 列序：id, seq, table, from, to, on_update, on_delete, match
_FK_TARGET, _FK_LOCAL_COL, _FK_ON_DELETE = 2, 3, 6

# ── 例外清单 A：带 character_id 但**不该**由 SPECS 直删（理由见各条） ──────────────
SPECS_EXCEPTIONS: dict[str, str] = {
    "agent_tasks": "无 character_id 外键；跨角色/全局任务运行态，删了丢任务留痕（级联模块 docstring 显式「不删」，留给数据治理批）",
    "agent_task_logs": "无 character_id 外键；任务执行日志同上，属审计流水",
    "channel_bindings": "无 character_id 外键；账号级渠道接入配置（角色可换绑），删角色不应断渠道",
    "image_gen_tasks": "无 character_id 外键；生图任务留痕含产物路径，属账号级资产",
    "lorebook_entries": "无 character_id 外键；设定集/世界观为多角色共享知识",
    "world_facts": "无 character_id 外键；世界事实为共享知识，同上",
    "shared_events": "无 character_id 外键；跨角色共享事件（多角色共同经历），按角色删会破坏他人叙事",
    "memory_write_receipts": "无 character_id 外键且列可空；记忆写入回执是 append-only 审计流水",
    "prospective_intents": "不删行，但未触发的（pending/matched）在级联里置 cancelled（P2-1，防到期反复白烧 LLM）；已兑现/作废行留痕",
    "ai_moments": "二级级联：先按 moment_id 清赞/评论子树再删本体（级联第 1 步），不能按 character_id 直删",
    "moment_ai_likes": "二级级联：既随 TA 自己动态清，也按 character_id 清其在他人动态下的 AI 赞（级联第 1 步）",
    "weave_cards": "二级级联：多角色共享卡转移归属给另一角色、独占卡才删本体（级联第 2 步），按 character_id 直删会误删共享卡",
}

# ── 例外清单 B：不在 SPECS，仅靠物理外键 ON DELETE CASCADE 兜底 ──────────────────
# 登记表而非直接放行：一旦哪天模型收紧（去掉 ondelete）或老库 DDL 本就无 CASCADE，
# 这张清单会提醒「该进 SPECS 了」，而不是悄悄长出孤儿。
SPECS_DB_CASCADE_ONLY: dict[str, str] = {
    "game_achievements": "成就解锁记录按 (user, character, game_type, key) 累计，character_id 可空；靠物理外键 ON DELETE CASCADE",
    "game_stats": "战绩累计行 character_id 可空（NULL=真人维度）；靠物理外键 ON DELETE CASCADE",
    "group_char_cognitions": "群内「角色↔角色」认知条目；靠物理外键 ON DELETE CASCADE",
}


def _scan(db_path) -> dict[str, set[str]]:
    """量出「实际库里」的三个集合：有 character_id 列的表 / SPECS / 物理级联兜底的表。"""
    con = sqlite3.connect(str(db_path))
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        with_col: set[str] = set()
        fk_cascade: set[str] = set()
        for table in tables:
            cols = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
            if FAMILY_COLUMN in cols:
                with_col.add(table)
            for fk in con.execute(f'PRAGMA foreign_key_list("{table}")'):
                if fk[_FK_TARGET] == CHARACTER_ROOT_TABLE and (fk[_FK_ON_DELETE] or "").upper() == "CASCADE":
                    fk_cascade.add(table)
    finally:
        con.close()
    from app.application.character_cascade import CHARACTER_DELETE_SPECS
    return {
        "with_col": with_col,
        "fk_cascade": fk_cascade,
        "specs": {model.__tablename__ for model, _col in CHARACTER_DELETE_SPECS},
    }


@pytest.fixture()
def census(tmp_path):
    """临时库（会话级模板库克隆）→ 只量结构不种数据，跑完即弃。"""
    db_path = tmp_path / "census.db"
    engine = clone_engine(db_path)
    engine.sync_engine.dispose()  # 先放句柄，再用裸 sqlite3 读结构
    return _scan(db_path)


def test_级联普查_集合差不得超出例外清单(census):
    # 判据①：既没进 SPECS 也没物理级联兜底的表，必须是已登记理由的例外
    uncovered = census["with_col"] - (census["specs"] | census["fk_cascade"])
    assert uncovered <= set(SPECS_EXCEPTIONS), (
        f"以下表带 character_id 却无人负责删除，且不在例外清单：{sorted(uncovered - set(SPECS_EXCEPTIONS))}"
        "；新表要么进 CHARACTER_DELETE_SPECS，要么在此登记理由"
    )
    # 判据②：没进 SPECS 的表（哪怕有 DB 兜底）也必须被两份清单之一覆盖
    not_in_specs = census["with_col"] - census["specs"]
    assert not_in_specs <= set(SPECS_EXCEPTIONS) | set(SPECS_DB_CASCADE_ONLY), (
        f"以下表未被 CHARACTER_DELETE_SPECS 覆盖且无登记理由：{sorted(not_in_specs - set(SPECS_EXCEPTIONS) - set(SPECS_DB_CASCADE_ONLY))}"
    )
    # 僵尸防护：清单里的表必须真的在 A 里（表改名/删列后清单不会留着假审查）
    for name, listed in (("SPECS_EXCEPTIONS", set(SPECS_EXCEPTIONS)),
                         ("SPECS_DB_CASCADE_ONLY", set(SPECS_DB_CASCADE_ONLY))):
        zombie = listed - census["with_col"]
        assert not zombie, f"{name} 存在僵尸条目（库里已无该 character_id 表）：{sorted(zombie)}"
    # 清单 B 的理由必须站得住：这些表确实由物理外键 CASCADE 兜底
    relying = set(SPECS_DB_CASCADE_ONLY) & census["with_col"]
    assert relying <= census["fk_cascade"], (
        f"以下表登记为「仅靠物理级联」但库里并无 ON DELETE CASCADE 外键：{sorted(relying - census['fk_cascade'])}"
    )
    # SPECS 也不许有僵尸：清单内每张表在库里都得有 character_id 列
    assert census["specs"] <= census["with_col"], (
        f"CHARACTER_DELETE_SPECS 含库中不存在该列的表：{sorted(census['specs'] - census['with_col'])}"
    )


def test_级联普查_例外理由非空且普查集合非平凡(census):
    """防「空清单/空理由」把红灯变成绿灯。"""
    for name, listed in (("SPECS_EXCEPTIONS", SPECS_EXCEPTIONS),
                         ("SPECS_DB_CASCADE_ONLY", SPECS_DB_CASCADE_ONLY)):
        assert listed, f"{name} 不应为空——为空说明普查已把清单退化成摆设"
        assert all(reason.strip() for reason in listed.values()), f"{name} 存在空理由"
    assert len(census["with_col"]) >= 40, "普查集合异常小（临时库没建全？别再走 ORM 清单）"
    assert len(census["specs"]) >= 40, "CHARACTER_DELETE_SPECS 条目异常少（被误删/import 失败？）"
