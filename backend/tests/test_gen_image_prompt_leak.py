# -*- coding: utf-8 -*-
"""批次四任务 2（P1-4，2026-09-16）：生图 prompt 穿帮——prompt 只进 meta，绝不进可见 content。

现场形态（生产库只读抽样）：消息 content 里直接存着
「[GEN_IMAGE] 傍晚的厨房，灶上一只砂锅咕嘟炖着红烧肉……生活感插画风格」整段生图 prompt，
meta 里另有 kind=ai_image / gen_image / prompt；生图失败或标记未剥离时前端会把 prompt 显示给用户。

本文件锁定四类行为：
1. 变体标记（全/半角方括号、标记内空白、大小写漂移、漏闭合）都能提取，prompt 走 meta 通道，
   clean_text 零标记、零 prompt；
2. 可见正文/配文若「就是 prompt 本身」→ 一律判空（裸 prompt 永不进 content）；
3. 生图失败/权限拒绝/额度用尽 → 只发占位文案，不露 prompt 与内部标记；
4. image_gen 工具入口落库前同源清洗（prompt 去残片、裸 prompt 不作配文）。
"""
import asyncio

from app.agent import actions
from app.tools.builtin.image_tool import _execute_image

_PROMPT = "傍晚的厨房，灶上一只砂锅咕嘟炖着红烧肉，暖黄灯光，生活感插画风格"
_LIVE_CONTENT = f"[GEN_IMAGE] {_PROMPT}"


# ────────────────── 1：标记变体容错 + 零残留 ──────────────────

def test_变体标记_全角半角空白大小写都提取():
    for raw in (
        f"[GEN_IMAGE]{_PROMPT}[/GEN_IMAGE]",
        f"【GEN_IMAGE】{_PROMPT}【/GEN_IMAGE】",
        f"[ GEN_IMAGE ] {_PROMPT}",
        f"【gen_image】{_PROMPT}",
        f"正文一句。\n[GEN_IMAGE] {_PROMPT}",   # 漏写闭合
    ):
        clean, prompt, _ = actions.extract_gen_image(raw)
        assert prompt == _PROMPT, raw
        assert "[GEN_IMAGE]" not in clean.upper() and "【GEN_IMAGE】" not in clean.upper()
        assert _PROMPT not in clean, raw


def test_单标记块_正文为空且无裸prompt():
    clean, prompt, _ = actions.extract_gen_image(_LIVE_CONTENT)
    assert prompt == _PROMPT
    assert clean == "", f"只含 prompt 的消息可见正文必须为空，实际={clean!r}"


def test_正文与prompt并存时正文保留():
    clean, prompt, _ = actions.extract_gen_image(f"行，等着。\n[GEN_IMAGE] {_PROMPT}")
    assert prompt == _PROMPT
    assert clean == "行，等着。"


def test_strip_image_residue_变体与孤立闭合():
    assert _PROMPT not in actions.strip_image_residue(_LIVE_CONTENT)
    assert _PROMPT not in actions.strip_image_residue(f"【GEN_IMAGE】{_PROMPT}")
    for raw in (_LIVE_CONTENT, "正文[/GEN_IMAGE]尾", "正文【/IMG_TEXT】尾"):
        out = actions.strip_image_residue(raw)
        assert "GEN_IMAGE" not in out.upper() and "IMG_TEXT" not in out.upper(), raw


def test_strip_stream_display_零生图残片():
    from app.agent.response_parser import strip_stream_display
    out = strip_stream_display(f"{_LIVE_CONTENT}\n{_PROMPT}？不，是我画的。")
    assert "GEN_IMAGE" not in out.upper()
    assert _PROMPT not in out


# ────────────────── 2：裸 prompt 永不进 content ──────────────────

def test_裸prompt不作配文():
    assert actions.sanitize_image_caption(f"[IMG_TEXT]{_PROMPT}[/IMG_TEXT]", _PROMPT) is None
    assert actions.sanitize_image_caption(_PROMPT, _PROMPT) is None
    # 真配文（含人设口语）保留
    assert actions.sanitize_image_caption("……就这一张。", _PROMPT) == "……就这一张。"


