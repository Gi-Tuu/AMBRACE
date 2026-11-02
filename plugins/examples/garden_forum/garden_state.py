# -*- coding: utf-8 -*-
"""garden_forum 插件的本地状态与凭据存储（JSON 文件，凭据走本机主密钥信封加密）。

为什么是这个位置
----------------
- `backend/data/plugins/` 已在 .gitignore 里 ⇒ 凭据**不可能**被提交（这条比"记得别提交"可靠）。
- 不用 `sdk.plugin_base()` 建 ORM 表：`plugin_metadata` 是进程级全局 MetaData，
  新增表会让 `backend/tests/test_plugin_isolated_metadata.py` 的**精确等值断言**当场变红
  （它只认 douyin 的 5 张 + wechat 的 2 张）。派单的 5.3 也是这个建议。
- 加密直接复用现成设施 `app.utils.credential_crypto.encrypt_json_document/decrypt_json_document`
  （A8 方案 B，此前全项目无人调用；字段名即 AAD，所以 JSON 里的键名必须就叫
  `partner_key` / `api_key`，改成别的名字会让既有密文判空）。

日志纪律：**任何情况下都不打印 key 本体**，只打印「有没有」「长度」「绑定几个角色」。
"""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# 与 main.py 同深度（plugins/examples/<插件>/<文件>）⇒ parents[3] 都是仓库根。
_REPO_ROOT = Path(__file__).resolve().parents[3]
STATE_PATH = _REPO_ROOT / "backend" / "data" / "plugins" / "garden_forum_state.json"

# 凭据字段名＝AAD，不能改（改了等于把已有密文判成空）
CREDENTIAL_FIELDS = ("partner_key", "api_key", "user_key")

# 本地去重窗：论坛对同一对象 24h 内只允许 3 次互动，读过的帖也不该反复报"我读了"
READ_TTL_SEC = 26 * 3600
REPLY_TTL_SEC = 26 * 3600      # 「这条留言已经问过模型了」——模型沉默时不再每拍重问
DM_TTL_SEC = 7 * 24 * 3600
SEEN_CAP = 200

_empty: dict[str, Any] = {}


def _crypto():
    """拿加密设施；拿不到就返回 None（调用侧降级为明文，可用性优先，与 credential_crypto 同口径）。"""
    try:
        from app.utils import credential_crypto as cc
        return cc
    except Exception:
        return None


def _blank(source: str = "missing") -> dict:
    return {"base_url": "", "partner_key": "", "manual_only": False, "rev": 0,
            "_state_source": source, "bound": {}, "next_at": {},
            "read_seen": {}, "reply_seen": {}, "dm_seen": {}, "last_beat": 0,
            "digest": {}, "posts": {}, "pending": {}, "pending_tombstones": {},
            "observations": {}, "observe_epoch": 0,
            "user_bindings": {}, "user_binding_epoch": 0}


def load(path: Path | None = None) -> dict:
    """读状态并解出凭据。文件不存在／坏 JSON／不是对象 ⇒ 返回空档。

    这里**任何**异常都不许往外抛：调用方是 `schedule_tick`，抛出去就变成每 30 秒一条 warning，
    而"未绑定"本来就该安静地不动作。
    """
    p = path or STATE_PATH
    cc = _crypto()
    base = _blank()
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            return _blank("corrupt")
        base.update(doc)
    except Exception:
        if not p.exists():
            # 真正首次安装：只给“缺失 + 加密可用”的空档可信 meta；
            # 损坏文件、旧明文/不可解密文仍走 corrupt/credential 拒绝，绝不重置。
            fresh = _blank("missing")
            if cc is not None:
                fresh["_credential_meta"] = {"crypto": True, "decrypt_failed": 0,
                                              "plaintext": [], "ok": True}
            return fresh
        return _blank("corrupt")
    meta = {"crypto": cc is not None, "decrypt_failed": 0, "plaintext": [],
            "ok": cc is not None}
    if cc is not None:
        # 先按**文件原值**判断明文，再解密（解密后当然不是密文，不能事后再判）
        plain = []
        for container, key in _walk_credential_fields(base):
            value = container.get(key)
            if value and not cc.is_encrypted(value):
                plain.append(key)
        try:
            # 有密文但原主密钥文件不存在：不要调用 decrypt（其会静默新建密钥，等于永久毁掉旧密文）
            has_cipher = any(
                cc.is_encrypted(container.get(key))
                for container, key in _walk_credential_fields(base)
            )
            if has_cipher and not cc.key_file_path().is_file():
                # 保留磁盘原文；内存按旧契约置空，且不调用 decrypt（避免静默新建密钥）
                for container, key in _walk_credential_fields(base):
                    if cc.is_encrypted(container.get(key)):
                        container[key] = None
                meta["decrypt_failed"] = 1
            else:
                _doc, _dec, failed = cc.decrypt_json_document(base, CREDENTIAL_FIELDS)
                meta["decrypt_failed"] = int(failed)
        except Exception:
            meta["decrypt_failed"] = 1
        meta["plaintext"] = sorted(set(plain))
        meta["ok"] = meta["decrypt_failed"] == 0 and not meta["plaintext"]
    try:
        base["_rev"] = int(base.get("rev") or 0)
    except (TypeError, ValueError):
        base["_rev"] = 0
    base["_gen"] = str(base.get("gen") or "")
    base["_state_source"] = "ok"
    base["_credential_meta"] = meta
    return base


