"""事实生命周期策略表（A4 批 1 / T3；P0–P2 ＝ 零行为）。

把「一条事实什么时候过期、被谁取代、按什么路由召回」从散落在多个模块的硬编码窗口，
收敛成**一张按 fact_kind 的策略表**。本模块只提供**纯数据 + 纯函数（零 IO、零副作用）**：

- P0：策略表（`POLICY`）＋ `is_expired` 确定性判定 ＋ 同槽取代候选判定（纯函数）；
- P1/P2：`resolve_fact_kind` / `observation_line` 供维护拍子做**干跑观测**（`maintenance_schedule.scan_lifecycle_policy`）
  —— 只计算 + 只记一条 INFO，**不筛选、不改状态、不写库**；
- **P4（2026-09-29，B9 十三拍干跑判读后拍板）＝生效范围只放开 `plan` 一类**：维护拍子按 `current_gear()`
  分流，生效档把「按既有口径已过期的计划记忆」交给 `memory/maintain_plan_expiry.expire_stale_plans`
  置 stale（现状面退出、检索/复习面保留、不物理删除）；**其它 kind 与槽层继续干跑**（只统计、不动作）。
  三档由**既有两个开关**承载（`FLAG_KEY` 观测 + `APPLY_FLAG_KEY` 作用），**不新造第二套开关**。
- **P3（同槽取代）不需要新机制**：三层承接者早已实现，本模块只登记口径（见下方承接者清单）与槽层观测。

纪律（与《批 1 设计草案 v1》一致）：

1. **不新造衰减曲线**：`decay_profile` 只是引用既有 `constants.S_BY_TYPE` 的档位名，或 `inherit`（沿用行自身类型）；
2. **同槽取代只认显式槽键**，不使用相似度（相似度仍只服务既有「同值强化」那条老路）；
3. 全部纯函数 ⇒ 可离线复算、可单测、可审计；**本模块不 import DB / 不读写任何表**。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence

# 观测/干跑开关（P1–P2）；**默认关**（登记在 flags/agent_flags.py::AGENT_FLAGS）
FLAG_KEY = "fact_lifecycle_policy"
# 失效动作授权开关：**复用 L4 既有灰度**（2026-09-09 起它就在 maintain_plan_expiry 里 gate 同一个动作），
# 原 P3 占位名 `fact_lifecycle_policy_apply` 作废——从未登记、从未有调用点，且另起一把闸＝新造第二套。
APPLY_FLAG_KEY = "review_plan_expire_stale"

# 三档（单一事实源）：关 / 干跑（只统计）/ plan 生效（只放开 plan 一类，其余保持干跑）
GEAR_OFF = "off"
GEAR_DRY_RUN = "dry_run"
GEAR_APPLY_PLAN = "apply_plan"
GEARS: tuple[str, ...] = (GEAR_OFF, GEAR_DRY_RUN, GEAR_APPLY_PLAN)

ROUTE_CURRENT_ONLY = "current_only"   # 只在现状面（受 current_facts_active_only 约束）
ROUTE_RECALL_OK = "recall_ok"         # 复习/怀旧面可见 stale
ROUTE_BOTH = "both"
ROUTES: tuple[str, ...] = (ROUTE_CURRENT_ONLY, ROUTE_RECALL_OK, ROUTE_BOTH)

DECAY_INHERIT = "inherit"             # 沿用行自身 memory_type 对应档（不改衰减）


@dataclass(frozen=True)
class Policy:
    """一类事实的生命周期口径（纯数据）。"""

    ttl_days: int | None          # None = 不因时间失效（长期）；到期由 is_expired 判
    supersede_key: str | None     # None = 不参与同槽取代
    decay_profile: str            # "inherit" 或 constants.S_BY_TYPE 的键名
    recall_route: str             # current_only / recall_ok / both
    note: str = ""


# 用户属性槽族的 TTL：**沿用既有口径**（memory/user_facts.py::VOLATILE_FACT_TTL_DAYS），
# 只把散点写死的 dict 收进策略表；relationship / goal_state **显式取 None =「被取代才失效、不按钟表失效」**
# （这两个槽的口径在 P3 拍板时再确认；v1 不改任何行为）。
_SLOT_TTL_DAYS: dict[str, int | None] = {
    "location": 30, "health": 14, "job": 60, "living": 60,
    "relationship": None, "goal_state": None,
}
_SLOT_SUPERSEDE_KEYS: dict[str, str] = {
    "location": "user.location", "health": "user.health", "job": "user.job",
    "living": "user.living", "relationship": "user.relationship",
    "goal_state": "user.goal_state",
}

_SLOT_NOTE = "用户属性槽：同槽取代是批 1 唯一启用路径（P3，flag 二段）"


def _slot_policies() -> dict[str, Policy]:
    out: dict[str, Policy] = {}
    for slot, ttl in _SLOT_TTL_DAYS.items():
        out[f"user_attr.{slot}"] = Policy(
            ttl_days=ttl,
            supersede_key=_SLOT_SUPERSEDE_KEYS[slot],
            decay_profile=DECAY_INHERIT,
            recall_route=ROUTE_CURRENT_ONLY,
            note=_SLOT_NOTE,
        )
    return out


POLICY: dict[str, Policy] = {
    **_slot_policies(),
    "plan": Policy(None, None, "event", ROUTE_RECALL_OK,
                   "计划/约定：过期由 valid_to 单独判，不写死天数（P4 唯一落地生效的类）"),
    "event": Policy(None, None, "event", ROUTE_RECALL_OK,
                    "已发生事件：靠衰减曲线，不按 TTL 失效"),
    "preference": Policy(None, None, "preference", ROUTE_BOTH, "长期偏好"),
    "identity": Policy(None, None, "user_info", ROUTE_BOTH, "身份/长期属性"),
    "insight": Policy(None, None, "insight", ROUTE_RECALL_OK, "洞察"),
    "world_fact": Policy(None, None, DECAY_INHERIT, ROUTE_CURRENT_ONLY,
                         "世界事实：新鲜窗仍由既有 events/facts 判定，v1 只登记"),
    "transient": Policy(None, None, "event", ROUTE_RECALL_OK, "瞬时状态"),
}


def all_kinds() -> tuple[str, ...]:
    """全部已登记的 fact_kind（顺序 = 声明顺序）。"""
    return tuple(POLICY)


def policy_for(kind: str) -> Policy | None:
    """取策略（未登记返回 None；调用方必须显式处理 None，不得默认放行取代）。"""
    return POLICY.get(kind or "")


def supersede_key_for(kind: str) -> str | None:
    """同槽取代键（None = 该类不参与同槽取代）。"""
    pol = policy_for(kind)
    return pol.supersede_key if pol else None


def slot_ttl_days(slot: str) -> int | None:
    """槽级 TTL（供 user_facts 复用与单测对齐既有口径）。未登记槽返回 None。"""
    pol = policy_for(f"user_attr.{slot or ''}")
    return pol.ttl_days if pol else None


def resolve_fact_kind(*, slot: str | None = None, memory_type: str | None = None,
                      sub_type: str | None = None, is_core: bool = False,
                      core_category: str | None = None, text: str = "") -> str:
    """把「现有元数据」映射到 fact_kind（纯函数）。

    - 槽类优先：给了 slot 且已登记 ⇒ `user_attr.<slot>`；给了未登记 slot ⇒ 降级 `transient`（绝不乱取代）；
    - 其余复用读取侧现算口径 `memory.tense.classify_tense`（**不另造一套分类**），再做类型细分；
    - 认不出的一律降级 `transient`（保守：不参与同槽取代、无 TTL）。
    """
    if slot:
        kind = f"user_attr.{slot}"
        return kind if kind in POLICY else "transient"
    try:
        from app.memory.tense import classify_tense
        tense = classify_tense({"memory_type": memory_type or "", "sub_type": sub_type or "",
                                "is_core": bool(is_core), "core_category": core_category,
                                "content": text or ""})
    except Exception:
        return "transient"
    if tense == "plan":
        return "plan"
    if tense == "transient":
        return "transient"
    if tense == "episodic":
        return "event"
    mt = (memory_type or "").strip().lower()
    if mt == "preference":
        return "preference"
    if mt == "insight":
        return "insight"
    if mt == "event":
        return "event"
    return "identity"


def is_expired(kind: str, *, created_at: datetime | None = None,
               valid_to: datetime | None = None, superseded: bool = False,
               now: datetime | None = None) -> bool:
    """确定性失效判定（纯函数）：`已取代` > `valid_to 到期` > `TTL 到期`。

    任一侧缺信息（未登记类型 / TTL 为 None / 无 created_at / 无 now）一律 **判未失效**（保守）。
    """
    if superseded:
        return True
    if valid_to is not None and now is not None and valid_to <= now:
        return True
    pol = policy_for(kind)
    if pol is None or pol.ttl_days is None or created_at is None or now is None:
        return False
    return (now - created_at).days >= int(pol.ttl_days)


# ── P4 档位（生效范围的选择器）──────────────────────────────────────────────
# 只读进程内的 AGENT_FLAGS 字典（app/flags/agent_flags.py 是纯 dict、不碰 DB），
# 本模块仍然「零 IO、不读写任何表」。


def gear_of(value) -> str:
    """把开关值归一成档位（纯函数）：关 / 干跑 / plan 生效。

    - falsy（False / None / 0 / 空串）⇒ `off`：连扫描都不跑，逐字节旧行为；
    - `True` ⇒ `dry_run`：**向后兼容**——runtime_flags 只能写 bool，线上现有的「拨开」＝干跑，
      本批不把它升级成生效（否则上线即改行为）；
    - `"dry_run"` ⇒ `dry_run`；`"apply_plan"` ⇒ `apply_plan`（只让 plan 一类落地）；
    - **其它任何值（含拼错的字符串）⇒ `dry_run`**：认不出就只统计，绝不擅自生效。
    """
    if not value:
        return GEAR_OFF
    if isinstance(value, str):
        return GEAR_APPLY_PLAN if value.strip().lower() == GEAR_APPLY_PLAN else GEAR_DRY_RUN
    return GEAR_DRY_RUN


def current_gear() -> str:
    """当前档位（读 AGENT_FLAGS[FLAG_KEY]；读不到/异常一律 `off`＝最保守的一侧）。"""
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        return gear_of(AGENT_FLAGS.get(FLAG_KEY, False))
    except Exception:
        return GEAR_OFF


def plan_apply_allowed() -> bool:
    """是否允许「把已过期计划置 stale」这一动作发生（单一授权口，供两处调用点共用）。

    任一即放行：① L4 既有灰度 `review_plan_expire_stale`（日终维护通道，语义逐字节不变）；
    ② 策略表生效档 `current_gear() == apply_plan`（P4 新增，只授权 plan TTL 这一件事）。
    读不到开关一律 False（关）。
    """
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        if bool(AGENT_FLAGS.get(APPLY_FLAG_KEY, False)):
            return True
    except Exception:
        return False
    return current_gear() == GEAR_APPLY_PLAN


_VALUE_NOISE = " \t\r\n，。；;,.!！?？、·“”\"'（）()【】[]"


def normalize_value(value: str) -> str:
    """槽值归一（去空白/常见标点 + casefold）：只用于「同值判定」，不改变落库内容。"""
    s = (value or "").strip().casefold()
    for ch in _VALUE_NOISE:
        s = s.replace(ch, "")
    return s


def same_value(a: str, b: str) -> bool:
    """保守判「同一值」：归一后相等，或互为子串（两侧均 ≥2 字）。

    **入参应为「槽值」（短值，如「北京」/「北京市」），不要拿整句记忆正文来比** ——
    句子形态（「用户在长沙」vs「我现在在北京」）本函数**不保证**判同，会落到「值不同」这一侧；
    句子级的同义判断不在本表职责内（那属于既有写入侧查重/合并那条链路）。
    """
    x, y = normalize_value(a), normalize_value(b)
    if not x or not y:
        return False
    if x == y:
        return True
    return len(x) >= 2 and len(y) >= 2 and (x in y or y in x)


def plan_slot_replacements(existing: Sequence[tuple[int, str]], new_value: str) -> dict:
    """给「同一槽键的现存 active 行」算取代计划（纯函数，**不写库**）。

    入参 `existing` 必须是 **(行 id, 槽值)** 且值应为**短槽值**（不是整句记忆正文，见 same_value）。
    返回 `{"supersede": [id...], "reinforce": [id...]}`：
    - 新值归一后为空 ⇒ **全部 reinforce**（值无法判定，宁可不取代）；
    - 旧行值为空/判不出 ⇒ 同样 reinforce（**绝不因为比不出来而取代**）；
    - 值确实不同 ⇒ supersede。
    """
    ids = [int(r[0]) for r in (existing or ())]
    if not normalize_value(new_value):
        return {"supersede": [], "reinforce": ids}
    sup: list[int] = []
    rein: list[int] = []
    for row_id, value in existing or ():
        # 自己这行没有可比的值（空值/无法判定）⇒ 一律走强化，绝不因为「比不出来」而取代
        if not normalize_value(value) or same_value(value, new_value):
            rein.append(int(row_id))
        else:
            sup.append(int(row_id))
    return {"supersede": sup, "reinforce": rein}


def observation_line(*, sampled: int, by_kind: Mapping[str, int],
                     expired: Mapping[str, int]) -> str:
    """干跑观测的单行摘要（供维护拍子打成一条 INFO；空项不展示）。"""
    kinds = " ".join(f"{k}={int(by_kind[k])}" for k in sorted(by_kind) if by_kind[k])
    exp = " ".join(f"{k}={int(expired[k])}" for k in sorted(expired) if expired[k])
    return f"sampled={int(sampled)} kinds[{kinds or '-'}] expired[{exp or '-'}]"


def apply_observation_line(gear: str, applied: int) -> str:
    """档位与动作量摘要（并入同一条 INFO；干跑档恒 `plan_expired_applied=0`，便于核对档位）。"""
    return f"gear={gear} plan_expired_applied={int(applied)}"


# ── 槽类事实「旧值记忆失效」的承接者登记（A4 批 1 / P3，2026-09-27）─────────────
# 实测结论（只读侦察）：这条链路**项目里早已实现**，共三层触发，无需另写取代机制：
#   ① 写时（单角色）：`memory.extractor` 槽 upsert 成功后调
#      `cross_char_sync.stale_character_slot_memory(character_id, slot, previous_value)`；
#   ② 激活时（每角色）：`agent.context_builder` 调 `cross_char_sync.align_character_to_user_facts`；
#   ③ 每日（全角色）：`scheduling.daily_memory_maintenance` 调
#      `cross_char_sync.sweep_all_characters_alignment(user_id)`。
# 匹配口径（三层同源）：**old_value 前 6 字文本锚点** + 仅 `memory_type == 'user_info'`（写时那条限当前角色）
#   ⇒ insight / event / preference 以及 `sub_type='relationship'`（关系摘要域，生产 1677 行）**都不误伤**。
# 目标状态：`stale`（不是 superseded）—— 仍留痕、复习面可见、现状面（current_facts_active_only）不再当现状。
# 本模块只**登记口径**（单一事实源），不执行任何写操作。
SLOT_MEMORY_STALE_ANCHOR_CHARS = 6
SLOT_MEMORY_STALE_MEMORY_TYPE = "user_info"
SLOT_MEMORY_STALE_STATUS = "stale"
ACTIVE_STATUS = "active"   # 库内 active 口径（只读过滤用；与 memory.supersede.ACTIVE 同值）
SLOT_MEMORY_STALE_IMPLEMENTORS: tuple[str, ...] = (
    "memory.cross_char_sync.stale_character_slot_memory（写时·单角色）",
    "memory.cross_char_sync.align_character_to_user_facts（激活时·每角色）",
    "scheduling.daily_memory_maintenance.sweep_all_characters_alignment（每日·全角色）",
)


def plan_slot_memory_stale(existing: Sequence[tuple[int, str]], old_value: str) -> dict:
    """按既有承接者口径算「哪些旧值镜像记忆该标 stale」（纯函数，与三处实现同源）。

    返回 `{"stale": [id...], "keep": [id...]}`：锚点为空 ⇒ 全 keep（绝不乱标）。
    """
    ids = [int(r[0]) for r in (existing or ())]
    anchor = (old_value or "").strip()[:SLOT_MEMORY_STALE_ANCHOR_CHARS]
    if not anchor:
        return {"stale": [], "keep": ids}
    stale: list[int] = []
    keep: list[int] = []
    for row_id, content in existing or ():
        (stale if anchor in (content or "") else keep).append(int(row_id))
    return {"stale": stale, "keep": keep}


def slot_layer_observation_line(*, facts: int, by_slot: Mapping[str, int], with_prev: int,
                                expired: int, stale_candidates: int) -> str:
    """槽层观测单行摘要（只读统计；供维护拍子打成一条 INFO）。"""
    slots = " ".join(f"{k}={int(by_slot[k])}" for k in sorted(by_slot) if by_slot[k])
    return (f"facts={int(facts)} slots[{slots or '-'}] with_prev={int(with_prev)} "
            f"expired={int(expired)} stale_candidates={int(stale_candidates)}")


def table_snapshot() -> list[dict]:
    """策略表快照（只读，供测试/排查）。"""
    return [
        {"kind": k, "ttl_days": p.ttl_days, "supersede_key": p.supersede_key,
         "decay_profile": p.decay_profile, "recall_route": p.recall_route, "note": p.note}
        for k, p in POLICY.items()
    ]
