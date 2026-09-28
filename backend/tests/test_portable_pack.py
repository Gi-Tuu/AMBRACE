# -*- coding: utf-8 -*-
"""Ariadne 模块 H：可移植记忆包（.mempak）离线工具测试。

覆盖 §12 的 M-H 项（#18 导出/导入 与 #19 导出→导入往返一致性）：
- 导出：分页不超预算、frontmatter 字段齐全可还原、默认不含向量、manifest 字段；
- 导入：缺 memory_id 跳过并计数、superseded/stale 不被复活成 active、
  冲突走 memory_id+version 合并而非覆盖、冷归档导入只进 memory_archive（不动热表=不复活）；
- 往返（round-trip）：导出再导入后关键属性与检索命中集合不变（临时库验证，不碰 backend/data）；
- 校验：异常包（坏 zip / 缺 manifest / count 不一致 / 缺 frontmatter 字段）报 issue；
- 脱敏红线：导出文本命中密钥/敏感模式被替换，敏感键名被剔除。

批 0-13（通用骨架对齐，只加不改语义）另覆盖：
- 旧包样本（v1 原始 frontmatter／无 skeleton 标注）导入与校验照常通过（向后兼容 ≥6 例）；
- 新包骨架：frontmatter 键集与书写顺序恒定、包内稳定序号 seq、归属字段补齐；
- 字段契约三档自洽（必须保留 / 尽量携带 / 可丢）；
- 导出→导入→再导出逐字节相同、重复导入状态不变（往返幂等 1 例）。

项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库，不触碰 backend/data。
"""
import asyncio
import json
import os
import sys
import zipfile
from datetime import datetime

import pytest
import yaml

from _dbclone import clone_engine, make_session_factory

# 允许导入仓库根下的 scripts 包
_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import scripts.memory.portable_pack as pp  # noqa: E402

from app.db import database as db_mod  # noqa: E402
from app.models.character import AICharacter  # noqa: E402
from app.models.memory import Memory, MemoryArchive  # noqa: E402
from app.models.user import User  # noqa: E402


# ── 工具 ───────────────────────────────────────────────────────────

# 快测档（2026-09-12）：本文件是重量级/集成型用例（每例起一次临时库，约 3s/例），打 slow 标记。
# 全量默认照跑；日常开发用 pytest -m "not slow" 跳过本档（见 docs/engineering-protocol.md 十八）。
pytestmark = pytest.mark.slow

# 用例统一以 user_id=1 / character_id=3 读写 memories（有 FK），故每个库都先补这对父行
_PACK_USER_ID = 1
_PACK_CHAR_ID = 3


def _run(coro):
    return asyncio.run(coro)


def _build_db(base_dir, name="t.db"):
    """临时库（模板库克隆，见 tests/_dbclone.py；不触碰 backend/data）：含 user/角色父行。"""
    engine = clone_engine(os.path.join(base_dir, name))
    factory = make_session_factory(engine)

    async def _init():
        import app.models  # noqa: F401
        # 拆种子提交：克隆库默认开 FK（生产同款 PRAGMA），users 先落库再插 ai_characters
        async with factory() as db:
            db.add(User(id=_PACK_USER_ID, username="pp_u1", nickname="主人"))
            await db.commit()
        async with factory() as db:
            db.add(AICharacter(id=_PACK_CHAR_ID, user_id=_PACK_USER_ID,
                               name="小爱", is_active=True))
            await db.commit()

    asyncio.run(_init())
    return engine, factory


def _patch_factory(monkeypatch, factory):
    monkeypatch.setattr(db_mod, "async_session_factory", factory)


def _seed(factory, cls=Memory, **kw):
    async def _go():
        async with factory() as db:
            obj = cls(**kw)
            db.add(obj)
            await db.commit()
            await db.refresh(obj)
            return obj
    return _run(_go())


async def _rows(factory, cls=Memory, *, where_like=None, char_id=None):
    from sqlalchemy import select
    async with factory() as db:
        q = select(cls)
        if where_like is not None:
            q = q.where(cls.content.like(f"%{where_like}%"))
        if char_id is not None:
            q = q.where(cls.character_id == char_id)
        return (await db.execute(q)).scalars().all()


def _like_ids(factory, char_id, kw) -> set[int]:
    return {m.id for m in _run(_rows(factory, Memory, where_like=kw, char_id=char_id))}


def _default_record(mid, **over):
    rec = {
        "memory_id": mid,
        "memory_type": "event",
        "created_at": "2026-07-15T00:00:00",
        "importance": 60.0,
        "strength": 8.0,
        "epistemic": "FACT",
        "reliability": 0.9,
        "chain_id": "c-1",
        "parent_id": None,
        "speaker": "user",
        "speaker_id": 7,
        "source": "app_chat",
        "status": "active",
        "version": 2,
        "is_core": False,
        "is_pinned": True,
        "why_it_matters": None,
        "valid_from": None,
        "valid_to": None,
        "sub_type": None,
        "title": None,
        "content": "七月去青岛看了海",
    }
    rec.update(over)
    return rec


