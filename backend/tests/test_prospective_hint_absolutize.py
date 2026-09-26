# -*- coding: utf-8 -*-
"""Q2b（2026-09-25）：兑现路径的「相对时间词绝对化」。

同一缺陷在**真实主动消息的兑现链路**上：``run_prospective_due`` 把 ``prospective_intents.content``
原文塞进提示词，而 content 是当初写下的那句话（「我承诺明天替用户喂芒芒」写于 09-24）——事后兑现时
「明天」早已过期，模型会照着说错时间。修法与 Q2（现状 trace）一致：以候选 created_at（缺失退
updated_at，再退该行同名字段）为基准日换算，**基准日拿不到或异常则原样返回**（fail-open）。

覆盖：
- 纯函数 ``_hint_content``：created_at / updated_at / row 三级回退 + 三处皆缺与脏值 fail-open；
- ``collect_due_promises``：候选下发 created_at / updated_at；
- 端到端 ``run_prospective_due``：喂给模型的 user message 正文已无「明天」、出现绝对日期
  （AGENT_FLAGS.promise_self_side_split 关＝legacy 分支、开＝新分支，两条都必须过）。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；DB 用例走临时库，不碰生产库。）
"""
import asyncio
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from _dbclone import clone_engine, make_session_factory

from app.models.memory import ProspectiveIntent

# 派单实例：承诺写于北京 2026-09-24（库内 naive UTC 07:59:52）⇒「明天」＝9月25日
_RAW = "我承诺明天替用户喂芒芒，让用户躺着休息"
_ABS = "我承诺9月25日替用户喂芒芒，让用户躺着休息"
_BASE = datetime(2026, 9, 24, 7, 59, 52)


# ───────────────────────── 纯函数（快测档，零 DB）─────────────────────────

def test_hint_content_uses_candidate_created_at():
    """a. 候选带 created_at ⇒ 正文按该基准日换算，句子其余部分逐字不动。"""
    from app.scheduling.prospective_intent import _hint_content
    cand = {"content": _RAW, "created_at": _BASE}
    assert _hint_content(cand) == _ABS
    assert _hint_content(cand, None) == _ABS


def test_hint_content_falls_back_to_updated_at():
    """b. 候选没有 created_at ⇒ 退到 updated_at 作基准日。"""
    from app.scheduling.prospective_intent import _hint_content
    cand = {"content": _RAW, "updated_at": _BASE}
    assert _hint_content(cand) == _ABS
    # created_at 优先于 updated_at（两者不同时取前者）
    cand2 = {"content": _RAW, "created_at": _BASE,
             "updated_at": _BASE + timedelta(days=5)}
    assert _hint_content(cand2) == _ABS


def test_hint_content_falls_back_to_row_columns():
    """c. 候选两个时间都没有 ⇒ 退到 row（ORM 行 / dict 都支持）。"""
    from app.scheduling.prospective_intent import _hint_content
    cand = {"content": _RAW}
    row_orm = SimpleNamespace(created_at=_BASE, updated_at=None)
    assert _hint_content(cand, row_orm) == _ABS
    # row 只有 updated_at 时同样兜住
    assert _hint_content(cand, SimpleNamespace(created_at=None, updated_at=_BASE)) == _ABS
    # row 是 dict 也可取值（getattr/get 双兼容）
    assert _hint_content(cand, {"created_at": _BASE}) == _ABS


def test_hint_content_fail_open_keeps_raw():
    """d. 三处都没有基准日、或基准日是脏值 ⇒ 原样返回且绝不抛。"""
    from app.scheduling.prospective_intent import _hint_content
    assert _hint_content({"content": _RAW}) == _RAW
    assert _hint_content({"content": _RAW}, None) == _RAW
    assert _hint_content({"content": _RAW}, SimpleNamespace(created_at=None, updated_at=None)) == _RAW
    # 脏基准日：absolutize 内部收敛为原文（宁漏不编）
    assert _hint_content({"content": _RAW, "created_at": object()}) == _RAW
    assert _hint_content({"content": "明天见", "created_at": "不是日期"}) == "明天见"
    # 取值本身炸（row 属性访问抛）也不得冒泡
    class _Boom:
        @property
        def created_at(self):
            raise RuntimeError("detached row")

        updated_at = _BASE

    assert _hint_content({"content": _RAW}, _Boom()) == _RAW
    # 空正文不折腾
    assert _hint_content({"content": "   "}) == ""


