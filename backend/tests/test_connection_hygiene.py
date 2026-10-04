# -*- coding: utf-8 -*-
"""A21-b 连接卫生守卫（2026-10-04）：把「会话必须归还」钉成可跑断言，探测器自带变异自测。

背景（探针 v4 归属；证据日志留档在仓库外（`output/a21/`））：全量每轮固定有若干条
``non-checked-in`` SAWarning，形态是同一种——「取到会话后既不 commit 也不 close，直接进引用环」，
于是只能等分代 GC 把它掐掉；**被掐掉之前它一直占着那条库连接**，挂着未提交写时就是 A21 那把
写锁的持有者。分类结论（A21-b §1.2）：

- **测试侧没有例行的漏**：泄漏点只出现在两处「道具」上（本文件的变异自测、
  ``test_sqlite_busy_reclaim.py`` 的 ``_abandon_a_writer``），都是刻意构造且就地断言；
- 主线 3 条源自**生产代码** ``app/application/llm_config_service.py`` 的 ``_with_db``
  （异步生成器被 ``async for`` 提前 ``return`` 弃养 ⇒ ``async with`` 直到 GC 才解栈）。
  A21-b 只归属不动它；**A21-d（2026-10-05）已把它和同族 ``flag_service._policy_session`` 改成
  ``@asynccontextmanager``**，并由本文件末尾两条断言钉住（见 :func:`_prod_early_return_paths`）。

判据用 Codex 定的那条：**同一测试路径连跑两次，第二次不得再出现 non-checked-in**。

六条断言（前两条钉克隆库层、三四条钉会话沙箱库层、五六条钉生产侧提前 `return` 路径，
每层都是「正确写法必须干净 ＋ 泄漏写法必须被检出」一对）：
1. :func:`test_hygienic_usage_is_clean_across_two_rounds`——正确写法（``async with`` + commit）
   连跑两遍，每遍「本引擎建了几条连接 vs 归还几条」差额必须为 0，且捕到的 non-checked-in
   里**没有一条属于本引擎**；
2. :func:`test_leaked_usage_is_detected`——变异自测：同一条路径只把「归还」去掉，同一个探测器
   必须立刻报红（差额 1 ＋ 警告按地址命中）⇒ 证明第 1 条不是空断言；
3. :func:`test_sandbox_factory_hygienic_usage_is_clean`——会话共享沙箱库那一层（A21 锁族现场
   ``test_ws_notify_disabled`` 所在层）也必须 0 差额；
4. :func:`test_sandbox_leaked_usage_is_detected`——该层的变异自测，同上成对；
5. :func:`test_prod_early_return_paths_are_hygienic`——生产侧 ``_with_db`` / ``_policy_session``
   全部「提前 ``return``」路径（``db=None`` 自开会话）连跑两轮，每轮零差额、零 non-checked-in；
6. :func:`test_prod_abandoned_generator_form_is_detected`——第 5 条的变异自测：就地复刻修复前
   的「异步生成器 + ``async for`` 提前 return」形态，探测器必须命中 ⇒ 第 5 条不是空断言。

为什么警告要「按地址归属」：SAWarning 的文案带的是 aiosqlite 连接对象地址
（``<AdaptedConnection <aiosqlite.core.Connection object at 0x…>>``）。全量里同一时刻可能存在
**别的用例**留下的待回收连接，若只按「窗口内捕到几条」计数就会把它们误算到本守卫头上
（表现为随机红）。只认本引擎登记过的地址，判据才与执行顺序无关。
"""
from __future__ import annotations

import asyncio
import contextlib
import gc
import re
import warnings
from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import SAWarning

from _dbclone import clone_engine, make_session_factory

_ADDR = re.compile(r"at 0x([0-9a-fA-F]+)")
_INSERT = text("INSERT INTO server_settings(key,value) VALUES(:k,:v)")
_DELETE = text("DELETE FROM server_settings WHERE key=:k")


