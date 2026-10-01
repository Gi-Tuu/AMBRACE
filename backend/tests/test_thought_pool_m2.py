# -*- coding: utf-8 -*-
"""批 4 M2-a · 念头池结算（升级 / 释放 / 挤出）的**表驱动**断言。

钉住三件事（改任一即红）：
1. **升级/降级**（§2.2）：``salt ≥ S_OBS`` 且 ``≥2`` 个不同来源面才升级；``age > TTL`` 才降级；
2. **挤出**（§2.2）：容量超限按 ``salt × novelty`` 升序淘汰最弱者，且**先升级后挤出**；
3. **释放**（§2.3）：三档（spent / told_flat / never_told）各自写回字段正确，
   ``tell_count ≥ 2`` 后强制 faded、never_told 不惩罚。

判据本体在 ``dynamics``（M0 已单测），本文件测的是 **settle.py 的组合与顺序**。
零 IO：不连生产库、不建表、只喂内存 dict。
"""
from __future__ import annotations

import pytest

from app.domain.thought import dynamics as dyn
from app.domain.thought import settle


def _row(rid="1", status=dyn.STATUS_SPARK, salt=1.0, nov=0.5, age=0.0, faces=()):
    return {"id": rid, "status": status, "salt": salt, "novelty": nov,
            "age_days": age, "hit_sources": list(faces)}


# ───────────────── 1. 升级 / 降级（§2.2）─────────────────

@pytest.mark.parametrize("salt,faces,expect", [
    (2.0, ["activity", "fact"], dyn.STATUS_OBSESSION),   # 恰好达标
    (2.5, ["activity", "fact"], dyn.STATUS_OBSESSION),
    (1.9, ["activity", "fact"], dyn.STATUS_SPARK),       # 咸度差一点
    (2.0, ["activity"], dyn.STATUS_SPARK),               # 只有 1 个来源面
    (2.0, ["activity", "activity"], dyn.STATUS_SPARK),   # 同一面重复不算
])
def test_promote_table(salt, faces, expect):
    """升级表驱动：salt ≥ S_OBS **且** ≥2 个不同来源面，缺一不升级。"""
    assert settle.next_active_status(dyn.STATUS_SPARK, salt, faces, 0.0) == expect


@pytest.mark.parametrize("age,expect", [
    (0.0, dyn.STATUS_OBSESSION),
    (21.0, dyn.STATUS_OBSESSION),   # 边界含等号：> TTL 才降
    (21.1, dyn.STATUS_FADED),
    (40.0, dyn.STATUS_FADED),
])
def test_fade_table(age, expect):
    """降级表驱动：obsession 且 age_days > TTL(21) ⇒ faded；边界含等号。"""
    assert settle.next_active_status(dyn.STATUS_OBSESSION, 2.0, ["a", "b"], age) == expect


def test_spark_never_fades_directly():
    """spark 不会直接 TTL 降级（只走升级或被挤出）。"""
    assert settle.next_active_status(dyn.STATUS_SPARK, 2.0, ["a", "b"], 99.0) == dyn.STATUS_OBSESSION


def test_told_flat_is_untouched_by_promotion():
    """told_flat 不参与升级判定（它的出路是释放或过期）。"""
    assert settle.next_active_status(dyn.STATUS_TOLD_FLAT, 9.9, ["a", "b"], 0.0) == dyn.STATUS_TOLD_FLAT


# ───────────────── 2. 结算组合：先升级后挤出 ─────────────────

def test_settle_promotes_then_evicts_in_order():
    """★ 顺序钉死：**先升级**（改变各档计数）**再挤出**，不能颠倒。"""
    rows = [_row(str(i), salt=2.0, nov=0.5, faces=["activity", "fact"])
            for i in range(5)]
    out = settle.settle_pool(rows, cap_obsession=2)
    assert len(out["promoted"]) == 5, "先全部升级为 obsession"
    assert len(out["evicted"]) == 3, "obsession 顶=2 ⇒ 挤出 3 条最弱者"


def test_settle_no_eviction_under_cap():
    rows = [_row(str(i), salt=1.0, nov=0.5) for i in range(5)]
    out = settle.settle_pool(rows, cap_spark=12)
    assert out["evicted"] == []
    assert all(s == dyn.STATUS_SPARK for s in out["statuses"].values())


def test_settle_evicts_weakest_first():
    """挤出按 ``salt × novelty`` **升序**淘汰 ⇒ 最弱的先走。"""
    rows = [
        _row("weak", salt=0.1, nov=0.1),
        _row("strong", salt=3.0, nov=0.9),
        _row("mid", salt=1.0, nov=0.5),
    ]
    out = settle.settle_pool(rows, cap_spark=1)
    # capacity=1 ⇒ 3 条里淘汰 2 条最弱（weak 0.01 < mid 0.5 < strong 2.7）
    assert out["evicted"] == ["weak", "mid"]
    assert out["statuses"]["strong"] == dyn.STATUS_SPARK


