# -*- coding: utf-8 -*-
"""A4 批 8 M0 · ``backend/scripts/extension_audit.py`` 定向用例（只读对账读数）。

覆盖任务单要求的四类：
  ①权限名漂移检测：按文档**行内实现状态标记**分桶（「实现漂移 / 计划项 / 状态未标注待办」），
    另含真实文档的契约守卫（每条权限都带标记、权限名字面值零改动）
  ②只读性（一写就抛的假连接 + SQL 文本闸门 + 标识符注入）
  ③报告字段齐备（四块 + 自检，每条结论带文件:行号）
  ④异常隔离（单块炸不影响其余块，且失败被如实登记而非静默填 0）
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "backend" / "scripts" / "extension_audit.py"


# 公开仓快照（origin/main）刻意**不含 `docs/`**（只留 `docs/changelog.md`）⇒ 依赖内部契约文档的
# 「真实仓库读数」类用例在公开 CI 上必然失败（脚本读不到 `docs/extension-contract.md`）。统一用这一个判据：
# 内部仓库（docs 齐备）照常真跑，公开快照自动 skip（CI 不红）。
_INTERNAL_DOCS = REPO_ROOT / "docs"
requires_internal_docs = pytest.mark.skipif(
    not _INTERNAL_DOCS.is_dir(),
    reason="内部文档不在公开快照内（公开仓 CI 自动跳过）",
)



def _load(name: str = "extension_audit_m0"):
    spec = importlib.util.spec_from_file_location(name, str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ea = _load()


# ────────────────────────── ① 漂移检测（合成样本） ──────────────────────────

def test_drift_splits_implementation_drift_from_planned_items():
    """「文档有、代码无」按文档自己声明的状态分桶：说已落地=真漂移(high)，说预告/未实现=计划项(info)。"""
    entries = [
        {"permission": "write_memory", "doc_line": 98, "status": "implemented", "status_label": "已实现", "is_pattern": False},
        {"permission": "douyin_publish", "doc_line": 111, "status": "implemented", "status_label": "已实现", "is_pattern": False},
        {"permission": "net:outbound", "doc_line": 120, "status": "preview", "status_label": "仅预告", "is_pattern": False},
        {"permission": "legacy_perm", "doc_line": 112, "status": "planned", "status_label": "未实现", "is_pattern": False},
    ]
    out = ea.diff_permission_drift(entries, ["write_memory"])
    drift = {d["permission"]: d for d in out["implementation_drift"]}
    planned = {d["permission"]: d for d in out["planned_items"]}
    assert set(drift) == {"douyin_publish"}
    assert set(planned) == {"net:outbound", "legacy_perm"}
    assert drift["douyin_publish"]["severity"] == "high"
    assert planned["net:outbound"]["severity"] == "info"
    assert out["todo_items"] == []
    assert out["counts"]["implementation_drift"] == 1
    assert out["counts"]["planned_items"] == 2
    assert out["counts"]["todo_items"] == 0
    assert out["counts"]["doc_and_code_match"] == 1  # write_memory 两边都有
    # 三桶互斥且合计＝全部「文档有代码无」行
    assert (out["counts"]["implementation_drift"] + out["counts"]["planned_items"]
            + out["counts"]["todo_items"]) == 3


def test_drift_code_has_doc_lacks_detected():
    """「代码有、文档无」必须单独成列（不能被三个桶吸收）。"""
    entries = [{"permission": "write_memory", "doc_line": 98, "status": "implemented",
                "status_label": "已实现", "is_pattern": False}]
    out = ea.diff_permission_drift(entries, ["write_memory", "zed:read", "aaa_write"])
    assert [d["permission"] for d in out["drift_code_has_doc_lacks"]] == ["aaa_write", "zed:read"]
    assert out["counts"]["code_has_doc_lacks"] == 2
    assert out["counts"]["implementation_drift"] == 0


def test_drift_wildcard_covers_dynamic_permission_names():
    """文档写 ``device:*`` ⇒ 动态 device 权限名不计为漂移，但要在 wildcard_notes 里露出覆盖数与状态。"""
    entries = [
        {"permission": "device:*", "doc_line": 122, "status": "preview", "status_label": "仅预告", "is_pattern": True},
        {"permission": "send_message", "doc_line": 99, "status": "implemented",
         "status_label": "已实现", "is_pattern": False},
    ]
    code = ["send_message", "device:battery:read", "device:action_tap:write"]
    out = ea.diff_permission_drift(entries, code)
    assert out["drift_code_has_doc_lacks"] == []
    assert out["wildcard_notes"][0]["pattern"] == "device:*"
    assert out["wildcard_notes"][0]["covers_code_names"] == 2
    assert out["wildcard_notes"][0]["doc_status"] == "preview"
    # 通配条目本身不占「文档有代码无」的任何一桶
    assert out["counts"]["implementation_drift"] + out["counts"]["planned_items"] + out["counts"]["todo_items"] == 0


def test_extract_doc_entries_reads_per_line_status_markers():
    """N3 口径：状态是**行内标记**（已实现/仅预告/未实现），不再靠「预告块」上下文推断。"""
    doc = "\n".join([
        "## 3. 权限模型",
        "- `write_memory` — `已实现`：插件写记忆的唯一窄口。",
        "- `ghost_perm` — `已实现`：文档说已落地但代码没有。",
        "- `net:outbound` — `仅预告`：外部动作类权限，落地以 X4 批次为准。",
        "- `fs:limited` — `仅预告`：外部动作类权限，落地以 X4 批次为准。",
        "- `douyin_publish` — `未实现`：X4 之前的历史写法。",
        "- `device:*` — `仅预告`：设备动作面的通配写法。",
        "",
        "## 4. SDK 面",
        "- `not_in_section_perm` — `已实现`：§4 不进口径。",
    ])
    entries = ea.extract_doc_permission_entries(ea.doc_section_lines(doc, "## 3"))
    by_name = {e["permission"]: e for e in entries}
    assert set(by_name) == {"write_memory", "ghost_perm", "net:outbound", "fs:limited",
                            "douyin_publish", "device:*"}
    assert by_name["write_memory"]["status"] == "implemented"
    assert by_name["net:outbound"]["status"] == "preview"
    assert by_name["douyin_publish"]["status"] == "planned"
    assert by_name["device:*"]["is_pattern"] is True
    assert by_name["write_memory"]["is_pattern"] is False
    assert by_name["net:outbound"]["status_label"] == "仅预告"
    assert "not_in_section_perm" not in by_name  # §4 不进口径
    # 行号必须是文档内的真实行号（供报告逐条溯源）
    assert by_name["net:outbound"]["doc_line"] == 4


def test_missing_status_marker_falls_back_to_unknown_and_lands_in_todo_items():
    """**缺标记不按「没写就是已实现」兜底**：落 unknown ⇒ 计入待办（这正是本单要抓的漂移）。"""
    doc = "\n".join([
        "## 3. 权限模型",
        "- `write_memory`：这一行忘了写状态标记。",
        "- `ghost_perm` — `已实现`：文档说已落地但代码没有。",
        "",
        "## 4. SDK 面",
    ])
    entries = ea.extract_doc_permission_entries(ea.doc_section_lines(doc, "## 3"))
    by_name = {e["permission"]: e for e in entries}
    assert by_name["write_memory"]["status"] == ea.STATUS_UNKNOWN
    assert by_name["write_memory"]["status_label"] == ea.STATUS_UNKNOWN_LABEL
    out = ea.diff_permission_drift(entries, [])
    assert [d["permission"] for d in out["todo_items"]] == ["write_memory"]
    assert out["todo_items"][0]["severity"] == "medium"
    # 未标注 ≠ 实现漂移：它是「文档没说清」，要单独待办；同文档里真·说已落地而代码没有的另计
    assert [d["permission"] for d in out["implementation_drift"]] == ["ghost_perm"]
    assert out["counts"]["implementation_drift"] == 1
    assert out["counts"]["todo_items"] == 1
    assert out["counts"]["status_unknown_entries"] == 1


def test_conflicting_markers_on_one_line_are_flagged_not_guessed():
    """同一行出现两个不同状态标记 ⇒ 判冲突、按未标注处理，并把冲突标记带回报告。"""
    doc = "\n".join([
        "## 3. 权限模型",
        "- `weird_perm` — `已实现`：这里又补了句 `仅预告`，到底哪个算数？",
        "",
        "## 4. SDK 面",
    ])
    entries = ea.extract_doc_permission_entries(ea.doc_section_lines(doc, "## 3"))
    assert entries[0]["status"] == ea.STATUS_UNKNOWN
    assert sorted(entries[0]["status_conflict"]) == ["仅预告", "已实现"]
    out = ea.diff_permission_drift(entries, [])
    assert [d["permission"] for d in out["todo_items"]] == ["weird_perm"]
    assert "已实现" in out["todo_items"][0]["note"] and "仅预告" in out["todo_items"][0]["note"]


def test_same_permission_with_two_statuses_is_reported_as_conflict():
    """同一权限名在文档里出现两种状态 ⇒ 名单级冲突登记（不静默取一个）。"""
    entries = [
        {"permission": "write_memory", "doc_line": 98, "status": "implemented", "is_pattern": False},
        {"permission": "write_memory", "doc_line": 120, "status": "preview", "is_pattern": False},
    ]
    out = ea.diff_permission_drift(entries, [])
    assert out["status_conflicts"] == [{
        "permission": "write_memory",
        "statuses": ["implemented", "preview"],
        "handling": "按最保守的一档计入（unknown → 待办）",
    }]


def test_status_legend_and_markers_exposed_for_consumers():
    """状态图例与标记词要随读数一起给出（报告/JSON 消费方不必各自硬编码）。"""
    entries = [{"permission": "net:outbound", "doc_line": 120, "status": "preview", "is_pattern": False}]
    out = ea.diff_permission_drift(entries, [])
    assert out["status_markers"] == {"已实现": "implemented", "仅预告": "preview",
                                     "未实现": "planned", "规划中": "planned"}
    assert set(out["status_legend"]) == {"implemented", "preview", "planned", "unknown"}
    assert out["status_legend"]["implemented"] != out["status_legend"]["preview"]


# ────────────────────────── ② 只读性 ──────────────────────────

class _Rows:
    """最小游标替身：脚本对 PRAGMA 用迭代、对 SELECT length() 用 fetchall()，两者都要有。"""

    def __init__(self, rows: list[tuple]):
        self._rows = list(rows)

    def __iter__(self):
        return iter(self._rows)

    def fetchall(self) -> list[tuple]:
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class ReadOnlyFakeConn:
    """一写就抛的假连接：只放行 SELECT/PRAGMA/WITH/EXPLAIN 文本，其余直接 raise。

    按 SQL 形态给回 canned rows（PRAGMA table_info 与 sqlite_master 的列位置不同，
    统一回一份会让脚本内部 r[1] 越界，那是在测假件而不是测只读性）。
    """

    def __init__(self, rows=None):
        self.rows = rows
        self.seen: list[str] = []

    def _respond(self, sql: str) -> list[tuple]:
        if self.rows is not None:
            return list(self.rows)
        text = sql.lower()
        if "sqlite_master" in text:
            return [("proactive_message_logs",), ("llm_usage",)]
        if "table_info" in text:
            return [("0", "id", "INTEGER"), ("1", "content", "TEXT")]
        if "index_list" in text:
            return [("0", "sqlite_autoindex_llm_usage_1", "1")]
        if "length(" in text:
            return [(10,), (500,)]
        return [(1,)]

    def execute(self, sql, params=()):
        text = str(sql).strip().lower()
        if not text.startswith(("select", "pragma", "with", "explain")):
            raise AssertionError(f"假连接被要求执行写操作: {sql!r}")
        self.seen.append(str(sql))
        return _Rows(self._respond(str(sql)))

    def close(self):
        pass


@pytest.mark.parametrize("bad", [
    "UPDATE llm_usage SET task='x'",
    "INSERT INTO t VALUES (1)",
    "DELETE FROM t",
    "DROP TABLE t",
    "ALTER TABLE t ADD COLUMN c TEXT",
    "SELECT 1; DROP TABLE t",
    "PRAGMA journal_mode=WAL",
    "PRAGMA user_version=9",
])
def test_readonly_sql_rejects_every_write_shape(bad):
    with pytest.raises(ea.ReadOnlyViolation):
        ea.readonly_sql(bad)


@pytest.mark.parametrize("good", [
    "SELECT name FROM sqlite_master WHERE type='table'",
    'PRAGMA table_info("llm_usage")',
    "PRAGMA query_only=ON",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "EXPLAIN QUERY PLAN SELECT 1",
])
def test_readonly_sql_allows_read_shapes(good):
    assert ea.readonly_sql(good) == good.strip().rstrip(";").strip()


def test_query_gateway_blocks_write_before_reaching_conn():
    """闸门在 conn 之前：写 SQL 既抛错，也绝不会被送到连接。"""
    conn = ReadOnlyFakeConn()
    with pytest.raises(ea.ReadOnlyViolation):
        ea.query(conn, "UPDATE proactive_message_logs SET content=''")
    assert conn.seen == []


def test_measure_functions_only_issue_read_sql():
    """两个实测函数走完后，假连接收到的每条 SQL 都必须过只读闸门（一写就抛）。"""
    conn = ReadOnlyFakeConn()
    assert ea.measure_notify_body_length(conn)["ok"] is True
    assert ea.measure_cost_schema(conn)["ok"] is True
    assert conn.seen, "实测函数不应一条 SQL 都不发"
    for sql in conn.seen:
        ea.readonly_sql(sql)  # 不过闸门即抛


def test_safe_ident_blocks_identifier_injection():
    conn = ReadOnlyFakeConn()
    with pytest.raises(ValueError):
        ea.measure_notify_body_length(conn, table='x"; DROP TABLE t; --')
    assert conn.seen == []


def test_measure_notify_body_length_percentiles_on_memory_db():
    """内存库造长度分布，验证 P50/P90/P99 与两个占比、以及 500 截顶占比（只读阶段）。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE proactive_message_logs (content TEXT)")
    lengths = [10, 20, 30, 45, 60, 120, 500, 500]
    conn.executemany("INSERT INTO proactive_message_logs VALUES (?)", [("x" * n,) for n in lengths])
    conn.commit()
    out = ea.measure_notify_body_length(conn)
    assert out["ok"] is True and out["rows"] == 8
    assert out["percentiles"]["p50"] == 45
    assert out["max"] == 500
    assert out["ceiling"] == ea.PROACTIVE_LOG_CEILING
    assert out["share_gt_preview"] == 0.5        # 60/120/500/500 四条 >50
    assert out["share_gt_target"] == 0.625       # 再加 45，共五条 >40
    assert out["share_at_ceiling"] == 0.25       # 两条 =500 ⇒ 截顶证据
    conn.close()


