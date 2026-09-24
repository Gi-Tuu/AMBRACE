# -*- coding: utf-8 -*-
"""P1 压缩存活项清单（2026-09-23，雷达 §2）：构造口径 + 灰度双条件 + 注入接线 + 留痕。

守的底线（派单要求 A/B 逐条对应，禁止为绿放宽）：
1. 三段固定顺序（当前目标 → 未决问题/计划 → 硬约束），空段整段省略，全空返回空串；
2. 单行 ≤ ``CHECKLIST_LINE_CHARS``、总长 ≤ ``CHECKLIST_TOTAL_CHARS``；多行原文压成单行；
3. 硬约束只做字面筛选，一条都没有时输出「（无显式硬约束记录）」，**绝不编造**；
4. 只读：不调 LLM、不写库（user_facts / prospective_intents / agent_task_logs 行数不变）、不上屏；
   任何异常 fail-open（空串 + WARNING）；
5. 槽值走读取侧白名单（敏感槽 relationship/health 未经账号显式开启永不带出）、TTL 过期不进；
   计划只取 ``status == "pending"``，按 ``due_start`` 取最近 ``INTENT_LIMIT`` 条（无期限排最后）；
6. ``survival_checklist`` 默认关 **且** 角色命中 ``SURVIVAL_CHECKLIST_GRAY_CHARS`` 才生效；
   关时逐字旧行为：日摘要 prompt 一字不变、不注入块、不多一次查库；
7. 注入位置在宿主 user 消息之前（红线②：user 恒为最后一条），块优先级 2 ——
   只有【系统指令】/【本轮提醒】比它高，system 超预算时它最后才被动；
8. 留痕只记长度/sha8/各段条数，**绝不落清单正文**（含用户硬约束原文）。

DB 用例统一走 tmp_path 临时库（_dbclone 模板克隆）+ pytest fixture，不触生产库。
"""
import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta

import pytest

from _dbclone import clone_engine, make_session_factory

from app.agent import context_builder as cb
from app.agent import survival_checklist as sc

pytestmark = pytest.mark.slow

_CHAR = 13           # 灰度白名单内角色
_OTHER_CHAR = 1      # 白名单外角色
_USER = 1
_SESSION = 1
_MARK = "独特句子ABC123"   # 留痕用例据此断言清单正文没被写进观测事件
_NOTE = "以下字段必须原文保留，不得改写、不得省略：\n"


class _Msg:
    """最小消息替身（``_build_older_summaries`` 只读 created_at / sender_type / content）。"""

    def __init__(self, created_at, sender_type, content):
        self.created_at = created_at
        self.sender_type = sender_type
        self.content = content


# ────────────────────────── 临时库夹具 ──────────────────────────

@pytest.fixture()
def cl_db(monkeypatch, tmp_path):
    """临时库（模板库克隆）：两处会话工厂都 patch（context_builder 模块级 + app.db.database），
    users/ai_characters/chat_sessions 父行就位（FK 生产同款开启）；细槽总闸置开使
    goal_state/job/living 可读；观测事件不落库（个别用例自行换成 sink 断言内容）。"""
    db_path = os.path.join(str(tmp_path), "cl.db")
    engine = clone_engine(db_path)
    factory = make_session_factory(engine)

    async def _init():
        import app.models  # noqa: F401
        from app.models.character import AICharacter
        from app.models.chat import ChatSession
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=_USER, username="sc_u1", nickname="主人"))
            db.add(AICharacter(id=_CHAR, user_id=_USER, name="小爱"))
            db.add(ChatSession(id=_SESSION, user_id=_USER, character_id=_CHAR))
            await db.commit()

    asyncio.run(_init())
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(cb, "async_session_factory", factory)
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "global_user_facts", True)
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: None)
    yield factory
    engine.sync_engine.dispose()


def _seed(factory, *rows):
    async def _go():
        async with factory() as db:
            for r in rows:
                db.add(r)
            await db.commit()
    asyncio.run(_go())


def _slot(slot, value, *, valid_to=None):
    from app.models.user import GlobalUserFact
    return GlobalUserFact(user_id=_USER, slot=slot, value=value, valid_to=valid_to)


