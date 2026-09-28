# -*- coding: utf-8 -*-
"""拥爱（AMBRACE）每日备份脚本。

备份内容（zip，日期命名，保留最近 KEEP_DAYS 份）：
  - 源码：backend/app、scripts、server_controller、flutter_app/lib、docs
  - 关键文件：AGENTS.md、flutter_app/pubspec.yaml、flutter_app/analysis_options.yaml
  - 数据：backend/data/sqlite/ai_companion.db（SQLite backup API，运行中可安全复制）、backend/data/server_config.json
  - **不含任何密钥文件**（见 SECRET_BASENAMES：凭据主密钥 / JWT 签名密钥 / 推送服务账号），
    库内凭据自 A8 方案 B 起为密文（enc:v1: 前缀），没有主密钥就解不开——这正是排除的意义。

用法：
  backend\\.venv\\Scripts\\python.exe scripts\\backup.py            # 立即备份
  （watchdog 启动后会每天自动执行一次；也可加入计划任务）

注意：本脚本内所有文件写入均为 UTF-8，不经过 PowerShell 管道，避免中文损坏。
"""
import os
import re
import sqlite3
import sys
import zipfile
from datetime import datetime, timedelta

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根目录
BACKUP_ROOT = os.path.join(SERVER_DIR, "backups")

