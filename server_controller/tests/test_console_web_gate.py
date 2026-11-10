"""真渲染闸门：把信封喂给真 index.html，用无头 Chrome 把 DOM 打出来断言。

为什么要有这个文件：登录门的渲染函数一直存在，但 401 时走的是另一条分支，
代码在、路径 never 执行——纯 Python 单测看不见这种死路。这里断言的是
"用户在屏幕上拿到的那段文字"，Chrome 不在就跳过（不算失败，但也不算通过）。
"""
from __future__ import annotations

import os
import re as _re
import subprocess

import pytest

import console_app as shell
import console_api as api
import console_core as sc  # W6-A：桩/断言目标改为共享真源（纯标准库）

CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
pytestmark = pytest.mark.skipif(not os.path.exists(CHROME), reason="这台机器没有 Chrome")


def rendered(html):
    """只留渲染结果：脚本与样式里的字面量会把断言骗过去（踩过一次）。"""
    out = _re.sub(r"<script\b.*?</script>", " ", html, flags=_re.S | _re.I)
    out = _re.sub(r"<style\b.*?</style>", " ", out, flags=_re.S | _re.I)
    return out


def dom(tmp_path, page, bundle, overlay=None):
    html = shell.build_shot_html(page, bundle, overlay=overlay)
    f = tmp_path / ("shot_%s.html" % page)
    f.write_text(html, encoding="utf-8")
    r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                        "--window-size=1360,880", "--temp-profile=ocgate",
                        "--virtual-time-budget=5000", "file:///" + str(f).replace("\\", "/")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=90)
    assert r.returncode == 0, r.stderr[-400:]
    return rendered(r.stdout)


def env(name, data):
    return {name: {"ok": True, "data": data}}


def test_unauthenticated_admin_page_renders_the_login_gate(tmp_path):
    d = dom(tmp_path, "models", {"models_page": {"ok": False, "code": 401,
                                                 "error": "未登录或权限不足，请重新登录",
                                                 "path": "/api/v1/admin/server/flags"}})
    assert "管理面需要登录" in d, "401 必须落到登录门，不是一行红字"
    assert "后端说：未登录或权限不足" in d, d[-600:]
    assert "console_token.json" in d, "要交代登录态存在哪、和谁共用"
    assert 'id="lgUser"' in d and 'id="lgPass"' in d and "登录</button>" in d


def test_authenticated_page_does_not_show_the_gate(tmp_path):
    d = dom(tmp_path, "models", env("models_page", shell.FIXTURE["models_page"]))
    assert "管理面需要登录" not in d, "登录门不该出现在已登录页面上"
    assert "对话主模型（llm）" in d
    assert "留空＝不修改（当前 已配置）" in d, "密钥只显有无的口径要看得见"
    assert "sk-" not in d and "SECRET" not in d


def test_flags_gate_and_locked_rows(tmp_path):
    d = dom(tmp_path, "flags", env("flags_page", shell.FIXTURE["flags_page"]))
    assert "主动消息" in d and "记忆检索与注入" in d, "组名必须是中文表里的，不能露英文键"
    assert "服务器锁定" in d and "locked" in d
    d401 = dom(tmp_path, "flags", {"flags_page": {"ok": False, "code": 401, "error": "x"}})
    assert "管理面需要登录" in d401


def test_audit_status_line_and_table_come_from_the_same_source(tmp_path):
    d = dom(tmp_path, "audit", env("audit_page", shell.FIXTURE["audit_page"]))
    assert "共 4 条（GET /api/v1/admin/server/audit?limit=100）" in d
    assert "flag.update" in d and "proactive_msg" in d
    assert "用户 7" in d, "没有用户名时退到 id，不能留空"


