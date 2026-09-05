# -*- coding: utf-8 -*-
"""crypto_util 单测：iLink bot_token 加密存储（P0-4）。

- 密文 ≠ 明文；解密回明文；
- 坏 key 解密失败 → 明确报错（RuntimeError，按实现口径）；
- 空串 roundtrip；
- 密钥缺失 → 明确报错（绝不 fallback 生成随机 key）；
- 密钥只来自环境变量（AMBRACE_SECRET_KEY 优先，ILINK_TOKEN_KEY 兜底），不硬编码。
"""
import importlib.util
import pathlib

import pytest

_PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "wechat_ilink"


def _load(name: str):
    """加载 plugins/examples/wechat_ilink/<name>.py；唯一模块名避免与项目内同名模块冲突。"""
    spec = importlib.util.spec_from_file_location("dsh_wechat_crypto_" + name, _PLUGIN_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


cu = _load("crypto_util")

_KEY_A = "test-passphrase-key-a-0000000000000001"
_KEY_B = "test-passphrase-key-b-9999999999999999"


def test_encrypt_not_plaintext_and_roundtrip(monkeypatch):
    monkeypatch.setenv("AMBRACE_SECRET_KEY", _KEY_A)
    enc = cu.encrypt("ilink-secret-token")
    assert enc != ""
    assert enc != "ilink-secret-token"
    assert cu.decrypt(enc) == "ilink-secret-token"


def test_decrypt_roundtrip_unicode(monkeypatch):
    monkeypatch.setenv("AMBRACE_SECRET_KEY", _KEY_A)
    assert cu.decrypt(cu.encrypt("随便一段 token 内容 with spaces")) == "随便一段 token 内容 with spaces"


def test_empty_roundtrip(monkeypatch):
    monkeypatch.setenv("AMBRACE_SECRET_KEY", _KEY_A)
    assert cu.encrypt("") == ""
    assert cu.decrypt("") == ""
    assert cu.decrypt(cu.encrypt("")) == ""


def test_bad_key_raises_clear_error(monkeypatch):
    monkeypatch.setenv("AMBRACE_SECRET_KEY", _KEY_A)
    enc = cu.encrypt("secret")
    monkeypatch.setenv("AMBRACE_SECRET_KEY", _KEY_B)
    with pytest.raises(RuntimeError):
        cu.decrypt(enc)


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("AMBRACE_SECRET_KEY", raising=False)
    monkeypatch.delenv("ILINK_TOKEN_KEY", raising=False)
    with pytest.raises(RuntimeError):
        cu.encrypt("secret")


def test_ilink_token_key_fallback(monkeypatch):
    monkeypatch.delenv("AMBRACE_SECRET_KEY", raising=False)
    monkeypatch.setenv("ILINK_TOKEN_KEY", _KEY_A)
    enc = cu.encrypt("fallback-key-token")
    assert enc != "fallback-key-token"
    assert cu.decrypt(enc) == "fallback-key-token"


def test_valid_44char_fernet_key_used_directly(monkeypatch):
    # 44 字符 base64 Fernet key：不派生、原样使用，仍能 roundtrip
    import base64
    raw_key = base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode()
    assert len(raw_key) == 44
    monkeypatch.setenv("AMBRACE_SECRET_KEY", raw_key)
    enc = cu.encrypt("direct-fernet-key")
    assert cu.decrypt(enc) == "direct-fernet-key"
