# -*- coding: utf-8 -*-
"""控制台删号·第三期：i18n 回填校验 + 清除报告读端（2026-09-24 派单第 1、2 项）。

为什么两块并在同一个文件：本批白名单只放行这一个新建后端测试文件（`test_account_purge.py`
不在可改范围内），而读端与 i18n 回填同属「删号第三期」、要用同一个沙箱夹具。

覆盖派单逐条：
1. 12 条删号文案确实住进 ``app/i18n.py``，zh/en **成对且非空**；
2. **逐字不变**：与第一期/第二期两份本地表（下方快照抄自改动前的源）一字不差——
   对外错误文案口径有下游依赖（既有断言、运维脚本、控制台提示都吃它）；
3. 带占位符的 key 用 ``tr_lang(lang, key, **kw)`` 真替换，且 **zh / en 两条接口路径**
   经 HTTP 拿到的 ``detail`` / ``guards`` 与快照逐字相等；
4. 两份本地表与 ``account_purge._msg`` 原先的两级回落确实删掉了（文案只有一个真源，
   不留死代码）；
5. ``GET .../purge-report``：无账本 → 200 + ``job: null``（不是 404）；有账本 → report
   解析出逐表行数；``report_json`` 坏掉 → 不 500，降级 ``report=null`` + ``report_raw``。

口径：沙箱库/文件全部来自 ``test_account_purge.purge_env``（pytest ``tmp_path`` + 克隆库），
不碰 backend/data 与生产库；护栏类断言走的是**零写入**的 4xx 分支。
"""
from __future__ import annotations

import json
import sqlite3

import pytest
from test_account_purge import (  # fixtures/helpers 复用，避免同一套沙箱建两遍
    ACTOR_UID, OTH_UID, TGT_UID, _auth, _client, _job, _mark_deleted, _purge, purge_env)

from app.application import account_deletion, account_purge
from app.i18n import _MESSAGES, tr_lang

pytestmark = pytest.mark.slow

#: ``purge_env`` 是 pytest 按**参数名**解析的夹具，本模块从不显式引用它，ruff 的 F 类会当成
#: 未使用导入（进而把每个用例的参数判为重复定义）。这里登记进 ``__all__`` 就是告诉静态检查
#: 「这个绑定是有意的」，不改测试写法、不加 noqa 噪音。
__all__ = ["purge_env"]

#: 改动前 ``account_deletion._MESSAGES``（9 条）+ ``account_purge._MESSAGES``（3 条）的**逐字快照**。
#: 这串常量的意义就是「不许漂」：回填前后任何一字之差都算回归（en 分句拼接后的整串也一致）。
VERBATIM: dict[str, tuple[str, str]] = {
    "user_not_found": ("用户不存在", "User not found"),
    "cannot_delete_self": ("不能删除自己的账号", "You cannot delete your own account"),
    "last_server_admin": ("不能删除最后一个服务器管理员，请先授予其他账号",
                          "Cannot delete the last server admin; grant it to another account first"),
    "family_last_admin": ("家庭内至少保留一个主账号，不能删除该账号",
                          "At least one main account must remain in the family"),
    "family_root_has_members": ("该账号是家庭根，名下还有 {n} 个子账号，请先删除或转移子账号",
                                "This account is a family root with {n} sub-account(s); "
                                "delete or move the sub-accounts first"),
    "confirm_username_required": ("请填写 confirm_username 以确认删除",
                                  "confirm_username is required to confirm deletion"),
    "confirm_username_mismatch": ("确认用户名与目标账号不一致，已终止删除",
                                  "The confirmation username does not match the target account"),
    "purge_now_over_threshold": ("数据量 {rows} 行超过立即清除阈值 {limit} 行，"
                                 "请走 7 天宽限期（purge_now=false）",
                                 "{rows} rows exceed the immediate-purge threshold of {limit}; "
                                 "use the 7-day grace period instead (purge_now=false)"),
    "already_deleted": ("该账号已在回收站中", "This account is already in the recycle bin"),
    "not_in_recycle_bin": ("只能清除回收站里的账号（该账号未标记删除）；请先调 delete 标记——"
                           "force 也只对已标记删除的账号生效",
                           "Only accounts already in the recycle bin can be purged; mark it with "
                           "delete first (force applies to already-deleted accounts only)"),
    "grace_not_due": ("宽限期未到（purge_after={at}），确认要提前清除请带 force=true",
                      "Grace period has not elapsed (purge_after={at}); pass force=true "
                      "to purge now"),
    "purge_failed": ("物理清除中断（进度已落盘，可重跑续删）：{err}",
                     "Purge interrupted (progress persisted, rerun to resume): {err}"),
}


