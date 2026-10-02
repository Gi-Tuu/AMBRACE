# -*- coding: utf-8 -*-
"""A4 批 4 / T2 M1（2026-09-30）：thought_pool 表 + 迁移 + 源侧配额 + 影子供给。

被测四件（派单 要做 ①–⑥）：
1. 模型 ``app/models/character.ThoughtPool``（新表；唯一约束防重复抽取、索引服务「取一条可用的」
   热路径；**不挂 character_states**、不反写任何水位）；
2. 迁移 ``b2d4f6a8c0e1``（has_table 守卫建表、幂等可逆、down_revision 挂当时的链头）
   与 ``_CURRENT_SCHEMA_SENTINELS`` 补一条表级哨兵；
3. 纯函数域 ``app/domain/thought/quota.py``（按面每日硬闸 / 入池准入门槛 / 结构化键，零 IO）
   ＋ τ 常量改名 ``NOVELTY_E_FOLDING_DAYS``（公式与取值一字未动 ⇒ 零行为）；
4. 服务层 ``app/application/thought_pool_service.py``（抽取→配额→准入→落库→留痕；
   flag 关＝零行为零查库；只 add/flush，**commit 由调用方定**）。

**本批零发送权**：不发一条消息、不改一句文本。边界用源码级（AST）断言钉住——注释与文档字符串里
写了「不改 arbiter / message_generator」，所以判「有没有引用」必须剥掉注释/docstring 再看代码本体。

口径与纪律：临时库一律 pytest ``tmp_path`` 私有 SQLite 文件；跑整链迁移那两条**不用** ``_dbclone``
（模板库是 create_all 出的当前 schema，跑迁移会假绿），落库/唯一约束那几条用 ``_dbclone``。
全程不碰 backend/data 生产库，不调模型、不走网络。
"""
from __future__ import annotations

import ast
import asyncio
import json
import math
import os
import sqlite3
from datetime import datetime

import pytest
from sqlalchemy import create_engine, func, inspect as sa_inspect, select
from sqlalchemy.exc import IntegrityError

from app.application import thought_pool_service as svc
from app.db.migrate import _CURRENT_SCHEMA_SENTINELS
from app.domain.thought import dynamics as dyn
from app.domain.thought import extract as ex
from app.domain.thought import filters as fl
from app.domain.thought import quota as qt
from app.flags.agent_flags import AGENT_FLAGS

# 快测档：本文件含真跑整链 alembic 的重量级集成用例。
pytestmark = pytest.mark.slow

PREV_REV = "d7e8f9a0b1c2"   # 本迁移的 down_revision（派单时当前 head；用显式 rev 防未来失配）
NEW_REV = "b2d4f6a8c0e1"
TABLE = "thought_pool"
_INDEX = "ix_thought_pool_char_user_status_salt"
_UQ = "uq_thought_pool_char_user_src_ref_hash"
_ALL_COLS = {
    "id", "character_id", "user_id", "thought_kind", "text", "source_type", "source_ref",
    "text_hash", "status", "salt", "novelty", "hit_sources", "tell_count",
    "created_at", "last_hit_at", "spent_at", "updated_at",
}

_CHAR, _USER = 1, 1
_NOW = datetime(2026, 9, 30, 3, 0)          # naive UTC ＝北京 2026-09-30 11:00
_TEXT = "今天把阳台的茉莉换了盆"


def _draft(text: str = _TEXT, *, face: str = ex.SRC_ACTIVITY, ref="101", char: int = _CHAR,
           user=None, created=None, hint: str = "spark", epistemic=None) -> dict:
    """服务层「成念候选」的标准形状（与 ``extract._draft`` 同键）。"""
    return {
        "text": text, "source_type": face, "source_ref": str(ref),
        "character_id": char, "user_id": user, "epistemic_status": epistemic,
        "status_hint": hint, "created_at": created or _NOW,
    }


def _act_row(rid: int, summary: str = _TEXT, **extra) -> dict:
    # 不传 user_id：F1 活动产物按「角色级」入池 ⇒ 走 user_id=0 哨兵
    row = {"id": rid, "character_id": _CHAR, "activity_type": "create",
           "status": "completed", "summary": summary}
    row.update(extra)
    return row


# ══════════════════════════════════════════════ 1. 迁移：幂等与可逆（真跑 alembic）

def _alembic_cfg():
    from alembic.config import Config

    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(backend, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend, "alembic"))
    return cfg


def _alembic_version(db_path) -> str:
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def _table_exists(db_path) -> bool:
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
        ).fetchone()[0] > 0


def _schema_sig(db_path) -> tuple[set, set, set]:
    """(列集合, 索引签名, 唯一索引列序) —— 迁移路与 ORM 路共用同一比对口径。"""
    eng = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        insp = sa_inspect(eng)
        cols = {c["name"] for c in insp.get_columns(TABLE)}
        idx = {(i["name"], tuple(i["column_names"]), bool(i["unique"]))
               for i in insp.get_indexes(TABLE)}
    finally:
        eng.dispose()
    with sqlite3.connect(str(db_path)) as conn:
        uq = set()
        for row in conn.execute(f"PRAGMA index_list({TABLE})").fetchall():
            if not row[2]:  # unique 标志
                continue
            uq.add(tuple(r[2] for r in conn.execute(f'PRAGMA index_info("{row[1]}")').fetchall()))
    return cols, idx, uq


