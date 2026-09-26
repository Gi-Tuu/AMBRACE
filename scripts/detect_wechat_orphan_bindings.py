# -*- coding: utf-8 -*-
"""一次性收敛脚本（只读 + 预览，不 apply）：检测「孤儿微信绑定」。

孤儿定义：wechat_ilink_bindings 中 enabled=1 的行，在 channel_bindings 里找不到
对应的 (channel='wechat', tenant_id, bot_account_id) 行。

第二项检测（2026-09-26 批 E）：wechat_ilink_bindings / wechat_ilink_messages 里
character_id 指向**已不存在的角色**的行数——角色行已硬删而渠道自有数据还在
（绑定可能仍持凭据并参与 relay 路由、消息仍留着对话内容）。删角色路径自批 E 起会转调
渠道 on_character_deleted 清理，这项非 0 即存量或绕过路径，属本批要长期盯的形态。
两类分开报：「已停用 + 凭据已清」的绑定行是批 E 有意保留的**留痕行**（对齐解绑语义，
不算问题）；仍启用/仍持凭据的绑定行与指向已删角色的消息行为**需处理**。

背景（2026-09-06，Codex 派工第 8 项）：内核渠道绑定升级为 channel_bindings 表后，
部分部署的 wechat_ilink_bindings 存量行有 enabled=1 但没有对应 channel_bindings 行。
这种孤儿绑定仍会被插件 relay 路由（插件行仍存在），但因 channel_bindings 已删/未建，
面板状态与绑定接口视图不一致（Codex 已手工收敛现网 1 例；本脚本用于未来/其它部署检测）。

纪律：
- 只读：仅执行 SELECT，绝不 INSERT/UPDATE/DELETE；
- 无副作用：不改任何表，不写日志，不生成补丁；
- 输出：①孤儿绑定清单（id/tenant_id/bot_account_id/character_id/enabled）；②指向已删角色的
  渠道数据（wechat_ilink_bindings / wechat_ilink_messages 各自行数与明细）；各附修复建议。

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
MESSAGE_TABLE = "wechat_ilink_messages"
CHANNEL_TABLE = "channel_bindings"
CHARACTER_TABLE = "ai_characters"
CHANNEL = "wechat"
_DETAIL_LIMIT = 20  # 明细最多列这么多行（计数不受限）
# 判定绑定行是否「仍在服役」的列（凭据列只判空不读内容，P0-4 口径）
_LIVE_COL = "enabled"
_CRED_COLS = ("bot_token_enc", "ilink_bot_id", "baseurl", "poll_buf")


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


def detect_dangling_character(db_path: str) -> dict[str, list[dict]]:
    """检测「character_id 指向已不存在的角色」的渠道自有数据（2026-09-26 批 E 长期盯的形态）。

    与 detect() 互补：detect 看「enabled=1 但内核无 channel_bindings 行」（面板不一致），
    本函数看「角色行已硬删而渠道数据还在」。分两种，处置口径不同：
    - 绑定行仍 enabled 或仍残留凭据、以及指向已删角色的消息行 → **需处理**（批 E 起删角色会自动清）；
    - 绑定行已 enabled=0 且凭据全空 → 批 E 有意保留的**留痕行**（对齐解绑语义），不算问题。
    凭据只判「是否为空」，绝不把内容读进脚本。

    某张插件表不存在（渠道未装）或缺相关列 → 按空处理，不算检测失败。
    """
    out: dict[str, list[dict]] = {WECHAT_TABLE: [], MESSAGE_TABLE: []}
    conn = _open_ro(db_path)
    try:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if CHARACTER_TABLE not in tables:
            raise RuntimeError(f"缺少表：{CHARACTER_TABLE}（当前库不含角色表？）")
        for table in (WECHAT_TABLE, MESSAGE_TABLE):
            if table not in tables:
                continue
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if not {"id", "character_id"} <= cols:
                continue
            if table != WECHAT_TABLE or _LIVE_COL not in cols:
                sel = "id, character_id"
            else:
                cred = " OR ".join(f"COALESCE({c}, '') != ''" for c in _CRED_COLS if c in cols) or "0"
                sel = (f"id, character_id,"
                       f" CAST(COALESCE({_LIVE_COL}, 0) != 0 AS INTEGER) AS still_enabled,"
                       f" CAST(({cred}) AS INTEGER) AS has_credentials")
            rows = []
            for r in conn.execute(
                f"SELECT {sel} FROM {table}"
                " WHERE character_id IS NOT NULL"
                f"   AND character_id NOT IN (SELECT id FROM {CHARACTER_TABLE})"
                " ORDER BY character_id, id"
            ):
                item: dict = {"id": r["id"], "character_id": r["character_id"]}
                if "still_enabled" in r.keys():
                    item["still_enabled"] = bool(r["still_enabled"])
                    item["has_credentials"] = bool(r["has_credentials"])
                    item["residual"] = bool(r["still_enabled"] or r["has_credentials"])
                rows.append(item)
            out[table] = rows
        return out
    finally:
        conn.close()


def _dangling_needs_action(rows: list[dict]) -> list[dict]:
    """该表里「需处理」的行：消息行全算（批 E 口径＝物理删除），绑定行只算仍启用/仍持凭据的。"""
    return [r for r in rows if r.get("residual", True)]


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
        dangling = detect_dangling_character(args.db_path)
    except Exception as e:
        print("[ERROR] 检测失败：%s" % e, file=sys.stderr)
        return 1

    if args.json:
        import json
        print(json.dumps({"db": args.db_path, "orphans": orphans, "dangling_character": dangling},
                         ensure_ascii=False, indent=2))
        return 0

    if not orphans:
        print("[OK] 未发现孤儿微信绑定（所有 enabled=1 的 wechat_ilink_bindings 均有对应 channel_bindings 行）。")
    else:
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

    # ②批 E（2026-09-26）：渠道自有数据指向「已不存在的角色」——本批要长期盯的形态
    total = sum(len(rows) for rows in dangling.values())
    need = {t: _dangling_needs_action(rows) for t, rows in dangling.items()}
    need_total = sum(len(v) for v in need.values())
    if not total:
        print("[OK] 未发现指向已删角色的渠道数据（%s 0 行 / %s 0 行）。"
              % (WECHAT_TABLE, MESSAGE_TABLE))
        return 0
    if not need_total:
        print("[OK] 指向已删角色的 %d 行均为「已停用 + 凭据已清」留痕行（对齐解绑语义），符合批 E 口径。" % total)
        return 0
    print("[WARN] 指向已删角色的渠道数据共 %d 行，其中需处理 %d 行（角色行已硬删、数据还在）"
          % (total, need_total))
    for table in (WECHAT_TABLE, MESSAGE_TABLE):
        rows = dangling.get(table) or []
        kept = len(rows) - len(need.get(table, []))
        print("  %s: %d 行（需处理 %d%s）" % (
            table, len(rows), len(need.get(table, [])),
            " / 留痕保留 %d" % kept if kept else ""))
        for r in (need.get(table) or [])[:_DETAIL_LIMIT]:
            flag = ""
            if "still_enabled" in r:
                flag = "  enabled=%s 凭据残留=%s" % (r["still_enabled"], r["has_credentials"])
            print("    id=%-6s character=%-6s%s" % (r["id"], r["character_id"], flag))
        if len(need.get(table) or []) > _DETAIL_LIMIT:
            print("    …另有 %d 行未列出（全量见 --json）" % (len(need[table]) - _DETAIL_LIMIT))
    print()
    print("修复建议（人工确认后执行，本脚本不写库）：")
    print("  A. 今后删角色一律走 App/API（DELETE /api/v1/characters/{id}）——内核 notify_character_deleted")
    print("     会转调渠道 on_character_deleted：绑定行停用清凭据（留痕保留），消息历史物理删除；")
    print("  B. 存量按 character_id 收敛（等价于该回调的效果）：")
    print("       UPDATE wechat_ilink_bindings SET enabled=0, bot_token_enc='', ilink_bot_id='', baseurl='', poll_buf='' WHERE character_id=<id>;")
    print("       DELETE FROM wechat_ilink_messages WHERE character_id=<id>;")
    return 0


if __name__ == "__main__":
    sys.exit(main())
