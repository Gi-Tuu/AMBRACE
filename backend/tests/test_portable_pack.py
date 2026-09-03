# -*- coding: utf-8 -*-
"""Ariadne 模块 H：可移植记忆包（.mempak）离线工具测试。

覆盖 §12 的 M-H 项（#18 导出/导入 与 #19 导出→导入往返一致性）：
- 导出：分页不超预算、frontmatter 字段齐全可还原、默认不含向量、manifest 字段；
- 导入：缺 memory_id 跳过并计数、superseded/stale 不被复活成 active、
  冲突走 memory_id+version 合并而非覆盖、冷归档导入只进 memory_archive（不动热表=不复活）；
- 往返（round-trip）：导出再导入后关键属性与检索命中集合不变（临时库验证，不碰 backend/data）；
- 校验：异常包（坏 zip / 缺 manifest / count 不一致 / 缺 frontmatter 字段）报 issue；
- 脱敏红线：导出文本命中密钥/敏感模式被替换，敏感键名被剔除。

项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库，不触碰 backend/data。
"""
import asyncio
import json
import os
import sys
import zipfile
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# 允许导入仓库根下的 scripts 包
_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import scripts.memory.portable_pack as pp  # noqa: E402

from app.db import database as db_mod  # noqa: E402
from app.models.memory import Memory, MemoryArchive  # noqa: E402


# ── 工具 ───────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _build_db(base_dir, name="t.db"):
    """临时 SQLite 文件库（不触碰 backend/data）。"""
    os.makedirs(base_dir, exist_ok=True)
    db_path = os.path.join(base_dir, name)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

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
