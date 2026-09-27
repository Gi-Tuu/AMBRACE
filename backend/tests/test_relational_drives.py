# -*- coding: utf-8 -*-
"""A4 批 3 / T1 M1a（2026-09-27）：relational_drives 表 + 迁移 + 纯函数域单测。

被测三件（派单 M1a，零钩子、零行为）：
1. 模型 ``app/models/character.RelationalDrive``（新表，唯一约束 + 热路径索引）；
2. 迁移 ``a1c4e7f9b2d5``（has_table 守卫建表）与 ``_CURRENT_SCHEMA_SENTINELS`` 登记一条；
3. 纯函数域 ``app/domain/relational/drives.py``（增长/封顶/夜间倍率/释放/候选，零 IO）。

覆盖派单 8 条：迁移幂等可逆、settle 白天/跨北京日界/夜间倍率/封顶、游标幂等、时钟回拨、
last_settled_at=None、release_open/release_full、top_candidate_drive（不含 intimacy / 并列取序 /
全 0 ⇒ None）、唯一约束 IntegrityError。

口径与纪律：临时库一律 pytest ``tmp_path`` 私有 SQLite 文件；跑整链迁移那条**不用** ``_dbclone``
（模板库是 create_all 出的当前 schema，跑迁移会假绿），唯一约束那条用 ``_dbclone``（纯 ORM 读写、
不碰版本链）。全程不碰 backend/data 生产库，不调模型、不走网络。
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect as sa_inspect
from sqlalchemy.exc import IntegrityError

from app.db.migrate import _CURRENT_SCHEMA_SENTINELS
from app.domain.relational import drives as d
from app.utils.timeutil import shift_utc_naive

# 快测档：本文件含真跑整链 alembic 的重量级集成用例。
pytestmark = pytest.mark.slow

PREV_REV = "c6d7e8f9a0b1"   # 本迁移的 down_revision（当前 head；用显式 rev 防未来失配）
NEW_REV = "a1c4e7f9b2d5"
TABLE = "relational_drives"
_INDEX = "ix_relational_drives_char_user_level"
_ALL_COLS = {
    "id", "character_id", "user_id", "drive_key", "level",
    "last_settled_at", "last_released_at", "last_released_ratio", "created_at", "updated_at",
}


def _bj(hour: int, minute: int = 0, *, day: int = 2) -> datetime:
    """北京时间 hour:minute → naive UTC（项目口径：北京 = UTC+8，库内一律 naive UTC）。"""
    return shift_utc_naive(datetime(2026, 9, day, hour, minute), -8)


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
    """裸库整链跑到本迁移：建表 → 幂等重放 → downgrade → 再 upgrade（派单 2.5 第 1 条）。

    方式＝真跑 alembic 编程接口（command.upgrade / command.downgrade）对临时文件库，
    且用**显式 rev**（PREV_REV/NEW_REV）而不是字符串 "head"，防未来新迁移令本用例失配。
    """
    from alembic import command

    import app.config as app_cfg

    db_path = tmp_path / "m1a_mig.db"
    monkeypatch.setattr(
        app_cfg.settings, "database_url", "sqlite+aiosqlite:///" + db_path.as_posix()
    )
    cfg = _alembic_cfg()

    # ① 老库形态：跑到上一节 → 表不存在（baseline 是逐表 create_table，不会顺手建出新 ORM 表）
    command.upgrade(cfg, PREV_REV)
    assert _alembic_version(db_path) == PREV_REV
    assert not _table_exists(db_path)

    # ② upgrade 本迁移 → 表/列/索引/唯一约束齐
    command.upgrade(cfg, NEW_REV)
    assert _alembic_version(db_path) == NEW_REV
    cols, idx, uq = _schema_sig(db_path)
    assert cols == _ALL_COLS
    assert _INDEX in {i[0] for i in idx}
    assert ("character_id", "user_id", "drive_key") in uq

    # ③ 幂等：版本号退回上一节但表还在 → 重放本迁移不报错、不动数据
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            f"INSERT INTO {TABLE} (character_id, user_id, drive_key, level, last_settled_at, "
            "last_released_ratio) VALUES (1, 1, 'longing', 12.5, '2026-09-01 02:00:00', 0.0)"
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

    # ⑤ 再 upgrade：表复原（本单表内无历史数据要保留，删表即空表重来＝「只多一张空表」）
    command.upgrade(cfg, NEW_REV)
    assert _table_exists(db_path)
    assert _INDEX in {i[0] for i in _schema_sig(db_path)[1]}


def test_迁移路与ORM建表结构一致(tmp_path, monkeypatch):
    """两路收敛：本迁移建出的表 ≡ ORM create_all 的表（列集合 + 索引签名 + 唯一索引）。

    防「迁移与模型各写一套」的静默漂移：M1b 起读写本表，两路不一致会让老库缺列/缺索引。
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


