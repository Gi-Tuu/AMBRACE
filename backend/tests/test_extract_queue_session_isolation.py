# -*- coding: utf-8 -*-
"""守卫（A50 红块前置，2026-10-12）：待提取队列落盘路径必须被测试会话沙箱接管。

背景
----
`app/memory/extract_queue.py` 的 `_QUEUE_FILE` 是 **import 期算出的模块级常量**，默认指向
`backend/data/extract_queue.json`（生产目录）。`tests/conftest.py` 此前只接管了 DB / 向量库 /
主密钥三项，没接管队列文件 ⇒ 任何走真实提取路径（`add` / `peek` / `take` / `complete`）
又没自行 monkeypatch 的用例，都会把**合成配对**写进生产队列文件
（2026-10-12 14:08:45 定向跑实测该文件被改动）。

本守卫只做两件事
--------------
① **纯读**断言：当前队列路径落在本 pytest 进程**自己的**会话沙箱根下（xdist 下每 worker 各有根），
   且与沙箱 DB / 沙箱主密钥同属一个根；生产默认位置（由模块文件位置推出，不写死本机绝对路径）
   已不再是当前路径。生产队列文件本身**只比对路径，绝不读内容、不写、不删**。
② **写路径**上的真实流程：`add` / `take` / `complete` 之前先过归属断言
   （`_assert_session_owned` 查基线路径、`_claim_tmp_queue` 查临时路径），归属不过就拒绝，
   写操作根本不会发生；归属过了才跑真实 `add → peek → take → complete →
   pending_source_ids`，并证明别处用例的 monkeypatch 结束后**回到会话沙箱**而非生产默认路径。

口径纪律：不连生产库、不读/写/删生产队列、不删任何非本进程目录、队列样本全合成、
不写死作者机器绝对路径。本文件只断言，不留残留。
"""
from __future__ import annotations

import importlib
import sqlite3
import os
import time
from pathlib import Path

import pytest

from app.memory import extract_queue

# 合成样本：会话/来源 id 取测试专用高位段，正文是固定占位文本，与任何真实对话无关。
# ts 必须取当下时刻：extract_queue._load 会丢弃超 HARD_AGE_SEC 的滞留配对（生产语义）。
_SID = 4242001
_PAIRS = [
    {"user_message": "占位提问甲", "ai_response": "占位回答甲", "source_id": 900000001},
    {"user_message": "占位提问乙", "ai_response": "占位回答乙", "source_id": 900000002},
    {"user_message": "占位提问丙", "ai_response": "占位回答丙", "source_id": 900000003},
]


def _session_root() -> Path | None:
    """本进程会话沙箱根（xdist 下每 worker 各有一份自己的）。

    取自本进程加载的 `tests/conftest.py`；模块导入失败按"守卫必须成立"处理，不静默放行。
    """
    return importlib.import_module("conftest")._SESS_ROOT


def _norm(path) -> Path:
    """绝对化 + 归一化（Windows 下大小写与分隔符都统一，避免同根不同串的比对失真）。"""
    return Path(os.path.normpath(str(Path(path).absolute())))


def _prod_default_queue_file() -> Path:
    """生产默认队列位置：由模块文件位置推出（= `parents[2]/data/extract_queue.json`），不写死绝对路径。"""
    return Path(extract_queue.__file__).resolve().parents[2] / "data" / "extract_queue.json"


def _sandbox_root_from_db_url() -> Path:
    """从 `DATABASE_URL` 独立反推会话沙箱根（不靠 conftest 这一个真源）。"""
    url = os.environ["DATABASE_URL"]
    prefix = "sqlite+aiosqlite:///"
    assert url.startswith(prefix), f"测试库 URL 形态变了，反推口径要同步: {url}"
    db_file = Path(url[len(prefix):])
    # 布局固定为 <root>/data/sqlite/test.db ⇒ 根 = data/sqlite 的上上级
    return _norm(db_file).parents[2]


