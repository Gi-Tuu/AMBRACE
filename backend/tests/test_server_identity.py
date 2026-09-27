# -*- coding: utf-8 -*-
"""服务器身份固定（批 0-3 M0-a）专项测试。

对应派单 7 条覆盖：
1. 三态（off / shadow / enforce），含「off 档逐字节不变」「shadow 验签失败仍放行」；
2. 配对流程（成功 / 错配对码 / 一次性 / 过期）；
3. 轮换（旧密钥立即失效、新码可重配）；
4. 配对码保密（硬断言：码不出现在任何响应体与日志文本）；
5. challenge-response（篡改 1 字节失败 / 过期失败 / 跨实现向量）；
6. 「0 步」绑定地址可配置化（未设配置仍旧值、脏配置不炸）；
7. 签名头正确性（只有 SIGN_PATHS 内的 GET/POST JSON 响应挂头）。

口径：**以代码实际实现为准**钉行为（实现与方案/派单文案的差异写在交付说明里，不改实现对齐）。
临时库一律 pytest tmp_path + tests/_dbclone（页级克隆模板库），绝不连生产库；
身份密钥文件一律经 ``AMBRACE_SERVER_IDENTITY_KEY_FILE`` 指向 tmp_path。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app import server_identity as si
from app.api import admin as admin_api
from app.api import system as system_api
from app.application import server_settings_service as sss
from app.auth.config import create_token

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
platform_util = pytest.importorskip("platform_util")

ADMIN_UID = 901
SIGNED_BODY = {"probe": "ambrace-identity-probe", "n": 1}


class _RecLogger:
    """录制型 logger：把 (fmt, *args) 渲染成文本存下，供「配对码绝不出现在日志」硬断言。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def _rec(self, fmt, *args) -> None:
        text = str(fmt)
        if args:
            try:
                text = text % args
            except (TypeError, ValueError, KeyError):
                text = "%s %r" % (text, args)
        self.lines.append(text)

    def info(self, fmt, *args):
        self._rec(fmt, *args)

    def warning(self, fmt, *args):
        self._rec(fmt, *args)

    def error(self, fmt, *args):
        self._rec(fmt, *args)

    def debug(self, fmt, *args):
        self._rec(fmt, *args)

    def text(self) -> str:
        return "\n".join(self.lines)


def _patch_session_factories(monkeypatch, factory) -> None:
    """把私有临时库工厂接到所有 app.* 的 ``async_session_factory``（含早绑定引用）。"""
    import app.db.database as db_mod
    import app.db.session as session_mod

    original = db_mod.async_session_factory
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(session_mod, "async_session_factory", factory, raising=False)
    for name, mod in list(sys.modules.items()):
        if not (name == "app" or name.startswith("app.")):
            continue
        try:
            if getattr(mod, "async_session_factory", None) is original:
                monkeypatch.setattr(mod, "async_session_factory", factory)
        except Exception:
            continue


@pytest.fixture()
def id_db(monkeypatch, tmp_path):
    """私有临时库：一个 server_admin 账号（控制台端点要真实过鉴权）。"""
    engine = clone_engine(tmp_path / "ident.db")
    factory = make_session_factory(engine)

    async def _init():
        from app.models.user import User

        async with factory() as db:
            db.add(User(id=ADMIN_UID, username="root", nickname="根",
                        is_admin=True, server_admin=True, password_hash="x"))
            await db.commit()

    asyncio.run(_init())
    _patch_session_factories(monkeypatch, factory)
    yield factory
    engine.sync_engine.dispose()


@pytest.fixture(autouse=True)
def ident_env(monkeypatch, tmp_path):
    """每个用例：密钥文件指向 tmp_path、进程内状态清零、限流计数清零、日志走录制器。"""
    from app.auth import ratelimit

    key_file = tmp_path / "server_identity.key"
    monkeypatch.setenv(si.KEY_FILE_ENV, str(key_file))
    monkeypatch.setattr(si, "_logger", _RecLogger())
    monkeypatch.setattr(system_api, "_logger", _RecLogger())
    monkeypatch.setattr(admin_api, "_logger", _RecLogger())
    si.reset_state_for_test()
    ratelimit._failures.clear()
    ratelimit._locked.clear()
    yield {"key_file": key_file}
    si.reset_state_for_test()
    ratelimit._failures.clear()
    ratelimit._locked.clear()


@pytest.fixture()
def logtext(ident_env):
    """三个被测模块的日志合并文本（si / api.system / api.admin）。"""
    def _get() -> str:
        return "\n".join([si._logger.text(), system_api._logger.text(), admin_api._logger.text()])
    return _get


# ── 应用装配与工具 ─────────────────────────────────────────────────────────────

def _add_middleware(app: FastAPI) -> FastAPI:
    """与 main.py 同形态挂响应签名中间件（写在 CORS 之后 → 运行时在最外层）。"""
    @app.middleware("http")
    async def _identity_proof_middleware(request, call_next):  # noqa: E306
        return await si.sign_response(request, call_next)
    return app


