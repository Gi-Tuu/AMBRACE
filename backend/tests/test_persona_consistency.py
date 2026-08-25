"""人格一致性测试：公开平台表达裁剪纯函数 + 主动通道身份块注入性格/聊天风格。

对应工程规范（docs/engineering-protocol.md）：Persona Kernel 唯一人格核心，
外部模块只能读取人格 + 按平台调整表达，不能重新定义人格。
"""

from app.agent.persona import _build_platform_profile_text


class _FakeChar:
    def __init__(self, personality: str = "友善", chat_style: str = "自然"):
        self.id = 13
        self.name = "小遥"
        self.gender = "male"
        self.personality = personality
        self.chat_style = chat_style
        self.is_partner = True
        self.relation_type = "朋友"
        self.relationship_summary = "用户希望我（小遥）永远记得他"


def test_身份块注入性格与聊天风格(monkeypatch):
    """回归：主动通道（情绪关怀/记忆复习/宠物提醒等）提示词必须含性格+聊天风格，
    防止生成消息脱离人设（历史 bug：小遥冷漠人设被生成撒娇语气）。"""
    import asyncio

    import app.agent.user_profile as up
    from app.agent.user_profile import build_role_prompt_block

    fake_user = type("U", (), {"nickname": "小满", "username": "xiaoman", "gender": "male"})()
    fake_partner = _FakeChar()

    class _FakeDB:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def get(self, *a, **k): return fake_user
        async def execute(self, *a, **k):
            class _R:
                def scalars(self): return self
                def all(self): return [fake_partner]
            return _R()

    monkeypatch.setattr(up, "async_session_factory", lambda: _FakeDB())

    out = asyncio.run(build_role_prompt_block(_FakeChar(personality="冷漠，话少", chat_style="一针见血，不腻歪"), 1))
    assert "你的性格：冷漠，话少" in out
    assert "你的聊天风格：一针见血，不腻歪" in out
    assert "你是用户的爱人/伴侣" in out


def test_app私有平台不输出平台约束():
    assert _build_platform_profile_text("app", None, public=False) == ""
    assert _build_platform_profile_text("app", {"tone": "social"}, public=False) == ""


def test_未配置档案不输出():
    assert _build_platform_profile_text("douyin", None, public=True) == ""


def test_公开平台输出约束且不暴露私密():
    out = _build_platform_profile_text("douyin", {"tone": "social"}, public=True)
    assert "公开平台（douyin）" in out
    assert "不暴露与用户的私密关系" in out
    assert "社交化" in out


def test_公开平台tone映射():
    creative = _build_platform_profile_text("douyin", {"tone": "creative"}, public=True)
    assert "有创意、有个性" in creative
    private = _build_platform_profile_text("douyin", {"tone": "private"}, public=True)
    assert "私密、亲近" in private


def test_未知tone默认社交化():
    out = _build_platform_profile_text("douyin", {"tone": "unknown"}, public=True)
    assert "社交化" in out
