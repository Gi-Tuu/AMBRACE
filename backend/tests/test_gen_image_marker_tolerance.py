# -*- coding: utf-8 -*-
"""生图标记解析容错 + 落库清洗（P0'，2026-09-10）针对性回归。

现场（真机 session 11）：LLM 输出漏写 [/IMG_TEXT]、[/GEN_IMAGE]，旧「开+闭」成对正则提取失败，
既不触发生图也不剥标记，流式分块把标记段落当成两条独立 AI 消息落库（id 11521 / 11522）。

覆盖：
1. 无闭合 GEN_IMAGE：prompt 提取成功、展示文本无 [GEN_IMAGE] 残留；
2. 无闭合 IMG_TEXT：img_text 提取成功、展示文本无 [IMG_TEXT] 残留；
3. 闭合标签回归：取值/剥离与容错前完全一致（含跨行闭合配文）；
4. 组合场景（复刻现场）：clean_text 精确等于正文、img_text/prompt 正确，且生图触发恰好一次；
5. 落库无泄漏：流式分块落库路径（_persist_ai_chunks）后消息文本中标记出现次数为 0，
   纯标记块不再作为独立消息落库。

临时 SQLite 走 conftest 会话级沙箱库（不自建临时目录）。
"""
import asyncio
import re

from sqlalchemy import select

from app.agent import actions
from app.application.chat.streaming import _persist_ai_chunks
from app.db.database import async_session_factory
from app.models.chat import ChatMessage, ChatSession
from app.models.character import AICharacter
from app.models.user import User

_USER_ID = 9601
_CHAR_ID = 9602

# 现场原文（session 11，模型漏写两个闭合标签）
_LIVE_RAW = (
    "行，等着。画胖点，跟你喂的一个样。[IMG_TEXT] ……就这一张。\n"
    "[GEN_IMAGE] 一只圆滚滚的金棕色小仓鼠，抱着一颗向日葵籽，插画风格，浅木色调背景"
)
# 现场落库的两条 AI 消息（容错前的泄漏产物），用作落库清洗的输入样本
_LIVE_CHUNKS = [
    "行，等着。画胖点，跟你喂的一个样。[IMG_TEXT] ……就这一张。",
    "[GEN_IMAGE] 一只圆滚滚的金棕色小仓鼠，抱着一颗向日葵籽，插画风格，浅木色调背景",
]


# ── 1/2/3/4：解析容错（纯函数，无 DB）───────────────────────────────
def test_无闭合_gen_image_提取并剥离():
    clean, prompt, img_text = actions.extract_gen_image("正文\n[GEN_IMAGE] 一只猫")
    assert prompt == "一只猫"
    assert img_text is None
    assert clean == "正文"
    assert "[GEN_IMAGE]" not in clean


def test_无闭合_img_text_提取并剥离():
    clean, prompt, img_text = actions.extract_gen_image("正文[IMG_TEXT] 就这一张。")
    assert img_text == "就这一张。"
    assert prompt is None
    assert clean == "正文"
    assert "[IMG_TEXT]" not in clean


def test_闭合标签回归_取值与剥离不变():
    # 与容错前逐字一致（tests/test_agent_actions.py::test_extract_gen_image_含配文 同口径）
    clean, prompt, img_text = actions.extract_gen_image(
        "[IMG_TEXT]配文[/IMG_TEXT]a[GEN_IMAGE]图[/GEN_IMAGE]b"
    )
    assert (clean, prompt, img_text) == ("ab", "图", "配文")
    # 闭合分支优先：跨行配文仍整段取（不被「行尾」边界截断）
    clean2, prompt2, img_text2 = actions.extract_gen_image(
        "正文[IMG_TEXT]第一行\n第二行[/IMG_TEXT][GEN_IMAGE]画面\n描述[/GEN_IMAGE]"
    )
    assert img_text2 == "第一行\n第二行"
    assert prompt2 == "画面\n描述"
    assert clean2 == "正文"
    # strip_actions 对闭合标签的旧行为不变
    assert actions.strip_actions(
        "a[SEARCH]q[/SEARCH]b[GEN_IMAGE]p[/GEN_IMAGE]c[MEMO]m[/MEMO]d[timer:5m]e"
    ) == "abcde"


