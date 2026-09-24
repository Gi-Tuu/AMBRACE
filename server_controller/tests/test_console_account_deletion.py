"""控制台删号·第三期：回收站开关 / 关键字筛选 / 两段式删除确认 / 立即清除 / 清除报告。

沿用 `test_console_smoke.py` 的口径：**HTTP 全打桩**（未登记端点直接 AssertionError）、
`sqlite3` 换成炸断言（不许直连库）、窗口 `withdraw()` 离屏构造、不截图、不发真请求。
红线尤其重要：这里出现的 `delete` / `purge` 全部打在桩上，真服务器只能吃 GET 与 dry-run。
"""
from __future__ import annotations

import datetime as dt

import conftest as cf
import pytest
import server_controller as sc
import tkinter as tk

API = sc.ADMIN_API_PREFIX
NORMAL_ROW = {"id": 2, "username": "beta", "nickname": "贝塔", "is_admin": False,
              "server_admin": False, "disabled_at": None, "llm_mode": "default_allowed",
              "llm_total_limit": 3000, "llm_total_limit_source": "global",
              "deleted_at": None, "purge_after": None}


def _bin_row(days_ahead: int = 5) -> dict:
    """回收站样本行：``purge_after`` 一律相对**真实现在**算，倒计时文案才不至于今天过。

    倒计时比的是「库内 naive UTC」与 utcnow，写死日期会让这条断言在若干天后自己烂掉。
    """
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    return {"id": 4, "username": "delta", "nickname": "德尔塔", "is_admin": True,
            "server_admin": False, "disabled_at": now.isoformat(timespec="seconds"),
            "deleted_at": now.isoformat(timespec="seconds"),
            "purge_after": (now + dt.timedelta(days=days_ahead)).isoformat(timespec="seconds"),
            "llm_mode": "default_allowed", "llm_total_limit": None,
            "llm_total_limit_source": "unset"}


def walk(w):
    yield w
    for child in w.winfo_children():
        yield from walk(child)


def texts_of(root) -> list:
    return [str(w.cget("text")) for w in walk(root) if isinstance(w, tk.Label)]


def has_text(root, needle: str) -> bool:
    return any(needle in t for t in texts_of(root))


def dialogs(app) -> list:
    return [w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel)]


def last_dialog(app):
    got = dialogs(app)
    assert got, "没有弹出任何确认卡"
    return got[-1]


def tables_in(root) -> list:
    return [w for w in walk(root) if isinstance(w, sc.CUI.DataTable)]


def entries_in(root) -> list:
    return [w for w in walk(root) if isinstance(w, tk.Entry)]


def button_named(root, name: str):
    for w in walk(root):
        if isinstance(w, sc.RoundedButton) and w._text == name:
            return w
    return None


@pytest.fixture(autouse=True)
def _close_dialogs(app):
    yield
    for win in dialogs(app):
        try:
            win.destroy()
        except Exception:
            pass


# ── 纯函数（展示口径；判据一律在后端）──────────────────────────────

def test_countdown_text_buckets():
    """倒计时三档：剩余整天向下取整（不足一天兜到 1）、已到期给固定文案、字段缺失不许瞎猜。"""
    now = dt.datetime(2026, 9, 24, 2, 0, 0, tzinfo=dt.timezone.utc)
    assert sc._purge_countdown_text("2026-09-27 02:03:04", now) == "3 天后自动清除"
    assert sc._purge_countdown_text("2026-09-24 01:00:00", now) == sc.PURGE_DUE_TEXT
    assert sc._purge_countdown_text(None, now) == "在回收站"
    assert sc._purge_countdown_text("不像时间", now) == "在回收站"
    # 库内是 naive UTC：换算成本地(UTC+8)再比会整整差 8 小时，这里钉住口径
    assert sc._purge_countdown_text("2026-09-24 09:00:00", now) == "1 天后自动清除"
    assert sc._purge_countdown_text(dt.datetime(2026, 9, 26, 2, 3, 4, tzinfo=dt.timezone.utc)
                                    .replace(tzinfo=None), now) == "2 天后自动清除"


