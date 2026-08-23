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
    from app.models.runtime_flag import RuntimeFlag
    from app.agent.loop import AGENT_FLAGS
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(select(RuntimeFlag))).scalars().all()
        n = 0
        for r in rows:
            if r.key in AGENT_FLAGS:
                AGENT_FLAGS[r.key] = bool(r.enabled)
                n += 1
        if n:
            _logger.info('Runtime flags loaded from DB: %d overrides', n)
        return n
    except Exception as e:
        _logger.warning('Load runtime flags failed: %s', e)
        return 0


async def set_runtime_flag(key: str, enabled: bool) -> bool:
    '''设置开关：写 DB + 热更新 AGENT_FLAGS 内存；key 不在 AGENT_FLAGS 返回 False'''
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.runtime_flag import RuntimeFlag
    from app.agent.loop import AGENT_FLAGS
    if key not in AGENT_FLAGS:
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


async def get_all_flags() -> list:
    '''返回 AGENT_FLAGS 全量（source: db=被 DB 覆盖 / default=硬编码默认）'''
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.runtime_flag import RuntimeFlag
    from app.agent.loop import AGENT_FLAGS
    db_keys = set()
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(select(RuntimeFlag.key))).scalars().all()
        db_keys = set(rows)
    except Exception:
        pass
    return [
        {'key': k, 'enabled': bool(v), 'source': ('db' if k in db_keys else 'default')}
        for k, v in AGENT_FLAGS.items()
    ]
