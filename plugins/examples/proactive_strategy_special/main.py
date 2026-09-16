"""示例策略包：节日 / 生日 / 认识纪念日祝福（X6 proactive_strategy —— special 类）

策略包只回答三件事：
    1. **今天该不该发**（日期判定：是否生日 / 节日 / 认识里程碑天）；
    2. **发哪一类**（birthday / holiday / anniversary）；
    3. **文案怎么说**（模板 → hint，交给内核生成与发送）。

**绝对不要**（全是内核职责，写进插件就会双发或绕过频控）：
    ✗ 选人（谁有资格被考虑）     → 内核经 ``ctx["roster"]`` 下发；
    ✗ 频控 / 去重 / 免打扰       → 内核 arbiter 统一闸门 + 按 message_type 当日去重；
    ✗ 自己发消息                 → 内核统一发送（所以本包不需要 send_message 权限）。

运行前提：内核 flag ``proactive_strategy_plugins`` 开启 **且** 本包启用。
flag 关闭时内核不下发 roster → 本 hook 直接返回 None（零行为变化，防双发）。
"""
import datetime

from app.plugins import sdk

# 必须与 manifest 的 config.strategy_category 一致：内核据此判定「本类别已被接管」并让位
STRATEGY = "special"
# 认识第 N 天算里程碑
ANNIVERSARY_MILESTONES = (7, 30, 100, 365, 730)


# ─────────────── 配置读取（全部带兜底，配置写错也不抛错）───────────────

def _kinds(cfg):
    v = cfg.get("kinds")
    if isinstance(v, list) and v:
        return [str(x) for x in v]
    return ["birthday", "holiday", "anniversary"]


def _as_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


def _render(tpl, **fields):
    try:
        return str(tpl).format(**fields)
    except Exception:
        return str(tpl)


def _mmdd(day):
    return day.strftime("%m-%d")


def _festival_names(cfg, day):
    """策略包自带的节日表（MM-DD → 名称），用户可在扩展页改；不依赖内核日历。"""
    table = cfg.get("festivals")
    if not isinstance(table, dict):
        return []
    name = table.get(_mmdd(day))
    return [str(name)] if name else []


def _anniversary_days(first_session_at, today):
    """认识天数（北京日界口径，与内核 triggers 一致）；无首个会话返回 None。"""
    if not first_session_at:
        return None
    try:
        first = datetime.datetime.fromisoformat(str(first_session_at)) + datetime.timedelta(hours=8)
    except Exception:
        return None
    return (today - first.date()).days + 1


# ─────────────── 策略核心：一个角色 + 一个类别 → 一个候选 ───────────────

def _plan(entry, kind, today, lead_days, cfg):
    """纯函数：判定今天该不该发这一类、发什么。不该发返回 None（无状态、无副作用）。"""
    templates = cfg.get("templates") or {}
    nickname = entry.get("nickname") or "你"
    char_name = entry.get("character_name") or ""
    base = {
        "character_id": entry.get("character_id"),
        "user_id": entry.get("user_id"),
        "session_id": entry.get("session_id"),
        "strategy": STRATEGY,          # 告诉内核：这是策略候选，请按声明口径落库/去重
    }

    if kind == "birthday":
        if not entry.get("birthday_enabled") or not entry.get("birthday"):
            return None
        if entry["birthday"] != _mmdd(today + datetime.timedelta(days=lead_days)):
            return None
        tpl = templates.get("birthday") or "今天是 {nickname} 的生日，送上一句真诚的生日祝福。"
        return {
            **base,
            "message_type": "birthday",
            "hint": _render(tpl, nickname=nickname, character_name=char_name),
        }

    if kind == "holiday":
        if not entry.get("holiday_enabled"):
            return None
        names = _festival_names(cfg, today + datetime.timedelta(days=lead_days))
        if not names:
            return None
        tpl = templates.get("holiday") or "今天是 {holiday_name}，送上一句轻松的节日祝福。"
        return {
            **base,
            "message_type": "holiday",
            "holiday_name": names[0],
            "hint": _render(
                tpl, nickname=nickname, character_name=char_name, holiday_name="、".join(names),
            ),
        }

    if kind == "anniversary":
        # 认识纪念日是「已经发生的第 N 天」，不支持提前（lead_days 只对生日/节日生效）
        days = _anniversary_days(entry.get("first_session_at"), today)
        if days not in ANNIVERSARY_MILESTONES:
            return None
        tpl = templates.get("anniversary") or "今天是你和 {nickname} 认识的第 {days} 天。"
        return {
            **base,
            "message_type": "anniversary",
            "anniversary_days": days,
            "hint": _render(tpl, nickname=nickname, character_name=char_name, days=days),
        }

    return None


@sdk.hook("proactive_candidate")
async def special_strategy(ctx):
    """内核每 tick 调用：只把 roster 映射成候选，不做任何节流/去重/发送。

    ctx（仅 flag 开且本类别被接管时才有）：
        {"strategy_categories": ["special"], "roster": [{character_id, user_id, session_id,
          character_name, nickname, birthday, birthday_enabled, holiday_enabled,
          first_session_at}, ...]}
    """
    ctx = ctx or {}
    if STRATEGY not in (ctx.get("strategy_categories") or []):
        return None                     # flag 关 / 本类别未被接管 → 零输出（防双发）
    roster = ctx.get("roster") or []
    if not roster:
        return None

    cfg = sdk.get_config() or {}
    kinds = _kinds(cfg)
    lead_days = _as_int(cfg.get("lead_days"), 0)
    today = datetime.date.today()

    out = []
    for entry in roster:
        for kind in kinds:
            cand = _plan(entry, kind, today, lead_days, cfg)
            if cand:
                out.append(cand)
    return out or None