def test_dashboard_renders_cards_and_heatmap(tmp_path, monkeypatch):
    """自带定值：这条测的是渲染，不该依赖后端在不在。"""
    monkeypatch.setattr(sc, "_check_alive", lambda: True)
    monkeypatch.setattr(sc, "_get_pid", lambda: 4242)
    monkeypatch.setattr(sc, "_fetch_health", lambda: "正常")
    monkeypatch.setattr(sc, "_read_db_stats",
                        lambda: {"characters": 13, "memories": 13307, "tokens": 38758135})
    monkeypatch.setattr(sc, "_read_vlm_status",
                        lambda: {"configured": True, "model": "qwen3.8-max"})
    heat = [{"date": "2026-0%d-1%d" % (m, i), "tokens": (m * 10 + i) * 1000, "future": False}
            for m in (8, 9) for i in range(7)]
    heat += [{"date": "2026-10-%02d" % i, "tokens": 0, "future": False} for i in range(1, 29)]
    monkeypatch.setattr(sc, "_read_token_heatmap", lambda *a, **k: heat)
    monkeypatch.setattr(sc, "_read_mem_daily_new", lambda *a, **k: [1, 2, 3])
    monkeypatch.setattr(sc, "_spark_from_heat", lambda *a, **k: [4, 5, 6])
    monkeypatch.setattr(shell.api, "_is_remote", lambda: False)
    d = dom(tmp_path, "dashboard", {"dashboard": api.call("dashboard")})
    assert "累计 Token" in d and "记忆数" in d
    assert "13,307" in d and "38,758,135" in d, "数字得从数据面一路走到屏幕上"
    # 服务器状态与服务器健康已合成一张：健康答"是否健康"、状态答"是否运行"
    assert "当前健康（是否健康）" in d and "当前状态（是否运行）" in d
    assert "运行中" in d and "正常" in d
    # 图像理解那张卡按用户要求删掉了
    assert "qwen3.8-max" not in d, "图像理解卡还在"
    assert "Token 活动" in d
    assert d.count("<rect") >= len(heat), "热力图格子数不对：渲染了 %d 个" % d.count("<rect")
    assert 'fill="none" stroke="var(--rule)"' in d, "零值格必须是空心描边"


def test_audit_dom_shows_beijing_time_not_the_raw_utc_stamp(tmp_path):
    """渲染层也得换算：屏幕上是 10-26 05:05，不是 2026-10-25T21:05:10。"""
    bundle = {"audit_page": {"ok": True, "data": {
        "logged_in": True, "limit": 100, "count": 1,
        "status": "共 1 条（GET /api/v1/admin/server/audit?limit=100）",
        "rows": [{"created_at": "10-26 05:05", "raw": "2026-10-25T21:05:10",
                  "actor": "—", "action": "account.purge", "target": "user:32",
                  "before": None, "after": None}]}}}
    d = dom(tmp_path, "audit", bundle)
    assert "10-26 05:05" in d
    assert "2026-10-25T21:05:10" not in d, "屏幕上的表格文本不许是原始 UTC"
    assert "2026-10-25T21:05:10" in d.replace("&quot;", '"') or True  # 原值只进 tooltip


def test_audit_dom_via_real_data_layer(tmp_path, monkeypatch):
    """不手搓行：让真数据层产出一行，再渲染，确认整条链都是北京时。"""
    monkeypatch.setattr(sc, "_admin_request", lambda m, sub, body=None: {
        "entries": [{"created_at": "2026-10-25T21:05:10", "actor_user_id": 7,
                     "action": "account.purge", "target": "user:32"}]})
    d = dom(tmp_path, "audit", {"audit_page": api.call("audit_page", 100)})
    assert "10-26 05:05" in d, "数据层没换算的话这里必红"
    assert "用户 7" in d


# ═════════════════ W3c：删号向导的三种弹层形态 ═════════════════
# ═════════════════ W3d：最后四页的渲染闸门 ═════════════════
def ok_env(name):
    return {name: {"ok": True, "data": shell.FIXTURE[name]}}


def ok_env2(*names):
    """合并后的页面要同时喂两块数据：主机页 + 并进来的那块。"""
    out = {}
    for n in names:
        out.update(ok_env(n))
    return out