def test_哨兵已登记relational_drives_level():
    """派单 2.3：老库缺表必须判「落后」走 upgrade（表级哨兵，同 plugin_consents 先例）。"""
    assert (TABLE, "level") in _CURRENT_SCHEMA_SENTINELS, _CURRENT_SCHEMA_SENTINELS

    from app.models.character import RelationalDrive

    assert RelationalDrive.__table__.c.level.nullable is False
    assert {c.name for c in RelationalDrive.__table__.primary_key.columns} == {"id"}
    # 外键只写 FK 字符串、不带 relationship()（不新增 ORM 侧加载行为）
    assert {fk.target_fullname for fk in RelationalDrive.__table__.foreign_keys} == {
        "ai_characters.id", "users.id"
    }
    assert not [r for r in RelationalDrive.__mapper__.relationships]


# ══════════════════════════════════════════════ 2. settle_level：分段增长与封顶

def test_settle_白天整小时增量等于增速():
    """北京 10:00→11:00（白天）：1 小时 × 0.090 = 0.090，游标推进到 now。"""
    cursor, now = _bj(10), _bj(11)
    level, new_cursor = d.settle_level(10.0, d.DRIVE_LONGING, cursor, now)
    assert level == pytest.approx(10.0 + d.DRIVE_GROWTH_PER_HOUR[d.DRIVE_LONGING])
    assert new_cursor == now


def test_settle_跨北京日边界按段乘倍率():
    """北京 22:30→次日 00:30：半小时白天 + 1.5 小时夜间（longing 夜间倍率 0.6）。"""
    cursor, now = _bj(22, 30), _bj(0, 30, day=3)
    g = d.DRIVE_GROWTH_PER_HOUR[d.DRIVE_LONGING]
    n = d.DRIVE_NIGHT_MULTIPLIER[d.DRIVE_LONGING]
    level, new_cursor = d.settle_level(10.0, d.DRIVE_LONGING, cursor, now)
    assert level == pytest.approx(10.0 + 0.5 * g + 1.5 * g * n)
    assert new_cursor == now
    # 夜间倍率 1.0 的驱力不受时段影响（同区间＝纯小时数）
    level_c, _ = d.settle_level(10.0, d.DRIVE_CONCERN, cursor, now)
    assert level_c == pytest.approx(10.0 + 2.0 * d.DRIVE_GROWTH_PER_HOUR[d.DRIVE_CONCERN])


def test_settle_夜间段用该驱力夜间倍率():
    """北京 23:30→次日 00:30 全夜间：intimacy 倍率 0.4。"""
    cursor, now = _bj(23, 30), _bj(0, 30, day=3)
    level, new_cursor = d.settle_level(0.0, d.DRIVE_INTIMACY, cursor, now)
    assert level == pytest.approx(
        d.DRIVE_GROWTH_PER_HOUR[d.DRIVE_INTIMACY] * d.DRIVE_NIGHT_MULTIPLIER[d.DRIVE_INTIMACY]
    )
    assert new_cursor == now