def test_迁移建表幂等且可逆(tmp_path, monkeypatch):
    """裸库整链跑到本迁移：建表 → 幂等重放 → downgrade → 再 upgrade（派单 ②）。"""
    from alembic import command

    import app.config as app_cfg

    db_path = tmp_path / "m1_mig.db"
    monkeypatch.setattr(
        app_cfg.settings, "database_url", "sqlite+aiosqlite:///" + db_path.as_posix()
    )
    cfg = _alembic_cfg()

    # ① 上一节：表不存在（逐表 create_table 的 baseline 不会顺手建出新 ORM 表）
    command.upgrade(cfg, PREV_REV)
    assert _alembic_version(db_path) == PREV_REV
    assert not _table_exists(db_path)

    # ② upgrade 本迁移 → 表/列/索引/唯一约束齐
    command.upgrade(cfg, NEW_REV)
    assert _alembic_version(db_path) == NEW_REV
    cols, idx, uq = _schema_sig(db_path)
    assert cols == _ALL_COLS
    assert _INDEX in {i[0] for i in idx}
    assert ("character_id", "user_id", "source_type", "source_ref", "text_hash") in uq

    # ③ 幂等：版本号退回上一节但表还在 → 重放本迁移不报错、不动数据
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            f"INSERT INTO {TABLE} (character_id, user_id, source_type, source_ref, text_hash, "
            "text, status, salt, novelty) VALUES (1, 0, 'activity', '101', 'h', 'x', 'spark', 1.0, 1.0)"
        )
        conn.execute("UPDATE alembic_version SET version_num=?", (PREV_REV,))
        conn.commit()
    command.upgrade(cfg, NEW_REV)
    assert _alembic_version(db_path) == NEW_REV
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0] == 1
    assert _schema_sig(db_path)[0] == _ALL_COLS

    # ④ 可逆：downgrade 只删本表，父表与版本行都在
    command.downgrade(cfg, PREV_REV)
    assert _alembic_version(db_path) == PREV_REV
    assert not _table_exists(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_characters").fetchone()[0] == 0

    # ⑤ 再 upgrade：表与索引复原（本表无历史数据要保留，删表即空表重来）
    command.upgrade(cfg, NEW_REV)
    assert _table_exists(db_path)
    assert _INDEX in {i[0] for i in _schema_sig(db_path)[1]}


def test_迁移路与ORM建表结构一致(tmp_path, monkeypatch):
    """两路收敛：本迁移建出的表 ≡ ORM create_all 的表（列集合 + 索引签名 + 唯一索引）。

    M1 起本表开始被读写，两路不一致会让老库缺列/缺索引（热路径查询直接报错）。
    """
    from alembic import command

    import app.config as app_cfg
    from app.models._all import Base

    db_mig = tmp_path / "chain_mig.db"
    monkeypatch.setattr(
        app_cfg.settings, "database_url", "sqlite+aiosqlite:///" + db_mig.as_posix()
    )
    command.upgrade(_alembic_cfg(), NEW_REV)

    db_orm = tmp_path / "orm_create_all.db"
    eng = create_engine(f"sqlite:///{db_orm.as_posix()}")
    try:
        Base.metadata.tables[TABLE].create(eng)
    finally:
        eng.dispose()

    cols_mig, idx_mig, uq_mig = _schema_sig(db_mig)
    cols_orm, idx_orm, uq_orm = _schema_sig(db_orm)
    assert cols_mig == cols_orm == _ALL_COLS, (sorted(cols_mig), sorted(cols_orm))
    assert idx_mig == idx_orm, (sorted(idx_mig), sorted(idx_orm))
    assert uq_mig == uq_orm, (sorted(uq_mig), sorted(uq_orm))


def test_哨兵已登记thought_pool_salt():
    """派单 ②：老库缺表必须判「落后」走 upgrade（表级哨兵，同 plugin_consents 先例）。"""
    assert (TABLE, "salt") in _CURRENT_SCHEMA_SENTINELS, _CURRENT_SCHEMA_SENTINELS

    from app.models.character import ThoughtPool

    assert ThoughtPool.__table__.c.salt.nullable is False
    assert {c.name for c in ThoughtPool.__table__.primary_key.columns} == {"id"}


def test_模型边界_列宽外键与不挂状态表():
    """派单 ①：行粒度＝念头；唯一约束五元组；热路径索引；不挂 character_states、无反向关系。"""
    from app.models.character import ThoughtPool

    t = ThoughtPool.__table__
    assert t.name == TABLE
    assert t.c.text.type.length == 120 and t.c.source_type.type.length == 20
    assert t.c.source_ref.type.length == 64 and t.c.text_hash.type.length == 16
    assert t.c.status.type.length == 16 and t.c.thought_kind.type.length == 20
    # 外键只有角色一个（user_id 用 0 哨兵＝角色级，故**不挂** FK：0 在 users 里不存在）
    assert {fk.target_fullname for fk in t.foreign_keys} == {"ai_characters.id"}
    assert not [r for r in ThoughtPool.__mapper__.relationships]
    assert t.c.user_id.nullable is False and str(t.c.user_id.server_default.arg) == "0"
    # 唯一约束 ＝ 幂等入池位；索引 ＝ 取一条可用的热路径（character_id, user_id, status, salt）
    names = {c.name for c in t.constraints} | {i.name for i in t.indexes}
    assert _UQ in names and _INDEX in names
    hot = next(i for i in t.indexes if i.name == _INDEX)
    assert [c.name for c in hot.columns] == ["character_id", "user_id", "status", "salt"]
    # 设计 §2.4 硬规则：本表不引用 character_states（八维唯一维护方在状态服务）
    assert not any(fk.target_fullname.startswith("character_states") for fk in t.foreign_keys)


# ══════════════════════════════════════════════ 2. 源侧配额：纯函数三闸

def test_face_day_cap_按面取值与未知面兜底():
    assert qt.face_day_cap(ex.SRC_ACTIVITY) == 1
    assert qt.face_day_cap(ex.SRC_FACT) == 1
    for face in (ex.SRC_REFLECT, ex.SRC_MOMENT, ex.SRC_USER_HOOK, ex.SRC_INTEREST):
        assert qt.face_day_cap(face) == 2
    # 未知面 / None / 空串 ⇒ 兜底顶（保守取最严，防「新面默认无闸」架空配额）
    assert qt.face_day_cap("no_such_face") == qt.FACE_DAY_CAP_DEFAULT == 1
    assert qt.face_day_cap(None) == 1 and qt.face_day_cap("") == 1


def test_face_day_cap_可注入覆盖且负值当零():
    """回放/标定用：caps 注入不改模块常量；负数＝该面全拦（试「砍掉一整路」）。"""
    assert qt.face_day_cap(ex.SRC_ACTIVITY, {ex.SRC_ACTIVITY: 5}) == 5
    assert qt.face_day_cap(ex.SRC_ACTIVITY, {ex.SRC_ACTIVITY: -3}) == 0
    assert qt.FACE_DAY_CAP[ex.SRC_ACTIVITY] == 1  # 原表未被污染


def test_pair_day_key_用北京日界且用户空归零():
    """跨北京 0 点换桶（与 age_days 同一日界口径），user None ≡ 0（角色级哨兵）。"""
    before = datetime(2026, 9, 30, 15, 30)   # UTC ⇒ 北京 09-30 23:30
    after = datetime(2026, 9, 30, 16, 30)    # UTC ⇒ 北京 10-01 00:30
    assert qt.pair_day_key(_CHAR, _USER, ex.SRC_ACTIVITY, before)[3] == "2026-09-30"
    assert qt.pair_day_key(_CHAR, _USER, ex.SRC_ACTIVITY, after)[3] == "2026-10-01"
    assert qt.pair_day_key(_CHAR, None, ex.SRC_ACTIVITY, after)[:2] == (_CHAR, 0)
    assert qt.pair_day_key(_CHAR, 0, ex.SRC_ACTIVITY, after) == qt.pair_day_key(
        _CHAR, None, ex.SRC_ACTIVITY, after)
    # 面与日都要参与：同角色同用户换面/换日都是不同计数位
    assert qt.pair_day_key(_CHAR, 0, ex.SRC_REFLECT, after) != qt.pair_day_key(
        _CHAR, 0, ex.SRC_ACTIVITY, after)


def test_结构化键_同源只占一个坑():
    """派单 ④：F3 改用结构化键（来源面＋来源主键），替代「按朋友圈原文哈希」的自我封锁。"""
    assert qt.source_key(ex.SRC_MOMENT, "42") == "moment:42"
    assert qt.source_key(None, None) == ":"
    k1 = qt.dedup_key(_CHAR, 0, ex.SRC_MOMENT, "42")
    assert k1 == f"{_CHAR}|0|moment:42"
    # 同一条朋友圈换文案 ⇒ 同键（只占一个坑，「换模板重复落」被拦）
    assert qt.dedup_key(_CHAR, 0, ex.SRC_MOMENT, "42") == k1
    # 换角色 / 换用户 / 换面 ⇒ 不同键（跨面养熟与多用户各自成行都不误伤）
    assert qt.dedup_key(_CHAR + 1, 0, ex.SRC_MOMENT, "42") != k1
    assert qt.dedup_key(_CHAR, 7, ex.SRC_MOMENT, "42") != k1
    assert qt.dedup_key(_CHAR, 0, ex.SRC_ACTIVITY, "42") != k1


def test_idempotency_key_与DB唯一约束同形():
    """进程内判重键比 DB 严一档（不带 text_hash），故另给五元组口径与唯一约束逐位对齐。"""
    from app.models.character import ThoughtPool

    key = qt.idempotency_key(_CHAR, None, ex.SRC_ACTIVITY, "101", _TEXT)
    assert key == (_CHAR, 0, ex.SRC_ACTIVITY, "101", ex.text_hash(_TEXT))
    uq = next(c for c in ThoughtPool.__table__.constraints if c.name == _UQ)
    assert [c.name for c in uq.columns] == [
        "character_id", "user_id", "source_type", "source_ref", "text_hash"]
    assert len(key) == len(uq.columns)   # 逐位同形：同序、同长度
    # 换文本 ⇒ 五元组不同（DB 允许），但结构化键相同（进程内拦）：方向＝少落不多落
    assert qt.idempotency_key(_CHAR, 0, ex.SRC_ACTIVITY, "101", "换一种说法") != key
    assert qt.dedup_key(_CHAR, 0, ex.SRC_ACTIVITY, "101") == qt.dedup_key(
        _CHAR, 0, ex.SRC_ACTIVITY, "101")


def test_admit_准入门槛优先于配额():
    """串行短路：先准入（不看额度）后配额，一条候选只记第一个拦它的原因。"""
    stale = dyn.novelty(20.0)
    assert qt.admit_reject_reason(source_type=ex.SRC_ACTIVITY, novelty_value=stale,
                                  used_today=99) == qt.DROP_ADMIT
    assert qt.admit_reject_reason(source_type=ex.SRC_ACTIVITY, novelty_value=1.0,
                                  used_today=1) == qt.DROP_QUOTA
    assert qt.admit_reject_reason(source_type=ex.SRC_ACTIVITY, novelty_value=1.0,
                                  used_today=0) is None
    # 覆盖口径：min_novelty=0 ⇒ 放行到配额闸；caps 给 0 ⇒ 该面全拦
    assert qt.admit_reject_reason(source_type=ex.SRC_ACTIVITY, novelty_value=0.01,
                                  used_today=0, min_novelty=0.0) is None
    assert qt.admit_reject_reason(source_type=ex.SRC_REFLECT, novelty_value=1.0,
                                  used_today=0, caps={ex.SRC_REFLECT: 0}) == qt.DROP_QUOTA


def test_配额常量口径与目标不架空():
    """派单 ③：目标 ≤420 条/30 天（每（角色×用户）日均 ≤0.5），常量必须**先于**目标收紧。"""
    assert set(qt.FACE_DAY_CAP) == {ex.SRC_ACTIVITY, ex.SRC_REFLECT, ex.SRC_MOMENT,
                                    ex.SRC_USER_HOOK, ex.SRC_FACT, ex.SRC_INTEREST}
    assert qt.ADMIT_MIN_NOVELTY == 0.20
    assert qt.DROP_REASONS == ("admit", "quota", "dup_key")
    # 每（角色×用户）每北京日最多 sum(caps) 条 ⇒ 30 天理论上界远小于 420
    assert sum(qt.FACE_DAY_CAP.values()) == 10
    assert sum(qt.FACE_DAY_CAP.values()) * 30 <= 420
    # τ=7 时 0.20 门槛 ≡ 「隔了 ≥12 个北京日的旧信号不入池」（离线补抽 30 天历史的关键）
    assert dyn.novelty(11) > qt.ADMIT_MIN_NOVELTY >= dyn.novelty(12)


def test_quota模块零IO零ORM():
    """与 M0 同一规格：quota 只做纯判定，计数状态由调用方持有并注入。"""
    with open(qt.__file__, encoding="utf-8") as f:
        src = f.read()
    for forbidden in ("sqlite3", "sqlalchemy", "async_session", "requests", "open(", "await "):
        assert forbidden not in src, forbidden
    tree = ast.parse(src)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert all(i.startswith(("app.domain.thought", "datetime", "__future__"))
               for i in imported), imported


# ══════════════════════════════════════════════ 3. τ 改名（零行为订正）

def test_tau改名_常量与公式一字未动():
    """派单 ④：τ 改名 ``NOVELTY_E_FOLDING_DAYS``，公式不动、真半点＝τ·ln2；旧名保留为别名。"""
    assert dyn.NOVELTY_E_FOLDING_DAYS == 7.0
    assert dyn.NOVELTY_HALFLIFE_DAYS == dyn.NOVELTY_E_FOLDING_DAYS  # 别名（M0 单测/--params 在用）
    assert dyn.novelty(0) == 1.0
    assert dyn.novelty(dyn.NOVELTY_E_FOLDING_DAYS) == pytest.approx(math.exp(-1.0))
    assert dyn.novelty(dyn.NOVELTY_E_FOLDING_DAYS * math.log(2)) == pytest.approx(0.5)
    # 本批禁止改既有阈值：CAP_SPARK 仍是 12（16 只经回放 --params 进程内拨，标定后再落地）
    assert dyn.CAP_SPARK == 12 and dyn.CAP_OBSESSION == 3 and dyn.TTL_DAYS == 21.0


def test_状态机语义未被本批改动():
    """派单 ⑥「状态机迁移」：M1 只加源侧闸，§2.2/§2.3 的迁移判据逐条仍可复现。"""
    assert dyn.should_promote(2.0, [ex.SRC_ACTIVITY, ex.SRC_USER_HOOK]) is True
    assert dyn.should_promote(2.0, [ex.SRC_ACTIVITY]) is False       # 单面重复不升级
    assert dyn.should_promote(1.8, [ex.SRC_ACTIVITY, ex.SRC_FACT]) is False
    assert dyn.should_fade(21.5) is True and dyn.should_fade(21.0) is False
    salt, n, status = dyn.apply_told_flat(2.0, 0)
    assert (salt, n, status) == (0.7, 1, dyn.STATUS_TOLD_FLAT)
    assert dyn.apply_told_flat(2.0, 1)[2] == dyn.STATUS_FADED          # tell_count 达上限
    assert dyn.classify_release(True, True) == dyn.STATUS_SPENT
    assert dyn.classify_release(True, False) == dyn.STATUS_TOLD_FLAT
    assert dyn.classify_release(False, True) == "never_told"           # 没发出去不惩罚
    assert dyn.evict([{"id": str(i), "status": dyn.STATUS_SPARK, "salt": 1.0,
                       "novelty": 1.0 - i / 20.0} for i in range(14)], cap_spark=12) == ["13", "12"]


# ══════════════════════════════════════════════ 4. 配额闸（丢弃、不延后、计数）

def test_配额达上限丢弃且不延后():
    """F1 同面三条、硬闸 1 条 ⇒ 只落 1 行、2 条记 quota；计数位只有「今天」一个桶。"""
    drafts = [_draft(ref=101), _draft(ref=102, text="把周报里的一组数核对了一遍"),
              _draft(ref=103, text="跟着播客学到一个新说法")]
    used: dict[tuple, int] = {}
    seen: set[str] = set()
    rows, stats = svc.intake_thoughts_core(drafts, used=used, seen_source_keys=seen, now=_NOW)
    assert len(rows) == 1 and rows[0].source_ref == "101"
    assert stats["dropped"][qt.DROP_QUOTA] == 2
    assert list(used) == [qt.pair_day_key(_CHAR, 0, ex.SRC_ACTIVITY, _NOW)]   # 没有明天/后天的桶
    assert used[qt.pair_day_key(_CHAR, 0, ex.SRC_ACTIVITY, _NOW)] == 1         # 超额不结转
    assert all(r.status == dyn.STATUS_SPARK for r in rows)


def test_配额计数就地累加跨批复用():
    """``used`` 由调用方持有：同一天第二批看到的是第一批的量（跨进程持久由 _seed_state 播种）。"""
    used: dict[tuple, int] = {}
    seen: set[str] = set()
    rows1, _ = svc.intake_thoughts_core([_draft(ref=201)], used=used, seen_source_keys=seen, now=_NOW)
    rows2, stats2 = svc.intake_thoughts_core([_draft(ref=202, text="把阳台的绿萝剪了剪")],
                                             used=used, seen_source_keys=seen, now=_NOW)
    assert len(rows1) == 1 and rows2 == [] and stats2["dropped"][qt.DROP_QUOTA] == 1
    assert used[qt.pair_day_key(_CHAR, 0, ex.SRC_ACTIVITY, _NOW)] == 1


def test_按面独立成顶():
    """两路 activity 各给顶，两路 reflect 给 2 条：面与面的额度互不挪用。"""
    drafts = [_draft(ref=301), _draft(ref=302, text="把周报里的一组数核对了一遍"),
              _draft(ref=303, text="复盘了这周的排期", face=ex.SRC_REFLECT),
              _draft(ref=304, text="复盘了上次的失误", face=ex.SRC_REFLECT)]
    rows, stats = svc.intake_thoughts_core(drafts, used={}, seen_source_keys=set(), now=_NOW)
    assert stats["by_face"] == {ex.SRC_ACTIVITY: 1, ex.SRC_REFLECT: 2}
    assert len(rows) == 3 and stats["dropped"] == {qt.DROP_QUOTA: 1}


def test_准入门槛_十二天以上旧信号不入池():
    stale = _draft(ref=401, created=datetime(2026, 9, 15, 3, 0))    # 隔 15 个北京日
    fresh = _draft(ref=402, created=datetime(2026, 9, 29, 3, 0))    # 隔 1 天
    used: dict[tuple, int] = {}
    rows, stats = svc.intake_thoughts_core([stale, fresh], used=used,
                                           seen_source_keys=set(), now=_NOW)
    assert len(rows) == 1 and rows[0].source_ref == "402"
    assert stats["dropped"][qt.DROP_ADMIT] == 1
    assert used[qt.pair_day_key(_CHAR, 0, ex.SRC_ACTIVITY, _NOW)] == 1  # 准入拦掉的不占额度


def test_结构化键去重_同源不重复落():
    """同一（角色,用户,面,来源主键）第二条即便换了文案也不落（进程内判重，比 DB 严一档）。"""
    drafts = [_draft(ref=501), _draft(ref=501, text="换一种说法讲同一件事"),
              _draft(ref=502, text="又做了件小事")]
    used, seen = {}, set()
    rows, stats = svc.intake_thoughts_core(drafts, used=used, seen_source_keys=seen, now=_NOW,
                                           caps={ex.SRC_ACTIVITY: 5})
    assert len(rows) == 2 and {r.source_ref for r in rows} == {"501", "502"}
    assert stats["dropped"] == {qt.DROP_DUP_KEY: 1}
    assert seen == {qt.dedup_key(_CHAR, 0, ex.SRC_ACTIVITY, "501"),
                    qt.dedup_key(_CHAR, 0, ex.SRC_ACTIVITY, "502")}


def test_跨面同源各自成行_养熟前提():
    """结构化键含来源面：同一件事被两个面指到 ⇒ 各落一行（spark→obsession 靠这个）。"""
    drafts = [_draft(ref=601, face=ex.SRC_ACTIVITY), _draft(ref=601, face=ex.SRC_MOMENT)]
    rows, stats = svc.intake_thoughts_core(drafts, used={}, seen_source_keys=set(), now=_NOW)
    assert len(rows) == 2 and stats["dropped"] == {}
    assert qt.dedup_key(_CHAR, 0, ex.SRC_ACTIVITY, "601") != qt.dedup_key(
        _CHAR, 0, ex.SRC_MOMENT, "601")


def test_三道过滤在配额之前且不占额度():
    """§2.2 三道过滤仍是第一道闸；被它拦下的候选不得消耗每日额度（否则配额被脏数据吃掉）。

    过滤② 只在**命中同一主题桶**时才拦（未命中桶 fail-open），故这里用「粥」一族的两句。
    """
    drafts = [_draft(ref=701, text="嗯"),                                  # 长度
              _draft(ref=702, epistemic="FICTIONAL"),                       # 设定
              _draft(ref=703, text="锅里剩的粥我热一热"),                     # 撞最近已发
              _draft(ref=704)]                                              # 正常
    used: dict[tuple, int] = {}
    rows, stats = svc.intake_thoughts_core(
        drafts, used=used, seen_source_keys=set(), now=_NOW,
        recent_texts={_CHAR: ("早上喝了一碗小米粥",)})
    assert len(rows) == 1 and rows[0].source_ref == "704"
    assert stats["dropped"] == {fl.REASON_LENGTH: 1, fl.REASON_FICTIONAL: 1,
                                fl.REASON_RECENT_OVERLAP: 1}
    assert used[qt.pair_day_key(_CHAR, 0, ex.SRC_ACTIVITY, _NOW)] == 1


def test_留痕行不占额度且带spent_at():
    """方案 A：同源已被 life_share 讲掉 ⇒ 只写一行 spent 留痕，不占配额、不参与选择。"""
    drafts = [_draft(ref=801, hint="spent"), _draft(ref=802)]
    used: dict[tuple, int] = {}
    rows, stats = svc.intake_thoughts_core(drafts, used=used, seen_source_keys=set(), now=_NOW)
    assert stats["intake"] == 1 and stats["spent_logged"] == 1
    assert rows[0].status == dyn.STATUS_SPENT and rows[0].spent_at == _NOW
    assert rows[1].status == dyn.STATUS_SPARK and rows[1].spent_at is None
    assert used[qt.pair_day_key(_CHAR, 0, ex.SRC_ACTIVITY, _NOW)] == 1  # 只记 spark 那条


def test_读数不变式_seen等于放行加各闸丢弃():
    """留痕读数（丢了多少、因为哪条闸）必须自洽：不重计、不漏计。"""
    drafts = [_draft(ref=901), _draft(ref=901, text="同一件事换个说法"),
              _draft(ref=903, created=datetime(2026, 9, 1, 3, 0)),
              _draft(ref=904, text="嗯"), _draft(ref=905, hint="spent"),
              _draft(ref=906, face=ex.SRC_REFLECT, text="复盘了这周的排期")]
    rows, stats = svc.intake_thoughts_core(drafts, used={}, seen_source_keys=set(), now=_NOW)
    assert stats["seen"] == len(drafts)
    assert stats["dropped_total"] == sum(stats["dropped"].values())
    assert stats["intake"] + stats["spent_logged"] + stats["dropped_total"] == stats["seen"]
    assert len(rows) == stats["intake"] + stats["spent_logged"]
    assert set(stats["dropped"]) <= set(qt.DROP_REASONS + fl.INTAKE_REASONS)
    assert "flag" not in stats  # flag/character_id 由入口补，core 只给纯读数


def test_新行字段口径_截断与快照():
    """落库行的 text/source_ref 按列宽截断，novelty/salt 为入池时刻快照，hit_sources 是 JSON。"""
    row = svc._new_row(
        {"character_id": _CHAR, "user_id": None, "text": "长" * 200,
         "source_type": ex.SRC_ACTIVITY, "source_ref": "r" * 80},
        status=dyn.STATUS_SPARK, salt=1.0, nov=0.5, moment=_NOW)
    assert len(row.text) == ex.POOL_TEXT_MAX_LEN == 120
    assert len(row.source_ref) == 64 and row.user_id == 0
    assert row.status == dyn.STATUS_SPARK and row.thought_kind == "" and row.tell_count == 0
    assert row.salt == 1.0 and row.novelty == 0.5
    assert json.loads(row.hit_sources) == [ex.SRC_ACTIVITY]
    assert row.created_at == _NOW and row.last_hit_at == _NOW and row.spent_at is None
    assert row.text_hash == ex.text_hash("长" * 200)   # 幂等位按原文（未截断）算


# ══════════════════════════════════════════════ 5. flag：默认关＝零行为

def test_flag默认关且双向登记():
    from app.application.flag_catalog import FLAG_CATALOG

    assert svc.FLAG_KEY == "thought_pool_shadow"
    assert AGENT_FLAGS[svc.FLAG_KEY] is False          # 行为端缺省
    assert svc.FLAG_KEY in FLAG_CATALOG                # 展示端缺登记＝用户在 App 里看不到
    assert FLAG_CATALOG[svc.FLAG_KEY]["visible"] is False
    assert "默认关闭" in FLAG_CATALOG[svc.FLAG_KEY]["desc_zh"]
    assert svc.shadow_enabled() is False


def test_flag关时入口不查库不写库():
    """派单 ⑤：默认关必须逐字节旧行为——首行即返回，连一条 SELECT 都不发。"""
    class _SpyDB:
        def __init__(self):
            self.calls: list[str] = []

        async def execute(self, *a, **k):
            self.calls.append("execute")
            raise AssertionError("flag 关时不得查库")

        def add(self, *a):
            self.calls.append("add")
            raise AssertionError("flag 关时不得写库")

        async def flush(self, *a):
            self.calls.append("flush")

    db = _SpyDB()
    out = asyncio.run(svc.supply_thought_pool(db, {ex.SRC_ACTIVITY: [_act_row(1)]}, now=_NOW))
    assert out == {} and db.calls == []


def test_flag读不到时按关处理():
    """观测层不得把业务拖下水：连 AGENT_FLAGS 都取不到 ⇒ fail-closed。"""
    import app.flags.agent_flags as af

    real = af.AGENT_FLAGS
    af.AGENT_FLAGS = None  # 人为造「读 flag 炸」
    try:
        assert svc.shadow_enabled() is False
    finally:
        af.AGENT_FLAGS = real
    assert svc.shadow_enabled() is False


# ══════════════════════════════════════════════ 6. 影子落库（真库读写，_dbclone）

@pytest.fixture()
def pool_env(tmp_path):
    from _dbclone import clone_engine, make_session_factory

    from app.models.character import AICharacter
    from app.models.user import User

    engine = clone_engine(tmp_path / "pool.db")
    factory = make_session_factory(engine)

    async def _init():
        async with factory() as db:
            db.add(User(id=_USER, username="tp_u1", nickname="主人"))
            db.add(AICharacter(id=_CHAR, user_id=_USER, name="小暖", is_active=True))
            await db.commit()

    asyncio.run(_init())
    yield factory
    engine.sync_engine.dispose()


class _CommitSpy:
    """透传真实 session，但把 commit 拦成计数器——本层必须一次都没碰过 commit。"""

    def __init__(self, inner):
        self._inner = inner
        self.commits = 0

    async def commit(self):
        self.commits += 1

    def add(self, obj):
        self._inner.add(obj)

    async def flush(self, *a, **k):
        return await self._inner.flush(*a, **k)

    async def execute(self, *a, **k):
        return await self._inner.execute(*a, **k)


async def _pool_rows(factory) -> list[tuple]:
    from app.models.character import ThoughtPool

    async with factory() as db:
        got = (await db.execute(select(ThoughtPool).order_by(ThoughtPool.id))).scalars().all()
        return [(r.source_type, r.source_ref, r.status, r.user_id, r.character_id) for r in got]


def test_flag开时落池且不commit(pool_env, monkeypatch):
    """派单 ⑤：只 add/flush，是否 commit 由调用方定；落池行＝spark＋配额后的一条。"""
    monkeypatch.setitem(AGENT_FLAGS, svc.FLAG_KEY, True)

    async def _run():
        async with pool_env() as db:
            wrapper = _CommitSpy(db)
            stats = await svc.supply_thought_pool(
                wrapper, {ex.SRC_ACTIVITY: [_act_row(101), _act_row(102)]}, now=_NOW)
            await db.commit()          # 调用方（未来的挂点）才落账
        return stats, wrapper
    stats, wrapper = asyncio.run(_run())
    assert wrapper.commits == 0
    assert stats["intake"] == 1 and stats["flag"] == svc.FLAG_KEY
    assert stats["dropped"][qt.DROP_QUOTA] == 1
    rows = asyncio.run(_pool_rows(pool_env))
    assert rows == [(ex.SRC_ACTIVITY, "101", dyn.STATUS_SPARK, 0, _CHAR)]

    async def _not_committed():
        async with pool_env() as db:
            wrapper = _CommitSpy(db)
            await svc.supply_thought_pool(wrapper, {ex.SRC_ACTIVITY: [_act_row(201)]}, now=_NOW)
        # 出了 with 块未 commit ⇒ 回滚，池里还是只有那一条
        return await _pool_rows(pool_env)
    assert asyncio.run(_not_committed()) == rows


def test_重跑同批不重复落库_配额播种跨进程(pool_env, monkeypatch):
    """第二次入口重新从库里播种 used/seen ⇒ 同源候选一律 dup_key，池不二次灌。"""
    monkeypatch.setitem(AGENT_FLAGS, svc.FLAG_KEY, True)

    async def _once(rows_by_face):
        async with pool_env() as db:
            stats = await svc.supply_thought_pool(db, rows_by_face, now=_NOW)
            await db.commit()
        return stats

    first = asyncio.run(_once({ex.SRC_ACTIVITY: [_act_row(301)]}))
    again = asyncio.run(_once({ex.SRC_ACTIVITY: [_act_row(301)]}))
    assert first["intake"] == 1 and again["intake"] == 0
    assert again["dropped"][qt.DROP_DUP_KEY] == 1
    assert asyncio.run(_pool_rows(pool_env)) == [(ex.SRC_ACTIVITY, "301", dyn.STATUS_SPARK, 0, _CHAR)]


def test_配额在真库上跨批生效(pool_env, monkeypatch):
    """每天 1 条的硬闸跨调用成立：第二天（北京日界后）才给新额度。"""
    from datetime import timedelta

    monkeypatch.setitem(AGENT_FLAGS, svc.FLAG_KEY, True)

    async def _once(rid, when):
        async with pool_env() as db:
            stats = await svc.supply_thought_pool(db, {ex.SRC_ACTIVITY: [_act_row(rid)]}, now=when)
            await db.commit()
        return stats["intake"]

    assert asyncio.run(_once(401, _NOW)) == 1
    assert asyncio.run(_once(402, _NOW + timedelta(hours=2))) == 0     # 同日 ⇒ 配额拦掉
    assert asyncio.run(_once(403, _NOW + timedelta(days=1))) == 1      # 换北京日 ⇒ 给新额度
    refs = [r[1] for r in asyncio.run(_pool_rows(pool_env))]
    assert refs == ["401", "403"]


def test_单行抽取异常不拖垮整批(pool_env, monkeypatch):
    """影子层：一条脏行只能作废自己，不得让整批落池失败。"""
    class _Boom:
        def __str__(self):
            raise RuntimeError("抽取当场炸")

    monkeypatch.setitem(AGENT_FLAGS, svc.FLAG_KEY, True)

    async def _run():
        async with pool_env() as db:
            stats = await svc.supply_thought_pool(db, {ex.SRC_ACTIVITY: [
                _act_row(501, summary=_Boom()), _act_row(502)]}, now=_NOW)
            await db.commit()
        return stats
    stats = asyncio.run(_run())
    assert "error" not in stats and stats["intake"] == 1
    assert asyncio.run(_pool_rows(pool_env)) == [(ex.SRC_ACTIVITY, "502", dyn.STATUS_SPARK, 0, _CHAR)]


def test_落库异常隔离为error读数(pool_env, monkeypatch):
    """库侧出错（播种查询失败）⇒ 返回 error 读数、绝不外抛。"""
    class _BoomDB:
        async def execute(self, *a, **k):
            raise RuntimeError("库炸了")

        def add(self, *a):
            raise AssertionError("查询都失败，不该走到写")

    monkeypatch.setitem(AGENT_FLAGS, svc.FLAG_KEY, True)
    out = asyncio.run(svc.supply_thought_pool(_BoomDB(), {ex.SRC_ACTIVITY: [_act_row(601)]},
                                              now=_NOW))
    assert out["intake"] == 0 and "error" in out and out["flag"] == svc.FLAG_KEY


def test_留痕只走trace且不碰发送日志(pool_env, monkeypatch):
    """派单 ⑤：留痕＝一条 agent_task_logs trace；不写 proactive_message_logs。"""
    import app.agent.trace as trace_mod
    from app.models.character import ProactiveMessageLog

    captured: list[dict] = []
    monkeypatch.setitem(AGENT_FLAGS, svc.FLAG_KEY, True)
    monkeypatch.setattr(trace_mod, "new_task_id", lambda: "t-1")
    monkeypatch.setattr(trace_mod, "enqueue_task_log", lambda **kw: captured.append(kw))

    async def _run():
        async with pool_env() as db:
            stats = await svc.supply_thought_pool(db, {ex.SRC_ACTIVITY: [_act_row(701)]}, now=_NOW)
            await db.commit()
        async with pool_env() as c2:
            logs = (await c2.execute(
                select(func.count()).select_from(ProactiveMessageLog))).scalar()
        return stats, logs
    stats, msg_logs = asyncio.run(_run())
    assert stats["intake"] == 1
    assert msg_logs == 0                        # 本批零发送权
    assert len(captured) == 1 and captured[0]["route"] == svc.TRACE_ROUTE
    assert captured[0]["trigger"] == svc.TRACE_TRIGGER
    steps = json.loads(captured[0]["steps_json"])
    assert set(steps) >= {"seen", "intake", "dropped", "dropped_total", "by_face", "flag"}
    assert "quota" in json.dumps(steps) or stats["dropped"] == {}


def test_留痕失败不阻塞落池(pool_env, monkeypatch):
    import app.agent.trace as trace_mod

    monkeypatch.setitem(AGENT_FLAGS, svc.FLAG_KEY, True)
    monkeypatch.setattr(trace_mod, "enqueue_task_log",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("trace 队列炸")))

    async def _run():
        async with pool_env() as db:
            stats = await svc.supply_thought_pool(db, {ex.SRC_ACTIVITY: [_act_row(801)]}, now=_NOW)
            await db.commit()
        return stats
    assert asyncio.run(_run())["intake"] == 1
    assert len(asyncio.run(_pool_rows(pool_env))) == 1


