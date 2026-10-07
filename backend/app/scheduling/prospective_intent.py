# -*- coding: utf-8 -*-
"""Ariadne 模块G：前瞻意图（promise/cue）采集、状态机、到期采集与兑现（2026-09-04）。

- 不进 memories，不参与检索/衰减/查重/#70 supersede；
- promise：有 due 时间窗，Scheduler 周期扫 due_end<=now 的 pending；
- cue：可带 due 时间窗（日期型跨天即 stale）或无 due（创建超 30 天即 stale），聊天时由 context
  分区用 cue_terms 做确定性匹配（在线零 LLM）；与 promise 同构纳入时效治理（2026-09-15，plans #72）；
- 兑现即焚 discharged（一次性）；due_end+7 天未兑现 → expired（留痕不删）；用户取消 → cancelled；
- 时间型触发经 3.10 TriggerSource（scheduling/sources/prospective_intent.py）接入 arbiter，
  本模块放真实逻辑（薄 source 只适配，对齐 unfinished_topic 的分工）。

前瞻约定治理（2026-09-13，交接：主体口径 / 时效收窄 / 去模板去重 / 存量清理）：
- ① side=self|user：落库时按 content/kind 判定「谁答应的」，写进 cue_terms_json 元数据
  （只加标记不改语义，零迁移）；渲染按 side 分流，由既有 promise_self_side_split 开关门控，
  开关关=逐字节回退当前口径。
- ② 时效收窄：promise 只有 due_end 之后 ≤ PROMISE_ACTIVE_WINDOW_HOURS 小时才算「可自然提起」，
  超窗由 mark_stale_overdue() 置 stale；cue 同构纳入时效治理（2026-09-15，plans #72）——带时间窗的 cue
  （日期型跨天 / 非日期型超 CUE_ACTIVE_WINDOW_HOURS）与无 due 的纯线索（创建超 STALE_NODUE_DAYS 天）
  一律置 stale（留痕不删，仍可检索/回忆，但不进主动提起 / 线索注入）。
- ② 时效口径修正（2026-09-16，批次一任务1/2）：
  * 日期型 promise（due_end 时分=23:59）= **到期日当天**（北京自然日 00:00–23:59）任一时刻可自然提起，
    跨天即作废；mark_stale_overdue() 必须豁免日期型（否则当天上午就被 2 小时窗清掉）。
    精确时刻型（非 23:59）维持 [now - 2h, now] 与超窗 stale 不变。
  * 无 due 的陈旧闸从「仅 cue」扩到 promise：kind∈{cue,promise} 且 due_end 为空、创建超
    STALE_NODUE_DAYS 天 → stale。判定唯一走 _intent_is_stale()（在线硬闸与周期清扫共用）。
- ③ 去模板去重：同一 intent 幂等只提一次（原子认领 pending→discharged，失败回滚）；
  同角色一小时内同款开场最多 1 条（recent_opener_exists）；写入期近重复合并。
- ④ 存量清理只读/写脚本化：scripts/cleanup_prospective_intents.py（dry-run 默认）。
- ⑤ 事件兑现即静默关闭（A32，2026-10-07，零 LLM）：promise 除了「被提起即焚」，新增两条关闭口——
  用户消息落库后 ``settle_promises_on_user_message`` 当场结算（到家 / 吃药类信号命中 → discharged、
  不发消息），以及 ``run_prospective_due`` 认领前的现状校验闸门（双保险）。真机现场：用户 12:54
  「我到家了」之后，171-174 仍在 13:06~15:21 被逐条 fire 催问旧剧情。
- ⑥ 事件/时钟显式分类（A33，2026-10-07，B.3.2）：承诺落库时把「在等什么」写成 cue_terms_json 的
  ``trigger`` 元数据（arrival|medication|clock，口径同 ⑤ 的正则，只加标记不改 cue_terms 语义、零迁移）；
  读侧一律**优先用已存标签**、缺失时回退按内容推断（旧行不受影响）。采集层（``collect_due_promises``）
  对「日期型 + 事件型」候选先做一次现状校验：信号已出现＝已兑现，不进候选并就地置 discharged，
  未兑现才保留「到期日当天全天可提起」的既有语义（把 ⑤ 的 fire 前闸门口径提前到筛选层，省一次白跑调度）。
- ⑦ 同主题合并（A34 批3，2026-10-07，B.3.2）：写入期把「同一角色 + 同一个在等的事件（trigger）」的
  事件型承诺并到**更早创建**的那条（正文无损合成、cue_terms 取并集），clock 型不参与；
  合不进去（会丢语义）就不并。现场：一次「到家/接人」挂 4 条（171-174）被逐条催问 4 次。
- ⑧ 双发抑制（A34 批3，B.3.5）：同会话 PROACTIVE_DUAL_SEND_SUPPRESS_SEC 秒内已有 AI 消息 ⇒ 承诺
  本轮**不抢发**（即时回复路径不受影响）；不认领、不写日志，状态留 pending 以便下轮重试。
- ⑨ 回忆式开场分家（A34 批3，B.3.5）：「我记得你之前说过…」这类开场**只留给回忆通道
  （memory_review）**；承诺主动提起改用自述（self）/ 当下询问（user）口吻，句首命中即拦回重生成。

TODO（Ariadne 模块G 一期裁剪，2026-09-04 拍板，不实现）：
- kind=wish（无条件心愿）：一期裁掉，只做 promise/cue；
- 线索型主动推送（到期 cue 主动发消息）：一期只注入本轮提醒块，主动推送二期；
- cue 匹配一期之子串，A32（2026-10-07）补了「到达类高频变体表」``_CUE_VARIANTS``（仍零 LLM、
  非开放式语义匹配）；正则/语义匹配仍是二期（可复用 LorebookEntry 的 is_regex 经验）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.db.database import async_session_factory
from app.models.chat import ChatMessage, ChatSession
from app.models.character import AICharacter, ProactiveMessageLog
from app.models.memory import ProspectiveIntent
from app.scheduling import state_guard
from app.utils.logger import get_logger

_logger = get_logger("scheduling.prospective_intent")

GRACE_DAYS = 7              # 到期宽限：超过 due_end 7 天仍未兑现 → expired
PROMISE_SCAN_LOOKAHEAD = timedelta(days=3650)  # 防御性上限（无 due_end 的 cue 不被时间扫描捞到）

# ── 前瞻约定治理常量（2026-09-13）──
PROMISE_ACTIVE_WINDOW_HOURS = 2    # ② due_end 之后 ≤ N 小时才可自然提起（超窗 → stale）
#    2026-09-14 收紧：12h 太宽（叠加下面的时区口径错位，会拖到第二天傍晚还在提昨天的事）。
CUE_ACTIVE_WINDOW_HOURS = 24       # ②cue 带「非日期型」时间窗时，到期后宽限 N 小时再 stale
#    （当前提取器只产出日期型 due=23:59，跨天硬闸优先；此分支仅为防御未来出现时分粒度 due）
STALE_NODUE_DAYS = 30              # ④ 无 due 的 intent（cue 纯线索 / promise 无时间窗）创建超 M 天 → stale
#    2026-09-15 接线（plans #72）：由 mark_stale_cues() 周期清扫 + match_cue_intents() 在线硬闸使用，
#    原来是定义后未引用的死常量，导致纯线索 cue 永不过期。
#    2026-09-16（批次一任务2）：覆盖范围扩到 kind='promise' 且 due_end is null（仅 pending/matched），
#    判定统一收敛到 _intent_is_stale()，在线路径与周期清扫共用同一口径。
SIMILAR_INTENT_THRESHOLD = 0.95    # ③ 写入期近重复合并阈值（同角色 + 同 due 窗口）
OPENING_COOLDOWN_HOURS = 1         # ③ 同款开场冷却：同角色一小时内最多 1 条
IDEMPOTENT_FIRED_PREFIX = "prospective_intent_fired"  # ③ 幂等键前缀

# ① 主体口径判定（零 LLM）：自述开头的 AI 承诺 → side=self；显式「用户…」或 cue → side=user。
_SELF_SIDE_PREFIXES = (
    "我承诺", "我答应", "我保证", "我来", "我会", "我去", "我要",
    "我将", "我负责", "我准备", "我打算",
)
_USER_SIDE_PREFIXES = ("用户", "对方", "ta", "TA", "他", "她")
_USER_SIDE_INFIX = ("用户", "对方")

# ③ 同款开场（模板复读）标志串；命中这些开场的主动消息计入一小时内冷却。
_PROACTIVE_OPENING_MARKERS = (
    "我记得你之前说过", "你之前不是说", "我记得你说过", "你之前说过",
    "我记得你之前", "你之前不是说过", "我记得你以前说过",
)
# ② 禁止的「当下时态断言」：跨天/迟到提起时不得暗示现在正是那个时候。
_PRESENT_TENSE_ASSERTIONS = (
    "现在时间也差不多了", "现在时间差不多了", "现在已经到时间了",
    "现在已经差不多了", "时间也差不多了",
)

# ─────────────── A32（2026-10-07）事件兑现信号：确定性、零 LLM ───────────────
# 背景：promise 只有「被主动提起（fire）即焚」一条关闭路径，用户早已「到家了 / 吃了」之后
# 仍被逐条催问旧剧情（Sam char=13 intent 171-175，2026-10-06 现场）。这里补一条在线判定：
# 承诺在等的事件信号若已出现在用户消息里 ⇒ 静默 discharged，不 fire、不发消息、不调模型。
# 只做字面/正则匹配（口径与 cue 一致），**不得**为了「更准」去调模型。
ARRIVAL_PAT = re.compile(r"(到家|回家了|回来了|回来啦|到门口|在楼下|进门|上楼|我到了|^到了$)")
MED_PAT = re.compile(r"(吃过药了|吃药了|药吃了|吃了药|已经吃药|药已经吃|^吃了$)")
# ↑ A38（2026-10-07）收窄：原先的 `到了|已到|刚到|在楼下了|到门口了` 里，裸「到了」在生产语料
#   （最近 4000 条用户消息）命中 48 条，绝大多数不是到达（搜到了/知道了/找到了/感受到了/刺激到了/
#   排到了/外卖到了），「刚到」还会命中「外卖刚到」⇒ 一律去掉，只留明确到达语义的形态；代价是
#   「已经到了/终于到了」这类真到达不再算信号——按 A32 的「宁可漏关不误关」取舍（fail-open）。
# promise 正文的「在等什么」判定（保守：药优先，其次到达类；都不像则返回 None＝不管）
_AWAIT_MEDICATION_PAT = re.compile(r"药")
_AWAIT_ARRIVAL_PAT = re.compile(
    r"到家|到地方|到达|到门口|在门口|楼下|到了之后|到了以后|定位|上车|会合|接我|接你|去接|接用户")
# ↑ A38 收窄：原先的裸「到」把「到点喊用户起床」（intent 12）「回到我手里」（intent 96）「揉到睡」
#   「外卖到了」（intent 48）「早七点到场」（intent 16）一律判成 arrival，再被用户一句带「到家」的
#   无关话误关；「接」同样过泛（「用杯子接」intent 64、「转接头」intent 103）。**只做收窄**：形态表
#   里的每一项都是原裸「到/接/门口」命中集的子集（「回来/取回来」这类补语不进表，宁可漏判不误判）。
# cue 变体表（B.3.3）：只补「到达」这类高频、低歧义形态，不做开放式语义匹配（避免误触发）。
_CUE_VARIANTS: dict[str, tuple[str, ...]] = {
    "到了": ("到家", "到了", "已到", "刚到", "回来了", "回家了"),
    "到家": ("到家", "到了", "已到", "刚到", "回到家"),
    "回来": ("回来", "回到家", "到家"),
    "上车": ("上车", "已上车", "上车了"),
    "进门": ("进门", "到家", "到了"),
}

# ─────────────── A34 批 3（2026-10-07）同主题合并 / 双发抑制 / 开场分家 ───────────────
# 三件都是**零 LLM** 的确定性治理，现场见模块 docstring ⑦⑧⑨（调查报告 B.3.2 / B.3.5）。
TOPIC_MERGE_TRIGGERS = ("arrival", "medication")  # ⑦ 只有「在等一个事件」的承诺参与同主题合并
TOPIC_MERGE_MAX_PARTS = 4      # ⑦ 合并后正文最多并列几段（含原有段），装不下就**不并**
TOPIC_MERGE_MAX_CHARS = 500    # ⑦ 合并后正文长度上限（与 upsert 的 content[:500] 同口径）
TOPIC_MERGE_JOIN = "；"         # ⑦ 并列段的分隔符（同主题多义务，一条消息一次提起）

PROACTIVE_DUAL_SEND_SUPPRESS_SEC = 20   # ⑧ 同会话 N 秒内已有 AI 消息 ⇒ 承诺本轮不抢发
_RECALL_HEAD_CLAUSES = 2                # ⑨ 「开场」只看句首前两个短句（容忍「嘿，」「宝贝，」）


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


BEIJING_OFFSET = timedelta(hours=8)


def _now_local_naive() -> datetime:
    """北京口径的「现在」（naive）。

    2026-09-14 真机反馈修复：库内 due_start/due_end 是提取器按模型给出的**北京日历日**写入的
    naive 值（日期 + 23:59），而 _now_naive() 是 UTC。直接用 UTC 比较会让约定「晚 8 小时到期」，
    再叠加 12h 窗口，就会出现「第二天还在使劲提昨天的事」——真机现场：约定 09-13 的豆腐/收游戏，
    在 09-14 18:25~19:29（北京）被成对提起。到期判定统一改走本函数。
    """
    return _now_naive() + BEIJING_OFFSET


def _is_date_scoped(due_end: datetime | None) -> bool:
    """是否是「日期型」约定（提取器统一写 23:59）——这类只在当天有效，跨天不再主动提起。"""
    return due_end is not None and due_end.hour == 23 and due_end.minute == 59


def _intent_is_stale(
    row,
    *,
    now_utc: datetime | None = None,
    now_local: datetime | None = None,
) -> bool:
    """意图是否已陈旧应退场（2026-09-15 plans #72；2026-09-16 批次一任务2 收敛为唯一判定）。

    覆盖两类时间字段、时区口径不同：
    - ``due_end`` 非空且 kind=cue：提取器按**北京日历日**写入（日期型统一 23:59）→ 用北京 now 判：
      日期型跨天即 stale；非日期型超 CUE_ACTIVE_WINDOW_HOURS 即 stale。
      （kind=promise 的非空 due_end 由 ``mark_stale_overdue`` / ``collect_due_promises`` 负责，
       本函数对其返回 False，避免两套窗口口径互相打架。）
    - ``due_end`` 为空（cue 纯线索 / promise 无时间窗）：看 ``created_at``
      （SQLite CURRENT_TIMESTAMP，**UTC**）→ 用 UTC now 判，创建超 STALE_NODUE_DAYS 天即 stale。

    只对 pending/matched 生效；discharged/cancelled/expired/stale 一律返回 False。
    在线匹配（match_cue_intents）、到期采集（collect_due_promises）与周期清扫（mark_stale_cues）
    共用本函数，保证口径唯一。
    """
    kind = getattr(row, "kind", None)
    if kind not in ("cue", "promise"):
        return False
    if getattr(row, "status", None) not in ("pending", "matched"):
        return False
    now_utc = now_utc or _now_naive()
    now_local = now_local or _now_local_naive()
    due = getattr(row, "due_end", None)
    if due is not None:
        if kind != "cue":
            return False  # promise 的到期/超窗走 mark_stale_overdue（2h 窗）与跨天闸
        if _is_date_scoped(due):
            return due.date() < now_local.date()
        return due < (now_local - timedelta(hours=CUE_ACTIVE_WINDOW_HOURS))
    created = getattr(row, "created_at", None)
    if created is not None:
        return created < (now_utc - timedelta(days=STALE_NODUE_DAYS))
    return False


def _cue_is_stale(
    row,
    *,
    now_utc: datetime | None = None,
    now_local: datetime | None = None,
) -> bool:
    """兼容别名：历史调用点/测试使用 ``_cue_is_stale``；判定唯一走 ``_intent_is_stale``。"""
    return _intent_is_stale(row, now_utc=now_utc, now_local=now_local)


def classify_intent_trigger(content: str) -> str:
    """A33 ⑥ 写入期事件分类（零 LLM）：'medication' / 'arrival' / 'clock'（不等待定事件）。

    判定口径与 A32 的 ``_promise_awaits`` 完全一致（药优先、其次到达类、都不像则 clock），
    区别只在返回值的表达：落库存的是「这条承诺在等什么」的**类别标签**，clock 显式写出来，
    这样读侧可以区分「已判定为时钟型」与「旧行没有标签、还得现算」。
    """
    t = content or ""
    if _AWAIT_MEDICATION_PAT.search(t):
        return "medication"
    if _AWAIT_ARRIVAL_PAT.search(t):
        return "arrival"
    return "clock"


def get_intent_trigger(row) -> str | None:
    """读出一条 intent 已存的 trigger 标签；无标签/旧 list 格式/坏 JSON → None（由调用侧回退推断）。"""
    trigger = _loads_intent_meta(getattr(row, "cue_terms_json", "") or "").get("trigger")
    return trigger if trigger in ("arrival", "medication", "clock") else None


def effective_intent_trigger(row) -> str:
    """trigger 唯一读取口（A33 ⑥）：优先落库标签，缺失时按内容推断（向后兼容旧行）。"""
    return get_intent_trigger(row) or classify_intent_trigger(getattr(row, "content", "") or "")


def _promise_awaits(content: str, *, stored: str | None = None) -> str | None:
    """这条承诺在等哪类事件信号：'arrival'（到达）/ 'medication'（吃药）/ None（不判定）。

    A33 ⑥：``stored``（落库的 trigger 标签）在时**以它为准**（clock＝不等事件），缺失才按内容推断，
    保证升级前的旧行与手工构造的候选 dict 行为不变。
    """
    if stored == "clock":
        return None
    if stored in ("arrival", "medication"):
        return stored
    t = content or ""
    if _AWAIT_MEDICATION_PAT.search(t):
        return "medication"
    if _AWAIT_ARRIVAL_PAT.search(t):
        return "arrival"
    return None


def _signal_seen(kind: str, *texts: str | None) -> bool:
    """承诺在等的事件信号是否已出现在给定文本中（字面/正则，零 LLM）。"""
    pat = ARRIVAL_PAT if kind == "arrival" else MED_PAT
    return any(t and pat.search(t) for t in texts)


async def _latest_user_message(session_id: int) -> str | None:
    """该会话最新一条**用户**消息正文（无则 None）。fail-open：异常一律 None，绝不冒泡。"""
    try:
        async with async_session_factory() as db:
            return (await db.execute(
                select(ChatMessage.content)
                .where(ChatMessage.session_id == session_id, ChatMessage.sender_type == "user")
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                .limit(1)
            )).scalar_one_or_none()
    except Exception as e:  # 现状校验拿不到文本＝不关闭，继续既有语义（宁可漏关不误关）
        _logger.warning("prospective latest user message failed sess=%s: %s", session_id, e)
        return None


async def recent_ai_message_id(
    session_id: int, *, within_sec: int = PROACTIVE_DUAL_SEND_SUPPRESS_SEC,
) -> int | None:
    """⑧ 该会话最近 ``within_sec`` 秒内是否**已有 AI 消息**落库（返回其 id，无则 None）。

    只读、零 LLM。fail-open：查询异常一律返回 None ⇒ 照发（漏抑制一次双发，好过漏掉一条承诺）。
    即时回复路径**不经过**本函数（用户说话的回应必须发），只有主动/承诺发送前自查。
    """
    try:
        cutoff = _now_naive() - timedelta(seconds=within_sec)
        async with async_session_factory() as db:
            return (await db.execute(
                select(ChatMessage.id)
                .where(
                    ChatMessage.session_id == session_id,
                    ChatMessage.sender_type == "ai",
                    ChatMessage.created_at >= cutoff,
                )
                .order_by(ChatMessage.id.desc())
                .limit(1)
            )).scalar_one_or_none()
    except Exception as e:  # 拿不到＝不抑制
        _logger.warning("prospective recent ai check failed sess=%s: %s", session_id, e)
        return None


def _loads_cue_terms(s: str) -> list[str]:
    """cue_terms_json → terms list。

    兼容两种格式：
    - 旧：`["火锅", "周末"]`（直接 list[str]）
    - 新（2026-09-07 放宽口径）：`{"confidence": "low|medium|high", "terms": ["火锅", "周末"]}`
    无法解析时回退空列表，匹配静默失败（fail-open）。
    """
    try:
        v = json.loads(s or "")
    except Exception:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, dict):
        terms = v.get("terms")
        if isinstance(terms, list):
            return [str(x).strip() for x in terms if str(x).strip()]
    return []


# ───────────────────────── ① 主体口径（side=self|user），零 LLM，落库即定 ─────────────────────────
def classify_intent_side(content: str, kind: str = "promise") -> str:
    """判定「这个约定/承诺是谁答应的」：'self'（AI 自己许的）或 'user'（用户侧）。

    规则（与交接 ① 对齐，保守）：
    - kind=cue → user（线索型一律用户侧）；
    - content 以「用户/对方/他/她/TA」开头 → user；
    - content 以「我承诺/我答应/我来…」这类自述开头 → self；
    - 其余含「用户/对方」→ user，含「我承诺/我答应/我保证」→ self；
    - 兜底 user（与既有渲染口径一致，避免把 AI 承诺误标为用户侧）。
    """
    t = (content or "").strip()
    if kind == "cue":
        return "user"
    if t.startswith(_USER_SIDE_PREFIXES):
        return "user"
    if t.startswith(_SELF_SIDE_PREFIXES):
        return "self"
    if any(p in t for p in _USER_SIDE_INFIX):
        return "user"
    if any(p in t for p in ("我承诺", "我答应", "我保证")):
        return "self"
    return "user"


def _loads_intent_meta(s: str) -> dict:
    """cue_terms_json → 元数据 dict；旧 list 格式 / 解析失败 → {}（fail-open）。"""
    try:
        v = json.loads(s or "")
    except Exception:
        return {}
    return v if isinstance(v, dict) else {}


def get_intent_side(row) -> str:
    """读出一条 intent 的 side：优先元数据落库值，旧行回退按 content/kind 现算。"""
    meta = _loads_intent_meta(getattr(row, "cue_terms_json", "") or "")
    side = meta.get("side")
    if side in ("self", "user"):
        return side
    return classify_intent_side(
        getattr(row, "content", "") or "", getattr(row, "kind", "promise") or "promise"
    )


# ───────────────────────── ③ 近重复 intent 合并（写入期，保守阈值）─────────────────────────
def normalize_intent_text(text: str) -> str:
    """归一化 intent 文本：去空白/标点、小写，供相似度比较。"""
    return re.sub(r"[\s，。！？、,.!?;；:：\"'“”‘’()（）\[\]【】~～\-—]", "", (text or "").lower())


def similar_intent_text(a: str, b: str, threshold: float = SIMILAR_INTENT_THRESHOLD) -> bool:
    """两段 intent 文本是否高度相近（同义重复）。纯函数、零 IO。

    先比归一化全等；再做「长度接近的子串包含」与 SequenceMatcher 比值（阈值默认 0.95，
    只合并近乎逐字重复者，不吞并"带你去吃火锅"/"下周带你去吃火锅"这类不同粒度）。
    """
    na, nb = normalize_intent_text(a), normalize_intent_text(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na in nb or nb in na:
        if min(len(na), len(nb)) / max(len(na), len(nb)) >= threshold:
            return True
    from difflib import SequenceMatcher
    return SequenceMatcher(None, na, nb).ratio() >= threshold


def fired_idempotency_key(intent_id: int) -> str:
    """③ 同一 intent 提起幂等键（防重复落地；随主动消息 extra_meta 留痕）。"""
    return f"{IDEMPOTENT_FIRED_PREFIX}:{int(intent_id)}"


def has_proactive_opening(text: str) -> bool:
    """③ 字符串级检查：文本是否**含有**「我记得你之前说过 / 你之前不是说」这类同款开场。

    用在「一小时内同款开场冷却」上（宽口径：只要出现就算复读）。
    ⑨ 承诺通道的「禁用回忆式开场」不用本函数，用 :func:`starts_with_recall_opener`（只看句首）。
    """
    return any(m in (text or "") for m in _PROACTIVE_OPENING_MARKERS)


def starts_with_recall_opener(text: str) -> bool:
    """⑨ 回忆式开场判定（纯函数）：文本**句首**是否用「我记得你之前说过…」这类回忆句式起头。

    口径：按标点切成短句，只看前 ``_RECALL_HEAD_CLAUSES``（=2）个短句——允许「嘿，」「宝贝，」这类
    起头词，但「我记得你之前说过…／你之前不是说…」一旦出现在开场就命中。
    与 :func:`has_proactive_opening`（全文命中，判复读）分工不同：句中正常引用
    （「你说过想去吃火锅，后来去了吗」）不该被当成回忆式开场，故本函数不看后文。
    """
    clauses = [c for c in re.split(r"[，。！？、,!.?;；:：~～\s]+", (text or "").strip()) if c]
    head = clauses[:_RECALL_HEAD_CLAUSES]
    return any(any(m in c for m in _PROACTIVE_OPENING_MARKERS) for c in head)


def has_forbidden_present_tense(text: str) -> bool:
    """② 渲染闸门：禁止「现在时间也差不多了」这类当下时态断言。"""
    return any(m in (text or "") for m in _PRESENT_TENSE_ASSERTIONS)


# ───────────────────────── 写入（extractor 便车调用，幂等）─────────────────────────
async def _find_near_duplicate(
    db, *, character_id: int, content: str, due_start: datetime | None, due_end: datetime | None,
    side: str = "user",
) -> int | None:
    """③ 同角色 + 同 due 窗口 + 语义高度相近的 pending promise → 返回既有 id（合并）。

    ① 口径纠正：若既有行被判为 user 侧、而本次新内容明确是 self 侧，则把既有行的
    side 元数据升级为 self（只改标记，渲染即回到自述口径）。
    """
    rows = (await db.execute(
        select(ProspectiveIntent).where(
            ProspectiveIntent.character_id == character_id,
            ProspectiveIntent.kind == "promise",
            ProspectiveIntent.status == "pending",
            ProspectiveIntent.due_start == due_start,
            ProspectiveIntent.due_end == due_end,
        )
    )).scalars().all()
    for r in rows:
        if not similar_intent_text(r.content, content):
            continue
        if side == "self" and get_intent_side(r) != "self":
            meta = _loads_intent_meta(r.cue_terms_json)
            meta["side"] = "self"
            r.cue_terms_json = json.dumps(meta, ensure_ascii=False)
            db.add(r)
            await db.commit()
        return r.id
    return None


# ─────────────── ⑦ 同主题（同一在等的事件）承诺合并（A34 批3，写入期，零 LLM）───────────────
def _split_topic_parts(content: str) -> list[str]:
    """把（可能已并过多次的）承诺正文按并列分隔符拆段，去空段。纯函数。"""
    return [p.strip() for p in (content or "").split(TOPIC_MERGE_JOIN) if p.strip()]


def compose_topic_content(kept: str, incoming: str) -> str | None:
    """同主题两条承诺正文合成一条；**无法无损合并时返回 None（宁可不并）**。纯函数。

    判据（保守边界，写死在此便于对照与回归）：
    - 任一侧为空 → None（没什么可并，也不制造空段）；
    - 一方归一化后包含另一方，或 :func:`similar_intent_text` 命中 → 取**更长（信息更全）的一条**，
      逐字不动（「到了发定位」/「到了发定位。」这类同义重复不该越拼越长）；
    - 否则把新句作为一段并列进来，**要求装得下**：并列段数 ≤ ``TOPIC_MERGE_MAX_PARTS``、
      总长 ≤ ``TOPIC_MERGE_MAX_CHARS``；装不下 → None（两条各自保留，绝不截断丢字）。
      这条就是「两条内容差异很大时不并」的实现：并进去会丢的那部分，宁可让它继续挂着。
    """
    a = (kept or "").strip()
    b = (incoming or "").strip()
    if not a or not b:
        return None
    na, nb = normalize_intent_text(a), normalize_intent_text(b)
    if na == nb or na in nb or nb in na or similar_intent_text(a, b):
        return a if len(a) >= len(b) else b
    parts = _split_topic_parts(a) + [b]
    joined = TOPIC_MERGE_JOIN.join(parts)
    if len(parts) > TOPIC_MERGE_MAX_PARTS or len(joined) > TOPIC_MERGE_MAX_CHARS:
        return None
    return joined


def _absolutize_incoming(text: str) -> str:
    """新写入句子里的「明天/下周三」按**今天**定形后再并进的合并体（fail-open，异常原样返回）。

    合并体的基准日是「更早那条」的 created_at，兑现时 ``_hint_content`` 会拿它整段换算相对时间词；
    新段不先绝对化，几天后兑现时会把新段的「明天」按旧基准日算错（与 Q2b 同一类缺陷）。
    """
    try:
        from app.utils.relative_time import absolutize_relative_dates
        return absolutize_relative_dates(text, _now_naive()) or text
    except Exception as e:  # 绝不因定形失败而丢承诺
        _logger.warning("prospective topic absolutize failed, keep raw: %s", e)
        return text


def _compose_for_merge(kept: str, incoming: str) -> str | None:
    """``compose_topic_content`` 的写入期入口：并列新段时先把新段的相对时间词定形。"""
    base = compose_topic_content(kept, incoming)
    if base is None:
        return None
    if base in ((kept or "").strip(), (incoming or "").strip()):
        return base          # 「取信息更全的一条」：逐字保留，不改写既有措辞
    return compose_topic_content(kept, _absolutize_incoming(incoming))


def _same_raise_window(a_due: datetime | None, b_due: datetime | None, *, now_local: datetime | None = None) -> bool:
    """两条承诺是否落在**同一档提起窗**内（⑦ 合并的时间闸，纯函数）。

    「两道闸」＝① 日期型（due_end 时分=23:59）＝到期日当天（北京自然日）全天可提；
    ② 精确时刻型＝ ``[due_end - PROMISE_ACTIVE_WINDOW_HOURS, due_end]`` 这一轮扫描窗。
    同档才算「同一件事的同一个提起时机」：

    - 都为空（无 due）→ 同档（都属不会被时间扫描捞到的那一类，合并只减僵尸 pending）；
    - 都是日期型 → **同一个北京日历日**才算同档（跨天各提各的，不能把明天的并到昨天那条上）；
    - 都是精确时刻型 → 两个 due_end 相距 ≤ ``PROMISE_ACTIVE_WINDOW_HOURS``（会被同一轮捞到）；
    - 一空一有 / 一日期型一精确型 → 不同档，不并。
    """
    if a_due is None and b_due is None:
        return True
    if a_due is None or b_due is None:
        return False
    now_local = now_local or _now_local_naive()
    a_date, b_date = _is_date_scoped(a_due), _is_date_scoped(b_due)
    if a_date and b_date:
        return a_due.date() == b_due.date()
    if a_date != b_date:
        return False
    return abs((a_due - b_due).total_seconds()) <= PROMISE_ACTIVE_WINDOW_HOURS * 3600


async def _find_topic_duplicate(
    db, *, user_id: int, character_id: int, content: str, cue_terms: list[str],
    due_end: datetime | None, side: str, trigger: str,
    source_message_id: int | None = None,
) -> int | None:
    """⑦ 同角色 + 同 trigger（arrival/medication）+ 同提起档的 pending promise ⇒ 并进**更早创建**的那条。

    与 :func:`_find_near_duplicate`（同 due 窗口 + 近逐字重复）互补：真机 171-174「到了发定位 /
    在门口等我 / 下楼接你去急诊 / 上了车把定位甩过来」文本互不相似，却都在等同一个「到达」事件，
    同一天被逐条 fire ⇒ 用户被连催 4 次。本函数在**写入期**就把它们收成一条。

    不参与合并（一律返回 None 让调用侧照常新增）：
    - ``clock`` 型（日期/时刻承诺各提各的，误并会把「明早叫你」并进「晚上问你」）；
    - cue（线索型不是承诺，且按设计可长期复用）；
    - 不同 user_id / 不同 side（谁答应的一致才是一条事）/ 非 pending 行；
    - 时间不同档（:func:`_same_raise_window`）；
    - 正文并进去会丢语义（:func:`_compose_for_merge` 装不下 ⇒ 保守不并）。

    合并动作：保留既有行（id、created_at、due 都不动）＋ content 无损合成 ＋ cue_terms 取并集 ＋
    trigger/side 元数据补齐；**INFO 留痕**写明 src_msg → kept id（现场一次事件挂几条全靠这行还原）。
    """
    if trigger not in TOPIC_MERGE_TRIGGERS:
        return None
    rows = (await db.execute(
        select(ProspectiveIntent).where(
            ProspectiveIntent.character_id == character_id,
            ProspectiveIntent.user_id == user_id,
            ProspectiveIntent.kind == "promise",
            ProspectiveIntent.status == "pending",
        ).order_by(ProspectiveIntent.created_at.asc(), ProspectiveIntent.id.asc())
    )).scalars().all()
    now_local = _now_local_naive()
    for r in rows:
        if effective_intent_trigger(r) != trigger:
            continue
        if get_intent_side(r) != side:
            continue
        if not _same_raise_window(r.due_end, due_end, now_local=now_local):
            continue
        merged = _compose_for_merge(r.content or "", content)
        if merged is None:
            continue
        meta = _loads_intent_meta(r.cue_terms_json)
        terms: list[str] = []
        for t in _loads_cue_terms(r.cue_terms_json) + list(cue_terms or []):
            t = (t or "").strip()
            if t and t not in terms:
                terms.append(t)
        meta["terms"] = terms[:6]
        meta.setdefault("trigger", trigger)
        meta.setdefault("side", side)
        r.cue_terms_json = json.dumps(meta, ensure_ascii=False)
        r.content = merged[:TOPIC_MERGE_MAX_CHARS]
        db.add(r)
        await db.commit()
        _logger.info(
            "Prospective same-topic merged char=%d trigger=%s src_msg=%s absorbed=%.60s -> kept id=%s (parts=%d)",
            character_id, trigger, source_message_id, content, r.id, len(_split_topic_parts(merged)),
        )
        return r.id
    return None


async def upsert_intent(
    *, user_id: int, character_id: int, content: str, kind: str = "promise",
    cue_terms: list[str] | None = None, due_start: datetime | None = None,
    due_end: datetime | None = None, source_message_id: int | None = None,
    chat_session_id: int | None = None,
    confidence: str = "medium", side: str | None = None,
) -> int | None:
    """落一条前瞻意图。同一 source_message_id 已存在 → 幂等跳过，返回既有 id。

    保守原则（宁漏不误）：content 为空 / promise 缺时间且无线索 / cue 缺线索 → 不写。

    置信（confidence，2026-09-07 放宽口径引入）：写入 cue_terms_json 的 dict 包装
    （`{"confidence": "low|medium|high", "terms": [...]}`）。旧 list 格式仍可被
    ``_loads_cue_terms`` 解析（match_cue_intents 向后兼容）。

    ① 主体口径（2026-09-13）：side 缺省按 content/kind 现算，与 confidence/terms 一起写进
    cue_terms_json 元数据（`"side": "self"|"user"`，只加标记不改语义、零迁移）；显式传 side
    可覆盖。渲染侧是否按 side 分流由 promise_self_side_split 开关决定（见 run_prospective_due）。

    ③ 去重（2026-09-13）：同角色 + 同 due 窗口 + 近逐字重复的 pending promise → 复用既有行。

    ⑥ 事件分类（A33，2026-10-07）：kind=promise 时额外写 ``"trigger": "arrival"|"medication"|"clock"``
    （按 content 判定，口径见 ``classify_intent_trigger``）；cue 不打标签。只加元数据，
    ``terms`` 本体与 confidence/side 全部不变。

    ⑦ 同主题合并（A34 批3，2026-10-07）：事件型（arrival/medication）承诺在 ③ 没命中时，再看
    「同角色 + 同 trigger + 同提起档」——命中则并进更早创建的那条（正文无损合成、terms 取并集），
    不再新增一行；clock 与 cue 不走这条路（判据与保守边界见 ``_find_topic_duplicate``）。
    """
    content = (content or "").strip()
    if not content or kind not in ("promise", "cue"):
        return None
    cues = [c.strip() for c in (cue_terms or []) if c and len(c.strip()) >= 2][:6]
    if kind == "cue" and not cues:
        return None
    if kind == "promise" and due_end is None and not cues:
        # 既无时间窗又无线索的「承诺」无法可靠兑现，宁可不写
        return None
    if confidence not in ("low", "medium", "high"):
        confidence = "medium"
    if side not in ("self", "user"):
        side = classify_intent_side(content, kind)

    async with async_session_factory() as db:
        if source_message_id is not None:
            existed = (await db.execute(
                select(ProspectiveIntent).where(
                    ProspectiveIntent.source_message_id == source_message_id,
                    ProspectiveIntent.character_id == character_id,
                    ProspectiveIntent.content == content,
                )
            )).scalar_one_or_none()
            if existed is not None:
                return existed.id
        if kind == "promise":
            trigger = classify_intent_trigger(content)   # ⑥ 类别标签（⑦ 同主题合并也用它）
            dup_id = await _find_near_duplicate(
                db, character_id=character_id, content=content,
                due_start=due_start, due_end=due_end, side=side,
            )
            if dup_id is not None:
                return dup_id
            topic_id = await _find_topic_duplicate(
                db, user_id=user_id, character_id=character_id, content=content,
                cue_terms=cues, due_end=due_end, side=side, trigger=trigger,
                source_message_id=source_message_id,
            )
            if topic_id is not None:
                return topic_id
        cue_payload_dict = {
            "confidence": confidence, "terms": cues, "side": side,
        }
        if kind == "promise":
            # A33 ⑥：只加「在等什么」类别标签，terms/confidence/side 一律不变（既有匹配逻辑不受影响）
            cue_payload_dict["trigger"] = trigger
        cue_payload = json.dumps(cue_payload_dict, ensure_ascii=False)
        row = ProspectiveIntent(
            user_id=user_id, character_id=character_id, content=content[:500],
            kind=kind, cue_terms_json=cue_payload,
            due_start=due_start, due_end=due_end, status="pending",
            source_message_id=source_message_id, chat_session_id=chat_session_id,
        )
        db.add(row)
        await db.commit()
        return row.id


# ───────────────────────── 状态流转（纯状态，留痕不物理删）─────────────────────────
async def _set_status(ids: list[int], status: str, *, discharge: bool = False) -> None:
    if not ids:
        return
    async with async_session_factory() as db:
        rows = (await db.execute(select(ProspectiveIntent).where(ProspectiveIntent.id.in_(ids)))).scalars().all()
        for r in rows:
            r.status = status
            if discharge:
                r.discharged_at = _now_naive()
            db.add(r)
        await db.commit()


async def expire_overdue() -> int:
    """due_end + 7 天仍 pending → expired（周期任务调用，幂等）。"""
    cutoff = _now_local_naive() - timedelta(days=GRACE_DAYS)
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ProspectiveIntent).where(
                ProspectiveIntent.status == "pending",
                ProspectiveIntent.kind == "promise",
                ProspectiveIntent.due_end.is_not(None),
                ProspectiveIntent.due_end < cutoff,
            )
        )).scalars().all()
        for r in rows:
            r.status = "expired"
            db.add(r)
        await db.commit()
        return len(rows)


async def mark_stale_overdue(*, window_hours: float | None = None) -> int:
    """② 超窗未兑现 → stale（留痕不删，仍可检索/回忆，但不进主动提起）。

    判定：status=pending、kind=promise、due_end 非空且 `due_end < now - N 小时`
    （N 默认 ``PROMISE_ACTIVE_WINDOW_HOURS``）。周期任务调用，幂等。
    只动 pending：discharged/cancelled/expired/matched/cue 一律不受影响。

    2026-09-16（批次一任务1）：**日期型 promise（due_end 时分=23:59）豁免本函数**——它的口径是
    「到期日当天任一时刻可自然提起」，若仍按 2 小时窗清，当天上午就把当天的约定杀掉了。日期型只在
    跨天时 stale，由 ``collect_due_promises`` 的跨天闸（与 ``run_prospective_due`` 的防御硬闸）负责。
    """
    hours = PROMISE_ACTIVE_WINDOW_HOURS if window_hours is None else float(window_hours)
    cutoff = _now_local_naive() - timedelta(hours=hours)
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ProspectiveIntent).where(
                ProspectiveIntent.status == "pending",
                ProspectiveIntent.kind == "promise",
                ProspectiveIntent.due_end.is_not(None),
                ProspectiveIntent.due_end < cutoff,
            )
        )).scalars().all()
        n = 0
        for r in rows:
            if _is_date_scoped(r.due_end):
                continue  # 任务1：日期型只在跨天时 stale（当天全天有效）
            r.status = "stale"
            db.add(r)
            n += 1
        if n:
            await db.commit()
        return n


async def mark_stale_cues() -> int:
    """②cue/promise 无 due 纳入 stale 覆盖（2026-09-15 plans #72；2026-09-16 批次一任务2 扩到 promise）。

    陈旧线索/承诺置 stale（留痕不删，仍可检索/回忆，但不再在线索命中时注入「你之前还惦记着…」，
    也不再被任何主动通道翻出来）。周期任务每小时调用，幂等。

    覆盖：① 带时间窗的 cue（日期型跨天 / 非日期型超 CUE_ACTIVE_WINDOW_HOURS）；
    ② 无时间窗的 cue 与 **无时间窗的 promise**（created_at 超 STALE_NODUE_DAYS 天）。
    只动 pending/matched；已终态行不受影响。判定唯一走 ``_intent_is_stale``（与在线硬闸同口径）。
    """
    now_utc, now_local = _now_naive(), _now_local_naive()
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ProspectiveIntent).where(
                ProspectiveIntent.status.in_(["pending", "matched"]),
                ProspectiveIntent.kind.in_(["cue", "promise"]),
            )
        )).scalars().all()
        n = 0
        for r in rows:
            if _intent_is_stale(r, now_utc=now_utc, now_local=now_local):
                r.status = "stale"
                db.add(r)
                n += 1
        if n:
            await db.commit()
        return n


async def cancel_by_content(character_id: int, text: str) -> int:
    """用户说「算了/不用了」且文本高相似命中某 pending 意图 → cancelled（保守，需明显指向）。"""
    text = (text or "").strip()
    if len(text) < 2:
        return 0
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ProspectiveIntent).where(
                ProspectiveIntent.character_id == character_id,
                ProspectiveIntent.status == "pending",
            )
        )).scalars().all()
        hit = [r for r in rows if (r.content or "")[:10] in text or text in (r.content or "")]
        for r in hit:
            r.status = "cancelled"
            db.add(r)
        await db.commit()
        return len(hit)


# ───────────────────────── 时间型：Scheduler 采集到期承诺 ─────────────────────────
async def _settled_event_promises(cands: list[dict]) -> list[int]:
    """A33 ⑥ 采集层现状校验：事件型（arrival/medication）承诺在等的信号是否**已经出现**。

    与 ``run_prospective_due`` 的 fire 前闸门同源同口径（``_promise_awaits`` + ``_signal_seen``，
    读本会话最新一条用户消息 ＋ 现状锚），差别只在时机——提前到候选筛选层，已兑现的不再进候选、
    不占调度。取不到文本＝判不出＝不关（fail-open，宁可漏关不误关）；现状锚按 (角色, 用户) 缓存，
    同一次采集不重复取锚。
    """
    anchor_cache: dict[tuple, str] = {}
    settled: list[int] = []
    for c in cands:
        awaits = _promise_awaits(c.get("content") or "", stored=c.get("trigger"))
        if awaits is None:
            continue
        texts: list[str | None] = []
        session_id = c.get("session_id") or c.get("chat_session_id")
        if session_id:
            texts.append(await _latest_user_message(int(session_id)))
        key = (c.get("character_id"), c.get("user_id"))
        if key not in anchor_cache:
            anchor_cache[key] = await state_guard.current_state_anchor(
                character_id=c.get("character_id"), user_id=c.get("user_id")
            )
        texts.append(anchor_cache[key])
        if _signal_seen(awaits, *texts):
            settled.append(int(c["pis_id"]))
    return settled


async def collect_due_promises() -> list[dict]:
    """返回「可自然提起」的 pending promise 候选，供 TriggerSource。

    ② 时效收窄（2026-09-13）：先 expire（7 天宽限）/ mark_stale（N 小时超窗）清理，再筛候选。

    ② 口径修正（2026-09-16，批次一任务1）：候选分两档——
    - **日期型**（due_end 时分=23:59）：到期日**当天（北京自然日 00:00–23:59）任一时刻**都可自然提起
      （提取器只写 23:59，旧口径把可提起窗口压成当天 23:59:00–23:59:59 一分钟，错过就静默作废）；
      跨天（due_end.date() < now.date()）则直接置 stale 作废，不再第二天翻旧账。
    - **精确时刻型**（非 23:59）：维持既有 `[now - window, now]` 不变。

    任务2：无 due 的 promise 不走时间窗，改用与周期清扫同一判定（_intent_is_stale，创建超 30 天 → stale）。

    ⑥ A33（2026-10-07）：候选 dict 附带 ``trigger``（读侧优先落库标签、缺失回退推断），供下游
    ``run_prospective_due`` 直接用；**日期型 + 事件型**（arrival/medication）另过一道现状校验
    （``_settled_event_promises``）——信号已出现＝已兑现，不进候选并就地置 discharged；未兑现才保留
    「当天全天可提起」的既有语义。精确时刻型与无 due 不受本条影响。
    """
    await expire_overdue()          # 先清 7 天宽限外（保持既有 expired 语义）
    await mark_stale_overdue()      # 再把非日期型的 N 小时超窗置 stale（日期型豁免，见该函数）
    now_utc, now = _now_naive(), _now_local_naive()
    window_start = now - timedelta(hours=PROMISE_ACTIVE_WINDOW_HOURS)
    candidates: list[dict] = []
    stale_ids: list[int] = []
    day_event_cands: list[dict] = []   # 日期型 + 事件型：出库后要做现状校验的那批
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ProspectiveIntent).where(
                ProspectiveIntent.status == "pending",
                ProspectiveIntent.kind == "promise",
                ProspectiveIntent.due_end.is_not(None),
            ).order_by(ProspectiveIntent.due_end.asc())
        )).scalars().all()
        for r in rows:
            date_scoped = _is_date_scoped(r.due_end)
            if date_scoped:
                # 日期型：当天全天有效；跨天即作废（2026-09-14 用户明确反馈「第二天接着提昨天的事」）
                if r.due_end.date() < now.date():
                    stale_ids.append(r.id)
                    continue
                if r.due_end.date() > now.date():
                    continue  # 未来日期（防御：正常不会出现 pending 的未来 due）
            elif not (window_start <= r.due_end <= now):
                continue
            cand = {
                "pis_id": r.id, "user_id": r.user_id, "character_id": r.character_id,
                "content": r.content, "due_end": r.due_end,
                # Q2b：兑现时把正文里的相对时间词按「写下计划那天」换算，需要基准日随候选下发
                "created_at": r.created_at, "updated_at": r.updated_at,
                "side": get_intent_side(r),  # ① 主体口径（self|user）
                "trigger": effective_intent_trigger(r),  # ⑥ 事件/时钟分类标签（arrival|medication|clock）
                "session_id": r.chat_session_id,  # arbiter 发送/追踪统一用 session_id 键
                "chat_session_id": r.chat_session_id,
            }
            candidates.append(cand)
            if date_scoped and cand["trigger"] in ("arrival", "medication"):
                day_event_cands.append(cand)
        # 任务2（在线硬闸）：无 due 的 pending promise 创建超 STALE_NODUE_DAYS 天 → stale，
        # 与 mark_stale_cues() 周期清扫共用 _intent_is_stale，口径唯一。
        nodue_rows = (await db.execute(
            select(ProspectiveIntent).where(
                ProspectiveIntent.status.in_(["pending", "matched"]),
                ProspectiveIntent.kind == "promise",
                ProspectiveIntent.due_end.is_(None),
            )
        )).scalars().all()
        for r in nodue_rows:
            if _intent_is_stale(r, now_utc=now_utc, now_local=now):
                stale_ids.append(r.id)
    if stale_ids:
        await _set_status(stale_ids, "stale")   # 留痕不删，只是不再主动提起
        _logger.info("Prospective intents marked stale (cross-day/nodue): %d", len(stale_ids))
    # ⑥ 事件型「当天全天可提起」不再无条件成立：先确认在等的事件还没发生
    settled_ids = await _settled_event_promises(day_event_cands)
    if settled_ids:
        drop = set(settled_ids)
        candidates = [c for c in candidates if c["pis_id"] not in drop]
        await _set_status(settled_ids, "discharged", discharge=True)  # 已兑现＝静默关闭，与 A32 闸门同结局
        _logger.info("Prospective settled at collect layer (event fulfilled): %d", len(settled_ids))
    return candidates


async def mark_discharged_many(ids: list[int]) -> None:
    await _set_status(ids, "discharged", discharge=True)


async def settle_promises_on_user_message(
    character_id: int, session_id: int | None, user_text: str,
) -> list[int]:
    """A32（2026-10-07）：用户消息表明某些**等待中的承诺已兑现** → 批量静默 discharged（不发消息）。

    与 ``run_prospective_due`` 的 fire 前闸门同口径（``_promise_awaits`` + ``_signal_seen``），
    差别只在时机：本函数在**事件发生的当时**就关闭，把「已到家却被连着催问」的窗口压到 0；
    闸门只作双保险（消息落库与调度 tick 之间的残留窗口）。

    保守（宁可漏关，不可误关）：只动 ``kind=promise`` 且 ``status in (pending, matched)`` 且
    **非 stale** 的行；判定零 LLM；不写 ``proactive_message_logs``、不发送任何消息。
    返回被关闭的 intent id 列表（异常由调用侧吞掉，本函数内部不 catch——保持与同模块其它函数一致）。
    """
    text = (user_text or "").strip()
    if not text:
        return []
    now_utc, now_local = _now_naive(), _now_local_naive()
    settled: list[int] = []
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ProspectiveIntent).where(
                ProspectiveIntent.character_id == character_id,
                ProspectiveIntent.status.in_(["pending", "matched"]),
                ProspectiveIntent.kind == "promise",
            )
        )).scalars().all()
        for r in rows:
            if _intent_is_stale(r, now_utc=now_utc, now_local=now_local):
                continue
            awaits = _promise_awaits(r.content, stored=get_intent_trigger(r))
            if awaits is not None and _signal_seen(awaits, text):
                r.status = "discharged"
                r.discharged_at = now_utc
                db.add(r)
                settled.append(r.id)
        if settled:
            await db.commit()
    if settled:
        _logger.info(
            "Prospective settled silently (event fulfilled) char=%d sess=%s ids=%s",
            character_id, session_id, settled,
        )
    return settled


# ───────────────────────── 线索型：聊天确定性匹配（零 LLM）─────────────────────────
def _cue_hit(cue_terms: list[str], user_text: str) -> bool:
    """线索命中判定（A32 B.3.3 升级）：原字面子串 ＋ **到达类高频变体表**。

    只查 ``_CUE_VARIANTS`` 里逐条列出的低歧义形态（到了/到家/回来/上车/进门），
    不做开放式语义匹配——误触发会把「还惦记着」的提醒打没，比漏触发更伤。
    """
    t = (user_text or "").lower()
    for c in cue_terms:
        c = (c or "").strip()
        if len(c) < 2:
            continue
        cl = c.lower()
        if cl in t or any(v in t for v in _CUE_VARIANTS.get(cl, ())):
            return True
    return False


async def match_cue_intents(character_id: int, user_text: str) -> list[ProspectiveIntent]:
    """当前用户文本命中某 pending/matched cue 的 cue_terms → 返回命中行（子串 + 到达类变体，仍零 LLM）。

    一期：只用于「本轮注入提醒块」，不主动发消息；命中后置 matched（不 discharged，
    因为线索可能被多次提及，是否兑现由对话推进决定，到期/取消再终态）。

    2026-09-15（plans #72）在线硬闸：陈旧 cue（日期型跨天 / 非日期型超窗 / 无 due 创建超 30 天）
    不参与匹配，并顺手置 stale（lazy sweep，双保险——即使每小时周期清扫未跑、或是升级前老数据，
    也不会把陈年线索翻出来注入）。与 promise 在 run_prospective_due 的 cross-day 硬闸同理。
    """
    if not (user_text or "").strip():
        return []
    now_utc, now_local = _now_naive(), _now_local_naive()
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ProspectiveIntent).where(
                ProspectiveIntent.character_id == character_id,
                ProspectiveIntent.status.in_(["pending", "matched"]),
                ProspectiveIntent.kind == "cue",
            )
        )).scalars().all()
        live: list[ProspectiveIntent] = []
        changed = False
        for r in rows:
            if _intent_is_stale(r, now_utc=now_utc, now_local=now_local):
                r.status = "stale"          # 陈旧线索在线退场（留痕不删）
                db.add(r)
                changed = True
                continue
            live.append(r)
        hit = [r for r in live if _cue_hit(_loads_cue_terms(r.cue_terms_json), user_text)]
        for r in hit:
            if r.status == "pending":
                r.status = "matched"
                db.add(r)
                changed = True
        if changed:
            await db.commit()
        return hit


# ───────────────────────── 时间型：到期承诺自然提起（arbiter._execute 调用）─────────────────────────
def _side_split_on() -> bool:
    """① 渲染分流开关：沿用既有 promise_self_side_split（字典默认 True；写 False 才逐字节回退当前口径）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("promise_self_side_split", False))
    except Exception:
        return False


