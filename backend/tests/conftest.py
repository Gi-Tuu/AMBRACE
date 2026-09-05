# -*- coding: utf-8 -*-
"""pytest 全局夹具（backend/tests）。

- 会话级：给全局库建表（init_db）——部分用例（arbiter 节日 / weave / 日复盘）直接经全局
  async_session_factory 查表，干净环境（CI）无生产库时会报 missing table；先建表与本机已初始化的
  生产库行为一致。
- 每个测试：BM25 索引持久化根重定向到临时目录（不写生产缓存）。
- 每个测试：把 4 个内置工具（search/image_gen/note_calendar/note_memo）的执行入口注册到
  ToolRegistry（AMBRACE 重构步骤 8）。原先这些工具在 app/agent.tools 模块 import 时登记；
  迁到 app/tools/builtin 后改由 register_builtin_tools() 显式注册（main.py 启动时调用），
  测试侧经本夹具幂等注册（不重复/覆盖无副作用）。
- 每个测试结束：清理全局 event_bus 订阅者——内置订阅者经 TestClient/lifespan 的
  register_builtin_handlers 注册后会残留到后续用例（如 tool.executed → 织库增量联动），
  造成跨用例污染。
"""
import asyncio
from pathlib import Path

import pytest

import app.memory.bm25_index as bm25
from app.events.bus import event_bus


def pytest_configure(config):
    """注册自定义标记，避免「需要真实微信扫码联调」的 live 用例产生未注册标记告警。"""
    config.addinivalue_line("markers", "live: 需要真实微信扫码/真机联调，默认不运行（PR3 阻塞项）")


@pytest.fixture(scope="session", autouse=True)
def _init_test_schema():
    """会话开始时给全局库建表（幂等，兼容干净环境/CI 缺失生产库）。"""
    from app.db.database import init_db
    from app.db.migrate import ensure_alembic_revision

    asyncio.run(init_db())
    asyncio.run(ensure_alembic_revision())
    yield


@pytest.fixture(autouse=True)
def _isolate_bm25_persist(tmp_path):
    """每个测试把 BM25 持久化根重定向到临时目录，测试间互不污染、不写生产缓存。"""
    bm25._persist_root = Path(tmp_path)
    yield
    bm25._persist_root = None


@pytest.fixture(autouse=True)
def _register_builtin_tools():
    """每个测试把 4 个内置工具注册到 ToolRegistry（幂等可重入）。

    AMBRACE 步骤 8：内置工具执行入口迁到 app/tools/builtin，由 register_builtin_tools() 显式注册。
    """
    from app.tools import register_builtin_tools

    register_builtin_tools()
    yield


@pytest.fixture(autouse=True)
def _reset_event_bus_after_test():
    """用例结束后清空全局事件总线订阅者，防止内置订阅者残留污染后续用例。"""
    yield
    event_bus._subscribers = {}