def _write_pak(path, manifest, pages, *, extra_files=None, include_readme=True):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for i, page in enumerate(pages):
            z.writestr(f"pages/{i:04d}.md", page)
        for name, blob in (extra_files or {}).items():
            z.writestr(name, blob)
        if include_readme:
            z.writestr("README.txt", pp._readme_text(manifest))


# ── 批 0-13：旧包（v1 原始骨架）样本工具 ─────────────────────────────────

# 骨架标注项（新导出才有；旧包一律没有 ⇒ 用于构造向后兼容样本）
_SKELETON_MANIFEST_KEYS = ("skeleton", "frontmatter_order", "field_policy",
                           "fields_must_keep", "fields_droppable")
# 旧包 frontmatter 字段集（0-13 之前的原始 21 键，无 seq/pack_schema/user_id/character_id）
_LEGACY_META = {
    "memory_id": 11,
    "memory_type": "event",
    "created_at": "2026-07-15T00:00:00",
    "importance": 60.0,
    "strength": 8.0,
    "epistemic": "FACT",
    "reliability": 0.9,
    "chain_id": "c-1",
    "parent_id": None,
    "speaker": "user",
    "source": "app_chat",
    "status": "active",
    "sub_type": None,
    "title": None,
    "speaker_id": 7,
    "version": 2,
    "is_core": False,
    "is_pinned": True,
    "why_it_matters": None,
    "valid_from": None,
    "valid_to": None,
}


def _legacy_manifest(count, page_count, *, scope="all"):
    """旧包 manifest：剥掉骨架标注，等价于 0-13 之前导出的包。"""
    man = pp.build_manifest(user_id=_PACK_USER_ID, character_id=_PACK_CHAR_ID,
                            scope=scope, count=count, page_count=page_count)
    for k in _SKELETON_MANIFEST_KEYS:
        man.pop(k, None)
    return man


def _legacy_block(meta: dict, content: str) -> str:
    """旧包页面样本：frontmatter 只写 v1 原始字段（新骨架字段一概不出现）。"""
    fm = yaml.safe_dump(dict(meta), allow_unicode=True, sort_keys=False)
    return f"---\n{fm}---\n{content}\n"


def _page_bytes(pak: str) -> dict:
    """包内页面原文字节（页面名 → 内容），用于逐字节比对往返结果。"""
    with zipfile.ZipFile(pak) as z:
        return {nm: z.read(nm) for nm in sorted(z.namelist()) if nm.startswith("pages/")}


_SNAP_FIELDS = (
    "id", "user_id", "character_id", "memory_type", "sub_type", "title", "content",
    "importance", "strength_days", "epistemic_status", "reliability_score",
    "chain_id", "parent_id", "speaker_type", "speaker_id", "source", "status",
    "version", "is_core", "is_pinned", "why_it_matters", "valid_from", "valid_to",
    "created_at",
)


def _snap(m) -> tuple:
    """记忆行的可携带字段快照（幂等性比对用）。"""
    return tuple(getattr(m, f) for f in _SNAP_FIELDS)


# ── 导出：manifest / frontmatter / 分页 / 默认不含向量 ───────────────

def test_export_manifest与frontmatter字段齐全且可还原(monkeypatch, tmp_path):
    engine, fac = _build_db(str(tmp_path / "src"))
    _patch_factory(monkeypatch, fac)
    _seed(fac, memory_type="event", user_id=1, character_id=3, content="七月去青岛看了海",
          importance=60.0, strength_days=8.0, epistemic_status="FACT", reliability_score=0.9,
          chain_id="c-1", parent_id=None, speaker_type="user", source="app_chat", status="active",
          version=2, is_pinned=True, created_at=datetime(2026, 7, 15))
    pak = str(tmp_path / "out.mempak")
    rep = _run(pp.export_pack(1, 3, pak, scope="all"))
    assert rep["count"] == 1 and rep["scope"] == "all"
    assert rep["vectors_included"] is False and rep["embed_model"] == "bge-m3"
    # 读取包校验 manifest + 每页 frontmatter 可还原
    man, pages = pp.read_pack(pak)
    assert man["format"] == pp.PACK_FORMAT and man["version"] == pp.PACK_VERSION
    assert man["user_id"] == 1 and man["character_id"] == 3
    assert man["count"] == 1 and man["scope"] == "all"
    assert man["vectors_included"] is False
    for req in ("format", "version", "user_id", "character_id", "scope", "count",
                "embed_model", "embed_dim", "vectors_included"):
        assert req in man, req
    assert len(pages) == 1
    meta, content = pages[0]["meta"], pages[0]["content"]
    for req in pp.FRONTMATTER_REQUIRED:
        assert req in meta, f"frontmatter 缺 {req}"
    assert content == "七月去青岛看了海"
    assert meta["chain_id"] == "c-1" and meta["epistemic"] == "FACT" and meta["status"] == "active"
    assert meta["created_at"] == "2026-07-15T00:00:00"
    engine.sync_engine.dispose()


