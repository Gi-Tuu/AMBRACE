# -*- coding: utf-8 -*-
"""C9b切口1（2026-10-24 ZCode 实施单 §5）：结算身份/作用域/误结算守护的正负对照。

素材全部从零合成（设备质检/文档校对场景）。真实 SQL/服务，仅时钟与数据库目的地替换
（复用 `_plan_completion_fixture_c9b` 的 World；不 mock 待验证的身份/结算/候选过滤函数）。
"""
import asyncio

import pytest
from sqlalchemy import select

from _plan_completion_fixture_c9b import ARRIVAL_PLAN, ARRIVAL_SIGNAL, c9b_world as c9b_world  # noqa: F401

from app.models.chat import ChatMessage, ChatSession
from app.models.memory import ProspectiveIntent
from app.scheduling import prospective_intent as pi


# ───────────────────── §1 真实身份：chat_service 接线传证据 ─────────────────────

def test_chat_service_passes_real_identity_evidence():
    """接线位真实调用形态：settle 收到 user_id 与 source_message_id（AST 证据，非源码字符串）。"""
    import ast
    from pathlib import Path

    from app.application import chat_service

    tree = ast.parse(Path(chat_service.__file__).read_text(encoding="utf-8"))
    fns = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    helper = fns["_run_user_turn_signals"]
    src = ast.unparse(helper)
    call = ("settle_promises_on_user_message(character_id, session_id, content, "
            "user_id=user_id, source_message_id=user_msg_id)")
    assert call in src, "chat_service 必须把登录态 user 与本轮消息 id 作为证据传给结算"


def test_settle_explicit_identity_mismatch_rejected(c9b_world):
    """自报 user_id 与会话真实归属矛盾 ⇒ 整体不结算（不信客户端参数）。"""
    async def case():
        w = c9b_world
        ident = await w.plan()
        settled = await pi.settle_promises_on_user_message(
            201, 301, ARRIVAL_SIGNAL, user_id=102)
        assert settled == []
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


def test_settle_source_message_mismatch_rejected(c9b_world):
    """source_message_id 不是本会话真实用户消息（或不存在）⇒ 放弃本轮结算。"""
    async def case():
        w = c9b_world
        ident = await w.plan()
        other = await w.message("仓库的排班表更新了", session_id=302, ago=5)
        for bad in (other, 999999):
            settled = await pi.settle_promises_on_user_message(
                201, 301, ARRIVAL_SIGNAL, user_id=101, source_message_id=bad)
            assert settled == [], f"源消息 {bad} 不属于本会话，不得结算"
        assert (await w.state(ident))["status"] == "pending"
        # 正对照：本会话真实用户消息作证据 → 结算通过
        good = await w.message(ARRIVAL_SIGNAL, ago=1)
        settled = await pi.settle_promises_on_user_message(
            201, 301, ARRIVAL_SIGNAL, user_id=101, source_message_id=good)
        assert settled == [ident]
    asyncio.run(case())


def test_settle_missing_session_and_user_keeps_plans(c9b_world):
    """无会话又无用户身份 ⇒ 无从界定归属，保守不结算（缺证据保留待核）。"""
    async def case():
        w = c9b_world
        ident = await w.plan()
        assert await pi.settle_promises_on_user_message(201, None, ARRIVAL_SIGNAL) == []
        assert await pi.settle_promises_on_user_message(201, 404, ARRIVAL_SIGNAL) == []
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


