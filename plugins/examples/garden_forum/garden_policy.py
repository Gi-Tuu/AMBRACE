# -*- coding: utf-8 -*-
"""M3 策略层：只做纯判断，不碰网络/DB/时钟。

为什么单独一层：`main.py` 在插件上下文外不可 import（`sdk.hook` 会抛），
`garden_beat.py` 已经承载旧决策；M3 的新增安全边界（公开资料白名单、双闸、
随机节律、板块、输出分类、pending 规则）放在这里，全部可零网络单测。
"""
from __future__ import annotations

import hashlib
import json
import random
from typing import Any

PUBLIC_KEYS = ("name", "display_name", "bio", "interests", "circles")
PRIVATE_KEYS = ("personality", "self_statement", "memory", "memories", "chat",
                "conversation", "private", "secret", "api_key", "partner_key")

MIN_INTERVAL_MIN = 20
MAX_INTERVAL_MIN = 150
MAX_CHARS_PER_BEAT = 3


def as_bool(value: Any) -> bool:
    return value is True


def allow_llm(cfg: dict) -> bool:
    """模型闸：缺省 false；只有显式 True 才放行。"""
    return as_bool((cfg or {}).get("garden_forum_allow_llm"))


def allow_write(cfg: dict) -> bool:
    """总写闸：覆盖所有 say（含 read_post/评论/私信/主动发帖）。缺省 false。"""
    return as_bool((cfg or {}).get("garden_forum_allow_write"))


def public_profile(cfg: dict, character_id: Any) -> dict | None:
    """取该角色显式配置的公开资料；缺/坏/含私密字段 => None（调用方不得回退完整人设）。"""
    profiles = (cfg or {}).get("public_profiles")
    if not isinstance(profiles, dict):
        return None
    raw = profiles.get(str(character_id))
    if not isinstance(raw, dict):
        return None
    if any(k in raw for k in PRIVATE_KEYS):
        return None
    out: dict[str, Any] = {}
    for key in PUBLIC_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if key == "interests":
            if not isinstance(value, list):
                return None
            cleaned = [str(x).strip()[:80] for x in value if str(x).strip()]
            if cleaned:
                out[key] = cleaned[:8]
        elif key == "circles":
            if not isinstance(value, list):
                return None
            cleaned = [str(x).strip()[:40] for x in value if str(x).strip()]
            if cleaned:
                out[key] = cleaned[:8]
        else:
            if not isinstance(value, str):
                return None
            text = value.strip()
            if text:
                out[key] = text[:300]
    if not (out.get("name") or out.get("display_name")):
        return None
    # 公开简介里夹带私密字段名/凭据形态 => 宁可不发，也不做“猜着脱敏”
    blob = json.dumps(out, ensure_ascii=False).lower()
    if any(tok in blob for tok in ("api_key", "partner_key", "enc:v1:", "password")):
        return None
    return out


def choose_interval_sec(cfg: dict, rng: random.Random | None = None) -> int:
    """M3 随机节律（秒）。

    新配置 `garden_forum_interval_min/max`（默认 20/150 分钟）优先；
    旧 `garden_forum_tmin` 兼容为**下限**（没有显式 min 时使用），不会把 20 分钟
    随机缩成固定旧值。服务端更长的 next_check_in 仍由 next_wait 取 max。
    """
    rng = rng or random.Random()
    c = cfg or {}
    try:
        lo = int(c.get("garden_forum_interval_min", 0) or 0)
    except (TypeError, ValueError):
        lo = 0
    try:
        hi = int(c.get("garden_forum_interval_max", 0) or 0)
    except (TypeError, ValueError):
        hi = 0
    if lo <= 0:
        try:
            lo = max(MIN_INTERVAL_MIN, int(c.get("garden_forum_tmin", 0) or 0))
        except (TypeError, ValueError):
            lo = MIN_INTERVAL_MIN
    if hi <= 0:
        hi = MAX_INTERVAL_MIN
    lo = max(MIN_INTERVAL_MIN, min(MAX_INTERVAL_MIN, lo))
    hi = max(MIN_INTERVAL_MIN, min(MAX_INTERVAL_MIN, hi))
    if hi < lo:
        lo, hi = hi, lo
    return rng.randint(lo, hi) * 60


