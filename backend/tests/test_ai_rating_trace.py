# -*- coding: utf-8 -*-
"""AI 评星留痕（2026-09-26 派单 Part A）：批次 + 输入摘要 + 输出星分 + 入口计数。

守的底线（禁止为绿放宽）：
1. **只加观测不改行为**：``run_ai_rating()`` 返回值、每条记忆写库字段、额度账本数值，
   在「留痕正常 / 留痕通道炸掉」两态下逐字相同；``_rate_batch`` 传不传 ``obs`` 返回值也逐字相同；
2. 一次执行＝一个批次：批次号落 ``agent_task_logs.task_id``（带索引），
   ``WHERE task_id=batch`` 能把该批输入行与输出行整体取回（＝选表理由，可复原性）；
3. 输入摘要＝候选条数 + 每条 id + 正文前 80 字（与喂模型的同一口径），每行 steps_json 必须是
   合法 JSON（≤1600，超限即截断事故）；
4. 输出留痕记 1-5 的**原始星分**（不是 ×20 的重要性），并另存模型原样返回值（可核对被裁剪的条目）；
5. 入口计数：每次调用 start/end 各一条；每个角色**无论跳过还是失败**都恰好一条结论行，
   跳过原因可区分（quota_full / no_candidates / 解析失败各型 / 调用失败）；
6. fail-open：写留痕抛异常只记 WARNING，绝不冒泡进评星主链路。

DB 用例走 tmp_path 临时库（_dbclone 模板克隆）+ patch async_session_factory，不触生产库；
额度账本文件重定向到 tmp_path，绝不碰 backend/data/。
"""
import asyncio
import json
import os
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.memory import ai_rating as ar
from app.memory import rating_quota as rq

pytestmark = pytest.mark.slow

_USER = 1
_CHAR = 13          # 有候选记忆的角色
_CHAR_IDLE = 14     # 无候选记忆的角色（走 no_candidates）
_LONG = "连" * 200  # 超长正文：验证留痕截到 80 字（与喂模型口径一致）
_FIXED_NOW = datetime(2026, 9, 26, 1, 53, 0)   # 冻结「本拍时间」，使两态写库结果可比


# ────────────────────────── 夹具 ──────────────────────────

@pytest.fixture()
def rating_db(monkeypatch, tmp_path):
    """临时库（含 user/两个角色）+ 额度账本落 tmp_path + 批次号固定。"""
    engine = clone_engine(os.path.join(str(tmp_path), "ar.db"))
    factory = make_session_factory(engine)
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(ar, "async_session_factory", factory)   # 模块级 import，须单独 patch
    monkeypatch.setattr(rq, "_STATE_FILE", tmp_path / "quota.json")
    monkeypatch.setattr(ar, "_new_batch_id", lambda now: "ar20260926015300-abc123")

    from app.models.character import AICharacter
    from app.models.user import User

    async def _init():
        async with factory() as db:
            db.add(User(id=_USER, username="ar_u1", nickname="主人"))
            db.add(AICharacter(id=_CHAR, user_id=_USER, name="小暖", is_active=True))
            db.add(AICharacter(id=_CHAR_IDLE, user_id=_USER, name="小冷", is_active=True))
            await db.commit()
    asyncio.run(_init())
    yield factory
    engine.sync_engine.dispose()


def _mem(mid, content, *, character_id=_CHAR):
    from app.models.memory import Memory
    return Memory(id=mid, user_id=_USER, character_id=character_id,
                  memory_type="event", content=content, title=f"t{mid}")


def _seed(factory, *rows):
    async def _go():
        async with factory() as db:
            for r in rows:
                db.add(r)
            await db.commit()
    asyncio.run(_go())


@pytest.fixture()
def llm(monkeypatch):
    """LLM 打桩：改 ``reply`` 决定返回文本，改 ``boom`` 决定抛异常；``prompts`` 记录送模型的正文。"""
    import app.agent.llm_client as llm_mod

    class _Stub:
        reply = '[{"id": 101, "star": 4}]'
        boom = None
        prompts: list = []

    async def _fake(messages=None, *_a, **_k):
        if _Stub.boom:
            raise _Stub.boom
        _Stub.prompts.append(messages[-1]["content"])
        return _Stub.reply

    monkeypatch.setattr(llm_mod, "chat_completion", _fake)
    return _Stub


