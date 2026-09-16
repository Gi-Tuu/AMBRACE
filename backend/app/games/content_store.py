"""游戏内容源（#62 Phase 3）：用户自定义 > 插件内容包 > 内置常量兜底。

内容 = 引擎可玩素材（词池/词对/题库/安全题模板），与规则判定解耦。三个来源：

1. **内置常量**：各内置引擎在 import 期调用 :func:`register_builtin_content`
   把模块常量登记进来（唯一数据源，不复制第二份）；
2. **插件内容包**：``type=content`` 且 ``manifest.content.kind == "game_content"``
   的声明型扩展（schema 见 ``app.plugins.content_schema``），条目形如
   ``{"game_type": "twenty_q", "key": "word_pool", "values": [...]}``；
   仅已启用的插件生效，加载失败/校验失败的包不会进注册表；
3. **用户自定义**：``game_content_overrides`` 表（user_id + game_type + key），
   由 ``PUT /api/v1/games/content`` 写入；对局开始时 ``GameEngine.load_content(db)``
   把当前用户在该游戏下的覆盖载入引擎实例缓存。

解析顺序：**首个非空来源胜出，整段替换（不做合并）**——用户覆盖了某 key 就完全
以用户内容为准；没有用户内容才看插件内容包；都没有才回落内置常量。任何异常一律
静默回落（内容源绝不阻塞、也不弄崩对局）。

本模块纯读、无副作用；写库只由 API 层显式调用 :func:`upsert_user_override`。
"""
from __future__ import annotations

import re

# 与 registry._GAME_TYPE_RE 同口径（game_type 2-24 位小写字母/数字/下划线）
game_type_re = re.compile(r"^[a-z0-9_]{2,24}$")
content_key_re = re.compile(r"^[a-z0-9_]{2,40}$")

MAX_VALUES = 200          # 单 key 最多条目数（与 content_schema.MAX_ITEMS 对齐）
MAX_TEXT_LEN = 200        # 普通文本条目上限
MAX_OBJECT_TEXT = 300     # 结构化条目内单字段文本上限
MAX_LIST_FIELD = 20       # 结构化条目内列表字段元素数上限

# game_type -> {key: [values]}（内置常量，由引擎 import 期登记）
_BUILTIN: dict[str, dict[str, list]] = {}


def register_builtin_content(game_type: str, key: str, values) -> None:
    """登记内置内容常量（幂等；同 key 重复登记以最后一次为准）。

    只应由内置引擎模块在顶层调用。插件不得调用（插件请走 content 内容包）。
    """
    if not game_type_re.match(str(game_type or "")) or not content_key_re.match(str(key or "")):
        return
    _BUILTIN.setdefault(str(game_type), {})[str(key)] = list(values or [])


def builtin_content(game_type: str, key: str) -> list | None:
    """内置常量（不存在返回 None）。返回副本，调用方可安全改动。"""
    vals = (_BUILTIN.get(str(game_type)) or {}).get(str(key))
    return list(vals) if vals else None


def builtin_content_keys(game_type: str) -> list[str]:
    return sorted((_BUILTIN.get(str(game_type)) or {}).keys())


# ── 插件内容包（只读内存注册表，零 IO）──
def _iter_enabled_content_items():
    """遍历所有已启用插件内容包（kind=game_content）的条目；异常静默空。"""
    try:
        from app.plugins.registry import _enabled, _loaded
    except Exception:
        return
    for name, entry in list(_loaded.items()):
        if not _enabled.get(name, False):
            continue
        content = (entry.get("info") or {}).get("content") or {}
        if content.get("kind") != "game_content":
            continue
        items = content.get("items")
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, dict):
                yield it


def plugin_content(game_type: str, key: str) -> list | None:
    """已启用内容包提供的该 (game_type, key) 内容；无则 None（首个命中胜出）。"""
    try:
        for it in _iter_enabled_content_items():
            if it.get("game_type") == game_type and it.get("key") == key:
                vals = it.get("values")
                if isinstance(vals, list) and vals:
                    return list(vals)
    except Exception:
        return None
    return None


