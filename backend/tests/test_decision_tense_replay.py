# -*- coding: utf-8 -*-
"""A3 阶段 1·序 2：记忆时态离线回放器（`backend/scripts/decision_tense_replay.py`）单测。

脚本从磁盘 importlib 加载（scripts/ 不是 Python 包，沿用项目既有惯例）。
覆盖回放器三段契约 + 两条纪律：
1. state 文本拼装（时间锚两行 + 正文截断上限 + 空文本丢弃）；
2. 输出解析（1/2/3/4 → 四类，含 no_token / out_of_set 失败分支）；
3. 统计口径（总体/主指标一致率、分类别召回、格式失败率、延迟分位、plan 时窗切片）；
4. 只读纪律：connect_ro 连上去就写不动；
5. 额度纪律：--backend llm 不带 --allow-llm 必须原地退出，--limit 超硬上限直接拒。
临时库一律 tmp_path，绝不触碰 backend/data 生产库。
"""
import importlib.util
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "backend" / "scripts" / "decision_tense_replay.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("decision_tense_replay", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dtr = _load_script()

_COLS = """
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    character_id INTEGER,
    memory_type TEXT,
    sub_type TEXT,
    title TEXT,
    content TEXT,
    why_it_matters TEXT,
    is_core BOOLEAN,
    core_category TEXT,
    status TEXT,
    is_archived BOOLEAN,
    created_at DATETIME,
    valid_to DATETIME
"""

# 造 6 条覆盖四类真值 + 两个样本池 + 空文本 + 不该入样的行
_ROWS = [
    # (id, character_id, memory_type, sub_type, title, content, why, is_core, core_category,
    #  status, is_archived, created_at, valid_to)
    (1, 7, "insight", "extracted", "用户喜欢奶茶", "用户喜欢喝奶茶，三分糖", "稳定偏好",
     0, None, "active", 0, "2026-09-01 10:00:00", None),
    (2, 7, "user_info", "plan", "明日行程", "用户打算明天去长沙", "行程确认",
     0, None, "active", 0, "2026-09-10 08:00:00", None),
    (3, 7, "user_info", "location", "常驻地", "用户现在常驻杭州", "长期定位",
     0, None, "active", 0, "2026-09-11 08:00:00", None),
    (4, 7, "working_state", "status", "当前状态", "用户正在加班", "瞬时现状",
     0, None, "active", 0, "2026-09-12 20:00:00", None),
    (5, 8, "event", "plan", "搬家安排", "用户计划下周搬家", "已归档但仍是 plan",
     0, None, "active", 1, "2026-09-13 09:00:00", None),
    (6, 7, "event", "moment", "去过书店", "今天去了书店", "stale 行不进可注入面",
     0, None, "stale", 0, "2026-09-14 09:00:00", None),
    (7, 7, "insight", "extracted", "", "   ", "", 0, None, "active", 0, "2026-09-15 09:00:00", None),
]


@pytest.fixture(scope="module")
def replay_db(tmp_path_factory):
    """临时只读库（module 级复用，纯读不写）。"""
    db = tmp_path_factory.mktemp("tense_replay_db") / "mini.db"
    con = sqlite3.connect(str(db))
    con.execute(f"CREATE TABLE memories ({_COLS})")
    con.executemany(
        "INSERT INTO memories (id, character_id, memory_type, sub_type, title, content, why_it_matters,"
        " is_core, core_category, status, is_archived, created_at, valid_to)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        _ROWS,
    )
    con.commit()
    con.close()
    return str(db)


@pytest.fixture(scope="module")
def exported(replay_db):
    """跑一次 --export 等价流程，返回 (samples, stats)。"""
    conn = dtr.connect_ro(replay_db)
    try:
        return dtr.export_samples(conn, db_path=replay_db,
                                  now_today=datetime(2026, 9, 25, 12, 0, 0))
    finally:
        conn.close()


# ────────────────────────── 1. state 文本拼装 ──────────────────────────

def test_build_state_text_has_two_anchor_lines_and_truncates_body():
    state = dtr.build_state_text("北" * 400, "2026-09-01", "2026-09-04")
    lines = state.split("\n")
    assert len(lines) == 3
    assert lines[0] == "记录日期：2026-09-01"
    assert lines[1] == "现在：2026-09-04"
    assert len(lines[2]) == dtr.STATE_MAX_CHARS          # 正文截到 300，保留头部
    assert lines[2].startswith("北北北")


def test_build_state_text_collapses_whitespace_and_keeps_anchors_when_body_short():
    assert dtr.build_state_text("  多行\n\n文本   ", "2026-09-01", "2026-09-04") == \
        "记录日期：2026-09-01\n现在：2026-09-04\n多行 文本"