def _probe_app(with_middleware: bool = True) -> FastAPI:
    """合成应用：响应体完全确定（不含 timestamp），用于「off 档逐字节不变」与方法/类型分支。

    路径刻意取真实白名单内的 ``/api/v1/system/liveness``（JSON）与
    ``/api/v1/system/ready``（非 JSON），另加白名单外路径做对照。
    """
    app = FastAPI()
    if with_middleware:
        _add_middleware(app)

    @app.api_route("/api/v1/system/liveness", methods=["GET", "POST", "OPTIONS"])
    async def _probe():
        return JSONResponse(SIGNED_BODY)

    @app.get("/api/v1/system/ready")
    async def _not_json():
        return Response(content=b"plain-text-not-json", media_type="text/plain")

    @app.get("/api/v1/system/control-not-signed")
    async def _control():
        return JSONResponse(SIGNED_BODY)

    return app


def _api_app(with_middleware: bool = True) -> FastAPI:
    """真实路由（system + admin）+ 响应签名中间件。"""
    app = FastAPI()
    if with_middleware:
        _add_middleware(app)
    app.include_router(system_api.router)
    app.include_router(admin_api.router)
    return app


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _auth() -> dict:
    return {"Authorization": "Bearer %s" % create_token(ADMIN_UID)}


def _mode(factory) -> str:
    """在独立会话里读档位（避免测试内泄漏未关闭的 session 句柄）。"""
    async def _r():
        async with factory() as db:
            return await sss.get_identity_enforce_mode(db)

    return asyncio.run(_r())


def _set_mode(factory, value) -> None:
    """直接写 server_settings（实现里没有 identity 档位的 setter）。"""
    from app.models.config import ServerSetting

    async def _w():
        async with factory() as db:
            row = await db.get(ServerSetting, sss.IDENTITY_ENFORCE_MODE_KEY)
            if row is None:
                db.add(ServerSetting(key=sss.IDENTITY_ENFORCE_MODE_KEY, value=value))
            else:
                row.value = value
            await db.commit()

    asyncio.run(_w())


def _client_proof(secret: bytes, nonce, status: int, body: bytes, ts: int) -> str:
    """App 侧对称实现（等价于 M0-b 的 Dart），用于「服务器口径可被独立复算」的断言。"""
    canonical = "\n".join([nonce or "-", str(status),
                           hashlib.sha256(body).hexdigest(), str(ts)]).encode("utf-8")
    return "v1 %d %s" % (ts, hmac.new(secret, canonical, hashlib.sha256).hexdigest())


def _issue_code(c: TestClient) -> str:
    r = c.post("/api/v1/admin/server/identity/pairing-code", headers=_auth(), json={})
    assert r.status_code == 200, r.text
    return r.json()["code"]


def _challenge(c: TestClient) -> str:
    r = c.post("/api/v1/system/identity/pair-start", json={})
    assert r.status_code == 200, r.text
    return r.json()["challenge"]


def _do_pair(c: TestClient, code: str) -> dict:
    """App 侧完整配对：领 challenge → HKDF(码) 算 mac → pair 换指纹。"""
    challenge = _challenge(c)
    r = c.post("/api/v1/system/identity/pair",
               json={"challenge": challenge,
                     "mac": si._pair_mac(si.derive_shared(code), challenge)})
    assert r.status_code == 200, r.text
    return r.json()


