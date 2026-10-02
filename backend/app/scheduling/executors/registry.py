"""etype 执行器注册表 — HANDLERS / @handler / BACKGROUND_TYPES（A20 批 4a，方案 §2.3）

**分派化的前提：键集互不相交。** 原 ``arbiter._execute`` 梯子里每个分支都以
``etype == "<某值>"`` 或 ``etype in (<一组值>)`` 为条件，各键两两不重叠 ⇒ 「有序 if 梯子」与
「无序 dict 查表」在 etype 维度上等价。批 4 每搬一组都要重核键集（守卫 ⑨ 钉住本批的键）。

``@handler`` 对同一 etype 二次登记**直接抛**：键集一旦重叠，梯子「先命中者赢」的语义就悄悄变了，
而查表只会留最后登记的那份——宁可炸在导入期，也不要静默换实现。

未接管的键（outreach 五类 / plugin，批 4b 才搬）由 ``dispatch`` 返回 ``None`` 交回 arbiter 梯子。
"""
from collections.abc import Callable

# 后台行为类型：不推送消息，不受用户活跃/主动消息限额影响（自身限额在各分支内部处理）
# A20 批 4a 起由本模块单一来源持有：guards 的 Stage 2 豁免与执行器分组共用同一份键集。
BACKGROUND_TYPES = ("ai_social", "group_active", "pet_visit")

HANDLERS: dict[str, Callable] = {}


def handler(*etypes: str):
    """把执行器登记到 ``HANDLERS[etype]``（可一次登记多个键）。"""
    def _decorator(fn):
        for etype in etypes:
            if etype in HANDLERS:
                raise ValueError(
                    f"etype {etype!r} 重复登记：已被 {HANDLERS[etype].__qualname__} 占用，"
                    f"{fn.__qualname__} 不能再登记（键集必须互不相交）"
                )
            HANDLERS[etype] = fn
        return fn
    return _decorator
