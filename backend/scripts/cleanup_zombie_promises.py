# -*- coding: utf-8 -*-
"""A35（2026-10-07，批 4）一次性清理：既无 due、又没人关闭的「僵尸 pending」承诺。

背景（调查报告根因 4；生产实测候选 23 条，落在 char=6／char=13，如 id=175「我要看着用户吃药」）：
``kind='promise'`` 且 ``due_end IS NULL`` 的行不会被时间扫描捞到（``collect_due_promises`` 只查
``due_end.is_not(None)``），也没有别的入口把它们关掉，只能等创建满 ``STALE_NODUE_DAYS``(30) 天由
``mark_stale_cues`` 置 stale。这期间它们一直挂在待办池里（并且被 ``survival_checklist``／
``state_trace`` 当「未完成计划」读进上下文——两处查询都只按 ``status='pending'`` 过滤、不看 due），
若日后有新的扫描接入还会误触发。A32 补的是「用户消息里出现兑现信号 → 当场静默关闭」，
**只覆盖信号出现那一刻还在在线路径里的场景**；本脚本处理剩下的存量：没有 due、也没有（在线）信号可命中的那批。

两条处置（同一行只归一类，**B 优先于 A**：能确认已兑现的就记成兑现，而不是拖到超期）：
  A. 置 stale —— 创建时间超过 N 天（默认 30＝``STALE_NODUE_DAYS``，``--days`` 覆盖）⇒ ``status='stale'``，
     与 ``mark_stale_cues`` / ``_intent_is_stale`` 的无 due 分支同口径（同样拿 **UTC** now 比 ``created_at``）。
  B. 已兑现的直接关闭 —— 该行「在等的事件」类型（A33 落库的 ``trigger`` 标签，缺失则按正文推断）
     的信号，已出现在**本会话最近 M 条用户消息**里 ⇒ ``status='discharged'`` 并写 ``discharged_at``。
     判定**逐字复用生产函数** ``_promise_awaits`` ＋ ``_signal_seen``（A32 口径唯一来源），
     脚本里**不另写一套正则**。M 默认 5（``--msgs`` 覆盖）。
     行上没有 ``chat_session_id`` 时退到「该角色全部会话最近 M 条」，输出里标明用的是哪个范围，
     便于人工核对是否误关。

有 ``due_end`` 的 pending **一律不动**（到期/超窗/跨天由 ``collect_due_promises`` 与周期清扫负责）。

用法（默认 dry-run，一个字节都不写；写库前自动整库备份）：
    backend\\.venv\\Scripts\\python.exe backend/scripts/cleanup_zombie_promises.py            # dry-run
    backend\\.venv\\Scripts\\python.exe backend/scripts/cleanup_zombie_promises.py --apply    # 真写（先备份）
可选：``--days=30``（A 阈值）、``--msgs=5``（B 取最近几条用户消息）、``--no-discharge``（只跑 A，
B 仍判定并打印但不进改动清单）。

路径口径：优先环境变量 AMBRACE_DB，缺省用「仓库根/backend/data/sqlite/ai_companion.db」，不写死作者机器路径。
**生产库是否执行 --apply 由用户/Codex 决定**；建议运行期间停止服务器，避免并发写。
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
DB = Path(os.environ.get("AMBRACE_DB", str(ROOT / "backend" / "data" / "sqlite" / "ai_companion.db")))
DEFAULT_MSGS = 5
COLUMNS = "id, user_id, character_id, content, status, cue_terms_json, chat_session_id, created_at"

sys.path.insert(0, str(ROOT / "backend"))
try:
    from app.scheduling.prospective_intent import (
        STALE_NODUE_DAYS, _now_naive, _promise_awaits, _signal_seen, get_intent_trigger,
    )
except Exception as e:  # 用系统 python 跑的常见后果：给出可执行的纠正指引，而不是半截改库
    print(f"[ERROR] 无法导入 app.scheduling.prospective_intent（兑现信号判定口径唯一来源）: {e}\n"
          f"        请用 venv：backend\\.venv\\Scripts\\python.exe {sys.argv[0]}")
    raise SystemExit(2)


def parse_dt(raw) -> datetime | None:
    """SQLite 里存量的 naive UTC 时间串 → datetime；解析不了返回 None（判不了就不动）。"""
    text = str(raw or "").strip().replace("T", " ")[:19]
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def decide(row, *, now_utc: datetime, days: int, user_texts: list[str]) -> tuple[str | None, str, str]:
    """一行僵尸 promise 该怎么处置 → ``(新状态|None, A/B 标记, 说明)``。纯函数、零 IO。

    B 先判（能确认兑现就记兑现），其次 A（超期退场）；都不成立 ⇒ None＝不动。
    """
    awaits = _promise_awaits(row.content or "", stored=get_intent_trigger(row))
    if awaits is not None:
        hit = next((t for t in user_texts if _signal_seen(awaits, t)), None)
        if hit is not None:
            short = (hit or "").strip().replace("\n", " ")[:24]
            return "discharged", "B", f"等 {awaits} 的信号已出现：「{short}」"
    created = parse_dt(row.created_at)
    if created is None:
        return None, "-", f"created_at 无法解析（{row.created_at!r}），不动"
    age = (now_utc - created).days
    if created < now_utc - timedelta(days=days):
        return "stale", "A", f"无 due 已挂 {age} 天（阈值 {days}）"
    return None, "-", f"未超期（{age} 天 < {days}）且无兑现信号，不动"


def _open_ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def zombie_rows(con: sqlite3.Connection) -> list[SimpleNamespace]:
    """口径三条件：kind=promise ＋ status∈(pending,matched) ＋ 无 due_end。"""
    return [SimpleNamespace(**dict(r)) for r in con.execute(
        f"SELECT {COLUMNS} FROM prospective_intents "
        "WHERE kind = 'promise' AND status IN ('pending', 'matched') AND due_end IS NULL "
        "ORDER BY character_id, id"
    ).fetchall()]


def recent_user_texts(con: sqlite3.Connection, row, limit: int) -> tuple[list[str], str]:
    """该行「最近 M 条用户消息」：优先本会话（chat_session_id），没有会话则退到该角色全部会话。

    没有 chat_messages 表（插件裸 schema）⇒ 空列表＝B 判不出来，只可能走 A。
    """
    if row.chat_session_id is not None:
        where, args, scope = "m.session_id = ?", (int(row.chat_session_id),), f"会话 {row.chat_session_id}"
    else:
        where = "m.session_id IN (SELECT id FROM chat_sessions WHERE character_id = ?)"
        args, scope = (int(row.character_id),), f"角色 {row.character_id}（本行无会话）"
    try:
        rows = con.execute(
            f"SELECT m.content FROM chat_messages m WHERE m.sender_type = 'user' AND {where} "
            "ORDER BY m.created_at DESC, m.id DESC LIMIT ?", (*args, int(limit))
        ).fetchall()
    except sqlite3.OperationalError as e:            # 无消息表/无会话表：B 无从判定，不是错误
        print(f"[提示] 取不到用户消息（{e}），本轮只可能归 A")
        return [], scope
    return [str(r["content"] or "") for r in rows], scope


def _int_opt(argv, name, default):
    for a in argv:
        if a.startswith(f"--{name}"):
            raw = a.split("=", 1)[1] if "=" in a else ""
            try:
                return max(1, int(raw))
            except ValueError:
                print(f"[ERROR] --{name} 需要整数（收到 {raw!r}）")
                raise SystemExit(2)
    return default


def build_plan(con: sqlite3.Connection, *, days: int, msgs: int, now_utc: datetime, keep_b: bool = True):
    """逐行判定并打印明细 → ``(候选行, [(id, 新状态, A/B 标记)])``；同一会话/角色的消息只取一次。

    ``keep_b=False``（``--no-discharge``）＝只跑 A：B 仍然判定并打印（读数要看得见），但不进改动清单。
    给「不接受裸『到』误伤」（A32/A33 已登记观察项）而仍想收掉超期僵尸的操作者留一条退路。
    """
    rows = zombie_rows(con)
    plan: list[tuple[int, str, str]] = []
    cache: dict[tuple, tuple[list[str], str]] = {}
    for row in rows:
        key = ("sess", row.chat_session_id) if row.chat_session_id is not None else ("char", row.character_id)
        if key not in cache:
            cache[key] = recent_user_texts(con, row, msgs)
        texts, scope = cache[key]
        status, tag, why = decide(row, now_utc=now_utc, days=days, user_texts=texts)
        if status == "discharged" and not keep_b:
            status, why = None, f"{why}（--no-discharge：只跑 A，本条不动）"
            tag = "B*"
        print(f"[{'将改' if status else '不动'}] id={row.id} char={row.character_id} {tag} → "
              f"{status or row.status}｜{scope}｜{why}｜正文={str(row.content)[:40]}")
        if status:
            plan.append((int(row.id), status, tag))
    return rows, plan


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    days = _int_opt(argv, "days", STALE_NODUE_DAYS)
    msgs = _int_opt(argv, "msgs", DEFAULT_MSGS)
    if not DB.exists():
        print(f"[ERROR] 库不存在: {DB}")
        return 2

    now_utc = _now_naive()
    keep_b = "--no-discharge" not in argv
    con = _open_ro(DB)
    try:
        rows, plan = build_plan(con, days=days, msgs=msgs, now_utc=now_utc, keep_b=keep_b)
    except sqlite3.OperationalError as e:            # 插件裸 schema 库无此表（同迁移守卫先例）
        print(f"[SKIP] 库中无 prospective_intents（或所需列），无需清理：{e}")
        return 0
    finally:
        con.close()

    detail = " / ".join(f"{pid}({tag}, {'已兑现' if tag == 'B' else '超期'})" for pid, _, tag in plan) or "无"
    print(f"[{'APPLY' if apply else 'DRY-RUN'}] 僵尸 pending {len(rows)} 条 / 将改 {len(plan)} 条"
          f"（A stale {sum(1 for _, s, _ in plan if s == 'stale')} ＋ B discharged "
          f"{sum(1 for _, s, _ in plan if s == 'discharged')}）: {detail}")
    if not plan:
        return 0
    if not apply:
        print(f"（只读演练，取值 now(UTC)={now_utc:%Y-%m-%d %H:%M:%S} days={days} msgs={msgs} "
              f"discharge={'开' if keep_b else '关（--no-discharge）'}；确认无误后加 --apply 执行，会先整库备份）")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = DB.with_name(DB.name + f".bak-zombie-{stamp}")
    # 用 sqlite3 在线 backup API，而不是文件拷贝：库处于 WAL 活跃状态时，
    # 直接拷 .db 可能漏掉还在 -wal 里的最新事务；backup API 取一致性快照。
    _src = _open_ro(DB)
    _dst = sqlite3.connect(str(bak))
    _src.backup(_dst)
    _dst.close()
    _src.close()

    con = sqlite3.connect(str(DB))
    con.execute("PRAGMA busy_timeout=30000")
    discharged_at = now_utc.isoformat(sep=" ")
    done = 0
    with con:
        for pid, status, _tag in plan:
            # 重读三个筛选条件：dry-run 到 apply 之间若已被在线路径改动，这里就不会覆盖它
            if status == "discharged":
                cur = con.execute(
                    "UPDATE prospective_intents SET status = 'discharged', discharged_at = ? "
                    "WHERE id = ? AND kind = 'promise' AND status IN ('pending', 'matched') AND due_end IS NULL",
                    (discharged_at, pid))
            else:
                cur = con.execute(
                    "UPDATE prospective_intents SET status = 'stale' "
                    "WHERE id = ? AND kind = 'promise' AND status IN ('pending', 'matched') AND due_end IS NULL",
                    (pid,))
            done += cur.rowcount
    con.close()
    print(f"[OK] 已清理 {done}/{len(plan)} 条（其余 {len(plan) - done} 条已被在线路径改过，未覆盖）；备份: {bak}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