def test_settle_does_not_mutate_input():
    rows = [_row("1", salt=2.0, faces=["a", "b"])]
    snapshot = [dict(r) for r in rows]
    settle.settle_pool(rows, cap_obsession=0)
    assert rows == snapshot


def test_settle_tolerates_dirty_rows():
    """缺字段/脏值不该让整池结算失败（形状层不为单行脏数据抛错）。"""
    out = settle.settle_pool([{"id": "x"}], cap_spark=12)
    assert out["statuses"]["x"] == dyn.STATUS_SPARK


def test_settle_empty_pool():
    out = settle.settle_pool([])
    assert out == {"statuses": {}, "promoted": [], "faded": [], "evicted": []}


# ───────────────── 3. 释放（§2.3）─────────────────

def test_release_spent_when_replied_in_window():
    """全额释放：发送成功 ∧ 60 分钟内有回 ⇒ spent + 记 spent_at。"""
    out = settle.apply_release(_row(salt=2.0), sent_ok=True,
                               replied_within_window=True, spent_at="2026-10-01 12:00:00")
    assert out["release"] == dyn.STATUS_SPENT
    assert out["status"] == dyn.STATUS_SPENT
    assert out["spent_at"] == "2026-10-01 12:00:00"
    assert out["salt"] == 2.0, "salt 冻结（不再参与选择）"


def test_release_told_flat_when_sent_but_no_reply():
    """半释放：发出去没人接 ⇒ salt *= 0.35、tell_count += 1，仍活跃。"""
    out = settle.apply_release(_row(salt=1.0, status=dyn.STATUS_OBSESSION),
                               sent_ok=True, replied_within_window=False)
    assert out["release"] == dyn.STATUS_TOLD_FLAT
    assert out["status"] == dyn.STATUS_TOLD_FLAT
    assert out["salt"] == pytest.approx(0.35)
    assert out["tell_count"] == 1
    assert out["spent_at"] is None


def test_release_told_flat_twice_forces_faded():
    """★ 沉默不等于可以无限重试：tell_count ≥ 2 ⇒ 强制 faded。"""
    row = _row(salt=1.0, status=dyn.STATUS_TOLD_FLAT)
    row["tell_count"] = 1          # 已经说过一次 ⇒ 本次是第二次
    out = settle.apply_release(row, sent_ok=True, replied_within_window=False)
    assert out["tell_count"] == 2
    assert out["status"] == dyn.STATUS_FADED


def test_release_never_told_is_not_punished():
    """没发出去 ⇒ never_told，**不惩罚**（未用的东西不该被惩罚）。"""
    row = _row(salt=1.7, status=dyn.STATUS_SPARK)
    out = settle.apply_release(row, sent_ok=False, replied_within_window=True)
    assert out["release"] == "never_told"
    assert out["status"] == dyn.STATUS_SPARK
    assert out["salt"] == 1.7 and out["tell_count"] == 0


@pytest.mark.parametrize("sent,replied,release", [
    (True, True, dyn.STATUS_SPENT),
    (True, False, dyn.STATUS_TOLD_FLAT),
    (False, True, "never_told"),
    (False, False, "never_told"),
])
def test_release_classification_table(sent, replied, release):
    """释放分类表驱动（没发出去一律 never_told，与「有没有回」无关）。"""
    assert settle.apply_release(_row(), sent_ok=sent,
                                replied_within_window=replied)["release"] == release


def test_release_does_not_mutate_input():
    row = _row(salt=1.0)
    snapshot = dict(row)
    settle.apply_release(row, sent_ok=True, replied_within_window=False)
    assert row == snapshot


# ───────────────── 4. 纯态与接线边界 ─────────────────

def test_settle_module_is_pure():
    """结算层不引入任何 IO / ORM / 业务模块（结构性保证）。"""
    import ast
    import inspect

    path = inspect.getfile(settle)   # getfile 已返回 .py 路径，不能再取 dirname
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    banned = {"sqlalchemy", "sqlite3", "requests", "httpx", "asyncio", "fastapi"}
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if a.name.split(".")[0] in banned
                    or a.name.split(".")[0] == "app" and "domain.thought" not in a.name]
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in banned or (root == "app" and "domain.thought" not in node.module):
                bad.append(node.module)
    assert not bad, f"settle.py 必须纯函数，却引入了：{bad}"


def test_settle_reuses_dynamics_not_duplicated_judgement():
    """★ 不新造判据：升级/降级/挤出/释放全部转调 dynamics 的既有纯函数。"""
    assert settle.next_active_status(dyn.STATUS_SPARK, 2.0, ["a", "b"], 0.0) == (
        dyn.STATUS_OBSESSION if dyn.should_promote(2.0, ["a", "b"]) else dyn.STATUS_SPARK)
    assert dyn.CAP_SPARK == 12 and dyn.TTL_DAYS == 21.0 and dyn.SALT_OBSSESSION_THRESHOLD == 2.0