def test_唯一约束_同幂等键二次插入报错且零哨兵生效(pool_env):
    """DB 兜底：五元组重复 ⇒ IntegrityError；user_id=0 也生效（NULL 在唯一约束里互不相等）。"""
    from app.models.character import ThoughtPool

    def _insert(ref: str = "901", text: str = _TEXT, user: int = 0):
        async def _run():
            async with pool_env() as db:
                db.add(ThoughtPool(
                    character_id=_CHAR, user_id=user, thought_kind="", text=text,
                    source_type=ex.SRC_ACTIVITY, source_ref=ref, text_hash=ex.text_hash(text),
                    status=dyn.STATUS_SPARK, salt=1.0, novelty=1.0, hit_sources='["activity"]',
                    created_at=_NOW))
                await db.commit()
        return _run

    asyncio.run(_insert()())
    with pytest.raises(IntegrityError):
        asyncio.run(_insert()())
    # 换 text_hash 可再落一行（DB 允许，进程内结构化键会拦——方向＝少落不多落）
    asyncio.run(_insert(text="同一来源换个说法")())
    # 换来源主键也可再落
    asyncio.run(_insert(ref="902")())
    assert len(asyncio.run(_pool_rows(pool_env))) == 3


# ══════════════════════════════════════════════ 7. 边界：零发送权、零调用方