def test_build_state_text_empty_body_returns_empty_string():
    """空文本样本导出侧据此丢弃（勘察 §3.6：state_text 空则丢弃该样本）。"""
    assert dtr.build_state_text("   \n  ", "2026-09-01", "2026-09-01") == ""


def test_render_messages_shape():
    msgs = dtr.render_messages("记录日期：2026-09-01\n现在：2026-09-01\n用户打算明天去长沙")
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert "只输出一个数字" in msgs[0]["content"]
    assert "1 = 长期成立" in msgs[1]["content"]
    assert "4 = 当时的瞬时状态" in msgs[1]["content"]
    assert msgs[1]["content"].endswith("只输出 1 / 2 / 3 / 4 中的一个数字。")
    assert "用户打算明天去长沙" in msgs[1]["content"]


# ────────────────────────── 2. 输出解析（含失败分支） ──────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("1", "enduring"), ("2", "episodic"), ("3", "plan"), ("4", "transient"),
    (" 2\n", "episodic"), ("3。", "plan"), ("4 是瞬时状态", "transient"),
    (("1", "reasoning"), "enduring"),          # include_reasoning 形态回元组
])
def test_parse_choice_success(raw, expected):
    assert dtr.parse_choice(raw) == (expected, None)


@pytest.mark.parametrize("raw,reason", [
    (None, "no_token"), ("", "no_token"), ("   \n", "no_token"),
    ("5", "out_of_set"), ("0", "out_of_set"), ("episodic", "out_of_set"),
    ("[]", "out_of_set"), ("json", "out_of_set"),
])
def test_parse_choice_failure(raw, reason):
    label, fail = dtr.parse_choice(raw)
    assert label is None
    assert fail == reason


# ────────────────────────── 3. 统计口径 ──────────────────────────

def _res(sample_id, truth, candidate, fail=None, latency=10.0):
    return {"sample_id": sample_id, "pools": ["injectable"], "truth": truth, "truth_raw": truth,
            "candidate": candidate, "fail_reason": fail,
            "agree": candidate is not None and candidate == truth,
            "latency_ms": latency, "prompt_chars": 100, "completion_chars": 1 if candidate else 0,
            "raw_output": "", "is_plan": truth == "plan",
            "expired_primary": False, "expired_today": truth == "plan"}


def test_summarize_agreement_and_failure_rates():
    results = [
        _res(1, "episodic", "episodic"),
        _res(2, "episodic", "enduring"),
        _res(3, "enduring", "enduring"),
        _res(4, "plan", None, "out_of_set"),
        _res(5, "transient", "transient"),
    ]
    st = dtr.summarize(results)
    assert st["n_total"] == 5
    assert st["n_evaluated"] == 4 and st["n_failed"] == 1
    assert st["format_failure_rate"] == 0.2
    # 总体一致率只算成功产出类别的样本：3/4
    assert st["agreement_overall"] == 0.75
    # 把格式失败计为不一致：3/5
    assert st["agreement_counting_failures"] == 0.6
    # 主指标排除 transient：成功产出类别的 4 条里去掉 transient 1 条 → 3 条（episodic×2 + enduring×1）
    assert st["n_main"] == 3
    assert st["agreement_main"] == round(2 / 3, 4)
    assert st["fail_reasons"] == {"out_of_set": 1}
    assert st["transient_observed"] == {"n": 1, "n_agreed": 1}
    cls = st["by_truth_class"]
    assert cls["episodic"] == {"n": 2, "n_evaluated": 2, "n_agreed": 1, "n_failed": 0, "recall": 0.5}
    assert cls["plan"]["n_evaluated"] == 0 and cls["plan"]["recall"] is None
    assert cls["transient"]["n"] == 1 and cls["transient"]["n_agreed"] == 1
    assert cls["enduring"]["recall"] == 1.0


def test_summarize_empty_and_latency_percentiles():
    st = dtr.summarize([])
    assert st["n_total"] == 0
    assert st["agreement_overall"] is None and st["format_failure_rate"] is None
    assert st["latency_p50_ms"] is None
    lat = [_res(i, "episodic", "episodic", latency=float(i)) for i in range(1, 21)]
    st2 = dtr.summarize(lat)
    assert st2["latency_p50_ms"] == 10.0 and st2["latency_p95_ms"] == 19.0
    assert st2["prompt_chars_total"] == 2000


def test_percentile_nearest_rank():
    assert dtr.percentile([], 50) is None
    assert dtr.percentile([5], 95) == 5
    assert dtr.percentile([1, 2, 3, 4], 50) == 2
    assert dtr.percentile([1, 2, 3, 4], 90) == 4


