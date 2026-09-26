"""AI 自主评星（P2，2026-08-05）：LLM 按内容批量评估记忆重要性 → 更新 importance/S，标记 ai_rated。

- 每角色每日上限 AI_RATING_MAX_PER_CHAR（10 条），单次 LLM 批量 AI_RATING_BATCH（10 条）
- 只评未评过（ai_rated=False）且非置顶/锁定/归档的记忆
- 评星结果与手动评星同语义：importance = star*20（上限 120%）、S 拉高、刷新遗忘起点、清除删除倒计时
- 成本：每角色每天最多 1 次 LLM 调用（批量 10 条，≈0.5-1k token），可控
"""
import json
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.db.database import async_session_factory
from app.domain.decision import ask_score
from app.models.memory import Memory
from app.models.character import AICharacter
from app.utils.logger import get_logger
from app.memory.rating_quota import add as _quota_add, used_today as _quota_used
from app.memory.rating_quota import diff_observed
from app.memory.constants import (
    AI_RATING_MAX_PER_CHAR, AI_RATING_BATCH, S_MAX_DAYS, S_MIN_DAYS,
    DECAY_MAX_PCT, S_DEFAULT,
)

_logger = get_logger("memory.ai_rating")


from app.utils.timeutil import now_naive_utc as _now_naive


# ── 评星留痕（2026-09-26 派单 Part A）：批次 + 输入摘要 + 输出星分 + 入口计数 ──
#
# 落在 agent_task_logs（不选 memory_write_receipts，理由）：
# ① 本表有**带索引的 task_id**（String(40)）⇒ 批次号写进 task_id 后
#    ``WHERE task_id='<batch_id>'`` 一次就能把该批的输入行/输出行整体拉回，正是
#    「按批次还原输入 + 输出」这件事；receipt 表没有批次列（只有 character_id/memory_id/action），
#    批次只能塞 detail_json 靠 json_extract 聚合，且 memory_id 是单值列——一批 10 条要么拆 10 行、
#    要么丢掉「哪条评了几星」的对应关系。
# ② 语义归属：receipt 表答「这条记忆为什么在/不在库里」（写入终态追踪，action 枚举被下游按名读取，
#    评星不是写入回执）；评星留痕答「这一拍喂了什么、评了几星、有没有被调用」＝执行流水，
#    本就是 agent_task_logs 的定位（memory_obs、decision_layer_shadow 两条同类留痕都在这张表）。
# ③ 星分本身仍写 memories.importance（行为不变），留痕只是旁证，不改任何写库路径。
TRACE_TRIGGER = "memory_obs"     # 与 app/memory/observability.py 同一 trigger 口径
TRACE_ROUTE_RUN = "ai_rating_run"      # 入口计数：这一拍被调用了几次（start/end 各一条）
TRACE_ROUTE_CHAR = "ai_rating_char"    # 每角色结论：评了几星 / 为什么跳过
TRACE_ROUTE_INPUT = "ai_rating_input"  # 每角色输入摘要：候选 id + 正文前 80 字
TRACE_TASK_PREFIX = "ar"               # task_id 前缀（一眼认出是评星批次）
TRACE_STEPS_MAX = 1600                 # steps_json 上限，与 obs_event / decision shadow 同口径
TRACE_CONTENT_CHARS = 80               # 正文留痕＝喂模型的同一口径（那里也截 80，不再截更狠）
TRACE_INPUT_CHUNK = 10                 # 输入摘要每行条数（10×(80+id) < 上限，防截断成坏 JSON）


def _new_batch_id(now) -> str:
    """批次号＝时间戳 + 短随机（23 字符，落 agent_task_logs.task_id 的 String(40) 内）。"""
    return f"{TRACE_TASK_PREFIX}{now:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"