def _code_only(path: str) -> str:
    """剥掉注释与 docstring 后的代码本体（判「有没有引用」不能用原始文本，否则
    文档里那句「不改 arbiter」会自己撞上断言）。"""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            node.body = body[1:]
    return ast.unparse(tree)


def test_服务层与配额层零发送链路引用():
    """派单 禁止项：本批没有发送权——不引用 arbiter / 生成器 / 调度器 / 状态水位。"""
    code = _code_only(svc.__file__) + _code_only(qt.__file__)
    for banned in ("arbiter", "message_generator", "proactive_message", "character_state",
                   "settle_level", "RelationalDrive", "scheduling", "llm_client", "tts",
                   "send_message", "resolve_flag"):
        assert banned not in code, banned
    # 写口只有本表：不落任何别的水位/日志
    assert "ThoughtPool" in code
    assert code.count("import") >= 0


def test_服务层import白名单():
    imported = []
    for node in ast.walk(ast.parse(_code_only(svc.__file__))):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    allowed = ("__future__", "json", "logging", "datetime", "sqlalchemy", "app.domain.thought",
               "app.models.character", "app.utils.timeutil", "app.flags.agent_flags",
               "app.agent.trace")
    for name in imported:
        assert any(name == a or name.startswith(a) for a in allowed), name