def choose_beat_characters(cap: int, rng: random.Random | None = None) -> int:
    """每拍处理几个角色：1–3，且不超过配置 cap（cap<=0 视为 1，绝不“全跑”）。"""
    rng = rng or random.Random()
    try:
        n = int(cap)
    except (TypeError, ValueError):
        n = 1
    n = max(1, min(MAX_CHARS_PER_BEAT, n))
    return rng.randint(1, n)


def pick_circle(circles: Any, allowed: Any, profile_allowed: Any) -> str | None:
    """发帖板块必须同时满足三方交集：角色公开资料 circles ∩ 宿主配置 ∩ state 有效板块。

    缺角色 circles / 空交集 => None（本拍不生成、不发）；不静默落第一个，也不拿全局许可
    代替角色许可。
    """
    role_set = {str(x).strip() for x in (profile_allowed or []) if str(x).strip()}
    host_set = {str(x).strip() for x in (allowed or []) if str(x).strip()}
    valid = {}
    for c in circles or ():
        if not isinstance(c, dict):
            continue
        slug = str(c.get("slug") or "").strip()
        if slug:
            valid[slug] = str(c.get("name") or slug)
    for slug in sorted(role_set & host_set & set(valid)):
        return slug
    return None


def classify_generation(raw: Any, *, error: str = "", timed_out: bool = False,
                        truncated: bool = False) -> str:
    """把模型返回/故障分成可区分的状态（不把未知空输出伪装成“自愿沉默”）。"""
    if timed_out:
        return "timeout"
    if error:
        return "error"
    if not isinstance(raw, str):
        return "empty_unknown"
    text = raw.strip()
    if not text:
        return "empty_unknown"
    if truncated:
        return "truncated"
    if text.lower() in {"无", "無", "不", "没有", "没什么好说的", "不回复", "沉默",
                        "empty", "skip", "none", "nil", "(空)", "（空）"}:
        return "explicit_silence"
    return "ok"


def is_explicit_silence(status: str) -> bool:
    return status == "explicit_silence"


