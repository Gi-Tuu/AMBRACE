"""纯逻辑自检：字号阶梯 / 组件皮肤禁令 / 色阶梯 / 判空口径。

这些断言刻意**不起窗口**：它们锁的是"W1 改完之后不许再退回去"的口径，
跑得快、任何机器（含无显示环境）都能跑。真构造控件的那部分在 `test_console_smoke.py`。
"""
from __future__ import annotations

import inspect
import pathlib
import re

import conftest as cf  # noqa: F401  （触发 sys.path 装配，本文件只用 sc）
import server_controller as sc

SRC = pathlib.Path(cf.CONSOLE_DIR, "server_controller.py").read_text(encoding="utf-8")


# ── W1.2 字号阶梯：一处真源，全文件不许再出现裸字号 ──────────────────

def test_font_tokens_are_a_ladder():
    """四档（标题/正文/次要/数值）必须落在设计系统的阶梯上，且彼此不等。"""
    sizes = {sc.FS_TITLE, sc.FS_BODY, sc.FS_CAPTION, sc.FS_NUM}
    assert len(sizes) == 4, "四档字号退化成了同一档"
    for role in sizes:
        assert role in sc.CUI.TYPE, "%s 不是 CUI.TYPE 里的档位" % role
    assert sc.CUI.TYPE[sc.FS_TITLE] > sc.CUI.TYPE[sc.FS_BODY] > sc.CUI.TYPE[sc.FS_CAPTION]
    assert sc.SEG_LABEL_PAD > 0


def test_no_bare_font_size_left():
    """全量替换的机械锁：裸字号 / 裸字体元组 / 把档位名写成数字，一律算回归。"""
    banned = {
        r"font_size\s*=\s*\d": "RoundedButton 只接受 font_role=FS_*（档位名），不接受点数",
        r"font\s*=\s*\(" : "字体一律走 CUI.f(档位)，不许手写元组",
        r"font\s*=\s*CUI\.f\(\s*\d": "CUI.f 的第一参数是档位名，不是数字",
    }
    for pat, why in banned.items():
        hits = [ln for ln, line in enumerate(SRC.splitlines(), 1) if re.search(pat, line)]
        assert not hits, "%s —— 命中行 %s：%s" % (why, hits, pat)


def test_every_font_role_resolves():
    """文案里写的档位名必须真实存在（含 FS_* 常量间接引用的）。"""
    inline = set(re.findall(r'CUI\.f\(\s*"([A-Za-z_0-9]+)"', SRC))
    inline |= set(re.findall(r'CUI\.TYPE\[\s*"([A-Za-z_0-9]+)"', SRC))
    assert inline, "没扫到任何档位引用，说明扫描式失效了"
    unknown = inline - set(sc.CUI.TYPE)
    assert not unknown, "引用了不存在的字号档位：%s" % sorted(unknown)
    consts = set(re.findall(r"\b(FS_[A-Z_]+)\b", SRC))
    for name in consts:
        if name == "FS_" or not hasattr(sc, name):
            continue
        assert getattr(sc, name) in sc.CUI.TYPE, "%s 指向了不存在的档位" % name
    assert consts >= {"FS_TITLE", "FS_BODY", "FS_CAPTION", "FS_NUM"}


def test_rounded_button_signature_has_no_font_size():
    params = inspect.signature(sc.RoundedButton.__init__).parameters
    assert "font_size" not in params
    assert params["font_role"].default == sc.FS_BODY


# ── W1.1 组件皮肤：原生勾选/单选控件不得回潮 ─────────────────────────

def test_no_native_toggle_widgets_in_source():
    for pat in (r"tk\.Checkbutton\s*\(", r"ttk\.Checkbutton\s*\(", r"tk\.Radiobutton\s*\("):
        hits = [ln for ln, line in enumerate(SRC.splitlines(), 1) if re.search(pat, line)]
        assert not hits, "原生方框控件回潮（%s）行号 %s" % (pat, hits)
    assert "LabeledSwitch(" in SRC and "class LabeledSwitch" in SRC
    assert SRC.count("CUI.Switch(") >= 2, "自绘开关至少覆盖 开关页 与 LabeledSwitch 内部"


