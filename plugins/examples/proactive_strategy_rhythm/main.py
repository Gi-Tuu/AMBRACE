"""示例策略包：随机想起（节律）（X6-b proactive_strategy —— rhythm 类）

策略包只回答两件事：
    1. **今天该不该发起**（时段 + 概率 + 角色状态）；
    2. **发起哪一类行为**（greeting / status_update / proactive_chat / goodnight /
       moment_publish / moment_comment）。

**绝对不要**（全是内核职责，写进插件就会双发或绕过频控）：
    ✗ 选人（谁有资格被考虑）       → 内核经 ``ctx["roster"]`` 下发；
    ✗ 频控 / 去重 / 免打扰         → 内核：每日上限、pending 计时器/剧情线互斥、
                                     arbiter 统一闸门（最小间隔 / 每小时 / 未回复冷却）；
    ✗ 素材装配（会话/最近消息/闲置）→ 内核 prepare_strategy_candidate 补齐；
    ✗ 自己发消息                   → 内核统一发送（所以本包不需要 send_message 权限）。

素材（只读，按需 pull）：
    - ``time_ctx``：北京日期 / 小时 / 时段名 / 时段倾向（全局，一次取）；
    - ``character_state``：角色八维状态（按角色取，仅在概率命中后才取，省查询）。

运行前提：内核 flag ``proactive_strategy_plugins`` 开启 **且** 本包启用。
flag 关闭时内核不下发 roster → 本 hook 直接返回 None（零行为变化，防双发）。
"""
import random

from app.plugins import sdk

# 必须与注册口径一致：内核据此判定「rhythm 已被接管」并让位；白名单防伪造落库口径
STRATEGY = "rhythm"
# 本包可产出的行为（= 注册给内核的 message_type 白名单）
BEHAVIORS = (
    "greeting", "status_update", "proactive_chat", "goodnight",
    "moment_publish", "moment_comment",
)

sdk.register_proactive_strategy(STRATEGY, message_types=list(BEHAVIORS))


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


def _probability(cfg, window):
    """该时段的发起概率（windows 覆盖 > 全局 probability > 兜底 0.4）。"""
    try:
        table = cfg.get("windows") or {}
        if window and window in table:
            return _as_float(table.get(window), 0.4)
    except Exception:
        pass
    return _as_float(cfg.get("probability"), 0.4)


def _candidates_behaviors(cfg, window, tendencies):
    """该时段的候选行为（配置表 > time_ctx 时段倾向；都为空则不投）。"""
    table = cfg.get("behaviors") or {}
    got = [str(b) for b in (table.get(window) or []) if str(b) in BEHAVIORS]
    if not got:
        got = [str(b) for b in (tendencies or []) if str(b) in BEHAVIORS]
    return got


def _pick(behaviors, state, cfg):
    """从候选行为里选一个：状态偏置优先，否则按「首选 70% / 次选 30%」加权。"""
    if not behaviors:
        return None
    if len(behaviors) == 1:
        return behaviors[0]
    fatigue = _as_int((state or {}).get("fatigue"), 50)
    mood = _as_int((state or {}).get("mood"), 50)
    if fatigue >= _as_int(cfg.get("tired_fatigue"), 75) and "goodnight" in behaviors:
        return "goodnight"
    if mood >= _as_int(cfg.get("happy_mood"), 75) and "status_update" in behaviors:
        return "status_update"
    return behaviors[0] if random.random() < 0.7 else behaviors[1]


# ─────────────── 策略核心 ───────────────

@sdk.hook("proactive_candidate")
async def rhythm_strategy(ctx):
    """内核每 tick 调用：只把 roster 映射成候选，不做任何节流/去重/素材装配/发送。

    ctx（仅 flag 开且本类别被接管时才有）：
        {"strategy_categories": ["rhythm"], "roster": [{character_id, user_id, session_id,
          character_name, nickname, ...}, ...]}
    """
    ctx = ctx or {}
    if STRATEGY not in (ctx.get("strategy_categories") or []):
        return None                     # flag 关 / 本类别未被接管 → 零输出（防双发）
    roster = ctx.get("roster") or []
    if not roster:
        return None

    cfg = sdk.get_config() or {}
    try:
        tctx = (await sdk.get_proactive_context(["time_ctx"])).get("time_ctx") or {}
    except Exception:
        return None                     # 素材端口 fail-open：拿不到时间就不发
    window = str(tctx.get("window") or "")
    if not window:
        return None                     # 凌晨等非活跃时段不发起（让位后也不空转）
    prob = _probability(cfg, window)

    out = []
    for entry in roster:
        try:
            if random.random() >= prob:
                continue
            behaviors = _candidates_behaviors(cfg, window, tctx.get("tendencies"))
            if not behaviors:
                continue
            state = {}
            try:
                data = await sdk.get_proactive_context(
                    ["character_state"], character_id=entry.get("character_id"),
                )
                state = data.get("character_state") or {}
            except Exception:
                state = {}              # 状态取不到也能发（只是少了偏置）
            behavior = _pick(behaviors, state, cfg)
            if not behavior:
                continue
            out.append({
                "character_id": entry.get("character_id"),
                "user_id": entry.get("user_id"),
                "session_id": entry.get("session_id"),
                "strategy": STRATEGY,           # 告诉内核：这是策略候选，请按声明口径执行/去重
                "message_type": behavior,       # 内核执行路由：走该行为的既定执行链
                "behavior": behavior,
                "hint": f"现在是{window}，以{entry.get('character_name') or ''}的口吻自然地发起一次{behavior}",
            })
        except Exception:
            continue                    # 单角色失败不影响其他角色
    return out or None