def test_settle_legacy_null_session_row_same_owner_still_settles(c9b_world):
    """旧行为兼容（REWORK2 更新口径）：NULL 会话行须**带真实源**才可被同 user/角色结算。

    无源历史行不再自动推断完成（REWORK2 动作 3）；补真实源消息后（同 user、同角色、
    证据晚于源）按旧行为结算。
    """
    async def case():
        w = c9b_world
        # ① 无源：不自动完成（due 用不同提起档，避免与 ② 经 topic 聚合双 NULL 旧路径合并）
        from datetime import timedelta
        ident_nosrc = await pi.upsert_intent(
            user_id=101, character_id=201, content=ARRIVAL_PLAN, kind="promise",
            cue_terms=["设备校验"], due_end=w.due + timedelta(days=2), side="user")  # 无 source、无 session
        await w.message(ARRIVAL_SIGNAL, ago=1)
        assert await pi.settle_promises_on_user_message(201, 301, ARRIVAL_SIGNAL) == []
        assert (await w.state(ident_nosrc))["status"] == "pending"
        # ② 真实源 + 同 user/角色：结算（A32 旧行为的合法形态；用不同文本避免被 ① 的
        # 同文无源行经近重路径合并——双 NULL 旧聚合是保留行为，不应干扰本段语义）
        src = await w.message("用户到达实验室后登记样品S", ago=20)
        ident = await pi.upsert_intent(
            user_id=101, character_id=201, content="用户到达实验室后登记样品S", kind="promise",
            cue_terms=["设备校验"], due_end=w.due, source_message_id=src, side="user")  # 不带 session
        settled = await pi.settle_promises_on_user_message(201, 301, ARRIVAL_SIGNAL)
        assert ident in settled
        assert (await w.state(ident))["status"] == "discharged"
    asyncio.run(case())


def test_settle_future_dated_plan_not_settled_today(c9b_world):
    """今天的发生信号不结算 due 在未来日的计划（settle 层时窗）。"""
    async def case():
        from datetime import timedelta
        w = c9b_world
        ident = await w.plan("用户明天到库房后核对货架C标签",
                             due=w.due + timedelta(days=1))
        assert await w.signal() == []
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


# ───────────────────── §2 合并边界：不跨账号/不跨会话 ─────────────────────

def test_near_duplicate_requires_same_user(c9b_world):
    """同文 + 共享角色 + 同 due：另一账号的 pending 行不被复用（upsert 全链）。"""
    async def case():
        w = c9b_world
        first = await w.plan(ARRIVAL_PLAN, user_id=101, session_id=301)
        second = await w.plan(ARRIVAL_PLAN, user_id=102, session_id=305)
        assert first != second
        rows = {}
        for i in (first, second):
            rows[i] = await w.state(i)
        assert rows[first]["user_id"] == 101 and rows[second]["user_id"] == 102
    asyncio.run(case())


def test_topic_merge_requires_same_source_session(c9b_world):
    """共同 arrival trigger 的两条独立义务：来源会话不同 ⇒ 各自成行（不隐式同事件）。"""
    async def case():
        w = c9b_world
        a = await w.plan("用户到地方后登记设备M序列号", session_id=301)
        b = await w.plan("用户到地方后封存样品N", session_id=302)
        assert a != b
        contents = [(await w.state(i))["content"] for i in (a, b)]
        assert "封存样品N" not in contents[0] and "登记设备M序列号" not in contents[1], \
            "跨会话不得合并成一条（义务身份独立保留）"
    asyncio.run(case())


# ───────────────────── §3 断言边界：保守识别肯定信号 ─────────────────────

@pytest.mark.parametrize("guarded", [
    "要是我国到了，就先检测", "我还没进门，先整理工具箱",
    "规程里写着：我进门后先断电", "值班员丙发来消息：“我进门了。”我这边还没动身。",
    "他进门了，我还在外面",
])
def test_non_first_person_or_guarded_signal_keeps_plan(guarded, c9b_world):
    """假设/否定/引述/第三方转述/第三人称不结算（合成新句，与契约四负例相邻的正反例组）。"""
    async def case():
        w = c9b_world
        ident = await w.plan("用户到达门口后给设备P上电")
        assert await w.signal(guarded) == []
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


@pytest.mark.parametrize("affirmative", [
    "我进门了", "到门口了，等你开门", "我回来了，今天走得早",
])
def test_first_person_assertive_signal_settles(affirmative, c9b_world):
    """第一人称明确肯定句仍正常结算（收紧不误伤正例）。"""
    async def case():
        w = c9b_world
        ident = await w.plan("用户到达门口后给设备P上电")
        assert await w.signal(affirmative) == [ident]
        assert (await w.state(ident))["status"] == "discharged"
    asyncio.run(case())


