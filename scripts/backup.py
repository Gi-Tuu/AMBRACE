# -*- coding: utf-8 -*-
"""拥爱（AMBRACE）每日备份脚本。

备份内容（zip，日期命名，保留最近 KEEP_DAYS 份）：
  - 源码：backend/app、scripts、server_controller、flutter_app/lib、docs
  - 关键文件：AGENTS.md、flutter_app/pubspec.yaml、flutter_app/analysis_options.yaml
  - 数据：backend/data/sqlite/ai_companion.db（SQLite backup API，运行中可安全复制）、backend/data/server_config.json

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
KEEP_DAYS = 14

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
    """清理 7 天前的轮转日志（app.log.YYYY-MM-DD）；当前活动日志 app.log/server_stderr.log/watchdog.log 不删"""
    if not os.path.isdir(LOG_DIR):
        return "日志轮换：无日志目录"
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
        cutoff = (datetime.now() - timedelta(days=TRIGGER_LOG_KEEP_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
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
    today = datetime.now().strftime("%Y%m%d")
    zip_path = os.path.join(BACKUP_ROOT, f"{today}.zip")
    if os.path.exists(zip_path):
        # 备份已存在（如当天多次调用）也执行日志轮换
        return f"已存在，跳过: {zip_path}；{rotate_logs()}；{prune_trigger_logs()}"

    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
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
                    zf.write(fp, os.path.relpath(fp, SERVER_DIR))
                    count += 1
        for f in SRC_FILES:
            fp = os.path.join(SERVER_DIR, f)
            if os.path.isfile(fp):
                zf.write(fp, f)
                count += 1
        if os.path.isfile(CONFIG_FILE):
            zf.write(CONFIG_FILE, os.path.relpath(CONFIG_FILE, SERVER_DIR))
            count += 1
        count += _add_sqlite_backup(zf)

    # 清理过期备份
    removed = []
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
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