# -*- coding: utf-8 -*-
"""A21 锁族守卫（2026-10-04）：把「先诊断后修」的诊断结论钉成一条可跑的断言。

诊断（现场三证记在 docs/plans.md 的 A21 行）：偶发的 ``database is locked`` 出现在**两层库**
上——每例克隆库（``test_prospective_intent_governance`` / ``test_context_no_caller_failclosed``）
与会话共享沙箱库（``test_ws_notify_disabled``）；``PRAGMA busy_timeout=10000`` 确实生效却仍等满
10 s ⇒ 持锁者不是慢写，而是一条「已发出写、既不提交也不归还」的连接——它进了引用环，
只能等分代 GC 才被终止。受害者阻塞在 aiosqlite 工作线程里时 Python 侧不再分配对象，
分代回收没机会被阈值触发 ⇒ **单纯重试无效**，必须先逼一次回收。

本文件三条断言各钉一半：
1. ``test_gc_held_write_lock_is_broken_by_reclaim``：复现该机理并验证被 :mod:`_dbclone`
   的会话层处置打断；同一用例内把「回收」那一步摘掉（变异自测）⇒ 必须重新报 locked，
   证明起作用的是**回收**而不是重试。
2. ``test_live_writer_still_raises``：对手是**活连接**（真·抢写）时必须原样抛错，且
   重试次数**恰好等于上界**（3 次）⇒ 处置既没把锁问题掩盖成通过，也没退化成无限重试。
   （这里刻意不断言墙钟耗时：满载的会话里一次 ``gc.collect()`` 就要 0.4 s 量级，
   10-04 全量现场实测 3.08 s 撞死在 3 s 界上——计数字段才是确定性的上界。）
3. ``test_sandbox_factory_wired``：会话共享沙箱库那一层（探针现场抓到
   ``test_ws_notify_disabled`` 两例红在该库）也必须挂同一处置，接线在 ``conftest.py`` 末尾。

每例把 ``busy_timeout`` 压到 300 ms：修复在位时用不到它；修复被摘掉时快速失败，
不会让守卫用例白等 10 s。

A21-b（2026-10-04）追加：本用例的**道具**就是一条「不还」的连接，它被 GC 掐掉时必然产生
``non-checked-in`` SAWarning。该警告在 :func:`_holder_warning` 里**就地捕获并断言确实发生**
（局部 ``catch_warnings``，出块即还原），这样 ``-W error::sqlalchemy.exc.SAWarning`` 才能只
惩罚真实例行泄漏。禁止用 ``filterwarnings`` 全局静音——那会把真泄漏一起藏掉。
"""
from __future__ import annotations

import asyncio
import contextlib
import gc
import sqlite3
import warnings
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError, PendingRollbackError, SAWarning
from sqlalchemy.orm import unitofwork
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import _dbclone
from _dbclone import BusyReclaimSession, clone_engine, make_session_factory

SHORT_BUSY_MS = 300
_INSERT = text("INSERT INTO server_settings(key,value) VALUES(:k,:v)")
_DELETE = text("DELETE FROM server_settings WHERE key=:k")
_LOCKED = "database is locked"


def _non_checked_in(caught) -> list[str]:
    return [str(w.message) for w in caught
            if issubclass(w.category, SAWarning) and "non-checked-in" in str(w.message)]


