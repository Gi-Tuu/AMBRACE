# -*- coding: utf-8 -*-
"""ownerC1：C 家族「角色归属缺失」兜底改 fail-closed（守卫 2 处 + 读面 8 行 + 下游 2 处 None 守卫）。

背景（方案 `AMBRACE_C家族_逐处方案_20260921.md`，2026-09-21）：``char.user_id or 1`` 的触发条件是
**角色自身归属缺失/为 0**（不是 B 家族的「宿主拿不到 caller」）。写面一旦落到 1 号账号
（ai_moments.user_id / memories.user_id 都带上 1 号），就是「朋友圈里冒出别人动态」这种
跨账号**写串**；写 NULL 也不是出路（memories.user_id 是 NOT NULL + FK；ai_moments 的 NULL 行
feed 查不到、generate_comments_for_moment 又要求 owner）⇒ 唯一正确口径是**无归属就不生成**。

本文件 5 条用例对应派单 §2：
1. ``publish_moment`` 守卫（正/反）；2. ``generate_diary_for_character`` 守卫（正/反）；
3. 读面不借 1 号账号画像/权威位置（``build_moment_prompt``）；4. 下游 ``build_role_prompt_block`` /
``get_user_nickname`` 的 None 守卫（不再打 SAWarning）；5. 连线——守卫在 prompt 构建与生成之前。

造「无归属角色」：``ai_characters.user_id`` 带外键且克隆库逐连接 FK=ON（生产同语义），不能凭空写 0
⇒ 先补一行 ``User(id=0, username="__ownerless__")`` 作父行，再建 ``AICharacter(user_id=0)``；
这样既是真实的「归属损坏」形态（user_id=0），又满足 FK。

防假绿：反证一侧也给无归属角色种好**可用的会话上下文**并把 LLM 打桩成返回有效文本，
「删掉守卫」这一变异会一路走到写库/广播而让断言变红（方案 §7 变异对照表）；
正向对照一律先钉住哨兵/行数确实出现，再判反向缺席。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时库走 tests/_dbclone.py，绝不碰生产库。）
"""
import asyncio
import warnings
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SAWarning

from _dbclone import clone_engine, make_session_factory

from app.utils.timeutil import app_local_now
from app.models.character import AICharacter
from app.models.life import AIDiary, AIMoment
from app.models.memory import Memory
from app.models.user import User

# 重量级/集成型用例（每例起一次临时库），与同族朋友圈/日记测试一致打 slow 标记。
pytestmark = pytest.mark.slow

U1 = 1                # 归属正常的账号（昵称/权威位置都带 SENTINEL_ 哨兵）
U_OWNERLESS = 0       # 「无主」父行主键：只为满足 ai_characters.user_id 外键
CHAR_OK = 41          # user_id=1 的角色
CHAR_OWNERLESS = 42   # user_id=0 的角色（归属损坏形态）
NICK_SENTINEL = "SENTINEL_U1"
LOC_SENTINEL = "SENTINEL_LOC常驻杭州市"
MOMENT_TEXT = "和朋友吃了顿火锅，心情不错"
DIARY_TEXT = "今天和朋友吃了顿火锅，聊了很久。"
_MOMENT_EVT = "life.moment_published"


class _Harness:
    """用例入口：临时库会话工厂 + 事件总线/持久事件流水的记录器。"""

    def __init__(self, factory, published, domain_events):
        self.factory = factory
        self.published = published
        self.domain_events = domain_events