def _intent(content, *, status="pending", character_id=_CHAR, due_start=None, due_end=None):
    from app.models.memory import ProspectiveIntent
    return ProspectiveIntent(user_id=_USER, character_id=character_id, content=content,
                             status=status, due_start=due_start, due_end=due_end)


def _seeds_all(factory):
    """三段齐备的种子：目标 + 处境三槽（living 那条同时字面命中硬约束）+ 1 pending / 2 非 pending。"""
    base = datetime(2026, 9, 20, 2, 0)
    _seed(factory,
          _slot("goal_state", "在备考CPA"),
          _slot("job", "在某某公司实习"),
          _slot("living", "独居，别劝我搬家"),
          _slot("location", "常驻示例市"),
          _intent("周末一起去看展", due_start=base + timedelta(days=1), due_end=base + timedelta(days=2)),
          _intent("已兑现的约定", status="discharged", due_start=base),
          _intent("改口的约定", status="stale", due_start=base))


def _detail(factory, character_id=_CHAR, user_id=_USER):
    async def _go():
        async with factory() as db:
            return await sc.build_checklist_detail(db, user_id=user_id, character_id=character_id)
    return asyncio.run(_go())


def _build(factory, character_id=_CHAR, user_id=_USER):
    async def _go():
        async with factory() as db:
            return await sc.build_survival_checklist(db, user_id=user_id, character_id=character_id)
    return asyncio.run(_go())


def _spy_builder(monkeypatch, sink: list):
    """把清单构造换成记录器：用例据此断言「关 / 未命中灰度时一次都不许多构造」。"""
    async def _spy(db, *, user_id=None, character_id=None):
        sink.append({"user_id": user_id, "character_id": character_id})
        return "", {"goal_n": 0, "open_n": 0, "hard_n": 0}
    monkeypatch.setattr(sc, "build_checklist_detail", _spy)
    return _spy


# ────────────────────────── 纯函数：三段顺序 / 省略 / 截断 ──────────────────────────

def test_三段固定顺序与内容():
    out = sc.render_survival_checklist(["- 当前目标：在备考CPA"], ["- 周末去看展"], ["- 别喝冰的"])
    lines = out.splitlines()
    assert lines[0].startswith("【存活项清单】"), "首行应为清单头"
    assert lines.index(sc._SEC_GOAL) < lines.index("- 当前目标：在备考CPA") < \
        lines.index(sc._SEC_OPEN) < lines.index("- 周末去看展") < \
        lines.index(sc._SEC_HARD) < lines.index("- 别喝冰的"), f"三段顺序必须固定：{lines}"


def test_空段整段省略不留空标题():
    only_plan = sc.render_survival_checklist([], ["- 周末去看展"], [])
    assert sc._SEC_OPEN in only_plan
    assert sc._SEC_GOAL not in only_plan, "空的目标段不得留下空标题"
    assert sc._SEC_HARD in only_plan and sc._NO_CONSTRAINT in only_plan, \
        "有内容却无硬约束 → 写明「无显式硬约束记录」，不留空让模型自行脑补"


def test_三段全空返回空串():
    assert sc.render_survival_checklist() == ""
    assert sc.render_survival_checklist([], [], []) == ""
    assert sc.render_survival_checklist(["  "], [""], []) == "", "全空白等同三段全空，不得只输出标题"


def test_单行截断不超过CHECKLIST_LINE_CHARS():
    long_line = sc._one_line("连" * 500)
    assert len(long_line) <= sc.CHECKLIST_LINE_CHARS
    out = sc.render_survival_checklist([f"- 当前目标：{long_line}"], [long_line], [long_line])
    body = [ln for ln in out.splitlines() if ln and not ln.startswith(("【", "·"))]
    assert body and all(len(ln) <= sc.CHECKLIST_LINE_CHARS for ln in body), \
        f"单行必须 ≤{sc.CHECKLIST_LINE_CHARS}：{[len(x) for x in body]}"


def test_多行原文被压成单行():
    out = sc.render_survival_checklist([], [sc.intent_line({"content": "第一行\n第二行"})], [])
    assert "- 第一行 第二行" in out.splitlines(), f"多行原文必须压成单行：{out}"


