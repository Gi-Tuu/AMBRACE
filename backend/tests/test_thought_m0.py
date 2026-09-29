# -*- coding: utf-8 -*-
"""A4 批 4 M0 —— 念头池 T2 纯函数 + 离线回放脚本只读性测试。

覆盖（设计 §7 M0 行 DoD）：
①六个来源面的抽取分支；②入池三道过滤各一例；③novelty/salt 边界（0/1/相同句/空串）；
④升级与挤出判据；⑤回放脚本**只读性**（用「一写就抛」的假连接跑通全脚本取数）。

M0 零行为：本文件不连生产库、不建表、不起服务——只喂内存字典。
"""
from __future__ import annotations

import importlib.util
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.thought import dynamics as dyn
from app.domain.thought import extract as ex
from app.domain.thought import filters as fl

_NOW = datetime(2026, 9, 29, 6, 0, 0)  # naive UTC＝北京 14:00
_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "thought_replay.py")


def _days_ago(n: int) -> datetime:
    return _NOW - timedelta(days=n)


# ─────────────────────────── ① 抽取规则：六个面 ───────────────────────────
def test_f1_activity_create_completed_becomes_spark():
    rows = ex.extract_activity(
        {"id": 1, "character_id": 13, "activity_type": "create", "status": "completed",
         "summary": "画了一张秋天的赛博朋克城市"},
    )
    assert len(rows) == 1
    assert rows[0]["source_type"] == ex.SRC_ACTIVITY
    assert rows[0]["status_hint"] == "spark"
    assert rows[0]["source_ref"] == "1"


def test_f1_activity_skips_non_f1_type_and_failed_status():
    """F1 两个否定分支：活动类型不在白名单 / 状态非 completed。"""
    assert ex.extract_activity({"id": 1, "activity_type": "sleep", "status": "completed"}) == []
    assert ex.extract_activity({"id": 2, "activity_type": "create", "status": "failed"}) == []


def test_f1_activity_already_shared_by_life_share_marks_spent_not_spark():
    """§3.2 第 1 行方案 A：已被 life_share 讲掉 → 只留痕（spent），不以闪念入池。"""
    row = {"id": 7, "character_id": 13, "activity_type": "study", "status": "completed",
           "summary": "翻完了那本讲睡眠的书"}
    assert ex.extract_activity(row, shared_refs={"7"})[0]["status_hint"] == dyn.STATUS_SPENT
    assert ex.extract_activity(row, shared_refs=set())[0]["status_hint"] == "spark"


def test_f2_reflection_splits_and_keeps_only_trigger_word_hits():
    row = {"id": 300, "character_id": 13, "user_id": 1, "sub_type": "plan",
           "content": "今天学了折纸。下次想试试给ta折一只 crane。网络卡了有点扫兴。天气不错。"}
    out = ex.extract_reflection(row)
    assert len(out) <= ex.F2_MAX_CANDIDATES
    assert [d["text"] for d in out] == ["下次想试试给ta折一只 crane"]
    assert out[0]["source_ref"].startswith("300#")


def test_f2_reflection_caps_at_three_candidates_and_rejects_bad_sub_type():
    long_text = "。".join([f"要不要试试第{i}件小事啊喂" for i in range(6)])
    assert len(ex.extract_reflection({"id": 1, "sub_type": "review", "content": long_text})) == 3
    assert ex.extract_reflection({"id": 1, "sub_type": "diary", "content": long_text}) == []


def test_f3_moment_requires_elapsed_window_and_zero_engagement():
    base = {"id": 500, "character_id": 13, "user_id": 1, "content": "秋天的阳光正好"}
    assert ex.extract_moment({**base, "age_days": 3, "engagement_count": 0}) == []      # 未过窗
    assert ex.extract_moment({**base, "age_days": 30, "engagement_count": 4}) == []     # 有人理
    assert ex.extract_moment({**base, "age_days": 30, "engagement_count": None}) == []  # 互动未知
    ok = ex.extract_moment({**base, "age_days": 30, "engagement_count": 0})
    assert len(ok) == 1 and ok[0]["source_type"] == ex.SRC_MOMENT