def test_settle_超长静默封顶一百():
    """一年静默也只到 LEVEL_MAX（长静默不无限累积；0.090/小时 ⇒ 满 100 约 46 天）。"""
    cursor, now = _bj(10), datetime(2027, 9, 2, 2, 0)  # now ＝北京 2027-09-02 10:00
    level, new_cursor = d.settle_level(50.0, d.DRIVE_LONGING, cursor, now)
    assert level == d.LEVEL_MAX == 100.0
    assert new_cursor == now


def test_settle_未知驱力增速按零但游标推进():
    cursor, now = _bj(10), _bj(12)
    level, new_cursor = d.settle_level(33.0, "no_such_drive", cursor, now)
    assert level == 33.0
    assert new_cursor == now


# ══════════════════════════════════════════════ 3. 幂等 / 回拨 / 首次建行

def test_settle_游标幂等重复调用不再增量():
    """同一 (cursor, now) 反复调用：第二次起游标已＝now ⇒ 零增量（游标是唯一事实）。"""
    cursor, now = _bj(10), _bj(13)
    lvl1, cur1 = d.settle_level(5.0, d.DRIVE_SHARING, cursor, now)
    assert cur1 == now and lvl1 > 5.0
    assert d.settle_level(lvl1, d.DRIVE_SHARING, cur1, now) == (lvl1, now)
    assert d.settle_level(lvl1, d.DRIVE_SHARING, cur1, now) == (lvl1, now)


def test_settle_时钟回拨不改水位不前进光标():
    cursor = _bj(12)
    level, new_cursor = d.settle_level(40.0, d.DRIVE_LONGING, cursor, _bj(11))
    assert level == 40.0
    assert new_cursor == cursor
    assert d.settle_level(40.0, d.DRIVE_LONGING, cursor, cursor) == (40.0, cursor)


def test_settle_游标为空时增量为零且光标落在now():
    """首次建行只定游标，不从零时刻补算历史（也不抹平已有水位）。"""
    now = _bj(9)
    assert d.settle_level(0.0, d.DRIVE_LONGING, None, now) == (0.0, now)
    assert d.settle_level(7.5, d.DRIVE_LONGING, None, now) == (7.5, now)


def test_settle_接受带tzinfo入参并与naive等价():
    """库内口径是 naive UTC；带 tzinfo 先归一，两种口径不会算错段。"""
    from datetime import timezone

    naive_cursor, naive_now = _bj(22, 30), _bj(0, 30, day=3)
    aware = (naive_cursor.replace(tzinfo=timezone.utc), naive_now.replace(tzinfo=timezone.utc))
    assert d.settle_level(1.0, d.DRIVE_LONGING, *aware) == d.settle_level(
        1.0, d.DRIVE_LONGING, naive_cursor, naive_now
    )


# ══════════════════════════════════════════════ 4. 释放两档

def test_release_open_返回释放后的余量():
    """release_* 的返回值＝释放后的新水位（部分释放留余量：没聊开就继续惦记）。"""
    assert d.release_open(100.0, d.DRIVE_LONGING) == pytest.approx(65.0)
    assert d.release_open(50.0, d.DRIVE_CONCERN) == pytest.approx(15.0)
    assert d.release_open(10.0, d.DRIVE_INTIMACY) == pytest.approx(8.2)
    for key in d.DRIVE_ALL_KEYS:
        left = d.release_open(30.0, key)
        assert left == pytest.approx(30.0 * (1.0 - d.DRIVE_OPEN_RELEASE_RATIO[key]))
        assert 0.0 <= left <= 30.0
    # 下限 0：负水位不产生负数；未知键不释放（原样保留，宁可不释放）
    assert d.release_open(-5.0, d.DRIVE_LONGING) == 0.0
    assert d.release_open(20.0, "no_such_drive") == 20.0


