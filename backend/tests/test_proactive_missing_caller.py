# -*- coding: utf-8 -*-
"""proactiveD1：D 家族——主动链 ``generate_proactive_event`` 的 7 处 ``user_id or 1`` 改 fail-closed。

背景（派单 D1，2026-09-21）：B 家族修的是「宿主拿不到 caller」，C 家族修的是「角色自身归属缺失」，
本批修的是主动消息链 **自身内部** 的 7 处硬兜底。user_id 为 None 时，旧写法会拿 1 号账号的画像/天气/
前台应用去喂这一次生成（跨账号读）；写面 ``request_check_in(user_id or 1, ...)`` 更实在——
``check_in_requests.user_id`` 是 NOT NULL + FK（app/models/device/__init__.py:82），兜底成 1 号
等于把查岗请求登记进 **1 号账号的手机队列**（1 号客户端轮询到会真的去采集快照）。

口径（下游已逐处核实）：
- 读面 6 处直接透传 ``user_id``：None 时 ``build_user_profile_text`` 走 B2 的 None 守卫（通用画像、
  不借 1 号）；``get_user_weather_line`` / ``get_check_in_foreground_app`` 查不到 → 空串（调用方据此
  不注入）；persona 两处与 ``load_fresh_active_topics_text`` 的 user_id 根本不进 SQL（no-op，透传更诚实）。
- 写面 1 处加守卫：无归属就不登记查岗（fail-closed），但 ``[CHECK_IN]`` 标记仍要剥离。

3 条用例对应派单 §2：①读面连线（一次覆盖 6 处，None / 1 各跑「旧链路 + outreach 链路」两趟）；
②查岗写面（正/反：零调用 + 全表 0 行 vs 1 次调用 + 1 行 user_id=1）；
③不借 1 号画像（行为级：最终 prompt 不含 SENTINEL_U1，正向先钉住它确实注入）。

spy 必须打在 **定义处模块**（这 6 处都是函数体内惰性 import，打在 message_generator 上无效）；
LLM 是模块级 import（message_generator.py:7），故直接打在 ``app.scheduling.message_generator.chat_completion``。

（项目未装 pytest-asyncio，统一 asyncio.run；临时库走 tests/_dbclone.py，绝不碰生产库。）
"""
import asyncio

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.domain.proactivity.outreach import FOLLOW_UP, TIER_RECENT
from app.models.character import AICharacter, ProactiveSettings
from app.models.device import CheckInRequest
from app.models.user import User
from app.scheduling import message_generator as mg

# 每例起一次临时库（克隆建表），与同族主动链路测试一致打 slow 标记。
pytestmark = pytest.mark.slow

U1 = 1                    # 归属正常的账号（昵称带 SENTINEL_ 哨兵）
CID = 51                  # user_id=1 的角色，且已开启「查岗」
NICK_SENTINEL = "SENTINEL_U1"
LLM_TEXT = "我刚跑完步回来，整个人都清醒了。\n你今晚有空吗，想找你聊两句。"
LLM_TEXT_CHECKIN = "随口问一句，你此刻在忙什么呢？\n[CHECK_IN]"


class _Harness:
    """用例入口：临时库会话工厂 + LLM 出口文本/送进 LLM 的 messages 记录器。"""

    def __init__(self, factory):
        self.factory = factory
        self.llm_text = LLM_TEXT
        self.prompts: list[list[dict]] = []