# ═══════════════════════════════════════════════════════════════════════════════
# 1/2：key 归位 + 逐字不变（纯逻辑，不需要库）
# ═══════════════════════════════════════════════════════════════════════════════

def test_all_deletion_keys_live_in_i18n():
    missing = [k for k in VERBATIM if k not in _MESSAGES]
    assert not missing, "回填漏了这些 key：%s" % missing


@pytest.mark.parametrize("key", sorted(VERBATIM))
def test_texts_verbatim_and_paired(key: str):
    """zh/en 都在、都非空，且与改动前的本地表**逐字**相等（回填最容易坏的就是这条）。"""
    assert key in _MESSAGES, key
    zh, en = _MESSAGES[key]
    assert zh.strip() and en.strip(), "%s 有空语言版本" % key
    assert (zh, en) == VERBATIM[key], "%s 文案漂了：\n出 %r\n实 %r" % (key, VERBATIM[key], (zh, en))
    # 成对：占位符集合必须两边一致，否则 en 路径会漏替换（或反之留下裸花括号）
    assert _braces(zh) == _braces(en), "%s zh/en 占位符不配对" % key


def _braces(text: str) -> set:
    import re
    return set(re.findall(r"\{(\w+)}", text))


# ═══════════════════════════════════════════════════════════════════════════════
# 3：占位符替换（tr_lang 层）
# ═══════════════════════════════════════════════════════════════════════════════

def test_placeholders_substitute_on_both_languages():
    assert tr_lang("zh", "family_root_has_members", n=3) == \
        "该账号是家庭根，名下还有 3 个子账号，请先删除或转移子账号"
    assert tr_lang("en", "family_root_has_members", n=1) == \
        VERBATIM["family_root_has_members"][1].replace("{n}", "1")
    assert tr_lang("zh", "purge_now_over_threshold", rows=2500, limit=2000) == \
        "数据量 2500 行超过立即清除阈值 2000 行，请走 7 天宽限期（purge_now=false）"
    zh = tr_lang("ZH ", "grace_not_due", at="2026-09-30T12:00:00")
    assert zh == "宽限期未到（purge_after=2026-09-30T12:00:00），确认要提前清除请带 force=true"
    en = tr_lang("en-US", "purge_failed", err="disk full")
    assert en == "Purge interrupted (progress persisted, rerun to resume): disk full"
    # 替换后不许残留裸花括号（漏传参数会静默输出 {at} 这种运维读不懂的串）
    for key, kw in (("family_root_has_members", {"n": 2}), ("grace_not_due", {"at": "x"}),
                    ("purge_now_over_threshold", {"rows": 1, "limit": 2}),
                    ("purge_failed", {"err": "boom"})):
        for lang in ("zh", "en"):
            assert not _braces(tr_lang(lang, key, **kw)), (lang, key)


def test_no_placeholder_keys_pass_through_untouched():
    for key in ("cannot_delete_self", "already_deleted", "not_in_recycle_bin"):
        assert tr_lang("zh", key) == VERBATIM[key][0]
        assert tr_lang("en", key) == VERBATIM[key][1]