def test_plan_expiry_slice_two_anchors():
    st = dtr.summarize([_res(1, "plan", "plan"), _res(2, "plan", "plan", latency=1.0)])
    sl = st["plan_expiry_slice"]
    assert sl["plan_total"] == 2
    assert sl["anchor_created_at"] == {"not_expired": 2, "expired": 0}
    assert sl["anchor_today"] == {"not_expired": 0, "expired": 2}


# ────────────────────────── 4. 显示态前缀（format.py 镜像） ──────────────────────────

@pytest.mark.parametrize("label,expired,status,tag", [
    ("plan", False, "active", "［计划］"),
    ("plan", True, "active", "［旧安排·已过期］"),
    ("episodic", False, "active", "［往事］"),
    ("transient", False, "active", "［当时状态］"),
    ("enduring", False, "active", ""),
    # status 改写覆盖一切（format.py:97-98）
    ("plan", False, "superseded", "［往事/已过时］"),
    ("enduring", False, "stale", "［往事/已过时］"),
    ("episodic", True, "EXPIRED", "［往事/已过时］"),
])
def test_display_tag_matches_production_prefix_table(label, expired, status, tag):
    assert dtr.display_tag(label, expired, status) == tag


# ────────────────────────── 5. 只读纪律 ──────────────────────────

def test_connect_ro_refuses_writes(replay_db):
    conn = dtr.connect_ro(replay_db)
    try:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE memories SET status='active' WHERE id=1")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM memories WHERE id=1")
    finally:
        conn.close()


def test_connect_ro_missing_db_exits(tmp_path):
    with pytest.raises(SystemExit):
        dtr.connect_ro(tmp_path / "nope.db")


# ────────────────────────── 6. 直载规则 = 生产规则 ──────────────────────────

def test_direct_loaded_rules_equal_production_module(exported):
    """回放器直载的 tense 必须与生产模块逐条同判（否则真值口径就漂了）。"""
    from app.memory.tense import classify_tense as prod_cls, is_happened_source as prod_happened
    rules = dtr.load_rules()
    assert rules.__name__ == "_ambrace_replay_tense"
    for s in exported[0]:
        mem = dtr.row_to_mem(s)
        assert rules.classify_tense(mem) == prod_cls(mem)
        assert rules.is_happened_source(mem) == prod_happened(mem)


def test_load_rules_does_not_shadow_real_packages():
    """直载不得把 app / app.memory 塞成空壳包（会遮蔽真实包、污染同进程其他使用方）。"""
    dtr.load_rules()
    for name in ("_ambrace_replay_tense", "_ambrace_replay_meta_guard"):
        assert name in sys.modules
    if "app.memory" in sys.modules:
        # 真实包在则必须还是真实包（不是本脚本造的空壳）
        assert getattr(sys.modules["app.memory"], "__file__", None)


# ────────────────────────── 7. 导出契约 ──────────────────────────

def test_export_samples_pools_and_truths(exported):
    samples, stats = exported
    by_id = {s["sample_id"]: s for s in samples}
    assert set(by_id) == {1, 2, 3, 4, 5}          # 6=stale 非 plan 不入样；7=空文本被丢弃
    assert stats["skipped_empty_text"] == 1
    assert stats["n_samples"] == 5
    assert stats["n_pools"] == {"injectable": 4, "plan_all": 2, "plan_not_injectable": 1}
    assert by_id[1]["truth_rule"] == "episodic"        # insight = 天然已发生来源短路
    assert by_id[1]["truth_rule_raw"] == "episodic"
    assert by_id[2]["truth_rule"] == "plan"
    assert by_id[2]["truth_display_tag"] == "［计划］"
    assert by_id[2]["is_plan_expired_rule"] is False   # now := created_at 还原当时语境
    assert by_id[2]["is_plan_expired_at_today"] is True    # 副口径：以今天为 now 已过期
    assert by_id[2]["truth_display_tag_at_today"] == "［旧安排·已过期］"
    assert by_id[3]["truth_rule"] == "enduring"
    assert by_id[4]["truth_rule"] == "transient"
    assert by_id[5]["pools"] == ["plan_all"]           # 归档 plan 只进 plan 池
    assert by_id[2]["now_date"] == "2026-09-10" and by_id[2]["record_date"] == "2026-09-10"
    assert by_id[2]["state_text"].startswith("记录日期：2026-09-10\n现在：2026-09-10\n")
    # 真值取 _tcls 生效口径（拍板），两列都要留
    assert stats["truth_rule_dist"] == {"enduring": 1, "episodic": 1, "plan": 2, "transient": 1}
    assert stats["truth_rule_raw_dist"]["episodic"] == 1
    assert stats["state_len"]["max"] <= dtr.STATE_MAX_CHARS
    assert stats["truncation_flip_counts"] == {80: 0, 120: 0, 150: 0, 240: 0}


