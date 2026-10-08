# -*- coding: utf-8 -*-
"""garden_forum —— 让角色在「花园论坛」上自主社交（读帖、回评、回私信、留痕）。

形态：挂 `schedule_tick` 的插件，插件自己出网调论坛接口（`garden_forum_sdk`，标准库 urllib 直连）。

三条口径来自派单第三节，且都在本仓代码里核过（不是照抄文档）：
1. **自主调度路径不执行工具**（`app/scheduling/message_llm.py` 里没有工具阶段）
   ⇒ 没人聊天时模型调不了任何工具，心跳只能由插件自己发 HTTP。
2. **插件 HTTP 桥会剥掉 Authorization**（`application/plugin_bridge_service.py` 的 `_sanitize_headers`）
   ⇒ 不走 `http_proxy` 桥，用 SDK 直连。
3. **工具声明只有 800 token 预算**（`agent/context/section_mcp.py`）
   ⇒ 论坛契约**一个字都不进 prompt**（连极简的 `/api/ops` 都不取）；模型只看人设与那条内容。

分工：本文件只做**接线**（钩子、节流、串行轮转、错误隔离、调内核）。
所有可离线验证的判断在 `garden_beat.py`，凭据与去重账本在 `garden_state.py`。

安全边界：默认关（manifest `garden_forum_on=false`，插件本身在库里也默认未启用）；
`partner_key` 只存在于宿主侧的本地文件、**绝不进 AI 上下文**；论坛挂了不影响陪伴主流程
（全 try/except ＋ 整轮 30 秒截止 ＋ 单次 HTTP 8 秒超时 ＋ 站点连不上立刻结束本轮）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone

# 插件目录不是 package（loader 用 spec_from_file_location 单文件加载 main.py），
# 兄弟模块靠这句找得到；模块名一律带 garden_ 前缀，防与其它插件撞 sys.modules（docs/plugin-development.md P3-3）。
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from app.plugins import sdk  # noqa: E402
import garden_beat as beat  # noqa: E402
import garden_state as store  # noqa: E402
from garden_forum_sdk import GardenError, GardenForum  # noqa: E402

DEFAULT_URL = "http://127.0.0.1:8080"
HTTP_TIMEOUT_SEC = 8          # 论坛再慢也不许把 tick 拖满
BEAT_DEADLINE_SEC = 30        # 一轮心跳的总预算（manifest hook_timeout=45，留余量给收尾）
LLM_BUDGET_SEC = 25           # 自己掐模型调用的时限，别等内核超时来取消
STATE_LIMIT = 10              # 每次向论坛要多少条现状
DND_POSTPONE_SEC = 600        # 免打扰时段：10 分钟后再判一次，不空转也不越窗说话
BROKEN_POSTPONE_SEC = 6 * 3600    # key 失效（多半是 hard 注销过）：绑着也没用，6 小时后再试
RETRY_POSTPONE_SEC = 600          # 其它单次故障：10 分钟后再试，避免每 30 秒撞一次
SITE_BACKOFF_SEC = 900            # 整站不通：15 分钟内一次都不探（论坛挂着时每 30 秒撞一次＝纯噪音＋占心跳预算）

_lock = asyncio.Lock()
_reconciled = False
_hinted: set[str] = set()

_REASON_COMMENT = "这条在我自己的帖子下面，我想回一句。"
_REASON_DM = "收到私信，我回一句。"
_REASON_POST = "今天想留一句，就发了。"


def _who(rec: dict) -> str:
    """日志里的角色标识——只给名字，**绝不给 key**。"""
    return (rec or {}).get("name") or "?"


def _hint_once(tag: str, msg: str, *args) -> None:
    """开机类提示只报一次（tick 每 30 秒一次，重复刷屏会让日志失去意义）。"""
    if tag in _hinted:
        return
    _hinted.add(tag)
    sdk.log(msg, *args)


@sdk.hook("schedule_tick")
async def on_tick(ctx):
    try:
        cfg = sdk.get_config() or {}
        if not cfg.get("garden_forum_on", False):
            return
        if _lock.locked():
            return          # 上一轮还没结束（被超时取消过也会这样），不叠第二遍
        async with _lock:
            await _beat(cfg)
    except Exception as e:
        sdk.log("garden_forum tick 异常: %s", e)      # 绝不冒泡：外挂不能碰陪伴主链路


async def _beat(cfg: dict) -> None:
    global _reconciled
    doc = store.load()
    pk = (doc.get("partner_key") or "").strip()
    base = (cfg.get("garden_forum_url") or doc.get("base_url") or DEFAULT_URL).strip()
    ids = store.bound_ids(doc)
    if not pk or not ids:
        _hint_once("unbound", "garden_forum 已开但还没绑定：请在仓库根跑 "
                              "backend\\.venv\\Scripts\\python.exe plugins\\examples\\garden_forum\\garden_setup.py bind")
        return

    if not _reconciled:
        _reconciled = True
        if await _reconcile(pk, base, doc, ids):
            store.save(doc)
            return

    # 整站故障退避期内：一次 HTTP 都不发、也不再刷「站点级故障」那一行日志
    if time.time() < float(doc.get("site_block_until") or 0):
        return

    interval = 60 * beat.clamp_minutes(cfg.get("garden_forum_tmin", 15))
    # chars_per_beat 夹到 ≥1：配成 0 会被下面当成"不限量"，那就成了"面板上填 0＝每拍把所有角色跑一遍"
    per_beat = max(1, beat.read_limit(cfg, "chars_per_beat", 3))
    now = time.time()
    t0 = time.monotonic()
    served = 0
    dirty = False
    # 按「离下次再来的远近」排：刚服务过的会被推到队尾 ⇒ 多个角色天然轮转，最后一个不会被饿死
    for cid in sorted(ids, key=lambda c: store.next_at(doc, c)):
        if not beat.due(now, store.next_at(doc, cid), interval):
            continue
        if time.monotonic() - t0 > BEAT_DEADLINE_SEC:
            sdk.log("garden_forum 本轮超预算（%ds），剩下的角色下一拍再服务", BEAT_DEADLINE_SEC)
            break
        dirty = True
        if await _in_dnd(cid):
            store.set_next_at(doc, cid, now + DND_POSTPONE_SEC)
            continue
        stop = await _one_character(base, doc, cid, cfg, now, interval)
        served += 1
        if not stop:
            doc["site_block_until"] = 0.0     # 有角色被正常服务＝站点活着 ⇒ 解除退避
        if stop or (per_beat and served >= per_beat):
            break
    doc["last_beat"] = int(now)
    if dirty:
        # **只有真动过才写盘**：这个文件装着凭据，内核每 30 秒调一次 tick，
        # 无条件写＝一天 2880 次重写同一个密文文件（15 分钟节律下本来只有 ~96 次）。
        store.save(doc)


async def _reconcile(pk: str, base: str, doc: dict, ids: list[str]) -> bool:
    """宿主自省（派单 5.2 第 2 步，**重启后必做**）：配额剩多少、站长停没停我、本地 key 对得上吗。

    返回 True＝本轮不要再往下走（站点不通，或宿主被停用）。
    """
    admin = GardenForum(base, "", timeout=HTTP_TIMEOUT_SEC)
    try:
        r = await asyncio.to_thread(admin.reconcile, pk, ids)
    except GardenError as e:
        _hint_once("net", "garden_forum 连不上论坛（%s），本轮不动作；论坛故障不影响陪伴主流程", e.code)
        return True
    if r.get("disabled"):
        _hint_once("disabled", "garden_forum：站长已停用本宿主（partner_disabled），不再动作")
        return True
    if r.get("unknown_locally"):
        _hint_once("orphan", "garden_forum：站上有 %d 个角色但本地没有 key（key 只发一次、要不回来），"
                             "要恢复只能换 character_id 重绑：%s",
                   len(r["unknown_locally"]), ", ".join(str(x) for x in r["unknown_locally"][:5]))
    if (r.get("remaining") or 0) <= 0:
        _hint_once("quota", "garden_forum：宿主配额已用满（remaining=0），新角色绑不上")
    got_key = False
    for cid in (r.get("need_register") or [])[:3]:
        rec = (doc.get("bound") or {}).get(cid) or {}
        try:
            res = await asyncio.to_thread(admin.register, pk, cid, rec.get("name") or "", "")
        except GardenError as e:
            sdk.log("garden_forum 补注册角色 %s 失败: %s", _who(rec), e.code)
            continue
        if res.get("api_key"):
            store.put_binding(doc, cid, res["api_key"], rec.get("name") or "", rec.get("user_id"))
            got_key = True
            sdk.log("garden_forum：角色 %s 的论坛账号曾被硬删除，已重建（新 key 存本地，历史从零）", _who(rec))
    if got_key:
        # **当轮必须落盘**：api_key 只发一次，服务端只存哈希。
        # 跟着"这轮有没有别的动作"一起写＝可能拿到新 key 却不存，下一拍再注册也只是又一个存不下来的 key。
        store.save(doc)
    return False


async def _one_character(base: str, doc: dict, cid: str, cfg: dict, now: float, interval: int) -> bool:
    """一个角色的一拍。返回 True＝整轮该停了（站点级故障，不是这个角色自己的问题）。"""
    rec = (doc.get("bound") or {}).get(cid) or {}
    gf = GardenForum(base, rec.get("api_key") or "", name=rec.get("name") or "", timeout=HTTP_TIMEOUT_SEC)
    try:
        st = await asyncio.to_thread(gf.state, STATE_LIMIT)
    except GardenError as e:
        return _fail_note(doc, cid, rec, e, now)

    # 论坛的 next_check_in_sec 是建议：取「配置间隔」与它的较大者，绝不允许跑得比配置更快
    store.set_next_at(doc, cid, now + beat.next_wait(st.get("next_check_in_sec"), interval))

    # 顺手存下"我这阵子在论坛做了什么"：聊天注入只读这个本地文件，**绝不在聊天路径上联网**
    # 上游把摘要收回（变空）时本地必须跟着变空：留着旧文案＝一段论坛侧已经不认账的经历，
    # 会在 DIGEST_MAX_AGE_SEC 有效期内继续被角色当成真事讲出去。
    dz = st.get("digest_zh")
    dz = dz.strip()[:beat.DIGEST_LEN] if isinstance(dz, str) else ""
    doc.setdefault("digest", {})[cid] = {"text": dz, "at": now}

    # ① 免 token 的只读动作：告诉论坛「我读了这些」
    #    （read_post 也留痕、也占对同一对象的互动额度，所以本地记着读过的帖，不重复报）
    cap = beat.read_limit(cfg, "read_posts_per_beat", 2)
    for pid in beat.pick_reads(st.get("unanswered") or [], store.seen_reads(doc, cid, now), cap):
        r = await asyncio.to_thread(gf.say, "act.read_post", post_id=pid, raises=False)
        store.mark_read(doc, cid, pid, now)
        if not r.get("ok") and beat.stop_beat_on_error(r.get("code") or ""):
            return True

    # ② 要说话时：判断与措辞都由这个角色自己的 LLM 出，**沉默合法**
    asked = False
    target, kind = _speak_target(st, doc, cid, now)
    if target is not None:
        asked = True
        text = await _ask_llm(cid, rec, kind, target, cfg)
        if text:
            r = await _say(gf, kind, target, text)
            if not r.get("ok"):
                if beat.stop_beat_on_error(r.get("code") or ""):
                    return True
                sdk.log("garden_forum 角色 %s 的%s没发出去: %s", _who(rec), kind, r.get("code"))
            elif r.get("soft_note"):
                sn = r["soft_note"]
                sdk.log("garden_forum 角色 %s 收到频率提示（论坛不拦，只提示）：%s 24h 内 %s 条",
                        _who(rec), sn.get("label", kind), sn.get("count_24h", "?"))

    # ③ 主动发帖：默认关（`garden_forum_daily_post=0`）。一拍只问模型一次，所以②问过就不再③
    limit = beat.read_limit(cfg, "garden_forum_daily_post", 0)
    day = beat.beijing_day(now)
    if limit and not asked and beat.may_post((doc.get("posts") or {}).get(cid), day, limit):
        await _try_post(gf, doc, cid, rec, cfg, now, st, day)
    return False


async def _try_post(gf: GardenForum, doc: dict, cid: str, rec: dict, cfg: dict,
                    now: float, st: dict, day: str) -> None:
    """一天最多 N 帖。⚠️ **不论成败都记一笔**：失败多半是余额不足/标题太短这类"今天再试也没用"的，
    不记就会每拍重试、每拍白烧一次模型调用。"""
    doc.setdefault("posts", {})[cid] = beat.bump_posts((doc.get("posts") or {}).get(cid), day)
    persona: dict = {}
    try:
        persona = dict(await sdk.get_persona(int(cid)) or {})
    except Exception:
        pass
    persona.setdefault("name", rec.get("name") or "")
    messages = beat.build_post_messages(persona, cfg.get("garden_forum_tone"),
                                        st.get("today_threads"), st.get("circles"))
    try:
        from app.agent.llm_client import chat_completion
        raw = await asyncio.wait_for(
            chat_completion(messages=messages, temperature=0.9, max_tokens=260,
                            user_id=rec.get("user_id"), character_id=int(cid)),
            timeout=LLM_BUDGET_SEC)
    except Exception as e:
        sdk.log("garden_forum 角色 %s 发帖没拿到模型回复: %s", _who(rec), type(e).__name__)
        return
    title, body = beat.parse_post(raw)
    if not title:
        return                                    # 沉默或残缺＝今天不发，额度照样记掉（见上）
    r = await asyncio.to_thread(gf.say, "act.post", title=title, text=body,
                                reason=_REASON_POST, raises=False)
    if not r.get("ok"):
        sdk.log("garden_forum 角色 %s 的帖子没发出去: %s", _who(rec), r.get("code"))
        return
    sdk.log("garden_forum 角色 %s 发了一篇《%s》", _who(rec), title[:20])


@sdk.hook("context_inject")
async def on_inject(ctx):
    """把论坛经历注进聊天上下文——**只读本地文件，不联网、不计费**。

    为什么不在这里现拉 `state`：这个钩子在对话链路上，多一次外网往返就多一分把聊天拖慢的风险，
    而"拥爱不该被论坛拖垮"是硬要求。摘要由心跳顺手存盘（`digest_zh`），这里只做搬运。
    论坛没给这个字段时（M2 派单尚未落地）这里就是空转，什么都不注入。
    """
    try:
        cfg = sdk.get_config() or {}
        if not cfg.get("garden_forum_on", False) or not cfg.get("garden_forum_inject", True):
            return
        cid = (ctx or {}).get("character_id")
        msgs = (ctx or {}).get("context_messages")
        if cid is None or not isinstance(msgs, list):
            return
        text = beat.render_digest((store.load().get("digest") or {}).get(str(cid)), time.time())
        if text:
            msgs.append({"role": "system", "content": text})
    except Exception as e:
        sdk.log("garden_forum 注入异常: %s", e)


def _speak_target(st: dict, doc: dict, cid: str, now: float):
    """这一拍要说就只说一条：优先回「别人在我帖下的留言」，其次回未读私信。

    两类都**先记账再过模型**（记的是"这条已经拿去过模型了"，不是"已经回复了"）：
    论坛筛 `replies_to_me` 只看"我回过没有"、私信 `read_at` 又只有网页端会置位，
    所以模型一旦选择沉默，同一条内容会原样回来——不记这笔就会每拍重问一次（白烧 token），
    而且评论优先会让私信永远排不进去（端到端桩测实测到的饥饿）。
    """
    reply = beat.pick_reply_target(st.get("replies_to_me"), store.seen_replies(doc, cid, now))
    if reply is not None:
        store.mark_reply(doc, cid, reply.get("id"), now)
        return reply, "comment"
    dms = beat.pick_dm_target(st.get("unread_dm") or [], store.seen_dms(doc, cid, now), cap=1)
    if dms:
        store.mark_dm(doc, cid, dms[0].get("id"), now)
        return dms[0], "dm"
    return None, ""


async def _say(gf: GardenForum, kind: str, target: dict, text: str) -> dict:
    if kind == "dm":
        return await asyncio.to_thread(gf.say, "act.message", text=text,
                                       to=target.get("from_agent") or "",
                                       reason=_REASON_DM, raises=False)
    # parent_id 必带：论坛靠「这条评论下面有没有我的回复」筛 replies_to_me，
    # 只带 post_id 会把同一条留言反复捞上来，变成对着同一个人自问自答。
    return await asyncio.to_thread(gf.say, "act.comment", text=text,
                                   post_id=target.get("post_id"), parent_id=target.get("id"),
                                   reason=_REASON_COMMENT, raises=False)


async def _ask_llm(cid: str, rec: dict, kind: str, target: dict, cfg: dict) -> str:
    """唯一花 token 的地方。失败／超时／没配 key 一律回空串＝这一拍不说。"""
    persona: dict = {}
    try:
        persona = dict(await sdk.get_persona(int(cid)) or {})
    except Exception:
        pass                                    # 权限没声明或被家庭校验拦了，就用绑定记录里的名字兜底
    persona.setdefault("name", rec.get("name") or "")
    messages = beat.build_messages(persona, cfg.get("garden_forum_tone"), kind, target)
    try:
        from app.agent.llm_client import chat_completion
        raw = await asyncio.wait_for(
            chat_completion(messages=messages, temperature=0.8, max_tokens=200,
                            user_id=rec.get("user_id"), character_id=int(cid)),
            timeout=LLM_BUDGET_SEC)
    except Exception as e:
        sdk.log("garden_forum 角色 %s 没拿到模型回复（本轮不说）: %s", _who(rec), type(e).__name__)
        return ""
    return beat.parse_reply(raw)


def _fail_note(doc: dict, cid: str, rec: dict, e: GardenError, now: float) -> bool:
    """按故障类型决定「停整轮」还是「推迟这个角色」，日志里只出现 code，不出现凭据。"""
    if beat.stop_beat_on_error(e.code):
        doc["site_block_until"] = beat.site_block_until(now, e.code, SITE_BACKOFF_SEC)
        sdk.log("garden_forum 站点级故障（%s），本轮结束；%d 分钟内不再探测论坛", e.code, SITE_BACKOFF_SEC // 60)
        return True
    gap = BROKEN_POSTPONE_SEC if beat.binding_broken(e.code) else RETRY_POSTPONE_SEC
    store.set_next_at(doc, cid, now + gap)
    if beat.binding_broken(e.code):
        sdk.log("garden_forum 角色 %s 的 key 已失效（%s）：本地绑定保留，请跑 garden_setup.py bind 重绑",
                _who(rec), e.code)
    else:
        sdk.log("garden_forum 角色 %s 读现状失败（%s），%d 分钟后再试", _who(rec), e.code, gap // 60)
    return False


async def _in_dnd(cid: str) -> bool:
    """免打扰：内核的 DND 闸只管「主动消息推给用户」，插件出网不经过它 ⇒ 自己判。"""
    cn_now = datetime.now(timezone(timedelta(hours=8)))
    try:
        from app.scheduling.gates import is_dnd_now
        return bool(await is_dnd_now(int(cid), cn_now))
    except Exception:
        return cn_now.hour < 7          # 读不到角色配置就沿用内核硬编码的深夜 0-7 点
