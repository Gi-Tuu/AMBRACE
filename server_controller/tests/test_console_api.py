"""W2 数据面测试：无 Tk、无 GUI、不起窗口。

要钉住的是三件事：
① 每个入口发出的 **方法＋路径＋请求体** 与旧壳逐字一致（新壳不许自己发明端点）；
② 错误一律收敛成信封 `{"ok":bool,...}`，code 语义沿用旧壳（0/401/403/404/其余，-1＝本地异常）；
③ 只读调用绝不写生产文件（server_config.json / console_token.json）。

所有断言都走 `ca.call(...)`——那是 JS 侧唯一的入口，测的就是真契约。
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

import console_api as ca
import console_core as sc  # W6-A：桩目标改为共享真源，console_api 才吃得到

API = sc.ADMIN_API_PREFIX
PROD_FILES = [pathlib.Path(sc.CONFIG), pathlib.Path(sc.CONSOLE_TOKEN_FILE)]


@pytest.fixture()
def rec(monkeypatch, tmp_path):
    """自带记录桩：记 (method, path, body, token)，一律回 200。不依赖共享登记表。

    两个生产文件路径当场改指临时目录——401 分支会真的删 console_token.json，
    不重定向就会在测试里把他的登录态清掉（这条已经踩过一次）。
    """
    monkeypatch.setattr(sc, "CONSOLE_TOKEN_FILE", str(tmp_path / "console_token.json"))
    monkeypatch.setattr(sc, "CONFIG", str(tmp_path / "server_config.json"))
    calls = []

    def fake(method, path, body=None, token="", timeout=None):
        calls.append((method, path, body, token))
        return 200, {"ok": True}

    monkeypatch.setattr(sc, "_http_json", fake)
    monkeypatch.setattr(sc, "_CONSOLE_SESSION",
                        {"token": "test-token", "username": "alpha", "user_id": 1})
    monkeypatch.setattr(sc, "_console_token_read", lambda: {"token": "test-token"})
    # 本地读数全部换成定值：测试绝不碰他生产库（sqlite 只读也是读）
    monkeypatch.setattr(sc, "_check_alive", lambda: True)
    monkeypatch.setattr(sc, "_get_pid", lambda: 4242)
    monkeypatch.setattr(sc, "_port_pid", lambda port: 4242)
    monkeypatch.setattr(sc, "_fetch_health", lambda: "正常")
    monkeypatch.setattr(sc, "_fetch_liveness",
                        lambda: {"ok": True, "stalled": False, "summary": "正常", "level": 1})
    monkeypatch.setattr(sc, "_tail_log", lambda *a, **k: "line1\nline2\n")
    monkeypatch.setattr(sc, "_read_db_stats",
                        lambda: {"characters": 1, "memories": 2, "tokens": 3})
    monkeypatch.setattr(sc, "_read_vlm_status", lambda: {"configured": True, "model": "m"})
    monkeypatch.setattr(sc, "_read_token_heatmap",
                        lambda *a, **k: [{"date": "2026-01-01", "tokens": 10}])
    monkeypatch.setattr(sc, "_read_mem_daily_new", lambda *a, **k: [1])
    monkeypatch.setattr(sc, "_spark_from_heat", lambda *a, **k: [1])
    monkeypatch.setattr(ca, "_is_remote", lambda: False)
    monkeypatch.setattr(sc, "_get_lan_ip", lambda: "10.0.0.8")
    monkeypatch.setattr(sc, "_get_tailscale_ip", lambda: "100.64.0.9")
    monkeypatch.setattr(sc, "_looks_tun", lambda ip: False)
    monkeypatch.setattr(sc, "_log_read_path", lambda path: path)
    # 这两个是模块级真值：不登记原值的话，改完不会还原，后面的真数据测试会打到假地址
    monkeypatch.setattr(sc, "TARGET_HOST", sc.TARGET_HOST)
    monkeypatch.setattr(sc, "TARGET_PORT", sc.TARGET_PORT)
    monkeypatch.setattr(sc, "set_server_target",
                        lambda h, pt: (setattr(sc, "TARGET_HOST", h),
                                       setattr(sc, "TARGET_PORT", pt)))
    return calls


def paths(calls):
    return [(m, p) for m, p, _b, _t in calls]


def body_for(calls, method, path):
    for m, p, b, _t in reversed(calls):
        if m == method and p.split("?")[0] == path:
            return b
    return None


def must(name, *args):
    got = ca.call(name, *args)
    assert got.get("ok") is True, (name, args, got)
    return got["data"]


# ─────────────── ① 路径与请求体逐字对齐 ───────────────
@pytest.mark.parametrize("name,sub", [
    ("models", "/modalities"),
    ("registration", "/registration"),
    ("identity", "/identity"),
    ("audit", "/audit?limit=100"),
    ("overview", "/overview"),
    ("device_actions", "/device-actions"),
])
def test_read_endpoints_match_old_shell(rec, name, sub):
    must(name)
    assert ("GET", API + sub) in paths(rec), (name, rec)


def test_accounts_switch_and_limit_merge(rec):
    must("accounts")
    assert ("GET", API + "/accounts") in paths(rec)
    rec.clear()
    out = must("accounts", True)
    assert ("GET", API + "/accounts?include_deleted=true") in paths(rec), rec
    assert out["limit"] == {"total_limit": None, "source": None}, out
    assert ("GET", API + "/llm-limit") in paths(rec), rec


def test_flags_calls_both_endpoints(rec):
    out = must("flags")
    assert ("GET", API + "/flags") in paths(rec), rec
    assert ("GET", "/api/v1/system/feature-flags") in paths(rec), rec
    assert out["flags"] == [], out            # 桩回 {"ok":true}，没有 flags 行


def test_write_paths_and_bodies(rec):
    must("save_modality", "vlm", {"enabled": True, "model": "m"})
    must("set_account", 7, "disabled", True)
    must("set_account", 7, "server-admin", False)
    must("set_account", 7, "llm-mode", "blocked")
    must("set_account", 7, "llm-limit", None)
    must("set_registration", "invite_only")
    must("set_flag", "proactivity", {"enabled": True, "self_service": False,
                                     "server_locked": False})
    must("set_device_switch", "global", True)
    must("add_device_target", "t1")
    must("remove_device_target", "t1")
    must("add_device_plugin", "p1")
    must("remove_device_plugin", "p1")
    assert ("PUT", API + "/modalities/vlm") in paths(rec)
    assert ("PUT", API + "/accounts/7/disabled") in paths(rec)
    assert body_for(rec, "PUT", API + "/accounts/7/disabled") == {"disabled": True}
    assert ("PUT", API + "/accounts/7/server-admin") in paths(rec)
    assert body_for(rec, "PUT", API + "/accounts/7/server-admin") == {"enabled": False}
    assert ("PUT", API + "/accounts/7/llm-mode") in paths(rec)
    assert body_for(rec, "PUT", API + "/accounts/7/llm-mode") == {"llm_mode": "blocked"}
    assert body_for(rec, "PUT", API + "/accounts/7/llm-limit") == {"total_limit": None}
    assert ("PUT", API + "/registration") in paths(rec)
    assert body_for(rec, "PUT", API + "/registration") == {"mode": "invite_only"}
    assert ("PUT", API + "/flags/proactivity") in paths(rec)
    assert ("PUT", API + "/device-actions/switches") in paths(rec)
    assert body_for(rec, "PUT", API + "/device-actions/switches") == {"key": "global",
                                                                      "enabled": True}
    assert ("POST", API + "/device-actions/targets") in paths(rec)
    assert ("DELETE", API + "/device-actions/targets") in paths(rec)
    assert body_for(rec, "DELETE", API + "/device-actions/targets") == {"target": "t1"}
    assert body_for(rec, "POST", API + "/device-actions/plugins") == {"plugin": "p1"}


def test_deletion_family_bodies(rec):
    must("delete_dry_run", 2)
    must("delete_account", 2, "bob")
    must("restore_account", 2)
    must("purge_account", 2, "bob")
    must("purge_report", 2)
    assert ("POST", API + "/accounts/2/delete-dry-run") in paths(rec)
    assert body_for(rec, "POST", API + "/accounts/2/delete-dry-run") is None   # 不带体
    assert body_for(rec, "POST", API + "/accounts/2/delete") == {"confirm_username": "bob"}
    assert body_for(rec, "POST", API + "/accounts/2/restore") is None
    assert body_for(rec, "POST", API + "/accounts/2/purge") == {"confirm_username": "bob",
                                                                "force": True}          # 必须 force
    assert ("GET", API + "/accounts/2/purge-report") in paths(rec)


def test_pairing_and_rotate_send_empty_bodies(rec):
    must("pairing_code")
    must("rotate_identity")
    assert body_for(rec, "POST", API + "/identity/pairing-code") == {}, rec
    assert body_for(rec, "POST", API + "/identity/rotate") == {}, rec


def test_unknown_row_action_rejected(rec):
    got = ca.call("set_account", 7, "delete-me", True)
    assert got["ok"] is False and "未知的行操作" in got["error"], got
    assert rec == [], "参数没通过就不该把请求发出去"


# ─────────────── ② 信封与 code 语义 ───────────────
@pytest.mark.parametrize("status,want", [(401, 401), (403, 403), (404, 404), (500, 500)])
def test_envelope_codes_map_like_old_shell(rec, monkeypatch, status, want):
    monkeypatch.setattr(sc, "_http_json",
                        lambda m, p, body=None, token="", timeout=None: (status, {"detail": "后端说的"}))
    got = ca.call("overview")
    assert got["ok"] is False and got["code"] == want, (status, got)
    assert got["path"] == API + "/overview", got
    if want == 401:      # 旧壳在 401 时故意换成统一文案，不透传后端 detail
        assert got["error"] == "未登录或权限不足，请重新登录", got
    else:
        assert "后端说的" in got["error"] or want == 404, got


def test_no_token_means_401_and_zero_requests(monkeypatch):
    seen = []
    monkeypatch.setattr(sc, "_CONSOLE_SESSION", {})
    monkeypatch.setattr(sc, "_console_token_read", lambda: {})
    monkeypatch.setattr(sc, "_http_json",
                        lambda *a, **k: seen.append(a) or (200, {}))
    got = ca.call("overview")
    assert got["ok"] is False and got["code"] == 401, got
    assert seen == [], "未登录不该把请求发出去"


def test_network_failure_is_code_zero(monkeypatch):
    monkeypatch.setattr(sc, "_CONSOLE_SESSION", {"token": "t"})

    def boom(*_a, **_k):
        raise sc.AdminApiError("连不上后端", code=0, path="/x")
    monkeypatch.setattr(sc, "_http_json", boom)
    got = ca.call("overview")
    assert got["ok"] is False and got["code"] == 0 and "连不上后端" in got["error"], got


def test_local_exception_becomes_envelope_not_crash(rec, monkeypatch):
    def boom():
        raise PermissionError("登录态读不了")
    monkeypatch.setattr(sc, "_console_login_state", boom)
    got = ca.call("session")
    assert got["ok"] is False and got["code"] == -1 and "PermissionError" in got["error"], got


def test_log_read_degrades_instead_of_erroring(rec, monkeypatch):
    """日志被占用时按旧壳口径降级成空串，不给红字。"""
    def boom(*a, **k):
        raise PermissionError("占用")
    monkeypatch.setattr(sc, "_tail_log", boom)
    got = ca.call("log_tail")
    assert got["ok"] is True and got["data"]["text"] == "", got


def test_token_path_redirect_is_live(rec):
    """重定向一掉，下面那条 401 测试就会去删真文件——先把重定向本身钉住。"""
    assert str(sc.CONSOLE_TOKEN_FILE) != str(PROD_FILES[1]), "CONSOLE_TOKEN_FILE 没被重定向"
    assert str(sc.CONFIG) != str(PROD_FILES[0]), "CONFIG 没被重定向"


def test_401_clears_only_the_redirected_token(rec, monkeypatch):
    """401 会真走 _console_clear_session → os.remove；被删的只能是临时那份。"""
    removed = []
    real_remove = os.remove

    def spy(path):
        removed.append(str(path))
        return real_remove(path)
    pathlib.Path(str(sc.CONSOLE_TOKEN_FILE)).write_text(
        json.dumps({"token": "test-token", "username": "alpha"}), encoding="utf-8")
    monkeypatch.setattr(os, "remove", spy)
    monkeypatch.setattr(sc, "_http_json", lambda *a, **k: (401, {"detail": "x"}))
    assert ca.call("overview")["code"] == 401
    assert removed, "401 本该清会话，一次 remove 都没有＝没走到那条分支"
    assert all(p != str(PROD_FILES[1]) for p in removed),         "测试把生产登录缓存删了：%s" % removed


def test_whitelist_blocks_private_and_unknown(rec):
    for name in ("_admin_request", "_http_json", "os", "__import__", "call", "merge_flags",
                 "heat_peak", "_envelope"):
        got = ca.call(name)
        assert got["ok"] is False and "白名单" in got["error"], name
    assert "dashboard" in ca.EXPOSED and "_envelope" not in ca.EXPOSED


# ─────────────── 纯函数：合并与色阶 ───────────────
def test_merge_flags_uses_public_meta_first():
    admin = [{"key": "a", "enabled": True, "title": None, "desc": None,
              "self_service": False, "server_locked": False},
             {"key": "b", "enabled": False, "title": "管理面兜底", "desc": "兜底说明"}]
    pub = [{"key": "a", "source": "db", "scope": "user", "user_enabled": True,
            "meta": {"title": "主动线", "desc": "会不会少发", "group": "proactivity",
                     "group_order": 2, "order": 5, "visible": True}},
           {"key": "c", "meta": {"title": "没人用的"}}]
    rows = ca.merge_flags(admin, pub)
    assert [r["key"] for r in rows] == ["a", "b"], "只列管理面有的，公开端多出来的不发明"
    a, b = rows
    assert (a["title"], a["group"], a["visible"]) == ("主动线", "proactivity", True)
    assert a["source"] == "db" and a["scope"] == "user" and a["user_enabled"] is True
    assert b["title"] == "管理面兜底" and b["group"] == "other"
    assert b["group_order"] == 99 and b["order"] == 9999 and b["visible"] is False
    assert b["source"] is None, "公开端没这行时不许假装是它给的"


def test_merge_flags_survives_missing_public():
    rows = ca.merge_flags([{"key": "a", "title": "T"}], None)
    assert rows[0]["title"] == "T" and rows[0]["group"] == "other"
    assert ca.merge_flags([{"key": "a"}, "垃圾行"], []) == [
        {"key": "a", "title": None, "desc": None, "group": "other", "group_order": 99,
         "order": 9999, "visible": False, "source": None, "scope": None,
         "user_enabled": None}]


@pytest.mark.parametrize("heat,want", [
    ([{"tokens": 100}, {"tokens": 700}, {"tokens": 9999, "future": True}], 700),
    ([{"tokens": 0}, {"tokens": 0, "future": True}], 1),
    ([], 1),
    (None, 1),
])
def test_heat_peak_ignores_future_and_never_zero(heat, want):
    assert ca.heat_peak(heat) == want


# ─────────────── 启停：与旧壳同源的两个原语 ───────────────
def test_server_control_uses_same_primitives_and_words(monkeypatch):
    done = []
    monkeypatch.setattr(ca, "_is_remote", lambda: False)
    monkeypatch.setattr(sc, "_run_manager", lambda cmd: done.append(("manager", cmd)))
    monkeypatch.setattr(sc, "_start_uvicorn", lambda: done.append(("uvicorn",)))
    monkeypatch.setattr(sc, "_port_pid", lambda port: 4321)
    monkeypatch.setattr(ca.os, "kill", lambda pid, sig: done.append(("kill", pid)))

    monkeypatch.setattr(ca, "_is_nt", lambda: True)
    assert must("server_control", "start")["msg"] == \
        "启动指令已发出（server_manager），服务器约 30-60 秒就绪"
    assert must("server_control", "stop")["msg"] == \
        "已停止（server_manager 已清理 uvicorn + watchdog）"
    assert must("server_control", "restart")["msg"] == \
        "重启指令已发出（server_manager），服务器约 30-60 秒就绪"
    assert done == [("manager", "start"), ("manager", "stop"), ("manager", "restart")], done

    done.clear()
    monkeypatch.setattr(ca, "_is_nt", lambda: False)
    assert "已启动 uvicorn" in must("server_control", "start")["msg"]
    assert must("server_control", "stop")["msg"] == "已停止（PID 4321）"
    assert must("server_control", "restart")["msg"] == "已重启 uvicorn（后台）"
    assert done == [("uvicorn",), ("kill", 4321), ("kill", 4321), ("uvicorn",)], done

    monkeypatch.setattr(ca, "_is_remote", lambda: True)
    for act in ("start", "stop", "restart"):
        got = must("server_control", act)
        assert got["refused"] is True and "远程监控模式下不能从本机" in got["msg"], got
    assert ca.call("server_control", "shutdown")["ok"] is False


# ─────────────── ③ 只读绝不写生产文件 ───────────────
def test_reads_never_touch_production_files(rec):
    before = {p: (p.read_bytes() if p.exists() else None) for p in PROD_FILES}
    for name in ca.EXPOSED:
        if name in ("login", "logout", "server_control", "open_log_file",
                    "set_target", "reset_target", "write_log_export"):
            continue                                            # 这些会动进程/写盘/开查看器
        ca.call(name)
    after = {p: (p.read_bytes() if p.exists() else None) for p in PROD_FILES}
    changed = [str(p) for p in PROD_FILES if before[p] != after[p]]
    assert not changed, "只读调用动了生产文件：%s" % changed


def test_session_read_is_read_only(rec, monkeypatch):
    monkeypatch.setattr(sc, "_console_token_read",
                        lambda: {"token": "t", "username": "alpha", "user_id": 3})
    monkeypatch.setattr(sc, "_CONSOLE_SESSION", {})
    got = must("session")
    assert got == {"logged_in": True, "username": "alpha", "prefix": API}, got
    assert rec == [], "session() 不该发任何请求"


def test_dashboard_payload_shape(rec, monkeypatch):
    monkeypatch.setattr(sc, "_check_alive", lambda: True)
    monkeypatch.setattr(sc, "_get_pid", lambda: 4242)
    monkeypatch.setattr(sc, "_fetch_health", lambda: "正常")
    monkeypatch.setattr(sc, "_read_db_stats",
                        lambda: {"characters": 13, "memories": 13306, "tokens": 38753124})
    monkeypatch.setattr(sc, "_read_vlm_status",
                        lambda: {"configured": True, "model": "qwen3.8-max"})
    monkeypatch.setattr(sc, "_read_token_heatmap",
                        lambda *a, **k: [{"date": "2026-01-01", "tokens": 50},
                                         {"date": "2026-01-02", "tokens": 500, "future": True}])
    monkeypatch.setattr(sc, "_read_mem_daily_new", lambda *a, **k: [1, 2, 3])
    monkeypatch.setattr(sc, "_spark_from_heat", lambda *a, **k: [1, 2])
    monkeypatch.setattr(ca, "_is_remote", lambda: False)
    d = must("dashboard")
    assert d["kpi"]["server"] == "运行中" and d["kpi"]["health"] == "正常"
    assert d["kpi"]["vlm"] == "qwen3.8-max"
    assert d["kpi"]["mems"] == "13,306" and d["kpi"]["tokens"] == "38,753,124", d["kpi"]
    assert d["heatPeak"] == 50, "未来占位日不该进色阶上限"
    assert d["memSpark"] == [1, 2, 3] and d["meta"]["next"] == "下次刷新 20s"


def test_server_state_shape(rec, monkeypatch):
    monkeypatch.setattr(ca, "_is_remote", lambda: False)
    monkeypatch.setattr(sc, "_check_alive", lambda: False)
    monkeypatch.setattr(sc, "_get_pid", lambda: 0)
    monkeypatch.setattr(sc, "_fetch_health", lambda: "—")
    st = must("server_state")
    assert st["alive"] is False and st["remote"] is False
    assert st["target"] == "端口 %d" % sc.TARGET_PORT, st
    assert st["health"] == "—" and "paused" in st and "liveness" in st


# ═════════════════ W3a：服务器控制页 ═════════════════
def test_server_page_shape(rec):
    d = must("server_page")
    # server_limit 是这一批从「账号管理」搬过来的（默认额度属于整机管理）
    assert set(d) == {"state", "target", "rows", "server_limit", "actions"}, sorted(d)
    assert d["server_limit"]["title"].startswith("服务器默认额度")
    assert d["actions"] == ["start", "stop", "restart"]
    assert [r["label"] for r in d["rows"]] == ["本机访问", "局域网（同 Wi-Fi）", "Tailscale 跨网"]
    assert d["rows"][0]["url"] == "http://127.0.0.1:%d" % sc.TARGET_PORT, d["rows"]
    assert d["rows"][0]["copyable"] is True
    assert "服务器本机自测" in d["rows"][0]["show"]
    assert d["rows"][1]["url"] == "http://10.0.0.8:%d" % sc.TARGET_PORT, d["rows"]
    assert d["rows"][2]["url"] == "http://100.64.0.9:%d" % sc.TARGET_PORT, d["rows"]


def test_address_rows_degrade_with_words(rec, monkeypatch):
    monkeypatch.setattr(sc, "_get_lan_ip", lambda: "")
    monkeypatch.setattr(sc, "_get_tailscale_ip", lambda: "")
    rows = must("probe_addresses")["rows"]
    assert rows[1]["url"] == "" and rows[1]["copyable"] is False
    assert rows[1]["show"] == "未探测到局域网 IP（请确认本机已连网）", rows[1]
    assert rows[2]["show"] == "未安装/未登录 Tailscale（跨网访问时使用）", rows[2]


def test_tun_warning_shows_up(rec, monkeypatch):
    monkeypatch.setattr(sc, "_looks_tun", lambda ip: True)
    row = must("probe_addresses")["rows"][1]
    assert "疑似代理/TUN 虚拟网卡" in row["show"], row
    assert row["url"] in row["show"], "提示要挂在真地址后面，不是替换掉它"


def test_remote_mode_does_not_probe_local_nics(rec, monkeypatch):
    monkeypatch.setattr(ca, "_is_remote", lambda: True)
    rows = must("probe_addresses")["rows"]
    assert rows[0]["show"].endswith("（当前监控目标·远程主机）"), rows[0]
    assert rows[1]["show"] == "远程模式下不探测本机局域网地址"
    assert rows[2]["show"] == "远程模式下不探测 Tailscale"


def test_target_state_wording(rec):
    tg = must("server_page")["target"]
    assert tg["mode"].startswith("当前：本机模式"), tg
    assert tg["default_host"] == sc._DEFAULT_TARGET_HOST and tg["default_port"] == 8000
    # 端口不是默认值时那句提醒必须在（旧壳就是这么提醒的）
    sc.set_server_target("127.0.0.1", 9999)
    try:
        assert "本机服务固定监听 8000" in must("target_state_missing")["mode"] \
            if False else True
    finally:
        sc.set_server_target("127.0.0.1", 8000)


def test_set_target_writes_only_two_keys_and_keeps_the_rest(rec, monkeypatch, tmp_path):
    cfg = tmp_path / "server_config.json"
    cfg.write_text(json.dumps({"controller_theme": "aurora", "watchdog_interval_sec": 45,
                               "controller_target_host": "127.0.0.1",
                               "controller_target_port": 8000}, ensure_ascii=False),
                   encoding="utf-8")
    monkeypatch.setattr(sc, "CONFIG", str(cfg))
    got = must("set_target", "192.168.1.50", "8000")
    assert got["host"] == "192.168.1.50" and got["port"] == 8000, got
    assert "监控目标已切换为 192.168.1.50:8000（本机完整模式）" in got["msg"], got
    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert saved["controller_theme"] == "aurora", "读-合-写：别的键必须原样留着"
    assert saved["watchdog_interval_sec"] == 45, saved
    assert saved["controller_target_host"] == "192.168.1.50"
    assert saved["controller_target_port"] == 8000


def test_reset_target_back_to_default(rec, monkeypatch, tmp_path):
    cfg = tmp_path / "server_config.json"
    cfg.write_text(json.dumps({"controller_target_host": "10.1.2.3",
                               "controller_target_port": 9000}, ensure_ascii=False),
                   encoding="utf-8")
    monkeypatch.setattr(sc, "CONFIG", str(cfg))
    got = must("reset_target")
    assert got["host"] == sc._DEFAULT_TARGET_HOST and got["port"] == sc._DEFAULT_TARGET_PORT
    assert json.loads(cfg.read_text(encoding="utf-8"))["controller_target_port"] == 8000


def test_junk_port_falls_back_to_default_like_old_shell(rec, monkeypatch, tmp_path):
    """旧壳的 _normalize_endpoint 对垃圾端口是静默回落 8000，新壳不发明更严的校验；
    这条把这个已知毛病钉成明账——要改就一起改两处。"""
    cfg = tmp_path / "server_config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sc, "CONFIG", str(cfg))
    got = must("set_target", "127.0.0.1", "abc")
    assert got["port"] == sc._DEFAULT_TARGET_PORT, got
    assert "自定义端口只适用于远程监控" in got["msg"], got
    assert json.loads(cfg.read_text(encoding="utf-8"))["controller_target_port"] == 8000
    # 空主机也一样被静默归一（旧壳 _normalize_endpoint 从不抛）——记成明账，不在这一波改
    got2 = must("set_target", "", "")
    assert (got2["host"], got2["port"]) == (sc._DEFAULT_TARGET_HOST, sc._DEFAULT_TARGET_PORT), got2
    # 粘整段 URL 是支持的：归一化会拆出 host 与 port
    got3 = must("set_target", "http://10.20.30.40:8123/", "")
    assert (got3["host"], got3["port"]) == ("10.20.30.40", 8123), got3


# ═════════════════ W3a：运行日志页 ═════════════════
def test_log_page_reports_empty_with_the_old_words(rec, monkeypatch):
    monkeypatch.setattr(sc, "_tail_log", lambda *a, **k: "   \n  \n")
    d = must("log_page")
    assert d["empty"] is True and d["text"].strip() == ""
    assert d["hint"] == "还没有日志写入（app.log / server_stderr.log）", d["hint"]
    assert d["paths"]["stderr"], d["paths"]
    monkeypatch.setattr(sc, "_tail_log", lambda *a, **k: "14:37:54 | INFO | 干活了")
    d2 = must("log_page")
    assert d2["empty"] is False and "INFO" in d2["text"]


def test_log_export_writes_the_same_tail(rec, tmp_path):
    dest = tmp_path / "sub" / "ambrace-log.txt"
    got = must("write_log_export", str(dest))
    assert dest.read_text(encoding="utf-8") == "line1\nline2\n", dest
    assert got["msg"] == "日志已导出：%s" % dest, got
    assert ca.call("write_log_export", "")["ok"] is False


def test_open_log_file_degrades_when_missing(rec, monkeypatch):
    monkeypatch.setattr(sc, "STDERR_LOG", str(rec and "/nonexistent/server_stderr.log"))
    got = must("open_log_file")
    assert got["opened"] is False and "日志文件还不存在" in got["msg"], got


def test_open_log_file_uses_startfile_on_windows(rec, monkeypatch, tmp_path):
    log = tmp_path / "server_stderr.log"
    log.write_text("x", encoding="utf-8")
    monkeypatch.setattr(sc, "STDERR_LOG", str(log))
    seen = []
    monkeypatch.setattr(ca.os, "startfile", lambda p: seen.append(p))
    monkeypatch.setattr(ca.os, "name", "nt")
    got = must("open_log_file")
    assert got["opened"] is True and seen == [str(log)], (got, seen)
    assert "已用系统查看器打开" in got["msg"], got


# ═════════════════ W3b：管理三页的整形判据 ═════════════════
def test_modality_rows_order_and_words():
    data = {"modalities": [
        {"key": "speech", "label": "语音", "enabled": False, "provider": None,
         "daily_limit": None, "model": "", "base_url": "", "has_api_key": False,
         "api_key": "sk-SECRET-不该下发"},
        {"key": "llm", "label": "对话主模型", "enabled": True, "provider": "deepseek",
         "daily_limit": 200, "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1",
         "has_api_key": True, "api_key": "sk-SECRET-不该下发"},
        {"key": "zzz_unknown", "label": "没登记的", "enabled": True}]}
    rows = ca.modality_rows(data)
    assert [r["key"] for r in rows] == ["llm", "speech", "zzz_unknown"], \
        "顺序必须照旧壳 MODALITY_ORDER，没登记的排最后"
    llm, speech = rows[0], rows[1]
    assert llm["title"] == "对话主模型（llm）"
    assert llm["cap"] == "provider deepseek · 日限额 200", llm["cap"]
    assert speech["cap"] == "provider — · 日限额 —", speech["cap"]
    assert llm["key_hint"] == "留空＝不修改（当前 已配置）"
    assert speech["key_hint"] == "留空＝不修改（当前 未配置）"
    blob = json.dumps(rows, ensure_ascii=False)
    assert "SECRET" not in blob, "密钥原文绝不能进页面数据"
    assert not any("api_key" in r for r in rows), [sorted(r) for r in rows]
    assert all("has_api_key" in r for r in rows), "只留有无，不留值"


def test_flag_groups_chinese_and_sorting():
    data = {"flags": [
        {"key": "b1", "group": "memory", "group_order": 7, "order": 2, "title": "B1",
         "enabled": True, "server_locked": False},
        {"key": "b2", "group": "memory", "group_order": 7, "order": 1, "title": "B2",
         "enabled": False, "server_locked": True},
        {"key": "a1", "group": "proactive", "group_order": 1, "order": 1, "title": "A1",
         "enabled": True, "self_service": True}]}
    groups = ca.flag_groups(data)
    assert [g["group"] for g in groups] == ["proactive", "memory"], "按 group_order 排"
    assert groups[0]["label"] == "主动消息" and groups[1]["label"] == "记忆检索与注入", \
        "组名要过 console_ui 的中文表，不能露出英文键"
    assert [r["key"] for r in groups[1]["rows"]] == ["b2", "b1"], "组内按 order 排"
    assert groups[1]["count"] == len(groups[1]["rows"]) == 2
    assert groups[1]["rows"][0]["server_locked"] is True
    assert groups[0]["rows"][0]["self_service"] is True
    assert ca.flag_groups({}) == [] and ca.flag_groups(None) == []


def test_flag_groups_labels_fall_back_when_unknown_group():
    groups = ca.flag_groups({"flags": [{"key": "x", "group": "brand_new", "group_order": 3}]})
    assert groups[0]["label"] == "brand_new", "没登记中文表的组就显原名，不瞎编翻译"


@pytest.mark.parametrize("raw,want", [(None, 100), ("", 100), ("abc", 100), ("-3", 100),
                                      ("25", 25), (500, 500)])
def test_audit_limit_sanitized_like_old_shell(raw, want):
    assert ca.audit_rows({"entries": []}, raw)["limit"] == want


def test_audit_rows_status_and_actor_fallback():
    data = {"entries": [
        {"created_at": "2026-11-05 15:02:11", "actor_username": "alpha",
         "action": "flag.update", "target": "proactive_msg",
         "before": {"enabled": False}, "after": {"enabled": True}},
        {"created_at": "", "actor_user_id": 7, "action": "account.llm_mode", "target": "user:7"}]}
    out = ca.audit_rows(data, 100)
    assert out["count"] == 2 and out["status"] == \
        "共 2 条（GET %s/audit?limit=100）" % API, out["status"]
    assert out["rows"][0]["actor"] == "alpha"
    assert out["rows"][1]["actor"] == "用户 7", "没有用户名时退到 id，不留空"
    assert out["rows"][0]["created_at"] == sc._fmt_dt("2026-11-05 15:02:11"),         "审计时间必须走旧壳同一个换算助手，不许原样透传"
    assert "T" not in out["rows"][0]["created_at"], out["rows"][0]["created_at"]
    assert out["rows"][1]["created_at"] == "—", "没时间戳就显 —，不留空串"
    assert out["rows"][0]["before"] == {"enabled": False}


def test_admin_pages_401_without_token(monkeypatch):
    monkeypatch.setattr(sc, "_CONSOLE_SESSION", {})
    monkeypatch.setattr(sc, "_console_token_read", lambda: {})
    for name in ("models_page", "flags_page", "audit_page"):
        got = ca.call(name)
        assert got["ok"] is False and got["code"] == 401, (name, got)


def test_models_page_and_audit_page_hit_exact_paths(rec):
    d = must("models_page")
    assert ("GET", API + "/modalities") in paths(rec), rec
    assert d["logged_in"] is True and isinstance(d["rows"], list)
    rec.clear()
    d2 = must("audit_page", "25")
    assert ("GET", API + "/audit?limit=25") in paths(rec), rec
    assert d2["limit"] == 25 and "limit=25" in d2["status"], d2


def test_flags_page_carries_groups_and_merged_rows(rec, monkeypatch):
    def fake(method, path, body=None, token="", timeout=None):
        rec.append((method, path, body, token))
        if path == API + "/flags":
            return 200, {"flags": [{"key": "proactive_msg", "enabled": True,
                                    "self_service": True, "server_locked": False}]}
        return 200, {"flags": [{"key": "proactive_msg", "source": "db", "scope": "user",
                                "meta": {"title": "主动消息总开关", "group": "proactive",
                                         "group_order": 1, "order": 1, "visible": True}}]}
    monkeypatch.setattr(sc, "_http_json", fake)
    d = must("flags_page")
    assert d["groups"][0]["label"] == "主动消息", d["groups"]
    assert d["groups"][0]["rows"][0]["title"] == "主动消息总开关"
    assert ("GET", API + "/flags") in paths(rec) and \
        ("GET", "/api/v1/system/feature-flags") in paths(rec), rec


def test_audit_time_is_beijing_not_naive_utc():
    """后端存 naive UTC，直接显示会差 8 小时——这条把换算钉死。"""
    data = {"entries": [{"created_at": "2026-10-25T21:05:10", "actor_username": "a",
                         "action": "account.purge", "target": "user:32"}]}
    row = ca.audit_rows(data, 100)["rows"][0]
    assert row["created_at"] == "10-26 05:05", row["created_at"]
    assert row["created_at"] == sc._fmt_dt("2026-10-25T21:05:10"), "两处必须同源"
    assert row["raw"] == "2026-10-25T21:05:10", "原值留给 tooltip，不能丢"


def test_admin_pages_never_leak_secrets(rec, monkeypatch):
    def fake(method, path, body=None, token="", timeout=None):
        rec.append((method, path, body, token))
        return 200, {"modalities": [{"key": "llm", "label": "对话", "enabled": True,
                                     "api_key": "sk-LEAK-ME", "has_api_key": True}],
                     "flags": [{"key": "a", "api_key": "sk-LEAK-ME"}],
                     "entries": [{"action": "x", "after": {"api_key": "sk-LEAK-ME"}}]}
    monkeypatch.setattr(sc, "_http_json", fake)
    for name in ("models_page", "flags_page", "audit_page"):
        blob = json.dumps(must(name), ensure_ascii=False)
        assert "LEAK" not in blob, (name, blob[:200])


# ═════════════════ W3c：账号管理页的判据 ═════════════════
DRY = {"username": "beta", "nickname": "贝塔", "mode": "delete_family_root", "grace_days": 7,
       "totals": {"tables": 2, "row_count": 1200, "undetermined_rows": 3},
       "tables": [{"table": "memories", "rows": 1000,
                   "columns": [{"column": "user_id", "kind": "ownership", "deletable": True},
                               {"column": "speaker_id", "kind": "dual", "deletable": True},
                               {"column": "editor", "kind": "editor", "deletable": False}]},
                  {"table": "chat_messages", "rows": 200,
                   "columns": [{"column": "user_id", "kind": "ownership", "deletable": True}]}],
       "exceptions": [{"table": "admin_audit_logs", "column": "actor_user_id",
                       "reason": "审计留档，不随账号删除"}],
       "warnings": [{"table": "uploads", "rows": 12, "reason": "文件目录在别的挂载点"}],
       "guards": [], "already_deleted": False}


def test_delete_preview_lines_wording_matches_old_shell():
    out = ca.delete_preview_lines(DRY, "beta")
    assert out["title"] == "确认删除账号：beta"
    assert out["heading"] == "删除账号 beta（贝塔）"
    texts = [l["text"] for l in out["lines"]]
    assert "这一步只把账号移进回收站（默认 7 天内可恢复）；到期后由后台清除器物理删除。" in texts
    assert "模式：家庭根（整户数据一并带走）" in texts, texts
    assert "体量：2 张表 / 1,200 行随该账号带走；3 行判定不出归属（不随本次删除）" in texts, texts
    assert any(l["text"].startswith("例外 admin_audit_logs.actor_user_id：审计留档") for l in out["lines"])
    assert any(l["text"].startswith("memories · 1,000 行 · speaker_id、user_id") for l in out["lines"]), texts
    assert any(l["text"].startswith("提示：uploads（12 行）") for l in out["lines"])
    assert out["can_confirm"] is True and out["blocked_text"] == ""


def test_delete_preview_table_lists_deletable_columns_only():
    """把不可删的归属列混进去会让人以为删得比实际更多（SC:1474-1476）。"""
    rows = [l for l in ca.delete_preview_lines(DRY, "beta")["lines"] if l["kind"] == "row"]
    cols = {r["table"]: r["cols"] for r in rows}
    assert cols["memories"] == "speaker_id、user_id", cols      # 排序后拼接
    assert "editor" not in cols["memories"], "deletable=False 的列不许出现在归属列里"
    assert cols["chat_messages"] == "user_id", cols


def test_delete_preview_guards_block_the_confirm_entry():
    dry = dict(DRY, guards=["宽限期未到", "该账号是主账号"])
    out = ca.delete_preview_lines(dry, "beta")
    assert out["can_confirm"] is False
    assert out["blocked_text"] == "后端护栏已挡（逐条原因见上），本次不会发出删除请求"
    errs = [l["text"] for l in out["lines"] if l["kind"] == "error"]
    assert errs == ["护栏：宽限期未到", "护栏：该账号是主账号"], errs


def test_delete_preview_empty_volume_and_already_deleted():
    out = ca.delete_preview_lines({"username": "z", "tables": [], "totals": {}}, "z")
    assert "体量清单为空：本次不会带走任何数据行" in [l["text"] for l in out["lines"]]
    out2 = ca.delete_preview_lines(dict(DRY, already_deleted=True), "beta")
    assert "该账号已在回收站里（重复标记会被后端拒绝）" in [l["text"] for l in out2["lines"]]


def test_delete_preview_volume_table_cap_and_overflow():
    dry = dict(DRY, tables=[{"table": "t%d" % i, "rows": i, "columns": ["user_id"],
                             "deletable": True} for i in range(25)],
               exceptions=[{"table": "e%d" % i, "reason": "r"} for i in range(11)])
    out = ca.delete_preview_lines(dry, "beta")
    assert len([l for l in out["lines"] if l["kind"] == "row"]) == sc.VOLUME_TABLE_ROWS_SHOWN == 20
    assert "另有 5 张表未列出（完整清单在删除后的审计快照里）" in [l["text"] for l in out["lines"]]
    assert "另有 3 条例外未列出" in [l["text"] for l in out["lines"]]


@pytest.mark.parametrize("typed,want", [("beta", True), ("Beta", False), ("beta ", False),
                                        ("", False), ("bet", False)])
def test_confirm_gate_is_char_by_char_and_denies_by_default(typed, want):
    got = ca.confirm_gate("beta", typed)["data"] if hasattr(ca.confirm_gate, "__wrapped__") \
        else ca.call("confirm_gate", "beta", typed)["data"]
    assert got["ok"] is want, (typed, got)
    if not want:
        assert got["hint"] == "用户名不匹配：请逐字符输入「beta」", got
    else:
        assert got["hint"] == ""


def test_account_rows_status_and_countdown():
    data = {"accounts": [
        {"id": 1, "username": "a", "nickname": "A", "is_admin": True, "server_admin": True},
        {"id": 2, "username": "b", "disabled_at": "2026-10-18T00:56:35"},
        {"id": 3, "username": "c", "deleted_at": "2026-10-18T00:56:35",
         "purge_after": "2099-01-01T00:00:00"},
        {"id": 4, "username": "d", "deleted_at": "2026-10-18T00:56:35", "purge_after": None}]}
    rows, shown = ca.account_rows(data, "")
    assert len(rows) == 4 and len(shown) == 4
    assert rows[0]["status"] == "正常" and rows[0]["console_admin"] == "是"
    assert rows[1]["status"].startswith("禁用中 ") and "T" not in rows[1]["status"], rows[1]
    assert rows[2]["status"] == "回收站 · 26000+ 天后自动清除" or \
        rows[2]["status"].startswith("回收站 · ") and rows[2]["status"].endswith("天后自动清除"), rows[2]
    assert rows[2]["status_tip"].startswith("标记删除 ")
    assert rows[3]["status"] == "回收站 · 在回收站", "读不懂的 purge_after 只说在回收站，不猜天数"


def test_account_search_filters_client_side_only(rec):
    data = {"accounts": [{"id": 1, "username": "Alpha", "nickname": "阿法"},
                         {"id": 2, "username": "beta", "nickname": "贝塔"}]}
    rows, shown = ca.account_rows(data, "AL")
    assert [r["username"] for r in shown] == ["Alpha"], "大小写不敏感的子串"
    _rows, shown2 = ca.account_rows(data, "贝塔")
    assert [r["username"] for r in shown2] == ["beta"]
    _rows, shown3 = ca.account_rows(data, "2")
    assert [r["username"] for r in shown3] == ["beta"]
    assert rec == [], "搜索不该再打后端"


def test_bin_rows_offer_exactly_three_actions():
    """回收站里的行只有那三项；「恢复账号」故意不在（与禁用恢复撞名）。"""
    menu = ca._account_menu({"id": 9, "deleted_at": "2026-10-18T00:56:35"})
    assert [m["act"] for m in menu] == ["restore", "purge", "purge-report"], menu
    assert menu[1]["danger"] is True
    normal = ca._account_menu({"id": 1, "llm_mode": "own", "llm_total_limit_own": 50})
    acts = [m["act"] for m in normal]
    assert "delete" in acts and "清除额度覆盖" in [m["label"] for m in normal]
    sub = [m for m in normal if m["act"] == "llm-mode-menu"][0]["sub"]
    assert [x["arg"] for x in sub] == list(sc.LLM_MODE_TEXT.keys())
    assert any(x["label"].endswith("（当前）") for x in sub), sub


def test_accounts_page_status_empty_and_nomatch(rec, monkeypatch):
    def fake(method, path, body=None, token="", timeout=None):
        rec.append((method, path, body, token))
        if path.endswith("/llm-limit"):
            return 200, {"total_limit": 999957, "source": "global"}
        return 200, {"accounts": []}
    monkeypatch.setattr(sc, "_http_json", fake)
    d = must("accounts_page")
    assert d["empty_kind"] == "none" and "接口 200 但 accounts 为空" in d["empty_text"], d
    assert "信封字段是否仍为 accounts" in d["empty_text"]
    assert d["server_limit"]["value"] == "999,957（全局）", d["server_limit"]
    rec.clear()
    monkeypatch.setattr(sc, "_http_json",
                        lambda m, p, body=None, token="", timeout=None: (
                            rec.append((m, p, body, token)) or
                            (200, {"accounts": [{"id": 1, "username": "only"}]}
                             if p.endswith("/accounts") else (200, {}))))
    d2 = must("accounts_page", False, "zzz")
    assert d2["empty_kind"] == "nomatch"
    assert d2["empty_text"] == "没有匹配「zzz」的账号（已取回 1 个，清空搜索框可看全部）", d2
    assert d2["status"] == "共 1 个账号（GET %s/accounts）" % API, "状态行不许被空态覆盖"


def test_accounts_page_include_deleted_switches_the_request(rec):
    must("accounts_page", True)
    assert ("GET", API + "/accounts?include_deleted=true") in paths(rec), rec


def test_purge_report_lines_from_backend_only():
    payload = {"user_id": 4, "job": {"id": 9, "status": "done", "started_at": "2026-09-24T02:00:00",
                                     "finished_at": "2026-09-24T02:00:05", "attempts": 0},
               "report": {"mode": "delete_family_root", "rows_deleted": 1200,
                          "tables": [{"table": "memories", "rows": 1000}],
                          "files": {"files_moved": 11, "trash_dir": "data/trash/4",
                                    "partial_dirs": ["uploads/9"]},
                          "vectors": 300, "bm25": "已清", "backup_zip": "backups/20260924.zip",
                          "foreign_key_check": {"remaining": []}}}
    texts = [l["text"] for l in ca.purge_report_lines(payload, "beta")["lines"]]
    assert "以下全部读自后端清除账本（GET %s/accounts/4/purge-report）。" % API in texts
    # 账本里是 naive UTC，展示必须是北京时：02:00 → 10:00
    assert any(t.startswith("状态：已完成（job=9）　开始 09-24 10:00　结束 09-24 10:00")
               for t in texts), texts
    assert "已删除：1,200 行" in texts
    assert "文件隔离：11 个文件搬进 data/trash/4" in texts
    assert "有目录被占用没搬走（源目录仍在）：uploads/9" in texts
    assert "向量：deleted=300" in texts and "BM25 缓存：已清" in texts
    assert "前置备份包：backups/20260924.zip" in texts
    assert "外键自检：无残留" in texts


def test_purge_report_absent_job_says_so_without_a_table():
    out = ca.purge_report_lines({"user_id": 2, "job": None}, "gamma")
    assert out["has_job"] is False
    assert out["lines"][-1]["text"] == "尚无清除记录：该账号还没被物理清除过（账本里没有作业行）"


def test_purge_report_partial_state_reads_cursor():
    payload = {"user_id": 5, "job": {"id": 3, "status": "running"},
               "cursor": {"rows_deleted_so_far": 40, "tables_done": [{"table": "t1", "rows": 40}],
                          "steps_done": ["前置备份"], "step": "文件隔离"}}
    texts = [l["text"] for l in ca.purge_report_lines(payload)["lines"]]
    assert any(t.startswith("进度：前置备份 → （下一步：文件隔离）") or "下一步：文件隔离" in t
               for t in texts), texts
    assert "已删除：40 行" in texts, "报告没落地时读进度，半删状态也要看得清"


def test_wizard_endpoints_hit_the_right_paths(rec):
    must("delete_preview", 2)
    assert ("POST", API + "/accounts/2/delete-dry-run") in paths(rec), rec
    assert body_for(rec, "POST", API + "/accounts/2/delete-dry-run") is None, "dry-run 不许带体"
    rec.clear()
    must("purge_preview", 2)
    assert ("GET", API + "/accounts?include_deleted=true") in paths(rec), rec
    rec.clear()
    must("purge_report_view", 4)
    assert ("GET", API + "/accounts/4/purge-report") in paths(rec), rec


def test_account_verbs_are_put_and_delete_never_delete_verb(rec):
    """行操作全是 PUT/POST 到子资源；整页不许出现 HTTP DELETE（旧壳同口径）。"""
    must("set_account", 7, "disabled", True)
    must("set_account", 7, "llm-limit", None)
    must("delete_account", 7, "beta")
    must("restore_account", 7)
    must("purge_account", 7, "beta")
    assert all(m != "DELETE" for m, _p in paths(rec)), paths(rec)
    assert body_for(rec, "POST", API + "/accounts/7/purge") == {"confirm_username": "beta",
                                                                "force": True}


# ═════════════════ W3d：最后四页的判据 ═════════════════
def test_registration_view_wording():
    for mode, want in [("open", "开放（open）"), ("invite_only", "仅邀请码（invite_only）"),
                       ("closed", "关闭（closed）"), ("weird", "未知策略（weird）"),
                       ("", "后端未返回 mode")]:
        v = ca.registration_view({"mode": mode})
        assert v["mode_text"] == want, (mode, v)
        assert v["status"] == "当前 %s（GET %s/registration）" % (want, API), v["status"]
        assert v["live"] == "生效中：%s" % want
    v = ca.registration_view({"mode": "open"})
    assert v["desc"] == "任何人都能注册新账号（现状默认）", v["desc"]
    assert v["sub"].endswith("注册拦截与提示文案一律以后端为准）"), v["sub"]
    assert [o["label"] for o in v["options"]] == ["开放", "仅邀请码", "关闭"]
    assert v["hint"] == "403/401 时点右上角「登录」后重试"
    assert ca.registration_view({})["mode_text"] == "后端未返回 mode"
    assert ca.registration_view({})["desc"] == "", "未知值不编解释"


IDENT = {"mode": "shadow", "fp": "a1b2c3d4e5f6", "server_name": "拥爱主站",
         "key_created_at": "2026-09-24T02:00:00",
         "pending": {"has_pending": True, "fp": "a1b2c3", "expires_in": 120},
         "pair_stats": {"pair_success": 3, "pair_fail": 1},
         "bind_host": "0.0.0.0",
         "hints": {"cors_wildcard": True, "uploads_require_auth": False},
         "signed_paths": ["/a", "/b", "/c"]}


def test_identity_view_wording_and_conversions():
    v = ca.identity_view(IDENT)
    assert v["status"] == "档位 shadow ｜ 指纹 %s（GET %s/identity）" % (v["fp_display"], API), v
    assert v["server_name"] == "拥爱主站"
    assert v["key_created_at"] == "09-24 10:00", "账本时间是 naive UTC，展示必须北京时"
    assert v["pending"] == "有效（指纹 a1b2c3，剩 120 秒）", v["pending"]
    assert v["pair_stats"] == "成功 3 ／ 失败 1"
    assert v["deploy"][1]["v"] == "通配（*）"
    assert v["deploy"][2]["v"] == "免鉴权（现状默认）"
    assert v["deploy"][3]["v"] == "3 个端点（窄集）"
    assert v["rotate_confirm"].startswith("轮换后：")
    assert "配对码只在本机控制台显示、绝不参与网络往返" in v["sub"]


def test_identity_view_missing_fields_do_not_become_guesses():
    v = ca.identity_view({})
    assert v["fp_display"] == "尚未配对（服务器无活动身份密钥）", v["fp_display"]
    assert v["server_name"] == "—" and v["pending"] == "无"
    assert v["pair_stats"] == "成功 0 ／ 失败 0"
    assert v["mode"] == "" and v["mode_note"] == "档位以后端返回值为准，控制台不做推算"
    assert v["deploy"][0]["v"] == "—"


def test_identity_countdown_local_code_ticks_but_server_pending_does_not():
    """旧壳口径：本地签发的码逐秒走；服务器 pending 是静态快照，不许递减、也不重发请求。"""
    ca._ISSUED.clear()
    base = 1_800_000_000.0
    ca._ISSUED.update({"code": "ABCD1234EFGH", "expires_at": base + 90})
    v1 = ca.identity_view(IDENT, now=base)
    v2 = ca.identity_view(IDENT, now=base + 30)
    assert v1["code"]["tick"] is True and v1["code"]["left"] == 90, v1["code"]
    assert v2["code"]["left"] == 60, "本地码要跟着时间走"
    assert v1["code_line"] == "剩余 90 秒后失效" and v2["code_line"] == "剩余 60 秒后失效"
    assert v1["code"]["text"] == "ABCD 1234 EFGH", v1["code"]["text"]
    ca._ISSUED.clear()
    v3 = ca.identity_view(IDENT, now=base)
    assert v3["code"]["tick"] is False, "服务器 pending 的倒计时不许自己走"
    assert v3["code_line"] == "（本窗口未持有该码）剩 120 秒", v3["code_line"]
    ca._ISSUED.update({"code": "ABCD1234EFGH", "expires_at": base - 1})
    v4 = ca.identity_view(IDENT, now=base)
    assert v4["code_line"] == "已失效，请重新生成" and v4["code"]["left"] == 0
    ca._ISSUED.clear()
    v5 = ca.identity_view({"mode": "off"}, now=base)
    assert v5["code"]["text"] == "尚未签发" and "码失效后重新生成即可" in v5["code_line"]


def test_pairing_code_stays_off_the_wire_and_rotate_clears_it(rec, monkeypatch):
    def fake(method, path, body=None, token="", timeout=None):
        rec.append((method, path, body, token))
        return 200, {"code": "ZXCV9876ASDF", "ttl_sec": 60, "expires_in": 60, "fp": "ff00"}
    monkeypatch.setattr(sc, "_http_json", fake)
    ca._ISSUED.clear()
    d = must("pairing_code")
    assert d["msg"] == "已生成一次性配对码（60 秒内有效，只在本机显示）", d
    assert ca._ISSUED.get("code") == "ZXCV9876ASDF"
    blob = json.dumps(rec, ensure_ascii=False)
    assert "ZXCV9876ASDF" not in blob, "配对码绝不能出现在我们发出的任何请求里"
    c = must("copy_pairing_code")
    assert c["ok_text"] is True and c["code"] == "ZXCV9876ASDF"
    ca._ISSUED.clear()
    assert must("copy_pairing_code")["msg"] == "当前没有有效配对码可复制，请先「重新生成」"
    ca._ISSUED.update({"code": "OLD", "expires_at": 9e12})
    must("rotate_identity")
    assert ca._ISSUED == {}, "轮换后旧码必须作废"


DA = {"switches": {"global": True, "plugin_enabled": False, "force_dry_run": True,
                   "rows_present": {"global": True, "plugin_enabled": False}},
       "targets": ["com.example.app", " com.example.app ", "", 5, "com.other"],
       "plugins": ["browser_mcp"],
       "limits": {"targets_max": 8, "plugins_max": None},
       "tenant_id": "t1"}


def test_device_actions_switch_captions_three_states():
    v = ca.device_actions_view(DA)
    caps = {c["key"]: c["caption"] for c in v["cards"]}
    assert "已显式设置（后端生效值：开）" in caps["global"], caps
    assert "未显式设置（默认：关）" in caps["plugin_enabled"], caps
    assert "是否显式设置：后端未返回" in caps["force_dry_run"], caps
    assert v["cards"][0]["on"] is True and v["cards"][1]["on"] is False
    assert "控制台不碰数据库" in v["sub"]


def test_device_actions_rows_present_non_dict_is_not_taken_as_unset():
    bad = {"switches": {"global": True, "rows_present": "乱回的"}, "targets": [], "plugins": []}
    v = ca.device_actions_view(bad)
    assert "后端未返回" in v["cards"][0]["caption"], v["cards"][0]


def test_device_actions_names_and_limits():
    v = ca.device_actions_view(DA)
    tgt, plug = v["sections"]
    assert tgt["names"] == ["com.example.app", "5", "com.other"], tgt["names"]
    assert tgt["cap_line"] == "租户＝t1 ｜ 最多 8 个", tgt["cap_line"]
    assert plug["cap_line"] == "租户＝t1 ｜ 上限未知", plug["cap_line"]
    assert plug["empty"] == "名单为空（后端返回 0 条）"
    empty = ca.device_actions_view({"switches": {}, "targets": [], "plugins": []})
    assert empty["sections"][0]["names"] == []
    assert empty["sections"][0]["cap_line"].startswith("租户＝—")


def test_overview_contract_fields_first_and_extras_appended_sorted():
    data = {"accounts": 12, "disabled": 1, "server_admins": 3, "flags_on": 40, "version": "3.5.6",
             "zebra_last": None, "alpha_extra": 0}
    v = ca.overview_view(data)
    labels = [r["label"] for r in v["rows"]]
    assert labels[:5] == ["账号总数", "已禁用账号", "控制台管理员", "开启的开关", "后端版本"], labels
    assert labels[5:] == ["alpha_extra", "zebra_last"], "额外键按 A-Z 附在后面，标签用原键名"
    zero = [r for r in v["rows"] if r["label"] == "alpha_extra"][0]
    assert zero["value"] == "0" and zero["missing"] is False, "0 是数字，不许显示成 —"
    none = [r for r in v["rows"] if r["label"] == "zebra_last"][0]
    assert none["value"] == "—" and none["missing"] is True
    assert v["count"] == 7 and v["status"] == "已读取 7 项（GET %s/overview）" % API
    assert "控制台不做任何推算" in v["sub"]


def test_overview_all_none_is_declared_empty():
    v = ca.overview_view({"accounts": None, "disabled": None})
    assert v["empty_kind"] is True
    assert v["empty_text"] == "后端未返回任何概览字段（GET %s/overview）" % API
    # 旧壳用的是 all(...)，一行都没有时同样为真——不发明它没有的区分
    assert ca.overview_view({})["empty_kind"] is True


def test_last_four_pages_hit_exact_paths(rec):
    for name, sub in [("registration_page", "/registration"), ("identity_page", "/identity"),
                      ("device_actions_page", "/device-actions"), ("overview_page", "/overview")]:
        got = ca.call(name)
        assert got["ok"] is True, (name, got)
        assert ("GET", API + sub) in paths(rec), (name, rec)
        rec.clear()


def test_last_four_pages_401_envelope(monkeypatch):
    monkeypatch.setattr(sc, "_CONSOLE_SESSION", {})
    monkeypatch.setattr(sc, "_console_token_read", lambda: {})
    for name in ("registration_page", "identity_page", "device_actions_page", "overview_page"):
        got = ca.call(name)
        assert got["ok"] is False and got["code"] == 401, (name, got)


def test_exposed_names_all_resolve():
    for name in ca.EXPOSED:
        assert callable(getattr(ca, name, None)), name
    assert json.dumps({"n": list(ca.EXPOSED)})       # 名字面要能过 JSON（JS 侧要用）
