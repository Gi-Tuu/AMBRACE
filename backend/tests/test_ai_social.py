# -*- coding: utf-8 -*-
"""AI-AI 私聊串线防护测试（2026-08-16）"""
from app.scheduler.ai_social import _filter_cross_char_news


def test_filter_涉及其他角色私事排除():
    texts = [
        "用户最近在加班，很累",
        "用户和小遥计划后天去杭州",
        "小遥的宠物团团来用户家玩了",
        "用户换了个新手机",
    ]
    out = _filter_cross_char_news(texts, ["小遥", "阿澈", "小满"])
    assert "小遥的宠物团团来用户家玩了" not in out
    assert "用户和小遥计划后天去杭州" not in out
    assert "用户最近在加班，很累" in out
    assert "用户换了个新手机" in out


def test_filter_无角色名全保留():
    texts = ["用户今天心情不错", "用户想学做饭"]
    assert _filter_cross_char_news(texts, ["小遥"]) == texts


def test_filter_空文本跳过():
    assert _filter_cross_char_news(["", "有用信息"], ["小遥"]) == ["有用信息"]


def test_filter_多角色名之一命中即排除():
    texts = ["用户和小满约了明天吃饭", "用户想养一只猫", "阿澈说要来串门"]
    out = _filter_cross_char_news(texts, ["小满", "阿澈"])
    assert "用户和小满约了明天吃饭" not in out
    assert "阿澈说要来串门" not in out
    assert "用户想养一只猫" in out


def test_filter_角色名非目标场景不排除():
    # 无任何角色名 → 全保留（含"用户"字样不参与过滤）
    texts = ["用户今天心情不错", "用户明天要去开会"]
    assert _filter_cross_char_news(texts, ["小遥", "小满"]) == texts


def test_filter_空名字列表等价不过滤():
    texts = ["小遥说后天回来"]
    assert _filter_cross_char_news(texts, []) == texts


def test_filter_群聊多角色名单全量匹配():
    # 群聊场景：名单 = 群内其他所有角色，任何提及都不共享
    texts = ["用户和小遥、阿澈一起玩游戏", "用户自己在家做饭"]
    names = ["小遥", "阿澈", "小满"]
    out = _filter_cross_char_news(texts, names)
    assert "用户和小遥、阿澈一起玩游戏" not in out
    assert "用户自己在家做饭" in out
