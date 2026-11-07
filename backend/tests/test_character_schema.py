"""角色创建/编辑 schema 完整性回归（2026-08-15）

背景：create_character 里 data.bio 但 CharacterCreate 缺 bio 字段 → 创建好友必崩
（用户 8 报「编辑好友保存失败」，实为创建路径 AttributeError）。
"""
from app.schemas.character import CharacterCreate, CharacterUpdate


def test_character_create_has_bio():
    """创建 schema 必须含 bio（create_character 端点使用 data.bio）"""
    assert "bio" in CharacterCreate.model_fields
    c = CharacterCreate(name="测试", bio="背景信息")
    assert c.bio == "背景信息"


def test_character_schema_fields_cover_endpoint_usage():
    """端点用到的 data.* 字段必须在 schema 中定义（防属性缺失崩溃）"""
    used = {
        "name", "personality", "chat_style", "greeting_message", "avatar_url",
        "height", "weight", "gender", "birthday", "appearance", "voice",
        "voice_rate", "voice_pitch", "timezone_offset", "bio",
    }
    create_missing = used - set(CharacterCreate.model_fields)
    assert not create_missing, f"CharacterCreate 缺字段: {create_missing}"
    update_missing = used - set(CharacterUpdate.model_fields)
    assert not update_missing, f"CharacterUpdate 缺字段: {update_missing}"
