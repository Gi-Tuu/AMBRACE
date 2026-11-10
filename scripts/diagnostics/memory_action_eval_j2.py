# -*- coding: utf-8 -*-
"""J2 生成档评测器（2026-11-07 实现）：真实写侧链认证。

链路＝签名落库（生产签名口）→ 生成回复（计费）→ 落 AI → `extract_single`
（计费，带 source_event_uid 绑定校验）→ 读 `user_facts` 槽 → `j2_verdict`。

写侧闸：`global_user_facts`（生产已开）＋细粒度槽 flag（`user_fact_relationship`／
`user_fact_living` 等）——按数据集 `expect.slots` 的槽名补齐并记账。
独立模块的原因：run_generate_eval 的三跑守卫（_GEN_ROUNDS_IN_PROC）是进程级互斥，
J2 与 J1 不能同进程混跑；J2 单跑一档，互不干扰。
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any

import memory_action_eval as ev

EVAL_REASONING_LEVEL = ev.EVAL_REASONING_LEVEL
EVAL_CHAT_TASK = ev.EVAL_CHAT_TASK
CHAT_MAX_TOKENS = ev.CHAT_MAX_TOKENS
GENERATE_USER_ID = ev.GENERATE_USER_ID
EVAL_TEMPERATURE = ev.EVAL_TEMPERATURE
FILLER_TARGET_DEFAULT = ev.FILLER_TARGET_DEFAULT
BASELINE_FLAGS = ev.BASELINE_FLAGS


async def _persist_signed_turn(user_id: int, cid: int, case: dict) -> tuple[int, int, int]:
    """按生产口径建会话＋落库用户消息并**签名**（签名的 uid 是抽取绑定校验的锚）。

    与 ensure_chat_rows 的差别＝本函数不落 turn（turn 经 chat_store._persist_user_message
    走生产签名口），context_before 落成历史消息。
    """
    from datetime import datetime, timedelta

    from app.db.database import async_session_factory
    from app.models.chat import ChatMessage, ChatSession

    await ev._ensure_parents(user_id, cid)
    base = datetime(2026, 10, 5, 12, 0, 0)
    async with async_session_factory() as db:
        s = ChatSession(user_id=user_id, character_id=cid, title="memact-eval",
                        is_active=True, created_at=base, updated_at=base)
        db.add(s)
        await db.flush()
        for i, m in enumerate(case.get("context_before") or []):
            db.add(ChatMessage(session_id=s.id,
                               sender_type="user" if m.get("role") == "user" else "ai",
                               content=m.get("text") or "", is_read=True,
                               created_at=base + timedelta(seconds=i)))
        await db.commit()
        sid = int(s.id)
    from app.application import chat_store
    _mid, info = await chat_store._persist_user_message(
        sid, user_id, cid, str(case.get("turn") or ""), save_user_message=True)
    # source_event_uid（mse-…）＝抽取绑定校验的锚；消息 id 另返回（会话内定位用）。
    return sid, int(_mid), str(info.get("_source_event_uid") or "")


async def run_j2_eval(cases, *, user_id=GENERATE_USER_ID,
                      temperature=EVAL_TEMPERATURE,
                      filler_target=FILLER_TARGET_DEFAULT, flags=None,
                      only_cid: str = "", evidence=None) -> list[dict[str, Any]]:
    """J2 生成档主循环。rows 形状与 run_generate_eval 对齐（fold 留待 J2 三跑拍板）。"""
    todo = [c for c in cases if c.get("judge") == "J2"]
    if only_cid:
        todo = [c for c in todo if c.get("cid") == only_cid]
    prod_path = ev.prod_database_path()
    tmp = tempfile.mkdtemp(prefix="ambrace_memact_j2_")
    rows: list[dict[str, Any]] = []
    try:
        await ev._init_temp_env(tmp)
        ev.borrow_llm_config(prod_path, os.path.join(tmp, "eval.db"))
        counter = ev.install_llm_call_counter()
        base_flags = dict(BASELINE_FLAGS, **(flags or {}))
        # 生产实配钉进进程＋临时库 runtime_flags 表（2026-11-08）：评测进程不加载生产
        # runtime_flags，写侧闸走 resolve_flag 解析链（AGENTS＋DB 双层）——双层都要有值。
        _needed = {"memory_source_proof_v1": True}
        for _c in todo:
            for _k in ((_c.get("expect") or {}).get("slots") or {}):
                _needed[_k] = True
        from app.flags.agent_flags import AGENT_FLAGS as _AF
        _saved = {k: _AF.get(k) for k in _needed}
        for _k, _v in _needed.items():
            _AF[_k] = _v
        # DB 层：往临时库 runtime_flags 插行（resolve_flag 读 DB 时也能命中）
        from sqlalchemy import text as _text
        from app.db.database import async_session_factory as _asf
        async with _asf() as db:
            for _k in _needed:
                db.execute(_text(
                    "INSERT OR IGNORE INTO runtime_flags(key, enabled) VALUES (:k, 1)"),
                    {"k": _k})
            await db.commit()
        for c in todo:
            expect = c.get("expect") or {}
            slots_req = expect.get("slots") or {}
            # 细粒度槽 flag 按题面要求补齐（J2 量的是写侧链，槽关着＝链路没跑）
            run_flags = dict(base_flags)
            for k in slots_req:
                if k in run_flags:
                    run_flags[k] = True
            _case_idx = cases.index(c)
            try:
                cid, *_ = await ev.seed_library(
                    c, _case_idx, user_id, filler_target=filler_target, filler_idx=_case_idx)
                await ev.reset_user_scope(user_id, cid)
                sid, _mid, ev_uid = await _persist_signed_turn(user_id, cid, c)
                state = await ev.gen_state(c, cid, user_id, flags=run_flags,
                                           chat_rows=(sid, _mid))
                from app.agent.llm_client import chat_completion as _cc
                text = await _cc(
                    messages=state["context_messages"],
                    temperature=temperature if temperature is not None else 0.0,
                    max_tokens=CHAT_MAX_TOKENS, task=EVAL_CHAT_TASK,
                    user_id=user_id, character_id=cid)
                if isinstance(text, tuple):
                    text = text[0]
                # 落 AI 回复（生产口径：extract 的 conv 需要 user+ai 两条）
                from app.db.database import async_session_factory
                from app.models.chat import ChatMessage as _CM
                async with async_session_factory() as db:
                    db.add(_CM(session_id=sid, sender_type="ai",
                               content=text or "", is_read=True))
                    await db.commit()
                # 真实写侧抽取（计费；带绑定校验——source_id＝签名消息行 id，expected_uid＝mse 串）
                from app.memory.extractor import extract_single
                await extract_single(sid, cid, user_id,
                                     str(c.get("turn") or ""), text or "",
                                     source_id=_mid, source_event_uid=ev_uid)
                # 读 user_facts 槽 → written_slots（轮询：upsert 若走后台任务，落库有延迟；
                # 最多等 3 秒，读不到就按当时事实判——不无限等）
                from sqlalchemy import select as _sel
                from app.db.database import async_session_factory
                from app.memory.user_facts import GlobalUserFact as _GUF
                written = {}
                for _attempt in range(6):
                    await asyncio.sleep(0.5)
                    async with async_session_factory() as db:
                        grows = (await db.execute(
                            _sel(_GUF).where(_GUF.user_id == user_id))).scalars().all()
                    written = {g.slot: g.value for g in grows}
                    if written:
                        break
                v = ev.j2_verdict(expect, written)
                rows.append({"cid": c["cid"], "category": c.get("category"),
                             "judge": "J2", "pass": v["pass"], "err": v["err"],
                             "why": v["why"], "written_slots": written,
                             "expect_slots": slots_req,
                             "expect_none": list(expect.get("slots_none") or []),
                             "reply_head": (text or "")[:120], "reply_text": text or "",
                             "temperature": temperature,
                             "reasoning_level": ev.EVAL_REASONING_LEVEL})
            except Exception as e:  # noqa: BLE001 —— 单题失败不影响其余计费题
                rows.append({"cid": c["cid"], "category": c.get("category"),
                             "judge": "J2", "pass": False, "err": "E_gen_failed",
                             "why": repr(e)[:200], "written_slots": {},
                             "expect_slots": slots_req,
                             "expect_none": list((c.get("expect") or {}).get("slots_none") or []),
                             "reply_head": "", "reply_text": "",
                             "temperature": temperature,
                             "reasoning_level": ev.EVAL_REASONING_LEVEL})
        await asyncio.sleep(1.5)  # 给 fire-and-forget 的用量落库一点时间
    finally:
        for _k, _v in _saved.items():
            if _v is None:
                from app.flags.agent_flags import AGENT_FLAGS as _AF2
                _AF2.pop(_k, None)
            else:
                from app.flags.agent_flags import AGENT_FLAGS as _AF3
                _AF3[_k] = _v
        from app.db.session import engine as _eng
        try:
            await _eng.dispose()
        except Exception:  # noqa: BLE001
            pass
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return rows


def render_j2(rows) -> str:
    """J2 报表（与 run_generate_eval 的报表键对齐，字段按 J2 口径）。"""
    from collections import Counter
    total = len(rows)
    passed = sum(1 for r in rows if r.get("pass"))
    pct = f"{100.0 * passed / total:.1f}%" if total else "0"
    err_dist = ", ".join(f"{(k or '(空)')}: {v}"
                         for k, v in sorted(Counter(r.get("err") or "" for r in rows).items()))
    out = ["# 动作式记忆评测 · J2 生成档（写侧抽取认证）", "",
           f"- 题数 **{total}**；J2 通过 **{passed}／{total}** ＝ {pct}",
           f"- 错因分布 {{{err_dist}}}",
           "", "| cid | 类别 | PASS | 错因 | 判分说明 | 期望槽 | 实写槽 | 回复起头 |",
           "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append("| {cid} | {cat} | {mark} | {err} | {why} | {exp} | {got} | {head} |".format(
            cid=r["cid"], cat=r.get("category") or "", mark="✓" if r.get("pass") else "✗",
            err=r.get("err") or "—",
            why=(r.get("why") or "—")[:80],
            exp=str(r.get("expect_slots") or {})[:80],
            got=str(r.get("written_slots") or {})[:80],
            head=(r.get("reply_head") or "")[:60]))
    out.append("")
    out.append("## 逐题回复全文（取证留底）")
    out.append("")
    for r in rows:
        head = (r.get("reply_text") or "")[:400]
        out.append("- **%s**（%s／pass=%s／err=%s）：%s" % (
            r["cid"], r.get("category") or "", r.get("pass"), r.get("err") or "—", head))
    out.append("")
    return "\n".join(out)