@contextlib.contextmanager
def _holder_warning():
    """局部捕获本用例道具连接的 ``non-checked-in`` SAWarning，出块时断言它确实出现过。

    出现 = 道具连接真的进了引用环、只能靠分代回收掐掉（= A21 现场形态复现成功）；
    没出现 = 道具被正常归还，机理没复现，守卫就成了空断言。
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")        # 只在本块内生效；块外照常受 -W error 约束
        yield caught
    assert _non_checked_in(caught), \
        "道具连接未被 GC 掐掉（无 non-checked-in）⇒ A21 现场形态没复现，守卫失去意义"


def _abandon_a_writer(factory, key: str) -> None:
    """造出现场那条连接：发一条写、不提交也不关闭，再把它塞进引用环（只能等分代 GC）。

    刻意**不在这里** ``gc.collect()``：道具的价值就是「一直持锁直到受害者被 BUSY 卡住」，
    提前回收会把锁放掉，机理就复现不出来了（警告交由 :func:`_holder_warning` 的用例级窗口接住）。
    """
    async def _go():
        session = factory()
        await session.execute(_INSERT, {"k": key, "v": "1"})
        ring = [session]
        ring.append(ring)                    # 引用环 ⇒ 返回后成为不可达垃圾，靠 gc 才回收
    asyncio.run(_go())


async def _victim(factory, key: str) -> None:
    """下一个 asyncio.run 块里的一条普通写（= 现场 ``DELETE FROM proactive_message_logs`` 的形状）。"""
    async with factory() as session:
        await session.execute(text(f"PRAGMA busy_timeout={SHORT_BUSY_MS}"))
        await session.execute(_DELETE, {"k": key})
        await session.commit()


def _cleanup(*engines) -> None:
    gc.collect()                             # 把守卫自己造的垃圾连接当场收掉，别留 -wal 句柄
    for eng in engines:
        asyncio.run(eng.dispose())


@pytest.mark.slow
def test_gc_held_write_lock_is_broken_by_reclaim(tmp_path: Path, monkeypatch) -> None:
    engine = clone_engine(tmp_path / "t.db")
    with _holder_warning():                   # 道具连接的 non-checked-in 在块内断言（见 _holder_warning）
        try:
            factory = make_session_factory(engine)

            _abandon_a_writer(factory, "holder-1")
            before = _dbclone.BUSY_RECLAIMS
            asyncio.run(_victim(factory, "nothing-1"))          # 修复在位 ⇒ 这条写必须成功
            assert _dbclone.BUSY_RECLAIMS > before, \
                "写成功了但没走「回收」路径 ⇒ 机理没被真正复现（守卫失去意义）"

            # 变异自测：只摘掉「逼一次分代回收」，重试次数与退避原样保留
            monkeypatch.setattr(_dbclone, "_reclaim", lambda: 0)
            _abandon_a_writer(factory, "holder-2")
            with pytest.raises(Exception) as excinfo:
                asyncio.run(_victim(factory, "nothing-2"))
            assert "database is locked" in str(excinfo.value), \
                f"摘掉回收后仍通过 ⇒ 这条守卫没有钉在回收上（{type(excinfo.value).__name__}）"
        finally:
            _cleanup(engine)


@pytest.mark.slow
def test_live_writer_still_raises(tmp_path: Path) -> None:
    """真·抢写（持锁者是活连接）必须照旧报错：处置不得把锁问题掩盖成通过。"""
    db = tmp_path / "live.db"
    engine = clone_engine(db)
    holder = sqlite3.connect(str(db))
    holder.isolation_level = None                           # 自己管事务
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO server_settings(key,value) VALUES('live','1')")
    try:
        factory = make_session_factory(engine)
        before = _dbclone.BUSY_RECLAIMS
        with pytest.raises(Exception) as excinfo:
            asyncio.run(_victim(factory, "nothing"))
        assert "database is locked" in str(excinfo.value), \
            f"异常类型不对（{type(excinfo.value).__name__}）"
        attempts = _dbclone.BUSY_RECLAIMS - before
        assert attempts == len(_dbclone._BUSY_BACKOFF), (
            f"重试走了 {attempts} 次，上界是 {len(_dbclone._BUSY_BACKOFF)} 次"
            "⇒ 要么退化成无限重试，要么根本没走重试路径")
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        _cleanup(engine)


def test_sandbox_factory_wired() -> None:
    """会话共享沙箱库那一层也必须挂同一处置（接线见 ``conftest.py`` 末尾）。

    探针现场：10-04 带探针的全量里 ``test_ws_notify_disabled.py`` 两例红在该库，
    克隆库侧的 ``make_session_factory`` 管不到它。
    """
    from app.db.session import async_session_factory

    assert async_session_factory.class_ is BusyReclaimSession, (
        "沙箱库会话工厂退回普通 AsyncSession ⇒ A21 锁族在这一层没有处置（conftest 接线被移走）")


# ── A36（2026-10-07）：flush/commit 撞锁的「错误诚实化」＋ 单元级可重试 ────────────────
# 上面 :func:`test_live_writer_still_raises` 的读数只覆盖**语句级**（``execute``）；本段钉的是
# 事务级：``commit()`` / ``flush()`` 撞 BUSY 时会话已进入「必须先 rollback」状态，旧实现原地重放
# ``super().commit()`` 立刻抛 ``PendingRollbackError`` —— 既救不回来，又把原始 locked 掩盖成
# 另一个错（10-07 现场：``test_ws_notify_disabled._seed`` 报的是 PendingRollbackError，
# 排查的人不会想到是锁）。四例成对：两条守卫 ＋ 各自一条变异自测。

def _arm_busy_in_flush(monkeypatch, fail_times: int = 1) -> list:
    """把 ``database is locked`` 钉进 flush 的**执行体内**（返回命中清单供守卫自证）。

    为什么打在 :class:`sqlalchemy.orm.unitofwork.UOWTransaction` 的 ``execute`` 上，而不是整个
    换掉 ``Session.flush``：SQLAlchemy 只有在 flush **体内**报错时才会把事务置为 DEACTIVE 并记下
    ``_rollback_exception``。10-07 探针实测：替换 ``Session.flush`` 不触发该状态，重放 commit 反而
    「成功」⇒ 那样写出来的守卫与变异自测都是空断言。
    """
    hits: list = []
    real = unitofwork.UOWTransaction.execute

    def _fake(self):
        if len(hits) < fail_times:
            hits.append(1)
            raise OperationalError("INSERT INTO users", {}, sqlite3.OperationalError(_LOCKED))
        return real(self)

    monkeypatch.setattr(unitofwork.UOWTransaction, "execute", _fake)
    return hits


def _a36_user(uid: int):
    from app.models.user import User

    return User(id=uid, username="a36_%d" % uid, nickname="a36_%d" % uid, password_hash="x")


@pytest.mark.slow
def test_commit_busy_raises_locked_not_pending_rollback(tmp_path: Path, monkeypatch) -> None:
    """A36 守卫①：commit 撞 BUSY ⇒ 原样抛 locked（不许变成 PendingRollbackError）＋ 会话当场可续用。"""
    from app.models.user import User

    engine = clone_engine(tmp_path / "honest.db")
    hits = _arm_busy_in_flush(monkeypatch, 1)
    try:
        factory = make_session_factory(engine)
        before = _dbclone.BUSY_RECLAIMS

        async def _go():
            async with factory() as db:
                db.add(_a36_user(990001))
                with pytest.raises(OperationalError) as excinfo:
                    await db.commit()
                assert _LOCKED in str(excinfo.value), \
                    f"抛出的不是 locked 文本：{str(excinfo.value)[:120]}"
                assert not isinstance(excinfo.value, PendingRollbackError), \
                    "原始 BUSY 又被包装成 PendingRollbackError ⇒ 锁问题会被排查成别的东西"
                # 抛出前已当场复位 ⇒ 同一会话还能接着用（旧实现做不到这一步）
                db.add(_a36_user(990002))
                await db.commit()
                got = (await db.execute(select(User).where(User.id == 990002))).scalar_one_or_none()
                assert got is not None, "复位后写进去的行读不到 ⇒ 会话状态没真的干净"

        asyncio.run(_go())
        assert hits, "BUSY 道具没被触发（flush 体内一次都没进）⇒ 上面全是空断言"
        assert _dbclone.BUSY_RECLAIMS > before, "错误抛出前没逼回收 ⇒ A21 那条打断锁的链路又断了"
    finally:
        _cleanup(engine)


@pytest.mark.slow
def test_mutation_without_reset_reintroduces_pending_rollback(tmp_path: Path, monkeypatch) -> None:
    """上一条的变异自测：摘掉「当场复位」⇒ 会话立刻不可续用（守卫①的第二半必须变红）。

    摘掉后 commit 仍原样抛 locked（那半不受影响），所以红点落在「同一会话还能不能用」上——
    正是 :func:`_dbclone._reset_after_busy` 起作用的地方。
    """
    from app.models.user import User

    monkeypatch.setattr(_dbclone, "_reset_after_busy", lambda sess: asyncio.sleep(0))
    engine = clone_engine(tmp_path / "nomutate.db")
    _arm_busy_in_flush(monkeypatch, 1)
    try:
        factory = make_session_factory(engine)

        async def _go():
            async with factory() as db:
                db.add(_a36_user(990003))
                with pytest.raises(OperationalError) as excinfo:
                    await db.commit()
                assert _LOCKED in str(excinfo.value)
                db.add(_a36_user(990004))
                with pytest.raises(PendingRollbackError):
                    await db.commit()
                await db.rollback()          # 收尾复位，别让 async with 的 close 再叠一条警告
                assert (await db.execute(select(User).where(User.id == 990004))).scalar_one_or_none() is None

        asyncio.run(_go())
    finally:
        _cleanup(engine)


@pytest.mark.slow
def test_run_unit_of_work_retries_with_a_fresh_session(tmp_path: Path, monkeypatch) -> None:
    """A36 守卫②：单元级重试 —— 第一次被 locked 打掉后**重建会话**再跑一遍，最终落库成功。"""
    from app.models.user import User

    engine = clone_engine(tmp_path / "uow.db")
    hits = _arm_busy_in_flush(monkeypatch, 1)
    try:
        factory = make_session_factory(engine)
        runs: list = []

        async def _unit(db):
            runs.append(1)
            db.add(_a36_user(990005))
            await db.commit()

        before = _dbclone.BUSY_RECLAIMS
        asyncio.run(_dbclone.run_unit_of_work(factory, _unit))
        assert len(runs) == 2, f"单元跑了 {len(runs)} 次（应为 2）⇒ 要么没重试，要么根本没撞锁"
        assert hits, "BUSY 道具没被触发 ⇒ 上面是空断言"
        assert _dbclone.BUSY_RECLAIMS > before, "单元级重试没逼回收 ⇒ 持锁者还挂在 GC 上，重试等于白跑"

        async def _read():
            async with factory() as db:
                return (await db.execute(select(User).where(User.id == 990005))).scalar_one_or_none()

        assert asyncio.run(_read()) is not None, "重试报成功但行没落库"
    finally:
        _cleanup(engine)


@pytest.mark.slow
def test_mutation_replaying_on_same_session_is_detected(tmp_path: Path, monkeypatch) -> None:
    """上一条的变异道具：就地复刻「重试不重建会话」（＝旧代码对 ``super().commit()`` 原地重试的语义）⇒ 必须报红。

    刻意按旧代码的真实形态写：同一会话、不复位、原样重放。10-07 探针实测**只 close 不重建是分不清的**
    （``Session.close()`` 顺手把会话复位了，重放反而成功）⇒ 道具若写成「每轮 close 再复用」就成了空断言。
    """
    engine = clone_engine(tmp_path / "uowmut.db")
    _arm_busy_in_flush(monkeypatch, 1)
    try:
        # A36（Codex 收尾）：变异必须走**普通 AsyncSession** —— BusyReclaimSession 的 commit() 撞 BUSY
        # 会自动复位会话，旧写法「不换会话原地重放」就复现不出来（本条最初因此假绿，故改此）。
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        runs: list = []

        async def _unit(db):
            runs.append(1)
            db.add(_a36_user(990006))
            await db.commit()

        async def _old_shape():
            session = factory()                # 旧语义：一个会话吃下所有重试
            try:
                for attempt in range(3):
                    try:
                        await _unit(session)
                        return "不该走到这里"
                    except Exception as exc:
                        if not _dbclone._is_sqlite_busy(exc) or attempt + 1 >= 3:
                            raise
                        await asyncio.sleep(0)  # 旧实现也退避，但**不换会话**
            finally:
                await session.rollback()
                await session.close()

        with pytest.raises(PendingRollbackError):
            asyncio.run(_old_shape())
        assert len(runs) == 2, f"旧形态跑了 {len(runs)} 次（应为 2：一次撞锁一次被 PendingRollback 挡回）"
    finally:
        _cleanup(engine)
