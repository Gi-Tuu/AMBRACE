"""内容包（content_pack）schema 校验（X2，2026-08-31）。

内容包 = type="content" 的声明型扩展（**零代码、最高安全级**），manifest 携带：
    "content": {"kind": "<种类>", "items": [ ... ]}

首批 kind：
- holiday_fixed：固定日期节日补充（items: {date: "MM-DD", name, lang?}）——
  经 app.scheduling.holiday_calendar.get_holidays 合并生效（真实 loader）；
- opening_lines：角色开场白/剧情模板片段（items: {text}）——schema 先行，
  消费端（人设装配）后续批次接入。

校验失败返回错误字符串（安装被拒），合法返回 None。纯函数、零 IO。
"""
from __future__ import annotations

import re

DATE_RE = re.compile(r"^\d{2}-\d{2}$")

MAX_ITEMS = 200


def _err(msg: str) -> str:
    return f"content: {msg}"


def _check_common(items) -> str | None:
    if not isinstance(items, list) or not items:
        return _err("items 必须是非空数组")
    if len(items) > MAX_ITEMS:
        return _err(f"items 最多 {MAX_ITEMS} 条")
    return None


def _validate_holiday_fixed(items) -> str | None:
    bad = _check_common(items)
    if bad:
        return bad
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            return _err(f"items[{i}] 必须是对象")
        date = str(it.get("date") or "")
        if not DATE_RE.match(date):
            return _err(f"items[{i}].date 需为 MM-DD 格式")
        try:
            mm, dd = int(date[:2]), int(date[3:])
            if not (1 <= mm <= 12 and 1 <= dd <= 31):
                raise ValueError
        except ValueError:
            return _err(f"items[{i}].date 月份/日期越界: {date}")
        name = str(it.get("name") or "").strip()
        if not (1 <= len(name) <= 32):
            return _err(f"items[{i}].name 需 1-32 字符")
        lang = it.get("lang", "zh")
        if lang not in ("zh", "en"):
            return _err(f"items[{i}].lang 只允许 zh/en")
    return None


def _validate_opening_lines(items) -> str | None:
    bad = _check_common(items)
    if bad:
        return bad
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            return _err(f"items[{i}] 必须是对象")
        text = str(it.get("text") or "").strip()
        if not (2 <= len(text) <= 200):
            return _err(f"items[{i}].text 需 2-200 字符")
    return None


_VALIDATORS = {
    "holiday_fixed": _validate_holiday_fixed,
    "opening_lines": _validate_opening_lines,
}

CONTENT_KINDS = tuple(_VALIDATORS.keys())


def validate_content_payload(payload) -> str | None:
    """校验 manifest.content 块；合法返回 None，否则错误信息。"""
    if not isinstance(payload, dict):
        return _err("必须是对象 {kind, items}")
    kind = payload.get("kind")
    if kind not in CONTENT_KINDS:
        return _err(f"kind 必须是 {CONTENT_KINDS}")
    return _VALIDATORS[kind](payload.get("items"))