def test_measure_missing_table_reports_error_not_crash():
    conn = sqlite3.connect(":memory:")
    out = ea.measure_notify_body_length(conn)
    assert out["ok"] is False and "表不存在" in out["error"]
    conn.close()


# ────────────────────────── ③ 真实仓库读数 + 报告字段齐备 ──────────────────────────

@pytest.fixture(scope="module")
def audit_data():
    if not _INTERNAL_DOCS.is_dir():
        pytest.skip("内部文档不在公开快照内（公开仓 CI 自动跳过）")
    return ea.run_audit()


@requires_internal_docs
def test_real_doc_section3_every_permission_line_carries_a_status_marker():
    """**契约守卫**：文档 §3 每条权限都得带实现状态标记。

    这条同时兜住两类回潮：①有人加了权限却忘了写标记（会落 unknown、被脚本当待办）；
    ②有人在散文里误用反引号包了非权限的下划线标识符（会被当权限条目 ⇒ 同样是 unknown）。
    """
    text = (REPO_ROOT / ea.CONTRACT_DOC).read_text(encoding="utf-8")
    entries = ea.extract_doc_permission_entries(ea.doc_section_lines(text, "## 3"))
    assert entries, "§3 一条权限都没解析出来＝解析口径坏了"
    unknown = [e for e in entries if e["status"] == ea.STATUS_UNKNOWN]
    assert unknown == [], f"§3 存在未标注实现状态的条目：{[(e['permission'], e['doc_line']) for e in unknown]}"
    assert all(e["status_label"] in ea.STATUS_MARKERS for e in entries)
    assert not [e for e in entries if e["status_conflict"]], "§3 存在一行多个状态标记的冲突行"


