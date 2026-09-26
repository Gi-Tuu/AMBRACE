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
        assert model.__table__.c.api_key.type.__class__ is cc.EncryptedText, table
    # DDL 不得漂移（P3-1：凭据列一律 TEXT，不再带长度）
    from sqlalchemy.dialects import sqlite

    d = sqlite.dialect()
    assert ApiConfig.__table__.c.api_key.type.compile(dialect=d) == "TEXT"
    assert UserLlmConfig.__table__.c.api_key.type.compile(dialect=d) == "TEXT"


def test_encrypted_text_ddl_is_text(keyfile):
    """EncryptedText 的 DDL 方言类型必须是 TEXT（无长度上限，密文膨胀不再受长度假设约束）。"""
    from sqlalchemy.dialects import sqlite

    d = sqlite.dialect()
    assert cc.EncryptedText().compile(dialect=d) == "TEXT"
    # 类型层不再携带长度（EncryptedString(255) 那种「按明文长度设列宽」的假设已废除）
    assert getattr(cc.EncryptedText(), "length", None) is None
    assert cc.EncryptedString(255).compile(dialect=d) == "VARCHAR(255)"
    # cache_ok 不继承父类（SQLAlchemy 只读类自身 __dict__）⇒ 子类必须自己声明，否则语句不产缓存键
    assert cc.EncryptedText.cache_ok is True


def test_encrypted_text_roundtrips_long_plaintext(keyfile):
    """明文 ≥ 500 字符（含中文）经 bind/result 往返必须原样解回。"""
    from sqlalchemy.dialects import sqlite

    col = cc.EncryptedText()
    plain = "sk-密钥-" + "密钥x" * 300  # 900+ 字符，含中文
    stored = col.process_bind_param(plain, sqlite.dialect())
    assert stored.startswith(cc.PREFIX) and plain not in stored
    assert len(stored) > len(plain), "密文比明文更长：旧的 VARCHAR(255)/VARCHAR(500) 假设站不住"
    assert col.process_result_value(stored, sqlite.dialect()) == plain


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


# ── ⑦ 主密钥健康探测（P3-7，2026-09-26 全量审查批 C/D）────────────────────────
# 守的底线：密钥文件被删/被换时，「既有密文解不开」必须**主动**暴露（启动/巡检探测 + 内存快照
# + 每进程一次 ERROR），而不是等下一次业务调用把凭据静默判空（用户视角＝「Key 凭空丢了」）。

@pytest.fixture()
def probe_state(monkeypatch):
    """复位模块级快照与告警去重集合（二者是进程内全局，逐例隔离防互相污染）。"""
    monkeypatch.setattr(cc, "_LAST_PROBE", {})
    monkeypatch.setattr(cc, "_ALERTED", set())


def test_探测只统计密文样本_明文与空值不计入(keyfile, probe_state):
    mixed = [cc.encrypt("sk-a"), "sk-legacy-plain", None, "", cc.encrypt("sk-b")]
    result = cc.probe_ciphertexts(mixed)
    assert (result["checked"], result["failed"], result["ok"]) == (2, 0, True), "明文解得开，与密钥健康无关"
    assert cc.probe_ciphertexts(["plain", None, ""])["checked"] == 0
    assert cc.probe_ciphertexts([])["ok"] is True, "无样本＝无证据，不报警"


def test_换主密钥后探测判失败且样本全数解不开(keyfile, tmp_path, monkeypatch, probe_state):
    stored = [cc.encrypt("sk-1"), cc.encrypt("sk-2"), "sk-plain"]
    monkeypatch.setenv(cc.KEY_ENV_VAR, str(tmp_path / "rotated.key"))
    cc.load_or_create_master_key()          # ＝密钥文件丢失后当场重建（换机/误清 data 目录）
    result = cc.probe_ciphertexts(stored)
    assert result["ok"] is False
    assert result["failed"] == result["checked"] == 2
    assert "解不开" in result["detail"]