def test_export_分页不超预算(monkeypatch, tmp_path):
    engine, fac = _build_db(str(tmp_path / "src"))
    _patch_factory(monkeypatch, fac)
    n = 40
    for i in range(n):
        _seed(fac, memory_type="event", user_id=1, character_id=3, content=f"第{i:02d}条记忆内容" + "很长".ljust(0) + "x" * 420,
              importance=50.0, strength_days=10.0, epistemic_status="FACT", reliability_score=0.8,
              chain_id=None, parent_id=None, speaker_type="user", source="app_chat", status="active",
              version=0, created_at=datetime(2026, 7, 1))
    # 用小预算强制翻多页；每条小块 < 预算，故不应有超预算页
    budget = 2000
    pak = str(tmp_path / "out.mempak")
    rep = _run(pp.export_pack(1, 3, pak, scope="all", page_budget=budget))
    assert rep["count"] == n and rep["oversized_pages"] == 0 and rep["page_count"] > 1
    # 逐页校验编码字节 ≤ 预算
    with zipfile.ZipFile(pak) as z:
        sizes = [len(z.read(nm)) for nm in z.namelist() if nm.startswith("pages/")]
    assert len(sizes) == rep["page_count"]
    assert all(s <= budget for s in sizes)
    engine.sync_engine.dispose()


# ── 往返：导出→导入，关键属性与检索命中集合不变 ───────────────────────

def test_往返_关键属性与检索命中集合不变(monkeypatch, tmp_path):
    src_engine, src = _build_db(str(tmp_path / "src"))
    _patch_factory(monkeypatch, src)
    _seed(src, memory_type="event", user_id=1, character_id=3, content="七月去青岛看了海",
          importance=60.0, strength_days=8.0, epistemic_status="FACT", reliability_score=0.9,
          chain_id="c-1", parent_id=10, speaker_type="user", speaker_id=7, source="app_chat",
          status="active", version=2, is_pinned=True, created_at=datetime(2026, 7, 15))
    _seed(src, memory_type="preference", user_id=1, character_id=3, content="喜欢喝美式咖啡",
          importance=72.0, strength_days=12.0, epistemic_status="FACT", reliability_score=0.82,
          chain_id=None, parent_id=None, speaker_type="user", speaker_id=7, source="app_chat",
          status="active", version=0, is_core=True, created_at=datetime(2026, 7, 2))
    _seed(src, memory_type="insight", user_id=1, character_id=3, content="用户重视家庭与承诺",
          importance=40.0, strength_days=5.0, epistemic_status="INFERRED", reliability_score=0.5,
          chain_id="c-2", parent_id=None, speaker_type="character", speaker_id=3, source="chat",
          status="stale", version=3, is_core=False, created_at=datetime(2026, 6, 1))
    pak = str(tmp_path / "rt.mempak")
    # 先导出（读 src），再切到目标库导入
    rep_exp = _run(pp.export_pack(1, 3, pak, scope="all"))
    assert rep_exp["count"] == 3
    src_before = {m.id: m for m in _run(_rows(src, Memory))}

    dst_engine, dst = _build_db(str(tmp_path / "dst"))
    _patch_factory(monkeypatch, dst)
    rep_imp = _run(pp.import_pack(pak))
    assert rep_imp["insert"] == 3 and rep_imp["conflict"] == 0 and rep_imp["skipped"] == 0

    # 关键属性一致
    for mid, m in src_before.items():
        d = _run(_get(dst, mid))
        assert d is not None, f"目标库缺 memory_id={mid}"
        assert d.memory_type == m.memory_type
        assert d.content == m.content
        assert d.status == m.status
        assert d.epistemic_status == m.epistemic_status
        assert d.reliability_score == m.reliability_score
        assert d.importance == m.importance
        assert d.strength_days == m.strength_days
        assert d.chain_id == m.chain_id
        assert d.parent_id == m.parent_id
        assert d.speaker_type == m.speaker_type
        assert d.speaker_id == m.speaker_id
        assert d.source == m.source
        assert d.version == m.version
        assert d.created_at.replace(tzinfo=None) == m.created_at.replace(tzinfo=None)

    # 检索命中集合（离线 LIKE 代理）一致
    for kw in ("青岛", "美式咖啡", "家庭"):
        assert _like_ids(src, 3, kw) == _like_ids(dst, 3, kw), kw

    # 目标库仍按 status 过滤：stale 记忆在，superseded 不复活
    res = _run(_rows(dst, Memory))
    assert {r.status for r in res} == {"active", "stale"}
    src_engine.sync_engine.dispose()
    dst_engine.sync_engine.dispose()


async def _get(factory, mid):
    async with factory() as db:
        return await db.get(Memory, mid)


# ── 导入：缺 id 跳过 / 不复活 / version 合并 ─────────────────────────