@pytest.fixture()
def c1_db(monkeypatch, tmp_path):
    """克隆库 + 种子：两个账号（1 / 无主 0）各一个角色，两个角色各有**可用当天会话上下文**。

    给无归属角色也种会话，是为了让「删守卫」这一变异能一路走到写库（否则它会在
    「无当天聊天上下文」处提前 return，反证就成了空断言）。
    """
    engine = clone_engine(tmp_path / "c1.db")
    factory = make_session_factory(engine)
    published: list[tuple] = []
    domain_events: list[dict] = []

    async def _seed():
        from app.models.chat import ChatMessage, ChatSession
        from app.models.user import GlobalUserFact

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with factory() as db:
            db.add(User(id=U1, username="c1_u1", nickname=NICK_SENTINEL, password_hash="x"))
            db.add(User(id=U_OWNERLESS, username="__ownerless__",
                        nickname="无主占位账号", password_hash="x"))
            # FK 图有环 ⇒ SQLAlchemy 的表间插入序不保证「父先子后」，显式 flush 两个 users 父行
            await db.flush()
            db.add(AICharacter(id=CHAR_OK, user_id=U1, name="有主角色", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            db.add(AICharacter(id=CHAR_OWNERLESS, user_id=U_OWNERLESS, name="无主角色",
                               personality="开朗", chat_style="口语化",
                               relation_type="朋友", is_active=True))
            await db.flush()
            db.add(GlobalUserFact(user_id=U1, slot="location", value=LOC_SENTINEL,
                                  source="chat", confidence=1.0, valid_from=now))
            await db.commit()
            # 两个角色各一条会话 + 当天一条用户消息（日记/朋友圈的上下文来源）
            for uid, cid in ((U1, CHAR_OK), (U_OWNERLESS, CHAR_OWNERLESS)):
                sess = ChatSession(user_id=uid, character_id=cid)
                db.add(sess)
                await db.flush()
                db.add(ChatMessage(session_id=sess.id, sender_type="user",
                                   content="今天一起去吃火锅吧", created_at=now))
            await db.commit()

    asyncio.run(_seed())

    # 读取方逐模块绑定（模块级 from … import 已绑死旧名字），含日记/朋友圈自身
    for path in (
        "app.db.database.async_session_factory",
        "app.application.moment_service.async_session_factory",
        "app.scheduling.diary_generator.async_session_factory",
        "app.agent.user_profile.async_session_factory",
        "app.memory.user_facts.async_session_factory",
        "app.application.chat_service.async_session_factory",
        "app.memory.service.async_session_factory",
        "app.events.store.async_session_factory",
    ):
        monkeypatch.setattr(path, factory)

    import app.application.moment_service as ms

    async def _append_spy(*args, **kwargs):
        domain_events.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(ms, "append_domain_event", _append_spy)
    monkeypatch.setattr("app.events.publish", lambda *a, **k: published.append(a))

    yield _Harness(factory, published, domain_events)
    asyncio.run(engine.dispose())


# ───────────────────────────────── 取数 helper（每次新开 session，断言的是落库事实）


def _all(h: _Harness, model, *conds) -> list:
    async def _run():
        async with h.factory() as db:
            stmt = select(model)
            if conds:
                stmt = stmt.where(*conds)
            return list((await db.execute(stmt)).scalars().all())
    return asyncio.run(_run())


def _count(h: _Harness, model, *conds) -> int:
    async def _run():
        async with h.factory() as db:
            stmt = select(func.count()).select_from(model)
            if conds:
                stmt = stmt.where(*conds)
            return int((await db.execute(stmt)).scalar() or 0)
    return asyncio.run(_run())


def _char(h: _Harness, character_id: int):
    async def _run():
        async with h.factory() as db:
            return await db.get(AICharacter, character_id)
    return asyncio.run(_run())


def _moment_events(h: _Harness) -> list[tuple]:
    return [c for c in h.published if c and c[0] == _MOMENT_EVT]


# ───────────────────── 1. publish_moment 守卫：无归属不生成 / 有归属零行为变化


def test_publish_moment_skips_ownerless_character(c1_db, monkeypatch):
    """反：无归属角色 → 返回 None、动态 0 行、moment 记忆 0 行、事件未广播、持久事件未落。

    正（对照）：归属正常 → 落 1 行且 user_id==1，事件广播 1 次。
    LLM 打桩成返回有效文本 ⇒ 删掉守卫时旧写法会真写出 user_id=1 的动态，反证必红。
    """
    import app.application.moment_service as ms

    async def _fake_content(prompt, char_name, max_retries=2):
        return MOMENT_TEXT

    monkeypatch.setattr(ms, "_generate_moment_content", _fake_content)

    out = asyncio.run(ms.publish_moment(CHAR_OWNERLESS, skip_interval=True))
    assert out is None, "无归属角色的动态仍被发布（守卫未生效）"
    assert _all(c1_db, AIMoment, AIMoment.character_id == CHAR_OWNERLESS) == []
    # 旧写法的串号形态：动态以 1 号账号名义落库 → 全表计数钉死
    assert _count(c1_db, AIMoment) == 0, "无归属角色的动态以别的账号名义落了库"
    assert _count(c1_db, Memory, Memory.sub_type == "moment") == 0, "无归属角色的动态写进了别人的记忆库"
    assert _moment_events(c1_db) == [], "无归属角色的动态被广播出去了（target/user_id 会指向 1 号）"
    assert c1_db.domain_events == [], "无归属角色的动态落了持久事件流水"

    out_ok = asyncio.run(ms.publish_moment(CHAR_OK, skip_interval=True))
    assert out_ok is not None, "归属正常的角色发不出动态 → 反证的「不生成」会是空断言"
    rows = _all(c1_db, AIMoment, AIMoment.character_id == CHAR_OK)
    assert len(rows) == 1 and rows[0].user_id == U1 and rows[0].content == MOMENT_TEXT
    mems = _all(c1_db, Memory, Memory.sub_type == "moment")
    assert len(mems) == 1 and mems[0].user_id == U1 and mems[0].character_id == CHAR_OK
    assert len(_moment_events(c1_db)) == 1, "归属正常的角色动态未被广播（行为变化）"
    assert len(c1_db.domain_events) == 1


# ───────────────────── 2. generate_diary_for_character 守卫：同口径


def test_generate_diary_skips_ownerless_character(c1_db, monkeypatch):
    """反：无归属角色 → 返回 None、ai_diaries 0 行、diary 记忆 0 行，且根本没调 LLM。

    正（对照）：归属正常 → 当天日记 1 行 + diary 记忆记在该账号名下。
    种子给无归属角色也备了当天会话 ⇒ 删守卫时旧写法会走到写库（owner_id 兜底成 1 号）。
    """
    import app.scheduling.diary_generator as dg

    llm_calls: list[dict] = []

    async def _fake_llm(messages=None, **kwargs):
        llm_calls.append(kwargs)
        return DIARY_TEXT

    monkeypatch.setattr(dg, "chat_completion", _fake_llm)

    # 日记日期必须用**北京日期**：get_today_chat_context 的窗口是「北京 0 点 = UTC 前一天 16 点」起算，
    # 而 CI runner 的进程时区是 UTC —— 在 UTC 16:00~24:00（北京 00:00~08:00）用 date.today() 会取到
    # 「昨天」，种子消息（created_at=UTC now）就落在窗口外 → 生成直接返回 None。
    # 2026-09-22 00:53（北京）CI 实测：3081 绿 / 唯一红就是这条（本机时区是北京，所以一直没显形）。
    target_date = app_local_now().date()

    assert asyncio.run(dg.generate_diary_for_character(CHAR_OWNERLESS, target_date)) is None
    assert _all(c1_db, AIDiary, AIDiary.character_id == CHAR_OWNERLESS) == []
    assert _count(c1_db, AIDiary) == 0, "无归属角色的日记仍落库"
    assert _count(c1_db, Memory, Memory.sub_type == "diary") == 0, "无归属角色的日记记忆写进了别人的库"
    assert llm_calls == [], "无归属角色仍在生成日记（守卫必须在 LLM 之前）"

    out = asyncio.run(dg.generate_diary_for_character(CHAR_OK, target_date))
    assert out is not None, "归属正常的角色日记生不出来 → 反证会是空断言"
    assert out["content"] == DIARY_TEXT
    assert out["diary_date"] == target_date.strftime("%Y-%m-%d")
    rows = _all(c1_db, AIDiary, AIDiary.character_id == CHAR_OK)
    assert len(rows) == 1
    mems = _all(c1_db, Memory, Memory.sub_type == "diary")
    assert len(mems) == 1 and mems[0].user_id == U1 and mems[0].character_id == CHAR_OK
    assert len(llm_calls) == 1


# ───────────────────── 3. 读面：无归属角色的 prompt 不得借 1 号账号画像/权威位置


def test_moment_prompt_does_not_borrow_user_one(c1_db):
    """build_moment_prompt 去掉 ``or 1`` 后：无归属角色拿不到 1 号的昵称与权威位置。

    正向对照先钉住两个哨兵确实注入（否则「不含哨兵」只是下游坏了造成的空断言）。
    """
    import app.application.moment_service as ms

    pos = asyncio.run(ms.build_moment_prompt(_char(c1_db, CHAR_OK)))
    assert NICK_SENTINEL in pos, "正向对照没注入 1 号昵称 → 反证会是空断言"
    assert LOC_SENTINEL in pos, "正向对照没注入 1 号权威位置 → 反证会是空断言"

    neg = asyncio.run(ms.build_moment_prompt(_char(c1_db, CHAR_OWNERLESS)))  # 不抛异常
    assert "无主角色" in neg, "无归属角色的 prompt 本身没构建出来 → 反证会是空断言"
    assert "SENTINEL_" not in neg, "无归属角色的 prompt 借到了 1 号账号的数据（画像/位置串号）"


# ───────────────────── 4. 下游 None 守卫：不再打 SAWarning（同 B2 写法）


def test_downstream_none_guards_emit_no_sa_warning(c1_db):
    """build_role_prompt_block / get_user_nickname 收到 None 时不得走 db.get（SAWarning 卫生）。

    同一捕获窗口里跑一次**裸** ``db.get(User, None)`` 作对照：它必须真的打出 SAWarning，
    否则「守卫那两次没有告警」只是捕获失效。
    """
    import app.agent.user_profile as up

    char = _char(c1_db, CHAR_OK)

    async def _bare_get():
        async with c1_db.factory() as db:
            return await db.get(User, None)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        block = asyncio.run(up.build_role_prompt_block(char, None))
        nickname = asyncio.run(up.get_user_nickname(None))
        guarded = [w for w in caught if issubclass(w.category, SAWarning)]
        assert asyncio.run(_bare_get()) is None
        control = [w for w in caught if issubclass(w.category, SAWarning)]

    assert control, "捕获窗口抓不到裸 db.get(User, None) 的 SAWarning → 本例反证会是空断言"
    assert not guarded, f"user_id=None 时仍走了 db.get，打出 SAWarning：{[str(w.message) for w in guarded]}"
    assert nickname == "用户"
    assert "用户昵称：用户" in block and NICK_SENTINEL not in block


# ───────────────────── 5. 连线：守卫在「取到 char 之后、构建 prompt / 调 LLM 之前」


def test_guard_precedes_prompt_build_and_generation(c1_db, monkeypatch):
    """删守卫的代价必须体现在「生成之前」：无归属角色一次都不触发 prompt 构建与内容生成。

    与用例 1 的分工：1 钉「不落库/不广播」，这里钉「不调用」（守卫位置），
    对照侧两者都被调用 1 次。
    """
    import app.application.moment_service as ms

    prompts: list[int] = []
    contents: list[tuple] = []

    async def _spy_prompt(char, extra_hint=""):
        prompts.append(char.id)
        return "PROMPT_STUB"

    async def _spy_content(prompt, char_name, max_retries=2):
        contents.append((prompt, char_name))
        return MOMENT_TEXT

    monkeypatch.setattr(ms, "build_moment_prompt", _spy_prompt)
    monkeypatch.setattr(ms, "_generate_moment_content", _spy_content)

    assert asyncio.run(ms.publish_moment(CHAR_OWNERLESS, skip_interval=True)) is None
    assert prompts == [], "无归属角色仍构建了朋友圈 prompt（守卫没在生成之前）"
    assert contents == [], "无归属角色仍调用了内容生成（守卫没在生成之前）"
    assert _count(c1_db, AIMoment) == 0

    assert asyncio.run(ms.publish_moment(CHAR_OK, skip_interval=True)) is not None
    assert prompts == [CHAR_OK], "归属正常的角色没走到 prompt 构建"
    assert [name for _p, name in contents] == ["有主角色"]
    assert _count(c1_db, AIMoment, AIMoment.character_id == CHAR_OK) == 1
