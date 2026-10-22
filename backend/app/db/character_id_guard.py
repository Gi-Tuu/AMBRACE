# -*- coding: utf-8 -*-
"""B2c：角色 ID 客户端安全上限（2^53-1）的数据库守卫与安装/核验。

设计取舍：
- 新库 create_all / 旧库候选重建在表 DDL 上带 CHECK 约束（由模型/迁移提供）；
- 已存在 AUTOINCREMENT 但无 CHECK 的库，用 AFTER INSERT/AFTER UPDATE 触发器补守卫；
  BEFORE INSERT 时 NEW.id 对自动分配不可靠，不能作为唯一防线（主审已实证）。
- verify 只读，不向业务库插入探测行；行为验证在临时库进行。
- 只识别本模块生成的规范 CHECK/触发器定义；同名但残缺/无 ABORT/错事件/错阈值/无效 WHEN
  的对象不被当成有效守卫，由 ensure 修复或最终拒绝。不触碰其它业务触发器。
- helper 不清理、不改型、不重绑已有数据；已有超界数据先拒绝。
"""
import re
import sqlite3

CLIENT_SAFE_MAX_ID = (1 << 53) - 1
CHECK_NAME = "ck_ai_characters_client_safe_id"
INSERT_TRIGGER = "ai_characters_client_safe_insert_after"
UPDATE_TRIGGER = "ai_characters_client_safe_update_after"
# B2b 交付过的 BEFORE INSERT/UPDATE 提案名；若曾被手工安装，先移除，避免半守卫。
LEGACY_TRIGGERS = (
    "ai_characters_client_safe_insert",
    "ai_characters_client_safe_update_id",
)
TABLE = "ai_characters"
CAPACITY_CODE = "character_id_capacity_exhausted"


class CharacterIdCapacityExceeded(Exception):
    """数据库守卫拒绝了客户端不可精确表示的 id；由 API 映射为 409。"""


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _sql_tokens(sql: str) -> list[tuple[str, str]]:
    """Keep literals and quoted identifiers distinct when matching owned SQL forms."""
    pattern = re.compile(
        r"""'(?:''|[^'])*'|"(?:""|[^"])*"|`(?:``|[^`])*`|\[[^\]]*\]|--[^\n]*|/\*.*?\*/|[A-Za-z_][A-Za-z_0-9]*|[0-9]+|<=|[^\s]""",
        re.S,
    )
    tokens = []
    for match in pattern.finditer(sql or ""):
        token = match.group()
        if token.startswith(("--", "/*")):
            continue
        if token.startswith("'"):
            tokens.append(("string", token[1:-1].replace("''", "'")))
        elif token[0] in ('"', '`', '['):
            quote = token[0]
            value = token[1:-1]
            if quote != '[':
                value = value.replace(quote * 2, quote)
            tokens.append(("quoted", value.lower()))
        elif token[0].isalpha() or token[0] == '_':
            tokens.append(("word", token.lower()))
        elif token.isdigit():
            tokens.append(("number", token))
        else:
            tokens.append(("symbol", token))
    return tokens


def _table_ddl(conn) -> str | None:
    row = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
    ).fetchone()
    return row[0] if row else None


def _trigger_sqls(conn) -> dict[str, str]:
    return {
        row[0]: (row[1] or "")
        for row in conn.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
            (TABLE,),
        ).fetchall()
    }


def _check_is_canonical(ddl: str) -> bool:
    tokens = _sql_tokens(ddl)
    for i in range(len(tokens) - 7):
        part = tokens[i:i + 8]
        if (
            part[0] == ("word", "constraint")
            and part[1][0] in ("word", "quoted") and part[1][1] == CHECK_NAME.lower()
            and part[2] == ("word", "check") and part[3] == ("symbol", "(")
            and part[4][0] in ("word", "quoted") and part[4][1] == "id"
            and part[5] == ("symbol", "<=")
            and part[6] == ("number", str(CLIENT_SAFE_MAX_ID))
            and part[7] == ("symbol", ")")
        ):
            return True
    return False


def _trigger_expected(name: str, event: str) -> str:
    return (
        f'CREATE TRIGGER {_q(name)} AFTER {event} ON {_q(TABLE)} FOR EACH ROW '
        f"WHEN NEW.id > {CLIENT_SAFE_MAX_ID} "
        f"BEGIN SELECT RAISE(ABORT, '{CAPACITY_CODE}'); END"
    )


def _trigger_is_canonical(sql: str, name: str, event: str) -> bool:
    return bool(sql) and _sql_tokens(sql) == _sql_tokens(_trigger_expected(name, event))


