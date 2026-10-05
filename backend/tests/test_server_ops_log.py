# -*- coding: utf-8 -*-
"""server_manager 运维留痕（server_ops.log）守卫（2026-10-05 立）。

背景：2026-10-05 02:25:39→03:20:09 服务器停摆 **54.5 分钟**，事后查「是谁停的」只能靠
app.log 空档 + agent_task_logs 写入断点 + Windows 事件日志反推（结论：无进程崩溃记录，
watchdog 与 uvicorn 同时消失，与 `server_manager stop`/控制台停止按钮的行为完全吻合）。
根因是**结构性的**：``log()`` 只 print 到当前窗口，没人看就全丢；``paused.flag`` 一被
``start`` 删掉，「人为停」与「被杀」就再也分不开。

本文件钉住三件事：
1. ``ops_log`` 的行格式＝「时间 | 子命令 | 消息」，且**任何写盘失败都不许影响主流程**；
2. ``clear_pause`` 删标记前必须先把标记原文落进运维日志（否则归因链断在最关键的一环）；
3. 留痕点（stop_all / clear_pause / _ensure_started / cmd_status）不许被悄悄删掉——
   用 AST 读源码核，不靠跑真启停（跑真启停会打断生产服务）。
"""
import ast
import importlib.util
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "scripts"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_sm_src = _SCRIPTS / "server_manager.py"


def _load_sm(name: str = "_test_server_ops_log"):
    spec = importlib.util.spec_from_file_location(name, str(_sm_src))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _redirect_paths(mod, tmp_path):
    """把日志/标记/锁文件路径全部指向 tmp_path（绝不写真实的 backend/data）。

    WATCHDOG_PID_FILE 尤其要改：``clean_lock_files()`` 会**删掉**它，
    指回真实路径就是当场删掉正在运行的 watchdog 的 pid 文件（下次可能拉起第二个守护）。
    """
    logs = tmp_path / "logs"
    logs.mkdir()
    mod.LOGS_DIR = str(logs)
    mod.OPS_LOG = str(logs / "server_ops.log")
    mod.PAUSE_FLAG = str(tmp_path / "paused.flag")
    mod.LOCKS_DIR = str(tmp_path / "locks")
    mod.WATCHDOG_PID_FILE = str(tmp_path / "locks" / "watchdog.pid")
    return logs


def _read_ops(mod):
    p = Path(mod.OPS_LOG)
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ── 1. 行格式：时间 | 子命令 | 消息 ─────────────────────────────────────
def test_运维日志行格式是时间_子命令_消息(tmp_path, monkeypatch):
    sm = _load_sm()
    _redirect_paths(sm, tmp_path)
    monkeypatch.setattr(sys, "argv", ["server_manager.py", "stop"])

    sm.ops_log("杀进程 watchdog=[111] uvicorn=[222]")

    line = _read_ops(sm).strip()
    parts = line.split(" | ")
    assert len(parts) == 3, f"应为三段「时间|子命令|消息」，实得 {parts}"
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", parts[0]), parts[0]
    assert parts[1] == "stop", f"子命令段必须是调用方 argv[1]，实得 {parts[1]}"
    assert "watchdog=[111]" in parts[2]


def test_多次动服务按顺序追加不覆盖(tmp_path, monkeypatch):
    sm = _load_sm()
    _redirect_paths(sm, tmp_path)
    monkeypatch.setattr(sys, "argv", ["server_manager.py", "stop"])
    sm.ops_log("a")
    monkeypatch.setattr(sys, "argv", ["server_manager.py", "start"])
    sm.ops_log("b")
    lines = _read_ops(sm).strip().splitlines()
    assert [l.split(" | ")[1] for l in lines] == ["stop", "start"], lines


# ── 2. 删暂停标记前先留住原文（归因链的关键一环）──────────────────────
def test_清除暂停标记前把原文落进运维日志(tmp_path):
    sm = _load_sm()
    _redirect_paths(sm, tmp_path)
    Path(sm.PAUSE_FLAG).write_text("stopped by server_manager 2026-10-05 02:25:41", encoding="utf-8")

    sm.clear_pause()

    assert not Path(sm.PAUSE_FLAG).exists(), "标记没删掉＝守护会一直被暂停"
    ops = _read_ops(sm)
    assert "2026-10-05 02:25:41" in ops, f"标记原文没留底，下次仍分不清人为停/被杀：{ops!r}"


