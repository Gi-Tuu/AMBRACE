# -*- coding: utf-8 -*-
"""决策层薄抽象（阶段 0，2026-09-25）：三原语透传 + 影子留痕，开关关＝零行为、零额外开销。

三原语命名对齐 docs/decision-layer-research.md §2（noul=布尔判定 / choice=封闭候选 / score=区间打分）。
**阶段 0 不换实现、不引新模型**：每个原语只调用一次 ``legacy()``（＝该决策点原来怎么算就怎么算），
并把它的返回值**原样**交回（不裁剪、不转型、不复制），``confidence`` 恒为 ``None``＝未校准
（宁可没有，也不在此刻编造概率值）。

开关：``AGENT_FLAGS["decision_layer_shadow"]``（默认关）。
- 关：首行判定即返回 ⇒ 不建记录对象、不起计时器、不碰任何 IO，与「根本没接这层」逐字一致；
- 开：写一条 ``agent_task_logs``（``route='decision_layer_shadow'``），字段约定沿用
  ``app/memory/observability.py`` 的 obs_event 与 ``app/agent/trace.py`` 的既有写入通道。

两种留痕形态（由 ``sink`` 选）：
- ``direct``（挂点 A·记忆评星，异步路径）：一条决策＝一行，经 ``enqueue_task_log`` fire-and-forget
  （⇒ 同批多行的**落库先后不保证**，事后核对请按 ``context`` 里的 id 关联，别按 id 顺序读）；
- ``buffer``（挂点 B·记忆时态，热路径同步调用）：先进进程内缓冲，攒满 ``_BUFFER_FLUSH_COUNT`` 条、
  或最旧一条已等待超过 ``_BUFFER_FLUSH_AGE_S`` 秒，才交**一个**后台任务批量写（仍是一决策一行、
  同一路由）⇒ 调用点永不出现阻塞式写库。无运行中事件循环时记录退回缓冲，等有循环的调用点再攒批；
  缓冲有硬上限，溢出丢最旧并只记一次 WARNING。

fail-open：观测部分整体包在 try 里，序列化/写库/调度失败只记 WARNING，绝不影响决策返回值；
但 ``legacy()`` 抛出的业务异常**照常向外传播**（本层绝不吞）。
"""
import asyncio
import json
import time

from app.utils.logger import get_logger

_logger = get_logger("domain.decision")

FLAG_KEY = "decision_layer_shadow"
SHADOW_ROUTE = FLAG_KEY          # agent_task_logs.route（String(30)，本值 21 字符）
SHADOW_TRIGGER = "decision_layer"
SINK_DIRECT = "direct"
SINK_BUFFER = "buffer"

_STATE_MAX = 120        # 影子记录里 state 片段长度（只留一小段文本，够事后核对）
_STEPS_MAX = 1600       # steps_json 上限，与 obs_event 同口径
_MARKER_MAX = 6         # 单个 marker 列表留痕条数上限（防 steps_json 被截成坏 JSON）

_BUFFER_FLUSH_COUNT = 25
_BUFFER_FLUSH_AGE_S = 120.0
_BUFFER_HARD_CAP = 200

_shadow_buffer: list = []
_shadow_buffer_since: float | None = None
_shadow_dropped = 0
_warned: set = set()