def test_keyword_filter_matches_name_nickname_and_id():
    rows = [NORMAL_ROW, _bin_row(), {"id": 3, "username": "gamma", "nickname": ""}]
    assert sc._account_rows_filtered(rows, "") == rows
    assert [r["id"] for r in sc._account_rows_filtered(rows, "DEL")] == [4]
    assert [r["id"] for r in sc._account_rows_filtered(rows, "贝塔")] == [2]
    assert [r["id"] for r in sc._account_rows_filtered(rows, " 3 ")] == [3]
    assert sc._account_rows_filtered(rows, "没有这个账号") == []
    assert sc._account_rows_filtered([None, "x", NORMAL_ROW], "") == [NORMAL_ROW]


def test_volume_rows_and_report_rows_shape():
    vol = sc._volume_table_rows(cf.delete_dry_run_payload()["tables"])
    assert vol[0] == ["chat_messages", "900", "user_id"], vol[0]
    assert vol[1][2] == "speaker_id、user_id", \
        "命中列要合并去重后字典序，且 deletable=False 的 editor_user_id 不许混进来"
    rep = cf.purge_report_payload()
    assert sc._report_table_rows(rep["report"], rep["cursor"]) == [
        ["chat_messages", "900"], ["memories", "300"]]
    # 报告没落地（running/failed 半删）时读进度，不许显示成「什么都没删」
    assert sc._report_table_rows({}, rep["cursor"])
    assert sc._report_table_rows(None, None) == []
    assert sc._kv_brief({"deleted": 300}) == "deleted=300"


# ── 工具条：显示回收站 + 关键字筛选 ────────────────────────────────

def test_recycle_bin_hidden_until_switched_on(app, stub):
    app._load_accounts()
    body = app._admin_meta["accounts"]["body"]
    assert sum(cf.row_count(t) for t in tables_in(body)) == 3
    assert not has_text(body, "delta"), "默认开关关着就该把回收站藏起来"
    assert ("GET", API + "/accounts") in stub.calls
    assert ("GET", API + "/accounts?include_deleted=true") not in stub.calls

    app._accounts_show_deleted.set(True)
    app._load_accounts()
    assert ("GET", API + "/accounts?include_deleted=true") in stub.calls
    body = app._admin_meta["accounts"]["body"]
    assert sum(cf.row_count(t) for t in tables_in(body)) == 4
    assert has_text(body, "delta")


def test_recycle_bin_row_dimmed_and_countdown(app, stub):
    """回收站行：状态列「回收站 · X 天后自动清除」，整行取色比正常行暗一档。"""
    app._accounts_show_deleted.set(True)
    app._load_accounts()
    body = app._admin_meta["accounts"]["body"]
    assert has_text(body, "回收站 · ") and has_text(body, "天后自动清除")
    assert has_text(body, sc.PURGE_DUE_TEXT) is False
    t = app.theme
    by_name = {}
    for tbl in tables_in(body):
        for child in walk(tbl):
            if isinstance(child, tk.Label):
                by_name.setdefault(str(child.cget("text")), []).append(child)
    assert str(by_name["delta"][0].cget("fg")).lower() == str(t.text_muted).lower(), \
        "回收站行的用户名没压暗"
    assert str(by_name["alpha"][0].cget("fg")).lower() == str(t.text).lower()


