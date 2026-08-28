# -*- coding: utf-8 -*-
"""真心话大冒险 fallback_action 专项回归测试。

覆盖 v3.3.8 遗留 bug：fallback_action 在 answer 阶段不区分 truth/dare，
大冒险时错误返回 answer_truth 导致引擎拒绝、AI 永久卡死。

测试分两层：
1. 引擎单元测试：直接验证 fallback_action 返回值；
2. API 集成测试：mock ai_decide 返回非法动作，验证 _resume_ai_turns
   走 fallback 后游戏不卡死、回合正确推进。
"""
import asyncio

# 复用 test_game_api 的 fixture 和真实 resume
from tests.test_game_api import game_api_db, _make_client, _REAL_RESUME  # noqa: F401


# ──────────────────────────────────────────────
# 引擎单元测试：fallback_action 返回值
# ──────────────────────────────────────────────

class _FakePlayer:
    def __init__(self, seat, name, player_type="ai", user_id=None):
        self.seat = seat
        self.name = name
        self.role = "player"
        self.alive = True
        self.is_spectator = False
        self.score = 0
        self.private_json = "{}"
        self.player_type = player_type
        self.character_id = 101 if player_type == "ai" else None
        self.user_id = user_id


class _FakeSession:
    def __init__(self):
        self.id = 999
        self.user_id = 1
        self.game_type = "truth_or_dare"
        self.round = 1
        self.phase = "choose"
        self.status = "playing"


def _make_engine():
    """构造一个双人真心话大冒险引擎（seat0=用户, seat1=AI）。"""
    from app.games.truth_or_dare import TruthOrDareEngine
    engine = TruthOrDareEngine(_FakeSession())
    p0 = _FakePlayer(0, "你", player_type="user", user_id=1)
    p1 = _FakePlayer(1, "小艾")
    engine.players = [p0, p1]
    engine.build_player_meta({
        0: {"name": "你", "character_id": None, "personality": "", "chat_style": "", "relation_type": ""},
        1: {"name": "小艾", "character_id": 101, "personality": "温柔", "chat_style": "口语化", "relation_type": "朋友"},
    })
    return engine


def test_fallback_answer_dare_returns_complete_dare():
    """大冒险回答阶段 fallback 必须返回 complete_dare，不能返回 answer_truth。"""
    engine = _make_engine()
    asyncio.run(engine.setup())

    # 模拟：AI 选了大冒险，用户已出题，现在 AI 需要完成大冒险
    engine.state["stage"] = "answer"
    engine.state["last_choice"] = "dare"
    engine.state["turn_seat"] = 1

    fb = asyncio.run(engine.fallback_action(1))
    assert fb["action"] == "complete_dare", (
        f"BUG 回归：大冒险阶段 fallback 返回了 {fb['action']!r}，应为 'complete_dare'，"
        f"否则引擎会拒绝动作导致游戏卡死"
    )

    # 验证该 fallback 动作能被引擎接受
    result = asyncio.run(engine.apply_action(1, fb["action"], fb.get("payload", {})))
    assert result.ok, f"complete_dare fallback 被引擎拒绝: {result.error}"


def test_fallback_answer_truth_returns_answer_truth():
    """真心话回答阶段 fallback 应正常返回 answer_truth。"""
    engine = _make_engine()
    asyncio.run(engine.setup())

    engine.state["stage"] = "answer"
    engine.state["last_choice"] = "truth"
    engine.state["turn_seat"] = 1

    fb = asyncio.run(engine.fallback_action(1))
    assert fb["action"] == "answer_truth"

    result = asyncio.run(engine.apply_action(1, fb["action"], fb.get("payload", {})))
    assert result.ok, f"answer_truth fallback 被引擎拒绝: {result.error}"


def test_fallback_give_stage_matches_choice():
    """出题阶段 fallback 必须与 last_choice 一致。"""
    engine = _make_engine()
    asyncio.run(engine.setup())

    # 真心话出题
    engine.state["stage"] = "give"
    engine.state["last_choice"] = "truth"
    engine.state["giver_seat"] = 1
    fb = asyncio.run(engine.fallback_action(1))
    assert fb["action"] == "give_truth"

    # 大冒险出题
    engine.state["last_choice"] = "dare"
    fb = asyncio.run(engine.fallback_action(1))
    assert fb["action"] == "give_dare"


def test_fallback_choose_stage():
    """选择阶段 fallback 默认选真心话。"""
    engine = _make_engine()
    asyncio.run(engine.setup())

    engine.state["stage"] = "choose"
    engine.state["turn_seat"] = 1
    fb = asyncio.run(engine.fallback_action(1))
    assert fb["action"] == "choose"
    assert fb["payload"]["choice"] in ("truth", "dare")


# ──────────────────────────────────────────────
# 完整流程测试：大冒险循环中 AI LLM 失败不卡死
# ──────────────────────────────────────────────

