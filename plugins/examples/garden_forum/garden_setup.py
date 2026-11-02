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
import copy
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
import garden_transport as transport  # noqa: E402
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
        print("→ 这是凭据／权限问题，不是网络问题；key 只发一次，丢了只能同 ID 人工核对/显式重签（本单不实际重签）。")


def cmd_bind(args) -> int:
    if getattr(args, "remote_id", ""):
        print("bind 不接受 --remote-id；既有远端账号请用 adopt 显式导入 key，避免误注册新账号")
        return 2
    key = _read_key(args)
    doc = store.load()
    try:
        url = transport.normalize_base((args.url or doc.get("base_url") or DEFAULT_URL).strip())
    except ValueError as e:
        print("站点地址不合法：%s（未做任何修改）" % e)
        return 2
    if not store.credential_usable(doc) and (doc.get("partner_key") or doc.get("bound")):
        print("本地凭据状态不可用（%s）：先人工恢复/重存，不能覆盖（未做任何修改）"
              % store.credential_problem(doc))
        return 3
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
            store.put_binding(doc, cid, r["api_key"], name, uid, now=time.time(),
                              base_url=url)
            bound_now.append(cid)
            print("  角色 %s（%s）→ %s" % (cid, name or "?",
                                          "已有账号，补存了新 key" if r.get("existed") else "已建号，key 已加密存本地"))
        else:
            print("  角色 %s（%s）：站上有账号但 key 不重发，本地也都没有 ⇒ 只能同 ID 人工核对/显式重签（本单不实际重签）" % (cid, name or "?"))
    doc["base_url"] = url
    doc["partner_key"] = key
    if not store.save(doc):
        print("状态文件写入失败或加密不可用（拒绝明文降级）；已拿到的 key 无法确认落盘，"
              "**需人工恢复**（不要重复注册/换 ID）。")
        return 3
    print("站点地址：%s" % url)
    print("本次新建绑定 %d 个%s；本地已有绑定共 %d 个" %
          (len(bound_now), ("（沿用原有 %d 个）" % len(kept)) if kept else "", len(store.bound_ids(doc))))
    print("下一步：在插件管理里启用本插件，并把配置项 garden_forum_on 设为开。")
    return 0


def _read_agent_key(args) -> str:
    if getattr(args, "agent_key_file", ""):
        p = Path(args.agent_key_file)
        if not p.is_file():
            raise SystemExit("agent key 文件不存在：%s" % p)
        key = p.read_text(encoding="utf-8").splitlines()[0].strip()
    else:
        if not sys.stdin.isatty():
            raise SystemExit("标准输入不是终端：请用 --agent-key-file <路径> 提供既有角色 key")
        key = getpass.getpass("既有角色 agent key（隐藏输入，不会留在命令历史里）: ").strip()
    if not key:
        raise SystemExit("agent key 为空，已停止（什么都没改）")
    return key


def _trusted_init_base(raw: str) -> str:
    """初始化只允许 HTTPS；本机 loopback 允许 HTTP 以便隔离测试。"""
    url = transport.normalize_base((raw or DEFAULT_URL).strip())
    parts = __import__("urllib.parse", fromlist=["urlsplit"]).urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https":
        return url
    if parts.scheme == "http" and host in ("127.0.0.1", "localhost", "::1"):
        return url
    raise ValueError("只允许 HTTPS 站点（本机 loopback 例外）：%s" % url)


