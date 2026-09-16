# -*- coding: utf-8 -*-
"""批次五 P1 数据卫生（话题噪声/状态一致、reflection 真校验、游戏记忆第一人称、写入标注）。

覆盖：
- P1-6 conversation_topics：噪声输入不产生 topic；status/progress 一致性；合并去重（_overlap）。
- P1-7 reflection：旧场景当现状 → FAIL + 闭环处置留痕；正常样本仍 pass；含现状词纳入抽检。
- P1-8 游戏记忆：文案统一角色第一人称（我（角色名）…）。
- P1-2 写入标注：群聊记忆 speaker/group/epistemic 正确（不串角色库）；周复盘/游戏指针带标注。

纪律：测试用 tmp_path / 会话临时库；不连生产库、不写生产库（cleanup 脚本默认 dry-run）。
"""
import asyncio

from app.agent import reflection as reflection_mod
from app.agent.topic_tracker import (
    _extract_candidates,
    _overlap,
    update_topic_resolution,
)
from app.games.memory_bridge import _build_summary_pointer


# ───────────────────────── P1-6 conversation_topics ─────────────────────────
def test_topic_extract_noise_rejected():
    """噪声片段（括号/半句/纯助词碎片）不产生 topic。"""
    for noise in ("倒水】", "卧室】", "的事", "做的事哦", "弄弄", "哪儿", "啥", "吗"):
        assert _extract_candidates(noise) == [], f"噪声未过滤：{noise!r}"
    # 纯标点
    assert _extract_candidates("。。。") == []


def test_topic_extract_valid_kept():
    """规范话题短语仍被提取。"""
    cands = _extract_candidates("我打算去长沙旅游顺便看个展")
    topics = [c for c, _ in cands]
    assert "长沙旅游" in topics or any("长沙" in t for t in topics)


def test_topic_overlap_merge_semantics():
    """_overlap：包含关系或公共子串 >= 4 字判重叠（合并去重依据）。"""
    assert _overlap("看那部悬疑片", "那部悬疑片") is True
    assert _overlap("复习高数", "复习线代") is False


def test_topic_status_progress_consistency(tmp_path):
    """终态（完成）不再挂 progress=进行中：用户说「搞定」后 progress 同步为完成。"""
    from sqlalchemy import text

    from app.db.database import async_session_factory

    async def _setup():
        async with async_session_factory() as db:
            await db.execute(text(
                "INSERT INTO users (id, username, nickname) VALUES (1,'u1','U')"))
            await db.commit()
        async with async_session_factory() as db:
            await db.execute(text(
                "INSERT INTO ai_characters (id, user_id, name, is_partner, is_active, "
                "cognitive_loop_enabled, memory_v2_enabled, talkativeness_locked) "
                "VALUES (1,1,'x',0,1,1,1,0)"))
            await db.commit()
        async with async_session_factory() as db:
            await db.execute(text(
                "INSERT INTO conversation_topics "
                "(id, character_id, user_id, topic, status, progress, goal, importance, follow_up) "
                "VALUES (1,1,1,'复习高数','进行中','进行中',1,0.6,1)"))
            await db.commit()

    async def _run():
        await update_topic_resolution(1, 1, "复习搞定了终于")
        async with async_session_factory() as db:
            row = (await db.execute(text(
                "SELECT status, progress FROM conversation_topics WHERE topic='复习高数'"
            ))).fetchone()
            return row[0], row[1]

    asyncio.run(_setup())
    status, progress = asyncio.run(_run())
    # runtime 路径：用户说「搞定」→ 终态（完成）不再挂「进行中」
    assert status == "完成"
    assert progress == "完成"  # P1-6：终态不再挂「进行中」


# ───────────────────────── P1-7 reflection 真校验 ─────────────────────────
def test_reflection_stale_scene_fails_and_disposes(monkeypatch):
    """「长沙当现状」样本 → memory_consistency FAIL + 闭环处置留痕。"""
    async def _fake_anchor(*, character_id, user_id, **_kw):
        return "\nTA 当前已知现状（以此为准）：位置：示例市；状态：在读学生。\n"
    monkeypatch.setattr(
        "app.memory.current_state.current_user_state_anchor", _fake_anchor)
    # 强制触发（抽样命中）
    monkeypatch.setattr("app.agent.reflection.random.random", lambda: 0.0)

    receipts = []
    import app.memory.receipt as receipt_mod
    monkeypatch.setattr(
        receipt_mod, "emit_memory_receipt",
        lambda cid, mid, action, **kw: receipts.append((cid, mid, action)))

    state = {
        "ai_response": "我还在长沙呢，今天去吃臭豆腐",
        "retrieved_memories": [{"id": 99, "sub_type": "event", "content": "长沙之行"}],
        "perception": {"intent": "chat", "topic": "other"},
        "character_id": 13, "user_id": 3, "session_id": 1,
    }
    result = asyncio.run(reflection_mod.evaluate_reflection(state))
    assert result is not None
    mc = result["checks"]["memory_consistency"]
    assert mc["pass"] is False
    assert mc["disposition"] == "downweight"
    assert mc["offending_memory_id"] == 99

    # 闭环：persist 必须写降级回执（不只记日志）
    asyncio.run(reflection_mod.persist_reflection(13, 3, 555, result))
    assert any(mid == 99 and action == "downgrade" for _, mid, action in receipts)


