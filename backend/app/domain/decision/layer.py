# -*- coding: utf-8 -*-
"""决策层薄抽象（阶段 0，2026-09-25）：三原语透传 + 影子留痕，开关关＝零行为、零额外开销。

三原语命名对齐 docs/decision-layer-research.md §2（noul=布尔判定 / choice=封闭候选 / score=区间打分）。
**阶段 0 不换实现、不引新模型**：每个原语只调用一次 ``legacy()``（＝该决策点原来怎么算就怎么算），
并把它的返回值**原样**交回（不裁剪、不转型、不复制），``confidence`` 恒为 ``None``＝未校准
（宁可没有，也不在此刻编造概率值）。

开关：``AGENT_FLAGS["decision_layer_shadow"]``（默认关），经 DecisionShadowPorts 取表
（app/domain/decision/ports.py，生产实现 app/application/decision_layer_ports.py）。
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

from app.domain.decision.ports import DecisionShadowPorts, ShadowPortsNotInjected
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

# ── IO 端口（断点 #1 · V2b）：开关表 / 时钟 / trace 通道 / 后台调度 / 批量落库 ──────
_DEFAULT_PORTS: DecisionShadowPorts | None = None


def set_default_shadow_ports(ports: DecisionShadowPorts | None) -> None:
    """注册默认端口实现（传 None 清空＝恢复「未注入」状态，供接线与排查使用）。"""
    global _DEFAULT_PORTS
    _DEFAULT_PORTS = ports


def _bind_production_ports() -> DecisionShadowPorts:
    """迁移期兼容钩子：无人注入时惰性绑定 application 侧生产实现。

    存在的理由：本层的两个生产调用点（app/memory/ai_rating.py 挂点 A、app/memory/format.py
    挂点 B）与既有测试（tests/test_decision_layer.py）都不在白名单内、拿不到 ports；
    留着这条兜底，未注入调用点行为与改动前逐字一致（读同一张 AGENT_FLAGS、同一个时钟源）。
    """
    from app.application.decision_layer_ports import production_decision_layer_ports
    return production_decision_layer_ports


def _resolve_ports(ports: DecisionShadowPorts | None = None) -> DecisionShadowPorts:
    """取端口实现：显式注入优先 → 默认端口 → 惰性绑定生产实现 → 清晰报错（不静默降级）。"""
    global _DEFAULT_PORTS
    if ports is not None:
        return ports
    if _DEFAULT_PORTS is not None:
        return _DEFAULT_PORTS
    try:
        bound = _bind_production_ports()
    except ShadowPortsNotInjected:
        raise
    except Exception as e:
        raise ShadowPortsNotInjected(
            "decision shadow IO 端口未注入且生产实现绑定失败：请显式传入 DecisionShadowPorts"
            "（生产实现 app.application.decision_layer_ports.production_decision_layer_ports）"
            "——架构地图断点 #1"
        ) from e
    _DEFAULT_PORTS = bound
    return bound


def shadow_enabled(ports: DecisionShadowPorts | None = None) -> bool:
    """影子留痕总闸（缺省关；端口取表失败也按关处理——观测层不得把业务拖下水）。

    唯一会向上抛的情况是「一个端口都拿不到」（ShadowPortsNotInjected）：那属于接线事故，
    必须当场暴露，不允许降级成「开关看起来永远是关」。
    """
    try:
        flags = _resolve_ports(ports).flags()
        return bool(flags.get(FLAG_KEY, False))
    except ShadowPortsNotInjected:
        raise
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
             character_id=None, user_id=None, context=None, ports=None):
    """布尔判定原语 → ``(value, confidence)``。阶段 0：value 就是 ``legacy()`` 的返回值本身，confidence 恒 None。"""
    if not shadow_enabled(ports):
        return legacy(), None
    return _ask_shadowed("noul", state, question, legacy, hook=hook, sink=sink,
                         character_id=character_id, user_id=user_id, context=context,
                         ports=ports)


def ask_choice(state, question, options, *, legacy, hook="", sink=SINK_DIRECT,
               character_id=None, user_id=None, context=None, ports=None):
    """封闭候选原语 → ``(value, confidence)``。阶段 0：value 就是 ``legacy()`` 的返回值本身（不校验、不改选）。"""
    if not shadow_enabled(ports):
        return legacy(), None
    return _ask_shadowed("choice", state, question, legacy, hook=hook, sink=sink,
                         character_id=character_id, user_id=user_id, context=context,
                         options=options, ports=ports)


def ask_score(state, question, lo, hi, *, legacy, hook="", sink=SINK_DIRECT,
              character_id=None, user_id=None, context=None, ports=None):
    """区间打分原语 → ``(value, confidence)``。阶段 0：value 就是 ``legacy()`` 的返回值本身（不裁剪、不取整）。"""
    if not shadow_enabled(ports):
        return legacy(), None
    return _ask_shadowed("score", state, question, legacy, hook=hook, sink=sink,
                         character_id=character_id, user_id=user_id, context=context,
                         bounds=(lo, hi), ports=ports)


def _ask_shadowed(primitive, state, question, legacy, *, hook, sink,
                  character_id, user_id, context=None, options=None, bounds=None,
                  ports=None):
    """影子模式下的一次决策：计时 → 透传 legacy → 尽力留痕 → 原样交回。

    ``legacy()`` 刻意放在 try 之外：业务异常照常抛出，本层不吞、不降级。
    """
    resolved = _resolve_ports(ports)
    t0 = resolved.perf_counter()
    value = legacy()
    latency_ms = int((resolved.perf_counter() - t0) * 1000)
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
            _buffer_append(record, character_id=character_id, user_id=user_id,
                           latency_ms=latency_ms, ports=resolved)
        else:
            _write_direct(record, character_id=character_id, user_id=user_id,
                          latency_ms=latency_ms, ports=resolved)
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


def _write_direct(record, *, character_id, user_id, latency_ms, ports=None) -> None:
    """一条决策＝一行，fire-and-forget（调用点不 await；失败由 trace 通道自行静默）。"""
    resolved = _resolve_ports(ports)
    resolved.enqueue_task_log(**_row_kwargs(record, character_id=character_id, user_id=user_id,
                                            latency_ms=latency_ms, task_id=resolved.new_task_id()))


def _buffer_append(record, *, character_id, user_id, latency_ms, ports=None) -> None:
    """热路径同步入口：只往进程内缓冲追加，绝不在这里碰数据库。"""
    global _shadow_buffer_since, _shadow_dropped
    resolved = _resolve_ports(ports)
    now = resolved.monotonic()
    if _shadow_buffer_since is None:
        _shadow_buffer_since = now
    _shadow_buffer.append({"record": record, "character_id": character_id,
                           "user_id": user_id, "latency_ms": latency_ms})
    if (len(_shadow_buffer) >= _BUFFER_FLUSH_COUNT
            or (now - _shadow_buffer_since) >= _BUFFER_FLUSH_AGE_S):
        flush_shadow_buffer(ports=resolved)
    elif len(_shadow_buffer) > _BUFFER_HARD_CAP:
        overflow = len(_shadow_buffer) - _BUFFER_HARD_CAP
        del _shadow_buffer[:overflow]
        _shadow_dropped += overflow
        _warn_once("dropped", "decision shadow buffer overflow, dropped oldest %d record(s)", overflow)


def flush_shadow_buffer(ports: DecisionShadowPorts | None = None) -> int:
    """把缓冲整批交给**一个**后台任务落库（调用点不写库）。返回已交出的条数；交不出则整批留在缓冲。"""
    global _shadow_buffer_since
    if not _shadow_buffer:
        return 0
    resolved = _resolve_ports(ports)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 没有事件循环＝这一处调用点落不了库：整批留在缓冲，且重置计时避免同一窗口反复空转
        _shadow_buffer_since = resolved.monotonic()
        _warn_once("noloop", "decision shadow: no running loop, %d record(s) stay buffered",
                   len(_shadow_buffer))
        return 0
    batch = _shadow_buffer[:]
    del _shadow_buffer[:]
    _shadow_buffer_since = None
    coro = _write_batch(batch, ports=resolved)
    try:
        resolved.spawn_background(coro, name="decision_shadow_batch")
        return len(batch)
    except Exception as e:
        try:
            coro.close()          # 交不出去就关掉协程，免得「never awaited」告警
        except Exception:
            pass
        _requeue(batch, ports=resolved)
        _warn_once("defer", "decision shadow flush deferred: %s", e)
        return 0


def _requeue(batch, ports=None) -> None:
    """落库时机未到（无事件循环）时把记录放回缓冲头部，等有循环的调用点再攒批。"""
    global _shadow_buffer_since, _shadow_dropped
    resolved = _resolve_ports(ports)
    head = batch[-_BUFFER_HARD_CAP:] if len(batch) > _BUFFER_HARD_CAP else batch
    if len(batch) > _BUFFER_HARD_CAP:
        _shadow_dropped += len(batch) - len(head)
    _shadow_buffer[:0] = head
    if _shadow_buffer_since is None:
        _shadow_buffer_since = resolved.monotonic()


async def _write_batch(batch, ports: DecisionShadowPorts | None = None) -> None:
    """一个会话、一次 commit 写完整批（一个决策一行，路由与 direct 形态一致）。失败只记 WARNING。"""
    try:
        resolved = _resolve_ports(ports)
        rows = []
        for item in batch:
            uid = item.get("user_id")
            cid = item.get("character_id")
            if not uid and cid:
                uid = await resolved.resolve_owner_user_id(cid)
            rows.append(_row_kwargs(
                item["record"], character_id=cid, user_id=uid,
                latency_ms=item.get("latency_ms", 0), task_id=resolved.new_task_id()))
        await resolved.write_shadow_rows(rows)
    except Exception as e:
        _logger.warning("decision shadow batch write failed(%d): %s", len(batch), e)


# ────────────────────────────── 挂点观测适配器 ──────────────────────────────

TENSE_OPTIONS = ("enduring", "episodic", "plan", "transient")


def observe_tense_decision(m, label, *, tense_hint=None, character_id=None, user_id=None,
                           ports=None) -> None:
    """挂点 B（记忆时态）的影子留痕适配器：在**调用方**记录规则判定的输入 marker 与给出的 label。

    刻意不放进 ``app/memory/tense.py``（同步纯函数、热路径同步调用，禁止在其内部 await/写库）；
    判定结果由调用方算好后原样透传（``legacy`` 只是把它交回来），本函数不参与判定、无返回值。
    留痕走 ``SINK_BUFFER``：调用点只追加内存缓冲，批量落库由 ``flush_shadow_buffer`` 交后台任务完成。
    """
    if not shadow_enabled(ports):
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
               user_id=user_id, context=context, ports=ports)


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
    "set_default_shadow_ports",
]


# ── 本文件已去 IO（架构地图断点 #1 · domain 去 IO 铺开 V2b，2026-09-29）──────────
# 1. 新增纯类型层 app/domain/decision/ports.py：DecisionShadowPorts 协议（typing.Protocol，
#    无框架，**8 个方法** = flags / perf_counter / monotonic / new_task_id /
#    enqueue_task_log / spawn_background / resolve_owner_user_id / write_shadow_rows）
#    + ShadowPortsNotInjected + ShadowRow；只依赖 typing。
# 2. 本模块去掉全部 IO import：原函数体内的 app.agent.loop.AGENT_FLAGS、
#    app.agent.trace.{enqueue_task_log, new_task_id, resolve_owner_user_id}、
#    app.utils.async_tasks.spawn_background、app.db.database.async_session_factory、
#    app.models.agent.AgentTaskLog 以及 import time（时钟改由端口提供）——
#    全部搬到 app/application/decision_layer_ports.py（原样搬迁、仍是函数级惰性 import，
#    故既有测试 monkeypatch app.agent.trace / app.db.database 照旧命中）。
#    保留的两处非 IO import：app.memory.tense（纯文本取器，不碰 DB）与 stdlib
#    asyncio/json（get_running_loop 判定与 steps_json 序列化）。
# 3. 零行为核对：三原语「关＝只调一次 legacy 并原样交回 (value, None)」的短路位置、
#    legacy() 在 try 之外（业务异常照常抛出）、record 字段集合与顺序（primitive/hook/state/
#    question/output/confidence=None/latency_ms/source='legacy'，choice 追加 options(前 12 条、
#    每条截 40)/output_in_options（先 list() 物化再判 in）、score 追加 lo/hi/output_in_range、
#    context 仅在真值时追加）、_STATE_MAX=120 / _STEPS_MAX=1600 / _MARKER_MAX=6、
#    _row_kwargs 八字段与 trigger/route/status='ok'、缓冲三常数 _BUFFER_FLUSH_COUNT=25 /
#    _BUFFER_FLUSH_AGE_S=120.0 / _BUFFER_HARD_CAP=200、溢出丢最旧 + 只 WARNING 一次的
#    _warn_once 口径、无事件循环时整批留缓冲并重置年龄计时、交不出去时 coro.close() 后
#    _requeue 回头部，全部逐字未动。
# 4. 接线：新参数 ports 全部是 keyword-only 且默认 None ⇒ 两个生产调用点
#    （memory/ai_rating.py 挂点 A、memory/format.py 挂点 B）与既有测试无需改动。
# 5. 遗留（下一批删）：`_DEFAULT_PORTS` + `_bind_production_ports` 这条迁移期兼容钩子
#    （唯一理由是两个生产调用点与 tests/test_decision_layer.py 都在本单白名单外）。
#    它一旦删除，本文件即达 domain 零 IO import 终态；ShadowPortsNotInjected 保留为
#    「显式传 ports=None 以外场景」的接线事故出口（不静默降级成「开关永远看起来是关」）。
#
# 同类待做 domain 文件清单（只列名字，本批未改）：
# - app/domain/emotion/care.py（四个迁移期兼容钩子 + _LegacyCarePorts 段待删）
