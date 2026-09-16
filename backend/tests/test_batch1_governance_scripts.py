# -*- coding: utf-8 -*-
"""批次一（2026-09-16）治理脚本单测：world_facts 清理（任务6）+ 计划/锚点治理（任务3/5）纯函数。

纪律：测试一律用 tmp_path 建临时 SQLite 文件库，绝不触碰 backend/data 生产库。
脚本以 importlib 从磁盘加载（scripts/ 不是 Python 包）。
"""
import importlib.util
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent


def _load(name: str, rel: str):
    path = _REPO / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def wf():
    """scripts/cleanup_world_facts.py 模块。"""
    return _load("cleanup_world_facts_mod", "scripts/cleanup_world_facts.py")


@pytest.fixture(scope="module")
def gov():
    """scripts/memory/batch1_plan_and_anchor_governance.py 模块。"""
    return _load("batch1_gov_mod", "scripts/memory/batch1_plan_and_anchor_governance.py")


# ────────────────────────── 任务6：world_facts 清理 ──────────────────────────

def _mk_world_facts_db(tmp_path):
    db = str(tmp_path / "wf.db")
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE world_facts (id INTEGER PRIMARY KEY, user_id INTEGER, character_id INTEGER,"
        " subject_type TEXT, subject_id INTEGER, predicate TEXT, object_value TEXT, status TEXT,"
        " kind TEXT, superseded_by INTEGER, superseded_at TEXT, asserted_at TEXT, updated_at TEXT)"
    )
    rows = [
        # 错误身份事实（active）
        (15, 3, 13, "curated", "用户是宣传部艺术负责人", "active", "fact", "2026-09-10 07:05:08"),
        (42, 3, 13, "curated", "用户是学校工作人员", "active", "fact", "2026-09-12 06:03:40"),
        (64, 3, 13, "curated", "用户是设计师，从事设计工作", "active", "fact", "2026-09-14 14:01:36"),
        # 批次一现状锚点（char13）
        (200, 3, 13, "curated", "用户当前常驻：湛江市·广东海洋大学湖光校区·学生宿舍", "active", "fact", "2026-09-16 05:00:00"),
        # 关系基线重复：char13 三条、char6 两条
        (13, 3, 13, "curated", "我是用户的老公，关系稳定", "active", "relationship_baseline", "2026-09-10 05:24:53"),
        (94, 3, 13, "curated", "我是用户的老公，称呼sam", "active", "relationship_baseline", "2026-09-16 02:54:58"),
        (100, 3, 13, "curated", "用户是男性，是sam的老公", "active", "relationship_baseline", "2026-09-16 04:36:34"),
        (53, 3, 6, "curated", "用户的对象是sam（男）", "active", "relationship_baseline", "2026-09-13 10:49:40"),
        (56, 3, 6, "curated", "用户的对象是sam（男），sam不是当前AI", "active", "relationship_baseline", "2026-09-13 11:08:11"),
        # 裸 expired（只报告，不动）
        (8, 3, 13, "activity", "路灯刚亮时…", "expired", "status", "2026-09-05 10:01:16"),
    ]
    con.executemany(
        "INSERT INTO world_facts (id,user_id,character_id,predicate,object_value,status,kind,asserted_at,"
        "updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[7]) for r in rows],
    )
    con.commit()
    con.close()
    return db


def test_wf_planning_finds_targets(wf, tmp_path):
    db = _mk_world_facts_db(tmp_path)
    con = wf.connect_ro(db)
    try:
        wrong = wf.find_wrong_identity_facts(con, wf.DEFAULT_WRONG_IDS)
        assert [r["id"] for r in wrong] == [15, 42, 64]
        anchors = wf.find_anchor_fact_ids(con)
        assert anchors == {13: 200}
        groups = wf.find_relationship_baseline_groups(con)
        assert [g["character_id"] for g in groups] == [6, 13]
        by_char = {g["character_id"]: g for g in groups}
        assert by_char[13]["keep"]["id"] == 100                       # asserted_at 最新
        assert sorted(r["id"] for r in by_char[13]["drop"]) == [13, 94]
        assert by_char[6]["keep"]["id"] == 56
        assert [r["id"] for r in by_char[6]["drop"]] == [53]
        assert len(wf.find_bare_expired(con)) == 1
    finally:
        con.close()