def pending_id(character_id: Any, kind: str, target: Any, *,
               base_url: str = "", source: str = "") -> str:
    """稳定动作键：站点/宿主/角色/源事件/动作身份；不靠正文碰撞。

    base_url/source 缺省保持旧调用兼容；主路径会传站点与源事件 id。
    """
    raw = "%s|%s|%s|%s|%s" % (base_url or "", character_id, kind,
                              target if target is not None else "", source or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def can_send(doc: dict, pid: str) -> bool:
    """同一动作键只要有过任何发送记录（inflight/success/failed/unknown）就不再自动发。

    新 DM/新帖/新评论的键不同，仍可正常发送；不靠“成功”当重放通行证，也不做正文碰撞。
    已知 4xx 失败也只留给人工/新动作，不自动重试非幂等写。
    """
    return (str(pid) not in (doc.get("pending") or {})
            and str(pid) not in (doc.get("pending_tombstones") or {}))


def transition(doc: dict, pid: str, state: str, *, code: str = "",
               now: float = 0, meta: dict | None = None) -> None:
    """pending 状态机：inflight → success / failed / unknown。unknown 永不自动重发。"""
    if state not in ("inflight", "success", "failed", "unknown"):
        raise ValueError("bad pending state")
    prev = (doc.get("pending") or {}).get(str(pid)) or {}
    entry = {"state": state, "code": (code or "")[:80], "at": int(now or 0)}
    for key in ("cid", "kind", "action", "target", "base_url", "source", "created_at"):
        if key in prev:
            entry[key] = prev[key]
    if meta:
        for key in ("cid", "kind", "action", "target", "base_url", "source", "created_at"):
            if key in meta:
                entry[key] = meta[key]
    doc.setdefault("pending", {})[str(pid)] = entry


def classify_send_result(result: Any) -> str:
    """发送结果分类：success / failed / unknown。

    5xx（含 internal_error 等自定义 code）、丢响应、超时、取消、不可解析/不可信返回
    一律 unknown；只有明确 4xx/明确业务拒绝才算 failed；unknown 不自动重发。
    """
    if not isinstance(result, dict):
        return "unknown"
    try:
        status = int(result.get("http_status") or 0)
    except (TypeError, ValueError):
        status = 0
    code = str(result.get("code") or "").lower()
    if status >= 500 or code in ("internal_error", "database_error", "server_error",
                                 "backup_failed", "unreachable", "timeout", "cancelled",
                                 "no_response", "bad_response"):
        return "unknown"
    if result.get("ok") is True:
        return "success"
    if 400 <= status < 500:
        return "failed"
    if status == 0 and code in ("bad_request", "body_too_short", "body_too_large",
                                "title_too_short", "title_too_long", "not_found",
                                "not_yours", "only_author", "only_self", "peer_cap",
                                "rate_write", "rate_comment", "rate_post", "rate_follow",
                                "invalid_agent_key", "missing_agent_key", "not_enough_coins",
                                "content_blocked", "name_taken", "need_reason"):
        return "failed"
    return "unknown"


def recover_inflight(doc: dict) -> int:
    """进程重载：把上次停在 inflight 的动作降为 unknown（不猜成功、不重发）。"""
    n = 0
    for p in (doc.get("pending") or {}).values():
        if isinstance(p, dict) and p.get("state") == "inflight":
            p["state"] = "unknown"
            p["code"] = p.get("code") or "process_reload"
            n += 1
    return n


def usage_begin(doc: dict, now: float) -> dict:
    """本地每拍/每日计数骨架；日期按北京时间，跨天/跨拍自动清零。"""
    from datetime import datetime, timedelta, timezone
    day = datetime.fromtimestamp(float(now), timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    u = doc.get("usage") or {}
    if u.get("date") != day:
        u = {"date": day, "day_gen": 0, "day_write": 0,
             "beat_at": int(now), "beat_gen": 0, "beat_write": 0}
    elif int(u.get("beat_at") or 0) != int(now):
        u["beat_at"] = int(now)
        u["beat_gen"] = 0
        u["beat_write"] = 0
    doc["usage"] = u
    return u


def usage_check(doc: dict, now: float, kind: str, per_beat: int, per_day: int) -> bool:
    """kind=gen/write；限额<=0 视为关闭该能力（保守），坏值按 0。"""
    u = usage_begin(doc, now)
    try:
        pb, pd = int(per_beat), int(per_day)
    except (TypeError, ValueError):
        pb = pd = 0
    if pb <= 0 or pd <= 0:
        return False
    if kind == "gen":
        return u.get("beat_gen", 0) < pb and u.get("day_gen", 0) < pd
    return u.get("beat_write", 0) < pb and u.get("day_write", 0) < pd


def usage_inc(doc: dict, now: float, kind: str) -> None:
    u = usage_begin(doc, now)
    if kind == "gen":
        u["beat_gen"] = u.get("beat_gen", 0) + 1
        u["day_gen"] = u.get("day_gen", 0) + 1
    elif kind == "write":
        u["beat_write"] = u.get("beat_write", 0) + 1
        u["day_write"] = u.get("day_write", 0) + 1


def target_from_state(kind: str, item: dict) -> dict | None:
    """只接受当前 state 里合法对象：评论要 post_id+id，私信要 from_agent。"""
    if not isinstance(item, dict):
        return None
    if kind == "comment":
        if item.get("post_id") is None or item.get("id") is None:
            return None
        return {"post_id": item.get("post_id"), "id": item.get("id")}
    if kind == "dm":
        if not str(item.get("from_agent") or "").strip():
            return None
        return {"from_agent": str(item["from_agent"])}
    return None
