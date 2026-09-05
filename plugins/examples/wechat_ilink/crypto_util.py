# -*- coding: utf-8 -*-
"""iLink 凭据（bot_token）加解密工具（PR2 新增）。

【命名空间】渠道插件顶层模块名易撞，本插件沿用包内相对导入/文件名，新渠道须用包内相对导入或模块名前缀（registry 加载器保持现状）。

【api_configs 加密核实结论（2026-09-04，写入注释备查）】
- ``app.models.config.ApiConfig`` 的 ``api_key`` 以**明文**存入 ``String(255)`` 列
  （同理 VlmConfig / SpeechConfig / MultimodalConfig / UserLlmConfig）。
- 现网仅做**响应层脱敏**：读接口不回传明文、只给 ``has_api_key`` 布尔，或经
  ``llm_config_service.mask_api_key`` 输出 ``sk-...abcd`` 掩码。
- 全仓 ``grep cryptography/Fernet/encrypt/decrypt`` 在 app 内**无任何对称加密/密钥管理**实现。
- 结论：**api_configs 存储未加密**，bot_token 不能复用其「明文 + 脱敏」机制。
  故本插件自建 crypto_util.py：Fernet 对称加密，密钥**仅来自环境变量**。

安全约束（P0-4）：
- 密钥只来自环境变量（``AMBRACE_SECRET_KEY`` 优先，``ILINK_TOKEN_KEY`` 兜底）；
  不硬编码、不打进插件包、不进日志、不进异常消息正文、不进前端返回。
- 密钥缺失 → 明确抛 ``RuntimeError``（绝不 fallback 生成随机 key——那会导致重启后无法解密）。
- 环境变量可直接给 44 位 base64 Fernet key；否则用 SHA-256 从口令派生**稳定** key。
- 解密失败（坏 key / 密文损坏）→ 抛 ``RuntimeError``（调用方按 P0-5 异常隔离吞掉，
  绝不能上抛炸掉主链路）。
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

_KEY_ENV_PRI = "AMBRACE_SECRET_KEY"
_KEY_ENV_FALLBACK = "ILINK_TOKEN_KEY"


def _derive_key(raw: str) -> bytes:
    """把环境变量口令派生为稳定 Fernet key（base64url 编码的 32 字节 SHA-256 摘要）。

    允许直接给 44 位 base64 Fernet key（原样使用）；否则 SHA-256 派生，保证每次一致。
    """
    raw = str(raw).strip()
    if not raw:
        raise RuntimeError(f"缺少 {_KEY_ENV_PRI} 环境变量，无法加解密 iLink 凭据")
    try:
        # 原生 Fernet key：44 字符 base64（len==44 在 Fernet() 内部校验），直接使用。
        Fernet(raw.encode())
        return raw.encode()
    except Exception:  # noqa: BLE001 - 非 44 位 base64 → 走派生
        digest = hashlib.sha256(raw.encode()).digest()
        return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    """每次读取环境变量新建 Fernet；无缓存，避免密钥更换后残留旧 key。"""
    raw = os.environ.get(_KEY_ENV_PRI) or os.environ.get(_KEY_ENV_FALLBACK) or ""
    return Fernet(_derive_key(raw))


def encrypt(secret: str) -> str:
    """加密明文返回密文字符串；空串/None 原样返回空串（无凭据可加密）。"""
    if not secret:
        return ""
    return _fernet().encrypt(str(secret).encode()).decode()


def decrypt(ciphertext: str) -> str:
    """解密密文返回明文；空串原样返回空串。

    解密失败（坏 key/密文损坏）→ 抛 RuntimeError（明确报错口径），由调用方按
    P0-5 隔离：插件内 send/poll 捕获后记日志并静默降级，绝不外泄凭据或拖垮主链路。
    """
    if not ciphertext:
        return ""
    try:
        token = _fernet().decrypt(str(ciphertext).encode())
    except InvalidToken as e:
        raise RuntimeError("iLink 凭据解密失败（密钥不匹配或密文损坏）") from e
    return token.decode()