class _PoolLedger:
    """给某个 engine 记账：建了几条连接、正常归还了几条、每条的地址。

    ``connect`` 建一条 ⇒ ``created += 1``；``close``/``invalidate`` 归还一条 ⇒ ``returned += 1``。
    NullPool 没有 ``checkedout()`` 可用（``pool.status()`` 只回一个字面量），所以「有没有签出未还」
    只能自己按事件差值算——被 GC 掐掉的那条**不会**触发 close 事件（探针 v4 实测 ``close事件=False``），
    因此差额恰好就是泄漏条数。
    """

    def __init__(self, engine) -> None:
        self.created = 0
        self.returned = 0
        self.addrs: set[int] = set()
        sync_engine = engine.sync_engine
        event.listen(sync_engine, "connect", self._on_connect)
        event.listen(sync_engine, "close", self._on_release)
        event.listen(sync_engine, "invalidate", self._on_release)

    def _on_connect(self, dbapi_conn, _record=None) -> None:
        self.created += 1
        # aiosqlite 侧被警告引用的是**内层**连接对象；同步方言则就是它自己
        inner = getattr(dbapi_conn, "_connection", None)
        self.addrs.add(id(inner if inner is not None else dbapi_conn))
        self.addrs.add(id(dbapi_conn))

    def _on_release(self, dbapi_conn, _record=None, *_rest) -> None:
        self.returned += 1

    def snapshot(self) -> tuple[int, int]:
        return (self.created, self.returned)

    @staticmethod
    def leaked(before: tuple[int, int], after: tuple[int, int]) -> int:
        """窗口内「建了但没还」的条数（= 本引擎的签出未归还连接数）。"""
        return (after[0] - before[0]) - (after[1] - before[1])

    def ours(self, caught) -> list[str]:
        """把窗口内的 non-checked-in 警告**按地址归属**到本引擎。"""
        hits = []
        for w in caught:
            msg = str(w.message)
            if not issubclass(w.category, SAWarning) or "non-checked-in" not in msg:
                continue
            if any(int(h, 16) in self.addrs for h in _ADDR.findall(msg)):
                hits.append(msg)
        return hits


