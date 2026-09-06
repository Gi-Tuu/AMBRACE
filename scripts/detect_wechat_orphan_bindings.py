# -*- coding: utf-8 -*-
"""一次性收敛脚本（只读 + 预览，不 apply）：检测「孤儿微信绑定」。

孤儿定义：wechat_ilink_bindings 中 enabled=1 的行，在 channel_bindings 里找不到
对应的 (channel='wechat', tenant_id, bot_account_id) 行。

背景（2026-09-06，Codex 派工第 8 项）：内核渠道绑定升级为 channel_bindings 表后，
部分部署的 wechat_ilink_bindings 存量行有 enabled=1 但没有对应 channel_bindings 行。
这种孤儿绑定仍会被插件 relay 路由（插件行仍存在），但因 channel_bindings 已删/未建，
面板状态与绑定接口视图不一致（Codex 已手工收敛现网 1 例；本脚本用于未来/其它部署检测）。

纪律：
- 只读：仅执行 SELECT，绝不 INSERT/UPDATE/DELETE；
- 无副作用：不改任何表，不写日志，不生成补丁；
- 输出：孤儿清单（id/tenant_id/bot_account_id/character_id/enabled）+ 修复建议。

用法：
    python scripts/detect_wechat_orphan_bindings.py [DB_PATH] [--json]
默认 DB_PATH = backend/data/sqlite/ai_companion.db（如不存在回退到环境变量 DATABASE_URL 指向的路径）。
"""
import argparse
import os
import sqlite3
import sys

# backend/data/sqlite/ai_companion.db（与 server_controller / settings.database_url 同口径）
_DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),  # scripts/ 上一级 = 仓库根
    "backend", "data", "sqlite", "ai_companion.db",
)

WECHAT_TABLE = "wechat_ilink_bindings"
CHANNEL_TABLE = "channel_bindings"
CHANNEL = "wechat"


def _open_ro(db_path: str) -> sqlite3.Connection:
    """以只读方式打开 SQLite（URI mode=ro），运行期绝不写库。"""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")  # 双保险：库内任何写都被 SQLite 拒绝
    return conn


def detect(db_path: str) -> list[dict]:
    """返回孤儿绑定清单（每项含 id/tenant_id/bot_account_id/character_id/ilink_user_id）。"""
    conn = _open_ro(db_path)
    try:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = {WECHAT_TABLE, CHANNEL_TABLE} - tables
        if missing:
            raise RuntimeError(
                "缺少表：%s（当前库不含渠道绑定表，插件可能未初始化）" % ", ".join(sorted(missing))
            )

        wx_cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({WECHAT_TABLE})")}
        ch_cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({CHANNEL_TABLE})")}
        for col in ("tenant_id", "bot_account_id", "enabled", "character_id"):
            if col not in wx_cols:
                raise RuntimeError(f"{WECHAT_TABLE} 缺字段 {col}")
        for col in ("tenant_id", "bot_account_id", "channel"):
            if col not in ch_cols:
                raise RuntimeError(f"{CHANNEL_TABLE} 缺字段 {col}")

        # channel_bindings 中 channel='wechat' 的 (tenant_id, bot_account_id) 集合
        ch_pairs = set(
            (r["tenant_id"], r["bot_account_id"])
            for r in conn.execute(
                f"SELECT tenant_id, bot_account_id FROM {CHANNEL_TABLE} WHERE channel = ?",
                (CHANNEL,),
            )
        )

        orphan_cols = ["id", "tenant_id", "bot_account_id", "character_id", "enabled"]
        if "ilink_user_id" in wx_cols:
            orphan_cols.append("ilink_user_id")
        cols_sql = ", ".join(orphan_cols)

        orphans = []
        for r in conn.execute(
            f"SELECT {cols_sql} FROM {WECHAT_TABLE} WHERE enabled = 1"
        ):
            key = (r["tenant_id"], r["bot_account_id"])
            if key not in ch_pairs:
                orphans.append({
                    "id": r["id"],
                    "tenant_id": r["tenant_id"],
                    "bot_account_id": r["bot_account_id"],
                    "character_id": r["character_id"],
                    "enabled": bool(r["enabled"]),
                    "ilink_user_id": r["ilink_user_id"] if "ilink_user_id" in wx_cols else None,
                })
        return orphans
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="检测孤儿微信绑定（只读预览，不 apply）")
    ap.add_argument("db_path", nargs="?", default=_DEFAULT_DB, help="SQLite 库路径（默认 backend/data/sqlite/ai_companion.db）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出（便于程序消费）")
    args = ap.parse_args()

    if not os.path.exists(args.db_path):
        print("[ERROR] 数据库不存在：%s" % args.db_path, file=sys.stderr)
        return 2

    try:
        orphans = detect(args.db_path)
    except Exception as e:
        print("[ERROR] 检测失败：%s" % e, file=sys.stderr)
        return 1

    if args.json:
        import json
        print(json.dumps({"db": args.db_path, "orphans": orphans}, ensure_ascii=False, indent=2))
        return 0

    if not orphans:
        print("[OK] 未发现孤儿微信绑定（所有 enabled=1 的 wechat_ilink_bindings 均有对应 channel_bindings 行）。")
        return 0

    print("[检测到 %d 条孤儿微信绑定]" % len(orphans))
    for o in orphans:
        print(
            "  绑定 id=%-6s tenant=%-4s bot=%-24s character=%-6s ilink_user=%s"
            % (o["id"], o["tenant_id"], o["bot_account_id"], o["character_id"], o["ilink_user_id"])
        )
    print()
    print("修复建议（人工确认后执行，本脚本不写库）：")
    print("  A. 若该 bot 确应保留 → 补建 channel_bindings 行：")
    print("       INSERT INTO channel_bindings (channel, tenant_id, owner_user_id, bot_account_id, character_id)")
    print("       VALUES ('wechat', <tenant_id>, <tenant_id>, '<bot_account_id>', <character_id>);")
    print("  B. 若该 bot 已废弃 → 停用插件绑定（enabled=0）或解绑：")
    print("       UPDATE wechat_ilink_bindings SET enabled=0 WHERE id=<id>;  # 或走解绑 API")
    return 0


if __name__ == "__main__":
    sys.exit(main())