def test_search_filters_rendered_rows(app, stub):
    app._load_accounts()
    body = app._admin_meta["accounts"]["body"]
    assert sum(cf.row_count(t) for t in tables_in(body)) == 3
    # 搜索框走 <KeyRelease> 重渲染（与开关页同一口径），测试里直接设值后手动渲染一步
    app._accounts_search.set("gamma")
    app._render_accounts()
    body = app._admin_meta["accounts"]["body"]
    assert sum(cf.row_count(t) for t in tables_in(body)) == 1
    assert has_text(body, "gamma") and not has_text(body, "alpha")
    # 关键字谁都不匹配：给"没有匹配"的追加式提示，且不许覆盖页头的"共 N 个账号"计数
    app._accounts_search.set("zzz")
    app._render_accounts()
    body = app._admin_meta["accounts"]["body"]
    assert has_text(body, "没有匹配")
    assert "共 3 个账号" in str(app._admin_meta["accounts"]["status"].cget("text"))
    app._accounts_search.set("")
    app._render_accounts()
    assert sum(cf.row_count(t) for t in tables_in(app._admin_meta["accounts"]["body"])) == 3


def test_toolbar_switch_is_self_drawn(app):
    """工具条开关沿用 W1 自绘口径：不许长出原生 Checkbutton。"""
    bar = app._admin_meta["accounts"]["body"].master
    switches = [w for w in walk(bar) if isinstance(w, sc.CUI.Switch)]
    assert switches, "回收站开关不是自绘 Switch"
    assert not [w for w in walk(bar) if isinstance(w, (tk.Checkbutton, tk.Radiobutton))]
    assert any(isinstance(w, tk.Entry) for w in walk(bar)), "筛选框没长在工具条里"


# ── 行 ⋯ 菜单：正常账号 / 回收站行 ─────────────────────────────────

def _menu_labels(menu):
    """`index("end")` 是**最后一项的下标**（不是条目数），所以 range 要 +1，否则永远漏掉末项。"""
    out = []
    for i in range(menu.index("end") + 1):
        if menu.type(i) == "command":
            out.append(str(menu.entrycget(i, "label")))
    return out


def test_normal_row_menu_has_danger_delete(app, stub):
    m = app._build_account_menu(dict(NORMAL_ROW))
    labels = _menu_labels(m)
    assert "删除账号…" in labels, labels
    assert "立即清除" not in labels and "恢复（移出回收站）" not in labels
    assert "禁用账号" in labels and "设为控制台管理员" in labels
    t = app.theme
    idx = [i for i in range(m.index("end") + 1)
           if m.type(i) == "command" and str(m.entrycget(i, "label")) == "删除账号…"][0]
    assert str(m.entrycget(idx, "foreground")).lower() == str(t.btn_danger_fg).lower(), \
        "危险项没走危险色"
    m.destroy()


def test_recycle_bin_row_menu_replaces_admin_actions(app, stub):
    m = app._build_account_menu(_bin_row())
    labels = _menu_labels(m)
    assert labels == ["恢复（移出回收站）", "立即清除", "查看清除结果"], labels
    # 「恢复账号」（撤销禁用）与「恢复」（移出回收站）撞名会让人误按，回收站行不再给前者
    assert "禁用账号" not in labels and "恢复账号" not in labels and "删除账号…" not in labels
    m.destroy()


def test_delete_menu_entry_calls_dry_run_and_not_delete(app, stub):
    """点「删除账号…」只许打 dry-run；真正的 POST delete 必须等到用户名匹配之后（红线）。"""
    app._open_delete_account_dialog(dict(NORMAL_ROW))
    assert ("POST", API + "/accounts/2/delete-dry-run") in stub.calls
    assert ("POST", API + "/accounts/2/delete") not in stub.calls
    win = last_dialog(app)
    assert has_text(win, "家庭根（整户数据一并带走）"), "模式没按后端给的 mode 显示"
    assert has_text(win, "1,200")
    assert has_text(win, "chat_messages")
    assert has_text(win, "审计留档"), "生效例外没显示"
    assert has_text(win, "3 行判定不出归属"), "判不出归属的行数没显示"
    assert button_named(win, "确认删除")._enabled is False, "空输入就点亮了危险按钮"