def test_快照按值返回且同一结论只告警一次(keyfile, tmp_path, monkeypatch, probe_state, caplog):
    ct = cc.encrypt("sk-alert-once")
    assert cc.last_probe()["checked"] == 0, "未探测前回报「尚未探测」，不能凭空判健康"
    monkeypatch.setenv(cc.KEY_ENV_VAR, str(tmp_path / "rotated2.key"))
    cc.load_or_create_master_key()
    bad = cc.probe_ciphertexts([ct])
    with caplog.at_level(logging.ERROR, logger="app.credential_crypto"):
        for _ in range(3):                   # 模拟每日巡检重复探到同一结论
            cc.record_probe(bad)
    alerts = [r for r in caplog.records if r.levelno >= logging.ERROR and "主密钥不可用" in r.getMessage()]
    assert len(alerts) == 1, "同一结论本进程只报一次（巡检不得刷屏）"
    assert cc.KEY_ENV_VAR in alerts[0].getMessage(), "告警必须指向密钥文件这个根因"
    snap = cc.last_probe()
    assert snap == bad and snap is not bad, "快照按副本返回，防调用方改坏内部状态"
    with caplog.at_level(logging.ERROR, logger="app.credential_crypto"):
        cc.record_probe(cc.probe_ciphertexts([cc.encrypt("sk-ok")]))
    assert cc.last_probe()["ok"] is True, "换回可用密钥后快照跟着恢复"


def test_启动探测读原始密文而非ORM自动解密(clone_db, monkeypatch, probe_state):
    """端到端：探测必须绕过 EncryptedText 的 result 处理器，否则解不开时拿到 None→被过滤→永远假绿。"""
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
    secret = "sk-探测密钥-abc"

    async def _seed():
        async with factory() as db:
            db.add(User(id=7301, username="u7301", nickname="u"))
            await db.flush()
            for model in (ApiConfig, VlmConfig, SpeechConfig, MultimodalConfig, ImageGenConfig):
                db.add(model(user_id=0, api_key=secret))
            db.add(TaskLlmConfig(user_id=7301, task="memory", api_key=secret))
            db.add(UserLlmConfig(user_id=7301, name="cfg-1", api_key=secret))
            await db.commit()

    asyncio.run(_seed())
    monkeypatch.setattr("app.db.database.async_session_factory", factory)

    targets = set(cc.credential_probe_targets())
    known = {(t, "api_key") for t in (
        "api_configs", "vlm_configs", "speech_configs", "multimodal_configs",
        "image_gen_configs", "task_llm_configs", "user_llm_configs")}
    assert targets >= known, f"凭据列扫描漏了表：{sorted(known - targets)}（新表应自动进入探测面）"

    healthy = asyncio.run(cc.probe_stored_credentials())
    assert healthy["ok"] is True and healthy["checked"] == len(targets), healthy
    assert cc.last_probe() == healthy, "启动探测结论必须落进 /liveness 读的快照"

    monkeypatch.setenv(cc.KEY_ENV_VAR, str(_db_path.parent / "probe-rotated.key"))
    cc.load_or_create_master_key()
    broken = asyncio.run(cc.probe_stored_credentials())
    assert broken["ok"] is False and broken["failed"] == broken["checked"] == len(targets), (
        f"换密钥后必须逐表判失败（拿到 checked=0 说明探测走了 ORM 自动解密＝假绿）：{broken}"
    )


def test_启动探测异常只降级不抛(monkeypatch, probe_state):
    def _boom(*_a, **_kw):
        raise RuntimeError("模拟会话工厂不可用")

    monkeypatch.setattr("app.db.database.async_session_factory", _boom)
    result = asyncio.run(cc.probe_stored_credentials())
    assert result["checked"] == 0 and result["failed"] == 0 and result["ok"] is True
    assert result["detail"].startswith("probe error:")
    assert cc.last_probe()["detail"].startswith("probe error:")


def test_liveness转述快照但不因密钥故障判stalled(probe_state, monkeypatch):
    """边界（P3-7）：/liveness 只**转述**内存快照。密文解不开是数据事故不是进程故障——
    翻转 stalled 会驱动 watchdog 二级判断/自动重启，既修不好密钥又掩盖真因，故必须为 False。"""
    from app.api import system as system_api
    from app.utils.supervisor import supervisor

    bad = {"checked": 3, "failed": 3, "ok": False, "detail": "3/3 个密文样本解不开，首个原因：GCM 认证失败"}
    cc.record_probe(bad)
    monkeypatch.setattr(supervisor, "liveness", lambda: {})   # 循环心跳置空，排除本例无关的停摆源

    def _no_db(*_a, **_kw):
        raise RuntimeError("本例不连库")

    monkeypatch.setattr("app.db.database.async_session_factory", _no_db)
    info = asyncio.run(system_api._liveness_detail())
    assert info["credentials"] == bad, "明细必须原样转述快照（零 DB 查询）"
    assert info["stalled"] is False, "密钥故障不得翻转 stalled"
