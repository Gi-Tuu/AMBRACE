# -*- coding: utf-8 -*-
"""Task Trace 冒烟（Phase A，2026-08-16）：写失败静默、fire-and-forget 不阻塞"""
import asyncio

from app.agent import trace


def test_new_task_id_格式():
    tid = trace.new_task_id()
    assert len(tid) == 12
    assert tid.isalnum()


def test_enqueue_without_loop_不抛():
    # 无运行中事件循环时 ensure_future 抛 RuntimeError → 内部捕获静默
    trace.enqueue_task_log(trigger="chat", tool_calls=1)


def test_write_task_log_失败静默():
    # 非法字段 → 落库失败被捕获，不抛给调用方
    asyncio.run(trace.write_task_log(unknown_column_xyz=1))
