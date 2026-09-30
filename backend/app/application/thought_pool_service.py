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
from app.models.character import ThoughtPool
from app.utils.timeutil import now_naive_utc

_logger = logging.getLogger(__name__)

# 影子总闸键（登记在 app/flags/agent_flags.py；默认关）
FLAG_KEY = "thought_pool_shadow"
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
) -> tuple[list[ThoughtPool], dict]:
    """纯判定段（不落库、不查库，便于单测直接喂候选）：三道过滤 → 准入 → 配额 → 幂等键。

    ``used`` / ``seen_source_keys`` 由调用方持有并在放行时就地自增——这样同一批里第二条同面
    候选能看到第一条，不需要每建一行就回查一次库。返回 ``(待 add 的行, 读数)``；
    **超额丢弃、不延后**（延后＝把今天的洪峰挪到明天，配额就退化成排队）。
    """
    moment = _resolve_now(now)
    by_recent = recent_texts or {}
    rows: list[ThoughtPool] = []
    dropped: dict[str, int] = {}
    by_face: dict[str, int] = {}
    spent_logged = 0

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
        "intake": len(rows) - spent_logged,
        "spent_logged": spent_logged,
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
    recent_texts: dict[int, tuple[str, ...]] | None = None,
    caps: dict[str, int] | None = None,
    min_novelty: float | None = None,
) -> dict:
    """影子供给唯一入口：取数结果 → 抽取 → 配额/准入/去重 → 落库（add+flush）→ 留痕。

    ``rows_by_face`` ＝ ``{来源面: [原始行 dict]}``（行形状见 ``app/domain/thought/extract``
    各抽取器；取数由调用方负责，本层不新建查询——设计 §3.1「一条内核闸都不新建、读法复用现成」）。
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
            now=moment, caps=caps, min_novelty=min_novelty,
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
