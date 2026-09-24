# -*- coding: utf-8 -*-
"""删号 cascade 清单的**库结构自动发现**（控制台删号·第一期地基，2026-09-24 派单第 1 批）。

为什么自动发现而不手写表清单
----------------------------
方案 v2 §二.4 的教训：手写清单**一定漏**。三个结构性原因——
① 归属列名不统一（8 个名字表达同一件事：谁的数据）；
② **插件表不在主 ``Base.metadata``**（``douyin_*.tenant_id`` 只有插件装载后模型才注册，
   未启用的部署连表都没有）→ 只看 ORM 模型会漏；
③ 以后新加的表不会有人回头改这份清单。

所以本模块一律扫**实际库结构**（``sqlite_master`` + ``PRAGMA table_info``
+ ``PRAGMA foreign_key_list``），不碰 ``Base.metadata``。

两族候选（v2 §3.2 修订 4：只扫用户族会漏 24 张表）
--------------------------------------------------
- **用户族**：命中 8 个归属列之一（:data:`USER_FAMILY_COLUMNS`）；
- **角色族**：含 ``character_id`` 列，**或**有物理外键指向 ``ai_characters``
  （覆盖 ``character_a_id`` / ``character_b_id`` 这类改名列）。
  角色族按「第 0 步」固化的**该账号名下角色 id 集合**过滤——``ai_characters`` 行一旦删掉，
  这些 id 永久不可反查，故计划里直接回包 ``character_ids``。

例外分两类（语义不同，代码里分开表达）
--------------------------------------
① :data:`RETAINED_TABLES` = **永久保留**（append-only 合规记录，删了审计就读不懂了）；
② :data:`FAMILY_ROOT_TABLES` / :data:`FAMILY_ROOT_COLUMNS` = **家庭根归属**：删**子账号**时不动
   （行属于整户，不属于这个成员）；删**家庭根**时纳入候选（整户一起走）。
   故例外是否生效取决于 ``scope``。

列语义也不是一句「等于 user_id 就删」
------------------------------------
- ``speaker_id`` **双语义**（值可能是 user_id 也可能是 character_id，而 users.id 与
  ai_characters.id 都是自增整数、值域重叠）→ 先比对「目标用户 id」「该账号角色 id 集合」
  判定归属，判定不出的行数单独计数（``undetermined_speaker_rows``），**不删**。
  另外它只在「本表没有更硬的归属列」时才当删除依据：实测 memories / ai_chats /
  group_memories 三张带 ``speaker_id`` 的表都另有 ``user_id``——那一行属于**行主人**，
  ``speaker_id`` 只是「谁在这行里说过话」，拿它删会越界删别人的行（故只报行数、不进 WHERE）。
- ``updated_by`` 是**最后编辑者标记**，不是归属：按它删会把「别人被这个账号改过的配置」一起
  带走（最坏案例 ``llm_usage_limits``——服务器默认额度只有 id=1 一行，管理员改过就永久记着
  他的 id，删该管理员等于清空全局额度配置）。故只登记行数、不作为删除依据。

纪律：本模块**纯读、零写入**；识别不了的情况抛 :class:`UserCascadeError`（可读错误），
不静默跳过——静默跳过正是「漏表」的形态。
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import bindparam, text

# ── 扫描范围 ──────────────────────────────────────────────────────────────────

#: 用户族归属列（v2 §3.2 口径，8 个）。顺序即展示顺序，勿随意调整（测试与公告都按它比对）。
USER_FAMILY_COLUMNS: tuple[str, ...] = (
    "user_id",
    "owner_user_id",
    "actor_user_id",
    "tenant_id",
    "group_owner_id",
    "creator_id",
    "speaker_id",
    "updated_by",
)

#: 角色族列名（另外「物理外键指向 :data:`CHARACTER_ROOT_TABLE` 的本地列」也算，见 `_scan_schema`）。
CHARACTER_FAMILY_COLUMN = "character_id"
#: 角色族的根表（该账号名下的角色集合 = 角色族要删的行集合）。
CHARACTER_ROOT_TABLE = "ai_characters"

SCOPE_DELETE_SUB_ACCOUNT = "delete_sub_account"
SCOPE_DELETE_FAMILY_ROOT = "delete_family_root"
SCOPES: tuple[str, ...] = (SCOPE_DELETE_SUB_ACCOUNT, SCOPE_DELETE_FAMILY_ROOT)

# ── 例外清单 ──────────────────────────────────────────────────────────────────

#: ① 永久保留（append-only 合规）：任何 scope 都不删。
#: ``domain_events`` 用的是 ``actor_id``（不在 8 列里，天然不会命中），仍登记一次：
#: 将来谁给它补一个 ``user_id`` 列，本清单保证它继续被保留。
RETAINED_TABLES: dict[str, str] = {
    "admin_audit_log": "永久保留：控制台审计是 append-only 合规记录（actor_user_id 允许悬空，"
                       "删号时把用户名与体量快照写进 detail，行本身不删）",
    "domain_events": "永久保留：领域事件是 append-only 合规记录（actor_id/actor_type 允许悬空）",
    "account_purge_jobs": "永久保留：本表是物理清除器自己的进度账本（有 user_id 列会被 cascade 命中，"
                          "但删了它 = 清除跑到一半把账本抹掉，既无法续跑也查不到删到哪了）",
}

#: ② 家庭根归属（整户共享）：``scope=delete_sub_account`` 时不动，``delete_family_root`` 时纳入候选。
FAMILY_ROOT_TABLES: dict[str, str] = {
    "chat_groups": "家庭根归属：家庭群聊属于整户，删子账号只应移除其成员行",
    "shared_events": "家庭根归属：跨账号共享事件（整户可见）",
    "plugin_consents": "家庭根归属：插件权限按租户授权，删子账号不该收回整户授权",
    "channel_bindings": "家庭根归属：渠道（微信/抖音等）绑定挂在家庭根上",
    "device_action_targets": "家庭根归属：行动目标白名单按 tenant（家庭根）配置",
}

#: ② 的列级形态（整张表不属家庭根，只有这一列是家庭根归属）。
FAMILY_ROOT_COLUMNS: dict[tuple[str, str], str] = {
    ("llm_usage", "group_owner_id"):
        "列级例外：家庭根归属（#68 P6 组聚合归因列），删子账号不该带走整户的用量归因",
}

#: 「最后编辑者」标记列——命中即登记行数，但**不作为删除依据**（见模块 docstring）。
EDITOR_ONLY_COLUMNS = frozenset({"updated_by"})
#: 双语义列（值可能是 user_id 也可能是 character_id）——删前必须比对判定归属。
DUAL_SEMANTIC_COLUMNS = frozenset({"speaker_id"})

KIND_OWNERSHIP = "ownership"
KIND_DUAL = "dual_semantic"
KIND_EDITOR = "editor_only"

#: 扩展哨兵用正则：列名长得像「谁的数据」的写法（含 8 列之外的改名风险）。
_OWNERSHIP_NAME_RE = re.compile(
    r"(^|_)(user|owner|tenant|actor|creator|speaker|member|account)(_|$)|user$|_uid$",
    re.I,
)
#: 已知「名字像归属列但不是本地 user/character 归属」的列（(表, 列) → 为什么不是）。
#: :func:`unknown_ownership_columns` 抓到新列时，要么把它并进 :data:`USER_FAMILY_COLUMNS`，
#: 要么在这里写清理由——不允许默默漏掉（这就是测试锁 ① 的非空洞部分）。
NON_OWNERSHIP_COLUMNS: dict[tuple[str, str], str] = {
    ("domain_events", "actor_id"): "动作发起者（user/character/system 两义），本表已登记永久保留",
    ("domain_events", "actor_type"): "枚举文本（user/character/system），不是 id",
    ("users", "username"): "账号自身的标识列（删号删的是整行，不按它匹配他人）",
    ("users", "user_location"): "文本字段（用户自定义位置名），不是 id",
    ("chat_group_messages", "notify_user"): "整型 0/1 开关（这条消息是否通知用户），不是 user id",
    ("game_events", "actor_seat"): "游戏内座位号，不是用户 id",
    ("life_goals", "related_user"): "布尔（是否与用户相关），不是 user id",
    ("processed_extractions", "user_message_id"): "指向消息行的 id，不是账号归属",
    ("scheduled_events", "owner"): "枚举文本（ai=AI 承诺 / user=用户承诺），不是 id",
    ("social_memories", "external_user_key"): "外部平台用户标识（字符串 key），与本地 user_id 无关",
    ("storyline_events", "user_context"): "文本（用户当时行为摘要），不是 id",
}


# ── 错误 ──────────────────────────────────────────────────────────────────────

class UserCascadeError(RuntimeError):
    """库结构识别不了 / 入参非法 → 显式抛错。

    刻意不「跳过继续」：静默跳过就是漏表的表现形态。
    """


# ── 低层扫描（PRAGMA 不吃绑定参数 → 标识符必须先过白名单）────────────────────

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _q(ident: str) -> str:
    """标识符包成双引号形式；不合法直接抛错（表名来自 sqlite_master，异常值＝库不正常）。"""
    if not isinstance(ident, str) or not _IDENT_RE.match(ident):
        raise UserCascadeError(f"库中出现无法识别的标识符，拒绝拼接 SQL: {ident!r}")
    return f'"{ident}"'


async def _fetchall(conn, stmt, params: dict[str, Any] | None = None):
    return (await conn.execute(stmt, params or {})).fetchall()


async def _table_names(conn) -> list[str]:
    rows = await _fetchall(conn, text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ))
    names = [r[0] for r in rows]
    if not names:
        raise UserCascadeError("库里没有任何表——cascade 发现无从下手（确认连的是哪个库）")
    return names


async def _scan_schema(conn) -> dict[str, dict[str, Any]]:
    """逐表 ``PRAGMA table_info`` / ``foreign_key_list`` → {表: {columns, user_hits, character_hits}}。

    插件表（``douyin_*`` 等）与主表走同一条路径：判据是**实际库结构**，与模型是否装载无关。
    """
    schema: dict[str, dict[str, Any]] = {}
    for table in await _table_names(conn):
        cols = [r[1] for r in await _fetchall(conn, text(f"PRAGMA table_info({_q(table)})"))]
        # foreign_key_list 列序：id, seq, table, from, to, on_update, on_delete, match
        fks = await _fetchall(conn, text(f"PRAGMA foreign_key_list({_q(table)})"))
        char_hits: list[str] = []
        if CHARACTER_FAMILY_COLUMN in cols:
            char_hits.append(CHARACTER_FAMILY_COLUMN)
        for fk in fks:
            ref, local = fk[2], fk[3]
            if ref == CHARACTER_ROOT_TABLE and local not in char_hits:
                char_hits.append(str(local))
        schema[table] = {
            "columns": cols,
            "user_hits": [c for c in USER_FAMILY_COLUMNS if c in cols],
            "character_hits": char_hits,
        }
    if CHARACTER_ROOT_TABLE not in schema:
        raise UserCascadeError(f"库中缺 {CHARACTER_ROOT_TABLE} 表，角色族无法判定归属——先确认建库/迁移")
    return schema


# ── 例外判定 ──────────────────────────────────────────────────────────────────

def _column_kind(column: str) -> str:
    if column in EDITOR_ONLY_COLUMNS:
        return KIND_EDITOR
    if column in DUAL_SEMANTIC_COLUMNS:
        return KIND_DUAL
    return KIND_OWNERSHIP


def _table_exception(table: str, scope: str) -> str | None:
    """表级例外理由（永久保留，或「家庭根归属 ＋ 当前是删子账号」）。"""
    if table in RETAINED_TABLES:
        return RETAINED_TABLES[table]
    if scope == SCOPE_DELETE_SUB_ACCOUNT and table in FAMILY_ROOT_TABLES:
        return FAMILY_ROOT_TABLES[table]
    return None


def _column_exception(table: str, column: str, scope: str) -> str | None:
    if scope == SCOPE_DELETE_SUB_ACCOUNT:
        return FAMILY_ROOT_COLUMNS.get((table, column))
    return None


# ── 删除依据（SQL 片段）───────────────────────────────────────────────────────

def _ownership_fragment(column: str) -> str:
    return f"{_q(column)} = :v_{column}"


def _character_fragment(column: str) -> str:
    return f"{_q(column)} IN :c_{column}"


def _foreign_char_clash(column: str) -> str:
    """``:v_col`` 这个数字**同时是别人角色的 id** 时为真（``speaker_id`` 双语义的歧义来源）。"""
    return (
        f"EXISTS (SELECT 1 FROM {_q(CHARACTER_ROOT_TABLE)} _ac "
        f"WHERE _ac.id = :v_{column} AND _ac.user_id <> :v_{column})"
    )


def _dual_fragments(column: str) -> dict[str, str]:
    clash = _foreign_char_clash(column)
    return {
        # 值就是目标本人，且这个数字没有被别人的角色占用 → 归属明确
        "user": f"({_q(column)} = :v_{column} AND NOT {clash})",
        "character": _character_fragment(column),
        # 判定不出：值撞上别人角色的 id，既不能当「本人在说话」也不能当「自家角色在说话」
        "undetermined": f"({_q(column)} = :v_{column} AND {clash})",
    }


def _union_frag(frags: dict[str, str]) -> str:
    """同列多片段（双语义列）的去重行数用：``(a OR b)``；单片段原样返回。"""
    uniq = list(dict.fromkeys(frags.values()))
    if not uniq:
        return ""
    if len(uniq) == 1:
        return uniq[0]
    return "(" + " OR ".join(f"({f})" for f in uniq) + ")"


async def _count_fragments(
    conn, table: str, where_frags: list[str], case_frags: list[str], *, uid: int, cids: list[int]
) -> tuple[int, dict[str, int]]:
    """一趟扫描取回「去重后总行数」（``where_frags`` 并集）＋「逐片段行数」（``case_frags``）。

    逐列各跑一次 ``COUNT(*)`` 会把大表扫 N 遍（``memories``/``agent_task_logs`` 这类百万级
    日志表上有的是多列命中）；``SUM(CASE WHEN ...)`` 让所有片段共用一趟扫描。

    **总行数走标量子查询、逐片段走全表**：不能把 ``SUM`` 塞在 ``WHERE 并集`` 之后——
    「只登记不删」的引用列（``updated_by`` / 非唯一归属的 ``speaker_id``）恰恰要统计
    **没被选中删除**的那些行（「这个账号在别人行里留了引用」的体量），过滤后会少报；
    而真正的删除依据列在两种口径下数值相同（片段本身就是 WHERE 的一项）。
    """
    where = list(dict.fromkeys(where_frags))
    cases = list(dict.fromkeys(case_frags))
    if not where and not cases:
        return 0, {}
    total_sel = ("(SELECT COUNT(*) FROM " + _q(table) + " WHERE "
                 + " OR ".join(f"({f})" for f in where) + ")") if where else None
    if cases:
        selects = ([total_sel] if total_sel else []) + [
            f"SUM(CASE WHEN {f} THEN 1 ELSE 0 END)" for f in cases]
        sql = f"SELECT {', '.join(selects)} FROM {_q(table)}"
    else:
        sql = f"SELECT {total_sel}"  # 只有删除依据、无引用列：不必再全表扫
    frags = where + cases
    vcols = {m for f in frags for m in re.findall(r":v_([A-Za-z0-9_]+)", f)}
    ccols = {m for f in frags for m in re.findall(r":c_([A-Za-z0-9_]+)", f)}
    stmt = text(sql).bindparams(*[bindparam(f"c_{c}", expanding=True) for c in sorted(ccols)])
    args: dict[str, Any] = {f"v_{c}": uid for c in vcols}
    args.update({f"c_{c}": list(cids) for c in ccols})
    row = (await conn.execute(stmt, args)).fetchone()
    out: dict[str, int] = {}
    if row is None:  # 空表 + 外层 FROM：没有任何行可统计
        return 0, {f: 0 for f in cases}
    idx = 0
    if total_sel:
        out["__total__"] = int(row[0] or 0)
        idx = 1 if cases else 0
    for i, frag in enumerate(cases):
        out[frag] = int(row[idx + i] or 0)
    return out.get("__total__", 0), out


async def _account_character_ids(conn, user_id: int) -> list[int]:
    rows = await _fetchall(
        conn,
        text(f"SELECT id FROM {_q(CHARACTER_ROOT_TABLE)} WHERE user_id = :uid ORDER BY id"),
        {"uid": user_id},
    )
    return [int(r[0]) for r in rows]


# ── 计划 ──────────────────────────────────────────────────────────────────────

async def discover_purge_plan(conn, *, user_id: int, scope: str) -> dict[str, Any]:
    """扫实际库结构，产出「这个账号删下去会带走哪些表、各多少行」的 cascade 计划（纯读）。

    参数
    ----
    conn
        任何有 ``await execute(text())`` 的对象（``AsyncSession`` / ``AsyncConnection``）。
    user_id
        目标账号 id。
    scope
        :data:`SCOPE_DELETE_SUB_ACCOUNT`（目标是子账号，只删这个成员）或
        :data:`SCOPE_DELETE_FAMILY_ROOT`（目标是家庭根，整户共享数据一并带走）。

    返回（可直接 JSON 序列化，进 dry-run 回包与审计快照）
    ---------------------------------------------------
    ``scope`` / ``user_id`` / ``character_ids``（第 0 步固化的角色集合）/ ``character_count`` /
    ``user_family`` + ``character_family``：``[{table, rows, columns[{column,kind,rows,deletable}]}]``
    （``rows`` 是**本表两族并集去重后**的行数）/
    ``exceptions``：``[{table, column, family, reason, columns}]``——当前 scope 下**生效**的例外 /
    ``undetermined_speaker_rows``：``[{table, column, rows}]``——判定不出归属、不删的行 /
    ``totals``：``tables``（清除器实际会发 DELETE 的表数：有删除依据列的候选表，含当前 0 行的）/
    ``row_count``（清除器实际会删的行数，按表去重＝阈值判据）/
    ``undetermined_rows`` / ``editor_only_rows``（仅登记不删的行数）/
    ``warnings``：**仅家庭根 scope**——逐张家庭共享表提示「删除随整户带走」（名下无成员、
    当前 0 行也照样列出，见 :data:`FAMILY_ROOT_TABLES`）
    """
    if scope not in SCOPES:
        raise UserCascadeError(f"scope 只能是 {' / '.join(SCOPES)}，收到 {scope!r}")
    try:
        uid = int(user_id)
    except (TypeError, ValueError) as e:
        raise UserCascadeError(f"user_id 必须是整数，收到 {user_id!r}") from e
    if uid <= 0:
        raise UserCascadeError(f"user_id 必须是正整数，收到 {uid}")

    schema = await _scan_schema(conn)
    cids = await _account_character_ids(conn, uid)

    user_family: list[dict[str, Any]] = []
    char_family: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []
    undetermined: list[dict[str, Any]] = []
    rows_by_table: dict[str, int] = {}

    for table in sorted(schema):
        info = schema[table]
        user_hits, char_hits = info["user_hits"], info["character_hits"]
        if not user_hits and not char_hits:
            continue

        reason = _table_exception(table, scope)
        if reason:
            exceptions.append({
                "table": table, "column": None, "family": "user" if user_hits else "character",
                "reason": reason, "columns": sorted(set(user_hits) | set(char_hits)),
            })
            continue

        # ── 逐列分类：删除依据片段（进 WHERE 并集）/ 仅测量片段 / 列级例外 ──
        where_frags: list[str] = []
        case_frags: list[str] = []
        user_entries: list[dict[str, Any]] = []
        char_entries: list[dict[str, Any]] = []
        # 同表里「更硬」的归属列（真正的行主人）：决定 speaker_id 能不能单独当删除依据。
        live_owner_cols = [
            c for c in user_hits
            if _column_kind(c) == KIND_OWNERSHIP and not _column_exception(table, c, scope)
        ]
        for column in user_hits:
            col_reason = _column_exception(table, column, scope)
            if col_reason:
                exceptions.append({"table": table, "column": column, "family": "user",
                                   "reason": col_reason})
                continue
            kind = _column_kind(column)
            entry: dict[str, Any] = {"column": column, "kind": kind, "rows": 0}
            if kind == KIND_EDITOR:
                frag = _ownership_fragment(column)
                case_frags.append(frag)  # 只测量、不进 WHERE → 不作为删除依据
                entry["deletable"] = False
                entry["_frags"] = {}
                entry["_measured"] = frag
                entry["note"] = ("最后编辑者标记，不作为删除依据"
                                 "（按它删会带走他人/服务器级配置行，如全局额度单行表）")
            elif kind == KIND_DUAL:
                dual = _dual_fragments(column)
                frags = {"user": dual["user"]}
                if cids:
                    frags["character"] = dual["character"]
                # 实测：本库三张带 speaker_id 的表（memories / ai_chats / group_memories）
                # 都另有 user_id 列 —— 那一行属于「行主人」，speaker_id 只是「谁在这行里说过话」。
                # 拿它当删除依据会越界删别人的行（root 的记忆里引用了 sub 发言 → sub 被删不该带走它），
                # 故只在「本列是该表唯一归属信号」时才进 WHERE；判定始终照做，行数列照常报出。
                sole = not live_owner_cols and not char_hits
                if sole:
                    where_frags += list(frags.values())
                case_frags += list(frags.values()) + [dual["undetermined"], _union_frag(frags)]
                entry["deletable"] = sole
                entry["_frags"] = frags
                entry["_union"] = _union_frag(frags)
                entry["_measured"] = "" if sole else _union_frag(frags)
                entry["_undetermined"] = dual["undetermined"]
                entry["note"] = ("双语义列（值可能是 user_id 或 character_id）：先与目标用户 id、"
                                 "该账号角色 id 集合比对判定归属，判定不出的行不删"
                                 + ("" if sole else
                                    "；本表另有更硬的归属列 → 本列只报行数，不作为删除依据"))
            else:
                frag = _ownership_fragment(column)
                where_frags.append(frag)
                case_frags.append(frag)
                entry["deletable"] = True
                entry["_frags"] = {column: frag}
                entry["_union"] = frag
            user_entries.append(entry)

        if cids:
            for column in char_hits:
                col_reason = _column_exception(table, column, scope)
                if col_reason:
                    exceptions.append({"table": table, "column": column, "family": "character",
                                       "reason": col_reason})
                    continue
                frag = _character_fragment(column)
                where_frags.append(frag)
                case_frags.append(frag)
                char_entries.append({"column": column, "kind": KIND_OWNERSHIP, "deletable": True,
                                     "rows": 0, "_frags": {column: frag}, "_union": frag})

        total, counts = await _count_fragments(
            conn, table, where_frags, case_frags, uid=uid, cids=cids
        )
        if where_frags:
            rows_by_table[table] = total  # 只登记不删的表（editor_only）不计入清除体量

        for entry in user_entries + char_entries:
            frags = entry.pop("_frags")
            union = entry.pop("_union", "")
            # rows = 本列**实际会删**的行数（多片段按 (a OR b) 去重：users.id 与 ai_characters.id
            # 都是自增整数，同一行可能同时满足「值 = 目标 uid」和「值 ∈ 该账号角色集合」）。
            # 不作为删除依据的列（editor_only / 非唯一归属的 speaker_id）恒 0，实测量另列。
            entry["rows"] = counts.get(union, 0) if (entry["deletable"] and union) else 0
            measured = entry.pop("_measured", None)
            if measured:
                entry["rows_matched"] = counts.get(measured, 0)
            if entry["kind"] == KIND_DUAL:
                und_frag = entry.pop("_undetermined")
                entry["rows_user"] = counts.get(frags.get("user", ""), 0)
                entry["rows_character"] = counts.get(frags.get("character", ""), 0)
                entry["rows_undetermined"] = counts.get(und_frag, 0)
                if entry["rows_undetermined"]:
                    undetermined.append({"table": table, "column": entry["column"],
                                         "rows": entry["rows_undetermined"]})

        if user_entries:
            user_family.append({"table": table, "rows": total, "columns": user_entries})
        if char_entries:
            char_family.append({"table": table, "rows": total, "columns": char_entries})

    exceptions.sort(key=lambda e: (e["table"], e["column"] or ""))
    undetermined.sort(key=lambda e: (e["table"], e["column"]))
    editor_rows = sum(c.get("rows_matched", 0) for t in user_family for c in t["columns"]
                      if c["kind"] == KIND_EDITOR)

    warnings: list[dict[str, Any]] = []
    if scope == SCOPE_DELETE_FAMILY_ROOT:
        # v2 护栏：家庭根即便名下没有成员（此时它是「可删」的），也要显式提示会带走家庭共享数据。
        # 名下无成员的整户删除没有第二个人能拦，警告是给运维的唯一提示位 → 0 行的表也列出来。
        for table in sorted(FAMILY_ROOT_TABLES):
            if table not in schema:
                continue
            warnings.append({
                "table": table, "rows": rows_by_table.get(table, 0),
                "reason": "这是家庭根账号：家庭共享数据（配置/授权/绑定）整户共用，删除随整户一起带走",
            })

    return {
        "scope": scope,
        "user_id": uid,
        "character_ids": cids,
        "character_count": len(cids),
        "user_family": user_family,
        "character_family": char_family,
        "exceptions": exceptions,
        "undetermined_speaker_rows": undetermined,
        "totals": {
            "tables": len(rows_by_table),
            # 阈值判据：清除器实际会删的行数（两族并集、多列命中按表去重；editor_only 不算）
            "row_count": sum(rows_by_table.values()),
            "undetermined_rows": sum(e["rows"] for e in undetermined),
            "editor_only_rows": editor_rows,
        },
        "warnings": warnings,
    }


# ── 测试锁 ────────────────────────────────────────────────────────────────────

async def user_family_census(conn) -> list[dict[str, Any]]:
    """**客观**清单：库里每一张带用户族归属列的表（不做任何例外判定）。

    测试拿它和「计划里的候选 ∪ 生效例外」做集合差，差集必须为空——这是锁 ① 的独立算法版本，
    两侧同时被改错才会一起漏（v2 §二.4：判据来自实际库结构，不来自文档也不来自 ORM 模型）。
    """
    schema = await _scan_schema(conn)
    return [
        {"table": table, "columns": sorted(info["user_hits"])}
        for table, info in sorted(schema.items()) if info["user_hits"]
    ]


async def character_family_census(conn) -> list[dict[str, Any]]:
    """**客观**清单：每一张按 ``character_id`` 或「物理外键 → ``ai_characters``」归属的表。

    含改名列（``character_a_id`` / ``character_b_id`` 这类靠外键认出来的）。锁 ② 用它比对，
    防的正是「24 张只按 character_id 归属的表被漏掉」那一版翻车（静默孤儿 ＋ 删
    ``ai_characters`` 时抛表名根本不在清单里的外键错）。
    """
    schema = await _scan_schema(conn)
    return [
        {"table": table, "columns": sorted(info["character_hits"])}
        for table, info in sorted(schema.items()) if info["character_hits"]
    ]


async def unhandled_user_family_tables(conn, *, scope: str = SCOPE_DELETE_SUB_ACCOUNT) -> list[dict[str, Any]]:
    """测试锁 ①（模块自检）：带用户族归属列、却既没进候选也没拿到例外理由的表。

    ``_table_exception`` / ``_column_exception`` / 列 kind 三者构成对每张命中表的完整划分，
    正常恒为空；分类被绕过（新增列没有归属语义、或例外判定改坏）时立即非空。
    **真正的独立交叉核对在测试里**：用 :func:`user_family_census` 与计划回包做集合差。
    """
    if scope not in SCOPES:
        raise UserCascadeError(f"scope 只能是 {' / '.join(SCOPES)}，收到 {scope!r}")
    schema = await _scan_schema(conn)
    out = []
    for table in sorted(schema):
        hits = schema[table]["user_hits"]
        if not hits or _table_exception(table, scope):
            continue
        live = [c for c in hits if not _column_exception(table, c, scope)]
        covered = bool(live) or all(_column_exception(table, c, scope) for c in hits)
        if not covered:
            out.append({"table": table, "columns": live})
    return out


async def unhandled_character_family_tables(conn, *, scope: str = SCOPE_DELETE_SUB_ACCOUNT) -> list[dict[str, Any]]:
    """测试锁 ②（模块自检）：角色族归属表里没被分类覆盖的那些（正常恒为空）。

    防的正是「24 张只按 character_id 归属的表被漏掉」那一版翻车（静默孤儿 ＋ 删
    ``ai_characters`` 时抛表名根本不在清单里的外键错）；独立核对同样走
    :func:`character_family_census`。
    注意「该账号名下无角色」不算未处理：那时角色族片段天然匹配 0 行，计划里不出现这些表是
    正确行为（判据是分类，不是行数）。
    """
    if scope not in SCOPES:
        raise UserCascadeError(f"scope 只能是 {' / '.join(SCOPES)}，收到 {scope!r}")
    schema = await _scan_schema(conn)
    out = []
    for table in sorted(schema):
        hits = schema[table]["character_hits"]
        if not hits or _table_exception(table, scope):
            continue
        live = [c for c in hits if not _column_exception(table, c, scope)]
        covered = bool(live) or all(_column_exception(table, c, scope) for c in hits)
        if not covered:
            out.append({"table": table, "columns": live})
    return out


async def unknown_ownership_columns(conn) -> list[dict[str, Any]]:
    """扩展哨兵：列名像归属列、但既不在 8 列口径内也未被写明「不是归属」的（表, 列）。

    测试锁 ① 只覆盖**已知的 8 个列名**；将来有人新增一张表把归属列起名 ``uid`` / ``member_user``，
    自动发现会「扫不到也不报错」——本哨兵把这种漏法变成红灯。
    """
    schema = await _scan_schema(conn)
    out = []
    for table in sorted(schema):
        info = schema[table]
        if info["user_hits"] or info["character_hits"]:
            continue  # 该表已被归属列覆盖，杂名列不影响删除范围
        for column in info["columns"]:
            if column in USER_FAMILY_COLUMNS or column == CHARACTER_FAMILY_COLUMN:
                continue
            if not _OWNERSHIP_NAME_RE.search(column):
                continue
            if (table, column) in NON_OWNERSHIP_COLUMNS:
                continue
            out.append({"table": table, "column": column})
    return out
