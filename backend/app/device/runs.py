# -*- coding: utf-8 -*-
"""宿主侧 run registry（YAI 接入 M2）。

语义（与 YAI Core v1.0 冻结）：
- ``run_id`` 由宿主（AMBRACE）预生成 UUIDv4，随请求传入 Core 并原样回带；
- 取消由宿主执行：``cancel`` 后 registry 记 ``cancelled``，**不伪造 DONE**；
- 30s deadline 由宿主 ``asyncio`` 计时负责（Core 的分项超时不算整 run deadline）；
- 事件统一小写：``tool_call`` / ``tool_result`` / ``model_message`` / ``done``。
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field

DEFAULT_DEADLINE_S = 30.0
MAX_RUNS = 200
MAX_EVENTS = 2000  # 每 run 有界缓冲（SSE 契约）
CANCEL_GRACE_S = 2.0  # 取消后等待 in-flight tool_result 的 grace


@dataclass
class RunState:
    run_id: str
    user_id: int
    session_id: str
    task: str
    tools: list[str]
    approval_mode: str
    deadline_s: float
    created_at: float = field(default_factory=time.monotonic)
    status: str = "running"  # running | cancelled | expired | done
    events: list[dict] = field(default_factory=list)
    source: str = "host"
    closed: bool = False
    next_offset: int = 0
    timer: asyncio.Task | None = None
    grace_timer: object | None = None


_RUNS: dict[str, RunState] = {}


def _prune() -> None:
    if len(_RUNS) <= MAX_RUNS:
        return
    for rid in sorted(_RUNS, key=lambda r: _RUNS[r].created_at)[: len(_RUNS) - MAX_RUNS]:
        st = _RUNS.pop(rid, None)
        if st and st.timer:
            st.timer.cancel()


def create_run(*, user_id: int, session_id: str = "", task: str = "",
               tools: list[str] | None = None, approval_mode: str = "ask",
               deadline_s: float = DEFAULT_DEADLINE_S,
               run_id: str | None = None) -> RunState:
    # run_id：宿主可预生成并透传；缺省由 registry 生成（与 Core 约定「原样回带」）
    run_id = (run_id or "").strip() or f"run-{uuid.uuid4()}"
    st = RunState(run_id=run_id, user_id=int(user_id), session_id=session_id, task=task,
                  tools=list(tools or []), approval_mode=approval_mode,
                  deadline_s=max(0.05, float(deadline_s)))
    _RUNS[run_id] = st
    _prune()

    async def _expire() -> None:
        try:
            await asyncio.sleep(st.deadline_s)
        except asyncio.CancelledError:
            raise
        if st.status in ("running", "starting"):
            st.status = "expired"
            # registry 级终态：不伪造 Core 的 done
            append_event(run_id, "run_terminal",
                         {"status": "expired", "code": "deadline_exceeded",
                          "synthetic": True, "source": "registry"})

    try:
        st.timer = asyncio.ensure_future(_expire())
    except RuntimeError:
        st.timer = None  # 无事件循环（同步调用/测试）时不排 deadline

    return st


def append_event(run_id: str, event_type: str, data: dict, force: bool = False) -> bool:
    st = _RUNS.get(run_id)
    if st is None:
        return False
    if event_type not in ("tool_call", "tool_result", "model_message", "done", "error", "run_terminal"):
        raise ValueError(f"unknown event type: {event_type}")
    # 终态屏障：cancel/deadline 后拒绝新业务事件；grace 内 tool_result 允许并标 late
    if st.closed and not force and event_type != "run_terminal":
        return False
    if st.status == "cancelling":
        if event_type == "tool_call":
            return False
        if event_type == "tool_result" and isinstance(data, dict):
            data = {**data, "late": True}
    st.next_offset += 1
    st.events.append({"offset": st.next_offset, "type": event_type,
                      "data": data, "ts": time.time()})
    if len(st.events) > MAX_EVENTS:
        del st.events[: len(st.events) - MAX_EVENTS]
    if event_type == "done" and st.status == "running":
        st.status = "done"
        if st.timer:
            st.timer.cancel()
    return True


def close_run(run_id: str, status: str, payload: dict | None = None, source: str = "registry") -> bool:
    """原子收口：只允许一次；已终态返回 False。所有终态路径（local/Core finalize/cancel/expire）都走这里。"""
    st = _RUNS.get(run_id)
    if st is None:
        return False
    if st.closed:
        return False
    st.closed = True
    if status == "done":
        data = payload or {"status": "ok"}
        st.status = "done"
        append_event(run_id, "done", data, force=True)
    elif status == "error":
        st.status = "error"
        append_event(run_id, "error", payload or {}, force=True)
        append_event(run_id, "run_terminal",
                     {"status": "error", "synthetic": True, "source": source}, force=True)
    elif status == "cancelled":
        st.status = "cancelled"
        append_event(run_id, "run_terminal",
                     {"status": "cancelled", "synthetic": True, "source": source}, force=True)
    elif status == "expired":
        st.status = "expired"
        append_event(run_id, "run_terminal",
                     {"status": "expired", "synthetic": True, "source": source}, force=True)
    else:
        st.closed = False
        return False
    if st.timer:
        st.timer.cancel()
    return True


def cancel_run(run_id: str, *, user_id: int | None = None) -> bool:
    st = _RUNS.get(run_id)
    if st is None or (user_id is not None and st.user_id != int(user_id)):
        return False
    if st.status in ("starting", "running"):
        st.status = "cancelling"
        if st.timer:
            st.timer.cancel()

        def _finish() -> None:
            if st.status != "cancelling":
                return
            close_run(run_id, "cancelled")

        timer = threading.Timer(CANCEL_GRACE_S, _finish)
        timer.daemon = True
        st.grace_timer = timer
        timer.start()
        return True
    return False





def get_run(run_id: str, *, user_id: int | None = None) -> dict | None:
    st = _RUNS.get(run_id)
    if st is None or (user_id is not None and st.user_id != int(user_id)):
        return None
    return {
        "run_id": st.run_id,
        "status": st.status,
        "source": st.source,
        "closed": st.closed,
        "task": st.task,
        "tools": st.tools,
        "approval_mode": st.approval_mode,
        "deadline_s": st.deadline_s,
        "last_offset": st.next_offset,
        "oldest_available": st.events[0]["offset"] if st.events else st.next_offset + 1,
        "events": st.events[-200:],
    }


def clear() -> None:
    for st in _RUNS.values():
        if st.timer:
            st.timer.cancel()
    _RUNS.clear()


def validate_event_order(events: list[dict]) -> list[str]:
    """校验事件顺序：tool_call 必须先于对应 tool_result；done/run_terminal 必须是最后一个事件。

    返回问题列表（空列表＝顺序合法）。run_terminal 视为 registry 级终态，不得与 done 混用。
    """
    problems: list[str] = []
    pending_calls: list[str] = []
    terminal_seen = False
    for idx, ev in enumerate(events):
        etype = ev.get("type")
        if terminal_seen:
            problems.append(f"event after terminal at {idx}: {etype}")
        if etype == "tool_call":
            pending_calls.append(str((ev.get("data") or {}).get("tool") or ""))
        elif etype == "tool_result":
            if not pending_calls:
                problems.append(f"tool_result without tool_call at {idx}")
            else:
                pending_calls.pop(0)
        elif etype in ("done", "run_terminal"):
            terminal_seen = True
    if pending_calls:
        problems.append(f"unmatched tool_call: {pending_calls}")
    return problems
