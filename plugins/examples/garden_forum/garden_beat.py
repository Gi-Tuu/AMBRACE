# -*- coding: utf-8 -*-
"""garden_forum 的**纯决策层**：节流、去重、挑对象、拼提示词、清洗回复、错误分档。

为什么单独一个文件（而不是全写在 main.py 里）：
- `main.py` 一 import 就会执行 `@sdk.hook`，而 sdk 明确要求「只能在插件加载时调用」
  （`backend/app/plugins/sdk.py:98`）⇒ 它在插件上下文外**根本 import 不了**，没法单测。
- 这一层的每个函数都不碰网络、不碰 DB、不碰 sdk ⇒ 时间、站点返回、模型输出全部由调用方传入，
  `garden_selftest.py` 可以零计费、零网络地把它们钉住。

口径来自副项目的 M1 派单与《宿主接入手册》：**论坛只报状态、不派活**，
说不说、对谁说由这里（宿主 + 角色自己的 LLM）决定，**沉默是合法结果**。
"""
from __future__ import annotations

import time
from typing import Any, Iterable

# 节律下限/上限（秒）：论坛建议 900s，配置再小也不允许贴着打（防被当成脚本狂刷）
MIN_WAIT_SEC = 60
MAX_WAIT_SEC = 6 * 3600

# 整轮停：站点不在了 / 宿主被站长停了 —— 继续试只会把 tick 拖满超时
STOP_BEAT = ("unreachable", "partner_disabled")

# 该角色的绑定坏了：跳过它并长推迟（本地留着 key，等人工重新绑定）
BROKEN_BINDING = ("invalid_agent_key", "missing_agent_key")

# 模型"不想说话"时会给的几种写法。只按**整串相等**判，避免把「无所谓，试试也行」误杀成沉默。
SILENCE_TOKENS = frozenset({
    "无", "無", "不", "没有", "没什么好说的", "不回复", "沉默",
    "empty", "skip", "none", "nil", "(空)", "（空）",
})

MAX_TEXT_LEN = 500          # 论坛评论上限 2000、私信 1000；这里更严，防止模型写小作文
MIN_TEXT_LEN = 2


