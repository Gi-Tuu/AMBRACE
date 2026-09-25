"""X7-M4a 行动裁决：意图契约 + 闸门链（内置 / 插件两条身份）+ 内存待执行队列（**零真实执行**）。

M4 是 X7 唯一「写」的一批，本批只做**裁决侧**：意图进来 → 闸门裁决 → 审计 → 入待执行队列
→ App 取走/回报。**执行体不存在**（批准即 ``status="not_implemented"``），留 M4b；插件灰度闸
已于 M4c-1 落地；两份**名单**（目标白名单 / 插件灰度名单）自 M4c-3 起**以库为权威**
（``device_action_targets`` / ``device_action_plugins``，重启不丢）；限流/熔断计数属运行时统计，
仍留内存。

闸门（决策①③，2026-09-23 用户拍板：**默认全禁、逐条授权**；任一不过即拒，顺序固定）：
① 全局 kill switch —— ``AGENT_FLAGS`` 的 ``device_actions_enabled``（C1a 起登记进常规开关体系，
   App 开关页可见但默认服务器锁定）；**取不到该键即视为关**；
② 账号级 —— ``user_runtime_flags`` 同名键（按 user_id），**缺行即关**；
③ 插件侧三条闸（M4c-1 灰度；**内置调用方整层不适用**，见下面「身份来源」段）：
   ③a 灰度闸 —— 总闸 ``AGENT_FLAGS`` 的 ``device_actions_plugin_enabled``（**取不到即关**，
       未开即拒 ``plugin_actions_disabled``）**且**该插件已被灰度放开（:data:`PLUGIN_ACTION_ENABLED_PLUGINS`
       ∪ :func:`allow_plugin_actions` 写进 ``device_action_plugins`` 的放开集，默认空集＝一条都不放，
       未命中拒 ``plugin_not_graylisted``；读库失败只认编译期常量，不额外放行）；
   ③b 能力授权 —— ``registry.has_capability_permission(plugin, tenant, capability)``：未安装/停用/
       拿不到租户/未同意 ``device:<id>:write`` 一律拒 ``plugin_unauthorized``
       （复用 M3 判定，读写权限分离）；
④ 目标白名单 —— 按租户读 ``device_action_targets``，**无行＝空集＝全拒**（读库异常同样空集）；
   黑名单（:data:`TARGET_BLACKLIST`，编译期常量）优先于白名单。

闸门之后另有 ``device_actions_force_dry_run``（**取不到即开＝强制干跑**，M4c-1）：为真时插件提交的
意图即使全部闸门通过也**不发 token、不入队**，只回 ``status="dry_run"``——灰度期「只看裁决、不动手机」。

**内置调用方不受 ③a / ③b / 强制干跑这三条插件闸影响**（仍走 ①②④ + 熔断 + 限流 + 审计一条不少），
但身份只能来自**服务端可判定的来源**（M4c-1 收口，堵掉「请求体自称内置即可绕过闸门③」）：
``POST /api/v1/device/actions`` 恒为 :data:`BUILTIN_CALLER`，请求体带非空 ``plugin`` 直接拒
``invalid_intent:plugin_not_allowed``；插件身份只能来自 ``POST /api/v1/plugins/{name}/bridge``
的**路径名** ``{name}``（该端点既有的「插件存在 / 停用 / 对 caller 可见」三道校验即其授权前提）。
调用方在请求体或桥 ``params`` 里自称一律不采信。

闸门之后另加两道护栏（防止裁决被刷）：按 ``(tenant, plugin)`` 令牌桶限流
:data:`RATE_LIMIT_PER_MINUTE` 次/分钟；同一 ``(tenant, plugin)`` 连续
:data:`CIRCUIT_BREAK_THRESHOLD` 次失败回报即熔断后续请求。限流与熔断都是**内存计数**
（进程重启即清零）——它们属运行时统计，M4c-3 刻意不落库：重启后带着旧的失败计数继续熔断
反而更难排查。

审计（本批重点，补 M3 缺 user_id 的缺口）：``decide_action`` 与 ``report_result`` 各落一条
INFO，字段顺序固定 ``ts tenant_id user_id plugin capability action target_app by result
elapsed_ms dry_run reason``（值里的空格/换行折成下划线，保持单行可 grep）。

内置通道的意图由 ``POST /api/v1/device/actions`` 提交（身份恒为 :data:`BUILTIN_CALLER`，不由请求体声明）；
插件的意图由桥 ``device_action`` api 提交（身份取路径名）。审计里的 ``plugin=builtin`` 即一眼可辨
「这条动作是 App 内置通道发起的」，与插件名同列、无需额外字段。

会话工厂刻意按属性访问（``database.async_session_factory``）而非 ``from … import`` 绑死名字，
与 :mod:`app.device.port` 同口径：测试只 patch ``app.db.database`` 上的名字即可覆盖本模块全部
读点（闸门 ② 的账号级开关、闸门④ 与 ③a 的两份名单、以及它们的写入路径同源；闸门 ①/③a 总闸与
强制干跑自 C1a 起读 ``AGENT_FLAGS`` 现值，不再打库）。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, ValidationError

from app.db import database
from app.device.capabilities import CAPABILITIES, get_capability
from app.utils.logger import get_logger
from app.utils.timeutil import now_naive_utc

_logger = get_logger("device.actions")

# ── 闸门 ①② 的键名（全局与账号级同名；账号级仍直接读表，全局见下）──
KILL_SWITCH_KEY = "device_actions_enabled"

# ── 插件灰度闸（M4c-1 ③a + 强制干跑）的键名 ──
# C1a（2026-09-25）起三条键都登记进 ``AGENT_FLAGS``（app/agent/loop.py），闸门读的是它的现值
# （= 硬编码默认 + 启动时 DB 覆盖 + 控制台热改），不再各自打库；三者的缺省方向不同，都是刻意
# 更严的一侧：
#   KILL_SWITCH_KEY / PLUGIN_KILL_SWITCH_KEY 取不到＝关（通道不可用）；
#   FORCE_DRY_RUN_KEY                        取不到＝开（裁决通过也不下发可执行 token）。
PLUGIN_KILL_SWITCH_KEY = "device_actions_plugin_enabled"
FORCE_DRY_RUN_KEY = "device_actions_force_dry_run"

# ── 控制台（M4e-1）改这三条开关时用的**逻辑名** → 真实 ``runtime_flags`` 键（与 AGENT_FLAGS 同名）──
# 控制台契约不暴露物理键名，只认这三个逻辑名；三者的缺省方向各不相同（见 :func:`action_flag_states`）。
ACTION_FLAG_KEYS = {
    "global": KILL_SWITCH_KEY,
    "plugin_enabled": PLUGIN_KILL_SWITCH_KEY,
    "force_dry_run": FORCE_DRY_RUN_KEY,
}

# 逐插件灰度白名单（编译期默认值）：**空集＝没有任何插件被灰度到**。运行期放开走
# :func:`allow_plugin_actions`（M4c-3 起写进 ``device_action_plugins``，重启不丢）；判定取
# 「本常量 ∪ 库里放开集」，两者都不含即拒 ``plugin_not_graylisted``；读库失败只剩本常量（更严的一侧）。
PLUGIN_ACTION_ENABLED_PLUGINS: frozenset[str] = frozenset()

# ── 内置调用方保留名（M4b-1）：只由服务端可判定的来源给出，不接受调用方自称 ──
# POST /api/v1/device/actions 恒用它（请求体带 plugin 即拒）；插件身份取桥端点的路径名 {name}。
# 只在闸门③ 上区别于插件（内置没有安装记录，不该被「插件是否同意过权限」挡住）；其余闸门照旧。
BUILTIN_CALLER = "builtin"

RATE_LIMIT_PER_MINUTE = 10       # 令牌桶容量与补充速率（次/分钟）
CIRCUIT_BREAK_THRESHOLD = 5      # 连续失败回报次数 → 熔断
PENDING_TTL_SECONDS = 30         # 待执行项存活时长（App 未取走即作废）
VALID_TAP_BY = ("text", "id")    # action_tap 的定位方式（只允许声明式定位，禁坐标盲点）

# 目标黑名单：M4a 起放模块级常量（黑名单优先于白名单）。M4c-3 只把两份**白名单**落库，
# 黑名单仍是编译期常量（它的使用方式是「出问题就加一条并重启」，不是运维在管理端逐条配）。
TARGET_BLACKLIST: frozenset[str] = frozenset()

# ── 机器可读拒绝原因（reason 字段取值，插件侧按字面量判定）──
REASON_INVALID_INTENT = "invalid_intent"
REASON_GLOBAL_OFF = "global_kill_switch_off"
REASON_ACCOUNT_OFF = "account_flag_off"
# ③a 灰度闸：插件总闸未开 / 该插件不在灰度白名单（M4c-1）
REASON_PLUGIN_ACTIONS_DISABLED = "plugin_actions_disabled"
REASON_PLUGIN_NOT_GRAYLISTED = "plugin_not_graylisted"
# ③b 能力授权（M3 判定，M4c-1 起编号为 ③b）
REASON_PLUGIN_UNAUTHORIZED = "plugin_unauthorized"
# 身份不接受自称：请求体 / 桥 params 里带了 plugin 字段（M4c-1，与 invalid_intent 同族前缀）
REASON_PLUGIN_NOT_ALLOWED = "plugin_not_allowed"
REASON_TARGET_BLACKLISTED = "target_blacklisted"
REASON_TARGET_NOT_ALLOWED = "target_not_allowed"
REASON_CIRCUIT_OPEN = "circuit_open"
REASON_RATE_LIMITED = "rate_limited"

# ── 批准后的执行状态（本批执行体不存在，恒 not_implemented / dry_run）──
STATUS_DENIED = "denied"
STATUS_NOT_IMPLEMENTED = "not_implemented"
STATUS_DRY_RUN = "dry_run"


class ActionIntent(BaseModel):
    """一次行动意图：只声明「对哪个应用做什么」，不含任何执行能力，也不回传节点树/屏幕文本。"""

    model_config = ConfigDict(extra="forbid")

    capability: str                  # 行动类能力 id（action_open_app / action_tap / action_set_text）
    target_app: str = ""             # 包名或应用标签（恒必填）
    by: str | None = None            # 仅 action_tap：text / id
    query: str | None = None         # 仅 action_tap：要定位的文本或控件 id
    text: str | None = None          # 仅 action_set_text：要输入的文本
    dry_run: bool = False            # 干跑：只裁决，不入队、不产生任何动作


@dataclass
class Decision:
    """一次裁决的结果。``allowed`` 是唯一放行判据，``reason`` 恒为机器可读字面量。"""

    allowed: bool
    reason: str = ""
    action_token: str | None = None
    dry_run: bool = False
    status: str = STATUS_DENIED


@dataclass
class _PendingAction:
    """待执行队列里的一项（内存，TTL :data:`PENDING_TTL_SECONDS` 秒）。"""

    token: str
    user_id: int | None
    tenant_id: int | None
    plugin: str
    intent: ActionIntent
    enqueued_at: float               # time.monotonic() 起点，用于 TTL 与 elapsed_ms

    @property
    def action(self) -> str:
        return self.intent.capability.removeprefix("action_")

    def as_dict(self) -> dict:
        """App 侧取走的载荷（只给意图字段，不给内核内部状态）。"""
        return {
            "action_token": self.token,
            "capability": self.intent.capability,
            "action": self.action,
            "plugin": self.plugin,
            "target_app": self.intent.target_app,
            "by": self.intent.by,
            "query": self.intent.query,
            "text": self.intent.text,
            "dry_run": self.intent.dry_run,
            "status": STATUS_NOT_IMPLEMENTED,   # 本批无执行体：取到也不许执行
        }


# ── 名单落库（X7-M4c-3）──
# 进程内只留「运行时统计」（待执行队列 / 限流桶 / 熔断计数）——这三样重启即清零是**预期语义**。
# 两份**名单**（目标白名单、插件灰度名单）此前也在内存里，M4c-3 起以库为权威：
# ``device_action_targets`` / ``device_action_plugins``（见 app.models.device）。
_PENDING: dict[str, _PendingAction] = {}
# (tenant_id, plugin) → (剩余令牌, 上次补充时刻)
_BUCKETS: dict[tuple[int | None, str], tuple[float, float]] = {}
# (tenant_id, plugin) → 连续失败回报次数
_FAILURES: dict[tuple[int | None, str], int] = {}


async def _stored_targets(tenant_id: int) -> frozenset[str]:
    """库里某租户的目标白名单行；**读失败一律空集**（fail-closed＝全拒，不猜测）。"""
    from sqlalchemy import select

    from app.models.device import DeviceActionTarget

    try:
        async with database.async_session_factory() as db:
            rows = (await db.execute(
                select(DeviceActionTarget.target)
                .where(DeviceActionTarget.tenant_id == tenant_id)
            )).scalars().all()
    except Exception as e:  # 缺表 / 库坏 → 当成空名单，绝不因读名单失败而放行行动
        _logger.warning("目标白名单读库失败 tenant=%s err=%s", tenant_id, e)
        return frozenset()
    return frozenset(str(r).strip() for r in rows)


async def configured_targets(tenant_id: int | None) -> frozenset[str]:
    """某租户当前的目标白名单（闸门④；**拿不到租户＝空集**，只读视图）。"""
    if tenant_id is None:
        return frozenset()
    try:
        tid = int(tenant_id)
    except (TypeError, ValueError):
        return frozenset()
    return await _stored_targets(tid)


async def allow_target(tenant_id: int, target: str) -> bool:
    """给某租户放开一个行动目标（闸门④：默认无行＝全拒，逐条显式放开）。**幂等写库**。

    对外的配置入口是 ``POST /api/v1/device/actions/targets``（服务器控制台管理员），单租户容量
    上限由那一侧把；本函数不校验包名形态——闸门只认「精确匹配白名单里的字符串」，写库前只
    做 ``strip``（与 M4b-1 口径一致）。同一 ``(tenant_id, target)`` 重复添加不报错、不产生第二行。

    返回 ``False`` ＝「这一条没落库」（租户不是整数 / 读写库异常）。调用方必须据此如实回错：
    假装成功会让运维以为目标已放开，而闸门仍在全拒。
    """
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.models.device import DeviceActionTarget

    target = str(target).strip()
    try:
        tid = int(tenant_id)
    except (TypeError, ValueError):
        return False
    try:
        async with database.async_session_factory() as db:
            hit = (await db.execute(
                select(DeviceActionTarget.id)
                .where(DeviceActionTarget.tenant_id == tid,
                       DeviceActionTarget.target == target)
            )).scalar_one_or_none()
            if hit is not None:
                return True                       # 已在库里：幂等成功
            db.add(DeviceActionTarget(tenant_id=tid, target=target))
            try:
                await db.commit()
            except IntegrityError:                # 并发下另一路已写同一行：唯一约束即幂等
                await db.rollback()
    except Exception as e:
        _logger.warning("目标白名单写库失败 tenant=%s target=%s err=%s", tid, target, e)
        return False
    return True


async def remove_target(tenant_id: int | None, target: str) -> int:
    """从某租户目标白名单删除一条（闸门④ 的收回入口）。**幂等**：不存在即返回 0。

    返回实际删掉的行数（同一 ``(tenant_id, target)`` 唯一约束下最多 1）。``tenant_id`` 解析不到
    （``None`` / 非整数）时**不查库直接返回 0**；写库异常同样收敛为 0（绝不谎称已删）。删除与
    添加一样只认 ``strip`` 后的精确串（与 :func:`allow_target` 的落库口径一致）。
    """
    from sqlalchemy import delete

    from app.models.device import DeviceActionTarget

    if tenant_id is None:
        return 0
    try:
        tid = int(tenant_id)
    except (TypeError, ValueError):
        return 0
    target = str(target or "").strip()
    try:
        async with database.async_session_factory() as db:
            res = await db.execute(
                delete(DeviceActionTarget).where(DeviceActionTarget.tenant_id == tid,
                                                 DeviceActionTarget.target == target)
            )
            await db.commit()
    except Exception as e:  # 删不动如实回 0，不假装成功
        _logger.warning("目标白名单删除失败 tenant=%s target=%s err=%s", tid, target, e)
        return 0
    return int(res.rowcount or 0)


async def _stored_plugins() -> frozenset[str]:
    """库里已灰度放开的插件名；**读失败回退为空集**（只剩编译期常量，不额外放行任何一个插件）。"""
    from sqlalchemy import select

    from app.models.device import DeviceActionPlugin

    try:
        async with database.async_session_factory() as db:
            rows = (await db.execute(
                select(DeviceActionPlugin.plugin_name)
            )).scalars().all()
    except Exception as e:
        _logger.warning("插件灰度名单读库失败，本轮回退为只认编译期常量: %s", e)
        return frozenset()
    return frozenset(str(r).strip() for r in rows if str(r).strip())


async def configured_plugins() -> frozenset[str]:
    """当前被放开行动能力的插件全集（库里 ∪ :data:`PLUGIN_ACTION_ENABLED_PLUGINS`）。"""
    return frozenset(PLUGIN_ACTION_ENABLED_PLUGINS) | await _stored_plugins()


async def allow_plugin_actions(name: str) -> bool:
    """把一个插件放进灰度白名单（闸门 ③a 的放开入口）。**幂等写库**、重启不丢。

    总闸 ``device_actions_plugin_enabled`` 仍必须先开——本函数只决定「哪个插件在灰度范围内」，
    不构成第二条放行路径。返回 ``False`` ＝没有落库（名字为空 / 读写库异常）。
    """
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.models.device import DeviceActionPlugin

    name = str(name or "").strip()
    if not name:
        return False          # 空白名进不了名单（与 M4c-1 口径一致）
    try:
        async with database.async_session_factory() as db:
            hit = (await db.execute(
                select(DeviceActionPlugin.id).where(DeviceActionPlugin.plugin_name == name)
            )).scalar_one_or_none()
            if hit is not None:
                return True
            db.add(DeviceActionPlugin(plugin_name=name))
            try:
                await db.commit()
            except IntegrityError:                # 并发重复：唯一约束已挡住，视为幂等成功
                await db.rollback()
    except Exception as e:
        _logger.warning("插件灰度名单写库失败 plugin=%s err=%s", name, e)
        return False
    return True


async def revoke_plugin_actions(name: str) -> int:
    """把一个插件从灰度名单收回（闸门 ③a 的逐插件收回入口）。**幂等**：不存在即返回 0。

    返回实际删掉的行数。收回只影响「哪些插件在灰度范围内」——总闸 ``device_actions_plugin_enabled``
    关着本就一条都不放，本函数不构成第二条通道。空白名不查库直接返回 0；写库异常同样收敛为 0。
    """
    from sqlalchemy import delete

    from app.models.device import DeviceActionPlugin

    name = str(name or "").strip()
    if not name:
        return 0
    try:
        async with database.async_session_factory() as db:
            res = await db.execute(
                delete(DeviceActionPlugin).where(DeviceActionPlugin.plugin_name == name)
            )
            await db.commit()
    except Exception as e:  # 删不动如实回 0，不假装成功
        _logger.warning("插件灰度名单删除失败 plugin=%s err=%s", name, e)
        return 0
    return int(res.rowcount or 0)


async def plugin_graylisted(name: str) -> bool:
    """该插件是否在灰度范围内（:data:`PLUGIN_ACTION_ENABLED_PLUGINS` ∪ 库里的放开集）。"""
    name = str(name or "").strip()
    if not name:
        return False
    if name in PLUGIN_ACTION_ENABLED_PLUGINS:
        return True
    return name in await _stored_plugins()


# ── 行动确认策略落库（C1b，2026-09-25 派单：X7 遗留②）──
# 三档字面量与 Flutter 侧（``device_action_prefs.dart``）逐字对齐，缺省档＝中档。
# 本段只负责「按账号存/取一行档位」，**不参与闸门裁决**：档位是 App 端的确认交互策略，
# 服务端持久化只是让换机/重装后不必回到缺省档，不构成任何行为放开。
ACTION_POLICY_TIERS: tuple[str, ...] = ("once_ever", "first_per_type", "every_time")
ACTION_POLICY_DEFAULT = "first_per_type"


def action_capabilities() -> tuple[str, ...]:
    """``kind="act"`` 的能力 id —— 策略端点校验 capability 的唯一权威源（清单只在 capabilities）。"""
    return tuple(spec.id for spec in CAPABILITIES.values() if spec.kind == "act")


async def configured_policies(user_id: int | None) -> dict[str, str]:
    """某账号**已配置过**的档位（``capability → policy``）。

    无行＝没配过 → 返回空 dict（App 据此回落缺省档）；``user_id`` 拿不到同样回空 dict。
    读库异常**也回空 dict**（绝不伪造档位）——App 端把「空」当成「服务端没配过」从而保留本机
    现值，不会因为后端抖动就被清成缺省档。
    """
    from sqlalchemy import select

    from app.models.device import DeviceActionPolicy

    if user_id is None:
        return {}
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return {}
    try:
        async with database.async_session_factory() as db:
            rows = (await db.execute(
                select(DeviceActionPolicy.capability, DeviceActionPolicy.policy)
                .where(DeviceActionPolicy.user_id == uid)
            )).all()
    except Exception as e:  # 缺表 / 库坏 → 当成「没配过」，不猜任何档位
        _logger.warning("行动策略读库失败 user=%s err=%s", uid, e)
        return {}
    return {str(cap): str(pol) for cap, pol in rows}


async def store_policy(user_id: int | None, capability: str, policy: str) -> bool:
    """幂等 upsert 一行档位（同一 ``(user_id, capability)`` 永远只有一行）。

    档位与能力名的合法性由**端点**校验（非法一律 400，不许静默落库）；本函数只负责写。
    返回 ``False`` ＝「这一条没落库」（账号拿不到 / 写库异常），调用方必须据此如实回错——
    假装成功会让 App 以为换档已跨端生效，而下次开机同步又回到旧档。
    """
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.models.device import DeviceActionPolicy

    if user_id is None:
        return False                      # 拿不到账号＝不写库（不做全表匹配，与 remove_target 同口径）
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return False

    async def _row(db):
        return (await db.execute(
            select(DeviceActionPolicy).where(DeviceActionPolicy.user_id == uid,
                                             DeviceActionPolicy.capability == capability)
        )).scalar_one_or_none()

    try:
        async with database.async_session_factory() as db:
            hit = await _row(db)
            if hit is None:
                db.add(DeviceActionPolicy(user_id=uid, capability=capability, policy=policy))
            else:
                hit.policy = policy
            try:
                await db.commit()
            except IntegrityError:
                # 并发下另一路刚插入同一 (user, capability)：唯一约束已挡住第二行，改写它那一行
                await db.rollback()
                hit = await _row(db)
                if hit is None:
                    return False
                hit.policy = policy
                await db.commit()
    except Exception as e:
        _logger.warning("行动策略写库失败 user=%s capability=%s err=%s", uid, capability, e)
        return False
    return True


def reset_runtime_state() -> None:
    """清空进程内**运行时统计**（限流桶 / 熔断计数 / 待执行队列）。

    存在的唯一理由：这些计数跨用例、跨进程复用会让判定漂移（用例间互相污染），
    测试与运维需要一条显式的清零入口。

    **不动两份名单**（M4c-3 起）：目标白名单与插件灰度名单在库里（``device_action_targets`` /
    ``device_action_plugins``），清零它们等于把运维配的白名单删了——跨用例隔离改由各用例自己的
    私有临时库保证（见 tests/test_device_actions.py 的 ``act_db`` fixture）。
    """
    _PENDING.clear()
    _BUCKETS.clear()
    _FAILURES.clear()


def validate_intent(intent: ActionIntent) -> str | None:
    """按能力契约逐字段对账；返回机器可读原因（``None`` ＝ 通过）。

    字段清单只有 :class:`~app.device.capabilities.CapabilitySpec` 的 ``schema`` 一份权威源：
    schema 里除 ``dry_run`` 外的键都是该能力的必填字段（``target_app`` 三条皆有，``tap``
    另有 ``by``+``query``，``set_text`` 另有 ``text``），schema 外的字段即「本能力不认」——
    故 ``open_app`` 只认 ``target_app``。只读能力不得作为行动提交（读写权限分离）。
    """
    spec = get_capability(intent.capability)
    if spec is None:
        return f"unknown_capability:{intent.capability}"
    if spec.kind != "act":
        return f"not_an_action:{intent.capability}"
    fields = set(spec.schema) - {"dry_run"}
    for name in sorted(fields):
        if not str(getattr(intent, name, "") or "").strip():
            return f"missing_field:{name}"
    for name in ("by", "query", "text"):
        if name not in fields and getattr(intent, name) is not None:
            return f"unexpected_field:{name}"
    if intent.by is not None and intent.by not in VALID_TAP_BY:
        return f"bad_value:by:{intent.by}"
    return None


def invalid_intent_reason(exc: ValidationError) -> str:
    """把请求体 / 桥 ``params`` 的**形状**错误折成机器可读原因（两条提交通道共用一份口径）。

    仍然 200 + ``reason``，不抛 422：调用方（插件 JS / App）按字面量判定被哪一步挡住。
    """
    errors = exc.errors()
    first = errors[0] if errors else {}
    loc = first.get("loc") or ("body",)
    field = str(loc[-1] if loc else "body")
    msg = str(first.get("msg") or "invalid")
    if msg.startswith("Field required"):
        return f"{REASON_INVALID_INTENT}:field_required:{field}"
    if msg.startswith("Extra inputs"):
        return f"{REASON_INVALID_INTENT}:unknown_field:{field}"
    return f"{REASON_INVALID_INTENT}:bad_{field}"


async def _flag_enabled(model, user_id: int | None = None, *, key: str = KILL_SWITCH_KEY,
                        missing_means: bool = False) -> bool:
    """直接读一行开关；**无行 / 读失败一律按 ``missing_means`` 兜底**（fail-closed 到更严的一侧）。

    ``model`` 取 ``UserRuntimeFlag``（账号级，此时必须给 ``user_id``，拿不到账号即关，不查库）；
    全局三条闸自 C1a 起改读 ``AGENT_FLAGS``（见 :func:`_agent_flag`），不再走本函数。
    ``missing_means`` 是「读不到这一行」时的取值：账号级闸传 ``False``（读不到＝关）。
    """
    from sqlalchemy import select

    if user_id is None and hasattr(model, "user_id"):
        return False  # 账号级开关：拿不到账号即视为关，不做全表查询
    try:
        async with database.async_session_factory() as db:
            stmt = select(model).where(model.key == key)
            if user_id is not None:
                stmt = stmt.where(model.user_id == int(user_id))
            row = (await db.execute(stmt)).scalar_one_or_none()
    except Exception as e:  # 表缺失 / 库坏 → 按缺省方向处理，绝不因读开关失败而放行行动
        _logger.warning("行动开关读取失败 key=%s err=%s", key, e)
        return missing_means
    if row is None:
        return missing_means
    return bool(row.enabled)


def _agent_flag(key: str, *, missing_means: bool) -> bool:
    """读 ``AGENT_FLAGS`` 里的行动开关现值（C1a：三条键已登记进常规开关体系）。

    现值来源：硬编码默认 → 启动时 ``load_runtime_flags`` 覆盖 → 控制台热改（:func:`set_action_flag`
    与 ``flag_service.set_runtime_flag`` 都同步内存）。**取不到该键或读的过程中出任何异常**一律按
    ``missing_means`` 兜底（关 / 关 / 开＝各自更严的一侧），绝不因读开关失败而放行行动。
    """
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS[key])
    except Exception as e:  # 键缺失 / loop 导入失败 → 缺省方向
        _logger.warning("行动开关读取失败 key=%s err=%s", key, e)
        return missing_means


async def _global_actions_enabled() -> bool:
    return _agent_flag(KILL_SWITCH_KEY, missing_means=False)


async def _account_actions_enabled(user_id: int | None) -> bool:
    from app.models.config import UserRuntimeFlag

    return await _flag_enabled(UserRuntimeFlag, user_id)


async def _plugin_actions_enabled() -> bool:
    """闸门 ③a 的插件总闸：**取不到该键即视为关**（与全局开关同口径）。"""
    return _agent_flag(PLUGIN_KILL_SWITCH_KEY, missing_means=False)


async def _force_dry_run() -> bool:
    """插件提交的强制干跑闸：**取不到该键即视为开**（缺省更严，灰度期默认不动手机）。"""
    return _agent_flag(FORCE_DRY_RUN_KEY, missing_means=True)


async def set_action_flag(key: str, enabled: bool) -> dict:
    """控制台改一条行动开关（M4e-1）：把三个**逻辑名**之一 upsert 进 ``runtime_flags``。

    只认 :data:`ACTION_FLAG_KEYS` 的三个键（``global`` / ``plugin_enabled`` / ``force_dry_run``），
    映射到各自的物理键后**用 :class:`RuntimeFlag` 直写**；写成功后再把同一份值同步进
    ``AGENT_FLAGS`` 内存（C1a：闸门读点与 App 开关页都看它），下一次裁决即生效。
    **库里那一行才是权威**——进程重启后由 ``load_runtime_flags`` 从表里恢复。非法逻辑名抛
    :class:`ValueError`（由端点翻译成 400）。

    写库失败**不抛异常**：返回 ``{"ok": False, "key": key, "error": "store_unavailable"}``，
    调用方据此如实回错——绝不静默成功。内存同步失败只记 WARNING（库已写成功，不得回报成失败）。
    成功返回 ``{"ok": True, "key": key, "enabled": bool}``。
    """
    from sqlalchemy import select

    from app.models.config import RuntimeFlag

    if key not in ACTION_FLAG_KEYS:
        raise ValueError(f"unknown_action_flag:{key}")
    real_key = ACTION_FLAG_KEYS[key]
    value = bool(enabled)
    try:
        async with database.async_session_factory() as db:
            row = (await db.execute(
                select(RuntimeFlag).where(RuntimeFlag.key == real_key)
            )).scalar_one_or_none()
            if row is None:
                db.add(RuntimeFlag(key=real_key, enabled=value))
            else:
                row.enabled = value
            await db.commit()
    except Exception as e:  # 写不动如实回错，绝不假装已生效
        _logger.warning("行动开关写入失败 key=%s err=%s", real_key, e)
        return {"ok": False, "key": key, "error": "store_unavailable"}
    try:
        from app.agent.loop import AGENT_FLAGS
        AGENT_FLAGS[real_key] = value
    except Exception as e:  # 落库已成功：内存同步失败只留痕，不改写结果
        _logger.warning("行动开关内存同步失败 key=%s err=%s", real_key, e)
    return {"ok": True, "key": key, "enabled": value}


async def action_flag_states() -> dict:
    """三开关的「生效值 + 库里是否真有这一行」（控制台只读视图，缺省方向与各闸门一致）。

    生效值按各闸门自己的缺省方向：``global`` / ``plugin_enabled`` 缺行＝False（读不到即关），
    ``force_dry_run`` 缺行＝**True**（读不到即强制干跑，宁可不动手机）。``rows_present`` 表示
    库里是否存在这一行（区分「显式关」与「压根没配」）。读库异常按缺省方向返回、``rows_present``
    全 False——绝不因读失败而显出更松的状态。
    """
    from sqlalchemy import select

    from app.models.config import RuntimeFlag

    defaults = {"global": False, "plugin_enabled": False, "force_dry_run": True}
    out = {
        "global": defaults["global"],
        "plugin_enabled": defaults["plugin_enabled"],
        "force_dry_run": defaults["force_dry_run"],
        "rows_present": {name: False for name in defaults},
    }
    real_to_logical = {real: logical for logical, real in ACTION_FLAG_KEYS.items()}
    try:
        async with database.async_session_factory() as db:
            rows = (await db.execute(
                select(RuntimeFlag.key, RuntimeFlag.enabled)
                .where(RuntimeFlag.key.in_(list(real_to_logical)))
            )).all()
    except Exception as e:  # 读失败：只回缺省方向（更严的一侧），rows_present 全 False
        _logger.warning("行动开关状态读取失败 err=%s", e)
        return out
    for real_key, enabled in rows:
        logical = real_to_logical.get(real_key)
        if logical is None:
            continue
        out["rows_present"][logical] = True
        out[logical] = bool(enabled)
    return out


def _take_token(key: tuple[int | None, str]) -> bool:
    """令牌桶取一枚：满桶 :data:`RATE_LIMIT_PER_MINUTE` 枚、按分钟匀速补充。"""
    now = time.monotonic()
    tokens, last = _BUCKETS.get(key, (float(RATE_LIMIT_PER_MINUTE), now))
    tokens = min(float(RATE_LIMIT_PER_MINUTE), tokens + (now - last) * (RATE_LIMIT_PER_MINUTE / 60.0))
    if tokens < 1.0:
        _BUCKETS[key] = (tokens, now)
        return False
    _BUCKETS[key] = (tokens - 1.0, now)
    return True


def _field(value) -> str:
    """审计字段单行化：空格/换行折成下划线（保持 ``key=value`` 可 grep）。"""
    text = "" if value is None else str(value)
    for ch in (" ", "\t", "\r", "\n"):
        text = text.replace(ch, "_")
    return text


def _audit(*, user_id, tenant_id, plugin: str, intent: ActionIntent,
           result: str, reason: str, elapsed_ms: int) -> None:
    """固定字段顺序的一条 INFO 审计（**带 user_id**，补 M3 缺口）。"""
    _logger.info(
        "ts=%s tenant_id=%s user_id=%s plugin=%s capability=%s action=%s target_app=%s "
        "by=%s result=%s elapsed_ms=%d dry_run=%s reason=%s",
        now_naive_utc().isoformat(), _field(tenant_id), _field(user_id), _field(plugin),
        _field(intent.capability), _field(intent.capability.removeprefix("action_")),
        _field(intent.target_app), _field(intent.by), _field(result), int(elapsed_ms),
        "true" if intent.dry_run else "false", _field(reason),
    )


def _finish(*, started: float, user_id, tenant_id, plugin_name: str, intent: ActionIntent,
            allowed: bool, status: str, reason: str = "",
            action_token: str | None = None) -> Decision:
    _audit(user_id=user_id, tenant_id=tenant_id, plugin=plugin_name, intent=intent,
           result="approved" if allowed else "denied", reason=reason,
           elapsed_ms=int((time.monotonic() - started) * 1000))
    return Decision(allowed=allowed, reason=reason, action_token=action_token,
                    dry_run=intent.dry_run, status=status)


async def decide_action(*, user_id: int | None, tenant_id: int | None,
                        plugin_name: str, intent: ActionIntent) -> Decision:
    """按固定顺序过闸门，批准即入队；**本批不产生任何真实动作**。

    顺序：契约对账（意图合不合能力）→ ①全局 ②账号 ③a 插件灰度闸（仅插件）③b 插件能力授权
    （仅插件）④目标白名单 → 熔断 → 限流 → 强制干跑改写（仅插件）→ 入队。契约对账排在闸门之前，
    否则「意图不合法」会被误报成「被某层闸门拒」，插件读到的原因就没用了。

    ``plugin_name`` 即身份，**只能由调用方（服务端）给出**：内置端点恒传 :data:`BUILTIN_CALLER`，
    桥通道传路径名 —— 本函数不接收任何来自请求体的自称。
    """
    started = time.monotonic()
    plugin_name = str(plugin_name or "")
    is_builtin = plugin_name == BUILTIN_CALLER
    target = (intent.target_app or "").strip()

    invalid = validate_intent(intent)
    if invalid is not None:
        return _finish(started=started, user_id=user_id, tenant_id=tenant_id,
                       plugin_name=plugin_name, intent=intent, allowed=False, status=STATUS_DENIED,
                       reason=f"{REASON_INVALID_INTENT}:{invalid}")

    if not await _global_actions_enabled():
        return _finish(started=started, user_id=user_id, tenant_id=tenant_id,
                       plugin_name=plugin_name, intent=intent, allowed=False, status=STATUS_DENIED,
                       reason=REASON_GLOBAL_OFF)

    if not await _account_actions_enabled(user_id):
        return _finish(started=started, user_id=user_id, tenant_id=tenant_id,
                       plugin_name=plugin_name, intent=intent, allowed=False, status=STATUS_DENIED,
                       reason=REASON_ACCOUNT_OFF)

    if not is_builtin:
        # ③a 灰度闸（M4c-1）：总闸读到即关、灰度名单默认空集——两条都过才继续
        if not await _plugin_actions_enabled():
            return _finish(started=started, user_id=user_id, tenant_id=tenant_id,
                           plugin_name=plugin_name, intent=intent, allowed=False,
                           status=STATUS_DENIED, reason=REASON_PLUGIN_ACTIONS_DISABLED)
        if not await plugin_graylisted(plugin_name):
            return _finish(started=started, user_id=user_id, tenant_id=tenant_id,
                           plugin_name=plugin_name, intent=intent, allowed=False,
                           status=STATUS_DENIED, reason=REASON_PLUGIN_NOT_GRAYLISTED)

        # ③b 能力授权：内置不是插件（plugins 表里没有安装记录，走 M3 判定永远过不去），故不适用
        from app.plugins import registry

        if not await registry.has_capability_permission(
                plugin_name, tenant_id, intent.capability):
            return _finish(started=started, user_id=user_id, tenant_id=tenant_id,
                           plugin_name=plugin_name, intent=intent, allowed=False,
                           status=STATUS_DENIED, reason=REASON_PLUGIN_UNAUTHORIZED)

    if target in TARGET_BLACKLIST:
        return _finish(started=started, user_id=user_id, tenant_id=tenant_id,
                       plugin_name=plugin_name, intent=intent, allowed=False, status=STATUS_DENIED,
                       reason=REASON_TARGET_BLACKLISTED)

    if target not in await configured_targets(tenant_id):
        return _finish(started=started, user_id=user_id, tenant_id=tenant_id,
                       plugin_name=plugin_name, intent=intent, allowed=False, status=STATUS_DENIED,
                       reason=REASON_TARGET_NOT_ALLOWED)

    key = (tenant_id, plugin_name)
    if _FAILURES.get(key, 0) >= CIRCUIT_BREAK_THRESHOLD:
        return _finish(started=started, user_id=user_id, tenant_id=tenant_id,
                       plugin_name=plugin_name, intent=intent, allowed=False, status=STATUS_DENIED,
                       reason=REASON_CIRCUIT_OPEN)

    if not _take_token(key):
        return _finish(started=started, user_id=user_id, tenant_id=tenant_id,
                       plugin_name=plugin_name, intent=intent, allowed=False, status=STATUS_DENIED,
                       reason=REASON_RATE_LIMITED)

    if intent.dry_run:
        # 干跑：只回裁决结论，不发 token、不入队 → 链路上不可能出现真实动作
        return _finish(started=started, user_id=user_id, tenant_id=tenant_id,
                       plugin_name=plugin_name, intent=intent, allowed=True, status=STATUS_DRY_RUN)

    if not is_builtin and await _force_dry_run():
        # 插件灰度期的强制干跑（读不到开关即开）：结论照常回，但不下发可执行 token
        return _finish(started=started, user_id=user_id, tenant_id=tenant_id,
                       plugin_name=plugin_name, intent=intent, allowed=True, status=STATUS_DRY_RUN)

    token = str(uuid.uuid4())
    _PENDING[token] = _PendingAction(token=token, user_id=user_id, tenant_id=tenant_id,
                                      plugin=plugin_name, intent=intent, enqueued_at=time.monotonic())
    return _finish(started=started, user_id=user_id, tenant_id=tenant_id,
                   plugin_name=plugin_name, intent=intent, allowed=True, status=STATUS_NOT_IMPLEMENTED,
                   action_token=token)


def _prune_expired() -> None:
    """丢弃超时未被取走的待执行项（TTL 到期即作废，不补发）。"""
    now = time.monotonic()
    for token in [t for t, p in _PENDING.items() if now - p.enqueued_at >= PENDING_TTL_SECONDS]:
        _PENDING.pop(token, None)


def take_pending(user_id: int | None) -> list[dict]:
    """App 取走当前账号的待执行动作（不删除：回报或 TTL 到期才清除，避免 App 中途崩溃丢动作）。

    只按 ``user_id`` 匹配——拿不到账号（``None``）一律空列表，跨账号/跨租户天然隔离。
    """
    _prune_expired()
    if user_id is None:
        return []
    return [p.as_dict() for p in _PENDING.values() if p.user_id == int(user_id)]


def report_result(token: str, ok: bool, detail: str = "", user_id: int | None = None) -> bool:
    """App 回报执行结果：命中即出队 + 记审计，并按连续失败次数驱动熔断。

    传入 ``user_id`` 时校验归属（**别人的 token 不得替他回报**，fail-closed）；
    连续 :data:`CIRCUIT_BREAK_THRESHOLD` 次失败即对该 ``(租户, 插件)`` 熔断，成功回报清零。
    """
    _prune_expired()
    entry = _PENDING.pop(token, None)
    if entry is None:
        _logger.info(
            "ts=%s tenant_id=%s user_id=%s plugin=%s capability=%s action=%s target_app=%s "
            "by=%s result=unknown_token elapsed_ms=0 dry_run=false reason=token_not_found",
            now_naive_utc().isoformat(), "-", _field(user_id), "-", "-", "-", "-", "-",
        )
        return False
    if user_id is not None and entry.user_id != int(user_id):
        # 归属不符：把条目放回去（真正的所有者还能取到），并按拒绝回报处理
        _PENDING[token] = entry
        _audit(user_id=user_id, tenant_id=entry.tenant_id, plugin=entry.plugin,
               intent=entry.intent, result="report_denied", reason="token_owner_mismatch",
               elapsed_ms=0)
        return False
    key = (entry.tenant_id, entry.plugin)
    if ok:
        _FAILURES.pop(key, None)
    else:
        _FAILURES[key] = _FAILURES.get(key, 0) + 1
    _audit(user_id=entry.user_id, tenant_id=entry.tenant_id, plugin=entry.plugin,
           intent=entry.intent, result="report_ok" if ok else "report_failed",
           reason=(detail or "").strip() or ("ok" if ok else "failed"),
           elapsed_ms=int((time.monotonic() - entry.enqueued_at) * 1000))
    return True