def _require_sandbox() -> Path:
    """会话沙箱根；逃生舱模式下显式跳过（不假绿）。"""
    root = _session_root()
    if root is None:
        pytest.skip("逃生舱 AMBRACE_TEST_KEEP_PROD=1：刻意对真实库调试，本守卫不适用")
    return _norm(root)


def _under_any_sandbox(path) -> bool:
    """是否落在**某个**会话沙箱（目录名以 ``ambrace_pytest_`` 开头）之下。

    xdist 下的继承口径（实测）：conftest 里 `AMBRACE_CREDENTIAL_KEY_FILE` 用的是
    `os.environ.setdefault`，xdist worker 继承到的是 **controller 那一份**，所以它的根
    不一定是本 worker 的 `_SESS_ROOT`；`DATABASE_URL` / `CHROMA_PERSIST_DIR` 是直接赋值，
    才是每 worker 一份（队列重定向同后者）。这是既有 A8 重定向的既有行为，本守卫只断言
    「没落到生产目录」，不去修它。
    """
    return any(part.startswith("ambrace_pytest_") for part in _norm(path).parts)


def _assert_session_owned(path) -> Path:
    """**归属守卫（严格档）**：只认「本进程会话沙箱」这一个归属。

    这是"测试写生产队列"的最后一道闸：路径不在本会话沙箱之下就直接 AssertionError，
    调用方随后任何 `add` / `take` 都不会发生。变异 1/2 打的就是这一关。
    """
    root = _require_sandbox()
    candidate = _norm(path)
    assert candidate == root / "data" / "extract_queue.json" or root in candidate.parents, (
        f"队列落盘路径不属本会话沙箱，拒绝写入: {candidate}（沙箱根={root}）"
    )
    return candidate


def _claim_tmp_queue(monkeypatch, tmp_path: Path) -> Path:
    """把队列指向 pytest 分配的 `tmp_path`（用例级临时目录），再改模块状态。

    归属口径：目标必须落在 pytest 给的 `tmp_path` 里——它是本用例独占的临时目录，
    既不是会话沙箱、更不可能是生产路径。改模块状态前先断言，不通过就抛。
    """
    target = _norm(tmp_path / "extract_queue.json")
    assert target.parent == _norm(tmp_path), f"临时队列路径不在 tmp_path 内: {target}"
    monkeypatch.setattr(extract_queue, "_QUEUE_FILE", target)
    return target


def test_队列路径落在本会话沙箱且与沙箱库同根():
    """纯读：不开队列、不碰任何文件。"""
    root = _require_sandbox()
    queue_file = Path(extract_queue._QUEUE_FILE)
    assert _norm(queue_file) == root / "data" / "extract_queue.json", (
        f"队列未落在会话沙箱: {queue_file}（沙箱根={root}）"
    )
    assert _assert_session_owned(queue_file) == root / "data" / "extract_queue.json"

    # 与沙箱 DB 同根：独立从 DATABASE_URL 反推，不靠 conftest 单一真源
    assert _sandbox_root_from_db_url() == root, "反推库沙箱根与 conftest 不一致"
    # 沙箱主密钥（A8 同款重定向）不得落在生产 data/ 目录（xdist 继承口径见 _under_any_sandbox）
    key_file = _norm(os.environ["AMBRACE_CREDENTIAL_KEY_FILE"])
    assert _under_any_sandbox(key_file), f"主密钥逃出沙箱: {key_file}"
    assert key_file.parent != _norm(_prod_default_queue_file()).parent

    # 生产默认位置已不是当前路径，且它不是当前路径的祖先
    prod_default = _norm(_prod_default_queue_file())
    assert _norm(queue_file) != prod_default, "队列仍指向生产默认位置"
    assert prod_default not in _norm(queue_file).parents


def test_归属守卫拒绝非沙箱路径(tmp_path):
    """守卫本身有效：只有会话沙箱过；生产默认位置、pytest 临时目录、沙箱根的父目录都拒绝。"""
    root = _require_sandbox()
    assert _assert_session_owned(root / "data" / "extract_queue.json") == (
        root / "data" / "extract_queue.json"
    )
    for bad in (_prod_default_queue_file(), tmp_path / "extract_queue.json", root.parent / "data"):
        with pytest.raises(AssertionError):
            _assert_session_owned(bad)