def test_f3_moment_pool_text_hash_is_blocked():
    """§3.2 最后一行的去重位＝**入池文本**哈希（同一条重复抽取/他面照抄会撞上）。"""
    row = {"id": 501, "content": "刚下楼买了杯奶茶", "age_days": 30, "engagement_count": 0}
    draft = ex.extract_moment(row)[0]["text"]
    assert ex.extract_moment(row, blocked_text_hashes={ex.text_hash(draft)}) == []
    assert len(ex.extract_moment(row, blocked_text_hashes=set())) == 1


def test_f4_user_hook_needs_ongoing_status_and_idle_days():
    base = {"id": 600, "character_id": 13, "user_id": 1, "topic": "那个新番的结局到底怎样",
            "status": ex.F4_ACTIVE_STATUS, "idle_days": 5.0}
    assert len(ex.extract_user_hook(base)) == 1
    assert ex.extract_user_hook({**base, "status": "完成"}) == []
    assert ex.extract_user_hook({**base, "idle_days": 1.0}) == []


def test_f4_user_hook_excludes_topics_claimed_by_unfinished_topic():
    """§3.2 第 2 行防线①：命中 unfinished_topic 词表的话题不入池（双向排除）。"""
    assert ex.extract_user_hook(
        {"id": 601, "topic": "下次一起去看那个展", "status": ex.F4_ACTIVE_STATUS, "idle_days": 9.0}
    ) == []
    # 同一话题，换掉词表入参即可放行（证明词表是注入式，可对齐生产漂移）
    assert len(ex.extract_user_hook(
        {"id": 601, "topic": "下次一起去看那个展", "status": ex.F4_ACTIVE_STATUS, "idle_days": 9.0},
        unfinished_keywords=(),
    )) == 1


def test_f5_fact_only_inferred_or_unverified_and_empty_value_dropped():
    base = {"id": 700, "character_id": 13, "user_id": 1}
    assert len(ex.extract_fact({**base, "epistemic_status": "INFERRED", "value": "ta是不是换工作了"})) == 1
    assert len(ex.extract_fact({**base, "epistemic_status": "UNVERIFIED", "value": "周报交了吗"})) == 1
    assert ex.extract_fact({**base, "epistemic_status": "FACT", "value": "住在杭州"}) == []
    assert ex.extract_fact({**base, "epistemic_status": "INFERRED", "value": "   "}) == []


def test_f6_interest_new_or_rising_only():
    assert len(ex.extract_interest({"id": 800, "character_id": 13, "name": "胶片摄影"})) == 1
    assert len(ex.extract_interest({"id": 801, "character_id": 13, "name": "胶片摄影",
                                    "level": 5, "prev_level": 3})) == 1
    assert ex.extract_interest({"id": 802, "character_id": 13, "name": "胶片摄影",
                                "level": 3, "prev_level": 3}) == []
    assert ex.extract_interest({"id": 803, "character_id": 13, "name": "  "}) == []


def test_unknown_source_type_is_rejected_by_draft_factory():
    d = ex._draft("随便一句话", "not_a_face", "1", 13, 1)
    assert d["status_hint"] == "rejected"


# ─────────────────────────── ② 入池三道过滤 ───────────────────────────
def test_filter_1_length_rejects_too_short_and_too_long():
    assert fl.filter_length("吃了饭") is False                       # 3 字 < 6
    assert fl.filter_length("今天阳光正好适合发呆") is True            # 11 字
    assert fl.filter_length("一" * (fl.TEXT_MAX_LEN + 1)) is False    # 超长＝抄原文


def test_filter_2_recent_overlap_uses_topic_bucket():
    recent = ["粥在锅里热着，趁喝", "早点睡吧别熬了"]
    assert fl.filter_recent_overlap("那碗粥我一直惦记着想喝点热的", recent) is False  # 同 meal 桶
    assert fl.filter_recent_overlap("那本书的结局真的很意外啊", recent) is True        # 无桶→放行


