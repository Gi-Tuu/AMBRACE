# -*- coding: utf-8 -*-
"""A30 私有留底离线复算闭环的守卫（**零 LLM、零计费、零生产库、零网络**）。

覆盖交接单 §5.2 的执行用例：
① 合成完整留底真实 CLI 跨进程（>70 字载荷、900 字窗外回复、date/type/tag、JSON 嵌套、未知合法键）
   —— 重放与原判据对得上，公共 stdout 无原文；
② 时间漂移用既有 stable 规则保持可比；输入/题面/导出尺子变化分别被识别；无源码原件时不伪称完整溯源；
③ 缺字段/缺跑/重复冲突/错配 sent_fp/未知 stage/坏 JSON/截断旧样本/仅 export/空样本 —— 结果与分母清楚、无伪成功；
④ 存下 pass/err 被篡改、完整回复被篡改、动作结构不自洽 —— 必须有真正对账差异；
⑤ 仓库内/软链接回流路径、详细输出未显式启用、JSON 非法值、带原文的异常文本、半写入失败；
⑥ ≥2 发**隔离且精确语义变异**（真 pytest 红 ⇒ 撤变异恢复绿）；
⑦ 旧 recount/五轴、原 gold/认证/钟点规则兼容；新 CLI 导入与运行均不接生产/模型/网络。

隔离铁律：任何子进程在**出生前**就显式设沙箱 `DATABASE_URL`/`CHROMA_PERSIST_DIR`/`TEMP`，
哨兵 SQLite 全程逐字节不变（`parse_actions`/`extract_memory_channel` 会经 `app.memory.__init__`
在导入期建 engine，故必须先钉沙箱）。
"""
import hashlib
import importlib.util
import json
import copy
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
REPLAY = REPO / "scripts" / "diagnostics" / "memory_private_replay.py"
EVAL = REPO / "scripts" / "diagnostics" / "memory_action_eval.py"