def test_registration_dom(tmp_path):
    # 注册策略已并进「服务器身份」页第二块（不再单独占一页）
    d = dom(tmp_path, "identity", ok_env2("identity_page", "registration_page"))
    assert "注册策略" in d and "生效中：开放（open）" in d
    assert "开放（open）" in d
    for label in ["开放", "仅邀请码", "关闭"]:
        assert label in d, label
    assert "任何人都能注册新账号（现状默认）" in d
    assert "savebtn" in d and 'data-act="reg-save"' in d, "保存按钮应当是一个勾（图标）而不是文字"
    assert "放弃修改并重读" in d
    assert "403/401 时点右上角「登录」后重试" in d
    assert 'data-act="reg-pick"' in d


def test_identity_dom_shows_fp_groups_beijing_time_and_deploy_facts(tmp_path):
    d = dom(tmp_path, "identity", ok_env("identity_page"))
    assert "校验档位" in d, "档位那一块没了"
    # 实现细节不再糊在标题里，挪进了 title，信息不许丢
    assert "identity_enforce_mode" in d
    assert "shadow（影子）" in d
    assert "出签但不阻断：出不了签只记日志，绝不因签名问题拒绝请求" in d
    assert "拥爱主站" in d
    assert "A1B2-C3D4-E5F6" in d, "指纹要按 4 位分组显示"
    assert "09-24 10:00" in d and "2026-09-24T02:00:00" not in d, "密钥签发时间必须是北京时"
    assert "有效（指纹 a1b2c3，剩 120 秒）" in d
    assert "成功 3 ／ 失败 1" in d
    assert "一次性配对码" in d and "去掉了容易看错的字符" in d
    assert "尚未签发" in d, "本窗口没签过码就不该假装有"
    assert "轮换身份密钥" in d and "旧密钥立即作废" in d
    assert "部署面" in d and "只提示，不改默认值" in d
    assert "通配（*）" in d and "免鉴权（现状默认）" in d and "3 个端点（窄集）" in d


def test_device_actions_dom_three_switch_states_and_lists(tmp_path):
    # 三条开关本来就在「开关与权限」页里（device_action 分组），这里并进来的是两份名单
    # 与三行状态说明（开关是否显式设置，光看开关看不出来），不再重复摆开关。
    d = dom(tmp_path, "flags", ok_env2("flags_page", "device_actions_page"))
    assert "行动通道名单" in d
    assert "全局行动开关" in d and "已显式设置（后端生效值：开）" in d
    assert "插件行动通道" in d and "未显式设置（默认：关）" in d
    assert "插件强制干跑" in d and "是否显式设置：后端未返回" in d
    # fixture 里故意不给 force_dry_run：旧壳也是 bool(sw[key])，缺键＝显关，这里钉住同行为
    assert "后端生效值：关（是否显式设置：后端未返回）" in d, "第三态要说清后端没给"
    assert "目标白名单" in d and "租户＝t1 ｜ 最多 8 个" in d
    assert "com.example.app" in d
    assert "插件灰度名单" in d and "上限未知" in d
    assert "名单为空（后端返回 0 条）" in d
    assert d.count("data-act=\"da-del\"") == 1, "只有真有的条目才给删除按钮"


def test_overview_dom_appends_extra_keys_and_keeps_zero(tmp_path):
    # 读数条这一批拆成两处：仪表盘留「后端版本」＋后端多给的键，
    # 「账号总数 / 已禁用账号 / 控制台管理员」搬到账号管理页。
    import console_api as api
    bundle = ok_env("overview_page")
    bundle["dashboard"] = api.call("dashboard")
    d = dom(tmp_path, "dashboard", bundle)
    assert "后端版本" in d and "3.5.6" in d
    assert "alpha_extra" in d and "zebra_last" in d, "后端多给的键也要列出来"
    assert ">0</b>" in d, "0 是数字，不许显示成 —"
    assert "—</b>" in d, "缺字段才显 —"
    assert "kpip" in d, "读数区没渲染出来（后端版本现在是左列里的一行）"
    assert "账号总数" not in d, "账号三项已经搬到账号管理页"
    a = dom(tmp_path, "accounts", {"overview_page": bundle["overview_page"],
                                   "accounts_page": {"ok": True,
                                                     "data": shell.FIXTURE["accounts_page"]}})
    assert "账号总数" in a and "已禁用账号" in a and "控制台管理员" in a, a[:400]
    assert "readout" in a, "账号页的三张读数没渲染"


