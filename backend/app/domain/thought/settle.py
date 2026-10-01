# -*- coding: utf-8 -*-
"""批 4 M2-a · 念头池**结算**（升级 / 释放 / 挤出）——纯函数**组合**层。

⚠️ 本模块**不新造任何判据**：§2.2 的升级/降级/挤出 与 §2.3 的三档释放，判据全部复用
``dynamics`` 里 M0 已落并单测过的纯函数（``should_promote`` / ``should_fade`` /
``evict`` / ``classify_release`` / ``apply_told_flat``）。这里只做两件事：

①把「给定一行 ⇒ 下一状态」的**组合与顺序**钉住（顺序不可颠倒：先升级会改变各档计数，
  再按容量挤出才准）；
②给出可**表驱动**测试的结算入口，便于 M2-b 接线前先把判据验完。

边界（与 domain/thought 同级同纪律）：**零 IO、零 ORM、零 DB、零 flag、零网络**。

口径来源：AMBRACE_批4_念头池T2_详细设计_v1_20260929.md §2.2（升级判据）/ §2.3（释放）。
"""
from __future__ import annotations

from app.domain.thought import dynamics as dyn


def next_active_status(
    status: str,
    salt: float,
    hit_sources: tuple[str, ...] | list[str],
    age_days: float,
) -> str:
    """活跃池内的状态迁移（**不含挤出**——挤出按容量单独算，见 ``settle_pool``）。

    - ``spark → obsession``：``salt ≥ S_OBS`` 且命中过 ``≥2`` 个不同来源面；
    - ``obsession → faded``：``age_days > TTL``（只标状态、不删行，协议 §十七）；
    - 其余（含 ``told_flat``）原样返回。
    """
    if status == dyn.STATUS_SPARK and dyn.should_promote(salt, hit_sources):
        return dyn.STATUS_OBSESSION
    if status == dyn.STATUS_OBSESSION and dyn.should_fade(age_days):
        return dyn.STATUS_FADED
    return str(status)


def settle_pool(
    rows: list[dict],
    *,
    cap_spark: int | None = None,
    cap_obsession: int | None = None,
    cap_told_flat: int | None = None,
) -> dict:
    """一次完整结算：**先升级/降级，再按容量挤出**（顺序不可颠倒）。

    入参每行需含 ``id / status / salt / novelty``，升级判定另需 ``age_days`` 与
    ``hit_sources``（缺失按 0 / 空处理，脏行不该让整池结算失败）。

    返回 ``{"statuses", "promoted", "faded", "evicted"}``；**不修改入参**。

    - ``promoted``：本轮 spark→obsession 的 id；
    - ``faded``：本轮 TTL 到期转 faded 的 id（不含被挤出的）；
    - ``evicted``：容量超限被挤出的 id（按 ``salt × novelty`` 升序淘汰最弱者）。
    """
    caps = {
        "cap_spark": dyn.CAP_SPARK if cap_spark is None else cap_spark,
        "cap_obsession": dyn.CAP_OBSESSION if cap_obsession is None else cap_obsession,
        "cap_told_flat": dyn.CAP_TOLD_FLAT if cap_told_flat is None else cap_told_flat,
    }

    statuses: dict[str, str] = {}
    promoted: list[str] = []
    faded: list[str] = []
    records: list[dict] = []

    for row in rows:
        rid = str(row.get("id"))
        status = str(row.get("status") or dyn.STATUS_SPARK)
        salt = float(row.get("salt") or 0.0)
        nov = float(row.get("novelty") or 0.0)
        new_status = next_active_status(
            status, salt, row.get("hit_sources") or (), float(row.get("age_days") or 0.0))
        statuses[rid] = new_status
        if new_status != status:
            (promoted if new_status == dyn.STATUS_OBSESSION else faded).append(rid)
        records.append({"id": rid, "status": new_status, "salt": salt, "novelty": nov})

    evicted = [str(i) for i in dyn.evict(records, **caps)]
    for rid in evicted:
        statuses[rid] = dyn.STATUS_FADED

    return {"statuses": statuses, "promoted": promoted, "faded": faded, "evicted": evicted}


def apply_release(
    row: dict,
    *,
    sent_ok: bool,
    replied_within_window: bool,
    spent_at=None,
) -> dict:
    """§2.3 三档释放：给定一条**被引用**的念头与它的发送结果，返回该行的写回字段。

    - ``spent``（全额）：发送成功 ∧ 60 分钟内有回 ⇒ ``status=spent``、记 ``spent_at``、
      ``salt`` 冻结不再参与选择（被接住的事重复讲最刺眼）；
    - ``told_flat``（半释放）：发出去了但没人接 ⇒ ``salt *= 0.35``、``tell_count += 1``，
      仍活跃以便换角度重提；``tell_count ≥ 2`` ⇒ 强制 ``faded``（沉默 ≠ 可无限重试）；
    - ``never_told``：没发出去 ⇒ **不惩罚**，原样返回（未用的东西不该被惩罚）。

    返回的 dict 含 ``status / salt / tell_count / spent_at / release``；调用方照它写回
    ``thought_pool`` 即可（本函数不写库、不改入参）。
    """
    kind = dyn.classify_release(bool(sent_ok), bool(replied_within_window))
    salt = float(row.get("salt") or 0.0)
    tell_count = int(row.get("tell_count") or 0)
    status = str(row.get("status") or dyn.STATUS_SPARK)

    if kind == dyn.STATUS_SPENT:
        return {"status": dyn.STATUS_SPENT, "salt": salt, "tell_count": tell_count,
                "spent_at": spent_at, "release": dyn.STATUS_SPENT}
    if kind == dyn.STATUS_TOLD_FLAT:
        new_salt, new_count, new_status = dyn.apply_told_flat(salt, tell_count)
        return {"status": new_status, "salt": new_salt, "tell_count": new_count,
                "spent_at": None, "release": dyn.STATUS_TOLD_FLAT}
    return {"status": status, "salt": salt, "tell_count": tell_count,
            "spent_at": None, "release": "never_told"}
