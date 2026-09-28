# -*- coding: utf-8 -*-
"""服务器身份固定（批 0-3 M0-a，2026-09-27）——配对码派生身份密钥 + 关键响应签名。

方案：``AMBRACE_批0-3_服务器身份固定_方案_v1_20260927.md`` §0 / §4。

三条不可破坏的口径（改本文件前先读）：

1. **配对码绝不过网**。App 与服务器各自 ``shared = HKDF-SHA256(code)``，网络上只跑
   challenge / mac；任何端点的响应体与日志都不出现配对码本身。
2. **只防伪造，不防窃听**。HMAC 给完整性与来源，不给保密性（威胁 T3 要等 B 档 TLS）。
3. **响应签名优先，请求侧不校验**。服务器只看 ``X-Ambrace-Challenge``  nonce 做防重放
   原料，**不验、不因缺失而拒绝**（旧包永不被拒）。

跨实现契约（M0-b 的 Dart / Kotlin 必须逐字节对齐，测试 ``test_server_identity.py`` 固化了向量）::

    site    = 全站单份身份密钥（32B，backend/data/server_identity.key，缺文件自动生成）
    shared  = HKDF-SHA256(ikm=code, salt=b"ambrace-id-v1", info=b"ambrace-pair-v1", L=32)  # 配对码本地派生
    ks      = HKDF-SHA256(ikm=code, salt=b"ambrace-id-v1", info=b"ambrace-ks-v1",  L=32)  # 包裹用密钥流
    fp      = HMAC-SHA256(site, b"ambrace-fp-v1")[:6].hex()  → 12 位十六进制，显示成 4-4-4
    pairMac = HMAC-SHA256(shared, b"v1\\n" + challenge).hex()
    wrapped = hex(site XOR ks)            # 身份密钥以「码派生密钥流」包裹后随 pair 响应下发
    proof   = "v1 {ts} " + HMAC-SHA256(site, "\\n".join([nonce or "-", status,
              sha256(body).hex(), str(ts)])).hex()

**为什么是「包裹」而不是「用码当密钥」**（派单硬要求，方案 §6-R5）：身份密钥全站单份，
签发新配对码、第二台设备配对都**不得**更换它——否则已配对设备集体失配。因此配对成功时
服务器只把既有密钥包裹后交给新设备，密钥本身只在首次生成/显式 rotate 时变。

**未覆盖清单（诚实记账，方案 §4.5 / 风险 R4）**：图片音频裸 URL、下载直链、SSE 分块、
WebSocket 帧、Kotlin 原生通道都不在 :data:`SIGN_PATHS` 内 → 本模块只给「部分受保护」。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
import threading
import time
from pathlib import Path

from app.utils.logger import get_logger

_logger = get_logger("server_identity")

# ── 形态常量 ──────────────────────────────────────────────────────────────────
CODE_LEN = 12                                    # 方案 §7-Q2 已拍板：12 位
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # 去掉易混字符 0/O/1/I/L（≈2^59）
CODE_TTL_SEC = int(os.environ.get("AMBRACE_PAIRING_TTL_SEC", "300"))  # 方案 §6-R1：≤5 分钟
CHALLENGE_TTL_SEC = 60

HKDF_SALT = b"ambrace-id-v1"
HKDF_INFO = b"ambrace-pair-v1"
KS_INFO = b"ambrace-ks-v1"          # 包裹身份密钥用的密钥流派生标签（与 pair-mac 密钥分开）
FP_LABEL = b"ambrace-fp-v1"
SERVER_NAME = "AMBRACE Server"        # 展示用，不参与信任（信任根只有密钥本身）
KEY_FILE_ENV = "AMBRACE_SERVER_IDENTITY_KEY_FILE"

CHALLENGE_HEADER = "x-ambrace-challenge"
PROOF_HEADER = "x-ambrace-proof"

#: 响应签名白名单（方案 §7-Q3 最小集；方案写的「runtime_flags 快照」现网无对应公开端点，
#: 只有需登录的 ``/api/v1/system/feature-flags`` 与管理面 ``/server/flags``，故不入清单）。
#: 加条目前请先确认该端点：低特权或匿名可达、且 App 会据响应改行为（方案 §1.7 / T6）。
SIGN_PATHS = frozenset({
    "/api/v1/system/health",
    "/api/v1/system/ready",
    "/api/v1/system/status",
    "/api/v1/system/liveness",
    "/api/v1/system/updates",
    "/api/v1/auth/login",
    "/api/v1/system/identity/pair-start",
    "/api/v1/system/identity/pair",
})


class PairError(Exception):
    """配对链路失败（挑战过期 / mac 不符 / 无待用配对码）。

    ``message`` 必须是不可区分文案：不能让调用方据此判断「配对码存在但 mac 错」。
    """

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# ── 密钥文件 ──────────────────────────────────────────────────────────────────

def key_file_path() -> Path:
    """身份密钥文件路径（环境变量优先，便于测试/多实例；默认 backend/data/server_identity.key）。"""
    raw = (os.environ.get(KEY_FILE_ENV) or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path(__file__).resolve().parents[1] / "data" / "server_identity.key"


def _harden(path: Path) -> None:
    """权限收紧复用凭据主密钥的实现（chmod 0600 + Windows 用户 ACL，失败只 WARNING）。"""
    from app.utils.credential_crypto import _harden_key_file_permissions

    _harden_key_file_permissions(path)


def _read_key_file(path: Path) -> bytes | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except Exception as e:  # noqa: BLE001 —— 读不到密钥不能让 health 挂掉
        _logger.warning("[identity] 身份密钥读取失败（视为未签发）: %s", e)
        return None
    if not raw:
        return None
    try:
        key = base64.b64decode(raw.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError) as e:
        _logger.warning("[identity] 身份密钥文件内容非法（视为未签发）: %s", e)
        return None
    return key if len(key) == 32 else None


def _write_key_file(secret: bytes) -> None:
    path = key_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(base64.b64encode(secret).decode("ascii"), encoding="utf-8")
    _harden(path)


# 状态缓存：(文件 mtime_ns, size) -> 密钥；密钥变更（含 rotate/文件被换）必须留痕（方案 §6-R5）
_LOCK = threading.Lock()
_STAMP_MEMORY = "in-memory"        # 密钥只在内存里（落盘失败 fail-open）时的缓存戳
_ACTIVE: bytes | None = None
_ACTIVE_STAMP: tuple | str | None = None
_ACTIVE_FP: str = ""
_PENDING: dict | None = None          # {code_sha256, shared, keystream, wrapped, fp, expires_at}（从不存明文码）
_CHALLENGES: dict[str, float] = {}    # challenge -> expires_at
_STATS = {"pair_success": 0, "pair_fail": 0}


def _file_stamp(path: Path) -> tuple | None:
    """(mtime_ns, size)；读不到（不存在/权限异常）一律 None ＝ 未签发。"""
    try:
        st = path.stat()
    except Exception:  # noqa: BLE001
        return None
    return (st.st_mtime_ns, st.st_size)


def _persist(secret: bytes) -> tuple | str:
    """身份密钥落盘并返回缓存戳；**写失败 fail-open**（只用内存副本，记 WARNING，不抛）。"""
    try:
        _write_key_file(secret)
    except Exception as e:  # noqa: BLE001 —— 落盘失败不能让签发链路挂掉
        _logger.warning("[identity] 身份密钥落盘失败，本次仅用内存副本（重启后需重新配对）: %s", e)
        return _STAMP_MEMORY
    return _file_stamp(key_file_path())


def load_or_create_identity() -> bytes | None:
    """全站单份身份密钥（派单 §2.1）：文件缺失自动生成并落盘；读不到可用密钥 → None。

    - **缺文件 → 自动生成**（32B 随机）并落盘，写失败则退化为内存副本 + 日志（fail-open）；
    - 文件存在但内容非法/读取失败 → 返回 None，**绝不静默覆盖**（覆盖＝已配对设备集体失配，
      违反方案 §6-R5）；需要换密钥走显式 ``rotate_identity()``。
    """
    global _ACTIVE, _ACTIVE_STAMP, _ACTIVE_FP
    path = key_file_path()
    stamp = _file_stamp(path)
    want_stamp = _STAMP_MEMORY if stamp is None else stamp
    with _LOCK:
        if _ACTIVE is not None and _ACTIVE_STAMP == want_stamp:
            return _ACTIVE
    if stamp is None:
        secret = secrets.token_bytes(32)
        cached_with = _persist(secret)
        fp = fingerprint_of(secret)
        with _LOCK:
            _ACTIVE, _ACTIVE_STAMP, _ACTIVE_FP = secret, cached_with, fp
        _logger.info("[identity] 身份密钥文件缺失，已自动生成（fp=%s）", fp)
        return secret
    secret = _read_key_file(path)
    fp = fingerprint_of(secret) if secret else ""
    with _LOCK:
        if _ACTIVE_FP and fp and _ACTIVE_FP != fp:
            _logger.warning("[identity] 身份密钥已变更（%s -> %s），已配对设备需重新配对", _ACTIVE_FP, fp)
        _ACTIVE, _ACTIVE_STAMP, _ACTIVE_FP = secret, stamp, fp
    return secret


def active_secret() -> bytes | None:
    """当前用于响应签名/配对的身份密钥（等价于 :func:`load_or_create_identity`）。"""
    return load_or_create_identity()


def get_fp() -> str:
    """当前身份密钥的指纹短码（12 位十六进制）；无可用密钥 → 空串。"""
    secret = load_or_create_identity()
    return fingerprint_of(secret) if secret else ""


def current_fingerprint() -> str:
    return get_fp()


def key_created_at() -> str:
    """密钥文件创建时间（控制台展示，供 R5「文件被换过」目视核对）；未签发 → 空串。"""
    try:
        st = key_file_path().stat()
    except Exception:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))


def reset_state_for_test() -> None:
    """清空进程内状态（测试隔离用；生产路径不调用）。"""
    global _ACTIVE, _ACTIVE_STAMP, _ACTIVE_FP, _PENDING
    with _LOCK:
        _ACTIVE, _ACTIVE_STAMP, _ACTIVE_FP = None, None, ""
        _PENDING = None
        _CHALLENGES.clear()
        _STATS.update(pair_success=0, pair_fail=0)


# ── 派生原语（Dart / Kotlin 必须同值） ────────────────────────────────────────

def hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    """RFC 5869 HKDF（extract + expand），纯标准库。"""
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm, block, counter = b"", b"", 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def derive_shared(code: str) -> bytes:
    """配对码本地派生的 pair-mac 密钥（App 侧同法，码本身永不过网）。"""
    return hkdf_sha256(code.strip().upper().encode("ascii"), HKDF_SALT, HKDF_INFO)


def derive_keystream(code: str) -> bytes:
    """配对码派生的**包裹用**密钥流（与 pair-mac 密钥不同标签，互不挪用）。"""
    return hkdf_sha256(code.strip().upper().encode("ascii"), HKDF_SALT, KS_INFO)


def wrap_identity(secret: bytes, keystream: bytes) -> str:
    """身份密钥 ↔ 十六进制密文（对称：同一函数用于加与解）。

    这不是加密（无保密性保证之外的抗 tamper 设计），只是「没有配对码就还原不出密钥」，
    与整条链路口径一致：防伪造，不防窃听。
    """
    return bytes(a ^ b for a, b in zip(secret, keystream)).hex()


def unwrap_identity(wrapped_hex: str, keystream: bytes) -> bytes:
    """从包裹态还原身份密钥（App 侧口径：本地码派生密钥流 → 亦即同一 XOR）。"""
    return bytes(a ^ b for a, b in zip(bytes.fromhex(wrapped_hex), keystream))


def code_digest(code: str) -> str:
    """配对码摘要（服务器只存这个，**不存明文码**）。"""
    return hashlib.sha256(code.strip().upper().encode("ascii")).hexdigest()


def fingerprint_of(secret: bytes) -> str:
    return hmac.new(secret, FP_LABEL, hashlib.sha256).digest()[:6].hex()


def format_fingerprint(fp: str) -> str:
    return "-".join(fp[i:i + 4] for i in range(0, len(fp), 4))


def proof_for(secret: bytes, nonce: str, status: int, body: bytes,
              ts: int | None = None) -> tuple[int, str]:
    """响应签名（方案 §4.0：请求侧只带 nonce，签名体现在响应头）。"""
    ts = int(time.time()) if ts is None else int(ts)
    canonical = "\n".join([
        nonce or "-",
        str(status),
        hashlib.sha256(body).hexdigest(),
        str(ts),
    ]).encode("utf-8")
    mac = hmac.new(secret, canonical, hashlib.sha256).hexdigest()
    return ts, "v1 %d %s" % (ts, mac)


def verify_proof(secret: bytes, header_value: str, nonce: str, status: int, body: bytes) -> bool:
    """给测试/自查用的对称实现（服务器不拒绝任何请求，见模块 docstring 第 3 条）。"""
    parts = (header_value or "").split()
    if len(parts) != 3 or parts[0] != "v1" or not parts[2]:
        return False
    try:
        ts = int(parts[1])
    except ValueError:
        return False
    _, expect = proof_for(secret, nonce, status, body, ts=ts)
    return hmac.compare_digest(expect, header_value)


def _pair_mac(secret: bytes, challenge: str) -> str:
    return hmac.new(secret, b"v1\n" + challenge.encode("utf-8"), hashlib.sha256).hexdigest()


# ── 配对码签发（控制台带外显示） ──────────────────────────────────────────────

def issue_pairing_code() -> dict:
    """生成一次性配对码，返回 ``{code, fp, fp_display, ttl_sec, expires_in}``。

    **code 只能回给桌面控制台**（本机带外面）；App 侧任何端点都不回配对码。
    新码会顶掉上一个未使用的码（同一时刻只允许一屏在输的码）。
    **签发新码不改变身份密钥**（派单硬要求 / 方案 §6-R5）：``fp`` 恒为当前身份密钥指纹，
    因此旧设备不受影响，控制台显示的指纹也不会因“再点一次生成码”而跳动。
    """
    site = load_or_create_identity()
    if site is None:
        # 密钥文件存在但不可用（损坏/被换）：不签发，避免把一份设备无法使用的密钥继续分发
        raise PairError(503, "identity key unavailable")
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
    shared = derive_shared(code)
    keystream = derive_keystream(code)
    fp = fingerprint_of(site)
    pending = {
        "code_sha256": code_digest(code),   # 只留摘要供核对/一次性，明文码不落任何存储
        "shared": shared,
        "keystream": keystream,
        "wrapped": wrap_identity(site, keystream),
        "fp": fp,
        "expires_at": time.time() + CODE_TTL_SEC,
    }
    global _PENDING
    with _LOCK:
        _PENDING = pending
        _CHALLENGES.clear()  # 新码顶掉上一个未使用的码（同一时刻只允许一屏在输的码）
    _logger.info("[identity] 已签发一次性配对码（fp=%s，有效 %ds）", fp, CODE_TTL_SEC)
    return {
        "code": code,
        "fp": fp,
        "fp_display": format_fingerprint(fp),
        "ttl_sec": CODE_TTL_SEC,
        "expires_in": CODE_TTL_SEC,
    }


def pending_info() -> dict:
    """待用配对码的可见信息（**不含码本身**）：``{has_pending, fp, expires_in}``。"""
    global _PENDING
    with _LOCK:
        pending = dict(_PENDING) if _PENDING else None
    if not pending:
        return {"has_pending": False, "fp": "", "expires_in": 0}
    left = int(pending["expires_at"] - time.time())
    if left <= 0:
        with _LOCK:
            if _PENDING and _PENDING["expires_at"] <= time.time():
                _PENDING = None
        return {"has_pending": False, "fp": "", "expires_in": 0}
    return {"has_pending": True, "fp": pending["fp"], "expires_in": left}


def rotate_identity() -> dict:
    """轮换身份密钥（方案 §6-R5：**显式两步**＝换新密钥+签新码，并提示旧设备需重新配对）。

    这是唯一「合法地」改变身份密钥的入口（另一个是密钥文件缺失时的自动生成）。旧密钥
    立刻失效：已配对设备验签会失败 → App 侧提示重新配对；``enforce`` 下服务器用新密钥出签，
    旧设备解不开。签发过程本身不落明文码。
    """
    global _ACTIVE, _ACTIVE_STAMP, _ACTIVE_FP, _PENDING
    with _LOCK:
        _PENDING = None
        _CHALLENGES.clear()
    old_fp = _ACTIVE_FP
    secret = secrets.token_bytes(32)
    cached_with = _persist(secret)
    fp = fingerprint_of(secret)
    with _LOCK:
        _ACTIVE, _ACTIVE_STAMP, _ACTIVE_FP = secret, cached_with, fp
    issued = issue_pairing_code()
    issued["old_fp"] = old_fp
    issued["note"] = "旧设备需重新配对（身份密钥与指纹已更换）"
    return issued


def _take_pending() -> dict | None:
    """取并清空待用配对码（一次性消费）。"""
    global _PENDING
    with _LOCK:
        pending = dict(_PENDING) if _PENDING else None
        _PENDING = None
        return pending


# ── challenge / response ──────────────────────────────────────────────────────

def pair_start() -> dict:
    pending = pending_info()
    if not pending["has_pending"]:
        raise PairError(409, "no active pairing code")
    challenge = secrets.token_urlsafe(24)
    now = time.time()
    with _LOCK:
        _CHALLENGES.clear()  # 只留最新一次挑战（同时只有一台设备在配）
        _CHALLENGES[challenge] = now + CHALLENGE_TTL_SEC
        if _PENDING is None:
            raise PairError(409, "no active pairing code")
    return {"challenge": challenge, "server_name": SERVER_NAME}


def pair_finish(challenge: str, mac: str) -> dict:
    """校验 challenge/mac，成功返回身份密钥的包裹态 + 指纹（**不改动身份密钥**）。

    App 侧用 ``HKDF(本地输入的码)`` 解出密钥并核对 ``fp`` 与控制台屏幕上的那个（双源比对）。
    失败文案对「无待用码 / 码已过期 / mac 不符」一律同一句（401），不给探测留缝隙。
    """
    def _fail() -> PairError:
        with _LOCK:
            _STATS["pair_fail"] += 1
        return PairError(401, "pairing failed")

    if not challenge or not mac:
        raise _fail()
    now = time.time()
    with _LOCK:
        expires = _CHALLENGES.get(str(challenge))
        expired = expires is not None and expires <= now
        pending = dict(_PENDING) if _PENDING else None
    if expired:
        with _LOCK:
            _CHALLENGES.pop(str(challenge), None)
        _logger.warning("[identity] 配对挑战已过期")
        raise _fail()
    if expires is None or pending is None:
        raise _fail()
    if not hmac.compare_digest(_pair_mac(pending["shared"], str(challenge)), str(mac)[:256]):
        raise _fail()
    with _LOCK:
        _CHALLENGES.pop(str(challenge), None)
    # 一次性消费：码用掉即作废（重复使用 → 无待用码 → 401）
    taken = _take_pending()
    if taken is None:
        raise _fail()
    with _LOCK:
        _STATS["pair_success"] += 1
    _logger.info("[identity] 配对成功，身份密钥已下发（fp=%s，密钥未变更）", taken["fp"])
    return {
        "status": "ok",
        "server_name": SERVER_NAME,
        "fp": taken["fp"],
        "fp_display": format_fingerprint(taken["fp"]),
        "wrapped_key": taken["wrapped"],
    }


def pair_stats() -> dict:
    with _LOCK:
        return dict(_STATS)


# ── enforce 模式读取 / health 字段 / 自查信息 ─────────────────────────────────

def identity_info() -> dict:
    """已配对设备**自查**用：服务器名 + 指纹短码。

    **它不是信任根**：明文 http 下这个响应本身可被伪造者替换。信任根只有配对时经控制台屏幕
    带外确认、并落进设备安全存储的那份身份密钥（方案 §3.1）。本端点存在只是为了让设备/运维
    能核对「现在这台服务器还是不是我配过的那台」。
    """
    fp = get_fp()
    return {
        "server_name": SERVER_NAME,
        "fp": fp,
        "fp_display": format_fingerprint(fp) if fp else "",
    }


async def current_mode() -> str:
    """``off``（默认）/ ``shadow`` / ``enforce``；读失败一律回落 ``off``（零行为变化）。"""
    from app.application.server_settings_service import get_identity_enforce_mode
    from app.db.database import async_session_factory

    try:
        async with async_session_factory() as db:
            return await get_identity_enforce_mode(db)
    except Exception as e:  # noqa: BLE001 —— 配置表缺失/被锁不打挂 health
        _logger.warning("[identity] enforce 模式读取失败，回落 off: %s", e)
        return "off"


async def health_identity_block() -> dict | None:
    """``/health`` 的 ``identity`` 节：仅在非 off 模式出现（off 时 health 逐字节不变）。

    字段只作「已配对设备自查/展示」，**不是信任根**——明文下这个字段本身可被替换（方案 §3.1）。
    """
    mode = await current_mode()
    if mode == "off":
        return None
    return {
        "server_name": SERVER_NAME,
        "fp_hint": current_fingerprint(),
        "mode": mode,
    }


# ── 响应签名中间件 ────────────────────────────────────────────────────────────

async def _drain_body(resp) -> bytes:
    chunks = []
    async for chunk in resp.body_iterator:
        chunks.append(chunk if isinstance(chunk, (bytes, bytearray)) else str(chunk).encode("utf-8"))
    return b"".join(chunks)


async def _replay(resp, body: bytes) -> None:
    async def _gen():
        yield body

    resp.body_iterator = _gen()


async def sign_response(request, call_next):
    """给白名单端点的 JSON 响应追加 ``X-Ambrace-Proof``（BaseHTTPMiddleware dispatch 形态）。

    三态口径（方案 §4.7 + 派单 §2.1）：

    - ``off``：什么都不做（既有响应逐字节不变，也不加头）；
    - ``shadow``：出签；**没有身份密钥时只记 WARNING 并照常放行**，绝不因签名问题拒绝；
    - ``enforce``：同上出签，但**请求带了 nonce 却出不了签**（密钥缺失）→ 503 拒绝。
      没带 ``X-Ambrace-Challenge`` 的请求（探针 / 旧包 / 未配对 App）照旧放行，
      免得把 watchdog / K8s 探针一起打挂。
    """
    from starlette.responses import JSONResponse

    resp = await call_next(request)
    if request.method not in ("GET", "POST") or request.url.path not in SIGN_PATHS:
        return resp
    if request.method == "OPTIONS":
        return resp
    mode = await current_mode()
    if mode == "off":
        return resp
    if "application/json" not in (resp.headers.get("content-type") or ""):
        if mode == "shadow":
            _logger.warning("[identity] 响应非 JSON，跳过签名: %s", request.url.path)
        return resp
    body = await _drain_body(resp)
    secret = active_secret()
    nonce = (request.headers.get(CHALLENGE_HEADER) or "")[:128]
    if secret is None:
        await _replay(resp, body)
        if mode == "enforce" and nonce:
            _logger.warning("[identity] enforce 下无可用身份密钥: %s", request.url.path)
            return JSONResponse(
                status_code=503,
                content={"ok": False,
                         "error": {"code": "identity_key_unavailable",
                                   "message": "服务器身份密钥未签发，请重新配对",
                                   "detail": "server identity key is not available"}},
            )
        if mode == "shadow":
            _logger.warning("[identity] 无身份密钥，响应未签名: %s", request.url.path)
        return resp
    _ts, proof = proof_for(secret, nonce, resp.status_code, body)
    resp.headers[PROOF_HEADER] = proof
    await _replay(resp, body)
    return resp