def test_reflection_normal_consistent_passes(monkeypatch):
    """正常样本（现址示例市、时态一致）仍 pass。"""
    async def _fake_anchor(*, character_id, user_id, **_kw):
        return "\nTA 当前已知现状：位置：示例市；状态：在读学生。\n"
    monkeypatch.setattr(
        "app.memory.current_state.current_user_state_anchor", _fake_anchor)
    monkeypatch.setattr("app.agent.reflection.random.random", lambda: 0.0)

    state = {
        "ai_response": "你示例市的宿舍还住得惯吗，最近降温了",
        "retrieved_memories": [{"id": 1, "sub_type": "event", "content": "示例市宿舍"}],
        "perception": {"intent": "chat", "topic": "other"},
        "character_id": 13, "user_id": 3, "session_id": 1,
    }
    result = asyncio.run(reflection_mod.evaluate_reflection(state))
    assert result["checks"]["memory_consistency"]["pass"] is True


def test_reflection_status_word_triggers_sample(monkeypatch):
    """含现状锚点词的回复纳入抽检（status_word 触发），不依赖随机抽样。"""
    async def _fake_anchor(*, character_id, user_id, **_kw):
        return ""
    monkeypatch.setattr(
        "app.memory.current_state.current_user_state_anchor", _fake_anchor)
    # 抽样不命中（高随机数），intent 非高风险，但文本含现状词
    monkeypatch.setattr("app.agent.reflection.random.random", lambda: 1.0)

    state = {
        "ai_response": "你示例市的宿舍最近潮不潮",
        "retrieved_memories": [],
        "perception": {"intent": "smalltalk", "topic": "other"},
        "character_id": 13, "user_id": 3, "session_id": 1,
    }
    result = asyncio.run(reflection_mod.evaluate_reflection(state))
    assert result is not None
    assert "status_word" in result["triggers"]


# ───────────────────────── P1-8 游戏记忆第一人称 ─────────────────────────
class _FakeSeat:
    def __init__(self, name):
        self._name = name
    def __str__(self):
        return self._name


class _FakePlayer:
    def __init__(self, seat, role="thinker", alive=True, character_id=13):
        self.seat = seat
        self.role = role
        self.alive = alive
        self.character_id = character_id
        self.player_type = "ai"
        self.is_spectator = False


class _FakeEngine:
    def __init__(self, players, names):
        self.players = players
        self._names = names
    def meta(self):
        return {"name": "海龟汤"}
    def name_of(self, seat):
        return self._names[str(seat)]
    def view_for(self, seat):
        return type("V", (), {"private": {"word": "汤底"}})()
    def public_events_for(self, seat):
        return []
    def my_events(self, seat):
        return []


class _FakeSession:
    user_id = 3
    id = 1
    game_type = "turtle_soup"
    round = 3


def _fake_engine_for_turtle():
    p = _FakePlayer(_FakeSeat("13"), role="thinker")
    sam = _FakePlayer(_FakeSeat("11"), role="member")
    deepseek = _FakePlayer(_FakeSeat("12"), role="member")
    return _FakeEngine(
        [p, sam, deepseek],
        {"13": "轩", "11": "sam", "12": "DeepSeek"},
    ), p


def test_game_summary_first_person_turtle_soup(monkeypatch):
    """海龟汤：角色第一人称显式自指，且用户(sam)作为参与方而非「我」。"""
    monkeypatch.setattr(
        "app.games.memory_bridge._player_won", lambda *a, **k: True)
    engine, player = _fake_engine_for_turtle()
    out = _build_summary_pointer(_FakeSession(), player, engine, character_name="轩")
    assert "我（轩）" in out
    assert "sam" in out and "DeepSeek" in out
    assert "当主持人" in out
    # 不应仍用无指代的「我当主持人」旧写法
    assert "我当主持人" not in out


