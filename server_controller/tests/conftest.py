"""控制台测试基座：离屏起窗 + 全量打桩（**不发真实 HTTP、不连任何数据库文件**）。

为什么必须有这几个文件：控制台此前零测试覆盖，2026-09-20 的「账号页恒显示 0 行」
是走**成功分支**的静默解析回归（`for r in <dict>` 迭代出键名被 isinstance 全滤掉），
py_compile 与后端 pytest 都抓不到它。这里把两类检查落成可重复执行的资产：

- `test_console_smoke.py`：离屏（`withdraw()`）真起一次窗口，逐页构造 + 逐页取数渲染，
  断言"接口给了 N 条 → 界面就有 N 行"。控件构造期的 TypeError/NameError 只有这条路能抓。
- `test_console_design.py`：纯逻辑自检（字号阶梯、色阶梯、列权重、栅格参数），不需要窗口。

红线是结构性的，不靠自觉：`sc.sqlite3` 被整体换成"一调就抛"的桩，任何代码路径试图
开库都会立刻失败；HTTP 只有 `_http_json` 一个出口，替换它即覆盖管理面 + 登录 + 公开
开关端点，健康/活性与 socket 探活也一并打桩，冒烟全程不碰网络。
"""
from __future__ import annotations

import pathlib
import sys
import types

import pytest

CONSOLE_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(CONSOLE_DIR) not in sys.path:
    sys.path.insert(0, str(CONSOLE_DIR))

import server_controller as sc  # noqa: E402

API = sc.ADMIN_API_PREFIX

# ── 打桩响应（字段口径照 docs 里的管理面契约；条数故意 >0，才能区分
#    「后端真的没数据」和「解析写错导致 0 行」——后者正是 W0 那个 bug 的形状）──

MODALITY_KEYS = ("llm", "multimodal", "image", "vlm", "speech")


def modality_payload():
    return {"modalities": [
        {"key": k, "label": "模态 " + k, "provider": "dashscope", "enabled": i % 2 == 0,
         "model": "qwen3.5-omni-plus-2026-12-01",
         "base_url": "https://ws-jklo8eko4a7.cn-beijing.example.com/compatible-mode/v1",
         "daily_limit": None if k == "llm" else 500, "has_api_key": k != "speech"}
        for i, k in enumerate(MODALITY_KEYS)]}


def accounts_payload(query: str = ""):
    """默认 3 个正常账号；`?include_deleted=true` 多给一个回收站行（宽限期按现在 +5 天）。

    回收站行只在「显示回收站」打开时出现，正是后端 `include_deleted` 的口径；
    关着就能拿到 4 行的话，「默认隐藏回收站」这条断言就永远钉不住了。
    """
    rows = [
        {"id": 1, "username": "alpha", "nickname": "阿法", "is_admin": True,
         "server_admin": True, "disabled_at": None, "llm_mode": "own",
         "llm_total_limit": 8000, "llm_total_limit_source": "user"},
        {"id": 2, "username": "beta", "nickname": "贝塔", "is_admin": False,
         "server_admin": False, "disabled_at": "2026-09-01T02:03:04", "llm_mode": "blocked",
         "llm_total_limit": None, "llm_total_limit_source": "unset"},
        {"id": 3, "username": "gamma", "nickname": "", "is_admin": False,
         "server_admin": True, "disabled_at": None, "llm_mode": "default_allowed",
         "llm_total_limit": 3000, "llm_total_limit_source": "global"},
    ]
    if "include_deleted=true" in (query or ""):
        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
        rows.append({"id": 4, "username": "delta", "nickname": "德尔塔", "is_admin": True,
                     "server_admin": False, "disabled_at": now.isoformat(timespec="seconds"),
                     "deleted_at": now.isoformat(timespec="seconds"),
                     "purge_after": (now + _dt.timedelta(days=5)).isoformat(timespec="seconds"),
                     "llm_mode": "default_allowed",
                     "llm_total_limit": 3000, "llm_total_limit_source": "global"})
    return {"accounts": rows}