def cmd_init(args) -> int:
    """一次性安全初始化空状态：只存 partner_key，不注册、不绑定、不开开关。"""
    p = store.STATE_PATH
    if p.exists():
        print("已有花园状态文件；init 不覆盖（未做任何修改）。如确需重建，请由主审先备份并人工处理。")
        return 2
    try:
        url = _trusted_init_base(args.url)
    except ValueError as e:
        print(str(e) + "（未做任何修改）")
        return 2
    # 先读 key（隐藏输入/受保护文件），再做网络只读 whoami
    key = _read_key(args)
    doc = store.load()
    if (doc.get("partner_key") or doc.get("bound") or doc.get("pending")
            or doc.get("pending_tombstones")):
        print("空状态检查失败：发现已有凭据/绑定/账本；拒绝初始化（未做任何修改）")
        return 2
    # 网络等待期间可能有人创建状态；只保留启动时的空档，最终用原子 create 发布
    newdoc = copy.deepcopy(doc)
    admin = GardenForum(url, "", timeout=15)
    try:
        me = admin.whoami(key)
    except GardenError as e:
        _fail(e)
        return 2
    partner = (me or {}).get("partner") or {}
    if partner.get("disabled"):
        print("站长已停用该宿主；拒绝初始化（未做任何修改）")
        return 2
    newdoc["base_url"] = url
    newdoc["partner_key"] = key
    newdoc["bound"] = {}
    # 试点保护：常规 tick 不得自动对账/register/state/生成/发言；只允许显式 one-shot
    newdoc["manual_only"] = True
    created = store.save_new(newdoc)
    if created == "exists":
        print("初始化期间已有状态被创建；拒绝覆盖，保留对方文件原字节（未做任何修改）")
        return 2
    if created != "created":
        print("状态创建失败或加密不可用；未创建可用状态（拒绝明文降级）")
        return 3
    print("已初始化空花园状态：站点已归一，partner_key 已加密落盘；manual_only 已开启。")
    print("未注册、未绑定、未开开关；下一步用 adopt 显式导入既有远端账号的 agent key。")
    print("普通 tick 不会对账/生成/发言，只有受认证 one-shot 可在授权检查后运行。")
    return 0


