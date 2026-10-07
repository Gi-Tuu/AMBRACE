# -*- coding: utf-8 -*-
"""A35（2026-10-07，批 4）僵尸 pending 承诺清理脚本 `cleanup_zombie_promises.py`。

现场（调查报告根因 4）：`kind=promise` 且 `due_end IS NULL` 的行既不会被时间扫描捞到
（`collect_due_promises` 只查 `due_end.is_not(None)`），也没有事件兑现入口 ⇒ 挂在待办池里被
`survival_checklist`／`state_trace` 当「未完成计划」读进上下文，只能等创建满 30 天由
`mark_stale_cues` 收掉。本脚本一次性清理存量：A＝超期置 stale、B＝信号已兑现直接 discharged。

钉住的语义（派单 §3 四档 ＋ 保守边界）：
1. 无 due ＋ 超期 ⇒ 归 A（stale）；未超期 ⇒ 不动；**恰好 N 天不算超期**（与 `_intent_is_stale` 同为严格 `<`）；
2. 无 due ＋ 近期用户消息出现**该行在等的那类**信号 ⇒ 归 B（discharged）并写 `discharged_at`；
3. **有 due 的 pending 一律不进候选**（本单不碰时间窗那一档）；
4. `--days` / `--msgs` 覆盖生效；**dry-run 库逐字节不变**；`--apply` 先整库备份、写回时重读筛选条件。
另：等「药」而消息只有「到家」不构成兑现（不跨类误关）；AI 自己的消息不算兑现信号；
A33 落库的 `trigger=clock` 压过正文推断；`--no-discharge` 只跑 A；
没有 `prospective_intents` 表的库（插件裸 schema）优雅 `[SKIP]`、不抛栈。

（脚本走裸 sqlite3、不经 ORM，测试同口径建最小表；`_now_naive` 打桩钉死「现在」。）
"""
import importlib.util
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

_REPO = Path(__file__).resolve().parent.parent