def delete_dry_run_payload():
    """beta（id=2）可删：家庭根、两张表 1200 行、1 条例外、3 行判不出归属。"""
    return {"user_id": 2, "username": "beta", "nickname": "贝塔",
            "mode": "delete_family_root", "already_deleted": False, "grace_days": 7,
            "guards": [], "may_delete": True,
            "totals": {"tables": 2, "row_count": 1200, "undetermined_rows": 3,
                       "editor_only_rows": 7},
            "tables": [
                {"table": "chat_messages", "rows": 900,
                 "columns": [{"column": "user_id", "kind": "ownership", "rows": 900,
                              "deletable": True}]},
                {"table": "memories", "rows": 300,
                 "columns": [{"column": "user_id", "kind": "ownership", "rows": 300,
                              "deletable": True},
                             {"column": "speaker_id", "kind": "dual", "rows": 0,
                              "deletable": True},
                             # 命中但不作为删除依据的列（后端 totals.editor_only_rows 那类）：
                             # 确认卡的「命中归属列」不许把它混进来
                             {"column": "editor_user_id", "kind": "ownership", "rows": 7,
                              "deletable": False}]},
            ],
            "exceptions": [{"table": "admin_audit_logs", "column": "actor_user_id",
                            "family": "user", "reason": "审计留档， actor 列不作为删除依据"}],
            "undetermined_speaker_rows": [{"table": "group_messages", "column": "speaker_id",
                                           "rows": 3}],
            "warnings": [], "character_ids": [21],
            "purge_now_allowed_below": 2000, "purge_now_would_be_allowed": True,
            "dry_run": True}


def delete_dry_run_guarded_payload():
    """alpha（id=1）不许删：命中「最后一个 server_admin」→ 确认卡必须挡住。"""
    payload = delete_dry_run_payload()
    payload.update({"user_id": 1, "username": "alpha", "nickname": "阿法",
                    "may_delete": False,
                    "guards": ["这是最后一个服务器管理员账号，删掉后控制台再也进不去"]})
    return payload


def delete_marked_payload():
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    return {"status": "ok", "user_id": 2, "username": "beta", "mode": "delete_family_root",
            "deleted_at": now.isoformat(timespec="seconds"),
            "purge_after": (now + _dt.timedelta(days=7)).isoformat(timespec="seconds"),
            "purge_now": False, "totals": {"tables": 2, "row_count": 1200,
                                           "undetermined_rows": 3}}


def restore_account_payload():
    return {"status": "ok", "user_id": 4, "username": "delta", "restored": True}


PURGE_REPORT = {
    "user_id": 4,
    "job": {"id": 9, "user_id": 4, "status": "done",
            "started_at": "2026-09-24T02:00:00", "finished_at": "2026-09-24T02:00:41",
            "error": None, "attempts": None},
    "report": {"job_id": 9, "user_id": 4, "username": "delta", "mode": "delete_family_root",
               "status": "done", "rows_deleted": 1200,
               "tables": [{"table": "chat_messages", "rows": 900},
                          {"table": "memories", "rows": 300}],
               "files": {"trash_dir": "data/trash/4", "upload_dir_count": 6,
                         "files_moved": 11, "partial_dirs": []},
               "vectors": {"deleted": 300}, "bm25": {"characters": 1, "invalidated": 1},
               "backup_zip": "backups/20260924.zip",
               "foreign_key_check": [], "foreign_key_check_rows": 0,
               "frozen": {"character_count": 1, "session_count": 2, "memory_count": 300},
               "elapsed_seconds": 41.2},
    "cursor": {"stages_done": ["backup_zip", "frozen", "files", "vectors", "bm25",
                               "tables_done"],
               "next_stage": None,
               "tables_done": [{"table": "chat_messages", "rows": 900},
                               {"table": "memories", "rows": 300}],
               "tables_done_count": 2, "rows_deleted_so_far": 1200, "blocked_reason": None},
}


def purge_report_payload():
    import copy
    return copy.deepcopy(PURGE_REPORT)


def purge_now_payload():
    """`POST .../purge` 的回包就是清除器报告本身（_compact 的那几个字段）。"""
    report = purge_report_payload()["report"]
    report.update({"already_done": False, "idempotent": False})
    return report


def purge_report_absent_payload():
    """从没被清过的账号：账本没有作业行 → 200 + `job: null`（不是 404）。"""
    return {"user_id": 2, "job": None}


def flags_payload(n: int = 12):
    rows = []
    for i in range(n):
        rows.append({"key": "flag_%02d" % i, "enabled": i % 3 == 0,
                     "self_service": i % 4 == 0, "server_locked": i % 5 == 0,
                     "value": i % 3 == 0, "title": None, "desc": None})
    return {"flags": rows}


