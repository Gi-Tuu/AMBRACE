"""世界认知审计第三批测试（P1-07 成本归因收尾 / P2-05 user_id 回填 / 观察脚本）

覆盖：
1. 漏网调用点清单断言（AST 级）：backend/app 下所有 chat_completion / chat_completion_stream /
   llm_call 调用必须带 task 关键字（回归：extractor/voice 等已补 task，后续新增调用点漏传即失败）；
2. TASK_PLUGIN_AI 常量（plan 48 记账语义）存在且为 "plugin_ai"；
3. user_id 回填纯函数（scripts/oneoff/backfill_world_user_id.py）：按 character.user_id 解析与落库，不可解析保持；
4. 观察脚本可运行（临时库 smoke）：scripts/diagnostics/observe_cognitive_health.py 对最小 schema 库输出正常、只读。
"""
import ast
import importlib.util
import sqlite3
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"

LLM_CALL_NAMES = ("chat_completion", "chat_completion_stream", "llm_call")


# F0 scripts 归类（2026-08-31）：backfill_* 在 oneoff/，观察脚本在 diagnostics/
_SCRIPT_SUBDIR = {"backfill_world_user_id": "oneoff", "observe_cognitive_health": "diagnostics"}


def _load_script(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS_DIR / _SCRIPT_SUBDIR.get(module_name, "") / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _llm_call_nodes(tree: ast.AST):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in LLM_CALL_NAMES
        ):
            yield node