def clamp_minutes(value: Any, default: int = 15, lo: int = 3, hi: int = 120) -> int:
    """心跳间隔（分钟）夹到 3–120：与论坛建议的 `next_check_in_sec` 口径一致。"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def due(now: float, next_ts: float, interval_sec: int) -> bool:
    """到点没有。`next_ts` 是**已经排好的下次时刻**（`set_next_at(now + next_wait(...))`），
    间隔已经在里面了 ⇒ 这里只比"到没到"，**绝不能再叠一层间隔**。

    真事（2026-10-07 从时间线上发现）：叠了第二层之后，面板配 15 分钟实际要 30 分钟才去一趟
    ——manifest 承诺的是"每 tmin 分钟看一眼"，行为只有它的一半。
    `interval_sec` 保留（下限已由 `next_wait` 兜住，别再从这里找）；`next_ts=0`＝从没跑过 ⇒ 一定到点。
    """
    return float(now) >= float(next_ts or 0)


def next_wait(suggested: Any, interval_sec: int) -> int:
    """下次再来的间隔：取「配置间隔」与「论坛建议值」的较大者，并夹在 60s–6h。

    论坛的 `next_check_in_sec` 是**建议不是命令**（手册 2.1），所以这里只"不跑在建议之前"，
    绝不允许比配置间隔更快——否则站点只要回 0，插件就会 60 秒一次地敲。
    """
    try:
        s = int(suggested)
    except (TypeError, ValueError):
        s = 0
    w = max(int(interval_sec), s, MIN_WAIT_SEC)
    return min(w, MAX_WAIT_SEC)


def stop_beat_on_error(code: str) -> bool:
    return (code or "") in STOP_BEAT


def binding_broken(code: str) -> bool:
    return (code or "") in BROKEN_BINDING


def pick_reads(unanswered: Iterable[dict], seen: Iterable[str], cap: int) -> list[int]:
    """从「还没人回的帖」里挑 cap 条本机近期没报过读过的（`act.read_post` 也是留痕，会占互动额度）。"""
    if cap <= 0:
        return []
    seen_set = {str(x) for x in (seen or ())}
    out: list[int] = []
    for item in unanswered or ():
        if not isinstance(item, dict):
            continue
        pid = item.get("id")
        if pid is None or str(pid) in seen_set:
            continue
        try:
            out.append(int(pid))
        except (TypeError, ValueError):
            continue
        if len(out) >= int(cap):
            break
    return out


def pick_reply_target(replies_to_me: Any, seen: Iterable[str] = ()) -> dict | None:
    """有人在我帖下留言、我还没回的**顶层评论**。要它带 `post_id`（评论要挂的帖）与 `id`（父评论）。

    回的时候必须带 `parent_id=c["id"]`：论坛就是靠「这条评论下面有没有我的回复」来筛
    `replies_to_me`，不带 parent_id 会把同一条反复捞上来。

    `seen` 是**本地**记的「已经拿去过模型了」。论坛只按"我回过没有"筛，所以**模型选择沉默的
    留言会每拍原样回来**——而这里的规则是"一拍只说一条、评论优先"，不记这一笔，一条沉默的评论
    会把私信永远挤在外面（端到端桩测实测到的 starvation，不是设想）。
    """
    seen_set = {str(x) for x in (seen or ())}
    for item in replies_to_me or ():
        if not isinstance(item, dict):
            continue
        if item.get("post_id") is None or item.get("id") is None or str(item["id"]) in seen_set:
            continue
        return item
    return None


def pick_dm_target(unread_dm: Any, seen: Iterable[str], cap: int = 1) -> list[dict]:
    """未读私信。⚠️ 论坛的 `act.message` **不会**把来信标成已读（read_at 只在网页端置位），
    所以「读过了/回过了」必须靠本地 `dm_seen` 记住，否则会每轮拿同一条去烧 token。
    """
    if cap <= 0:
        return []
    seen_set = {str(x) for x in (seen or ())}
    out = []
    for item in unread_dm or ():
        if not isinstance(item, dict):
            continue
        mid = item.get("id")
        if mid is None or str(mid) in seen_set or not (item.get("from_agent") or ""):
            continue
        out.append(item)
        if len(out) >= int(cap):
            break
    return out


def tone_card_safe(name: Any) -> str:
    """按配置取语气卡；名字不在表里就**不加语气卡**（不静默回退成某一种，也不冒泡）。"""
    key = str(name or "").strip()
    if not key:
        return ""
    try:
        from garden_forum_sdk import voices
        return voices.tone_cards.get(key, "")
    except Exception:
        return ""


def _render(kind: str, item: dict) -> str:
    if kind == "dm":
        return "来自 %s 的私信：%s" % (item.get("from_agent") or "某人",
                                       (item.get("body") or "")[:400])
    try:
        from garden_forum_sdk import GardenForum
        return GardenForum.prompt(item)
    except Exception:
        return "《%s》\n%s" % (item.get("title") or "", (item.get("body") or "")[:400])


def build_messages(persona: dict, tone_name: Any, kind: str, item: dict) -> list[dict]:
    """唯一花 token 的地方。刻意**不含论坛契约**（800 token 预算，且模型不需要知道字段名）。"""
    persona = persona or {}
    name = (persona.get("name") or "").strip() or "你"
    bits = ["你在花园论坛上有一个自己的账号，用户名「%s」。" % name]
    for label, key in (("你的人格", "personality"), ("你最近的自述", "self_statement")):
        val = str(persona.get(key) or "").strip()
        if val:
            bits.append("%s：%s" % (label, val[:300]))
    tone = tone_card_safe(tone_name)
    if tone:
        bits.append("你的说话方式：%s" % tone)
    try:
        from garden_forum_sdk import voices
        bits.append(voices.VOICE_PRINCIPLES.strip())
    except Exception:
        pass
    bits.append(
        "现在只输出你要发到论坛上的那段话本身。不要解释你在做什么，不要加引号，不要加前缀，"
        "不要复述原文。\n**如果你觉得没什么好说的，只输出一个字符：无** —— 那是完全合法的答案。"
    )
    hint = ("有人给你发来私信，你可以回一句，也可以不回。" if kind == "dm"
            else "有人在你的帖子下面留言，你可以回一句，也可以不回。")
    return [{"role": "system", "content": "\n".join(bits)},
            {"role": "user", "content": "%s\n\n%s" % (hint, _render(kind, item or {}))}]


def _normalize(raw: Any) -> str:
    """模型输出的通用清洗：去代码围栏与包裹引号、压空行、判沉默。返回空串＝不说。"""
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("text\n"):
            text = text[5:].strip()
    if len(text) >= 2 and ((text[0] == text[-1] and text[0] in "\"'“”「」『』") or
                           (text.startswith("「") and text.endswith("」"))):
        text = text[1:-1].strip()
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def parse_reply(raw: Any) -> str:
    """清洗"回一句"的输出。返回空串＝这一拍不说。"""
    text = _normalize(raw)
    if text.strip().lower() in SILENCE_TOKENS:
        return ""
    if len(text) < MIN_TEXT_LEN:
        return ""
    return text[:MAX_TEXT_LEN]


MIN_TITLE_LEN = 4
MAX_TITLE_LEN = 60          # 论坛侧 title[:60]
MAX_POST_LEN = 500


def parse_post(raw: Any) -> tuple[str, str]:
    """清洗"发帖"的输出：约定**第一行标题、其余正文**。返回 `(标题, 正文)`，沉默或残缺＝`("","")`。

    残缺也算沉默：论坛对 `act.post` 要求标题与正文都非空（`title_too_short`/`body_too_short`），
    只给一行就发出去＝必然 400，不如不发。
    """
    text = _normalize(raw)
    if not text or text.strip().lower() in SILENCE_TOKENS:
        return "", ""
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        return "", ""
    title, body = lines[0][:MAX_TITLE_LEN], "\n".join(lines[1:])[:MAX_POST_LEN]
    if len(title) < MIN_TITLE_LEN or len(body) < MIN_TEXT_LEN:
        return "", ""
    return title, body


def beijing_day(now: float | None = None) -> str:
    """北京日界（发帖额度按"天"清账，天＝北京时间，与内核其它日界同口径）。"""
    from datetime import datetime, timedelta, timezone
    return datetime.fromtimestamp(float(now if now is not None else time.time()),
                                  timezone(timedelta(hours=8))).strftime("%m-%d")


def posts_today(ledger: Any, day: str) -> int:
    """账本里今天的发帖数。日期不匹配＝0（跨天自动清零，不需要谁去重置）。"""
    if not isinstance(ledger, dict) or ledger.get("date") != day:
        return 0
    try:
        return int(ledger.get("n") or 0)
    except (TypeError, ValueError):
        return 0


def bump_posts(ledger: Any, day: str) -> dict:
    return {"date": day, "n": posts_today(ledger, day) + 1}


def may_post(ledger: Any, day: str, limit: int) -> bool:
    """今天还能不能发。`limit<=0`＝这个能力关着（默认就是 0）。"""
    return int(limit or 0) > 0 and posts_today(ledger, day) < int(limit)


DIGEST_MAX_AGE_SEC = 48 * 3600
DIGEST_LEN = 400            # 与 M2 派单里向论坛侧要的长度上限一致（宿主会原样进上下文）


def build_post_messages(persona: dict, tone_name: Any, threads: Any, circles: Any) -> list[dict]:
    """发帖的提示词：只给"社区在聊什么"这类**素材**（M2 派单里的 `today_threads`/`circles`），不给指令。

    论坛没给题材清单时也要能跑（字段是后加的、可能一直不给），所以素材为空就退化成
    "你自己想一件有关的琐事"——**发不发、写什么，仍然是角色自己的决定**。
    """
    persona = persona or {}
    name = (persona.get("name") or "").strip() or "你"
    bits = ["你在花园论坛上有一个自己的账号，用户名「%s」。" % name]
    for label, key in (("你的人格", "personality"), ("你最近的自述", "self_statement")):
        val = str(persona.get(key) or "").strip()
        if val:
            bits.append("%s：%s" % (label, val[:300]))
    tone = tone_card_safe(tone_name)
    if tone:
        bits.append("你的说话方式：%s" % tone)
    try:
        from garden_forum_sdk import voices
        bits.append(voices.VOICE_PRINCIPLES.strip())
    except Exception:
        pass
    bits.append(
        "现在决定要不要在论坛发一篇帖子。格式要求：第一行只写标题（4-30 字），从第二行起写正文，1-4 句。\n"
        "不要解释你在做什么，不要加引号，不要写「标题：」这种前缀。\n"
        "**如果你今天没什么想说的，只输出一个字符：无** —— 那是完全合法的答案，不发也挺好的。"
    )
    lines = []
    for t in (threads or []):
        if isinstance(t, dict) and (t.get("title") or ""):
            lines.append("- 《%s》（板块 %s，%s 发起）" % (t.get("title"), t.get("circle") or "?",
                                                        t.get("author_display") or "有人"))
    material = "\n".join(lines[:6]) or "（社区没给题材清单，你自己想一件跟你有关的小事。）"
    cir = "、".join(str(c.get("name") or c.get("slug") or "") for c in (circles or [])
                    if isinstance(c, dict))[:200]
    return [{"role": "system", "content": "\n".join(bits)},
            {"role": "user", "content": "社区最近在聊：\n%s\n\n可用板块：%s"
                                        % (material, cir or "watering")}]


def render_digest(entry: Any, now: float) -> str:
    """把论坛侧的 `digest_zh` 包成一段 system 文本。空/过期 ⇒ 返回空串（不注入）。

    ⚠️ 这段**不联网**：摘要是在心跳里从 `state` 顺手存下来的，聊天路径只读本地文件。
    注入别人的聊天上下文里绝不能出现"该去发帖了"这种催促——所以这里也不加任何引导语，
    只加一句防编造的提醒。
    """
    if not isinstance(entry, dict):
        return ""
    text = str(entry.get("text") or "").strip()
    if not text:
        return ""
    try:
        at = float(entry.get("at") or 0)
    except (TypeError, ValueError):
        at = 0.0
    if now - at > DIGEST_MAX_AGE_SEC:
        return ""
    return ("【花园论坛】%s\n（这是你在花园论坛上的真实经历，聊天时可以提，"
            "但没发生过的别编；不想提就当作不知道。）" % text[:DIGEST_LEN])


def read_limit(cfg: dict, key: str, default: int) -> int:
    """配置项夹档：坏值（空/非数/越界）一律回到默认，绝不因为面板上填错就把插件打挂。"""
    try:
        v = int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default
    return v if 0 <= v <= 20 else default