def test_is_bare_image_prompt_判定():
    assert actions.is_bare_image_prompt(_PROMPT, _PROMPT) is True
    assert actions.is_bare_image_prompt(f"[IMG_TEXT]{_PROMPT}", _PROMPT) is True
    assert actions.is_bare_image_prompt("行，等着。", _PROMPT) is False
    assert actions.is_bare_image_prompt("", _PROMPT) is False
    assert actions.is_bare_image_prompt(_PROMPT, None) is False   # prompt 缺失不误伤


def test_配文与prompt同现时正文不被吞():
    raw = f"行，等着。\n[IMG_TEXT]……就这一张。[/IMG_TEXT][GEN_IMAGE]{_PROMPT}[/GEN_IMAGE]"
    clean, prompt, img_text = actions.extract_gen_image(raw)
    assert clean == "行，等着。"
    assert prompt == _PROMPT
    assert img_text == "……就这一张。"


def test_配文照抄prompt_判为裸prompt剔除():
    # 现场形态：模型把同一段画面描述既当 IMG_TEXT 又当 GEN_IMAGE → 配文必须判空
    raw = f"[IMG_TEXT]{_PROMPT}[/IMG_TEXT][GEN_IMAGE]{_PROMPT}[/GEN_IMAGE]"
    clean, prompt, img_text = actions.extract_gen_image(raw)
    assert prompt == _PROMPT
    assert img_text is None, "照抄 prompt 的配文不得落库为 content"
    assert clean == ""


def test_超长配文一律不作为配文():
    # 配文提示词要求 12 字内；阈值与落库截断同口径（60 字）
    assert actions.sanitize_image_caption("喵" * 61, None) is None
    assert actions.sanitize_image_caption("喵" * 60, None) == "喵" * 60


def test_parse_actions_配文裸prompt被剔除():
    acts = actions.parse_actions(f"[IMG_TEXT]{_PROMPT}[/IMG_TEXT][GEN_IMAGE]{_PROMPT}[/GEN_IMAGE]")
    kinds = {a.action_type: a.payload for a in acts}
    assert kinds["GEN_IMAGE"]["prompt"] == _PROMPT
    assert "IMG_TEXT" not in kinds, "裸 prompt 不应作为图片配文动作输出"


# ────────────────── 3：失败路径不露 prompt/标记 ──────────────────

def _patch_permission_allow(monkeypatch):
    from app.application import permission_service

    async def _allow(*_a, **_k):
        return "allow"

    monkeypatch.setattr(permission_service, "check_mode", _allow)


def _no_trace(monkeypatch):
    """生图流程会写 Task Trace（后台落库）——用例里置空，避免残留后台任务与「loop closed」噪音。"""
    import app.agent.trace as trace

    monkeypatch.setattr(trace, "enqueue_task_log", lambda **_k: None)
    monkeypatch.setattr(trace, "new_task_id", lambda: "test-task")


def _capture_appends(monkeypatch):
    import app.application.chat.io as io
    captured = {"text": [], "image": []}

    async def _text(session_id, content):
        captured["text"].append(content)

    async def _image(session_id, image_url, prompt, content=None):
        captured["image"].append({"url": image_url, "prompt": prompt, "content": content})

    monkeypatch.setattr(io, "_append_ai_text_message", _text)
    monkeypatch.setattr(io, "_append_ai_image_message", _image)
    return captured


def test_生图失败_只发占位不露prompt(monkeypatch):
    from app.application.chat import tools as chat_tools
    from app.application import image_gen_service

    _patch_permission_allow(monkeypatch)
    _no_trace(monkeypatch)
    captured = _capture_appends(monkeypatch)

    async def _limit(_uid):
        return False

    class _Task:
        id = 1

    async def _create(*_a, **_k):
        return _Task()

    async def _run(_tid):
        return None  # 生成失败

    monkeypatch.setattr(image_gen_service, "check_daily_limit", _limit)
    monkeypatch.setattr(image_gen_service, "create_image_gen_task", _create)
    monkeypatch.setattr(image_gen_service, "run_image_gen_task", _run)

    asyncio.run(chat_tools._gen_image_flow(1, 2, 3, _PROMPT, None))
    assert captured["image"] == []
    assert len(captured["text"]) == 1
    msg = captured["text"][0]
    assert _PROMPT not in msg
    assert "GEN_IMAGE" not in msg.upper() and "IMG_TEXT" not in msg.upper()


