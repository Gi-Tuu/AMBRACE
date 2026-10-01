# -*- coding: utf-8 -*-
"""A4 批 8 块 C「短句形态约束」M0＋M1 测试（零行为优先，flag 默认关）。

钉住五件事：
① 通知预览纯函数与**收口前旧表达式逐字节等价**（含恰好 50 字 / 49 / 51 / 空串 / 纯空白 /
   中文按字符数 / emoji 代理对）；
② 两份 ``[:50]`` 拷贝确实收口成单一真源（调用点源码断言，防止「收了个寂寞」）；
③ 读数分桶与截顶占比口径（含 ``>500`` 恒 0 的天花板、SQL length 取字符数不是字节数）；
④ flag ``message_shape_notify_limit`` 默认 False 且双向登记（AGENT_FLAGS ＋ 开关目录）；
⑤ prompt 侧：关＝逐字节旧 prompt（返回原列表对象本身），开＝只多那一句且不改动传入列表
   （只进上下文、不落库不进记忆）。

项目未装 pytest-asyncio，统一 asyncio.run；临时库一律 tmp_path，不连生产库。
"""
import asyncio
import inspect
import os
import string

import pytest
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agent import nodes
from app.application.chat import io as chat_io
from app.domain import message_shape as ms
from app.flags.agent_flags import AGENT_FLAGS
from app.scheduling import scheduler as scheduler_engine


def _legacy(content: str) -> str:
    """收口前两处逐字重复的旧表达式（对照基准，禁止改）。"""
    return content[:50] + ("…" if len(content) > 50 else "")


@pytest.fixture()
def flags():
    saved = dict(AGENT_FLAGS)
    yield AGENT_FLAGS
    AGENT_FLAGS.clear()
    AGENT_FLAGS.update(saved)


# ── ① 预览纯函数：与旧实现逐例对照 ──

CORPUS = [
    "", " ", "  \t \n ", "短", "你好",
    "a" * 49, "a" * 50, "a" * 51, "a" * 200,
    "中" * 49, "中" * 50, "中" * 51, "中" * 120,          # CJK：按字符数不是 UTF-8 字节数
    "😀" * 30, "😀" * 51,                                  # 代理对
    "第一句在这里结束。后面还有很长的内容" * 6,
    "混合 abc 中文 😀 标点！？——换行\n尾巴" * 5,
    string.printable[:70],
]


@pytest.mark.parametrize("text", CORPUS)
def test_preview_identical_to_legacy_expression(text):
    assert ms.notify_preview(text) == _legacy(text)


def test_preview_exact_50_has_no_ellipsis():
    text = "字" * 50
    assert ms.notify_preview(text) == text
    assert "…" not in ms.notify_preview(text)


def test_preview_49_and_51_boundary():
    assert ms.notify_preview("字" * 49) == "字" * 49
    assert ms.notify_preview("字" * 51) == "字" * 50 + "…"
    assert len(ms.notify_preview("字" * 51)) == 51        # 截到 50 再补省略号，与旧口径一致


def test_preview_empty_and_whitespace_only():
    assert ms.notify_preview("") == ""
    assert ms.notify_preview("   ") == "   "              # 纯空白不截不加省略号（旧行为）
    assert ms.notify_preview(" \n\t" * 30) == (" \n\t" * 30)[:50] + "…"


def test_preview_counts_characters_not_utf8_bytes():
    text = "我爱你中国" * 20                                    # 100 字符 / 300 字节
    out = ms.notify_preview(text)
    assert out == text[:50] + "…"
    assert len(out.encode("utf-8")) > len(out)             # 字节数确实在增加，口径没跟着变


# ── ② 两份拷贝收口（调用点源码断言） ──

def test_chat_io_preview_copy_collapsed():
    src = inspect.getsource(chat_io._push_user_notify)
    assert "notify_body(" in src
    assert "content[:50]" not in src                        # 旧拷贝已不在此处


def test_scheduler_preview_copy_collapsed():
    src = inspect.getsource(scheduler_engine.send_to_session)
    assert "notify_body(" in src
    assert "content[:50]" not in src
    assert "content[:500]" in src                           # 留痕截顶属既有行为，未动


# ── ③ fit_to_form：关闸逐字节 / 载体口径 / 首句次序 ──

def test_fit_disabled_returns_unchanged_even_when_over_limit():
    text = "先说一句。" + "后面拉很长的一段话用来验证关闸不裁剪" * 4
    assert ms.fit_to_form(text, "notify", False) == text    # enabled=False ⇒ 原样对象


def test_fit_unknown_form_and_none_limit_return_unchanged():
    text = "超长" * 200
    assert ms.fit_to_form(text, "surface_not_defined", True) == text
    for form in ("bubble", "group_preview"):
        assert ms.MSG_FORM_LIMITS[form] is None             # 展示侧不裁剪（§2.3(1) 判断）
        assert ms.fit_to_form(text, form, True) == text


