# -*- coding: utf-8 -*-
"""A4 批 8 M0 · ``backend/scripts/extension_audit.py`` 定向用例（只读对账读数）。

覆盖任务单要求的四类：
  ①权限名漂移检测（「文档有代码无」与「代码有文档无」两种样本）
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


def _load(name: str = "extension_audit_m0"):
    spec = importlib.util.spec_from_file_location(name, str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ea = _load()


# ────────────────────────── ① 漂移检测（合成样本） ──────────────────────────

def test_drift_doc_has_code_lacks_asserted_and_preview():
    """「文档有、代码无」两条都要检出，且区分严重度（声称已落地 > 仅预告）。"""
    entries = [
        {"permission": "write_memory", "doc_line": 82, "kind": "asserted"},
        {"permission": "douyin_publish", "doc_line": 82, "kind": "asserted"},
        {"permission": "net:outbound", "doc_line": 89, "kind": "preview"},
    ]
    out = ea.diff_permission_drift(entries, ["write_memory"])
    names = {d["permission"]: d for d in out["drift_doc_has_code_lacks"]}
    assert set(names) == {"douyin_publish", "net:outbound"}
    assert names["douyin_publish"]["severity"] == "high"
    assert names["net:outbound"]["severity"] == "medium"
    assert out["counts"]["doc_has_code_lacks"] == 2
    assert out["counts"]["doc_has_code_lacks_asserted"] == 1
    assert out["counts"]["doc_has_code_lacks_preview"] == 1
    assert out["counts"]["both_ok"] == 1  # write_memory 两边都有


def test_drift_code_has_doc_lacks_detected():
    """「代码有、文档无」必须单独成列（不能被 doc_only 吸收）。"""
    entries = [{"permission": "write_memory", "doc_line": 82, "kind": "asserted"}]
    out = ea.diff_permission_drift(entries, ["write_memory", "zed:read", "aaa_write"])
    assert [d["permission"] for d in out["drift_code_has_doc_lacks"]] == ["aaa_write", "zed:read"]
    assert out["counts"]["code_has_doc_lacks"] == 2
    assert out["counts"]["doc_has_code_lacks"] == 0


def test_drift_wildcard_covers_dynamic_permission_names():
    """文档写 ``device:*`` ⇒ 动态 device 权限名不计为漂移，但要在 wildcard_notes 里露出覆盖数。"""
    entries = [
        {"permission": "device:*", "doc_line": 89, "kind": "pattern"},
        {"permission": "send_message", "doc_line": 82, "kind": "asserted"},
    ]
    code = ["send_message", "device:battery:read", "device:action_tap:write"]
    out = ea.diff_permission_drift(entries, code)
    assert out["drift_code_has_doc_lacks"] == []
    assert out["wildcard_notes"][0]["pattern"] == "device:*"
    assert out["wildcard_notes"][0]["covers_code_names"] == 2


def test_extract_doc_entries_splits_asserted_from_preview_block():
    """预告块的判定：命中「仅预告/目标枚举」后的行算 preview，空行复位。"""
    doc = "\n".join([
        "## 3. 权限模型",
        "**X4 已落地**：写权限 `write_memory` / `ghost_perm`",
        "",
        "X4 目标三类枚举（此处仅预告）：",
        "- **外部动作**：`net:outbound` `device:*`",
        "",
        "## 4. SDK 面",
        "`not_in_section_perm`",
    ])
    entries = ea.extract_doc_permission_entries(ea.doc_section_lines(doc, "## 3"))
    by_name = {e["permission"]: e for e in entries}
    assert by_name["write_memory"]["kind"] == "asserted"
    assert by_name["ghost_perm"]["kind"] == "asserted"
    assert by_name["net:outbound"]["kind"] == "preview"
    assert by_name["device:*"]["kind"] == "pattern"
    assert "not_in_section_perm" not in by_name  # §4 不进口径
    # 行号必须是文档内的真实行号（供报告逐条溯源）
    assert by_name["net:outbound"]["doc_line"] == 5


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
    return ea.run_audit()


def test_real_repo_drift_readings_are_self_consistent(audit_data):
    b2 = audit_data["sections"]["B2_doc_code_drift"]
    assert b2["ok"] is True
    assert b2["counts"]["doc_has_code_lacks"] == len(b2["drift_doc_has_code_lacks"])
    assert b2["counts"]["doc_has_code_lacks"] == (
        b2["counts"]["doc_has_code_lacks_asserted"] + b2["counts"]["doc_has_code_lacks_preview"])
    # 「文档声称已落地、代码无」只允许 douyin_publish 这一条（其余都是文档明示的预告）
    asserted = {d["permission"] for d in b2["drift_doc_has_code_lacks"] if d["doc_kind"] == "asserted"}
    assert asserted <= {"douyin_publish"}
    for item in b2["drift_doc_has_code_lacks"]:
        assert ":" in item["code_reference_evidence"] or item["code_reference_evidence"] == "（0 命中）"
        assert item["doc_line"] > 0
        # 残留读数只认运行时代码，且不得被本审计自身污染（脚本/用例里都有这些名字的字面量）
        assert "extension_audit" not in item["code_reference_evidence"]
        assert item["code_reference_hits"] == 0 or item["code_reference_evidence"].startswith(
            ("backend/app/", "flutter_app/lib/"))
    # douyin_publish 的真相：文档说已落地，代码枚举里没有，但 Flutter 市场页仍在渲染它
    douyin = next((d for d in b2["drift_doc_has_code_lacks"] if d["permission"] == "douyin_publish"), None)
    if douyin is not None:
        assert douyin["doc_kind"] == "asserted" and douyin["code_reference_hits"] >= 1


def test_real_repo_negative_assertions_carry_hit_counts(audit_data):
    c1 = audit_data["sections"]["C1_form_surface"]
    assert c1["ok"] is True and c1["all_absent"] is True
    assert all(a["hit_count"] == 0 for a in c1["upstream_wording_absent"])
    # 「两处逐字重复」必须恰好 2，且正好落在这两个收敛点上
    copies = c1["existing_length_gates"]["notify_preview_50_copies"]
    assert copies["count"] == 2
    assert {h["file"] for h in copies["hits"]} == set(ea.NOTIFY_CONVERGENCE_FILES)


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