def _write_key(secret: bytes) -> None:
    Path(si.key_file_path()).write_text(base64.b64encode(secret).decode("ascii"), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 三态
# ═══════════════════════════════════════════════════════════════════════════════

def test_off_mode_is_byte_identical_and_unsigned(id_db):
    """off（默认）⇒ 被签名单路径无 X-Ambrace-Proof，且响应体与「未挂中间件」逐字节一致。"""
    plain = _client(_probe_app(with_middleware=False)).get("/api/v1/system/liveness")
    withmw = _client(_probe_app()).get("/api/v1/system/liveness")
    assert withmw.status_code == 200
    assert si.PROOF_HEADER not in withmw.headers
    assert withmw.content == plain.content

    r = _client(_api_app()).get("/api/v1/system/health")
    assert r.status_code == 200, r.text
    assert si.PROOF_HEADER not in r.headers
    assert set(r.json()) == {"status", "timestamp"}, "off 档 health 不得多出 identity 节"


def test_mode_falls_back_to_off_for_missing_dirty_and_read_failure(id_db, monkeypatch):
    """缺行 / 脏值 / 读失败 ⇒ 一律回落 off（三种情形响应都不挂头）。"""
    assert _mode(id_db) == "off"  # 缺行

    _set_mode(id_db, "ENFORCE")            # 大小写归一 → 合法
    assert _mode(id_db) == "enforce"

    _set_mode(id_db, "not-a-mode")         # 脏值
    assert _mode(id_db) == "off"
    assert si.PROOF_HEADER not in _client(_probe_app()).get("/api/v1/system/liveness").headers

    _set_mode(id_db, None)                 # 有行但值为空
    assert _mode(id_db) == "off"

    async def _boom(db):
        raise RuntimeError("config table locked")

    monkeypatch.setattr(sss, "get_setting", _boom)   # 读失败
    assert _mode(id_db) == "off"
    assert si.PROOF_HEADER not in _client(_probe_app()).get("/api/v1/system/liveness").headers


def test_off_mode_read_failure_of_session_factory_does_not_break_health(id_db, monkeypatch):
    """current_mode 整条链路炸掉（会话工厂坏了）也不能打挂被签名的端点，回落 off。"""
    import app.db.database as db_mod

    def _raise():
        raise RuntimeError("no engine")

    monkeypatch.setattr(db_mod, "async_session_factory", _raise)
    assert asyncio.run(si.current_mode()) == "off"
    r = _client(_api_app()).get("/api/v1/system/health")
    assert r.status_code == 200 and si.PROOF_HEADER not in r.headers


def test_shadow_mode_never_rejects_and_signs_when_paired(id_db, logtext):
    """shadow：无密钥时不出签也照常 200（只记日志）；有密钥则出签且 App 侧可独立复算验证。"""
    _set_mode(id_db, "shadow")
    c = _client(_api_app())
    r = c.get("/api/v1/system/health")
    assert r.status_code == 200, r.text
    assert si.PROOF_HEADER not in r.headers
    assert "无身份密钥" in logtext()

    code = _issue_code(c)
    assert _do_pair(c, code)["fp"] == si.fingerprint_of(si.derive_shared(code))

    nonce = "nonce-shadow-1"
    r2 = c.get("/api/v1/system/health", headers={si.CHALLENGE_HEADER: nonce})
    assert r2.status_code == 200, r2.text
    secret = si.active_secret()
    assert si.verify_proof(secret, r2.headers[si.PROOF_HEADER], nonce, 200, r2.content)
    body = r2.json()
    assert body["identity"] == {"server_name": si.SERVER_NAME,
                                "fp_hint": si.current_fingerprint(), "mode": "shadow"}


def test_shadow_mode_rejects_nothing_on_bad_client_signature(id_db):
    """验签失败/缺签在 shadow 下仍放行（200）——错签、乱格式头都不改变响应。"""
    _set_mode(id_db, "shadow")
    c = _client(_api_app())
    _do_pair(c, _issue_code(c))
    assert si.active_secret() is not None
    probe = _client(_probe_app())
    assert probe.get("/api/v1/system/liveness",
                     headers={si.CHALLENGE_HEADER: "n", si.PROOF_HEADER: "garbage"}).status_code == 200
    assert probe.get("/api/v1/system/liveness",
                     headers={si.PROOF_HEADER: "v1 0 " + "00" * 32}).status_code == 200


def test_enforce_rejects_when_key_missing_and_nonce_present(id_db, logtext):
    """enforce：带 nonce 却出不了签（密钥缺失）⇒ 503 + identity_key_unavailable；无 nonce 照旧放行。"""
    _set_mode(id_db, "enforce")
    probe = _client(_probe_app())
    r = probe.get("/api/v1/system/liveness", headers={si.CHALLENGE_HEADER: "nonce-1"})
    assert r.status_code == 503, r.text
    assert r.json() == {"ok": False, "error": {
        "code": "identity_key_unavailable",
        "message": "服务器身份密钥未签发，请重新配对",
        "detail": "server identity key is not available"}}
    assert "enforce 下无可用身份密钥" in logtext()

    r2 = probe.get("/api/v1/system/liveness")  # 探针 / 旧包 / 未配对 App 不被打挂
    assert r2.status_code == 200, r2.text
    assert r2.content == json.dumps(SIGNED_BODY, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")


def test_enforce_signs_and_passes_once_paired(id_db):
    """enforce + 已配对 ⇒ 200 且带头，App 用同一密钥验签通过。"""
    c = _client(_api_app())
    _do_pair(c, _issue_code(c))
    _set_mode(id_db, "enforce")
    nonce = "nonce-enforce"
    r = c.get("/api/v1/system/health", headers={si.CHALLENGE_HEADER: nonce})
    assert r.status_code == 200, r.text
    assert si.verify_proof(si.active_secret(), r.headers[si.PROOF_HEADER], nonce, 200, r.content)


def test_enforce_does_not_validate_request_side_signatures(id_db):
    """实现口径：服务器不校验请求侧签名（缺签/错签都不拒），只对响应出签。"""
    c = _client(_api_app())
    _do_pair(c, _issue_code(c))
    _set_mode(id_db, "enforce")
    r = c.get("/api/v1/system/health",
              headers={si.CHALLENGE_HEADER: "n", si.PROOF_HEADER: "v1 0 " + "11" * 32})
    assert r.status_code == 200, r.text


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 配对流程
# ═══════════════════════════════════════════════════════════════════════════════

def test_pair_start_without_pending_code_is_409(id_db):
    c = _client(_api_app())
    r = c.post("/api/v1/system/identity/pair-start", json={})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "no active pairing code"


def test_pairing_roundtrip_yields_usable_key(id_db):
    """pair-start → pair 拿到可用凭据：密钥落盘、指纹与控制台签发值一致、health 可验签。"""
    c = _client(_api_app())
    issued = c.post("/api/v1/admin/server/identity/pairing-code", headers=_auth(), json={}).json()
    code = issued["code"]
    assert len(code) == si.CODE_LEN and set(code) <= set(si.CODE_ALPHABET)
    assert issued["fp"] == si.fingerprint_of(si.derive_shared(code))
    assert issued["fp_display"] == si.format_fingerprint(issued["fp"])
    assert issued["ttl_sec"] == si.CODE_TTL_SEC

    challenge = _challenge(c)
    r = c.post("/api/v1/system/identity/pair",
               json={"challenge": challenge, "mac": si._pair_mac(si.derive_shared(code), challenge)})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got == {"status": "ok", "server_name": si.SERVER_NAME,
                   "fp": issued["fp"], "fp_display": issued["fp_display"]}
    assert si.active_secret() == si.derive_shared(code)
    key = Path(si.key_file_path())
    assert key.exists() and base64.b64decode(key.read_text(encoding="utf-8").strip()) == si.derive_shared(code)

    _set_mode(id_db, "shadow")
    nonce = "n1"
    r2 = c.get("/api/v1/system/health", headers={si.CHALLENGE_HEADER: nonce})
    assert si.verify_proof(si.active_secret(), r2.headers[si.PROOF_HEADER], nonce, 200, r2.content)


def test_wrong_pairing_code_fails_indistinguishably(id_db):
    """错配对码 ⇒ 401，文案与「无待用码」不同但错码之间不可区分；错码不消耗待用码。"""
    c = _client(_api_app())
    code = _issue_code(c)
    challenge = _challenge(c)
    wrong = si._pair_mac(si.derive_shared("QQQQQQQQQQQQ"), challenge)
    r = c.post("/api/v1/system/identity/pair", json={"challenge": challenge, "mac": wrong})
    assert r.status_code == 401, r.text
    assert r.json()["detail"] == "pairing failed"

    r2 = c.post("/api/v1/system/identity/pair", json={"challenge": challenge, "mac": "00" * 32})
    assert r2.status_code == 401 and r2.json()["detail"] == "pairing failed"

    assert _do_pair(c, code)["fp"] == si.fingerprint_of(si.derive_shared(code)), "错码后正确码仍可用"


def test_pairing_code_is_single_use(id_db):
    """一次性：成功配对后同一 challenge/mac 重放被拒，待用码作废（再 pair-start → 409）。"""
    c = _client(_api_app())
    code = _issue_code(c)
    challenge = _challenge(c)
    mac = si._pair_mac(si.derive_shared(code), challenge)
    assert c.post("/api/v1/system/identity/pair",
                  json={"challenge": challenge, "mac": mac}).status_code == 200
    replay = c.post("/api/v1/system/identity/pair", json={"challenge": challenge, "mac": mac})
    assert replay.status_code == 401, replay.text
    assert c.post("/api/v1/system/identity/pair-start", json={}).status_code == 409
    assert si.pending_info()["has_pending"] is False


def test_pairing_code_expiry(id_db, monkeypatch):
    """过期（用 CODE_TTL_SEC 造时钟）：待用码过期后 pending_info 归零、pair-start → 409。"""
    c = _client(_api_app())
    monkeypatch.setattr(si, "CODE_TTL_SEC", -1)
    _issue_code(c)
    assert si.pending_info() == {"has_pending": False, "fp": "", "expires_in": 0}
    assert c.post("/api/v1/system/identity/pair-start", json={}).status_code == 409


def test_challenge_expiry(id_db, monkeypatch):
    """挑战过期 ⇒ 401（与错码同文案）且不落密钥。"""
    c = _client(_api_app())
    code = _issue_code(c)
    monkeypatch.setattr(si, "CHALLENGE_TTL_SEC", -1)  # 造时钟：领到的挑战即刻过期
    challenge = _challenge(c)
    r = c.post("/api/v1/system/identity/pair",
               json={"challenge": challenge, "mac": si._pair_mac(si.derive_shared(code), challenge)})
    assert r.status_code == 401, r.text
    assert r.json()["detail"] == "pairing failed"
    assert si.active_secret() is None


def test_new_pairing_code_invalidates_previous_challenge(id_db):
    """同一时刻只允许一屏在输的码：签发新码后旧 challenge 作废（旧 mac 重放 → 401）。"""
    c = _client(_api_app())
    old_code = _issue_code(c)
    old_challenge = _challenge(c)
    new_code = _issue_code(c)
    assert new_code != old_code
    r = c.post("/api/v1/system/identity/pair",
               json={"challenge": old_challenge, "mac": si._pair_mac(si.derive_shared(old_code), old_challenge)})
    assert r.status_code == 401, r.text
    assert _do_pair(c, new_code)["status"] == "ok"


def test_pair_rejects_empty_credentials(id_db):
    c = _client(_api_app())
    _issue_code(c)
    assert c.post("/api/v1/system/identity/pair",
                  json={"challenge": "", "mac": ""}).status_code == 401
    assert c.post("/api/v1/system/identity/pair", json={}).status_code == 401
    assert c.post("/api/v1/system/identity/pair",
                  json={"challenge": "no-such-challenge", "mac": "ab" * 32}).status_code == 401


def test_pair_stats_count_success_and_failure(id_db):
    c = _client(_api_app())
    code = _issue_code(c)
    challenge = _challenge(c)
    c.post("/api/v1/system/identity/pair", json={"challenge": challenge, "mac": "00" * 32})
    assert si.pair_stats() == {"pair_success": 0, "pair_fail": 1}
    _do_pair(c, code)
    assert si.pair_stats() == {"pair_success": 1, "pair_fail": 1}


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 轮换
# ═══════════════════════════════════════════════════════════════════════════════

def test_rotate_invalidates_old_key_and_new_code_works(id_db):
    """rotate 后：旧密钥签名立即失效（停止出签）、旧 mac 重放被拒；新码可重配并得到新指纹。"""
    c = _client(_api_app())
    old_code = _issue_code(c)
    old_fp = _do_pair(c, old_code)["fp"]
    old_secret = si.derive_shared(old_code)
    _set_mode(id_db, "shadow")
    nonce = "nonce-rotate"
    r = c.get("/api/v1/system/health", headers={si.CHALLENGE_HEADER: nonce})
    assert si.verify_proof(old_secret, r.headers[si.PROOF_HEADER], nonce, 200, r.content)

    rr = c.post("/api/v1/admin/server/identity/rotate", headers=_auth(), json={})
    assert rr.status_code == 200, rr.text
    rotated = rr.json()
    assert rotated["note"] and rotated["fp"] and rotated["fp"] != old_fp
    assert si.active_secret() is None and si.current_fingerprint() == ""
    assert not Path(si.key_file_path()).exists()

    r2 = c.get("/api/v1/system/health", headers={si.CHALLENGE_HEADER: nonce})
    assert r2.status_code == 200 and si.PROOF_HEADER not in r2.headers

    assert _do_pair(c, rotated["code"])["fp"] == rotated["fp"]
    assert c.post("/api/v1/system/identity/pair",
                  json={"challenge": "x", "mac": si._pair_mac(old_secret, "x")}).status_code == 401


def test_rotate_without_previous_pair(id_db):
    """未配对也能轮换（幂等：无旧密钥可删）；结果是拿到一张新码。"""
    c = _client(_api_app())
    r = c.post("/api/v1/admin/server/identity/rotate", headers=_auth(), json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fp"] == si.fingerprint_of(si.derive_shared(body["code"]))
    assert si.active_secret() is None


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 配对码保密（硬断言）
# ═══════════════════════════════════════════════════════════════════════════════

def test_pairing_code_never_leaks_to_app_responses_or_logs(id_db, logtext):
    """配对码字符串不出现在任何 App 侧/控制台读端点的响应体，也不出现在捕获日志里；只有签发响应带码。"""
    c = _client(_api_app())
    issued = c.post("/api/v1/admin/server/identity/pairing-code", headers=_auth(), json={}).json()
    code = issued["code"]
    assert code in json.dumps(issued)  # 唯一合法出口：控制台带外面

    bodies = [
        c.get("/api/v1/system/health").text,
        c.get("/api/v1/admin/server/identity", headers=_auth()).text,
        si.pending_info(),
    ]

    rs = c.post("/api/v1/system/identity/pair-start", json={})
    bodies.append(rs.text)
    challenge = rs.json()["challenge"]
    rp = c.post("/api/v1/system/identity/pair",
                json={"challenge": challenge, "mac": si._pair_mac(si.derive_shared(code), challenge)})
    assert rp.status_code == 200, rp.text
    bodies.append(rp.text)
    c.post("/api/v1/admin/server/identity/rotate", headers=_auth(), json={})

    for item in bodies:
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, default=str)
        assert code not in text, "配对码泄漏进响应体"
    assert code not in logtext(), "配对码泄漏进日志"

    audit = c.get("/api/v1/admin/server/audit", headers=_auth()).json()["entries"]
    rows = [a for a in audit if a.get("action", "").startswith("server.identity")]
    assert rows, "配对码签发/轮换必须留审计"
    assert code not in json.dumps(rows, ensure_ascii=False), "配对码泄漏进审计"


def test_pending_info_exposes_only_fingerprint(id_db):
    _issue_code(_client(_api_app()))
    info = si.pending_info()
    assert set(info) == {"has_pending", "fp", "expires_in"} and info["has_pending"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# 5. challenge-response 数学 / 跨实现向量
# ═══════════════════════════════════════════════════════════════════════════════

def test_cross_implementation_vectors():
    """钉住跨实现契约（M0-b 的 Dart/Kotlin 必须逐字节一致）：HKDF / 指纹 / pairMac。"""
    code = "ABCDEFGHJKMN"
    assert si.derive_shared(code).hex() == (
        "feda0ac7a4ed60f62d66296d98c251c2abff6ff86a5c6ff6811016b3f4f1aed8")
    assert si.derive_shared(" abcdefghjkmn ") == si.derive_shared(code)  # 大小写/空白不参与信任
    fp = si.fingerprint_of(si.derive_shared(code))
    assert fp == "94d09229adea" and si.format_fingerprint(fp) == "94d0-9229-adea"
    assert si._pair_mac(si.derive_shared(code), "test-challenge-abc") == (
        "30556b9e650f72065a5fa7e4809bc6086b144c1edc18cd426b1875acecafecef")


def test_hkdf_matches_rfc5869_test_case_1():
    """HKDF 自实现必须等于 RFC 5869 A.1 Test Case 1（L=42）。"""
    out = si.hkdf_sha256(
        b"\x0b" * 22,
        bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20212223"),
        bytes.fromhex("f0f1f2f3f4f5f6f7f8f9"), 42)
    assert out.hex() == ("d88c770608c467ef73c4f61f0f70f63fc84b30c6b6acb899cb12ef633532f304bb71453fdeda435d3288")


def test_proof_verify_accepts_and_rejects_tampering():
    """正确响应通过；篡改 1 字节（body / nonce / status / ts / mac / 格式）一律失败。"""
    secret = si.derive_shared("ABCDEFGHJKMN")
    body = b'{"status":"ok"}'
    ts, proof = si.proof_for(secret, "nonce-a", 200, body, ts=1700000000)
    assert ts == 1700000000 and proof.startswith("v1 1700000000 ")
    assert si.verify_proof(secret, proof, "nonce-a", 200, body)
    assert proof == _client_proof(secret, "nonce-a", 200, body, ts), "服务器/客户端两套口径必须一致"

    assert not si.verify_proof(secret, proof, "nonce-b", 200, body)
    assert not si.verify_proof(secret, proof, "noncea", 200, body)
    assert not si.verify_proof(secret, proof, "nonce-a", 500, body)
    assert not si.verify_proof(secret, proof, "nonce-a", 200, b'{"status":"okx"}')
    assert not si.verify_proof(secret, proof, "nonce-a", 200, b"")
    flipped = proof[:-1] + ("0" if proof[-1] != "0" else "1")
    assert not si.verify_proof(secret, flipped, "nonce-a", 200, body)
    assert not si.verify_proof(secret, proof.replace("1700000000", "1700000001"), "nonce-a", 200, body)
    assert not si.verify_proof(secret, "v2 1700000000 " + "ab" * 32, "nonce-a", 200, body)
    assert not si.verify_proof(secret, "", "nonce-a", 200, body)
    assert not si.verify_proof(secret, proof + " extra", "nonce-a", 200, body)
    assert not si.verify_proof(secret, proof.split()[0] + " x " + proof.split()[2], "nonce-a", 200, body)
    assert not si.verify_proof(b"\x00" * 32, proof, "nonce-a", 200, body)


def test_missing_nonce_uses_placeholder():
    """无 nonce 时 canonical 用 "-" 占位：空串与 "-" 等价（实现口径），其它值不等价。"""
    secret = si.derive_shared("ABCDEFGHJKMN")
    body = b"{}"
    _, proof = si.proof_for(secret, "", 200, body, ts=1700000000)
    assert si.verify_proof(secret, proof, "", 200, body)
    assert si.verify_proof(secret, proof, "-", 200, body)
    assert not si.verify_proof(secret, proof, "real-nonce", 200, body)


def test_proof_timestamp_is_now_by_default_and_embedded_in_header():
    secret = si.derive_shared("ABCDEFGHJKMN")
    before = int(time.time())
    ts, proof = si.proof_for(secret, "n", 200, b"{}")
    assert before <= ts <= before + 5
    assert proof.split()[1] == str(ts)


def test_ttl_constants_match_plan():
    assert si.CODE_TTL_SEC <= 300, "方案 §6-R1：配对码有效期 ≤ 5 分钟"
    assert si.CHALLENGE_TTL_SEC == 60
    assert si.CODE_LEN == 12
    for ch in "0O1IL":
        assert ch not in si.CODE_ALPHABET, "配对码字母表须去易混字符"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. 「0 步」绑定地址可配置化
# ═══════════════════════════════════════════════════════════════════════════════

def test_resolve_bind_host_default_unchanged(tmp_path, monkeypatch):
    """未设任何配置 ⇒ 仍旧值 0.0.0.0（与改动前三处硬编码逐字一致），与后端 settings 默认同源。"""
    monkeypatch.delenv("SERVER_HOST", raising=False)
    assert platform_util.DEFAULT_BIND_HOST == "0.0.0.0"
    assert platform_util.resolve_bind_host(str(tmp_path)) == "0.0.0.0"
    from app.config import settings

    assert settings.server_host == "0.0.0.0"


def test_resolve_bind_host_reads_config_and_survives_dirty_values(tmp_path, monkeypatch):
    """脏配置不炸：坏 JSON / 非对象 / 空值 / 缺文件一律回落默认；正常值生效；环境变量优先。"""
    monkeypatch.delenv("SERVER_HOST", raising=False)
    data = tmp_path / "data"
    data.mkdir()
    cfg = data / "server_config.json"

    cfg.write_text('{"server_host": "10.1.2.3"}', encoding="utf-8")
    assert platform_util.resolve_bind_host(str(tmp_path)) == "10.1.2.3"

    cfg.write_text('{"server_host": "   "}', encoding="utf-8")
    assert platform_util.resolve_bind_host(str(tmp_path)) == "0.0.0.0"

    cfg.write_text("{not json", encoding="utf-8")
    assert platform_util.resolve_bind_host(str(tmp_path)) == "0.0.0.0"

    cfg.write_text("[]", encoding="utf-8")
    assert platform_util.resolve_bind_host(str(tmp_path)) == "0.0.0.0"

    cfg.write_text('{"server_host": 127}', encoding="utf-8")
    assert platform_util.resolve_bind_host(str(tmp_path)) == "127"

    assert platform_util.resolve_bind_host(str(tmp_path / "nope")) == "0.0.0.0"

    monkeypatch.setenv("SERVER_HOST", " 127.0.0.1 ")
    assert platform_util.resolve_bind_host(str(tmp_path)) == "127.0.0.1"


def test_bind_host_callers_delegated_to_single_source():
    """三处拉起入口统一走 resolve_bind_host，不再各自硬编码 --host。"""
    repo = Path(__file__).resolve().parents[2]
    for path in (repo / "scripts" / "watchdog.py", repo / "scripts" / "server_manager.py",
                 repo / "server_controller" / "server_controller.py"):
        src = path.read_text(encoding="utf-8")
        assert "resolve_bind_host(" in src, path.name
        assert '"--host", "0.0.0.0"' not in src, path.name


def test_identity_key_file_is_gitignored_and_backup_excluded():
    """密钥文件不进版本库、不被备份带走（方案 §4.2.6）。"""
    repo = Path(__file__).resolve().parents[2]
    assert "server_identity.key" in (repo / ".gitignore").read_text(encoding="utf-8")
    assert "server_identity.key" in (repo / "scripts" / "backup.py").read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# 7. 签名头正确性 / 密钥文件形态
# ═══════════════════════════════════════════════════════════════════════════════

def test_only_whitelisted_json_paths_are_signed(id_db):
    """白名单内 GET/POST 挂头；非白名单路径、OPTIONS 预检、非 JSON 响应都不挂。"""
    c = _client(_api_app())
    _do_pair(c, _issue_code(c))
    _set_mode(id_db, "shadow")
    probe = _client(_probe_app())

    for method in ("get", "post"):
        r = getattr(probe, method)("/api/v1/system/liveness")
        assert r.status_code == 200 and si.PROOF_HEADER in r.headers, method

    assert si.PROOF_HEADER not in probe.options("/api/v1/system/liveness").headers
    assert si.PROOF_HEADER not in probe.get("/api/v1/system/control-not-signed").headers
    assert si.PROOF_HEADER not in probe.post("/api/v1/system/control-not-signed").headers

    rj = probe.get("/api/v1/system/ready")
    assert rj.status_code == 200 and si.PROOF_HEADER not in rj.headers

    r = c.get("/api/v1/system/health")
    assert r.status_code == 200 and si.PROOF_HEADER in r.headers


def test_signed_paths_are_real_routes():
    """SIGN_PATHS 每条都必须真的存在（不存在的条目＝白名单死账）。"""
    from fastapi.routing import APIRoute

    from app.auth.router import router as auth_router

    known = {r.path for rt in (system_api.router, admin_api.router, auth_router)
             for r in rt.routes if isinstance(r, APIRoute)}
    for path in si.SIGN_PATHS:
        assert path in known, "SIGN_PATHS 含不存在的路由: %s" % path
    assert "/api/v1/system/health" in si.SIGN_PATHS
    assert "/api/v1/auth/login" in si.SIGN_PATHS


def test_signature_covers_final_status_and_exact_body(id_db):
    """签名覆盖真实状态码与最终字节：错误响应（409）也出签，改状态码即验签失败。"""
    c = _client(_api_app())
    _do_pair(c, _issue_code(c))
    _set_mode(id_db, "shadow")
    secret = si.active_secret()
    nonce = "nonce-409"
    r = c.post("/api/v1/system/identity/pair-start", json={}, headers={si.CHALLENGE_HEADER: nonce})
    assert r.status_code == 409, r.text
    proof = r.headers[si.PROOF_HEADER]
    assert si.verify_proof(secret, proof, nonce, 409, r.content)
    assert not si.verify_proof(secret, proof, nonce, 200, r.content)
    assert not si.verify_proof(secret, proof, nonce, 409, r.content + b" ")

    r2 = c.get("/api/v1/system/no-such-endpoint", headers={si.CHALLENGE_HEADER: nonce})
    assert r2.status_code == 404 and si.PROOF_HEADER not in r2.headers


def test_challenge_header_longer_than_cap_still_signs(id_db):
    """nonce 被截到 128 字符再入签：超长 nonce 不出签失败（服务器与 App 都按截断值算）。"""
    c = _client(_api_app())
    _do_pair(c, _issue_code(c))
    _set_mode(id_db, "enforce")
    nonce = "x" * 200
    r = c.get("/api/v1/system/health", headers={si.CHALLENGE_HEADER: nonce})
    assert r.status_code == 200, r.text
    assert si.verify_proof(si.active_secret(), r.headers[si.PROOF_HEADER], nonce[:128], 200, r.content)


def test_key_file_dirty_content_treated_as_unsigned(ident_env):
    """密钥文件缺失 / 空 / 非 base64 / 长度不对 ⇒ 视为未签发（None），不能让 health 挂掉。"""
    key = Path(ident_env["key_file"])
    assert si.active_secret() is None and si.current_fingerprint() == ""
    assert si.key_created_at() == ""

    for raw in ("", "   ", "not-base64-!!", base64.b64encode(b"short").decode("ascii"), "e30="):
        key.write_text(raw, encoding="utf-8")
        si.reset_state_for_test()
        assert si.active_secret() is None, repr(raw)
        assert si.current_fingerprint() == ""

    key.write_bytes(b"\x00\x01")  # 二进制垃圾：读取抛错也不能崩
    si.reset_state_for_test()
    assert si.active_secret() is None

    _write_key(b"\x11" * 32)
    si.reset_state_for_test()
    assert si.active_secret() == b"\x11" * 32
    assert si.current_fingerprint() == si.fingerprint_of(b"\x11" * 32)
    assert si.key_created_at()


def test_key_file_swap_is_logged_with_fingerprint_hint(ident_env, logtext):
    """密钥文件被换掉（方案 §6-R5）⇒ 下次读取留下「身份密钥已变更」痕迹，供目视核对。"""
    key = Path(ident_env["key_file"])
    key.write_text(base64.b64encode(b"\x11" * 32).decode("ascii"), encoding="utf-8")
    first = si.current_fingerprint()
    key.write_text(base64.b64encode(b"\x22" * 32).decode("ascii") + " ", encoding="utf-8")
    assert si.current_fingerprint() != first
    assert "身份密钥已变更" in logtext()


def test_console_identity_snapshot_shape(id_db):
    """控制台快照：档位 / 指纹 / 待用码状态 / 签名清单 / 0 步读数齐备，且不含配对码。"""
    _set_mode(id_db, "shadow")
    c = _client(_api_app())
    code = _issue_code(c)
    snapshot = c.get("/api/v1/admin/server/identity", headers=_auth()).json()
    assert snapshot["mode"] == "shadow"
    assert snapshot["server_name"] == si.SERVER_NAME
    assert snapshot["signed_paths"] == sorted(si.SIGN_PATHS)
    assert snapshot["bind_host"] == "0.0.0.0"
    assert snapshot["paired"] is False and snapshot["fp"] == ""
    assert snapshot["pending"] == {"has_pending": True,
                                   "fp": si.fingerprint_of(si.derive_shared(code)),
                                   "expires_in": pytest.approx(si.CODE_TTL_SEC, abs=2)}
    assert set(snapshot["hints"]) == {"cors_wildcard", "uploads_require_auth"}
    assert set(snapshot["pair_stats"]) == {"pair_success", "pair_fail"}
    assert code not in json.dumps(snapshot, ensure_ascii=False)


def test_console_identity_endpoints_require_server_admin(id_db):
    """控制台身份端点必须过 require_server_admin（未登录不得匿名签发/轮换）。"""
    c = _client(_api_app())
    assert c.get("/api/v1/admin/server/identity").status_code in (401, 403)
    for path in ("/api/v1/admin/server/identity/pairing-code",
                 "/api/v1/admin/server/identity/rotate"):
        assert c.post(path, json={}).status_code in (401, 403), path