@pytest.fixture()
def events(monkeypatch):
    """捕获 fire-and-forget 的写库入参（不落库），按 route 归类便于断言。"""
    import app.agent.trace as trace
    got: list[dict] = []
    monkeypatch.setattr(trace, "enqueue_task_log", lambda **kw: got.append(kw))
    return got


def _by_route(got, route):
    """按 route 取事件并解码 steps_json，同时带上列级字段（character_id/status）便于断言。"""
    out = []
    for ev in got:
        if ev.get("route") == route:
            payload = json.loads(ev["steps_json"])
            payload["status"] = ev.get("status")
            payload["character_id"] = ev.get("character_id")
            out.append(payload)
    return out


def _char_rows(got, character_id=_CHAR):
    return [r for r in _by_route(got, ar.TRACE_ROUTE_CHAR) if r["character_id"] == character_id]


def _run():
    async def _go():
        out = await ar.run_ai_rating()
        await _drain()
        return out
    return asyncio.run(_go())


async def _drain(timeout: float = 10.0) -> None:
    """等在途 fire-and-forget 后台任务真正结束（不用固定 sleep 赌时序）。"""
    cur = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not cur and not t.done()]
    if pending:
        await asyncio.wait(pending, timeout=timeout)


def _fetch_batch(factory, batch_id):
    """按批次号取回留痕（可复原性就走这条真实查询路径）。"""
    from app.models.agent import AgentTaskLog

    async def _go():
        async with factory() as db:
            r = await db.execute(select(AgentTaskLog).where(AgentTaskLog.task_id == batch_id)
                                 .order_by(AgentTaskLog.id))
            return [(x.route, x.trigger, x.status, x.character_id, json.loads(x.steps_json or "{}"))
                    for x in r.scalars().all()]
    return asyncio.run(_go())


def _mem_state(factory, mid):
    """评星真正写进 memories 的全部字段（两态对照用）。

    刻意不含 ``updated_at``：它由 ``server_default/onupdate=func.now()`` 走**数据库时钟**（秒级），
    与本模块冻结的 ``_now_naive`` 无关，跨两次运行必然可能差 1 秒 ⇒ 放进来只会造成假红。
    """
    from app.models.memory import Memory

    async def _go():
        async with factory() as db:
            m = await db.get(Memory, mid)
            return (m.importance, m.strength_days, m.review_count, m.ai_rated, m.delete_at,
                    m.next_review_at, m.last_reinforce_at)
    return asyncio.run(_go())


# ────────────────────────── ① 批次号 ──────────────────────────

def test_批次号可辨识带短随机且不超列宽():
    from app.utils.timeutil import now_naive_utc
    ids = {ar._new_batch_id(now_naive_utc()) for _ in range(50)}
    assert len(ids) == 50, "同一秒内多次调用也必须互不相同（时间戳 + 短随机）"
    one = next(iter(ids))
    assert one.startswith(ar.TRACE_TASK_PREFIX)
    assert len(one) <= 40, f"agent_task_logs.task_id 是 String(40)：{one}"


def test_一次执行一个批次_按批次号可整体取回(rating_db, llm):
    """真落临时库：WHERE task_id=batch 一次取回入口/输入/输出/收尾四类行（不靠 json_extract）。"""
    _seed(rating_db, _mem(101, "用户说明天要去长沙出差"), _mem(102, "用户喜欢吃香菜"))
    llm.reply = '[{"id": 101, "star": 4}, {"id": 102, "star": 2}]'
    assert _run() == 2

    rows = _fetch_batch(rating_db, "ar20260926015300-abc123")
    routes = [r[0] for r in rows]
    assert routes.count(ar.TRACE_ROUTE_RUN) == 2, "start + end 各一条"
    assert routes.count(ar.TRACE_ROUTE_CHAR) == 2, "两个角色各一条结论行"
    assert ar.TRACE_ROUTE_INPUT in routes
    assert all(r[1] == "memory_obs" for r in rows), "沿用既有 trigger 口径"
    assert all(r[4]["batch_id"] == "ar20260926015300-abc123" for r in rows), "每行都自带批次号"
    char = [r[4] for r in rows if r[0] == ar.TRACE_ROUTE_CHAR and r[3] == _CHAR][0]
    assert char["stars"] == {"101": 4, "102": 2}, "记 1-5 原始星分而非 ×20"
    assert char["outcome"] == "rated" and char["written"] == 2
    end = [r[4] for r in rows if r[0] == ar.TRACE_ROUTE_RUN and r[4]["phase"] == "end"][0]
    assert end["rated_total"] == 2 and end["outcomes"] == {"rated": 1, "no_candidates": 1}