def test_delete_confirm_requires_exact_username(app, stub):
    app._show_delete_confirm(cf.delete_dry_run_payload())
    win = last_dialog(app)
    ent = entries_in(win)[0]
    btn = button_named(win, "确认删除")
    ent.insert(0, "bet")                      # 少一个字符：不点亮，也不许发请求
    assert btn._enabled is False
    ent.delete(0, "end")
    ent.insert(0, "beta")                     # 逐字符相等才点亮
    assert btn._enabled is True
    btn.command()
    assert stub.body_for("POST", API + "/accounts/2/delete") == {"confirm_username": "beta"}
    assert not dialogs(app), "删除成功后确认卡该关掉"


def test_delete_confirm_mismatch_never_posts(app, stub):
    app._show_delete_confirm(cf.delete_dry_run_payload())
    win = last_dialog(app)
    btn = button_named(win, "确认删除")
    ent = entries_in(win)[0]
    ent.insert(0, "Beta")                     # 大小写不同＝不是同一个账号，后端也一定拒绝
    assert btn._enabled is False
    btn.command()                             # 强行调用回调也不许发请求
    assert stub.body_for("POST", API + "/accounts/2/delete") is None
    assert any("用户名不匹配" in t for t in texts_of(win)), "不匹配只在按钮上拦，页面上没说为什么"


def test_delete_confirm_blocked_by_guards_shows_reason(app, stub):
    app._show_delete_confirm(cf.delete_dry_run_guarded_payload())
    win = last_dialog(app)
    assert has_text(win, "护栏：这是最后一个服务器管理员账号")
    assert has_text(win, "后端护栏已挡")
    assert button_named(win, "确认删除") is None, "护栏命中还给确认按钮"
    # 挡住就不给输入框：留一个点不亮的按钮只会诱导人硬闯
    assert entries_in(win) == []


# ── 恢复 / 立即清除 / 清除报告 ─────────────────────────────────────

def test_restore_posts_restore_then_reloads(app, stub):
    before = len([1 for m, p in stub.calls if m == "GET" and p.startswith(API + "/accounts")])
    app._restore_account(4, "delta")
    assert ("POST", API + "/accounts/4/restore") in stub.calls
    after = len([1 for m, p in stub.calls if m == "GET" and p.startswith(API + "/accounts")])
    assert after == before + 1, "恢复后没刷新列表"


def test_purge_confirm_requires_username_and_forces(app, stub):
    app._open_purge_confirm_dialog(_bin_row())
    win = last_dialog(app)
    assert has_text(win, "不可恢复")
    # 倒计时按向下取整算（样本是"从现在 +5 天"，跑到这里已差几毫秒）→ 只钉档位不钉数字
    assert has_text(win, "回收站状态：") and has_text(win, "天后自动清除")
    btn = button_named(win, "立即清除")
    assert btn._enabled is False
    ent = entries_in(win)[0]
    ent.insert(0, "delta")
    assert btn._enabled is True
    btn.command()
    assert stub.body_for("POST", API + "/accounts/4/purge") == {"confirm_username": "delta",
                                                                "force": True}
    # 清除完成后自动读报告（读端就是给这一步用的），报告卡里要有逐表行数
    assert ("GET", API + "/accounts/4/purge-report") in stub.calls
    report_win = last_dialog(app)
    assert has_text(report_win, "chat_messages")
    assert has_text(report_win, "已完成")
    assert has_text(report_win, "backups/20260924.zip")
    assert has_text(report_win, "外键自检：无残留")


def test_purge_report_renders_ledger_offline(app, stub):
    app._show_purge_report(cf.purge_report_payload(), "delta")
    win = last_dialog(app)
    assert has_text(win, "状态：已完成")
    assert has_text(win, "已删除：1,200 行")
    assert has_text(win, "文件隔离：11 个文件搬进 data/trash/4")
    assert has_text(win, "向量：deleted=300")
    assert has_text(win, "前置备份包：backups/20260924.zip")
    tbl = tables_in(win)
    assert tbl and cf.row_count(tbl[0]) == 2
    assert button_named(win, "关闭") is not None


