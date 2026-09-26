# -*- coding: utf-8 -*-
"""A8 方案 B · 本地凭据信封加密原语（本机主密钥文件 + AES-256-GCM）。

用户已接受的前提：**主密钥丢失＝凭据重填**（不做 DPAPI、不做云 KMS）。

密文格式：``enc:v1:<b64(nonce)>:<b64(ciphertext)>``
  - AES-256-GCM，nonce 12 字节随机（同一明文两次加密密文必然不同）
  - ``aad``（附加认证数据）用字段名，DB 侧一律 ``api_key``（见 :data:`AAD_FIELD`）

四条口径（写死在此，全项目唯一出口）：
  1. **读兼容**：不以 ``enc:v1:`` 开头 ⇒ 视为明文原样返回（迁移前的老数据照常可用）。
  2. **读失败**：以 ``enc:v1:`` 开头但解不开（密钥被换/内容被篡改）⇒ 记 ERROR 并按「空」
     处理（＝该凭据需重填）；**绝不静默当明文**，也绝不把异常抛到启动/请求链路。
  3. **写幂等**：已是 ``enc:v1:`` 的值不重复加密。
  4. **写失败保可用**：主密钥不可用（只读目录等）时记 ERROR 并原样写入明文——
     可用性优先于保密性，且 :mod:`backend.scripts.encrypt_credentials` 迁移脚本
     事后仍能把残留明文补加密（fail-closed 会让用户「存不进 Key」，属更差的结果）。

密钥文件解析顺序：环境变量 ``AMBRACE_CREDENTIAL_KEY_FILE`` ＞ 默认 ``backend/data/secrets.key``。
默认路径与 JWT 签名密钥（``app/auth/config.py``）同目录同风格；测试/多实例部署用环境变量指向别处。

**禁止**把主密钥、明文凭据写进日志、异常信息或仓库。
"""
from __future__ import annotations

import base64
import binascii
import logging
import os
import re
import secrets
import subprocess
from pathlib import Path
from typing import Any, Iterable, Iterator

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import String, Text
from sqlalchemy.types import TypeDecorator

_logger = logging.getLogger("app.credential_crypto")

PREFIX = "enc:v1:"
KEY_ENV_VAR = "AMBRACE_CREDENTIAL_KEY_FILE"
KEY_BYTES = 32
NONCE_BYTES = 12
AAD_FIELD = "api_key"  # DB 凭据列的 aad（列名）；改值会让既有密文全部判空
# JSON 配置里按「字段名」加密（server_config.json 实测只含看门狗/控制台运行态配置，
# 无凭据字段；这里保留能力，命中几处就报几处，不命中则零改动）
JSON_CREDENTIAL_FIELDS = ("api_key",)

DEFAULT_KEY_FILE = Path(__file__).resolve().parents[2] / "data" / "secrets.key"

# (path, mtime_ns, size) -> key：密钥文件被替换时自动失效，不做跨文件混用
_KEY_CACHE: dict[str, tuple[tuple[int, int], bytes]] = {}


class CredentialCryptoError(Exception):
    """主密钥不可用/不可信，或密文结构非法（strict 解密时抛出）。"""


def key_file_path() -> Path:
    """主密钥文件路径（环境变量优先，可指向仓库外）。"""
    raw = (os.environ.get(KEY_ENV_VAR) or "").strip()
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_KEY_FILE


def _decode_key(raw_text: str) -> bytes:
    try:
        key = base64.b64decode(raw_text.strip().encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError) as e:
        raise CredentialCryptoError(f"密钥文件内容非法（base64 解析失败）: {e}") from e
    if len(key) != KEY_BYTES:
        raise CredentialCryptoError(f"密钥文件长度非法（期望 {KEY_BYTES} 字节，实得 {len(key)}）")
    return key


def _harden_key_file_permissions(path: Path) -> None:
    """尽力收紧密钥文件权限；任何失败只记 WARNING（不同系统/文件系统差异大，不因此拒绝工作）。"""
    try:
        os.chmod(path, 0o600)
    except Exception as e:
        _logger.warning("[credential] 密钥文件 chmod 收紧失败（不影响使用）: %s", e)
    if os.name != "nt":
        return
    user = (os.environ.get("USERDOMAIN") or "").strip()
    name = (os.environ.get("USERNAME") or "").strip()
    account = f"{user}\\{name}" if user and name else name
    if not account:
        return
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:F"],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except Exception as e:
        _logger.warning("[credential] icacls 收紧权限失败（不影响使用）: %s", e)