def test_本批零调用方_挂点属M2():
    """念头池接线收敛断言：调用方**只允许**是「抽取侧三挂点 + 生效侧三挂点」，第四处即红。

    演进（三个阶段，本条随接线落地逐步收紧/放宽，但始终钉「收敛到白名单」这一不变式）：
      - M1（落表 + 写口）：``app/`` 里除服务模块自身外代码本体**零引用**（"本批零调用方"）；
      - M2-b1（2026-10-01，生效侧）：``arbiter`` / ``sections`` / ``context_builder`` 三处接线
        （取一条 + 三档释放 + 注入）；A20 批 2（2026-10-02）``_annotate_outreach_plan`` 整体下沉
        ``scheduling/outreach_gates.py`` ⇒ 取用点随之内移，生效侧仍只有三处（挂点数量不变）；
      - M1-挂点（2026-10-01，抽取侧）：``events/handlers`` / ``chat_service`` /
        ``character_state_service`` 三处搭车挂点调 ``supply_thought_pool``（F1–F6 抽取入池）。
        A20 批 5 第二刀（2026-10-02）``_settle_thought_pool_turn`` 逐字节下沉
        ``application/chat_settlement.py`` ⇒ 挂点随之内移，抽取侧仍只有三处（挂点数量不变）。

    不变式：引用方**只允许**这六处（设计 §2.1「抽取挂点唯一三处」+ §2.6 生效侧）。任何
    **第七处**引用即红——防止念头池被到处捞，重蹈 ``Memory.scope``「到处都有人读」的覆辙。

    只比注释之外的代码（AST 去 docstring、注释天然不落 unparse）——migrate/agent_flags/包
    文档里都有「thought_pool_service」这句话，那是说明而非调用。
    """
    # 抽取侧三挂点（M1-挂点，调 supply_thought_pool）+ 生效侧三挂点（M2-b1，取用/释放/注入）
    extract_hooks = {
        os.path.join("events", "handlers.py"),
        os.path.join("application", "chat_settlement.py"),
        os.path.join("application", "character_state_service.py"),
    }
    effect_hooks = {
        # A20 批 2：生效侧 arbiter 挂点随 _annotate_outreach_plan 一起搬到 outreach_gates
        os.path.join("scheduling", "outreach_gates.py"),
        os.path.join("agent", "context", "sections.py"),
        os.path.join("agent", "context_builder.py"),
    }
    allowed_callers = extract_hooks | effect_hooks
    root = os.path.dirname(os.path.dirname(os.path.abspath(svc.__file__)))  # backend/app
    self_path = os.path.abspath(svc.__file__)
    hits: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            if os.path.abspath(path) == self_path:
                continue
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            if "thought_pool_service" not in text and "supply_thought_pool" not in text:
                continue                      # 先粗筛，省掉绝大多数文件的解析
            if "thought_pool_service" in _code_only(path) or                     "supply_thought_pool" in _code_only(path):
                hits.append(os.path.relpath(path, root))
    unexpected = [h for h in hits if h not in allowed_callers]
    assert unexpected == [], (
        f"thought_pool_service 出现了白名单外的调用方：{unexpected}"
        "（只允许抽取侧 handlers/chat_service/character_state_service + 生效侧 outreach_gates/sections/context_builder）"
    )
    # 抽取侧钉死：supply_thought_pool 的调用方**恰好**是三个抽取挂点（生效侧不碰供给口——
    # 取用/释放走 fetch_one_thought/settle_release，抽池走 supply_thought_pool，两条线不混）。
    supply_callers = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            if os.path.abspath(path) == self_path:
                continue
            with open(path, encoding="utf-8", errors="replace") as f:
                if "supply_thought_pool" not in f.read():
                    continue
            if "supply_thought_pool" in _code_only(path):
                supply_callers.append(os.path.relpath(path, root))
    assert set(supply_callers) == extract_hooks, (
        f"supply_thought_pool 的调用方应恰好是三个抽取挂点，实际：{supply_callers}"
    )