def public_flags_payload(n: int = 12):
    groups = ("agent", "proactive", "games", "memory")
    rows = []
    for i in range(n):
        rows.append({"key": "flag_%02d" % i, "source": "db" if i % 6 == 0 else "default",
                     "scope": "user" if i % 7 == 0 else "server",
                     "user_enabled": i % 2 == 0,
                     "meta": {"title": "开关 %d" % i, "desc": "说明 " + ("长" * 60),
                              "group": groups[i % len(groups)],
                              "group_order": i % len(groups), "order": i,
                              "visible": i < 3}})
    return {"flags": rows}


def audit_payload(n: int = 5):
    return {"entries": [
        {"created_at": "2026-09-2%dT10:00:00" % (i % 10), "actor_username": "alpha",
         "action": "flag.update", "target": "flag_%02d" % i,
         "before": {"enabled": False}, "after": {"enabled": True, "api_key": "sk-secret"}}
        for i in range(n)]}


def registration_payload():
    return {"mode": "open", "invite_codes": 3}


def overview_payload():
    return {"accounts": 25, "disabled": 2, "server_admins": 3, "flags_on": 69,
            "version": "3.4.1", "extra_field": "后端多给的字段也要显示"}


def device_actions_payload():
    return {"switches": {"global": True, "plugin_enabled": False, "force_dry_run": True,
                         "rows_present": {"global": True, "plugin_enabled": True,
                                          "force_dry_run": True}},
            "targets": ["com.example.app", "com.other.app"],
            "plugins": ["browser_mcp"],
            "limits": {"targets_max": 8, "plugins_max": 5}, "tenant_id": "t-1"}


def llm_limit_payload():
    return {"total_limit": 20000, "source": "global"}


def heatmap_payload(days: int = 26 * 7):
    """近 N 周网格：末尾留 3 天"未来"占位，前 22 周故意全 0（复现"最低档看不见"的场景）。"""
    import datetime as _dt
    start = _dt.date(2026, 3, 2)
    out = []
    for i in range(days):
        d = start + _dt.timedelta(days=i)
        future = i >= days - 3
        tok = 0 if (future or i < 154) else 100 + i * 7
        out.append({"date": d.isoformat(), "tokens": tok, "future": future,
                    "tasks": {} if tok == 0 else {"聊天": tok}})
    return out


# ── 打桩基座 ────────────────────────────────────────────────────────

class HttpCalls:
    """记录每一次"HTTP"，让测试能断言没有任何端点绕过打桩直接出去。

    删号第三期起，管理面不再只有 GET：dry-run / delete / restore / purge 是 POST，
    且必须带 body 断言位（`bodies`）——「确认删除只在后缀用户名匹配时才发出去」这条
    护栏得能看到请求体长什么样。登记表里没写的端点照旧直接 AssertionError。
    """

    def __init__(self):
        self.calls = []
        self.bodies = []

    def __call__(self, method, path, body=None, token="", timeout=None):
        self.calls.append((method, path))
        if body is not None:
            self.bodies.append((method, path, body))
        key = path.split("?")[0]
        query = path.partition("?")[2]
        gets = {
            API + "/modalities": modality_payload,
            API + "/accounts": lambda: accounts_payload(query),
            API + "/flags": flags_payload,
            API + "/audit": audit_payload,
            API + "/registration": registration_payload,
            API + "/overview": overview_payload,
            API + "/device-actions": device_actions_payload,
            API + "/llm-limit": llm_limit_payload,
            API + "/accounts/4/purge-report": purge_report_payload,
            API + "/accounts/2/purge-report": purge_report_absent_payload,
            "/api/v1/system/feature-flags": public_flags_payload,
        }
        posts = {
            API + "/accounts/1/delete-dry-run": delete_dry_run_guarded_payload,
            API + "/accounts/2/delete-dry-run": delete_dry_run_payload,
            API + "/accounts/2/delete": delete_marked_payload,
            API + "/accounts/2/restore": restore_account_payload,
            API + "/accounts/4/restore": restore_account_payload,
            API + "/accounts/4/purge": purge_now_payload,
            sc.AUTH_LOGIN_PATH: lambda: {"access_token": "t", "username": "alpha",
                                         "user_id": 1},
        }
        table = posts if method == "POST" else gets
        if key not in table:
            raise AssertionError("冒烟出现了未登记的端点：%s %s" % (method, path))
        return 200, table[key]()

    def paths(self):
        return {p for _m, p in self.calls}

    def body_for(self, method: str, path: str):
        """最后一次发往 (method, path) 的请求体（没发过返回 None）。"""
        for m, p, b in reversed(self.bodies):
            if m == method and p.split("?")[0] == path:
                return b
        return None