def _trace_event(batch_id: str, character_id, route: str, detail: dict, *, status: str = "ok") -> None:
    """写一条评星留痕（fire-and-forget：不 await、失败只记 WARNING，绝不影响评星主链路）。

    刻意不直接复用 ``observability.obs_event``：它自己 ``new_task_id()``，同批多行拿不到同一个号；
    这里要把批次号写进**带索引的 task_id**（``trigger/route/steps_json`` 字段风格与它保持一致）。
    """
    try:
        from app.agent.trace import enqueue_task_log
        enqueue_task_log(
            task_id=batch_id,
            character_id=character_id,
            trigger=TRACE_TRIGGER,
            route=route[:30],
            steps_json=json.dumps({"batch_id": batch_id, **detail},
                                  ensure_ascii=False, default=str)[:TRACE_STEPS_MAX],
            status=status,
        )
    except Exception as e:
        _logger.warning("AI rating trace event failed(%s): %s", route, e)


def _trace_inputs(batch_id: str, character_id, items: list) -> None:
    """输入摘要：每条记忆的 id + 正文前 80 字（与 _rate_batch 喂模型的口径一致），按块分行防截断。"""
    entries = [{"id": m.id, "preview": (m.content or "")[:TRACE_CONTENT_CHARS]} for m in items]
    for seq in range(0, len(entries), TRACE_INPUT_CHUNK):
        _trace_event(batch_id, character_id, TRACE_ROUTE_INPUT, {
            "seq": seq // TRACE_INPUT_CHUNK,
            "candidate_count": len(entries),
            "candidates": entries[seq:seq + TRACE_INPUT_CHUNK],
        })


def _note_parse_failure(obs: dict | None, reason: str, raw: str) -> None:
    """解析失败的留痕（obs=None ⇒ 调用方不留痕，与本层接线前逐字同行为）。"""
    if obs is None:
        return
    obs["fail_reason"] = reason
    obs["raw_len"] = len(raw)
    obs["raw_head"] = raw[:TRACE_CONTENT_CHARS]


async def _daily_rated(_db, character_id: int) -> int:
    """今天（北京时间）已评星条数——读独立账本。

    原实现按 ``memories.updated_at`` 近似统计，会被**同拍执行**的记忆衰减刷新的
    ``updated_at`` 污染（2026-09-25 实测：衰减刷新 1089 行后评星恒被判「今日已评满」
    ⇒ rated=0 且无日志，评星因此从 09-20 起静默停摆）。详见 app/memory/rating_quota.py。
    """
    return _quota_used(character_id)


async def _observed_today_counts(db) -> dict[int, int]:
    """库内「今日已评星」观测值：ai_rated=1 且 last_reinforce_at >= 北京今日零点（换算成 UTC-naive）。

    为什么用 last_reinforce_at：评星落库时**只有** ai_rating.apply 会同时写 ai_rated=1 与 = now；
    不能用 updated_at（衰减结算也会刷新它 —— 这正是 09-25 那次停摆的根因）。
    查询失败返回 {}（调用方只跳过对账，绝不阻断评星）。

    ⚠ 口径警示（2026-09-26 批次 C 复核）：**这个观测值偏高** —— ``_apply_reinforce`` 的检索命中 /
    主动复习通道也会把老评星行的 ``last_reinforce_at`` 刷成 now（且不看 ``ai_rated``），于是「今天被
    强化过的老评星行」都会被算进来。因此它**只能用于报告差异**，绝不能拿它去抬账本（会把当天额度
    一次抬满、评星全停摆）。详见 ``rating_quota.diff_observed`` 的 docstring。
    """
    from sqlalchemy import func

    from app.utils.timeutil import beijing_day_start_utc
    try:
        result = await db.execute(
            select(Memory.character_id, func.count(Memory.id))
            .where(
                Memory.ai_rated == True,
                Memory.last_reinforce_at >= beijing_day_start_utc(),
            )
            .group_by(Memory.character_id)
        )
        return {int(row[0]): int(row[1]) for row in result.all()}
    except Exception as e:
        _logger.warning("AI rating reconcile observation failed: %s", e)
        return {}


