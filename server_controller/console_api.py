"""拥爱服务器控制台 · 无 Tk 的数据面（W2）。

新壳（pywebview / HTML）与旧壳（tkinter）共用同一批判据：这里只 import 共享真源
`console_core` 的 `_admin_request` / `_http_json` / `_guard_http_url` / 本地读数函数，
不复制、不改写，所以两个壳看到的数字与错误语义必然一致。
W6-A 后网页壳不再连带加载 Tk 巨石，启动只依赖纯标准库数据面。

约定：每个入口都返回信封 `{"ok": bool, ...}`，异常一律收敛，绝不抛到 JS 层。
    成功 → {"ok": True, "data": ...}
    失败 → {"ok": False, "code": int, "error": str, "path": str}
code 沿用旧壳的语义化状态码：0=网络不可达，401=登录态，403=权限/服务器锁定，
404=接口未就绪，其余＝后端原样状态码。
"""
import json
import os
import signal as _sig
import subprocess
import sys
import datetime as dt
import time as _t

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import console_core as sc  # noqa: E402  W6-A 共享真源，纯标准库、绝不启动 Tk

ADMIN_PREFIX = sc.ADMIN_API_PREFIX
EXPOSED = ()  # 允许 JS 调用的名字，见文件末尾


def _envelope(fn):
    def wrapped(*a, **kw):
        try:
            return {"ok": True, "data": fn(*a, **kw)}
        except sc.AdminApiError as e:
            return {"ok": False, "code": int(getattr(e, "code", 0) or 0),
                    "error": str(getattr(e, "message", "") or e)[:300],
                    "path": str(getattr(e, "path", "") or "")}
        except Exception as e:  # noqa: BLE001  本地读数炸了也要回信封，不许白屏
            return {"ok": False, "code": -1, "error": ("%s: %s" % (type(e).__name__, e))[:300],
                    "path": ""}
    wrapped.__name__ = fn.__name__
    return wrapped


# ───────────────────────── 会话 ─────────────────────────
@_envelope
def session():
    """只读登录态；本函数永不写 console_token.json。"""
    user = sc._console_login_state()
    return {"logged_in": bool(user), "username": user, "prefix": ADMIN_PREFIX}


@_envelope
def login(username, password):
    data = sc._admin_login(str(username or ""), str(password or ""))
    return {"username": data.get("username") or username,
            "user_id": data.get("user_id"), "expires_in": data.get("expires_in")}


@_envelope
def logout():
    sc._console_clear_session()
    return {"logged_out": True}


# ───────────────────────── 本地读数 ─────────────────────────
def _safe(fn, default):
    try:
        out = fn()
        return default if out is None else out
    except Exception:  # noqa: BLE001
        return default


def _server_state():
    """服务器控制页与页眉共用的状态：活/PID/暂停/目标/健康。未包装，供内部组合用。"""
    alive = _safe(sc._check_alive, False)
    remote = _is_remote()
    pid = 0 if remote else _safe(sc._get_pid, 0)
    return {
        "alive": alive,
        "pid": pid,
        "remote": remote,
        "paused": bool(os.path.exists(sc.PAUSE_FLAG)) if hasattr(sc, "PAUSE_FLAG") else False,
        "target": ("远程 %s:%d" % (sc.TARGET_HOST, sc.TARGET_PORT)) if remote
                  else ("端口 %d" % sc.TARGET_PORT),
        "health": _safe(sc._fetch_health, "—"),
        "liveness": _safe(sc._fetch_liveness, {}),
    }


def _is_remote():
    return _safe(sc._is_remote, False)


def _is_nt():
    """单独包一层，好让测试能替换平台分支（旧壳读的是 os.name）。"""
    return os.name == "nt"


@_envelope
def log_tail():
    return {"text": _safe(sc._tail_log, ""), "path": getattr(sc, "APP_LOG", "")}


def heat_peak(heat):
    """热力图色阶上限：忽略未来占位日，全零时返回 1（避免除零，也不画假线）。"""
    vals = [int(r.get("tokens") or 0) for r in (heat or []) if not r.get("future")]
    if not vals:
        return 1
    return max(max(vals), 1)


SENSITIVE = ("api_key", "apikey", "token", "password", "secret")


def merge_flags(admin_rows, pub_rows):
    """管理面 /flags 与公开 /system/feature-flags 按键名合并。

    口径抄旧壳 3766-3797：中文标题/分组/visible/source/scope 只有公开端点有，
    管理面的 title/desc 恒为 null ⇒ 前端合并，不改后端。公开端点取不到时
    管理面字段原样留着，不假装是公开端给的。
    """
    pub_map = {}
    for r in (pub_rows or []):
        if isinstance(r, dict) and r.get("key"):
            pub_map[str(r["key"])] = r
    rows = []
    for r in (admin_rows or []):
        if not isinstance(r, dict):
            continue
        pub = pub_map.get(str(r.get("key") or ""), {})
        cm = pub.get("meta") if isinstance(pub.get("meta"), dict) else {}
        merged = {k: v for k, v in dict(r).items() if str(k).lower() not in SENSITIVE}
        merged["title"] = cm.get("title") or r.get("title")
        merged["desc"] = cm.get("desc") or r.get("desc")
        merged["group"] = str(cm.get("group") or "other")
        merged["group_order"] = cm.get("group_order", 99)
        merged["order"] = cm.get("order", 9999)
        merged["visible"] = bool(cm.get("visible"))
        merged["source"] = pub.get("source")
        merged["scope"] = pub.get("scope")
        merged["user_enabled"] = pub.get("user_enabled")
        rows.append(merged)
    return rows


@_envelope
def server_state():
    return _server_state()


@_envelope
def dashboard():
    """仪表盘六格 + 26 周热力图（与旧壳同一批函数、同一把尺）。"""
    st = _server_state()
    stats = _safe(sc._read_db_stats, {}) or {}
    vlm = _safe(sc._read_vlm_status, None)
    heat = _safe(lambda: sc._read_token_heatmap(sc.HEATMAP_WEEKS), [])
    if isinstance(vlm, dict) and vlm.get("configured"):
        vlm_text, vlm_cap = str(vlm.get("model") or "已配置"), "云端视觉 API"
    elif vlm is None:
        vlm_text, vlm_cap = "—", "云端视觉 API"
    else:
        vlm_text, vlm_cap = "未配置", "云端视觉 API"
    return {
        "kpi": {
            "server": "运行中" if st["alive"] else "已停止",
            "serverCap": ("远程 %s:%d" % (sc.TARGET_HOST, sc.TARGET_PORT)) if st["remote"]
                          else ("PID %d · 端口 %d" % (st["pid"], sc.TARGET_PORT)),
            "health": st["health"],
            "vlm": vlm_text, "vlmCap": vlm_cap,
            "chars": sc._fmt_int(stats.get("characters")),
            "mems": sc._fmt_int(stats.get("memories")),
            "tokens": sc._fmt_int(stats.get("tokens")),
        },
        "heat": heat,
        "heatPeak": heat_peak(heat),
        "memSpark": _safe(lambda: sc._read_mem_daily_new(sc.KPI_SPARK_DAYS), []),
        "tokenSpark": _safe(lambda: sc._spark_from_heat(heat, sc.KPI_SPARK_DAYS), []),
        "meta": {"target": st["target"], "at": dt.datetime.now().strftime("%H:%M:%S"),
                 "next": "下次刷新 20s", "version": "—"},
    }