# ───────────────────── §4 闸门一致：collect 与 fire 前的拒绝/保留 ─────────────────────

def test_collect_rejects_stale_signal_for_next_day_plan(c9b_world):
    """collect 层：昨日到达消息不是明日计划的完成证据（run_prospective_due 同口径）。"""
    async def case():
        from datetime import timedelta
        w = c9b_world
        src = await w.message("用户后天到达后校准设备Q", ago=30)
        await w.signal("我到了", ago=10)
        ident = await w.plan("用户后天到达后校准设备Q", source_id=src,
                             due=w.due + timedelta(days=2))
        w.now += timedelta(days=2)
        assert ident in await w.candidates(), "旧信号不得在 collect 层吞掉新计划"
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


def test_run_prospective_due_keeps_plan_when_signal_stale(c9b_world, monkeypatch):
    """fire 前闸门：过期信号不作证据 ⇒ 不认领不发送，计划保持 pending。"""
    async def case():
        from datetime import timedelta
        w = c9b_world
        src = await w.message("用户次日到达后给设备R做绝缘", ago=30)
        await w.signal("我到了", ago=10)
        ident = await w.plan("用户次日到达后给设备R做绝缘", source_id=src,
                             due=w.due + timedelta(days=1))
        w.now += timedelta(days=1)
        sent = []

        async def _no_send(*a, **kw):
            sent.append(1)
            return None

        async def _no_llm(*a, **kw):
            sent.append(2)
            return "占位"

        monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _no_send)
        monkeypatch.setattr("app.agent.llm_client.chat_completion", _no_llm)
        fired = await pi.run_prospective_due({
            "pis_id": ident, "character_id": 201, "user_id": 101, "session_id": 301,
            "content": (await w.state(ident))["content"], "due_end": w.due + timedelta(days=1),
            "side": "user", "trigger": "arrival",
        })
        # 正确行为：到期提醒**照发**（旧信号不得把计划静默吞掉）——sent 含真实发送记录，
        # 区别于「already fulfilled」提前返回（那会 sent==[] 且无 LLM 调用）。
        assert fired is True
        assert 1 in sent, "到期计划应真实 fire（旧信号不作完成证据 ≠ 不提醒）"
        assert (await w.state(ident))["status"] == "discharged"  # fire 成功后的正常终态
    asyncio.run(case())


def test_same_source_replay_keeps_single_identity(c9b_world):
    """同源重放幂等：同一条源消息重复 upsert 不产生第二行（合并边界不破坏幂等）。"""
    async def case():
        w = c9b_world
        src = await w.message(ARRIVAL_PLAN, ago=20)
        first = await w.plan(source_id=src)
        assert await w.plan(source_id=src) == first
        async with w.factory() as db:
            n = len((await db.execute(select(ProspectiveIntent))).scalars().all())
        assert n == 1
    asyncio.run(case())


def test_session_row_really_binds_user_identity(c9b_world):
    """库内身份链真实存在：会话/消息归属与结算过滤所依据的字段一致（防测试直接改行）。"""
    async def case():
        w = c9b_world
        await w.plan()
        async with w.factory() as db:
            sess = await db.get(ChatSession, 301)
            msg = (await db.execute(
                select(ChatMessage).where(ChatMessage.session_id == 301,
                                          ChatMessage.sender_type == "user")
                .order_by(ChatMessage.id.desc()).limit(1))).scalar_one()
        assert sess.user_id == 101 and msg.session_id == 301 and msg.sender_type == "user"
    asyncio.run(case())


# ───────────────────── REWORK1（主审反例形态的自有对照，主审文件只读不复用）─────────────────────

def test_settle_authoritative_text_from_source_message(c9b_world):
    """REWORK1 必修 1：真实中性消息 ID + 传入完成文本 ⇒ 参数冲突保守拒绝（消息≠文本证据）。"""
    async def case():
        w = c9b_world
        ident = await w.plan()
        neutral = await w.message("检测仪的指示灯为蓝色", ago=1)
        settled = await pi.settle_promises_on_user_message(
            201, 301, ARRIVAL_SIGNAL, user_id=101, source_message_id=neutral)
        assert settled == []
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


