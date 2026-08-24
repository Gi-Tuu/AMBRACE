"""记忆来源元数据注册表 — 统一管理来源的展示名与图标标识（前端据此渲染，消除硬编码不一致）"""
from __future__ import annotations

# 顶层来源（memories.source 字段）
SOURCE_META: dict[str, dict] = {
    "chat": {"label": "聊天", "icon": "chat"},
    "moment": {"label": "朋友圈", "icon": "moment"},
    "diary": {"label": "日记", "icon": "diary"},
    "bio": {"label": "自述", "icon": "bio"},
    "status": {"label": "状态", "icon": "status"},
    "profile": {"label": "用户主页", "icon": "profile"},
    "pet": {"label": "宠物", "icon": "pet"},
    "life": {"label": "AI 生活", "icon": "life"},  # 2026-08-12 Life Engine：AI 自己的生活事件/反思/笔记
    "group": {"label": "群聊", "icon": "chat"},  # 2026-08-14 Phase 3：家庭群聊记忆
}

# chat 来源下的子分类（memories.sub_type 字段）细分展示
CHAT_SUB_META: dict[str, dict] = {
    "extracted": {"label": "提取", "icon": "extracted"},
    "bio": {"label": "自述", "icon": "bio"},
    "status": {"label": "状态", "icon": "status"},
    "relationship": {"label": "关系", "icon": "relationship"},
}

_UNKNOWN = {"label": "未知", "icon": "unknown"}


def memory_source_meta(source: str | None, sub_type: str | None = None) -> dict:
    """返回来源展示元数据：{label, icon}

    - 无来源 -> 未知
    - source=chat 且 sub_type 可细分时 -> 「聊天·提取」等组合标签
    """
    base = SOURCE_META.get(source or "", _UNKNOWN)
    label, icon = base["label"], base["icon"]
    if source == "chat" and sub_type:
        sub = CHAT_SUB_META.get(sub_type)
        if sub:
            label = f"{base['label']}·{sub['label']}"
            icon = sub["icon"]
    return {"label": label, "icon": icon}