def test_purge_report_without_job_is_not_an_error(app, stub):
    """没清过＝200 + job:null（后端刻意不 404）；界面显示「尚无清除记录」，不许报错样。"""
    app._show_purge_report({"user_id": 2, "job": None}, "beta")
    win = last_dialog(app)
    assert has_text(win, "尚无清除记录")
    assert not tables_in(win)


def test_purge_report_running_shows_progress(app, stub):
    """半删（running）不许粉饰：状态给「清除中」，进度与挡点照实显示。"""
    payload = cf.purge_report_payload()
    payload["job"]["status"] = "running"
    payload["job"]["error"] = None
    payload["job"]["finished_at"] = None
    payload["report"] = None
    payload["cursor"]["stages_done"] = ["backup_zip", "frozen"]
    payload["cursor"]["next_stage"] = "files"
    payload["cursor"]["blocked_reason"] = "宽限期未到"
    app._show_purge_report(payload, "delta")
    win = last_dialog(app)
    assert has_text(win, "清除中")
    assert has_text(win, "下一步：文件隔离")
    assert has_text(win, "挡在哪：宽限期未到")
    assert has_text(win, "chat_messages"), "报告没落地时也要从进度里列出已删表"


def test_purge_report_survives_broken_report_json(app, stub):
    """后端降级回 report=null + report_raw：控制台照旧显示账本状态，不许抛异常。"""
    payload = cf.purge_report_payload()
    payload["report"] = None
    payload["report_raw"] = "{truncated"
    app._show_purge_report(payload, "delta")
    win = last_dialog(app)
    assert has_text(win, "账本报告未能解析")
    assert has_text(win, "状态：已完成")


def test_report_loader_goes_through_admin_request_only(app, stub):
    """只读走 GET：不许出现任何 DELETE / 直连库（sqlite3 在桩里一调就抛）。"""
    app._load_purge_report(4, {"username": "delta"})
    assert ("GET", API + "/accounts/4/purge-report") in stub.calls
    assert [m for m, _p in stub.calls if m not in ("GET", "POST")] == []
    assert all("DELETE" not in p.upper() for _m, p in stub.calls)


# ── 弹窗滚动（实机反馈：内容明明没显示全，却怎么滚都没反应）────────────


def test_delete_confirm_body_is_scrollable_and_footer_pinned(app):
    """删除确认卡：正文登记进 App 级滚轮派发，确认输入区钉在底部不随正文滚走。"""
    before = list(app._scroll_canvases)
    app._show_delete_confirm(cf.delete_dry_run_payload())
    win = last_dialog(app)
    fresh = [c for c in app._scroll_canvases if c not in before]
    assert len(fresh) == 1, "弹窗正文应新登记 1 个滚动画布，实际 %d 个" % len(fresh)
    body = fresh[0]
    assert app._scroll_canvases.index(body) == len(app._scroll_canvases) - 1,         "弹窗必须晚于页面登记，App 级兜底派发才会把它当成最上层那个"

    foot = app._dialog_footer(win)
    ent = entries_in(win)
    assert ent, "确认输入区没渲染出来"
    assert ent[0] in walk(foot), "确认输入区被塞进可滚动正文里：正文一长它就被顶出屏幕外"

    win.ambrace_body_sync()
    assert int(body.cget("height")) <= sc.CUI.px(sc.DIALOG_BODY_MAX_H), "正文高度没封顶"
    region = str(body.cget("scrollregion") or "").split()
    assert len(region) == 4 and float(region[3]) > 0, "正文没有算出 scrollregion＝滚不动"
    win.destroy()


def test_purge_now_dialog_also_scrollable(app, stub):
    """「立即清除」确认卡同口径：它比删除确认更长（账本逐表行数）。"""
    before = list(app._scroll_canvases)
    app._open_purge_confirm_dialog(dict(NORMAL_ROW, deleted_at="2026-09-24T10:00:00"))
    win = last_dialog(app)
    assert len([c for c in app._scroll_canvases if c not in before]) == 1
    assert entries_in(win)[0] in walk(app._dialog_footer(win))
    win.destroy()