def test_filter_2_only_looks_at_last_three_sent():
    recent = ["粥在锅里热着"] + ["无关话题甲乙丙丁"] * 3
    assert fl.filter_recent_overlap("想再喝碗粥", recent) is True  # 第 4 条以前不参与比对


def test_filter_3_fictional_blocked_and_none_passes():
    assert fl.filter_epistemic("FICTIONAL") is False
    assert fl.filter_epistemic("FACT") is True
    assert fl.filter_epistemic(None) is True


def test_intake_reject_reason_is_short_circuit_and_single_label():
    assert fl.intake_reject_reason("太短", "FACT") == fl.REASON_LENGTH
    assert fl.intake_reject_reason("这条完全是虚构设定的一段话", "FICTIONAL") == fl.REASON_FICTIONAL
    assert fl.intake_reject_reason("趁热把这碗粥喝了如何", "FACT", ["我刚喝了粥"]) == fl.REASON_RECENT_OVERLAP
    assert fl.intake_reject_reason("那本讲睡眠的书结局很意外", "FACT") is None


# ─────────────────────────── ③ novelty / salt 边界 ───────────────────────────
def test_novelty_bounds_zero_one_and_halflife():
    assert dyn.novelty(0) == 1.0                                   # 当天＝最新鲜
    # 设计 §2.2 的公式是 exp(-t/τ)：τ 是 e 折叠时间，真减半点在 τ·ln2（此不一致记入报告第 6 节）
    assert math.isclose(dyn.novelty(dyn.NOVELTY_HALFLIFE_DAYS), 1 / math.e, rel_tol=1e-12)
    assert math.isclose(
        dyn.novelty(dyn.NOVELTY_HALFLIFE_DAYS * math.log(2)), 0.5, rel_tol=1e-9
    )
    assert dyn.novelty(500) > 0.0 and dyn.novelty(500) < 1e-20
    assert dyn.novelty(-3) == 1.0                                  # 未来时间戳夹到 0
    assert dyn.novelty(7, halflife_days=0) == 0.0                  # 退化半衰期不除零
    assert dyn.novelty(1) > dyn.novelty(5) > dyn.novelty(30)        # 单调衰减


def test_salt_same_source_twice_and_empty_sources():
    """「相同句」＝同一来源面重复命中不加盐；空来源＝0 盐。"""
    assert dyn.salt_of([ex.SRC_USER_HOOK]) == 1.2
    assert dyn.salt_of([ex.SRC_USER_HOOK, ex.SRC_USER_HOOK, ex.SRC_USER_HOOK]) == 1.2
    assert dyn.salt_of([]) == 0.0
    assert dyn.salt_of(["", None]) == 0.0
    assert dyn.salt_of(["nope"]) == 0.0
    assert dyn.salt_of([ex.SRC_ACTIVITY, ex.SRC_REFLECT], bump_hits=2) == 1.0 + 0.8 + 2 * dyn.SALT_BUMP_WEIGHT


def test_normalize_text_empty_string_and_punctuation_only():
    assert ex.normalize_text("") == ""
    assert ex.normalize_text(None) == ""
    assert ex.normalize_text("  ，。！~  ") == ""
    assert ex.normalize_text("下次  再聊，好吗？") == "下次再聊好吗"
    assert ex.normalized_length("下次再聊好吗？") == 6
    assert ex.text_hash("A B!") == ex.text_hash("ab")              # 归一后同哈希（幂等位）


def test_age_days_uses_beijing_calendar_boundary_not_24h_window():
    """北京日界：UTC 15:30（北京 23:30）与次日 UTC 15:30 差 1 日，同日内的早晚不各自加一天。"""
    early = datetime(2026, 9, 1, 6, 0)      # 北京 09-01 14:00
    late = datetime(2026, 9, 1, 16, 30)     # 北京 09-02 00:30 → 跨了自然日
    assert dyn.age_days(early, late) == 1.0
    assert dyn.age_days(early, early + timedelta(hours=3)) == 0.0
    assert dyn.age_days(None, _NOW) == 0.0