def test_fit_within_limit_unchanged():
    text = "今天路过便利店，买了你想喝的那瓶。"
    assert len(text) <= ms.NOTIFY_PREVIEW_LIMIT
    assert ms.fit_to_form(text, "notify", True) == text


def test_fit_takes_first_sentence_when_head_within_limit():
    text = "先把最要紧的说完了。" + "后面这些是展开解释，通知里不需要。" + "补" * 80
    out = ms.fit_to_form(text, "notify", True, limit=20)
    assert out == "先把最要紧的说完了。"                    # 首句整句保留，不加省略号
    assert "…" not in out
    assert len(out) <= 20


def test_fit_hard_truncates_when_first_sentence_still_over_limit():
    text = "这一句长得离谱一直到把上限全部吃完还不够" * 5      # 无句末标点 → 整段即首句
    out = ms.fit_to_form(text, "notify", True, limit=10)
    assert out == text[:10] + "…"                           # 与旧 [:N]+"…" 同一视觉形态


def test_fit_empty_text_is_stable():
    assert ms.fit_to_form("", "notify", True) == ""
    assert ms.first_sentence("") == ""


# ── ④ 发送侧入口：flag 关＝逐字节旧行为 ──

def test_notify_body_flag_off_matches_legacy_preview(flags):
    flags["message_shape_notify_limit"] = False
    for text in CORPUS:
        assert ms.notify_body(text) == _legacy(text)


def test_notify_body_flag_on_applies_carrier_limit(flags):
    flags["message_shape_notify_limit"] = True
    text = "先说最要紧的这句。然后是长篇解释" + "长" * 120
    assert ms.notify_body(text) == ms.fit_to_form(text, "notify", True)
    flags["message_shape_notify_limit"] = False
    assert ms.notify_body(text) == _legacy(text)             # 一键回退，逐字节旧行为


# ── ⑤ 读数分桶与截顶占比 ──

def test_bucket_edges_are_inclusive_and_match_spec():
    assert ms.length_bucket(0) == "le_50"
    assert ms.length_bucket(50) == "le_50"
    assert ms.length_bucket(51) == "51_100"
    assert ms.length_bucket(100) == "51_100"
    assert ms.length_bucket(101) == "101_200"
    assert ms.length_bucket(200) == "101_200"
    assert ms.length_bucket(201) == "201_500"
    assert ms.length_bucket(500) == "201_500"
    assert ms.length_bucket(501) == "gt_500"


def test_summarize_buckets_counts_and_ratios():
    lengths = [10, 50, 51, 100, 101, 200, 201, 500]
    out = ms.summarize_lengths(lengths)
    assert out["samples"] == 8
    assert out["buckets"] == {
        "le_50": 2, "51_100": 2, "101_200": 2, "201_500": 2, "gt_500": 0,
    }
    assert out["ratios"]["le_50"] == 0.25
    assert out["over_preview_limit"] == 0.75                 # 原始正文超 50 字的占比
    assert out["ceiling_hit"] == {"count": 1, "ratio": 0.125}  # 落在 500 上＝截顶证据


def test_summarize_over_preview_limit_is_one_when_nothing_fits():
    out = ms.summarize_lengths([80, 120, 500])
    assert out["over_preview_limit"] == 1.0                  # 非「比值」而是「全部超限」
    assert out["ratios"]["le_50"] == 0.0


def test_summarize_empty_samples_no_division_by_zero():
    out = ms.summarize_lengths([])
    assert out["samples"] == 0
    assert set(out["buckets"].values()) == {0}
    assert out["ratios"]["le_50"] == 0.0
    assert out["over_preview_limit"] == 0.0
    assert out["ceiling_hit"] == {"count": 0, "ratio": 0.0}
    assert out["percentiles"] == {"p50": 0, "p90": 0, "p99": 0}


def test_summarize_percentiles_nearest_rank():
    out = ms.summarize_lengths(list(range(1, 101)))          # 1..100
    assert out["percentiles"] == {"p50": 50, "p90": 90, "p99": 99}
    assert ms.summarize_lengths([7])["percentiles"]["p99"] == 7


def test_read_endpoint_length_probe_uses_characters(tmp_path):
    """端点取数口径核对：SQL length() 在 sqlite 下返回**字符数**（中文不按字节膨胀）。"""
    from app.models.character import ProactiveMessageLog

    db_path = os.path.join(str(tmp_path), "shape.db").replace("\\", "/")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    texts = ["短句。", "中" * 60, "混合 abc 中文 😀 尾巴" * 4, "长" * 600]

    async def _run():
        import app.models  # noqa: F401  确保全部 ORM 进入 metadata
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(
                c, tables=[ProactiveMessageLog.__table__], checkfirst=True
            ))
        async with factory() as db:
            for text in texts:
                db.add(ProactiveMessageLog(
                    character_id=1, message_type="proactive", content=text[:500]
                ))
            await db.commit()
        async with factory() as db:
            got = (await db.execute(
                select(sa_func.length(ProactiveMessageLog.content))
                .order_by(ProactiveMessageLog.id)
            )).scalars().all()
        return [int(g) for g in got]

    try:
        got = asyncio.run(_run())
    finally:
        asyncio.run(engine.dispose())

    assert got == [len(t[:500]) for t in texts]              # 与 Python len 同口径
    out = ms.summarize_lengths(got)
    assert out["samples"] == 4
    assert out["buckets"]["le_50"] == 1 and out["buckets"]["51_100"] == 2
    assert out["buckets"]["gt_500"] == 0                     # 结构上永远为 0（截顶在前）
    assert out["ceiling_hit"] == {"count": 1, "ratio": 0.25}  # 600 字那条被截到 500＝截顶证据


