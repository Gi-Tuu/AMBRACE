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
import time
from pathlib import Path
from typing import Any

# 与 main.py 同深度（plugins/examples/<插件>/<文件>）⇒ parents[3] 都是仓库根。
_REPO_ROOT = Path(__file__).resolve().parents[3]
STATE_PATH = _REPO_ROOT / "backend" / "data" / "plugins" / "garden_forum_state.json"

# 凭据字段名＝AAD，不能改（改了等于把已有密文判成空）
CREDENTIAL_FIELDS = ("partner_key", "api_key")

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


def _blank() -> dict:
    return {"base_url": "", "partner_key": "", "bound": {}, "next_at": {},
            "read_seen": {}, "reply_seen": {}, "dm_seen": {}, "last_beat": 0}


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
            return base
        base.update(doc)
    except Exception:
        return _blank()
    if cc is not None:
        try:
            cc.decrypt_json_document(base, CREDENTIAL_FIELDS)
        except Exception:
            pass
    return base


def save(doc: dict, path: Path | None = None) -> bool:
    """写状态（凭据先加密）。落盘＝临时文件 + `os.replace`，避免半截文件被下一轮读到。"""
    p = path or STATE_PATH
    cc = _crypto()
    payload = json.loads(json.dumps(doc, ensure_ascii=False))  # 深拷贝，不改调用方的 doc
    if cc is not None:
        try:
            cc.encrypt_json_document(payload, CREDENTIAL_FIELDS)
        except Exception:
            pass
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def bound_ids(doc: dict) -> list[str]:
    """已绑定（本地存着 api_key）的角色 id，按数字序。"""
    out = []
    for k in (doc.get("bound") or {}):
        try:
            out.append((int(k), k))
        except (TypeError, ValueError):
            out.append((10 ** 9, str(k)))
    return [k for _n, k in sorted(out)]


def put_binding(doc: dict, character_id: Any, api_key: str, name: str = "",
                user_id: Any = None, now: float | None = None) -> None:
    doc.setdefault("bound", {})[str(character_id)] = {
        "api_key": api_key or "", "name": name or "", "user_id": user_id,
        "bound_at": int(now if now is not None else time.time())}


def drop_binding(doc: dict, character_id: Any) -> bool:
    cid = str(character_id)
    got = (doc.get("bound") or {}).pop(cid, None)
    (doc.get("next_at") or {}).pop(cid, None)
    (doc.get("read_seen") or {}).pop(cid, None)
    (doc.get("dm_seen") or {}).pop(cid, None)
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