def load_or_create_master_key(path: str | os.PathLike[str] | None = None) -> bytes:
    """取主密钥；文件不存在时首次使用自动生成（32 字节随机 → base64 一行）。

    文件存在但内容非法时**抛错而不是重新生成**——覆盖密钥等于让既有密文永久失效。
    """
    p = Path(path) if path is not None else key_file_path()
    cache_key = str(p)
    try:
        stat = p.stat()
    except FileNotFoundError:
        stat = None
    if stat is not None:
        cached = _KEY_CACHE.get(cache_key)
        if cached is not None and cached[0] == (stat.st_mtime_ns, stat.st_size):
            return cached[1]
        try:
            key = _decode_key(p.read_text(encoding="utf-8"))
        except OSError as e:
            raise CredentialCryptoError(f"密钥文件读取失败: {e}") from e
        _KEY_CACHE[cache_key] = ((stat.st_mtime_ns, stat.st_size), key)
        return key

    key_bytes = secrets.token_bytes(KEY_BYTES)
    key = base64.b64encode(key_bytes).decode("ascii")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(key + "\n", encoding="utf-8")
        _harden_key_file_permissions(p)
        _logger.info("[credential] 主密钥不存在，已自动生成并持久化: %s", p)
    except OSError as e:
        raise CredentialCryptoError(f"主密钥生成/写入失败: {e}") from e
    try:
        stat = p.stat()
        _KEY_CACHE[cache_key] = ((stat.st_mtime_ns, stat.st_size), key_bytes)
    except OSError:
        pass
    return key_bytes


def is_encrypted(value: Any) -> bool:
    """是否为本模块产出的密文（只看前缀）。"""
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(plaintext: str | None, *, aad: str = AAD_FIELD) -> str | None:
    """加密（幂等）：None/空串原样返回；已是密文原样返回；否则返回 ``enc:v1:...``。"""
    if plaintext is None:
        return None
    text = str(plaintext)
    if not text or is_encrypted(text):
        return text
    nonce = secrets.token_bytes(NONCE_BYTES)
    ct = AESGCM(load_or_create_master_key()).encrypt(
        nonce, text.encode("utf-8"), aad.encode("utf-8")
    )
    return f"{PREFIX}{base64.b64encode(nonce).decode('ascii')}:{base64.b64encode(ct).decode('ascii')}"


def decrypt(value: str | None, *, aad: str = AAD_FIELD, strict: bool = False) -> str | None:
    """解密（兼容读取）。

    非密文 → 原样返回；密文解不开 → ``strict=True`` 抛 :class:`CredentialCryptoError`，
    否则记 ERROR 返回 ``None``（调用方口径＝该凭据按空处理，用户重填）。
    """
    if value is None:
        return None
    text = str(value)
    if not is_encrypted(text):
        return text
    try:
        return _decrypt_strict(text, aad=aad)
    except CredentialCryptoError as e:
        if strict:
            raise
        _logger.error("[credential] 凭据解密失败，已按空值处理（需重新填写）: %s", e)
        return None


def _decrypt_strict(text: str, *, aad: str = AAD_FIELD) -> str:
    body = text[len(PREFIX):]
    parts = body.split(":")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise CredentialCryptoError("密文结构非法（nonce/ciphertext 段缺失）")
    try:
        nonce = base64.b64decode(parts[0].encode("ascii"), validate=True)
        ct = base64.b64decode(parts[1].encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError) as e:
        raise CredentialCryptoError(f"密文 base64 解析失败: {e}") from e
    if len(nonce) != NONCE_BYTES:
        raise CredentialCryptoError(f"nonce 长度非法（期望 {NONCE_BYTES} 字节，实得 {len(nonce)}）")
    try:
        plain = AESGCM(load_or_create_master_key()).decrypt(
            nonce, ct, aad.encode("utf-8")
        )
    except Exception as e:
        raise CredentialCryptoError(f"GCM 认证失败（密钥不符或内容被篡改）: {e.__class__.__name__}") from e
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError as e:
        raise CredentialCryptoError("明文解码失败") from e


# ── JSON 配置侧（字段名即 aad）──────────────────────────────────────────────