def test_总长硬上限CHECKLIST_TOTAL_CHARS():
    lines = [f"- 计划{i}：" + "胖" * 110 for i in range(20)]
    out = sc.render_survival_checklist(lines, lines, lines)
    assert out and len(out) <= sc.CHECKLIST_TOTAL_CHARS, \
        f"总长必须 ≤{sc.CHECKLIST_TOTAL_CHARS}，实际 {len(out)}"


def test_预算不足则整段不塞进不留光标题(monkeypatch):
    """总预算只装得下一个分区标题 → 撤标题、返回空串（宁可不注入，也不注入半成品）。"""
    monkeypatch.setattr(sc, "CHECKLIST_TOTAL_CHARS",
                        len(sc._HEADER) + 1 + len(sc._SEC_GOAL) + 1)
    assert sc.render_survival_checklist(["- 当前目标：装不下的目标"], ["- 装不下的计划"], []) == ""


def test_硬约束判定只做字面命中():
    assert sc.is_hard_constraint("别给我推荐咖啡")
    assert sc.is_hard_constraint("不要催我睡觉")
    assert sc.is_hard_constraint("必须记得喝水")
    assert not sc.is_hard_constraint("最近在学做饭")
    assert not sc.is_hard_constraint(""), "空值恒 False"
    assert sc.hard_constraint_line("别给我推荐咖啡") == "- 别给我推荐咖啡", "原文引用，不改写"
    assert sc.hard_constraint_line("   ") == ""


def test_计划行有due_end才标注期限():
    due = datetime(2026, 9, 25, 18, 0)
    from app.utils.timeutil import app_tz_offset_hours, shift_utc_naive
    expect = shift_utc_naive(due, app_tz_offset_hours()).strftime("%m-%d")
    assert sc.intent_line({"content": "交周报", "due_end": due}) == f"- 交周报（截止 {expect}）"
    assert sc.intent_line({"content": "交周报", "due_end": None}) == "- 交周报"
    assert sc.intent_line({"content": "交周报", "due_end": "不是日期"}) == "- 交周报", "脏值宁漏不编"
    assert sc.intent_line({"content": "   "}) == ""


def test_处境行按job_living_location固定顺序():
    line = sc.situation_line({"location": "常驻示例市", "job": "在某某公司实习", "living": "独居",
                             "goal_state": "不该出现在处境行"})
    assert line == "- 当前处境：工作/学业：在某某公司实习；居住情况：独居；位置/城市：常驻示例市"
    assert sc.situation_line({}) == ""
    assert sc.situation_line({"relationship": "单身"}) == "", "敏感槽不参与处境合成"


# ────────────────────────── 走库：口径与红线 ──────────────────────────

def test_三段在真实库上齐备(cl_db):
    _seeds_all(cl_db)
    out = _build(cl_db)
    lines = out.splitlines()
    assert sc._SEC_GOAL in lines and sc._SEC_OPEN in lines and sc._SEC_HARD in lines
    assert "在备考CPA" in out
    assert "在某某公司实习" in out and "独居" in out and "常驻示例市" in out, "job/living/location 合成一行当前处境"
    assert "周末一起去看展" in out and "截止" in out
    assert "别劝我搬家" in out, "字面命中强约束词的槽值进硬约束段"
    assert "已兑现的约定" not in out and "改口的约定" not in out, "只有 pending 计划进清单"


def test_计划按due_start取最近五条_无期限排最后(cl_db):
    base = datetime(2026, 9, 20, 2, 0)
    _seed(cl_db,
          _intent("计划A", due_start=base + timedelta(days=1)),
          _intent("计划B", due_start=base + timedelta(days=3)),
          _intent("计划D", due_start=base + timedelta(days=5)),
          _intent("计划E", due_start=base + timedelta(days=7)),
          _intent("计划F", due_start=base + timedelta(days=9)),
          _intent("计划G", due_start=base + timedelta(days=11)),
          _intent("计划H没有期限"))
    out = _build(cl_db)
    kept = [ln for ln in out.splitlines() if ln.startswith("- 计划")]
    assert len(kept) == sc.INTENT_LIMIT, f"最多取 {sc.INTENT_LIMIT} 条：{kept}"
    assert "计划A" in out and "计划B" in out, "due_start 近的优先"
    assert "计划G" not in out and "计划H没有期限" not in out, "超出条数与无期限的排后面，装不下就不进"
    assert out.index("计划A") < out.index("计划B") < out.index("计划D"), "按 due_start 升序（最近优先）"