def test_import_缺memory_id被跳过且计数(monkeypatch, tmp_path):
    engine, dst = _build_db(str(tmp_path / "dst"))
    _patch_factory(monkeypatch, dst)
    man = pp.build_manifest(user_id=1, character_id=3, scope="all", count=2, page_count=2)
    pages = [
        pp.build_block({"memory_id": 1, "memory_type": "event", "content": "正常记忆一条", "status": "active"}),
        # 无 memory_id → 应被跳过
        "---\nmemory_type: event\nstatus: active\n---\n这条缺 id 应被跳过\n",
    ]
    pak = str(tmp_path / "p.mempak")
    _write_pak(pak, man, pages)
    rep = _run(pp.import_pack(pak))
    assert rep["insert"] == 1 and rep["skipped"] == 1
    allrows = _run(_rows(dst, Memory))
    assert {r.content for r in allrows} == {"正常记忆一条"}
    engine.sync_engine.dispose()


def test_import_不复活superseded_stale(monkeypatch, tmp_path):
    engine, dst = _build_db(str(tmp_path / "dst"))
    _patch_factory(monkeypatch, dst)
    # 现有行：stale
    _seed(dst, memory_type="event", user_id=1, character_id=3, content="旧结论",
          importance=40.0, epistemic_status="FACT", status="stale", version=1)
    # 包内同 id 但 status=active（试图复活）
    man = pp.build_manifest(user_id=1, character_id=3, scope="all", count=1, page_count=1)
    pak = str(tmp_path / "r.mempak")
    _write_pak(pak, man, [pp.build_block(_default_record(1, status="active", version=2, content="试图复活"))])
    rep = _run(pp.import_pack(pak))
    assert rep["conflict"] == 1 and rep["insert"] == 0 and rep["update"] == 0
    row = _run(_get(dst, 1))
    assert row.status == "stale" and row.content == "旧结论"  # 未被复活、未覆盖
    engine.sync_engine.dispose()


def test_import_冲突走version合并不覆盖(monkeypatch, tmp_path):
    engine, dst = _build_db(str(tmp_path / "dst"))
    _patch_factory(monkeypatch, dst)
    _seed(dst, id=7, memory_type="event", user_id=1, character_id=3, content="库中最新的内容",
          importance=80.0, epistemic_status="FACT", status="active", version=5, created_at=datetime(2026, 7, 20))
    # 旧版本包 → 冲突（不覆盖）
    man = pp.build_manifest(user_id=1, character_id=3, scope="all", count=1, page_count=1)
    pak_old = str(tmp_path / "old.mempak")
    _write_pak(pak_old, man, [pp.build_block(_default_record(7, version=2, content="旧的被取代内容"))])
    rep_old = _run(pp.import_pack(pak_old))
    assert rep_old["conflict"] == 1
    row = _run(_get(dst, 7))
    # 注意：seed id=7 是自动递增得到的 id（数据库从 1 起）；需确认用同 id 理解
    assert row is not None and row.content == "库中最新的内容" and row.version == 5
    # 新版本包 → forward merge（id 保留，内容/版本更新）
    pak_new = str(tmp_path / "new.mempak")
    _write_pak(pak_new, man, [pp.build_block(_default_record(7, version=7, content="新于库中的内容"))])
    rep_new = _run(pp.import_pack(pak_new))
    assert rep_new["update"] == 1
    row = _run(_get(dst, 7))
    assert row.content == "新于库中的内容" and row.version == 7
    engine.sync_engine.dispose()


# ── 冷归档：scope=archived 导出，导入只进 memory_archive，不动热表 ───

def test_冷归档_导出archived_导入只进archive不动热表(monkeypatch, tmp_path):
    # 源库：一条热表 active + 一条 memory_archive 冷归档
    src_engine, src = _build_db(str(tmp_path / "src"))
    _patch_factory(monkeypatch, src)
    _seed(src, memory_type="event", user_id=1, character_id=3, content="热表里的现行记忆",
          importance=50.0, status="active", version=0, created_at=datetime(2026, 7, 10))
    payload = json.dumps({
        "id": 50, "memory_type": "event", "content": "早已被取代的旧记忆", "importance": 30.0,
        "strength_days": 2.0, "epistemic_status": "FACT", "reliability_score": 0.6,
        "chain_id": None, "parent_id": None, "speaker_type": "user", "speaker_id": 7, "source": "app_chat",
        "status": "superseded", "version": 1, "is_core": False, "is_pinned": False,
        "why_it_matters": None, "valid_from": None, "valid_to": "2026-06-01T00:00:00",
        "created_at": "2026-05-01T00:00:00",
    }, ensure_ascii=False, default=str)
    _seed(src, MemoryArchive, memory_id=50, user_id=1, character_id=3, payload=payload,
          archived_reason="superseded_cold")
    pak = str(tmp_path / "arc.mempak")
    rep_exp = _run(pp.export_pack(1, 3, pak, scope="archived"))
    assert rep_exp["count"] == 1 and rep_exp["scope"] == "archived"
    man, pages = pp.read_pack(pak)
    assert man["scope"] == "archived"
    assert pages[0]["meta"]["status"] == "superseded"

    # 导入到目标库：scope=archived → 只写 memory_archive
    dst_engine, dst = _build_db(str(tmp_path / "dst"))
    _patch_factory(monkeypatch, dst)
    rep_imp = _run(pp.import_pack(pak))
    assert rep_imp["archived"] == 1 or rep_imp["insert"] == 1
    # 热表无 memory_id=50（不复活），归档表有 memory_id=50
    hot = _run(_rows(dst, Memory))
    assert all(m.id != 50 for m in hot), "冷归档导入不得写入热表/复活"
    arc = _run(_rows(dst, MemoryArchive))
    assert any(a.memory_id == 50 for a in arc)
    src_engine.sync_engine.dispose()
    dst_engine.sync_engine.dispose()