def _load_replay():
    spec = importlib.util.spec_from_file_location(
        "_mempriv_replay_under_test", str(REPLAY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rp = _load_replay()


# ── 沙箱环境（子进程出生前就设，绝不继承裸 os.environ）────────────────────────────
def _uri(p):
    return "sqlite+aiosqlite:///" + str(p).replace(os.sep, "/")


def _sandbox_env(root: Path, **extra):
    """把 DATABASE_URL/CHROMA/TEMP 全钉进 `root`（哨兵库），拒绝模型/网络入口。"""
    env = {
        "PYTHONIOENCODING": "utf-8",
        "DATABASE_URL": _uri(root / "sentinel.db"),
        "CHROMA_PERSIST_DIR": str(root / "chroma"),
        "TEMP": str(root), "TMP": str(root), "TMPDIR": str(root),
        "PYTHONWARNINGS": "ignore",
    }
    for k in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "USERPROFILE", "HOME", "HOMEDRIVE",
              "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PATHEXT", "LANG", "LC_ALL"):
        if k in os.environ:
            env[k] = os.environ[k]
    env.update(extra)
    return env


def _cli_py():
    py = REPO / "backend" / ".venv" / "Scripts" / "python.exe"
    return str(py) if py.is_file() else sys.executable


def _write_sentinel(path: Path, marker="SENTINEL-DO-NOT-TOUCH"):
    con = sqlite3.connect(str(path))
    try:
        con.execute("CREATE TABLE IF NOT EXISTS marker(x TEXT)")
        con.execute("DELETE FROM marker")
        con.execute("INSERT INTO marker VALUES (?)", (marker,))
        con.commit()
    finally:
        con.close()


def _sentinel_snapshot(path: Path):
    con = sqlite3.connect(str(path))
    try:
        rows = con.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
        data = con.execute("SELECT * FROM marker").fetchall()
    finally:
        con.close()
    return hashlib.sha256(json.dumps([rows, data], sort_keys=True).encode()).hexdigest()


@pytest.fixture()
def outside_repo(tmp_path):
    """私有产物落盘夹具**必须在仓库外**（项目全量 --basetemp 在仓库内，会让闸门测成假红）。"""
    with tempfile.TemporaryDirectory(prefix="ambrace_replay_ev_", dir=Path.home()) as td:
        root = Path(td)
        assert not os.path.normcase(str(root)).startswith(
            os.path.normcase(rp.repo_root_dir()) + os.sep), "夹具目录落进仓库 ⇒ 下面全是假绿"
        yield root


# ── 合成夹具（形状走 make_private_evidence 原样；不取真实题集）──────────────────
def _synth_case(cid="syn01", **over):
    c = {"cid": cid, "category": "temporal", "judge": "J1",
         "expect": {"action_type": ["CAL_NOTE"], "field_match": {"*": "接孩子"},
                    "forbidden": [], "gold_seed_idx": [0]},
         "time_anchor": {"as_of": "2026-11-19", "expect_date": "2026-11-20"}}
    c.update(over)
    return c


_LONG_MEMO = "甲" * 74 + "◆载荷尾巴◆"                 # 80 字，越过报表 [:70]
_NESTED = '{"每周":{"周三":["接孩子"]},"unknown_legal_key":"v◆JSON尾◆"}'
# 标记形态按 `parse_actions` 真实口径（`_CAL_DONE_RE`/`_MEMO_DONE_RE`/`_TIMER_RE`/`_MCP_TOOL_RE`）：
# NOTE_DONE 由 CAL_DONE/MEMO_DONE 产生（payload.type=calendar/memo）；TIMER 是 `[timer: 30m]`（tag=原始串）；
# JSON 嵌套＋未知合法键走 MCP 通道（`mcp.*` 的 payload 是 dict 原样，一位不丢）。
_FULL_REPLY = (
    "好的，我记下了。\n"
    "[MEMO]%s[/MEMO]\n" % _LONG_MEMO +
    "[CAL_NOTE]2026-11-20 18:00 接孩子放学[/CAL_NOTE]\n"
    "[CAL_DONE]接孩子[/CAL_DONE]\n"
    "[MEMO_DONE]接孩子[/MEMO_DONE]\n"
    "[timer: 30m]\n"
    "闲话。" + "哈" * 920 + "\n"
    "[mcp.notes.save]%s[/mcp.notes.save]" % _NESTED
)


def _eval_mod():
    spec = importlib.util.spec_from_file_location("_mae_for_replay_test", str(EVAL))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _build_record(cid="syn01", *, reply=None, case=None, messages=None, actions=None,
                  stage="judge", run=1, **row_over):
    """用 `make_private_evidence` 原样拼一行留底（判据本体不另造）。"""
    mae = _eval_mod()
    case = case or _synth_case(cid)
    msgs = messages if messages is not None else [
        {"role": "system", "content": "## 当前时间\n2026年11月19日 星期三 17:12"},
        {"role": "user", "content": "明天傍晚六点提醒我接孩子放学"}]
    reply = _FULL_REPLY if reply is None else reply
    import app.agent.actions as act_mod
    anchor = str((case.get("time_anchor") or {}).get("as_of") or "").strip() or None
    if actions is None:
        actions = act_mod.parse_actions(reply or "", base_date=anchor)
    chan = mae.extract_memory_channel(reply or "")
    v = mae.j1_verdict(case["expect"], actions,
                       expect_date=mae.resolve_expect_date(case["time_anchor"]),
                       memory_texts=chan)
    row = {"cid": cid, "run": run, "category": case.get("category"), "judge": case.get("judge"),
           "prompt_fp_stable": mae.stable_fp(msgs), "prompt_fp": mae._prompt_fp(msgs),
           "pass": v["pass"], "err": v["err"], "why": v["why"],
           "exemptions": list(v.get("exempted") or []),
           "dates_seen": sorted({str((a.payload or {}).get("date") or "")[:10]
                                 for a in actions if isinstance(a.payload, dict)
                                 and (a.payload or {}).get("date")}),
           "actions_seen": [a.action_type for a in actions],
           "mem_channel": chan}
    row.update(row_over)
    return mae.make_private_evidence(
        stage, row=row, case=case, run=run, messages=msgs,
        actions=actions, reply_text=reply, criteria=case)


def _dump(rows, path: Path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")


def _run_cli(args, env, cwd=None):
    cmd = [_cli_py(), str(REPLAY)] + [str(x) for x in args]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, cwd=str(cwd or REPO), timeout=120)
    return p.returncode, p.stdout or "", p.stderr or ""


# ════════════════════════════════ ① 合成跨进程重放 ════════════════════════════════
def test_合成完整留底跨进程重放_载荷窗外回复_date_type_tag_JSON嵌套_对得上且无原文泄漏(tmp_path):
    root = Path(tmp_path)
    _write_sentinel(root / "sentinel.db")
    before = _sentinel_snapshot(root / "sentinel.db")
    env = _sandbox_env(root)

    with tempfile.TemporaryDirectory(prefix="ambrace_replay_ev_", dir=Path.home()) as td:
        ev_root = Path(td)
        ev = ev_root / "evidence.jsonl"
        ds = ev_root / "dataset.jsonl"
        _dump([_build_record("syn01")], ev)
        ds.write_text(json.dumps(_synth_case("syn01"), ensure_ascii=False) + "\n", encoding="utf-8")

        rc, out, err = _run_cli(["replay", "--evidence", str(ev), "--dataset", str(ds),
                                 "--out", str(ev_root / "rep.md")], env)
        assert rc == 0, err[-500:]
        rep = (ev_root / "rep.md").read_text(encoding="utf-8")
        # ① 重放与原判据对得上（新口径：判分局部一致与证据完整合格**分栏**，各自分母）
        assert "| 判分局部一致（pass/err＋被比字段逐字全 match） | 1 | 1 |" in rep
        assert "| 证据完整合格（evidence_ok 全维自证） |" in rep
        assert "| effective | 1 |" in rep and "| conflict | 0 |" in rep
        assert "| 一致 |" in rep
        # ② 输入/题面分维
        assert "input_fp_match" in rep
        assert "verified" in rep
        # ②' 尺子来源分维必须在公共报表闭合展示（修复⑤）
        assert "尺子来源分维" in rep and "换当前尺子复算" in rep
        # ③ 公共 stdout 与报表**无原文**：80 字载荷尾、900 字填充、JSON 嵌套、未知键、原始 cid
        for leak in ("◆载荷尾巴◆", "哈哈", "unknown_legal_key", "◆JSON尾◆", "接孩子放学", "syn01", "甲甲甲"):
            assert leak not in out, "公共 stdout 泄漏原文：%s" % leak
            assert leak not in rep, "公共报表泄漏原文：%s" % leak
    assert _sentinel_snapshot(root / "sentinel.db") == before, "CLI 碰了外部哨兵库"


def test_重放读回actions_full保住date_type_tag与JSON嵌套_一位不丢():
    """回复原文重放出的动作 vs 留底 actions_full 自洽，且非文本字段（date/type/tag）与嵌套不丢。"""
    rec = _build_record("syn01")
    acts = rp._rebuild_actions(rec["actions_full"])
    assert acts is not None
    types = {a.action_type for a in acts}
    assert {"CAL_NOTE", "NOTE_DONE", "TIMER", "MEMO"} <= types
    cal = next(a for a in acts if a.action_type == "CAL_NOTE")
    assert cal.payload.get("date") == "2026-11-20"        # date 非文本字段
    nd = next(a for a in acts if a.action_type == "NOTE_DONE")
    assert nd.payload.get("type") in ("calendar", "memo")  # type 非文本字段
    timer = next(a for a in acts if a.action_type == "TIMER")
    assert timer.payload.get("tag") and "timer" in str(timer.payload.get("tag")).lower()
    nested = next(a for a in acts if str(a.action_type).startswith("mcp.") and "每周" in json.dumps(a.payload, ensure_ascii=False))
    assert nested is not None
    assert nested.payload.get("unknown_legal_key") == "v◆JSON尾◆"   # 未知合法键一位不丢
    assert nested.payload.get("每周") == {"周三": ["接孩子"]}          # JSON 嵌套不丢
    assert rp._actions_sig(rp._replay_actions(rec["reply_text_full"], "2026-11-19")) == rp._actions_sig(acts)


# ════════════════════════════════ ② 输入/来源分维不互顶 ════════════════════════════════
def test_只有带时钟的prompt_fp时不能冒充stable_缺哪维写unknown():
    rec = _build_record("syn01")
    rec["prompt_fp_stable"] = rp.UNKNOWN        # 缺 stable
    out = rp.replay_one(rec)
    assert out["fp"]["input_stable"] == rp.R_INPUT_FP_UNKNOWN

    rec2 = _build_record("syn01")
    rec2["ctx_messages"] = rp.UNKNOWN           # 无 ctx_messages ⇒ 不能现算
    out2 = rp.replay_one(rec2)
    assert out2["fp"]["input_stable"] == rp.R_INPUT_FP_UNKNOWN


def test_实质输入变了stable跟着变_时间变了stable不变():
    mae = _eval_mod()
    a = [{"role": "system", "content": "## 当前时间\n2026年11月19日 星期三 17:12"},
         {"role": "user", "content": "提醒我接孩子"}]
    b = [{"role": "system", "content": "## 当前时间\n2026年11月19日 星期三 17:59"},   # 只变时钟
         {"role": "user", "content": "提醒我接孩子"}]
    c = [{"role": "system", "content": "## 当前时间\n2026年11月19日 星期三 17:12"},
         {"role": "user", "content": "提醒我接狗狗"}]                                # 实质变
    assert mae.stable_fp(a) == mae.stable_fp(b), "时钟变了 stable 不该变"
    assert mae.stable_fp(a) != mae.stable_fp(c), "实质输入变了 stable 必须变"


def test_无独立题面原件时只说留存标识相同_不伪称题面真实性已校验():
    rec = _build_record("syn01")
    out = rp.replay_one(rec, case_map=None)     # 没给题面原件
    assert out["fp"]["case_source"] == "retained_only"
    # 给了题面原件才可 verified
    out2 = rp.replay_one(rec, case_map={"syn01": _synth_case("syn01")})
    assert out2["fp"]["case_source"] == "verified"


def test_导出尺子与当前尺子两腿不同时明确标识diff_不自动当同源():
    rec = _build_record("syn01")
    rec["export_evaluator_source_fp"] = "cafebabe0000"
    rec["evaluator_source_fp"] = "deadbeef1111"
    out = rp.replay_one(rec)
    assert out["fp"]["export_vs_current"] == "diff"
    rec2 = _build_record("syn01")
    rec2["export_evaluator_source_fp"] = rp.UNKNOWN
    out2 = rp.replay_one(rec2)
    assert out2["fp"]["export_vs_current"] == rp.R_INPUT_FP_UNKNOWN


# ════════════════════════════════ ③ 边界：缺字段/重复/未知stage/截断/仅export ════════════════════════════════
def test_缺判据或缺原文_判不可复算_不伪成功():
    rec = _build_record("syn01")
    rec["expect"] = rp.UNKNOWN
    rec["time_anchor"] = rp.UNKNOWN
    out = rp.replay_one(rec)
    assert out["verdict_match"] is None and rp.R_MISSING_JUDGE in out["reasons"]

    rec2 = _build_record("syn01")
    rec2["reply_text_full"] = rp.UNKNOWN
    rec2["actions_full"] = rp.UNKNOWN
    out2 = rp.replay_one(rec2)
    assert out2["verdict_match"] is None and rp.R_MISSING_REPLY in out2["reasons"]


def test_仅export行只核输入与来源_不算判分通过也不算生成失败():
    rec = _build_record("syn01", stage="export", reply=None, actions=None)
    out = rp.replay_one(rec)
    assert out["verdict_match"] is None
    assert rp.R_EXPORT_ONLY in out["reasons"]


def test_未知stage与截断旧样本走unknown_不进分母():
    rec = _build_record("syn01", stage="weird_stage")
    out = rp.replay_one(rec)
    assert out["verdict_match"] is None and rp.R_UNKNOWN_STAGE in out["reasons"]

    rec2 = _build_record("syn01")
    rec2["truncated"] = True
    out2 = rp.replay_one(rec2)
    assert out2["verdict_match"] is None and rp.R_STALE_TRUNC in out2["reasons"]


def test_重复stage_cid_run被显式点名_不静默挑一行(outside_repo):
    """修复①反例：重复 (stage,cid,run) 两行**全部进 conflict 桶**，不进一致分母、不给干净退出码。"""
    ev = outside_repo / "evidence.jsonl"
    _dump([_build_record("syn01"), _build_record("syn01")], ev)
    env = _sandbox_env(outside_repo)
    rc, out, err = _run_cli(["replay", "--evidence", str(ev)], env)
    assert rc == rp.EXIT_CONFLICT, "重复键冲突不该给干净退出码（rc=%d）：%s" % (rc, out[-300:])
    assert "duplicate_key" in out, "重复键没被点名：%s" % out[-300:]
    assert "| conflict | 2 |" in out, "两行重复键应全进 conflict 桶：%s" % out[-400:]
    assert "| effective | 0 |" in out, "重复行不得进有效分母：%s" % out[-400:]
    assert "| 判分局部一致（pass/err＋被比字段逐字全 match） | 0 | 0 |" in out, \
        "重复行不得作有效一致证据：%s" % out[-400:]


def test_坏JSON留底当场拒_不回显含原文的异常repr(outside_repo):
    ev = outside_repo / "bad.jsonl"
    ev.write_text('{"cid":"syn01","payloads_full":"泄漏原文甲甲甲", bad json }\n', encoding="utf-8")
    env = _sandbox_env(outside_repo)
    rc, out, err = _run_cli(["replay", "--evidence", str(ev)], env)
    assert rc == rp.EXIT_BAD
    assert "泄漏原文" not in (out + err), "异常回显了原文"


# ════════════════════════════════ ④ 对账差异：篡改存值/篡改回复 ════════════════════════════════
def test_存下pass_err被篡改_必须是真正对账差异():
    rec = _build_record("syn01")                # 真值 pass=True
    rec["pass"] = False
    rec["err"] = "E4"
    out = rp.replay_one(rec)
    assert out["verdict_match"] is False
    assert rp.R_DIFF_PASS in out["reasons"]


def test_完整回复被篡改_动作不自洽且判分差异():
    tampered = _FULL_REPLY.replace("接孩子放学", "接狗狗放学")
    rec = _build_record("syn01", reply=tampered)
    rec["actions_full"] = _build_record("syn01")["actions_full"]   # 存的是原动作
    out = rp.replay_one(rec)
    assert out["self_consistent"] is False
    assert rp.R_SELFINC in out["reasons"]


# ════════════════════════════════ ⑤ 隐私：路径闸 / 详细输出 / JSON 非法值 / 半写入 ════════════════════════════════
def test_详细输出在仓库内一律拒绝(tmp_path):
    inside = REPO / "leak_detail.jsonl"
    with pytest.raises(ValueError):
        rp.guard_outside_repo(str(inside), "逐跑详细结果")
    with pytest.raises(ValueError):
        rp.guard_outside_repo("", "逐跑详细结果")


def test_软链接落回仓库内部同样拒绝(outside_repo):
    link = outside_repo / "linkback.jsonl"
    target = REPO / "inner_target.jsonl"
    try:
        os.symlink(str(target), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("无符号链接权限")
    try:
        with pytest.raises(ValueError):
            rp.guard_outside_repo(str(link), "逐跑详细结果")
    finally:
        if link.exists() or link.is_symlink():
            link.unlink()


def test_JSON非法值NaN整批拒写_不有损default_str(outside_repo):
    out = outside_repo / "detail.jsonl"
    with pytest.raises(RuntimeError):
        rp._write_jsonl_strict(str(out), [{"bad": float("nan")}])
    with pytest.raises(RuntimeError):
        rp._write_jsonl_strict(str(out), [{"bad": float("inf")}])
    assert not out.exists() or out.stat().st_size == 0


def test_写盘失败只说可能已写部分_不伪称零写入(outside_repo):
    out = outside_repo / "detail.jsonl"
    out.write_text("", encoding="utf-8")
    os.chmod(str(out), 0o444)                   # 只读 ⇒ open 失败
    try:
        with pytest.raises(RuntimeError) as e:
            rp._write_jsonl_strict(str(out), [{"a": 1}, {"b": 2}])
        assert "可能已写" in str(e.value) or "失败" in str(e.value)
    finally:
        os.chmod(str(out), 0o644)


def test_详细输出未显式启用时不多落任何原文文件(outside_repo):
    ev = outside_repo / "evidence.jsonl"
    _dump([_build_record("syn01")], ev)
    env = _sandbox_env(outside_repo)
    jsonl_before = {p.name for p in outside_repo.glob("*.jsonl")}
    rc, out, err = _run_cli(["replay", "--evidence", str(ev)], env)
    assert rc == 0
    # CLI 只在显式 --detail-out/--map-out 时落盘；不给开关就不得新增任何 jsonl/原文文件
    jsonl_after = {p.name for p in outside_repo.glob("*.jsonl")}
    assert jsonl_after == jsonl_before, "没给 --detail-out 却多了 jsonl：%s" % (jsonl_after - jsonl_before)
    for p in outside_repo.iterdir():
        if p.is_file() and p.name != "evidence.jsonl" and p.suffix in (".jsonl", ".json"):
            assert not p.stat().st_size or p.name in jsonl_before, "多落了文件：%s" % p.name


# ════════════════════════════════ ⑥ 变异反证（真 pytest 红 ⇒ 撤变异恢复绿）═══════════════════════════════
_MUT_PLUGIN = '''# -*- coding: utf-8 -*-
import os
from importlib.machinery import SourceFileLoader
_ORIG = SourceFileLoader.exec_module


def _patched(self, module, *a, **k):
    _ORIG(self, module, *a, **k)          # exec_module 返回 None，直接用入参 module
    mode = os.environ.get("REPLAY_MUTATION", "")
    if not mode or not hasattr(module, "replay_one"):
        return module
    if mode == "trust_stored":          # 直接信存下 pass ⇒ 取消输入核对
        def mut(rec, *, case_map=None, _u=module.UNKNOWN):
            out = {"stage": rec.get("stage"), "cid": rec.get("cid"), "run": rec.get("run"),
                   "reasons": [module.R_MATCH], "source_used": "trust_stored",
                   "stored": {"pass": rec.get("pass"), "err": rec.get("err")},
                   "recomputed": {"pass": rec.get("pass"), "err": rec.get("err")},
                   "verdict_match": True, "self_consistent": None, "fp": {}}
            return out
        module.replay_one = mut
    elif mode == "skip_input_check":     # 取消输入核对：永远 input_fp_match
        _orig_fp = module._fp_dims
        def mut_fp(rec, out, case_map):
            out["fp"]["input_stable"] = module.R_INPUT_FP_MATCH
        module._fp_dims = mut_fp
    elif mode == "ignore_fields":        # 返工②语义：逐字段对账全假 match（只比 pass/err 的旧病）
        def mut_rf(rec, source_used, raw_now, _m=module):
            return {k: "match" for k in _m._CMP_FIELDS}
        module._recon_fields = mut_rf
    elif mode == "keep_dup_effective":   # 返工①语义：重复键行照样进有效分母（旧病）
        def mut_bucket(rec, out, dup_keys, _m=module):
            return _m.B_EFFECTIVE
        module._compute_bucket = mut_bucket
    elif mode == "partial_effective":    # 第二轮②语义：partial 字段仍算有效（不进 incomparable）
        def mut_bucket2(rec, out, dup_keys, _m=module):
            return _m.B_EFFECTIVE
        module._compute_bucket = mut_bucket2
    elif mode == "trust_case_source":    # 第二轮①语义：只看留存题面指纹，忽略实际执行判据
        def mut_eok(out, _m=module):
            fp = out.get("fp") or {}
            fields = out.get("fields") or {}
            return (out.get("bucket") == _m.B_EFFECTIVE
                    and out.get("verdict_match") is True
                    and bool(fields) and all(v == "match" for v in fields.values())
                    and fp.get("input_stable") == _m.R_INPUT_FP_MATCH
                    and fp.get("case_source") == "verified"
                    and out.get("self_consistent") is not False)   # 去掉 criteria_binding 门槛
        module._compute_evidence_ok = mut_eok
    return module


SourceFileLoader.exec_module = _patched
'''
_MUT_TARGETS = {
    "trust_stored": "tests/test_memory_private_replay.py::test_存下pass_err被篡改_必须是真正对账差异",
    "skip_input_check": "tests/test_memory_private_replay.py::test_只有带时钟的prompt_fp时不能冒充stable_缺哪维写unknown",
    "ignore_fields": "tests/test_memory_private_replay.py::test_篡改why或exemptions_必须是逐字段对账差异_不再match",
    "keep_dup_effective": "tests/test_memory_private_replay.py::test_重复键冲突行_全部进conflict桶_不进一致分母",
    "partial_effective": "tests/test_memory_private_replay.py::test_partial字段落incomparable桶_不进有效分母_报表分母归零",
    "trust_case_source": "tests/test_memory_private_replay.py::test_改判据但留存题面指纹仍verified_必须报criteria_diff且证据不合格",
}


def test_变异电池_两发精准语义变异让真pytest变红_撤变异立刻恢复绿(outside_repo):
    """变异不落工作区：临时插件只在那一发子进程注入；跑完比对 replay 脚本 sha1 前后相同。"""
    plugin_dir = outside_repo / "mut"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "replay_mut_plugin.py").write_text(_MUT_PLUGIN, encoding="utf-8")
    script_sha = hashlib.sha1(REPLAY.read_bytes()).hexdigest()
    bt = outside_repo / "basetemp"
    bt.mkdir(parents=True, exist_ok=True)

    for mode, node_id in _MUT_TARGETS.items():
        env = _sandbox_env(outside_repo, PYTHONPATH=str(plugin_dir), REPLAY_MUTATION=mode)
        cmd = [_cli_py(), "-m", "pytest", "-q", "-p", "replay_mut_plugin", "-p", "no:cacheprovider",
               "--basetemp", str(bt / mode), node_id]
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, cwd=str(REPO / "backend"), timeout=150)
        blob = (p.stdout or "") + (p.stderr or "")
        assert p.returncode != 0, "变异 %s 没让真 pytest 变红 ⇒ 那条守卫是空跑：%s" % (mode, blob[-400:])
        assert "1 failed" in blob, "变异 %s 红得不对题：%s" % (mode, blob[-500:])

    ctrl = _sandbox_env(outside_repo, PYTHONPATH=str(plugin_dir), REPLAY_MUTATION="none")
    p = subprocess.run([_cli_py(), "-m", "pytest", "-q", "-p", "replay_mut_plugin", "-p", "no:cacheprovider",
                        "--basetemp", str(bt / "ctrl"), *_MUT_TARGETS.values()],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=ctrl, cwd=str(REPO / "backend"), timeout=150)
    assert p.returncode == 0, "撤掉变异后没恢复绿：%s" % (p.stdout or "")[-600:]
    assert hashlib.sha1(REPLAY.read_bytes()).hexdigest() == script_sha, "变异电池动了工作区"