# ────────────────────────── ② 输入摘要口径 ──────────────────────────

def test_输入摘要带候选条数与每条id加正文前80字(rating_db, llm, events):
    _seed(rating_db, _mem(101, _LONG), _mem(102, "短正文"))
    llm.reply = '[{"id": 101, "star": 5}]'
    _run()

    ins = _by_route(events, ar.TRACE_ROUTE_INPUT)
    assert len(ins) == 1
    row = ins[0]
    assert row["candidate_count"] == 2 and row["seq"] == 0
    assert [c["id"] for c in row["candidates"]] == [101, 102]
    assert row["candidates"][0]["preview"] == _LONG[:80]
    assert len(row["candidates"][0]["preview"]) == 80, "留痕口径＝喂模型口径（前 80 字，不再截狠）"
    assert _LONG[:80] in llm.prompts[0], "必须与真实 prompt 里的那 80 字逐字一致"


def test_输入摘要分行且每行JSON不被截坏(rating_db, llm, events, monkeypatch):
    """满额 10 条长正文：每行 steps_json 必须仍是合法 JSON（截断＝留痕报废）。"""
    monkeypatch.setattr(ar, "TRACE_INPUT_CHUNK", 4)
    _seed(rating_db, *[_mem(200 + i, _LONG) for i in range(10)])
    llm.reply = json.dumps([{"id": 200 + i, "star": 3} for i in range(10)])
    assert _run() == 10

    ins = _by_route(events, ar.TRACE_ROUTE_INPUT)
    assert [r["seq"] for r in ins] == [0, 1, 2], f"10 条按每行 4 条分三行：{[r['seq'] for r in ins]}"
    assert sum(len(r["candidates"]) for r in ins) == 10
    assert all(r["candidate_count"] == 10 for r in ins)
    for ev in events:
        assert len(ev["steps_json"]) <= ar.TRACE_STEPS_MAX, "超上限会被截成坏 JSON"
        json.loads(ev["steps_json"])


# ────────────────────────── ③ 入口计数与跳过原因 ──────────────────────────

def test_入口计数_每调用一次恰有一对start与end(rating_db, llm, events):
    _seed(rating_db, _mem(101, "用户不吃香菜"))
    llm.reply = '[{"id": 101, "star": 3}]'
    assert _run() == 1

    runs = _by_route(events, ar.TRACE_ROUTE_RUN)
    starts = [r for r in runs if r["phase"] == "start"]
    ends = [r for r in runs if r["phase"] == "end"]
    assert len(starts) == 1 and len(ends) == 1, "start 在查角色之前就已落，end 收尾补总量"
    assert starts[0]["ts"], "start 行带本拍时间"
    assert ends[0]["char_count"] == 2 and ends[0]["outcomes"]["no_candidates"] == 1


def test_额度已满的角色被跳过且写明原因(rating_db, llm, events):
    _seed(rating_db, _mem(101, "用户说明天要去长沙"))
    rq.add(_CHAR, ar.AI_RATING_MAX_PER_CHAR)     # 今日已评满（账本读写时机不变，留痕只读不写）
    assert _run() == 0

    assert llm.prompts == [], "额度已满时不得再调 LLM"
    assert _char_rows(events)[0]["outcome"] == "quota_full"
    assert _char_rows(events)[0]["quota_used_before"] == ar.AI_RATING_MAX_PER_CHAR
    assert _by_route(events, ar.TRACE_ROUTE_INPUT) == [], "跳过的一趟不该有输入摘要"
    assert _mem_state(rating_db, 101)[3] is False, "跳过时不得写 ai_rated"


