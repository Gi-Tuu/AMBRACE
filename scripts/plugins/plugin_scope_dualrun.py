# -*- coding: utf-8 -*-
"""A2 M3 双跑校验（只读，2026-09-20）：证明「插件列表按账号收敛后，本账号该看到的插件一条不少」。

做法：对生产库每个账号（可 --user N 指定）分别算
    旧口径 = ``plugins`` 表全部行（= 改前 ``registry.list_plugins()`` 的全量口径）
    新口径 = 按 ``plugin_user_scope`` 谓词过滤后的可见集
比较 same / only_old / only_new；对 only_old 逐条标注该插件的
``owner_user_id`` / ``owner_tenant_id`` / ``source``。

判定：
- ``only_old`` 全部属于「非本租户安装的非内置插件」= PASS；
- 出现内置插件、本租户插件或服务级（owner 两列均为 NULL）插件丢失 = FAIL（口径写错）；
- 出现 ``only_new``（新口径多出旧口径没有的）= FAIL。

谓词单一真源：直接复用 ``app.plugins.registry.plugin_visible_to_tenant``，脚本不复制一份口径。

只读：sqlite 以 ``mode=ro`` 打开，不写库、不改文件、**不加载任何插件**（不执行插件代码，
也不触发 registry 的建表/收敛副作用）。

用法（项目根目录下）：
    backend\\.venv\\Scripts\\python.exe scripts\\plugins\\plugin_scope_dualrun.py --help
    backend\\.venv\\Scripts\\python.exe scripts\\plugins\\plugin_scope_dualrun.py
    backend\\.venv\\Scripts\\python.exe scripts\\plugins\\plugin_scope_dualrun.py --user 1 --user 6
"""
import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.plugins.registry import plugin_visible_to_tenant  # noqa: E402


def _db_path() -> str:
    """从 settings.database_url 推导 sqlite 文件路径（仅支持 sqlite；其它方言脚本拒绝跑）。"""
    url = str(getattr(settings, "database_url", "") or "")
    if "sqlite" not in url:
        raise SystemExit(f"[error] 本脚本只支持 sqlite 库，当前 database_url={url!r}")
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            return url[len(prefix):].replace("\\", "/")
    raise SystemExit(f"[error] 无法从 database_url 解析库路径：{url!r}")


def _ro_conn() -> sqlite3.Connection:
    path = _db_path()
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _load_plugins() -> list[dict]:
    """只读加载 plugins 表（name/source/owner 两列）；表缺失 → 空列表。"""
    conn = _ro_conn()
    try:
        rows = conn.execute(
            "SELECT name, source, owner_user_id, owner_tenant_id FROM plugins ORDER BY name"
        ).fetchall()
    except sqlite3.Error as e:
        print(f"[error] 读取 plugins 表失败：{e}", file=sys.stderr)
        rows = []
    finally:
        conn.close()
    return [
        {
            "name": str(r[0]),
            "source": str(r[1] or "builtin"),
            "owner_user_id": int(r[2]) if r[2] is not None else None,
            "owner_tenant_id": int(r[3]) if r[3] is not None else None,
        }
        for r in rows
    ]


def _load_family_roots() -> dict[int, int]:
    """只读加载 users(id -> parent_id)，返回 id -> 家庭根（parent_id 非空取 parent_id，否则取自己）。"""
    conn = _ro_conn()
    try:
        rows = conn.execute("SELECT id, parent_id FROM users ORDER BY id").fetchall()
    except sqlite3.Error as e:
        print(f"[error] 读取 users 表失败：{e}", file=sys.stderr)
        rows = []
    finally:
        conn.close()
    roots: dict[int, int] = {}
    for uid, parent_id in rows:
        roots[int(uid)] = int(parent_id) if parent_id else int(uid)
    return roots


def _classify(plugin: dict, root: int) -> str:
    """only_old 单条归因。

    lost_foreign        = 非本租户安装的非内置插件（PASS 允许丢失）
    lost_other_tenant   = owner_tenant_id 属于别的家庭（同上，语义化别名）
    lost_same_tenant    = 本家庭安装（不该丢 → FAIL）
    lost_builtin        = 内置（不该丢 → FAIL）
    lost_service_level  = 服务级/存量（owner 两列均 NULL，不该丢 → FAIL）
    lost_unknown        = 无 owner_tenant 但带 owner_user（无法证明是别家，按 FAIL 处理）
    """
    if plugin["source"] == "builtin":
        return "lost_builtin"
    if plugin["owner_user_id"] is None and plugin["owner_tenant_id"] is None:
        return "lost_service_level"
    if plugin["owner_tenant_id"] is not None and plugin["owner_tenant_id"] != root:
        return "lost_foreign"
    if plugin["owner_tenant_id"] is not None and plugin["owner_tenant_id"] == root:
        return "lost_same_tenant"
    return "lost_unknown"