@requires_internal_docs
def test_real_doc_permission_names_are_unchanged_by_the_marker_convention():
    """**权限名字面值一个都不许动**（改名会让已装插件的 consent 记录整体失配）。"""
    text = (REPO_ROOT / ea.CONTRACT_DOC).read_text(encoding="utf-8")
    entries = ea.extract_doc_permission_entries(ea.doc_section_lines(text, "## 3"))
    named = {e["permission"] for e in entries if not e["is_pattern"]}
    patterns = {e["permission"] for e in entries if e["is_pattern"]}
    assert named == {
        "write_memory", "send_message",
        "persona:read", "memory:read", "life:read", "relationship:read", "proactive:read",
        "douyin_publish",
        "channel:read", "channel:publish", "net:outbound", "fs:limited",
    }
    assert patterns == {"device:*"}


def test_real_repo_drift_readings_are_self_consistent(audit_data):
    b2 = audit_data["sections"]["B2_doc_code_drift"]
    assert b2["ok"] is True
    counts = b2["counts"]
    assert counts["doc_entry_count"] == b2["doc_entry_count"]
    assert counts["implementation_drift"] == len(b2["implementation_drift"])
    assert counts["planned_items"] == len(b2["planned_items"])
    assert counts["todo_items"] == len(b2["todo_items"])
    # 三桶互斥：合计＝全部「文档有代码无」行
    assert (counts["implementation_drift"] + counts["planned_items"] + counts["todo_items"]
            == len(b2["implementation_drift"]) + len(b2["planned_items"]) + len(b2["todo_items"]))
    # §3 已按 N3 标注完毕 ⇒ 没有任何条目处于「状态未标注」
    assert counts["status_unknown_entries"] == 0 and b2["todo_items"] == []
    assert b2["status_conflicts"] == []
    rows = [r for bucket in ea.DRIFT_BUCKETS for r in b2[bucket]]
    for item in rows:
        assert item["doc_line"] > 0
        assert ":" in item["code_reference_evidence"] or item["code_reference_evidence"] == "（0 命中）"
        # 残留读数只认运行时代码，且不得被本审计自身污染（脚本/用例里都有这些名字的字面量）
        assert "extension_audit" not in item["code_reference_evidence"]
        assert item["code_reference_hits"] == 0 or item["code_reference_evidence"].startswith(
            ("backend/app/", "flutter_app/lib/"))


