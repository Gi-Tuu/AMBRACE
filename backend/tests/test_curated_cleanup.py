# -*- coding: utf-8 -*-
"""C14 curated 存量清账执行器测试（2026-09-25）。

**只用临时库**（tests/_dbclone 模板库页级克隆到 tmp_path），绝不连生产库。
脚本从磁盘 importlib 加载（scripts/ 不是 Python 包，沿用项目既有惯例）。

覆盖：
1. 判据边界（直接吃生产判据 ``_same_curated_value``）：只共享 3 字前缀不并、同模板不同宾语
   不并（C13b）、共享核心前缀且停在子句边界才并；
2. dry-run：存量/拟合并/保留/簇数与簇划分符合预期（代表＝簇内最新行），且库逐字节不变；
3. apply：成员行 status/superseded_by/时间列正确、代表行零改动、范围外行零改动、无删除；
4. 幂等：第二次 apply 变更 0 行，再跑 dry-run 拟合并 0；
5. 护栏：``--apply`` 缺 ``--yes`` → 返回码 2 且不改库；``--yes`` 缺 ``--apply`` 同样拒绝。

（项目未装 pytest-asyncio，异步由脚本 main 内部 asyncio.run 承载。）
"""
import asyncio
import importlib.util
import sqlite3
from pathlib import Path

import pytest

from _dbclone import clone_engine, make_session_factory

from app.events.facts import _same_curated_value
from app.models.memory import WorldFact

# 本文件每例起一次临时库（集成型），按项目惯例打 slow 标记（pytest -m "not slow" 可跳）
pytestmark = pytest.mark.slow

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "curated_cleanup.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("curated_cleanup", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cc = _load_script()

# ────────────────── 造数（范围内：character_id=13 + predicate=curated + status=active） ──────────────────
# ① 应合并成一族：共享核心「我是用户的老公」且前缀停在子句边界（C13/C13b）；
#    101 是 103 的前缀（既有「前缀包含」判据），102/103 靠共享核心前缀互并。
_REL = [
    (101, "relationship_baseline", "我是用户的老公"),
    (102, "relationship_baseline", "我是用户的老公，与用户同住"),
    (103, "relationship_baseline", "我是用户的老公，会为他做饭"),
]
# ② 应合并成一族：短串（≥6 字）是长串前缀
_PREF = [
    (110, "preference_profile", "用户不吃辣的菜"),
    (111, "preference_profile", "用户不吃辣的菜，饮食需顾及胃"),
]
# ③ 不该合并：只共享 3 字前缀「用户腰」（< _CORE_PREFIX_MIN_LEN=7）
_WAIST = [
    (120, "constraint", "用户腰不能压，侧躺需垫东西"),
    (121, "constraint", "用户腰部有伤，需避免趴着"),
]
# ④ 不该合并：LCP=7 且占比够，但前缀停在「美/拿」不是子句边界（C13b 专治此形）
_COFFEE = [
    (130, "preference_profile", "用户平时喜欢喝美式咖啡"),
    (131, "preference_profile", "用户平时喜欢喝拿铁咖啡"),
]
IN_SCOPE = _REL + _PREF + _WAIST + _COFFEE

# 范围外行：正文与簇代表逐字相同，用来钉死三条范围护栏（谓词/角色/状态）
# id 刻意小于范围内行，便于「越界行被塞进簇」的用例绕过代表最新性检查、直击写库护栏
OUT_OF_SCOPE = [
    # (id, character_id, predicate, status, kind, object_value)
    (90, 13, "status", "active", "status", "我是用户的老公，会为他做饭"),
    (91, 14, "curated", "active", "relationship_baseline", "我是用户的老公"),
    (92, 13, "curated", "superseded", "relationship_baseline", "我是用户的老公"),
    (93, 13, "curated", "expired", "fact", "我是用户的老公"),
]


