# -*- coding: utf-8 -*-
"""包 D（2026-09-06 待排期清理）：抖音 P3 三小项纯函数测试。

① FFmpeg 多图轮播 filter/cmd 构造（纯函数，不调 ffmpeg 可执行）；
② 音乐情绪匹配/缺失兜底；
③ 发布文案去模板感（与微信净文互不干扰）。
"""
import importlib.util
import pathlib
import sys

_BASE = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "douyin_mcp"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _BASE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------- ① FFmpeg 轮播构造 ----------------

def test_carousel_filter_single_image():
    m = _load("publish")
    f = m._build_carousel_filter(1, 3.0, fps=30)
    assert "[0:v]" in f and "concat=n=1" in f and "zoompan" in f and "1080x1920" in f


def test_carousel_filter_multi_image_alternates_zoom():
    m = _load("publish")
    f = m._build_carousel_filter(3, 2.0, fps=30)
    assert f.count(":v]scale") == 3 and "concat=n=3" in f
    # 奇偶交替：放大（+0.10*on）与缩小（1.15-0.10*on）各出现
    assert "min(1.0+0.10*on" in f and "max(1.15-0.10*on" in f


def test_video_cmd_shape_and_safety():
    m = _load("publish")
    cmd = m._build_video_cmd(["a 好图.png", "b.png"], "bgm.mp3", "out.mp4", 2.5)
    # 参数列表 + 无 shell（调用方 shell=False）；文件名原样进列表（无拼接注入面）
    assert cmd[0] == "ffmpeg" and "-y" in cmd
    assert cmd.count("-i") == 3  # 2 图 + 1 音乐
    assert "a 好图.png" in cmd and "bgm.mp3" in cmd and "out.mp4" in cmd
    assert "-shortest" in cmd and "-filter_complex" in cmd


# ---------------- ② 音乐情绪匹配 ----------------

def test_match_music_mood_by_content_keywords():
    m = _load("music")
    assert m.match_music_mood("今天和朋友出去玩，超开心！") == "欢快"
    assert m.match_music_mood("有点难过，想安静一会儿") in ("伤感", "安静")
    assert m.match_music_mood("无情绪关键词的中性描述") == ""


def test_pick_music_mood_fallback_chain():
    m = _load("music")
    assert m.pick_music_mood("治愈") == "治愈"  # 白名单 fallback 优先
    assert m.pick_music_mood("不在白名单", content="好可爱的小猫") == "可爱"  # 内容匹配兜底
    assert m.pick_music_mood("", content="") in m.MUSIC_MOODS  # 随机安全池兜底
    # 向后兼容：旧签名（单参）行为不变
    assert m.pick_music_mood("安静") == "安静"


# ---------------- ③ 发布文案去模板感 ----------------

def test_humanize_post_text_strips_template_phrases():
    m = _load("content")
    out = m.humanize_post_text("大家好，今天给大家分享我的日常，希望你们喜欢！")
    assert "大家好" not in out and "给大家" not in out and "希望你们喜欢" not in out
    assert "分享我的日常" in out  # 正文保留


def test_humanize_post_text_compresses_repeated_emoji():
    m = _load("content")
    out = m.humanize_post_text("好开心😊😊😊")
    assert out == "好开心😊"


def test_humanize_post_text_empty_and_clean():
    m = _load("content")
    assert m.humanize_post_text("") == ""
    out = m.humanize_post_text("正常的文案，没有模板句。")
    assert out == "正常的文案，没有模板句。"