def _is_cross_day(due_end: datetime | None, now_naive: datetime | None = None) -> bool:
    """② due_end 是否已跨天（与 collect 同口径：库内 naive 日期直接比较）。"""
    if due_end is None:
        return False
    return due_end.date() < (now_naive or _now_local_naive()).date()


def _hint_content(candidate: dict, row=None) -> str:
    """兑现提示词该用的正文（Q2b，2026-09-25）：相对时间词按基准日换成绝对日期。

    ``content`` 是当初写下计划的那句话，里面的「明天/下周三」锚定的是写计划那天；兑现时早已
    过期，原样塞进提示词会让模型说错时间（与 Q2 修的现状 trace 同一缺陷）。基准日取候选
    ``created_at``，缺失退 ``updated_at``，两者都缺退 ``row`` 的同名字段（row 可能是 ORM 行
    也可能是 dict）。基准日拿不到、或换算中任何异常 ⇒ 原样返回（fail-open，本函数绝不抛）。
    """
    cand = candidate if isinstance(candidate, dict) else {}
    text = str(cand.get("content") or "").strip()
    if not text:
        return text

    def _field(obj, key):
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    try:
        base = (
            _field(cand, "created_at") or _field(cand, "updated_at")
            or _field(row, "created_at") or _field(row, "updated_at")
        )
        if base is None:
            return text
        from app.utils.relative_time import absolutize_relative_dates
        return absolutize_relative_dates(text, base) or text
    except Exception as e:  # 绝不冒泡到主动消息主链路
        _logger.warning("prospective hint absolutize failed, keep raw: %s", e)
        return text