# ─────────────────────────── ④ 升级与挤出判据 ───────────────────────────
def test_promote_needs_both_salt_and_two_distinct_faces():
    assert dyn.should_promote(dyn.salt_of([ex.SRC_USER_HOOK, ex.SRC_ACTIVITY]),
                              [ex.SRC_USER_HOOK, ex.SRC_ACTIVITY]) is True
    assert dyn.should_promote(dyn.salt_of([ex.SRC_USER_HOOK]), [ex.SRC_USER_HOOK]) is False   # 单面
    assert dyn.should_promote(dyn.salt_of([ex.SRC_INTEREST, ex.SRC_MOMENT]),
                             [ex.SRC_INTEREST, ex.SRC_MOMENT]) is False                        # 0.5+0.6<2.0
    assert dyn.should_promote(dyn.SALT_OBSSESSION_THRESHOLD, [ex.SRC_FACT, ex.SRC_MOMENT]) is True  # 边界取等


def test_ttl_and_told_flat_progression():
    assert dyn.should_fade(dyn.TTL_DAYS) is False                       # 恰好 21 天不枯
    assert dyn.should_fade(dyn.TTL_DAYS + 0.5) is True
    s1, n1, st1 = dyn.apply_told_flat(3.0, 0)
    assert (n1, st1) == (1, dyn.STATUS_TOLD_FLAT) and math.isclose(s1, 3.0 * dyn.SALT_TOLD_FLAT_RATIO)
    _s2, n2, st2 = dyn.apply_told_flat(s1, 1)
    assert (n2, st2) == (2, dyn.STATUS_FADED)                           # tell_count≥2 强制枯


def test_classify_release_three_tiers():
    assert dyn.classify_release(True, True) == dyn.STATUS_SPENT
    assert dyn.classify_release(True, False) == dyn.STATUS_TOLD_FLAT
    assert dyn.classify_release(False, True) == "never_told"            # 没发出去不惩罚
    assert dyn.classify_release(False, False) == "never_told"           # 被闸拦下≠说了


def test_evict_drops_weakest_by_salt_times_novelty_per_status():
    recs = [{"id": i, "status": dyn.STATUS_SPARK, "salt": 1.0, "novelty": 0.1 + i * 0.05}
            for i in range(dyn.CAP_SPARK + 3)]
    recs.append({"id": "obs-strong", "status": dyn.STATUS_OBSESSION, "salt": 5.0, "novelty": 1.0})
    doomed = dyn.evict(recs)
    assert len(doomed) == 3
    assert set(doomed) == {"0", "1", "2"}                               # 最弱的三条
    assert "obs-strong" not in doomed                                   # obsession 未超顶不动


def test_evict_respects_obsession_and_told_flat_caps_independently():
    recs = [{"id": f"o{i}", "status": dyn.STATUS_OBSESSION, "salt": 2.0, "novelty": 0.5}
            for i in range(dyn.CAP_OBSESSION + 1)]
    recs += [{"id": f"t{i}", "status": dyn.STATUS_TOLD_FLAT, "salt": 2.0, "novelty": 0.5}
             for i in range(dyn.CAP_TOLD_FLAT + 2)]
    doomed = dyn.evict(recs)
    assert len([d for d in doomed if d.startswith("o")]) == 1
    assert len([d for d in doomed if d.startswith("t")]) == 2
    assert len(dyn.evict(recs[: dyn.CAP_OBSESSION])) == 0               # 未超顶一律不挤


def test_faded_rows_are_not_counted_by_evict():
    """已 faded/spent 的行不参与容量计数（只改状态、不物理删，协议 §十七）。"""
    recs = [{"id": i, "status": st, "salt": 1.0, "novelty": 0.5}
            for i, st in enumerate([dyn.STATUS_FADED, dyn.STATUS_SPENT])]
    assert dyn.evict(recs) == []
    assert dyn.STATUS_FADED not in dyn.ACTIVE_STATUSES
    assert dyn.STATUS_SPENT not in dyn.ACTIVE_STATUSES