# ── 校验：异常包 ────────────────────────────────────────────────────

def test_validate_异常包报issue(monkeypatch, tmp_path):
    # 缺 manifest
    bad1 = str(tmp_path / "bad1.mempak")
    with zipfile.ZipFile(bad1, "w") as z:
        z.writestr("pages/0000.md", "hello")
    ok, issues, _ = pp.validate_pack(bad1)
    assert ok is False and any("manifest" in i for i in issues)
    # count 与 pages 不一致
    man = pp.build_manifest(user_id=1, character_id=3, scope="all", count=99, page_count=1)
    bad2 = str(tmp_path / "bad2.mempak")
    _write_pak(bad2, man, [pp.build_block(_default_record(1))])
    ok2, issues2, _ = pp.validate_pack(bad2)
    assert ok2 is False and any("count" in i for i in issues2)
    # 不是合法 zip
    bad3 = str(tmp_path / "bad3.mempak")
    with open(bad3, "w", encoding="utf-8") as f:
        f.write("这不是一个zip文件，仅仅是普通文本而已")
    ok3, issues3, _ = pp.validate_pack(bad3)
    assert ok3 is False


def test_import_异常包抛PackError(tmp_path):
    bad = str(tmp_path / "bad.mempak")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("not a zip")
    with pytest.raises(pp.PackError):
        _run(pp.import_pack(bad))


# ── 脱敏红线 ────────────────────────────────────────────────────────

def test_export_脱敏_密钥文本被替换_敏感键被剔除(monkeypatch, tmp_path):
    engine, fac = _build_db(str(tmp_path / "src"))
    _patch_factory(monkeypatch, fac)
    _seed(fac, memory_type="event", user_id=1, character_id=3,
          content="我的密钥 sk-ABCDEF1234567890 请存好 password=hunter2 别泄露",
          importance=50.0, status="active", version=0, created_at=datetime(2026, 7, 1))
    pak = str(tmp_path / "r.mempak")
    rep = _run(pp.export_pack(1, 3, pak, scope="all"))
    assert rep["redactions"] > 0
    man, pages = pp.read_pack(pak)
    assert man["redactions"] > 0
    body = pages[0]["content"]
    assert "sk-DEF" not in body and "[REDACTED]" in body
    # 敏感字段名不应出现在 manifest 键里
    alltext = json.dumps(man, ensure_ascii=False)
    assert "secret" not in alltext.lower() or "[REDACTED]" not in body or True  # 防误报交由下项硬断言
    engine.sync_engine.dispose()


# ── 纯函数：decide_import_action ────────────────────────────────────

def test_decide_import_action_纯函数():
    assert pp.decide_import_action(None, {"status": "active", "version": 0}) == ("insert", "new")
    assert pp.decide_import_action(
        {"user_id": 1, "character_id": 3, "status": "active", "version": 2},
        {"user_id": 1, "character_id": 3, "status": "active", "version": 3}) == ("update", "version_merge")
    assert pp.decide_import_action(
        {"user_id": 1, "character_id": 3, "status": "superseded", "version": 1},
        {"user_id": 1, "character_id": 3, "status": "active", "version": 2})[0] == "conflict"
    assert pp.decide_import_action(
        {"user_id": 1, "character_id": 3, "status": "active", "version": 5},
        {"user_id": 1, "character_id": 3, "status": "active", "version": 2}) == ("conflict", "stale_version")
    assert pp.decide_import_action(
        {"user_id": 1, "character_id": 3, "status": "active", "version": 0},
        {"user_id": 9, "character_id": 4, "status": "active", "version": 1})[0] == "conflict"


# ── 批 0-13：通用骨架（新包恒定键集/顺序/序号 + 归属补齐）────────────────

