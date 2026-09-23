"""phone sections（步骤5）：手机感知 / 小手机 → 模板槽「phone_perception」「phone_desktop」。

- phone_perception：**X7-M1 起改为结构化消费**（派单 P7/P8 A 段）。取数收敛在
  ``device.port.read_perception_records``（窗口/去重口径与旧渲染一致），本段只做渲染：
  拿到 ``structured``（客户端 ``payload_json`` 字段级载荷）的记录**按字段逐项渲染**，
  拿不到载荷的记录**逐字回落到迁移前的文本行** ``[来源标签 N分钟前] 正文``。
  ⚠️ **M1 起本段文本形态可与旧版不同**——差异只允许出现在「带结构化载荷」的那些行；
  无载荷 / 空数据必须与旧版逐字一致（回归用例见 ``tests/test_device_capabilities.py``、
  ``tests/test_phone_perception_payload.py``）。渲染不含任何随机因子，且时间差按调用方传入的
  ``now`` 计算 → 同一份数据必得同一份文案（字段名升序、值截断上限固定）。
- phone_desktop：角色日历备注 + 浏览器最近搜索（仅文本注入；失败静默「无」）。

避免 agent → services 硬 import：builder 内部惰性引入 port / phone_desktop_service
（与 section_mcp 引用 permission_service 同模式）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from app.agent.context.sections import ContextSection, register_section, TARGET_TEMPLATE
from app.utils.timeutil import now_naive_utc

if TYPE_CHECKING:  # 仅类型标注：port 仍按原有约定在函数内惰性引入（避免 agent → services 硬 import）
    from app.device.port import PerceptionRecord

_logger = logging.getLogger("agent.context.section_phone")

_MAX_CHARS = 500          # 注入配额（与旧渲染一致）
_MAX_FIELD_VALUE = 200    # 单个字段值上限，防止一条长文挤掉其余字段
_FIELD_SEP = " | "

# 来源标签：与 application.phone_service 旧渲染逐字同口径（未登记来源直接显示 source 原值）
_SOURCE_LABEL = {
    "accessibility": "屏幕",
    "clipboard": "剪贴板",
    "media": "相册",
    "media_video": "视频",
    "media_audio": "音频",
    "media_document": "文件",
    "notification": "通知",
    "action_result": "操作结果",
}


def _text_body(record: PerceptionRecord) -> str:
    """旧口径正文：``content`` ＋（有图时）``[图片] image_desc``，用「；」拼接。"""
    parts = []
    if record.raw_text:
        parts.append(record.raw_text)
    if record.image_desc:
        parts.append(f"[图片] {record.image_desc}")
    return "；".join(parts)


def _field_text(structured: dict) -> str:
    """字段级渲染：``k=v`` 按字段名升序（客户端 JSON 的键序不参与，保证确定性）。

    空值（``None`` / 空串）跳过；字符串原样、布尔写 ``true``/``false``、数字按 JSON 字面量；
    容器（对象/数组）走紧凑 JSON 且内部键名也升序。每个值一律截断到 :data:`_MAX_FIELD_VALUE`。
    """
    parts = []
    for key in sorted(structured):
        value = structured[key]
        if value is None or value == "":
            continue
        if isinstance(value, str):
            text = value
        elif isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, (int, float)):
            text = json.dumps(value)
        else:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        parts.append(f"{key}={text[:_MAX_FIELD_VALUE]}")
    return _FIELD_SEP.join(parts)


def _render_record(record: PerceptionRecord, now: datetime) -> str:
    """单条记录 → 一行文案（正文为空则返回空串，由调用方丢弃）。"""
    minutes_ago = max(0, int((now - record.observed_at).total_seconds() // 60))
    head = f"[{_SOURCE_LABEL.get(record.source, record.source)} {minutes_ago}分钟前]"
    fields = _field_text(record.structured) if record.structured else ""
    if not fields:
        body = _text_body(record)
        return f"{head} {body}" if body else ""
    parts = [fields]
    # 载荷已含 OCR 原文时不再重复（同一句占两遍配额）；图片描述始终补一行
    if record.raw_text and record.raw_text not in record.structured.values():
        parts.append(f"原文={record.raw_text[:_MAX_FIELD_VALUE]}")
    if record.image_desc:
        parts.append(f"[图片] {record.image_desc[:_MAX_FIELD_VALUE]}")
    return f"{head} {_FIELD_SEP.join(parts)}"


def render_perception_records(records: list[PerceptionRecord], now: datetime) -> str:
    """感知记录清单 → 注入文案（最新在前，换行拼接，总长截断到 :data:`_MAX_CHARS`）。"""
    lines = [line for line in (_render_record(r, now) for r in records) if line]
    return "\n".join(lines)[:_MAX_CHARS]


async def phone_perception_section(state: dict, ctx: dict) -> str:
    """phone_perception 分区：手机感知（template 槽；无则缺省「无」）。"""
    from app.device.port import read_perception_records

    phone_perception = "无"
    try:
        records = await read_perception_records(state.get("user_id"))
        text = render_perception_records(records, now_naive_utc())
        if text:
            phone_perception = text
    except Exception as e:
        _logger.warning("Failed to load phone perception: %s", e)
    return phone_perception


async def phone_desktop_section(state: dict, ctx: dict) -> str:
    """phone_desktop 分区：小手机（角色日历备注 + 浏览器最近搜索）（template 槽；无则缺省「无」）。"""
    from app.application.phone_desktop_service import get_phone_desktop_inject_text

    phone_desktop = "无"
    try:
        _cid = state.get("character_id")
        if _cid:
            _pdt = await get_phone_desktop_inject_text(int(_cid))
            if _pdt:
                phone_desktop = _pdt
    except Exception as e:
        _logger.warning("Phone desktop inject failed: %s", e)
    return phone_desktop


register_section(ContextSection(
    key="phone_perception",
    builder=phone_perception_section,
    target=TARGET_TEMPLATE,
    slot="phone_perception",
    quota_tokens=400,
    order=61,
))
register_section(ContextSection(
    key="phone_desktop",
    builder=phone_desktop_section,
    target=TARGET_TEMPLATE,
    slot="phone_desktop",
    quota_tokens=400,
    order=62,
))
