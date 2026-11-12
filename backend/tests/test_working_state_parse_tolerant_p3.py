# -*- coding: utf-8 -*-
"""P3 合同：工作记忆 JSON 容错解析（flag `working_state_parse_tolerant_v1` 默认关）。

背景：生产 `working_state_skipped` 中 89.8% 是 `bad_json`（模型输出夹带文字／被 600 token 截断），
导致工作记忆从未落库。修复＝去围栏＋取首个 `{` 到末个 `}`＋失败重试一次＋预算 900，全部由 flag 控制。
"""
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
FLAG = "working_state_parse_tolerant_v1"


def test_parse_pure_json():
    from app.memory.working_state import parse_json_tolerant

    assert parse_json_tolerant('{"ongoing": []}') == {"ongoing": []}


def test_parse_fenced_and_prose_around():
    from app.memory.working_state import parse_json_tolerant

    assert parse_json_tolerant('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_tolerant('好的，结果如下：{"a": 2} 希望有帮助') == {"a": 2}
    assert parse_json_tolerant('```\n{"b": 3}\n``` 以上') == {"b": 3}


def test_parse_rejects_truncated_and_garbage():
    from app.memory.working_state import parse_json_tolerant

    assert parse_json_tolerant('{"ongoing": [{"topic": "半截') is None, "无闭合括号＝截断"
    assert parse_json_tolerant("这里没有 JSON") is None
    assert parse_json_tolerant("") is None
    assert parse_json_tolerant(None) is None


def test_flag_default_off_and_helpers():
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import working_state as ws

    assert AGENT_FLAGS.get(FLAG) is False, "默认关＝旧路径逐字不变"
    assert ws.PARSE_TOLERANT_FLAG == FLAG
    assert ws.parse_tolerant_enabled() is False


def test_service_wiring_ratchets():
    src = (APP / "application" / "working_state_service.py").read_text(encoding="utf-8")
    assert "parse_tolerant_enabled()" in src, "服务层必须按 flag 分档"
    assert "parse_json_tolerant(" in src, "容错档必须走容错解析"
    assert "max_tokens=900 if _tol else 600" in src, "容错档预算 900；关档保持 600"
    assert '"bad_json_truncated" if "}" not in _raw_txt else "bad_json_text"' in src, "失败原因要细分"
    # 关档旧路径逐字保留（严格 json.loads + bad_json）
    assert "desired = ws.validate_desired(json.loads(raw))" in src
    assert '"reason": "bad_json"}' in src
