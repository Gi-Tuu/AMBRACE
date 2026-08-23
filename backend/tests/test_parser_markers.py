"""标记剥离测试（2026-08-14）：状态更新/自述更新从正文剥离，解析值正确"""
from app.agent.response_parser import parse_response


def _state(user_msg: str = ""):
    return {"character_info": {"bio": ""}, "user_message": user_msg}


def test_status_update_stripped():
    out = parse_response("我困了先睡了【状态更新：准备睡觉】", _state())
    assert out["status_update"] == "准备睡觉"
    assert "状态更新" not in out["ai_response"]
    assert out["ai_response"].strip() == "我困了先睡了"


def test_bio_update_stripped():
    out = parse_response("我好像变了【自述更新：更沉稳了】", _state())
    assert out["bio_update"] == "更沉稳了"
    assert "自述更新" not in out["ai_response"]


def test_memory_marker_still_works():
    out = parse_response("正文【记忆：用户喜欢咖啡】", _state())
    assert out["ai_response"].strip() == "正文"
    assert len(out["new_memories"]) == 1


def test_plain_text_unchanged():
    out = parse_response("今天天气真好", _state())
    assert out["ai_response"].strip() == "今天天气真好"
    assert out["status_update"] is None


def test_memo_marker_extract():
    from app.services.chat_service import _extract_memo
    assert _extract_memo("正文[MEMO]记得买猫粮[/MEMO]") == "记得买猫粮"
    assert _extract_memo("没有标记") is None
    assert _extract_memo("[MEMO][/MEMO]") is None


def test_cal_note_marker_extract():
    from app.services.chat_service import _extract_cal_note
    r = _extract_cal_note("正文[CAL_NOTE]2026-08-20 晚上一起做饭[/CAL_NOTE]")
    assert r is not None
    assert r[0] == "2026-08-20"
    assert r[1] == "晚上一起做饭"
    assert _extract_cal_note("没有标记") is None