def test_release_full_恒归零():
    assert d.release_full(0.0) == 0.0
    assert d.release_full(99.9) == 0.0


# ══════════════════════════════════════════════ 5. 候选与常量口径

def test_top_candidate_排除intimacy并按固定顺序破并列():
    # intimacy 最高也不进候选
    assert d.top_candidate_drive({d.DRIVE_INTIMACY: 99.0, d.DRIVE_LONGING: 10.0}) == "longing"
    # 只有 intimacy ⇒ 无候选（回落现有加权随机）
    assert d.top_candidate_drive({d.DRIVE_INTIMACY: 99.0}) is None
    # 并列按 DRIVE_ALL_KEYS 固定顺序取第一个
    assert d.top_candidate_drive({d.DRIVE_CONCERN: 5.0, d.DRIVE_LONGING: 5.0}) == "longing"
    assert d.top_candidate_drive({d.DRIVE_SHARING: 7.0, d.DRIVE_AFFECTION: 7.0}) == "affection"
    # 正常取最高
    assert d.top_candidate_drive(
        {d.DRIVE_LONGING: 3.0, d.DRIVE_CURIOSITY: 8.5, d.DRIVE_SHARING: 4.0}
    ) == "curiosity"
    # 空 / 全 0 / 非正数与脏值不参与 ⇒ None
    assert d.top_candidate_drive({}) is None
    assert d.top_candidate_drive({k: 0.0 for k in d.DRIVE_CANDIDATE_KEYS}) is None
    assert d.top_candidate_drive({d.DRIVE_LONGING: -1.0, d.DRIVE_CONCERN: None}) is None


def test_常量表口径():
    """键集合/顺序/候选前缀/夜间窗口/双向映射与设计 §3.1–§3.3 一致（派单 2.4 常量清单）。"""
    assert d.DRIVE_ALL_KEYS == (
        "longing", "concern", "affection", "sharing", "curiosity", "intimacy",
    )
    assert d.DRIVE_CANDIDATE_KEYS == d.DRIVE_ALL_KEYS[:5]
    assert d.DRIVE_INTIMACY not in d.DRIVE_CANDIDATE_KEYS  # 只观测，永不进主动候选
    for table in (d.DRIVE_GROWTH_PER_HOUR, d.DRIVE_NIGHT_MULTIPLIER, d.DRIVE_OPEN_RELEASE_RATIO):
        assert set(table) == set(d.DRIVE_ALL_KEYS), table
    assert d.DRIVE_GROWTH_PER_HOUR == {
        "longing": 0.090, "concern": 0.075, "affection": 0.060,
        "sharing": 0.050, "curiosity": 0.035, "intimacy": 0.020,
    }
    assert d.DRIVE_NIGHT_MULTIPLIER == {
        "longing": 0.6, "concern": 1.0, "affection": 1.0,
        "sharing": 1.0, "curiosity": 1.0, "intimacy": 0.4,
    }
    assert d.DRIVE_OPEN_RELEASE_RATIO == {
        "longing": 0.35, "concern": 0.70, "affection": 0.40,
        "sharing": 0.45, "curiosity": 0.50, "intimacy": 0.18,
    }
    assert (d.NIGHT_START_HOUR, d.NIGHT_END_HOUR) == (23, 7)
    assert d.is_night_hour(23) and d.is_night_hour(0) and d.is_night_hour(6)
    assert not d.is_night_hour(7) and not d.is_night_hour(12) and not d.is_night_hour(22)
    # intent ↔ drive 双向映射（5 对；intimacy 无意图）
    assert d.DRIVE_TO_INTENT == {
        "longing": "check_in", "concern": "follow_up", "sharing": "share_self",
        "curiosity": "interest_hook", "affection": "recall_shared",
    }
    assert len(d.INTENT_TO_DRIVE) == len(d.DRIVE_TO_INTENT) == 5
    for drive, intent in d.DRIVE_TO_INTENT.items():
        assert d.INTENT_TO_DRIVE[intent] == drive
        assert drive in d.DRIVE_CANDIDATE_KEYS
    # 映射只定义在本纯函数域：outreach 的既有常量未被改动（且它自己不 import 本包）
    from app.domain.proactivity import outreach

    assert set(outreach.ALL_INTENTS) == {
        "check_in", "share_self", "recall_shared", "follow_up", "interest_hook",
    }


