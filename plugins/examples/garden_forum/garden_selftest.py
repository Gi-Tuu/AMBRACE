# -*- coding: utf-8 -*-
"""garden_forum 的离线守卫（零网络、零计费、零 DB）。

为什么放在插件目录而不是 `backend/tests/`：这一单的红线是「只碰 `plugins/garden_forum/`，不改任何
现有文件」，新增测试文件虽然不算改，但它会进全量套件与 CI 的采集面。所以守卫先落在插件内，
需要进 CI 时说一句即可搬走。

跑法（cwd 随意）：
    backend\\.venv\\Scripts\\python.exe plugins\\examples\\garden_forum\\garden_selftest.py
退出码 0＝全过；非 0＝打印了哪条没过。

覆盖三层：`garden_beat`（纯决策）、`garden_state`（凭据与去重账本）、`main.py` 的接线锚点
（`main.py` 一 import 就会执行 `@sdk.hook`，在插件上下文外**根本没法 import**，所以只能用源码锚点核）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PLUGIN_DIR.parents[2]
for _p in (str(_REPO_ROOT / "backend"), str(_PLUGIN_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import garden_beat as beat  # noqa: E402
import garden_state as store  # noqa: E402

# 日志里绝对不该出现的东西：两把凭据的本体（`pk` 是 partner_key 在那个作用域里的变量名）
SECRET_SUBSTRINGS = ("api_key", "partner_key")

FAILS: list[str] = []
OKS = 0


def check(label: str, cond, *detail) -> None:
    global OKS
    if cond:
        OKS += 1
    else:
        FAILS.append("❌ %s %s" % (label, " ".join(str(d) for d in detail)))


def t_next_wait():
    check("next_wait：论坛回 0 也不能比配置更快", beat.next_wait(0, 900) == 900, beat.next_wait(0, 900))
    check("next_wait：采纳论坛建议（900s）", beat.next_wait(900, 300) == 900)
    check("next_wait：建议比配置小 ⇒ 用配置", beat.next_wait(30, 900) == 900)
    check("next_wait：建议荒谬地大 ⇒ 夹到 6 小时", beat.next_wait(10 ** 9, 900) == 6 * 3600)
    check("next_wait：建议是坏值 ⇒ 不炸，用配置", beat.next_wait("x", 900) == 900)
    check("next_wait：下限 60 秒", beat.next_wait(None, 1) == 60)


def t_due_and_clamp():
    check("due：从没跑过＝到点", beat.due(1000.0, 0, 900))
    check("due：排好的时刻刚到＝到点（间隔已在 next_wait 里）", beat.due(1900.0, 1900.0, 900))
    check("due：还差一秒没到＝不做事", not beat.due(1899.0, 1900.0, 900))
    check("due：排定时刻已过 400 秒就该去（旧写法要等满两个间隔＝配 15 分钟实走 30 分钟）",
          beat.due(2300.0, 1900.0, 900))
    check("clamp_minutes：太小回到下限 3", beat.clamp_minutes(1) == 3)
    check("clamp_minutes：太大回到上限 120", beat.clamp_minutes(999) == 120)
    check("clamp_minutes：空串回默认", beat.clamp_minutes("") == 15)
    check("clamp_minutes：None 回默认", beat.clamp_minutes(None) == 15)
    check("clamp_minutes：字符串数字照收", beat.clamp_minutes("20") == 20)


def t_picks():
    posts = [{"id": 7}, {"id": 8}, {"id": 9}, {"title": "没有 id"}]
    check("pick_reads：跳过近期读过的", beat.pick_reads(posts, ["8"], 5) == [7, 9])
    check("pick_reads：cap 生效", beat.pick_reads(posts, [], 2) == [7, 8])
    check("pick_reads：cap=0 就一条都不报", beat.pick_reads(posts, [], 0) == [])
    check("pick_reads：没 id 的条目不进来", 9 not in beat.pick_reads(posts[:3] + [{"title": "x"}], [7, 8, 9], 5))
    reps = [{"id": 3, "post_id": 11, "body": "…"}, {"id": 4}, {"id": 6, "post_id": 11}]
    check("pick_reply_target：要同时带 post_id 与自身 id", beat.pick_reply_target(reps)["post_id"] == 11)
    check("pick_reply_target：只有 parent 没 post_id 的会被跳过（否则会 400）",
          beat.pick_reply_target([{"id": 4}, {"id": 5, "post_id": 12}])["id"] == 5)
    check("pick_reply_target：空＝None", beat.pick_reply_target([]) is None)
    check("pick_reply_target：模型已经沉默过的留言不再重问（换下一条）",
          beat.pick_reply_target(reps, ["3"])["id"] == 6)
    check("pick_reply_target：全部问过＝None（这一拍没话可回，私信才轮得到）",
          beat.pick_reply_target(reps, ["3", "6"]) is None)
    dms = [{"id": 1, "from_agent": "a"}, {"id": 2, "from_agent": ""}, {"id": 3}]
    check("pick_dm_target：跳过发信人为空与没 id 的", [d["id"] for d in beat.pick_dm_target(dms, [])] == [1])
    check("pick_dm_target：已见过的不再烧 token", beat.pick_dm_target(dms, ["1"]) == [])


def t_parse_reply():
    check("parse_reply：无＝沉默", beat.parse_reply("无") == "")
    check("parse_reply：带空格的『无』也是沉默", beat.parse_reply("  无 \n") == "")
    check("parse_reply：EMPTY/skip 都算沉默", beat.parse_reply("EMPTY") == "" and beat.parse_reply("skip") == "")
    check("parse_reply：**「无所谓…」不能被误杀成沉默**",
          beat.parse_reply("无所谓，明天再试一次") != "")
    check("parse_reply：角括号包裹要剥掉", beat.parse_reply("「陶盆蒸发快一倍」") == "陶盆蒸发快一倍")
    check("parse_reply：代码围栏要剥掉", "hello" in beat.parse_reply("```text\nhello world\n```"))
    check("parse_reply：单字不成话＝沉默", beat.parse_reply("好") == "")
    check("parse_reply：None/非字符串＝沉默", beat.parse_reply(None) == "" and beat.parse_reply(3) == "")
    check("parse_reply：长文截到 500 字", len(beat.parse_reply("句" * 900)) == 500)
    check("parse_reply：压掉空行", "\n\n" not in beat.parse_reply("第一段\n\n\n第二段"))


def t_error_class():
    check("错误分档：站点不通＝结束整轮", beat.stop_beat_on_error("unreachable"))
    check("错误分档：宿主被停用＝结束整轮", beat.stop_beat_on_error("partner_disabled"))
    check("错误分档：key 失效＝只算这个角色的绑定坏了", beat.binding_broken("invalid_agent_key"))
    check("错误分档：互动上限不该停整轮",
          not beat.stop_beat_on_error("peer_cap") and not beat.binding_broken("peer_cap"))
    check("错误分档：未知码默认不扩大影响面", not beat.stop_beat_on_error(""))


def t_messages():
    item = {"id": 3, "post_id": 11, "author": "taisheng", "body": "土表干不等于里面干", "title": "浇水"}
    blob = json.dumps(beat.build_messages({"name": "小叶", "personality": "少话"}, "", "comment", item),
                      ensure_ascii=False)
    check("提示词：必须写明沉默合法", "只输出一个字符：无" in blob)
    check("提示词：内容本身要进去", "土表干不等于里面干" in blob)
    check("提示词：人设要进去", "少话" in blob)
    for kind, item2 in (("comment", item), ("dm", {"id": 1, "from_agent": "someone", "body": "在吗"})):
        blob2 = json.dumps(beat.build_messages({"name": "小叶"}, "", kind, item2), ensure_ascii=False)
        check("提示词：两种场景都不许带论坛契约（红线 5）",
              "/api/" not in blob2 and "act." not in blob2, kind)
    dm_blob = json.dumps(beat.build_messages({"name": "小叶"}, "", "dm",
                                             {"id": 1, "from_agent": "someone", "body": "在吗"}),
                         ensure_ascii=False)
    check("提示词：私信用发信人称谓而不是帖子标题", "someone" in dm_blob and "《》" not in dm_blob)
    tone = json.dumps(beat.build_messages({"name": "小叶"}, "quiet", "dm",
                                           {"from_agent": "s", "body": "x"}), ensure_ascii=False)
    check("提示词：语气卡按配置拼上", "你说话很少" in tone)
    bad = json.dumps(beat.build_messages({"name": "小叶"}, "不存在的语气", "dm",
                                         {"from_agent": "s", "body": "x"}), ensure_ascii=False)
    check("提示词：未知语气不炸也不静塞一张卡", "你说话很少" not in bad)


def t_state():
    with tempfile.TemporaryDirectory() as td:
        key_file = Path(td) / "k.key"
        os.environ["AMBRACE_CREDENTIAL_KEY_FILE"] = str(key_file)   # 不碰真的 backend/data/secrets.key
        p = Path(td) / "garden_forum_state.json"
        doc = store.load(p)
        check("状态：文件不存在＝空档而不是抛错", doc.get("bound") == {} and doc.get("partner_key") == "")
        store.put_binding(doc, "3", "AK_SENTINEL_1234567890", "小叶", 1, now=time.time())
        doc["partner_key"] = "PK_SENTINEL_9876543210"
        doc["base_url"] = "http://127.0.0.1:8080"
        check("状态：save 返回成功", store.save(doc, p) is True)
        raw = p.read_text(encoding="utf-8")
        check("状态：**partner_key 不明文落盘**", "PK_SENTINEL" not in raw)
        check("状态：**api_key 不明文落盘**", "AK_SENTINEL" not in raw)
        check("状态：非凭据字段仍可读（站点地址要能手工核）", "http://127.0.0.1:8080" in raw)
        back = store.load(p)
        check("状态：解得回来（往返一致）",
              back["partner_key"] == "PK_SENTINEL_9876543210"
              and back["bound"]["3"]["api_key"] == "AK_SENTINEL_1234567890",
              str(back["partner_key"])[:16])
        check("状态：绑定里带着 user_id（调模型要用角色自己的 key）", back["bound"]["3"]["user_id"] == 1)

        store.mark_read(back, "3", 77, now=time.time())
        check("状态：读过的帖能被认出来", "77" in store.seen_reads(back, "3"))
        old = time.time() - 30 * 3600
        store.mark_read(back, "3", 78, now=old)
        check("状态：超过 26 小时的已读记录自动过期", "78" not in store.seen_reads(back, "3"))
        t = time.time()
        for i in range(store.SEEN_CAP + 60):
            store.mark_read(back, "3", 1000 + i, now=t - i)          # 越新的先塞，制造超限
        left = store.prune_reads(back, "3", now=t)
        check("状态：已读账本有上限（不无界膨胀）", len(left) <= store.SEEN_CAP, len(left))

        store.mark_reply(back, "3", 31, now=time.time())
        check("状态：问过模型的留言被记住（沉默也算问过，不每拍重问）", "31" in store.seen_replies(back, "3"))
        store.mark_reply(back, "3", 32, now=time.time() - 30 * 3600)
        check("状态：留言去重按 26 小时过期（隔天可以再问一次）", "32" not in store.seen_replies(back, "3"))
        store.mark_dm(back, "3", 5, now=time.time() - 8 * 24 * 3600)
        check("状态：私信去重按 7 天过期（一周前没回的还有一次机会）", "5" not in store.seen_dms(back, "3"))

        d = {"bound": {"10": {}, "9": {}, "2": {}}}
        check("状态：绑定按数字序而不是字典序", store.bound_ids(d) == ["2", "9", "10"], store.bound_ids(d))
        check("状态：解绑会连带清掉节律与去重账本",
              store.drop_binding(d, "9") is True and "9" not in d["bound"])
        p.write_text("{这不是 JSON", encoding="utf-8")
        check("状态：坏文件＝空档（不让 tick 冒异常）", store.load(p).get("bound") == {})
        p.write_text("[1, 2]", encoding="utf-8")
        check("状态：合法 JSON 但不是对象＝同样空档", store.load(p).get("bound") == {})
        p.write_text(json.dumps({"partner_key": "enc:v1:非法的密文"}), encoding="utf-8")
        check("状态：解不开的密文按空处理，绝不抛出去", isinstance(store.load(p).get("partner_key"), (str, type(None))))
        os.environ.pop("AMBRACE_CREDENTIAL_KEY_FILE", None)


def _strip_module_docstring(src: str) -> str:
    """去掉模块文档字符串，只留代码——「不要走 http_proxy 桥」这句**说明**写在头部注释里，
    不该被「源码里不许出现这个词」的反向断言误伤（真用了才是问题）。"""
    import ast
    tree = ast.parse(src)
    first = tree.body[0] if tree.body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
            and isinstance(first.value.value, str) and first.end_lineno:
        return "".join(src.splitlines(keepends=True)[first.end_lineno:])
    return src


def _no_secret_in_logs(src: str) -> bool:
    """用 AST 找**真的日志调用**，核它们的参数里没有凭据。

    按文本数会把注释和文档字符串一起数进去（自己咬自己的假通过），所以走语法树。
    """
    import ast
    import re

    bad = []
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        fname = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
        if fname not in ("log", "_hint_once"):
            continue
        seg = ast.get_source_segment(src, node) or ""
        if any(tk in seg for tk in SECRET_SUBSTRINGS) or re.search(r"\bpk\b", seg):
            bad.append(seg[:80])
    return not bad


def _saves_are_conditional(src: str) -> bool:
    """`main.py` 里**每一次** `store.save(doc)` 都必须待在某个 `if` 里面。

    内核每 30 秒调一次 tick，无条件写盘＝一天 2880 次重写那个**装着加密凭据**的文件
    （15 分钟节律本应只有约 96 次）。按 AST 判，不按文本数——文本数会把注释里那句"store.save"也算进去。
    """
    import ast
    tree = ast.parse(src)
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[id(child)] = node
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and (ast.get_source_segment(src, n) or "") == "store.save(doc)"]
    if not calls:
        return False
    for node in calls:
        up = parent.get(id(node))
        while up is not None and not isinstance(up, ast.If):
            up = parent.get(id(up))
        if up is None:
            return False
    return True


def _digest_write_unconditional(src: str) -> bool:
    """存论坛摘要那一句必须**无条件**：上游把 `digest_zh` 收回（变空）时，本地要能跟着变空。

    真事（2026-10-06 21:03）：站点在 M2.1 修复前给了含模拟活动的摘要，宿主写成"非空才存"，
    修好后站点返回空串却清不掉本地那一份——角色照旧把论坛已否认的经历当真事讲，最长滞留 48 小时。
    """
    import ast
    tree = ast.parse(src)
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[id(child)] = node
    hits = [n for n in ast.walk(tree)
            if isinstance(n, ast.Assign) and '"digest"' in (ast.get_source_segment(src, n) or "")]
    if not hits:
        return False
    for node in hits:
        up = parent.get(id(node))
        while up is not None and not isinstance(up, (ast.If, ast.While)):
            up = parent.get(id(up))
        if up is not None:
            return False
    return True


def _assigns_without_global(src: str) -> list[str]:
    """列出「函数里改了模块级变量却没写 global」的位置——这类写法运行时必炸 UnboundLocalError。

    真事：`_beat` 第一版就是少了 `global _reconciled`，ruff F823 抓到，插件会**每拍都异常**
    （被 on_tick 的 try 吞掉，表现为"开关打开了却什么都不发生"，最难查的那种）。
    """
    import ast
    tree = ast.parse(src)
    mod = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            for tgt in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                if isinstance(tgt, ast.Name):
                    mod.add(tgt.id)
    bad = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        declared = set()
        for st in ast.walk(fn):
            if isinstance(st, (ast.Global, ast.Nonlocal)):
                declared.update(st.names)
        for st in ast.walk(fn):
            tgts = st.targets if isinstance(st, ast.Assign) else ([st.target] if isinstance(st, ast.AugAssign) else [])
            for tgt in tgts:
                if isinstance(tgt, ast.Name) and tgt.id in mod and tgt.id not in declared:
                    bad.append("%s → %s" % (fn.name, tgt.id))
    return bad


def t_post_and_digest():
    check("parse_post：无＝不发", beat.parse_post("无") == ("", ""))
    check("parse_post：只给一行标题不算一篇帖子", beat.parse_post("今天想说说浇水") == ("", ""))
    check("parse_post：标题＋正文照收",
          beat.parse_post("陶盆和塑料盆的水量差别\n用了三年才发现，蒸发差一倍。")
          == ("陶盆和塑料盆的水量差别", "用了三年才发现，蒸发差一倍。"))
    check("parse_post：标题太短＝不发（论坛会 400）", beat.parse_post("盆\n正文正文") == ("", ""))
    check("parse_post：标题截到 60 字", len(beat.parse_post("句" * 90 + "\n正文正文正文")[0]) == 60)
    check("parse_post：正文截到 500 字", len(beat.parse_post("一个够长的标题\n" + "字" * 900)[1]) == 500)
    check("parse_post：代码围栏里的内容照样能读", beat.parse_post("```\n标题写在这里\n正文在这里\n```")[0] == "标题写在这里")

    day = beat.beijing_day(time.mktime(time.strptime("2026-10-06 01:30", "%Y-%m-%d %H:%M")))
    check("beijing_day：北京时间 01:30 算当天（不是 UTC 的前一天）", day == "10-06", day)
    check("may_post：默认 0＝这个能力关着", not beat.may_post({}, "10-06", 0))
    check("may_post：当天没到上限可以发", beat.may_post({}, "10-06", 1))
    check("may_post：到了上限就停", not beat.may_post({"date": "10-06", "n": 1}, "10-06", 1))
    check("posts_today：跨天自动清零（不需要谁去重置）",
          beat.posts_today({"date": "10-05", "n": 3}, "10-06") == 0)
    check("bump_posts：账本写的是当天日期", beat.bump_posts({"date": "10-05", "n": 3}, "10-06") == {"date": "10-06", "n": 1})

    now = time.time()
    check("render_digest：空摘要＝什么都不注入", beat.render_digest(None, now) == "")
    check("render_digest：没有 text 字段也不炸", beat.render_digest({"at": now}, now) == "")
    ok = beat.render_digest({"text": "近 7 天你读了 4 篇帖，回了 2 条留言。", "at": now - 60}, now)
    check("render_digest：正常摘要会包成一段", "【花园论坛】" in ok and "近 7 天" in ok, ok[:40])
    check("render_digest：只加防编造提醒，不加催促",
          "该去发帖" not in ok and "多参与" not in ok and "没发生过的别编" in ok)
    check("render_digest：超过 48 小时就不注入了",
          beat.render_digest({"text": "很久以前的", "at": now - 49 * 3600}, now) == "")
    check("render_digest：长度封顶（别把聊天上下文挤爆）",
          len(beat.render_digest({"text": "字" * 900, "at": now}, now)) < 900)

    blob = json.dumps(beat.build_post_messages({"name": "小叶"}, "", [], []), ensure_ascii=False)
    check("发帖提示词：也不许带论坛契约", "/api/" not in blob and "act." not in blob)
    check("发帖提示词：必须允许沉默", "只输出一个字符：无" in blob)
    check("发帖提示词：格式说清楚（第一行标题）", "第一行只写标题" in blob)
    check("发帖提示词：没题材清单时不炸，也不硬塞话题", "社区没给题材清单" in blob)
    th = [{"title": "连晴第九天结束", "circle": "watering", "author_display": "灵芝"}]
    b2 = json.dumps(beat.build_post_messages({"name": "小叶"}, "quiet", th,
                                             [{"slug": "watering", "name": "浇水"}]), ensure_ascii=False)
    check("发帖提示词：给了素材就带上", "连晴第九天结束" in b2 and "浇水" in b2)


def _func_source(src: str, name: str) -> str:
    """按名字取一个函数的源码（AST 定位，不靠缩进猜）。"""
    import ast
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return ast.get_source_segment(src, n) or ""
    return ""


def _call_arity_problems(caller_src: str, callee_src: str, prefix: str = "beat") -> list[str]:
    """核 `prefix.fn(...)` 的调用参数对不对（个数＋关键字名）。

    为什么值得写这一条：`_beat` 外面包着 `try/except`，参数少传一个只会变成一条
    "tick 异常"日志 ⇒ 功能**静默失效**，文本锚点全都照绿（今天就踩了：`may_post()` 少传 limit）。
    """
    import ast
    sigs = {}
    for n in ast.walk(ast.parse(callee_src)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = n.args
            req = len(a.args) - len(a.defaults)
            names = {p.arg for p in a.args} | {p.arg for p in a.kwonlyargs}
            sigs[n.name] = (req, len(a.args), names, bool(a.vararg), bool(a.kwarg))
    bad = []
    for node in ast.walk(ast.parse(caller_src)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == prefix):
            continue
        sig = sigs.get(node.func.attr)
        if sig is None:
            bad.append("%s.%s：这一层里根本没有这个函数" % (prefix, node.func.attr))
            continue
        req, total, names, has_var, has_kw = sig
        kw = {k.arg for k in node.keywords if k.arg}
        if kw - names:
            bad.append("%s.%s：传了不存在的关键字 %s" % (prefix, node.func.attr, sorted(kw - names)))
        given = len(node.args) + len(kw)
        if not has_var and len(node.args) > total:
            bad.append("%s.%s：位置参数给了 %d 个，签名只有 %d 个" % (prefix, node.func.attr, len(node.args), total))
        if len(node.args) < req and len(node.args) + len(kw) < req:
            bad.append("%s.%s：至少要 %d 个参数，只给了 %d" % (prefix, node.func.attr, req, given))
    return bad


def t_main_wiring():
    """`main.py` 在插件上下文外 import 不了（`sdk.hook` 会抛），所以这里用源码锚点核接线。"""
    src = (_PLUGIN_DIR / "main.py").read_text(encoding="utf-8")
    man = json.loads((_PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))
    check("接线：挂的是 schedule_tick 且是 async 函数",
          '@sdk.hook("schedule_tick")' in src and "async def on_tick" in src)
    check("接线：总开关先判（没开就一条 HTTP 都不发）", 'cfg.get("garden_forum_on"' in src)
    check("接线：SDK 的同步 urllib 必须包在 to_thread 里（不能阻塞事件循环）",
          "to_thread(gf.state" in src and "to_thread(gf.say" in src)
    check("接线：重入锁（上一轮没跑完不叠第二遍）", "_lock.locked()" in src)
    check("接线：写盘必须挂在条件里（不每 30 秒重写一次装着凭据的文件）", _saves_are_conditional(src))
    check("接线：论坛摘要变空要能清掉本地那份（不是「非空才存」）", _digest_write_unconditional(src))
    check("接线：chars_per_beat 填 0 不能变成「每拍跑完所有角色」",
          'max(1, beat.read_limit(cfg, "chars_per_beat", 3))' in src)
    check("接线：整轮有截止（慢站点不能拖住调度循环）", "BEAT_DEADLINE_SEC" in src)
    check("接线：异常绝不冒泡", 'sdk.log("garden_forum tick 异常' in src)
    check("接线：不用上一版派单里那个不存在的 sdk.store_get", "store_get" not in src)
    body = _strip_module_docstring(src)
    check("接线：不走会剥 Authorization 的插件 HTTP 桥，也不自己搓 urllib",
          "http_proxy" not in body and "urllib" not in body and "GardenForum(" in body)
    check("红线：日志语句里不引用凭据（AST 找真调用，不按文本数）", _no_secret_in_logs(src))
    check("manifest：声明了 persona:read（拿人设要权限，否则一律 PermissionError）",
          "persona:read" in man["permissions"])
    check("manifest：声明的钩子都是内核认识的（且只有心跳＋注入这两个）",
          set(man["hooks"]) == {"schedule_tick", "context_inject"}, man["hooks"])
    check("manifest：默认关闭", man["config"]["garden_forum_on"] is False)
    check("manifest：hook_timeout 在 45 秒（内核不夹档，原值生效）", man["hook_timeout"] == 45)
    texts = src + man["usage"] + (_PLUGIN_DIR / "garden_state.py").read_text(encoding="utf-8") \
        + (_PLUGIN_DIR / "garden_beat.py").read_text(encoding="utf-8") \
        + (_PLUGIN_DIR / "garden_setup.py").read_text(encoding="utf-8")
    check("红线：插件目录内**不写本机绝对路径**（这个目录会随脱敏快照进公开仓）",
          "D:" not in texts and "C:\\Users" not in texts)
    check("红线：不接 --key 参数（命令行会留在 shell 历史与进程列表里）",
          'add_argument("--key"' not in texts)
    leaks = _assigns_without_global(src)
    check("接线：改模块级状态必须写 global（漏了就每拍 UnboundLocalError）",
          not leaks, "; ".join(leaks[:3]))
    inj = _func_source(src, "on_inject")
    check("接线：context_inject 钩子存在且挂在合法钩子名上",
          bool(inj) and '@sdk.hook("context_inject")' in src)
    check("接线：注入路径上**绝不联网、绝不调模型**（聊天不能被论坛拖慢）",
          all(tk not in inj for tk in ("to_thread", "chat_completion", "GardenForum(")) and "store.load()" in inj,
          inj[:70])
    check("接线：注入前先看两个开关", 'cfg.get("garden_forum_on"' in inj and 'cfg.get("garden_forum_inject"' in inj)
    tp = _func_source(src, "_try_post")
    check("接线：发帖**先记账再问模型**（否则失败的那次每拍重烧一遍）",
          bool(tp) and 0 <= tp.index("beat.bump_posts") < tp.index("chat_completion"))
    arity = (_call_arity_problems(src, (_PLUGIN_DIR / "garden_beat.py").read_text(encoding="utf-8"), "beat")
             + _call_arity_problems(src, (_PLUGIN_DIR / "garden_state.py").read_text(encoding="utf-8"), "store"))
    check("接线：调 beat.*/store.* 的参数个数与名字都对（错一个会被 try 吞成静默失效）",
          not arity, "; ".join(arity[:3]))
    check("接线：一拍只问模型一次（回过话就不再发帖）", "if limit and not asked and" in src)
    check("manifest：两个钩子都声明了", man["hooks"] == ["schedule_tick", "context_inject"], man["hooks"])
    check("manifest：主动发帖默认关", man["config"]["garden_forum_daily_post"] == 0)
    check("manifest：注入默认开", man["config"]["garden_forum_inject"] is True)
    import re as _re
    dl = _re.search(r"BEAT_DEADLINE_SEC\s*=\s*(\d+)", src)
    lb = _re.search(r"LLM_BUDGET_SEC\s*=\s*(\d+)", src)
    check("接线：整轮截止必须短于内核 hook_timeout（超了不是「慢一点」，是被内核取消）",
          dl is not None and int(dl.group(1)) < int(man["hook_timeout"]), dl and dl.group(1))
    check("接线：模型时限必须短于整轮截止（否则第二次调用必然被截在半路）",
          lb is not None and dl is not None and int(lb.group(1)) < int(dl.group(1)))


def t_kernel_contract():
    """用内核自己的校验器过一遍 manifest——比自己判字段可靠（它说了算）。"""
    try:
        from app.plugins.manifest import validate_manifest
    except Exception as e:
        FAILS.append("❌ 拿不到内核校验器（守卫没跑）：%s" % e)
        return
    man = json.loads((_PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))
    err = validate_manifest(man)
    check("内核 manifest 校验通过", err is None, err)


def main() -> int:
    for fn in (t_next_wait, t_due_and_clamp, t_picks, t_parse_reply, t_error_class,
               t_messages, t_post_and_digest, t_state, t_main_wiring, t_kernel_contract):
        fn()
    print("合：%d 条通过，%d 条没牙/失败" % (OKS, len(FAILS)))
    for f in FAILS:
        print(f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
