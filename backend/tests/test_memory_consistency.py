"""记忆一致性测试：星级映射 / 台词原文拦截 / 领域常量（纯函数，不依赖 DB/LLM）。

对应工程规范（docs/engineering-protocol.md）：Memory 不是聊天记录仓库、记忆写入前置校验。
"""

from app.memory.constants import (
    S_BY_TYPE,
    REVIEW_MIN_IMPORTANCE,
    VECTOR_DEDUP_THRESHOLD,
    DECAY_THRESHOLD_PCT,
)
from app.memory.dialogue_filter import looks_like_raw_dialogue
from app.memory.service import star_from_pct


def test_star_from_pct_边界():
    assert star_from_pct(0) == 1
    assert star_from_pct(20) == 1
    assert star_from_pct(40) == 2
    assert star_from_pct(60) == 3
    assert star_from_pct(80) == 4
    assert star_from_pct(100) == 5
    assert star_from_pct(120) == 5  # 封顶
    assert star_from_pct(-10) == 1  # 下限


def test_star_from_pct_与重要性一致性():
    # 高重要性必然高星级（单调不降）
    assert star_from_pct(70) >= star_from_pct(50)
    assert star_from_pct(110) == 5


def test_初始强度按类型查表():
    # 每种常见记忆类型都有初始强度，且 user_info 强于 event（更持久）
    for t in ("user_info", "preference", "insight", "event"):
        assert t in S_BY_TYPE
    assert S_BY_TYPE["user_info"] > S_BY_TYPE["event"]


def test_复习与去重阈值常量():
    assert REVIEW_MIN_IMPORTANCE == 40.0
    assert 0.0 < VECTOR_DEDUP_THRESHOLD < 1.0
    assert DECAY_THRESHOLD_PCT == 20.0


def test_台词原文拦截_省略号开头():
    assert looks_like_raw_dialogue("……怕什么怕")
    assert looks_like_raw_dialogue("。。。没事吧")


def test_台词原文拦截_叙事括号():
    assert looks_like_raw_dialogue("（移开视线，声音闷闷的）我不饿")
    assert looks_like_raw_dialogue("（摸了摸她的头）别难过")


def test_台词原文不误伤正常记忆():
    # 强信号启发式，普通陈述不应被拦截
    assert not looks_like_raw_dialogue("用户喜欢被照顾")
    assert not looks_like_raw_dialogue("我们一起吃了火锅")
    assert not looks_like_raw_dialogue("")
    assert not looks_like_raw_dialogue("啊")