# ───────────────────────── DB 用例（slow，临时库）─────────────────────────

@pytest.fixture()
def pi_db(monkeypatch, tmp_path):
    """临时库（模板库克隆，见 tests/_dbclone.py）+ 把 database / prospective_intent 的
    工厂指向临时工厂；种子（账号 1 / 角色 11）与治理用例一致。"""
    tmp = str(tmp_path)
    engine = clone_engine(os.path.join(tmp, "t.db"))
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="u1", nickname="用户"))
            db.add(AICharacter(id=11, user_id=1, name="sam", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()            # 2026-09-26（审查 P2-1 防御回归）：真实链路必有一条私聊会话行，
            # 此前夹具只种角色、用例却传 session_id=7；护栏加上后暴露了这份失真。
            from app.models.chat import ChatSession
            db.add(ChatSession(id=7, user_id=1, character_id=11))
            await db.commit()

    asyncio.run(_seed())
    import app.db.database as db_mod
    import app.scheduling.prospective_intent as pi
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(pi, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


async def _set_created_at(factory, pis_id: int, created: datetime) -> None:
    """把某行 created_at 定为固定基准日（server_default 是真实 UTC，不钉住会让断言随时钟漂移）。"""
    async with factory() as db:
        await db.execute(
            update(ProspectiveIntent).where(ProspectiveIntent.id == pis_id).values(created_at=created)
        )
        await db.commit()


@pytest.mark.slow
@pytest.mark.parametrize("side_split", [False, True], ids=["flag_off_legacy", "flag_on_split"])
def test_run_prospective_due_hint_body_absolutized(pi_db, monkeypatch, side_split):
    """端到端：兑现时喂给模型的正文已无「明天」，而是换算出的 9月25日。

    flag 关 → ``_build_prospective_hint_legacy``；flag 开 → ``_build_prospective_hint``。
    两个分支都必须走换算；同时 side 判定仍按原文（我承诺… → self）。
    """
    import app.scheduling.prospective_intent as pi
    from app.agent.loop import AGENT_FLAGS
    from app.scheduling.prospective_intent import (
        collect_due_promises, run_prospective_due, upsert_intent,
    )

    fixed_utc = _BASE                                    # 北京 2026-09-24 15:59
    monkeypatch.setattr(pi, "_now_naive", lambda: fixed_utc)
    monkeypatch.setitem(AGENT_FLAGS, "promise_self_side_split", side_split)

    pis_id = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content=_RAW, kind="promise",
        due_end=datetime(2026, 9, 24, 23, 59),           # 日期型，当天（北京 09-24）有效
        source_message_id=3001, chat_session_id=7,
    ))
    assert pis_id is not None
    asyncio.run(_set_created_at(pi_db, pis_id, fixed_utc))

    cands = asyncio.run(collect_due_promises())
    assert pis_id in {c["pis_id"] for c in cands}
    cand = [c for c in cands if c["pis_id"] == pis_id][0]
    assert cand["created_at"] == fixed_utc               # ① 基准日随候选下发
    assert cand["updated_at"] is not None
    assert cand["side"] == "self"                        # side 判定仍用原文

    prompts, sends = [], []

    async def _fake_llm(**kw):
        prompts.append(kw["messages"][-1]["content"])
        return "芒芒我喂好啦，你躺着歇着。"

    async def _fake_send(session_id, character_id, user_id, content,
                         message_type="prospective_intent", **kw):
        sends.append(content)

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_llm)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _fake_send)

    ok = asyncio.run(run_prospective_due(cand))
    assert ok is True
    assert len(sends) == 1

    assert len(prompts) == 1
    body = prompts[0]
    assert "明天" not in body
    assert "9月25日" in body
    assert _ABS in body                                  # 正文逐字为换算后版本
    assert _RAW not in body                              # 原文（含过期相对词）不再进提示词