def test_real_repo_has_zero_implementation_drift_after_n3(audit_data):
    """N3 订正后「文档说已落地、代码无」必须为空——文档不再承诺代码没有的权限。"""
    b2 = audit_data["sections"]["B2_doc_code_drift"]
    assert b2["implementation_drift"] == []
    assert b2["counts"]["implementation_drift"] == 0
    # 但代码也没有越出文档承诺面（device:* 通配覆盖 11 条动态权限名）
    assert b2["counts"]["code_has_doc_lacks"] == 0
    assert b2["wildcard_notes"][0]["pattern"] == "device:*"
    assert b2["wildcard_notes"][0]["covers_code_names"] == 11


def test_douyin_publish_is_a_planned_item_with_residual_reference_todo(audit_data):
    """douyin_publish 的真相：文档已订正为「未实现」，但 Flutter 市场页仍渲染它 ⇒ 进残留待办。"""
    b2 = audit_data["sections"]["B2_doc_code_drift"]
    row = next(d for d in b2["planned_items"] if d["permission"] == "douyin_publish")
    assert row["doc_status"] == "planned" and row["doc_status_label"] == "未实现"
    assert row["code_reference_hits"] >= 1
    assert row["code_reference_evidence"].startswith("flutter_app/lib/")
    todo = next(t for t in b2["residual_reference_todo"] if t["permission"] == "douyin_publish")
    assert todo["bucket"] == "planned_items" and todo["todo"]