def plugin_content_map(game_type: str) -> dict[str, list]:
    """该游戏下所有插件内容包提供的 {key: values}（首个命中胜出）。"""
    out: dict[str, list] = {}
    try:
        for it in _iter_enabled_content_items():
            if it.get("game_type") != game_type:
                continue
            k = it.get("key")
            vals = it.get("values")
            if isinstance(k, str) and isinstance(vals, list) and vals and k not in out:
                out[k] = list(vals)
    except Exception:
        return out
    return out


def allowed_keys(game_type: str) -> set[str]:
    """该游戏当前可自定义的 key 集合 = 内置 key ∪ 插件内容包 key。"""
    return set(builtin_content_keys(game_type)) | set(plugin_content_map(game_type).keys())


# ── 用户自定义（DB 读写；异常静默，读失败=无覆盖）──
async def load_user_overrides(db, *, user_id: int, game_type: str) -> dict[str, list]:
    """读取某用户在某游戏下的全部自定义内容；表缺失/异常返回 {}。"""
    import json

    from sqlalchemy import select

    from app.models.game import GameContentOverride
    out: dict[str, list] = {}
    try:
        rows = (await db.execute(
            select(GameContentOverride).where(
                GameContentOverride.user_id == int(user_id),
                GameContentOverride.game_type == str(game_type),
            )
        )).scalars().all()
    except Exception:
        return out
    for r in rows:
        try:
            vals = json.loads(r.values_json or "[]")
        except Exception:
            continue
        if isinstance(vals, list) and vals:
            out[r.content_key] = vals
    return out


async def upsert_user_override(db, *, user_id: int, game_type: str, key: str, values: list) -> None:
    """写入/覆盖某用户的某 (game_type, key) 自定义内容（不 commit，由调用方提交）。"""
    import json

    from sqlalchemy import select

    from app.models.game import GameContentOverride
    row = (await db.execute(
        select(GameContentOverride).where(
            GameContentOverride.user_id == int(user_id),
            GameContentOverride.game_type == str(game_type),
            GameContentOverride.content_key == str(key),
        )
    )).scalar_one_or_none()
    payload = json.dumps(list(values), ensure_ascii=False)
    if row is None:
        db.add(GameContentOverride(
            user_id=int(user_id), game_type=str(game_type),
            content_key=str(key), values_json=payload,
        ))
    else:
        row.values_json = payload
    await db.flush()


async def delete_user_override(db, *, user_id: int, game_type: str, key: str) -> bool:
    """删除用户覆盖；返回是否删除了行（不 commit）。"""
    from sqlalchemy import delete

    from app.models.game import GameContentOverride
    res = await db.execute(
        delete(GameContentOverride).where(
            GameContentOverride.user_id == int(user_id),
            GameContentOverride.game_type == str(game_type),
            GameContentOverride.content_key == str(key),
        )
    )
    return bool(getattr(res, "rowcount", 0))


async def list_effective(db, *, user_id: int, game_type: str) -> list[dict]:
    """列出某游戏当前生效内容（按优先级标注 source：user/plugin/builtin）。"""
    overrides = await load_user_overrides(db, user_id=user_id, game_type=game_type)
    plugin = plugin_content_map(game_type)
    keys = sorted(set(builtin_content_keys(game_type)) | set(plugin) | set(overrides))
    items: list[dict] = []
    for k in keys:
        if overrides.get(k):
            source, vals = "user", overrides[k]
        elif plugin.get(k):
            source, vals = "plugin", plugin[k]
        else:
            source, vals = "builtin", builtin_content(game_type, k) or []
        items.append({"key": k, "source": source, "count": len(vals), "values": vals})
    return items