def test_纯函数域零IO零ORM():
    """派单 2.4 边界：drives 模块只依赖 stdlib 与纯函数域，不碰 DB/ORM/flag/网络。"""
    with open(d.__file__, encoding="utf-8") as f:
        src = f.read()
    forbidden = ("sqlalchemy", "app.db", "app.models", "async_session", "resolve_flag",
                 "settings", "httpx", "requests", "datetime.now")
    hits = [name for name in forbidden if name in src]
    assert not hits, f"纯函数域出现禁止依赖：{hits}"
    assert "app.utils.timeutil" in src  # 时区换算复用现成件，不自写时区逻辑


# ══════════════════════════════════════════════ 6. 唯一约束（真库读写）

_USER, _CHAR = 1, 1


@pytest.fixture()
def drive_env(tmp_path):
    from _dbclone import clone_engine, make_session_factory

    from app.models.character import AICharacter
    from app.models.user import User

    engine = clone_engine(tmp_path / "drives.db")
    factory = make_session_factory(engine)

    async def _init():
        async with factory() as db:
            db.add(User(id=_USER, username="rd_u1", nickname="主人"))
            db.add(AICharacter(id=_CHAR, user_id=_USER, name="小暖", is_active=True))
            await db.commit()

    asyncio.run(_init())
    yield factory
    engine.sync_engine.dispose()


def _insert_drive(factory, *, user_id: int = _USER, drive_key: str = "longing", level: float = 0.0):
    from app.models.character import RelationalDrive
    from app.utils.timeutil import now_naive_utc

    async def _run():
        async with factory() as db:
            db.add(RelationalDrive(
                character_id=_CHAR, user_id=user_id, drive_key=drive_key, level=level,
                last_settled_at=now_naive_utc(),
            ))
            await db.commit()

    asyncio.run(_run())


def test_同一角色用户驱力只能一行(drive_env):
    """UniqueConstraint(character_id, user_id, drive_key)：插两次 → IntegrityError（派单第 8 条）。"""
    _insert_drive(drive_env, drive_key=d.DRIVE_LONGING, level=12.5)
    with pytest.raises(IntegrityError):
        _insert_drive(drive_env, drive_key=d.DRIVE_LONGING, level=30.0)

    # 换 drive_key 不冲突；换 user_id 也不冲突（家庭共享口径下水位按用户分开），但未建账号会被 FK 拒
    _insert_drive(drive_env, drive_key=d.DRIVE_CONCERN, level=5.0)

    from sqlalchemy import select

    from app.models.character import RelationalDrive

    async def _rows():
        async with drive_env() as db:
            got = (await db.execute(
                select(RelationalDrive).order_by(RelationalDrive.drive_key)
            )).scalars().all()
            return [(r.drive_key, r.user_id, r.level, r.last_released_ratio, r.last_released_at)
                    for r in got]

    assert [r[:3] for r in asyncio.run(_rows())] == [
        (d.DRIVE_CONCERN, _USER, 5.0), (d.DRIVE_LONGING, _USER, 12.5)
    ]
    # 可空列与默认值按模型定义落库（last_released_at 可空、ratio 默认 0）
    assert all(r[3] == 0.0 and r[4] is None for r in asyncio.run(_rows()))

    with pytest.raises(IntegrityError):
        _insert_drive(drive_env, user_id=_USER + 99, drive_key=d.DRIVE_LONGING)