def test_无闭合不吞后续标记():
    # 无闭合 GEN_IMAGE 以「下一个标记」为界，MEMO 仍能独立解析
    clean, prompt, _ = actions.extract_gen_image("正文[GEN_IMAGE] 一只猫[MEMO]买菜[/MEMO]")
    assert prompt == "一只猫"
    assert actions.extract_memo(clean) == "买菜"
    assert actions.strip_actions("正文[GEN_IMAGE] 一只猫[MEMO]买菜[/MEMO]尾巴") == "正文尾巴"


def test_strip_actions_无闭合零残留():
    for raw in (
        "正文[GEN_IMAGE] 一只猫",
        "正文[IMG_TEXT] 就这一张。",
        _LIVE_RAW,
        "正文[/GEN_IMAGE]",  # 孤立闭合标签（开标签缺失的漏网产物）
    ):
        out = actions.strip_actions(raw)
        assert "[GEN_IMAGE]" not in out and "[IMG_TEXT]" not in out, f"标记残留：{out!r}"
        assert "[/GEN_IMAGE]" not in out and "[/IMG_TEXT]" not in out, f"闭合标签残留：{out!r}"


def test_组合场景_复刻现场_解析():
    clean, prompt, img_text = actions.extract_gen_image(
        "行，等着。[IMG_TEXT] ……就这一张。\n[GEN_IMAGE] 一只圆滚滚的仓鼠，插画风格"
    )
    assert clean == "行，等着。"
    assert img_text == "……就这一张。"
    assert prompt == "一只圆滚滚的仓鼠，插画风格"
    # 动作解析：GEN_IMAGE / IMG_TEXT 各恰好一条（不重复触发）
    acts = actions.parse_actions(_LIVE_RAW)
    kinds = [a.action_type for a in acts]
    assert kinds.count("GEN_IMAGE") == 1
    assert kinds.count("IMG_TEXT") == 1


def test_组合场景_生图触发恰好一次(monkeypatch):
    """提取 → 收尾触发：_run_post_processing 对同一轮只调用 _gen_image_flow 一次。"""
    import app.application.chat_service as cs

    calls: list[tuple] = []
    spawned: list[str] = []

    def _fake_gen_image_flow(user_id, character_id, session_id, prompt, img_text=None):
        calls.append((user_id, character_id, session_id, prompt, img_text))

        async def _noop():
            return None

        return _noop()

    def _fake_spawn(coro, *, name=None):
        # 不真正调度后台任务：记录并关闭协程，避免「never awaited」告警
        spawned.append(getattr(coro, "__name__", type(coro).__name__))
        try:
            coro.close()
        except Exception:
            pass
        return None

    monkeypatch.setattr(cs, "_gen_image_flow", _fake_gen_image_flow)
    monkeypatch.setattr(cs, "spawn_background", _fake_spawn)

    clean, prompt, img_text = actions.extract_gen_image(_LIVE_RAW)
    assert clean == "行，等着。画胖点，跟你喂的一个样。"

    asyncio.run(cs._run_post_processing(
        1, _USER_ID, _CHAR_ID, "给我画个芒芒", {}, clean, None, None,
        gen_prompt=prompt, img_text=img_text,
    ))
    assert len(calls) == 1, f"生图应触发恰好一次，实际 {len(calls)} 次"
    assert calls[0][3] == prompt
    assert calls[0][4] == img_text


# ── 5：落库无泄漏（走会话沙箱库）─────────────────────────────────
async def _seed_session() -> int:
    async with async_session_factory() as db:
        db.add(User(id=_USER_ID, username="marker_tol_u", nickname="标记容错用户"))
        db.add(AICharacter(id=_CHAR_ID, user_id=_USER_ID, name="标记容错角色"))
        await db.flush()
        sess = ChatSession(user_id=_USER_ID, character_id=_CHAR_ID)
        db.add(sess)
        await db.flush()
        await db.commit()
        return sess.id


async def _cleanup(session_id: int) -> None:
    async with async_session_factory() as db:
        for row in (await db.execute(
            select(ChatMessage).where(ChatMessage.session_id == session_id)
        )).scalars().all():
            await db.delete(row)
        sess = await db.get(ChatSession, session_id)
        if sess is not None:
            await db.delete(sess)
        char = await db.get(AICharacter, _CHAR_ID)
        if char is not None:
            await db.delete(char)
        user = await db.get(User, _USER_ID)
        if user is not None:
            await db.delete(user)
        await db.commit()