@contextlib.contextmanager
def _capture():
    """局部捕获警告（含 SAWarning），出块即还原过滤器——**不做全局静音**。

    这样 ``-W error::sqlalchemy.exc.SAWarning`` 下：本文件之外的真·例行泄漏照红，
    本文件刻意构造的那条泄漏则被明确断言（而不是被 ignore 掉）。
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        yield caught


def _write_hygienic(factory, key: str) -> None:
    """正确写法：``async with`` 拿会话、提交、退出时自动归还。"""
    async def _go():
        async with factory() as session:
            await session.execute(_INSERT, {"k": key, "v": "1"})
            await session.commit()
    asyncio.run(_go())


def _write_and_abandon(factory, key: str) -> None:
    """现场写法（变异自测用）：取出会话发一条写，既不提交也不关闭，直接进引用环。"""
    async def _go():
        session = factory()
        await session.execute(_INSERT, {"k": key, "v": "1"})
        ring = [session]
        ring.append(ring)
    asyncio.run(_go())


def _delete_hygienic(factory, key: str) -> None:
    """正确写法（删除侧）：同样 ``async with`` + commit。"""
    async def _go():
        async with factory() as session:
            await session.execute(_DELETE, {"k": key})
            await session.commit()
    asyncio.run(_go())


def _dispose(engine) -> None:
    gc.collect()
    asyncio.run(engine.dispose())


@pytest.mark.slow
def test_hygienic_usage_is_clean_across_two_rounds(tmp_path: Path) -> None:
    """同一写路径连跑两遍：每遍都不得留下「签出未归还」，也不得由本引擎产生 non-checked-in。"""
    engine = clone_engine(tmp_path / "t.db")
    ledger = _PoolLedger(engine)
    factory = make_session_factory(engine)
    try:
        for rnd in (1, 2):
            before = ledger.snapshot()
            with _capture() as caught:
                _write_hygienic(factory, f"hyg-{rnd}")
                gc.collect()
            after = ledger.snapshot()
            leaked = ledger.leaked(before, after)
            assert leaked == 0, (
                f"第 {rnd} 遍正确写法后仍有 {leaked} 条连接未归还"
                f"（建 {after[0] - before[0]} / 还 {after[1] - before[1]}）⇒ 夹具没闭环")
            ours = ledger.ours(caught)
            assert not ours, f"第 {rnd} 遍本引擎产生 {len(ours)} 条 non-checked-in：{ours[0][:90]}"
    finally:
        _dispose(engine)


@pytest.mark.slow
def test_leaked_usage_is_detected(tmp_path: Path) -> None:
    """变异自测：把「归还」去掉，探测器必须报红 ⇒ 上一条绿灯不是空断言。"""
    engine = clone_engine(tmp_path / "leak.db")
    ledger = _PoolLedger(engine)
    factory = make_session_factory(engine)
    try:
        before = ledger.snapshot()
        with _capture() as caught:
            _write_and_abandon(factory, "leak-1")
            gc.collect()
        after = ledger.snapshot()
        assert ledger.leaked(before, after) == 1, (
            f"泄漏写法后差额={ledger.leaked(before, after)}（应为 1）⇒ 台账没在数连接")
        assert ledger.ours(caught), "刻意泄漏的连接没被探测器命中 ⇒ 探测逻辑失效"
    finally:
        _dispose(engine)


def test_sandbox_factory_hygienic_usage_is_clean() -> None:
    """会话共享沙箱库那一层也必须归还干净（A21 锁族现场 ``test_ws_notify_disabled`` 就在该库）。

    这里用**全局单例引擎**（生产同款工厂），同一引擎被别的用例共用，故只统计本窗口内的增量，
    并按地址确认命中的警告不是我们造的。
    """
    from app.db.engine import engine
    from app.db.session import async_session_factory

    ledger = _PoolLedger(engine)
    before = ledger.snapshot()
    with _capture() as caught:
        _write_hygienic(async_session_factory, "a21b-sandbox-hyg")
        _delete_hygienic(async_session_factory, "a21b-sandbox-hyg")
        gc.collect()
    after = ledger.snapshot()
    leaked = ledger.leaked(before, after)
    assert leaked == 0, f"沙箱库工厂窗口内 {leaked} 条连接签出未归还 ⇒ 该层又出现漏"
    assert not ledger.ours(caught), "沙箱库工厂窗口内本层连接产生 non-checked-in"


def test_sandbox_leaked_usage_is_detected() -> None:
    """沙箱库那一层的变异自测：同一工厂只去掉「归还」，探测器必须命中（上一条绿灯不是空断言）。

    道具连接在窗口内就被 ``gc.collect()`` 收掉 ⇒ 它持锁的时间只有几毫秒，不会波及同会话其它用例。
    """
    from app.db.engine import engine
    from app.db.session import async_session_factory

    ledger = _PoolLedger(engine)
    before = ledger.snapshot()
    with _capture() as caught:
        _write_and_abandon(async_session_factory, "a21b-sandbox-leak")
        gc.collect()
    after = ledger.snapshot()
    assert ledger.leaked(before, after) == 1, (
        f"沙箱层泄漏写法后差额={ledger.leaked(before, after)}（应为 1）⇒ 该层台账没在数连接")
    assert ledger.ours(caught), "沙箱层刻意泄漏的连接未被命中 ⇒ 该层探测失效"
    _delete_hygienic(async_session_factory, "a21b-sandbox-leak")   # 道具行别留在共享沙箱库


# ── A21-d：生产侧「提前 return」路径（_with_db 家族）────────────────────────────

_POLICY_KEY = "a21d-early-return-guard"
_DELETE_POLICY = text("DELETE FROM flag_settings WHERE key=:k")


def _prod_early_return_paths():
    """生产侧全部「提前 ``return``」路径：一律 ``db=None`` ⇒ 走自开会话那一支（修复前后的分歧点）。

    四条 :mod:`app.application.llm_config_service` 解析链 + 同族 ``flag_service.set_flag_policy``
    （写完 commit 后立刻 ``return``，修复前同样把会话丢给收尾）。查询对象统一用不存在的 id，
    保证覆盖的是「提前返回」这条控制流，而不是某段业务结果。
    """
    from app.application.flag_service import set_flag_policy
    from app.application.llm_config_service import (
        resolve_character_llm_config,
        resolve_family_default_config,
        resolve_modality_config,
        resolve_user_default_config,
    )
    return [
        ("resolve_character_llm_config",
         lambda: resolve_character_llm_config(9_999_999, 9_999_999)),   # 角色不存在 → 提前 return
        ("resolve_user_default_config",
         lambda: resolve_user_default_config(9_999_999)),               # 无默认配置 → 提前 return
        ("resolve_family_default_config",
         lambda: resolve_family_default_config(9_999_999)),             # 根＝自己 → 提前 return
        ("resolve_modality_config",
         lambda: resolve_modality_config("vlm", user_id=None)),         # 整链未命中 → return None
        ("set_flag_policy",
         lambda: set_flag_policy(_POLICY_KEY, self_service=True)),      # commit 后提前 return
    ]


def _delete_policy_row() -> None:
    """道具行清理（走正确写法，别在共享沙箱库里再造一个签出未还）。"""
    async def _go():
        from app.db.database import async_session_factory
        async with async_session_factory() as session:
            await session.execute(_DELETE_POLICY, {"k": _POLICY_KEY})
            await session.commit()
    asyncio.run(_go())


def _abandon_via_async_for(factory, key: str) -> None:
    """变异道具：就地复刻**修复前**的形态——异步生成器持会话，调用体 ``async for`` 里提前 ``return``。

    归还时机探针（A21-d 实测）：这种形态在「函数刚返回」时差额＝1（连接仍签出），
    但要等 GC 才真正被终止并抛 non-checked-in；``asyncio.run`` 的事件循环收尾可能替它补上归还，
    所以**生产侧守卫必须逐次调用后立刻记账**，不能等整轮跑完再量（否则变异钉不住）。
    """
    async def _with_db_like(db):
        if db is not None:
            yield db
            return
        async with factory() as own:
            yield own

    async def _go():
        async for session in _with_db_like(None):
            await session.execute(_INSERT, {"k": key, "v": "1"})
            return                                    # 弃养生成器 ⇒ async with 不解栈
    asyncio.run(_go())


@pytest.mark.slow
def test_prod_early_return_paths_are_hygienic() -> None:
    """A21-d 守卫：生产侧每次提前 ``return`` 后，连接必须**当场**归还（不等 GC、不等事件循环收尾），两轮皆然。

    逐次调用后立刻记账，是因为 A21-d 时机探针实测：弃养形态在「函数刚返回」时差额＝1，
    但 ``asyncio.run`` 的事件循环收尾（asyncgen finalizer + ``shutdown_asyncgens``）**可能**替它补上
    归还——整轮跑完再量就会把变异放过去。
    已知观测边界：只读路径（``resolve_*``）不提交 ⇒ 弃养会留一条签出未还的连接，本判据抓得到；
    写路径（``set_flag_policy``）先 ``commit()`` ⇒ NullPool 在提交时就把连接交回，池账本看不出差别
    （该处的修复属同族一致性，不当作「被本判据钉住」宣称）。
    """
    from app.db.engine import engine

    ledger = _PoolLedger(engine)

    async def _drive():
        for rnd in (1, 2):
            for name, call in _prod_early_return_paths():
                before = ledger.snapshot()
                await call()
                after = ledger.snapshot()             # 中间不 await ⇒ 谁也没法替它补收尾
                leaked = ledger.leaked(before, after)
                assert leaked == 0, (
                    f"第 {rnd} 轮 {name}() 返回后仍有 {leaked} 条连接未当场归还"
                    f"（建 {after[0] - before[0]} / 还 {after[1] - before[1]}）"
                    "⇒ _with_db 家族又退回弃养写法（会话等 GC）")

    try:
        with _capture() as caught:
            asyncio.run(_drive())
            gc.collect()
        assert not ledger.ours(caught), (
            f"生产路径产生 {len(ledger.ours(caught))} 条 non-checked-in：{ledger.ours(caught)[0][:90]}")
    finally:
        _delete_policy_row()


def test_prod_abandoned_generator_form_is_detected() -> None:
    """上一条的变异自测：同一工厂、只把写法退回 ``async for`` 弃养，探测器必须立刻命中。"""
    from app.db.engine import engine
    from app.db.session import async_session_factory

    ledger = _PoolLedger(engine)
    before = ledger.snapshot()
    with _capture() as caught:
        _abandon_via_async_for(async_session_factory, "a21d-form-mutation")
        gc.collect()
    after = ledger.snapshot()
    assert ledger.leaked(before, after) == 1, (
        f"弃养写法后差额={ledger.leaked(before, after)}（应为 1）⇒ 台账数不到这条，上一条绿灯失真")
    assert ledger.ours(caught), "弃养写法生成的连接未被命中 ⇒ 生产侧守卫失效"
    _delete_hygienic(async_session_factory, "a21d-form-mutation")