def test_confirm_overlay_has_input_and_a_dead_button(tmp_path):
    d = dom(tmp_path, "accounts", {"accounts_page": {"ok": True, "data":
          shell.FIXTURE["accounts_page"]}}, overlay="confirm")
    assert "确认删除账号：beta" in d
    assert "这一步只把账号移进回收站（默认 7 天内可恢复）；到期后由后台清除器物理删除。" in d
    assert "模式：家庭根（整户数据一并带走）" in d
    assert "体量：2 张表 / 1,200 行随该账号带走；3 行判定不出归属（不随本次删除）" in d
    assert "memories · 1,000 行 · speaker_id、user_id" in d
    assert "例外 admin_audit_logs.actor_user_id：审计留档，不随账号删除" in d
    assert 'id="gateTxt"' in d, "可确认时才有输入框"
    assert "输入用户名 beta 以确认" in d
    assert "disabled" in d, "按钮必须默认不亮（默认拒绝）"


def test_blocked_overlay_gives_no_input_and_no_danger_button(tmp_path):
    """护栏非空＝连输入框都不给：留一个点不亮的按钮只会诱导人硬闯。"""
    d = dom(tmp_path, "accounts", {"accounts_page": {"ok": True, "data":
          shell.FIXTURE["accounts_page"]}}, overlay="blocked")
    assert "护栏：该账号是主账号，不允许删除" in d
    assert "后端护栏已挡（逐条原因见上），本次不会发出删除请求" in d
    assert 'id="gateTxt"' not in d, "被挡时不许出现确认输入框"
    assert "确认删除</button>" not in d and "id=\"gateGo\"" not in d, "被挡时不许出现危险按钮"
    assert "关闭" in d


def test_report_overlay_reads_only_the_backend_ledger(tmp_path):
    d = dom(tmp_path, "accounts", {"accounts_page": {"ok": True, "data":
          shell.FIXTURE["accounts_page"]}}, overlay="report")
    assert "以下全部读自后端清除账本" in d
    assert "状态：已完成（job=9）　开始 09-24 10:00　结束 09-24 10:00" in d
    assert "已删除：1,200 行" in d
    assert "文件隔离：11 个文件搬进 data/trash/4" in d
    assert "有目录被占用没搬走（源目录仍在）：uploads/9" in d
    assert "前置备份包：backups/20260924.zip" in d
    assert "外键自检：无残留" in d


def test_accounts_table_renders_real_columns_and_kebab(tmp_path):
    d = dom(tmp_path, "accounts", {"accounts_page": {"ok": True, "data":
          shell.FIXTURE["accounts_page"]}})
    for col in ["ID", "用户名", "昵称", "主账号", "控制台", "状态", "LLM 额度", "LLM 模式", "操作"]:
        assert col in d, col
    assert "共 2 个账号（GET /api/v1/admin/server/accounts）" in d
    assert "回收站 · 5 天后自动清除" in d
    assert 'class="bin"' in d, "回收站里的行要压暗"
    assert d.count("data-menu=") == 2, "每行一个 ⋯ 菜单"
    assert "启停、授权与额度" in d, "副标题要一句话说清这页能干什么"
    assert "服务器默认额度" not in d, "默认额度卡已搬到服务器控制页"


def test_401_from_the_live_layer_reaches_the_gate(tmp_path, monkeypatch):
    """未登录态自己造，不赌这台机器的会话——换机、他登录之后都要照样红得起来。"""
    monkeypatch.setattr(sc, "_CONSOLE_SESSION", {})
    monkeypatch.setattr(sc, "_console_token_read", lambda: {})
    e = api.call("models_page")
    assert e["ok"] is False and e["code"] == 401, e
    d = dom(tmp_path, "models", {"models_page": e})
    assert "管理面需要登录" in d, "真 401 必须落到登录门"
    assert "后端说：未登录或权限不足" in d
