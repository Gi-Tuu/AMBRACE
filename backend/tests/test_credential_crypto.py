# -*- coding: utf-8 -*-
"""A8 方案 B · 本地凭据信封加密测试（派单 20260926 §3 五组用例）。

隔离口径（硬约束）：
- 主密钥逐例走 ``tmp_path`` + monkeypatch 环境变量，绝不读写 ``backend/data/`` 下任何文件
  （conftest 已把会话默认密钥指向沙箱，本文件仍自带，防「单独跑本文件」时落到真实 data）；
- 迁移脚本只在临时库/临时 json 上跑，``--backup-dir`` 也指向 ``tmp_path``；
- 配置服务用例走 ``tests/_dbclone`` 模板克隆（不碰生产库）。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from app.application import system as system_svc
from app.utils import credential_crypto as cc

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT_PATH = BACKEND / "scripts" / "encrypt_credentials.py"

SAMPLES = [
    "",                        # 空串（口径：原样返回，不产密文）
    "a",                       # 1 字节
    "sk-abcdef-0123456789",
    "含中文的密钥🔑",            # 多字节 / emoji
    "sk-1,sk-2,sk-3",          # 多 Key 池（逗号分隔）
    "a:b:c",                   # 含分隔符 :（格式必须扛得住）
    "x" * 500,                 # 长值
]


@pytest.fixture()
def keyfile(monkeypatch, tmp_path) -> Path:
    """把主密钥指向本用例独占的 tmp_path 文件（默认不存在 → 首次加密时自动生成）。"""
    path = tmp_path / "secrets.key"
    monkeypatch.setenv(cc.KEY_ENV_VAR, str(path))
    return path


def _load_script():
    """按文件路径加载迁移脚本（backend/scripts 不是包，也不该被 app 导入）。"""
    spec = importlib.util.spec_from_file_location("encrypt_credentials", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── ① 加解密往返 + nonce 随机 ────────────────────────────────────────────────

def test_roundtrip_multi_field_multi_length(keyfile):
    for aad in ("api_key", "token", "secret"):
        for plain in SAMPLES:
            ct = cc.encrypt(plain, aad=aad)
            if plain:
                assert cc.is_encrypted(ct) and ct != plain
            else:
                assert ct == "", "空串不产密文"
            assert cc.decrypt(ct, aad=aad) == plain, (aad, plain[:8])


def test_same_plaintext_produces_different_ciphertext(keyfile):
    plain = "sk-deterministic-input"
    c1 = cc.encrypt(plain)
    c2 = cc.encrypt(plain)
    assert c1 != c2, "nonce 必须随机：同一明文两次加密不能相同"
    assert cc.decrypt(c1) == cc.decrypt(c2) == plain


def test_wrong_aad_is_rejected(keyfile):
    ct = cc.encrypt("sk-x", aad="api_key")
    assert cc.decrypt(ct, aad="other_field") is None, "AAD 绑定字段名：换字段必须解不开"


# ── ② 兼容读取 / 篡改 ────────────────────────────────────────────────────────

def test_plaintext_without_prefix_returns_as_is(keyfile):
    for legacy in ("sk-legacy-plaintext", "", "中文明文"):
        assert cc.decrypt(legacy) == legacy
    assert cc.decrypt(None) is None


def test_tampered_ciphertext_raises_strict_and_is_empty_lenient(keyfile, caplog):
    ct = cc.encrypt("sk-tamper-me")
    head, _, b64ct = ct[len(cc.PREFIX):].partition(":")
    replacement = "A" if b64ct[0] != "A" else "B"
    bad = f"{cc.PREFIX}{head}:{replacement}{b64ct[1:]}"
    assert bad != ct and len(bad) == len(ct), "只翻一个 base64 字符（长度/填充不变）"

    with pytest.raises(cc.CredentialCryptoError):
        cc.decrypt(bad, strict=True)

    with caplog.at_level(logging.ERROR, logger="app.credential_crypto"):
        assert cc.decrypt(bad) is None, "调用方口径＝记 ERROR 后按空值处理，绝不静默当明文"
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


def test_malformed_ciphertext_is_empty_not_plain(keyfile, caplog):
    with caplog.at_level(logging.ERROR, logger="app.credential_crypto"):
        assert cc.decrypt(cc.PREFIX + "notbase64!!:also-not") is None


# ── ③ 密钥文件 ──────────────────────────────────────────────────────────────

def test_master_key_file_created_on_first_use(keyfile):
    assert not keyfile.exists()
    ct = cc.encrypt("sk-bootstrap")
    assert keyfile.is_file(), "不存在时首次使用必须自动生成"
    body = keyfile.read_text(encoding="utf-8")
    assert len(base64.b64decode(body.strip())) == cc.KEY_BYTES
    assert "sk-bootstrap" not in body, "明文凭据不得出现在密钥文件里"
    assert cc.decrypt(ct) == "sk-bootstrap"


def test_same_key_decrypts_previous_round(keyfile):
    ct = cc.encrypt("sk-across-rounds")
    key_again = cc.load_or_create_master_key(keyfile)  # 再取一次＝重启语义（不重新生成）
    assert key_again == base64.b64decode(keyfile.read_text(encoding="utf-8").strip())
    assert cc.decrypt(ct) == "sk-across-rounds"


def test_rotated_key_makes_old_ciphertext_empty(keyfile, tmp_path, monkeypatch, caplog):
    old_ct = cc.encrypt("sk-old-master")
    rotated = tmp_path / "secrets2.key"          # 换主密钥（＝旧密钥丢失后重建）
    monkeypatch.setenv(cc.KEY_ENV_VAR, str(rotated))
    cc.load_or_create_master_key()
    with caplog.at_level(logging.ERROR, logger="app.credential_crypto"):
        assert cc.decrypt(old_ct) is None, "换密钥后旧密文必须判空＝凭据重填"
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


def test_key_replaced_at_same_path_is_not_served_from_cache(keyfile):
    ct = cc.encrypt("sk-v1")
    keyfile.write_text(base64.b64encode(b"\x11" * cc.KEY_BYTES).decode() + "\n", encoding="utf-8")
    st = keyfile.stat()  # 把 mtime 明确挪开，确保测的是「缓存按 mtime/size 失效」而非时间戳巧合
    os.utime(keyfile, ns=(st.st_atime_ns - 5_000_000_000, st.st_mtime_ns - 5_000_000_000))
    assert cc.decrypt(ct) is None, "同路径换密钥：旧密文必须立刻判空"
    assert cc.decrypt(cc.encrypt("sk-v2")) == "sk-v2"


def test_corrupted_key_file_is_not_silently_replaced(keyfile):
    keyfile.parent.mkdir(parents=True, exist_ok=True)
    keyfile.write_text("这不是 base64 密钥\n", encoding="utf-8")
    with pytest.raises(cc.CredentialCryptoError):
        cc.load_or_create_master_key()
    assert "这不是 base64 密钥" in keyfile.read_text(encoding="utf-8"), \
        "密钥文件脏了要报错，不能覆盖——覆盖等于让既有密文永久失效"


# ── ④ 配置服务收敛点 ────────────────────────────────────────────────────────

@pytest.fixture()
def clone_db(tmp_path, keyfile):
    """临时库（模板克隆，见 tests/_dbclone.py）：返回 (会话工厂, 库文件路径)。"""
    if str(BACKEND / "tests") not in sys.path:
        sys.path.insert(0, str(BACKEND / "tests"))
    from _dbclone import clone_engine, make_session_factory

    db_path = tmp_path / "t.db"
    engine = clone_engine(str(db_path))
    yield make_session_factory(engine), db_path
    engine.sync_engine.dispose()


def _raw_api_key(db_path: Path, user_id: int) -> str | None:
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT api_key FROM api_configs WHERE user_id = ?", (user_id,)
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def test_config_service_stores_ciphertext_returns_plaintext(clone_db):
    factory, db_path = clone_db

    async def _run():
        async with factory() as db:
            await system_svc.update_api_config(
                db, {"base_url": "https://api.example.com", "api_key": "sk-live-secret"}, user_id=7101
            )
            return await system_svc.get_api_config(db, 7101)

    view = asyncio.run(_run())
    raw = _raw_api_key(db_path, 7101)
    assert raw.startswith(cc.PREFIX), "落库必须是密文"
    assert "sk-live-secret" not in raw
    assert cc.decrypt(raw) == "sk-live-secret", "应用侧读出来必须是明文"
    assert view["has_api_key"] is True, "服务层判据（bool(api_key)）解密后照常成立"


def test_config_service_is_idempotent_on_reencrypt(clone_db):
    factory, db_path = clone_db

    async def _run():
        from sqlalchemy import select

        from app.models.config import ApiConfig
        async with factory() as db:
            await system_svc.update_api_config(db, {"api_key": "sk-once"}, user_id=7102)
            cipher = _raw_api_key(db_path, 7102)
            # 模拟「把读到的密文原样再写一次」（导出后回灌）：不得二次加密
            await system_svc.update_api_config(db, {"api_key": cipher}, user_id=7102)
            row = (await db.execute(select(ApiConfig).where(ApiConfig.user_id == 7102))).scalar_one()
            return row.api_key

    decrypted = asyncio.run(_run())
    raw = _raw_api_key(db_path, 7102)
    assert raw.startswith(cc.PREFIX)
    assert raw.count(cc.PREFIX) == 1, "已是密文的值不允许再套一层"
    assert decrypted == "sk-once", "写密文进库后读出来仍是原始明文"


def test_all_credential_columns_use_the_encrypted_type():
    """七个凭据列都要挂在同一收敛点上（漏一个＝该表继续明文）。"""
    from app.models.agent import TaskLlmConfig
    from app.models.config import (
        ApiConfig,
        MultimodalConfig,
        SpeechConfig,
        UserLlmConfig,
        VlmConfig,
    )
    from app.models.life import ImageGenConfig

    expected = {
        "api_configs": ApiConfig,
        "vlm_configs": VlmConfig,
        "speech_configs": SpeechConfig,
        "multimodal_configs": MultimodalConfig,
        "image_gen_configs": ImageGenConfig,
        "task_llm_configs": TaskLlmConfig,
        "user_llm_configs": UserLlmConfig,
    }
    for table, model in expected.items():
        assert model.__table__.c.api_key.type.__class__ is cc.EncryptedString, table
    # DDL 不得漂移（零 schema 变更＝不需要迁移）
    from sqlalchemy.dialects import sqlite

    d = sqlite.dialect()
    assert ApiConfig.__table__.c.api_key.type.compile(dialect=d) == "VARCHAR(255)"
    assert UserLlmConfig.__table__.c.api_key.type.compile(dialect=d) == "VARCHAR(500)"


def test_each_credential_column_roundtrips_plaintext(clone_db):
    """逐表写入明文 → 读出明文（FK 依赖用真实 user 行）。"""
    from sqlalchemy import select

    from app.models.agent import TaskLlmConfig
    from app.models.config import (
        ApiConfig,
        MultimodalConfig,
        SpeechConfig,
        UserLlmConfig,
        VlmConfig,
    )
    from app.models.life import ImageGenConfig
    from app.models.user import User

    factory, _db_path = clone_db
    secret = "sk-测试密钥-abc:def"

    async def _run():
        async with factory() as db:
            db.add(User(id=7199, username="u7199", nickname="u"))
            await db.flush()
            for model in (ApiConfig, VlmConfig, SpeechConfig, MultimodalConfig, ImageGenConfig):
                db.add(model(user_id=0, api_key=secret))
            db.add(TaskLlmConfig(user_id=7199, task="memory", api_key=secret))
            db.add(UserLlmConfig(user_id=7199, name="cfg-1", api_key=secret))
            await db.commit()

            out = {}
            for model in (ApiConfig, VlmConfig, SpeechConfig, MultimodalConfig, ImageGenConfig):
                row = (await db.execute(select(model).where(model.user_id == 0))).scalar_one()
                out[model.__tablename__] = row.api_key
            out["task_llm_configs"] = (
                await db.execute(select(TaskLlmConfig).where(TaskLlmConfig.user_id == 7199))
            ).scalar_one().api_key
            out["user_llm_configs"] = (
                await db.execute(select(UserLlmConfig).where(UserLlmConfig.user_id == 7199))
            ).scalar_one().api_key
            return out

    read_back = asyncio.run(_run())
    assert len(read_back) == 7
    for table, value in read_back.items():
        assert value == secret, table


# ── JSON 侧 ─────────────────────────────────────────────────────────────────

def test_json_document_roundtrip_and_counts(keyfile):
    doc = {
        "watchdog_interval_sec": 60,
        "gateways": [{"name": "g", "command": "x", "api_key": "sekret"}],
        "nested": {"api_key": "k2", "keep": "plain-value"},
    }
    assert cc.count_json_credential_fields(doc) == (2, 0)
    enc, changed = cc.encrypt_json_document(doc)
    assert changed == 2
    assert enc["gateways"][0]["api_key"].startswith(cc.PREFIX)
    assert enc["nested"]["keep"] == "plain-value", "非凭据字段一个都不许动"
    assert cc.count_json_credential_fields(enc) == (0, 2)
    dec, n, failed = cc.decrypt_json_document(json.loads(json.dumps(enc)))
    assert (n, failed) == (2, 0)
    assert dec["gateways"][0]["api_key"] == "sekret" and dec["nested"]["api_key"] == "k2"


def test_json_migration_backs_up_before_encrypting(tmp_path, keyfile):
    script = _load_script()
    cfg = tmp_path / "server_config.json"
    original = {"api_key": "sekret", "watchdog_interval_sec": 60}
    cfg.write_text(json.dumps(original), encoding="utf-8")

    assert script.migrate_json_file(cfg, cc.JSON_CREDENTIAL_FIELDS, apply=False)["done"] == 0
    assert json.loads(cfg.read_text(encoding="utf-8")) == original, "dry-run 不改文件"

    stats = script.migrate_json_file(cfg, cc.JSON_CREDENTIAL_FIELDS, apply=True)
    assert stats["done"] == 1 and Path(stats["backup"]).is_file(), "json 侧必须先备份再写"
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    assert doc["api_key"].startswith(cc.PREFIX) and doc["watchdog_interval_sec"] == 60
    assert json.loads(Path(stats["backup"]).read_text(encoding="utf-8")) == original
    assert script.migrate_json_file(cfg, cc.JSON_CREDENTIAL_FIELDS, apply=True)["done"] == 0, \
        "幂等：第二遍 0 改动"


# ── ⑤ 迁移脚本 ──────────────────────────────────────────────────────────────

# (user_id, api_key)：3 处明文 / NULL + 空串各 1 / 含冒号 1
SEED_ROWS = [
    (1, "sk-plain-one"),
    (2, None),
    (3, ""),
    (4, "sk-中文-key"),
    (5, "a:b:c"),
]


def _seed_plain_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE api_configs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER, api_key VARCHAR(255))"
    )
    con.executemany("INSERT INTO api_configs (user_id, api_key) VALUES (?, ?)", SEED_ROWS)
    con.commit()
    con.close()


def _rows(db_path: Path) -> dict:
    con = sqlite3.connect(db_path)
    try:
        return dict(con.execute("SELECT user_id, api_key FROM api_configs ORDER BY id").fetchall())
    finally:
        con.close()


def test_migration_dry_run_touches_nothing(tmp_path, keyfile, capsys):
    script = _load_script()
    db = tmp_path / "plain.db"
    _seed_plain_db(db)
    before = _sha256(db)

    rc = script.main(["--db", str(db), "--backup-dir", str(tmp_path / "bk")])
    assert rc == 0
    assert _sha256(db) == before, "--dry-run 必须一字节都不改"
    assert not (tmp_path / "bk").exists(), "dry-run 不产备份"
    out = capsys.readouterr().out
    assert "将加密 3 处" in out and "已是密文 0 处" in out and "空 2 处" in out
    assert "sk-plain-one" not in out, "审计输出不得含凭据明文"


def test_migration_apply_is_idempotent(tmp_path, keyfile, capsys):
    script = _load_script()
    db = tmp_path / "plain.db"
    _seed_plain_db(db)
    bk = tmp_path / "bk"

    rc = script.main(["--apply", "--db", str(db), "--backup-dir", str(bk)])
    assert rc == 0
    assert len(list(bk.glob("*.bak"))) == 1, "--apply 前必须整库备份一次"

    by_user = _rows(db)
    assert by_user[2] is None and by_user[3] == "", "空值保持为空"
    assert by_user[1].startswith(cc.PREFIX) and cc.decrypt(by_user[1]) == "sk-plain-one"
    assert cc.decrypt(by_user[4]) == "sk-中文-key"
    assert cc.decrypt(by_user[5]) == "a:b:c"
    assert "本次完成 3 处" in capsys.readouterr().out

    rc2 = script.main(["--apply", "--db", str(db), "--backup-dir", str(bk)])
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "将加密 0 处" in out2 and "本次完成 0 处" in out2, "第二遍必须 0 改动（幂等）"
    assert _rows(db) == by_user


def test_migration_limit_processes_batches(tmp_path, keyfile):
    script = _load_script()
    db = tmp_path / "plain.db"
    _seed_plain_db(db)
    stats = script.migrate_database(db, apply=True, limit=1, backup_dir=tmp_path / "bk")
    assert stats["done"] == 1 and stats["to_encrypt"] == 1
    stats2 = script.migrate_database(db, apply=True, limit=5, backup_dir=tmp_path / "bk")
    assert stats2["done"] == 2, "分批可续跑（幂等）"
    assert stats2["already_encrypted"] == 1


def test_migration_reports_missing_tables(tmp_path, keyfile):
    script = _load_script()
    db = tmp_path / "partial.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE api_configs (id INTEGER PRIMARY KEY, api_key VARCHAR(255))")
    con.execute("INSERT INTO api_configs (api_key) VALUES ('sk-x')")
    con.commit()
    con.close()
    stats = script.migrate_database(db, apply=False)
    assert stats["to_encrypt"] == 1
    assert "vlm_configs" in stats["missing_tables"], "未安装的表只提示不报错"


def test_migration_refuses_when_backup_fails(tmp_path, keyfile, monkeypatch):
    script = _load_script()
    db = tmp_path / "plain.db"
    _seed_plain_db(db)
    before = _sha256(db)

    def _boom(*a, **kw):
        raise RuntimeError("模拟整库备份失败")

    monkeypatch.setattr(script, "backup_database", _boom)
    with pytest.raises(RuntimeError):
        script.main(["--apply", "--db", str(db), "--backup-dir", str(tmp_path / "bk")])
    assert _sha256(db) == before, "备份失败必须 fail-closed：库文件一个字节都不改"
    assert not (tmp_path / "bk").exists()
