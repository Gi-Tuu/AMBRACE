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


# ── 开关策略元数据（控制台管理面 P2，2026-09-19）─────────────────────────────────
# flag_settings 每键一行：self_service（是否允许用户自助改）/ server_locked（锁定 = 仅控制台可改）。
# 缺行 = self_service=1 / server_locked=0 —— 与现状一致（用户仍可自助），故上线不锁死任何开关页。
# 读失败/表缺失一律 fail-open 到默认值：策略是旁路管控，不能把开关页与 App 写链路打挂。
# 注意：本段只承载「策略元数据」；非 bool 键仍由上方 set_runtime_flag 的类型防护拒绝热切。

FLAG_POLICY_DEFAULTS = {'self_service': True, 'server_locked': False}


def _policy_default(key: str) -> dict:
    '''缺行策略：自助开、未锁定、无标题/描述（契约 §1.3：可先只给 key，title/desc 缺省即可）。'''
    return {'key': key, 'self_service': True, 'server_locked': False,
            'title': None, 'desc': None, 'exists': False}


async def _policy_session(db):
    '''策略读写会话：传入 db 用调用方事务（不 commit）；否则自开会话。'''
    if db is not None:
        yield db
        return
    from app.db.database import async_session_factory
    async with async_session_factory() as own:
        yield own


async def get_flag_policies(keys, db=None) -> dict:
    '''批量读开关策略：{key: {self_service, server_locked, title, desc, exists}}；缺行按默认。'''
    out = {}
    for k in keys or []:
        out[k] = _policy_default(k)
    if not out:
        return out
    try:
        from sqlalchemy import select
        from app.models.config import FlagSetting
        async for session in _policy_session(db):
            rows = (await session.execute(
                select(FlagSetting).where(FlagSetting.key.in_(list(out.keys())))
            )).scalars().all()
            for r in rows:
                if r.key in out:
                    out[r.key] = {'key': r.key, 'self_service': bool(r.self_service),
                                  'server_locked': bool(r.server_locked), 'title': r.title,
                                  'desc': r.desc, 'exists': True}
    except Exception as e:
        _logger.warning('flag policy read failed: %s', e)
    return out


async def get_flag_policy(key: str, db=None) -> dict:
    '''单键策略（缺行/读失败 → 默认：自助开、未锁定）。'''
    return (await get_flag_policies([key], db=db)).get(key) or _policy_default(key)


async def set_flag_policy(key: str, *, self_service=None, server_locked=None, db=None) -> dict:
    '''写开关策略（缺行新建）；返回最新策略。key 是否合法由调用方校验（本函数只管策略行）。'''
    from sqlalchemy import select
    from app.models.config import FlagSetting
    async for session in _policy_session(db):
        row = (await session.execute(
            select(FlagSetting).where(FlagSetting.key == key)
        )).scalar_one_or_none()
        if row is None:
            row = FlagSetting(key=key)
            session.add(row)
            await session.flush()
        if self_service is not None:
            row.self_service = bool(self_service)
        if server_locked is not None:
            row.server_locked = bool(server_locked)
        await session.flush()
        await session.refresh(row)
        result = {'key': key, 'self_service': bool(row.self_service),
                  'server_locked': bool(row.server_locked), 'title': row.title,
                  'desc': row.desc, 'exists': True}
        if db is None:
            await session.commit()
        return result
    return _policy_default(key)


# ── 用户级开关覆盖（A5，2026-09-19）────────────────────────────────────────────
# 现象（Codex 只读侦察）：PUT /system/feature-flags/{key} 此前一律写全局 runtime_flags +
# 热更新进程级 AGENT_FLAGS —— 一台服务器上任何账号改一个开关，**所有账号一起变**。
# 方案：用户语义开关按账号落 user_runtime_flags 覆盖行，读侧走 resolve_flag 解析链：
#   server_locked 策略 → 直接返回全局值（锁定即忽略用户覆盖）
#   → 该账号有覆盖行 → 用户值
#   → 否则全局值（AGENT_FLAGS 现值）。
# 默认口径：没有覆盖行的账号，行为与改动前逐字节一致（回落全局 runtime_flags）。
#
# USER_SCOPED_FLAG_KEYS（共 10 个）：
# - 隐私细槽族 5 个（global_user_facts / user_fact_location / user_fact_relationship /
#   user_fact_health / user_current_location_share）按账号生效；
# - 社交/群聊/主动接触族 5 个（weave_3d / agent_social_light_context / agent_loop_group_chat /
#   agent_loop_social / proactive_outreach_v2）本批（batch G）已全部接 user_id 解析：
#   读取点取得 user 上下文，缺 user_id 时回落全局值（fail-open）；weave_3d 后端运行期零读取、
#   真消费者是客户端，仅加入集合供客户端按账号折叠。
# 注：flag_settings 策略判定（server_locked / self_service）与既有写路径完全一致，本段不放松权限。
# 10 个键（隐私细槽族 5 个 + 社交/群聊/主动接触族 5 个）均按账号生效；
# 后 5 个的读取点已全部接 user_id 解析（batch G：chat_groups.py:395/517、arbiter.py:866/1403/1656、
# message_generator.py:625），缺 user_id 时回落全局值（fail-open，与既有口径一致）。
USER_SCOPED_FLAG_KEYS: frozenset[str] = frozenset({
    'global_user_facts',
    'user_fact_location',
    'user_fact_relationship',
    'user_fact_health',
    'user_current_location_share',
    # ── 社交 / 群聊 / 主动接触族（batch G 接线）──
    'weave_3d',
    'agent_social_light_context',
    'agent_loop_group_chat',
    'agent_loop_social',
    'proactive_outreach_v2',
})