# ── ⑥ flag 双向登记 + 默认关 ──

def test_flag_registered_default_false_in_agent_flags():
    assert "message_shape_notify_limit" in AGENT_FLAGS
    assert AGENT_FLAGS["message_shape_notify_limit"] is False


def test_flag_registered_in_user_facing_catalog():
    from app.application.flag_catalog import FLAG_CATALOG

    row = FLAG_CATALOG.get("message_shape_notify_limit")
    assert row is not None, "新 flag 必须双向登记（缺项由 test_flag_catalog_metadata 兜底）"
    assert row["visible"] is False
    for field in ("title_zh", "desc_zh", "title_en", "desc_en"):
        assert row[field] and row[field].strip()
    assert row["desc_zh"] != row["desc_en"]


# ── ⑦ prompt 侧：关＝逐字节旧 prompt，开＝只多那一句 ──

BASE_MSGS = [
    {"role": "system", "content": "你是小慧"},
    {"role": "system", "content": "【本轮提醒】今天北京有雨"},
    {"role": "user", "content": "在干嘛"},
]


def test_hint_constant_is_centralized_and_shaped_like_wechat_hint():
    hint = nodes.NOTIFY_SHAPE_HINT
    assert "{limit}" in hint                                # 上限由载体口径下发，不写死两处
    assert "通知" in hint and "一句" in hint
    assert "元信息" in hint                                  # 三条纪律之一：不把元信息漏给对方
    assert nodes.WECHAT_CHANNEL_HINT != hint                 # 独立一句，不复用渠道提示


def test_compose_flag_off_returns_same_list_object_byte_identical(flags):
    flags["message_shape_notify_limit"] = False
    out = ms.compose_notify_shape_messages(BASE_MSGS, notify_surface=True)
    assert out is BASE_MSGS                                  # 同一对象 ⇒ prompt 逐字节旧行为
    assert out == BASE_MSGS


def test_compose_flag_on_online_surface_adds_nothing(flags):
    flags["message_shape_notify_limit"] = True
    out = ms.compose_notify_shape_messages(BASE_MSGS, notify_surface=False)
    assert out is BASE_MSGS                                  # 气泡面不注入（本轮不走通知）


def test_compose_flag_on_notify_surface_adds_exactly_one_line_after_last_system(flags):
    flags["message_shape_notify_limit"] = True
    out = ms.compose_notify_shape_messages(BASE_MSGS, notify_surface=True)
    assert len(out) == len(BASE_MSGS) + 1
    assert out[:2] == BASE_MSGS[:2]
    assert out[2]["role"] == "system"                        # 插在最后一条 system 之后
    assert nodes.NOTIFY_SHAPE_HINT.format(limit=ms.NOTIFY_PREVIEW_LIMIT) == out[2]["content"]
    assert out[3] == BASE_MSGS[2]                            # user 消息仍在（红线：user 不挪位）
    assert str(ms.NOTIFY_PREVIEW_LIMIT) in out[2]["content"]


def test_compose_does_not_mutate_input_list(flags):
    """只进上下文：传入的装配结果不被改动，提示文本进不了任何持久化通道。"""
    flags["message_shape_notify_limit"] = True
    original = [dict(m) for m in BASE_MSGS]
    ms.compose_notify_shape_messages(BASE_MSGS, notify_surface=True)
    assert BASE_MSGS == original
    assert all(nodes.NOTIFY_SHAPE_HINT not in m["content"] for m in BASE_MSGS)


def test_compose_with_no_system_message_inserts_at_head(flags):
    flags["message_shape_notify_limit"] = True
    msgs = [{"role": "user", "content": "hi"}]
    out = ms.compose_notify_shape_messages(msgs, notify_surface=True)
    assert out[0]["role"] == "system" and len(out) == 2


def test_compose_tolerates_malformed_entries(flags):
    """异常隔离：装配结果里混入非 dict / 无 role 元素时不炸，插入位仍单调。"""
    flags["message_shape_notify_limit"] = True
    msgs = [{"role": "system", "content": "s"}, None, {"content": "无 role"}, "裸串"]
    out = ms.compose_notify_shape_messages(msgs, notify_surface=True)
    assert len(out) == 5
    assert out[-1] == "裸串"


def test_compose_custom_limit_is_honoured(flags):
    flags["message_shape_notify_limit"] = True
    out = ms.compose_notify_shape_messages(BASE_MSGS, notify_surface=True, limit=40)
    assert "40" in out[2]["content"]