def _seed(tmp_path: Path) -> Path:
    """临时库建表（模板库克隆）+ 造数；返回库文件路径。"""
    dst = Path(tmp_path) / "t.db"
    engine = clone_engine(str(dst))
    factory = make_session_factory(engine)

    async def _fill():
        async with factory() as db:
            for rid, kind, value in IN_SCOPE:
                db.add(WorldFact(
                    id=rid, user_id=1, character_id=13, subject_type="character",
                    subject_id=13, predicate="curated", object_value=value,
                    status="active", kind=kind, audience='["public"]',
                    author="system", is_authoritative=True,
                ))
            for rid, cid, pred, status, kind, value in OUT_OF_SCOPE:
                db.add(WorldFact(
                    id=rid, user_id=1, character_id=cid, subject_type="character",
                    subject_id=cid, predicate=pred, object_value=value,
                    status=status, kind=kind, audience='["public"]',
                    author="system", is_authoritative=True,
                ))
            await db.commit()
    asyncio.run(_fill())
    asyncio.run(engine.dispose())
    return dst


def _snapshot(db: Path) -> dict:
    """全表逐行指纹（含 updated_at/superseded_at 文本），比较「有没有多改一行」最硬的手段。"""
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT * FROM world_facts ORDER BY id").fetchall()
        return {r["id"]: {k: _as_text(v) for k, v in dict(r).items()} for r in rows}
    finally:
        con.close()


def _as_text(v):
    return v.strftime("%Y-%m-%d %H:%M:%S.%f") if hasattr(v, "strftime") else v


def _row(db: Path, rid: int) -> dict:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return {k: _as_text(v) for k, v in dict(
            con.execute("SELECT * FROM world_facts WHERE id=?", (rid,)).fetchone()).items()}
    finally:
        con.close()


def _in_scope_dicts(db: Path) -> list[dict]:
    """按报告口径自行选行，喂给脚本的纯函数（不依赖脚本的引擎接缝）。"""
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, character_id, status, predicate, kind, object_value FROM world_facts "
            "WHERE character_id=13 AND status='active' AND predicate='curated' "
            "ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


# ────────────────── 1：判据边界（脚本复用生产判据本身） ──────────────────

def test_只共享三字前缀不合并():
    assert _same_curated_value("用户腰不能压，侧躺需垫东西", "用户腰部有伤，需避免趴着") is False


def test_同模板不同宾语不合并_c13b():
    assert _same_curated_value("用户平时喜欢喝美式咖啡", "用户平时喜欢喝拿铁咖啡") is False


def test_共享核心前缀停在子句边界才合并():
    assert _same_curated_value("我是用户的老公，与用户同住", "我是用户的老公，会为他做饭") is True
    assert _same_curated_value("我是用户的老公", "我是用户的老公，会为他做饭") is True


def test_脚本不自带判据实现_聚类函数吃注入的判据():
    # 传恒假判据 ⇒ 一行都不并；传恒真判据 ⇒ 全归进最新那行的簇。
    # 说明 build_plan 自身不含任何文本相似逻辑，判据完全来自 app.events.facts。
    newer, older = {"id": 2, "object_value": "乙"}, {"id": 1, "object_value": "甲"}
    assert cc.build_plan([older, newer], lambda a, b: False) == [
        {"representative": newer, "members": []},
        {"representative": older, "members": []},
    ]
    plan = cc.build_plan([older, newer], lambda a, b: True)
    assert plan == [{"representative": newer, "members": [older]}]
    assert cc.plan_actions(plan) == [(1, 2)]
    # 默认库路径＝生产库（本文件一律显式传 --db 临时库，绝不使用它）
    assert cc.DEFAULT_DB.replace("\\", "/").endswith("backend/data/sqlite/ai_companion.db")


# ────────────────── 2：dry-run 计数与簇划分 ──────────────────