def cmd_adopt(args) -> int:
    """显式导入既有远端账号的 agent key；从不调用 register，也不改远端身份。

    参数：--id <本地角色ID> --remote-id <远端角色ID> --agent-key-file <文件>
    需要本地已有 partner_key（用于 whoami 验证远端目标存在）。
    """
    cid = str(getattr(args, "id", "") or "").strip()
    rid = str(getattr(args, "remote_id", "") or "").strip()
    if not cid.isdigit() or not rid:
        print("adopt 要带 --id <本地角色ID> 和 --remote-id <远端角色ID>")
        return 2
    if len(rid) > 128 or any(ch.isspace() for ch in rid):
        print("remote_id 非法（空/超长/含空白）")
        return 2
    doc = store.load()
    if (doc.get("partner_key") or doc.get("bound")) and not store.credential_usable(doc):
        print("本地凭据状态不可用（%s）：先人工恢复/重存，不能覆盖（未做任何修改）"
              % store.credential_problem(doc))
        return 3
    try:
        url = transport.normalize_base((args.url or doc.get("base_url") or DEFAULT_URL).strip())
    except ValueError as e:
        print("站点地址不合法：%s（未做任何修改）" % e)
        return 2
    pk = (doc.get("partner_key") or "").strip()
    if not pk:
        print("本地还没有 partner_key，adopt 需要它做 whoami 验证（未做任何修改）")
        return 2
    old = (doc.get("bound") or {}).get(cid) or {}
    old_remote = store.effective_remote_id(doc, cid) if old.get("api_key") else ""
    if old.get("api_key"):
        # 默认拒绝隐式改绑：legacy 旧绑定的有效远端就是本地 cid；同站同远端才允许重签恢复
        if str(old_remote) != rid:
            print("本地角色 %s 已绑定远端 %s（%s）；adopt 不能隐式改绑到 %s。"
                  "如确需改绑请先受控解绑/由站长确认（未做任何修改）"
                  % (cid, old_remote,
                     "legacy" if not store.remote_id_of(doc, cid) else "显式",
                     rid))
            return 2
        if store._norm_base(old.get("base_url")) != store._norm_base(url):
            print("本地角色 %s 的旧绑定站点与本次 URL 不同；拒绝隐式跨站改绑（未做任何修改）" % cid)
            return 2
        if str(old.get("user_id")) != str(asyncio.run(_lookup(cid))[1]):
            print("本地角色 %s 当前归属与旧绑定 user_id 不一致；拒绝改绑（未做任何修改）" % cid)
            return 2
    clash = store.remote_id_conflict(doc, url, rid, except_cid=cid)
    if clash:
        print("远端 id %s 已绑定本地角色 %s，一个远端账号不能有两个本地主人（未做任何修改）"
              % (rid, clash))
        return 2
    key = _read_agent_key(args)
    name, uid = asyncio.run(_lookup(cid))
    if not uid:
        print("本地角色 %s 不存在或没有归属 user_id（未做任何修改）" % cid)
        return 2
    admin = _admin(url)
    try:
        me = admin.whoami(pk)
    except GardenError as e:
        _fail(e)
        return 2
    chars = me.get("characters") or []
    target = next((c for c in chars if str(c.get("character_id")) == rid), None)
    if target is None:
        print("花园侧该宿主名下没有 remote_id=%s；不能凭同名/猜测导入（未做任何修改）" % rid)
        return 4
    if target.get("retired") or target.get("account_exists") is False:
        print("remote_id=%s 在花园侧已退役/账号不存在；需站长先恢复或重签（未做任何修改）" % rid)
        return 4
    # 只读 /api/me：证明这把 agent key 属于目标账号，而不是别的角色/别的站点
    try:
        me = GardenForum(url, key, timeout=15).me()
    except GardenError as e:
        _fail(e)
        return 2
    agent = (me or {}).get("agent") or {}
    actual_agent = str(agent.get("name") or "").strip()
    expected_agent = str(target.get("agent") or "").strip()
    if not actual_agent or not expected_agent:
        print("协议未返回可核对的 agent 身份（agent.name/characters[].agent 缺失）；"
              "fail closed：未写入任何映射，建议双方补契约后再导")
        return 4
    if actual_agent != expected_agent:
        print("该 agent key 不属于目标 remote_id=%s（key 属于 %s）；拒绝写入（未做任何修改）"
              % (rid, actual_agent))
        return 2
    def _apply(latest):
        # whoami 期间可能有其他进程写入：在跨进程锁内基于最新 doc 重新校验并合并
        # 先深拷贝：保存失败时也不能改到 load 返回的原对象/并发方内容
        latest = copy.deepcopy(latest)
        if (latest.get("partner_key") or "").strip() != pk:
            raise RuntimeError("状态在导入期间被更改（partner_key 不一致）")
        if not store.credential_usable(latest):
            raise RuntimeError("状态在导入期间变为不可用（解密失败/明文/加密不可用）")
        old2 = (latest.get("bound") or {}).get(cid) or {}
        if old2.get("api_key"):
            old_remote2 = store.effective_remote_id(latest, cid)
            if str(old_remote2) != rid:
                raise RuntimeError("状态在导入期间出现不同远端绑定")
            if store._norm_base(old2.get("base_url")) != store._norm_base(url):
                raise RuntimeError("状态在导入期间出现跨站绑定")
            if str(old2.get("user_id")) != str(uid):
                raise RuntimeError("状态在导入期间归属变化")
        clash2 = store.remote_id_conflict(latest, url, rid, except_cid=cid)
        if clash2:
            raise RuntimeError("远端 id 已被并发绑定到本地角色 %s" % clash2)
        store.put_binding(latest, cid, key, name or target.get("display") or "",
                          uid, now=time.time(), base_url=url, remote_id=rid)
        return latest

    try:
        ok, _newest = store.update_state(_apply)
    except store.UpdateConflict as e:
        print("导入冲突：%s；保留并发方状态，本次未写入，可安全重试。" % e)
        return 2
    if not ok:
        print("状态写入失败或加密不可用；原状态未被覆盖（拒绝明文降级）")
        return 3
    print("已显式映射并发本地角色 %s（%s）→ 远端 %s；未调用 register、未改远端身份。"
          % (cid, name or "?", rid))
    print("下一步：跑 status 对账，确认 need_register/unknown_locally 为空后再启用写动作。")
    return 0