def test_没有暂停标记时不写留痕_阳性对照(tmp_path):
    """阳性对照：没标记就不该有「清除暂停标记」这行。

    少了这条断言，「无条件写日志」的实现也能骗过上一个用例。
    """
    sm = _load_sm()
    _redirect_paths(sm, tmp_path)
    sm.clear_pause()
    assert _read_ops(sm) == "", "无标记却写了留痕＝日志会淹掉真实信号"


def test_运维日志写不进去也不能阻断停服(tmp_path):
    """写盘失败（这里让 OPS_LOG 指向一个目录）必须被吞掉：运维日志丢了不如服务停错要紧。"""
    sm = _load_sm()
    logs = tmp_path / "logs"
    logs.mkdir()
    sm.LOGS_DIR = str(logs)
    sm.OPS_LOG = str(logs)                       # 目录 → open() 抛 IsADirectoryError
    sm.PAUSE_FLAG = str(tmp_path / "paused.flag")
    Path(sm.PAUSE_FLAG).write_text("stopped", encoding="utf-8")

    sm.ops_log("x")                              # 不许抛
    sm.clear_pause()                             # 不许抛，且标记照删
    assert not Path(sm.PAUSE_FLAG).exists()


# ── 3. 留痕点不许被悄悄删掉（AST 读源码，不跑真启停）──────────────────
def _func_calls(src: str, func_name: str) -> set[str]:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return {c.func.id for c in ast.walk(node)
                    if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    raise AssertionError(f"server_manager.py 里找不到函数 {func_name}（被改名/删掉都要先说清楚）")


def _read_tail_targets(src: str, func_name: str) -> set[str]:
    """cmd_status 里 read_tail(X) 的 X（只认直接写常量名的调用）。

    单看「有没有调 read_tail」是不够的：stderr/watchdog 两处还在，删掉运维日志那一处
    照样过——变异实测就是这样漏的，所以按**读的是哪个文件**核。
    """
    tree = ast.parse(src)
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for c in ast.walk(node):
                if (isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                        and c.func.id == "read_tail" and c.args
                        and isinstance(c.args[0], ast.Name)):
                    out.add(c.args[0].id)
    return out


def test_四个留痕点仍在原地():
    src = _sm_src.read_text(encoding="utf-8")
    for fn in ("stop_all", "clear_pause", "_ensure_started"):
        assert "ops_log" in _func_calls(src, fn), f"{fn}() 里的 ops_log 留痕被删了＝下次停摆又查不到谁干的"
    read = _read_tail_targets(src, "cmd_status")
    assert {"STDERR_LOG", "WATCHDOG_LOG", "OPS_LOG"} <= read, (
        f"status 体检必须同时带三份日志尾部，实得 {sorted(read)}——少了运维日志就断归因链")


def test_运维日志常量仍落在_logs_目录():
    sm = _load_sm()
    assert sm.OPS_LOG.endswith("server_ops.log")
    assert sm.OPS_LOG.startswith(sm.LOGS_DIR), "路径挪出 logs/ 会绕开备份与轮转口径"


def test_stop_all把杀掉的进程号记进运维日志(tmp_path, monkeypatch):
    """全打桩跑 stop_all（不真杀进程、不打 PowerShell）：留痕必须点名 PID。

    为什么单独测这条：生产里没人会把 stop_all 真跑一遍来验证日志，
    而「谁停了服务」这个问句要的恰好就是 PID 与时刻。
    """
    sm = _load_sm()
    _redirect_paths(sm, tmp_path)
    killed: list = []
    monkeypatch.setattr(sm, "log", lambda msg: None)
    monkeypatch.setattr(sm, "kill_pids", lambda pids, force=False: killed.extend(pids))
    # 端口/命令行查询：watchdog 侧 111/112，uvicorn 侧 222；停止后一律「端口已空」让等待循环立刻退出
    table = {sm.LOCK_PORT_WATCHDOG: [111], sm.PORT: [222], sm.LOCK_PORT_UVICORN: []}
    monkeypatch.setattr(sm, "get_port_pids", lambda port: list(table.get(port, [])))
    monkeypatch.setattr(sm, "get_cmdline_pids",
                        lambda kw: [112] if "watchdog" in kw else ([223] if "uvicorn" in kw else []))
    monkeypatch.setattr(sm.time, "sleep", lambda s: None)

    sm.stop_all()

    ops = _read_ops(sm)
    assert "watchdog=[111, 112]" in ops, f"留痕没记全 watchdog PID：{ops!r}"
    assert "uvicorn=[222]" in ops, f"留痕没记 uvicorn PID：{ops!r}"
    assert sorted(killed) == [111, 112, 222, 223], killed
    assert Path(sm.PAUSE_FLAG).exists(), "stop 之后必须留暂停标记，否则 watchdog 会立刻拉起"