def _walk_json_fields(obj: Any, fields: tuple[str, ...]) -> Iterator[tuple[dict, str]]:
    """递归遍历 dict/list，产出「键名命中 fields」的 (容器, 键)。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k in fields:
                yield obj, k
            yield from _walk_json_fields(v, fields)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_json_fields(item, fields)


def count_json_credential_fields(
    doc: Any, fields: tuple[str, ...] = JSON_CREDENTIAL_FIELDS
) -> tuple[int, int]:
    """只统计不改动：返回 (明文处数, 已密文处数)。"""
    plain = encrypted = 0
    for container, key in list(_walk_json_fields(doc, tuple(fields))):
        value = container.get(key)
        if is_encrypted(value):
            encrypted += 1
        elif isinstance(value, str) and value:
            plain += 1
    return plain, encrypted


def encrypt_json_document(
    doc: Any, fields: tuple[str, ...] = JSON_CREDENTIAL_FIELDS
) -> tuple[Any, int]:
    """就地加密 JSON 文档里的凭据字段（幂等）。返回 (doc, 本次加密处数)。"""
    changed = 0
    for container, key in list(_walk_json_fields(doc, tuple(fields))):
        value = container.get(key)
        if value is None or isinstance(value, (dict, list)):
            continue  # 只处理标量凭据字段
        encrypted = encrypt(value, aad=key)
        if encrypted != value:
            container[key] = encrypted
            changed += 1
    return doc, changed


def decrypt_json_document(
    doc: Any, fields: tuple[str, ...] = JSON_CREDENTIAL_FIELDS
) -> tuple[Any, int, int]:
    """就地解密 JSON 文档里的凭据字段（非密文原样保留）。返回 (doc, 解出处数, 失败判空数)。"""
    decrypted = failed = 0
    for container, key in list(_walk_json_fields(doc, tuple(fields))):
        value = container.get(key)
        if not is_encrypted(value):
            continue
        plain = decrypt(value, aad=key)
        if plain is None:
            failed += 1
        else:
            decrypted += 1
        container[key] = plain
    return doc, decrypted, failed


# ── ORM 收敛点：挂在凭据列定义上，读写全库自动加解密 ──────────────────────────

class EncryptedString(TypeDecorator):
    """把 String 列的值在「绑定写入」时加密、「读取结果」时解密（DDL 仍是 VARCHAR(n)，带长度上限）。

    凭据列请改用 :class:`EncryptedText`（DDL = TEXT）；本类保留给非凭据场景与作为父类。
    AAD 固定取列语义（:attr:`AAD_FIELD`），与 encrypt_credentials.py 迁移脚本同口径，
    否则脚本搬进去的密文读不出来。
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        text = str(value)
        try:
            return encrypt(text, aad=AAD_FIELD)
        except Exception as e:
            # 口径 4：存不下总比「用户改不了自己的 Key」好；事后跑迁移脚本可补加密
            _logger.error("[credential] 写入加密失败，本条按明文落库（请尽快排查密钥文件）: %s", e)
            return text

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        return decrypt(str(value), aad=AAD_FIELD)


class EncryptedText(EncryptedString):
    """EncryptedString 的 TEXT 版（DDL = TEXT，无长度上限）。

    P3-1（2026-09-26 全量审查）：密文格式 enc:v1:<b64 nonce>:<b64 ct+tag>，字节数约为明文的
    4/3 + 40 ⇒ 按明文长度设 VARCHAR(n) 在 SQLite 上不报错，但在 MySQL/PostgreSQL 严格模式会
    截断或直接报错。凭据列一律用本类型，不再带长度。
    """

    impl = Text
    # cache_ok 只按「类自己的 __dict__」判定（sqlalchemy type_api.py:1290），不继承父类；
    # 漏写会退化成「语句不产缓存键」并打 SAWarning。
    cache_ok = True


# ── 主密钥健康探测（P3-7，2026-09-26 批 C/D）────────────────────────────────────
# 为什么必须主动探一次：:func:`load_or_create_master_key` 在密钥文件**缺失时会当场生成一把
# 新密钥**（新部署开箱可用）。代价是「secrets.key 被删/被换」这种事故不会让启动失败，只会让
# 所有既有密文在 :func:`decrypt` 里静默判空——用户视角＝「模型 Key 凭空没了」，而且要等到
# 下一次真实调用才暴露。这里把结论提前到启动期固化成内存快照，供 /liveness 零成本读出。

_PROBE_SAMPLE_LIMIT = 1          # 每表取 1 条样本即可判「密钥对不对」，不扫全表
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")   # 表/列名白名单（拼进 raw SQL 前校验）
_LAST_PROBE: dict[str, Any] = {}
_ALERTED: set[str] = set()       # 已 ERROR 过的结论，防每 60s 巡检刷同一条告警


def credential_probe_targets() -> list[tuple[str, str]]:
    """待探测的 ``(表名, 列名)``：主 metadata 里所有类型为 :class:`EncryptedString` 的列。

    不写死「7 张凭据表」清单（与 P3-3 同一条理由）：手写清单会随新表过期，而「这列是加密列」
    本身就写在模型的类型声明里——扫 metadata 让新增凭据列自动进入探测面。
    """
    import app.models  # noqa: F401  # 延迟 import：确保全部模型已注册进 Base.metadata
    from app.models.base import Base

    targets = [
        (table.name, col.name)
        for table in Base.metadata.tables.values()
        for col in table.columns
        if isinstance(col.type, EncryptedString)
    ]
    return sorted(targets)