# ─────────────────────────── ⑤ 回放脚本只读性 ───────────────────────────
@pytest.fixture(scope="module")
def replay_mod():
    spec = importlib.util.spec_from_file_location("thought_replay", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _WriteForbiddenCursor:
    """一写就抛的假游标：任何非 SELECT 语句立刻失败。"""

    def __init__(self, owner):
        self._owner = owner

    def execute(self, sql, args=()):
        head = re.sub(r"^\s*", "", str(sql)).upper()
        if not head.startswith("SELECT"):
            raise AssertionError(f"回放脚本发出了非 SELECT 语句：{sql[:80]}")
        self._owner.seen.append(head.split()[0])
        return self

    def fetchall(self):
        return []


class _WriteForbiddenConn:
    def __init__(self):
        self.seen: list[str] = []

    def cursor(self):
        return _WriteForbiddenCursor(self)

    def execute(self, sql, args=()):
        if not str(sql).strip().upper().startswith("PRAGMA"):
            raise AssertionError(f"连接层直接发出非 PRAGMA 语句：{sql[:80]}")
        return self

    def fetchall(self):
        return []

    def close(self):
        pass


def test_replay_script_fetch_all_issues_select_only(replay_mod):
    con = _WriteForbiddenConn()
    data = replay_mod.fetch_all(con, 10, _NOW - timedelta(days=30), _NOW)
    assert set(data) >= {"activity", "reflect", "moment", "user_hook", "fact", "fact_mem",
                         "interest", "proactive_logs", "ai_moments", "moment_engage", "user_msgs"}
    assert con.seen and set(con.seen) == {"SELECT"}


def test_replay_script_ast_has_no_write_or_wiring_calls(replay_mod):
    """静态自检（走 AST，不受注释/文档字符串里「严禁 INSERT」这类字样干扰）：

    ① 不 import 任何业务重模块；② 所有 ``.execute()`` 的首参都是 SELECT/PRAGMA 字面量；
    ③ 不存在 ``executescript`` / ``.commit(``；④ 只读串与 query_only 必须写在代码里。
    """
    import ast

    src = open(replay_mod.__file__, encoding="utf-8").read()
    tree = ast.parse(src)

    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    for banned in ("app.db", "app.models", "app.scheduling", "app.application", "app.agent",
                   "app.services", "sqlalchemy", "alembic"):
        assert not any(i == banned or i.startswith(banned + ".") for i in imported), banned
    assert all(i.startswith("app.domain.thought") or not i.startswith("app.") for i in imported)

    sql_head = re.compile(r"(?i)^\s*(select|insert\b|update\b|delete\b|drop\b|create\s+table"
                          r"|alter\b|pragma)\b")
    sql_like = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if sql_head.match(node.value):
                sql_like.append(node.value.strip())
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "executescript":
                raise AssertionError("脚本用了 executescript")
            if node.func.attr == "commit":
                raise AssertionError("脚本里有 commit")
    assert len(sql_like) >= 8, f"只找到 {len(sql_like)} 条 SQL 字面量——取数逻辑可能被改空了"
    for s in sql_like:
        assert s.upper().startswith(("SELECT", "PRAGMA")), f"非只读 SQL：{s[:70]}"

    assert "mode=ro" in src and "query_only=ON" in src and "uri=True" in src


def test_replay_connect_readonly_uses_ro_uri(replay_mod, tmp_path, monkeypatch):
    """真连一次临时空库：只读模式下任何写操作必须被 SQLite 驱动层拒绝。"""
    import sqlite3

    db = tmp_path / "fake.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE life_activity_logs (id INTEGER, character_id INTEGER,"
                " activity_type TEXT, status TEXT, input_json TEXT, output_json TEXT,"
                " completed_at TEXT, started_at TEXT)")
    con.execute("INSERT INTO life_activity_logs VALUES (1,13,'create','completed','{}','{}',NULL,NULL)")
    con.commit()
    con.close()

    captured = {}
    real_connect = sqlite3.connect

    def spy_connect(*a, **kw):
        captured["args"] = (a, kw)
        return real_connect(*a, **kw)

    monkeypatch.setattr(replay_mod.sqlite3, "connect", spy_connect)
    ro = replay_mod.connect_readonly(str(db))
    assert captured["args"][1].get("uri") is True
    assert "mode=ro" in captured["args"][0][0]
    assert ro.execute("PRAGMA query_only").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO life_activity_logs VALUES (2,13,'x','y','{}','{}',NULL,NULL)")
    ro.close()