def shadow_enabled() -> bool:
    """影子留痕总闸（缺省关；连导入都失败也按关处理——观测层不得把业务拖下水）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get(FLAG_KEY, False))
    except Exception:
        return False


def _clip(value, limit: int) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text[:limit]


def _jsonable(value):
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return _clip(value, 120)


def _in_range(value, lo, hi):
    try:
        return bool(lo <= value <= hi)
    except Exception:
        return None


def _warn_once(key: str, msg: str, *args) -> None:
    if key in _warned:
        return
    _warned.add(key)
    _logger.warning(msg, *args)


# ────────────────────────────── 三原语（阶段 0＝透传 legacy） ──────────────────────────────

def ask_noul(state, question, *, legacy, hook="", sink=SINK_DIRECT,
             character_id=None, user_id=None, context=None):
    """布尔判定原语 → ``(value, confidence)``。阶段 0：value 就是 ``legacy()`` 的返回值本身，confidence 恒 None。"""
    if not shadow_enabled():
        return legacy(), None
    return _ask_shadowed("noul", state, question, legacy, hook=hook, sink=sink,
                         character_id=character_id, user_id=user_id, context=context)


def ask_choice(state, question, options, *, legacy, hook="", sink=SINK_DIRECT,
               character_id=None, user_id=None, context=None):
    """封闭候选原语 → ``(value, confidence)``。阶段 0：value 就是 ``legacy()`` 的返回值本身（不校验、不改选）。"""
    if not shadow_enabled():
        return legacy(), None
    return _ask_shadowed("choice", state, question, legacy, hook=hook, sink=sink,
                         character_id=character_id, user_id=user_id, context=context,
                         options=options)


def ask_score(state, question, lo, hi, *, legacy, hook="", sink=SINK_DIRECT,
              character_id=None, user_id=None, context=None):
    """区间打分原语 → ``(value, confidence)``。阶段 0：value 就是 ``legacy()`` 的返回值本身（不裁剪、不取整）。"""
    if not shadow_enabled():
        return legacy(), None
    return _ask_shadowed("score", state, question, legacy, hook=hook, sink=sink,
                         character_id=character_id, user_id=user_id, context=context,
                         bounds=(lo, hi))


def _ask_shadowed(primitive, state, question, legacy, *, hook, sink,
                  character_id, user_id, context=None, options=None, bounds=None):
    """影子模式下的一次决策：计时 → 透传 legacy → 尽力留痕 → 原样交回。

    ``legacy()`` 刻意放在 try 之外：业务异常照常抛出，本层不吞、不降级。
    """
    t0 = time.perf_counter()
    value = legacy()
    latency_ms = int((time.perf_counter() - t0) * 1000)
    try:
        record = {
            "primitive": primitive,
            "hook": hook or "unknown",
            "state": _clip(state, _STATE_MAX),
            "question": _clip(question, 160),
            "output": _jsonable(value),
            "confidence": None,          # 未校准；禁止在此刻编造成 0/1
            "latency_ms": latency_ms,
            "source": "legacy",
        }
        if options is not None:
            opts = list(options)            # 先物化：生成器入参被 list() 消费后 in 判定会失真
            record["options"] = [_clip(o, 40) for o in opts[:12]]
            record["output_in_options"] = value in opts
        if bounds is not None:
            lo, hi = bounds
            record["lo"], record["hi"] = _jsonable(lo), _jsonable(hi)
            record["output_in_range"] = _in_range(value, lo, hi)
        if context:
            record["context"] = context
        if sink == SINK_BUFFER:
            _buffer_append(record, character_id=character_id, user_id=user_id, latency_ms=latency_ms)
        else:
            _write_direct(record, character_id=character_id, user_id=user_id, latency_ms=latency_ms)
    except Exception as e:      # fail-open：留痕再怎么坏，返回值已经算出来了
        _logger.warning("decision shadow record failed(%s/%s): %s", primitive, hook, e)
    return value, None


# ────────────────────────────── 落库通道（沿用 agent_task_logs 既有约定） ──────────────────────────────

def _row_kwargs(record, *, character_id, user_id, latency_ms, task_id) -> dict:
    return {
        "task_id": task_id,
        "character_id": character_id,
        "user_id": user_id,
        "trigger": SHADOW_TRIGGER,
        "route": SHADOW_ROUTE,
        "steps_json": json.dumps(record, ensure_ascii=False, default=str)[:_STEPS_MAX],
        "latency_ms": latency_ms,
        "status": "ok",
    }


def _write_direct(record, *, character_id, user_id, latency_ms) -> None:
    """一条决策＝一行，fire-and-forget（调用点不 await；失败由 trace 通道自行静默）。"""
    from app.agent.trace import enqueue_task_log, new_task_id
    enqueue_task_log(**_row_kwargs(record, character_id=character_id, user_id=user_id,
                                   latency_ms=latency_ms, task_id=new_task_id()))


def _buffer_append(record, *, character_id, user_id, latency_ms) -> None:
    """热路径同步入口：只往进程内缓冲追加，绝不在这里碰数据库。"""
    global _shadow_buffer_since, _shadow_dropped
    now = time.monotonic()
    if _shadow_buffer_since is None:
        _shadow_buffer_since = now
    _shadow_buffer.append({"record": record, "character_id": character_id,
                           "user_id": user_id, "latency_ms": latency_ms})
    if (len(_shadow_buffer) >= _BUFFER_FLUSH_COUNT
            or (now - _shadow_buffer_since) >= _BUFFER_FLUSH_AGE_S):
        flush_shadow_buffer()
    elif len(_shadow_buffer) > _BUFFER_HARD_CAP:
        overflow = len(_shadow_buffer) - _BUFFER_HARD_CAP
        del _shadow_buffer[:overflow]
        _shadow_dropped += overflow
        _warn_once("dropped", "decision shadow buffer overflow, dropped oldest %d record(s)", overflow)


def flush_shadow_buffer() -> int:
    """把缓冲整批交给**一个**后台任务落库（调用点不写库）。返回已交出的条数；交不出则整批留在缓冲。"""
    global _shadow_buffer_since
    if not _shadow_buffer:
        return 0
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 没有事件循环＝这一处调用点落不了库：整批留在缓冲，且重置计时避免同一窗口反复空转
        _shadow_buffer_since = time.monotonic()
        _warn_once("noloop", "decision shadow: no running loop, %d record(s) stay buffered",
                   len(_shadow_buffer))
        return 0
    batch = _shadow_buffer[:]
    del _shadow_buffer[:]
    _shadow_buffer_since = None
    coro = _write_batch(batch)
    try:
        from app.utils.async_tasks import spawn_background
        spawn_background(coro, name="decision_shadow_batch")
        return len(batch)
    except Exception as e:
        try:
            coro.close()          # 交不出去就关掉协程，免得「never awaited」告警
        except Exception:
            pass
        _requeue(batch)
        _warn_once("defer", "decision shadow flush deferred: %s", e)
        return 0


def _requeue(batch) -> None:
    """落库时机未到（无事件循环）时把记录放回缓冲头部，等有循环的调用点再攒批。"""
    global _shadow_buffer_since, _shadow_dropped
    head = batch[-_BUFFER_HARD_CAP:] if len(batch) > _BUFFER_HARD_CAP else batch
    if len(batch) > _BUFFER_HARD_CAP:
        _shadow_dropped += len(batch) - len(head)
    _shadow_buffer[:0] = head
    if _shadow_buffer_since is None:
        _shadow_buffer_since = time.monotonic()


async def _write_batch(batch) -> None:
    """一个会话、一次 commit 写完整批（一个决策一行，路由与 direct 形态一致）。失败只记 WARNING。"""
    try:
        from app.agent.trace import new_task_id, resolve_owner_user_id
        from app.db.database import async_session_factory
        from app.models.agent import AgentTaskLog
        async with async_session_factory() as db:
            for item in batch:
                uid = item.get("user_id")
                cid = item.get("character_id")
                if not uid and cid:
                    uid = await resolve_owner_user_id(cid, db=db)
                db.add(AgentTaskLog(**_row_kwargs(
                    item["record"], character_id=cid, user_id=uid,
                    latency_ms=item.get("latency_ms", 0), task_id=new_task_id())))
            await db.commit()
    except Exception as e:
        _logger.warning("decision shadow batch write failed(%d): %s", len(batch), e)


# ────────────────────────────── 挂点观测适配器 ──────────────────────────────

TENSE_OPTIONS = ("enduring", "episodic", "plan", "transient")


def observe_tense_decision(m, label, *, tense_hint=None, character_id=None, user_id=None) -> None:
    """挂点 B（记忆时态）的影子留痕适配器：在**调用方**记录规则判定的输入 marker 与给出的 label。

    刻意不放进 ``app/memory/tense.py``（同步纯函数、热路径同步调用，禁止在其内部 await/写库）；
    判定结果由调用方算好后原样透传（``legacy`` 只是把它交回来），本函数不参与判定、无返回值。
    留痕走 ``SINK_BUFFER``：调用点只追加内存缓冲，批量落库由 ``flush_shadow_buffer`` 交后台任务完成。
    """
    if not shadow_enabled():
        return
    try:
        context = {"input": _tense_input(m), "via": "tense_hint" if tense_hint else "rule"}
        if tense_hint:
            context["tense_hint"] = _clip(tense_hint, 20)
    except Exception as e:      # 输入留痕取不到也不影响记账本身
        _logger.warning("decision shadow tense input failed: %s", e)
        context = {"via": "tense_hint" if tense_hint else "rule"}
    ask_choice(_tense_state(m), "memory_tense", TENSE_OPTIONS, legacy=lambda: label,
               hook="memory_tense", sink=SINK_BUFFER, character_id=character_id,
               user_id=user_id, context=context)


def _tense_state(m) -> str:
    """state 片段：优先取 tense 模块同一条文本口径（title+content+why_it_matters）。"""
    try:
        from app.memory import tense as _tense
        return _clip(_tense._text(m).strip(), _STATE_MAX)
    except Exception:
        return ""


def _tense_input(m) -> dict:
    """规则判定的输入留痕（只读复用 tense 模块既有常量与取值器，不复制第二套判定）。"""
    from app.memory import tense as _tense
    text = _tense._text(m)
    return {
        "memory_type": _clip(_tense._g(m, "memory_type", "") or _tense._g(m, "type", ""), 30),
        "sub_type": _clip(_tense._g(m, "sub_type", ""), 30),
        "is_core": bool(_tense._g(m, "is_core", False)),
        "plan_markers": [_clip(k, 12) for k in _tense.PLAN_MARKERS if k in text][:_MARKER_MAX],
        "done_markers": [_clip(k, 12) for k in _tense._DONE_MARKERS if k in text][:_MARKER_MAX],
        "guard_hit": bool(_tense._has_guard(text)),
        "happened_source": bool(_tense.is_happened_source(m)),
    }


def shadow_buffer_size() -> int:
    """当前缓冲条数（测试/排查用）。"""
    return len(_shadow_buffer)


def shadow_dropped_total() -> int:
    """因缓冲溢出丢弃的累计条数（测试/排查用）。"""
    return _shadow_dropped


def reset_shadow_state() -> None:
    """清空进程内缓冲与计数（仅供测试与排查使用，不参与任何决策）。"""
    global _shadow_buffer_since, _shadow_dropped
    del _shadow_buffer[:]
    _shadow_buffer_since = None
    _shadow_dropped = 0
    _warned.clear()


__all__ = [
    "FLAG_KEY", "SHADOW_ROUTE", "SHADOW_TRIGGER", "SINK_DIRECT", "SINK_BUFFER",
    "ask_noul", "ask_choice", "ask_score",
    "observe_tense_decision", "flush_shadow_buffer", "shadow_enabled",
    "shadow_buffer_size", "shadow_dropped_total", "reset_shadow_state",
]