def test_槽值过期不进且敏感槽未经开启永不带出(cl_db):
    from app.agent.loop import AGENT_FLAGS
    from app.utils.timeutil import now_naive_utc
    assert AGENT_FLAGS.get("user_fact_relationship") is False, "敏感槽默认关（红线）"
    _seed(cl_db, _slot("goal_state", "过期目标XYZ", valid_to=now_naive_utc()))
    assert "过期目标XYZ" not in _build(cl_db), "valid_to 已过的槽值属旧现状，不得进清单"

    _seed(cl_db, _slot("relationship", "感情状况PQR"))
    assert "感情状况PQR" not in _build(cl_db), "relationship 未经该账号显式开启永不带出"


def test_无任何记录返回空串(cl_db):
    assert _build(cl_db) == ""
    assert _detail(cl_db)[1] == {"goal_n": 0, "open_n": 0, "hard_n": 0}


def test_硬约束无记录时给占位行不编造(cl_db):
    _seed(cl_db, _slot("goal_state", "在备考CPA"), _slot("job", "在某某公司实习"))
    out = _build(cl_db)
    assert sc._NO_CONSTRAINT in out, "取不到硬约束要写明「无显式硬约束记录」，而不是留空"
    assert "在备考CPA" in out
    assert _detail(cl_db)[1]["hard_n"] == 0, "占位行不计入硬约束条数"


def test_构造零写入_只读不改任何表(cl_db):
    from sqlalchemy import func, select
    from app.models.agent import AgentTaskLog
    from app.models.memory import ProspectiveIntent
    from app.models.user import GlobalUserFact
    _seeds_all(cl_db)

    async def _counts():
        async with cl_db() as db:
            return [
                (await db.execute(select(func.count()).select_from(GlobalUserFact))).scalar(),
                (await db.execute(select(func.count()).select_from(ProspectiveIntent))).scalar(),
                (await db.execute(select(func.count()).select_from(AgentTaskLog))).scalar(),
            ]
    before = asyncio.run(_counts())
    assert before[0] and before[1] and before[2] == 0, "夹具应已插入数据且观测表为空"
    assert _build(cl_db), "清单应非空（否则零写入断言没有意义）"
    assert asyncio.run(_counts()) == before, "清单构造必须零写入（含观测表 agent_task_logs 一行都不许多）"


def test_查库异常fail_open收敛空串并记WARNING(cl_db, monkeypatch):
    """fail-open：底层查库炸了也不冒泡（空串 + WARNING），主链路照旧。"""
    recorded: list[str] = []

    class _Loud:
        def warning(self, msg, *a):
            recorded.append(str(msg) % a if a else str(msg))

        def info(self, *_a):
            pass

    class _Boom:
        async def execute(self, *_a, **_k):
            raise RuntimeError("boom-db")

    monkeypatch.setattr(sc, "_logger", _Loud())
    text, counts = asyncio.run(sc.build_checklist_detail(_Boom(), user_id=_USER, character_id=_CHAR))
    assert text == "" and counts["goal_n"] == 0
    assert recorded and "boom-db" in recorded[0], f"应记 WARNING 且带上原因：{recorded}"


# ────────────────────────── 灰度双条件 ──────────────────────────

def test_flag默认关且白名单只有char13():
    from app.agent.loop import AGENT_FLAGS
    assert AGENT_FLAGS.get("survival_checklist") is False, "新 flag 必须默认关（零行为变化）"
    assert cb.SURVIVAL_CHECKLIST_GRAY_CHARS == frozenset({13})
    assert cb.survival_checklist_allowed(_CHAR) is False


