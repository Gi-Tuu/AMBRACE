# -*- coding: utf-8 -*-
"""pytest 全局夹具（backend/tests）。

2026-08-23 BM25 深化：角色索引持久化根默认指向 backend/data/bm25_cache。为避免测试误写生产
缓存、或跨测试复用同一 character_id 时通过磁盘残留读到上一库的旧索引，用 autouse 夹具把
bm25_index._persist_root 隔离到每个测试的临时目录：setup 指向 tmp_path、teardown 复原为 None
（回落到生产目录，仅生产进程使用）。各测试文件如自设 _persist_root 会更优先（测试体内覆盖）。
"""
from pathlib import Path

import pytest

import app.memory.bm25_index as bm25


@pytest.fixture(autouse=True)
def _isolate_bm25_persist(tmp_path):
    """每个测试把 BM25 持久化根重定向到临时目录，测试间互不污染、不写生产缓存。"""
    bm25._persist_root = Path(tmp_path)
    yield
    bm25._persist_root = None