def test_game_summary_first_person_werewolf(monkeypatch):
    """狼人杀：抽到角色用「我（角色名）」自指。"""
    monkeypatch.setattr(
        "app.games.memory_bridge._player_won", lambda *a, **k: True)
    p = _FakePlayer(_FakeSeat("13"), role="wolf")
    engine = _FakeEngine([p], {"13": "sam", "11": "DeepSeek"})
    s = _FakeSession()
    s.game_type = "werewolf"
    out = _build_summary_pointer(s, p, engine, character_name="轩")
    assert "我（轩）" in out
    assert "抽到「狼人」" in out


# ───────────────────────── P1-2 写入标注 ─────────────────────────
def test_group_memory_entries_speaker_attribution():
    """群聊记忆按发言者拆分：用户条目不串到角色库（回归 10408/10425/10426）。"""
    from app.application.chat_groups import build_group_memory_entries

    entries = build_group_memory_entries(
        user_content="周末去钓鱼啊",
        replies=[{"character_id": 11, "content": "好呀我去"}],
        name_map={11: "轩"},
    )
    assert len(entries) == 2
    user_e = entries[0]
    char_e = entries[1]
    assert user_e["speaker_type"] == "user"
    assert "用户在群里说" in user_e["content"]
    assert char_e["speaker_type"] == "character"
    assert char_e["speaker_id"] == 11
    assert "轩在群里说" in char_e["content"]
    # 用户的话绝不被错标成某个角色
    assert "轩" not in user_e["content"]


def test_daily_reflection_carries_annotations(monkeypatch):
    """周复盘记忆落库带 speaker_type + epistemic_status（不落空标注）。"""
    from app.agent import loop as _loop
    from app.scheduling import daily_reflection as dr

    async def _f_false(*a, **k):
        return False
    async def _f_data(*a, **k):
        return "活动记录"
    monkeypatch.setattr(dr, "_used_recently", _f_false)
    monkeypatch.setattr(dr, "_collect_week_data", _f_data)
    monkeypatch.setitem(_loop.AGENT_FLAGS, "agent_daily_reflection", True)

    captured = {}
    async def _fake_llm(*a, **k):
        return "这周过得还行，整体节奏比较平稳，也攒了点小进展。"
    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_llm)

    async def _fake_save(**kw):
        captured.update(kw)
        return 1
    monkeypatch.setattr("app.memory.save_memory", _fake_save)

    ok = asyncio.run(dr.generate_daily_reflection(13, user_id=3))
    assert ok is True
    assert captured.get("speaker_type") == "character"
    assert captured.get("speaker_id") == 13
    assert captured.get("epistemic_status") == "FACT"


def test_cleanup_script_plan_and_apply(tmp_path):
    """cleanup 脚本：plan 识别噪声/矛盾；apply 写前备份且只置完成不物理删（tmp_path 库）。"""
    import importlib.util
    import sqlite3
    from pathlib import Path

    script_path = (
        Path(__file__).resolve().parent.parent.parent
        / "scripts" / "cleanup_conversation_topics.py"
    )
    spec = importlib.util.spec_from_file_location("cleanup_ct", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _load_rows, apply_decisions, plan_cleanup = (
        mod._load_rows, mod.apply_decisions, mod.plan_cleanup,
    )

    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE conversation_topics ("
        "id INTEGER PRIMARY KEY, character_id INTEGER, user_id INTEGER, "
        "topic TEXT, status TEXT, progress TEXT, importance REAL)")
    rows = [
        (1, 1, 1, "倒水】", "进行中", None, 0.6),
        (2, 1, 1, "的事", "进行中", None, 0.6),
        (3, 1, 1, "看那部悬疑片", "完成", "进行中", 0.6),  # 矛盾
        (4, 1, 1, "复习线代", "进行中", None, 0.6),
        (5, 1, 1, "复习线代", "进行中", None, 0.6),         # 重复
    ]
    con.executemany(
        "INSERT INTO conversation_topics VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()

    loaded = _load_rows(con)
    decisions = plan_cleanup(loaded)
    actions = [d["action"] for d in decisions]
    assert actions.count("noise_done") == 2       # 倒水】、的事
    assert actions.count("fix_consistency") == 1   # 看那部悬疑片
    assert actions.count("merge_done") == 1        # 重复复习线代

    n = apply_decisions(con, decisions)
    assert n == 4
    after = con.execute(
        "SELECT count(*) FROM conversation_topics WHERE status='完成'").fetchone()[0]
    assert after == 4  # 全部改为完成，无物理删除
    con.close()
