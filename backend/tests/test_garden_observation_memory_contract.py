# -*- coding: utf-8 -*-
"""P3 合成合同：观察≠长期记忆；来源/时间/租户/过期门禁；不自动写记忆。"""
import pathlib
import sys
import time

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
sys.path.insert(0, str(PLUGIN))
BASE = "https://garden.example.invalid"


def _doc():
    now = int(time.time())
    return {"observations": {"13": {"base_url": BASE, "remote_id": "sam", "user_id": 7,
                                    "read_at": now, "expires_at": now + 60,
                                    "source": "readonly_browse",
                                    "items": [{"id": 7, "title": "合成"}]}}}


def test_observation_scope_expiry_and_tenant():
    import garden_state as state
    d = _doc()
    assert state.observation_of(d, 13, BASE, "sam", 7, time.time()) is not None
    assert state.observation_of(d, 13, BASE, "other", 7, time.time()) is None
    assert state.observation_of(d, 13, BASE, "sam", 8, time.time()) is None
    assert state.observation_of(d, 13, "https://other.invalid", "sam", 7, time.time()) is None
    assert state.observation_of(d, 13, BASE, "sam", 7, time.time() + 120) is None
    assert state.observation_of({}, 13, BASE, "sam", 7, time.time()) is None


def test_observation_is_not_memory_field():
    import garden_state as state
    d = _doc()
    # 观察只在 observations；不写 memories / 长期记忆字段
    assert "observations" in d and "memories" not in d
    state.clear_observation(d, 13)
    assert d["observations"] == {}
    assert "memories" not in d


def test_no_save_on_expiration_check():
    import garden_state as state
    d = _doc()
    before = repr(d)
    state.observation_of(d, 13, BASE, "sam", 7, time.time() + 120)
    assert repr(d) == before