def test_额度用尽_只发占位不露prompt(monkeypatch):
    from app.application.chat import tools as chat_tools
    from app.application import image_gen_service

    _patch_permission_allow(monkeypatch)
    _no_trace(monkeypatch)
    captured = _capture_appends(monkeypatch)

    async def _limit(_uid):
        return True

    monkeypatch.setattr(image_gen_service, "check_daily_limit", _limit)
    asyncio.run(chat_tools._gen_image_flow(1, 2, 3, _PROMPT, None))
    assert captured["image"] == []
    assert _PROMPT not in captured["text"][0]


def test_生成成功_配文裸prompt判空而prompt只进meta(monkeypatch):
    from app.application.chat import tools as chat_tools
    from app.application import image_gen_service

    _patch_permission_allow(monkeypatch)
    _no_trace(monkeypatch)
    captured = _capture_appends(monkeypatch)

    async def _limit(_uid):
        return False

    class _Task:
        id = 1

    async def _create(*_a, **_k):
        return _Task()

    async def _run(_tid):
        return "/uploads/gen_leak.png"

    monkeypatch.setattr(image_gen_service, "check_daily_limit", _limit)
    monkeypatch.setattr(image_gen_service, "create_image_gen_task", _create)
    monkeypatch.setattr(image_gen_service, "run_image_gen_task", _run)

    # 上游已把裸 prompt 清洗成 None（actions.sanitize_image_caption 的出口语义）
    caption = actions.sanitize_image_caption(_PROMPT, _PROMPT)
    asyncio.run(chat_tools._gen_image_flow(1, 2, 3, _PROMPT, caption))
    assert len(captured["image"]) == 1
    rec = captured["image"][0]
    assert rec["prompt"] == _PROMPT          # 画面描述只走 meta
    assert not rec["content"]                # content 不含 prompt


# ────────────────── 4：image_gen 工具入口同源清洗 ──────────────────

def _patch_tool_flow(monkeypatch):
    import app.application.chat.tools as chat_tools
    calls = []

    async def _fake_flow(user_id, character_id, session_id, prompt, img_text=None):
        calls.append({"prompt": prompt, "img_text": img_text})

    monkeypatch.setattr(chat_tools, "_gen_image_flow", _fake_flow)
    return calls


def test_工具入口_清掉prompt残片并拒空(monkeypatch):
    calls = _patch_tool_flow(monkeypatch)
    res = asyncio.run(_execute_image({"prompt": f"[/GEN_IMAGE]{_PROMPT}", "img_text": None},
                                     user_id=1, character_id=2, session_id=3))
    assert res["ok"] is True
    assert calls[0]["prompt"] == _PROMPT
    assert calls[0]["img_text"] is None

    res2 = asyncio.run(_execute_image({"prompt": "[/GEN_IMAGE]"}, user_id=1, character_id=2, session_id=3))
    assert res2["ok"] is False and len(calls) == 1


def test_工具入口_裸prompt不作配文(monkeypatch):
    calls = _patch_tool_flow(monkeypatch)
    asyncio.run(_execute_image({"prompt": _PROMPT, "img_text": _PROMPT},
                               user_id=1, character_id=2, session_id=3))
    assert calls[0]["prompt"] == _PROMPT
    assert calls[0]["img_text"] is None
    # 正常配文保留
    asyncio.run(_execute_image({"prompt": _PROMPT, "img_text": "……就这一张。"},
                               user_id=1, character_id=2, session_id=3))
    assert calls[1]["img_text"] == "……就这一张。"
