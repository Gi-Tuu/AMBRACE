"""记忆可观测埋点（M1-S11，2026-08-31）：五个指标的轻量事件写入 agent_task_logs。

指标 → 落点 → route：
- quota_clipped_sections ← context_builder._apply_system_total_quota（系统硬顶裁剪发生时）
- recall_pool_vs_return ← memory/service.search_memories 既有 memory_search trace
  （#70-B 已带 candidate_count/returned，无需新增埋点，读端直接聚合）
- decay_deleted_count ← memory/decay.py（countdown_set / countdown_cleared / deleted 三类，kind 区分）
- marker_truncated ← agent/response_parser.parse_response（回复尾部未闭合标记疑似截断）
- dual_write_dup_merge ← memory/service.save_memory（vector_dedup / text_dedup / merge 三类，kind 区分）

读端聚合示例：
  SELECT route, kind, COUNT(*) FROM agent_task_logs
  WHERE trigger='memory_obs'
  GROUP BY route, JSON_EXTRACT(steps_json,'$.kind');   -- SQLite 用 json_extract

约束：只写不读、fire-and-forget、失败静默；统一受 AGENT_FLAGS["memory_trace_debug"]（默认开）
门控，与 #70-B 检索轨迹同一开关；不新增表/依赖。
"""
import json
import re

from app.utils.logger import get_logger

_logger = get_logger("memory.obs")


def _flag_on() -> bool:
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("memory_trace_debug", True))
    except Exception:
        return False


def obs_event(character_id: int | None, metric: str, detail: dict, kind: str | None = None) -> None:
    """写一条 memory_obs 事件（route=指标名便于聚合，steps_json=明细 ≤1600 字符；失败静默）"""
    if not _flag_on():
        return
    try:
        if kind:
            detail = {**detail, "kind": kind}
        from app.agent.trace import enqueue_task_log, new_task_id
        enqueue_task_log(
            task_id=new_task_id(),
            character_id=character_id,
            trigger="memory_obs",
            route=(metric or "unknown")[:30],
            steps_json=json.dumps(detail, ensure_ascii=False, default=str)[:1600],
            status="ok",
        )
    except Exception as e:
        _logger.warning("memory obs event failed: %s", e)


async def obs_event_now(character_id: int | None, metric: str, detail: dict,
                      kind: str | None = None) -> None:
    """与 :func:`obs_event` 同口径，但**在本轮 await 内写完**（不 spawn 后台任务）。

    为什么需要它（2026-10-01 批 8 块 B 暴露）：请求路径上的 fire-and-forget 写库，在「一次性 loop」
    环境里（裸 `TestClient`、一次性 `asyncio.run`）会在 loop 关闭之后才由 aiosqlite worker 线程
    回灌结果，报 `RuntimeError: Event loop is closed`（pytest 记 `PytestUnhandledThreadExceptionWarning`）。
    凡「留痕必须落上、且调用点处在可能被立即关闭的 loop 里」的场合用本函数；
    长生命周期 loop（调度器 / 常驻服务）里不介意后台完成的，继续用 :func:`obs_event`。
    失败静默（只记 WARNING），绝不改变调用方的返回语义。
    """
    if not _flag_on():
        return
    try:
        if kind:
            detail = {**detail, "kind": kind}
        from app.agent.trace import new_task_id, write_task_log

        await write_task_log(
            task_id=new_task_id(),
            character_id=character_id,
            trigger="memory_obs",
            route=(metric or "unknown")[:30],
            steps_json=json.dumps(detail, ensure_ascii=False, default=str)[:1600],
            status="ok",
        )
    except Exception as e:
        _logger.warning("memory obs event (awaited) failed: %s", e)


def note_marker_truncation(response: str, character_id: int | None) -> bool:
    """S11 marker_truncated：回复尾部存在未闭合的【/[ 标记 → 疑似被 max_tokens 截断，登记丢失的标记片段。

    返回是否检测到截断（M2-S5：parse_response 据此置 state["marker_truncated"]，触发通道 B 优先补提）。
    """
    try:
        m = re.search(r"[\[【]([^\]】]{1,20})$", response or "")
        if m:
            obs_event(character_id, "marker_truncated", {"tail": m.group(1)[:40]})
            return True
    except Exception as e:
        _logger.warning("marker truncation check failed: %s", e)
    return False
