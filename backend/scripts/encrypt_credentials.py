# -*- coding: utf-8 -*-
"""A8 方案 B · 存量凭据一次性加密迁移（默认 --dry-run，不加 --apply 就一个字节都不改）。

它把库里/JSON 里**遗留的明文** api_key 补加密成 ``enc:v1:...``；新写入由
``app/models`` 上的 ``EncryptedString`` 列类型自动加密，本脚本只负责历史数据。
可重复执行（幂等）：已是密文的行跳过，因此中断后重跑安全。

用法（一律用项目 venv 的 python）::

    # ① 先看账（纯只读：mode=ro + PRAGMA query_only，绝不写任何文件）
    backend\\.venv\\Scripts\\python.exe backend\\scripts\\encrypt_credentials.py

    # ② 确认无误后由维护者手跑真迁移（先整库备份，备份失败直接拒绝执行）
    backend\\.venv\\Scripts\\python.exe backend\\scripts\\encrypt_credentials.py --apply
    #   分批：--limit 200 ；换库：--db <路径> ；审计日志落盘：--audit-log <路径>
    #   备份目录：--backup-dir <路径>（缺省 backups/）
    #   JSON 侧（按需显式指定，不给就不碰任何 json）：--json-file backend\\data\\server_config.json

退出码：0=正常；2=前置检查/备份失败（fail-closed 拒绝执行）；3=数据库不存在。

安全口径
--------
- 加密口径必须与列类型一致（AAD 固定 ``api_key``），否则搬进去的密文应用侧读不出来；
  因此 ``--apply`` 结尾会对本次改动的每一行**回读解密校验**，不一致即报错并提示回滚备份。
- 审计日志只记「表 / 行 / 字段 / 动作 / 明文长度」，**永不记录凭据明文或密钥**。
- 主密钥文件（默认 ``backend/data/secrets.key``）不参与本脚本搬迁，也不得进备份包。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.utils import credential_crypto as cc  # noqa: E402

_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")  # 表/列名白名单口径（拼进 SQL，禁止外部输入）

# 凭据列清单（唯一事实源=本清单；新增带 api_key 的表在这里加一行）
CREDENTIAL_TABLES: list[tuple[str, str]] = [
    ("api_configs", "api_key"),
    ("vlm_configs", "api_key"),
    ("speech_configs", "api_key"),
    ("multimodal_configs", "api_key"),
    ("image_gen_configs", "api_key"),
    ("task_llm_configs", "api_key"),
    ("user_llm_configs", "api_key"),
]

DEFAULT_DB = BACKEND_DIR / "data" / "sqlite" / "ai_companion.db"
DEFAULT_BACKUP_DIR = REPO_DIR / "backups"


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def backup_database(db_path: Path, backup_dir: Path) -> Path:
    """用 SQLite 在线备份 API 整库复制（运行中也可安全复制）。失败抛异常 → 调用方 fail-closed。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"{db_path.stem}.pre-a8b-{stamp}.bak"
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return target


