# -*- coding: utf-8 -*-
"""#67 抖音 MCP 升级：拆分模块纯函数 + 新 DB 字段测试。

- content（_de_ai / CONTENT_TYPES / pick_content_type / humanize 指令）
- music（MUSIC_MOODS / parse_music_mood / normalize_music_mood / pick_music_mood）
- DouyinPending / DouyinComment 新字段（music_mood/video_path/post_type / aweme_id/comment_id）

仅测纯函数与模型，浏览器流程不 mock 验证（运行时需真机）。
"""
import importlib.util
import pathlib

_PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "douyin_mcp"


def _load(name: str):
    # 加载 plugins/examples/douyin_mcp/<name>.py；用唯一模块名避免与项目内同名模块冲突
    spec = importlib.util.spec_from_file_location("dsh_" + name, _PLUGIN_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


content = _load("content")
music = _load("music")


# ------------------------------------------------------------------ content._de_ai / 内容类型

def test_de_ai_removes_ai_phrases():
    text = "作为一个 AI，感谢支持，希望大家喜欢。第一句。综上所讁。"
    out = content._de_ai(text)
    for phrase in ("作为一个AI", "感谢支持", "希望大家喜欢", "综上所述"):
        assert phrase not in out


def test_de_ai_strips_numbering_and_markdown_and_blank_lines():
    text = "1. 你好\n2. 世界\n\n\n**加粗**\n\n3. 结尾"
    out = content._de_ai(text)
    assert "你好" in out and "世界" in out and "结尾" in out
    assert "**" not in out
    assert "\n\n\n" not in out
    # 非空行首序号已被剥离
    for line in out.splitlines():
        if line:
            assert not line[0].isdigit()


def test_de_ai_handles_empty():
    assert content._de_ai("") == ""
    assert content._de_ai(None) == ""


def test_content_types_and_pick():
    assert set(content.CONTENT_TYPES.keys()) >= {"mood", "thought", "daily", "question", "share"}
    # pick_content_type 返回白名单内 key
    assert content.pick_content_type() in content.CONTENT_TYPES
    # content_type_hint 未知类型回退到默认（不抛异常）
    assert content.content_type_hint("unknown_type") == content.content_type_hint("mood")


def test_humanize_prompt_contains_personality_and_ban_words():
    p = content.humanize_image_prompt("高冷", "话少", "mood")
    assert "高冷" in p
    assert "话少" in p
    assert "禁止" in p  # 含写作禁止项
    r = content.humanize_reply_prompt("傲娇")
    assert "傲娇" in r
    assert "感谢支持" in content._AI_PHRASES  # 回复人味含反客服话术


# ------------------------------------------------------------------ music 情绪解析

def test_music_moods_whitelist():
    assert len(music.MUSIC_MOODS) >= 6
    assert "治愈" in music.MUSIC_MOODS and "欢快" in music.MUSIC_MOODS


def test_parse_music_mood():
    # 半角冒号
    assert music.parse_music_mood("...最后\n音乐:治愈") == "治愈"
    # 全角冒号
    assert music.parse_music_mood("音乐：欢快") == "欢快"
    # 方括号包裹
    assert music.parse_music_mood("【音乐:安静】正文") == "安静"


def test_parse_music_mood_unknown_or_default():
    # 未知情绪词 → 归一化失败 → 使用 default
    assert music.parse_music_mood("音乐:不存在的情绪", default="日常") == "日常"
    # 无音乐行 → default（已归一化）
    assert music.parse_music_mood("普通文案", default="欢快") == "欢快"
    # 无 default → 空串
    assert music.parse_music_mood("普通文案") == ""


def test_normalize_music_mood_alias():
    assert music.normalize_music_mood("治愈") == "治愈"
    assert music.normalize_music_mood("开心") == "欢快"  # 别名归一化
    assert music.normalize_music_mood("悲伤") == "伤感"
    assert music.normalize_music_mood("未知") == ""


def test_pick_music_mood():
    assert music.pick_music_mood() in music.MUSIC_MOODS
    assert music.pick_music_mood(fallback="治愈") == "治愈"
    # fallback 不在白名单 → 随机取白名单
    assert music.pick_music_mood(fallback="不存在") in music.MUSIC_MOODS


# ------------------------------------------------------------------ DB 新字段（#67）
def test_douyin_pending_new_fields():
    from app.models.douyin import DouyinPending
    assert hasattr(DouyinPending, "music_mood")
    assert hasattr(DouyinPending, "video_path")
    assert hasattr(DouyinPending, "post_type")
    # 默认值
    assert DouyinPending.__table__.c.post_type.default.arg == "image"


def test_douyin_comment_new_fields():
    from app.models.douyin import DouyinComment
    assert hasattr(DouyinComment, "aweme_id")
    assert hasattr(DouyinComment, "comment_id")


# ------------------------------------------------------------------ #67 P2 / 审查 P0：save_video（视频发布上传）
def test_save_video_uses_video_exts_and_limits(monkeypatch):
    """#67 P2（审查 P0）：save_video 复用 _save_upload，使用视频扩展名白名单与 200MB 上限。"""
    import asyncio
    from app.services import upload_service as us

    captured = {}

    async def _fake_save_upload(file, subdir, allowed_exts, max_bytes, lang="zh"):
        captured["file"] = file
        captured["subdir"] = subdir
        captured["allowed_exts"] = allowed_exts
        captured["max_bytes"] = max_bytes
        captured["lang"] = lang
        return f"/uploads/{subdir}/fake.mp4"

    monkeypatch.setattr("app.services.upload_service._save_upload", _fake_save_upload)

    class _F:
        filename = "demo.mp4"

    url = asyncio.run(us.save_video(_F(), "douyin/99"))
    # 返回 /uploads/... 相对 URL（与 save_image 一致的契约）
    assert url == "/uploads/douyin/99/fake.mp4"
    assert captured["subdir"] == "douyin/99"
    # 允许扩展名 / 大小上限均为视频专用值
    assert captured["allowed_exts"] == us.ALLOWED_VIDEO_EXTS
    assert captured["max_bytes"] == us.MAX_VIDEO_BYTES
