# -*- coding: utf-8 -*-
"""D5/D6 生成档评测器（2026-11-09 实现）。

D5＝纠错响应（supersede 链全通验证）：seed 旧记忆 → turn 纠正 → 生成（计费）→
    parse_response → save_memory → supersede 链 → 读 DB → d5_verdict。
D6＝隐私边界（abstain）：seed 无关记忆 → turn 试探 → 生成（计费）→
    parse_actions/parse_response → d6_verdict（不编造/不泄露）。

独立模块（与 J2 同架构）：进程级互斥、临时库、生产实配钉旗。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory_action_eval as ev
import memory_action_eval_j2 as _j2_base
from memory_action_eval_j2 import _persist_signed_turn


async def _run_d5_case(case, idx, user_id, temperature, flags, evidence=None) -> dict:
    """D5 单题：seed → 生成（计费）→ parse_response → save_memory → supersede → 读 DB → d5_verdict。"""
    from app.agent.llm_client import chat_completion as _cc

    # seed_ids＝seed_library 第二返回值（2026-11-10 修正：此前 `cid, *_=` 丢弃后判分读
    # 不存在的 `_db_id` 标注 ⇒ old_status 恒 None ⇒ E4 幻影——三轮计费判定全部作废）。
    cid, seed_ids, *_ = await ev.seed_library(case, idx, user_id,
                                              filler_target=ev.FILLER_TARGET_DEFAULT,
                                              filler_idx=idx)
    await ev.reset_user_scope(user_id, cid)
    sid, mid, ev_uid = await _persist_signed_turn(user_id, cid, case)
    state = await ev.gen_state(case, cid, user_id, flags=flags, chat_rows=(sid, mid))
    text = await _cc(messages=state["context_messages"],
                     temperature=temperature if temperature is not None else 0.0,
                     max_tokens=ev.CHAT_MAX_TOKENS, task=ev.EVAL_CHAT_TASK,
                     user_id=user_id, character_id=cid)
    if isinstance(text, tuple):
        text = text[0]
    # 落 AI 回复后走生产抽取链（含 supersede）——量"纠正是否触发取代"
    from app.db.database import async_session_factory as _asf0
    from app.models.chat import ChatMessage as _CM0
    async with _asf0() as _db0:
        _db0.add(_CM0(session_id=sid, sender_type="ai", content=text or "", is_read=True))
        await _db0.commit()
    # 回复标记路径（2026-11-10 补全：生产 nodes.py 同款）——上一版只跑 extract_single，
    # 漏掉模型自写【记忆：】标记的落库（生产由 parse_response→save_memory 处理），
    # d5s01 的「用户已经搬到城西」因此被误判 E4。本段按 nodes.py 语义逐字复刻：
    # parse_response → resolve_speaker_from_content → save_memory(source="chat")。
    marker_saved: list[str] = []
    try:
        from app.agent.response_parser import parse_response as _pr
        from app.memory.speaker import resolve_speaker_from_content as _rsv
        _st = {"user_message": str(case.get("turn") or ""), "user_id": user_id,
               "character_id": cid, "source_id": mid, "new_memories": []}
        _st = _pr(text or "", _st)
        from app.memory.service import save_memory as _sm
        for _mem in _st.get("new_memories") or []:
            _spk, _spk_id, _epi = _rsv(_mem.get("content") or "",
                                       _st.get("user_message") or "",
                                       _st.get("ai_response") or "",
                                       user_id, cid)
            await _sm(user_id=user_id, character_id=cid,
                      memory_type=_mem["type"], title=_mem.get("title", ""),
                      content=_mem["content"], importance=_mem.get("importance", 2),
                      source="chat", sub_type=_mem.get("sub_type"),
                      source_id=mid, speaker_type=_spk, speaker_id=_spk_id,
                      epistemic_status=_epi)
            marker_saved.append(str(_mem.get("content") or ""))
    except Exception as _me:  # noqa: BLE001 —— 标记路径失败不拖垮判分，留痕
        marker_saved.append(f"E_marker_failed: {repr(_me)[:120]}")
    from app.memory.extractor import extract_single
    _ext = await extract_single(sid, cid, user_id,
                                str(case.get("turn") or ""), text or "",
                                source_id=mid, source_event_uid=ev_uid)
    try:
        if isinstance(_ext, int):
            _ext_repr = [f"extract 落库 {int(_ext)} 条"]
        else:
            _ext_repr = [str(getattr(m, "content", m))[:80] for m in (_ext or [])]
    except Exception:  # noqa: BLE001
        _ext_repr = [repr(_ext)[:120]]
    # 纠错取代链（2026-11-10 方案 A，生产 chat_tooling 同款入口）：无引用纠错→
    # 检索确认取代。方案 A 验证跑必须走它，否则量不到旧值作废。
    _fb_note = ""
    try:
        from app.memory.reliability import process_feedback_signal as _pfs
        await _pfs(cid, user_id, str(case.get("turn") or ""), text or "",
                   turn_source_id=mid)
    except Exception as _fb_e:  # noqa: BLE001 —— 链失败不拖垮判分，留痕
        _fb_note = f"E_feedback_failed: {repr(_fb_e)[:120]}"
    # 读 DB：旧 seed 的 status + 新写的 memory content
    from sqlalchemy import select as _sel
    from app.db.database import async_session_factory as _asf
    from app.models.memory import Memory as _M
    async with _asf() as db:
        _gold_idx = (case.get("expect") or {}).get("gold_seed_idx", [0])[0]
        seed_id = None
        try:
            seed_id = int((seed_ids or [])[int(_gold_idx)])
        except (IndexError, TypeError, ValueError):
            seed_id = None
        if not seed_id:  # 兜底：按内容回查（seed_library 已回填不了时的保险）
            _gold_content = str(case["seeds"][int(_gold_idx)].get("content") or "")
            if _gold_content:
                _row = (await db.execute(
                    _sel(_M).where(_M.character_id == cid, _M.content == _gold_content)
                    .order_by(_M.id.asc()).limit(1))).scalar_one_or_none()
                seed_id = _row.id if _row else None
        old_m = (await db.execute(
            _sel(_M).where(_M.id == seed_id))).scalar_one_or_none() if seed_id else None
        new_ms = (await db.execute(
            _sel(_M).where(_M.character_id == cid, _M.status == "active")
            .order_by(_M.created_at.desc()).limit(5))).scalars().all()
    new_content = " ".join(str(m.content) for m in new_ms)
    old_status = old_m.status if old_m else None
    v = ev.d5_verdict(case.get("expect") or {}, {"old_status": old_status, "new_content": new_content})
    return {"cid": case["cid"], "category": case.get("category"), "judge": "D5",
            "pass": v["pass"], "err": v["err"], "why": v["why"],
            "old_status": old_status, "new_content_excerpt": new_content[:200],
            "marker_saved": marker_saved, "extract_saved": _ext_repr,
            "feedback_note": _fb_note,
            "reply_head": (text or "")[:120], "reply_text": text or "",
            "temperature": temperature, "reasoning_level": ev.EVAL_REASONING_LEVEL}


async def _run_d6_case(case, idx, user_id, temperature, flags, evidence=None) -> dict:
    """D6 单题：seed → 生成（计费）→ parse_actions/parse_response → d6_verdict。"""
    from app.agent.llm_client import chat_completion as _cc

    cid, *_ = await ev.seed_library(case, idx, user_id,
                                    filler_target=ev.FILLER_TARGET_DEFAULT, filler_idx=idx)
    await ev.reset_user_scope(user_id, cid)
    state = await ev.gen_state(case, cid, user_id, flags=flags)
    text = await _cc(messages=state["context_messages"],
                     temperature=temperature if temperature is not None else 0.0,
                     max_tokens=ev.CHAT_MAX_TOKENS, task=ev.EVAL_CHAT_TASK,
                     user_id=user_id, character_id=cid)
    if isinstance(text, tuple):
        text = text[0]
    from app.agent.actions import parse_actions
    actions = parse_actions(text or "", base_date=str(
        (case.get("time_anchor") or {}).get("as_of") or "") or None)
    chan = ev.extract_memory_channel(text or "")
    v = ev.d6_verdict(case.get("expect") or {}, actions, chan, text or "")
    return {"cid": case["cid"], "category": case.get("category"), "judge": "D6",
            "pass": v["pass"], "err": v["err"], "why": v["why"],
            "reply_head": (text or "")[:120], "reply_text": text or "",
            "temperature": temperature, "reasoning_level": ev.EVAL_REASONING_LEVEL}


async def run_d5d6_eval(cases, *, user_id=ev.GENERATE_USER_ID,
                        temperature=ev.EVAL_TEMPERATURE,
                        filler_target=ev.FILLER_TARGET_DEFAULT,
                        only_cid="", flags=None) -> list[dict]:
    """D5/D6 主循环：D5 走写侧链（含计费），D6 走纯生成。"""
    if getattr(run_d5d6_eval, "_ran", False):
        raise RuntimeError("一个进程只准跑一轮 D5/D6 评测")
    run_d5d6_eval._ran = True
    prod_path = ev.prod_database_path()
    tmp = tempfile.mkdtemp(prefix="ambrace_d5d6_")
    rows = []
    try:
        await ev._init_temp_env(tmp)
        ev.borrow_llm_config(prod_path, os.path.join(tmp, "eval.db"))
        ev.install_llm_call_counter()
        flags = dict(ev.BASELINE_FLAGS, **(flags or {}))
        # D5 需要 supersede 链＋纠错取代链——钉生产实配（方案 A 双闸同钉）
        from app.flags.agent_flags import AGENT_FLAGS as _AF
        _saved = {}
        for _k in ("memory_supersede", "memory_source_proof_v1",
                   "memory_correction_supersede_v1"):
            _saved[_k] = _AF.get(_k)
            _AF[_k] = True
        for c in cases:
            _case_idx = cases.index(c)
            temp = temperature if temperature is not None else 0.0
            try:
                if c.get("judge") == "D5":
                    r = await _run_d5_case(c, _case_idx, user_id, temp, flags)
                elif c.get("judge") == "D6":
                    r = await _run_d6_case(c, _case_idx, user_id, temp, flags)
                else:
                    continue
                rows.append(r)
            except Exception as e:
                rows.append({"cid": c["cid"], "category": c.get("category"),
                             "judge": c.get("judge"), "pass": False,
                             "err": "E_gen_failed", "why": repr(e)[:200],
                             "reply_head": "", "reply_text": "",
                             "temperature": temp,
                             "reasoning_level": ev.EVAL_REASONING_LEVEL})
        for _k, _v in _saved.items():
            if _v is None:
                _AF.pop(_k, None)
            else:
                _AF[_k] = _v
        await asyncio.sleep(1.5)
    finally:
        from app.db.session import engine as _eng
        try:
            await _eng.dispose()
        except Exception:
            pass
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return rows


def render_d5d6(rows) -> str:
    from collections import Counter
    total = len(rows)
    passed = sum(1 for r in rows if r.get("pass"))
    pct = f"{100.0 * passed / total:.1f}%" if total else "0"
    err_dist = ", ".join(f"{(k or '(空)')}: {v}"
                         for k, v in sorted(Counter(r.get("err") or "" for r in rows).items()))
    out = ["# 动作式记忆评测 · D5/D6 生成档（纠错响应＋隐私边界）", "",
           f"- 题数 **{total}**；通过 **{passed}／{total}** ＝ {pct}",
           f"- 错因分布 {{{err_dist}}}", "",
           "| cid | judge | 类别 | PASS | 错因 | 判分说明 | 回复起头 |",
           "|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append("| {cid} | {judge} | {cat} | {mark} | {err} | {why} | {head} |".format(
            cid=r["cid"], judge=r.get("judge", ""), cat=r.get("category") or "",
            mark="✓" if r.get("pass") else "✗", err=r.get("err") or "—",
            why=(r.get("why") or "—")[:80], head=(r.get("reply_head") or "")[:60]))
    out += ["", "## 逐题回复全文（取证留底）", ""]
    for r in rows:
        head = (r.get("reply_text") or "")[:400]
        out.append("- **%s**（%s／pass=%s／err=%s）：%s" % (
            r["cid"], r.get("judge") or "", r.get("pass"), r.get("err") or "—", head))
        if r.get("marker_saved"):
            out.append("  - 回复标记落库：%s" % "; ".join(str(x)[:60] for x in r["marker_saved"]))
        if r.get("extract_saved"):
            out.append("  - 抽取链落库：%s" % "; ".join(str(x)[:60] for x in r["extract_saved"]))
        if r.get("feedback_note"):
            out.append("  - 反馈链：%s" % r["feedback_note"])
    out.append("")
    return "\n".join(out)
