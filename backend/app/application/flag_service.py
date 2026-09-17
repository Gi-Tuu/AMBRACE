# 运行时 Feature Flag 开关（2026-08-18）：
# - AGENT_FLAGS（app/agent/loop.py）为硬编码默认值（单一事实源），各模块直接读同一 dict；
# - 启动时 load_runtime_flags() 把 runtime_flags 表中 enabled 覆盖进 AGENT_FLAGS 内存；
# - set_runtime_flag() 写表 + 热更新内存（立即生效，无需重启）；
# - 回退：改回硬编码默认，或删除 DB 行后重启恢复默认。
from app.utils.logger import get_logger

_logger = get_logger('services.flag_service')


async def load_runtime_flags() -> int:
    '''启动时把 DB 覆盖值合并进 AGENT_FLAGS，返回覆盖数；失败静默返回 0（用硬编码默认）'''
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.config import RuntimeFlag
    from app.agent.loop import AGENT_FLAGS
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(select(RuntimeFlag))).scalars().all()
        n = 0
        for r in rows:
            if r.key in AGENT_FLAGS:
                # 类型防护（2026-09-17）：只合并 bool 键；非 bool 键（数字型等）的历史
                # DB 行可能把 int 覆盖成 bool（如 domain_event_retention_days 被写成 True
                # → int(True)=1，保留期从永久变 1 天），故跳过、不更新内存、不计入覆盖数。
                if not isinstance(AGENT_FLAGS[r.key], bool):
                    continue
                AGENT_FLAGS[r.key] = bool(r.enabled)
                n += 1
        if n:
            _logger.info('Runtime flags loaded from DB: %d overrides', n)
        return n
    except Exception as e:
        _logger.warning('Load runtime flags failed: %s', e)
        return 0


async def set_runtime_flag(key: str, enabled: bool) -> bool:
    '''设置开关：写 DB + 热更新 AGENT_FLAGS 内存；key 不在 AGENT_FLAGS 返回 False。

    类型防护（2026-09-17）：非 bool 键（如 domain_event_retention_days=0、
    memory_recall_hop_limit=6 等数字型）拒绝热切——开关页点一下会写入 True，
    而下游按 int 读取时 int(True)=1，会把「永久保留」静默变成「保留 1 天」等错误值。
    这类键只能改硬编码默认/配置，禁止经本通道覆盖；返回 False 且不写库、不更新内存。
    '''
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.config import RuntimeFlag
    from app.agent.loop import AGENT_FLAGS
    if key not in AGENT_FLAGS:
        return False
    # 非 bool 键拒绝热切（见函数 docstring）：避免 bool 覆盖把数字型键静默改成 1
    if not isinstance(AGENT_FLAGS[key], bool):
        _logger.warning('set_runtime_flag rejected: key=%s is not bool (type=%s), skip hot-toggle',
                        key, type(AGENT_FLAGS[key]).__name__)
        return False
    try:
        async with async_session_factory() as db:
            row = (await db.execute(select(RuntimeFlag).where(RuntimeFlag.key == key))).scalar_one_or_none()
            if row is None:
                row = RuntimeFlag(key=key, enabled=enabled)
                db.add(row)
            else:
                row.enabled = enabled
            await db.commit()
        AGENT_FLAGS[key] = enabled
        _logger.info('Runtime flag set: %s=%s', key, enabled)
        return True
    except Exception as e:
        _logger.warning('Set runtime flag %s failed: %s', key, e)
        return False


def _flag_type(v) -> str:
    '''flag 注册表类型名（bool / int / float / 其它类名）——P3-7 展示层按类型序列化用。'''
    if isinstance(v, bool):
        return 'bool'
    if isinstance(v, int):
        return 'int'
    if isinstance(v, float):
        return 'float'
    return type(v).__name__


async def get_all_flags() -> list:
    '''返回 AGENT_FLAGS 全量（source: db=被 DB 覆盖 / default=硬编码默认）。

    P3-7（2026-09-17）：此前对每个值一刀切 ``bool(v)``，数字型 flag（如
    ``domain_event_retention_days=0``＝永久保留）被折叠成 ``False`` 展示成「关」，语义错。
    现按注册表类型序列化，字段口径：
    - ``value``：注册表**原值/原类型**（bool 保持 bool、int 保持 int、float 保持 float）；
    - ``type``：``bool`` / ``int`` / ``float``，供展示层决定渲染（开关 vs 数值）；
    - ``enabled``：**保持 bool**（兼容面不变）——App 端 ``FeatureFlagService.refresh`` 以
      ``f['enabled'] as bool?`` 解析，直接返回数字会让整个开关页加载失败；故数字型的
      「不显示成布尔」由新增的 ``value``/``type`` 承载，Flutter 展示层跟进另批处理。
    '''
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.config import RuntimeFlag
    from app.agent.loop import AGENT_FLAGS
    db_keys = set()
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(select(RuntimeFlag.key))).scalars().all()
        db_keys = set(rows)
    except Exception:
        pass
    return [
        {
            'key': k,
            'enabled': bool(v),
            'value': v,
            'type': _flag_type(v),
            'source': ('db' if k in db_keys else 'default'),
        }
        for k, v in AGENT_FLAGS.items()
    ]