def test_dry_run_计数与簇划分(tmp_path, capsys):
    db = _seed(tmp_path)
    before = _snapshot(db)
    assert cc.main(["--db", str(db), "--dry-run"]) == 0
    out = capsys.readouterr().out
    # 存量 9（范围外 4 行不计）；拟合并 3（101/102→103、110→111）；保留 6；含合并的簇 2
    assert "汇总：存量 9 行" in out
    assert "拟置 superseded 3 行" in out
    assert "合并后保留 6 行" in out
    assert "含合并动作的簇 2 个" in out
    assert "保留 id=103" in out and "保留 id=111" in out
    for rid in (101, 102, 110):
        assert f"拟置 superseded id={rid}" in out
    assert "拟置 superseded id=103" not in out and "拟置 superseded id=111" not in out
    # 不该合并的四行、以及范围外四行都不该出现在报告里
    for rid in (120, 121, 130, 131, 90, 91, 92, 93):
        assert f"id={rid}" not in out
    assert "[dry-run] 未写库" in out
    assert _snapshot(db) == before          # 只读：整库逐字节不变


def test_dry_run_代表是簇内最新行(tmp_path):
    db = _seed(tmp_path)
    clusters = cc.build_plan(_in_scope_dicts(db), _same_curated_value)
    merged = [c for c in clusters if c["members"]]
    assert {c["representative"]["id"] for c in merged} == {103, 111}
    for c in merged:
        ids = [c["representative"]["id"]] + [m["id"] for m in c["members"]]
        assert c["representative"]["id"] == max(ids)
        assert [m["id"] for m in c["members"]] == sorted(m["id"] for m in c["members"])
    assert cc.plan_violations(clusters) == []
    assert cc.plan_actions(clusters) == [(101, 103), (102, 103), (110, 111)]
    st = cc.stats(clusters, len(_in_scope_dicts(db)))
    assert (st["total"], st["merged"], st["kept"], st["clusters"]) == (9, 3, 6, 2)


# ────────────────── 3：apply 写入正确性 ──────────────────

