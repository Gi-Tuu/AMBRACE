# -*- coding: utf-8 -*-
"""M3 契约/策略纯函数：公开资料、双闸、节律、板块、输出分类、绑定归属。"""
import json
import pathlib
import random
import sys

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
sys.path.insert(0, str(PLUGIN))

import garden_policy as policy  # noqa: E402
import garden_state as store  # noqa: E402
import garden_transport as transport  # noqa: E402


def cfg(**kw):
    base = {"garden_forum_allow_llm": False, "garden_forum_allow_write": False}
    base.update(kw)
    return base


def profile(**kw):
    p = {"name": "合成小叶", "bio": "喜欢浇水", "interests": ["陶盆", "薄荷"]}
    p.update(kw)
    return p


def test_manifest_defaults_are_closed():
    man = json.loads((PLUGIN / "manifest.json").read_text(encoding="utf-8"))
    assert man["config"]["garden_forum_on"] is False
    assert man["config"]["garden_forum_allow_llm"] is False
    assert man["config"]["garden_forum_allow_write"] is False
    assert man["config"]["garden_forum_daily_post"] == 0
    assert man["config"]["garden_forum_interval_min"] == 20
    assert man["config"]["garden_forum_interval_max"] == 150
    assert man["config"]["public_profiles"] == {}
    assert "persona:read" not in man["permissions"]


def test_manifest_passes_kernel_validator():
    from app.plugins.manifest import validate_manifest
    man = json.loads((PLUGIN / "manifest.json").read_text(encoding="utf-8"))
    assert validate_manifest(man) is None


def test_gates_require_explicit_true():
    assert not policy.allow_llm(cfg())
    assert not policy.allow_write(cfg())
    assert not policy.allow_llm({"garden_forum_allow_llm": 1})
    assert policy.allow_llm({"garden_forum_allow_llm": True})
    assert policy.allow_write({"garden_forum_allow_write": True})


def test_public_profile_accepts_allowlist():
    got = policy.public_profile({"public_profiles": {"3": profile()}}, "3")
    assert got["name"] == "合成小叶"
    assert got["bio"] == "喜欢浇水"
    assert got["interests"] == ["陶盆", "薄荷"]


def test_public_profile_missing_does_not_fall_back():
    assert policy.public_profile({"public_profiles": {}}, "3") is None
    assert policy.public_profile({}, "3") is None
    assert policy.public_profile({"public_profiles": {"3": {"bio": "只有简介"}}}, "3") is None


def test_public_profile_rejects_private_keys():
    p = profile(personality="私密人格", memory="私密记忆")
    assert policy.public_profile({"public_profiles": {"3": p}}, "3") is None


def test_public_profile_rejects_credential_sentinel():
    p = profile(bio="api_key=AK_SENTINEL")
    assert policy.public_profile({"public_profiles": {"3": p}}, "3") is None


def test_public_profile_bad_types_rejected():
    assert policy.public_profile({"public_profiles": {"3": {"name": 3}}}, "3") is None
    assert policy.public_profile({"public_profiles": {"3": {"name": "x", "interests": "a"}}}, "3") is None


def test_interval_random_bounds_and_tmin_compat():
    rng = random.Random(7)
    for _ in range(200):
        sec = policy.choose_interval_sec({"garden_forum_tmin": 60}, rng)
        assert 60 * 60 <= sec <= 150 * 60
    rng = random.Random(7)
    for _ in range(200):
        sec = policy.choose_interval_sec({"garden_forum_interval_min": 20,
                                          "garden_forum_interval_max": 25}, rng)
        assert 20 * 60 <= sec <= 25 * 60


def test_interval_bad_values_clamped():
    sec = policy.choose_interval_sec({"garden_forum_interval_min": -5,
                                      "garden_forum_interval_max": 9999}, random.Random(1))
    assert 20 * 60 <= sec <= 150 * 60


def test_beat_characters_cap_and_random():
    assert 1 <= policy.choose_beat_characters(0, random.Random(1)) <= 1
    assert 1 <= policy.choose_beat_characters(2, random.Random(1)) <= 2
    assert 1 <= policy.choose_beat_characters(99, random.Random(1)) <= 3


def test_pick_circle_requires_allowed_and_state():
    circles = [{"slug": "watering", "name": "浇水"}, {"slug": "night", "name": "夜"}]
    assert policy.pick_circle(circles, ["watering"], ["watering"]) == "watering"
    assert policy.pick_circle(circles, ["watering"], ["night"]) is None
    assert policy.pick_circle(circles, ["missing"], ["watering"]) is None
    assert policy.pick_circle(circles, [], ["watering"]) is None
    assert policy.pick_circle(circles, ["watering"], []) is None
    assert policy.pick_circle([], ["watering"], ["watering"]) is None


