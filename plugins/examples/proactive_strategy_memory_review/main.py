"""示例策略包：主动到期复习（X6-b proactive_strategy —— memory_review 类）

策略包只回答一件事：**复习哪条记忆**（从内核下发的到期条目里挑一条）。

**绝对不要**（全是内核职责）：
    ✗ 选人（谁有资格被考虑）       → 内核经 ``ctx["roster"]`` 下发；
    ✗ 日上限 / 抽检间隔 / 时态闸门 → 内核 ``run_memory_review`` 内部，本包绕不开也不该绕；
    ✗ 去重 / 免打扰 / 发送         → 内核 arbiter 统一闸门 + 按 message_type 当日去重；
    ✗ 自己发消息                   → 内核统一发送（所以本包不需要 send_message 权限）。

素材（只读，按需 pull）：``due_reviews``（该角色当前到期/待复习的记忆条目，内核既定
到期与时态口径，已按角色隔离，不含其他角色的数据）。

运行前提：内核 flag ``proactive_strategy_plugins`` 开启 **且** 本包启用。
flag 关闭时内核不下发 roster → 本 hook 直接返回 None（零行为变化，防双发）。
"""
from app.plugins import sdk

# 必须与注册口径一致：内核据此判定「memory_review 已被接管」并让位
STRATEGY = "memory_review"
# 落库口径沿用内核既有 REVIEW_TYPE（"memory_review"），**不要新建一套**——
# 否则内核去重与统计都会失效。
MESSAGE_TYPE = "memory_review"

sdk.register_proactive_strategy(STRATEGY, message_types=[MESSAGE_TYPE])


def _as_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


@sdk.hook("proactive_candidate")
async def review_strategy(ctx):
    """内核每 tick 调用：把「到期复习条目」映射成候选，不做限额/去重/生成/发送。

    ctx（仅 flag 开且本类别被接管时才有）：
        {"strategy_categories": ["memory_review"], "roster": [{character_id, user_id,
          session_id, ...}, ...]}
    """
    ctx = ctx or {}
    if STRATEGY not in (ctx.get("strategy_categories") or []):
        return None                     # flag 关 / 本类别未被接管 → 零输出（防双发）
    roster = ctx.get("roster") or []
    if not roster:
        return None

    cfg = sdk.get_config() or {}
    max_per_char = max(1, _as_int(cfg.get("max_per_char"), 1))
    min_importance = _as_int(cfg.get("min_importance"), 40)

    out = []
    for entry in roster:
        try:
            data = await sdk.get_proactive_context(
                ["due_reviews"], character_id=entry.get("character_id"),
            )
        except Exception:
            continue                    # 素材端口 fail-open：取不到就不投（不阻塞主链路）
        items = data.get("due_reviews") or []
        picked = 0
        for m in items:
            if picked >= max_per_char:
                break
            mid = m.get("id")
            if not mid:
                continue
            if float(m.get("importance") or 0) < min_importance:
                continue
            out.append({
                "character_id": entry.get("character_id"),
                "user_id": entry.get("user_id"),
                "session_id": entry.get("session_id"),
                "strategy": STRATEGY,       # 告诉内核：这是策略候选，请按声明口径执行/去重
                "message_type": MESSAGE_TYPE,
                "memory_id": int(mid),      # 执行归内核：run_memory_review(char, user, memory_id)
            })
            picked += 1
    return out or None