def credential_usable(doc: dict) -> bool:
    """绑定/模型/写动作前的硬门：加密设施可用、密文可解、没有明文降级。"""
    meta = (doc or {}).get("_credential_meta") or {}
    return bool(meta.get("ok") is True and meta.get("crypto") is True
                and not meta.get("decrypt_failed") and not meta.get("plaintext"))


def credential_problem(doc: dict) -> str:
    meta = (doc or {}).get("_credential_meta") or {}
    if not meta:
        return "unknown"
    if not meta.get("crypto"):
        return "crypto_unavailable"
    if meta.get("decrypt_failed"):
        return "decrypt_failed"
    if meta.get("plaintext"):
        return "plaintext_credentials"
    return ""


def _walk_credential_fields(doc):
    if isinstance(doc, dict):
        for k, v in list(doc.items()):
            if k in CREDENTIAL_FIELDS:
                yield doc, k
            yield from _walk_credential_fields(v)
    elif isinstance(doc, list):
        for v in doc:
            yield from _walk_credential_fields(v)


_LOCK_LOCAL = threading.local()


@contextmanager
def _state_lock(path: Path):
    """跨进程独占锁（同目录 sidecar）；同线程可重入，拿不到就抛错。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    held = getattr(_LOCK_LOCAL, "held", None)
    if held is None:
        held = _LOCK_LOCAL.held = {}
    key = str(lock_path)
    if key in held:
        held[key]["depth"] += 1
        try:
            yield
        finally:
            held[key]["depth"] -= 1
        return
    fh = open(lock_path, "a+b")
    entry = {"fh": fh, "depth": 1}
    try:
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:
            fh.write(b"0"); fh.flush()
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        held[key] = entry
        try:
            yield
        finally:
            held.pop(key, None)
            if os.name == "nt":
                import msvcrt
                fh.seek(0); msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


def _subset_equal(provided, current) -> bool:
    """手工/旧调用的无版本 doc：只有与现有状态逻辑一致时允许幂等 no-op，不得覆盖差异。"""
    if isinstance(provided, dict):
        if not isinstance(current, dict):
            return False
        for k, v in provided.items():
            if str(k).startswith("_"):
                continue
            if k not in current or not _subset_equal(v, current.get(k)):
                return False
        return True
    if isinstance(provided, list):
        return isinstance(current, list) and len(provided) == len(current) and all(
            _subset_equal(a, b) for a, b in zip(provided, current))
    return provided == current


def _save_unlocked(doc: dict, path: Path | None = None) -> bool:
    p = path or STATE_PATH
    cc = _crypto()
    if cc is None:
        return False
    meta = (doc or {}).get("_credential_meta") or {}
    if meta and meta.get("ok") is False and (doc.get("partner_key") or doc.get("bound")):
        return False
    # 版本 CAS：旧 load 快照不得覆盖更新后的文件
    source = (doc or {}).get("_state_source") if isinstance(doc, dict) else None
    # 损坏快照没有可信来源：无论目标当前是否存在都拒绝，绝不新建空状态
    if source == "corrupt":
        return False
    current_rev = 0
    current_gen = ""
    target_exists = p.exists()
    if target_exists:
        try:
            current = json.loads(p.read_text(encoding="utf-8"))
            current_rev = int(current.get("rev") or 0)
            current_gen = str(current.get("gen") or "")
        except Exception:
            return False                       # 损坏/非对象：fail closed
        if source in ("missing", "corrupt"):
            return False                       # 缺失/损坏快照不得覆盖已存在状态
        if not isinstance(doc, dict) or "_rev" not in doc:
            # 合法旧版文件（无 rev/gen）允许被旧调用方更新；新版文件（有 gen）不允许无版本覆盖
            if current_rev == 0 and not current_gen:
                pass
            else:
                # 手工无版本 doc：只有与现有状态逻辑一致时幂等 no-op；不同内容必须拒绝
                try:
                    if _subset_equal(doc, load(p)):
                        return True
                except Exception:
                    pass
                return False
        doc_gen = str((doc or {}).get("_gen") or "")
        # 删除后重建的 ABA：新文件 generation 不同，旧快照即使 rev 碰巧相同也拒绝
        if current_gen and doc_gen and doc_gen != current_gen:
            return False
        if current_gen and not doc_gen:
            return False
    has_expected = isinstance(doc, dict) and "_rev" in doc
    try:
        expected_rev = int((doc or {}).get("_rev") or 0)
    except (TypeError, ValueError):
        return False
    if has_expected and expected_rev != current_rev:
        return False
    if not has_expected:
        expected_rev = current_rev
    payload = json.loads(json.dumps(doc, ensure_ascii=False))
    new_rev = current_rev + 1
    new_gen = current_gen or ("g-" + os.urandom(12).hex())
    payload["rev"] = new_rev
    payload["gen"] = new_gen
    for key in [k for k in payload if str(k).startswith("_")]:
        payload.pop(key, None)
    try:
        cc.encrypt_json_document(payload, CREDENTIAL_FIELDS)
    except Exception:
        return False
    for container, key in _walk_credential_fields(payload):
        value = container.get(key)
        if value and not cc.is_encrypted(value):
            return False
    tmp = p.with_suffix(p.suffix + ".strict.tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)
        # 只有实际提交成功后才推进调用方版本；失败保持旧版本
        try:
            doc["_rev"] = new_rev
            doc["_gen"] = new_gen
            doc["_state_source"] = "ok"
        except Exception:
            pass
        return True
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False


class UpdateConflict(Exception):
    """mutator 发现并发变化；不写，调用方可安全重试。"""


def update_state(mutator, path: Path | None = None):
    """跨进程锁内 load→mutator(latest)→save；冲突显式抛出，其他异常原文件不动。"""
    p = path or STATE_PATH
    try:
        with _state_lock(p):
            doc = load(p) if path is not None else load()
            try:
                newdoc = mutator(doc)
            except Exception as e:
                raise UpdateConflict(str(e)) from e
            if newdoc is None:
                return False, doc
            try:
                ok = save(newdoc, p) if path is not None else save(newdoc)
            except Exception:
                ok = False
            return ok, newdoc
    except UpdateConflict:
        raise
    except Exception:
        return False, None


def save(doc: dict, path: Path | None = None) -> bool:
    """写状态（凭据先加密 + 跨进程锁 + 原子 replace）。"""
    p = path or STATE_PATH
    try:
        with _state_lock(p):
            return _save_unlocked(doc, p)
    except Exception:
        return False


def manual_mode(doc: dict) -> str:
    """manual / off / invalid；字段缺失=legacy（不阻断旧状态）。"""
    if "manual_only" not in (doc or {}):
        return "legacy"
    value = doc.get("manual_only")
    if value is True:
        return "manual"
    if value is False:
        return "off"
    return "invalid"


def manual_blocks_tick(doc: dict) -> bool:
    return manual_mode(doc) in ("manual", "invalid")


def is_manual_only(doc: dict) -> bool:
    """兼容读取：仅严格 true。"""
    return (doc or {}).get("manual_only") is True


def save_new(doc: dict, path: Path | None = None) -> str:
    """原子“仅当不存在才创建”：temp 完整加密后 hardlink 到目标，绝不覆盖。"""
    p = path or STATE_PATH
    tmp = p.with_name("%s.create-%d-%d.tmp" % (p.name, os.getpid(), time.time_ns()))
    try:
        with _state_lock(p):
            if not _save_unlocked(doc, tmp):
                return "failed"
            try:
                os.link(tmp, p)
                return "created"
            except FileExistsError:
                return "exists"
            except OSError:
                return "failed"
    except Exception:
        return "failed"
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass



def bound_ids(doc: dict, base_url: str | None = None) -> list[str]:
    """已绑定角色；给了 base_url 时只取绑定到该站点的（改 host 不得复用旧 key）。"""
    """已绑定（本地存着 api_key）的角色 id，按数字序。"""
    out = []
    want = (base_url or "").strip().rstrip("/")
    for k, rec in (doc.get("bound") or {}).items():
        if want and str((rec or {}).get("base_url") or "").strip().rstrip("/") != want:
            continue
        try:
            out.append((int(k), k))
        except (TypeError, ValueError):
            out.append((10 ** 9, str(k)))
    return [k for _n, k in sorted(out)]


def _norm_base(base_url: Any) -> str:
    return str(base_url or "").strip().rstrip("/")


def remote_id_of(doc: dict, character_id: Any) -> str:
    """显式映射的远端 character_id；旧绑定没有该字段时返回空串（不是伪造 cid）。"""
    rec = (doc.get("bound") or {}).get(str(character_id)) or {}
    return str(rec.get("remote_id") or "").strip()


def effective_remote_id(doc: dict, character_id: Any) -> str:
    """真正发给论坛的身份：有显式映射用映射，旧绑定兼容为本地 id。"""
    return remote_id_of(doc, character_id) or str(character_id)


def local_for_remote(doc: dict, base_url: str, remote_id: Any, *, include_legacy: bool = True) -> str | None:
    """远端 id → 唯一本地 cid；同站点出现两个绑定或没有绑定都返回 None（歧义/未知）。"""
    rid = str(remote_id or "").strip()
    if not rid:
        return None
    want = _norm_base(base_url)
    hits = []
    for cid, rec in (doc.get("bound") or {}).items():
        rec = rec or {}
        if want and _norm_base(rec.get("base_url")) != want:
            continue
        mapped = str(rec.get("remote_id") or "").strip()
        if mapped == rid or (include_legacy and not mapped and str(cid) == rid):
            hits.append(str(cid))
    return hits[0] if len(hits) == 1 else None


def remote_id_conflict(doc: dict, base_url: str, remote_id: Any, *, except_cid: Any = None) -> str | None:
    """同一站点同一远端 id 只能挂一个本地角色；返回冲突的本地 cid。"""
    rid = str(remote_id or "").strip()
    if not rid:
        return None
    want = _norm_base(base_url)
    for cid, rec in (doc.get("bound") or {}).items():
        if except_cid is not None and str(cid) == str(except_cid):
            continue
        rec = rec or {}
        if want and _norm_base(rec.get("base_url")) != want:
            continue
        if str(rec.get("remote_id") or "").strip() == rid:
            return str(cid)
    return None


def put_binding(doc: dict, character_id: Any, api_key: str, name: str = "",
                user_id: Any = None, now: float | None = None,
                base_url: str | None = None, remote_id: str | None = None) -> None:
    cid = str(character_id)
    old = (doc.get("bound") or {}).get(cid) or {}
    rec = {
        "api_key": api_key or "", "name": name or "", "user_id": user_id,
        "base_url": _norm_base(base_url or doc.get("base_url")),
        "bound_at": int(now if now is not None else time.time())}
    # 未显式给 remote_id 时保留旧映射，避免普通重存把映射抹掉；旧绑定仍可没有该字段
    rid = str(remote_id if remote_id is not None else old.get("remote_id") or "").strip()
    if rid:
        rec["remote_id"] = rid
    doc.setdefault("bound", {})[cid] = rec


def binding_matches(doc: dict, character_id: Any, base_url: str, user_id: Any = None,
                    *, require_remote: bool = False) -> bool:
    """角色/宿主/站点归属同时匹配才允许使用；改名不影响，换站点/换归属必须重绑。

    ``require_remote=True`` 用于新接入/新写动作的严格场景；默认 False 保留旧绑定兼容。
    """
    rec = (doc.get("bound") or {}).get(str(character_id)) or {}
    if not rec.get("api_key"):
        return False
    want = _norm_base(base_url)
    got = _norm_base(rec.get("base_url"))
    if want and got != want:
        return False
    if user_id is None:
        return False
    if str(rec.get("user_id")) != str(user_id):
        return False
    if require_remote and not str(rec.get("remote_id") or "").strip():
        return False
    return True


OBSERVATION_TTL_SEC = 30 * 60
OBSERVATION_MAX_ITEMS = 5


def set_observation(doc: dict, character_id: Any, *, base_url: str, remote_id: str,
                    user_id: Any, items: list, now: float) -> None:
    """有界短期观察快照；只存公开最小字段，不存密钥/私信/原始响应。"""
    clean = []
    for it in (items or [])[:OBSERVATION_MAX_ITEMS]:
        if not isinstance(it, dict):
            continue
        raw_id = it.get("id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, int) or raw_id <= 0:
            continue
        def _txt(v, n):
            return v[:n] if isinstance(v, str) else ""
        clean.append({
            "id": raw_id,
            "title": _txt(it.get("title"), 120),
            "body": _txt(it.get("body"), 200),
            "author_display": _txt(it.get("author_display"), 60),
            "circle": _txt(it.get("circle"), 60),
            "created_at": _txt(it.get("created_at"), 40),
        })
    doc.setdefault("observations", {})[str(character_id)] = {
        "base_url": _norm_base(base_url),
        "remote_id": str(remote_id),
        "user_id": user_id,
        "read_at": int(now),
        "expires_at": int(now + OBSERVATION_TTL_SEC),
        "source": "readonly_browse",
        "items": clean,
    }


def _binding_key(user_id: Any, character_id: Any) -> str:
    return "%s:%s" % (str(user_id), str(character_id))


def user_binding_of(doc: dict, user_id: Any, character_id: Any = None) -> dict | None:
    """按 (user,character) 取绑定；兼容旧版单 user 键。"""
    bindings = doc.get("user_bindings") or {}
    if character_id is not None:
        rec = bindings.get(_binding_key(user_id, character_id))
        if isinstance(rec, dict):
            return rec
        old = bindings.get(str(user_id))
        if isinstance(old, dict) and str(old.get("character_id")) == str(character_id):
            return old
        return None
    old = bindings.get(str(user_id))
    if isinstance(old, dict):
        return old
    pref = "%s:" % str(user_id)
    for k, rec in bindings.items():
        if str(k).startswith(pref) and isinstance(rec, dict):
            return rec
    return None


def user_binding_for_role(doc: dict, base_url: str, role_id: str) -> str | None:
    """返回占用该 (站点,role_id) 的 user_id；非 active 不占。"""
    want = _norm_base(base_url)
    for key, rec in (doc.get("user_bindings") or {}).items():
        if not isinstance(rec, dict) or not rec.get("active"):
            continue
        if (_norm_base(rec.get("base_url")) == want
                and str(rec.get("role_id") or "") == str(role_id)):
            return str(rec.get("user_id") or str(key).split(":", 1)[0])
    return None


def put_user_binding(doc: dict, user_id: Any, *, base_url: str, role_id: str,
                     character_id: Any, remote_character_id: str, agent: str,
                     user_key: str, scope: str = "readonly", now: float | None = None) -> None:
    try:
        doc["user_binding_epoch"] = int(doc.get("user_binding_epoch") or 0) + 1
    except (TypeError, ValueError):
        doc["user_binding_epoch"] = 1
    if (doc.get("_state_source") == "missing" and not (doc.get("bound") or {})
            and not (doc.get("user_bindings") or {})):
        # 全新安装：默认手动只读，不自动开观察/自动行动；旧 Sam 状态不动。
        doc["manual_only"] = True
    doc.setdefault("user_bindings", {})[_binding_key(user_id, character_id)] = {
        "user_id": str(user_id),
        "base_url": _norm_base(base_url),
        "role_id": str(role_id),
        "character_id": str(character_id),
        "remote_character_id": str(remote_character_id),
        "agent": str(agent),
        "user_key": user_key or "",
        "scope": "readonly" if scope != "readonly" else "readonly",
        "active": True,
        "bound_at": int(now if now is not None else time.time()),
        "last_verified_at": int(now if now is not None else time.time()),
        "binding_epoch": int(doc.get("user_binding_epoch") or 0),
        "observation": None,
        "observation_epoch": 0,
    }


def clear_user_binding(doc: dict, user_id: Any, character_id: Any = None) -> bool:
    bindings = doc.get("user_bindings") or {}
    got = None
    if character_id is not None:
        got = bindings.pop(_binding_key(user_id, character_id), None)
        old = bindings.get(str(user_id))
        if got is None and isinstance(old, dict) and str(old.get("character_id")) == str(character_id):
            got = bindings.pop(str(user_id), None)
    else:
        for k in list(bindings):
            if k == str(user_id) or str(k).startswith("%s:" % str(user_id)):
                got = bindings.pop(k, None) or got
    try:
        doc["user_binding_epoch"] = int(doc.get("user_binding_epoch") or 0) + 1
    except (TypeError, ValueError):
        doc["user_binding_epoch"] = 1
    return got is not None


def set_user_observation(doc: dict, user_id: Any, character_id: Any, *, base_url: str, role_id: str,
                         items: list, now: float) -> bool:
    rec = user_binding_of(doc, user_id, character_id)
    if not rec or not rec.get("active"):
        return False
    if (_norm_base(rec.get("base_url")) != _norm_base(base_url)
            or str(rec.get("role_id")) != str(role_id)):
        return False
    clean = []
    for it in (items or [])[:5]:
        if not isinstance(it, dict):
            continue
        pid = it.get("id")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            continue
        clean.append({"id": pid, "title": str(it.get("title") or "")[:80],
                      "author_display": str(it.get("author_display") or "")[:40],
                      "circle": str(it.get("circle") or "")[:40],
                      "created_at": str(it.get("created_at") or "")[:40]})
    rec["observation"] = {"base_url": _norm_base(base_url), "role_id": str(role_id),
                          "read_at": int(now), "expires_at": int(now + OBSERVATION_TTL_SEC),
                          "items": clean}
    return bool(clean)


def user_observation_of(doc: dict, user_id: Any, now: float, character_id: Any = None,
                        verify_ttl: int = 300) -> dict | None:
    rec = user_binding_of(doc, user_id, character_id)
    if not rec or not rec.get("active"):
        return None
    if now - int(rec.get("last_verified_at") or 0) > verify_ttl:
        return None
    obs = rec.get("observation")
    if not isinstance(obs, dict) or int(obs.get("expires_at") or 0) <= int(now):
        return None
    return obs


def observation_of(doc: dict, character_id: Any, base_url: str, remote_id: str,
                   user_id: Any, now: float) -> dict | None:
    """仅在未过期、站点/远端/租户仍一致时返回快照；否则 None（不写状态）。"""
    rec = (doc.get("observations") or {}).get(str(character_id))
    if not isinstance(rec, dict):
        return None
    try:
        expires = int(rec.get("expires_at") or 0)
    except (TypeError, ValueError):
        return None
    if expires <= int(now):
        return None
    if _norm_base(rec.get("base_url")) != _norm_base(base_url):
        return None
    if str(rec.get("remote_id") or "") != str(remote_id):
        return None
    if str(rec.get("user_id")) != str(user_id):
        return None
    items = rec.get("items")
    return rec if isinstance(items, list) and items else None


def clear_observation(doc: dict, character_id: Any) -> None:
    (doc.get("observations") or {}).pop(str(character_id), None)
    try:
        doc["observe_epoch"] = int(doc.get("observe_epoch") or 0) + 1
    except (TypeError, ValueError):
        doc["observe_epoch"] = 1


def drop_binding(doc: dict, character_id: Any) -> bool:
    cid = str(character_id)
    got = (doc.get("bound") or {}).pop(cid, None)
    for bucket in ("next_at", "read_seen", "reply_seen", "dm_seen", "digest", "posts",
                   "observations"):
        (doc.get(bucket) or {}).pop(cid, None)
    pending = doc.get("pending") or {}
    keep = {}
    tomb = doc.setdefault("pending_tombstones", {})
    for key, val in pending.items():
        if not isinstance(val, dict):
            keep[key] = val
            continue
        own = str(val.get("cid")) == cid
        # 首交旧格式没有 cid：只在已无其它绑定时保守处理，避免把多角色未知证据误删
        legacy = not val.get("cid") and len(doc.get("bound") or {}) == 0
        if own or legacy:
            # unknown 事实不能物理遗忘：移到隔离 tombstone；同键重绑后仍不自动发
            if val.get("state") in ("unknown", "inflight"):
                tomb[str(key)] = dict(val, cid=cid, isolated_at=int(time.time()))
            continue
        keep[key] = val
    doc["pending"] = keep
    try:
        doc["observe_epoch"] = int(doc.get("observe_epoch") or 0) + 1
    except (TypeError, ValueError):
        doc["observe_epoch"] = 1
    return got is not None


def next_at(doc: dict, character_id: Any) -> float:
    try:
        return float((doc.get("next_at") or {}).get(str(character_id), 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def set_next_at(doc: dict, character_id: Any, ts: float) -> None:
    doc.setdefault("next_at", {})[str(character_id)] = int(ts)


def _seen(doc: dict, bucket: str, character_id: Any) -> dict:
    return (doc.get(bucket) or {}).get(str(character_id)) or {}


def _prune(store: dict, ttl: int, now: float) -> dict:
    """按时间窗过滤，再按时间从新到旧截到 SEEN_CAP（超窗的先丢，剩下的保最近的）。"""
    keep = {k: v for k, v in store.items() if isinstance(v, (int, float)) and now - v < ttl}
    if len(keep) > SEEN_CAP:
        newest = sorted(keep.items(), key=lambda kv: -kv[1])[:SEEN_CAP]
        keep = dict(newest)
    return keep


def _bucket(doc: dict, name: str, character_id: Any, ttl: int, now: float | None) -> dict:
    t = now if now is not None else time.time()
    store = _prune(_seen(doc, name, character_id), ttl, t)
    doc.setdefault(name, {})[str(character_id)] = store
    return store


def _mark(doc: dict, name: str, character_id: Any, key: Any, ttl: int, now: float | None) -> None:
    t = now if now is not None else time.time()
    _bucket(doc, name, character_id, ttl, t)[str(key)] = int(t)


def prune_reads(doc: dict, character_id: Any, now: float | None = None) -> dict:
    return _bucket(doc, "read_seen", character_id, READ_TTL_SEC, now)


def seen_reads(doc: dict, character_id: Any, now: float | None = None) -> set[str]:
    return set(prune_reads(doc, character_id, now).keys())


def mark_read(doc: dict, character_id: Any, post_id: Any, now: float | None = None) -> None:
    _mark(doc, "read_seen", character_id, post_id, READ_TTL_SEC, now)


def prune_dms(doc: dict, character_id: Any, now: float | None = None) -> dict:
    return _bucket(doc, "dm_seen", character_id, DM_TTL_SEC, now)


def seen_dms(doc: dict, character_id: Any, now: float | None = None) -> set[str]:
    return set(prune_dms(doc, character_id, now).keys())


def mark_dm(doc: dict, character_id: Any, dm_id: Any, now: float | None = None) -> None:
    _mark(doc, "dm_seen", character_id, dm_id, DM_TTL_SEC, now)


def seen_replies(doc: dict, character_id: Any, now: float | None = None) -> set[str]:
    return set(_bucket(doc, "reply_seen", character_id, REPLY_TTL_SEC, now).keys())


def mark_reply(doc: dict, character_id: Any, comment_id: Any, now: float | None = None) -> None:
    _mark(doc, "reply_seen", character_id, comment_id, REPLY_TTL_SEC, now)