def _build_prospective_hint_legacy(char_name: str, content: str, guard: str = "") -> str:
    """① flag 关时的旧话术（与 2026-09-04 上线版本逐字节一致，保证可回退）。

    C16 批次C（2026-09-25）：``guard`` 非空时前置「现状锚 + 时空纪律」护栏块；
    默认空串 ⇒ 返回值与旧版逐字节一致（话术本体不在本函数内改动）。
    """
    return guard + (
        f"你是{char_name}。你和用户之前有过一个约定/用户曾提到过：「{content}」。"
        "现在到了合适的时间，请用自己的语气自然提起这件事（可以说'我记得你之前说过…'，"
        "但不要生硬念稿、不要提'AI'、不要加引号标注），并顺势把话题抛给用户，不要替用户做决定。"
    )


def _build_prospective_hint(
    char_name: str, content: str, side: str, due_end: datetime | None = None,
    *, recent_opener: bool = False, now_naive: datetime | None = None, guard: str = "",
) -> str:
    """① 按 side 分流 + ② 跨天时间锚 + ③ 冷却期换开场 的渲染提示词（纯函数）。

    C16 批次C（2026-09-25）：``guard`` 非空时前置到返回文本最前（护栏块由调用点取锚构造，
    本函数保持无 IO 的纯函数）；默认空串 ⇒ 行为逐字不变。
    """
    if side == "self":
        head = (
            f"你是{char_name}。这件事是**你自己**之前说要做的：「{content}」。"
            "现在到了合适的时间，请用你自己的口吻自然提起——自述口径（例如"
            "「我把晚饭的事做好」「昨天说我来做晚饭，这就去弄」），把它当作你自己要去做的事。"
            "禁止使用「你之前说过…」「你之前不是说…」这类把责任推给用户的说法，"
            "禁止把它说成是用户答应的，不要替用户做决定。"
        )
    else:
        head = (
            f"你是{char_name}。用户之前提过/答应过：「{content}」。"
            "现在到了合适的时间，请用**当下询问**的口吻自然问一句（例如「你那边忙完了吗」"
            "「这件事现在方便说说吗」），把它当作此刻的对话，不要替用户做决定。"
        )
    parts = [head]
    parts.append(
        "起头不要用「我记得你之前说过 / 你之前不是说」这类**回忆式开场**——那类句式留给回忆"
        "（翻旧记忆）的场景，这里是你自己/用户约定的事到期了，直接说事本身。"
    )
    if _is_cross_day(due_end, now_naive):
        parts.append(
            "注意：这件事约定的是" + due_end.strftime("%Y-%m-%d") + "，已经跨天。"
            "提起时必须带明确的时间锚（例如「昨天你说过…」「之前你提到…」），"
            "严禁使用「现在时间也差不多了」「现在已经到时间了」这类当下时态断言，"
            "也不得暗示现在正是那个时候。"
        )
    if recent_opener:
        parts.append(
            "另外：你最近一小时内已经用过「我记得你之前说过 / 你之前不是说」这类开场，"
            "这次必须换一种说法，不要再重复同款开场。"
        )
    parts.append("不要生硬念稿、不要提'AI'、不要加引号标注。")
    return guard + "".join(parts)


