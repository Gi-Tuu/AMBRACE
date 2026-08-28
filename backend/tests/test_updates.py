# -*- coding: utf-8 -*-
"""更新公告 changelog 解析器测试（#68 修订，2026-08-28）。

get_updates（GET /api/v1/system/updates）依赖 docs/changelog.md；新格式
`## v3.3.9（2026-08-28）` / `## 待发布（2026-08-28）` 与旧格式
`## 2026-08-28（标题，待发布）` 均需兼容；循环 cur 需初始化为 None，
避免首行即表格/无日期时 NameError（旧实现导致接口 500）。
"""
from app.api.system import _changelog_title, _parse_changelog

CHANGELOG_NEW = """\
## 待发布（2026-08-28）
| 内容 | 原因 |
|---|---|
| 群聊游戏体验修复 | 游戏体验问题 |
| 按钮不再要点两次 | 交互优化 |

## v3.3.9（2026-08-28）
| 内容 | 原因 |
|---|---|
| 离线推送 | 消息不遗漏 |

## v3.3.8（2026-08-27）
| 内容 | 原因 |
|---|---|
| 换肤功能上线 | 个性化 |
"""

CHANGELOG_OLD = """\
## 2026-08-26（旧版标题，兼容格式）
| 内容 | 原因 |
|---|---|
| 群聊游戏新增 3 款 | 游戏更丰富 |
"""

CHANGELOG_EDGE = """\
| 首行就是表格 | 无日期 |
|---|---|
| 不应崩溃 | 防御 |
## 有日期标题（2026-08-25）
| 内容 | 原因 |
|---|---|
| 正常条目 | 正常原因 |
"""


def test_parse_new_format_versions():
    """新格式：`## v3.3.9（2026-08-28）` / `## 待发布（2026-08-28）` 解析出标题。"""
    days = _parse_changelog(CHANGELOG_NEW)
    by_date = {d['date']: d for d in days}
    # 2026-08-28 有两个标题（待发布 + v3.3.9）→ 合并为一个折叠日（2 节），标题取首个 + 节数
    assert '2026-08-28' in by_date
    d28 = by_date['2026-08-28']
    assert d28['title'] == '待发布（2 节）'
    assert d28['items'] == [
        {'content': '群聊游戏体验修复', 'reason': '游戏体验问题'},
        {'content': '按钮不再要点两次', 'reason': '交互优化'},
        {'content': '离线推送', 'reason': '消息不遗漏'},
    ]
    assert '2026-08-27' in by_date
    d27 = by_date['2026-08-27']
    assert d27['title'] == 'v3.3.8'
    assert d27['items'] == [{'content': '换肤功能上线', 'reason': '个性化'}]


def test_changelog_title_variants():
    """`_changelog_title`：新格式版本号 / 待发布 / 旧格式括号内标题 / 无括号前缀。"""
    assert _changelog_title('v3.3.9（2026-08-28）', '2026-08-28') == 'v3.3.9'
    assert _changelog_title('待发布（2026-08-28）', '2026-08-28') == '待发布'
    assert _changelog_title('2026-08-26（旧版标题，兼容格式）', '2026-08-26') == '旧版标题，兼容格式'
    assert _changelog_title('v3.3.8', '2026-08-27') == 'v3.3.8'


def test_parse_old_format():
    """旧格式：`## 2026-08-26（标题，待发布）` 兼容，标题取括号内文本。"""
    days = _parse_changelog(CHANGELOG_OLD)
    assert days[0]['date'] == '2026-08-26'
    assert days[0]['title'] == '旧版标题，兼容格式'
    assert days[0]['items'] == [{'content': '群聊游戏新增 3 款', 'reason': '游戏更丰富'}]


def test_parse_no_date_table_row_no_error():
    """首行即表格/无日期表格行不报错（cur 初始化为 None 防御）。"""
    days = _parse_changelog(CHANGELOG_EDGE)
    assert days[0]['date'] == '2026-08-25'
    assert days[0]['title'] == '有日期标题'
    assert days[0]['items'] == [{'content': '正常条目', 'reason': '正常原因'}]


def test_parse_single_section_no_suffix():
    """单节标题不追加 `（N 节）` 后缀。"""
    days = _parse_changelog(CHANGELOG_OLD)
    assert '（' not in days[0]['title']
