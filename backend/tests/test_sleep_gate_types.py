"""单 A（P2-1）· sleep 静默白名单守门测试：纯只读断言，不连库、不调 LLM。

覆盖 4 类：成员全集 / 守门（用户可见类型必须登记）/ 后台类型排除 / 源码锚定 + 语义用例。
"""
import asyncio
import pathlib

import pytest

from app.domain.proactivity.pacing import SESSION_RATE_TYPES
from app.domain.proactivity.sleep import SLEEP_SILENCED_TYPES
from app.scheduling import arbiter
from app.scheduling.executors import guards

# arbiter 原有 15 项 + 本次新增 4 项（anniversary / life_regression /
# unfinished_topic / memory_review_contextual）
EXPECTED_MEMBERS = (
    "birthday", "anniversary", "holiday", "greeting", "proactive_chat",
    "goodnight", "status_update", "state_trigger", "memory_review",
    "memory_review_contextual", "emotion_care", "pet_remind", "ai_care",
    "ai_adopt", "plugin", "motivation", "prospective_intent",
    "life_regression", "unfinished_topic",
)

# 不向用户露面的后台类型，故意不进 sleep 静默集合
BACKGROUND_TYPES = ("timer", "ai_social", "group_active", "pet_visit")


@pytest.mark.parametrize("etype", EXPECTED_MEMBERS)
def test_member_registered(etype):
    assert etype in SLEEP_SILENCED_TYPES, f"{etype} 未登记进 sleep 静默集合"
    assert isinstance(SLEEP_SILENCED_TYPES, frozenset), "集合应为 frozenset（防运行时被改动）"


def test_membership_is_exactly_19():
    assert len(SLEEP_SILENCED_TYPES) == 19
    assert SLEEP_SILENCED_TYPES == frozenset(EXPECTED_MEMBERS)


def test_user_visible_types_superset_guard():
    """守门断言：用户可见类型全集必须被 sleep 集合包含。

    将来新增用户可见类型却忘了登记 sleep 集合时，这条要红。
    """
    user_visible = SESSION_RATE_TYPES | {
        "birthday", "holiday", "plugin", "state_trigger",
        "prospective_intent", "ai_adopt",
    }
    missing = user_visible - SLEEP_SILENCED_TYPES
    assert not missing, f"用户可见类型未登记 sleep 静默集合：{sorted(missing)}"


@pytest.mark.parametrize("etype", BACKGROUND_TYPES)
def test_background_types_excluded(etype):
    assert etype not in SLEEP_SILENCED_TYPES, f"{etype} 是后台类型，不应进 sleep 静默集合"


def test_arbiter_wired_to_constant():
    """源码锚定（防回退）：sleep 闸门用常量，不再是内联元组。

    A20 批 3a（2026-10-02）：闸门本体自 ``arbiter._execute`` 搬到 ``executors/guards.pre_gates``，
    锚点随之改读 guards 源码（逻辑一行未动）。arbiter 侧仍保留常量重导出，最后一行照旧钉住。
    """
    text = pathlib.Path(guards.__file__).read_text(encoding="utf-8-sig")
    body = text[text.index("async def pre_gates"):]
    anchor = body.index("sleep after 21:00")
    window = body[max(0, anchor - 600):anchor + 120]
    assert "SLEEP_SILENCED_TYPES" in window, "sleep 闸门没有引用 SLEEP_SILENCED_TYPES"
    assert 'etype in ("' not in window, "sleep 闸门回退成内联元组"
    assert "return False" in window, "sleep 闸门拦截分支丢失"
    assert arbiter.SLEEP_SILENCED_TYPES is SLEEP_SILENCED_TYPES, "arbiter 未导入该常量"


@pytest.mark.parametrize(
    "etype", ["anniversary", "life_regression", "unfinished_topic", "memory_review_contextual"]
)
def test_sleep_gate_blocks_type(monkeypatch, etype):
    """21 点后用户说过睡觉 → 这 4 类必须被拦下（return False），且不碰数据库。"""
    async def _no_dnd(*_args, **_kwargs):
        return False

    async def _said_sleep(*_args, **_kwargs):
        return True

    def _no_db():
        raise AssertionError(f"{etype} 被 sleep 闸门拦下后不该再访问数据库")

    monkeypatch.setattr(arbiter, "is_dnd_now", _no_dnd)
    monkeypatch.setattr(arbiter, "has_user_said_sleep", _said_sleep)
    monkeypatch.setattr(arbiter, "async_session_factory", _no_db)

    item = {"type": etype, "candidate": {"character_id": 7, "user_id": 3}}
    assert asyncio.run(arbiter._execute(item)) is False, f"{etype} 未被 sleep 闸门拦截"
