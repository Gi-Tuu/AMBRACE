"""示例策略包：想念驱动主动搭话（X6-c proactive_strategy —— motivation 类）

策略包只回答一件事：**此刻要不要表达渴望、想表达什么**（想念/好奇/想聊）。

**绝对不要**（全是内核职责，写进插件就会双发或绕过频控）：
    ✗ 选人（谁有资格被考虑）          → 内核经 ``ctx["roster"]`` 下发；
    ✗ 关系门（渴望度够不够）           → 内核按关系标量算渴望度并对阈值；
    ✗ 免打扰 / 独立配额（6h 1 条、每日 2 条）→ 内核 prepare（想念通道有独立配额）；
    ✗ 去重                            → 内核按 (角色, 事件类型, 近 6h) 收口；
    ✗ 素材装配（会话/最近消息/闲置时长）→ 内核 prepare 补齐；
    ✗ 自己发消息                      → 内核统一发送（所以本包不需要 send_message 权限）。

素材（只读，按需 pull，一次取齐 4 个 key 省一次 await）：
    - ``quota``：本类别近 6h / 当日已用数与上限（先自查，配额用完就不必再取别的素材）；
    - ``relationship``：trust / attachment / curiosity（0-100 标量）；
    - ``user_rhythm``：距上次用户消息的小时数 + 用户活跃时段权重（0=明显不活跃就别打扰）；
    - ``character_state``：角色八维（desire 亲密渴望 / fatigue 疲惫 / mood 情绪）。

运行前提：内核 flag ``proactive_strategy_plugins`` 开启 **且** 本包启用。
flag 关闭时内核不下发 roster → 本 hook 直接返回 None（零行为变化，防双发）。
"""
from app.plugins import sdk

# 必须与注册口径一致：内核据此判定「motivation 已被接管」并让位
STRATEGY = "motivation"
# 落库/执行口径沿用内核想念通道（arbiter 剧情线分支的独立配额），**不要新建一套**
MESSAGE_TYPE = "motivation"

sdk.register_proactive_strategy(STRATEGY, message_types=[MESSAGE_TYPE])


# ─────────────── 配置读取（全部带兜底，配置写错也不抛错）───────────────

def _as_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def _score(rel: dict, rhythm: dict, state: dict, cfg: dict) -> float:
    """「想不想开口」的粗略打分（0-1+）：依恋/好奇/亲密渴望为主，久未互动加成，疲惫抑制。

    只是**策略层的意图强度**，不是内核闸门——真正的门槛（渴望度阈值、免打扰、配额）
    在内核 prepare 里，本包打分再高也不会绕过。
    """
    att = max(0.0, min(1.0, _as_float(rel.get("attachment"), 50) / 100.0))
    cur = max(0.0, min(1.0, _as_float(rel.get("curiosity"), 50) / 100.0))
    desire = max(0.0, min(1.0, _as_float(state.get("desire"), 50) / 100.0))
    fatigue = max(0.0, min(1.0, _as_float(state.get("fatigue"), 50) / 100.0))
    idle_h = rhythm.get("hours_since_last_user_message")
    if idle_h is None:
        idle_h = 24.0                       # 从未对话 → 按「很久」算（久未互动加成拉满）
    min_idle = _as_float(cfg.get("min_idle_hours"), 2.0)
    idle_factor = max(0.0, min(1.0, (_as_float(idle_h, 0.0) - min_idle) / 12.0))
    base = 0.35 * att + 0.20 * cur + 0.25 * desire
    return (base * (1.0 + 0.5 * idle_factor)
            * (1.0 - 0.4 * min(1.0, fatigue * 100.0 / max(1.0, _as_float(cfg.get("tired_fatigue"), 75)))))


@sdk.hook("proactive_candidate")
async def motivation_strategy(ctx):
    """内核每 tick 调用：只把 roster 映射成候选，不做节流/去重/素材装配/发送。

    ctx（仅 flag 开且本类别被接管时才有）：
        {"strategy_categories": ["motivation"], "roster": [{character_id, user_id,
          session_id, ...}, ...]}
    """
    ctx = ctx or {}
    if STRATEGY not in (ctx.get("strategy_categories") or []):
        return None                     # flag 关 / 本类别未被接管 → 零输出（防双发）
    roster = ctx.get("roster") or []
    if not roster:
        return None

    cfg = sdk.get_config() or {}
    min_score = _as_float(cfg.get("min_score"), 0.45)
    min_idle = _as_float(cfg.get("min_idle_hours"), 2.0)

    out = []
    for entry in roster:
        try:
            data = await sdk.get_proactive_context(
                ["quota", "relationship", "user_rhythm", "character_state"],
                character_id=entry.get("character_id"),
            )
        except Exception:
            continue                    # 素材端口 fail-open：取不到就不投（不阻塞主链路）

        # 配额自查（内核还会再判一次）：用完了就没必要继续取数/投候选
        quota = data.get("quota") or {}
        if quota:
            if int(quota.get("used_6h") or 0) >= int(quota.get("limit_6h") or 99):
                continue
            if int(quota.get("used_today") or 0) >= int(quota.get("limit_day") or 99):
                continue

        rhythm = data.get("user_rhythm") or {}
        weight = _as_float(rhythm.get("weight"), 1.0)
        if rhythm.get("learned") and weight <= 0.0:
            continue                    # 学到作息且此刻明显不活跃 → 别打扰
        idle_h = rhythm.get("hours_since_last_user_message")
        if idle_h is not None and _as_float(idle_h, 0.0) < min_idle:
            continue                    # 用户刚说过话，不急着开口

        score = _score(data.get("relationship") or {}, rhythm,
                       data.get("character_state") or {}, cfg)
        if score < min_score:
            continue

        out.append({
            "character_id": entry.get("character_id"),
            "user_id": entry.get("user_id"),
            "session_id": entry.get("session_id"),
            "strategy": STRATEGY,       # 告诉内核：这是策略候选，请按声明口径执行/去重
            "message_type": MESSAGE_TYPE,
            "behavior": MESSAGE_TYPE,
            "trigger_reason": f"渴望度{score:.2f}/闲置{idle_h}h",
        })
    return out or None