async def _pick_candidates(db, character_id: int, limit: int) -> list:
    from app.memory.service import _active_status_clause  # #70-C：失效记忆不再评星（flag 关=永真）
    result = await db.execute(
        select(Memory)
        .where(
            Memory.character_id == character_id,
            Memory.is_archived == False,
            Memory.is_pinned == False,
            Memory.is_locked == False,
            Memory.ai_rated == False,
            Memory.memory_type != "working_state",  # M3-a：结构化状态不送 LLM 评星、不占每日名额
            _active_status_clause(),
        )
        .order_by(Memory.id.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def _rate_batch(character, items: list, *, obs: dict | None = None) -> list[dict]:
    """一次 LLM 调用批量评星：返回 [{"id", "star"}]；失败/解析失败返回 []。

    挂点 A（决策层阶段 0，2026-09-25）：每条星分经 ``ask_score(lo=1, hi=5)`` 走一遍统一口径，
    ``legacy`` 就是原来的「夹到 1..5」算法 ⇒ 返回值与本层接线前逐字相同（透传、不裁剪、不转型）。
    影子留痕只在 decision_layer_shadow 开时发生（一条星分＝一行 agent_task_logs），关时零行为。

    ``obs``（评星留痕派单 Part A，2026-09-26）：调用方给一个 dict 就被动填写本次输入/输出留痕，
    仅用于旁证，**不参与返回值计算**；不传（旧调用方）则一行都不多写。
    """
    from app.agent.llm_client import chat_completion
    char_name = character.name if character else "我"
    mem_list = "\n".join(
        f"{i + 1}. id={m.id} 类型={m.memory_type} 内容：{(m.content or '')[:80]}"
        for i, m in enumerate(items)
    )
    hint = (
        f"你是{char_name}。下面是你关于用户的{len(items)}条记忆。"
        "请评估每条记忆对你们关系的重要性，输出严格 JSON 数组，"
        '格式：[{"id": 记忆id, "star": 1到5}]，star 越大越重要。只输出 JSON，不要其他文字。\n'
        f"记忆列表：\n{mem_list}"
    )
    if obs is not None:
        obs["candidate_count"] = len(items)
        obs["memory_ids"] = [m.id for m in items]
        obs["prompt_len"] = len(hint)
    text = await chat_completion(
        messages=[
            {"role": "system", "content": "只输出 JSON 数组。"},
            {"role": "user", "content": hint},
        ],
        temperature=0.2,
        max_tokens=512,
        task="memory", user_id=(character.user_id if character else 1),
    )
    text = (text or "").strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        _note_parse_failure(obs, "no_json_array", text)
        return []
    try:
        data = json.loads(text[start:end + 1])
    except Exception as e:
        _logger.warning("AI rating parse failed: %s", e)
        _note_parse_failure(obs, "json_parse_error", text)
        return []
    if not isinstance(data, list):
        _note_parse_failure(obs, "not_a_list", text)
        return []
    contents = {m.id: (m.content or "") for m in items}
    result = []
    model_stars = {}
    for row in data:
        if isinstance(row, dict) and "id" in row and isinstance(row.get("star"), int):
            star, _conf = ask_score(
                contents.get(row["id"], ""), "这条记忆对我们关系有多重要（1-5 星）", 1, 5,
                # 默认参数按行求值＝把原来的「夹到 1..5」整段搬进 legacy，ask_score 交回的就是它本身
                legacy=lambda v=max(1, min(5, row["star"])): v,
                hook="memory_star_rating",
                character_id=character.id if character else None,
                context={"memory_id": row["id"], "raw_star": row["star"]})
            result.append({"id": row["id"], "star": star})
            model_stars[str(row["id"])] = row["star"]
    if obs is not None:
        # 输出留痕：星分（1-5 原始值，非 ×20 的重要性）＋模型原样给的星分（可能被夹到 1..5）
        obs["stars"] = {str(r["id"]): r["star"] for r in result}
        obs["model_stars"] = model_stars
        obs["rows_returned"] = len(data)
    return result


class RatingPartialFailure(RuntimeError):
    """本次评星中至少一个角色「有候选但整批失败」（LLM 非 JSON / 解析失败 / 调用异常）；由 maintenance 捕获后按退避重试。"""


async def run_ai_rating() -> int:
    """扫描各角色未评记忆 → 批量 LLM 评星（每日限额）→ 更新。返回评星条数。

    评星留痕（Part A，2026-09-26）：本函数一次执行＝一个批次（``batch_id``），
    批次号写进每行 agent_task_logs.task_id；开跑先落一条 start（入口计数：这一拍到底被调用没有），
    每个角色一条 char（结论/跳过原因/星分）＋若干条 input（候选 id + 正文前 80 字），收尾一条 end。
    留痕只观测：返回值、写库内容、额度账本读写时机与数值均与本函数接线前逐字一致。

    失败冒泡（批 A，2026-09-26）：收尾留痕之后，只要有角色「有候选但整批失败」就抛
    ``RatingPartialFailure``——原先静默返回会让上层 maintenance 的失败回拨重试永不触发。
    """
    now = _now_naive()
    batch_id = _new_batch_id(now)
    outcomes: dict[str, int] = {}
    _trace_event(batch_id, None, TRACE_ROUTE_RUN, {"phase": "start", "ts": now.isoformat()})
    async with async_session_factory() as db:
        try:  # 批 C：**只报告**账本与库内观测值的差异，绝不写账本（观测口径含强化污染，见 diff_observed）
            diff = diff_observed(await _observed_today_counts(db))
            if diff:
                _logger.info("Rating quota diff (observed>ledger, 参考值): %d char(s) %s", len(diff),
                             "; ".join(f"char={c} {old} -> {new}"
                                       for c, (old, new) in diff.items()))
        except Exception as e:
            _logger.warning("AI rating quota diff skipped: %s", e)
        chars = (await db.execute(
            select(AICharacter).where(AICharacter.is_active == True)
        )).scalars().all()
    rated_total = 0
    failed: list[int] = []
    for char in chars:
        stage = "quota"
        outcome = "not_started"
        written = 0
        obs: dict = {}
        try:
            async with async_session_factory() as db:
                used = await _daily_rated(db, char.id)
                obs["quota_used_before"] = used
                obs["quota_limit"] = AI_RATING_MAX_PER_CHAR
                if used >= AI_RATING_MAX_PER_CHAR:
                    outcome = "quota_full"
                    continue
                quota = AI_RATING_MAX_PER_CHAR - used
                stage = "pick"
                items = await _pick_candidates(db, char.id, min(AI_RATING_BATCH, quota))
                if not items:
                    outcome = "no_candidates"
                    continue
                obs["quota_left"] = quota
                _trace_inputs(batch_id, char.id, items)   # 输入摘要（趁会话未关取正文）
                stage = "rate_batch"
                results = await _rate_batch(char, items, obs=obs)
                if not results:
                    outcome = obs.get("fail_reason", "parse_failed")
                    failed.append(char.id)
                    continue
                by_id = {r["id"]: r["star"] for r in results}
                stage = "apply"
                n = 0
                for m in items:
                    star = by_id.get(m.id)
                    if star is None:
                        continue
                    m.importance = min(DECAY_MAX_PCT, float(star * 20))
                    s = float(m.strength_days or S_DEFAULT)
                    m.strength_days = min(S_MAX_DAYS, max(S_MIN_DAYS, max(s, star / 5.0 * S_MAX_DAYS)))
                    m.review_count = (m.review_count or 0) + 1
                    m.last_reinforce_at = now
                    m.delete_at = None
                    m.next_review_at = now + timedelta(days=float(m.strength_days))
                    m.ai_rated = True
                    m.updated_at = now
                    n += 1
                await db.commit()
                written = n
                if n:
                    _quota_add(char.id, n)
                    _logger.info("AI rating char=%d rated=%d", char.id, n)
                    rated_total += n
                outcome = "rated" if n else "no_star_matched"
        except Exception as e:
            _logger.warning("AI rating char=%d failed: %s", char.id, e)
            obs["error"] = str(e)[:160]
            outcome = "call_failed" if stage == "rate_batch" else "error"
            failed.append(char.id)
        finally:
            # 留痕放在 finally：无论走 continue / 抛异常 / 正常评完，这一行都必落
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            _trace_event(batch_id, char.id, TRACE_ROUTE_CHAR,
                         {"outcome": outcome, "written": written, "stage": stage, **obs},
                         status=("error" if outcome in ("call_failed", "error") else "ok"))
    _trace_event(batch_id, None, TRACE_ROUTE_RUN, {
        "phase": "end", "char_count": len(chars),
        "rated_total": rated_total, "outcomes": outcomes,
    })
    if failed:
        raise RatingPartialFailure("rating failed for chars=" + repr(sorted(set(failed))))
    if rated_total:
        _logger.info("AI rating total=%d", rated_total)
    return rated_total
