"""示例策略包：对话未收尾跟进（X6-c proactive_strategy —— unfinished_topic 类）

策略包只回答一件事：**哪个话头值得追问**（从内核下发的未收尾话题里挑一个）。

**绝对不要**（全是内核职责，写进插件就会双发或绕过频控）：
    ✗ 选人（谁有资格被考虑）              → 内核经 ``ctx["roster"]`` 下发；
    ✗ 每日 1 条配额 / 最小间隔 / 免打扰 / 未回复冷却 → 内核 prepare（本类有独立配额）；
    ✗ 话头正文                            → 内核按 ``topic_id`` 复核后补齐（防伪造/防过期）；
    ✗ 去重 / 生成 / 发送                  → 内核（落库口径 ``unfinished_topic``）。

**本类别的两个口径（迁移前先钉死，否则会重演 rhythm 的「去重闸空转」）**：

- 落库 ``message_type = "unfinished_topic"``：与内核 ``run_unfinished_topic`` →
  ``send_to_session(message_type="unfinished_topic")`` 同口径，**不要新建一套**；
- 当日去重 = ``proactive_message_logs`` 中 (角色, ``unfinished_topic``, 北京日界) 已有行
  （内核 ``strategy.CATEGORY_DEDUP`` 登记为 ``("message_log", "day")``，与
  ``unfinished_topic._used_today`` 同口径）——本类消息不是剧情线，这条闸**真的会命中**。

素材（只读，按需 pull）：
    - ``open_topics``：该角色进行中且时效内的话题（id / 文本 / 重要度 / 是否目标 / 距上次提及小时数）；
    - ``recent_intents``：该角色未完成的前瞻意图（只用来**避开**已经在追的话头，防撞车）。

运行前提：内核 flag ``proactive_strategy_plugins`` 开启 **且** 本包启用。
flag 关闭时内核不下发 roster → 本 hook 直接返回 None（零行为变化，防双发）。
"""
from app.plugins import sdk

# 必须与注册口径一致：内核据此判定「unfinished_topic 已被接管」并让位
STRATEGY = "unfinished_topic"
# 落库口径沿用内核既有值（_used_today / send_to_session 都按它去重），**不要新建一套**
MESSAGE_TYPE = "unfinished_topic"

sdk.register_proactive_strategy(STRATEGY, message_types=[MESSAGE_TYPE])


# ─────────────── 配置读取（全部带兜底，配置写错也不抛错）───────────────

def _as_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def _as_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


def _overlap(a: str, b: str) -> bool:
    """两个话头是否互相包含（用来判断「这条已经被前瞻意图在追了」）。"""
    a, b = (a or "").strip(), (b or "").strip()
    if len(a) < 2 or len(b) < 2:
        return False
    return a in b or b in a


@sdk.hook("proactive_candidate")
async def unfinished_strategy(ctx):
    """内核每 tick 调用：只把 roster 映射成候选，不做节流/去重/正文装配/发送。

    ctx（仅 flag 开且本类别被接管时才有）：
        {"strategy_categories": ["unfinished_topic"], "roster": [{character_id, user_id,
          session_id, ...}, ...]}
    """
    ctx = ctx or {}
    if STRATEGY not in (ctx.get("strategy_categories") or []):
        return None                     # flag 关 / 本类别未被接管 → 零输出（防双发）
    roster = ctx.get("roster") or []
    if not roster:
        return None

    cfg = sdk.get_config() or {}
    min_importance = _as_float(cfg.get("min_importance"), 0.6)
    min_hours = _as_float(cfg.get("min_hours_since"), 2.0)
    max_per_char = max(1, _as_int(cfg.get("max_per_char"), 1))

    out = []
    for entry in roster:
        try:
            data = await sdk.get_proactive_context(
                ["open_topics", "recent_intents"], character_id=entry.get("character_id"),
            )
        except Exception:
            continue                    # 素材端口 fail-open：取不到就不投（不阻塞主链路）

        topics = data.get("open_topics") or []
        if not topics:
            continue
        # 已在追的前瞻意图：与它撞车的话头不再追问（避免与 prospective_intent 双线追同一件事）
        chasing = [str(i.get("summary") or "") for i in (data.get("recent_intents") or [])]

        picked = 0
        for t in topics:
            if picked >= max_per_char:
                break
            tid = t.get("id")
            if not tid:
                continue
            if _as_float(t.get("importance"), 0.0) < min_importance:
                continue
            if _as_float(t.get("hours_since"), 0.0) < min_hours:
                continue                # 刚聊过的话头不急着追（内核还有 2h 最小间隔兜底）
            text = str(t.get("topic") or "")
            if any(_overlap(text, s) for s in chasing):
                continue
            out.append({
                "character_id": entry.get("character_id"),
                "user_id": entry.get("user_id"),
                "session_id": entry.get("session_id"),
                "strategy": STRATEGY,     # 告诉内核：这是策略候选，请按声明口径执行/去重
                "message_type": MESSAGE_TYPE,
                "topic_id": int(tid),     # 只给 id：正文由内核按 id 复核后装配
                "trigger_reason": f"未收尾话题: {text[:40]}",
            })
            picked += 1
    return out or None