# 批 2b（2026-09-28）：备份文件名的日期统一走「应用本地时区」的今天（见 backup_day_key）。
# 独立运行（python scripts/backup.py）时按路径自举 backend/，使脚本与后端共用同一时区口径。
_BACKEND_DIR = os.path.join(SERVER_DIR, "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.utils.timeutil import app_local_now, now_naive_utc  # noqa: E402


def backup_day_key() -> str:
    """当天备份文件名的日期键（YYYYMMDD，**应用本地时区**）。

    生产端（本文件 do_backup）与消费端（application/system.py 的备份触发/下载、
    application/account_purge.py 的前置备份）都只走这一个入口：两边各写一次
    datetime.now().strftime("%Y%m%d") 时，服务器 OS 时区与应用时区不同（容器 UTC）
    就会各自算出不同日期 ⇒ isfile() 失配 ⇒ trigger_backup 500 / 前置备份 fail-closed。
    """
    return app_local_now().strftime("%Y%m%d")


# ── A8 方案 B（2026-09-26）：备份包内提示改为「凭据已加密，主密钥另存且不在本包内」──
README_IN_ZIP = "README-BACKUP.txt"
README_BACKUP_TEXT = (
    "AMBRACE 备份包说明（自动生成）\n"
    "\n"
    "本压缩包包含 backend/data/sqlite/ai_companion.db 与 backend/data/server_config.json。\n"
    "其中的模型 / 语音 / 多模态等 API 凭据采用本地信封加密存储（AES-256-GCM，密文以 enc:v1: 开头），\n"
    "解密用的主密钥是 backend/data/secrets.key —— **它刻意不在本备份包内**（同被排除的还有\n"
    "auth_secret.key 登录签名密钥、server_identity.key 服务器身份密钥、fcm-service-account.json\n"
    "推送服务账号）。\n"
    "\n"
    "因此：\n"
    "1) 请勿把本备份包外发、上传网盘或提交到代码仓库；密钥文件同样按机密件对待，两者分开保存；\n"
    "2) 还原到别的机器时，除本包外必须另拷 backend/data/secrets.key，并通过安全渠道传输；\n"
    "3) 主密钥丢失＝凭据无法还原，只能在设置里重新填写（这是 A8 方案 B 已拍板的前提）；\n"
    "4) 若库内字段仍是明文（尚未跑 backend\\scripts\\encrypt_credentials.py --apply），\n"
    "   先跑一次 dry-run 看清处数，再由维护者手跑 --apply 完成加密。\n"
)
KEEP_DAYS = 14

# A8 方案 B：密钥/凭据文件一律不进备份包（即便日后有人把 backend/data 加进 SRC_DIRS 也拦住）
SECRET_BASENAMES = {
    "secrets.key",                  # 凭据加密主密钥（app/utils/credential_crypto.py）
    "auth_secret.key",              # JWT 签名密钥（app/auth/config.py）
    "server_identity.key",          # 服务器身份密钥（批 0-3 M0-a，app/server_identity.py）
    "fcm-service-account.json",     # 推送服务账号（含私钥）
}


def is_secret_file(path: str) -> bool:
    """是否属于「绝不打包」的密钥/凭据文件。"""
    name = os.path.basename(path)
    return name in SECRET_BASENAMES or name.endswith((".key", ".pem")) or ".pre-a8b-" in name

SRC_DIRS = [
    "backend/app",
    "scripts",
    "server_controller",
    "flutter_app/lib",
    "docs",
]
SRC_FILES = [
    "AGENTS.md",
    "flutter_app/pubspec.yaml",
    "flutter_app/analysis_options.yaml",
]
DB_FILE = os.path.join(SERVER_DIR, "backend", "data", "sqlite", "ai_companion.db")
CONFIG_FILE = os.path.join(SERVER_DIR, "backend", "data", "server_config.json")

LOG_DIR = os.path.join(SERVER_DIR, "backend", "data", "logs")
LOG_KEEP_DAYS = 7  # 轮转日志保留天数（app.log.YYYY-MM-DD）
TRIGGER_LOG_KEEP_DAYS = 7  # 主动触发日志保留天数（proactive_trigger_logs）
SKIP_DIRS = {"__pycache__", "build", ".dart_tool", ".venv", "node_modules"}


def _add_sqlite_backup(zf: zipfile.ZipFile) -> int:
    """用 sqlite3 backup API 安全复制运行中的数据库（避免文件复制时数据不一致）"""
    if not os.path.isfile(DB_FILE):
        return 0
    tmp = DB_FILE + ".bak.tmp"
    try:
        src = sqlite3.connect(DB_FILE)
        try:
            dst = sqlite3.connect(tmp)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        arc = os.path.relpath(DB_FILE, SERVER_DIR)
        zf.write(tmp, arc)
        return 1
    except Exception as e:
        print(f"DB backup failed: {e}")
        return 0
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def rotate_logs() -> str:
    """清理 7 天前的轮转日志（app.log.YYYY-MM-DD）；当前活动日志 app.log/server_stderr.log/watchdog.log 不删

    C1（2026-09-17）：stdio / 网关日志（server_stderr.log、server_stdout.log、gateway_*.log）由
    scripts/log_rotate.py 在「打开重定向句柄之前」做启动前轮转（滚动为 <path>.1/.2），本函数只管
    app.log.YYYY-MM-DD 的按天清理。两套命名并存且不冲突：本函数正则只匹配 ^app\\.log\\.(\\d{4}-\\d{2}-\\d{2})$。
    """
    if not os.path.isdir(LOG_DIR):
        return "日志轮换：无日志目录"
    # 归档名 app.log.YYYY-MM-DD 由 TimedRotatingFileHandler 按 **OS 本地时区** 生成，
    # 故清理口径同为 OS 本地（与命名同源）。这是另一套命名，勿与备份文件名的应用时区混用。
    cutoff = datetime.now() - timedelta(days=LOG_KEEP_DAYS)
    removed = []
    for fn in os.listdir(LOG_DIR):
        m = re.match(r"^app\.log\.(\d{4}-\d{2}-\d{2})$", fn)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            continue
        if d < cutoff:
            try:
                os.remove(os.path.join(LOG_DIR, fn))
                removed.append(fn)
            except Exception:
                pass
    return f"日志轮换：清理 {len(removed)} 个过期日志 {removed or '无'}"


def prune_trigger_logs() -> str:
    """清理 7 天前的主动触发日志（proactive_trigger_logs），控制表膨胀（审计 P1-06，2026-08-15）"""
    try:
        if not os.path.isfile(DB_FILE):
            return "触发日志清理：无数据库"
        # created_at 是库内 UTC naive 列（server_default=func.now()），清理口径必须同为 UTC naive：
        # 旧写法用本地时区基准，在 UTC 容器里会把窗口多删 8 小时的数据。
        cutoff = (now_naive_utc() - timedelta(days=TRIGGER_LOG_KEEP_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        con = sqlite3.connect(DB_FILE)
        try:
            cur = con.execute("DELETE FROM proactive_trigger_logs WHERE created_at < ?", (cutoff,))
            con.commit()
            return f"触发日志清理：删除 {cur.rowcount} 条（{TRIGGER_LOG_KEEP_DAYS} 天前）"
        finally:
            con.close()
    except Exception as e:
        return f"触发日志清理失败：{e}"


def do_backup() -> str:
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    today = backup_day_key()
    zip_path = os.path.join(BACKUP_ROOT, f"{today}.zip")
    if os.path.exists(zip_path):
        # 备份已存在（如当天多次调用）也执行日志轮换
        return f"已存在，跳过: {zip_path}；{rotate_logs()}；{prune_trigger_logs()}"

    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # A8 方案 A（2026-09-25）：包内附醒目提示（凭据仍为明文，勿外发）
        zf.writestr(README_IN_ZIP, README_BACKUP_TEXT)
        for d in SRC_DIRS:
            p = os.path.join(SERVER_DIR, d)
            if not os.path.isdir(p):
                continue
            for root, dirs, files in os.walk(p):
                dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
                for fn in files:
                    if fn.endswith((".pyc", ".pyo")):
                        continue
                    fp = os.path.join(root, fn)
                    if is_secret_file(fp):  # A8 方案 B：密钥文件绝不进包
                        continue
                    zf.write(fp, os.path.relpath(fp, SERVER_DIR))
                    count += 1
        for f in SRC_FILES:
            fp = os.path.join(SERVER_DIR, f)
            if os.path.isfile(fp) and not is_secret_file(fp):
                zf.write(fp, f)
                count += 1
        if os.path.isfile(CONFIG_FILE) and not is_secret_file(CONFIG_FILE):
            zf.write(CONFIG_FILE, os.path.relpath(CONFIG_FILE, SERVER_DIR))
            count += 1
        count += _add_sqlite_backup(zf)

    # 清理过期备份
    removed = []
    # 备份文件名按 backup_day_key()（应用本地时区）命名，过期判断取同一来源
    cutoff = datetime.strptime(backup_day_key(), "%Y%m%d") - timedelta(days=KEEP_DAYS)
    for fn in os.listdir(BACKUP_ROOT):
        if not fn.endswith(".zip"):
            continue
        try:
            d = datetime.strptime(fn[:8], "%Y%m%d")
        except ValueError:
            continue
        if d < cutoff:
            try:
                os.remove(os.path.join(BACKUP_ROOT, fn))
                removed.append(fn)
            except Exception:
                pass
    rotate_msg = rotate_logs()
    prune_msg = prune_trigger_logs()
    return f"备份完成: {zip_path}（{count} 个文件）；清理过期备份: {removed or '无'}；{rotate_msg}；{prune_msg}"


if __name__ == "__main__":
    print(do_backup())