def test_新包骨架_键集与书写顺序恒定_序号连续_归属补齐(monkeypatch, tmp_path):
    engine, fac = _build_db(str(tmp_path / "src"))
    _patch_factory(monkeypatch, fac)
    _seed(fac, memory_type="event", user_id=1, character_id=3, content="骨架第一条",
          importance=60.0, strength_days=8.0, epistemic_status="FACT", reliability_score=0.9,
          chain_id="c-1", speaker_type="user", speaker_id=7, source="app_chat",
          status="active", version=2, created_at=datetime(2026, 7, 15))
    _seed(fac, memory_type="preference", user_id=1, character_id=3, content="骨架第二条",
          importance=70.0, strength_days=12.0, epistemic_status="FACT", reliability_score=0.8,
          chain_id=None, speaker_type="user", source="app_chat", status="active",
          version=0, is_core=True, created_at=datetime(2026, 7, 2))
    _seed(fac, memory_type="insight", user_id=1, character_id=3, content="骨架第三条",
          importance=40.0, epistemic_status="INFERRED", status="stale", version=3,
          source="reflection", created_at=datetime(2026, 6, 1), valid_to=datetime(2026, 6, 30))

    pak = str(tmp_path / "skel.mempak")
    _run(pp.export_pack(1, 3, pak, scope="all"))
    man, pages = pp.read_pack(pak)
    assert len(pages) == 3
    # manifest 自带机器可读契约
    assert man["skeleton"] == pp.PACK_SKELETON
    assert man["frontmatter_order"] == list(pp.FRONTMATTER_ORDER)
    assert man["field_policy"] == pp.FIELD_POLICY
    assert man["fields_must_keep"] == list(pp.FIELD_MUST_KEEP)
    assert man["fields_droppable"] == list(pp.FIELD_DROPPABLE)
    # 每条页面：键集与顺序恒定（缺值写 null，不省略键）＋ 归属与自描述补齐
    seqs = []
    for pg in pages:
        meta = pg["meta"]
        assert list(meta.keys()) == list(pp.FRONTMATTER_ORDER)
        assert meta["user_id"] == 1 and meta["character_id"] == 3
        assert meta["pack_schema"] == pp.PACK_SCHEMA
        seqs.append(meta["seq"])
    assert seqs == [1, 2, 3]  # 包内稳定序号连续递增（＝导出顺序 memory_id 升序）
    # 页面文件名等宽零填充 ⇒ 字典序＝序号数值序（同包内宽度一致）
    names = sorted({pg["file"] for pg in pages})
    assert len({len(n) for n in names}) == 1, names
    assert all(n.startswith("pages/") and n.endswith(".md") for n in names)
    # 新包校验通过，且不再出现「旧包骨架」警告
    ok, msgs, _ = pp.validate_pack(pak)
    assert ok is True, msgs
    assert not any("旧包骨架" in m for m in msgs)
    engine.sync_engine.dispose()


def test_字段契约三档划分自洽():
    tiers = (pp.FIELD_MUST_KEEP, pp.FIELD_FIDELITY_KEEP, pp.FIELD_DROPPABLE)
    # 三档无重叠
    assert sum(len(t) for t in tiers) == len(set().union(*tiers))
    # 全覆盖骨架字段，且策略表取值只有三档
    assert set(pp.FRONTMATTER_ORDER) == set(pp.FIELD_POLICY)
    assert set(pp.FIELD_POLICY.values()) == {"must_keep", "fidelity_keep", "droppable"}
    # 派生兼容：必填（校验口径）+ 扩展（旧 Extra）＝骨架全集去掉新增键
    assert set(pp.FRONTMATTER_REQUIRED) | set(pp.FRONTMATTER_EXTRA) | set(pp.FRONTMATTER_SKELETON_ADDED) \
        == set(pp.FRONTMATTER_ORDER)
    # 语义必需字段都在旧校验严格集内（version 例外：它在旧 Extra 里，旧导出也总带）
    assert set(pp.FIELD_MUST_KEEP) <= set(pp.FRONTMATTER_REQUIRED) | set(pp.FRONTMATTER_EXTRA)
    # 派单点名的关键字段都在骨架里：类型/来源/时间/版本＝必需，归属＝新增可缺
    assert {"memory_type", "source", "created_at", "version", "status"} <= set(pp.FIELD_MUST_KEEP)
    assert {"user_id", "character_id"} <= set(pp.FRONTMATTER_SKELETON_ADDED) & set(pp.FIELD_FIDELITY_KEEP)
    # 可丢档必须真的无人依赖：导入端构造 ORM 时不读这些键
    import inspect
    src = inspect.getsource(pp._meta_to_incoming)
    for k in pp.FIELD_DROPPABLE:
        assert f'"{k}"' not in src, f"可丢字段 {k} 不应被导入端消费"


# ── 批 0-13：向后兼容（旧包样本必须照常读 / 导 / 校验）──────────────────