def test_real_repo_preview_items_keep_unified_wording(audit_data):
    """四条「仅预告」：同一句式、状态一致、代码无属预期。"""
    b2 = audit_data["sections"]["B2_doc_code_drift"]
    preview = {d["permission"]: d for d in b2["planned_items"] if d["doc_status"] == "preview"}
    assert set(preview) == {"channel:read", "channel:publish", "net:outbound", "fs:limited"}
    for item in preview.values():
        assert item["doc_status_label"] == "仅预告"
        assert item["severity"] == "info"
        assert item["code_reference_hits"] == 0


def test_real_repo_negative_assertions_carry_hit_counts(audit_data):
    c1 = audit_data["sections"]["C1_form_surface"]
    assert c1["ok"] is True and c1["all_absent"] is True
    assert all(a["hit_count"] == 0 for a in c1["upstream_wording_absent"])
    # 块 C（2026-09-30）已把两份逐字拷贝收口成单一真源 ⇒ 字面重复必须为 0；
    # 同时两个收敛点都必须改调同一入口（防止有人偷偷写回第二份拷贝）。
    copies = c1["existing_length_gates"]["notify_preview_50_copies"]
    assert copies["count"] == 0
    single = c1["existing_length_gates"]["notify_preview_single_source"]
    assert single["ok"] is True
    assert single["definition_count"] == 1
    assert {h["file"] for h in single["call_sites"]} == set(ea.NOTIFY_CONVERGENCE_FILES)


