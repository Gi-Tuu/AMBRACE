# -*- coding: utf-8 -*-
"""召回后效用反馈测试（小增量 2026-09-16）

- classify_utility_signal：positive / negative / neutral 三条确定性判定；
- flag 关 = 零写入（schedule 不调度 apply）；
- apply：positive 微强化 / negative 微降权 / neutral 跳过，并写 memory_write_receipts 回执；
- 异步失败不影响主流程（apply 内部捕获，不抛）。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库，不触碰 backend/data）
"""
import asyncio
import os

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

import app.db.database as dbmod
from app.memory import utility_feedback as uf

pytestmark = pytest.mark.slow


@pytest.fixture()
def mem_db(monkeypatch, tmp_path):
    """临时 SQLite 文件库（模板库克隆，见 tests/_dbclone.py）：monkeypatch 全局
    async_session_factory（不触碰 backend/data）"""
    db_path = os.path.join(str(tmp_path), "t.db")
    engine = clone_engine(db_path)
    factory = make_session_factory(engine)

    async def _seed_parents():
        # _dbclone 默认开 FK（生产同款 PRAGMA）：memories.user_id / character_id 需父行先存在
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="utility_u1", nickname="效用用户"))
            db.add(AICharacter(id=1, user_id=1, name="效用角色"))
            await db.commit()

    asyncio.run(_seed_parents())
    monkeypatch.setattr(dbmod, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


# ─────────────────────────── classify 纯函数 ───────────────────────────

def test_classify_negative_on_correction_words():
    # 用户改口（纠正词在**用户消息**侧）→ negative；AI 回复里引用了同一条记忆也不翻判（①优先于②）
    sig = uf.classify_utility_signal(
        "用户喜欢喝美式咖啡",
        "那我改一下：你之前说过喜欢喝美式咖啡",
        user_message="你记错了，我其实喜欢喝拿铁",
    )
    assert sig == "negative"


def test_classify_correction_word_alone_now_negative():
    # 放宽判据（2026-09-21 任务2）：纠正词单命中即记 negative，不要求与记忆片段共现。
    # 收口（2026-10-04 A25 方案 a）：这一命中**只发生在用户消息侧**——「不对不对」得是用户说的。
    sig = uf.classify_utility_signal(
        "用户喜欢喝美式咖啡",
        "好累啊，那早点休息",
        user_message="不对不对，今天好累啊",
    )
    assert sig == "negative"


def test_classify_correction_without_reference_now_negative():
    # 纠正词（扩量后含「不对」）在用户消息里单命中 → negative（无记忆片段共现也记）。
    sig = uf.classify_utility_signal(
        "用户喜欢喝美式咖啡",
        "好，那这件事先这样吧",
        user_message="你说得不对，不过这件事先这样吧",
    )
    assert sig == "negative"


def test_classify_negative_via_user_message_reference():
    # 放宽判据：用户消息含纠正词（「你记错了」）→ 单命中即 negative，无需记忆片段共现
    sig = uf.classify_utility_signal(
        "用户喜欢喝美式咖啡",
        "好的，那我重新记一下",            # AI 回复无记忆片段
        user_message="你记错了，我根本不喝美式",  # 纠正词 → negative
    )
    assert sig == "negative"
    # 纠正词在用户消息、记忆指代也在用户消息（跨两段联合判定）→ negative
    sig2 = uf.classify_utility_signal(
        "用户喜欢喝美式咖啡",
        "你上次说喜欢喝美式咖啡对吧",     # AI 回复引用了记忆
        user_message="你记错了，我明明喜欢喝美式咖啡，拿铁才是后来才喝的",
    )
    assert sig2 == "negative"


def test_classify_positive_when_fragment_used():
    sig = uf.classify_utility_signal(
        "用户喜欢喝美式咖啡",
        "你上次说喜欢喝美式咖啡，今天要不要再点一杯",
    )
    assert sig == "positive"


def test_classify_neutral_when_unrelated():
    sig = uf.classify_utility_signal("用户喜欢喝美式咖啡", "今天天气不错")
    assert sig == "neutral"


# ─────────────────────────── 放宽判据（2026-09-21 任务2）───────────────────────────

def test_relaxed_each_single_signal_records():
    """两条通道的分工（A25② 收口后的口径，2026-10-05）：①纠正词只看用户消息、②引用只看 AI 回复。

    旧口径里的第 ③ 条（用户明确表态词）已撤：见 `test_表态词通道已撤_不再单独产生positive`。
    """
    mem = "用户喜欢喝美式咖啡"
    # ① 纠正词单命中（用户侧，无记忆片段共现）→ negative
    sig_correct = uf.classify_utility_signal(mem, "那我改一下", user_message="你记错了，我其实喝拿铁")
    assert sig_correct == "negative"
    # ② 记忆关键片段被**回复**引用 → positive
    sig_ref = uf.classify_utility_signal(mem, "你上次说喜欢喝美式咖啡，今天要不要再点一杯")
    assert sig_ref == "positive"
    # 旧 ③ 的样例（用户表态、回复没引用记忆）现在必须是 neutral：positive 只能来自"真的被用上"
    sig_att = uf.classify_utility_signal(mem, "好的，要不要再来一杯", user_message="说得对，你竟然还记得我喜欢喝美式")
    assert sig_att == "neutral"
    # 但"用户表态 + 回复确实引用了片段"仍走 ② 记 positive（不因撤③ 把该记的漏掉）
    sig_att_ref = uf.classify_utility_signal(mem, "你之前说喜欢喝美式咖啡来着", user_message="嗯，说得对")
    assert sig_att_ref == "positive"


def test_表态词通道已撤_不再单独产生positive():
    """A25②（2026-10-05）：25 个表态词在 2612 条真实用户消息里只命中 1 次，却贡献了 5 条误升权
    ⇒ 通道删除，词表也删（不留"定义了但没人用"的摆设）。"""
    mem = "用户喜欢喝美式咖啡"
    for u in ("被你说中了，我就爱喝美式", "你记性真好，居然还记得", "正合我意，你太懂我了", "没错没错"):
        assert uf.classify_utility_signal(mem, "嗯嗯", user_message=u) == "neutral", u
    assert not hasattr(uf, "_UTILITY_ATTITUDE_WORDS"), "词表应随之删除，别留在模块里当死代码"


def test_判定文本随回执落库_A27甲(mem_db):
    """A27甲：回执要能回答「当时到底读了什么才判成这样」。

    这条同时钉两面：① 带 evidence 时多两个键、且文本原样；② **不带 evidence 时 JSON 形态逐字节不变**
    （旧调用方与历史回执不受影响）。
    """
    import json as _json

    mid = asyncio.run(_seed_memory(mem_db, 40.0))
    ev = {"user_message": "你记错了，我喝拿铁", "ai_response": "那我改一下：你喜欢喝美式咖啡"}

    async def _go():
        await uf.apply_utility_feedback(1, 7, [(mid, "negative")], round_id=11, evidence=ev)

    asyncio.run(_go())
    details = asyncio.run(_read_receipt_details(mem_db, mid))
    assert details, "没写回执"
    d = details[0]
    assert d["evidence"] == ev, f"判定文本没原样落库：{d.get('evidence')}"
    assert d["signal"] == "negative" and d["round_id"] == 11 and d["user_id"] == 7 and d["skipped"] is False

    # 缺省不带 evidence ⇒ 一个键都不加（历史形态）
    async def _go2():
        await uf.apply_utility_feedback(1, 7, [(mid, "negative")])

    asyncio.run(_go2())
    d2 = asyncio.run(_read_receipt_details(mem_db, mid))[-1]
    assert set(d2) == {"memory_id", "signal", "round_id", "user_id", "skipped"}, f"多出键：{sorted(d2)}"
    assert _json.loads(uf._detail_json(mid, "negative", None, user_id=7)) == d2, "helper 与落库形态必须一致"


def test_判定文本按上限截断_不把回执撑成日志表(mem_db, monkeypatch):
    """截断发生在**入口**（schedule），序列化器只负责原样落库：所以这条走真路径，别只测 helper。

    上限的意义：留底是为了"事后能归因"，不是为了把聊天记录再存一份。
    """
    import app.utils.async_tasks as at

    mid = asyncio.run(_seed_memory(mem_db, 40.0))
    monkeypatch.setattr(uf, "_flag_on", lambda *a, **k: True)
    captured = {}
    monkeypatch.setattr(at, "spawn_background", lambda coro: captured.__setitem__("c", coro))

    uf.schedule_utility_feedback(
        1, 7, [{"id": mid, "content": "用户喜欢喝美式咖啡" + "内" * 900}],
        "你上次说喜欢喝美式咖啡" + "外" * 900,
        user_message="你记错了" + "话" * 900,
    )
    asyncio.run(captured["c"])
    d = asyncio.run(_read_receipt_details(mem_db, mid))[-1]
    assert set(d["evidence"]) == {"user_message", "ai_response"}
    assert len(d["evidence"]["user_message"]) == uf.UTILITY_EVIDENCE_MAX_CHARS, "用户侧文本没截断"
    assert len(d["evidence"]["ai_response"]) == uf.UTILITY_EVIDENCE_MAX_CHARS, "AI 侧文本没截断"
    assert d["evidence"]["user_message"].startswith("你记错了")


def test_relaxed_expanded_correction_words_fire():
    """扩大纠正词表（_UTILITY_EXTRA_CORRECT_WORDS）后，短纠正词也能触发 negative——都在用户消息侧。"""
    mem = "用户喜欢喝美式咖啡"
    for u in ("不对，我说的是别的", "搞反了，顺序不是这样的", "和我说的恰恰相反"):
        assert uf.classify_utility_signal(mem, "好，我改", user_message=u) == "negative", u


def test_relaxed_noise_chatter_stays_neutral():
    """噪声守卫：无关闲聊（不含纠正/表态/引用任一强信号）→ neutral，不误记。"""
    mem = "用户喜欢喝美式咖啡"
    assert uf.classify_utility_signal(mem, "今天天气不错，我们去散步吧") == "neutral"
    assert uf.classify_utility_signal(mem, "哈哈哈这个也太好笑了") == "neutral"
    # 纠正词/表态词未出现，纯叙述不触发
    assert uf.classify_utility_signal(mem, "刚才看了一会儿书，有点累了") == "neutral"


def test_classify_negative_takes_precedence_over_positive():
    # 用户改口（纠正词）＋ 回复引用了记忆片段（②本该 positive）→ 判 negative（纠正优先）
    sig = uf.classify_utility_signal(
        "用户喜欢喝美式咖啡",
        "你说过喜欢喝美式咖啡，要不要再来一杯",
        user_message="你记错了，我早改喝拿铁了",
    )
    assert sig == "negative"


# ─────────────── A25 方案 a：两段各扫各的（2026-10-04 实测病灶） ───────────────

def test_角色自己说记错了_不得判negative():
    """这条就是 10-04 实测抓出来的病灶：拼扫时**角色自己的措辞**被当成用户改口。

    生产原文（session 11，char13）：「……行，记错了，练背就练背。（顿了一下）发现bug就赶紧修…」
    含纠正词「错了」，但用户这轮只说了「那就练背吧」。旧口径据此把 3 条记忆各降权 0.8。
    """
    mem = "用户周三晚上要练背"
    ai_says = "……行，记错了，练背就练背。（顿了一下）发现bug就赶紧修，别拖到下午又忘了。"
    assert uf.classify_utility_signal(mem, ai_says, user_message="那就练背吧") == "neutral"
    # 同一条记忆，改口换成用户来说 → 必须记 negative（不是把负样本一刀切没了）
    assert uf.classify_utility_signal(mem, ai_says, user_message="不对，记错了，我练的是腿") == "negative"


def test_用户自己复述记忆片段_不算被用上():
    """②问的是「AI 用没用这条记忆」⇒ 只在回复里找片段。用户自己把记忆内容打出来不算用上。"""
    mem = "用户喜欢喝美式咖啡"
    assert uf.classify_utility_signal(mem, "今天想去散散步吗",
                                      user_message="我上次说我喜欢喝美式咖啡来着") == "neutral"
    assert uf.classify_utility_signal(mem, "你喜欢喝美式咖啡，附近有家店要去吗") == "positive"


def test_缺省user_message时只可能走引用通道():
    """老调用方／主动消息轮没有用户消息：① 一律不触发，只有 ②（回复引用记忆）能成立。

    样例是刻意挑来**区分通道**的（A25② 撤 ③ 后口径不变，只是"表态"那条已经不存在）：
    - AI 侧既有纠正词、又逐字引用了记忆 → positive（若 ① 还在扫 AI 侧就会翻成 negative）；
    - AI 侧有纠正词但没引用记忆 → neutral（旧口径这里是 negative，属误伤）；
    - AI 侧有原表态词但没引用记忆 → neutral（旧口径这里是 positive，属凭空加分——已随 ③ 一起消失）。
    """
    mem = "用户喜欢喝美式咖啡"
    assert uf.classify_utility_signal(mem, "你记错了，其实你说过喜欢喝美式咖啡") == "positive"
    assert uf.classify_utility_signal(mem, "你记错了，我重新想想这件事") == "neutral"
    assert uf.classify_utility_signal(mem, "说得对，你竟然还记得我上次讲的那件事") == "neutral"
    assert uf.classify_utility_signal(mem, "") == "neutral"


def test_core_snippet_strips_markers():
    assert uf._core_snippet("[记录于 2026-08-16][往事] 用户喜欢喝美式咖啡") == "用户喜欢喝美式咖啡"


# ─────────────────────────── flag 关 = 零写入 ───────────────────────────

def test_flag_off_does_not_schedule_apply(monkeypatch):
    called = []
    monkeypatch.setattr(uf, "_flag_on", lambda *a, **k: False)
    monkeypatch.setattr(uf, "apply_utility_feedback", lambda *a, **k: called.append(1))
    uf.schedule_utility_feedback(
        1, 1, [{"id": 5, "content": "用户喜欢喝美式咖啡"}], "你上次说喜欢喝美式咖啡"
    )
    assert called == []  # flag 关：apply 永不被调度（零写入）


def test_flag_off_schedule_returns_without_exception():
    # flag 默认关：即便传入召回与回复也不抛、不写
    uf.schedule_utility_feedback(
        1, 1, [{"id": 5, "content": "x"}], "回复文本"
    )


def test_is_enabled_delegates_to_flag(monkeypatch):
    # nodes 侧早判用的公开入口：与内部 _flag_on 同源，不会分叉（flag 关时调用方零 state 改动）
    monkeypatch.setattr(uf, "_flag_on", lambda *a, **k: False)
    assert uf.is_enabled() is False
    monkeypatch.setattr(uf, "_flag_on", lambda *a, **k: True)
    assert uf.is_enabled() is True


# ─────────────────────────── apply 作用 + 回执 ───────────────────────────

async def _seed_memory(factory, importance=40.0, is_archived=False):
    from app.models.memory import Memory
    async with factory() as db:
        m = Memory(
            user_id=1, character_id=1, memory_type="user_info",
            content="用户喜欢喝美式咖啡", importance=importance, is_archived=is_archived,
        )
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return m.id


async def _read_memory(factory, mid):
    from app.models.memory import Memory
    async with factory() as db:
        m = await db.get(Memory, mid)
        return float(m.importance) if m else None


async def _count_receipts(factory, mid):
    from app.models.memory import MemoryWriteReceipt
    async with factory() as db:
        rows = (await db.execute(
            select(MemoryWriteReceipt).where(MemoryWriteReceipt.memory_id == mid)
        )).scalars().all()
        return [(r.action, r.reason) for r in rows]


async def _read_receipt_details(factory, mid):
    import json as _json
    from app.models.memory import MemoryWriteReceipt
    async with factory() as db:
        rows = (await db.execute(
            select(MemoryWriteReceipt).where(MemoryWriteReceipt.memory_id == mid)
        )).scalars().all()
        return [_json.loads(r.detail_json) for r in rows]


def test_apply_positive_nudges_importance_and_writes_receipt(mem_db):
    mid = asyncio.run(_seed_memory(mem_db, 40.0))

    asyncio.run(uf.apply_utility_feedback(1, 1, [(mid, "positive")]))

    imp = asyncio.run(_read_memory(mem_db, mid))
    assert imp == pytest.approx(40.0 + uf.UTILITY_POSITIVE_IMPORTANCE_DELTA)
    receipts = asyncio.run(_count_receipts(mem_db, mid))
    assert receipts == [("utility_feedback", "positive")]


def test_apply_negative_lowers_importance(mem_db):
    mid = asyncio.run(_seed_memory(mem_db, 40.0))

    asyncio.run(uf.apply_utility_feedback(1, 1, [(mid, "negative")]))

    imp = asyncio.run(_read_memory(mem_db, mid))
    assert imp == pytest.approx(40.0 - uf.UTILITY_NEGATIVE_IMPORTANCE_DELTA)
    receipts = asyncio.run(_count_receipts(mem_db, mid))
    assert receipts == [("utility_feedback", "negative")]


def test_apply_neutral_skips_and_writes_no_receipt(mem_db):
    mid = asyncio.run(_seed_memory(mem_db, 40.0))

    asyncio.run(uf.apply_utility_feedback(1, 1, [(mid, "neutral")]))

    imp = asyncio.run(_read_memory(mem_db, mid))
    assert imp == pytest.approx(40.0)  # 不变
    receipts = asyncio.run(_count_receipts(mem_db, mid))
    assert receipts == []


def test_apply_multiple_signals(mem_db):
    m1 = asyncio.run(_seed_memory(mem_db, 40.0))
    m2 = asyncio.run(_seed_memory(mem_db, 40.0))

    asyncio.run(uf.apply_utility_feedback(1, 1, [(m1, "positive"), (m2, "negative")]))

    assert asyncio.run(_read_memory(mem_db, m1)) == pytest.approx(40.0 + uf.UTILITY_POSITIVE_IMPORTANCE_DELTA)
    assert asyncio.run(_read_memory(mem_db, m2)) == pytest.approx(40.0 - uf.UTILITY_NEGATIVE_IMPORTANCE_DELTA)


def test_apply_archived_memory_keeps_importance_but_leaves_receipt(mem_db):
    """已归档/不存在的记忆：不改权重，但仍留一条可追溯回执（detail 标 skipped=true）。"""
    mid = asyncio.run(_seed_memory(mem_db, 40.0, is_archived=True))

    asyncio.run(uf.apply_utility_feedback(1, 1, [(mid, "positive")]))

    assert asyncio.run(_read_memory(mem_db, mid)) == pytest.approx(40.0)  # 权重不变
    details = asyncio.run(_read_receipt_details(mem_db, mid))
    assert len(details) == 1
    assert details[0]["signal"] == "positive"
    assert details[0]["skipped"] is True
    assert details[0]["user_id"] == 1        # 回执记「谁」


def test_apply_missing_memory_is_silent_and_leaves_receipt(mem_db):
    """记忆 id 不存在（已物理删）：不抛、留 skipped 回执。"""
    asyncio.run(uf.apply_utility_feedback(1, 1, [(987654, "negative")]))
    details = asyncio.run(_read_receipt_details(mem_db, 987654))
    assert len(details) == 1
    assert details[0]["skipped"] is True


# ─────────────────────────── 异步失败不影响主流程 ───────────────────────────

class _BoomFactory:
    """async_session_factory 替身：进入即抛，模拟 DB 不可用。"""

    def __call__(self):
        return self

    async def __aenter__(self):
        raise RuntimeError("db down")

    async def __aexit__(self, *a):
        return False


def test_apply_async_failure_is_silent(monkeypatch):
    monkeypatch.setattr(dbmod, "async_session_factory", _BoomFactory())
    # 不应抛出异常（失败静默、不阻塞主链路）
    asyncio.run(uf.apply_utility_feedback(1, 1, [(9, "positive")]))


# ─────────────────────────── schedule(flag 开) 端到端 ───────────────────────────

def test_schedule_flag_on_writes_receipt(mem_db, monkeypatch):
    mid = asyncio.run(_seed_memory(mem_db, 40.0))
    monkeypatch.setattr(uf, "_flag_on", lambda *a, **k: True)
    # spawn_background 替身为同步执行，便于断言（不依赖事件循环调度时机）
    captured = {}

    def _run_sync(coro):
        captured["coro"] = coro
        return None

    # schedule 内部从 app.utils.async_tasks 导入 spawn_background，patch 该名字
    import app.utils.async_tasks as at
    monkeypatch.setattr(at, "spawn_background", _run_sync)

    uf.schedule_utility_feedback(
        1, 1, [{"id": mid, "content": "用户喜欢喝美式咖啡"}], "你上次说喜欢喝美式咖啡"
    )
    assert "coro" in captured
    asyncio.run(captured["coro"])  # 真正执行 apply
    receipts = asyncio.run(_count_receipts(mem_db, mid))
    assert receipts and receipts[0][1] == "positive"


def test_gray_chars_gate(monkeypatch):
    """灰度口径（09-18 用户拍板：先只在 char13 观察）：总开关开 **且** 角色命中白名单才生效。

    - 开关关 → 白名单角色也不生效（回滚＝关总开关，无需改代码）；
    - 开关开 → 仅 char13 生效，其余角色零行为变化；
    - 角色缺失/非法 → 保守 False。
    """
    from app.agent.loop import AGENT_FLAGS

    monkeypatch.setitem(AGENT_FLAGS, "memory_utility_feedback", False)
    assert uf.is_enabled(13) is False
    assert uf.is_enabled(6) is False

    monkeypatch.setitem(AGENT_FLAGS, "memory_utility_feedback", True)
    assert uf.is_enabled(13) is True
    assert uf.is_enabled(6) is False
    assert uf.is_enabled(None) is False
    assert uf.is_enabled("13") is True
    assert uf.is_enabled("abc") is False
    assert uf.UTILITY_FEEDBACK_GRAY_CHARS == frozenset({13})


def test_schedule_notes_neutral_and_scheduled(monkeypatch):
    """2026-09-18 补的可观测性：区分「判 neutral 未写回执」与「判出信号已调度」。"""
    import app.memory.observability as obs
    import app.utils.async_tasks as at

    seen: list = []
    monkeypatch.setattr(obs, "obs_event", lambda *a, **k: seen.append((a, k)))
    monkeypatch.setattr(uf, "_flag_on", lambda *a, **k: True)
    monkeypatch.setattr(at, "spawn_background", lambda c: c.close())
    recalled = [{"id": 7, "content": "用户喜欢喝美式咖啡"}]

    monkeypatch.setattr(uf, "classify_utility_signal", lambda *a, **k: "neutral")
    uf.schedule_utility_feedback(character_id=13, user_id=1, recalled=recalled, ai_response="好的", user_message="在吗")
    assert any("utility_feedback_note" in str(x) and "neutral" in str(x) for x in seen), seen

    seen.clear()
    monkeypatch.setattr(uf, "classify_utility_signal", lambda *a, **k: "positive")
    uf.schedule_utility_feedback(character_id=13, user_id=1, recalled=recalled, ai_response="好的", user_message="在吗")
    assert any("utility_feedback_note" in str(x) and "scheduled" in str(x) for x in seen), seen