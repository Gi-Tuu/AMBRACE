"""运行时 Feature Flag 读写应用服务（A22 第三刀，2026-10-02）。

本模块自 ``app/application/system.py`` 逐字节搬入。边界＝用户/服务器级功能开关读写。

跨块调用（``_require_admin`` / ``_audit`` 仍驻留 system.py）一律在函数内
``from app.application import system as _sys`` 后走 ``_sys.<name>``——不放模块顶层，
避免与 system.py 的重导出形成导入环。
"""
from fastapi import HTTPException

from app.i18n import tr_lang
from app.utils.logger import get_logger

_logger = get_logger("application.system")


async def get_feature_flags(
    user_id: int,
    lang: str,
):
    '''读取全部运行时 Feature Flag（主账号 = is_admin）；source: db=DB 覆盖 / default=硬编码默认

    契约 §3：读类保持现状（_require_admin），避免非 server_admin 账号的 App 开关页读接口 403。

    A5 用户级开关覆盖（2026-09-19）：每个条目新增
    - ``scope``：``'user'``（该键按账号生效，本批 5 个隐私细槽族键）/ ``'server'``（服务器级）；
    - ``user_enabled``：**该账号**的覆盖值（无覆盖行 = ``null``）；
    既有 key/enabled/value/type/source 字段语义保持不变。在服务层补充而非改 flag_service.get_all_flags
    签名，保持既有 monkeypatch（无参 fake）与调用方兼容。

    A4 目录元数据（2026-09-20）：每个条目**追加** ``meta``
    ``{title, desc, group, group_order, order, visible}``（按请求 lang 选 zh/en，缺省 zh；
    visible=True = App 常用开关直显）。来源 = ``app/application/flag_catalog.py`` 的纯内存字典，
    **不打库**；既有字段（key/enabled/value/type/source/scope/user_enabled）语义与顺序均不变。

    C1a（2026-09-25）：再**追加** ``locked`` 与 ``self_service`` 两个布尔（缺省语义同
    ``get_flag_policy``：未锁定 / 可自助改），来源 = ``flag_settings`` 策略，**一次批量取**
    （:func:`flag_service.get_flag_policies`，禁止逐键打库 N 次）。App 开关页据此把该条置灰只读。
    '''
    from app.application import system as _sys
    await _sys._require_admin(user_id, lang)
    from app.application import flag_catalog
    from app.application import flag_service
    flags = await flag_service.get_all_flags()
    user_flags = await flag_service.get_user_flags(user_id)
    policies = await flag_service.get_flag_policies([f.get('key') for f in flags])
    for f in flags:
        if f.get('key') in flag_service.USER_SCOPED_FLAG_KEYS:
            f['scope'] = 'user'
            f['user_enabled'] = user_flags.get(f['key'])  # 无覆盖 = None（不是 False）
        else:
            f['scope'] = 'server'
            f['user_enabled'] = None
        f['meta'] = flag_catalog.meta_for(f.get('key'), lang)
        policy = policies.get(f.get('key')) or {}
        f['locked'] = bool(policy.get('server_locked', False))
        f['self_service'] = bool(policy.get('self_service', True))
    return {'status': 'ok', 'flags': flags}


async def update_feature_flag(
    key: str,
    data: dict,
    user_id: int,
    lang: str,
):
    '''切换 Feature Flag（本账号可用；服务器控制台可逐键锁定）：写 DB + 热更新内存立即生效；未知 key 返回 404

    账号独立 P2（契约 §1.3 **Codex 09-19 修订**）：本端点即「用户侧写开关」路径——
    - 写权限**保持既有 `_require_admin`**：本产品里每个独立账号都用自己的 App 开关页，若一并
      收紧为 require_server_admin，会让所有非服务器管理员的账号一写开关就 403（功能回归）；
    - 服务器控制权改由**逐键策略**承担：``flag_settings.server_locked=1`` → 403（锁定＝仅控制台
      PUT /api/v1/admin/server/flags/{key} 可改）；``self_service=0`` → 同样不可自助改（403）。
      这正是用户要的「能让某些开关关闭、不放开开关权限」。
    - 真正「服务器级」的写入口（四模态服务器配置 / 任务 API 配置 / 额度 / 备份触发与下载）仍收紧为
      ``require_server_admin``（见契约 §3 与 `_require_server_admin`）。

    A5 用户级开关覆盖（2026-09-19）：写路径按 key 分流——
    - key ∈ ``USER_SCOPED_FLAG_KEYS`` → 写**该账号的覆盖行**（不写全局、不动进程级 AGENT_FLAGS），
      返回体沿用现状结构并标 ``scope='user'``；
    - key ∉ USER_SCOPED → **保持现状写全局**（权限判定不变），返回 ``scope='server'``。
    两条路径都**先过既有策略判定**（server_locked / self_service → 403），非 bool 键的类型防护
    由 set_runtime_flag / set_user_flag 内部承担，均不得绕过。
    '''
    from app.application import system as _sys
    await _sys._require_admin(user_id, lang)
    if 'enabled' not in data:
        raise HTTPException(status_code=400, detail='enabled required')
    from app.application import flag_service
    from app.agent.loop import AGENT_FLAGS
    # 先取策略与旧值（各自只读；策略读失败 fail-open 到「自助开、未锁定」）
    policy = await flag_service.get_flag_policy(key)
    if policy['server_locked']:
        raise HTTPException(status_code=403, detail=tr_lang(lang, 'flag_server_locked'))
    if not policy['self_service']:
        raise HTTPException(status_code=403, detail=tr_lang(lang, 'flag_self_service_disabled'))
    enabled = bool(data.get('enabled'))
    if key in flag_service.USER_SCOPED_FLAG_KEYS:
        # 用户语义键：写该账号覆盖行；全局值与其它账号均不受影响。
        _before = {'enabled': bool(await flag_service.resolve_flag(key, user_id))}
        ok = await flag_service.set_user_flag(key, user_id, enabled)
        if not ok:
            raise HTTPException(status_code=404, detail='unknown feature flag: ' + key)
        await _sys._audit(None, user_id, 'server.feature_flag.update', 'flag:' + key,
                          _before, {'enabled': enabled, 'scope': 'user'})
        return {'status': 'ok', 'key': key, 'enabled': enabled, 'scope': 'user'}
    _before = {'enabled': bool(AGENT_FLAGS.get(key)) if key in AGENT_FLAGS else None}
    ok = await flag_service.set_runtime_flag(key, enabled)
    if not ok:
        raise HTTPException(status_code=404, detail='unknown feature flag: ' + key)
    await _sys._audit(None, user_id, 'server.feature_flag.update', 'flag:' + key,
                      _before, {'enabled': enabled})
    return {'status': 'ok', 'key': key, 'enabled': enabled, 'scope': 'server'}