def test_real_repo_cost_constraints_present(audit_data):
    d = audit_data["sections"]["D_cost_panel"]
    assert d["ok"] is True
    assert d["estimated_column_present"] is False          # D-C2 的前提
    assert d["get_llm_usage_full_load"]["select_full_orm_lines"], "全表载入点必须被定位到"
    assert d["usage_report_path"]["pure_sql_aggregation"] is False  # 无 GROUP BY ⇒ 设计口径需订正
    assert d["price_table"]["empty"] is True                # D-C4：价目表仍为空
    ids = {c["id"] for c in d["constraints"]}
    assert {"D-C1", "D-C2", "D-C3", "D-C4", "D-C5"} == ids
    for c in d["constraints"]:
        assert c["statement"] and c["verify_by"] and c["evidence"]


def test_report_contains_four_sections_and_self_check(audit_data):
    text = ea.render_report(audit_data)
    for heading in ("## 1 B-2", "## 2 B-3", "## 3 C-1", "## 4 D-1/D-2", "## 5 自检"):
        assert heading in text
    assert "dry_run_readonly" in text
    assert "未连库" in text                      # 默认模式必须写明没连库
    assert "（每条结论带" not in text            # 不留模板占位
    assert "M0 验收姿态" in text                 # 收尾的零行为声明必须在


def test_report_separates_implementation_drift_from_planned_items(audit_data):
    """报告必须把「实现漂移 / 计划项 / 状态未标注 / 残留待办」分栏写，而不是混成一句「有漂移」。"""
    text = ea.render_report(audit_data)
    for heading in ("1.1 实现漂移", "1.2 计划项", "1.3 状态未标注", "1.4 残留引用待办", "1.7 处置登记"):
        assert heading in text
    assert "口径版本：**N3**" in text
    # 收尾声明必须写清：改的是文档措辞，权限名与 VALID_PERMISSIONS 一字未改
    assert "权限名字面值一字未改" in text
    assert "VALID_PERMISSIONS` 一字未改" in text