def test_classify_generation_distinguishes_states():
    assert policy.classify_generation("无") == "explicit_silence"
    assert policy.classify_generation("") == "empty_unknown"
    assert policy.classify_generation(None) == "empty_unknown"
    assert policy.classify_generation("正文", truncated=True) == "truncated"
    assert policy.classify_generation("", error="boom") == "error"
    assert policy.classify_generation("", timed_out=True) == "timeout"
    assert policy.classify_generation("正文") == "ok"


def test_target_from_state_rejects_invented_ids():
    assert policy.target_from_state("comment", {"id": 1, "post_id": 2}) == {"post_id": 2, "id": 1}
    assert policy.target_from_state("comment", {"id": 1}) is None
    assert policy.target_from_state("dm", {"from_agent": "a"}) == {"from_agent": "a"}
    assert policy.target_from_state("dm", {}) is None


def test_binding_matches_site_and_owner():
    doc = {"bound": {"3": {"api_key": "AK", "user_id": 1, "base_url": "http://127.0.0.1:8080"}}}
    assert store.binding_matches(doc, "3", "http://127.0.0.1:8080", 1)
    assert not store.binding_matches(doc, "3", "http://127.0.0.1:9999", 1)
    assert not store.binding_matches(doc, "3", "http://127.0.0.1:8080", 2)
    assert not store.binding_matches(doc, "4", "http://127.0.0.1:8080", 1)


def test_transport_requires_https_except_loopback():
    assert transport.normalize_base("https://forum.example/") == "https://forum.example"
    assert transport.normalize_base("http://127.0.0.1:8080") == "http://127.0.0.1:8080"
    with pytest.raises(ValueError):
        transport.normalize_base("http://forum.example")
    with pytest.raises(ValueError):
        transport.normalize_base("https://user:pass@forum.example")
    with pytest.raises(ValueError):
        transport.normalize_base("https://forum.example/?q=1")


def test_transport_origin_pin():
    assert transport.same_origin("https://a.example/x", "https://a.example")
    assert not transport.same_origin("https://evil.example/x", "https://a.example")
    assert not transport.same_origin("http://a.example/x", "https://a.example")


def test_transport_rejects_cross_origin_redirect():
    import urllib.request
    handler = transport.PinnedRedirect("https://a.example")
    req = urllib.request.Request("https://a.example/api/x")
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(req, None, 302, "Found", {},
                                 "https://evil.example/api/x")


def test_classify_send_result_5xx_is_unknown_not_failed():
    assert policy.classify_send_result({"ok": False, "code": "internal_error", "http_status": 500}) == "unknown"
    assert policy.classify_send_result({"ok": False, "code": "http_503"}) == "unknown"
    assert policy.classify_send_result({"ok": False, "code": "bad_request", "http_status": 400}) == "failed"
    assert policy.classify_send_result({"ok": True}) == "success"
    assert policy.classify_send_result(None) == "unknown"


def test_usage_limits_and_synthetic_day_boundary():
    doc = {}
    now = 1793000000.0
    assert policy.usage_check(doc, now, "gen", 1, 2)
    policy.usage_inc(doc, now, "gen")
    assert not policy.usage_check(doc, now, "gen", 1, 2)
    assert policy.usage_check(doc, now + 60, "gen", 1, 2)       # 新拍重置 beat，但日额度已用 1
    policy.usage_inc(doc, now + 60, "gen")
    assert not policy.usage_check(doc, now + 120, "gen", 1, 2)  # 日额度用完
    assert policy.usage_check(doc, now + 24 * 3600, "gen", 1, 2)  # 合成跨日清零


def test_server_wait_never_shortened():
    import garden_beat as beat
    assert beat.next_wait(12 * 3600, 900) >= 12 * 3600
    assert beat.next_wait(-5, 900) == 900


def test_pending_success_blocks_same_action_but_new_action_allowed():
    doc = {}
    pid = policy.pending_id("3", "comment", 11)
    policy.transition(doc, pid, "success", now=1)
    assert not policy.can_send(doc, pid)
    assert policy.can_send(doc, policy.pending_id("3", "comment", 12))


def teardown_module(module):
    while str(PLUGIN) in sys.path:
        sys.path.remove(str(PLUGIN))