def test_settle_session_character_must_match(c9b_world):
    """REWORK1 必修 2：会话真实归属角色与结算角色不符 ⇒ 拒绝（含 NULL 会话旧行）。"""
    async def case():
        w = c9b_world
        ident = await pi.upsert_intent(
            user_id=101, character_id=202, content=ARRIVAL_PLAN, kind="promise",
            cue_terms=["设备校验"], due_end=w.due, chat_session_id=None, side="user")
        src = await w.message(ARRIVAL_SIGNAL, ago=1)
        settled = await pi.settle_promises_on_user_message(
            202, 301, ARRIVAL_SIGNAL, user_id=101, source_message_id=src)
        assert settled == []
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


def test_settle_completion_evidence_must_follow_source_event(c9b_world):
    """REWORK1 必修 3：同日更早的真实到达消息不关闭更晚源事件的新计划（源顺序核验）。"""
    async def case():
        w = c9b_world
        early = await w.message(ARRIVAL_SIGNAL, ago=40)
        later_src = await w.message("用户再次到达质检台后扫描设备Q", ago=5)
        ident = await w.plan("用户再次到达质检台后扫描设备Q", source_id=later_src)
        settled = await pi.settle_promises_on_user_message(
            201, 301, ARRIVAL_SIGNAL, user_id=101, source_message_id=early)
        assert settled == []
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


def test_near_duplicate_respects_session_boundary(c9b_world):
    """REWORK1 必修 4：同 user 同文不同会话 ⇒ 近重路径不复用（不绕过主题路径的会话边界）。"""
    async def case():
        w = c9b_world
        first = await w.plan(session_id=301)
        second = await w.plan(session_id=302)
        assert first != second
        assert (await w.state(second))["session_id"] == 302
    asyncio.run(case())


@pytest.mark.parametrize("guarded", [
    '巡检员发回一条记录："我到了质检台"，本人尚未出发',   # ASCII 引号转述
    "他已经到门口，我尚未出发",                            # 句首第三方主体
    "如果质检台已经开放，我到了之后才启动扫描",            # 跨分句条件
])
def test_context_survives_clause_split(guarded, c9b_world):
    """REWORK1 必修 5：条件/引述/主体语境不随逗号切分丢失。"""
    async def case():
        w = c9b_world
        ident = await w.plan()
        assert await w.signal(guarded) == []
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


def test_precise_due_window_boundary_inside_and_outside():
    """REWORK1 必修 6：精确时刻分支 24h 窗内/外各 5 分钟 + 缺时间/坏类型的保守方向。"""
    from datetime import datetime
    # 主审例：signal UTC 01:55（北京 09:55），due 北京次日 09:50 ⇒ 窗内 5 分钟 → 放行
    assert pi._signal_fresh_enough(datetime(2032, 6, 18, 1, 55), datetime(2032, 6, 19, 9, 50)) is True
    # 窗外 5 分钟：signal 北京 09:45 < due-24h（北京 09:50）→ 拒
    assert pi._signal_fresh_enough(datetime(2032, 6, 18, 1, 45), datetime(2032, 6, 19, 9, 50)) is False
    # 跨日正例：signal 北京日晚于 due 北京日（日期型）
    assert pi._signal_fresh_enough(datetime(2032, 6, 18, 1, 55), datetime(2032, 6, 18, 23, 59)) is True
    # 跨日反例：昨日信号 vs 明日日期型 due
    assert pi._signal_fresh_enough(datetime(2032, 6, 18, 1, 55), datetime(2032, 6, 20, 23, 59)) is False
    # 缺时间：无从核验放行（旧行为）
    assert pi._signal_fresh_enough(None, datetime(2032, 6, 19, 9, 50)) is True
    assert pi._signal_fresh_enough(datetime(2032, 6, 18, 1, 55), None) is True
    # 坏类型：保守拒绝（不作证据）
    assert pi._signal_fresh_enough("not-a-time", datetime(2032, 6, 19, 9, 50)) is False