def migrate_database(
    db_path: Path,
    *,
    apply: bool,
    limit: int | None = None,
    tables: list[tuple[str, str]] | None = None,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    audit=None,
) -> dict:
    """扫描（可选加密）凭据列。dry-run 只读；apply 前必须先整库备份成功。

    返回统计：to_encrypt / already_encrypted / empty / done / verify_failed / missing_tables。
    """
    audit = audit or (lambda msg: None)
    stats = {
        "to_encrypt": 0,
        "already_encrypted": 0,
        "empty": 0,
        "done": 0,
        "verify_failed": 0,
        "missing_tables": [],
        "backup": None,
    }
    targets = tables if tables is not None else CREDENTIAL_TABLES

    conn = None if apply else _connect_readonly(db_path)
    try:
        if apply:
            stats["backup"] = str(backup_database(db_path, Path(backup_dir)))
            audit(f"[backup] 整库备份完成: {stats['backup']}")
            conn = sqlite3.connect(db_path)
        assert conn is not None
        existing = _existing_tables(conn)
        budget = limit if limit and limit > 0 else None

        for table, column in targets:
            if not (_IDENT_RE.match(table) and _IDENT_RE.match(column)):
                audit(f"[skip] 非法标识符，拒绝处理: {table}.{column}")
                continue
            if table not in existing:
                stats["missing_tables"].append(table)
                audit(f"[skip] 表不存在: {table}")
                continue
            cols = _table_columns(conn, table)
            if column not in cols:
                stats["missing_tables"].append(f"{table}.{column}")
                audit(f"[skip] 列不存在: {table}.{column}")
                continue
            rows = conn.execute(
                f'SELECT rowid, "{column}" FROM "{table}" ORDER BY rowid'
            ).fetchall()
            for rowid, value in rows:
                if budget is not None and stats["done"] >= budget:
                    audit(f"[limit] 达到 --limit {limit}，本行起未处理（可重复执行续跑）")
                    return stats
                if value is None or value == "":
                    stats["empty"] += 1
                    audit(f"[empty] {table} rowid={rowid} {column} 空值，跳过")
                    continue
                if cc.is_encrypted(value):
                    stats["already_encrypted"] += 1
                    audit(f"[skip] {table} rowid={rowid} {column} 已是密文")
                    continue
                stats["to_encrypt"] += 1
                audit(f"[plan] {table} rowid={rowid} {column} 明文 {len(value)} 字符 → 待加密")
                if not apply:
                    continue
                try:
                    encrypted = cc.encrypt(value, aad=cc.AAD_FIELD)
                    conn.execute(
                        f'UPDATE "{table}" SET "{column}" = ? WHERE rowid = ?',
                        (encrypted, rowid),
                    )
                    conn.commit()
                    raw = conn.execute(
                        f'SELECT "{column}" FROM "{table}" WHERE rowid = ?', (rowid,)
                    ).fetchone()[0]
                    if cc.decrypt(raw, aad=cc.AAD_FIELD) != value:
                        stats["verify_failed"] += 1
                        audit(
                            f"[FAIL] {table} rowid={rowid} {column} 回读解密与原文不一致"
                            f"（明文长度 {len(value)}）—— 请停止并考虑回滚 {stats['backup']}"
                        )
                    else:
                        stats["done"] += 1
                        audit(
                            f"[ok] {table} rowid={rowid} {column} 已加密"
                            f"（明文长度 {len(value)}，密文长度 {len(raw)}）"
                        )
                except Exception as e:
                    stats["verify_failed"] += 1
                    audit(f"[FAIL] {table} rowid={rowid} {column} 处理异常: {e.__class__.__name__}")
        if apply:
            conn.commit()
    finally:
        if conn is not None:
            conn.close()
    return stats


