# -*- coding: utf-8 -*-
"""念头池 T2 影子供给（A4 批 4 / M1，2026-09-30）：thought_pool 表的唯一写口。

定位：``app/domain/thought/`` 是纯规则（零 IO），本模块负责把规则结果落库——
「抽取 → 源侧配额/准入 → 幂等去重 → 落库 → 留痕」。与 ``relational_drive_service``（批 3
T1 M1b1）同级同规格：**本批零调用方**（三处抽取挂点属 M2，设计 §7）。

**本批没有发送权**：全模块不碰任何生成/发送链路（不改 ``arbiter`` / ``message_generator`` /
调度器，不读也不写 ``proactive_message_logs``），落池只影响这张新表；候选文本进 prompt 的
口径属 M2-a。⇒ 默认（flag 关）产品行为逐字节不变。

硬约束：
- flag ``thought_pool_shadow`` 关 ⇒ 入口首行即返回：不查库、不写库、不建对象（零行为零开销）；
- 用调用方传入的 AsyncSession，不自开 session；写操作只 ``add`` / ``flush``，**是否 commit 由
  调用方决定**（本层不持 session 故不 commit，避免替别人提前落账）；
- 影子层任何异常一律不外抛（留痕/落池出错绝不能把业务拖下水），只在返回值里标 error；
- import 期零 IO。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from app.domain.thought import filters as fl
from app.domain.thought import quota as qt
from app.domain.thought import dynamics as dyn
from app.domain.thought import extract as ex
from app.domain.thought import settle as st
from app.models.character import ThoughtPool
from app.utils.timeutil import now_naive_utc

_logger = logging.getLogger(__name__)

# 影子总闸键（登记在 app/flags/agent_flags.py；默认关）
FLAG_KEY = "thought_pool_shadow"
# 生效总闸键（M2-b1，登记在 app/flags/agent_flags.py；默认关）。开它隐含 shadow 语义在跑
# （不抽池就没有念头可取，设计 §4 两键关系）；本键只控制「取用 + 释放 + 注入」。
V1_FLAG_KEY = "thought_pool_v1"
# ── M2-b1 灰度：角色白名单 ∧ 稳定比例桶（照 scheduling/pacing.py 既有范式，设计 §4）──
# 白名单 2 角色：含 char 13（与既有节律/驱力/存活清单灰度同角色，便于三批对照观测），
# 另取 14 凑满设计要求的「2 个角色」。扩量终点＝清空集（约定：空白名单＝全量，仍受 ratio 约束）。
THOUGHT_POOL_GRAY_CHARS = frozenset({13, 14})
THOUGHT_POOL_GRAY_RATIO = 1.0
# 留痕位：agent_task_logs.route（String(30)，本值 19 字符）。选 trace 队列表而不是
# proactive_trigger_logs——后者 7 天被清（scripts/backup.py TRIGGER_LOG_KEEP_DAYS），配额读数
# 要留到判效窗之后。
TRACE_ROUTE = "thought_pool_shadow"
TRACE_TRIGGER = "thought_pool"
_STEPS_MAX = 1600
# 配额播种回看天数：北京日界最多跨 32 小时，回看 3 天足够覆盖「今天」且不会全表扫
_SEED_LOOKBACK_DAYS = 3


def shadow_enabled() -> bool:
    """影子总闸（缺省关；连读 flag 都失败也按关——观测层不得把业务拖下水）。"""
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        return bool(AGENT_FLAGS.get(FLAG_KEY, False))
    except Exception:
        return False


def _resolve_now(now: datetime | None) -> datetime:
    return now if now is not None else now_naive_utc()


def _extract_drafts(face: str, row: dict, shared_refs: frozenset[str]) -> list[dict]:
    """按来源面调用对应抽取器（各面入参签名不同，这里做统一分发）。

    F3 的重复供给由**结构化键**拦（``qt.source_key``，设计 §3.2 的 M1 订正位），不再传
    「朋友圈原文哈希集合」——那样会把每一条 F3 都按自己那条原文封锁掉（方案 F §5.2）。
    """
    if face == ex.SRC_ACTIVITY:
        return ex.extract_activity(row, shared_refs)
    if face == ex.SRC_MOMENT:
        return ex.extract_moment(row)
    extractor = ex.FACE_EXTRACTORS.get(face)
    return extractor(row) if extractor else []


async def _seed_state(db, character_ids: set[int], moment: datetime) -> tuple:
    """将「当日各面已用条数」与「已落结构化键」从池里读出来（配额播种，须跨进程持久）。

    配额必须是**跨进程持久**的：只在本批内存里计数的话，服务重启当天就能再灌一遍。
    返回 ``(used_by_pair_day, seen_source_keys)``；用与判定端**同一个** ``pair_day_key``
    分桶，避免两处各写一套日界。
    """
    if not character_ids:
        return {}, set()
    since = moment - timedelta(days=_SEED_LOOKBACK_DAYS)
    used: dict[tuple, int] = {}
    rows = (await db.execute(
        select(
            ThoughtPool.character_id, ThoughtPool.user_id,
            ThoughtPool.source_type, ThoughtPool.created_at, ThoughtPool.status,
        ).where(
            ThoughtPool.character_id.in_(character_ids),
            ThoughtPool.created_at >= since,
        )
    )).all()
    for char_id, user_id, source_type, created_at, status in rows:
        if str(status) == dyn.STATUS_SPENT:
            continue  # 留痕行只记账、不占额度
        key = qt.pair_day_key(char_id, user_id, source_type, created_at)
        used[key] = used.get(key, 0) + 1
    seen = {
        qt.dedup_key(char_id, user_id, source_type, source_ref)
        for char_id, user_id, source_type, source_ref in (await db.execute(
            select(
                ThoughtPool.character_id, ThoughtPool.user_id,
                ThoughtPool.source_type, ThoughtPool.source_ref,
            ).where(ThoughtPool.character_id.in_(character_ids))
        )).all()
    }
    return used, seen


def _new_row(draft: dict, *, status: str, salt: float, nov: float, moment: datetime) -> ThoughtPool:
    return ThoughtPool(
        character_id=int(draft.get("character_id") or 0),
        user_id=int(draft.get("user_id") or 0),  # 0＝角色级（见模型 docstring）
        thought_kind="",                         # intent 同族绑定属 M2（本批无发送权）
        text=str(draft.get("text") or "")[:ex.POOL_TEXT_MAX_LEN],
        source_type=str(draft.get("source_type") or ""),
        source_ref=str(draft.get("source_ref") or "")[:64],
        text_hash=ex.text_hash(draft.get("text")),
        status=status,
        salt=salt,
        novelty=nov,
        hit_sources=json.dumps([str(draft.get("source_type") or "")], ensure_ascii=False),
        tell_count=0,
        created_at=moment,
        last_hit_at=moment,
        spent_at=moment if status == dyn.STATUS_SPENT else None,
    )


def _trace(stats: dict) -> None:
    """影子留痕（fire-and-forget）：一条 trace 记全批配额读数，异常自吞。"""
    try:
        from app.agent.trace import enqueue_task_log, new_task_id

        enqueue_task_log(
            task_id=new_task_id(),
            character_id=stats.get("character_id"),
            trigger=TRACE_TRIGGER,
            route=TRACE_ROUTE,
            steps_json=json.dumps(stats, ensure_ascii=False, default=str)[:_STEPS_MAX],
            status="ok",
        )
    except Exception as e:  # 留痕失败绝不阻塞落池
        _logger.warning("thought pool shadow trace failed: %s", e)


def intake_thoughts_core(
    drafts: list[dict],
    *,
    used: dict[tuple, int],
    seen_source_keys: set[str],
    recent_texts: dict[int, tuple[str, ...]] | None = None,
    now: datetime | None = None,
    caps: dict[str, int] | None = None,
    min_novelty: float | None = None,
    faded_refs: frozenset[str] = frozenset(),
) -> tuple[list[ThoughtPool], dict]:
    """纯判定段（不落库、不查库，便于单测直接喂候选）：三道过滤 → 准入 → 配额 → 幂等键。

    ``used`` / ``seen_source_keys`` 由调用方持有并在放行时就地自增——这样同一批里第二条同面
    候选能看到第一条，不需要每建一行就回查一次库。返回 ``(待 add 的行, 读数)``；
    **超额丢弃、不延后**（延后＝把今天的洪峰挪到明天，配额就退化成排队）。

    ``faded_refs``（批 4 M2-b2 防线 5）：与 ``life_regression`` 同源去重——来源主键落在此集合
    的候选，说明该活动近 24h 已被生活回灌通道讲过/将讲，**直接标 ``faded`` 留痕**（不删行、
    不占额度、永不参与选择），避免「我最近做了什么」被两个通道各讲一遍。
    """
    moment = _resolve_now(now)
    by_recent = recent_texts or {}
    rows: list[ThoughtPool] = []
    dropped: dict[str, int] = {}
    by_face: dict[str, int] = {}
    spent_logged = 0
    faded_logged = 0

    for draft in drafts:
        face = str(draft.get("source_type") or "")
        text = str(draft.get("text") or "")
        char_id = int(draft.get("character_id") or 0)
        user_id = int(draft.get("user_id") or 0)
        # 闸 0：§2.2 三道确定性过滤（长度 / 撞最近已发 / 设定），沿用 M0 串行短路计数
        reason = fl.intake_reject_reason(text, draft.get("epistemic_status"), by_recent.get(char_id, ()))
        nov = dyn.novelty(dyn.age_days(draft.get("created_at") or moment, moment))
        salt = dyn.salt_of([face])
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
            continue
        key = qt.dedup_key(char_id, user_id, face, draft.get("source_ref"))
        if key in seen_source_keys:
            dropped[qt.DROP_DUP_KEY] = dropped.get(qt.DROP_DUP_KEY, 0) + 1
            continue
        # 方案 A 留痕行：同源已被 life_share 讲掉 ⇒ 只记一行 spent，不占额度、不参与选择
        if str(draft.get("status_hint") or "") == dyn.STATUS_SPENT:
            seen_source_keys.add(key)
            rows.append(_new_row(draft, status=dyn.STATUS_SPENT, salt=salt, nov=nov, moment=moment))
            spent_logged += 1
            continue
        # 批 4 M2-b2 防线 5：同源已被 life_regression 回灌讲过 ⇒ 只记一行 faded（不删行、不占额度、
        # 永不参与选择）。与 spent 留痕行同形，区别仅终态（spent＝被接住讲完，faded＝让位回灌通道）。
        if str(draft.get("source_ref") or "") in faded_refs:
            seen_source_keys.add(key)
            rows.append(_new_row(draft, status=dyn.STATUS_FADED, salt=salt, nov=nov, moment=moment))
            faded_logged += 1
            continue
        gate = qt.admit_reject_reason(
            source_type=face, novelty_value=nov,
            used_today=used.get(qt.pair_day_key(char_id, user_id, face, moment), 0),
            caps=caps, min_novelty=min_novelty,
        )
        if gate:
            dropped[gate] = dropped.get(gate, 0) + 1
            continue
        used[qt.pair_day_key(char_id, user_id, face, moment)] = (
            used.get(qt.pair_day_key(char_id, user_id, face, moment), 0) + 1
        )
        seen_source_keys.add(key)
        rows.append(_new_row(draft, status=dyn.STATUS_SPARK, salt=salt, nov=nov, moment=moment))
        by_face[face] = by_face.get(face, 0) + 1

    stats = {
        "seen": len(drafts),
        "intake": len(rows) - spent_logged - faded_logged,
        "spent_logged": spent_logged,
        "faded_logged": faded_logged,
        "dropped": dropped,
        "dropped_total": sum(dropped.values()),
        "by_face": by_face,
    }
    return rows, stats


async def supply_thought_pool(
    db,
    rows_by_face: dict[str, list[dict]],
    *,
    now: datetime | None = None,
    shared_refs: frozenset[str] = frozenset(),
    faded_refs: frozenset[str] = frozenset(),
    recent_texts: dict[int, tuple[str, ...]] | None = None,
    caps: dict[str, int] | None = None,
    min_novelty: float | None = None,
) -> dict:
    """影子供给唯一入口：取数结果 → 抽取 → 配额/准入/去重 → 落库（add+flush）→ 留痕。

    ``rows_by_face`` ＝ ``{来源面: [原始行 dict]}``（行形状见 ``app/domain/thought/extract``
    各抽取器；取数由调用方负责，本层不新建查询——设计 §3.1「一条内核闸都不新建、读法复用现成」）。
    ``shared_refs``（防线 1）：已被 ``life_share`` 当场讲掉的来源主键 ⇒ 只写 spent 留痕行；
    ``faded_refs``（防线 5）：已被 ``life_regression`` 回灌讲过的来源主键 ⇒ 只写 faded 留痕行。
    返回读数 dict（供判效聚合）；**flag 关 ⇒ 返回 ``{}`` 且不查库不写库**。
    """
    if not shadow_enabled():
        return {}
    moment = _resolve_now(now)
    try:
        drafts: list[dict] = []
        chars: set[int] = set()
        for face, face_rows in (rows_by_face or {}).items():
            for row in face_rows or []:
                try:
                    for draft in _extract_drafts(str(face), dict(row), shared_refs):
                        draft.setdefault("created_at", moment)
                        drafts.append(draft)
                        chars.add(int(draft.get("character_id") or 0))
                except Exception as e:  # 单行抽取异常不外抛、不拖垮整批
                    _logger.warning("thought extract failed face=%s: %s", face, e)
        used, seen = await _seed_state(db, chars, moment)
        rows, stats = intake_thoughts_core(
            drafts, used=used, seen_source_keys=seen, recent_texts=recent_texts,
            now=moment, caps=caps, min_novelty=min_novelty, faded_refs=faded_refs,
        )
        for row in rows:
            db.add(row)
        if rows:
            await db.flush()  # 是否 commit 由调用方决定
        stats["character_id"] = next(iter(chars), None)
        stats["flag"] = FLAG_KEY
        _trace(stats)
        return stats
    except Exception as e:
        _logger.warning("thought pool shadow supply failed (isolated): %s", e)
        return {"error": str(e), "flag": FLAG_KEY, "intake": 0, "dropped": {}, "dropped_total": 0}


# ── 批 4 M2-b2（2026-10-01）防线 1：与 life_share 同源二选一的「分享是否成功」判据 ──
# life_share 成功路径在发送前写一条 ProactiveTriggerLog(trigger_type='life_share',
# decision='approved') 并 commit（scheduling/life_share.py:312-314）；事件总线**顺序**执行
# 订阅者（events/bus.py:32-36），且念头挂点订阅在 life_share 之后（handlers.py 注册序），
# 故本挂点运行时该 approved 行已落库。只读这条既有留痕判定成败，**不碰 life_share 任何发送逻辑**。
# 识别窗口取 10 分钟：life_share 每角色 6h≤1（life_share._quota_ok），同 tick 的 approved 行
# created_at≈now，10 分钟窗既能覆盖本次、又能避开 6h 内更早的其它活动。
_LIFE_SHARE_RECENT_MINUTES = 10
_LIFE_SHARE_TRIGGER_TYPE = "life_share"


async def life_share_succeeded(db, character_id, *, now: datetime | None = None) -> bool:
    """防线 1：本次「活动完成」是否已被 ``life_share`` 当场分享成功（读其既有 approved 留痕）。

    成功 ⇒ 该活动只写一条 ``spent`` 留痕行（永不参与选择）；未成功 ⇒ 才以 ``spark`` 入池
    （设计 §3.2 第 1 行「同源归属二选一」）。异常一律按 False（未成功）处理——宁可多入一条
    spark，也不把没讲过的活动误判成已讲掉而丢料；判定失败绝不外抛。
    """
    if not character_id:
        return False
    try:
        from app.models.character import ProactiveTriggerLog
        moment = _resolve_now(now)
        since = moment - timedelta(minutes=_LIFE_SHARE_RECENT_MINUTES)
        row = (await db.execute(
            select(ProactiveTriggerLog.id).where(
                ProactiveTriggerLog.character_id == int(character_id),
                ProactiveTriggerLog.trigger_type == _LIFE_SHARE_TRIGGER_TYPE,
                ProactiveTriggerLog.decision == "approved",
                ProactiveTriggerLog.created_at >= since,
            ).limit(1)
        )).first()
        return row is not None
    except Exception as e:
        _logger.warning("life_share_succeeded check failed char=%s: %s", character_id, e)
        return False


# ══════════════════════════════════════════════════════════════════════════
# M2-b1（2026-10-01）：生效侧——「取一条」+「三档释放结算」+「聊天侧注入文本」
#
# 纪律（设计 §4「关＝逐字节旧行为」四条硬保证 + 派单硬约束）：
#   - 每个入口**先判 flag／灰度再查库**：v1 关或角色未命中白名单 ⇒ 首行即返回，一次 SQL 都不发
#     （照 arbiter._pacing_gate 与上面 shadow_enabled 的早退写法）；
#   - 本层**没有发送权**：不碰生成/发送链路、不写 proactive_message_logs、不改 intent；
#   - 释放结算幂等：spent_at 非空的行不再改（设计 §2.3 / §6 R7）；
#   - 任何异常一律不外抛（生效层出错绝不能把主动消息/聊天主链路拖下水），只返回空/原值。
# ══════════════════════════════════════════════════════════════════════════

def v1_enabled() -> bool:
    """生效总闸（缺省关；连读 flag 都失败也按关——生效层不得把业务拖下水）。"""
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        return bool(AGENT_FLAGS.get(V1_FLAG_KEY, False))
    except Exception:
        return False


def thought_pool_gray_hit(character_id, session_id=None) -> bool:
    """角色是否命中念头池灰度（白名单 ∧ 比例桶，设计 §4）。

    character_id 为空/非法 ⇒ False（fail-closed 到不生效）。

    口径与 ``scheduling/pacing.py`` 既有范式同构（白名单 ∧ 稳定比例桶）；本批
    ``THOUGHT_POOL_GRAY_RATIO = 1.0`` ⇒ 比例桶恒命中，故灰度判定退化为**纯白名单成员**
    （不引 pacing、不查库，保持服务层 import 边界与 M1 一致）。将来若把 ratio 调到 <1，
    再接 ``pacing.traffic_hit`` 补比例桶（届时属行为变更，需另开单）。
    """
    if character_id is None:
        return False
    try:
        cid = int(character_id)
    except (TypeError, ValueError):
        return False
    if THOUGHT_POOL_GRAY_CHARS and cid not in THOUGHT_POOL_GRAY_CHARS:
        return False
    # ratio>=1 ⇒ 全量命中（与 pacing.traffic_hit 的 ratio>=1 短路同口径）
    return THOUGHT_POOL_GRAY_RATIO >= 1.0


def thought_pool_v1_allowed(character_id, session_id=None) -> bool:
    """生效侧统一准入：flag 开 ∧ 角色命中灰度。**不查库**，供各入口首行早退用。"""
    return v1_enabled() and thought_pool_gray_hit(character_id, session_id)


async def fetch_one_thought(db, character_id, user_id, *, intent: str | None = None) -> dict | None:
    """「取一条」：从池里取该角色对该用户当前**最咸**的一条活跃念头（设计 §2.6 / §7 M2 行）。

    - **先判 flag／灰度再查库**：v1 关或角色未命中白名单 ⇒ 立即返回 None，一次 SQL 都不发；
    - 只在 ``ACTIVE_STATUSES``（spark/obsession/told_flat）里取，按 ``salt`` 降序、``id`` 升序兜底，
      取 1 条（命中索引 ``ix_thought_pool_char_user_status_salt``）；
    - ``intent`` 仅用于留痕/未来 thought_kind 同族绑定（本批不做词表绑定，传了也不改选择）；
    - 返回 dict（id/text/status/salt/tell_count/novelty），无可用念头 ⇒ None；异常 ⇒ None（不外抛）。

    本函数**只读**，不写库、不改状态（释放结算另走 ``settle_release``，由发送结果驱动）。
    """
    if not thought_pool_v1_allowed(character_id):
        return None
    try:
        cid = int(character_id)
        uid = int(user_id or 0)
    except (TypeError, ValueError):
        return None
    try:
        active = tuple(dyn.ACTIVE_STATUSES)
        row = (await db.execute(
            select(ThoughtPool)
            .where(
                ThoughtPool.character_id == cid,
                ThoughtPool.user_id == uid,
                ThoughtPool.status.in_(active),
            )
            .order_by(ThoughtPool.salt.desc(), ThoughtPool.id.asc())
            .limit(1)
        )).scalars().first()
        if row is None:
            return None
        return {
            "id": int(row.id),
            "text": str(row.text or ""),
            "status": str(row.status or ""),
            "salt": float(row.salt or 0.0),
            "tell_count": int(row.tell_count or 0),
            "novelty": float(row.novelty or 0.0),
            "intent": str(intent or ""),
        }
    except Exception as e:
        _logger.warning("thought pool fetch_one failed char=%s (isolated): %s", character_id, e)
        return None


async def settle_release(
    db,
    thought_id: int | None,
    *,
    sent_ok: bool,
    replied_within_window: bool,
    now: datetime | None = None,
) -> dict:
    """三档释放结算（设计 §2.3）：按发送结果把被引用的那条念头写回 ``thought_pool``。

    用 M2-a 的纯函数 ``settle.apply_release`` 算出写回字段（status/salt/tell_count/spent_at），
    本函数只负责**落库 + 幂等守卫**：
      - **先判 flag**：v1 关 ⇒ 立即返回 ``{}``，一次 SQL 都不发；
      - thought_id 为空 ⇒ ``{}``（没绑定念头就无从结算）；
      - 行不存在 ⇒ ``{}``；``spent_at`` 非空（已全额释放）⇒ 原样跳过（幂等，设计 §6 R7）；
      - ``never_told``（没发出去）⇒ 不惩罚、不写库（apply_release 原样返回，本函数据此跳过 update）；
      - 是否 commit 由调用方决定（本层不持 session 故不 commit，与 M1 供给口同纪律）。
    返回读数 dict（含 release 档位），异常 ⇒ ``{"error": ...}``（不外抛）。
    """
    if not v1_enabled():
        return {}
    if not thought_id:
        return {}
    moment = _resolve_now(now)
    try:
        row = (await db.execute(
            select(ThoughtPool).where(ThoughtPool.id == int(thought_id))
        )).scalars().first()
        if row is None:
            return {"thought_id": int(thought_id), "release": "row_missing"}
        if row.spent_at is not None:
            # 幂等位：已全额释放的行不再改（设计 §2.3「spent_at IS NOT NULL 的行不再改」）
            return {"thought_id": int(thought_id), "release": dyn.STATUS_SPENT, "idempotent": True}
        before = {
            "status": str(row.status or dyn.STATUS_SPARK),
            "salt": float(row.salt or 0.0),
            "tell_count": int(row.tell_count or 0),
        }
        out = st.apply_release(
            before,
            sent_ok=bool(sent_ok),
            replied_within_window=bool(replied_within_window),
            spent_at=moment,
        )
        release = str(out.get("release") or "")
        if release == "never_told":
            # 没发出去 ⇒ 不惩罚、不写库（设计 §2.3：未用的东西不该被惩罚）
            return {"thought_id": int(thought_id), "release": "never_told", "written": False}
        row.status = str(out.get("status") or before["status"])
        row.salt = float(out.get("salt") or 0.0)
        row.tell_count = int(out.get("tell_count") or 0)
        if out.get("spent_at") is not None:
            row.spent_at = out["spent_at"]
        row.last_hit_at = moment
        await db.flush()
        return {
            "thought_id": int(thought_id), "release": release, "written": True,
            "status": row.status, "salt": row.salt, "tell_count": row.tell_count,
        }
    except Exception as e:
        _logger.warning("thought pool settle_release failed id=%s (isolated): %s", thought_id, e)
        return {"error": str(e), "thought_id": thought_id}


def build_injection_text(thought: dict | None) -> str:
    """把取到的那条念头拼成**注入文本**（聊天侧 append 分区与主动侧素材共用同一口径）。

    硬约束（派单 + 设计 §8 不做清单第 7 条）：**不写元叙述**——不得出现「你有 N 条念头」
    「念头池」「执念」这类实现概念，只写那一件事本身。空/无效 ⇒ 空串（调用方据此跳过注入）。
    """
    if not thought:
        return ""
    text = str(thought.get("text") or "").strip()
    if not text:
        return ""
    # 只呈现「这件事」本身，作为可自然提起的谈资；不带计数、不带状态、不带来源面术语。
    return f"【可自然提起的一件事】{text}（如与当下语境相称，可自然聊起；不相称就放着，别硬提。）"