# ═══════════════════════════════════════════════════════════════════════════════
# 4：本地表与两级回落已删除（真源唯一）
# ═══════════════════════════════════════════════════════════════════════════════

def test_local_message_tables_are_gone():
    assert not hasattr(account_deletion, "_MESSAGES"), "第一期本地表还在：文案会有第二真源"
    assert not hasattr(account_purge, "_MESSAGES"), "第二期本地表还在"
    # 清除器不再"先查自己再回落"：两个模块共用同一个 _msg（也就是 tr_lang 的薄封装）
    assert account_purge._msg is account_deletion._msg
    assert account_deletion._msg("en", "not_in_recycle_bin") == VERBATIM["not_in_recycle_bin"][1]
    assert account_deletion._msg("zh", "purge_failed", err="e") == \
        tr_lang("zh", "purge_failed", err="e")


# ═══════════════════════════════════════════════════════════════════════════════
# 3'：zh / en 两条接口路径返回的文案与之前逐字一致（护栏分支 = 零写入）
# ═══════════════════════════════════════════════════════════════════════════════

def test_delete_guard_messages_match_snapshot_on_both_paths(purge_env):
    c = _client()
    # 删自己：护栏 1（dry-run 不抛错，guards 里就该是快照文案）
    for lang, idx in (("zh", 0), ("en", 1)):
        r = c.post(f"/api/v1/admin/server/accounts/{ACTOR_UID}/delete-dry-run",
                   headers={**_auth(ACTOR_UID), "lang": lang})
        assert r.status_code == 200, r.text
        assert VERBATIM["cannot_delete_self"][idx] in r.json()["guards"], r.json()
    # confirm 缺失 / 不匹配：400 的 detail 逐字等于快照
    for body, key in (({"confirm_username": ""}, "confirm_username_required"),
                      ({"confirm_username": "TGT"}, "confirm_username_mismatch")):
        for lang, idx in (("zh", 0), ("en", 1)):
            r = c.post(f"/api/v1/admin/server/accounts/{TGT_UID}/delete",
                       headers={**_auth(ACTOR_UID), "lang": lang}, json=body)
            assert r.status_code == 400, (lang, body, r.text)
            assert r.json()["detail"] == VERBATIM[key][idx], (lang, body, r.text)
    # 走到这里全是 4xx：目标账号必须还活着、没进回收站
    assert _job(purge_env.dst, TGT_UID) == {}


def test_already_deleted_and_purge_messages_match_snapshot(purge_env):
    c = _client()
    marked = _mark_deleted(c).json()
    at = marked["purge_after"]
    assert at, "拿不到宽限期时间戳就没法核对 {at} 的替换"
    for lang, idx in (("zh", 0), ("en", 1)):
        r = c.post(f"/api/v1/admin/server/accounts/{TGT_UID}/delete",
                   headers={**_auth(ACTOR_UID), "lang": lang}, json={"confirm_username": "tgt"})
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == VERBATIM["already_deleted"][idx], (lang, r.text)
        # 宽限期未到（未带 force）
        r2 = _purge_lang(c, lang, TGT_UID, confirm_username="tgt")
        assert r2.status_code == 400, r2.text
        assert r2.json()["detail"] == tr_lang(lang, "grace_not_due", at=at), (lang, r2.text)
    # 未标记删除的账号：not_in_recycle_bin（force 也不开后门）
    for lang, idx in (("zh", 0), ("en", 1)):
        r3 = _purge_lang(c, lang, OTH_UID, confirm_username="other", force=True)
        assert r3.status_code == 400, r3.text
        assert r3.json()["detail"] == VERBATIM["not_in_recycle_bin"][idx], (lang, r3.text)


def _purge_lang(c, lang, uid, **body):
    return c.post(f"/api/v1/admin/server/accounts/{uid}/purge",
                  headers={**_auth(ACTOR_UID), "lang": lang}, json=body)


