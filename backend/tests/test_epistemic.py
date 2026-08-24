"""Epistemic Status 流转（World & Cognition P3，2026-08-15）

覆盖：对话记忆提取的认知状态标注（用户在场→FACT/user，仅AI→INFERRED/character）；
检索/注入侧的标注前缀（FACT 默认不标，INFERRED/PLANNED/UNVERIFIED 显式标注）。
"""
from app.events.schema import EPISTEMIC_FACT, EPISTEMIC_INFERRED, EPISTEMIC_PLANNED
from app.memory.extractor import _extract_epistemic, _resolve_speaker
from app.agent.context_builder import _epistemic_prefix


def test_extract_epistemic_user_present():
    spk_type, spk_id, epi = _extract_epistemic("我今天很难受", "怎么了？", 4, 11)
    assert spk_type == "user"
    assert spk_id == 4
    assert epi == EPISTEMIC_FACT


def test_extract_epistemic_ai_only():
    spk_type, spk_id, epi = _extract_epistemic("", "我猜你今天有点累", 4, 11)
    assert spk_type == "character"
    assert spk_id == 11
    assert epi == EPISTEMIC_INFERRED


def test_extract_epistemic_blank_user_whitespace():
    spk_type, _, epi = _extract_epistemic("   ", "你好", 4, 11)
    assert spk_type == "character"
    assert epi == EPISTEMIC_INFERRED


def test_epistemic_prefix():
    assert _epistemic_prefix(None) == ""
    assert _epistemic_prefix("FACT") == ""
    assert _epistemic_prefix("INFERRED") == "[INFERRED] "
    assert _epistemic_prefix(EPISTEMIC_PLANNED) == "[PLANNED] "
    assert _epistemic_prefix("UNVERIFIED") == "[UNVERIFIED] "


def test_resolve_speaker_角色偏好():
    spk_type, spk_id, epi = _resolve_speaker("我喜欢吃辣", "我今天想吃辣的", "好呀", 4, 11)
    assert (spk_type, spk_id) == ("character", 11)


def test_resolve_speaker_用户偏好():
    spk_type, spk_id, epi = _resolve_speaker("用户喜欢喝美式咖啡", "用户说喜欢喝美式", "记住啦", 4, 11)
    assert (spk_type, spk_id) == ("user", 4)


def test_resolve_speaker_无主语回退():
    spk_type, _, _ = _resolve_speaker("喜欢下雨天", "嗯", "我也是", 4, 11)
    assert spk_type == "user"  # 用户消息在场回退 user