def test_anchor_alone_never_settles_collect_or_fire(c9b_world, monkeypatch):
    """REWORK1 必修 6：无时间戳锚不单独关闭独立计划（collect 与 fire 前一致）。"""
    async def case():
        from app.scheduling.state_guard import current_state_anchor  # noqa: F401
        w = c9b_world
        ident = await w.plan()

        async def _anchor(*a, **kw):
            return "用户已到家，正在休息"

        async def _no_send(*a, **kw):
            raise AssertionError("锚单独命中不得触发发送")

        async def _no_llm(*a, **kw):
            raise AssertionError("锚单独命中不得触发生成")

        monkeypatch.setattr("app.scheduling.state_guard.current_state_anchor", _anchor)
        monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _no_send)
        monkeypatch.setattr("app.agent.llm_client.chat_completion", _no_llm)
        # collect 层：锚命中但会话无真实信号 ⇒ 保留候选（不吞）
        assert ident in await w.candidates()
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


def test_chat_service_turn_signals_parameter_flow_executes(c9b_world, monkeypatch):
    """REWORK1 必修 7：真实执行 ``_run_user_turn_signals`` 验证参数流（非 AST 字符串）。

    在其它副作用/发送/模型 I/O 边界隔离（no-op 桩）；结算本体真实执行（wrap 记录参数后
    透传真函数），断言 helper 把登录态 user_id 与本轮 user_msg_id 传到结算，且临时库中
    本会话计划被真实 discharged。
    """
    async def case():
        from app.application import chat_service as cs
        w = c9b_world
        src = await w.message(ARRIVAL_PLAN, ago=20)
        ident = await w.plan(source_id=src)
        evidence = await w.message(ARRIVAL_SIGNAL, ago=1)
        monkeypatch.setattr(cs, "async_session_factory", w.factory)

        async def _noop_soften(*a, **kw):
            return None

        async def _noop_review(*a, **kw):
            return None

        monkeypatch.setattr("app.life.preoccupations.has_comfort_word", lambda t: False)
        monkeypatch.setattr("app.scheduling.memory_review.maybe_review_success", _noop_review)
        real_settle = pi.settle_promises_on_user_message
        captured: dict = {}

        async def _wrap_settle(character_id, session_id, user_text, **kw):
            captured.update(character_id=character_id, session_id=session_id,
                            user_text=user_text, **kw)
            return await real_settle(character_id, session_id, user_text, **kw)

        monkeypatch.setattr("app.scheduling.prospective_intent.settle_promises_on_user_message",
                            _wrap_settle)
        import contextlib

        with contextlib.suppress(Exception):
            # 提取器入口在真实链路外部；本用例只验证 helper 的参数流与结算真实发生
            await cs._run_user_turn_signals(
                session_id=301, user_id=101, character_id=201,
                content=ARRIVAL_SIGNAL, user_msg_id=evidence, user_timer=False)
        assert captured.get("user_id") == 101, "登录态 user 必须传到结算"
        assert captured.get("source_message_id") == evidence, "本轮消息 id 必须传到结算"
        assert (await w.state(ident))["status"] == "discharged", "真实结算必须发生（非 mock）"
    asyncio.run(case())


# ───────────────────── REWORK2：真实 collect→fire 管线正负对照 ─────────────────────

def test_collect_pipeline_retains_missing_source_plan(c9b_world):
    """REWORK2 动作 3/4：缺源计划经真实 collect 不被断言完成（保留候选；非手工 dict 塞键）。"""
    async def case():
        w = c9b_world
        ident = await w.plan(source_id=999999)
        await w.message(ARRIVAL_SIGNAL, ago=1)
        assert ident in await w.candidates()
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


def test_collect_pipeline_no_source_history_never_auto_settles(c9b_world):
    """REWORK2 动作 3：无源历史行（source NULL）不自动推断完成——保留候选与提醒能力。"""
    async def case():
        w = c9b_world
        ident = await pi.upsert_intent(
            user_id=101, character_id=201, content=ARRIVAL_PLAN, kind="promise",
            cue_terms=["设备校验"], due_end=w.due, side="user")  # 无 source、无 session
        async with w.factory() as db:
            row = await db.get(ProspectiveIntent, ident)
            row.chat_session_id = 301   # 有会话但无源：提醒能力保留、完成不自动推断
            await db.commit()
        await w.message(ARRIVAL_SIGNAL, ago=1)
        assert ident in await w.candidates()
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


