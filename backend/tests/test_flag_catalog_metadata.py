# -*- coding: utf-8 -*-
"""开关目录元数据测试（A4，2026-09-20）：目录改由后端下发，App 侧硬编码降为回落。

守的五条底线：
1. AGENT_FLAGS 的每个键都能在目录里取到条目（新增键忘了登记 → 直接失败并列出 key）；
2. zh/en 文案非空且不含实现术语（flag / AGENT_FLAGS / DB / prompt）；
3. 分组顺序稳定：组 id 集合与 App 侧 feature_flag_catalog.dart 的 _groupOrder 口径一致；
4. scope 与 flag_service.USER_SCOPED_FLAG_KEYS 一致（user 5 键、其余 server）；
5. visible 键集合 == App 现有「常用开关」白名单 10 键。
"""
import pytest

from app.agent.loop import AGENT_FLAGS
from app.application import flag_service
from app.application.flag_catalog import (
    CATALOG_GROUPS,
    FLAG_CATALOG,
    OTHER_GROUP,
    catalog_for,
    meta_for,
)

# App 侧 feature_flags_screen.dart 的 _visibleKeys（常用开关白名单）
APP_VISIBLE_KEYS = {
    'weave_3d',
    'agent_social_light_context',
    'agent_loop_group_chat',
    'agent_loop_social',
    'global_user_facts',
    'user_fact_location',
    'user_current_location_share',
    'user_fact_relationship',
    'user_fact_health',
    'proactive_outreach_v2',
}

# App 侧 feature_flag_catalog.dart 的 _groupOrder 分组口径 + 兜底「其他高级开关」组
EXPECTED_GROUP_IDS = [
    'agent',            # 智能体运行与认知
    'proactive',        # 主动消息
    'games',            # 群聊小游戏
    'life',             # AI 自主生活
    'lifesense',        # 生命感增强
    'outreach_natural',  # 主动消息自然化（B1）
    'memory',           # 记忆检索与注入（实验灰度）
    'curated',          # 编纂知识与前瞻意图
    'cross_char',       # 跨角色用户事实（B1）
    'working',          # 工作记忆（M3）
    'provider',         # 插件与提供商
    'pacing',           # 主动投放节制（B1）
    'review',           # 主动复习与回忆化（H）
    'channel',          # 渠道绑定与群认知
    'tool_trace',       # 工具轨迹治理
    'device_action',    # 行动通道（C1a）
    'other',            # 其他高级开关（未登记键兜底）
]

# 文案里不允许出现的实现术语（用户向文案，大小写不敏感）
_FORBIDDEN_TERMS = ('flag', 'agent_flags', 'db', 'prompt')


def test_every_agent_flag_has_catalog_entry():
    '''① 每个 AGENT_FLAGS 键都有目录条目（缺任一个直接失败并列出 key）。'''
    missing = [k for k in AGENT_FLAGS if k not in FLAG_CATALOG]
    assert not missing, f'catalog 缺少以下键：{missing}'
    unknown = [k for k in FLAG_CATALOG if k not in AGENT_FLAGS]
    assert not unknown, f'catalog 含已不存在的键：{unknown}'


@pytest.mark.parametrize('lang', ['zh', 'en'])
def test_catalog_texts_non_empty_and_user_facing(lang):
    '''② zh/en 文案非空，且不含 flag / AGENT_FLAGS / DB / prompt 等实现术语。'''
    keys = list(AGENT_FLAGS)
    items = {i['key']: i for g in catalog_for(keys, lang) for i in g['items']}
    assert len(items) == len(keys), 'catalog_for 丢键'
    for k in keys:
        it = items[k]
        assert it['title'].strip(), f'{lang} 标题为空：{k}'
        assert it['desc'].strip(), f'{lang} 说明为空：{k}'
        text = f"{it['title']} {it['desc']}".lower()
        for term in _FORBIDDEN_TERMS:
            assert term not in text, f'{lang} 文案含实现术语 {term!r}：{k} -> {it["title"]}'


def test_group_order_stable_and_matches_app():
    '''③ 分组顺序稳定，组 id 集合与 App 侧 _groupOrder 口径一致（含兜底 other）。'''
    ids = [g['id'] for g in CATALOG_GROUPS]
    assert ids == EXPECTED_GROUP_IDS, f'分组 id/顺序与 App 不一致：{ids}'
    orders = [g['order'] for g in CATALOG_GROUPS]
    assert orders == sorted(orders), 'CATALOG_GROUPS 必须按 order 升序'
    for g in CATALOG_GROUPS:
        assert g['label_zh'].strip() and g['label_en'].strip(), f'分组缺标签：{g["id"]}'
    # catalog_for 产出的分组顺序 == CATALOG_GROUPS 顺序（空组不出现）
    out = catalog_for(list(AGENT_FLAGS), 'zh')
    assert [g['group'] for g in out] == [g['group'] for g in catalog_for(list(AGENT_FLAGS), 'zh')]
    emitted = [g['group'] for g in out]
    assert emitted == [i for i in ids if i in emitted], 'catalog_for 分组顺序错乱'
    assert [g['group_order'] for g in out] == sorted(g['group_order'] for g in out)


def test_scope_matches_user_scoped_keys():
    '''④ scope 由 USER_SCOPED_FLAG_KEYS 推导：user 10 键，其余 server。'''
    items = {i['key']: i for g in catalog_for(list(AGENT_FLAGS), 'zh') for i in g['items']}
    user_keys = {k for k, i in items.items() if i['scope'] == 'user'}
    assert user_keys == set(flag_service.USER_SCOPED_FLAG_KEYS), \
        f'scope 与 USER_SCOPED_FLAG_KEYS 不一致：{user_keys ^ set(flag_service.USER_SCOPED_FLAG_KEYS)}'
    assert len(user_keys) == 10, f'按账号生效的键应为 10 个，实际 {len(user_keys)}'
    assert all(i['scope'] in ('user', 'server') for i in items.values())


def test_visible_keys_match_app_whitelist():
    '''⑤ visible 键集合 == App 现有常用开关白名单 10 键。'''
    visible = {k for k, m in FLAG_CATALOG.items() if m['visible']}
    assert visible == APP_VISIBLE_KEYS, \
        f'visible 与 App 白名单不一致：缺 {APP_VISIBLE_KEYS - visible} / 多 {visible - APP_VISIBLE_KEYS}'


def test_meta_for_contract_and_unknown_key():
    '''meta_for 契约：字段齐全、按 lang 选文案；未登记键落 other 组、标题用键名。'''
    m = meta_for('weave_3d', 'zh')
    assert set(m) == {'title', 'desc', 'group', 'group_order', 'order', 'visible'}
    assert m['title'] and m['desc'] and m['visible'] is True
    assert meta_for('weave_3d', 'en')['title'] != m['title'], 'en 文案应独立于 zh'
    assert meta_for('weave_3d', 'zh-CN')['title'] == m['title'], 'zh-CN 应走中文'

    unknown = meta_for('no_such_key', 'zh')
    assert unknown['group'] == OTHER_GROUP
    assert unknown['title'] == 'no_such_key'
    assert unknown['visible'] is False
    assert unknown['desc'].strip()

    # catalog_for 里未登记键落 other 组（不丢键）
    out = catalog_for(['weave_3d', 'no_such_key'], 'zh')
    keys = [i['key'] for g in out for i in g['items']]
    assert set(keys) == {'weave_3d', 'no_such_key'}
    other = [g for g in out if g['group'] == OTHER_GROUP]
    assert len(other) == 1 and other[0]['items'][0]['key'] == 'no_such_key'