def probe_ciphertexts(values: Iterable[Any], *, aad: str = AAD_FIELD) -> dict:
    """纯函数：给一批库里的原值，回报「主密钥能不能解开」。只统计密文样本（``is_encrypted``）。

    明文样本（迁移前老数据、或写失败回退的明文）不计入——它解得开，与密钥健康无关。
    返回 ``{checked, failed, ok, detail}``；``checked=0`` 视为 ok（无证据＝不报警）。
    """
    samples = [str(v) for v in values if v is not None and is_encrypted(str(v))]
    checked = len(samples)
    if checked == 0:
        return {"checked": 0, "failed": 0, "ok": True, "detail": "无密文样本（全新部署/凭据仍是明文）"}
    failed = 0
    first_reason = ""
    for sample in samples:
        try:
            _decrypt_strict(sample, aad=aad)
        except CredentialCryptoError as e:
            failed += 1
            if not first_reason:
                first_reason = str(e)
    ok = failed == 0
    detail = (
        f"{checked} 个密文样本全部可解" if ok
        else f"{failed}/{checked} 个密文样本解不开，首个原因：{first_reason}"
    )
    return {"checked": checked, "failed": failed, "ok": ok, "detail": detail}


def record_probe(result: dict) -> dict:
    """固化一次探测结论（内存快照）；失败结论按「同一结论每进程只 ERROR 一次」上报。"""
    global _LAST_PROBE
    _LAST_PROBE = dict(result)
    if not result.get("ok", True):
        conclusion = str(result.get("detail") or "")
        if conclusion not in _ALERTED:
            _ALERTED.add(conclusion)
            _logger.error(
                "[credential] 主密钥不可用：已有凭据密文解不开（%s）。请确认 %s 指向的密钥文件"
                "是否被替换或删除；解不开的凭据在业务侧一律按空值处理，需用户重新填写。"
                "同一结论本进程只报一次。",
                conclusion, KEY_ENV_VAR,
            )
    return _LAST_PROBE


def last_probe() -> dict:
    """最近一次探测快照（**零 DB 查询**，供 /liveness 直接读）。未探测过时回报「未知」。"""
    if not _LAST_PROBE:
        return {"checked": 0, "failed": 0, "ok": True, "detail": "本进程尚未探测（启动探测未跑）"}
    return dict(_LAST_PROBE)


async def probe_stored_credentials() -> dict:
    """每张凭据表取 1 条非空原值跑一次探测；整段 try 隔离，任何异常只降级、绝不抛。

    这里**必须**走 raw SQL：``EncryptedString``/``EncryptedText`` 会在 ORM 读取时自动解密，
    用 ORM 取列拿到的是明文（解不开时是 ``None``），探测就永远「一切正常」。
    """
    try:
        from sqlalchemy import text
        from app.db.database import async_session_factory

        samples: list[str] = []
        async with async_session_factory() as db:
            for table, column in credential_probe_targets():
                if not (_IDENT_RE.match(table) and _IDENT_RE.match(column)):
                    continue
                try:
                    rows = (await db.execute(text(
                        f'SELECT "{column}" FROM "{table}" '
                        f'WHERE "{column}" IS NOT NULL AND "{column}" <> \'\' '
                        f'LIMIT {_PROBE_SAMPLE_LIMIT}'
                    ))).all()
                except Exception as e:  # 单表缺表/权限异常不影响其余表结论
                    _logger.debug("[credential] 探测跳过 %s.%s: %s", table, column, e)
                    continue
                samples.extend(str(r[0]) for r in rows if r[0] is not None)
        return record_probe(probe_ciphertexts(samples))
    except Exception as e:
        return record_probe({
            "checked": 0, "failed": 0, "ok": True,
            "detail": f"probe error: {e.__class__.__name__}: {e}",
        })


__all__ = [
    "AAD_FIELD",
    "CredentialCryptoError",
    "EncryptedString",
    "EncryptedText",
    "JSON_CREDENTIAL_FIELDS",
    "KEY_ENV_VAR",
    "PREFIX",
    "count_json_credential_fields",
    "credential_probe_targets",
    "decrypt",
    "decrypt_json_document",
    "encrypt",
    "encrypt_json_document",
    "is_encrypted",
    "key_file_path",
    "last_probe",
    "load_or_create_master_key",
    "probe_ciphertexts",
    "probe_stored_credentials",
    "record_probe",
]