def test_purge_now_threshold_message_uses_real_numbers(purge_env, monkeypatch):
    """阈值文案里的 {rows}/{limit} 必须是**真实判定值**（占位符没替换或数错都算回归）。"""
    monkeypatch.setenv("ADMIN_PURGE_NOW_ROW_THRESHOLD", "1")   # 把阈值压到 1 行，必拒
    c = _client()
    for lang, idx in (("zh", 0), ("en", 1)):
        r = c.post(f"/api/v1/admin/server/accounts/{TGT_UID}/delete",
                   headers={**_auth(ACTOR_UID), "lang": lang},
                   json={"confirm_username": "tgt", "purge_now": True})
        assert r.status_code == 400, (lang, r.text)
        detail = r.json()["detail"]
        assert " 1 行" in detail or "of 1;" in detail, (lang, detail)
        assert not _braces(detail), detail
        assert detail == tr_lang(
            lang, "purge_now_over_threshold", rows=_plan_rows(), limit=1), (lang, detail)
    assert _job(purge_env.dst, TGT_UID) == {}, "被拒的 purge_now 不许留作业行"


def _plan_rows() -> int:
    """目标账号实际会被带走的行数（走 dry-run 拿，测试不自己数）。"""
    dry = _client().post(f"/api/v1/admin/server/accounts/{TGT_UID}/delete-dry-run",
                         headers=_auth(ACTOR_UID)).json()
    return int(dry["totals"]["row_count"])


# ═══════════════════════════════════════════════════════════════════════════════
# 5：报告读端 GET .../purge-report
# ═══════════════════════════════════════════════════════════════════════════════

def _report(c, uid=TGT_UID, lang="zh"):
    return c.get(f"/api/v1/admin/server/accounts/{uid}/purge-report",
                 headers={**_auth(ACTOR_UID), "lang": lang})


def test_purge_report_requires_server_admin(purge_env):
    c = _client()
    assert c.get(f"/api/v1/admin/server/accounts/{TGT_UID}/purge-report").status_code == 401
    for uid in (OTH_UID, TGT_UID):
        assert c.get(f"/api/v1/admin/server/accounts/{TGT_UID}/purge-report",
                     headers=_auth(uid)).status_code == 403, uid


def test_purge_report_without_job_is_200_null(purge_env):
    """从没清过：账本没有作业行 → 200 + job:null（「没记录」是常态，不该回 404）。"""
    c = _client()
    r = _report(c, TGT_UID)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job"] is None and body["user_id"] == TGT_UID
    assert "report" not in body and "cursor" not in body, "没有账本还硬塞空结构，控制台会显示假进度"
    # 未知 id 同样是「没有账本行」，不是 404（读端只认账本，不认账号是否存在）
    assert _report(c, 999).json()["job"] is None


def test_purge_report_parses_real_ledger(purge_env):
    """真清完之后读账本：逐表行数、文件/向量/BM25、backup_zip、外键自检、进度阶段都解析出来。"""
    c = _client()
    assert _mark_deleted(c).status_code == 200
    assert _purge(c, confirm_username="tgt", force=True).status_code == 200

    r = _report(c, TGT_UID)
    assert r.status_code == 200, r.text
    body = r.json()
    job = body["job"]
    assert job["status"] == "done" and job["user_id"] == TGT_UID
    assert job["started_at"] and job["finished_at"] and job["error"] is None

    rep = body["report"]
    assert rep["mode"] == "delete_family_root"
    assert rep["rows_deleted"] > 0 and rep["backup_zip"].endswith(".zip")
    tables = {t["table"]: t["rows"] for t in rep["tables"]}
    assert tables.get("users") == 1 and tables.get("chat_sessions", 0) >= 1, tables
    assert sum(tables.values()) == rep["rows_deleted"], "逐表行数与总行数对不上（报告自相矛盾）"
    assert rep["files"]["trash_dir"] and rep["vectors"] is not None
    assert rep["foreign_key_check_rows"] == len(rep["foreign_key_check"]) == 0

    cur = body["cursor"]
    assert cur["stages_done"] and cur["next_stage"] is None
    assert cur["rows_deleted_so_far"] == rep["rows_deleted"]
    assert {t["table"] for t in cur["tables_done"]} == set(tables)
    # 原始 cursor_json 不外发（frozen 的大数组对展示无用）：读端只发阶段摘要 + 已删表计数
    assert "cursor_json" not in body and "frozen" not in cur
    assert "report_json" not in body


