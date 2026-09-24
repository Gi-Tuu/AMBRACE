"""离屏冒烟：真起 Tk（`withdraw()` 不上屏）→ 逐页构造 → 逐页取数渲染。

控制台唯一的静默故障面就是"取数→渲染"这一段（W0 的账号页 0 行走的是成功分支），
而它只能靠真构造控件抓出来：`py_compile` 不执行、后端 pytest 不 import 这个文件。
所有外部依赖在 `conftest.stub` 里换掉，本文件不产生任何网络/数据库访问。
"""
from __future__ import annotations

import tkinter as tk

import conftest as cf
import server_controller as sc


def walk(w):
    yield w
    for child in w.winfo_children():
        yield from walk(child)


def labels_with_text(root, needle: str) -> list:
    return [w for w in walk(root)
            if isinstance(w, tk.Label) and needle in str(w.cget("text"))]


# ── 构造期：每一页都真的建出来 ──────────────────────────────────────

def test_all_ten_pages_built_offscreen(app):
    assert len(app._pages) == 10
    assert set(app._pages) == set(app._nav_widgets)
    for key, page in app._pages.items():
        assert page.winfo_children(), "%s 页是空的" % key
        app._select_page(key)                 # 切页不许抛


def test_no_native_checkbox_or_radio_anywhere(app):
    """W1.1：勾选/单选一律自绘，原生方框（黑底 ☑）不得再出现在任何页面。"""
    bad = [w for w in walk(app.root)
           if isinstance(w, (tk.Checkbutton, tk.Radiobutton))
           or isinstance(w, sc.ttk.Checkbutton)]
    assert not bad, "残留原生控件：%s" % [type(b).__name__ for b in bad]


def test_self_drawn_switches_exist(app):
    n = sum(1 for w in walk(app.root) if isinstance(w, sc.CUI.Switch))
    assert n >= 4, "自绘开关数量异常（应至少覆盖 开关页/服务器页/日志页/模型页）"


def test_login_state_lives_in_page_header(app):
    """W2 页头合并：登录态与页面标题在**同一条**页头里，不再单独占一张整宽卡片。"""
    lab = app._admin_meta["accounts"]["login_label"]
    head_row = lab.master.master              # 动作区 → 页头行
    texts = [str(w.cget("text")) for f in head_row.winfo_children()
             for w in f.winfo_children() if isinstance(w, tk.Label)]
    assert "账号管理" in texts, "登录态没有并进页头：%s" % texts


# ── 取数→渲染：条数必须落进表格 ─────────────────────────────────────

def test_accounts_loader_renders_every_row(app):
    """W0 回归锁：接口给 3 条，界面就得有 3 行，且不许出现"未返回任何账号"。"""
    app._load_accounts()
    status = str(app._admin_meta["accounts"]["status"].cget("text"))
    assert "共 3 个账号" in status, status
    assert not labels_with_text(app._pages["accounts"], "未返回任何账号")
    body = app._admin_meta["accounts"]["body"]
    tables = [w for w in walk(body) if isinstance(w, sc.CUI.DataTable)]
    assert sum(cf.row_count(t) for t in tables) == 3
    assert labels_with_text(body, "alpha") and labels_with_text(body, "gamma")
    # 服务器默认额度卡（同一次 loader 里顺带读的 /llm-limit）
    assert labels_with_text(body, "20000"), "额度卡没落地"


def test_flags_loader_merges_metadata(app):
    app._load_flags()
    rows = app._flags_rows
    assert len(rows) == 12
    assert rows[0]["title"], "公开端点的中文标题没合并进来"
    assert rows[0]["group"] in sc.CUI.GROUP_LABELS
    status = str(app._admin_meta["flags"]["status"].cget("text"))
    assert "共 12 个开关" in status, status