# ───────────────────────── 进程控制 ─────────────────────────
@_envelope
def server_control(action):
    """start / stop / restart：逐字照旧壳 4806-4848 的分支与文案。

    旧壳把这段写在三个方法里（带 Tk），这里不能 import 那个类，所以是**第二处实现**；
    test_console_api.py 的对齐测试会 monkeypatch 掉 `_run_manager`/`_start_uvicorn`/
    `os.kill`，把两个壳发出的命令与返回文案逐条比对，分叉当场变红。
    """
    if action not in ("start", "stop", "restart"):
        raise ValueError("action 只认 start / stop / restart")
    if _is_remote():
        return {"action": action, "refused": True,
                "msg": "远程监控模式下不能从本机%s远程服务器"
                       % {"start": "启动", "stop": "停止", "restart": "重启"}[action]}
    nt = _is_nt()
    if action == "start":
        if nt:
            sc._run_manager("start")
            return {"action": action, "msg": "启动指令已发出（server_manager），服务器约 30-60 秒就绪"}
        sc._start_uvicorn()
        return {"action": action, "msg": "已启动 uvicorn（后台），服务器约 30-60 秒就绪"}
    if action == "stop":
        if nt:
            sc._run_manager("stop")
            return {"action": action, "msg": "已停止（server_manager 已清理 uvicorn + watchdog）"}
        pid = sc._port_pid(sc.TARGET_PORT)
        if pid:
            os.kill(pid, _sig.SIGTERM)
            return {"action": action, "pid": pid, "msg": "已停止（PID %d）" % pid}
        return {"action": action, "msg": "未检测到运行中的服务器（端口 %d 无监听）" % sc.TARGET_PORT}
    if nt:
        sc._run_manager("restart")
        return {"action": action, "msg": "重启指令已发出（server_manager），服务器约 30-60 秒就绪"}
    pid = sc._port_pid(sc.TARGET_PORT)
    if pid:
        os.kill(pid, _sig.SIGTERM)
    sc._start_uvicorn()
    return {"action": action, "pid": pid, "msg": "已重启 uvicorn（后台）"}


# ═════════════════ 服务器控制页（口径抄旧壳 2349-2400 与 4850-4910）═════════════════
ADDR_LABELS = (("local", "本机访问"), ("lan", "局域网（同 Wi-Fi）"), ("ts", "Tailscale 跨网"))


def _address_view():
    """三行地址：{key: (纯净URL, 展示文本)}。远程模式不探本机局域网/Tailscale。"""
    if _is_remote():
        url = sc._target_base()
        return {"local": (url, url + "    （当前监控目标·远程主机）"),
                "lan": ("", "远程模式下不探测本机局域网地址"),
                "ts": ("", "远程模式下不探测 Tailscale")}
    lan_ip = _safe(sc._get_lan_ip, "")
    ts_ip = _safe(sc._get_tailscale_ip, "")
    local_url = "http://127.0.0.1:%d" % sc.TARGET_PORT
    if lan_ip:
        lan_url = "http://%s:%d" % (lan_ip, sc.TARGET_PORT)
        lan_show = lan_url + ("    （疑似代理/TUN 虚拟网卡，手机若连不上请关闭系统代理后重新探测）"
                              if _safe(lambda: sc._looks_tun(lan_ip), False) else "")
    else:
        lan_url, lan_show = "", "未探测到局域网 IP（请确认本机已连网）"
    if ts_ip:
        ts_url = "http://%s:%d" % (ts_ip, sc.TARGET_PORT)
        ts_show = ts_url
    else:
        ts_url, ts_show = "", "未安装/未登录 Tailscale（跨网访问时使用）"
    return {"local": (local_url, local_url + "    （服务器本机自测）"),
            "lan": (lan_url, lan_show), "ts": (ts_url, ts_show)}


def _address_rows(view):
    rows = []
    for key, label in ADDR_LABELS:
        url, show = view.get(key, ("", "探测失败"))
        rows.append({"key": key, "label": label, "url": url, "show": show,
                     "copyable": isinstance(url, str) and url.startswith("http")})
    return rows


def _target_state():
    remote = _is_remote()
    if remote:
        mode = "当前：远程监控模式 — 仅显示在线/健康；进程启停、本地数据库统计与热力图不可用"
    else:
        warn = "" if sc.TARGET_PORT == sc._DEFAULT_TARGET_PORT else             "（注意：本机服务固定监听 8000，改端口仅用于远程监控）"
        mode = "当前：本机模式 — 可启停服务、读取本地数据库统计与 Token 热力图" + warn
    return {"host": sc.TARGET_HOST, "port": sc.TARGET_PORT, "remote": remote, "mode": mode,
            "default_host": sc._DEFAULT_TARGET_HOST, "default_port": sc._DEFAULT_TARGET_PORT}