# ── 内容结构校验（内容包 schema 与 API 写入共用）──
def validate_content_values(key: str, values) -> str | None:
    """校验某 key 的 values 结构；合法返回 None，否则错误字符串。

    通用规则：values 为非空数组，条目为 1-200 字字符串，或「一层结构化对象」
    （字段值为 ≤300 字字符串或 ≤20 个短字符串的数组）。两个内置 key 额外收紧：
    - ``word_pairs``：每条必须是 2 个字符串（谁是卧底的词对）；
    - ``puzzles``：每条必须是含 ``surface`` / ``truth`` 的对象，``keywords`` 为字符串数组。
    """
    if not isinstance(values, list) or not values:
        return "values 必须是非空数组"
    if len(values) > MAX_VALUES:
        return f"values 最多 {MAX_VALUES} 条"
    if key == "word_pairs":
        for i, v in enumerate(values):
            if not (isinstance(v, (list, tuple)) and len(v) == 2
                    and all(isinstance(x, str) and x.strip() for x in v)):
                return f"values[{i}] 必须是 2 个非空字符串组成的词对"
            if any(len(x) > MAX_TEXT_LEN for x in v):
                return f"values[{i}] 词对元素需 ≤{MAX_TEXT_LEN} 字"
        return None
    if key == "puzzles":
        for i, v in enumerate(values):
            if not isinstance(v, dict):
                return f"values[{i}] 必须是对象"
            surface = str(v.get("surface") or "").strip()
            truth = str(v.get("truth") or "").strip()
            if not (1 <= len(surface) <= MAX_OBJECT_TEXT) or not (1 <= len(truth) <= MAX_OBJECT_TEXT):
                return f"values[{i}].surface/truth 需 1-{MAX_OBJECT_TEXT} 字"
            kws = v.get("keywords")
            if (not isinstance(kws, list) or not kws or len(kws) > MAX_LIST_FIELD
                    or not all(isinstance(x, str) and 1 <= len(x) <= 50 for x in kws)):
                return f"values[{i}].keywords 需 1-{MAX_LIST_FIELD} 个 ≤50 字的字符串"
        return None
    for i, v in enumerate(values):
        if isinstance(v, str):
            if not (1 <= len(v) <= MAX_TEXT_LEN):
                return f"values[{i}] 文本需 1-{MAX_TEXT_LEN} 字"
        elif isinstance(v, dict):
            err = _validate_object(v, i)
            if err:
                return err
        else:
            return f"values[{i}] 必须是字符串或对象"
    return None


def _validate_object(v: dict, i: int) -> str | None:
    if not v:
        return f"values[{i}] 对象不能为空"
    for fk, fv in v.items():
        if isinstance(fv, str):
            if len(fv) > MAX_OBJECT_TEXT:
                return f"values[{i}].{fk} 文本过长（≤{MAX_OBJECT_TEXT} 字）"
        elif isinstance(fv, list):
            if not (1 <= len(fv) <= MAX_LIST_FIELD):
                return f"values[{i}].{fk} 数组需 1-{MAX_LIST_FIELD} 项"
            if not all(isinstance(x, str) and 1 <= len(x) <= 50 for x in fv):
                return f"values[{i}].{fk} 数组元素需为 ≤50 字的字符串"
        else:
            return f"values[{i}].{fk} 只允许字符串或字符串数组"
    return None


def validate_write(game_type: str, key: str, values) -> str | None:
    """API 写入校验：游戏/ key 必须可用，且 values 结构合法。"""
    if not game_type_re.match(str(game_type or "")):
        return "game_type 非法"
    if not content_key_re.match(str(key or "")):
        return "key 非法（2-40 位小写字母/数字/下划线）"
    keys = allowed_keys(game_type)
    if not keys:
        return f"游戏 {game_type} 暂无可自定义内容"
    if key not in keys:
        return f"key 不适用：可选 {sorted(keys)}"
    return validate_content_values(key, values)
