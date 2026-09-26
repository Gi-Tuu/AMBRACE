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
import secrets
import subprocess
from pathlib import Path
from typing import Any, Iterator

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import String
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
    """把 String 列的值在「绑定写入」时加密、「读取结果」时解密（DDL 仍是 VARCHAR(n)，零 schema 变更）。

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


__all__ = [
    "AAD_FIELD",
    "CredentialCryptoError",
    "EncryptedString",
    "JSON_CREDENTIAL_FIELDS",
    "KEY_ENV_VAR",
    "PREFIX",
    "count_json_credential_fields",
    "decrypt",
    "decrypt_json_document",
    "encrypt",
    "encrypt_json_document",
    "is_encrypted",
    "key_file_path",
    "load_or_create_master_key",
]
