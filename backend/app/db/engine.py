"""DB 引擎（F1 拆分，2026-08-31）：引擎创建与 SQLite 目录准备；会话工厂见 session.py。"""
"""数据库连接与会话管理（原 database.py 头部）"""
import os
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

# 首次部署时 backend/data/sqlite/ 尚不存在，SQLite 会报 unable to open database file
if settings.database_url.startswith("sqlite"):
    _db_file = settings.database_url.split("///", 1)[-1].split("?", 1)[0]
    if _db_file and _db_file != ":memory:":
        os.makedirs(Path(_db_file).resolve().parent, exist_ok=True)

# 创建异步引擎
engine = create_async_engine(
    settings.database_url,
    echo=False,
    poolclass=NullPool,          # SQLite 不需要连接池
    connect_args={"check_same_thread": False, "timeout": 10},
)

# G1（v3.4.4 审查，2026-09-05）：启用 WAL——默认 rollback-journal 模式下写是全库排他锁，
# 后台长生命周期写者（MCP worker/调度器/异步维护）× 前台聊天并发写偶发 `database is locked`
# （全量测试已实锤）。WAL 允许「单写 + 多读」并发，显著降低 locked 概率。
# 每条新连接建立时执行一次；:memory: 库跳过（测试用临时库不落盘）。
# 回滚 = 删除本 listener（无 schema 迁移）；已生成的 -wal 文件在连接正常关闭时自动合并。
# 备份配套：scripts/backup.py 已用 sqlite3 backup API（对 WAL 库天然安全，含 -wal 数据），无需改动。
if settings.database_url.startswith("sqlite") and ":memory:" not in settings.database_url:
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL;")         # 写前日志，单写多读并发
            cur.execute("PRAGMA synchronous=NORMAL;")       # WAL 下 NORMAL 安全且更快
            cur.execute("PRAGMA busy_timeout=10000;")       # 毫秒级，与 connect_args.timeout 双保险
            cur.execute("PRAGMA wal_autocheckpoint=1000;")  # 每 1000 页自动 checkpoint，防 -wal 无限增长
        finally:
            cur.close()