def test_other_admin_loaders_render(app):
    for key, loader, expect in (
            ("models", app._load_modalities, "共 5 个模态"),
            ("audit", app._load_audit, "共 5 条"),
            ("overview", app._load_overview, "已读取 6 项"),
            ("registration", app._load_registration, "当前 开放（open）")):
        loader()
        status = str(app._admin_meta[key]["status"].cget("text"))
        assert expect in status, "%s → %s" % (key, status)
        assert app._admin_meta[key]["body"].winfo_children(), "%s 页没渲染出内容" % key
    app._load_device_actions()
    assert app._da_rows["targets"] == ["com.example.app", "com.other.app"]
    assert app._da_rows["plugins"] == ["browser_mcp"]


def test_empty_payloads_use_the_shared_state_block(app, monkeypatch):
    """空返回不许静默：走统一三态占位（含一枚「刷新」动作），而不是留一片空白。"""
    monkeypatch.setattr(sc, "_http_json",
                        lambda method, path, body=None, token="", timeout=None: (200, {}))
    app._load_accounts()
    body = app._admin_meta["accounts"]["body"]
    texts = [str(w.cget("text")) for w in walk(body) if isinstance(w, tk.Label)]
    assert any("accounts 为空" in t for t in texts), texts
    buttons = [w for w in walk(body) if isinstance(w, sc.RoundedButton)
               and w._text == "刷新"]
    assert buttons, "空态没有重试入口"
    # 计数行仍归页面自己管（追加式提示不得把"共 N 个…"覆盖掉）
    assert "共 0 个账号" in str(app._admin_meta["accounts"]["status"].cget("text"))


def test_admin_error_state_shows_retry(app, monkeypatch):
    """404（接口未上线）才给整页错误占位；403 是"登录了但没权限"，保留上次内容不覆盖。"""
    monkeypatch.setattr(sc, "_http_json",
                        lambda method, path, body=None, token="", timeout=None:
                        (404, {"detail": "no route"}))
    app._load_overview()
    body = app._admin_meta["overview"]["body"]
    assert [w for w in walk(body) if isinstance(w, sc.RoundedButton)
            and w._text == "重试"], "错误态缺「重试」入口"
    assert labels_with_text(body, "接口未就绪")


# ── W1.4：输入框按列拉伸，值不裁字 ─────────────────────────────────

def test_model_entries_are_column_stretched_with_full_values(app):
    app._load_modalities()
    body = app._admin_meta["models"]["body"]
    entries = [w for w in walk(body) if isinstance(w, sc.ttk.Entry)]
    assert len(entries) == 5 * 3, "每个模态卡应有 模型/BaseURL/api_key 三个输入框"
    long_vals = [e.get() for e in entries]
    assert "qwen3.5-omni-plus-2026-12-01" in long_vals, "模型名被截断或没回填"
    for e in entries:
        info = e.grid_info()
        assert set(info.get("sticky", "")) == {"w", "e"}, \
            "输入框没跟随列宽拉伸（旧版按固定字符宽裁字）"
        host = e.master
        assert int(host.grid_columnconfigure(int(info["column"]))["weight"]) == 1


def test_field_grid_weights():
    """标签列按内容、输入列等权：两列 weight=1 才能把长值摊开而不是挤在一侧。"""
    cfg = {}

    class _Cols:
        def grid_columnconfigure(self, i, **kw):
            cfg[i] = kw

    sc.ControllerApp._field_grid(None, _Cols())
    assert [cfg[i]["weight"] for i in range(4)] == [0, 1, 0, 1]
    assert cfg[1]["uniform"] == cfg[3]["uniform"] == "field"


# ── 热力图：数据到位后必须真的画出格子（含 0 档与未来格）────────────