@pytest.fixture()
def d1_db(monkeypatch, tmp_path):
    """克隆库 + 种子（1 号账号 / 其角色 / 该角色查岗已开），并把主动链前置查询接到临时库与桩上。

    画像 ``build_user_profile_text`` **保持真实**（用例 3 要靠它判是否串到 1 号）；
    自然度重试与分块护栏关掉：本批钉的是「传了谁的 id」，不是文本质量（用例 2 还要保证
    含 [CHECK_IN] 的段能活着走到写面判定）。
    """
    engine = clone_engine(tmp_path / "d1.db")
    factory = make_session_factory(engine)

    async def _seed():
        async with factory() as db:
            db.add(User(id=U1, username="d1_u1", nickname=NICK_SENTINEL, password_hash="x"))
            await db.flush()  # FK 图有环 ⇒ 显式让 users 父行先落，再挂角色
            db.add(AICharacter(id=CID, user_id=U1, name="小爱", personality="友善",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            await db.flush()
            db.add(ProactiveSettings(character_id=CID, check_in_enabled=True))
            await db.commit()

    asyncio.run(_seed())

    h = _Harness(factory)

    async def _noop(*_a, **_k):
        return ""

    async def _noop_list(*_a, **_k):
        return []

    async def _persona(*_a, **_k):
        return {"cognitive": True, "relationship_state": "", "active_topics": "",
                "storyline_status": "无"}

    async def _fake_llm(messages=None, **_kw):
        h.prompts.append(messages or [])
        return h.llm_text

    async def _level(*_a, **_k):
        return 0

    # 会话工厂逐模块绑定（各模块级 from … import 已绑死旧名字），含查岗写面所在模块
    for path in (
        "app.db.database.async_session_factory",
        "app.agent.user_profile.async_session_factory",
        "app.application.phone_service.async_session_factory",
    ):
        monkeypatch.setattr(path, factory)

    # 与本批无关的前置查询静音（保持轻量、确定）
    monkeypatch.setattr("app.agent.persona.assemble_persona_context", _persona)
    monkeypatch.setattr("app.application.weather_service.get_user_weather_line", _noop)
    monkeypatch.setattr("app.memory.search_memories", _noop_list)
    monkeypatch.setattr("app.memory.current_state.current_user_state_anchor", _noop)
    monkeypatch.setattr(mg, "_load_recent_reflection", _noop)
    monkeypatch.setattr(mg, "chat_completion", _fake_llm)
    monkeypatch.setattr(mg, "load_character_reasoning_level", _level)

    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "proactive_naturalness_score", False)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_segment_guard", False)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_outreach_v2", False)

    yield h
    asyncio.run(engine.dispose())


def _run(h: _Harness, user_id, *, outreach: bool = False):
    """跑一次主动生成；outreach=True 走 B1-③ 新链路（persona 第二处 + 新鲜话题那一处只在新链路里）。"""
    kw = dict(
        character_name="小爱", character_bio="", character_personality="友善",
        character_id=CID, user_id=user_id, current_status="在家",
    )
    if outreach:
        kw.update(outreach_intent=FOLLOW_UP, outreach_plan={
            "tier": TIER_RECENT, "allow_active_topics": True, "allow_storyline": False,
            "allow_recall": False, "memory_query": "", "must_return_question": False,
        })
    return asyncio.run(mg.generate_proactive_event(**kw))


def _rows(h: _Harness, model) -> list:
    """断言落库事实（每次新开 session，不看内存对象）。"""
    async def _read():
        async with h.factory() as db:
            return list((await db.execute(select(model))).scalars().all())
    return asyncio.run(_read())


def _prompt_text(h: _Harness) -> str:
    """本轮真正送进 LLM 的全部文本（重试会追加一轮，故一并拼接）。"""
    return "\n".join(
        m.get("content", "") for msgs in h.prompts for m in msgs if isinstance(m, dict)
    )


# ───────────── 1. 读面连线：6 处站点把 caller 原样透传（None 不借 1 号 / 有归属零行为变化）


def test_proactive_read_faces_pass_the_caller_through(d1_db, monkeypatch):
    """D1 的 #1~#6：无归属时读面收到 None（而不是 1），归属正常时仍收到 1。

    正/反各跑两趟：旧链路覆盖 #1 画像 / #2 persona / #5 天气 / #6 前台应用；
    outreach 链路覆盖 #1 / #3 persona（新分支那一处）/ #4 新鲜话题 / #5 / #6。
    故 persona 每趟 2 次（两个站点各一次）、topics 每趟 1 次，其余 2 次。
    """
    seen: dict[str, list] = {k: [] for k in
                             ("profile", "persona", "topics", "weather", "checkin_app")}

    def _make_spy(site: str, uid_pos: int, ret):
        async def _spy(*args, **kwargs):
            seen[site].append(args[uid_pos] if len(args) > uid_pos else kwargs.get("user_id"))
            return ret
        return _spy

    # 打在定义处模块（message_generator 内部惰性 import 才拿得到桩）；下标 = 该函数形参位置
    monkeypatch.setattr("app.agent.user_profile.build_user_profile_text",
                        _make_spy("profile", 0, ""))
    monkeypatch.setattr("app.agent.persona.assemble_persona_context",
                        _make_spy("persona", 1, {"cognitive": True}))
    monkeypatch.setattr("app.agent.topic_tracker.load_fresh_active_topics_text",
                        _make_spy("topics", 1, ""))
    monkeypatch.setattr("app.application.weather_service.get_user_weather_line",
                        _make_spy("weather", 0, ""))
    monkeypatch.setattr("app.application.phone_service.get_check_in_foreground_app",
                        _make_spy("checkin_app", 0, ""))

    expected_calls = {"profile": 2, "persona": 2, "topics": 1, "weather": 2, "checkin_app": 2}

    for uid, label in ((None, "无归属"), (U1, "归属正常")):
        for site in seen:
            seen[site].clear()
        _run(d1_db, uid)                    # 旧链路：#1 #2 #5 #6
        _run(d1_db, uid, outreach=True)     # outreach 链路：#1 #3 #4 #5 #6
        for site, n in expected_calls.items():
            assert len(seen[site]) == n, (
                f"{label}：{site} 被调用 {len(seen[site])} 次（应为 {n} 次）"
                " → 站点没连线，本例反证会是空断言"
            )
            assert seen[site] == [uid] * n, (
                f"{label}：{site} 收到的 user_id 是 {seen[site]}，应全为 {uid!r}"
                "（旧写法 or 1 的串号形态就是这里出现了 1）"
            )