def test_跳过与失败原因四类可区分(rating_db, llm, events):
    """无候选 / 无 JSON 数组 / JSON 坏 / 空数组（无 fail_reason）/ 调用失败 ⇒ 各自一名。"""
    _seed(rating_db, _mem(101, "用户喜欢吃香菜"))
    for reply, expect, has_head in [("not json at all", "no_json_array", True),
                                    ("[id: 101, star:]", "json_parse_error", True),
                                    ("[]", "parse_failed", False)]:
        llm.reply = reply
        # 批 A（2026-09-26）：有候选却整批失败 ⇒ 必须冒泡（原先被吞掉，上层 maintenance 的
        # 失败回拨重试因此永不触发）。留痕仍须在抛之前落，故结论行断言原样保留。
        with pytest.raises(ar.RatingPartialFailure):
            _run()
        row = _char_rows(events)[-1]
        assert row["outcome"] == expect, f"回复 {reply!r} 应判为 {expect}，实际 {row['outcome']}"
        assert row["written"] == 0 and not row.get("stars"), "解析失败不得留下星分"
        assert (("raw_head" in row) == has_head) and (("fail_reason" in row) == has_head)
        if has_head:
            assert row["raw_head"] == reply[:80] and row["raw_len"] == len(reply)
        assert row["status"] == "ok", "解析失败是「没评上」，不是留痕链路出错"

    llm.boom = RuntimeError("llm down")
    with pytest.raises(ar.RatingPartialFailure):   # 批 A：角色级异常也必须冒泡
        _run()
    row = _char_rows(events)[-1]
    assert row["outcome"] == "call_failed" and "llm down" in row["error"]
    assert row["status"] == "error"

    idle = _char_rows(events, _CHAR_IDLE)
    assert idle and all(r["outcome"] == "no_candidates" for r in idle)
    assert all("fail_reason" not in r for r in idle)


def test_留痕时机在写库之前且异常之后也必落结论行(rating_db, llm, events, monkeypatch):
    """输入摘要先于 LLM 调用；抛异常的角色也必须有结论行（finally 落，不因 continue/raise 丢失）。

    批 A（2026-09-26）口径：整批失败 / 角色级异常会自 run_ai_rating 冒泡出 RatingPartialFailure，
    但结论行在抛之前已落（收尾留痕顺序不变）。
    """
    _seed(rating_db, _mem(101, "用户说明天要去长沙"))
    seen: list[str] = []
    real_inputs = ar._trace_inputs

    def _spy_inputs(*a, **k):
        seen.append("input")
        return real_inputs(*a, **k)
    monkeypatch.setattr(ar, "_trace_inputs", _spy_inputs)

    import app.agent.llm_client as llm_mod
    real_fake = llm_mod.chat_completion

    async def _first_boom(*a, **k):
        seen.append("llm")
        raise RuntimeError("llm down")
    monkeypatch.setattr(llm_mod, "chat_completion", _first_boom)
    with pytest.raises(ar.RatingPartialFailure):   # 批 A：角色级异常也必须冒泡
        _run()
    assert seen == ["input", "llm"], f"输入摘要必须在调模型之前：{seen}"
    assert _char_rows(events)[-1]["outcome"] == "call_failed"

    monkeypatch.setattr(llm_mod, "chat_completion", real_fake)
    seen.clear()
    assert _run() == 1
    assert _char_rows(events)[-1]["outcome"] == "rated"


def test_模型漏评时written小于候选条数且可核对(rating_db, llm, events):
    _seed(rating_db, _mem(101, "用户说明天要去长沙"), _mem(102, "用户喜欢吃香菜"))
    llm.reply = '[{"id": 101, "star": 4}]'      # 102 漏评
    assert _run() == 1
    row = _char_rows(events)[0]
    assert row["outcome"] == "rated" and row["written"] == 1 and row["candidate_count"] == 2
    assert row["memory_ids"] == [101, 102]
    assert row["stars"] == {"101": 4} and row["model_stars"] == {"101": 4}


# ────────────────────────── ④ fail-open：留痕炸了不影响评星 ──────────────────────────