def test_heatmap_draws_every_cell_with_legend(app):
    app._heat_days = cf.heatmap_payload()
    c = app.heat_canvas
    # 离屏窗口未映射（winfo_width≈1），给画布一个固定视口，绘制路径与真机一致
    c.winfo_width = lambda: sc.CUI.px(900)
    c.winfo_height = lambda: sc.CUI.px(330)
    app._draw_heatmap()
    assert len(app._heat_cells) == 26 * 7, "格子没画满（有周不可见就是这一步没走通）"
    items = c.find_all()
    fills = {str(c.itemconfig(i, "fill")[-1]) for i in items if c.type(i) == "polygon"}
    colors = app._heat_cell_colors()
    assert colors[0] in fills, "0 档格子没被画出来"
    assert colors[4] in fills, "最高档格子没被画出来"
    texts = [str(c.itemconfig(i, "text")[-1]) for i in items if c.type(i) == "text"]
    assert "未来" in texts and "0" in texts, texts
    assert any(t in texts for t in ("周一", "周三", "周五")), "星期标签没画（左槽宽度不够）"


def test_segmented_and_labeled_switch_construct(app):
    """自绘控件的构造期（字体度量/染色）必须在真 Tk 上走通——纯逻辑测试覆盖不到。"""
    t = app.theme
    host = tk.Frame(app.root, bg=t.bg)
    var = tk.BooleanVar(value=True)
    sw = sc.LabeledSwitch(host, t, "省显存", var, bg=t.card)
    assert sw.sw._var.get() is True, "标签点击没接到同一个 BooleanVar"
    sw._toggle()
    assert sw.sw._var.get() is False, "点标签文字不生效（旧版原生勾选框整行可点，自绘后不能退化）"
    sw.set_locked(True)
    sw._toggle()
    assert sw.sw._var.get() is False, "锁定态还能改值"
    seg = sc.Segmented(host, t, [("a", "仅与默认不同"), ("b", "全部")], lambda _v: None,
                       width=None, height=26)
    assert seg.winfo_reqwidth() > 0
    seg.set_index(1)
    assert seg.idx == 1


# ── 开关页保存口径（用户已拍板：只有拨动过的行长出保存按钮）────────

def _flag_tables(app):
    return [w for w in walk(app._admin_meta["flags"]["body"])
            if isinstance(w, sc.CUI.DataTable)]


def _save_buttons(app):
    return [w for w in walk(app._admin_meta["flags"]["body"])
            if isinstance(w, sc.RoundedButton) and w._text == "保存"]


def test_flags_save_button_only_on_dirty_rows(app):
    app._load_flags()
    assert _save_buttons(app) == [], "干净行长出了保存按钮（V2a 口径：只给改过的行）"
    key = str(app._flags_rows[0]["key"])
    entry = app._flags_dirty[key]
    entry["vars"][0].set(not bool(entry["vars"][0].get()))
    app._flag_mark_dirty(key, entry["vars"])
    buttons = _save_buttons(app)
    assert len(buttons) == 1 and str(buttons[0].winfo_parent()) == str(entry["slot"]), \
        "保存按钮没长在改动那一行"


def test_flags_dirty_value_survives_rerender(app):
    app._load_flags()
    key = str(app._flags_rows[1]["key"])
    entry = app._flags_dirty[key]
    before = entry["vars"][0].get()
    entry["vars"][0].set(not bool(before))
    app._flag_mark_dirty(key, entry["vars"])
    app._flags_search.set(key)               # 搜索会整表重渲染
    app._render_flags()
    still = app._flags_dirty[key]
    assert bool(still["vars"][0].get()) == (not bool(before)), "重渲染把未保存的改动冲掉了"
    assert len(_save_buttons(app)) == 1


def test_flags_table_spans_the_card(app):
    """W1.3：名称列吃掉全部富余宽度（旧版表格只占左侧 45%，右边全空）。"""
    assert sc.FLAG_COLS[0]["weight"] >= sum(c["weight"] for c in sc.FLAG_COLS[1:])
    assert sc.FLAG_SAVE_COL == len(sc.FLAG_COLS) - 1
    app._load_flags()
    tables = _flag_tables(app)
    assert tables, "开关页没走 DataTable"
    for col in tables[0].cols:
        assert int(col["min"]) > 0 or int(col["weight"]) > 0, "列既不给最小宽也不给权重＝会被压成 0"