# ════════════════════════════════ ⑦ 隔离：真实 CLI 全程不碰哨兵 ════════════════════════════════
def test_真实CLI全程不改外部哨兵schema_marker_字节(tmp_path):
    root = Path(tmp_path)
    sp = root / "sentinel.db"
    _write_sentinel(sp)
    before_schema, before_data = _sentinel_snapshot(sp), _sentinel_snapshot(sp)
    env = _sandbox_env(root)
    with tempfile.TemporaryDirectory(prefix="ambrace_replay_ev_", dir=Path.home()) as td:
        ev_root = Path(td)
        ev = ev_root / "evidence.jsonl"
        _dump([_build_record("syn01")], ev)
        rc, out, err = _run_cli(["replay", "--evidence", str(ev)], env)
        assert rc == 0, err[-400:]
    after = _sentinel_snapshot(sp)
    assert after == before_schema == before_data, "CLI 写了外部哨兵库（schema/marker/字节变了）"
    # 反证：哨兵真的存在且非空（不是空库测了个寂寞）
    con = sqlite3.connect(str(sp))
    try:
        n = con.execute("SELECT COUNT(*) FROM marker").fetchone()[0]
    finally:
        con.close()
    assert n == 1


def test_导入CLI不预先建生产engine_沙箱生效(tmp_path, monkeypatch):
    """新进程导入 memory_private_replay 后，DATABASE_URL 指向沙箱而非生产库路径。"""
    root = Path(tmp_path)
    env = _sandbox_env(root)
    code = (
        "import os, sys; sys.path.insert(0, r'%s');"
        "import importlib.util;"
        "spec = importlib.util.spec_from_file_location('m', r'%s');"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m);"
        "m._sandbox_db_env();"
        "print('URL=' + os.environ.get('DATABASE_URL',''))"
    ) % (str(REPO / "backend"), str(REPLAY))
    p = subprocess.run([_cli_py(), "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=60)
    assert p.returncode == 0, p.stderr[-400:]
    url = [l for l in (p.stdout or "").splitlines() if l.startswith("URL=")][0]
    assert "sentinel" not in url and "ai_companion" not in url, "沙箱没生效：%s" % url


# ════════════════════════════════ ⑦ 兼容：原 gold/认证/钟点规则不改 ════════════════════════════════
def test_旧recount五轴与原认证钟点规则未被本工具改动():
    """memory_action_eval 的原判据函数 AST 不变（本工具只 import 复用，不改判据本体）。"""
    import ast
    src = EVAL.read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for fn in ("j1_verdict", "j2_verdict", "certified_j1", "sig_recompute", "mask_clock",
               "stable_fp", "resolve_expect_date", "fold_runs", "recount_rep"):
        assert fn in names, "原判据函数 %s 不见了 ⇒ 判据本体被动过" % fn
    # 本工具引用的就是这些原函数（不是另造一份）：源文件就是 memory_action_eval.py
    import inspect
    assert Path(inspect.getsourcefile(rp._ev().j1_verdict)).name == "memory_action_eval.py"


# ══════════════════════ 返工六组漏洞守卫（REVIEW.md 1–6，2026-10-15）══════════════════════
# 每条反例＝**失败前提明确**（修复前主审实测行为）＋修复后通过的真实断言。
# 敏感标记全用 _SENS 前缀常量，公共 stdout/report/error 零泄漏由专门守卫统一扫。
_SENS_STAGE = "SYNTH_PRIVATE_STAGE_MARK"
_SENS_RUN = "SYNTH_PRIVATE_RUN_MARK"
_SENS_ERR = "SYNTH_PRIVATE_ERR_MARK"
_SENS_ALL = (_SENS_STAGE, _SENS_RUN, _SENS_ERR, "◆载荷尾巴◆", "接孩子放学", "甲甲甲")


def _leak_scan(*blobs):
    for b in blobs:
        for s in _SENS_ALL:
            assert s not in b, "公共输出泄漏敏感标记 %s：%s" % (s, b[-300:])


# ── 修复②：完整判分字段 + 严格类型 + 缺判据 ─────────────────────────────────────
def test_篡改why或exemptions_必须是逐字段对账差异_不再match():
    """失败前提：主审实测 tampered_why / tampered_exemptions → verdict_match=True（只比 pass/err）。"""
    rec = _build_record("syn01")
    out0 = rp.replay_one(rec)
    assert out0["verdict_match"] is True, "真值基线应逐字段全 match"

    r1 = _build_record("syn01")
    r1["why"] = "SYNTH_CHANGED_WHY"
    o1 = rp.replay_one(r1)
    assert o1["verdict_match"] is False and rp.R_DIFF_FIELD in o1["reasons"], \
        "篡改 why 仍被判一致：%s" % o1["reasons"]
    assert o1["fields"]["why"] == "diff"

    r2 = _build_record("syn01")
    r2["exemptions"] = ["SYNTH_CHANGED_EXEMPTION"]
    o2 = rp.replay_one(r2)
    assert o2["verdict_match"] is False and rp.R_DIFF_FIELD in o2["reasons"], \
        "篡改 exemptions 仍被判一致：%s" % o2["reasons"]
    assert o2["fields"]["exemptions"] == "diff"


def test_pass非原生bool一律不可比_绝不bool强转():
    """失败前提：主审实测 pass="False" 被 bool() 转 True 仍 match。"""
    for bad in ("False", "True", 1, 0):
        rec = _build_record("syn01")
        rec["pass"] = bad
        out = rp.replay_one(rec)
        assert out["verdict_match"] is None, "pass=%r 竟进了对账分母" % (bad,)
        assert rp.R_VERDICT_TYPE_INVALID in out["reasons"]
        assert out["bucket"] == rp.B_INCOMPARABLE


def test_缺expect或缺time_anchor任一都判缺判据_绝不拿diff冒充处理正确():
    """失败前提：主审实测缺 expect 时仍复算得 diff_pass（AND 逻辑）；且「缺判据得 diff」不算处理正确。"""
    rec = _build_record("syn01")
    rec["expect"] = None                    # 缺 expect，time_anchor 还在
    out = rp.replay_one(rec)
    assert out["verdict_match"] is None and rp.R_MISSING_JUDGE in out["reasons"]
    assert rp.R_DIFF_PASS not in out["reasons"], "缺判据不得记成 diff_pass"
    assert out["bucket"] == rp.B_MISSING

    rec2 = _build_record("syn01")
    rec2["time_anchor"] = rp.UNKNOWN        # 缺 time_anchor，expect 还在
    out2 = rp.replay_one(rec2)
    assert out2["verdict_match"] is None and rp.R_MISSING_JUDGE in out2["reasons"]
    assert rp.R_DIFF_PASS not in out2["reasons"]


def test_time_anchor为unknown字符串_安全走缺判据_绝不AttributeError():
    """失败前提：主审实测 time_anchor="unknown" → AttributeError（对非 dict 调 .get）。"""
    rec = _build_record("syn01")
    rec["time_anchor"] = "unknown"
    out = rp.replay_one(rec)                 # 不许抛异常
    assert out["verdict_match"] is None and rp.R_MISSING_JUDGE in out["reasons"]


# ── 修复①：分母重分类 + 判分局部一致与证据完整合格分栏 ──────────────────────────
def test_输入指纹错配_判分局部一致成立_但证据完整合格必须记否():
    """失败前提：主审实测 bad_input_hash → match（判分一致与证据合格混栏）。
    现口径：判分可对上（记一致），但 input_stable=diff ⇒ evidence_ok=False，两栏分开。"""
    rec = _build_record("syn01")
    rec["prompt_fp_stable"] = "invalid"      # 输入指纹错配（ctx_messages 仍在，可现算）
    out = rp.replay_one(rec)
    assert out["verdict_match"] is True, "判分本身可复现，应记局部一致"
    assert out["fp"]["input_stable"] == rp.R_INPUT_FP_DIFF
    assert out["evidence_ok"] is False, "输入指纹错配不得记证据完整合格"
    assert out["bucket"] == rp.B_EFFECTIVE   # 判分可比（错配是证据维度，不是不可比）


def test_重复键冲突行_全部进conflict桶_不进一致分母():
    """失败前提：主审 duplicate_cli.txt 实测「一致 2/2」exit=0（重复两行都算 match）。
    现口径：两行全进 conflict、effective=0、判分一致分母 0、exit=EXIT_CONFLICT。"""
    recs = [_build_record("syn01"), _build_record("syn01")]
    results = rp.replay_all(recs)
    assert [r["bucket"] for r in results] == [rp.B_CONFLICT, rp.B_CONFLICT]
    assert all(rp.R_DUP in r["reasons"] for r in results)
    assert all(r["evidence_ok"] is False for r in results)


# ── 修复③：全分支匿名化 + audit 路径闸 ─────────────────────────────────────────
def test_stage_run_err元数据原样值_绝不进公共报表():
    """失败前提：主审实测 public_metadata_leak=true（合成 stage/run 原样插入公开报告）。"""
    # ① 未知 stage + 带标记 run：stage/run 原样值绝不出现，走 stage_other / run_other
    rec = _build_record("syn01")
    rec["stage"] = _SENS_STAGE
    rec["run"] = _SENS_RUN
    out = rp.replay_one(rec)
    public = rp._render_replay([out], rp.Anon(), [])
    _leak_scan(public)
    assert rp._STAGE_OTHER in public and "run_other" in public
    # ② 合法 stage + 存值 err 带标记：err 走枚举 err_other，原样值绝不出现
    rec2 = _build_record("syn01")
    rec2["err"] = _SENS_ERR
    out2 = rp.replay_one(rec2)
    public2 = rp._render_replay([out2], rp.Anon(), [])
    _leak_scan(public2)
    assert rp._ERR_OTHER in public2, "未知 err 码应显示 err_other：%s" % public2[-300:]


def test_audit_out落盘_一律过仓库外闸_拒绝即不落盘():
    """失败前提：主审实测 audit --out 在 L613 直接 write_text 绕过仓库外闸。"""
    inside = REPO / "leak_audit_report.md"
    with pytest.raises(ValueError):
        rp._write_text_guarded(str(inside), "# t", "审计报表")
    assert not inside.exists(), "闸拒绝后不得落盘"
    # CLI 路径同样走闸：audit --out 指向仓库内 ⇒ 拒绝且不落盘
    outside = Path(tempfile.mkdtemp(prefix="ambrace_replay_ev_", dir=Path.home()))
    try:
        rows_p = outside / "rows.jsonl"
        rows_p.write_text(json.dumps({"cid": "syn01", "pass": True, "sig": "x",
                                      "fp_spread": ["a"]}, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        rc, out, err = _run_cli(["audit", "--inputs", str(rows_p), "--out", str(inside)],
                                _sandbox_env(outside))
        assert rc == rp.EXIT_BAD, "audit --out 仓库内应当拒绝（rc=%d）" % rc
        assert not inside.exists(), "audit --out 被拒后不得落盘"
    finally:
        import shutil
        shutil.rmtree(outside, ignore_errors=True)


def test_异常与报错只给安全码_不带文件全路径():
    """失败前提：REVIEW §3 元数据/文件名/异常也需处理；报错只给 basename 与类型。"""
    missing = str(REPO / "docs") + "/not_exist_a30_rework.jsonl"
    with pytest.raises(FileNotFoundError) as e:
        rp.load_jsonl(missing)
    msg = str(e.value)
    assert "D:\\" not in msg and "d:\\" not in msg and "/docs/" not in msg, \
        "报错带了全路径：%s" % msg


# ── 修复④：真实 folded rows（首跑 + other_runs）逐跑对账 + 持久化 + sent_fp ──────
def _folded_rows(cid="syn01", n_extra=1):
    """用**原** `fold_runs` 折出真实 folded 形态：顶层＝首跑，other_runs[i] 无 run 字段。"""
    mae = _eval_mod()
    runs = []
    for i in range(1 + n_extra):
        runs.append({"cid": cid, "run": i + 1, "pass": True, "err": "",
                     "actions_seen": ["CAL_NOTE"], "dates_seen": ["2026-11-20"],
                     "exemptions": [], "payloads": ["接孩子放学"], "mem_channel": [],
                     "prompt_fp": "raw%d" % i, "prompt_fp_stable": "stable_same",
                     "reply_head": "好的", "ctx_chars": 30, "n_ctx_msgs": 2})
    return mae.fold_runs(runs)


def test_folded_rows首跑直配_other_runs按指纹不补跑号_缺跑点名():
    """失败前提：主审实测新工具只建顶层 cid/run 键、不展开 other_runs、静默覆盖重复键。"""
    fold = _folded_rows("syn01", n_extra=2)
    rows = [fold]
    recs = [_build_record("syn01", run=1),
            _build_record("syn01", run=2), _build_record("syn01", run=3)]
    for r, fp in zip(recs, ["raw0", "raw1", "raw2"]):
        r["prompt_fp_stable"] = "stable_same"
        r["prompt_fp"] = fp
    results = rp.replay_all(recs)
    recon = rp.reconcile_rows(results, rows)
    assert len(recon) == 3
    assert recon[0]["matched_by"] == "key" and recon[0]["recon"] == "rows_match"
    # 第 2/3 跑：rows 里是 other_runs（无 run 字段）⇒ 按**唯一精确 raw** 匹配、跑号 run_unproven 不补
    assert recon[1]["matched_by"] == "fp_raw" and recon[1]["run_display"] == "run_unproven"
    assert recon[2]["matched_by"] == "fp_raw" and recon[2]["run_display"] == "run_unproven"
    assert all(x["recon"] == "rows_match" for x in recon), recon

    # 缺跑：留底多一跑但 rows 没有 ⇒ rows_missing 点名
    recs4 = recs + [_build_record("syn01", run=4)]
    recs4[3]["prompt_fp"] = "raw_missing"
    r4 = rp.reconcile_rows(rp.replay_all(recs4), rows)
    assert r4[3]["recon"] == "rows_missing"


def test_rows文件内重复cid_run_报conflict绝不静默覆盖():
    """失败前提：REVIEW §4 rows 侧重复键静默覆盖（后来者赢）。"""
    dup = _folded_rows("syn01", n_extra=0)
    dup2 = dict(dup)                            # 同 (cid,run=1) 两行，存值不同
    dup2["pass"], dup2["err"] = False, "E3"
    rows = [dup, dup2]
    recs = [_build_record("syn01", run=1)]
    results = rp.replay_all(recs)
    recon = rp.reconcile_rows(results, rows)
    assert recon[0]["recon"] == rp.R_ROWS_CONFLICT, recon


def test_sent_fp错配_落不可比不进分母_没带则不拦():
    """失败前提：REVIEW §4 sent_fp 常量存在但未使用；错配应不可比、不进有效分母。"""
    rec = _build_record("syn01")
    rec["sent_fp"] = "bad_fp_value"          # 与 sent_fp_of(ctx_messages) 对不上
    out = rp.replay_one(rec)
    assert out["fp"]["sent_fp"] == rp.R_SENT_FP_MISMATCH
    assert rp.R_SENT_FP_MISMATCH in out["reasons"]
    assert out["bucket"] == rp.B_INCOMPARABLE and out["evidence_ok"] is False

    rec2 = _build_record("syn01")
    rec2["sent_fp"] = rp.UNKNOWN             # 没带 ⇒ 不拦
    out2 = rp.replay_one(rec2)
    assert out2["fp"]["sent_fp"] == rp.R_SENT_FP_NA
    assert out2["verdict_match"] is True


def test_rows对账结果随detail_out持久化_不只print():
    """失败前提：REVIEW §4 row_recon 仅打印汇总，--out/--detail-out 未保存逐跑对账。"""
    outside = Path(tempfile.mkdtemp(prefix="ambrace_replay_ev_", dir=Path.home()))
    try:
        ev = outside / "evidence.jsonl"
        rows_p = outside / "rows.jsonl"
        det = outside / "detail.jsonl"
        _rec = _build_record("syn01", run=1)
        # 首跑也核输入维度 ⇒ 让留底与 rows 的指纹坐标对齐，match 才成立
        _rec["prompt_fp_stable"], _rec["prompt_fp"] = "stable_same", "raw0"
        _dump([_rec], ev)
        rows_p.write_text(json.dumps(_folded_rows("syn01", 0), ensure_ascii=False) + "\n",
                          encoding="utf-8")
        rc, out, err = _run_cli(["replay", "--evidence", str(ev), "--rows", str(rows_p),
                                 "--detail-out", str(det)], _sandbox_env(outside))
        assert rc == 0, err[-300:]
        detail = [json.loads(l) for l in det.read_text(encoding="utf-8").splitlines() if l]
        kinds = {d.get("kind") for d in detail}
        assert "row_recon" in kinds, "rows 逐跑对账未随 --detail-out 持久化：%s" % kinds
        rr = next(d for d in detail if d.get("kind") == "row_recon")
        assert rr.get("recon") == "rows_match"
    finally:
        import shutil
        shutil.rmtree(outside, ignore_errors=True)


# ── 修复⑤：历史 vs 当前执行尺子分维闭合 + audit 按来源分组不覆盖 ────────────────
def test_历史尺子与现算尺子不同源_必须明示换尺子复算_不称原尺子复现():
    """失败前提：REVIEW §5 evaluator_now 算了却不比不展示；历史≠当前时不得称原尺子复现。"""
    rec = _build_record("syn01")
    rec["evaluator_source_fp"] = "deadbeef1111"     # 历史执行尺子 ≠ 现算
    out = rp.replay_one(rec)
    assert out["fp"]["evaluator_vs_now"] == "diff"
    assert rp.R_RULER_RECOMPUTED in out["reasons"]
    assert out["evidence_ok"] is False, "换尺子复算不得记证据完整合格"

    rec2 = _build_record("syn01")                    # 历史指纹＝现算 ⇒ same
    out2 = rp.replay_one(rec2)
    assert out2["fp"]["evaluator_vs_now"] == "same"
    assert rp.R_RULER_RECOMPUTED not in out2["reasons"]

    # 缺历史指纹 ⇒ unknown，维持 unknown 不补造
    rec3 = _build_record("syn01")
    rec3["evaluator_source_fp"] = rp.UNKNOWN
    out3 = rp.replay_one(rec3)
    assert out3["fp"]["evaluator_vs_now"] == rp.R_INPUT_FP_UNKNOWN


def test_尺子来源分维_必须出现在公共报表():
    """失败前提：REVIEW §5 这些维度不在公共报表展示。"""
    rec = _build_record("syn01")
    rec["evaluator_source_fp"] = "deadbeef1111"
    public = rp._render_replay([rp.replay_one(rec)], rp.Anon(), [])
    assert "尺子来源分维" in public and "换当前尺子复算" in public
    assert "| diff |" in public or "- `diff`：1" in public
    assert "deadbeef1111" not in public, "指纹原值不得裸进公共报表"


def test_audit同名cid逐文件分组不覆盖_后者不吞前腿():
    """失败前提：REVIEW §5 audit 对相同匿名 cid 逐文件覆盖，最后文件吞掉前腿。"""
    outside = Path(tempfile.mkdtemp(prefix="ambrace_replay_ev_", dir=Path.home()))
    try:
        f1 = outside / "rows_a.jsonl"
        f2 = outside / "rows_b.jsonl"
        row_a = _folded_rows("syn01", n_extra=2)     # 3 跑
        row_b = _folded_rows("syn01", n_extra=0)     # 1 跑
        f1.write_text(json.dumps(row_a, ensure_ascii=False) + "\n", encoding="utf-8")
        f2.write_text(json.dumps(row_b, ensure_ascii=False) + "\n", encoding="utf-8")
        rc, out, err = _run_cli(["audit", "--inputs", str(f1), str(f2)], _sandbox_env(outside))
        assert rc == 0, err[-300:]
        # 同一匿名题两行都要在（按文件分组保留），n_runs 分别 3 / 1，后者不覆盖前者
        assert out.count("A001") >= 2, "同名 cid 被覆盖了：%s" % out[-400:]
        assert "| 3 |" in out and "| 1 |" in out
    finally:
        import shutil
        shutil.rmtree(outside, ignore_errors=True)


# ── 修复⑥：沙箱生命周期回收 / 预绑定 engine 拒绝 / 严格读入 ─────────────────────
def test_子进程跑完_沙箱目录回收干净_不留ambrace_replay_sandbox(tmp_path):
    """失败前提：主审 duplicate_cli.txt 实测子进程结束后残留 ambrace_replay_sandbox_* 两目录。"""
    root = Path(tmp_path)
    env = _sandbox_env(root)                     # TEMP 钉进 root，沙箱 mkdtemp 就在 root 下
    ev = root / "evidence.jsonl"
    _dump([_build_record("syn01")], ev)
    rc, out, err = _run_cli(["replay", "--evidence", str(ev)], env)
    assert rc == 0, err[-300:]
    left = [p.name for p in root.glob("ambrace_replay_sandbox_*")]
    assert not left, "子进程结束后沙箱目录仍残留：%s" % left


def test_沙箱收尾恢复环境变量_不把改写后的TEMP泄漏给调用方(tmp_path):
    """失败前提：REVIEW §6「不恢复环境」——_cleanup_sandbox 必须还原被改写的变量。"""
    code = (
        "import os, sys; sys.path.insert(0, '{BACKEND}');"
        "import importlib.util;"
        "spec = importlib.util.spec_from_file_location('m', '{REPLAY}');"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m);"
        "before = os.environ.get('DATABASE_URL');"
        "m._sandbox_db_env();"
        "mid = os.environ.get('DATABASE_URL','');"
        "m._cleanup_sandbox();"
        "after = os.environ.get('DATABASE_URL');"
        "print('MID_SANDBOX=%s' % ('ambrace_replay_sandbox_' in mid));"
        "print('RESTORED=%s' % (after == before))"
    ).replace("{BACKEND}", str(REPO / "backend")).replace("{REPLAY}", str(REPLAY))
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"      # 可移植：不指任何真实/生产库路径
    p = subprocess.run([_cli_py(), "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=60)
    assert p.returncode == 0, p.stderr[-400:]
    assert "MID_SANDBOX=True" in p.stdout
    assert "RESTORED=True" in p.stdout, "沙箱收尾没恢复 DATABASE_URL：%s" % p.stdout


def test_预绑定非沙箱engine_拒绝执行_不改settings冒充隔离():
    """失败前提：REVIEW §6「拒绝预先绑定的非沙箱 engine，不能仅改 settings 后宣称已隔离」。

    反例用**可移植合成非沙箱路径**（驱动盘根下的假目录，不指任何真实/生产库）。
    """
    import types
    fake = types.ModuleType("app.db.engine")
    fake.engine = type("E", (), {"url": "sqlite+aiosqlite:////synthetic-non-sandbox/fake.db"})()
    saved_sandbox = rp._SANDBOX
    saved_mod = sys.modules.get("app.db.engine")
    rp._SANDBOX = None                        # 强制走完整沙箱路径（含预绑定检查）
    sys.modules["app.db.engine"] = fake
    try:
        with pytest.raises(RuntimeError) as e:
            rp._sandbox_db_env()
        assert "非沙箱" in str(e.value) or "拒绝执行" in str(e.value)
    finally:
        rp._SANDBOX = saved_sandbox           # 恢复原状态，不留第二个沙箱
        if saved_mod is None:
            sys.modules.pop("app.db.engine", None)
        else:
            sys.modules["app.db.engine"] = saved_mod


def test_load_jsonl严格读入_NaN_Infinity_非dict行全拒_不回显行内容():
    """失败前提：REVIEW §6「写出严格不能替代读入严格」——默认接受 NaN/Infinity、不保证每行 dict。"""
    outside = Path(tempfile.mkdtemp(prefix="ambrace_replay_ev_", dir=Path.home()))
    try:
        p_nan = outside / "nan.jsonl"
        p_nan.write_text('{"cid":"syn01","x":NaN,"payload":"泄漏甲甲甲"}\n', encoding="utf-8")
        with pytest.raises(ValueError) as e:
            rp.load_jsonl(str(p_nan))
        assert "NaN" in str(e.value) and "甲甲甲" not in str(e.value), "报错回显了行内容"

        p_inf = outside / "inf.jsonl"
        p_inf.write_text('{"cid":"syn01","x":Infinity}\n', encoding="utf-8")
        with pytest.raises(ValueError):
            rp.load_jsonl(str(p_inf))

        p_arr = outside / "arr.jsonl"
        p_arr.write_text('[1,2,3]\n', encoding="utf-8")
        with pytest.raises(ValueError) as e2:
            rp.load_jsonl(str(p_arr))
        assert "不是 JSON 对象" in str(e2.value)
    finally:
        import shutil
        shutil.rmtree(outside, ignore_errors=True)


# ── 修复②收口：未经比较的字段不得称逐字一致 ────────────────────────────────────
def test_被比字段含unknown或not_compared_不称逐字一致_不进一致分母():
    """失败前提：REVIEW §2「why/exemptions/… 未经比较的字段不能称逐字一致」。"""
    rec = _build_record("syn01")
    rec["exemptions"] = rp.UNKNOWN            # 存值缺读数 ⇒ 该字段不可比
    out = rp.replay_one(rec)
    assert out["fields"]["exemptions"] == "unknown"
    assert out["verdict_match"] is None, "含 unhealed unknown 却称逐字一致"
    assert rp.R_PARTIAL_FIELDS in out["reasons"]
    assert out["evidence_ok"] is False


# ═══════════════ 第二轮返工守卫（REVIEW2 §1–§6，2026-10-15）═══════════════
# 每条＝失败前提（主审实测）＋修复后断言；断言最终桶/分母/匹配/证据合格/隐私，不只查原因码。
# ── ② 严格可比分母：partial 字段不得留在有效桶 ──────────────────────────────
def test_partial字段落incomparable桶_不进有效分母_报表分母归零():
    """失败前提：REVIEW2 §2 实测 exemptions=unknown 时 bucket=effective、报表仍 0/1。"""
    rec = _build_record("syn01")
    rec["exemptions"] = rp.UNKNOWN
    out = rp.replay_one(rec)
    assert out["fields"]["exemptions"] == "unknown"
    assert out["bucket"] == rp.B_INCOMPARABLE, "partial 仍在有效桶：%s" % out["bucket"]
    assert out["verdict_match"] is None and out["evidence_ok"] is False
    line = [x for x in rp._render_replay([out], rp.Anon(), []).splitlines()
            if "判分局部一致（" in x]
    assert line and "| 0 | 0 |" in line[0], "报表分母没归零：%s" % line


def test_四桶互斥完备_合计等于总行数与报表一致(outside_repo):
    """失败前提：REVIEW2 §2 要求核对四桶与汇总数字。"""
    # 4 行：正常(effective)、重复×2(conflict)、未知 stage(incomparable)
    recs = [_build_record("syn01"),
            _build_record("syn02"), _build_record("syn02"),
            _build_record("syn03", stage="weird_stage")]
    ev = outside_repo / "evidence.jsonl"
    _dump(recs, ev)
    rc, out, err = _run_cli(["replay", "--evidence", str(ev)], _sandbox_env(outside_repo))
    assert rc == rp.EXIT_CONFLICT, "重复键应给冲突退出码：%s" % out[-300:]
    assert "| effective | 1 |" in out
    assert "| conflict | 2 |" in out
    assert "| incomparable | 1 |" in out
    assert "四桶合计 4 ＝ 总行数 4（一致）" in out, "四桶合计与总数不一致：%s" % out[-500:]


# ── ③ 零动作是真读数：[] 与 [] 应 match ────────────────────────────────────
def _none_case():
    c = _synth_case()
    c["expect"] = {"action_type": ["NONE"], "field_match": {}, "forbidden": []}
    c["time_anchor"] = {"as_of": "2026-11-19"}
    return c


def test_零动作留底actions_full空表_action_raw为match_可证据合格():
    """失败前提：REVIEW2 §3 实测 NONE 题（actions_full=[]）被误读为 action_raw=unknown。"""
    case = _none_case()
    zero = _build_record(case=case, reply="好的。")
    assert zero["actions_full"] == [], "夹具不是零动作留底：%s" % zero["actions_full"]
    out = rp.replay_one(zero, case_map={zero["cid"]: case})
    assert out["fields"]["action_raw"] == "match", "空表被当缺失：%s" % out["fields"]
    assert out["verdict_match"] is True
    assert out["bucket"] == rp.B_EFFECTIVE and out["evidence_ok"] is True


def test_零动作缺失或非法结构_action_raw仍unknown_不伪造成空表():
    """反向对照：None/unknown/非 dict 元素 ⇒ unknown（不得把缺失改成 []）。"""
    case = _none_case()
    for bad, label in ((rp.UNKNOWN, "unknown 串"), (None, "None"), (["bogus"], "非 dict 元素")):
        rec = _build_record(case=case, reply="好的。")
        rec["actions_full"] = bad
        out = rp.replay_one(rec, case_map={rec["cid"]: case})
        assert out["fields"]["action_raw"] == "unknown", "%s 竟成 %s" % (label, out["fields"]["action_raw"])
        assert out["verdict_match"] is None and rp.R_PARTIAL_FIELDS in out["reasons"]


# ── ① 题面绑定：留存指纹 verified 不等于实际执行判据来自该题面 ────────────────
def test_改判据但留存题面指纹仍verified_必须报criteria_diff且证据不合格():
    """失败前提：REVIEW2 §1 实测只改 expect.field_match（判分字段仍全一致）却 evidence_ok=True。"""
    orig = _synth_case("syn01")
    # 基线（判据与题面同源）应可证据合格
    base = rp.replay_one(_build_record("syn01"), case_map={"syn01": orig})
    assert base["fp"]["criteria_binding"] == "match" and base["evidence_ok"] is True

    altered = _build_record("syn01")
    altered["expect"]["field_match"]["*"] = "放学"    # 两个片段都在动作里 ⇒ 判分字段仍全 match
    out = rp.replay_one(altered, case_map={"syn01": orig})
    assert out["fp"]["case_source"] == "verified", "题面指纹仍应 verified（反例要的就是这个）"
    assert out["fp"]["criteria_binding"] == "diff"
    assert out["criteria_dims"]["expect"] == "diff"
    assert rp.R_CRITERIA_MISMATCH in out["reasons"]
    assert out["verdict_match"] is True               # 判分恰好仍一致 —— 正是盲区
    assert out["evidence_ok"] is False, "改了判据仍报证据完整合格"


def test_缺独立题面时criteria_binding为unknown_不得证据合格():
    """缺证据（无 --dataset）⇒ 判据绑定 unknown ⇒ 不得证据合格，不补造。"""
    rec = _build_record("syn01")
    out = rp.replay_one(rec)                          # 无 case_map
    assert out["fp"]["criteria_binding"] == rp.R_INPUT_FP_UNKNOWN
    assert out["evidence_ok"] is False


# ── ④ folded rows 不串跑：raw/stable 分维、唯一精确 raw、歧义不猜配、首跑核输入 ──
def test_folded三跑stable同raw异_倒置other_runs不得由全match变两diff():
    """失败前提：REVIEW2 §4 复现 A——仅倒置 other_runs 即由 match×3 变 match/diff/diff。"""
    recs = [_build_record("syn01", run=i) for i in (1, 2, 3)]
    for r, fp in zip(recs, ["raw0", "raw1", "raw2"]):
        r["prompt_fp_stable"], r["prompt_fp"] = "stable_same", fp
    recs[2]["pass"], recs[2]["err"] = False, "E3"      # 第 3 跑不过（判分仍完整）
    folded = _folded_rows("syn01", n_extra=2)
    for key in ("pass", "err", "prompt_fp", "prompt_fp_stable"):
        folded[key] = recs[0][key]
        for rr, src in zip(folded["other_runs"], recs[1:]):
            rr[key] = src[key]
    results = rp.replay_all(recs)
    good = rp.reconcile_rows(results, [folded])
    rev = copy.deepcopy(folded)
    rev["other_runs"].reverse()
    bad = rp.reconcile_rows(results, [rev])
    assert [x["recon"] for x in good] == ["rows_match"] * 3, good
    assert [x["recon"] for x in bad] == ["rows_match"] * 3, \
        "倒置 other_runs 后串跑了（共享 stable 压过可精确区分的 raw）：%s" % bad
    # 第 2/3 跑都靠唯一精确 raw 命中，不是靠共享 stable
    assert [x["matched_by"] for x in bad][1:] == ["fp_raw", "fp_raw"], bad


def test_folded共享stable多候选_报ambiguous不猜配():
    """失败前提：REVIEW2 §4——无唯一匹配证据时仍贪心取一条。"""
    fold = _folded_rows("syn01", n_extra=2)           # others raw1/raw2，stable 均 stable_same
    rec = _build_record("syn01", run=2)
    rec["prompt_fp"], rec["prompt_fp_stable"] = "rawX", "stable_same"   # 无精确 raw 命中
    recon = rp.reconcile_rows(rp.replay_all([rec]), [fold])
    assert recon[0]["recon"] == rp.R_ROWS_AMBIGUOUS, recon
    assert recon[0]["matched_by"] == "ambiguous"


def test_folded_raw矛盾不得被共享stable吞掉_报ambiguous():
    """唯一 stable 候选但其 raw 与留底矛盾 ⇒ 不配（不得用共享 stable 吞掉 raw 冲突）。"""
    fold = _folded_rows("syn01", n_extra=1)           # top(run1,raw0) + other(raw1)，stable 同
    r1 = _build_record("syn01", run=1)                # 先按键消费掉 top
    r2 = _build_record("syn01", run=2)
    r2["prompt_fp"], r2["prompt_fp_stable"] = "rawX", "stable_same"
    recon = rp.reconcile_rows(rp.replay_all([r1, r2]), [fold])
    assert recon[1]["recon"] == rp.R_ROWS_AMBIGUOUS, recon[1]


def test_folded首跑键相同但rows输入错配_不得rows_match():
    """失败前提：REVIEW2 §4 复现 B——首跑键不变、rows 侧 raw/stable 全改，pass/err 相同仍 rows_match。"""
    fold = _folded_rows("syn01", n_extra=0)
    wrong = copy.deepcopy(fold)
    wrong["prompt_fp_stable"], wrong["prompt_fp"] = "DIFFERENT_INPUT", "DIFFERENT_RAW"
    rec = _build_record("syn01", run=1)               # pass/err 与 rows 相同
    assert rec["pass"] == wrong["pass"]
    recon = rp.reconcile_rows(rp.replay_all([rec]), [wrong])
    assert recon[0]["matched_by"] == "key"
    assert recon[0]["recon"] == "rows_diff", "不同输入仍称 rows_match：%s" % recon[0]
    assert recon[0]["input"] == "diff"


@pytest.mark.parametrize("has_other", [False, True], ids=["known-run-only", "unnumbered-extra"])
def test_folded已知跑号不降格为未知跑号_且不抢占后来按键匹配(has_other):
    """相同输入不证明相同跑次；run2 不得消费明确标为 run1 的行。"""
    recs = [_build_record("syn01", run=i) for i in (2, 1)]
    folded = _folded_rows("syn01", n_extra=int(has_other))
    # 两跑输入及判分确实相同，不能靠指纹不同来绕开跑号约束。
    for row in [folded] + folded["other_runs"]:
        for key in ("pass", "err", "prompt_fp", "prompt_fp_stable"):
            row[key] = recs[0][key]
    recon = rp.reconcile_rows(rp.replay_all(recs), [folded])
    if has_other:
        assert recon[0]["recon"] == "rows_match"
        assert recon[0]["matched_by"] == "fp_raw"
        assert recon[0]["rows_run"] == "run_unproven"
    else:
        assert recon[0]["recon"] == "rows_missing"
        assert recon[0]["matched_by"] is None and recon[0]["rows_run"] is None
    assert recon[1]["matched_by"] == "key", "已知 run1 不可被前面的 run2 抢占"
    assert recon[1]["rows_run"] == 1 and recon[1]["recon"] == "rows_match"


def test_folded缺跑_报rows_missing不补造跑号():
    """缺跑：留底有 run 而 rows 无 ⇒ rows_missing，跑号不凭空补。"""
    fold = _folded_rows("syn01", n_extra=0)
    rec = _build_record("syn01", run=2)
    rec["prompt_fp"], rec["prompt_fp_stable"] = "nowhere", "nowhere2"
    recon = rp.reconcile_rows(rp.replay_all([rec]), [fold])
    assert recon[0]["recon"] == "rows_missing"
    assert recon[0]["matched_by"] is None and recon[0]["rows_run"] is None


# ── ⑤ 公共输出匿名：audit 聚合白名单 + 全错误分支安全码 ──────────────────────
_SENS_AGG = "SYNTH_PRIVATE_AGGREGATE_MARK"
_SENS_FN = "SYNTH_PRIVATE_FILENAME_MARK"


def test_audit聚合字段非原生类型_公共输出零泄漏且显示unknown(outside_repo):
    """失败前提：REVIEW2 §5 复现 A——clock_only_drift/n_runs 置合成串，报表原样出现。"""
    fold = _folded_rows("syn01", n_extra=2)
    fold["clock_only_drift"] = _SENS_AGG
    fold["n_runs"] = _SENS_AGG
    f = outside_repo / "audit.jsonl"
    _dump([fold], f)
    rc, out, err = _run_cli(["audit", "--inputs", str(f)], _sandbox_env(outside_repo))
    assert rc == 0, err[-300:]
    blob = out + err
    assert _SENS_AGG not in blob, "聚合敏感串泄漏：%s" % blob[-400:]
    assert "unknown" in out, "非法聚合值应显示 unknown：%s" % out[-400:]


def test_缺失dataset敏感文件名_错误分支只给安全码不回显():
    """失败前提：REVIEW2 §5 复现 B——--dataset 指向缺失的敏感名文件，stderr 原样出现文件名。"""
    outside = Path(tempfile.mkdtemp(prefix="ambrace_replay_ev_", dir=Path.home()))
    try:
        ev = outside / "evidence.jsonl"
        _dump([_build_record("syn01")], ev)
        rc, out, err = _run_cli(
            ["replay", "--evidence", str(ev), "--dataset", str(outside / (_SENS_FN + ".jsonl"))],
            _sandbox_env(outside))
        assert rc == rp.EXIT_BAD, "缺失输入应拒绝（rc=%d）" % rc
        blob = out + err
        assert _SENS_FN not in blob, "错误分支回显了文件名：%s" % blob[-300:]
        assert ".jsonl" not in blob, "错误分支仍回显 basename：%s" % blob[-300:]
    finally:
        import shutil
        shutil.rmtree(outside, ignore_errors=True)


def test_仓库内拒写路径错误_不回显basename():
    """拒写分支同样只给安全码/匿名标签。"""
    with pytest.raises(ValueError) as e:
        rp.guard_outside_repo(str(REPO / (_SENS_FN + ".md")), "复算报表")
    msg = str(e.value)
    assert _SENS_FN not in msg and ".md" not in msg, "拒写错误回显了 basename：%s" % msg


# ── 12 判据函数 AST 与基线比较（第二轮⑥：仓库内可移植夹具，不依赖作者机工作目录）────
def test_原判据12函数AST与基线逐函数一致_不是只看文件名():
    """比 `ast.dump` 哈希：判据本体一个字节都不许变（getsourcefile 只证明文件名，证不了内容）。

    基线＝仓库内纯代码指纹夹具 `backend/tests/fixtures/ast_baseline_memory_action_eval.json`
    （仅函数名＋sha256＋源码内容指纹，无路径），任何机器缺它＝真红，不 skip。
    """
    import ast
    base_p = Path(__file__).resolve().parent / "fixtures" / "ast_baseline_memory_action_eval.json"
    assert base_p.is_file(), "AST 基线夹具缺失（须随源码携带）：%s" % base_p.name
    fixture = json.loads(base_p.read_text(encoding="utf-8"))
    baseline = fixture["functions"]
    tree = ast.parse(EVAL.read_text(encoding="utf-8-sig"))
    now = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            now[node.name] = hashlib.sha256(ast.dump(node).encode("utf-8")).hexdigest()
    for fn, want in baseline.items():
        assert fn in now, "原判据函数 %s 不见了" % fn
        assert now[fn] == want, \
            "原判据函数 %s 的 AST 变了 ⇒ 判据本体被动过（基线 %s… / 现 %s…）" % (
                fn, want[:16], now[fn][:16])