def _verdict_of(counts: dict) -> str:
    fail_keys = ("lost_builtin", "lost_same_tenant", "lost_service_level", "lost_unknown", "only_new")
    return "FAIL" if any(counts.get(k) for k in fail_keys) else "PASS"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="A2 M3 插件可见性双跑校验（只读；判定 only_old 是否全部为跨租户插件）")
    parser.add_argument("--user", type=int, action="append", default=None,
                        help="只校验指定账号 id（可重复；默认全部账号）")
    parser.add_argument("--max-users", type=int, default=0, help="最多校验几个账号（0=全部）")
    parser.add_argument("--show-same", action="store_true",
                        help="同时打印 same（两口径都可见）的插件名")
    parser.add_argument("--show-only-new", action="store_true",
                        help="打印 only_new（新口径多出）的插件名；配合 --show-same 排查口径")
    args = parser.parse_args(argv)

    plugins = _load_plugins()
    roots = _load_family_roots()
    print(f"[dualrun] db={_db_path()} plugins={len(plugins)} accounts={len(roots)} "
          "predicate=app.plugins.registry.plugin_visible_to_tenant")

    users = list(dict.fromkeys(args.user)) if args.user else sorted(roots)
    if args.max_users and len(users) > args.max_users:
        users = users[:args.max_users]

    tot = {"accounts": 0, "plugins_total": 0, "same": 0, "only_old": 0, "only_new": 0,
           "lost_builtin": 0, "lost_same_tenant": 0, "lost_service_level": 0,
           "lost_foreign": 0, "lost_unknown": 0}
    print("user | tenant | plugins | same | only_old | only_new | lost_builtin | "
          "lost_same_tenant | lost_service | verdict")

    for uid in users:
        root = roots.get(uid)
        if root is None:
            print(f"{uid} | - | - | - | - | - | - | - | - | SKIP(no user row)")
            continue
        old = [p["name"] for p in plugins]
        new = [p["name"] for p in plugins if plugin_visible_to_tenant(
            source=p["source"], owner_user_id=p["owner_user_id"],
            owner_tenant_id=p["owner_tenant_id"], viewer_tenant_id=root,
        )]
        o, n = set(old), set(new)
        same, only_old, only_new = sorted(o & n), sorted(o - n), sorted(n - o)

        counts = {"only_new": len(only_new), "lost_builtin": 0, "lost_same_tenant": 0,
                  "lost_service_level": 0, "lost_foreign": 0, "lost_unknown": 0}
        by_name = {p["name"]: p for p in plugins}
        for name in only_old:
            p = by_name[name]
            kind = _classify(p, root)
            counts[kind] = counts.get(kind, 0) + 1
            print(f"    only_old: {name} [source={p['source']} "
                  f"owner_user_id={p['owner_user_id']} owner_tenant_id={p['owner_tenant_id']}] "
                  f"-> {kind}")
        for name in only_new if args.show_only_new else []:
            print(f"    only_new: {name}")
        if args.show_same:
            print(f"    same: {', '.join(same) if same else '(空)'}")

        verdict = _verdict_of(counts)
        tot["accounts"] += 1
        tot["plugins_total"] += len(old)
        for k in ("same", "only_old", "only_new", "lost_builtin", "lost_same_tenant",
                  "lost_service_level", "lost_foreign", "lost_unknown"):
            if k in ("same", "only_old"):
                tot[k] += len(same) if k == "same" else len(only_old)
            else:
                tot[k] += counts.get(k, 0)
        print(f"{uid} | {root} | {len(old)} | {len(same)} | {len(only_old)} | {len(only_new)} | "
              f"{counts['lost_builtin']} | {counts['lost_same_tenant']} | "
              f"{counts['lost_service_level']} | {verdict}")

    fail_keys = ("lost_builtin", "lost_same_tenant", "lost_service_level", "lost_unknown", "only_new")
    verdict = "PASS" if not any(tot[k] for k in fail_keys) else "FAIL"
    print("-" * 72)
    print(f"TOTAL accounts={tot['accounts']} plugins_total={tot['plugins_total']} "
          f"same={tot['same']} only_old={tot['only_old']} only_new={tot['only_new']}")
    print(f"only_old 归因：跨租户非内置={tot['lost_foreign']} 内置={tot['lost_builtin']} "
          f"本租户={tot['lost_same_tenant']} 服务级={tot['lost_service_level']} "
          f"无法归类={tot['lost_unknown']}")
    print(f"VERDICT: {verdict}")
    return 0 if verdict == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