def _set_target(host, port):
    """校验 → 读-合-写两键 → 生效到本进程。写盘口径与旧壳 _persist_target 逐字一致。"""
    norm_host, norm_port = sc._normalize_endpoint(str(host or ""), port)
    cfg = {}
    try:
        with open(sc.CONFIG, encoding="utf-8") as f:
            loaded = json.load(f)
        cfg = loaded if isinstance(loaded, dict) else {}
    except Exception:  # noqa: BLE001  配置坏了也要能改回来
        cfg = {}
    cfg["controller_target_host"] = norm_host
    cfg["controller_target_port"] = norm_port
    parent = os.path.dirname(sc.CONFIG)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(sc.CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    sc.set_server_target(norm_host, norm_port)
    remote = _is_remote()
    mode = "远程仅监控" if remote else "本机完整模式"
    note = "" if remote or norm_port != sc._DEFAULT_TARGET_PORT else         "；注意本机服务固定监听 8000，自定义端口只适用于远程监控"
    return {"host": norm_host, "port": norm_port, "remote": remote,
            "msg": "监控目标已切换为 %s:%d（%s）%s" % (norm_host, norm_port, mode, note)}


@_envelope
def server_page():
    """服务器控制页一次给全：状态＋目标＋三行地址。20s 刷新走这一个入口。"""
    st = _server_state()
    return {"state": st, "target": _target_state(), "rows": _address_rows(_address_view()),
            "actions": ["start", "stop", "restart"]}


@_envelope
def probe_addresses():
    return {"rows": _address_rows(_address_view())}


@_envelope
def set_target(host, port):
    return _set_target(host, port)


@_envelope
def reset_target():
    return _set_target(sc._DEFAULT_TARGET_HOST, sc._DEFAULT_TARGET_PORT)


BOOT = {"ping": None}


@_envelope
def ping(info):
    """页面渲染完的回敲。壳层用它判断窗口到底加载成功没有，不靠人眼。"""
    BOOT["ping"] = {"at": dt.datetime.now().strftime("%H:%M:%S"), "info": info}
    return BOOT["ping"]

THEMES = ("dark", "light")
THEME_NAME = {"dark": "墨纸", "light": "纸色"}


@_envelope
def set_theme(name):
    """主题偏好只写新壳自己的桌面状态文件。

    **绝不写 server_config.json**：那份是旧壳与后端的配置，controller_theme 一改
    旧壳跟着变，等于我替用户做了他没做的决定（W0 定的红线：新壳对旧配置只读）。
    """
    import console_desktop as cd
    name = str(name)
    if name not in THEMES:
        raise ValueError("主题只有两档：dark(墨纸) / light(纸色)，收到 %r" % (name,))
    if not cd.save_state(theme=name):
        raise OSError("状态文件写不进去，主题这次不会记住：%s" % cd.state_path())
    return {"theme": name, "shown": THEME_NAME[name], "path": cd.state_path()}


@_envelope
def get_theme():
    import console_desktop as cd
    saved = cd.load_state().get("theme")
    return {"theme": saved if saved in THEMES else "", "shown": THEME_NAME.get(saved, "未记住")}


# ═════════════════ W3b：管理三页的展示整形（纯函数，可脱离 GUI 测）═════════════════
def _modality_order_key(key):
    """按旧壳 MODALITY_ORDER 排，没登记的键排最后、按字面。"""
    order = getattr(sc, "MODALITY_ORDER", ())
    return (order.index(key) if key in order else len(order), str(key))


def modality_rows(data):
    """每个模态一行：标题「中文名（key）」、状态点、provider/日限额、四个字段。

    密钥永不下发原文，只给「已配置 / 未配置」——旧壳那句
    「留空＝不修改（当前 已配置）」是它的输入口径，这里逐字保留。
    """
    rows = []
    for r in sorted((data or {}).get("modalities") or [],
                    key=lambda x: _modality_order_key(str(x.get("key") or ""))):
        key = str(r.get("key") or "")
        label = str(r.get("label") or key)
        dl = r.get("daily_limit")
        has_key = bool(r.get("has_api_key"))
        rows.append({
            "key": key,
            "title": "%s（%s）" % (label, key),
            "enabled": bool(r.get("enabled")),
            "provider": r.get("provider") or "—",
            "daily_limit": dl,
            "cap": "provider %s · 日限额 %s" % (r.get("provider") or "—",
                                                 dl if dl not in (None, "") else "—"),
            "model": r.get("model") or "",
            "base_url": r.get("base_url") or "",
            "has_api_key": has_key,
            "key_hint": "留空＝不修改（当前 %s）" % ("已配置" if has_key else "未配置"),
        })
    return rows


def flag_groups(data):
    """按公开端给的 group/group_order/order 分组排序；组名走共享真源的中文表。

    server_locked 为真＝服务器锁定，这一行用户点不动（判据在后端，前端只翻译）。
    """
    labels = {}
    try:
        labels = dict(sc.GROUP_LABELS or {})   # W6-A：中文组名已迁入 console_core
    except Exception:  # noqa: BLE001
        labels = {}
    buckets = {}
    for r in (data or {}).get("flags") or []:
        if not isinstance(r, dict):
            continue
        g = str(r.get("group") or "other")
        buckets.setdefault(g, {"group": g, "label": labels.get(g) or r.get("group_label") or g,
                               "order": r.get("group_order", 99), "rows": []})["rows"].append({
            "key": str(r.get("key") or ""),
            "title": r.get("title") or r.get("key") or "",
            "desc": r.get("desc") or "",
            "enabled": bool(r.get("enabled")),
            "self_service": bool(r.get("self_service")),
            "server_locked": bool(r.get("server_locked")),
            "source": r.get("source"),
            "scope": r.get("scope"),
            "user_enabled": r.get("user_enabled"),
            "visible": bool(r.get("visible")),
            "order": r.get("order", 9999),
        })
    out = sorted(buckets.values(), key=lambda g: (g["order"], g["group"]))
    for g in out:
        g["rows"].sort(key=lambda x: (x["order"], x["key"]))
        g["count"] = len(g["rows"])
    return out




def _redact(v):
    """审计的 before/after 是后端原样记的，密钥类字段在这里遮掉——
    展示层不该把 sk-… 摊在屏幕上，哪怕它已经在库里了。"""
    if isinstance(v, dict):
        return {k: ("***" if str(k).lower() in SENSITIVE else _redact(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [_redact(x) for x in v]
    return v


def audit_rows(data, limit=None):
    """limit 与旧壳同一把尺：非数字/空 → 100；条数与状态文案同源，不各说各话。"""
    try:
        lim = int(str(limit if limit is not None else 100).strip() or 100)
    except (TypeError, ValueError):
        lim = 100
    if lim < 1:
        lim = 100
    rows = []
    for r in (data or {}).get("entries") or (data or {}).get("audit") or []:
        if not isinstance(r, dict):
            continue
        raw = r.get("created_at") or ""
        rows.append({"created_at": sc._fmt_dt(raw),   # 库里是 naive UTC，必须换算成北京时
                     "raw": raw,
                     "actor": r.get("actor_username") or ("用户 %s" % r.get("actor_user_id")
                                                          if r.get("actor_user_id") is not None else "—"),
                     "action": r.get("action") or "—",
                     "target": r.get("target") or "—",
                     "before": _redact(r.get("before")), "after": _redact(r.get("after"))})
    return {"limit": lim, "rows": rows, "count": len(rows),
            "status": "共 %d 条（GET %s/audit?limit=%d）" % (len(rows), ADMIN_PREFIX, lim)}


@_envelope
def models_page():
    return {"rows": modality_rows(sc._admin_request("GET", "/modalities")),
            "logged_in": bool(sc._console_login_state())}


@_envelope
def flags_page():
    return dict(_flags_payload(), logged_in=bool(sc._console_login_state()))


@_envelope
def audit_page(limit=None):
    data = sc._admin_request("GET", "/audit?limit=%d" % _audit_limit_num(limit))
    return dict(audit_rows(data, limit), logged_in=bool(sc._console_login_state()))


def _audit_limit_num(limit):
    try:
        n = int(str(limit if limit is not None else 100).strip() or 100)
    except (TypeError, ValueError):
        n = 100
    return n if n >= 1 else 100


# ═════════════════ 运行日志页（口径抄旧壳 2495-2550 与 5105-5125）═════════════════
@_envelope
def log_page():
    text = _safe(sc._tail_log, "")
    return {"text": text, "empty": not str(text).strip(),
            "hint": "还没有日志写入（app.log / server_stderr.log）",
            "paths": {"app": getattr(sc, "APP_LOG", ""),
                      "stderr": _safe(lambda: sc._log_read_path(sc.STDERR_LOG), "")}}


@_envelope
def write_log_export(dest):
    dest = str(dest or "")
    if not dest:
        raise ValueError("没选文件")
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(_safe(sc._tail_log, ""))
    return {"path": dest, "msg": "日志已导出：%s" % dest}


@_envelope
def open_log_file():
    """打开的是系统查看器，不是编辑器；路径为空或不存在就回话，不静默。"""
    path = _safe(lambda: sc._log_read_path(sc.STDERR_LOG), "")
    if not path or not os.path.exists(path):
        return {"opened": False, "msg": "日志文件还不存在：%s" % path}
    if os.name == "nt":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
    return {"opened": True, "path": path, "msg": "已用系统查看器打开：%s" % path}


# ───────────────────────── 11 页的接口面 ─────────────────────────
@_envelope
def models():
    return sc._admin_request("GET", "/modalities")


@_envelope
def save_modality(key, body):
    return sc._admin_request("PUT", "/modalities/%s" % key, body=body)


@_envelope
def accounts(include_deleted=False):
    sub = "/accounts?include_deleted=true" if include_deleted else "/accounts"
    out = dict(sc._admin_request("GET", sub))
    limit = _safe(lambda: sc._admin_request("GET", "/llm-limit"), {}) or {}
    out["limit"] = {"total_limit": limit.get("total_limit"), "source": limit.get("source")}
    return out


@_envelope
def set_account(uid, what, value):
    """what ∈ disabled | server-admin | llm-mode | llm-limit（路径与旧壳逐字一致）。"""
    table = {"disabled": ("PUT", "/accounts/%s/disabled", {"disabled": value}),
             "server-admin": ("PUT", "/accounts/%s/server-admin", {"enabled": value}),
             "llm-mode": ("PUT", "/accounts/%s/llm-mode", {"llm_mode": value}),
             "llm-limit": ("PUT", "/accounts/%s/llm-limit", {"total_limit": value})}
    if what not in table:
        raise ValueError("未知的行操作：%s" % what)
    method, sub, body = table[what]
    return sc._admin_request(method, sub % uid, body=body)


@_envelope
def delete_dry_run(uid):
    return sc._admin_request("POST", "/accounts/%s/delete-dry-run" % uid)


@_envelope
def delete_account(uid, confirm_username):
    return sc._admin_request("POST", "/accounts/%s/delete" % uid,
                             body={"confirm_username": confirm_username})


@_envelope
def restore_account(uid):
    return sc._admin_request("POST", "/accounts/%s/restore" % uid)


@_envelope
def purge_account(uid, confirm_username):
    return sc._admin_request("POST", "/accounts/%s/purge" % uid,
                             body={"confirm_username": confirm_username, "force": True})


@_envelope
def purge_report(uid):
    return sc._admin_request("GET", "/accounts/%s/purge-report" % uid)


@_envelope
def registration():
    return sc._admin_request("GET", "/registration")


@_envelope
def set_registration(mode):
    return sc._admin_request("PUT", "/registration", body={"mode": mode})


@_envelope
def identity():
    return sc._admin_request("GET", "/identity")


@_envelope
def pairing_code():
    """签发一次就把码留在内存（只在本机显示，绝不上网），倒计时才有的可走。"""
    data = sc._admin_request("POST", "/identity/pairing-code", body={})
    ttl = int(data.get("expires_in") or data.get("ttl_sec") or 0)
    _ISSUED.clear()
    if data.get("code"):
        _ISSUED.update({"code": str(data["code"]), "expires_at": _t.time() + ttl})
    return dict(data, ttl=ttl, msg="已生成一次性配对码（%s 秒内有效，只在本机显示）"
                % (data.get("ttl_sec") or 0))


@_envelope
def copy_pairing_code():
    """复制不发网络；没有有效码就说没有，不复制一个过期的。"""
    now = _t.time()
    code = str(_ISSUED.get("code") or "")
    if not code or float(_ISSUED.get("expires_at") or 0) <= now:
        return {"ok_text": False, "msg": "当前没有有效配对码可复制，请先「重新生成」"}
    return {"ok_text": True, "code": code, "msg": "已复制配对码到本机剪贴板（不经过网络）"}


@_envelope
def rotate_identity():
    data = sc._admin_request("POST", "/identity/rotate", body={})
    _ISSUED.clear()          # 轮换后旧码不再有效
    return dict(data, msg="身份密钥已轮换，新指纹 %s（旧设备需重新配对）"
                % sc._identity_fp_text(data.get("fp")))


def _flags_payload():
    admin = sc._admin_request("GET", "/flags")
    pub_rows = []
    try:
        token = str(sc._console_session().get("token") or "")
        status, pub = sc._http_json("GET", "/api/v1/system/feature-flags", token=token)
        if 200 <= status < 300 and isinstance(pub, dict):
            pub_rows = pub.get("flags") or []
    except Exception:  # noqa: BLE001  公开端点挂了不影响管理面能用（与旧壳同口径）
        pass
    merged = merge_flags(admin.get("flags") or [], pub_rows)
    return {"flags": merged, "groups": flag_groups({"flags": merged})}


@_envelope
def flags():
    return _flags_payload()


@_envelope
def open_channel_login():
    """在系统浏览器打开后端的本地扫码页。

    `/channel-login` 那条红线是**只放行 127.0.0.1**（二维码等于登录凭证），
    所以控制台连的是远程主机时不能去开本机这一页，直接说清楚。
    """
    import webbrowser
    host = str(getattr(sc, "TARGET_HOST", "") or "")
    try:
        port = int(getattr(sc, "TARGET_PORT", 8000) or 8000)
    except (TypeError, ValueError):
        port = 8000
    if host not in ("127.0.0.1", "localhost", "::1", ""):
        raise ValueError("渠道登录页只在服务器本机开放，控制台现在连的是 %s" % host)
    url = "http://127.0.0.1:%d/channel-login" % port
    ok = webbrowser.open(url)
    return {"url": url,
            "msg": "已在本机浏览器打开扫码页" if ok else "浏览器没打开，请手动访问 " + url}


SETTINGS_WD = (15, 3600)
SETTINGS_RF = (5, 600)


def _read_server_config():
    try:
        with open(sc.CONFIG, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


@_envelope
def server_settings():
    """读「守护检测间隔 / 界面刷新间隔」——旧壳设置对话框的那两个键。

    **C1 的例外**：新壳对 server_config.json 一律只读（W0 红线，`controller_theme` 更是一个键都不许写）。
    只有这两个键是旧壳本来就有对话框可改的，这里按同一口径来：只写这两个，其余键原样保留。
    """
    cfg = _read_server_config()
    try:
        wd = int(cfg.get("watchdog_interval_sec", 120))
    except (TypeError, ValueError):
        wd = 120
    try:
        rf = int(cfg.get("controller_refresh_ms", 20000)) // 1000
    except (TypeError, ValueError):
        rf = 20
    return {"watchdog_interval_sec": wd, "controller_refresh_sec": rf,
            "wd_range": list(SETTINGS_WD), "rf_range": list(SETTINGS_RF),
            "path": sc.CONFIG, "msg": ""}


@_envelope
def save_server_settings(wd_sec, refresh_sec):
    """写回那两个键（范围校验在本地做，落盘用临时文件替换，别的键一个字都不动）。"""
    try:
        wd, rf = int(wd_sec), int(refresh_sec)
    except (TypeError, ValueError):
        raise ValueError("两个间隔都要填整数秒")
    if not (SETTINGS_WD[0] <= wd <= SETTINGS_WD[1]):
        raise ValueError("守护间隔需在 %d-%d 秒之间" % SETTINGS_WD)
    if not (SETTINGS_RF[0] <= rf <= SETTINGS_RF[1]):
        raise ValueError("刷新间隔需在 %d-%d 秒之间" % SETTINGS_RF)
    cfg = _read_server_config()
    cfg["watchdog_interval_sec"] = wd
    cfg["controller_refresh_ms"] = rf * 1000
    path = sc.CONFIG
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return {"watchdog_interval_sec": wd, "controller_refresh_sec": rf,
            "msg": "已保存：守护 %ds / 刷新 %ds" % (wd, rf)}


@_envelope
def set_server_limit(value):
    """写服务器默认额度：0＝未设置，负数/非整数由后端拒 400（本层只透传）。"""
    body = {"total_limit": int(value)}
    return sc._admin_request("PUT", "/llm-limit", body=body)


@_envelope
def set_flag(key, body):
    return sc._admin_request("PUT", "/flags/%s" % key, body=body)


@_envelope
def audit(limit=100):
    return sc._admin_request("GET", "/audit?limit=%d" % int(limit))


@_envelope
def overview():
    return sc._admin_request("GET", "/overview")


@_envelope
def device_actions():
    return sc._admin_request("GET", "/device-actions")


@_envelope
def set_device_switch(key, enabled):
    return sc._admin_request("PUT", "/device-actions/switches",
                             body={"key": key, "enabled": bool(enabled)})


@_envelope
def add_device_target(target):
    return sc._admin_request("POST", "/device-actions/targets", body={"target": target})


@_envelope
def remove_device_target(target):
    return sc._admin_request("DELETE", "/device-actions/targets", body={"target": target})


@_envelope
def add_device_plugin(plugin):
    return sc._admin_request("POST", "/device-actions/plugins", body={"plugin": plugin})


@_envelope
def remove_device_plugin(plugin):
    return sc._admin_request("DELETE", "/device-actions/plugins", body={"plugin": plugin})

# ═════════════════ W3c：账号管理（判据一律复用旧壳的模块级函数）═════════════════
# 旧壳明写「判据不许有两套」（SC:3348-3350），所以倒计时、额度文案、体量表、
# 时间换算这些全部直接调 sc 的同名函数，这里只做"把回包摆成人话"的组装。
ACCOUNT_SUB = ("跨家庭账号：禁用/启用、授予/取消控制台管理员、设置 llm_mode、"
               "删除进回收站与到期清除（护栏与判据一律以后端为准）")
SERVER_LIMIT_TITLE = "服务器默认额度（账号无覆盖时回落到这里；0=未设置）"
SERVER_LIMIT_HINT = "负数/非整数由后端拒绝（400）"
DELETE_SUB = ("这一步只把账号移进回收站（默认 %s 天内可恢复）；"
              "到期后由后台清除器物理删除。")
PURGE_SUB = ("这一步**不可恢复**：先做前置备份，再删除该账号的全部数据行、文件、"
             "向量与 BM25 缓存。")
BLOCKED_TEXT = "后端护栏已挡（逐条原因见上），本次不会发出删除请求"
CONFIRM_MISS = "用户名不匹配：请逐字符输入「%s」"


def _account_menu(r):
    """行操作菜单与旧壳逐字一致；回收站里的行只有那三项（SC:3134-3180）。"""
    uid = r.get("id")
    in_bin = bool(r.get("deleted_at"))
    if in_bin:
        return [{"act": "restore", "label": "恢复（移出回收站）"},
                {"act": "purge", "label": "立即清除", "danger": True},
                {"act": "purge-report", "label": "查看清除结果"}]
    mode = str(r.get("llm_mode") or "")
    menu = [
        {"act": "server-admin", "arg": not bool(r.get("server_admin")),
         "label": "取消控制台管理员" if r.get("server_admin") else "设为控制台管理员"},
        {"act": "disabled", "arg": not bool(r.get("disabled_at")),
         "label": "恢复账号" if r.get("disabled_at") else "禁用账号"},
        {"act": "llm-mode-menu", "label": "LLM 模式",
         "sub": [{"act": "llm-mode", "arg": k,
                  "label": txt + ("（当前）" if k == mode else "")}
                 for k, txt in (getattr(sc, "LLM_MODE_TEXT", {}) or {}).items()]},
        {"act": "llm-limit-set", "label": "设置额度覆盖…"},
    ]
    if r.get("llm_total_limit_own") is not None:
        menu.append({"act": "llm-limit", "arg": None, "label": "清除额度覆盖"})
    menu.append({"act": "delete", "label": "删除账号…", "danger": True})
    return menu


def account_rows(data, query=""):
    """把回包摆成表格行；缺字段一律 —，不推算。"""
    rows = [{"id": r.get("id"), "username": str(r.get("username") or ""),
             "nickname": str(r.get("nickname") or ""),
             "admin": "是" if r.get("is_admin") else "否",
             "console_admin": "是" if r.get("server_admin") else "否",
             "in_bin": bool(r.get("deleted_at")),
             "status": ("回收站 · %s" % sc._purge_countdown_text(r.get("purge_after"))
                        if r.get("deleted_at")
                        else ("禁用中 %s" % sc._fmt_dt(r.get("disabled_at"))
                              if r.get("disabled_at") else "正常")),
             "status_tip": ("标记删除 %s" % sc._fmt_dt(r.get("deleted_at"))
                            if r.get("deleted_at") else ""),
             "llm_limit": sc._llm_limit_text(r),
             "llm_mode": (getattr(sc, "LLM_MODE_TEXT", {}) or {}).get(
                 str(r.get("llm_mode") or ""), r.get("llm_mode") or "—"),
             "raw": r} for r in (data or {}).get("accounts") or [] if isinstance(r, dict)]
    shown = sc._account_rows_filtered(rows, query)
    return rows, shown


def _server_limit_card(limit_obj):
    if not isinstance(limit_obj, dict):
        return {"title": SERVER_LIMIT_TITLE, "value": "未读取到（GET %s/llm-limit）" % ADMIN_PREFIX,
                "hint": SERVER_LIMIT_HINT}
    src = str(limit_obj.get("source") or "unset")
    label = (getattr(sc, "LLM_LIMIT_SOURCE_LABELS", {}) or {}).get(src, src)
    total = limit_obj.get("total_limit")
    try:
        total = int(total)
    except (TypeError, ValueError):
        total = 0
    return {"title": SERVER_LIMIT_TITLE,
            "value": "%s（%s）" % (sc._fmt_int(total), label),
            "total_limit": total,
            "hint": SERVER_LIMIT_HINT}


def delete_preview_lines(dry, username):
    """dry-run 回包 → 卡片行。判据（guards/体量/例外/提示）一律后端给的，这里只翻译。"""
    dry = dry if isinstance(dry, dict) else {}
    guards = [str(x) for x in (dry.get("guards") or [])]
    totals = dry.get("totals") if isinstance(dry.get("totals"), dict) else {}
    tables = sc._volume_table_rows(dry.get("tables"))
    mode = dry.get("mode")
    lines = [{"kind": "warn",
              "text": DELETE_SUB % (dry.get("grace_days") if dry.get("grace_days") is not None
                                    else "—")},
             {"kind": "info", "text": "模式：%s"
              % (getattr(sc, "ACCOUNT_SCOPE_TEXT", {}) or {}).get(mode, mode or "后端未返回模式")},
             {"kind": "info", "text": "体量：%s 张表 / %s 行随该账号带走；%s 行判定不出归属（不随本次删除）"
              % (sc._fmt_int(totals.get("tables")), sc._fmt_int(totals.get("row_count")),
                 sc._fmt_int(totals.get("undetermined_rows")))}]
    if tables:
        for name, rows_txt, colnames in tables[:sc.VOLUME_TABLE_ROWS_SHOWN]:
            # colnames 已经是「a、b」形态的字符串（空时是 —），不能再 join
            lines.append({"kind": "row", "text": "%s · %s 行 · %s" % (name, rows_txt, colnames),
                          "table": name, "rows": rows_txt, "cols": colnames})
        if len(tables) > sc.VOLUME_TABLE_ROWS_SHOWN:
            lines.append({"kind": "info",
                          "text": "另有 %d 张表未列出（完整清单在删除后的审计快照里）"
                                  % (len(tables) - sc.VOLUME_TABLE_ROWS_SHOWN)})
    else:
        lines.append({"kind": "warn", "text": "体量清单为空：本次不会带走任何数据行"})
    exc = [x for x in (dry.get("exceptions") or []) if isinstance(x, dict)]
    for x in exc[:sc.EXCEPTION_LINES_SHOWN]:
        lines.append({"kind": "warn", "text": "例外 %s%s：%s" % (
            str(x.get("table") or "—"),
            ("." + str(x.get("column"))) if x.get("column") else "",
            str(x.get("reason") or ""))})
    if len(exc) > sc.EXCEPTION_LINES_SHOWN:
        lines.append({"kind": "info", "text": "另有 %d 条例外未列出"
                      % (len(exc) - sc.EXCEPTION_LINES_SHOWN)})
    for w in [x for x in (dry.get("warnings") or []) if isinstance(x, dict)][:sc.EXCEPTION_LINES_SHOWN]:
        lines.append({"kind": "warn", "text": "提示：%s（%s 行）%s" % (
            str(w.get("table") or "—"), sc._fmt_int(w.get("rows")),
            ("：" + str(w.get("reason"))) if w.get("reason") else "")})
    if dry.get("already_deleted"):
        lines.append({"kind": "warn", "text": "该账号已在回收站里（重复标记会被后端拒绝）"})
    for g in guards:
        lines.append({"kind": "error", "text": "护栏：%s" % g})
    return {"username": username,
            "title": "确认删除账号：%s" % username,
            "heading": "删除账号 %s%s" % (username,
                                          "（%s）" % dry.get("nickname") if dry.get("nickname") else ""),
            "lines": lines,
            "can_confirm": not guards,
            "blocked_text": BLOCKED_TEXT if guards else ""}


def purge_report_lines(payload, username=""):
    """清除账本 → 卡片行。全部读后端，不直连库、不自己数行（SC:3613）。"""
    d = payload if isinstance(payload, dict) else {}
    job = d.get("job") if isinstance(d.get("job"), dict) else None
    who = username or ("账号 %s" % d.get("user_id") if d.get("user_id") is not None else "—")
    head = [{"kind": "info",
             "text": "以下全部读自后端清除账本（GET %s/accounts/%s/purge-report）。"
                     % (ADMIN_PREFIX, d.get("user_id"))}]
    if not job:
        head.append({"kind": "warn",
                     "text": "尚无清除记录：该账号还没被物理清除过（账本里没有作业行）"})
        return {"username": who, "lines": head, "has_job": False}
    st = {"done": "已完成", "ok": "已完成", "failed": "出错"}.get(str(job.get("status")),
                                                                 job.get("status") or "—")
    head.append({"kind": "info", "text": "状态：%s（job=%s）　开始 %s　结束 %s"
                 % (st, job.get("id"), sc._fmt_dt(job.get("started_at")),
                    sc._fmt_dt(job.get("finished_at")))})
    head.append({"kind": "info", "text": "自动重试次数：%s" % job.get("attempts", "—")})
    if job.get("error"):
        head.append({"kind": "error", "text": "错误：%s" % job.get("error")})
    rep = d.get("report") if isinstance(d.get("report"), dict) else {}
    cur = d.get("cursor") if isinstance(d.get("cursor"), dict) else {}
    if not rep and cur.get("rows_deleted_so_far") is not None:
        head.append({"kind": "warn", "text": "进度：%s（下一步：%s）"
                     % (" → ".join([str(x) for x in (cur.get("steps_done") or [])]) or "尚未开始",
                        cur.get("step") or "—")})
    head.append({"kind": "info", "text": "删除模式：%s"
                 % (getattr(sc, "ACCOUNT_SCOPE_TEXT", {}) or {}).get(
                     rep.get("mode"), rep.get("mode") or "—")})
    head.append({"kind": "info", "text": "已删除：%s 行"
                 % sc._fmt_int(rep.get("rows_deleted", cur.get("rows_deleted_so_far")))})
    tbls = rep.get("tables") or cur.get("tables_done") or []
    for t in tbls[:sc.VOLUME_TABLE_ROWS_SHOWN]:
        if isinstance(t, dict):
            head.append({"kind": "row",
                     "text": "%s · %s 行" % (t.get("table"), sc._fmt_int(t.get("rows"))),
                     "table": t.get("table"), "rows": sc._fmt_int(t.get("rows"))})
    if len(tbls) > sc.VOLUME_TABLE_ROWS_SHOWN:
        head.append({"kind": "info", "text": "另有 %d 张表未列出（完整账本在 account_purge_jobs.report_json）"
                     % (len(tbls) - sc.VOLUME_TABLE_ROWS_SHOWN)})
    files = rep.get("files") if isinstance(rep.get("files"), dict) else {}
    if files:
        head.append({"kind": "info", "text": "文件隔离：%s 个文件搬进 %s"
                     % (sc._fmt_int(files.get("files_moved")), files.get("trash_dir") or "—")})
        if files.get("partial_dirs"):
            head.append({"kind": "warn",
                         "text": "有目录被占用没搬走（源目录仍在）：%s"
                                % "、".join([str(x) for x in files["partial_dirs"]][:5])})
    if rep.get("vectors") is not None:
        head.append({"kind": "info", "text": "向量：deleted=%s" % sc._fmt_int(rep.get("vectors"))})
    if rep.get("bm25") is not None:
        head.append({"kind": "info", "text": "BM25 缓存：%s" % rep.get("bm25")})
    if rep.get("backup_zip"):
        head.append({"kind": "info", "text": "前置备份包：%s" % rep.get("backup_zip")})
    fk = rep.get("foreign_key_check")
    if isinstance(fk, dict):
        left = fk.get("remaining") or []
        head.append({"kind": "info" if not left else "warn",
                     "text": "外键自检：%s" % ("无残留" if not left else "%d 条残留" % len(left))})
        for x in left[:3]:
            head.append({"kind": "warn", "text": "残留：%s" % x})
    if rep.get("blocked"):
        head.append({"kind": "error", "text": "挡在哪：%s" % rep.get("blocked")})
    if d.get("report_unparsed") or (rep.get("_raw") if isinstance(rep, dict) else None):
        head.append({"kind": "warn", "text": "账本报告未能解析（原文摘要）：%s"
                     % str(rep.get("_raw") or d.get("report_unparsed"))[:160]})
    return {"username": who, "lines": head, "has_job": True}


@_envelope
def accounts_page(include_deleted=False, query=""):
    data = accounts_raw = None
    data = sc._admin_request("GET", "/accounts?include_deleted=true" if include_deleted
                             else "/accounts")
    limit = {}
    try:
        limit = sc._admin_request("GET", "/llm-limit") or {}
    except Exception:  # noqa: BLE001  读不到就整卡降级，不影响列表
        limit = {}
    rows, shown = account_rows(data, query)
    sub = "?include_deleted=true" if include_deleted else ""
    return {"logged_in": bool(sc._console_login_state()), "include_deleted": bool(include_deleted),
            "query": query or "", "rows": shown, "total": len(rows),
            "status": "共 %d 个账号（GET %s/accounts%s）" % (len(rows), ADMIN_PREFIX, sub),
            "server_limit": _server_limit_card(limit if isinstance(limit, dict) else {}),
            "empty_kind": ("none" if not rows else ("nomatch" if not shown else "")),
            "empty_text": ("" if rows and shown else (
                "接口 200 但 accounts 为空（GET %s/accounts%s）：请确认账号数据是否存在，"
                "以及响应结构是否变更（信封字段是否仍为 accounts）" % (ADMIN_PREFIX, sub)
                if not rows else
                "没有匹配「%s」的账号（已取回 %d 个，清空搜索框可看全部）" % (query, len(rows)))),
            "subtitle": ACCOUNT_SUB}


@_envelope
def account_menu(uid):
    """菜单单独取一次：行操作完刷新列表时也要重算（回收站里的三项不一样）。"""
    data = sc._admin_request("GET", "/accounts?include_deleted=true")
    for r in (data or {}).get("accounts") or []:
        if str(r.get("id")) == str(uid):
            return {"uid": uid, "menu": _account_menu(r)}
    return {"uid": uid, "menu": [], "missing": True}


@_envelope
def delete_preview(uid):
    dry = sc._admin_request("POST", "/accounts/%s/delete-dry-run" % uid)
    out = delete_preview_lines(dry, str(dry.get("username") or ""))
    out["uid"] = uid
    out["purge_sub"] = DELETE_SUB
    return out


@_envelope
def confirm_gate(username, typed):
    """默认拒绝：逐字符相等才允许发（与后端 confirm_username 同判据，SC:3463-3474）。"""
    ok = str(typed) == str(username)
    return {"ok": ok, "hint": "" if ok else CONFIRM_MISS % username}


@_envelope
def purge_preview(uid):
    """立即清除前的那段话：不可恢复＋当前回收站状态。"""
    data = sc._admin_request("GET", "/accounts?include_deleted=true")
    row = next((r for r in (data or {}).get("accounts") or []
                if isinstance(r, dict) and str(r.get("id")) == str(uid)), None)
    if not row:
        return {"uid": uid, "missing": True}
    return {"uid": uid, "username": str(row.get("username") or ""),
            "sub": PURGE_SUB,
            "bin_line": "回收站状态：%s（标记删除 %s）"
                        % (sc._purge_countdown_text(row.get("purge_after")),
                           sc._fmt_dt(row.get("deleted_at"))),
            "note": "清除完成后自动打开「清除结果」，逐表行数以后端账本为准。"}


@_envelope
def purge_report_view(uid):
    data = sc._admin_request("GET", "/accounts/%s/purge-report" % uid)
    out = purge_report_lines(data if isinstance(data, dict) else {})
    out["uid"] = uid
    return out



# ═════════════════ W3d：最后四页（判据一律复用旧壳的模块级助手）═════════════════
def registration_view(data):
    """注册策略：只读 mode；未知值原样标出来，不替后端编解释。"""
    mode = str((data or {}).get("mode") or "")
    modes = {k: (label, desc) for k, label, desc in getattr(sc, "REGISTRATION_MODES", ())}
    return {"mode": mode, "mode_text": sc._registration_mode_text(mode),
            "desc": modes.get(mode, ("", ""))[1],
            "options": [{"key": k, "label": v[0]} for k, v in modes.items()],
            "sub": ("开放 / 仅邀请码 / 关闭（保存后自动回读确认；注册拦截与提示文案一律以后端为准）"),
            "status": "当前 %s（GET %s/registration）" % (sc._registration_mode_text(mode), ADMIN_PREFIX),
            "live": "生效中：%s" % sc._registration_mode_text(mode),
            "hint": "403/401 时点右上角「登录」后重试",
            "logged_in": bool(sc._console_login_state())}


# 本地签发过的配对码只存内存：它才是会走的那个倒计时
_ISSUED = {}


def identity_code_display(now=None):
    """倒计时口径照旧壳 SC:4191-4200：本地签发的码会逐秒走；
    服务器 pending 里的那个是静态快照，**不许递减**，也不重新请求。"""
    now = now if now is not None else _t.time()
    code = str(_ISSUED.get("code") or "")
    exp = float(_ISSUED.get("expires_at") or 0)
    if code and exp > now:
        return {"text": sc._identity_code_text(code), "left": int(exp - now),
                "tick": True, "source": "local"}
    if code and exp <= now:
        return {"text": sc._identity_code_text(code), "left": 0, "tick": False, "source": "expired"}
    return {"text": "尚未签发", "left": 0, "tick": False, "source": "none"}


def identity_view(data, now=None):
    """服务器身份页：档位/指纹/配对码/轮换/部署面现状，文案逐字照旧壳。"""
    d = data if isinstance(data, dict) else {}
    mode = str(d.get("mode") or "")
    texts = getattr(sc, "IDENTITY_MODE_TEXTS", {}) or {}
    known = mode in texts
    pend = d.get("pending") if isinstance(d.get("pending"), dict) else {}
    stats = d.get("pair_stats") if isinstance(d.get("pair_stats"), dict) else {}
    hints = d.get("hints") if isinstance(d.get("hints"), dict) else {}
    signed = d.get("signed_paths") if isinstance(d.get("signed_paths"), list) else []
    code = identity_code_display(now)
    if code["source"] == "local":
        code_line = "剩余 %d 秒后失效" % code["left"]
    elif code["source"] == "expired":
        code_line = "已失效，请重新生成"
    elif pend.get("has_pending") and int(pend.get("expires_in") or 0) > 0:
        code_line = "（本窗口未持有该码）剩 %s 秒" % pend.get("expires_in")
    else:
        code_line = "码失效后重新生成即可；过期未用的码不会改动服务器身份"
    return {
        "mode": mode, "mode_known": known,
        "mode_text": texts[mode][0] if known else ("未知档位（%s）" % mode if mode else "—"),
        "mode_desc": texts[mode][1] if known else "",
        "mode_note": "" if known else "档位以后端返回值为准，控制台不做推算",
        "fp": d.get("fp") or "", "fp_display": sc._identity_fp_text(d.get("fp")),
        "server_name": d.get("server_name") or "—",
        "key_created_at": sc._fmt_dt(d.get("key_created_at")),
        "pending": ("有效（指纹 %s，剩 %s 秒）" % (pend.get("fp"), pend.get("expires_in"))
                    if pend.get("has_pending") else "无"),
        "pair_stats": "成功 %s ／ 失败 %s" % (stats.get("pair_success", 0), stats.get("pair_fail", 0)),
        "code": code, "code_line": code_line,
        "sub": ("配对码只在本机控制台显示、绝不参与网络往返；指纹供客户端核对服务器（动作后自动回读）"),
        "status": "档位 %s ｜ 指纹 %s（GET %s/identity）"
                  % (mode or "?", sc._identity_fp_text(d.get("fp")), ADMIN_PREFIX),
        "rotate_text": ("旧密钥立即作废，所有已配对设备需要重新配对；轮换同时签发一个新的配对码。"),
        "rotate_confirm": ("轮换后：\n· 旧身份密钥立即作废，所有已配对设备需重新配对\n"
                           "· 同时签发一个新的配对码（新指纹）\n\n确定继续吗？"),
        "deploy": [
            {"k": "监听地址", "v": d.get("bind_host") or "—",
             "tip": "收窄设环境变量 SERVER_HOST 后重启"},
            {"k": "CORS", "v": "通配（*）" if hints.get("cors_wildcard") else "已收敛",
             "tip": "收紧会连带影响 App 跨源请求，按需再议"},
            {"k": "/uploads", "v": "需要登录" if hints.get("uploads_require_auth")
                                else "免鉴权（现状默认）",
             "tip": "打开开关 uploads_require_auth 后生效"},
            {"k": "签名覆盖", "v": "%d 个端点（窄集）" % len(signed), "tip": "仅健康/登录/配对等 JSON 端点"}],
        "logged_in": bool(sc._console_login_state())}


def _da_names(seq):
    """名单去重与清洗照旧壳 _da_rows：去空白、非串转串、按原顺序去重。"""
    out, seen = [], set()
    for x in seq or []:
        v = str(x if isinstance(x, str) else (x or "")).strip()
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _da_switch_line(key, meta, rows_present, sw_val):
    """三条开关的副行三种写法：后端没给 rows_present 时不许假装"未设置"。"""
    label, default = meta[0], meta[1]
    present = rows_present.get(key) if isinstance(rows_present, dict) else None
    eff = "开" if sw_val else "关"
    if present is None:
        return "后端生效值：%s（是否显式设置：后端未返回）" % eff, "muted"
    if present:
        return "已显式设置（后端生效值：%s）" % eff, "sec"
    return "未显式设置（默认：%s）" % default, "muted"


def device_actions_view(data):
    d = data if isinstance(data, dict) else {}
    sw = d.get("switches") if isinstance(d.get("switches"), dict) else {}
    rows_present = sw.get("rows_present") if isinstance(sw.get("rows_present"), dict) else None
    meta = {k: (label, default) for k, label, default in
            getattr(sc, "DEVICE_ACTION_SWITCHES", ())}
    cards = []
    for key, (label, default) in meta.items():
        cap, tone = _da_switch_line(key, (label, default),
                                    rows_present if isinstance(rows_present, dict) else {},
                                    bool(sw.get(key)))
        cards.append({"key": key, "label": label, "on": bool(sw.get(key)),
                      "caption": cap, "tone": tone})
    limits = d.get("limits") if isinstance(d.get("limits"), dict) else {}
    sections = {k: (title, sample) for k, title, sample in
                getattr(sc, "DEVICE_ACTION_SECTIONS", ())}

    def section(kind, cap_key):
        names = _da_names(d.get(kind) or [])
        cap = limits.get(cap_key)
        title, sample = sections.get(kind, (kind, ""))
        return {"kind": kind, "title": title, "sample": sample,
                "names": names, "empty": "名单为空（后端返回 0 条）",
                "cap_line": "租户＝%s ｜ %s" % (d.get("tenant_id") or "—",
                                                "上限未知" if cap is None else "最多 %s 个" % cap)}
    return {"cards": cards, "sections": [section("targets", "targets_max"),
                                         section("plugins", "plugins_max")],
            "sub": ("三条开关 + 目标白名单 + 插件灰度名单（全部经 %s/device-actions 读写，"
                    "控制台不碰数据库）" % ADMIN_PREFIX),
            "status": "已读取（GET %s/device-actions）" % ADMIN_PREFIX,
            "logged_in": bool(sc._console_login_state())}


def overview_view(data):
    """概览：契约五字段在前，后端多出来的键按 A-Z 附在后面、直接用键名当标签。"""
    d = data if isinstance(data, dict) else {}
    fields = getattr(sc, "OVERVIEW_FIELDS", ()) or ()
    rows = [[label, d.get(key)] for key, label in fields]
    known = {key for key, _label in fields}
    rows += [[key, d.get(key)] for key in sorted(d) if key not in known]
    empty = bool(rows) and all(v[1] is None for v in rows)
    return {"rows": [{"label": lbl, "value": ("—" if val is None else str(val)),
                      "missing": val is None} for lbl, val in rows],
            "count": len(rows),
            "status": "已读取 %d 项（GET %s/overview）" % (len(rows), ADMIN_PREFIX),
            "empty_text": "后端未返回任何概览字段（GET %s/overview）" % ADMIN_PREFIX,
            "empty_kind": empty,
            "sub": "账号数/禁用数/控制台管理员/开启开关/后端版本（只读，控制台不做任何推算）",
            "logged_in": bool(sc._console_login_state())}


@_envelope
def registration_page():
    return registration_view(sc._admin_request("GET", "/registration"))


@_envelope
def identity_page():
    return identity_view(sc._admin_request("GET", "/identity"))


@_envelope
def device_actions_page():
    return device_actions_view(sc._admin_request("GET", "/device-actions"))


@_envelope
def overview_page():
    return overview_view(sc._admin_request("GET", "/overview"))




# ───────────────────────── 给 JS 的白名单 ─────────────────────────
LOCAL = ("session", "login", "logout", "server_state", "log_tail", "dashboard",
         "models", "save_modality", "accounts", "set_account", "delete_dry_run",
         "delete_account", "restore_account", "purge_account", "purge_report",
         "registration", "set_registration", "identity", "pairing_code", "rotate_identity",
         "flags", "set_flag", "audit", "overview", "device_actions", "set_device_switch",
         "add_device_target", "remove_device_target", "add_device_plugin",
         "remove_device_plugin", "server_control",
         "server_page", "probe_addresses", "set_target", "reset_target", "set_server_limit",
         "server_settings", "save_server_settings", "open_channel_login",
         "log_page", "write_log_export", "open_log_file", "ping",
         "models_page", "flags_page", "audit_page",
         "accounts_page", "account_menu", "delete_preview", "confirm_gate",
         "purge_preview", "purge_report_view",
         "registration_page", "identity_page", "device_actions_page", "overview_page",
         "copy_pairing_code", "set_theme", "get_theme")
EXPOSED = tuple(LOCAL)


def call(name, *args):
    """JS 侧唯一入口：只认白名单里的名字，且一律回信封。"""
    if name not in EXPOSED:
        return {"ok": False, "code": -1, "error": "接口不在白名单里：%s" % name, "path": ""}
    return globals()[name](*args)