async def claim_intent_for_fire(intent_id: int) -> bool:
    """③ 幂等认领：pending → discharged（原子条件更新，rowcount==1 才算本次认领成功）。

    同一 intent 的并发/重复触发只有一次能认领成功 → 只落地一条主动消息（幂等键语义）。
    生成/发送失败由调用方 ``_revert_to_pending`` 回滚，失败仍可下轮重试。
    """
    async with async_session_factory() as db:
        res = await db.execute(
            update(ProspectiveIntent)
            .where(ProspectiveIntent.id == intent_id, ProspectiveIntent.status == "pending")
            .values(status="discharged", discharged_at=_now_naive())
        )
        await db.commit()
        return bool(getattr(res, "rowcount", 0))


async def _revert_to_pending(intent_id: int) -> None:
    """认领后生成/发送失败 → 回滚为 pending（只回滚仍处于 discharged 的行）。"""
    async with async_session_factory() as db:
        await db.execute(
            update(ProspectiveIntent)
            .where(ProspectiveIntent.id == intent_id, ProspectiveIntent.status == "discharged")
            .values(status="pending", discharged_at=None)
        )
        await db.commit()


async def recent_opener_exists(character_id: int, *, now: datetime | None = None) -> bool:
    """③ 同款开场冷却：同角色最近 OPENING_COOLDOWN_HOURS 小时内是否已发过同款开场主动消息。"""
    cutoff = (now or _now_naive()) - timedelta(hours=OPENING_COOLDOWN_HOURS)
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ProactiveMessageLog.content).where(
                ProactiveMessageLog.character_id == character_id,
                ProactiveMessageLog.created_at >= cutoff,
            )
        )).scalars().all()
    return any(has_proactive_opening(c) for c in rows)