def test_purge_report_matches_purge_endpoint_output(purge_env):
    """同一份报告两条出口（POST purge 回包 / GET 读端）必须一致，否则控制台显示的是二手数据。"""
    c = _client()
    assert _mark_deleted(c).status_code == 200
    posted = _purge(c, confirm_username="tgt", force=True).json()
    got = _report(c, TGT_UID).json()["report"]
    for key in ("mode", "status", "rows_deleted", "tables", "files", "vectors",
                "backup_zip", "foreign_key_check"):
        assert got[key] == posted[key], key


def test_purge_report_survives_broken_report_json(purge_env):
    """``report_json`` 坏掉（截断/非 JSON）不许 500：降级 report=null + report_raw 摘要。"""
    env = purge_env
    c = _client()
    assert _mark_deleted(c).status_code == 200
    assert _purge(c, confirm_username="tgt", force=True).status_code == 200
    con = sqlite3.connect(str(env.dst))
    try:
        con.execute(f"UPDATE {account_purge.JOB_TABLE} SET report_json = ? WHERE user_id = ?",
                    ('{"tables": [{"table": "users", "rows"', TGT_UID))   # 故意截断
        con.commit()
    finally:
        con.close()

    r = _report(c, TGT_UID)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job"]["status"] == "done", "账本状态位还得照常可读"
    assert body["report"] is None
    assert body["report_raw"].startswith('{"tables"')
    # 进度位是从 cursor_json 另算的，report 坏了也不该跟着丢
    assert body["cursor"]["stages_done"] and body["cursor"]["tables_done"]


def test_purge_report_running_shows_blocked_reason_without_fabricating(purge_env):
    """半删（running）：读端照实给状态与「下一步」，不许凭空造出一份 report。"""
    env = purge_env
    c = _client()
    assert _mark_deleted(c).status_code == 200
    con = sqlite3.connect(str(env.dst))
    try:
        con.execute(f"INSERT INTO {account_purge.JOB_TABLE} "
                    "(user_id, status, started_at, cursor_json) VALUES (?, 'running', "
                    "'2026-09-24 02:00:00', ?)",
                    (OTH_UID, json.dumps({"backup_zip": True,
                                          "tables_done": {"memories": 3},
                                          "blocked_reason": "宽限期未到"})))
        con.commit()
    finally:
        con.close()

    body = _report(c, OTH_UID).json()
    assert body["job"]["status"] == "running" and body["job"]["finished_at"] is None
    assert body["report"] is None
    # 阶段位是从 cursor 的键推出来的：backup_zip/tables_done 有值就算走过，frozen 起才算没做
    assert body["cursor"]["stages_done"] == ["backup_zip", "tables_done"]
    assert body["cursor"]["next_stage"] == "frozen"
    assert body["cursor"]["rows_deleted_so_far"] == 3
    assert body["cursor"]["blocked_reason"] == "宽限期未到"
    assert body["cursor"]["tables_done"] == [{"table": "memories", "rows": 3}]
    # 只读：这一步之后账号行、数据行、作业行数都不许变
    assert _rows_of(env.dst, "users", OTH_UID) == 1
    assert _job(env.dst, OTH_UID)["status"] == "running"


def _rows_of(db_file, table, uid) -> int:
    con = sqlite3.connect(str(db_file))
    try:
        return int(con.execute(f'SELECT COUNT(*) FROM "{table}" WHERE id = ?', (uid,)).fetchone()[0])
    finally:
        con.close()