def test_replay_pipeline_runs_end_to_end_on_empty_reads(replay_mod):
    """取数全空时回放链路不崩（报告数字为 0 而不是异常）。"""
    data = {k: [] for k in ("activity", "reflect", "moment", "user_hook", "fact", "fact_mem",
                            "interest", "proactive_logs", "ai_moments", "moment_engage", "user_msgs")}
    rp = replay_mod.replay_faces(data, _NOW, 30, set())
    pressure = replay_mod.pool_pressure(rp, _NOW, 30)
    b1 = replay_mod.baseline_pool(rp, pressure, 30)
    b2 = replay_mod.baseline_effect(data, _NOW, 30)
    b3 = replay_mod.baseline_collision(rp, data)
    text = replay_mod.render_report(rp, pressure, b1, b2, b3, 30, "none.db", _NOW)
    assert b1["kept_total"] == 0 and b2["reply_rate_60min"] is None
    assert b3["overlap_rate_of_judged"] is None
    assert "不新增 flag、不新增表" in text


def test_replay_counts_kept_and_rejected_consistency(replay_mod):
    """跑通「原始信号 → 三道过滤」的分流恒等式：kept + rejected(三道) + 其它 = raw 总数。"""
    data = {
        "activity": [
            {"id": 1, "character_id": 13, "activity_type": "create", "status": "completed",
             "input_json": '{"reason":"画了一张秋天的赛博朋克城市"}', "output_json": "{}",
             "completed_at": "2026-09-20 02:00:00", "started_at": None},
            {"id": 2, "character_id": 13, "activity_type": "study", "status": "completed",
             "input_json": '{"reason":"短"}', "output_json": "{}",
             "completed_at": "2026-09-21 02:00:00", "started_at": None},
            {"id": 3, "character_id": 13, "activity_type": "sleep", "status": "completed",
             "input_json": "{}", "output_json": "{}",
             "completed_at": "2026-09-21 02:00:00", "started_at": None},
        ],
        "reflect": [{"id": 10, "character_id": 13, "user_id": 1, "sub_type": "review",
                     "content": "下次想试试自己做酸奶。今天很困。", "epistemic_status": "FACT",
                     "created_at": "2026-09-25 02:00:00"}],
        "moment": [{"id": 20, "character_id": 13, "user_id": 1,
                    "content": "发了一条朋友圈: 秋天适合坐在窗边发呆", "epistemic_status": "FACT",
                    "created_at": "2026-09-01 02:00:00"}],
        "user_hook": [{"id": 30, "character_id": 13, "user_id": 1, "topic": "那家新开的云南菜馆叫什么",
                       "status": "进行中", "last_touched_at": "2026-09-10 02:00:00"}],
        "fact": [{"id": 40, "character_id": 13, "user_id": 1, "predicate": "可能换工作",
                  "object_value": "ta上周面试了一家做地图的公司", "epistemic_status": "UNVERIFIED",
                  "created_at": "2026-09-26 02:00:00"}],
        "fact_mem": [],
        "interest": [{"id": 50, "character_id": 13, "name": "胶片摄影", "level": 3,
                      "created_at": "2026-09-27 02:00:00", "updated_at": None}],
        "ai_moments": [{"id": 60, "character_id": 13, "user_id": 1,
                        "content": "秋天适合坐在窗边发呆", "created_at": "2026-09-01 02:00:00"}],
        "moment_engage": [{"moment_id": 60, "n": 0}],
        "proactive_logs": [{"id": 70, "character_id": 13, "session_id": 1,
                            "message_type": "memory_review", "content": "趁热把那碗粥喝了吧",
                            "created_at": "2026-09-27 12:00:00"}],
        "user_msgs": [{"session_id": 1, "created_at": "2026-09-27 12:30:00"}],
    }
    rp = replay_mod.replay_faces(data, _NOW, 30, set())
    assert rp.face_raw[ex.SRC_ACTIVITY] == 2          # id=1/2 命中 F1（id=3 是 sleep，不产信号）
    assert rp.reject_reasons[fl.REASON_LENGTH] >= 1   # id=2 的「短」被过滤① 拦
    assert rp.face_kept[ex.SRC_USER_HOOK] == 1
    assert rp.face_kept[ex.SRC_FACT] == 1
    assert rp.drafts, "至少应有候选入池"
    assert all(d["rejected"] is None for d in rp.drafts)
    assert sum(rp.reject_reasons[r] for r in fl.INTAKE_REASONS) + len(rp.drafts) == sum(rp.face_raw.values())
    b2 = replay_mod.baseline_effect(data, _NOW, 30)
    assert b2["sends"] == 1 and b2["replied"] == 1 and b2["reply_rate_60min"] == 100.0
    b3 = replay_mod.baseline_collision(rp, data)
    assert b3["rival_buckets"] == ["meal"]