@pytest.mark.parametrize("flag_on,char,expected", [
    (False, _CHAR, False),
    (True, _CHAR, True),
    (True, _OTHER_CHAR, False),
    (True, None, False),
    (True, "13", True),
    (True, "abc", False),
])
def test_灰度双条件_开关开且命中白名单才生效(monkeypatch, flag_on, char, expected):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "survival_checklist", flag_on)
    assert cb.survival_checklist_allowed(char, flags=AGENT_FLAGS) is expected
    if flag_on:  # 生效时必须真读 AGENT_FLAGS（热切口径），不能只认显式传入的 flags
        assert cb.survival_checklist_allowed(char) is expected


# ────────────────────────── 注入接线 ──────────────────────────

def _state(messages=None):
    msgs = messages if messages is not None else [
        {"role": "system", "content": "主模板块"},
        {"role": "user", "content": "在忙吗"},
    ]
    return {"character_id": _CHAR, "user_id": _USER, "session_id": _SESSION,
            "user_message": "在忙吗", "context_messages": msgs,
            "_host_user_msg_index": len(msgs) - 1}


def test_注入位置在宿主user之前且块优先级为2(cl_db, monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "survival_checklist", True)
    _seeds_all(cl_db)
    state = _state()
    before = [dict(m) for m in state["context_messages"]]
    asyncio.run(cb._inject_survival_checklist(state))
    msgs = state["context_messages"]
    assert len(msgs) == 3, f"应多出一块清单：{msgs}"
    assert msgs[-1]["role"] == "user", "红线②：注入后 user 仍必须是最后一条"
    assert msgs[1]["content"].startswith("【存活项清单】"), "清单块必须插在宿主 user 之前"
    assert state["_host_user_msg_index"] == 2, "宿主 user 下标必须同步修正（后续 hook 靠它定位）"
    checklist = msgs[1]["content"]
    assert cb._block_priority(checklist) == 2
    assert cb._block_priority(before[0]["content"]) == cb._DEFAULT_BLOCK_PRIORITY == 3
    # 只有【系统指令】/【本轮提醒】比清单高；织库等低价值块比清单低（先牺牲）
    assert cb._block_priority("【系统指令】x") == 1
    assert cb._block_priority("【本轮提醒】x") == 1
    assert cb._block_priority("【全景记忆·织库】x") == 4 > 2 > 1
    assert _build(cl_db) == checklist, "注入的必须是确定性清单原文"


def test_flag关_不构造不注入不改结构(cl_db, monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "survival_checklist", False)
    calls: list = []
    _spy_builder(monkeypatch, calls)
    _seeds_all(cl_db)   # 库里有数据也不许被查出来
    state = _state()
    before = [dict(m) for m in state["context_messages"]]
    asyncio.run(cb._inject_survival_checklist(state))
    assert calls == [], "flag 关时不得构造清单（不多一次查库）"
    assert state["context_messages"] == before, "关时消息结构必须逐字不变"


def test_白名单外角色不构造不注入(cl_db, monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "survival_checklist", True)
    calls: list = []
    _spy_builder(monkeypatch, calls)
    _seeds_all(cl_db)
    state = _state()
    state["character_id"] = _OTHER_CHAR
    before = [dict(m) for m in state["context_messages"]]
    asyncio.run(cb._inject_survival_checklist(state))
    assert calls == [], "未命中灰度白名单不得构造清单"
    assert state["context_messages"] == before


def test_清单为空时逐字旧行为(cl_db, monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "survival_checklist", True)
    state = _state()   # 库里什么都不 seed → 清单为空
    before = [dict(m) for m in state["context_messages"]]
    asyncio.run(cb._inject_survival_checklist(state))
    assert state["context_messages"] == before, "空清单不得注入空块"


def test_构造抛异常时注入fail_open不冒泡(cl_db, monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "survival_checklist", True)
    _seeds_all(cl_db)

    class _Boom:
        def __call__(self):
            raise RuntimeError("boom-factory")
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", _Boom())
    recorded: list[str] = []

    class _Loud:
        def warning(self, msg, *a):
            recorded.append(str(msg) % a if a else str(msg))

        def __getattr__(self, name):
            return getattr(cb._logger, name)
    monkeypatch.setattr(cb, "_logger", _Loud())
    state = _state()
    before = [dict(m) for m in state["context_messages"]]
    asyncio.run(cb._inject_survival_checklist(state))   # 绝不冒泡
    assert state["context_messages"] == before, "构造异常时不得注入"
    assert any("boom-factory" in m for m in recorded), f"应记 WARNING：{recorded}"


