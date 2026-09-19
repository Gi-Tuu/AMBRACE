# -*- coding: utf-8 -*-
"""P3-① AI 动作清洗守卫（app/games/ai_player.ai_decide）——**不真调模型**。

背景：``_action_valid`` 内部用 ``action.strip()`` 判定，但校验通过时原样返回 decision，
LLM 常输出 ``" vote "`` 这类带首尾空白的动作：过得了本函数校验，却在调度方
``apply_action`` 的精确成员匹配下被判非法（白跑一次无效 apply，虽不卡死但属脏路径）。
修复=校验通过即把清洗值写回 ``decision["action"]``；本文件把该行为钉成回归测试。

约定（2026-09-19）：
- 全程 monkeypatch ``app.agent.llm_client.chat_completion``，**绝不真的调模型**（无网络/无 key）；
- 引擎用轻量桩：``ai_decide`` 只用到 ``build_ai_prompt`` / ``expected_action`` /
  ``fallback_action`` / ``session.user_id`` 四个入口，不依赖数据库与真实状态机；
- 项目未配 pytest-asyncio，统一 ``asyncio.run``。
"""
import asyncio
import json
from types import SimpleNamespace

from app.games.ai_player import ai_decide

_FALLBACK = {"action": "speak", "content": "兜底发言", "payload": {}}


def _stub_engine(expected: str, fallback_calls: list) -> SimpleNamespace:
    """最小引擎桩：够 ai_decide 跑通 prompt 组装 + 校验 + 兜底即可。"""
    me = SimpleNamespace(seat=1, name="阿白", role="狼人", alive=True, private={"word": "苹果"})
    ctx = SimpleNamespace(
        game_type="werewolf",
        rules_summary="狼人杀规则摘要",
        public_events=[],
        players_public=[
            {"seat": 1, "name": "阿白", "alive": True},
            {"seat": 2, "name": "小黑", "alive": True},
        ],
        my_view=me,
        my_persona={"personality": "自然", "chat_style": "口语化"},
        phase="night",
        round=1,
        my_turn=True,
    )

    async def fallback_action(seat):
        fallback_calls.append(seat)
        return dict(_FALLBACK)

    return SimpleNamespace(
        session=SimpleNamespace(user_id="u-action-hygiene"),
        build_ai_prompt=lambda seat: ctx,
        expected_action=lambda seat: expected,
        fallback_action=fallback_action,
    )


def _decide(monkeypatch, expected: str, raw: str | None, raises: bool = False):
    """跑一次 ai_decide，返回 (decision, llm 调用次数, 兜底调用到的 seat 列表)。"""
    llm_calls: list = []
    fallback_calls: list = []

    async def fake_chat_completion(**kwargs):
        llm_calls.append(kwargs)
        if raises:
            raise RuntimeError("stubbed LLM failure")
        return raw

    # ai_decide 内部 `from app.agent.llm_client import chat_completion`，调用时才解析 → 打模块属性即可
    monkeypatch.setattr("app.agent.llm_client.chat_completion", fake_chat_completion)
    decision = asyncio.run(ai_decide(_stub_engine(expected, fallback_calls), 1))
    return decision, len(llm_calls), fallback_calls


def test_带首尾空白的动作_回写为清洗值(monkeypatch):
    raw = json.dumps({"action": " vote ", "content": "我投2号", "payload": {"target_seat": 2}},
                     ensure_ascii=False)
    decision, n_llm, fallback_calls = _decide(monkeypatch, "vote", raw)

    assert decision["action"] == "vote", f"应回写 strip 后的动作，实际={decision['action']!r}"
    assert n_llm == 1 and not fallback_calls


def test_带换行空白的投降动作_同样清洗(monkeypatch):
    # 用 json.dumps 生成，保证 JSON 里的换行是转义的 \n（字面量换行会让 _parse_json 直接失败）
    raw = json.dumps({"action": "\n surrender ", "content": "我认输", "payload": {}},
                     ensure_ascii=False)
    decision, n_llm, fallback_calls = _decide(monkeypatch, "vote", raw)

    assert decision["action"] == "surrender"
    assert n_llm == 1 and not fallback_calls


def test_带空白的替代动作_同样清洗(monkeypatch):
    """替代动作（_ALT_ACTIONS 分支）同样要写回清洗值，不能只处理 expected 相等的情况。"""
    raw = '{"action": " declare ", "content": "出牌", "payload": {"number": 7}}'
    decision, n_llm, fallback_calls = _decide(monkeypatch, "follow_or_challenge", raw)

    assert decision["action"] == "declare"
    assert n_llm == 1 and not fallback_calls


def test_清洗只动_action_content与payload原样保留(monkeypatch):
    raw = json.dumps({"action": "  kill\t", "content": " 今晚刀2号 ", "payload": {"target_seat": 2}},
                     ensure_ascii=False)
    decision, _, _ = _decide(monkeypatch, "kill", raw)

    assert decision["action"] == "kill"
    assert decision["content"] == " 今晚刀2号 "          # 发言内容不做清洗（用户可见）
    assert decision["payload"] == {"target_seat": 2}


def test_非字符串动作_仍走兜底(monkeypatch):
    for raw in ('{"action": null, "content": "x", "payload": {}}',
                '{"action": 123, "content": "x", "payload": {}}'):
        decision, n_llm, fallback_calls = _decide(monkeypatch, "vote", raw)
        assert decision == _FALLBACK, f"非字符串动作应走兜底，实际={decision!r}"
        assert fallback_calls == [1] and n_llm == 1


def test_纯空白动作_仍走兜底(monkeypatch):
    decision, n_llm, fallback_calls = _decide(monkeypatch, "vote", '{"action": "   ", "content": "x"}')

    assert decision == _FALLBACK and fallback_calls == [1] and n_llm == 1


def test_阶段不符动作_仍走兜底(monkeypatch):
    decision, n_llm, fallback_calls = _decide(monkeypatch, "vote", '{"action": "kill", "content": "x"}')

    assert decision == _FALLBACK and fallback_calls == [1] and n_llm == 1


def test_LLM异常或不可解析_仍走兜底(monkeypatch):
    decision, n_llm, fallback_calls = _decide(monkeypatch, "vote", None, raises=True)
    assert decision == _FALLBACK and fallback_calls == [1] and n_llm == 1

    decision, n_llm, fallback_calls = _decide(monkeypatch, "vote", "这不是 JSON")
    assert decision == _FALLBACK and fallback_calls == [1] and n_llm == 1
