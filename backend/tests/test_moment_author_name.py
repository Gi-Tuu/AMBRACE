# -*- coding: utf-8 -*-
"""§4.2（2026-09-09）：朋友圈作者名解析不得产出字面 None。

- ``_resolve_author_name``：用户动态（character_id 为 0/None）返回空串，与
  ``_resolve_author_gender`` 对称，绝不隐式返回 None；
- 三处回复评论的 prompt（AI↔AI 互评 ×2、AI 回复用户评论 ×1）必须带 ``or '朋友'`` 兜底，
  否则用户自主动态下会拼出「不要把 None 做的事安到…」。
"""
import asyncio
import inspect


class _Moment:
    def __init__(self, character_id):
        self.character_id = character_id


def test_用户动态_返回空串而非None():
    from app.application.moment_service import _resolve_author_name

    for cid in (0, None):
        name = asyncio.run(_resolve_author_name(_Moment(cid)))
        assert name == "", cid
        assert name is not None


def test_与gender解析对称():
    """用户动态：name 与 gender 同为空串（修复前 name 为 None）"""
    from app.application.moment_service import _resolve_author_gender, _resolve_author_name

    m = _Moment(0)
    assert asyncio.run(_resolve_author_name(m)) == asyncio.run(_resolve_author_gender(m)) == ""


def test_回复评论prompt无裸author_name():
    """三处回复评论 prompt 必须带兜底：源码中不得残留裸 ``{author_name}``（无 or）。"""
    from app.application import moment_service

    src = inspect.getsource(moment_service)
    assert "{author_name}" not in src, "存在未兜底的 {author_name}，用户动态下会拼出 None"
    assert "不要把{author_name or '朋友'}做的事安到" in src
