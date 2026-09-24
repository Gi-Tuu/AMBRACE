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


def accounts_payload():
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
    return {"accounts": rows}


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
    """记录每一次"HTTP"，让测试能断言没有任何端点绕过打桩直接出去。"""

    def __init__(self):
        self.calls = []

    def __call__(self, method, path, body=None, token="", timeout=None):
        self.calls.append((method, path))
        key = path.split("?")[0]
        table = {
            API + "/modalities": modality_payload,
            API + "/accounts": accounts_payload,
            API + "/flags": flags_payload,
            API + "/audit": audit_payload,
            API + "/registration": registration_payload,
            API + "/overview": overview_payload,
            API + "/device-actions": device_actions_payload,
            API + "/llm-limit": llm_limit_payload,
            "/api/v1/system/feature-flags": public_flags_payload,
            sc.AUTH_LOGIN_PATH: lambda: {"access_token": "t", "username": "alpha",
                                         "user_id": 1},
        }
        if key not in table:
            raise AssertionError("冒烟出现了未登记的端点：%s %s" % (method, path))
        return 200, table[key]()

    def paths(self):
        return {p for _m, p in self.calls}


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