def test_all_llm_call_sites_have_task():
    """审计第三批回归：backend/app 下每个 LLM 调用点都必须带 task 关键字。"""
    missing = []
    for path in sorted(APP_DIR.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue  # 非 py 语法文件（理论无）跳过
        for node in _llm_call_nodes(tree):
            has_task = any(kw.arg == "task" for kw in node.keywords)
            if not has_task:
                missing.append(f"{path.relative_to(APP_DIR.parent)}:{node.lineno}")
    assert not missing, "以下 LLM 调用点未传 task（成本无法归因）：\n" + "\n".join(missing)


def test_plugin_ai_constant():
    from app.agent.llm_client import TASK_PLUGIN_AI
    assert TASK_PLUGIN_AI == "plugin_ai"


def test_extractor_and_voice_call_sites_pass_task():
    """漏网调用点修复回归：extractor（记忆提取）与 voice gateway（语音回复）必须带 task。"""
    extractor_src = (APP_DIR / "memory" / "extractor.py").read_text(encoding="utf-8")
    assert "task=TASK_MEMORY" in extractor_src
    gateway_src = (APP_DIR / "voice" / "gateway.py").read_text(encoding="utf-8")
    assert "task=TASK_CHAT" in gateway_src


# ── user_id 回填纯函数（scripts/oneoff/backfill_world_user_id.py）──


def _make_user_db(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE ai_characters (id INTEGER PRIMARY KEY, user_id INTEGER);
        CREATE TABLE ai_moments (id INTEGER PRIMARY KEY, character_id INTEGER, user_id INTEGER);
        INSERT INTO ai_characters VALUES (1, 7), (2, NULL), (3, 9);
        -- 1:NULL→7 可解析；2:NULL→角色 user_id 为空 不可解析；3:0→9 可解析；4:NULL→角色不存在 不可解析；5:正常不动
        INSERT INTO ai_moments VALUES
            (1, 1, NULL), (2, 2, NULL), (3, 3, 0), (4, 99, NULL), (5, 1, 7);
        """
    )
    con.commit()


def test_backfill_compute_updates():
    mod = _load_script("backfill_world_user_id", "backfill_world_user_id.py")
    con = sqlite3.connect(":memory:")
    con.text_factory = str
    _make_user_db(con)
    cur = con.cursor()
    char_map = mod.load_character_user_map(cur)
    assert char_map == {1: 7, 2: None, 3: 9}
    spec = ("ai_moments", "character_id", "user_id", "", "AI 动态归属")
    res = mod.compute_updates(cur, spec, char_map)
    assert res["total"] == 5
    assert res["missing"] == 4          # id 1,2,3,4
    assert res["resolvable"] == 2       # id 1→7, id 3→9
    assert res["unresolvable"] == 2     # id 2（角色 user_id 空）, id 4（角色不存在）
    assert dict(res["updates"]) == {1: 7, 3: 9}
    con.close()


def test_backfill_apply_updates():
    mod = _load_script("backfill_world_user_id", "backfill_world_user_id.py")
    con = sqlite3.connect(":memory:")
    con.text_factory = str
    _make_user_db(con)
    cur = con.cursor()
    char_map = mod.load_character_user_map(cur)
    spec = ("ai_moments", "character_id", "user_id", "", "AI 动态归属")
    res = mod.compute_updates(cur, spec, char_map)
    con.execute("BEGIN")
    n = mod.apply_updates(con, spec, res["updates"])
    con.commit()
    assert n == 2
    rows = dict(con.execute("SELECT id, user_id FROM ai_moments").fetchall())
    assert rows == {1: 7, 2: None, 3: 9, 4: None, 5: 7}  # 不可解析行保持原值
    con.close()


def test_backfill_dry_run_main_does_not_write(tmp_path):
    """--dry-run（默认）不写库：跑完 main 后库中 user_id 原样。"""
    mod = _load_script("backfill_world_user_id", "backfill_world_user_id.py")
    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    con.text_factory = str
    _make_user_db(con)
    con.close()
    rc = mod.main(["--db", str(db)])
    assert rc in (0, 2)  # 2=有可回填项（dry-run 完成）；0=无可回填
    con = sqlite3.connect(str(db))
    rows = dict(con.execute("SELECT id, user_id FROM ai_moments").fetchall())
    assert rows == {1: None, 2: None, 3: 0, 4: None, 5: 7}  # 未写库
    con.close()


# ── 观察脚本（scripts/diagnostics/observe_cognitive_health.py）临时库 smoke ──


def _make_observe_db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY, user_id INTEGER, character_id INTEGER,
            speaker_type TEXT, speaker_id INTEGER, epistemic_status TEXT, why_it_matters TEXT,
            contradiction_count INTEGER DEFAULT 0, reliability_score REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE world_facts (
            id INTEGER PRIMARY KEY, user_id INTEGER, character_id INTEGER,
            status TEXT, source TEXT, author TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE agent_task_logs (
            id INTEGER PRIMARY KEY, trigger TEXT, status TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        INSERT INTO memories (speaker_type, speaker_id, epistemic_status) VALUES ('user', 1, 'FACT');
        INSERT INTO memories (contradiction_count) VALUES (2);
        INSERT INTO memories (reliability_score) VALUES (0.2);
        INSERT INTO memories (why_it_matters) VALUES ('重要');
        INSERT INTO world_facts (status, source, author) VALUES ('active', 'chat_status', 'user');
        INSERT INTO agent_task_logs (trigger, status) VALUES ('chat', 'ok');
        """
    )
    con.commit()
    con.close()


def test_observe_script_smoke(tmp_path, capsys):
    mod = _load_script("observe_cognitive_health", "observe_cognitive_health.py")
    db = tmp_path / "obs.db"
    _make_observe_db(db)
    rc = mod.main(["--db", str(db), "--days", "7"])
    assert rc == 0
    out = capsys.readouterr().out
    # 关键段落与指标都出现在输出里
    assert "世界认知健康度观察" in out
    assert "speaker_type" in out and "epistemic_status" in out and "why_it_matters" in out
    assert "world_facts 增长" in out
    assert "contradiction_count > 0" in out and "**1**" in out
    assert "reliability_score < 0.4" in out and "**1**" in out
    assert "fact_check" in out
    assert "未写任何数据" in out


def test_observe_script_rejects_missing_db(tmp_path, capsys):
    mod = _load_script("observe_cognitive_health", "observe_cognitive_health.py")
    rc = mod.main(["--db", str(tmp_path / "nope.db")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "数据库不存在" in err