def test_apply_成员置superseded且代表与范围外零改动(tmp_path, capsys):
    db = _seed(tmp_path)
    before = _snapshot(db)
    assert cc.main(["--db", str(db), "--apply", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "本轮实际置 superseded 3 行" in out

    values = {rid: v for rid, _k, v in IN_SCOPE}
    for rid, rep in ((101, 103), (102, 103), (110, 111)):
        r = _row(db, rid)
        assert r["status"] == "superseded"
        assert r["superseded_by"] == rep
        assert r["superseded_at"] and r["superseded_at"].startswith("20"), r["superseded_at"]
        assert r["updated_at"] and r["updated_at"] >= r["superseded_at"]
        assert r["updated_at"] != before[rid]["updated_at"]
        assert r["object_value"] == values[rid]      # 正文一字不改
        assert r["kind"] == dict((i, k) for i, k, _v in IN_SCOPE)[rid]

    for rid in (103, 111, 120, 121, 130, 131):       # 代表行 + 未合并行：整行不动
        assert _snapshot(db)[rid] == before[rid], rid
    for rid, *_rest in OUT_OF_SCOPE:                 # 范围外行：整行不动
        assert _snapshot(db)[rid] == before[rid], rid
    assert set(_snapshot(db)) == set(before)         # 没有任何行被删除


def test_apply_幂等_第二次零变更(tmp_path, capsys):
    db = _seed(tmp_path)
    assert cc.main(["--db", str(db), "--apply", "--yes"]) == 0
    capsys.readouterr()
    mid = _snapshot(db)

    assert cc.main(["--db", str(db), "--apply", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "0 变更" in out
    assert _snapshot(db) == mid                      # 第二次 apply 逐字节不变

    assert cc.main(["--db", str(db), "--dry-run"]) == 0
    out2 = capsys.readouterr().out
    assert "汇总：存量 6 行" in out2
    assert "拟置 superseded 0 行" in out2
    assert "含合并动作的簇 0 个" in out2


# ────────────────── 4：双开关护栏 ──────────────────

def test_apply缺yes_拒绝执行且不改库(tmp_path, capsys):
    db = _seed(tmp_path)
    before = _snapshot(db)
    rc = cc.main(["--db", str(db), "--apply"])
    err = capsys.readouterr().err
    assert rc == 2                                   # 钉死：非 0 返回码
    assert "拒绝执行" in err and "--yes" in err
    assert _snapshot(db) == before


def test_yes缺apply_同样拒绝执行(tmp_path, capsys):
    db = _seed(tmp_path)
    before = _snapshot(db)
    assert cc.main(["--db", str(db), "--yes"]) == 2
    assert "拒绝执行" in capsys.readouterr().err
    assert _snapshot(db) == before


def test_apply与dry_run互斥(tmp_path, capsys):
    db = _seed(tmp_path)
    before = _snapshot(db)
    assert cc.main(["--db", str(db), "--apply", "--yes", "--dry-run"]) == 2
    assert "互斥" in capsys.readouterr().err
    assert _snapshot(db) == before


def test_越界成员被写库护栏拦下并整体回滚(tmp_path, capsys, monkeypatch):
    """把「范围外行」伪装成簇成员塞进计划（选行条件已正确，只能从计划层注入）：
    UPDATE 自带范围守卫 ⇒ 命中 0 行 ⇒ 必须整体回滚（0 行变更）并以非 0 退出，不许部分写入。
    """
    db = _seed(tmp_path)
    before = _snapshot(db)
    real = {r["id"]: r for r in _in_scope_dicts(db)}
    # id=90：character_id=13 但 predicate='status'（越界行，取真实行内容伪装成簇成员）
    interloper = {k: v for k, v in _row(db, 90).items()
                  if k in {"id", "character_id", "status", "predicate", "kind", "object_value"}}
    assert interloper["predicate"] == "status"
    tainted = [{"representative": real[103], "members": [real[102], interloper]}]

    monkeypatch.setattr(cc, "build_plan", lambda r, same: tainted)
    rc = cc.main(["--db", str(db), "--apply", "--yes"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "整体回滚" in err
    assert _snapshot(db) == before           # 含 id=102：一条都没写进去


def test_db不存在直接拒绝(tmp_path, capsys):
    assert cc.main(["--db", str(tmp_path / "nope.db"), "--dry-run"]) == 2
    assert "DB 不存在" in capsys.readouterr().err


# ────────────────── 5：范围护栏（前置断言口径） ──────────────────

def test_scope_ok_三条判据缺一不可():
    ok = {"id": 1, "character_id": 13, "status": "active", "predicate": "curated",
          "object_value": "甲"}
    assert cc.scope_ok(ok)
    for key, bad in (("character_id", 14), ("status", "superseded"), ("predicate", "status")):
        assert not cc.scope_ok({**ok, key: bad}), key
    assert cc.scope_violations([ok]) == []
    assert len(cc.scope_violations([{**ok, "character_id": 99}])) == 1
    # 正文为空的行判据无从比较 ⇒ 同样属前置断言拦截范围
    assert any("object_value" in m for m in cc.scope_violations([{**ok, "object_value": "  "}]))


def test_计划自检拦下非法簇():
    def row(i, v):
        return {"id": i, "character_id": 13, "status": "active",
                "predicate": "curated", "kind": "fact", "object_value": v}

    a, b = row(1, "甲"), row(2, "乙")
    # 代表不是最新行
    assert cc.plan_violations([{"representative": a, "members": [b]}])
    # 既是代表又是成员
    bad2 = [{"representative": b, "members": [a]}, {"representative": a, "members": []}]
    assert any("既是代表" in m for m in cc.plan_violations(bad2))
    assert cc.plan_violations([{"representative": b, "members": [a]}]) == []
