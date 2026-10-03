# -*- coding: utf-8 -*-
"""A4 批 8 块 C M1 —— 「通知面短句提示」接到**生成前**。

钉住的硬口径（改任一即红）：
1. 探针 `is_session_online` **只读**：四种输入都正确，且**绝不改动连接池**；
2. 判定 `notify_surface = not is_session_online(session_id)`；
   **兜底**：`session_id is None` 或探针抛错 ⇒ **视为通知面 True**（不许静默变 False）；
3. **flag 关 ⇒ 逐字节旧 prompt，且一次都不调用探针**（把探针换成 raise 也不炸）；
4. 注入位置＝最后一条 system 之后；内容恒等于 `NOTIFY_SHAPE_HINT`（含 limit 占位替换）；
   只进 LLM 上下文，**不落库**；
5. R7：与微信渠道 hint 同轮时两条都在、短句在后、含优先级声明；
6. arbiter 调用点补传 `session_id`；旧调用点不传 ⇒ 不报错且 flag 关时行为不变。

不连生产库、不建表：全部 monkeypatch。
"""
from __future__ import annotations

import inspect

import pytest

from app.agent.nodes import NOTIFY_SHAPE_HINT, WECHAT_CHANNEL_HINT
from app.domain import message_shape as ms
from app.scheduling import message_generator as mg
from app.ws import connection_manager as cm


@pytest.fixture(autouse=True)
def _clean_pool():
    """每个用例前后都保证连接池是干净的空 dict（也用于断言探针没改它）。"""
    saved = dict(cm.connected_clients)
    cm.connected_clients.clear()
    yield
    cm.connected_clients.clear()
    cm.connected_clients.update(saved)


def _flag(monkeypatch, on: bool):
    monkeypatch.setattr(ms, "notify_shape_flag_on", lambda: on)


def _online(monkeypatch, value):
    monkeypatch.setattr(cm, "is_session_online", lambda sid: value)


# ───────────────── 1. 探针四态 + 不改连接池 ─────────────────

def test_probe_true_when_session_in_pool():
    cm.connected_clients[42] = object()
    assert cm.is_session_online(42) is True


def test_probe_false_when_session_absent():
    assert cm.is_session_online(4242) is False


def test_probe_false_for_none():
    assert cm.is_session_online(None) is False


def test_probe_false_on_bad_input():
    """非预期入参（不可转 int）⇒ False，绝不抛。"""
    assert cm.is_session_online("not-an-int") is False


def test_probe_never_mutates_pool():
    """★ 只读：探测前后连接池逐键相等。"""
    cm.connected_clients[7] = object()
    before = dict(cm.connected_clients)
    cm.is_session_online(7)
    cm.is_session_online(8)
    cm.is_session_online(None)
    assert dict(cm.connected_clients) == before


# ───────────────── 2. 判定 + 兜底 ─────────────────

def test_predict_flag_off_returns_false_flag_off(monkeypatch):
    _flag(monkeypatch, False)
    _online(monkeypatch, True)
    assert mg._predict_notify_surface(42) == (False, "flag_off")


def test_predict_flag_off_never_calls_probe(monkeypatch):
    """★ flag 关时**一次都不调探针**：把探针换成 raise 也必须不炸、仍返回 flag_off。"""

    def _boom(_sid):
        raise RuntimeError("probe must not be called")

    monkeypatch.setattr(cm, "is_session_online", _boom)
    _flag(monkeypatch, False)
    assert mg._predict_notify_surface(42) == (False, "flag_off")


def test_predict_no_session_falls_back_to_notify_surface(monkeypatch):
    """★ 兜底钉死：session_id is None ⇒ 视为通知面 True（不许静默变 False）。"""
    _flag(monkeypatch, True)
    _online(monkeypatch, True)
    assert mg._predict_notify_surface(None) == (True, "no_session")


def test_predict_online_is_not_notify_surface(monkeypatch):
    _flag(monkeypatch, True)
    _online(monkeypatch, True)
    assert mg._predict_notify_surface(42) == (False, "ws_probe")


def test_predict_offline_is_notify_surface(monkeypatch):
    _flag(monkeypatch, True)
    _online(monkeypatch, False)
    assert mg._predict_notify_surface(42) == (True, "ws_probe")


def test_predict_probe_error_falls_back_to_notify_surface(monkeypatch):
    """★ 探针抛错 ⇒ 视为通知面 True（多给一句提示的代价 < 通知被截断）。"""

    def _boom(_sid):
        raise RuntimeError("ws down")

    monkeypatch.setattr(cm, "is_session_online", _boom)
    _flag(monkeypatch, True)
    assert mg._predict_notify_surface(42) == (True, "probe_error")


# ───────────────── 3. flag 关 ⇒ 逐字节旧 prompt ─────────────────

def test_compose_flag_off_returns_same_object(monkeypatch):
    """flag 关 ⇒ 返回**同一个列表对象**（逐字节旧 prompt，连复制都不做）。"""
    _flag(monkeypatch, False)
    base = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    assert ms.compose_notify_shape_messages(base, notify_surface=True) is base


def test_compose_notify_false_returns_same_object(monkeypatch):
    _flag(monkeypatch, True)
    base = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    assert ms.compose_notify_shape_messages(base, notify_surface=False) is base


# ───────────────── 4. 注入形态 ─────────────────

def _composed(monkeypatch, messages):
    _flag(monkeypatch, True)
    return ms.compose_notify_shape_messages(messages, notify_surface=True)


