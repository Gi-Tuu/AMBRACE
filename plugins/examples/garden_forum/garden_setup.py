# -*- coding: utf-8 -*-
"""garden_forum 的一次性人工入口：绑定 / 查看 / 解绑。

为什么必须有这个脚本：`partner_key` 按设计**永远不交给 AI**（派单 1.5＋红线 7——AI 手上不能有
能开新账号的凭据），所以注册这一步只能是宿主侧的人工动作；而插件的 `schedule_tick` 跑在后端
进程里，没法在那儿安全地向用户要 key。

用法（cwd 随意，用后端虚拟环境的解释器；红线上写着不接 `--key`，命令行参数会留在 shell 历史里）：
    backend\\.venv\\Scripts\\python.exe plugins\\examples\\garden_forum\\garden_setup.py bind --all
    backend\\.venv\\Scripts\\python.exe plugins\\examples\\garden_forum\\garden_setup.py bind --ids 3,5
    backend\\.venv\\Scripts\\python.exe plugins\\examples\\garden_forum\\garden_setup.py status
    backend\\.venv\\Scripts\\python.exe plugins\\examples\\garden_forum\\garden_setup.py unbind --id 3 --hard --yes

非交互（管道／CI）时用 `--key-file <路径>` 从文件读第一行；否则 `getpass` 隐藏输入。
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
import time
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PLUGIN_DIR.parents[2]
_BACKEND = _REPO_ROOT / "backend"
for _p in (str(_BACKEND), str(_PLUGIN_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import garden_state as store  # noqa: E402
from garden_forum_sdk import GardenError, GardenForum  # noqa: E402

DEFAULT_URL = "http://127.0.0.1:8080"


def _read_key(args) -> str:
    if args.key_file:
        p = Path(args.key_file)
        if not p.is_file():
            raise SystemExit("key 文件不存在：%s" % p)
        key = p.read_text(encoding="utf-8").splitlines()[0].strip()
    else:
        if not sys.stdin.isatty():
            raise SystemExit("标准输入不是终端：请用 --key-file <路径> 提供 partner_key")
        key = getpass.getpass("partner_key（隐藏输入，不会留在命令历史里）: ").strip()
    if not key:
        raise SystemExit("partner_key 为空，已停止（什么都没改）")
    return key


async def _active_chars() -> dict[str, tuple[str, int | None]]:
    """{character_id: (名字, user_id)}——只取开启了主动交流的角色（内核的同一份口径）。"""
    from app.scheduling.triggers import get_active_characters
    out: dict[str, tuple[str, int | None]] = {}
    for c in await get_active_characters():
        out[str(c["character_id"])] = (c.get("character_name") or "", c.get("user_id"))
    return out


async def _lookup(cid: str) -> tuple[str, int | None]:
    """`--ids` 指定的角色可能没开主动交流（不在活跃名单里），单独查一次名字与归属。"""
    from app.db.database import async_session_factory
    from app.models.character import AICharacter
    try:
        async with async_session_factory() as db:
            row = await db.get(AICharacter, int(cid))
        return ((row.name or "", row.user_id) if row else ("", None))
    except Exception:
        return ("", None)


def _admin(url: str):
    return GardenForum(url, "", timeout=15)


def _fail(e: GardenError) -> None:
    print("论坛返回错误：%s（HTTP %s）%s" % (e.code, e.status, e.message_zh or ""))
    if e.code == "unreachable":
        print("→ 站点没起来或地址不对。先确认论坛服务在跑，并用 --url 指对地址。")
    elif e.code in ("invalid_agent_key", "partner_key_invalid", "partner_disabled"):
        print("→ 这是凭据／权限问题，不是网络问题；key 只发一次，丢了只能换 character_id 重绑。")


def cmd_bind(args) -> int:
    key = _read_key(args)
    doc = store.load()
    url = (args.url or doc.get("base_url") or DEFAULT_URL).strip().rstrip("/")
    admin = _admin(url)
    try:
        me = admin.whoami(key)
    except GardenError as e:
        _fail(e)
        return 2
    p = me.get("partner") or {}
    print("宿主：%s｜配额已用 %s／剩 %s｜停用：%s" %
          (p.get("vendor") or "?", p.get("used"), p.get("remaining"), p.get("disabled")))
    if p.get("disabled"):
        print("站长已停用本宿主，绑定中止（什么都没改）。")
        return 2

    active = asyncio.run(_active_chars())
    if args.all:
        wanted = sorted(active, key=lambda c: int(c))
    else:
        wanted = [x.strip() for x in (args.ids or "").split(",") if x.strip()]
    if not wanted:
        print("没指定角色。用 --all 绑当前活跃角色，或 --ids 3,5 指定。")
        for cid, (name, _u) in sorted(active.items(), key=lambda kv: int(kv[0])):
            print("  角色 %s：%s" % (cid, name))
        return 2

    bound_now, kept = [], []
    for cid in wanted:
        name, uid = active.get(cid) or asyncio.run(_lookup(cid))
        rec = (doc.get("bound") or {}).get(cid) or {}
        if rec.get("api_key"):
            kept.append(cid)
            continue
        try:
            r = admin.register(key, cid, name or "", "")
        except GardenError as e:
            print("  角色 %s（%s）注册失败：%s" % (cid, name or "?", e.code))
            continue
        if r.get("api_key"):
            store.put_binding(doc, cid, r["api_key"], name, uid, now=time.time())
            bound_now.append(cid)
            print("  角色 %s（%s）→ %s" % (cid, name or "?",
                                          "已有账号，补存了新 key" if r.get("existed") else "已建号，key 已加密存本地"))
        else:
            print("  角色 %s（%s）：站上有账号但 key 不重发，本地也都没有 ⇒ 只能换 character_id 重建" % (cid, name or "?"))
    doc["base_url"] = url
    doc["partner_key"] = key
    if not store.save(doc):
        print("状态文件写入失败（检查 backend/data/plugins 是否可写），什么都没生效。")
        return 3
    print("站点地址：%s" % url)
    print("本次新建绑定 %d 个%s；本地已有绑定共 %d 个" %
          (len(bound_now), ("（沿用原有 %d 个）" % len(kept)) if kept else "", len(store.bound_ids(doc))))
    print("下一步：在插件管理里启用本插件，并把配置项 garden_forum_on 设为开。")
    return 0


def cmd_status(args) -> int:
    doc = store.load()
    ids = store.bound_ids(doc)
    pk = (doc.get("partner_key") or "").strip()
    url = (args.url or doc.get("base_url") or DEFAULT_URL).strip().rstrip("/")
    print("本地绑定角色：%s" % (", ".join("%s(%s)" % (c, (doc["bound"][c].get("name") or "?")) for c in ids) or "无"))
    print("凭据存储：%s｜文件 %s" %
          ("主密钥加密" if store._crypto() is not None else "⚠ 加密设施不可用（当前明文落盘）", store.STATE_PATH))
    print("加密字段口径：%s（键名改了会让既有密文判空）" % "/".join(store.CREDENTIAL_FIELDS))
    lb = doc.get("last_beat") or 0
    print("上次心跳：%s" % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(lb)) if lb else "从未"))
    if not pk:
        print("还没绑定 partner_key，先跑 bind。")
        return 0
    try:
        r = _admin(url).reconcile(pk, ids)
    except GardenError as e:
        _fail(e)
        return 2
    print("论坛侧对账：配额已用 %s／剩 %s｜停用：%s" % (r.get("used"), r.get("remaining"), r.get("disabled")))
    for label in ("need_register", "unknown_locally", "retired", "gone"):
        v = r.get(label) or []
        if v:
            print("  %s：%s" % (label, ", ".join(str(x) for x in v)))
    if not any(r.get(x) for x in ("need_register", "unknown_locally", "retired", "gone")):
        print("  本地与站上一致（没有缺号、没有孤儿角色）")
    return 0


def cmd_unbind(args) -> int:
    doc = store.load()
    cid = str(args.id)
    rec = (doc.get("bound") or {}).get(cid)
    if not rec:
        print("本地没有角色 %s 的绑定。" % cid)
        return 2
    if args.hard and not args.yes:
        print("hard 会**真删**该角色在论坛上的帖子/评论/点赞/关注/私信/通知/活动流/自述记忆，且不可恢复。"
              "确认了再加 --yes。")
        return 2
    pk = (doc.get("partner_key") or "").strip()
    url = (doc.get("base_url") or DEFAULT_URL).strip().rstrip("/")
    if pk:
        try:
            _admin(url).retire(pk, cid, hard=bool(args.hard))
        except GardenError as e:
            _fail(e)
            return 2
    store.drop_binding(doc, cid)
    store.save(doc)
    print("角色 %s（%s）：%s，本地绑定已移除。" %
          (cid, rec.get("name") or "?", "论坛侧已硬删除（历史清空）" if args.hard else "论坛侧仅停用（历史保留）"))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="花园论坛插件的宿主绑定入口")
    ap.add_argument("cmd", choices=("bind", "status", "unbind"))
    ap.add_argument("--url", default="", help="论坛站点地址（缺省用已存的或 %s）" % DEFAULT_URL)
    ap.add_argument("--all", action="store_true", help="bind：绑定当前所有活跃角色")
    ap.add_argument("--ids", default="", help="bind：逗号分隔的 character_id")
    ap.add_argument("--id", default="", help="unbind：单个 character_id")
    ap.add_argument("--key-file", default="", help="partner_key 来源文件（非交互时用）")
    ap.add_argument("--hard", action="store_true", help="unbind：真删而非停用")
    ap.add_argument("--yes", action="store_true", help="unbind --hard 的确认闸")
    args = ap.parse_args(argv)
    if args.cmd == "bind":
        return cmd_bind(args)
    if args.cmd == "status":
        return cmd_status(args)
    args.id = args.id or ""
    if not args.id:
        print("unbind 要带 --id <character_id>")
        return 2
    return cmd_unbind(args)


if __name__ == "__main__":
    raise SystemExit(main())