def test_export_writes_and_reads_back_jsonl(exported, tmp_path):
    samples, _ = exported
    path = tmp_path / "tense_samples_20260925.jsonl"
    dtr.write_jsonl(samples, path)
    back = dtr.read_jsonl(path)
    assert len(back) == len(samples)
    assert back[0] == samples[0]                       # 全字段无损往返（含中文 state）
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["truth_source"] == "effective_tcls"


def test_find_latest_samples_picks_newest(tmp_path):
    (tmp_path / "tense_samples_20260924.jsonl").write_text("{}", encoding="utf-8")
    (tmp_path / "tense_samples_20260925.jsonl").write_text("{}", encoding="utf-8")
    (tmp_path / "other.jsonl").write_text("{}", encoding="utf-8")
    assert dtr.find_latest_samples(tmp_path).name == "tense_samples_20260925.jsonl"
    assert dtr.find_latest_samples(tmp_path / "empty") is None


# ────────────────────────── 8. 回放：rule 自检 = 100% ──────────────────────────

def test_replay_rule_self_check_is_100_percent(exported, tmp_path):
    samples, _ = exported
    path = tmp_path / "tense_samples_20260925.jsonl"
    dtr.write_jsonl(samples, path)
    results = dtr.replay_rule(dtr.read_jsonl(path))
    st = dtr.summarize(results)
    assert st["n_total"] == len(samples)
    assert st["n_failed"] == 0 and st["format_failure_rate"] == 0.0
    assert st["agreement_overall"] == 1.0              # 管线自检必须满分
    assert st["agreement_main"] == 1.0
    assert all(c["recall"] in (1.0, None) for c in st["by_truth_class"].values())
    report = dtr.render_report("rule", results, {"all": st},
                               samples_path=path, db_path=":memory:", extra_notes=["自检。"])
    assert "## 7. 自检差异" in report and "无（一致率 100%）" in report
    assert "本后端零 LLM 调用，token 消耗为 0" in report


def test_replay_rule_flags_pipeline_corruption(exported):
    """候选与真值同源：若导出侧真值被串改，自检必须掉下 100%（证明这环节不是假满分）。"""
    tampered = [dict(s) for s in exported[0]]
    tampered[0]["truth_rule"] = "enduring" if tampered[0]["truth_rule"] != "enduring" else "episodic"
    st = dtr.summarize(dtr.replay_rule(tampered))
    assert st["agreement_overall"] == round((st["n_evaluated"] - 1) / st["n_evaluated"], 4)
    assert st["agreement_overall"] < 1.0


# ────────────────────────── 9. 回放：llm 闸口 ──────────────────────────

def test_llm_backend_requires_allow_llm(exported):
    samples, _ = exported
    with pytest.raises(SystemExit, match="--allow-llm"):
        dtr.replay_llm(samples, 5, allow_llm=False)


def test_cli_rejects_llm_without_allow_llm(exported, tmp_path, capsys):
    """CLI 层也要拦住：且在打开样本文件/建连接之前就退出。"""
    samples, _ = exported
    path = tmp_path / "tense_samples_20260925.jsonl"
    dtr.write_jsonl(samples, path)
    with pytest.raises(SystemExit) as ei:
        dtr.main(["--replay", "--backend", "llm", "--samples", str(path)])
    assert "--allow-llm" in str(ei.value)
    assert path.read_text(encoding="utf-8").count("\n") == len(samples)   # 样本文件未被改动


def test_cli_still_requires_backend(exported, tmp_path):
    samples, _ = exported
    path = tmp_path / "tense_samples_20260925.jsonl"
    dtr.write_jsonl(samples, path)
    with pytest.raises(SystemExit, match="--backend"):
        dtr.main(["--replay", "--samples", str(path)])


def test_llm_limit_hard_cap(exported):
    samples, _ = exported
    with pytest.raises(SystemExit, match="硬上限"):
        dtr.replay_llm(samples, dtr.LLM_LIMIT_HARD_MAX + 1, allow_llm=True)


def test_llm_prompt_is_single_token_shape():
    """薄约定：4 个单 token 编号 + 输出约束；候选集与 TENSE_OPTIONS 一一对应。"""
    assert list(dtr.CHOICE_TO_LABEL.values()) == list(dtr.TENSE_OPTIONS)
    assert dtr.LLM_LIMIT_DEFAULT <= dtr.LLM_LIMIT_HARD_MAX == 200
    for digit in ("1", "2", "3", "4"):
        assert dtr.parse_choice(digit)[0] in dtr.TENSE_OPTIONS