def _no_db(*_a, **_kw):
    raise AssertionError("测试禁止连接任何数据库文件（sqlite3.connect 被调用）")


@pytest.fixture()
def stub(monkeypatch):
    """把控制台所有外部依赖（HTTP / socket / sqlite / 日志文件）换成桩。"""
    calls = HttpCalls()
    monkeypatch.setattr(sc, "_CONSOLE_SESSION",
                        {"token": "test-token", "username": "alpha", "user_id": 1})
    monkeypatch.setattr(sc, "_http_json", calls)
    monkeypatch.setattr(sc, "sqlite3", types.SimpleNamespace(connect=_no_db))
    monkeypatch.setattr(sc, "_read_db_stats",
                        lambda: {"characters": 4, "memories": 77, "tokens": 123456})
    monkeypatch.setattr(sc, "_read_token_trend", lambda *a, **k: [])
    monkeypatch.setattr(sc, "_read_token_heatmap", lambda *a, **k: heatmap_payload())
    monkeypatch.setattr(sc, "_fetch_health", lambda: "ok")
    monkeypatch.setattr(sc, "_fetch_liveness",
                        lambda: {"ok": True, "stalled": False, "summary": "正常", "level": 1})
    monkeypatch.setattr(sc, "_check_alive", lambda: True)
    monkeypatch.setattr(sc, "_ollama_alive", lambda: False)
    monkeypatch.setattr(sc, "_get_pid", lambda: 4242)
    monkeypatch.setattr(sc, "_get_ollama_pid", lambda: 0)
    monkeypatch.setattr(sc, "_tail_log", lambda *a, **k: "line1\nline2\n")
    monkeypatch.setattr(sc, "_is_remote", lambda: False)
    monkeypatch.setattr(sc, "_local_ip_set", lambda: {"127.0.0.1"})
    return calls


@pytest.fixture(scope="session")
def tk_root():
    """整轮测试共用**一个** Tk 解释器。

    不能每个用例 `tk.Tk()` + `destroy()`：`CUI.ICONS/PHOTOS` 是模块级缓存，
    缓存里的 PhotoImage 属于上一个解释器，新窗口取用直接 `TclError: image doesn't exist`
    （真控制台只有一个 root，不会遇到；这里是为了让每个用例拿到干净实例又不炸缓存）。
    """
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture()
def app(stub, monkeypatch, tk_root):
    """离屏控制台实例：真建控件树与全部 10 个页面，但窗口不映射、不上屏。"""
    import tkinter as tk

    win = tk.Toplevel(tk_root)
    win.withdraw()                       # 用户的工作机：绝不显示窗口，也不截图
    try:
        instance = sc.ControllerApp(win)
    except Exception:
        win.destroy()
        raise
    instance._probe_addresses = lambda: None    # "探测服务器地址"是 socket 侧活，冒烟里关掉

    def _run_admin_inline(_self, title, work, on_ok=None, page_key=""):
        """把后台线程 + 队列回投改成同步步进：结果确定，且不依赖 update() 时序。"""
        try:
            payload = work()
        except sc.AdminApiError as e:
            _self._on_admin_error(e.message, e, page_key)
            return
        if on_ok:
            on_ok(payload)

    monkeypatch.setattr(sc.ControllerApp, "_run_admin", _run_admin_inline)
    yield instance
    try:
        # 先把 after_idle(_fit) 这类空闲回调跑掉：留到销毁之后触发，Tcl 端会刷一堆
        # "invalid command name" 噪音（不影响断言，但会把真实异常淹掉）
        tk_root.update_idletasks()
    except Exception:
        pass
    win.destroy()


def row_count(widget):
    """DataTable 里的数据行数（表头 1 行 + 分隔线不计数）。"""
    return max(0, int(getattr(widget, "_n", 0)))
