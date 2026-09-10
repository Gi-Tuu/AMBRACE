# -*- coding: utf-8 -*-
"""插件独立 ORM 基类与 MetaData（T5，#72 第三方插件市场地基；2026-09-10）。

渠道 / 第三方插件自带的关系表一律继承 ``PluginBase``，注册进独立的 ``plugin_metadata``，
不再污染主 ``app.models.base.Base.metadata``。

设计约束：
- 物理表仍建在主库（同一 engine/数据库文件），只是 MetaData 逻辑分离 —— 不拆库、不换连接；
- 主 alembic 版本链【不】管理插件表；插件表由 ``app.plugins.registry`` 在加载后
  以 ``plugin_metadata.create_all(checkfirst=True)`` 幂等建立，存量库零数据迁移；
- 【不设 naming_convention】：主 Base 为裸 DeclarativeBase，且插件表的 Index/UniqueConstraint
  均已显式命名（uq_douyin_* / uq_wechat_*），保持默认命名以与存量约束名完全一致；
- 插件表【禁止】ForeignKey 指向主表、主表也不 FK 插件表（跨 MetaData 无法建 FK）；
  需要关联时只存 id 列、在应用层 join（现状 7 张插件表已全部满足）。
"""
from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# 插件表独立 MetaData（与主 Base.metadata 平行；物理同库）。
plugin_metadata: MetaData = MetaData()


class PluginBase(DeclarativeBase):
    """插件自有 ORM 模型基类：metadata 独立于主 Base.metadata。

    用法（插件 models 模块）::

        from app.plugins.plugin_base import PluginBase

        class MyBinding(PluginBase):
            __tablename__ = "myplugin_bindings"
            ...
    """

    metadata = plugin_metadata