def test_all_thresholds_are_module_level_constants():
    """M0 要求「阈值一律做成模块级常量便于标定」——缺一即测试红。"""
    required = {
        ex: ["SALT_WEIGHT_BY_SOURCE", "F1_ACTIVITY_TYPES", "F2_MAX_CANDIDATES", "F2_TRIGGER_WORDS",
             "F3_ENGAGEMENT_WINDOW_DAYS", "F4_IDLE_DAYS", "F5_EPISTEMIC_ACCEPT"],
        fl: ["TEXT_MIN_LEN", "TEXT_MAX_LEN", "RECENT_SENT_LOOKBACK", "EPISTEMIC_BLOCKED", "TOPIC_BUCKETS"],
        dyn: ["NOVELTY_HALFLIFE_DAYS", "SALT_OBSSESSION_THRESHOLD", "MIN_DISTINCT_HIT_SOURCES",
              "TTL_DAYS", "CAP_SPARK", "CAP_OBSESSION", "CAP_TOLD_FLAT", "SALT_TOLD_FLAT_RATIO",
              "MAX_TELL_COUNT", "SALT_BUMP_WEIGHT", "REPLY_WINDOW_MINUTES"],
    }
    for mod, names in required.items():
        for n in names:
            assert hasattr(mod, n), f"{mod.__name__} 缺常量 {n}"


def test_domain_package_is_io_free():
    """域层零 IO：在**干净子进程**里单独 import 本包，只允许拉开关-free 的 domain 模块。

    必须在子进程里查：本 pytest 会话的 conftest 早已把 app.db/app.models 拉进 sys.modules，
    父进程里查必然假阳性。
    """
    import json
    import subprocess

    code = (
        "import sys, json; import app.domain.thought as t; "
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('app.'))))"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert r.returncode == 0, r.stderr[-800:]
    loaded = json.loads(r.stdout.strip().splitlines()[-1])
    assert all(m == "app" or m.startswith("app.domain") for m in loaded), loaded
    assert not [m for m in loaded if m.startswith(("app.db", "app.models", "app.scheduling",
                                                   "app.application", "app.agent"))]

    import app.domain.thought as pkg

    assert set(pkg.__all__) >= {"extract_activity", "intake_reject_reason", "salt_of", "evict"}
    for mod in (pkg, ex, fl, dyn):
        src = open(mod.__file__, encoding="utf-8").read()
        for token in ("sqlite3", "sqlalchemy", "async_session", "requests", "open(", "await "):
            assert token not in src, f"{mod.__name__} 含 IO/异步痕迹 {token}"
