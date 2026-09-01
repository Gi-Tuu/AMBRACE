# voice.gateway.clean_voice_text 单元测试：TTS 不念出说话人/神态前缀。
from app.voice.gateway import clean_voice_text


def test_clean_character_prefix_with_tone():
    assert clean_voice_text("sam（宠溺说）：我想你了", "sam") == "我想你了"
    assert clean_voice_text("sam：你好呀", "sam") == "你好呀"
    assert clean_voice_text("sam 语音说：在的", "sam") == "在的"


def test_clean_arbitrary_name_tone_prefix():
    assert clean_voice_text("另一个名字（笑）：哈哈哈") == "哈哈哈"


def test_clean_inline_tone_and_tags():
    assert clean_voice_text("我想你（笑）") == "我想你"
    assert clean_voice_text("【sam】吃饭了吗") == "吃饭了吗"


def test_clean_keeps_normal_speech():
    assert clean_voice_text("今天天气不错，我们出去走走吧") == "今天天气不错，我们出去走走吧"


def test_clean_empty_safe():
    assert clean_voice_text("") == ""
    assert clean_voice_text(None) == ""


def test_clean_keeps_real_talk_parens():
    # 句末真实口白括号不再被整段剥掉
    assert clean_voice_text("一共三个（都在桌上）") == "一共三个（都在桌上）"
    assert clean_voice_text("就是昨天那家（公司楼下）") == "就是昨天那家（公司楼下）"


def test_clean_keeps_mid_sentence_prefix():
    # 通用说话人前缀已锚定行首，句中片段不删
    assert clean_voice_text("其实 刚才（小声）：别担心") == "其实 刚才（小声）：别担心"


def test_clean_stage_tail_still_stripped():
    # 神态/动作提示仍剥（兼容既有行为）
    assert clean_voice_text("我想你（笑）") == "我想你"
    assert clean_voice_text("等我一下（叹气）") == "等我一下"