def migrate_json_file(
    path: Path,
    fields: tuple[str, ...],
    *,
    apply: bool,
    audit=None,
) -> dict:
    """JSON 配置侧：同样先备份再写（当前 server_config.json 实测无凭据字段 → 0 处改动）。"""
    audit = audit or (lambda msg: None)
    stats = {"file": str(path), "to_encrypt": 0, "already_encrypted": 0, "empty": 0, "done": 0, "backup": None}
    if not path.is_file():
        audit(f"[skip] JSON 不存在: {path}")
        return stats
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        audit(f"[FAIL] JSON 解析失败（不改动）: {path}: {e.__class__.__name__}")
        return stats
    plain_count, enc_count = cc.count_json_credential_fields(doc, tuple(fields))
    stats["to_encrypt"] = plain_count
    stats["already_encrypted"] = enc_count
    audit(f"[plan] {path.name} 命中字段 {list(fields)}：明文 {plain_count} 处 / 已密文 {enc_count} 处")
    if not apply or plain_count == 0:
        return stats
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.name}.pre-a8b-{stamp}.bak")
    shutil.copy2(path, backup)
    stats["backup"] = str(backup)
    enc_doc, changed = cc.encrypt_json_document(doc, tuple(fields))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(enc_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    stats["done"] = changed
    audit(f"[ok] {path.name} 已加密 {changed} 处，原文件备份: {backup}")
    return stats


def format_stats(stats: dict, label: str) -> str:
    return (
        f"{label}: 将加密 {stats['to_encrypt']} 处 / 已是密文 {stats['already_encrypted']} 处"
        f" / 空 {stats['empty']} 处 / 本次完成 {stats['done']} 处"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="A8 方案 B 存量凭据加密（默认 --dry-run 只统计，不加 --apply 不改任何字节）"
    )
    parser.add_argument("--apply", action="store_true", help="真正写入（先整库备份，备份失败即拒绝执行）")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"SQLite 库路径（默认 {DEFAULT_DB}）")
    parser.add_argument("--limit", type=int, default=None, help="本次最多处理多少行（分批用；可重复执行续跑）")
    parser.add_argument("--audit-log", type=Path, default=None, help="审计日志追加到此文件（缺省只打印到 stdout）")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR,
                        help=f"--apply 前置整库备份的目录（默认 {DEFAULT_BACKUP_DIR}）")
    parser.add_argument("--json-file", type=Path, action="append", default=None,
                        help="要一并处理的 JSON 配置文件（可重复；不传则只处理数据库）")
    parser.add_argument("--json-field", type=str, action="append", default=None,
                        help=f"JSON 中按字段名加密（可重复；默认 {list(cc.JSON_CREDENTIAL_FIELDS)}）")
    args = parser.parse_args(argv)

    log_lines: list[str] = []

    def audit(msg: str) -> None:
        log_lines.append(msg)
        print(msg)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== encrypt_credentials（{mode}）===")
    print(f"主密钥文件: {cc.key_file_path()}")
    print(f"目标库: {args.db}")

    json_targets: list[tuple[Path, tuple[str, ...]]] = []
    if args.json_file:
        fields = tuple(args.json_field) if args.json_field else cc.JSON_CREDENTIAL_FIELDS
        json_targets = [(p, fields) for p in args.json_file]

    if not args.db.is_file():
        print(f"[abort] 数据库不存在: {args.db}")
        return 3

    if args.apply:
        try:
            cc.load_or_create_master_key()
        except cc.CredentialCryptoError as e:
            print(f"[abort] 主密钥不可用，fail-closed 拒绝执行: {e}")
            return 2

    db_stats = migrate_database(
        args.db, apply=args.apply, limit=args.limit, backup_dir=args.backup_dir, audit=audit
    )
    print(format_stats(db_stats, "数据库"))
    if db_stats["backup"]:
        print(f"整库备份: {db_stats['backup']}")

    json_stats = [migrate_json_file(p, f, apply=args.apply, audit=audit) for p, f in json_targets]
    for s in json_stats:
        print(
            f"{s['file']}: 将加密 {s['to_encrypt']} 处 / 已是密文 {s['already_encrypted']} 处"
            f" / 本次完成 {s['done']} 处"
        )

    if args.audit_log:
        with args.audit_log.open("a", encoding="utf-8") as f:
            for line in log_lines:
                f.write(line + "\n")
        print(f"审计日志已追加: {args.audit_log}")

    if db_stats["missing_tables"]:
        print(f"提示：缺失表/列 {db_stats['missing_tables']}（未安装对应模块属正常）")
    verify_failed = db_stats["verify_failed"] + sum(s.get("verify_failed", 0) for s in json_stats)
    if args.apply and verify_failed:
        print(f"[FAIL] {verify_failed} 处加密后回读校验不一致——请勿继续使用，必要时回滚上面的整库备份")
        return 2
    if not args.apply:
        print("（DRY-RUN 未改动任何文件；确认数字后由维护者手跑 --apply）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