def test_admin_pages_share_one_scroll_container():
    """7 张管理页与仪表盘/服务器页共用 `_scroll_page`（视口自适应只有一份实现）。"""
    assert "return self._scroll_page(title, subtitle)" in inspect.getsource(
        sc.ControllerApp._make_admin_page)
    body = inspect.getsource(sc.ControllerApp)
    assert body.count("self._make_admin_page(") >= 7
    assert "_admin_note" not in body, "旧的散装提示函数应已被 `_admin_state` 取代"


def test_row_bg_zebra_and_tint():
    t = sc.THEMES["aurora"]
    even = sc._row_bg(t, 0)
    odd = sc._row_bg(t, 1)
    assert even == t.card and odd != even
    assert sc._row_bg(t, 2) == even
    locked = sc._row_bg(t, 0, t.warning)
    assert locked != even, "状态染色没生效"
    assert sc._row_bg(t, 0, t.warning, 0.0) == even, "ratio=0 应当等于不染"


# ── W1.5 热力图：0 档必须"看得见"，色阶必须单调 ──────────────────────

def _lum(color: str) -> float:
    r, g, b = sc.CUI.hex_rgb(color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _heat_colors(theme_name: str) -> list:
    fake = type("_T", (), {"theme": sc.THEMES[theme_name]})()
    return sc.ControllerApp._heat_cell_colors(fake)


def test_heat_ladder_is_visible_in_every_theme():
    for name in sc.THEMES:
        colors = _heat_colors(name)
        assert len(colors) == 5 and len(set(colors)) == 5, "%s 档色阶有重复色" % name
        card = sc.THEMES[name].card
        # 0 档与卡片底的可辨距离：肉眼能分出"当天没有量"和"这块没画"
        assert abs(_lum(colors[0]) - _lum(card)) >= 8, "%s 主题 0 档仍与卡片同色" % name
        ramp = [_lum(c) for c in colors]
        deltas = [b - a for a, b in zip(ramp, ramp[1:])]
        # 亮色主题 accent 比卡片暗、暗色主题相反 —— 方向不固定，但必须一路单向、不能折返
        assert all(d > 0 for d in deltas) or all(d < 0 for d in deltas), \
            "%s 色阶不单调：%s" % (name, [round(d, 1) for d in deltas])
        assert min(abs(d) for d in deltas) >= 6, \
            "%s 有相邻两档糊在一起（最小步长 %.1f）" % (name, min(abs(d) for d in deltas))
        assert colors[4] == sc.THEMES[name].accent.upper() or colors[4] == sc.THEMES[name].accent


# ── W0 §2.2 判空口径：None 不许显示成 "None" ─────────────────────────

def test_missing_values_render_as_placeholder():
    assert sc._llm_limit_text({"llm_total_limit": None, "llm_total_limit_source": "unset"}) \
        == "未设置"
    assert sc._llm_limit_text({"llm_total_limit": 8000, "llm_total_limit_source": "user"}) \
        == "8000（账号覆盖）"
    assert sc._registration_mode_text("") == "后端未返回 mode"
    assert sc._registration_mode_text("closed") == "关闭（closed）"
    assert "未知策略" in sc._registration_mode_text("weird")


def test_audit_value_masks_secrets_and_fits_the_cell():
    assert sc._fmt_audit_val(None) == "—"
    assert sc._fmt_audit_val({"api_key": "sk-very-secret"}) == "api_key=***"
    long_text = sc._fmt_audit_val({"note": "长" * 200})
    assert long_text.endswith("…") and len(long_text) <= 47


def test_mousewheel_is_bound_once_at_app_level():
    """滚轮只有 App 级一处绑定 + 各滚动容器登记，页内不得再自己绑/解绑。

    实机反馈（2026-09-24）：「整个控制台的滚轮都很难用，必须压在滚动条上才能拖，
    没有滚动条的页面甚至滚不了」。根因＝每个画布在 <Enter> 里全局绑滚轮、<Leave>
    里全局解绑，而指针一移进内嵌卡片（RoundedCard 本身就是 Canvas）就会给画布触发
    <Leave> ⇒ 绑定被摘掉，滚轮形同不存在；多画布时后绑的还会覆盖先绑的。

    断言刻意只看**调用形态**（unbind_all + "<MouseWheel>" 这一对），不看注释文字：
    注释里解释这段历史时必然会写到那几个字，否则这条棘轮会被自己的说明绊倒。
    """
    src = inspect.getsource(sc.ControllerApp)
    assert src.count('bind_all("<MouseWheel>"') == 1, "滚轮绑定必须只剩 App 级一处"
    assert '.unbind_all("<MouseWheel>")' not in src, "不得再在页内解绑全局滚轮"
    # 定义 1 处 + 三处滚动容器（_scroll_page / 服务器页 / 危险弹窗正文）各 1 处
    assert src.count("_register_scroll_canvas(") == 4, "滚动容器登记点数量变了，同步订正本用例"


def test_mousewheel_fallback_takes_topmost_not_only_one():
    """指针压在非画布处时的兜底：取【最后登记】的容器，不是「恰好只有一个才滚」。

    旧判定 `len(shown) == 1` 在弹窗一开就失效——页面画布仍映射 + 弹窗画布 = 2 个，
    结果页面和弹窗**谁都滚不动**（实机反馈：删除确认卡滚不动）。弹窗晚于页面登记，
    所以 `shown[-1]` 天然选中最上层那个。
    """
    src = inspect.getsource(sc.ControllerApp._on_mousewheel)
    assert "len(shown) == 1" not in src, "兜底不得要求「只有一个映射画布」"
    assert "shown[-1]" in src, "兜底应取最后登记的（＝最上层）容器"


def test_danger_dialog_is_three_pane_and_scrollable():
    """危险弹窗三段式：标题钉顶、正文可滚、确认区钉底。

    旧版整卡 `RoundedCard(fit_inner=True)` 按内容长高——删除确认卡有 20 行体量表加例外
    与提示，卡片比屏幕还高，确认区被顶出屏幕外，而弹窗**没有任何滚动容器**，
    于是"内容明明没显示全，却怎么滚都没反应"。
    """
    src = inspect.getsource(sc.ControllerApp._danger_dialog)
    # 断言查【精确调用形态】而不是裸 `fit_inner=True`：注释里解释历史时必然会写到后者，
    # 查裸串会被这条棘轮自己的说明绊倒（本用例第一版就栽在这儿）。
    assert "RoundedCard(win, t, pad=3, fit_inner=True)" not in src, \
        "弹窗整卡不得再按内容长高（会把确认区顶出屏幕）"
    assert "_register_scroll_canvas(" in src, "弹窗正文必须登记进 App 级滚轮派发"
    assert "DIALOG_BODY_MAX_H" in src, "正文高度要封顶，否则窗口比屏幕还高"
    assert 'pack(side="bottom"' in src, "确认区要先占底部，才不会被正文 expand 挤没"


def test_scroll_page_never_pins_the_content_height():
    """`_scroll_page` 不得再把 pad 的高度钉死——「铺满视口」只能由 scrollregion 兜底。

    钉高度会让内容变多时收不到 <Configure>，scrollregion 停在旧值，界面"滚不动又没显示全"。
    断言查精确写法，避免被本用例自己的说明文字绊倒（同文件滚轮那条已经栽过一次）。
    """
    src = inspect.getsource(sc.ControllerApp._scroll_page)
    assert "width=vw, height=0" in src, "pad 必须走内容自然高"
    assert "if need <= vh else 0" not in src, "不得再按视口高钉 pad"
    assert "max(need, vh)" in src, "视口铺满要改由 scrollregion 兜底"