def test_落库无泄漏_流式分块路径():
    """复刻现场的两块（含未闭合标记）走 _persist_ai_chunks：落库文本零标记、纯标记块不落库。"""
    async def _run():
        session_id = await _seed_session()
        try:
            saved = await _persist_ai_chunks(
                session_id, {}, list(_LIVE_CHUNKS), "一只圆滚滚的仓鼠",
                None, None, character_id=_CHAR_ID,
            )
            # [GEN_IMAGE] 整段块被清成空 → 不落库，只剩一条正文消息
            assert len(saved) == 1, f"纯标记块不应落库，实际 {[s['content'] for s in saved]}"
            assert saved[0]["content"] == "行，等着。画胖点，跟你喂的一个样。"
            async with async_session_factory() as db:
                rows = (await db.execute(
                    select(ChatMessage).where(ChatMessage.session_id == session_id)
                )).scalars().all()
            texts = "".join((r.content or "") for r in rows)
            assert len(re.findall(r"\[GEN_IMAGE\]", texts)) == 0
            assert len(re.findall(r"\[IMG_TEXT\]", texts)) == 0
            assert len(re.findall(r"\[/GEN_IMAGE\]|\[/IMG_TEXT\]", texts)) == 0
        finally:
            await _cleanup(session_id)

    asyncio.run(_run())


def test_落库无泄漏_流式生成到落库全链路(monkeypatch):
    """模拟真流式：LLM 分块吐出现场原文 → chunker 切块 → 落库，全链路零标记且仍能触发生图。"""
    from app.agent import nodes

    async def _fake_stream(**kw):
        yield "行，等着。画胖点，跟你喂的一个样。"
        yield "[IMG_TEXT] ……就这一张。\n"
        yield "[GEN_IMAGE] 一只圆滚滚的金棕色小仓鼠，抱着一颗向日葵籽，浅木色调背景"

    monkeypatch.setattr(nodes, "chat_completion_stream", _fake_stream)

    deltas: list[str] = []

    async def _sink(event, payload):
        if event == "delta":
            deltas.append(payload["text"])

    state = {"context_messages": [{"role": "user", "content": "给我画个芒芒"}],
             "emotional_state": "", "temperature": 0.8}
    raw = asyncio.run(nodes._stream_generate(state, None, _sink))

    # 展示层（delta / 语义块）零标记
    assert "[IMG_TEXT]" not in "".join(deltas) and "[GEN_IMAGE]" not in "".join(deltas)
    blocks = state["stream_blocks"]
    assert blocks == ["行，等着。画胖点，跟你喂的一个样。"], f"块异常：{blocks}"
    # 生图触发源仍是原始响应（标记完整可提取，恰好一次）
    _, prompt, img_text = actions.extract_gen_image(raw)
    assert prompt and "仓鼠" in prompt
    assert img_text == "……就这一张。"

    async def _run():
        session_id = await _seed_session()
        try:
            saved = await _persist_ai_chunks(
                session_id, {}, list(blocks), prompt, None, None, character_id=_CHAR_ID,
            )
            assert len(saved) == 1
            async with async_session_factory() as db:
                rows = (await db.execute(
                    select(ChatMessage).where(ChatMessage.session_id == session_id)
                )).scalars().all()
            texts = "".join((r.content or "") for r in rows)
            assert len(re.findall(r"\[GEN_IMAGE\]|\[IMG_TEXT\]", texts)) == 0
        finally:
            await _cleanup(session_id)

    asyncio.run(_run())


def test_落库无泄漏_全标记回复不落空消息():
    """整条回复只有生图标记时：不落任何文本消息（图片消息由生图流程单独追加）。"""
    async def _run():
        session_id = await _seed_session()
        try:
            saved = await _persist_ai_chunks(
                session_id, {}, ["[GEN_IMAGE] 一只猫"], "一只猫",
                None, None, character_id=_CHAR_ID,
            )
            assert saved == []
            async with async_session_factory() as db:
                rows = (await db.execute(
                    select(ChatMessage).where(ChatMessage.session_id == session_id)
                )).scalars().all()
            assert rows == []
        finally:
            await _cleanup(session_id)

    asyncio.run(_run())