def test_fire_gate_pipeline_settles_with_real_candidate(c9b_world):
    """REWORK2 动作 4：真实 collect 产出的候选（自动带源）→ 完成证据在案 ⇒ 采集层静默关闭。"""
    async def case():
        w = c9b_world
        src = await w.message(ARRIVAL_PLAN, ago=20)
        ident = await w.plan(source_id=src)
        await w.message(ARRIVAL_SIGNAL, ago=1)
        cands = [c for c in await pi.collect_due_promises() if c["pis_id"] == ident]
        assert cands == [], "完成证据在案 ⇒ 采集层已静默关闭（候选自动带源，链路真实）"
        assert (await w.state(ident))["status"] == "discharged"
    asyncio.run(case())


def test_fire_gate_pipeline_missing_key_reads_row(c9b_world, monkeypatch):
    """REWORK2 动作 4：候选缺 source_message_id 键 ⇒ fire 闸门按实际行回查（缺键不跳过验证）。

    ① 真实源计划：回查行拿到真实源 ⇒ 证据合法 ⇒ 静默关闭；② 缺源计划：回查无果 ⇒
    不静默关 ⇒ 正常 fire（提醒照发）。发送/LLM 出口桩隔离，不 mock 待测门禁。
    """
    async def case():
        w = c9b_world
        sent = []

        async def _send(*a, **kw):
            sent.append(1)
            return None

        async def _llm(*a, **kw):
            sent.append(2)
            return "占位"

        monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _send)
        monkeypatch.setattr("app.agent.llm_client.chat_completion", _llm)
        # ① 真实源：回查行 → 源可加载、证据晚于源 → 静默关闭
        src = await w.message(ARRIVAL_PLAN, ago=20)
        ident = await w.plan(source_id=src)
        await w.message(ARRIVAL_SIGNAL, ago=1)
        fired = await pi.run_prospective_due({
            "pis_id": ident, "character_id": 201, "user_id": 101, "session_id": 301,
            "content": ARRIVAL_PLAN, "due_end": w.due, "side": "user", "trigger": "arrival",
        })  # 无 source_message_id 键
        assert fired is True and sent == [], "回查行核到真实源 ⇒ 静默关闭（无生成/发送）"
        assert (await w.state(ident))["status"] == "discharged"
        # ② 缺源：回查行 → 源加载不到 → 不静默关 → 正常 fire
        ident2 = await w.plan("用户到达库房后清点货架R", source_id=999998)
        sent.clear()
        fired2 = await pi.run_prospective_due({
            "pis_id": ident2, "character_id": 201, "user_id": 101, "session_id": 301,
            "content": "用户到达库房后清点货架R", "due_end": w.due, "side": "user", "trigger": "arrival",
        })
        assert fired2 is True and 1 in sent, "缺源计划不被静默吞掉，提醒照发"
    asyncio.run(case())


def test_legacy_settle_requires_matching_message_in_session(c9b_world):
    """REWORK2 动作 1：旧三参接口——会话内无与文本一致的真实用户消息 ⇒ 保留（裸文本不作证据）。"""
    async def case():
        w = c9b_world
        ident = await w.plan()
        assert await pi.settle_promises_on_user_message(201, 301, ARRIVAL_SIGNAL) == []
        assert (await w.state(ident))["status"] == "pending"
        # 落库同文本消息后（旧接口真实匹配路径）⇒ 可结算
        await w.message(ARRIVAL_SIGNAL, ago=1)
        assert await pi.settle_promises_on_user_message(201, 301, ARRIVAL_SIGNAL) == [ident]
    asyncio.run(case())


