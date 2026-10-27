# -*- coding: utf-8 -*-
"""RC2 G2：记忆派生关系只读服务。

只读展示一条记忆的**直接**派生边（不递归、不写回、不自动推导）：
- ``parents``：当前行 ``derived_from_ids``（Text JSON）里记录的直接来源；
- ``children``：授权同角色记忆中，顶层 JSON 数组精确包含当前 id 的行。

边界（详见 delivery/PROTOCOL.md）：
- 只发 ``select``，不 commit/flush，不碰向量/索引/回执/flags；
- 两方向条目都必须同时满足 ``tenant_scope_ids`` 与当前记忆 ``character_id``；
- **证据规则两方向一致**：只认「顶层 JSON 数组 + 正整数成员」；``"1"``、``{"x":1}``、
  ``{"ids":1}``、空对象、标量等一律不是证据（坏数据 → ``data_incomplete``，不猜合法 ID）；
- 正整数按 SQLite 有符号 64 位范围保守校验：超出范围/非 ASCII 十进制游标一律 4xx；
  存量脏数据里的大数不参与绑定（JSON1 会视为 real/非整数），不得击穿 SQL；
- 反向先按**唯一记忆实体**去重，再 ``limit+1`` 分页（同一行 [1,1,1] 只算一条边）；
- 正向先做**权限过滤**再分页（不得先截前 N 条把后面的合法来源永久吞掉），分块查询避免绑定上限；
- 非法/缺失/旧格式 ``derived_from_ids``、非整数成员、引用不可见/悬空 ⇒ ``data_incomplete``，
  与「合法空数组＝确认无关联」严格区分；不回传坏 ID/隐藏计数。
"""
from __future__ import annotations

import json

from sqlalchemy import bindparam, select, text

from app.models.memory import Memory

DERIVATION_PAGE_DEFAULT = 20
DERIVATION_PAGE_MAX = 50
SQLITE_MAX_INT = 2**63 - 1
_ID_QUERY_CHUNK = 400


def parse_cursor(raw: str | None, name: str = "cursor") -> int:
    """游标只接受 ASCII 十进制正整数且 ≤ SQLite int64；其余抛 ValueError（调用方转 4xx）。"""
    if raw is None or raw == "":
        return 0
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdigit():
        raise ValueError(f"invalid_{name}")
    if len(raw) > 19:
        raise ValueError(f"invalid_{name}")
    value = int(raw)
    if value <= 0 or value > SQLITE_MAX_INT:
        raise ValueError(f"invalid_{name}")
    return value


def parse_forward_ids(raw: str | None) -> tuple[list[int], bool]:
    """解析当前行 ``derived_from_ids`` → (正整数 id 列表[去重、保序], 是否不完整)。

    不完整口径（保守，且与反向证据规则一致）：
    - ``None``/非字符串/空串/非法 JSON/**顶层非数组**；
    - 非法成员：bool、字符串、浮点、嵌套、0、负数、超出 SQLite int64 的大整数。
    合法空数组 → ``([], False)``（确认无关联）。**不截断前 N 条**：可见性过滤后才分页。
    """
    if raw is None or not isinstance(raw, str):
        return [], True
    if raw.strip() == "":
        return [], True
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return [], True
    if not isinstance(data, list):
        return [], True
    out: list[int] = []
    seen: set[int] = set()
    bad = False
    for value in data:
        if isinstance(value, bool) or not isinstance(value, int):
            bad = True
            continue
        if value <= 0 or value > SQLITE_MAX_INT:
            bad = True
            continue
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out, bad


def _item_from_row(row: Memory) -> dict:
    return {
        "id": int(row.id),
        "content": row.content or "",
        "title": row.title,
        "memory_type": row.memory_type,
        "status": getattr(row, "status", None) or "active",
        "is_archived": bool(row.is_archived),
    }


def _page_ids(ids: list[int], cursor: int, limit: int) -> tuple[list[int], bool, str | None]:
    rest = [i for i in ids if i > cursor]
    page = rest[:limit]
    has_more = len(rest) > limit
    next_cursor = str(page[-1]) if has_more and page else None
    return page, has_more, next_cursor


async def _visible_memories(db, ids: list[int], char_id: int,
                            scope_ids: list[int]) -> dict[int, Memory]:
    """按 id 分块查询可见行（避免 SQL 变量上限；不改变权限语义）。"""
    found: dict[int, Memory] = {}
    for start in range(0, len(ids), _ID_QUERY_CHUNK):
        chunk = ids[start:start + _ID_QUERY_CHUNK]
        rows = (await db.execute(
            select(Memory).where(
                Memory.id.in_(chunk),
                Memory.character_id == char_id,
                Memory.user_id.in_(scope_ids),
            )
        )).scalars().all()
        for row in rows:
            found[int(row.id)] = row
    return found