def test_留痕通道炸掉_返回值与写库逐字相同(rating_db, llm, monkeypatch, tmp_path):
    """两态对照：留痕正常 vs 留痕必抛 ⇒ 返回值 / 记忆字段 / 额度账本全等，且只记 WARNING。"""
    warnings: list[str] = []

    class _Loud:
        def __init__(self, inner):
            self._inner = inner

        def warning(self, msg, *a):
            warnings.append(str(msg) % a if a else str(msg))

        def __getattr__(self, name):
            return getattr(self._inner, name)
    monkeypatch.setattr(ar, "_logger", _Loud(ar._logger))
    monkeypatch.setattr(ar, "_now_naive", lambda: _FIXED_NOW)

    def _once(tag, ids):
        _seed(rating_db, *[_mem(i, "用户说明天要去长沙出差" if i == ids[0] else "用户喜欢吃香菜")
                           for i in ids])
        monkeypatch.setattr(rq, "_STATE_FILE", tmp_path / f"quota-{tag}.json")
        monkeypatch.setattr(ar, "_new_batch_id", lambda now: f"ar-batch-{tag}")
        got: list[dict] = []
        import app.agent.trace as trace
        if tag == "boom":
            def _boom(**_kw):
                raise RuntimeError("trace down")
            monkeypatch.setattr(trace, "enqueue_task_log", _boom)
        else:
            monkeypatch.setattr(trace, "enqueue_task_log", lambda **kw: got.append(kw))
        llm.reply = json.dumps([{"id": ids[0], "star": 4}, {"id": ids[1], "star": 9}])
        ret = _run()
        return ret, [_mem_state(rating_db, i) for i in ids], rq.used_today(_CHAR), got

    ret_a, states_a, quota_a, got_a = _once("ok", [101, 102])
    ret_b, states_b, quota_b, got_b = _once("boom", [201, 202])

    assert got_a and got_b == [], "第二趟留痕全炸 ⇒ 一条也没写出"
    assert any("trace down" in w for w in warnings), f"炸掉须记 WARNING：{warnings}"
    assert ret_b == ret_a == 2
    assert states_b == states_a, "记忆表写入字段必须与留痕正常时逐字相同"
    assert states_a[0][0] == 80.0 and states_a[1][0] == 100.0, "importance=star×20（9 已夹到 5）"
    assert quota_b == quota_a == 2, "额度账本数值不得因留痕而变"


def test_rate_batch传不传obs返回值逐字相同(rating_db, llm, events):
    """新增 obs 关键字参数：旧调用方（不传）返回值必须与新调用方逐字一致，且不留一字节。"""
    _seed(rating_db, _mem(101, "用户说明天要去长沙出差"), _mem(102, "用户喜欢吃香菜"))
    llm.reply = '[{"id": 101, "star": 4}, {"id": 102, "star": 9}, {"id": 103, "star": "3"}]'
    char = SimpleNamespace(id=_CHAR, name="小暖", user_id=_USER)
    items = [SimpleNamespace(id=101, memory_type="event", content="用户说明天要去长沙出差"),
             SimpleNamespace(id=102, memory_type="preference", content="用户喜欢吃香菜")]

    async def _go():
        plain = await ar._rate_batch(char, items)
        with_obs: dict = {}
        noted = await ar._rate_batch(char, items, obs=with_obs)
        await _drain()
        return plain, noted, with_obs
    plain, noted, with_obs = asyncio.run(_go())

    assert repr(plain) == repr(noted) == "[{'id': 101, 'star': 4}, {'id': 102, 'star': 5}]"
    assert with_obs["stars"] == {"101": 4, "102": 5}
    assert with_obs["model_stars"] == {"101": 4, "102": 9}, "模型原样星分另存，裁剪痕迹可回溯"
    assert with_obs["candidate_count"] == 2 and with_obs["memory_ids"] == [101, 102]


def test_无候选角色也留结论行但无输入摘要且返回值不变(rating_db, llm, events):
    """全库无候选：评星返回 0（语义不变），但入口计数与「无候选」原因仍必须落痕。"""
    assert _run() == 0
    assert llm.prompts == []
    assert [r["outcome"] for r in _by_route(events, ar.TRACE_ROUTE_CHAR)] == ["no_candidates"] * 2
    assert _by_route(events, ar.TRACE_ROUTE_INPUT) == []
    assert len(_by_route(events, ar.TRACE_ROUTE_RUN)) == 2