def test_wf_apply_supersedes_with_chain_and_is_idempotent(wf, tmp_path):
    """错误身份事实挂到现状锚点（supersede 链）、关系基线按角色各留 1 条；可重复跑。"""
    db = _mk_world_facts_db(tmp_path)
    con = wf.connect_ro(db)
    wrong = wf.find_wrong_identity_facts(con, wf.DEFAULT_WRONG_IDS)
    groups = wf.find_relationship_baseline_groups(con)
    anchors = wf.find_anchor_fact_ids(con)
    expired_before = len(wf.find_bare_expired(con))
    con.close()

    con = wf.connect_rw(db)
    try:
        w, m = wf.apply_changes(con, wrong, groups, anchors)
        assert (w, m) == (3, 3)
        rows = {r["id"]: dict(r) for r in con.execute(
            "SELECT id,status,superseded_by FROM world_facts")}
        for fid in (15, 42, 64):
            assert rows[fid]["status"] == "superseded"
            assert rows[fid]["superseded_by"] == 200                  # supersede 链指向现状锚点
        assert rows[100]["status"] == "active"                        # 权威记录保留
        assert rows[13]["superseded_by"] == 100 and rows[94]["superseded_by"] == 100
        assert rows[56]["status"] == "active" and rows[53]["superseded_by"] == 56
        # 幂等：再跑一遍没有可处理项，且不改动已 superseded 的行
        con2_wrong = wf.find_wrong_identity_facts(con, wf.DEFAULT_WRONG_IDS)
        con2_groups = wf.find_relationship_baseline_groups(con)
        assert con2_wrong == [] and con2_groups == []
        assert wf.apply_changes(con, con2_wrong, con2_groups, {}) == (0, 0)
        # 裸 expired 未被触碰
        assert len(wf.find_bare_expired(con)) == expired_before
    finally:
        con.close()


def test_wf_backup_path_and_apply_guard(wf, tmp_path):
    p = wf.backup_path(str(tmp_path), now=datetime(2026, 9, 16, 6, 30, 15))
    assert p.endswith("ai_companion.db.pre_worldfact_cleanup_20260916_063015")
    assert Path(p).parent == tmp_path
    # 库文件不存在 → 备份失败返回 None（调用方据此中止写库）
    assert wf.do_backup(str(tmp_path / "nope.db"), str(tmp_path)) is None


# ────────────────────────── 任务3/5：计划与锚点治理脚本 ──────────────────────────

def test_gov_residual_valid_to(gov):
    created = datetime(2026, 8, 13, 4, 46, 0)
    assert gov.residual_valid_to(created) == created + timedelta(days=7)
    assert gov.residual_valid_to(created, 3) == created + timedelta(days=3)
    near = gov.residual_valid_to(None)                     # 缺 created_at 时以「现在(UTC)」为锚 + 7 天
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((near - (now_utc + timedelta(days=7))).total_seconds()) < 300
    # 返回 naive（与 memories.valid_to 同口径）
    assert near.tzinfo is None


def test_gov_anchor_payload(gov):
    p = gov.anchor_payload()
    assert p["kind"] == "fact" and p["predicate"] == "curated"
    assert "湛江市" in p["object_value"] and "广东海洋大学湖光校区" in p["object_value"]
    assert "宿舍" in p["object_value"] and "大二" in p["object_value"]
    assert p["user_fact_slot"] == "location" and "湛江市" in p["user_fact_value"]
    assert p["is_authoritative"] is True
    # 触发键覆盖「现状/在哪/位置/学校/宿舍」等现状问法（复用 get_curated_facts._trigger_hit 置顶）
    assert {"现状", "在哪", "位置", "学校", "宿舍"} <= set(p["links"])


def test_gov_backup_path_and_db_url(gov, tmp_path):
    p = gov.backup_path(str(tmp_path), now=datetime(2026, 9, 16, 6, 30, 15))
    assert p.endswith("ai_companion.db.pre_batch1_20260916_063015")
    assert gov.db_file_path("sqlite+aiosqlite:///D:/x/ai_companion.db") == "D:/x/ai_companion.db"
    assert gov.db_file_path("postgresql://x") == ""


def test_gov_residual_ids_default(gov):
    """默认残留清单 = 交接点名的 5 条长沙出差残留。"""
    assert gov.DEFAULT_RESIDUAL_IDS == (7017, 7522, 7573, 7663, 7721)