def _global_flag_value(key: str) -> bool:
    '''全局现值（AGENT_FLAGS）；键缺失/读取异常 → False（保守，与 get_all_flags 的 bool(v) 口径一致）。'''
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get(key, False))
    except Exception:
        return False


async def get_user_flags(user_id) -> dict:
    '''读该账号全部用户级覆盖行 {key: bool}；表缺失/异常返回 {}（fail-open，不打挂开关页）。'''
    if not user_id:
        return {}
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.config import UserRuntimeFlag
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(UserRuntimeFlag).where(UserRuntimeFlag.user_id == int(user_id))
            )).scalars().all()
        return {r.key: bool(r.enabled) for r in rows}
    except Exception as e:
        _logger.warning('User runtime flags read failed user=%s: %s', user_id, e)
        return {}


async def resolve_flags(keys, user_id=None) -> dict:
    '''批量解析若干键对某账号的生效值：``{key: bool}``；**语义与逐键 resolve_flag 完全一致**。

    细槽族一次要判定 6~8 个键，逐键解析＝每键 2 条小查询（策略 + 覆盖行）。本函数把
    两层查询各自合并成一条：策略走 :func:`get_flag_policies`（IN 一次），覆盖行走
    :func:`get_user_flags`（该账号全表一次），故 N 个键恒为 2 条查询。
    fail-open 口径不变：任一层读失败（含缺表）只让**该层**回落到默认（未锁定 / 无覆盖），
    最终值仍回全局现值，绝不抛错打挂业务链。
    '''
    ks = list(keys or [])
    out = {k: _global_flag_value(k) for k in ks}  # 全局值先行：任何失败都停在默认上
    if not ks or not user_id:
        return out
    try:
        policies = await get_flag_policies(ks)
    except Exception as e:  # 策略是旁路管控：读失败按「未锁定」处理（get_flag_policies 自身已兜）
        _logger.warning('resolve_flags policy read failed user=%s: %s', user_id, e)
        policies = {}
    try:
        overrides = await get_user_flags(user_id)
    except Exception as e:  # 覆盖行读失败回全局（get_user_flags 自身已兜，这里是双保险）
        _logger.warning('resolve_flags override read failed user=%s: %s', user_id, e)
        overrides = {}
    for k in ks:
        if (policies.get(k) or {}).get('server_locked'):
            continue  # 锁定即忽略用户覆盖（与单键口径一致）
        if k in overrides:
            out[k] = overrides[k]
    return out


async def resolve_flag(key: str, user_id=None) -> bool:
    '''解析某键对某账号的生效值（A5 用户级开关解析链）；**任何异常 fail-open 回全局值**。

    顺序：server_locked 策略 → 全局值（锁定即忽略用户覆盖）→ 该账号有覆盖 → 用户值 → 全局值。
    策略是旁路管控、用户覆盖是可选层：缺表/读失败一律回全局现值，绝不抛错打挂业务链
    （记忆/注入链路调用 resolve_flag，必须 fail-open）。

    单键接口保持不变（实现委托给 :func:`resolve_flags`，同为 2 条查询）；
    一次要判定多个键的调用方（如细槽族）请直接用批量版本，避免逐键查询放大。
    '''
    return (await resolve_flags([key], user_id)).get(key, _global_flag_value(key))


async def set_user_flag(key: str, user_id, enabled) -> bool:
    '''写某账号的用户级覆盖（upsert）；**不得**改进程级 AGENT_FLAGS。

    key 必须在 AGENT_FLAGS 且当前值为 bool（沿用 set_runtime_flag 的类型防护：非 bool 键
    （数字型等）返回 False 且不写库，避免 bool 覆盖把 int 键静默改成 1）。未知 key 返回 False。
    失败返回 False（调用方据此回 404/500）。
    '''
    from app.agent.loop import AGENT_FLAGS
    if key not in AGENT_FLAGS:
        return False
    if not isinstance(AGENT_FLAGS[key], bool):
        _logger.warning('set_user_flag rejected: key=%s is not bool (type=%s), skip',
                        key, type(AGENT_FLAGS[key]).__name__)
        return False
    if not user_id:
        return False
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.config import UserRuntimeFlag
        async with async_session_factory() as db:
            row = (await db.execute(
                select(UserRuntimeFlag).where(
                    UserRuntimeFlag.user_id == int(user_id),
                    UserRuntimeFlag.key == key,
                )
            )).scalar_one_or_none()
            if row is None:
                db.add(UserRuntimeFlag(user_id=int(user_id), key=key, enabled=bool(enabled)))
            else:
                row.enabled = bool(enabled)
            await db.commit()
        # 刻意不touch AGENT_FLAGS：用户级覆盖只影响该账号，不能串到其它账号。
        _logger.info('User runtime flag set: user=%s %s=%s', user_id, key, enabled)
        return True
    except Exception as e:
        _logger.warning('Set user runtime flag %s user=%s failed: %s', key, user_id, e)
        return False