async def _fetch_parents(db, memory: Memory, scope_ids: list[int], *, cursor: int,
                         limit: int) -> dict:
    ids, incomplete = parse_forward_ids(memory.derived_from_ids)
    ids = [i for i in ids if i != int(memory.id)]  # 去自环
    if not ids:
        return {"items": [], "has_more": False, "next_cursor": None,
                "data_incomplete": incomplete}
    # R4：先权限过滤（全量、分块），再做可见分页；不得先截断再过滤。
    visible = await _visible_memories(db, ids, int(memory.character_id), scope_ids)
    if len(visible) < len(ids):
        incomplete = True  # 解析到但不可见/悬空：通用不完整，不回传 ID/数量
    visible_ids = sorted(visible)
    page, has_more, next_cursor = _page_ids(visible_ids, cursor, limit)
    return {
        "items": [_item_from_row(visible[i]) for i in page],
        "has_more": has_more,
        "next_cursor": next_cursor,
        "data_incomplete": incomplete,
    }


_ARRAY_JSON = ("CASE WHEN json_valid(m.derived_from_ids) "
               "AND json_type(m.derived_from_ids) = 'array' "
               "THEN m.derived_from_ids ELSE '[]' END")
_ARRAY_JSON_M2 = _ARRAY_JSON.replace("m.derived_from_ids", "m2.derived_from_ids")

# R2：先对唯一记忆实体去重，再 keyset 分页；R1：json_type='array' 限制顶层数组，
# json_each 只在该数组上展开；je.type='integer' 保证字符串/浮点/布尔不误中。
_CHILDREN_SQL = text(
    f"""
    SELECT m.id, m.content, m.title, m.memory_type, m.status, m.is_archived
    FROM memories AS m
    WHERE m.character_id = :char_id
      AND m.user_id IN :scope
      AND m.id != :mid
      AND m.id > :cursor
      AND m.id IN (
        SELECT DISTINCT m2.id
        FROM memories AS m2,
             json_each({_ARRAY_JSON_M2}) AS je
        WHERE m2.character_id = :char_id
          AND m2.user_id IN :scope
          AND je.type = 'integer'
          AND je.value = :mid
      )
    ORDER BY m.id
    LIMIT :limit
    """
).bindparams(bindparam("scope", expanding=True))


_BAD_ROW_SQL = text(
    """
    SELECT COUNT(*) FROM memories AS m
    WHERE m.character_id = :char_id
      AND m.user_id IN :scope
      AND (m.derived_from_ids IS NULL
           OR trim(m.derived_from_ids) = ''
           OR NOT json_valid(m.derived_from_ids)
           OR (json_valid(m.derived_from_ids)
               AND json_type(m.derived_from_ids) != 'array'))
    """
).bindparams(bindparam("scope", expanding=True))


_BAD_MEMBER_SQL = text(
    f"""
    SELECT COUNT(DISTINCT m.id) FROM memories AS m,
           json_each({_ARRAY_JSON}) AS je
    WHERE m.character_id = :char_id
      AND m.user_id IN :scope
      AND (je.type != 'integer'
           -- 与正向 parse_forward_ids 同口径：0/负数、以及 JSON1 标称 integer
           -- 但实际落成 REAL 的超 int64 字面（1208925819614629174706176 → real）
           OR typeof(je.value) != 'integer'
           OR je.value <= 0
           OR je.value > 9223372036854775807)
    """
).bindparams(bindparam("scope", expanding=True))


_BAD_CHAR_SQL = text(
    f"""
    SELECT COUNT(DISTINCT m.id) FROM memories AS m,
           json_each({_ARRAY_JSON}) AS je
    WHERE m.user_id IN :scope
      AND m.character_id != :char_id
      AND je.type = 'integer'
      AND je.value = :mid
    """
).bindparams(bindparam("scope", expanding=True))


async def _fetch_children(db, memory: Memory, scope_ids: list[int], *, cursor: int,
                          limit: int) -> dict:
    params = {"char_id": int(memory.character_id), "scope": list(scope_ids),
              "mid": int(memory.id), "cursor": max(int(cursor), 0), "limit": limit + 1}
    rows = (await db.execute(_CHILDREN_SQL, params)).mappings().all()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = str(page[-1]["id"]) if has_more and page else None
    bad_rows = int((await db.execute(_BAD_ROW_SQL, params)).scalar() or 0)
    bad_members = int((await db.execute(_BAD_MEMBER_SQL, params)).scalar() or 0)
    # 同租户但不同角色的引用：不返回、不泄露，但该边存在 ⇒ 通用不完整
    bad_char = int((await db.execute(_BAD_CHAR_SQL, params)).scalar() or 0)
    return {
        "items": [{
            "id": int(r["id"]),
            "content": r["content"] or "",
            "title": r["title"],
            "memory_type": r["memory_type"],
            "status": r["status"] or "active",
            "is_archived": bool(r["is_archived"]),
        } for r in page],
        "has_more": has_more,
        "next_cursor": next_cursor,
        "data_incomplete": bool(bad_rows or bad_members or bad_char),
    }


async def fetch_derivations(db, memory: Memory, scope_ids: list[int], *, limit: int,
                            parents_cursor: int = 0, children_cursor: int = 0) -> dict:
    """两方向独立分页；返回可直接序列化为响应模型的 dict。"""
    parents = await _fetch_parents(db, memory, scope_ids, cursor=parents_cursor, limit=limit)
    children = await _fetch_children(db, memory, scope_ids, cursor=children_cursor, limit=limit)
    return {"parents": parents, "children": children}