def test_full_dare_cycle_ai_completes_via_fallback():
    """完整大冒险循环：用户选大冒险→AI 出题→用户完成→AI 选大冒险→
    用户出题→AI 需要完成（模拟 LLM 失败，走 fallback）→游戏不卡死。"""
    engine = _make_engine()
    asyncio.run(engine.setup())

    # 第1轮：用户(seat0)选大冒险
    r = asyncio.run(engine.apply_action(0, "choose", {"choice": "dare"}))
    assert r.ok, f"用户选大冒险失败: {r.error}"
    asyncio.run(engine.advance())

    # AI(seat1)出大冒险题
    r = asyncio.run(engine.apply_action(1, "give_dare", {"content": "学猫叫一声"}))
    assert r.ok, f"AI 出大冒险题失败: {r.error}"
    asyncio.run(engine.advance())

    # 用户完成大冒险
    r = asyncio.run(engine.apply_action(0, "complete_dare", {"content": "喵~"}))
    assert r.ok, f"用户完成大冒险失败: {r.error}"
    asyncio.run(engine.advance())

    # 第2轮：AI(seat1)选大冒险
    assert engine.state["turn_seat"] == 1, "advance 后应轮到 AI 选择"
    r = asyncio.run(engine.apply_action(1, "choose", {"choice": "dare"}))
    assert r.ok, f"AI 选大冒险失败: {r.error}"
    asyncio.run(engine.advance())

    # 用户(seat0)给 AI 出大冒险题
    assert engine.state["giver_seat"] == 0
    r = asyncio.run(engine.apply_action(0, "give_dare", {"content": "夸夸我"}))
    assert r.ok, f"用户给 AI 出大冒险题失败: {r.error}"
    asyncio.run(engine.advance())

    # AI(seat1)需要完成大冒险 —— 这是 bug 发生点：
    # 模拟 LLM 失败，直接用 fallback_action
    assert engine.state["turn_seat"] == 1
    assert engine.state["stage"] == "answer"
    assert engine.state["last_choice"] == "dare"

    fb = asyncio.run(engine.fallback_action(1))
    r = asyncio.run(engine.apply_action(1, fb["action"], fb.get("payload", {})))
    assert r.ok, f"AI fallback 完成大冒险失败（游戏会卡死）: {r.error}"

    # advance 后应进入下一轮选择，而不是卡住
    asyncio.run(engine.advance())
    assert engine.state["stage"] == "choose", (
        f"大冒险完成后应进入 choose 阶段，实际 stage={engine.state['stage']}"
    )
    assert engine.state["turn_seat"] == 0, "应轮到用户选择"


def test_full_truth_cycle_ai_answers_via_fallback():
    """完整真心话循环：AI 选真心话→用户出题→AI fallback 回答→不卡死。"""
    engine = _make_engine()
    asyncio.run(engine.setup())

    # AI 先选（setup 后 turn_seat=0 是用户，先让用户选真心话走完一轮）
    r = asyncio.run(engine.apply_action(0, "choose", {"choice": "truth"}))
    assert r.ok
    asyncio.run(engine.advance())

    r = asyncio.run(engine.apply_action(1, "give_truth", {"content": "你喜欢什么颜色？"}))
    assert r.ok
    asyncio.run(engine.advance())

    r = asyncio.run(engine.apply_action(0, "answer_truth", {"content": "蓝色"}))
    assert r.ok
    asyncio.run(engine.advance())

    # 第二轮：AI 选真心话
    r = asyncio.run(engine.apply_action(1, "choose", {"choice": "truth"}))
    assert r.ok
    asyncio.run(engine.advance())

    # 用户出题
    r = asyncio.run(engine.apply_action(0, "give_truth", {"content": "你今天开心吗？"}))
    assert r.ok
    asyncio.run(engine.advance())

    # AI fallback 回答
    fb = asyncio.run(engine.fallback_action(1))
    assert fb["action"] == "answer_truth"
    r = asyncio.run(engine.apply_action(1, fb["action"], fb.get("payload", {})))
    assert r.ok, f"AI fallback 回答真心话失败: {r.error}"
    asyncio.run(engine.advance())

    assert engine.state["stage"] == "choose"


# ──────────────────────────────────────────────
# API 集成测试：mock ai_decide 返回非法动作，验证 resume 不卡死
# ──────────────────────────────────────────────