def test_build_context装配出口接线_双向(monkeypatch, cl_db):
    """build_context 出口接线：关 → 消息结构逐字旧行为；开 → 多一块清单且在宿主 user 之前。"""
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "agent_context_registry", False)
    monkeypatch.setitem(AGENT_FLAGS, "cross_char_fact_sync", False)
    _seeds_all(cl_db)

    async def _fake_legacy(state, **_kw):
        state["context_messages"] = [
            {"role": "system", "content": "主模板块"},
            {"role": "user", "content": "在忙吗"},
        ]
        state["_host_user_msg_index"] = 1
        return state
    monkeypatch.setattr(cb, "build_context_legacy", _fake_legacy)

    calls: list = []
    monkeypatch.setitem(AGENT_FLAGS, "survival_checklist", False)
    off = asyncio.run(cb.build_context(_state()))["context_messages"]
    assert off == [{"role": "system", "content": "主模板块"}, {"role": "user", "content": "在忙吗"}]
    assert calls == []

    monkeypatch.setitem(AGENT_FLAGS, "survival_checklist", True)
    on = asyncio.run(cb.build_context(_state()))["context_messages"]
    assert len(on) == 3 and on[1]["content"].startswith("【存活项清单】")
    assert on[-1] == off[-1] and on[0] == off[0], "宿主原有块不受影响"


def test_日摘要prompt_关时逐字一致开时拼入清单(cl_db, monkeypatch):
    """要求 B①：关 → gen_prompt 与改动前逐字节一致；开 → 拼入清单 + 「原文保留」要求。"""
    from app.agent.loop import AGENT_FLAGS
    day_msgs = [_Msg(datetime(2026, 9, 20, 6, 0), "user", "我在备考CPA"),
                _Msg(datetime(2026, 9, 20, 6, 1), "ai", "加油")]
    captured: list[str] = []

    async def _fake_cc(messages=None, **_kw):
        captured.append(messages[0]["content"])
        return "生成的摘要"
    monkeypatch.setattr(cb, "chat_completion", _fake_cc)
    _seeds_all(cl_db)
    checklist = _build(cl_db)
    assert checklist, "种子应能构造出清单"

    async def _wipe():
        from sqlalchemy import delete
        from app.models.memory import DailySummary
        async with cl_db() as db:
            await db.execute(delete(DailySummary))
            await db.commit()

    def _run(flag_on):
        asyncio.run(_wipe())
        captured.clear()
        monkeypatch.setitem(AGENT_FLAGS, "survival_checklist", flag_on)
        asyncio.run(cb._build_older_summaries(
            {"session_id": _SESSION, "user_id": _USER, "character_id": _CHAR},
            day_msgs, "小爱", {"summary_chars": 8000}))
        assert captured, "应真的走了一次补生成"
        return captured[0]

    prompt_off = _run(False)
    prompt_on = _run(True)
    assert _NOTE not in prompt_off and "【存活项清单】" not in prompt_off, "关时 prompt 一字不得改动"
    head, _, tail = prompt_off.partition("\n")
    assert prompt_on == head + "\n" + _NOTE + checklist + "\n\n" + tail
    assert "以下字段必须原文保留，不得改写、不得省略" in prompt_on
    asyncio.run(_wipe())