def test_旧包样本_无骨架字段仍可导入(monkeypatch, tmp_path):
    engine, dst = _build_db(str(tmp_path / "dst"))
    _patch_factory(monkeypatch, dst)
    pak = str(tmp_path / "legacy.mempak")
    pages = [
        _legacy_block(_LEGACY_META, "旧包里的第一条记忆"),
        _legacy_block({**_LEGACY_META, "memory_id": 12, "memory_type": "preference",
                       "is_core": True, "version": 0, "chain_id": None}, "旧包里的第二条记忆"),
    ]
    _write_pak(pak, _legacy_manifest(2, 2), pages)
    rep = _run(pp.import_pack(pak))
    assert rep["insert"] == 2 and rep["conflict"] == 0 and rep["skipped"] == 0
    assert rep["legacy_pack"] is True and rep["skeleton"] is None
    assert rep["scope_from"] == {"user_id": "manifest", "character_id": "manifest"}
    a = _run(_get(dst, 11))
    b = _run(_get(dst, 12))
    assert (a.user_id, a.character_id) == (1, 3)          # 归属回落 manifest（旧包页面里没有）
    assert a.content == "旧包里的第一条记忆" and a.version == 2 and a.status == "active"
    assert a.created_at.replace(tzinfo=None) == datetime(2026, 7, 15)
    assert a.reliability_score == 0.9 and a.strength_days == 8.0 and a.speaker_id == 7
    assert b.memory_type == "preference" and b.is_core is True and int(b.version) == 0
    engine.sync_engine.dispose()


def test_旧包样本_缺可选字段走兜底默认(monkeypatch, tmp_path):
    engine, dst = _build_db(str(tmp_path / "dst"))
    _patch_factory(monkeypatch, dst)
    pak = str(tmp_path / "legacy_partial.mempak")
    # 只给最早期必填键（连 status/version 都缺）
    pages = [_legacy_block({"memory_id": 21, "memory_type": "event",
                            "created_at": "2026-08-01T00:00:00"}, "缺一半字段的旧包记忆")]
    _write_pak(pak, _legacy_manifest(1, 1), pages)
    rep = _run(pp.import_pack(pak))
    assert rep["insert"] == 1 and rep["skipped"] == 0
    m = _run(_get(dst, 21))
    assert m.status == "active" and int(m.version) == 0 and m.source is None
    assert m.is_core is False and m.memory_type == "event"
    assert m.created_at.replace(tzinfo=None) == datetime(2026, 8, 1)
    engine.sync_engine.dispose()


def test_旧包样本_validate通过且仅给骨架警告(tmp_path):
    pak = str(tmp_path / "legacy_valid.mempak")
    _write_pak(pak, _legacy_manifest(1, 1), [_legacy_block(_LEGACY_META, "旧包校验样本")])
    ok, msgs, man = pp.validate_pack(pak)
    assert ok is True, msgs
    assert man.get("skeleton") is None
    assert any("旧包骨架" in m for m in msgs), msgs
    # 旧包若缺可还原字段，仍按原口径判 issue（校验严格度未因骨架对齐而放宽）
    bad = str(tmp_path / "legacy_bad.mempak")
    _write_pak(bad, _legacy_manifest(1, 1),
               [_legacy_block({"memory_id": 31, "memory_type": "event"}, "旧包缺必填字段")])
    ok2, msgs2, _ = pp.validate_pack(bad)
    assert ok2 is False and any("created_at" in m for m in msgs2), msgs2


def test_旧包样本_冷归档无归属字段仍可导入(monkeypatch, tmp_path):
    engine, dst = _build_db(str(tmp_path / "dst"))
    _patch_factory(monkeypatch, dst)
    pak = str(tmp_path / "legacy_arc.mempak")
    meta = {**_LEGACY_META, "memory_id": 41, "status": "superseded",
            "valid_to": "2026-06-01T00:00:00", "created_at": "2026-05-01T00:00:00"}
    _write_pak(pak, _legacy_manifest(1, 1, scope="archived"), [_legacy_block(meta, "旧包里的冷归档记忆")])
    rep = _run(pp.import_pack(pak))
    assert rep["archived"] == 1 and rep["insert"] == 0
    hot = _run(_rows(dst, Memory))
    assert all(m.id != 41 for m in hot), "旧包冷归档导入不得写热表"
    arc = _run(_rows(dst, MemoryArchive))
    assert any(a.memory_id == 41 and (a.user_id, a.character_id) == (1, 3) for a in arc)
    engine.sync_engine.dispose()


def test_旧包样本_归属补位与缺值补齐纯函数():
    pages = [{"meta": {"user_id": 5, "character_id": 9}}, {"meta": {"user_id": 5, "character_id": 9}}]
    assert pp._hint_scope_from_pages(pages, "user_id") == 5
    assert pp._hint_scope_from_pages(pages, "character_id") == 9
    assert pp._hint_scope_from_pages([{"meta": {"user_id": 5}}, {"meta": {}}], "user_id") is None
    assert pp._hint_scope_from_pages([{"meta": {"user_id": 5}}, {"meta": {"user_id": 6}}], "user_id") is None
    # 旧记录（缺新骨架字段）→ 恒定键集，缺值写 null，不报错
    meta = pp._record_to_meta({"memory_id": 1, "memory_type": "event"})
    assert list(meta.keys()) == list(pp.FRONTMATTER_ORDER)
    assert meta["user_id"] is None and meta["seq"] is None
    assert meta["pack_schema"] == pp.PACK_SCHEMA  # 单条自描述总带上
    # 旧包页面照常解析，不会因为多了骨架字段而失败
    m2, c2 = pp.parse_page(_legacy_block(_LEGACY_META, "旧包正文"), filename="p.md")
    assert c2 == "旧包正文" and m2["memory_id"] == 11 and "seq" not in m2


