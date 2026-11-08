"""拥爱服务器控制台 · 网页壳（W0 钉子）。

W6-A 后数据口径统一由共享真源 `console_core` 提供（纯标准库、无 Tk），
旧 tkinter 壳与网页壳取同一批函数与常量，不会出现两套数字。

    python console_app.py                 起窗口
    python console_app.py --shot [路径]   出 PNG（真数据，同一套 CSS/JS）
    python console_app.py --theme light   临时看亮档（不写配置）
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import console_core as sc  # noqa: E402  W6-A 共享真源，只用常量与只读函数

WEB = os.path.join(HERE, "web")
CONFIG = sc.CONFIG
THEME_MAP = {"aurora": "dark", "dark": "dark", "light": "light"}


def read_theme():
    """只读旧壳的主题设置，映射到新壳的两套原值；新壳永不写这个文件。"""
    try:
        with open(CONFIG, encoding="utf-8") as f:
            name = str(json.load(f).get("controller_theme") or "aurora")
    except (OSError, ValueError):
        name = "aurora"
    return THEME_MAP.get(name, "dark")


def preferred_theme():
    """启动主题：新壳自己记的 > 旧壳配置 > 墨纸。命令行 --theme 永远在最前面。"""
    import console_desktop as cd
    saved = cd.load_state().get("theme")
    return saved if saved in ("dark", "light") else read_theme()


import console_api as api  # noqa: E402  数据面（无 Tk），与旧壳共用同一批判据


# frameless 之后系统标题栏没了，最小化/最大化/还原/关闭改由页头自绘按钮驱动。
WINDOW_ACTIONS = ("win_minimize", "win_maximize", "win_close")


class Api:
    """JS 侧唯一入口：数据名字走 console_api 白名单，GUI 动作留在这里。"""

    def __init__(self):
        self._maximized = False

    def call(self, name, *args):
        name = str(name)
        if name == "export_log_dialog":
            return self.export_log_dialog()
        if name == "drag_by":
            return self.drag_by(*args)
        if name in WINDOW_ACTIONS:
            return self.window_action(name)
        return api.call(name, *args)

    def expose(self):
        return list(api.EXPOSED) + ["export_log_dialog", "drag_by"] + list(WINDOW_ACTIONS)

    def drag_by(self, dx=0, dy=0):
        """拖动窗口：页头拖动条按下后由 JS 按增量喂进来。

        补 WS_THICKFRAME 之前，拖动是 WebView2 的 app-region 原生行为；
        补了之后那条路断了（实测拖不动），所以改成自己算。
        """
        try:
            import console_desktop as cdx
            moved = cdx.drag_window(float(dx or 0), float(dy or 0))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": -1, "error": str(e), "path": ""}
        return {"ok": bool(moved), "data": {"dx": dx, "dy": dy}, "path": ""}

    def window_action(self, name):
        """窗口动作一律回信封，绝不把异常抛到 JS 层。"""
        import webview
        win = webview.windows[0] if webview.windows else None
        if win is None:
            return {"ok": False, "code": -1, "error": "窗口还没建好", "path": ""}
        try:
            if name == "win_minimize":
                win.minimize()
            elif name == "win_maximize":
                if self._maximized:
                    win.restore()
                else:
                    win.maximize()
                self._maximized = not self._maximized
            elif name == "win_close":
                win.destroy()
            return {"ok": True, "data": {"action": name}}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": -1, "error": str(e), "path": ""}

    def export_log_dialog(self):
        import webview
        from webview import SAVE_DIALOG
        default = "ambrace-log-%s.txt" % dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            win = webview.windows[0] if webview.windows else None
            picked = win.create_file_dialog(
                SAVE_DIALOG, save_as=True, initial_file=default, allow_override=True,
                file_types=("文本文件 (*.txt)", "所有文件 (*.*)")) if win else None
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": -1, "error": "对话框打不开：%s" % e, "path": ""}
        path = (picked[0] if isinstance(picked, (list, tuple)) and picked
                else (picked if isinstance(picked, str) else ""))
        if not path:
            return {"ok": False, "code": -2, "error": "已取消", "path": ""}
        return api.write_log_export(path)


FIXTURE = {
    "accounts_page": {"logged_in": True, "include_deleted": False, "query": "",
                      "total": 2, "rows": [
            {"id": 2, "username": "beta", "nickname": "贝塔", "admin": "否",
             "console_admin": "否", "in_bin": False, "status": "正常", "status_tip": "",
             "llm_limit": "未设置", "llm_mode": "可用服务器默认", "raw": {}},
            {"id": 3, "username": "gamma", "nickname": "伽玛", "admin": "否",
             "console_admin": "否", "in_bin": True, "status": "回收站 · 5 天后自动清除",
             "status_tip": "标记删除 09-24 10:00", "llm_limit": "未设置", "llm_mode": "—",
             "raw": {}}],
                      "status": "共 2 个账号（GET /api/v1/admin/server/accounts）",
                      "server_limit": {"title": "服务器默认额度（0=未设置）",
                                       "value": "999,957（全局）",
                                       "total_limit": 999957,
                                       "hint": "负数/非整数由后端拒绝（400）"},
                      "empty_kind": "", "empty_text": "",
                      "subtitle": "启停、授权与额度；判定一律以后端为准"},
    "models_page": {"logged_in": True, "rows": [
        {"key": "llm", "title": "对话主模型（llm）", "enabled": True, "provider": "deepseek",
         "daily_limit": 200, "cap": "provider deepseek · 日限额 200",
         "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1",
         "has_api_key": True, "key_hint": "留空＝不修改（当前 已配置）"},
        {"key": "vlm", "title": "视觉理解（vlm）", "enabled": True, "provider": "qwen",
         "daily_limit": None, "cap": "provider qwen · 日限额 —",
         "model": "qwen3.8-max", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
         "has_api_key": False, "key_hint": "留空＝不修改（当前 未配置）"},
        {"key": "speech", "title": "语音（speech）", "enabled": False, "provider": "—",
         "daily_limit": None, "cap": "provider — · 日限额 —", "model": "", "base_url": "",
         "has_api_key": False, "key_hint": "留空＝不修改（当前 未配置）"}]},
    "flags_page": {"logged_in": True, "flags": [], "groups": [
        {"group": "proactive", "label": "主动消息", "order": 1, "count": 3, "rows": [
            {"key": "proactive_msg", "title": "主动消息总开关", "desc": "关掉后角色不再自己开口",
             "enabled": True, "self_service": True, "server_locked": False,
             "source": "db", "scope": "user", "user_enabled": True, "visible": True, "order": 1},
            {"key": "proactive_hard_llm", "title": "硬触发用 LLM 判断", "desc": "",
             "enabled": False, "self_service": False, "server_locked": True,
             "source": "default", "scope": "system", "user_enabled": None, "visible": True, "order": 2}]},
        {"group": "memory", "label": "记忆检索与注入", "order": 2, "count": 2, "rows": [
            {"key": "memory_recall", "title": "记忆召回", "desc": "对话时检索相关记忆",
             "enabled": True, "self_service": True, "server_locked": False,
             "source": "db", "scope": "user", "user_enabled": True, "visible": True, "order": 1},
            {"key": "memory_review", "title": "记忆复盘", "desc": "夜间整理，受日限额约束",
             "enabled": True, "self_service": False, "server_locked": False,
             "source": "env", "scope": "system", "user_enabled": None, "visible": True, "order": 2}]},
        # 手机行动通道：三条开关本来就在开关页里，"行动通道"页并进来后这里还要挂两份名单
        {"group": "device_action", "label": "手机行动通道", "order": 3, "count": 3, "rows": [
            {"key": "device_actions_enabled", "title": "手机操作总开关", "desc": "",
             "enabled": True, "self_service": False, "server_locked": False,
             "source": "db", "scope": "system", "user_enabled": None, "visible": True, "order": 1},
            {"key": "device_actions_plugin_enabled", "title": "插件提交手机操作", "desc": "",
             "enabled": False, "self_service": False, "server_locked": False,
             "source": "db", "scope": "system", "user_enabled": None, "visible": True, "order": 2},
            {"key": "device_actions_force_dry_run", "title": "只看结论不动手机", "desc": "",
             "enabled": True, "self_service": False, "server_locked": False,
             "source": "default", "scope": "system", "user_enabled": None, "visible": True, "order": 3}]}]},
    "audit_page": {"logged_in": True, "limit": 100, "count": 4,
                   "status": "共 4 条（GET /api/v1/admin/server/audit?limit=100）",
                   "rows": [
        {"created_at": "2026-11-05 15:02:11", "actor": "alpha", "action": "flag.update",
         "target": "proactive_msg", "before": {"enabled": False}, "after": {"enabled": True}},
        {"created_at": "2026-11-05 14:47:03", "actor": "用户 7", "action": "account.llm_mode",
         "target": "user:7", "before": {"llm_mode": "own"}, "after": {"llm_mode": "blocked"}},
        {"created_at": "2026-11-05 13:20:55", "actor": "alpha", "action": "modality.update",
         "target": "vlm", "before": {"model": "qwen3-vl"}, "after": {"model": "qwen3.8-max"}},
        {"created_at": "2026-11-05 11:08:31", "actor": "alpha", "action": "registration.mode",
         "target": "server", "before": {"mode": "open"}, "after": {"mode": "invite_only"}}]},
}

# 四页的版式核对版：数据由真组装函数算出（判据同一条链），不是手搓字符串
FIXTURE.update({
    "registration_page": api.registration_view({"mode": "open"}),
    "identity_page": api.identity_view({
        "mode": "shadow", "fp": "a1b2c3d4e5f6", "server_name": "拥爱主站",
        "key_created_at": "2026-09-24T02:00:00",
        "pending": {"has_pending": True, "fp": "a1b2c3", "expires_in": 120},
        "pair_stats": {"pair_success": 3, "pair_fail": 1}, "bind_host": "0.0.0.0",
        "hints": {"cors_wildcard": True, "uploads_require_auth": False},
        "signed_paths": ["/a", "/b", "/c"]}),
    "device_actions_page": api.device_actions_view({
        "switches": {"global": True, "plugin_enabled": False,
                     "rows_present": {"global": True, "plugin_enabled": False}},
        "targets": ["com.example.app"], "plugins": [],
        "limits": {"targets_max": 8, "plugins_max": None}, "tenant_id": "t1"}),
    "overview_page": api.overview_view({"accounts": 12, "disabled": 1, "server_admins": 3,
                                        "flags_on": 40, "version": "3.5.6",
                                        "alpha_extra": 0, "zebra_last": None}),
})


OVERLAY_FIXTURE = {
    "confirm": {"kind": "delete", "uid": 2, "data": {
        "title": "确认删除账号：beta", "username": "beta",
        "lines": [{"kind": "warn", "text": "这一步只把账号移进回收站（默认 7 天内可恢复）；"
                                           "到期后由后台清除器物理删除。"},
                  {"kind": "info", "text": "模式：家庭根（整户数据一并带走）"},
                  {"kind": "info", "text": "体量：2 张表 / 1,200 行随该账号带走；"
                                           "3 行判定不出归属（不随本次删除）"},
                  {"kind": "row", "text": "memories · 1,000 行 · speaker_id、user_id"},
                  {"kind": "warn", "text": "例外 admin_audit_logs.actor_user_id：审计留档，不随账号删除"}],
        "can_confirm": True, "blocked_text": ""}},
    "blocked": {"kind": "delete", "uid": 1, "data": {
        "title": "确认删除账号：示例用户", "username": "示例用户",
        "lines": [{"kind": "info", "text": "体量：0 张表 / 0 行随该账号带走"},
                  {"kind": "error", "text": "护栏：该账号是主账号，不允许删除"}],
        "can_confirm": False,
        "blocked_text": "后端护栏已挡（逐条原因见上），本次不会发出删除请求"}},
    "report": {"kind": "report", "uid": 4, "data": {
        "username": "beta",
        "lines": [{"kind": "info", "text": "以下全部读自后端清除账本（GET /api/v1/admin/server/"
                                           "accounts/4/purge-report）。"},
                  {"kind": "info", "text": "状态：已完成（job=9）　开始 09-24 10:00　结束 09-24 10:00"},
                  {"kind": "info", "text": "已删除：1,200 行"},
                  {"kind": "info", "text": "文件隔离：11 个文件搬进 data/trash/4"},
                  {"kind": "warn", "text": "有目录被占用没搬走（源目录仍在）：uploads/9"},
                  {"kind": "info", "text": "前置备份包：backups/20260924.zip"},
                  {"kind": "info", "text": "外键自检：无残留"}]}},
}


def build_shot_html(page, bundle, theme="dark", overlay=None, sky=None):
    """把信封内联进真 index.html，返回一份可独立打开的 HTML（出图与测试共用）。"""
    html = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
    bundle = dict(bundle)
    bundle["_page"] = page
    if overlay:
        bundle["_ovl"] = OVERLAY_FIXTURE[overlay]
    payload = json.dumps(bundle, ensure_ascii=False).replace("</", "<\\/")
    pin = ("window.__SKY__ = %s;" % json.dumps(sky)) if sky is not None else ""
    return html.replace('<html lang="zh-CN" data-theme="dark">',
                        '<html lang="zh-CN" data-theme="%s">' % theme) \
        .replace("<script>",
                 "<script>window.__DATA__ = %s;window.__PORT__ = %s;%s</script>\n<script>"
                 % (payload, json.dumps(sc.TARGET_PORT), pin), 1) \
        .replace('let page = "dashboard"', 'let page = window.__DATA__._page || "dashboard"')


def shot(out_path, theme, page="dashboard", fixture=False, overlay=None, sky=None):
    """同一套 CSS/JS，把三页真数据一起内联，再用无头 Chrome 出图。"""
    bundle = {n: api.call(n) for n in ("dashboard", "server_page", "log_page",
                                      "models_page", "flags_page", "audit_page",
                                      "accounts_page", "registration_page", "identity_page",
                                      "device_actions_page", "overview_page")}
    if fixture:
        # 版式核对版：把成功信封的 data 换成示意数据，数字与文案不代表真实后端
        for k, v in FIXTURE.items():
            bundle[k] = {"ok": True, "data": v}
    tmp = os.path.join(WEB, "_shot.html")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(build_shot_html(page, bundle, theme, overlay, sky))
    chrome = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
    if not os.path.exists(chrome):
        print("没找到 Chrome，出图失败；窗口模式仍可用", file=sys.stderr)
        return 1
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--window-size=1360,880", "--temp-profile=ocshot",
                    "--screenshot=" + out_path, "--virtual-time-budget=5000",
                    "file:///" + tmp.replace("\\", "/")], check=True, capture_output=True)
    os.remove(tmp)
    print("shot ->", out_path)
    return 0


def run(theme, check=False):
    import threading
    import console_desktop as cd
    # 先声明 DPI 感知：否则量到的屏幕是被虚拟化过的假尺寸，记窗口/夹窗口都失真
    cd.ensure_dpi_aware()
    cd.set_app_id()  # 任务栏归到我们名下，不再顶着 pythonw
    # 再点一次图标不该多开一个窗口：叫回已有窗口就退场；记录对不上活窗口才自己顶上
    if cd.already_running() and cd.handoff_or_stay() == "handoff":
        print("已有一个控制台窗口在跑，已把它叫到前面。", flush=True)
        return 0
    geo = cd.stored_geometry()
    # 自愈：存过的几何若是哨兵坐标，stored_geometry 已把它换成居中位置——
    # 这里写回，免得毒值继续躺在状态文件里。
    cd.save_state(geometry=geo)
    import webview
    # 走 file:// URL 而不是内联 HTML：相对路径的 d2.css 与星空底板才解析得到
    # WebView2 的 file:// 不认查询串（会把 ?theme=dark 当成文件名去找），主题走片段
    url = "file:///" + os.path.join(WEB, "index.html").replace("\\", "/") + "#theme=" + theme
    win = webview.create_window(cd.TITLE, url=url, x=geo["x"], y=geo["y"],
                                width=geo["width"], height=geo["height"],
                                min_size=(cd.MIN_W, cd.MIN_H), js_api=Api(),
                                frameless=True,   # ② 系统标题栏交给页头自绘，easy_drag 只认细拖动条
                                # 拖动改由我们自己做（页头拖动条 → drag_by → SetWindowPos）：
                                # pywebview 的 easy_drag 走 window.move()，补了 WS_THICKFRAME 之后
                                # WebView2 的 app-region 原生拖动失效，而 move() 自己还有个签名坑。
                                easy_drag=False,
                                # ④ WebView2 的默认底色是白的：页面画出第一帧之前会先闪一下白，
                                # 开屏动画再好也盖不住。按当前主题给它同色底，闪的就是墨纸/纸色。
                                background_color="#f4f0e6" if theme == "light" else "#131110")

    cd.seed_geometry(geo)

    def on_shown():
        hwnd = cd.find_window()
        cd.set_window_icon(hwnd, os.path.join(HERE, "ambrace.ico"))
        # frameless 把 WS_THICKFRAME 一起摘了，边缘就拖不动；这里补回去（见 enable_resize）
        cd.enable_resize(hwnd)
        cd.save_state(hwnd=hwnd, pid=os.getpid())

    def on_moved(x, y):          # moved 事件直接给逻辑坐标，不必跨线程读窗口
        cd.remember(win, patch={"x": x, "y": y})

    def on_resized(width, height):
        cd.remember(win, patch={"width": width, "height": height})

    def on_closing():
        cd.remember(win, force=True)
        cd.forget_hwnd()

    win.events.shown += on_shown
    win.events.moved += on_moved
    win.events.resized += on_resized
    win.events.closing += on_closing
    if check:
        def watchdog():
            import time
            time.sleep(9)
            got = api.BOOT["ping"]
            print("BOOT_PING", json.dumps(got, ensure_ascii=False), flush=True)
            # 星空呼吸在真窗口里的状态：截图会被别的窗口挡住，直接问 WebView 自己
            js = ("(function(){var a=document.getAnimations().filter(function(x)"
                  "{return x.animationName==='sky-breathe'});"
                  "return {n:a.length,state:a.length?a[0].playState:'-',"
                  "t:a.length?Math.round(a[0].currentTime):-1,"
                  "op:a.length?getComputedStyle(document.getElementById('sky')).opacity:'-',"
                  "hidden:document.hidden};})()")
            try:
                print("BOOT_SKY", json.dumps(win.evaluate_js(js), ensure_ascii=False), flush=True)
            except Exception as e:  # noqa: BLE001
                print("BOOT_SKY 取不到：%s" % e, flush=True)
            cd.forget_hwnd()   # 自检是硬退出，走不到关窗那一下，别给下一个人留死句柄
            os._exit(0 if got else 1)
        threading.Thread(target=watchdog, daemon=True).start()
    webview.start(debug=("--debug" in sys.argv))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", nargs="?", const="console_shot.png", default=None)
    ap.add_argument("--theme", default=None, choices=["dark", "light"])
    ap.add_argument("--page", default="dashboard", choices=["dashboard", "server", "log", "models", "flags", "audit", "accounts",
                       "registration", "identity", "device_actions", "overview"])
    ap.add_argument("--overlay", default=None, choices=sorted(OVERLAY_FIXTURE),
                    help="把删号向导/护栏/清除账本三种弹层渲出来（出图与闸门测试用）")
    ap.add_argument("--fixture", action="store_true",
                    help="出图时用示意管理数据（管理页需要登录态，版式核对用）")
    ap.add_argument("--check", action="store_true",
                    help="起窗口并在 9 秒内等页面回敲，没等到就非零退出")
    ap.add_argument("--sky", type=float, default=None, metavar="0..1",
                    help="出图时把星空呼吸钉在这一帧（验中间态用，不影响真窗口）")
    args, rest = ap.parse_known_args()
    theme = args.theme or preferred_theme()
    if args.shot:
        return shot(os.path.abspath(args.shot), theme, args.page, args.fixture,
                    args.overlay, args.sky)
    return run(theme, check=args.check) or 0


if __name__ == "__main__":
    sys.exit(main())