def test_self_check_reports_bucket_evidence_coverage(audit_data):
    cov = audit_data["sections"]["SELF_check"]["evidence_coverage"]
    b2 = audit_data["sections"]["B2_doc_code_drift"]
    assert cov["B2_rows"] == sum(len(b2[bucket]) for bucket in ea.DRIFT_BUCKETS)
    assert cov["B2_rows_with_evidence"] == cov["B2_rows"]
    assert cov["B2_implementation_drift"] == 0
    assert cov["B2_residual_reference_todo"] == len(b2["residual_reference_todo"])
    assert cov["B2_status_unknown_entries"] == 0


def test_report_lines_carry_traceable_evidence(audit_data):
    """报告里出现的具体位置必须是「文件:行号」形态（可点进去核）。"""
    text = ea.render_report(audit_data)
    assert "backend/app/plugins/registry.py:" in text
    assert "docs/extension-contract.md:" in text
    assert "backend/app/application/system.py:" in text


def test_script_self_audit_has_no_write_sql_literals():
    """脚本自身所有 SQL 字面量都过只读闸门（改脚本时若引入写语句，这条先红）。"""
    literals = ea.collect_sql_literals(SCRIPT)
    assert literals, "脚本里应当有 SQL 字面量（连一条都没有说明取数路径被删空）"
    bad = []
    for sql in literals:
        try:
            ea.readonly_sql(sql)
        except ea.ReadOnlyViolation as exc:
            bad.append(f"{sql[:40]} → {exc}")
    assert bad == []


# ────────────────────────── ④ 异常隔离 / 落盘 ──────────────────────────

@requires_internal_docs
def test_section_failure_is_isolated_and_reported(monkeypatch):
    """单块抛异常 ⇒ 该块登记为不可用，其余三块照常出读数（M0 绝不静默编数）。"""
    def boom(*_a, **_kw):
        raise RuntimeError("模拟读数失败")

    monkeypatch.setattr(ea, "audit_form_surface", boom)
    data = ea.run_audit()
    c1 = data["sections"]["C1_form_surface"]
    assert c1["ok"] is False and "模拟读数失败" in c1["error"]
    assert data["sections"]["B2_doc_code_drift"]["ok"] is True
    assert data["sections"]["D_cost_panel"]["ok"] is True
    assert "C1_form_surface" in data["sections"]["SELF_check"]["failed_sections"]
    text = ea.render_report(data)
    assert "本块读数失败" in text


def test_run_audit_does_not_touch_db_by_default(audit_data):
    meta = audit_data["meta"]
    assert meta["db_connected"] is False
    assert meta["git_write_commands_executed"] == 0
    assert meta["files_written_in_repo"] == 0
    db = audit_data["sections"]["DB_readings"]
    assert db["status"] == "skipped" and "不编数" in ea.render_report(audit_data)


@requires_internal_docs
def test_main_writes_report_only_to_given_path(tmp_path, capsys):
    """``--report`` 指到临时目录：报告落盘、内容齐备，且不返回失败码。"""
    target = tmp_path / "sub" / "m0.md"
    rc = ea.main(["--report", str(target)])
    assert rc == 0
    assert target.is_file()
    text = target.read_text(encoding="utf-8")
    assert "只读对账读数" in text and "## 5 自检" in text
    assert "[report] 落盘" in capsys.readouterr().out


def test_app_db_flag_on_missing_file_is_reported_not_fatal(tmp_path, capsys):
    data = ea.run_audit(app_db=tmp_path / "nope.db")
    assert data["sections"]["DB_readings"]["status"] == "error"
    assert data["meta"]["db_connected"] is False
    assert "库文件不存在" in data["sections"]["DB_readings"]["reason"]
    capsys.readouterr()