def verify_client_safe_id_guard(conn) -> dict:
    """只读核验：present 仅由规范 CHECK 或两条规范触发器成立。"""
    ddl = _table_ddl(conn)
    if ddl is None:
        return {"present": False, "mode": "absent", "check": False,
                "triggers": [], "invalid_triggers": []}
    check = _check_is_canonical(ddl)
    triggers = _trigger_sqls(conn)
    valid: list[str] = []
    invalid: list[str] = []
    for name, event in ((INSERT_TRIGGER, "INSERT"), (UPDATE_TRIGGER, "UPDATE OF id")):
        if name not in triggers:
            continue
        if _trigger_is_canonical(triggers[name], name, event):
            valid.append(name)
        else:
            invalid.append(name)
    both = len(valid) == 2
    if invalid:
        # 同名受控对象存在但不规范：不得以 CHECK 有效为由把它算作有效守卫。
        mode = "invalid"
        present = False
    elif check and both:
        mode = "check+triggers"
        present = True
    elif check:
        mode = "check"
        present = True
    elif both:
        mode = "triggers"
        present = True
    else:
        mode = "none"
        present = False
    return {"present": present, "mode": mode, "check": check,
            "triggers": valid, "invalid_triggers": invalid}


def _create_trigger(conn, name: str, event: str) -> None:
    conn.exec_driver_sql(
        f'CREATE TRIGGER {_q(name)} AFTER {event} ON {_q(TABLE)} FOR EACH ROW '
        f"WHEN NEW.id > {CLIENT_SAFE_MAX_ID} "
        f"BEGIN SELECT RAISE(ABORT, '{CAPACITY_CODE}'); END"
    )


def _ensure_after_trigger(conn, name: str, event: str) -> None:
    existing = _trigger_sqls(conn).get(name)
    if existing is not None and _trigger_is_canonical(existing, name, event):
        return
    if existing is not None:
        conn.exec_driver_sql(f"DROP TRIGGER {_q(name)}")
    _create_trigger(conn, name, event)


def ensure_client_safe_id_guard(conn) -> dict:
    """安装/核验守卫；幂等。已有超界 id/引用/sequence 时在写前拒绝。"""
    ddl = _table_ddl(conn)
    if ddl is None:
        return verify_client_safe_id_guard(conn)

    # 延迟 import：migrate 在模块顶部 import 本模块的常量，避免顶层循环。
    from app.db.migrate import scan_character_id_watermark

    scan = scan_character_id_watermark(conn)
    if int(scan["high"]) > CLIENT_SAFE_MAX_ID:
        raise RuntimeError(
            f"B2c guard refused: existing high={scan['high']} exceeds client-safe "
            f"max={CLIENT_SAFE_MAX_ID}; data governance required before install"
        )

    for legacy in LEGACY_TRIGGERS:
        conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS {_q(legacy)}")

    check = _check_is_canonical(ddl)
    triggers = _trigger_sqls(conn)
    # 同名受控对象但不规范：删除后按需重建，绝不保留为“有效守卫”。
    for name, event in ((INSERT_TRIGGER, "INSERT"), (UPDATE_TRIGGER, "UPDATE OF id")):
        sql = triggers.get(name)
        if sql is not None and not _trigger_is_canonical(sql, name, event):
            conn.exec_driver_sql(f"DROP TRIGGER {_q(name)}")

    if not check:
        _ensure_after_trigger(conn, INSERT_TRIGGER, "INSERT")
        _ensure_after_trigger(conn, UPDATE_TRIGGER, "UPDATE OF id")

    result = verify_client_safe_id_guard(conn)
    if not result["present"]:
        raise RuntimeError(
            "B2c guard refused: could not install/verify a canonical CHECK or AFTER triggers"
        )
    return result


def _driver_error_text(exc: Exception) -> str:
    """只取底层驱动错误文本；绝不读取 SQLAlchemy 的 statement/params。"""
    orig = getattr(exc, "orig", None)
    if isinstance(orig, sqlite3.IntegrityError):
        return str(orig)
    if isinstance(exc, sqlite3.IntegrityError):
        return str(exc)
    return ""


def is_capacity_error(exc: Exception) -> bool:
    """只识别本守卫底层驱动的明确信号；参数里的同名标记不算。"""
    text = _driver_error_text(exc).lower()
    if not text:
        return False
    return (
        CAPACITY_CODE in text
        or f"check constraint failed: {CHECK_NAME.lower()}" in text
    )
