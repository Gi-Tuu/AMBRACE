"""知识边界/可见性（World & Cognition P2，2026-08-15）

核心约束：用户私聊 A 的事，B 的上下文不会出现，除非 A/用户公开告知。
"""
from app.events.schema import make_event, can_see, private_event, speaker_of


def test_private_event_visible_only_to_pair():
    evt = private_event(character_id=11, user_id=4, event_type="user.message",
                        speaker={"type": "user", "id": 4},
                        provenance={"origin": "user_message"},
                        data={"content": "我今天很难受"})
    assert can_see(4, evt) is True   # 用户可见
    assert can_see(11, evt) is True   # A（角色11）可见
    assert can_see(12, evt) is False  # B（角色12）不可见——私聊不扩散


def test_public_event_visible_to_all():
    evt = make_event("life.moment_published",
                     speaker={"type": "character", "id": 11},
                     audience=["public"],
                     provenance={"origin": "social_event"})
    assert can_see(12, evt) is True
    assert can_see(4, evt) is True


def test_speaker_of_mapping():
    assert speaker_of("user", 4) == {"type": "user", "id": 4}
    assert speaker_of("ai", 12) == {"type": "character", "id": 12}
