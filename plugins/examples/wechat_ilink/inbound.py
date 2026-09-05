# -*- coding: utf-8 -*-
"""iLink 入站消息标准化（纯解析层，PR1，不挂钩子/不读写 DB）。

【命名空间】渠道插件顶层模块名易撞，本插件沿用包内相对导入/文件名，新渠道须用包内相对导入或模块名前缀（registry 加载器保持现状）。

把 iLink 长轮询返回的、可能随微信版本变化的易变消息结构，收敛为固定字段的标准化 dict，
隔离协议变更。字段真实路径以真机扫码联调为准（NEEDS_RUNTIME_VERIFICATION），这里先按
§8.5 的多候选字段名兜底，真机校准后只改本文件。

本文件 ``_std_inbound`` / ``_inbound_placeholder`` 只包含纯函数（可独立单测），对外零依赖；
缺字段/非文本/空文本一律不抛异常。PR3 起在同一文件追加入站消费器（``poll_once`` /
``_process_inbound`` / ``_process_reply``），其 app 依赖全部**函数内惰性 import**，
以便纯解析层在任何环境可独立加载、消费逻辑在测试里可注入 mock。

消费器（PR3，§8.5）要点：
- ``LAST_TICK_AT``：插件自行节流（对齐 schedule_tick 30s）+ 每 binding 重入锁防并发 tick；
- 长轮询超时 < tick（25s < 30s），时间预算内连续拉空，退出前持久化游标；
- ``wechat_ilink_state.json`` 轻状态文件兜底游标（同 douyin_*_state.json 惯例）；
- 异常隔离：ILinkClient 出错/超时吞掉只记日志（P0-5），绝不影响主链路；
- 入站 → 重置配额窗口 → 走主认知链路 ``chat_service.send_and_receive`` → 整段 1 次 send_text；
- 幂等：``ilink_msg_id`` 唯一约束 + select 双保险，不重不漏。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

# 纯解析层（Pr1）：单候选字段名多兜底（真机核实前两套都兜底；NEEDS_RUNTIME_VERIFICATION）
_TEXT_KEYS = ("content", "text", "message", "msg_text")
_MSG_ID_KEYS = ("msg_id", "message_id", "id", "msgid")
_MSG_TYPE_KEYS = ("msg_type", "type", "kind")
_CONTEXT_KEYS = ("context_token", "reply_token", "context")

_TEXT = "text"


def _first_str(raw: dict, keys: tuple[str, ...]) -> str:
    """从候选字段名里取第一个非 None 值并转 str；全缺失返回空串。"""
    for k in keys:
        v = raw.get(k)
        if v is None:
            continue
        return v if isinstance(v, str) else str(v)
    return ""


def _std_inbound(raw) -> dict:
    """把易变入站消息标准化为固定字段；缺字段/非文本/空文本一律不抛。

    返回字段：
      msg_id         平台侧消息唯一 ID（去重键；多候选路径兜底）
      text           去除首尾空白后的文本（非文本消息通常为空）
      context_token  引用票据（回复该条时带上；不带=主动推送）
      msg_type       归一化消息类型（缺失时默认按 text 处理）
      is_text        是否文本消息（v1 只处理文本，非文本由消费层做占位/跳过）
    """
    if not isinstance(raw, dict):
        return {"msg_id": "", "text": "", "context_token": "", "msg_type": _TEXT, "is_text": False}
    msg_type = _first_str(raw, _MSG_TYPE_KEYS).strip().lower() or _TEXT
    return {
        "msg_id": _first_str(raw, _MSG_ID_KEYS),
        "text": _first_str(raw, _TEXT_KEYS).strip(),
        "context_token": _first_str(raw, _CONTEXT_KEYS),
        "msg_type": msg_type,
        "is_text": msg_type == _TEXT,
    }


def _inbound_placeholder(inb: dict) -> str:
    """非文本消息的 v1 占位文本（不把图片/语音当对话文本）。

    二进制/URL 不进入主认知链路；图片/语音/文件返回统一占位（P2-2 才做真实 CDN 理解）。
    """
    if not isinstance(inb, dict):
        return ""
    if inb.get("msg_type") == _TEXT:
        return inb.get("text") or ""
    kind = (inb.get("msg_type") or "").strip().lower()
    return f"[{kind}]" if kind else "[media]"


# ==================================================================== 轮询游标轻状态（P0-3）
# 与 douyin_*_state.json 同目录惯例（backend/data/plugins/），DB 更新失败时兜底防丢游标。
_STATE_DIR = Path(__file__).resolve().parents[3] / "backend" / "data" / "plugins"
_STATE_FILE = _STATE_DIR / "wechat_ilink_state.json"


def _load_state() -> dict:
    """读取轻状态文件；缺失/损坏返回空 dict（绝不抛）。"""
    try:
        st = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def _read_cursor(binding_id: int) -> str:
    """读取持久化游标（轻状态文件兜底）；无则返回空串。"""
    st = _load_state()
    buf = (st.get("poll_buf") or {}).get(str(binding_id), "")
    return str(buf) if buf else ""


def _save_cursor(binding_id: int, buf: str) -> None:
    """镜像游标到轻状态文件（失败静默，DB 兜底）。"""
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        st = _load_state()
        st.setdefault("poll_buf", {})[str(binding_id)] = buf
        _STATE_FILE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# ==================================================================== schedule_tick 节流/重入
# LAST_TICK_AT：插件自行节流（内核固定 30s 调一次，配置可改成更大间隔）。
LAST_TICK_AT: dict[str, float] = {"t": 0.0}
# 每 binding 一把重入锁：上一轮还没跑完的 binding，本轮直接跳过（P1-2 防并发 tick）。
_locks: dict[int, asyncio.Lock] = {}


def _reset_locks() -> None:
    """清理重入锁（测试隔离用；不影响生产，生产锁随对象生命周期自然释放）。"""
    _locks.clear()
    LAST_TICK_AT["t"] = 0.0


# ==================================================================== 主认知链路封装
async def _run_companion_reply(user_id: int, character_id: int, text: str) -> str:
    """走主认知链路（§8.5 真实入口）生成整段回复（落会话 + 记忆，App 内同样可见）。

    【调用点与签名核对（以现状代码为准，只读不改内核）】
    真实入口 = ``app/application/chat_service.send_and_receive``：
      ``send_and_receive(session_id, user_id, character_id, content, lang="zh",
                         quote=None, reply_delay=True) -> dict``
      -- 返回 ``{"ai_message": {"content": ...}, "memories_updated": ...}``。
    需要 ``session_id``：经 ``chat_service.create_session(user_id, character_id)`` 取/建最新活跃会话
      （App 标准取会话语义，复用最新活跃会话）。``reply_delay=False`` 对齐外部自动化通道
      （P3-5：非人工点击发送，跳过自然延迟）。``channel="wechat_ilink"`` 标注渠道来源（任务 A：
      App 内消息与微信桥消息可区分来源，sam 回复/记忆提炼时知道本条来自微信渠道）。
    本函数返回整段文本（非流式），保证消费层一次 send_text 合并发送（P1-1）。
    """
    from app.application.chat_service import create_session, send_and_receive  # noqa: PLC0415

    # P3-5（2026-09-05）：回复语言按绑定所属 User 的 lang（zh/en）；缺失/异常回落 zh，不再写死 zh。
    lang = "zh"
    try:
        from app.models.user import User  # noqa: PLC0415
        from app.db.database import async_session_factory  # noqa: PLC0415
        async with async_session_factory() as _db:
            _u = await _db.get(User, int(user_id))
            if _u is not None:
                _v = str(_u.lang or "").strip().lower()
                lang = _v if _v in ("zh", "en") else "zh"
    except Exception:  # noqa: BLE001 - 语言读取失败回落 zh，不影响主链路
        lang = "zh"

    try:
        session = await create_session(int(user_id), int(character_id))
        sid = session.get("id") if isinstance(session, dict) else None
        if not sid:
            return ""
        result = await send_and_receive(
            session_id=int(sid), user_id=int(user_id), character_id=int(character_id),
            content=str(text), lang=lang, reply_delay=False,
            channel="wechat_ilink",
        )
        ai = result.get("ai_message") if isinstance(result, dict) else None
        return (ai or {}).get("content") or ""
    except Exception:
        # P0-5：主链路异常（LLM 未配 key/失败/网络抖等）只记日志，绝不炸掉 schedule_tick。
        # 入场消息已落库（throttle 之外），此处返回空回复即本轮无 AI 回复，不重复重试轰炸。
        from app.plugins import sdk  # noqa: PLC0415
        sdk.log("wechat_ilink companion reply 异常（返回空回复）")
        return ""


# ==================================================================== 单条入站处理
async def _load_binding_for_update(db, binding_id: int):
    """加载 binding 行；PG 用行锁（FOR UPDATE）保证计数 read-modify-write 原子（P0-2）。

    SQLite 忽略 FOR UPDATE（单写语义已满足），SQLAlchemy 对不支持的方言不渲染该子句，无害。
    """
    from sqlalchemy import select  # noqa: PLC0415
    from models import WeChatILinkBinding  # noqa: PLC0415

    q = select(WeChatILinkBinding).where(WeChatILinkBinding.id == int(binding_id)).with_for_update()
    return (await db.execute(q)).scalars().first()


async def _process_inbound(binding_id: int, inb: dict, gate, client_factory, session_factory) -> bool:
    """处理一条入站消息：

    1. 幂等（ilink_msg_id 去重：select + 唯一约束双保险，不重不漏）；
    2. 重置配额窗口 + 落库入场消息（in，status=ok）；
    3. 走主认知链路生成整段回复；
    4. 进入 ``_process_reply``：QuotaGate 放行则 sendmessage、超限则 deferred（回复照常落 App）。

    非文本/空文本不入主链路（返回 False，不落库）。
    """
    if not isinstance(inb, dict) or not inb.get("text") or not inb.get("is_text"):
        return False

    from sqlalchemy import select  # noqa: PLC0415
    from models import WeChatILinkMessage  # noqa: PLC0415

    async with session_factory() as db:
        binding = await _load_binding_for_update(db, binding_id)
        if binding is None or not binding.enabled:
            return False
        uid, cid = int(binding.user_id), int(binding.character_id)
        # 幂等：同 ilink_msg_id 只处理一次（空 msg_id 无法去重，放行；唯一约束只约束非空）
        if inb.get("msg_id"):
            dup = (await db.execute(
                select(WeChatILinkMessage.id).where(
                    WeChatILinkMessage.binding_id == binding.id,
                    WeChatILinkMessage.ilink_msg_id == inb["msg_id"],
                    WeChatILinkMessage.direction == "in",
                )
            )).first()
            if dup:
                return False
        # 2) 用户入站 → 重置配额窗口（同一事务内与入场消息一起 commit）
        gate.on_inbound(binding)
        db.add(WeChatILinkMessage(
            binding_id=binding.id, character_id=cid, ilink_msg_id=inb["msg_id"],
            context_token=inb["context_token"], direction="in", content=inb["text"], status="ok",
        ))
        await db.commit()

    # 3) 主认知链路生成整段回复（不拆分；App 内同会话可见）
    reply = await _run_companion_reply(uid, cid, inb["text"])
    if not reply:
        return False

    # 4) 配额闸门 + 整段一次发送/降级
    await _process_reply(binding_id, inb, reply, gate, client_factory, session_factory)
    return True


async def _process_reply(binding_id: int, inb: dict, reply: str, gate, client_factory, session_factory) -> None:
    """回复出站：QuotaGate 放行 → 整段 1 次 send_text；超限/失败 → 落库 out 并标记（P1-1/§11.1#9）。

    - 放行且发送成功：out status=ok、quota_charged=True；
    - 放行但发送失败/返回非 ok：out status=failed、quota_charged=False（不浪费额度）；
    - 配额不足：out status=deferred、quota_charged=False（App 内已送达，微信侧延后）。
    计数读取+更新在同一事务/行锁内完成（gate.acquire 仅放行时+1）。
    """
    from models import WeChatILinkMessage  # noqa: PLC0415

    async with session_factory() as db:
        binding = await _load_binding_for_update(db, binding_id)
        if binding is None:
            return
        decision = gate.can_acquire(binding)
        out_status, charged = "deferred", False
        client = None
        try:
            if decision.allowed and reply:
                client = client_factory(binding.bot_token_enc, binding.baseurl)
                try:
                    res = await client.send_text(reply, context_token=inb.get("context_token") or None)
                    ok = isinstance(res, dict) and res.get("ok")
                    if ok:
                        gate.acquire(binding)  # 发送成功才计入配额（read+write 同一事务）
                        out_status, charged = "ok", True
                    else:
                        out_status, charged = "failed", False
                except Exception:
                    from app.plugins import sdk  # noqa: PLC0415
                    sdk.log("wechat_ilink sendmessage 异常")
                    out_status, charged = "failed", False
        finally:
            if client is not None:  # W2（v3.4.4 审查）：httpx.AsyncClient 用完必关，防长跑句柄缓增
                try:
                    await client.aclose()
                except Exception:
                    pass
        db.add(WeChatILinkMessage(
            binding_id=binding.id, character_id=binding.character_id, ilink_msg_id="",
            context_token=inb.get("context_token") or "", direction="out", content=reply,
            quota_charged=charged, status=out_status,
        ))
        await db.commit()


# ==================================================================== 一个 tick：时间预算内连续拉空
async def poll_once(*, client_factory, interval: int = 30, long_poll: int = 25,
                    quota: int = 10, session_factory=None) -> None:
    """一个 tick 内：对每个 enabled binding 在时间预算内反复长轮询，直到拉空或预算耗尽。

    - ``client_factory(bot_token_enc, baseurl, timeout)`` 返回具备 ``get_updates``/``send_text``
      的客户端（生产=port.make_client 解密 token 建 ILinkClient；测试=mock）；
    - 重入锁防并发 tick（上一轮未跑完的 binding 本轮跳过）；
    - ILinkClient 出错/超时（get_updates 返回 ok=False）只记日志并跳出该 binding，不影响其它；
    - 游标持久化：每批消息处理后写 DB poll_buf + 轻状态文件兜底。
    """
    from app.db.database import async_session_factory as _asf  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415
    from models import WeChatILinkBinding  # noqa: PLC0415
    from quota import QuotaGate  # noqa: PLC0415

    sf = session_factory or _asf
    gate = QuotaGate(quota) if not hasattr(quota, "n") else quota
    budget = max(0, min(interval - 3, long_poll))

    # 只读一次取 enabled binding（字段先快照，离开会话后不再访问 ORM 属性）
    async with sf() as db:
        rows = (await db.execute(
            select(WeChatILinkBinding).where(WeChatILinkBinding.enabled == True)  # noqa: E712
        )).scalars().all()
        bindings = [(b.id, b.character_id, b.poll_buf, b.bot_token_enc, b.baseurl) for b in rows]

    async def _poll_one(bid, _cid, db_buf, token_enc, baseurl) -> None:
        # W1（v3.4.4 审查）：每个 binding 独立时间预算——旧实现 deadline 在循环外只算一次，
        # 首个 binding 的 25s 长轮询会吃光预算、其余 binding 整轮被饿死（多角色收不到消息）。
        lock = _locks.setdefault(bid, asyncio.Lock())
        if lock.locked():  # 重入锁语义保持：上一 tick 未跑完，本 tick 跳过该 binding
            return
        async with lock:
            local_deadline = time.monotonic() + budget
            client = None
            try:
                buf = _read_cursor(bid) or (db_buf or "")
                client = client_factory(token_enc, baseurl, timeout=int(long_poll))
                while time.monotonic() < local_deadline:
                    data = await client.get_updates(buf)  # 长轮询（timeout < tick）
                    if not isinstance(data, dict) or not data.get("ok"):
                        from app.plugins import sdk  # noqa: PLC0415
                        msg = data.get("message") if isinstance(data, dict) else data
                        sdk.log("wechat_ilink getupdates 失败（隔离）: %s", msg)
                        break
                    new_buf = data.get("buf") or buf
                    msgs = data.get("messages") or []
                    if not msgs:
                        buf = new_buf
                        break
                    for raw in msgs:
                        await _process_inbound(bid, _std_inbound(raw), gate, client_factory, sf)
                    buf = new_buf
                    await _persist_cursor(bid, buf, sf)
            except Exception:
                from app.plugins import sdk  # noqa: PLC0415
                sdk.log("wechat_ilink poll_once binding %s 异常（隔离）", bid)
            finally:
                if client is not None:  # W2：client 用完必关（含异常路径），防长跑连接缓增
                    try:
                        await client.aclose()
                    except Exception:
                        pass

    # binding 数量通常 1~3，并发开销可忽略；return_exceptions 保证单 binding 失败不影响其它
    if bindings:
        await asyncio.gather(
            *(_poll_one(*b) for b in bindings),
            return_exceptions=True,
        )


async def _persist_cursor(binding_id: int, buf: str, session_factory) -> None:
    """持久化游标：轻状态文件兜底 + DB poll_buf 更新（同事务写，失败静默）。"""
    if buf:
        _save_cursor(binding_id, buf)
    from sqlalchemy import update  # noqa: PLC0415
    from models import WeChatILinkBinding  # noqa: PLC0415

    async with session_factory() as db:
        await db.execute(
            update(WeChatILinkBinding).where(WeChatILinkBinding.id == int(binding_id)).values(poll_buf=buf)
        )
        await db.commit()
