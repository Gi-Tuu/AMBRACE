"""拥爱（AMBRACE）服务器控制台 · 共享数据面（W6-A 唯一真源）。

旧 tkinter 壳与 pywebview 网页壳共用这里的只读读数、管理面请求、地址探测、
展示整形与文案常量，判据不许出现两套。本模块**不得 import tkinter / PIL / console_ui**，
这样网页壳启动不再连带加载 270KB 的 Tk 巨石。

实现从 `server_controller.py` 逐段原样抽出（不改语义、不改文案），由
`scripts` 下的生成脚本按 AST 行段抽取；改动请同步两处或在 core 里改后删除旧壳重复段。
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
import ipaddress
import urllib.request
import urllib.error
from urllib.parse import urlparse
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    import psutil
except ImportError:                      # 旧壳也是可选依赖，探测函数内有降级
    psutil = None

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_DIR = os.path.join(PROJECT_ROOT, "backend")
if os.path.join(PROJECT_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

from log_rotate import rotate_stdio_log  # noqa: E402
import platform_util  # noqa: E402

def _safe_traceback():
    """pythonw 下 sys.stderr 可能为 None，traceback.print_exc 会二次崩溃；安全降级。"""
    try:
        import traceback
        traceback.print_exc()
    except Exception:
        pass

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_DIR = os.path.join(PROJECT_ROOT, "backend")

PYTHONW, PYTHON = platform_util.venv_python_paths(SERVER_DIR)
PAUSE_FLAG = os.path.join(SERVER_DIR, "data", "paused.flag")
APP_LOG = os.path.join(SERVER_DIR, "data", "logs", "app.log")
STDERR_LOG = os.path.join(SERVER_DIR, "data", "logs", "server_stderr.log")
MANAGER_PY = os.path.join(PROJECT_ROOT, "scripts", "server_manager.py")
CONFIG = os.path.join(SERVER_DIR, "data", "server_config.json")

def _guard_http_url(url: str) -> None:
    """URL 守卫（2026-11-05 安全清理；同日复核补强）。

    控制台按设计只访问配置指向的后端（TARGET_HOST/TARGET_PORT，常为 127.0.0.1 或局域网），
    因此**放行私网/回环**；但仍拒绝链路本地（169.254.0.0/16、fe80::/10，云元数据端点在此段）、
    未指定、组播、保留与 198.18.0.0/15（假地址段）。实现内联（控制台是独立进程，不 import backend）。
    """
    from urllib.parse import urlparse

    pr = urlparse(url)
    if pr.scheme.lower() not in ("http", "https") or not pr.hostname:
        raise ValueError("仅允许显式 host 的 http(s) URL: %r" % (url,))
    import ipaddress
    try:
        infos = socket.getaddrinfo(
            pr.hostname, pr.port or (443 if pr.scheme.lower() == "https" else 80),
            proto=socket.IPPROTO_TCP)
    except OSError:
        return  # DNS 失败：放行（请求本身会失败），不因瞬时抖动打断控制台
    for info in infos:
        raw_ip = str(info[4][0]).split("%", 1)[0]  # IPv6 可能带 scope id
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            raise ValueError("无法判定目标地址，保守拒绝: %r" % (url,))
        if (ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified
                or ip in ipaddress.ip_network("198.18.0.0/15")):
            raise ValueError("拒绝链路本地/云元数据目标: %r" % (url,))

def _tool_path(p):
    """读写路径守卫（2026-11-05 安全清理；同日复核统一实现）：拒绝含 ``..`` 穿越分量的路径，返回绝对 Path。

    用 ``os.path.abspath`` 而不是 ``Path.resolve()``：本仓测试有把 ``os.name`` 改成 posix 模拟 Linux 分支的写法，
    此时 pathlib 会切到 PosixPath、把 Windows 路径当单段相对路径解析（同 server_manager 的修法）。
    """
    from pathlib import Path

    expanded = os.path.expanduser(str(p))
    if ".." in expanded.replace("\\", "/").split("/"):
        raise ValueError("路径不得包含 .. 分量: %r" % (p,))
    return Path(os.path.abspath(expanded))

LOG_TAIL = 40

NO_WINDOW = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))

def _load_config() -> dict:
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

_DEFAULT_TARGET_HOST = "127.0.0.1"
_DEFAULT_TARGET_PORT = 8000

def _normalize_endpoint(host, port=None):
    """归一化主机/端口：允许粘贴整段 URL，拆出 host 与 port。"""
    h = str(host or "").strip().replace("https://", "").replace("http://", "")
    h = h.rstrip("/").split("/")[0]
    if h.count(":") == 1 and h.rsplit(":", 1)[-1].isdigit():
        h, pp = h.rsplit(":", 1)
        if port in (None, ""):
            port = pp
    try:
        p = int(port)
    except Exception:
        p = _DEFAULT_TARGET_PORT
    if not (1 <= p <= 65535):
        p = _DEFAULT_TARGET_PORT
    return (h or _DEFAULT_TARGET_HOST), p

def _load_target():
    cfg = _load_config()
    return _normalize_endpoint(cfg.get("controller_target_host") or _DEFAULT_TARGET_HOST,
                               cfg.get("controller_target_port", _DEFAULT_TARGET_PORT))

TARGET_HOST, TARGET_PORT = _load_target()

def set_server_target(host: str, port: int):
    """运行时切换监控目标（保存配置后调用，立即生效）。"""
    global TARGET_HOST, TARGET_PORT
    TARGET_HOST = str(host or _DEFAULT_TARGET_HOST).strip()
    TARGET_PORT = int(port)

def _local_ip_set() -> set:
    ips = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
    try:
        import psutil
        for addrs in psutil.net_if_addrs().values():
            for ad in addrs:
                ips.add((ad.address or "").split("%")[0].lower())
    except Exception:
        pass
    return ips

def _is_remote() -> bool:
    """监控目标是否为另一台机器：远程只做状态监控，不做进程管理/本地库读取。"""
    return TARGET_HOST.strip().lower() not in _local_ip_set()

def _target_base() -> str:
    return "http://%s:%d" % (TARGET_HOST, TARGET_PORT)

def _check_alive() -> bool:
    try:
        with socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=1.5):
            return True
    except OSError:
        return False

def _port_pid(port: int) -> int:
    try:
        import psutil
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                return conn.pid or 0
        return 0
    except ImportError:
        pass
    except Exception:
        return 0
    if os.name == "nt":
        try:
            ps = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "$c = Get-NetTCPConnection -State Listen -LocalPort %d -ErrorAction SilentlyContinue; " % port,
                 "if ($c) { $c.OwningProcess } else { 0 }"],
                capture_output=True, text=True, timeout=5, creationflags=NO_WINDOW)
            out = (ps.stdout or "").strip().splitlines()
            return int(out[0]) if out and out[0].strip().isdigit() else 0
        except Exception:
            return 0
    try:
        ps = subprocess.run(["lsof", "-t", "-i:%d" % port], capture_output=True, text=True, timeout=5)
        out = (ps.stdout or "").strip().splitlines()
        return int(out[0]) if out and out[0].strip().isdigit() else 0
    except Exception:
        return 0

_TUN_NAME_HINTS = ("tailscale", "wintun", "clash", "singbox", "tun", "tap",
                   "hyper-v", "vethernet", "vmware", "virtualbox", "docker", "loopback")

def _is_lan_private(ip: str) -> bool:
    """是否为标准家用/企业局域网私网地址（10/8、172.16-31、192.168/16）。"""
    try:
        parts = ip.split(".")
        a, b = int(parts[0]), int(parts[1])
    except Exception:
        return False
    if a == 10:
        return True
    if a == 192 and b == 168:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    return False

def _looks_tun(ip: str) -> bool:
    """198.18/198.19 基准保留段（Clash/sing-box fake-ip）、100.64/10 CGNAT（Tailscale）。"""
    try:
        a, b = int(ip.split(".")[0]), int(ip.split(".")[1])
    except Exception:
        return False
    if a in (198,) and b in (18, 19):
        return True
    if a == 100 and 64 <= b <= 127:
        return True
    return False

def _psutil_lan_ip() -> str:
    try:
        import psutil
    except Exception:
        return ""
    found = []
    try:
        for name, addrs in psutil.net_if_addrs().items():
            low = name.lower()
            if any(k in low for k in _TUN_NAME_HINTS):
                continue
            for ad in addrs:
                fam = getattr(ad, "family", None)
                if fam is not None and getattr(fam, "name", "") == "AF_INET" \
                        and _is_lan_private(ad.address):
                    found.append(ad.address)
    except Exception:
        return ""
    return found[0] if found else ""

def _ipconfig_lan_ip() -> str:
    if os.name != "nt":
        return ""
    try:
        import re
        proc = subprocess.run(["ipconfig"], capture_output=True, timeout=5,
                              creationflags=NO_WINDOW)
        raw = proc.stdout or b""
        out = ""
        for enc in ("gbk", "utf-8", "mbcs"):  # 中文 Windows ipconfig 为 GBK
            try:
                out = raw.decode(enc, errors="ignore")
                break
            except Exception:
                continue
        ips = re.findall(r"IPv4[^:]*?:\s*([0-9.]+)", out)
        for ip in ips:
            if _is_lan_private(ip):
                return ip
    except Exception:
        return ""
    return ""

def _get_lan_ip() -> str:
    """真实局域网 IPv4。依次：psutil 网卡 → UDP 默认路由 → 主机名解析 → ipconfig；
    全程优先标准私网地址，避开 Clash/Tailscale 等 TUN 虚拟网卡；都没有再退回 UDP 结果。"""
    ip = _psutil_lan_ip()
    if ip:
        return ip
    udp = ""
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sk.connect(("8.8.8.8", 80))
            udp = sk.getsockname()[0]
        finally:
            sk.close()
    except Exception:
        udp = ""
    if _is_lan_private(udp):
        return udp
    try:
        for t in socket.getaddrinfo(socket.gethostname(), None):
            if t[0] == socket.AF_INET and _is_lan_private(t[4][0]):
                return t[4][0]
    except Exception:
        pass
    ip2 = _ipconfig_lan_ip()
    if ip2:
        return ip2
    return udp

def _get_tailscale_ip() -> str:
    """Tailscale IPv4（跨网络组网用）。未安装/未登录返回空串。"""
    try:
        out = subprocess.run(["tailscale", "ip", "-4"],
                             capture_output=True, text=True, timeout=3,
                             creationflags=NO_WINDOW)
        lines = (out.stdout or "").strip().splitlines()
        return lines[0].strip() if lines else ""
    except Exception:
        return ""

def _get_pid() -> int:
    if _is_remote():
        return 0  # 远程主机的进程 PID 无法在本机取得
    return _port_pid(TARGET_PORT)

def _run_manager(cmd: str) -> None:
    try:
        subprocess.Popen([PYTHON, MANAGER_PY, cmd], **platform_util.popen_kwargs())
    except Exception as e:
        raise RuntimeError("执行失败: {0}".format(e))

def _start_uvicorn():
    try:
        os.makedirs(os.path.dirname(STDERR_LOG), exist_ok=True)
        # C1：打开重定向句柄之前轮转（控制台侧无 log()，直接打印即可）
        _rot = rotate_stdio_log(STDERR_LOG, max_mb=10, keep=2)
        if _rot:
            print(_rot)
        with open(STDERR_LOG, "a", encoding="utf-8") as f:
            subprocess.Popen(
                [PYTHONW, "-m", "uvicorn", "app.main:app",
                 "--host", platform_util.resolve_bind_host(SERVER_DIR), "--port", str(TARGET_PORT)],
                cwd=SERVER_DIR, stdout=f, stderr=subprocess.STDOUT, **platform_util.popen_kwargs())
    except Exception as e:
        raise RuntimeError(f"启动失败: {e}")

def _log_read_path(path: str) -> str:
    """C1：启动前轮转后当前日志为空（或尚未重建）时优先读 .1。
    否则控制台日志区会显示一片空白，容易被误判成「服务启动失败」。"""
    alt = path + ".1"
    try:
        if os.path.exists(alt) and (not os.path.isfile(path) or os.path.getsize(path) == 0):
            return alt
    except OSError:
        pass
    return path

def _tail_log_parts():
    """按来源分别取末尾 LOG_TAIL 行，返回 {"app": ..., "stderr": ...}。

    以前两份日志被 `"\\n".join` 直接拼成一段文本，来源信息就此丢失、也没法按级别筛；
    这里保留各自的原文，由 console_api 解析成结构化行。_tail_log 仍保留给导出/旧调用方。
    """
    def tail(path, size=12000):
        if not os.path.exists(path):
            return ""
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                n = f.tell()
                f.seek(max(0, n - size))
                data = f.read().decode("utf-8", errors="replace")
            return "\n".join(data.splitlines()[-LOG_TAIL:])
        except Exception:
            return ""
    # C1：server_stderr.log 刚被轮转过（当前为空）时回退读 .1，避免日志区空白
    return {"app": tail(APP_LOG), "stderr": tail(_log_read_path(STDERR_LOG))}


def _tail_log():
    parts = _tail_log_parts()
    return "\n".join(p for p in (parts["app"], parts["stderr"]) if p.strip())

def _fetch_health() -> str:
    if not _check_alive():
        return "—"
    try:
        import urllib.request
        _guard_http_url(_target_base() + "/api/v1/system/health")
        with urllib.request.urlopen(_target_base() + "/api/v1/system/health", timeout=2.5) as r:
            return "正常" if r.status == 200 else "异常"
    except Exception:
        return "异常"

def _fmt_ago(sec) -> str:
    """秒数 →「X 单位前」人读格式（不足 1 分钟用秒，其余用分/时）。"""
    try:
        s = max(0, int(sec))
    except Exception:
        return "—"
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm" % (s // 60)
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60)

def _fetch_liveness() -> dict:
    """读取运行期活性（/api/v1/system/liveness），返回 {ok, stalled, summary, level}。

    - ok=False：端点不可用/网络失败 → 下游回落现状（summary 空，不改健康卡语义）。
    - level：0=正常(绿)、1=停滞预警(黄，如 scheduler 心跳超阈值)、2=停滞(红，端点 reported stalled)。
    - summary 例：「调度 12s 前 · 微信桥 3s 前」；微信桥取启用绑定最近 in/out 的最小间隔。
    """
    if not _check_alive():
        return {"ok": False, "stalled": False, "summary": "", "level": 0}
    try:
        import json
        import urllib.request
        _guard_http_url(_target_base() + "/api/v1/system/liveness")
        with urllib.request.urlopen(_target_base() + "/api/v1/system/liveness", timeout=2.5) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return {"ok": False, "stalled": False, "summary": "", "level": 0}
    stalled = bool(data.get("stalled"))
    loops = data.get("loops") or {}
    sched = loops.get("scheduler") or {}
    sched_secs = sched.get("seconds_since_heartbeat")
    sched_stalled = bool(sched.get("stalled"))
    ch = data.get("channels") or {}
    wx = ch.get("wechat_ilink") or {}
    wx_secs = None
    for b in (wx.get("bindings") or []):
        if not b.get("enabled"):
            continue
        for v in (b.get("seconds_since_inbound"), b.get("seconds_since_outbound")):
            if v is not None and (wx_secs is None or v < wx_secs):
                wx_secs = v
    parts = []
    if sched_secs is not None:
        parts.append("调度 %s 前" % _fmt_ago(sched_secs))
    if wx_secs is not None:
        parts.append("微信桥 %s 前" % _fmt_ago(wx_secs))
    level = 2 if stalled else (1 if sched_stalled else 0)
    return {"ok": True, "stalled": stalled, "summary": " · ".join(parts), "level": level}

def _fmt_compact(v) -> str:
    try:
        v = int(v)
    except Exception:
        return "0"
    if v >= 10 ** 6:
        x = v / 10 ** 6
        return f"{x:.1f}M" if x != int(x) else f"{int(x)}M"
    if v >= 10 ** 3:
        x = v / 10 ** 3
        return f"{x:.1f}k" if x != int(x) else f"{int(x)}k"
    return str(v)

def _fmt_short_date(date_str: str) -> str:
    try:
        y, m, d = str(date_str).split("-")
        return f"{int(m)}/{int(d)}"
    except Exception:
        return str(date_str)

def _bj_day_of(value, tz) -> str:
    """把库里的时间串落到**北京日界**（库里是 naive UTC，也可能带时区）。失败返回空串。

    单独一个函数是因为 KPI 卡的记忆趋势与 token 趋势必须用同一把尺——两处的日界换算
    各写一遍，早晚有一边漏掉 8 小时偏移（这条口径在 memory 里翻过好几次）。
    """
    if not value:
        return ""
    try:
        s = str(value).strip().replace("Z", "+00:00")
        if "T" not in s and " " in s:
            s = s.replace(" ", "T", 1)
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz).date().isoformat()
    except Exception:
        return ""

def _aggregate_token_trend(rows, days=7, tz_name="Asia/Shanghai", today=None) -> list:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone(timedelta(hours=8))
    ref = today or datetime.now(tz)
    today_date = ref.astimezone(tz).date() if getattr(ref, "tzinfo", None) else ref.date()
    start = today_date - timedelta(days=days - 1)
    buckets = {}
    for i in range(days):
        buckets[(start + timedelta(days=i)).isoformat()] = 0
    for created_at, tokens in rows:
        day = _bj_day_of(created_at, tz)
        if day in buckets:
            buckets[day] += int(tokens or 0)
    return [{"date": d, "tokens": buckets[d]} for d in sorted(buckets)]

def _read_token_trend(days=7, db=None, today=None) -> list:
    if db is None:
        db = os.path.join(SERVER_DIR, "data", "sqlite", "ai_companion.db")
    if not os.path.isfile(db):
        return []
    try:
        con = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=2)
    except Exception:
        return []
    try:
        rows = con.execute(
            "SELECT created_at, COALESCE(total_tokens, 0) FROM llm_usage WHERE created_at IS NOT NULL"
        ).fetchall()
    except Exception:
        return []
    finally:
        try:
            con.close()
        except Exception:
            pass
    return _aggregate_token_trend(rows, days=days, today=today)

HEATMAP_WEEKS = 26
KPI_SPARK_DAYS = 14        # KPI 卡右半那条迷你趋势线的窗口

TASK_LABELS = {
    "message": "主对话", "chat": "智能体思考", "memory": "记忆处理",
    "status": "状态评估", "card": "织库卡片", "review": "主动复习",
    "diary": "日记生成", "game": "游戏对局", "emotion": "情绪关怀",
    "reflection": "每日反思", "life_tick": "AI 生活", "life_loop": "AI 生活",
    "life_regression": "AI 生活", "life_share": "AI 生活",
    "timeline": "时间线", "plugin_ai": "其他", "eval_100": "其他",
    "": "历史未分类",
}

def _read_token_heatmap(weeks=HEATMAP_WEEKS, db=None, today=None):
    """近 N 周按天用量：周一列对齐、定长 weeks*7，末尾为本周未来占位日（future=True）。"""
    if _is_remote():
        return []  # 远程模式不读本地库，热力图显示「暂无数据」
    if db is None:
        db = os.path.join(SERVER_DIR, "data", "sqlite", "ai_companion.db")
    try:
        tz = ZoneInfo("Asia/Shanghai")
    except Exception:
        tz = timezone(timedelta(hours=8))
    ref = today or datetime.now(tz)
    today_date = ref.astimezone(tz).date() if getattr(ref, "tzinfo", None) else ref.date()
    this_mon = today_date - timedelta(days=today_date.weekday())
    start = this_mon - timedelta(weeks=weeks - 1)
    n = weeks * 7
    day_list = [start + timedelta(days=i) for i in range(n)]
    buckets = {d.isoformat(): 0 for d in day_list}
    tasks_by_day = {}
    if os.path.isfile(db):
        try:
            con = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=2)
            rows = con.execute(
                "SELECT created_at, COALESCE(task,''), COALESCE(total_tokens,0) FROM llm_usage "
                "WHERE created_at IS NOT NULL").fetchall()
            con.close()
        except Exception:
            rows = []
        for created_at, task, tokens in rows:
            try:
                s = str(created_at).strip().replace("Z", "+00:00")
                if "T" not in s and " " in s:
                    s = s.replace(" ", "T", 1)
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                k = dt.astimezone(tz).date().isoformat()
                if k in buckets:
                    buckets[k] += int(tokens or 0)
                    # 每日任务构成（展示名聚合），供热力图悬浮窗使用
                    label = TASK_LABELS.get((task or "").strip(), "其他")
                    day_t = tasks_by_day.setdefault(k, {})
                    day_t[label] = day_t.get(label, 0) + int(tokens or 0)
            except Exception:
                continue
    return [{"date": d.isoformat(), "tokens": buckets[d.isoformat()],
             "tasks": tasks_by_day.get(d.isoformat(), {}),
             "future": d > today_date} for d in day_list]

def _spark_from_heat(heat, days=KPI_SPARK_DAYS):
    """从已经算好的热力图数据里切最近 N 个**已发生**的日用量（未来占位日不算数）。

    刻意复用 `stats["heat"]`：KPI 卡画趋势不再多打一次库，刷新周期里那条线零成本。
    """
    done = [h for h in (heat or []) if not h.get("future")]
    return [int(h.get("tokens") or 0) for h in done[-days:]]

def _spark_points(values, w, h, pad=3):
    """一串数值 → 折线坐标（Canvas 物理像素，调用方已按 DPI 换算）。

    少于 2 点不画；全程为 0 不画（画一条贴底的线＝"看起来有内容"的假读数）；
    非零但全平 ⇒ 画在正中高度，读作「这段时间一直是这个量」。
    """
    vals = [float(v) for v in (values or []) if v is not None]
    if len(vals) < 2:
        return []
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        if hi <= 0:
            return []
        return [(pad + i * (w - 2 * pad) / (len(vals) - 1), h / 2.0)
                for i in range(len(vals))]
    span = hi - lo
    step = (w - 2 * pad) / (len(vals) - 1)
    return [(pad + i * step, h - pad - (v - lo) / span * (h - 2 * pad))
            for i, v in enumerate(vals)]

def _read_mem_daily_new(days=KPI_SPARK_DAYS, db=None, today=None):
    """近 N 天**每日新增**记忆条数（北京日界，与 token 趋势同一把尺）。远程模式返回 []。

    只取窗口内的行：库是 naive UTC，所以起点往前多推 8 小时，
    否则北京 00:00–08:00 那段会被整块漏掉（当日新增看着像少了一半）。
    """
    if _is_remote():
        return []
    if db is None:
        db = os.path.join(SERVER_DIR, "data", "sqlite", "ai_companion.db")
    if not os.path.isfile(db):
        return []
    try:
        tz = ZoneInfo("Asia/Shanghai")
    except Exception:
        tz = timezone(timedelta(hours=8))
    ref = (today or datetime.now(tz)).astimezone(tz)
    start = ref.date() - timedelta(days=days - 1)
    win_from = (datetime.combine(start, datetime.min.time()) - timedelta(hours=8))
    try:
        con = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=2)
    except Exception:
        return []
    try:
        rows = con.execute(
            "SELECT created_at FROM memories "
            "WHERE created_at IS NOT NULL AND created_at >= ?",
            (win_from.strftime("%Y-%m-%d %H:%M:%S"),)).fetchall()
    except Exception:
        return []
    finally:
        try:
            con.close()
        except Exception:
            pass
    buckets = {(start + timedelta(days=i)).isoformat(): 0 for i in range(days)}
    for (created_at,) in rows:
        day = _bj_day_of(created_at, tz)
        if day in buckets:
            buckets[day] += 1
    return [buckets[d] for d in sorted(buckets)]

def _read_db_stats() -> dict:
    stats = {"characters": None, "memories": None, "tokens": None}
    if _is_remote():
        return stats  # 远程主机的本地 SQLite 不可达，保持「—」
    db = os.path.join(SERVER_DIR, "data", "sqlite", "ai_companion.db")
    if not os.path.isfile(db):
        return stats
    try:
        con = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=2)
    except Exception:
        return stats
    try:
        queries = {
            "characters": "SELECT COUNT(*) FROM ai_characters",
            "memories": "SELECT COUNT(*) FROM memories",
            "tokens": "SELECT COALESCE(SUM(total_tokens),0) FROM llm_usage",
        }
        for key, sql in queries.items():
            try:
                stats[key] = con.execute(sql).fetchone()[0]
            except Exception:
                pass
    finally:
        try:
            con.close()
        except Exception:
            pass
    return stats

MEM_KIND_CN = {"user_info": "印象", "preference": "偏好", "event": "近期事件",
               "insight": "洞察", "working_state": "工作状态",
               "ai_reflection": "自我反思", "fact": "事实"}


def _read_memory_stats() -> dict:
    """记忆分布：按记忆本分类的总条数，以及每个角色的条数与构成。

    仪表盘的圆环靠这份数据画占比；角色下拉靠它列名单。远程模式读不到本地库，返回空结构
    （前端按「—」显示，不编数字）。只读打开，与统计口径同一把尺（memories 全表）。
    """
    empty = {"kinds": [], "roles": []}
    if _is_remote():
        return empty
    db = os.path.join(SERVER_DIR, "data", "sqlite", "ai_companion.db")
    if not os.path.isfile(db):
        return empty
    try:
        con = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=2)
    except Exception:
        return empty
    kinds, roles, names = {}, {}, {}
    try:
        for cid, mtype, n in con.execute(
                "SELECT character_id, COALESCE(memory_type,''), COUNT(*) FROM memories "
                "GROUP BY character_id, memory_type"):
            mtype = str(mtype or "")
            kinds[mtype] = kinds.get(mtype, 0) + int(n)
            r = roles.setdefault(cid, {"count": 0, "kinds": {}})
            r["count"] += int(n)
            r["kinds"][mtype] = r["kinds"].get(mtype, 0) + int(n)
        for cid, name in con.execute("SELECT id, COALESCE(name,'') FROM ai_characters"):
            names[cid] = str(name or "")
    except Exception:
        return empty
    finally:
        try:
            con.close()
        except Exception:
            pass

    def _pack(d):
        rows = [{"key": k or "", "label": MEM_KIND_CN.get(k, k or "未分类"), "count": v}
                for k, v in d.items()]
        rows.sort(key=lambda x: x["count"], reverse=True)
        return rows

    out_roles = [{"id": cid, "name": names.get(cid) or ("角色 %s" % cid),
                  "count": r["count"], "kinds": _pack(r["kinds"])}
                 for cid, r in roles.items() if r["count"] > 0]
    out_roles.sort(key=lambda x: x["count"], reverse=True)
    return {"kinds": _pack(kinds), "roles": out_roles[:12]}


def _read_vlm_status() -> dict | None:
    """云端识图配置（服务器级 vlm_configs 行，user_id=0）。远程或读不到返回 None。"""
    if _is_remote():
        return None
    db = os.path.join(SERVER_DIR, "data", "sqlite", "ai_companion.db")
    if not os.path.isfile(db):
        return None
    try:
        con = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=2)
    except Exception:
        return None
    try:
        row = con.execute(
            "SELECT model, enabled, api_key FROM vlm_configs WHERE user_id=0"
        ).fetchone()
    except Exception:
        row = None
    finally:
        try:
            con.close()
        except Exception:
            pass
    if not row:
        return {"configured": False, "model": ""}
    model, enabled, key = row
    return {"configured": bool(enabled) and bool(key), "model": model or ""}

def _fmt_int(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{int(v):,}"
    except Exception:
        return "—"

CONSOLE_TOKEN_FILE = os.path.join(SERVER_DIR, "data", "console_token.json")
ADMIN_API_PREFIX = "/api/v1/admin/server"
AUTH_LOGIN_PATH = "/api/v1/auth/login"
ADMIN_TIMEOUT = 6.0

MODALITY_ORDER = ("llm", "multimodal", "image", "vlm", "speech")

LLM_MODE_TEXT = {"own": "仅用自己的配置", "default_allowed": "可用服务器默认",
                 "blocked": "禁用服务器默认"}

REGISTRATION_MODES = (("open", "开放", "任何人都能注册新账号（现状默认）"),
                      ("invite_only", "仅邀请码", "注册需要邀请码"),
                      ("closed", "关闭", "注册端点直接返回 403"))
REGISTRATION_MODE_LABELS = {m: label for m, label, _desc in REGISTRATION_MODES}

OVERVIEW_FIELDS = (("accounts", "账号总数"),
                  ("disabled", "已禁用账号"),
                  ("server_admins", "控制台管理员"),
                  ("flags_on", "开启的开关"),
                  ("version", "后端版本"))

LLM_LIMIT_SOURCE_LABELS = {"user": "账号覆盖", "global": "全局", "unset": "未设置"}

IDENTITY_MODE_TEXTS = {
    "off": ("off（关闭，默认）", "不出签也不校验：既有响应逐字节不变"),
    "shadow": ("shadow（影子）", "出签但不阻断：出不了签只记日志，绝不因签名问题拒绝请求"),
    "enforce": ("enforce（强制）",
                "出签；客户端已带 nonce 而服务器出不了签时拒绝（探针与旧版本不受影响）"),
}

DEVICE_ACTION_SWITCHES = (("global", "全局行动开关", "关"),
                          ("plugin_enabled", "插件行动通道", "关"),
                          ("force_dry_run", "插件强制干跑", "开"))

DEVICE_ACTION_SECTIONS = (("targets", "目标白名单", "应用包名，如 com.example.app"),
                          ("plugins", "插件灰度名单", "插件名，如 browser_mcp"))

def _llm_limit_text(row) -> str:
    """账号额度展示：『生效值（来源）』；无生效额度显示「未设置」。

    只做展示，不做任何推算：生效值/来源一律取后端给的 llm_total_limit / llm_total_limit_source。
    """
    src = str(row.get("llm_total_limit_source") or "")
    val = row.get("llm_total_limit")
    if src == "unset" or not val:
        return "未设置"
    return "%s（%s）" % (val, LLM_LIMIT_SOURCE_LABELS.get(src, src))

ACCOUNT_SCOPE_TEXT = {
    "delete_family_root": "家庭根（整户数据一并带走）",
    "delete_sub_account": "子账号（仅该成员）",
}

PURGE_DUE_TEXT = "已到期，待清除"

VOLUME_TABLE_ROWS_SHOWN = 20

EXCEPTION_LINES_SHOWN = 8

def _purge_countdown_text(purge_after, now=None) -> str:
    """回收站行的倒计时文案：「X 天后自动清除」/「已到期，待清除」。

    ``purge_after`` 是后端存的 naive UTC，这里补 tzinfo=UTC 后与 UTC 现在比——
    换算成本地时区再比会整整差 8 小时（项目时间约定的老坑）。
    缺字段或解析不出来只回「在回收站」，不猜剩余天数。
    """
    s = str(purge_after or "").strip()
    if not s:
        return "在回收站"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "").replace("/", "-"))
    except Exception:
        return "在回收站"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    left = (dt - (now or datetime.now(timezone.utc))).total_seconds()
    if left <= 0:
        return PURGE_DUE_TEXT
    # 向下取整 + 保底 1 天：还剩 3 天 23 小时该读作「3 天后」而不是 4 天后；
    # 而不足一天的（left < 86400）显示 0 天会让人以为已经到期，所以兜到 1。
    return "%d 天后自动清除" % max(1, int(left // 86400))

def _account_rows_filtered(rows, query) -> list:
    """账号页关键字/前缀筛选（与开关页搜索框同口径：小写包含匹配，空查询＝全给）。"""
    q = str(query or "").strip().lower()
    out = [r for r in (rows or []) if isinstance(r, dict)]
    if not q:
        return out
    keep = []
    for r in out:
        hay = " ".join([str(r.get("username") or ""), str(r.get("nickname") or ""),
                        str(r.get("id") or "")]).lower()
        if q in hay:
            keep.append(r)
    return keep

def _volume_table_rows(tables) -> list:
    """dry-run 的 tables → 「表 / 行数 / 命中归属列」三列（后端已按行数降序排好）。

    只列 ``deletable`` 为真的列：后端还会带回「命中但不作为删除依据」的列（如 editor 列），
    把它们混进「命中归属列」会让人以为删得比实际更多。
    """
    out = []
    for t in tables or []:
        if not isinstance(t, dict):
            continue
        cols = {str(c.get("column") or "") for c in (t.get("columns") or [])
                if isinstance(c, dict) and c.get("deletable")}
        out.append([str(t.get("table") or ""), _fmt_int(t.get("rows")),
                    "、".join(sorted(cols)) or "—"])
    return out

class AdminApiError(Exception):
    """管理面请求失败。

    code：0=网络不可达/响应不可解析，401/403=登录态或权限问题，
    404=后端接口未就绪（另一端在并行开发），其余为后端原样返回的状态码。
    """

    def __init__(self, message, code=0, path=""):
        Exception.__init__(self, message)
        self.message = message
        self.code = code
        self.path = path

_CONSOLE_SESSION = {}

def _console_token_read() -> dict:
    """读取缓存登录态 {token, username, user_id}；文件缺失/损坏时返回空 dict。"""
    try:
        with open(CONSOLE_TOKEN_FILE, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("token"):
            return data
    except Exception:
        pass
    return {}

def _console_session() -> dict:
    """当前会话（内存优先，首次访问时从 console_token.json 恢复）。"""
    global _CONSOLE_SESSION
    if not _CONSOLE_SESSION:
        _CONSOLE_SESSION = _console_token_read()
    return _CONSOLE_SESSION

def _console_login_state() -> str:
    """已登录用户名；未登录返回空串。"""
    s = _console_session()
    return str(s.get("username") or "") if s.get("token") else ""

def _console_set_session(token: str, username: str = "", user_id=None) -> None:
    global _CONSOLE_SESSION
    _CONSOLE_SESSION = {"token": token, "username": username, "user_id": user_id}
    payload = dict(_CONSOLE_SESSION)
    payload["saved_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        os.makedirs(os.path.dirname(CONSOLE_TOKEN_FILE), exist_ok=True)
        with _tool_path(CONSOLE_TOKEN_FILE).open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        # 落盘失败只意味着下次启动要重新登录，不影响本次会话
        _safe_traceback()

def _console_clear_session() -> None:
    global _CONSOLE_SESSION
    _CONSOLE_SESSION = {}
    try:
        if os.path.exists(CONSOLE_TOKEN_FILE):
            os.remove(CONSOLE_TOKEN_FILE)
    except Exception:
        _safe_traceback()

def _http_json(method: str, path: str, body=None, token: str = "", timeout: float = ADMIN_TIMEOUT):
    """发一个 JSON HTTP 请求，返回 (status, parsed)。网络失败抛 AdminApiError(code=0)。

    非 2xx 不抛（HTTPError 的响应体也要读出来，后端可读提示在 detail 里）；
    响应体不是 JSON 时返回空 dict，由上层按状态码给文案。
    """
    import urllib.error
    import urllib.request
    url = _target_base() + path
    _guard_http_url(url)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + str(token))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            status = int(getattr(r, "status", 200) or 200)
    except urllib.error.HTTPError as e:
        status = int(e.code or 0)
        try:
            raw = e.read()
        except Exception:
            raw = b""
    except Exception as e:
        raise AdminApiError("无法连接后端 %s（%s）" % (url, e), code=0, path=path)
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        parsed = {}
    return status, parsed

def _err_text(parsed, default: str = "") -> str:
    """从 FastAPI 响应里取可读 detail（detail 可能是 str 或 dict）。"""
    if isinstance(parsed, dict):
        detail = parsed.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()[:200]
        if isinstance(detail, dict):
            for k in ("message", "detail", "msg"):
                v = detail.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()[:200]
    return default

def _admin_request(method: str, sub: str, body=None) -> dict:
    """管理面统一请求（自动带 Bearer）。成功返回 dict，失败抛 AdminApiError。

    语义化状态码（前端不做业务规则，只翻译后端返回）：
    - 无 token → 401（不发请求）
    - 401 → 清会话（token 过期/失效）
    - 403 → 保留会话：可能是非 server_admin，也可能是开关被服务器锁定
    - 404/405 → 接口未就绪（后端另一路在并行开发）
    """
    path = ADMIN_API_PREFIX + sub
    token = str(_console_session().get("token") or "")
    if not token:
        raise AdminApiError("未登录或权限不足，请重新登录", code=401, path=path)
    status, data = _http_json(method, path, body=body, token=token)
    if status == 401:
        _console_clear_session()
        raise AdminApiError("未登录或权限不足，请重新登录", code=401, path=path)
    if status == 403:
        raise AdminApiError(_err_text(data, "未登录或权限不足，请重新登录"), code=403, path=path)
    if status in (404, 405):
        raise AdminApiError("接口未就绪：%s %s" % (method, path), code=404, path=path)
    if not (200 <= status < 300):
        raise AdminApiError("后端返回 %d：%s" % (status, _err_text(data, str(data)[:200])),
                            code=status, path=path)
    return data if isinstance(data, dict) else {"data": data}

def _admin_login(username: str, password: str) -> dict:
    """POST /api/v1/auth/login，成功则缓存 JWT 到 console_token.json。"""
    status, data = _http_json("POST", AUTH_LOGIN_PATH,
                              body={"username": username, "password": password})
    if status in (401, 403):
        raise AdminApiError(_err_text(data, "用户名或密码不正确"), code=status, path=AUTH_LOGIN_PATH)
    if status == 429:
        raise AdminApiError(_err_text(data, "尝试次数过多，请稍后再试"), code=status, path=AUTH_LOGIN_PATH)
    if not (200 <= status < 300):
        raise AdminApiError("登录失败（后端返回 %d）：%s" % (status, _err_text(data, str(data)[:200])),
                            code=status, path=AUTH_LOGIN_PATH)
    token = str(data.get("access_token") or "") if isinstance(data, dict) else ""
    if not token:
        raise AdminApiError("登录响应缺少 access_token", code=0, path=AUTH_LOGIN_PATH)
    _console_set_session(token, str(data.get("username") or username), data.get("user_id"))
    return data

def _fmt_dt(v) -> str:
    """后端 UTC naive 时间串 → 北京时间展示（只截断到分钟，解析失败原样显示）。"""
    s = str(v or "").strip()
    if not s:
        return "—"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "").replace("/", "-"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    except Exception:
        return s.replace("T", " ")[:16]

def _registration_mode_text(mode) -> str:
    """mode → 「开放（open）」；未知值原样显示，未返回时给联调提示（不做业务推断）。"""
    m = str(mode or "").strip()
    if not m:
        return "后端未返回 mode"
    label = REGISTRATION_MODE_LABELS.get(m)
    return "%s（%s）" % (label, m) if label else "未知策略（%s）" % m

def _identity_fp_text(fp) -> str:
    """指纹（12 hex）→ 「ABCD-EFGH-IJKL」；没有活动密钥时明说，不拿空串糊过去。"""
    s = str(fp or "").strip().upper()
    if not s:
        return "尚未配对（服务器无活动身份密钥）"
    return "-".join(s[i:i + 4] for i in range(0, len(s), 4))

def _identity_code_text(code) -> str:
    """配对码 4 位分组显示（12 位 → 3 组），只为降低手抄出错率，不改变码本身。"""
    s = str(code or "").strip().upper()
    if not s:
        return "—"
    return " ".join(s[i:i + 4] for i in range(0, len(s), 4))

GROUP_LABELS = {
    "agent": "智能体运行与认知",
    "proactive": "主动消息",
    "games": "群聊小游戏",
    "life": "AI 自主生活",
    "lifesense": "生命感增强",
    "outreach_natural": "主动消息自然化",
    "memory": "记忆检索与注入",
    "curated": "编纂知识与前瞻意图",
    "cross_char": "跨角色用户事实",
    "working": "工作记忆",
    "provider": "插件与提供商",
    "pacing": "主动投放节制",
    "review": "主动复习与回忆化",
    "channel": "渠道绑定与群认知",
    "tool_trace": "工具轨迹治理",
    "device_action": "手机行动通道",
    "other": "其他高级开关",
}