def test_超预算时清单块最后才被动(cl_db, monkeypatch):
    """要求 B②：system 整体超硬顶 → 低价值块先牺牲，清单块与【系统指令】原文保留。"""
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "survival_checklist", True)
    _seeds_all(cl_db)
    filler = "".join(f"填充行内容凑长度用来触发整体硬裁剪{i}\n" for i in range(600))
    expected = _build(cl_db)
    msgs = [
        {"role": "system", "content": "主模板块\n" + filler},            # 默认 3
        {"role": "system", "content": "【全景记忆·织库】\n" + filler},    # 4，最先牺牲
        {"role": "system", "content": "【系统指令】用户没有说话"},         # 1，绝不动
        {"role": "user", "content": "在忙吗"},
    ]
    assert sum(len(m["content"]) for m in msgs if m["role"] == "system") > \
        cb.TOTAL_SYSTEM_QUOTA_TOKENS * cb._EST_CHARS_PER_TOKEN, "夹具本身必须超硬顶，否则用例没在测裁剪"
    woven_before = len(msgs[1]["content"])
    asyncio.run(cb._inject_survival_checklist(_state(msgs)))

    contents = [m["content"] for m in msgs]
    assert expected in contents, "超预算场景下清单块必须原文存活"
    assert "【系统指令】用户没有说话" in contents, "【系统指令】比清单更高，同样不得被动"
    assert msgs[-1]["role"] == "user", "红线②：user 恒为最后一条"
    woven_after = len([c for c in contents if c.startswith("【全景记忆·织库】")][0])
    assert woven_after < woven_before, "低价值块必须先替清单牺牲"
    total = sum(len(m["content"]) for m in msgs if m["role"] == "system")
    assert total <= cb.TOTAL_SYSTEM_QUOTA_TOKENS * cb._EST_CHARS_PER_TOKEN, \
        f"必须裁到硬顶以内，实际 {total}"


# ────────────────────────── 留痕 ──────────────────────────

def test_留痕只记长度与hash不落正文(monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    events: list[dict] = []
    monkeypatch.setitem(AGENT_FLAGS, "memory_trace_debug", True)
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: events.append(kw))
    text = f"【存活项清单】\n· 硬约束\n- 别喝冰的{_MARK}"
    cb._note_survival_checklist_injected(_CHAR, text, {"goal_n": 1, "open_n": 2, "hard_n": 3},
                                         messages_n=9, elapsed_ms=12.345)
    assert len(events) == 1, f"应落一条观测事件：{events}"
    ev = events[0]
    assert ev["route"] == "survival_checklist" and ev["character_id"] == _CHAR
    detail = json.loads(ev["steps_json"])
    assert set(detail) == {"enabled", "checklist_len", "checklist_sha8", "goal_n", "open_n",
                           "hard_n", "messages_n", "elapsed_ms"}, f"留痕字段必须恰好这 8 个：{set(detail)}"
    assert detail["enabled"] is True and detail["checklist_len"] == len(text)
    assert detail["goal_n"] == 1 and detail["open_n"] == 2 and detail["hard_n"] == 3
    assert detail["messages_n"] == 9 and detail["elapsed_ms"] == 12.3
    assert detail["checklist_sha8"] == hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    assert _MARK not in ev["steps_json"] and "硬约束" not in ev["steps_json"], "steps_json 绝不含清单正文"


def test_留痕各段条数来自清单且不含正文(cl_db, monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    events: list[dict] = []
    monkeypatch.setitem(AGENT_FLAGS, "memory_trace_debug", True)
    monkeypatch.setitem(AGENT_FLAGS, "survival_checklist", True)
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: events.append(kw))
    _seeds_all(cl_db)
    asyncio.run(cb._inject_survival_checklist(_state()))
    assert len(events) == 1, f"注入应恰好留痕一条：{events}"
    detail = json.loads(events[0]["steps_json"])
    assert detail["goal_n"] == 2, "目标行 + 处境合成行"
    assert detail["open_n"] == 1, "只有 pending 那条"
    assert detail["hard_n"] == 1
    assert "备考CPA" not in events[0]["steps_json"], "留痕不落清单正文"


def test_留痕失败静默不影响主链路(monkeypatch):
    def _boom(**_kw):
        raise RuntimeError("obs down")
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", _boom)
    cb._note_survival_checklist_injected(_CHAR, "任意清单", {"goal_n": 1}, 3, 1.0)


def test_flag已在开关目录登记():
    from app.application.flag_catalog import FLAG_CATALOG
    meta = FLAG_CATALOG["survival_checklist"]
    assert meta["group"] == "memory" and meta["visible"] is False
    assert meta["title_zh"] and meta["desc_zh"] and meta["title_en"] and meta["desc_en"]
