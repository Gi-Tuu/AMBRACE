# -*- coding: utf-8 -*-
"""P3-1（2026-09-18）：GameEngine.name_of 真人缺名字时走服务端 i18n，不再硬编码中文。

覆盖：
- 引擎语言 zh / en 两态；
- 显式 lang 参数覆盖引擎语言；
- 取不到语言时回落 settings.default_lang（zh）；
- AI 玩家缺名字仍回落语言中立的「{seat}号」。
"""
from types import SimpleNamespace

from app.config import settings
from app.games.base import GameEngine


class _FakePlayer:
    def __init__(self, seat: int, player_type: str):
        self.seat = seat
        self.player_type = player_type
        self.character_id = None


class _DummyEngine(GameEngine):
    """最小可实例化引擎：只实现 name_of 测试需要的路径。"""

    game_type = "dummy"

    async def setup(self, *a, **k):
        return []

    async def apply_action(self, *a, **k):
        return SimpleNamespace(ok=False)

    async def advance(self, *a, **k):
        return []

    async def check_winner(self, *a, **k):
        return None

    async def timeout(self, *a, **k):
        return []

    def view_for(self, *a, **k):
        return SimpleNamespace()

    def build_ai_prompt(self, *a, **k):
        return SimpleNamespace()

    def expected_action(self, *a, **k):
        return ""

    async def fallback_action(self, *a, **k):
        return {}

    def current_turn_seat(self, *a, **k):
        return None


def _make_engine(lang: str | None) -> _DummyEngine:
    eng = _DummyEngine(None)
    eng.players = [
        _FakePlayer(seat=0, player_type="user"),
        _FakePlayer(seat=1, player_type="ai"),
    ]
    eng.lang = lang
    return eng


def test_name_of_zh_returns_user():
    eng = _make_engine("zh")
    assert eng.name_of(0) == "用户"


def test_name_of_en_returns_user():
    eng = _make_engine("en")
    assert eng.name_of(0) == "User"


def test_name_of_explicit_lang_overrides_engine_lang():
    eng = _make_engine("zh")
    assert eng.name_of(0, lang="en") == "User"
    eng2 = _make_engine("en")
    assert eng2.name_of(0, lang="zh") == "用户"


def test_name_of_falls_back_to_settings_default_lang():
    eng = _make_engine(None)  # 引擎未持语言
    assert eng.name_of(0) == ("用户" if settings.default_lang == "zh" else "User")


def test_name_of_ai_seat_is_language_neutral():
    eng = _make_engine("en")  # 即使英文界面，AI 缺名字也用座位号（语言中立）
    assert eng.name_of(1) == "1号"