# ───────────── 2. 查岗写面：无归属不登记（且标记仍剥离）/ 有归属照常登记到本人名下


def test_proactive_check_in_registration_requires_owner(d1_db, monkeypatch):
    """D1 的 #7：LLM 输出 [CHECK_IN] 时，user_id=None 一次都不登记、全表 0 行。

    旧写法 ``request_check_in(user_id or 1, ...)`` 会把请求写进 1 号账号的手机队列
    （1 号客户端轮询到会去采集真实快照）⇒ 用「全表计数」钉死，而不只看有没有抛错。
    桩里同时调真实现，正向断言的是「确实落了 1 行且挂在 1 号名下」，不是「只记录了参数」。
    """
    import app.application.phone_service as ps

    d1_db.llm_text = LLM_TEXT_CHECKIN
    calls: list[tuple] = []
    _real = ps.request_check_in

    async def _spy(user_id, character_id):
        calls.append((user_id, character_id))
        return await _real(user_id, character_id)

    monkeypatch.setattr(ps, "request_check_in", _spy)

    segs = _run(d1_db, None)
    assert calls == [], "无归属时仍登记了查岗请求（守卫未生效）"
    assert _rows(d1_db, CheckInRequest) == [], "无归属的查岗请求以别的账号名义落了库"
    joined = "\n".join(segs)
    assert "[CHECK_IN]" not in joined, "无归属分支未剥离 [CHECK_IN] 标记（会把内部标注发给好友）"
    assert "随口问一句" in joined, "剥离标记后正文被吞掉（正常段应保留）"

    calls.clear()
    d1_db.prompts.clear()
    segs_ok = _run(d1_db, U1)
    assert calls == [(U1, CID)], f"归属正常时查岗未登记或参数不对：{calls}"
    rows = _rows(d1_db, CheckInRequest)
    assert len(rows) == 1 and rows[0].user_id == U1 and rows[0].character_id == CID, (
        "归属正常的查岗请求未登记到该账号名下 → 反证的「0 行」会是空断言"
    )
    assert "[CHECK_IN]" not in "\n".join(segs_ok), "正向分支未剥离 [CHECK_IN] 标记"


# ───────────── 3. 行为级：无归属时画像不得借 1 号账号（参数传对但下游又兜底也会被这例抓到）


def test_proactive_prompt_does_not_borrow_user_one_profile(d1_db):
    """画像走真实实现（不打桩）：user_id=None 时送进 LLM 的文本里不含 1 号昵称哨兵。

    先用归属正常那一趟钉住哨兵确实注入，否则「不含哨兵」可能只是画像整体坏了。
    """
    _run(d1_db, U1)
    pos = _prompt_text(d1_db)
    assert NICK_SENTINEL in pos, "正向对照没注入 1 号昵称 → 反证会是空断言"
    assert "好友画像" in pos

    d1_db.prompts.clear()
    _run(d1_db, None)
    neg = _prompt_text(d1_db)
    assert "小爱" in neg, "无归属时 prompt 本身没构建出来 → 反证会是空断言"
    assert NICK_SENTINEL not in neg, "无归属的主动消息借到了 1 号账号的画像（跨账号读）"
    # 画像仍要给出「通用」内容（B2 的 None 守卫语义），不是整块消失
    assert "用户昵称: 用户" in neg, "无归属时画像块被整体丢弃（应回落通用画像而非空）"