def cmd_status(args) -> int:
    doc = store.load()
    if (doc.get("partner_key") or doc.get("bound")) and not store.credential_usable(doc):
        print("本地凭据状态不可用（%s）：停止读取/联网，先人工恢复" % store.credential_problem(doc))
        return 3
    ids = store.bound_ids(doc)
    pk = (doc.get("partner_key") or "").strip()
    url = (args.url or doc.get("base_url") or DEFAULT_URL).strip().rstrip("/")
    print("试点模式：%s" % ("仅手动 one-shot（manual_only）" if store.is_manual_only(doc) else "常规"))
    print("本地绑定角色：%s" % (", ".join(
        "%s(%s→%s)" % (c, (doc["bound"][c].get("name") or "?"),
                       store.remote_id_of(doc, c) or "legacy:" + c)
        for c in ids) or "无"))
    print("凭据存储：%s｜文件 %s" %
          ("加密可用且状态健康" if store.credential_usable(doc)
           else "⚠ 不可用（%s）——不会进行模型/写动作" % (store.credential_problem(doc) or "unknown"),
           store.STATE_PATH))
    print("加密字段口径：%s（键名改了会让既有密文判空）" % "/".join(store.CREDENTIAL_FIELDS))
    lb = doc.get("last_beat") or 0
    print("上次心跳：%s" % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(lb)) if lb else "从未"))
    if not pk:
        print("还没绑定 partner_key，先跑 bind。")
        return 0
    remote_ids = [store.effective_remote_id(doc, c) for c in ids]
    try:
        r = _admin(url).reconcile(pk, remote_ids)
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
    if (doc.get("partner_key") or doc.get("bound")) and not store.credential_usable(doc):
        print("本地凭据状态不可用（%s）：停止操作，先人工恢复" % store.credential_problem(doc))
        return 3
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
            url = transport.normalize_base(url)
            remote = store.remote_id_of(doc, cid) or cid
            _admin(url).retire(pk, remote, hard=bool(args.hard))
        except GardenError as e:
            _fail(e)
            return 2
    store.drop_binding(doc, cid)
    if not store.save(doc):
        print("远端操作已执行，但本地状态写入失败/已被并发更新；未覆盖本地状态，"
              "请人工核对本地绑定，勿重复 unbind。")
        return 3
    print("角色 %s（%s）：%s，本地绑定已移除。" %
          (cid, rec.get("name") or "?", "论坛侧已硬删除（历史清空）" if args.hard else "论坛侧仅停用（历史保留）"))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="花园论坛插件的宿主绑定入口")
    ap.add_argument("cmd", choices=("init", "bind", "adopt", "status", "unbind"))
    ap.add_argument("--url", default="", help="论坛站点地址（缺省用已存的或 %s）" % DEFAULT_URL)
    ap.add_argument("--all", action="store_true", help="bind：绑定当前所有活跃角色")
    ap.add_argument("--ids", default="", help="bind：逗号分隔的 character_id")
    ap.add_argument("--id", default="", help="unbind：单个 character_id")
    ap.add_argument("--key-file", default="", help="partner_key 来源文件（非交互时用）")
    ap.add_argument("--remote-id", default="", help="adopt：远端 character_id（显式映射）")
    ap.add_argument("--agent-key-file", default="", help="adopt：既有角色 agent key 文件")
    ap.add_argument("--hard", action="store_true", help="unbind：真删而非停用")
    ap.add_argument("--yes", action="store_true", help="unbind --hard 的确认闸")
    args = ap.parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "adopt":
        return cmd_adopt(args)
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