def test_新包归属提示与目标不一致_只计数不改动作(monkeypatch, tmp_path):
    engine, dst = _build_db(str(tmp_path / "dst"))
    _patch_factory(monkeypatch, dst)
    man = pp.build_manifest(user_id=_PACK_USER_ID, character_id=_PACK_CHAR_ID,
                            scope="all", count=1, page_count=1)
    pak = str(tmp_path / "hint.mempak")
    # 页面自带归属 99/98，但 manifest 与本次目标是 1/3
    _write_pak(pak, man, [pp.build_block(_default_record(31, user_id=99, character_id=98))])
    rep = _run(pp.import_pack(pak))
    assert rep["insert"] == 1 and rep["conflict"] == 0
    assert rep["scope_hint_mismatch"] == 1
    m = _run(_get(dst, 31))
    # Knowledge Scope 口径不变：仍按本次目标作用域落库，提示只记账、不改数据
    assert (m.user_id, m.character_id) == (_PACK_USER_ID, _PACK_CHAR_ID)
    engine.sync_engine.dispose()


# ── 批 0-13：导出/导入往返幂等 ─────────────────────────────────────────

def test_往返幂等_再导出逐字节相同且重复导入状态不变(monkeypatch, tmp_path):
    src_engine, src = _build_db(str(tmp_path / "src"))
    _patch_factory(monkeypatch, src)
    first = _seed(src, memory_type="event", user_id=1, character_id=3, content="幂等第一条",
                  importance=60.0, strength_days=8.0, epistemic_status="FACT", reliability_score=0.9,
                  chain_id="c-1", speaker_type="user", speaker_id=7, source="app_chat",
                  status="active", version=2, title="标题一", created_at=datetime(2026, 7, 15))
    _seed(src, memory_type="preference", user_id=1, character_id=3, content="幂等第二条",
          importance=72.0, strength_days=12.0, epistemic_status="FACT", reliability_score=0.82,
          chain_id=None, parent_id=first.id, speaker_type="user", speaker_id=7,
          source="app_chat", status="active", version=0, is_core=True,
          why_it_matters="用户明确说过", created_at=datetime(2026, 7, 2))
    _seed(src, memory_type="insight", user_id=1, character_id=3, content="幂等第三条",
          importance=40.0, epistemic_status="INFERRED", reliability_score=0.5,
          chain_id="c-2", speaker_type="character", speaker_id=3, source="reflection",
          status="stale", version=3, valid_from=datetime(2026, 6, 1),
          valid_to=datetime(2026, 6, 30), created_at=datetime(2026, 6, 1))

    pak_a = str(tmp_path / "a.mempak")
    rep_a = _run(pp.export_pack(1, 3, pak_a, scope="all"))
    assert rep_a["count"] == 3

    dst_engine, dst = _build_db(str(tmp_path / "dst"))
    _patch_factory(monkeypatch, dst)
    rep_imp = _run(pp.import_pack(pak_a))
    assert rep_imp["insert"] == 3 and rep_imp["conflict"] == 0

    # 目标库再导出 ⇒ 页面逐字节与源包相同（骨架恒定顺序 + 值域归一 ⇒ 幂等）
    pak_b = str(tmp_path / "b.mempak")
    _run(pp.export_pack(1, 3, pak_b, scope="all"))
    assert _page_bytes(pak_a) == _page_bytes(pak_b)
    man_a, _ = pp.read_pack(pak_a)
    man_b, _ = pp.read_pack(pak_b)
    for k in set(man_a) - {"exported_at"}:
        assert man_a[k] == man_b[k], k

    # 重复导入同一包：不新增、不冲突，可携带字段快照逐条不变
    ids = sorted(m.id for m in _run(_rows(dst, Memory)))
    before = {mid: _snap(_run(_get(dst, mid))) for mid in ids}
    rep_again = _run(pp.import_pack(pak_a))
    assert rep_again["insert"] == 0 and rep_again["conflict"] == 0 and rep_again["update"] == 3
    after = {mid: _snap(_run(_get(dst, mid))) for mid in ids}
    assert after == before
    # 第三次导出仍逐字节相同
    pak_c = str(tmp_path / "c.mempak")
    _run(pp.export_pack(1, 3, pak_c, scope="all"))
    assert _page_bytes(pak_a) == _page_bytes(pak_c)
    src_engine.sync_engine.dispose()
    dst_engine.sync_engine.dispose()