async def run_prospective_due(candidate: dict) -> bool:
    """把一条到期承诺组织成一次自然主动消息；成功发送后 discharged（一次性）。

    发送走既有 engine.send_to_session（与 run_unfinished_topic 同构），
    走正常 run_tick 的角色分组/优先级/免打扰/额度，不另开旁路通道。

    ③ 幂等（2026-09-13）：进函数先原子认领 pending→discharged，拿不到＝已提过 → 跳过；
    生成/发送失败回滚 pending，下轮仍可重试（失败不算已提）。
    ① 渲染：promise_self_side_split 开 → 按 side 分流 + 跨天时间锚；关 → 旧话术逐字节回退。
    Q2b（2026-09-25）：两个分支的正文都先经 ``_hint_content`` 把相对时间词绝对化（side 判定仍用原文）。
    ②③ 输出闸门：当下时态断言 / 冷却期内同款开场 → 约束重生成一次，仍违规则跳过。
    ⑨（A34 批3）回忆式开场（「我记得你之前说过…」）无条件拦回，且不再由提示词邀请——
    这类开场只留给回忆通道（memory_review）。
    ⑧（A34 批3）双发抑制：认领前 + 发送前各查一次「同会话 N 秒内已有 AI 消息」，命中即本轮不抢发
    （认领前命中＝不认领、发送前命中＝回滚 pending），状态保持 pending 可下轮重试，不写发送日志。
    A32（2026-10-07）现状校验：认领**之前**先看「本会话最新用户消息 + 现状锚」是否已出现该承诺在等
    的事件信号（到家类 / 吃药类，确定性正则、零 LLM）；已兑现 → 静默 discharged 并返回 True，
    不生成、不发送、不写 proactive_message_logs。
    """
    char_id = candidate["character_id"]
    user_id = candidate["user_id"]
    session_id = candidate.get("session_id") or candidate.get("chat_session_id")
    content = (candidate.get("content") or "").strip()
    pis_id = candidate.get("pis_id")
    if pis_id is None or not session_id:
        return False
    intent_id = int(pis_id)
    # 2026-09-14 防御性硬闸：日期型约定跨天一律不提（即使上游采集漏了，也在这里兜住）
    _due_end = candidate.get("due_end")
    if _is_date_scoped(_due_end) and _due_end.date() < _now_local_naive().date():
        _logger.info("Prospective due skipped (cross-day) pis=%s", pis_id)
        await _set_status([intent_id], "stale")
        return False
    # 2026-09-26 修复（审查 P2-1 防御）：角色或私聊会话已被删除 → 终态取消，
    # 不认领、不调 LLM（避免删角色后每个 tick 白烧一次 + 外键失败回滚重试）。
    async with async_session_factory() as _probe:
        _alive_char = await _probe.get(AICharacter, char_id)
        _alive_sess = await _probe.get(ChatSession, session_id)
    if _alive_char is None or _alive_sess is None:
        _logger.info("Prospective due cancelled (owner/session missing) pis=%s", pis_id)
        await _set_status([intent_id], "cancelled")
        return False
    # A32 现状校验闸门（2026-10-07）：承诺在等的事件**已经发生** → 静默关闭，绝不 fire 催办。
    # 取「本会话最新一条用户消息」＋「现状锚」两个来源，命中即 discharged 后返回；
    # 不发任何消息、不写 proactive_message_logs、不调 LLM。放在认领之前＝不占用幂等认领。
    # A33 ⑥：等待类别优先用候选下发的 trigger 标签（采集层已按「落库值→内容推断」定序），
    # 手工构造的 dict 没有该键时 stored=None，行为与 A32 一致（按内容推断）。
    awaits = _promise_awaits(content, stored=candidate.get("trigger"))
    if awaits is not None:
        latest_user = await _latest_user_message(int(session_id))
        anchor = await state_guard.current_state_anchor(character_id=char_id, user_id=user_id)
        if _signal_seen(awaits, latest_user, anchor):
            _logger.info("Prospective already fulfilled, settle silently pis=%s", pis_id)
            await mark_discharged_many([intent_id])
            return True
    # ⑧ 双发抑制（A34 批3）：同会话 N 秒内已有 AI 消息 ⇒ 本轮**不抢发**（现场：用户说「晚安」后
    # 同秒既有一条即时回复、又有一条「明早八点叫你」的承诺主动提起）。
    # 放在认领**之前**：不认领、不烧 LLM、不写日志，状态留 pending，下个 tick 自然重试。
    _twin = await recent_ai_message_id(int(session_id))
    if _twin is not None:
        _logger.info("Prospective due deferred (AI msg %s within %ds) pis=%s",
                     _twin, PROACTIVE_DUAL_SEND_SUPPRESS_SEC, pis_id)
        return False
    if not await claim_intent_for_fire(intent_id):
        _logger.info("Prospective due skipped (already fired) pis=%s", pis_id)
        return False
    try:
        from app.agent.llm_client import chat_completion
        from app.scheduling import scheduler as engine
        async with async_session_factory() as db:
            char = await db.get(AICharacter, char_id)
        char_name = char.name if char else "我"
        side = candidate.get("side") if candidate.get("side") in ("self", "user") else None
        # 基准日缺失时回退查这一行：与 side 兜底共用同一次 lazy 查询，不多查一遍
        row = None
        if side is None or not (candidate.get("created_at") or candidate.get("updated_at")):
            async with async_session_factory() as db:
                row = await db.get(ProspectiveIntent, intent_id)
        if side is None:
            side = get_intent_side(row) if row is not None else classify_intent_side(content)
        # ③ 开场冷却独立于 ① 的 side 开关（模板复读治理必须始终生效）；
        # ① side 分流才由 promise_self_side_split 门控（关＝旧话术逐字节回退）。
        recent_opener = await recent_opener_exists(char_id)
        # Q2b：进提示词的正文按基准日绝对化；side 判定仍走原文（上面已定），两者不掺混
        hint_content = _hint_content(candidate, row)
        # C16 批次C（2026-09-25）：两个分支共用「现状锚 + 时空纪律」护栏块，
        # 取锚与文案唯一来源 scheduling/state_guard.py（内部 fail-open，拿不到锚只降级为纯纪律段）
        _guard = state_guard.guard_block(
            await state_guard.current_state_anchor(character_id=char_id, user_id=user_id))
        if _side_split_on():
            hint = _build_prospective_hint(
                char_name, hint_content, side, candidate.get("due_end"), recent_opener=recent_opener,
                guard=_guard,
            )
        else:
            hint = _build_prospective_hint_legacy(char_name, hint_content, guard=_guard)
        _sys = {"role": "system", "content": "直接输出内容，不要加引号和标注。"}

        def _gated(text: str) -> bool:
            """② 当下时态断言 / ⑨ 回忆式开场：无条件拦；③ 同款开场：仅冷却期内拦。"""
            return (has_forbidden_present_tense(text)
                    or starts_with_recall_opener(text)
                    or (recent_opener and has_proactive_opening(text)))

        msg = (await chat_completion(
            messages=[_sys, {"role": "user", "content": hint}],
            temperature=0.85, max_tokens=256, task="message",
        ) or "").strip().strip('"').strip("'")
        if _gated(msg):
            constrained = hint + (
                "\n严禁使用「现在时间也差不多了」这类当下时态断言；"
                "严禁以「我记得你之前说过 / 你之前不是说」这类回忆式开场起头"
                "（回忆式开场只留给回忆通道）；改用自述或当下询问的口吻直接说事本身。"
            )
            msg = (await chat_completion(
                messages=[_sys, {"role": "user", "content": constrained}],
                temperature=0.7, max_tokens=256, task="message",
            ) or "").strip().strip('"').strip("'")
            if _gated(msg):
                _logger.info("Prospective due gated (opening/present-tense) pis=%s", pis_id)
                await _revert_to_pending(intent_id)
                return False
        if len(msg) < 2:
            await _revert_to_pending(intent_id)
            return False
        # ⑧ 终局双发闸：上面这两次 LLM 生成之间，会话里可能刚好落了用户的即时回复
        # （正是「同秒双发」的真实窗口）。命中则**回滚 pending**、不发送、不写日志。
        _twin = await recent_ai_message_id(int(session_id))
        if _twin is not None:
            _logger.info("Prospective due skipped before send (AI msg %s within %ds) pis=%s",
                         _twin, PROACTIVE_DUAL_SEND_SUPPRESS_SEC, pis_id)
            await _revert_to_pending(intent_id)
            return False
        await engine.send_to_session(
            session_id, char_id, user_id, msg, message_type="prospective_intent",
            extra_meta=json.dumps(
                {"pis_id": intent_id, "idem": fired_idempotency_key(intent_id)},
                ensure_ascii=False,
            ),
        )
        _logger.info("Prospective due sent char=%d pis=%s side=%s", char_id, pis_id, side)
        return True
    except Exception as e:
        await _revert_to_pending(intent_id)
        _logger.warning("prospective_due failed pis=%s: %s", pis_id, e)
        return False  # 回滚 pending，等下个 tick（配合 collect 的到期顺序自然重试）