# 钉死的「现在」：UTC naive（库内 created_at 存的就是 UTC naive，见 _intent_is_stale 无 due 分支）
_NOW = datetime(2026, 10, 7, 4, 0, 0)
_MED = "我要看着用户吃药"                       # 等 medication（现场 id=175）
_ARRIVAL = "我承诺等用户到家后把火锅订好告诉他"   # 明确在等「到家」⇒ 等 arrival（A38 收窄后仍判 arrival；原「我承诺到点喊用户起床」靠裸「到」蒙中，现已归 clock）
_CLOCK = "我答应帮用户查清楚湖光附近的韩料店"      # 不等事件信号 ⇒ 只可能走 A


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "cleanup_zombie_promises_mod",
        _REPO / "scripts" / "cleanup_zombie_promises.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_db(path: Path, *, rows: list[tuple], msgs: list[tuple] = (), with_intents=True, with_chat=True) -> None:
    """最小表结构（列名与生产一致，脚本只读这些列）。rows ＝ prospective_intents 行。"""
    con = sqlite3.connect(str(path))
    if with_intents:
        con.execute(
            "CREATE TABLE prospective_intents (id INTEGER PRIMARY KEY, user_id INT, character_id INT,"
            " content TEXT, kind TEXT, status TEXT, cue_terms_json TEXT, due_start TEXT, due_end TEXT,"
            " chat_session_id INT, discharged_at TEXT, created_at TEXT)")
        con.executemany(
            "INSERT INTO prospective_intents (id, user_id, character_id, content, kind, status,"
            " cue_terms_json, due_start, due_end, chat_session_id, discharged_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    if with_chat:
        con.execute("CREATE TABLE chat_sessions (id INTEGER PRIMARY KEY, user_id INT, character_id INT)")
        con.execute("INSERT INTO chat_sessions VALUES (7, 1, 13)")      # 本行所属角色的会话
        con.execute("INSERT INTO chat_sessions VALUES (8, 1, 20)")      # 别的角色的会话
        con.execute("CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, session_id INT, sender_type TEXT,"
                    " content TEXT, created_at TEXT)")
        con.executemany("INSERT INTO chat_messages VALUES (?, ?, ?, ?, ?)", list(msgs))
    con.commit()
    con.close()


def _row(rid, content, *, kind="promise", status="pending", due_end=None, session=7, days_ago=40,
         cue_terms='{"terms": [], "side": "self"}'):
    return (rid, 1, 13, content, kind, status, cue_terms, None, due_end, session, None, _at(days_ago))


def _msg(rid, text, *, sender="user", session=7, minutes_ago=1):
    return (rid, session, sender, text, (_NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S"))


def _at(days_ago=45, hours=0):
    return (_NOW - timedelta(days=days_ago, hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def _statuses(db: Path) -> dict:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    out = {r[0]: (r[1], r[2]) for r in con.execute("SELECT id, status, discharged_at FROM prospective_intents")}
    con.close()
    return out


def _run(tmp_path, monkeypatch, capsys, rows, argv, msgs=(), **mk):
    """建库 → 打桩 DB/now → 跑 `main(argv)` → ``(退出码, stdout, 库路径)``。

    同一个用例里允许复跑（换 `--days` / `--msgs` 对照读数）：先删掉上一轮的库文件。
    """
    db = tmp_path / "zombie.db"
    if db.exists():
        db.unlink()
    _make_db(db, rows=rows, msgs=msgs, **mk)
    mod = _load_script()
    monkeypatch.setattr(mod, "DB", db)
    monkeypatch.setattr(mod, "_now_naive", lambda: _NOW)
    rc = mod.main(argv)
    return rc, capsys.readouterr().out, db


def _ns(content=_CLOCK, created_at=_at(), cue_terms="{}"):
    return SimpleNamespace(content=content, cue_terms_json=cue_terms, created_at=created_at)


# ─────────────── 纯函数：decide ＝ 唯一判定口（B 优先于 A）───────────────

def test_decide_three_outcomes():
    """decide 四档：超期无信号→A；未超期无信号→不动；有信号→B；**既超期又有信号→B（记兑现）**。"""
    mod = _load_script()
    kw = dict(now_utc=_NOW, days=30)

    assert mod.decide(_ns(_CLOCK, _at(45)), **kw, user_texts=[]) == \
        ("stale", "A", "无 due 已挂 45 天（阈值 30）")
    assert mod.decide(_ns(_CLOCK, _at(2)), **kw, user_texts=["随便聊点别的"])[0] is None
    assert mod.decide(_ns(_MED, _at(2)), **kw, user_texts=["吃了"])[0] == "discharged"
    assert mod.decide(_ns(_MED, _at(45)), **kw, user_texts=["吃了"])[0] == "discharged"


def test_decide_stale_boundary_is_strict_like_production():
    """边界钉住：恰好 30 天**不算**超期（与 `_intent_is_stale` 的严格 `<` 同口径），多 1 小时才退场。"""
    mod = _load_script()
    kw = dict(now_utc=_NOW, days=30, user_texts=[])

    assert mod.decide(_ns(_CLOCK, _at(30)), **kw)[0] is None
    assert mod.decide(_ns(_CLOCK, _at(30, hours=1)), **kw)[0] == "stale"


def test_decide_does_not_mix_signal_kinds():
    """不跨类误关：在等「药」的承诺不会被「到家了」关掉；反之亦然。"""
    mod = _load_script()
    kw = dict(now_utc=_NOW, days=30)

    assert mod.decide(_ns(_MED, _at(2)), **kw, user_texts=["我到家了"])[0] is None
    assert mod.decide(_ns(_MED, _at(2)), **kw, user_texts=["到家了", "吃了"])[0] == "discharged"
    assert mod.decide(_ns(_ARRIVAL, _at(2)), **kw, user_texts=["吃了"])[0] is None
    assert mod.decide(_ns(_ARRIVAL, _at(2)), **kw, user_texts=["我到家了"])[0] == "discharged"


def test_decide_prefers_stored_trigger_tag():
    """A33 ⑥ 口径延续：落库 trigger=clock ⇒ 正文像 arrival 也不按事件关闭（只能走 A）。"""
    mod = _load_script()
    tagged = _ns(_ARRIVAL, _at(2), cue_terms='{"terms": [], "trigger": "clock"}')
    assert mod.decide(tagged, now_utc=_NOW, days=30, user_texts=["我到家了"])[0] is None
    legacy_list = _ns(_ARRIVAL, _at(2), cue_terms='["到家"]')          # 旧 list 格式＝没标签
    assert mod.decide(legacy_list, now_utc=_NOW, days=30, user_texts=["我到家了"])[0] == "discharged"


def test_parse_dt_accepts_storage_shapes():
    """时间串解析与 SQLAlchemy 的 SQLite 存储口径对齐：秒/微秒/T 分隔都能读，脏值→None。"""
    mod = _load_script()
    assert mod.parse_dt("2026-10-06 15:09:29") == datetime(2026, 10, 6, 15, 9, 29)
    assert mod.parse_dt("2026-10-06T15:09:29.123456") == datetime(2026, 10, 6, 15, 9, 29)
    assert mod.parse_dt(None) is None and mod.parse_dt("") is None
    assert mod.parse_dt("四十五天前") is None


# ─────────────── 候选口径：只捞无 due 的 promise ───────────────

def test_overdue_nodue_goes_A_and_young_untouched(tmp_path, monkeypatch, capsys):
    """① 无 due ＋ 超期 ⇒ A；未超期 ⇒ 不动；cue／已终态行不进候选。"""
    rows = [_row(175, _MED, days_ago=45), _row(165, _CLOCK, days_ago=33), _row(999, _MED, days_ago=12),
            _row(888, "用户到家跟我说一声", kind="cue", days_ago=60),
            _row(777, _MED, status="discharged", days_ago=60)]
    rc, out, db = _run(tmp_path, monkeypatch, capsys, rows, [], msgs=[_msg(1, "还没吃")])

    assert rc == 0
    assert "僵尸 pending 3 条 / 将改 2 条（A stale 2 ＋ B discharged 0）: 165(A, 超期) / 175(A, 超期)" in out
    assert "[不动] id=999" in out and "未超期（12 天 < 30）" in out
    assert "id=888" not in out and "id=777" not in out              # cue／discharged 不进候选
    assert _statuses(db) == {175: ("pending", None), 165: ("pending", None), 999: ("pending", None),
                             888: ("pending", None), 777: ("discharged", None)}


def test_promise_with_due_end_never_enters_candidates(tmp_path, monkeypatch, capsys):
    """③ 有 due 的 pending **一律不动**（本单不碰）：超期 100 天、消息里带兑现信号也不进候选。"""
    due = _at(100)
    rows = [_row(1, _MED, days_ago=100, due_end=due), _row(2, _ARRIVAL, days_ago=100, due_end=due)]
    rc, out, db = _run(tmp_path, monkeypatch, capsys, rows, ["--apply"],
                       msgs=[_msg(1, "吃了"), _msg(2, "我到家了")])

    assert rc == 0 and "僵尸 pending 0 条 / 将改 0 条" in out
    assert _statuses(db) == {1: ("pending", None), 2: ("pending", None)}


# ─────────────── B 档：信号命中 → discharged（写 discharged_at）───────────────

def test_signal_hit_goes_B_and_writes_discharged_at(tmp_path, monkeypatch, capsys):
    """② 无 due ＋ 本会话近期用户消息命中 ⇒ B：置 discharged 并写 discharged_at（UTC now）。"""
    rows = [_row(175, _MED, days_ago=45), _row(176, _MED, days_ago=3)]
    rc, out, db = _run(tmp_path, monkeypatch, capsys, rows, ["--apply"],
                       msgs=[_msg(1, "随便聊点别的", minutes_ago=5), _msg(2, "吃了", minutes_ago=30)])

    assert rc == 0 and "将改 2 条（A stale 0 ＋ B discharged 2）: 175(B, 已兑现) / 176(B, 已兑现)" in out
    assert "等 medication 的信号已出现：「吃了」" in out
    st = _statuses(db)
    assert st[175][0] == "discharged" and st[176][0] == "discharged"
    assert st[175][1] == _NOW.strftime("%Y-%m-%d %H:%M:%S")          # 与 _set_status(discharge=True) 同口径
    assert "[不动] id=" not in out


def test_only_user_messages_count_as_fulfilment_signal(tmp_path, monkeypatch, capsys):
    """AI 自己说「吃了」不算兑现（与 A32 在线口径一致：只读 sender_type='user'）。"""
    rows = [_row(1, _MED, days_ago=2)]
    rc, out, db = _run(tmp_path, monkeypatch, capsys, rows, ["--apply"],
                       msgs=[_msg(1, "你吃药了吗", sender="ai")])

    assert rc == 0 and "将改 0 条" in out and _statuses(db) == {1: ("pending", None)}


def test_no_discharge_flag_runs_only_A(tmp_path, monkeypatch, capsys):
    """`--no-discharge`＝只跑 A（宽正则把 B 判定放宽时的退路，A38 收窄前登记的）：B 照样打印读数，但不进改动清单。"""
    rows = [_row(1, _ARRIVAL, days_ago=45), _row(2, _CLOCK, days_ago=45)]
    rc, out, db = _run(tmp_path, monkeypatch, capsys, rows, ["--apply", "--no-discharge"],
                       msgs=[_msg(1, "我不是早就到家了吗？")])

    assert rc == 0 and "B*" in out and "--no-discharge：只跑 A，本条不动" in out
    assert "将改 1 条（A stale 1 ＋ B discharged 0）: 2(A, 超期)" in out
    assert _statuses(db) == {1: ("pending", None), 2: ("stale", None)}


def test_no_session_row_falls_back_to_character_scope(tmp_path, monkeypatch, capsys):
    """行上没有 chat_session_id ⇒ 退到「该角色最近 M 条」；别的角色的消息不算（不跨角色误关）。"""
    row = _row(1, _MED, session=None, days_ago=3)
    rc, out, db = _run(tmp_path, monkeypatch, capsys, [row], ["--apply"], msgs=[_msg(1, "吃了", session=8)])
    assert rc == 0 and "角色 13（本行无会话）" in out and "将改 0 条" in out    # 信号来自角色 20
    assert _statuses(db) == {1: ("pending", None)}

    rc, out, db = _run(tmp_path, monkeypatch, capsys, [row], ["--apply"], msgs=[_msg(1, "吃了", session=7)])
    st = _statuses(db)
    assert "1(B, 已兑现)" in out and st[1][0] == "discharged"
    assert datetime.fromisoformat(st[1][1]) == _NOW


def test_msgs_window_limits_lookback(tmp_path, monkeypatch, capsys):
    """`--msgs`＝只往回看最近几条用户消息（默认 5）：信号被挤出窗口 ⇒ 不成 B，只剩 A。"""
    rows = [_row(1, _MED, days_ago=45)]
    msgs = [_msg(1, "闲聊", minutes_ago=1), _msg(2, "闲聊", minutes_ago=2),
            _msg(3, "闲聊", minutes_ago=3), _msg(4, "吃了", minutes_ago=4)]

    rc, out, db = _run(tmp_path, monkeypatch, capsys, rows, ["--apply", "--msgs=3"], msgs=msgs)
    assert rc == 0 and "将改 1 条（A stale 1 ＋ B discharged 0）: 1(A, 超期)" in out
    assert _statuses(db) == {1: ("stale", None)}

    rc, out, db = _run(tmp_path, monkeypatch, capsys, rows, ["--apply", "--msgs=4"], msgs=msgs)
    assert "1(B, 已兑现)" in out and _statuses(db)[1][0] == "discharged"


# ─────────────── ④ --days 覆盖 ＋ dry-run 不写库 ＋ --apply 备份 ───────────────

def test_days_override_moves_boundary(tmp_path, monkeypatch, capsys):
    """④ `--days=10` 覆盖生效：15 天前的无 due 行按默认 30 不动、按 10 天就归 A。"""
    rows = [_row(1, _CLOCK, days_ago=15)]
    rc, out, db = _run(tmp_path, monkeypatch, capsys, rows, [], msgs=[])
    assert rc == 0 and "将改 0 条" in out and "未超期（15 天 < 30）" in out
    assert _statuses(db) == {1: ("pending", None)}

    rc, out, db = _run(tmp_path, monkeypatch, capsys, rows, ["--apply", "--days=10"], msgs=[])
    assert rc == 0 and "将改 1 条（A stale 1 ＋ B discharged 0）: 1(A, 超期)" in out
    assert "无 due 已挂 15 天（阈值 10）" in out
    assert _statuses(db) == {1: ("stale", None)}


def test_dry_run_leaves_db_byte_identical(tmp_path, monkeypatch, capsys):
    """④ 默认 dry-run：判定照常打印（A ＋ B 两类都有），但库**逐字节**不变。"""
    db = tmp_path / "zombie.db"
    _make_db(db, rows=[_row(175, _MED, days_ago=45), _row(165, _CLOCK, days_ago=33),
                       _row(1, _ARRIVAL, days_ago=2)],
             msgs=[_msg(1, "到家了", minutes_ago=1)])
    mod = _load_script()
    monkeypatch.setattr(mod, "DB", db)
    monkeypatch.setattr(mod, "_now_naive", lambda: _NOW)
    before = db.read_bytes()

    assert mod.main([]) == 0
    out = capsys.readouterr().out
    assert "僵尸 pending 3 条 / 将改 3 条" in out
    assert "1(B, 已兑现) / 165(A, 超期) / 175(A, 超期)" in out
    assert "只读演练" in out
    assert db.read_bytes() == before                                # 跑前后一字节都没动
    assert _statuses(db) == {175: ("pending", None), 165: ("pending", None), 1: ("pending", None)}


def test_apply_writes_and_leaves_backup_of_original(tmp_path, monkeypatch, capsys):
    """`--apply` 先整库备份（sqlite3 在线 backup API）：备份里是改动前的原始状态。"""
    rows = [_row(175, _MED, days_ago=45), _row(165, _CLOCK, days_ago=33)]
    rc, out, db = _run(tmp_path, monkeypatch, capsys, rows, ["--apply"], msgs=[_msg(1, "吃了")])

    assert rc == 0 and "[OK] 已清理 2/2 条" in out and "备份" in out
    assert _statuses(db) == {175: ("discharged", _NOW.strftime("%Y-%m-%d %H:%M:%S")),
                             165: ("stale", None)}
    baks = sorted(tmp_path.glob("zombie.db.bak-zombie-*"))
    assert len(baks) == 1
    con = sqlite3.connect(f"file:{baks[0].as_posix()}?mode=ro", uri=True)
    kept = {r[0]: r[1] for r in con.execute("SELECT id, status FROM prospective_intents")}
    assert kept == {175: "pending", 165: "pending"}                 # 备份＝改动前快照
    con.close()


def test_apply_does_not_overwrite_rows_changed_in_between(tmp_path, monkeypatch, capsys):
    """写回时重读三个筛选条件：出清单后被在线路径改过的行**不覆盖**（rowcount 0，留在线结果）。"""
    db = tmp_path / "zombie.db"
    _make_db(db, rows=[_row(175, _CLOCK, days_ago=45)], msgs=[])
    mod = _load_script()
    monkeypatch.setattr(mod, "DB", db)
    monkeypatch.setattr(mod, "_now_naive", lambda: _NOW)
    monkeypatch.setattr(mod, "build_plan", lambda con, **kw: ([], [(175, "stale", "A")]))  # 清单已过期
    con = sqlite3.connect(str(db))
    con.execute("UPDATE prospective_intents SET status = 'discharged' WHERE id = 175")      # 在线先关掉了
    con.commit()
    con.close()

    assert mod.main(["--apply"]) == 0
    out = capsys.readouterr().out
    assert "已清理 0/1 条（其余 1 条已被在线路径改过，未覆盖）" in out
    assert _statuses(db)[175][0] == "discharged"


# ─────────────── 守卫：裸 schema ＋ 无消息表 ＋ 读不懂的 created_at ───────────────

def test_bare_schema_db_skips_gracefully(tmp_path, monkeypatch, capsys):
    """没有 prospective_intents 表（插件裸 schema 先例）⇒ [SKIP] 退出 0，不抛栈。"""
    rc, out, _ = _run(tmp_path, monkeypatch, capsys, [], [], with_intents=False)
    assert rc == 0 and "[SKIP]" in out and "Traceback" not in out


def test_missing_chat_tables_still_runs_A(tmp_path, monkeypatch, capsys):
    """没有 chat_messages 表 ⇒ B 判不出来（提示一句、不算错），A 照常清理。"""
    rows = [_row(175, _MED, days_ago=45), _row(1, _MED, days_ago=2)]
    rc, out, db = _run(tmp_path, monkeypatch, capsys, rows, ["--apply"], with_chat=False)

    assert rc == 0 and "取不到用户消息" in out
    assert "将改 1 条（A stale 1 ＋ B discharged 0）: 175(A, 超期)" in out
    assert _statuses(db) == {175: ("stale", None), 1: ("pending", None)}


def test_unparsable_created_at_is_left_alone(tmp_path, monkeypatch, capsys):
    """created_at 读不懂 ⇒ 不猜、不动（判不了就不改，与 A33「坏 JSON 跳过」同口径）。"""
    row = list(_row(1, _CLOCK, days_ago=45))
    row[-1] = "四十五天前"
    rc, out, db = _run(tmp_path, monkeypatch, capsys, [tuple(row)], ["--apply"], msgs=[])

    assert rc == 0 and "created_at 无法解析" in out and "将改 0 条" in out
    assert _statuses(db) == {1: ("pending", None)}


def test_default_days_reuses_production_constant_and_no_second_regex():
    """A 阈值默认值＝生产常量 STALE_NODUE_DAYS（口径唯一）；判定复用生产函数，脚本里没有第二套正则。"""
    from app.scheduling.prospective_intent import STALE_NODUE_DAYS

    src = (_REPO / "scripts" / "cleanup_zombie_promises.py").read_text(encoding="utf-8")
    assert STALE_NODUE_DAYS == 30
    assert "STALE_NODUE_DAYS" in src                                # 默认值不另写死 30
    assert "_promise_awaits" in src and "_signal_seen" in src       # 兑现判定复用 A32 生产函数
    assert "get_intent_trigger" in src                               # A33 标签优先
    assert "re.compile" not in src and "ARRIVAL_PAT = " not in src and "MED_PAT = " not in src