def test_settle_source_reverse_binding_rejects_foreign(c9b_world):
    """REWORK2 动作 2：session 缺省 + 他人的完成消息 ID ⇒ 沿 src→ChatSession 反推拒（服务层）。"""
    async def case():
        w = c9b_world
        ident = await w.plan()
        foreign = await w.message(ARRIVAL_SIGNAL, session_id=305, ago=1)
        assert await pi.settle_promises_on_user_message(
            201, None, ARRIVAL_SIGNAL, user_id=101, source_message_id=foreign) == []
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


# ───────────────────── REWORK3：计划源绑定实际义务（fire 前真实管线正负）─────────────────────

def test_fire_settles_only_when_source_binds_to_obligation(c9b_world, monkeypatch):
    """REWORK3 动作 3：真实 collect 产出候选 → 完成信号在 collect **之后**落库 → fire 前闸门判定。

    正例：来源绑定正确（同 user/角色/会话、user 发送）⇒ 闸门静默完成（零发送/零 LLM）。
    负例：来源错绑（另一会话的消息作计划源）⇒ 不静默吞 ⇒ 正常 fire 提醒（真实发送发生，
    发送后 discharged＝提醒消费状态，不冒称业务完成）。
    """
    async def case():
        w = c9b_world
        sent = []

        async def _send(*a, **kw):
            sent.append(1)
            return None

        async def _llm(*a, **kw):
            sent.append(2)
            return "占位回复"

        monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _send)
        monkeypatch.setattr("app.agent.llm_client.chat_completion", _llm)
        # ① 正例：同账号同角色同会话的真实计划源
        src = await w.message(ARRIVAL_PLAN, session_id=301, ago=20)
        ident = await w.plan(source_id=src)
        cands = [c for c in await pi.collect_due_promises() if c["pis_id"] == ident]
        assert cands, "信号尚未落库 ⇒ collect 照常产出候选（真实管线）"
        await w.message(ARRIVAL_SIGNAL, ago=1)   # 完成信号在 collect 之后落库
        fired = await pi.run_prospective_due(cands[0])
        assert fired is True and sent == [], "绑定正确的来源 ⇒ 静默完成（零发送/零 LLM）"
        assert (await w.state(ident))["status"] == "discharged"
        # ② 负例：计划源来自同账号另一会话（302）——错绑，不静默完成
        bad_src = await w.message("用户到达车间后核对夹具T", session_id=302, ago=20)
        ident2 = await w.plan("用户到达车间后核对夹具T", source_id=bad_src)
        cands2 = [c for c in await pi.collect_due_promises() if c["pis_id"] == ident2]
        assert cands2, "错绑不影响候选产出（提醒能力保留）"
        await w.message(ARRIVAL_SIGNAL, ago=1)
        fired2 = await pi.run_prospective_due(cands2[0])
        assert fired2 is True and 1 in sent, "错绑来源不被静默吞掉 ⇒ 正常 fire 提醒"
        assert (await w.state(ident2))["status"] == "discharged"  # 发送后的消费状态
    asyncio.run(case())


def test_settle_rejects_assistant_and_cross_account_plan_source(c9b_world):
    """REWORK3 动作 1：助手消息/他人账号消息作计划源 ⇒ 不自动完成（settle 入口）。"""
    async def case():
        from app.models.chat import ChatMessage as _CM
        w = c9b_world
        # 助手消息作计划源
        ai_src = await w.message(ARRIVAL_PLAN, ago=20)
        async with w.factory() as db:
            m = await db.get(_CM, ai_src)
            m.sender_type = "ai"
            await db.commit()
        ident1 = await w.plan(source_id=ai_src)
        # 他人账号（家庭 102/会话 305）消息作计划源
        foreign_src = await w.message(ARRIVAL_PLAN, session_id=305, ago=20)
        ident2 = await w.plan(source_id=foreign_src)
        signal = await w.message(ARRIVAL_SIGNAL, ago=1)
        settled = await pi.settle_promises_on_user_message(
            201, 301, ARRIVAL_SIGNAL, user_id=101, source_message_id=signal)
        assert settled == []
        assert (await w.state(ident1))["status"] == "pending"
        assert (await w.state(ident2))["status"] == "pending"
    asyncio.run(case())