def test_compose_inserts_after_last_system(monkeypatch):
    out = _composed(monkeypatch, [
        {"role": "system", "content": "S1"},
        {"role": "user", "content": "U"},
        {"role": "system", "content": "S2"},
        {"role": "user", "content": "U2"},
    ])
    hint_idx = next(i for i, m in enumerate(out) if "通知形式" in m["content"])
    assert hint_idx == 3, "提示必须插在最后一条 system（S2，index 2）之后 ⇒ index 3"
    assert out[2]["content"] == "S2"   # 前一条正是最后一条 system
    assert out[4]["content"] == "U2"   # 其余消息顺序不变


def test_compose_content_equals_hint_with_limit_replaced(monkeypatch):
    out = _composed(monkeypatch, [{"role": "system", "content": "S"},
                                  {"role": "user", "content": "U"}])
    hint = out[1]["content"]
    assert "{limit}" not in hint, "占位符必须被替换掉"
    assert hint == NOTIFY_SHAPE_HINT.format(limit=ms.NOTIFY_PREVIEW_LIMIT)


def test_compose_explicit_limit_is_used(monkeypatch):
    out = _composed(monkeypatch, [{"role": "system", "content": "S"}])
    assert str(ms.NOTIFY_PREVIEW_LIMIT) in out[1]["content"]


def test_compose_no_system_inserts_at_head(monkeypatch):
    out = _composed(monkeypatch, [{"role": "user", "content": "U"}])
    assert out[0]["role"] == "system" and "通知形式" in out[0]["content"]
    assert out[1] == {"role": "user", "content": "U"}


def test_compose_does_not_mutate_input(monkeypatch):
    base = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    snapshot = list(base)
    _composed(monkeypatch, base)
    assert base == snapshot


def test_compose_is_pure_no_db_write(monkeypatch):
    """★ 不落库断言：拼装过程不调用 save_memory / 不建 ChatMessage。"""
    import app.memory.service as mem_svc
    import app.models.chat as chat_models

    calls = {"n": 0}
    monkeypatch.setattr(mem_svc, "save_memory", lambda *a, **k: calls.__setitem__("n", 99))
    monkeypatch.setattr(chat_models, "ChatMessage",
                        lambda *a, **k: calls.__setitem__("n", 99))
    _composed(monkeypatch, [{"role": "system", "content": "S"}])
    assert calls["n"] == 0


# ───────────────── 5. R7 与微信 hint 共存 ─────────────────

def test_hint_contains_priority_clause():
    """R7：常量必须含优先级声明（与其它长度要求冲突时以本条为准）。"""
    assert "以本条为准" in NOTIFY_SHAPE_HINT
    assert NOTIFY_SHAPE_HINT.endswith("。")


def test_r7_both_hints_present_and_notify_last(monkeypatch):
    """R7：两条同时注入时都在，且短句提示在后。"""
    _flag(monkeypatch, True)
    base = [{"role": "system", "content": WECHAT_CHANNEL_HINT},
            {"role": "user", "content": "U"}]
    out = ms.compose_notify_shape_messages(base, notify_surface=True)
    contents = [m["content"] for m in out]
    assert WECHAT_CHANNEL_HINT in contents
    assert NOTIFY_SHAPE_HINT.format(limit=ms.NOTIFY_PREVIEW_LIMIT) in contents
    wechat_idx = contents.index(WECHAT_CHANNEL_HINT)
    notify_idx = contents.index(NOTIFY_SHAPE_HINT.format(limit=ms.NOTIFY_PREVIEW_LIMIT))
    assert notify_idx > wechat_idx, "短句提示必须在微信 hint 之后"


# ───────────────── 6. 接线：签名 + arbiter 调用点 ─────────────────

def test_generate_proactive_event_accepts_optional_session_id():
    """新增的是**可选** kwarg：默认 None ⇒ 旧调用点不用改、不报错。"""
    sig = inspect.signature(mg.generate_proactive_event)
    param = sig.parameters["session_id"]
    assert param.default is None
    assert param.kind is inspect.Parameter.KEYWORD_ONLY or param.default is None


def test_arbiter_call_site_passes_session_id():
    """调用点必须补传 session_id（该字段本就在 candidate 上）。

    A20 批 4b（2026-10-02）：outreach 分支体从 arbiter 下沉到 executors/outreach.py，
    调用点跟着搬走 ⇒ 源码锚改读 outreach 模块（原意不变：漏传 session_id 仍是回归）。
    """
    src = inspect.getsource(__import__("app.scheduling.executors.outreach", fromlist=["x"]))
    assert 'session_id=candidate["session_id"]' in src


def test_old_call_site_without_session_id_still_works(monkeypatch):
    """旧调用点不传 session_id ⇒ 不报错；flag 关时 prompt 逐字节不变。"""
    _flag(monkeypatch, False)
    base = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    # 模拟未传 session_id 的旧路径：判定仍可调用，且 flag 关 ⇒ 不加提示
    surface, source = mg._predict_notify_surface(None)
    assert (surface, source) == (False, "flag_off")
    assert ms.compose_notify_shape_messages(base, notify_surface=surface) is base


def test_probe_importable_from_ws_module():
    """探针挂在 ws 连接池模块上（单一事实源），可直接 import 使用。"""
    assert callable(cm.is_session_online)
    assert inspect.iscoroutinefunction(cm.is_session_online) is False, "探针是同步只读判定"
