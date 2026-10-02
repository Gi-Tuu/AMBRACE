"""系统状态 / 局域网地址 / 更新公告解析应用服务（A22 第四刀，2026-10-02）。

本模块自 ``app/application/system.py`` 逐字节搬入。边界＝**系统状态/局域网地址/更新公告解析**。

跨块调用约定（同 A22 前三刀）：需要 ``system.py`` 驻留的共享辅助（``_require_admin`` /
``_require_server_admin`` / ``_cfg_snapshot`` / ``_audit``）时，在**函数内** import 该模块后走
模块属性回指——放顶层会与 system.py 的重导出成环。本块 7 个函数彼此自洽（``_get_lan_ip``、
``_changelog_title``、``_parse_changelog`` 均为同模块互调），鉴权又都压在 api 层，
四个共享辅助一个都用不到，因此**本模块不存在任何回指**（守卫据此断言）。
"""
from datetime import datetime, timezone

from app.utils.logger import get_logger
from app.utils.version import get_project_version

_logger = get_logger("application.system")


def _is_private_ipv4(ip: str) -> bool:
    """判断是否为私网 IPv4（排除回环/链路本地/VPN 虚拟网卡地址）"""
    import ipaddress
    try:
        a = ipaddress.ip_address(ip)
        if a.version != 4 or a.is_loopback or a.is_link_local or not a.is_private:
            return False
        # Python 3.13+ 将 198.18.0.0/15（RFC 2544 benchmarking，常见于 VPN 虚拟网卡）判为私网，显式排除
        if a in ipaddress.ip_network("198.18.0.0/15"):
            return False
        return True
    except ValueError:
        return False


def _get_lan_ip() -> str:
    """探测局域网 IP：优先私网 IPv4（排除回环/链路本地/VPN 虚拟网卡），失败返回空串"""
    import socket
    # 1) UDP 默认路由法（不实际发包）：若路由 IP 是私网则直接采用
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if _is_private_ipv4(ip):
            return ip
    except Exception:
        pass
    # 2) 枚举所有网卡 IPv4，按网卡名过滤虚拟/隧道接口后取私网地址（psutil 已在依赖内）
    try:
        import psutil
        skip_keywords = (
            "vethernet", "wsl", "tailscale", "vmware", "virtualbox", "docker",
            "vpn", "tun", "tap", "ppp", "hyper-v", "loopback", "isatap",
            "teredo", "bluetooth",
        )
        candidates: list[str] = []
        for _iface, addrs in psutil.net_if_addrs().items():
            if any(k in _iface.lower() for k in skip_keywords):
                continue
            for a in addrs:
                if a.family == socket.AF_INET and _is_private_ipv4(a.address):
                    candidates.append(a.address)
        if candidates:
            return sorted(candidates)[0]
    except Exception:
        pass
    # 3) 兜底 getaddrinfo（去掉回环）
    try:
        ips = [i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None)
               if i[0] == socket.AF_INET and not i[4][0].startswith("127.")]
        return ips[0] if ips else ""
    except Exception:
        return ""


def _changelog_title(rest: str, date: str) -> str:
    """从 `## ` 后的文本提取标题。

    优先级：括号内非日期文本 → 括号外前缀（如 v3.3.9 / 待发布）→ 日期本身。
    """
    import re as _re
    if not rest:
        return date
    paren = _re.search(r"（(.+?)）", rest)
    if paren:
        inner = paren.group(1).strip()
        non_date = _re.sub(r"\d{4}-\d{2}-\d{2}", "", inner).strip("，,、 ")
        if non_date:
            return non_date
        prefix = rest[:paren.start()].strip()
        if prefix:
            return prefix
        return date
    return rest if rest else date


def _parse_changelog(text: str) -> list[dict]:
    """解析 changelog.md 文本，按天折叠（最新在前）。

    兼容标题格式：
    - 旧版：`## 2026-08-28（标题，待发布）`
    - 新版：`## v3.3.9（2026-08-28）` / `## 待发布（2026-08-28）`
    对每行 `## ` 开头用正则提取日期；`cur` 在循环前初始化为 None，首行即表格/无匹配时不 NameError。
    """
    import re as _re

    days_map: dict[str, dict] = {}
    order: list[str] = []
    cur: dict | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("##"):
            dm = _re.search(r"(\d{4}-\d{2}-\d{2})", line)
            if dm is None:
                # 无日期标题（防御）：不挂到条目，避免后续表格行飘到错误日
                cur = None
                continue
            date = dm.group(1)
            title = _changelog_title(line[2:].strip(), date)
            if date not in days_map:
                days_map[date] = {"date": date, "title": title, "items": [], "_sections": 1}
                order.append(date)
            else:
                # 同一天多个标题：合并为一个折叠日，标题标注节数
                days_map[date]["_sections"] += 1
            cur = days_map[date]
            continue
        if cur is None or not line.startswith("|") or line.count("|") < 3:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or cells[0] in ("内容", "---"):
            continue
        if len(cells) >= 2 and cells[0]:
            content = _re.sub(r"\*\*(.+?)\*\*", r"\1", cells[0])
            reason = _re.sub(r"\*\*(.+?)\*\*", r"\1", cells[1]) if len(cells) >= 2 else ""
            cur["items"].append({"content": content, "reason": reason})
    # 组装：同一天标题加节数标注；只展示最近 30 天，避免过长
    out: list[dict] = []
    for date in order:
        entry = days_map[date]
        sections = entry.pop("_sections", 1)
        if sections > 1:
            entry["title"] = f"{entry['title']}（{sections} 节）"
        out.append(entry)
    return out[:30]


async def system_status_public() -> dict:
    """匿名可见的最小状态：在线布尔 + 版本，不含局域网 IP / 内网 base_url。

    P3-B：公开 /status 只回这四项；完整版 system_status()（含 lan_ip / vlm）挪到需登录的
    GET /status/detail。
    """
    return {
        "server": "AMBRACE Server",
        "version": get_project_version(),
        "status": "running",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def system_status(
):
    """服务器运行状态（完整版，含局域网 IP 与图片理解配置状态）——仅经鉴权端点回传。"""
    from app.config import settings
    return {
        "server": "AMBRACE Server",
        "version": get_project_version(),
        "status": "running",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lan_ip": _get_lan_ip(),
        "vlm": {
            "enabled": bool(settings.vlm_enabled),
            "cloud_api_key_configured": bool(settings.vlm_api_key),
            "base_url": settings.vlm_base_url,
            "model": settings.vlm_model,
        },
    }


async def get_updates(
):
    """更新公告：解析 docs/changelog.md，按天折叠（最新在前），供 app 内「更新公告」页展示"""
    from pathlib import Path

    changelog_path = Path(__file__).resolve().parents[3] / "docs" / "changelog.md"
    try:
        text = changelog_path.read_text(encoding="utf-8")
    except Exception as e:
        _logger.warning("Changelog read failed: %s", e)
        return {"days": []}
    return {"days": _parse_changelog(text)}
