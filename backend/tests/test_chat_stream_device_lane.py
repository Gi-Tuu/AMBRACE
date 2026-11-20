import asyncio

from app.application.chat import streaming


def test_device_control_turn_detection():
    assert streaming._is_device_control_turn("帮我看一下手机界面")
    assert streaming._is_device_control_turn("帮我点一下输入框")
    assert streaming._is_device_control_turn("[DEVICE_ACT]{}[/DEVICE_ACT]")
    assert not streaming._is_device_control_turn("今天吃什么")


def test_device_lane_emits_block_and_done(monkeypatch):
    async def _fake_chunked(*_a, **_kw):
        return {"chunks": [{"id": 1, "content": "已滑"}], "memories_updated": False}

    monkeypatch.setattr(
        "app.application.chat_service.send_and_receive_chunked", _fake_chunked
    )
    events = []

    async def _sink(event, payload):
        events.append((event, payload))

    asyncio.run(streaming._run_device_lane_chunked(
        session_id=1, user_id=3, character_id=13, content="往上滑一下",
        lang="zh", quote=None, extra_capabilities=None, tts=False,
        user_msg_id=9, sink=_sink,
    ))
    assert [e for e, _ in events] == ["block", "done"]
    assert events[0][1]["index"] == 0
    assert events[1][1]["message"]["content"] == "已滑"