def _create_tod(client):
    """创建真心话大冒险对局（用户 + 1 AI = 2 人）。"""
    r = client.post("/api/v1/games/sessions", json={
        "game_type": "truth_or_dare", "player_ids": [101], "user_as_player": True,
    })
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_api_dare_fallback_after_invalid_ai_decision(game_api_db, monkeypatch):  # noqa: F811
    """API 层：AI 在大冒险完成阶段返回非法动作时，_resume_ai_turns 走 fallback，
    游戏不卡死、回合推进到用户。"""
    # mock ai_decide 返回一个在 answer+dare 阶段非法的动作
    async def _bad_ai_decide(engine, seat):
        return {"action": "answer_truth", "content": "我想想", "payload": {}}

    monkeypatch.setattr("app.games.ai_player.ai_decide", _bad_ai_decide)

    client = _make_client(1)
    sid = _create_tod(client)

    # 用户(seat0)选大冒险
    r = client.post(f"/api/v1/games/sessions/{sid}/action",
                    json={"seat": 0, "action": "choose", "payload": {"choice": "dare"}})
    assert r.status_code == 200, r.text

    # AI 出题（mock 返回 answer_truth，但当前 stage=give，引擎会拒绝，走 fallback give_dare）
    asyncio.run(_REAL_RESUME(sid))

    # 用户完成大冒险
    st = client.get(f"/api/v1/games/sessions/{sid}/state", params={"seat": 0}).json()
    assert st["my_turn"] is True, "AI 出题后应轮到用户"
    r = client.post(f"/api/v1/games/sessions/{sid}/action",
                    json={"seat": 0, "action": "complete_dare", "payload": {"content": "喵~"}})
    assert r.status_code == 200, r.text

    # AI 选大冒险（mock 返回 answer_truth，stage=choose 时非法，走 fallback choose truth）
    asyncio.run(_REAL_RESUME(sid))

    # 用户给 AI 出题（fallback 让 AI 选了 truth，所以用户 give_truth）
    st = client.get(f"/api/v1/games/sessions/{sid}/state", params={"seat": 0}).json()
    if st["my_turn"] and st.get("my_expected_action") == "give_truth":
        r = client.post(f"/api/v1/games/sessions/{sid}/action",
                        json={"seat": 0, "action": "give_truth", "payload": {"content": "你喜欢什么？"}})
        assert r.status_code == 200, r.text

        # AI 回答阶段：mock 返回 answer_truth（合法），应正常推进
        asyncio.run(_REAL_RESUME(sid))

    # 验证游戏仍在进行且没卡死
    st = client.get(f"/api/v1/games/sessions/{sid}/state", params={"seat": 0}).json()
    assert st["status"] == "playing", f"游戏异常结束: {st['status']}"


def test_api_dare_cycle_ai_completes_dare_with_fallback(game_api_db, monkeypatch):  # noqa: F811
    """API 层：完整模拟 AI 在大冒险完成阶段 LLM 失败，
    fallback 必须返回 complete_dare 让游戏继续。"""

    call_count = {"n": 0}

    async def _flaky_ai_decide(engine, seat):
        """前两次合法，第三次（大冒险完成阶段）返回非法动作触发 fallback。"""
        call_count["n"] += 1
        stage = engine.state.get("stage")
        if stage == "give" and engine.state.get("last_choice") == "dare":
            return {"action": "give_dare", "content": "学猫叫", "payload": {}}
        if stage == "choose":
            return {"action": "choose", "content": "我选大冒险", "payload": {"choice": "dare"}}
        # answer + dare 阶段：返回非法动作
        return {"action": "give_truth", "content": "非法动作", "payload": {}}

    monkeypatch.setattr("app.games.ai_player.ai_decide", _flaky_ai_decide)

    client = _make_client(1)
    sid = _create_tod(client)

    # 用户选大冒险
    client.post(f"/api/v1/games/sessions/{sid}/action",
                json={"seat": 0, "action": "choose", "payload": {"choice": "dare"}})
    asyncio.run(_REAL_RESUME(sid))

    # 用户完成大冒险
    st = client.get(f"/api/v1/games/sessions/{sid}/state", params={"seat": 0}).json()
    if st["my_turn"] and st.get("my_expected_action") == "complete_dare":
        client.post(f"/api/v1/games/sessions/{sid}/action",
                    json={"seat": 0, "action": "complete_dare", "payload": {"content": "喵~"}})

    # AI 选大冒险
    asyncio.run(_REAL_RESUME(sid))

    # 用户给 AI 出大冒险题
    st = client.get(f"/api/v1/games/sessions/{sid}/state", params={"seat": 0}).json()
    if st["my_turn"] and st.get("my_expected_action") == "give_dare":
        client.post(f"/api/v1/games/sessions/{sid}/action",
                    json={"seat": 0, "action": "give_dare", "payload": {"content": "夸夸我"}})

    # AI 需要完成大冒险 —— mock 返回非法动作，fallback 必须救场
    asyncio.run(_REAL_RESUME(sid))

    # 游戏必须还活着
    st = client.get(f"/api/v1/games/sessions/{sid}/state", params={"seat": 0}).json()
    assert st["status"] == "playing", (
        f"游戏卡死或异常结束（AI 大冒险 fallback 失败）: status={st['status']}, "
        f"phase={st.get('phase')}, turn={st.get('current_turn_seat')}"
    )