def test_真实队列流程在归属路径上正常(monkeypatch, tmp_path):
    """先归属、后写入：合成配对跑真实 add → peek → take → release 全流程。"""
    session_queue = Path(extract_queue._QUEUE_FILE)
    _assert_session_owned(session_queue)          # 基线路径归属先过（未过则本用例到此为止）
    existed_before = session_queue.exists()

    target = _claim_tmp_queue(monkeypatch, tmp_path)
    now = time.time()
    for pair in _PAIRS:
        extract_queue.add(_SID, {**pair, "ts": now})
    assert [p["source_id"] for p in extract_queue.peek(_SID)] == [900000001, 900000002, 900000003]

    # Legacy path is redirected above; its SQLite sibling must share that sandbox.
    assert extract_queue.database_path() == target.with_suffix(".sqlite3")
    with sqlite3.connect(extract_queue.database_path()) as db:
        assert db.execute("SELECT count(*) FROM extraction_tasks WHERE state='pending'").fetchone()[0] == 3

    rows = extract_queue.take(_SID)               # 取出 ⇒ 同时登记在途占位
    assert len(rows) == 3
    assert extract_queue.peek(_SID) == []
    assert extract_queue.pending_source_ids() == {900000001, 900000002, 900000003}

    assert extract_queue.complete(rows[0])      # 逐条提完各自放开
    assert extract_queue.pending_source_ids() == {900000002, 900000003}

    # remove_source（catchup／截断保底已即时提取 ⇒ 主链路不再重复提取）
    extract_queue.add(_SID, {"user_message": "占位提问丁", "ai_response": "占位回答丁",
                             "source_id": 900000004, "ts": now})
    extract_queue.remove_source(_SID, 900000004)
    assert extract_queue.peek(_SID) == []
    assert extract_queue.pending_source_ids() == {900000002, 900000003}

    # 本用例只认领了 tmp_path 这一个写入点：会话沙箱队列文件的存在性未被本用例改变
    assert session_queue.exists() == existed_before


def test_他处用例monkeypatch结束后回到会话沙箱(monkeypatch, tmp_path):
    """既有写法（`monkeypatch.setattr(extract_queue, "_QUEUE_FILE", tmp_path/...)`）的还原基线是沙箱。"""
    session_queue = _assert_session_owned(Path(extract_queue._QUEUE_FILE))
    sandbox_value = extract_queue._QUEUE_FILE

    monkeypatch.setattr(extract_queue, "_QUEUE_FILE", _norm(tmp_path / "extract_queue.json"))
    assert extract_queue._QUEUE_FILE != sandbox_value

    monkeypatch.undo()                           # 模拟该用例结束
    assert _norm(extract_queue._QUEUE_FILE) == session_queue, (
        "monkeypatch 还原后必须回到会话沙箱，而不是生产默认路径"
    )


def test_生产默认位置与sqlite配置未被测试引导改动():
    """重定向只发生在测试进程：生产默认常量按源码写在模块里，DB/密钥 env 仍是沙箱。"""
    root = _require_sandbox()
    source = Path(extract_queue.__file__).read_text(encoding="utf-8-sig")
    assert '_QUEUE_FILE = Path(__file__).resolve().parents[2] / "data" / "extract_queue.json"' in source, (
        "extract_queue.py 的生产默认路径常量被改了——本单只允许测试侧重定向"
    )
    assert _norm(_prod_default_queue_file()).parent.name == "data"

    # sqlite / 密钥 / 向量库三项引导未被本守卫或本次改动牵动
    assert _norm(_sandbox_root_from_db_url()) == root
    assert os.environ["CHROMA_PERSIST_DIR"] == (root / "vector_store").as_posix()
    assert _under_any_sandbox(os.environ["AMBRACE_CREDENTIAL_KEY_FILE"])