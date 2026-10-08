"""认知投影（A28-②a，2026-10-07）：把**已有系统**的结构化认知投进 Cognitive Workspace。

派单：`AMBRACE_paidan_A28b2a_projection_20261007.md`；考察报告：
`AMBRACE_A28b2a_workspace_projection_考察_20261007.md`（仓库外的派发文档，不在本仓内）。

四条硬边界（守卫逐条钉住，违反即红）：

1. **只做投影**：不新写 SQL、不开会话、不调 LLM、不执行 Tool、不写 Memory、不控制 LangGraph、不决定 Action；
   需要取数时**只调用既有只读接口**（`memory.current_state.get_current_user_state` /
   `events.world_state.world_state_snapshot`），且这一步整体在影子闸后面。
2. **不替代、不合并**：Memory Retrieval / Topic Tracker / Working State / World State 各自仍是唯一事实源；
   Working State ＝持久滚动工作记忆，Workspace ＝本轮瞬时认知，投影只是**另开一个字段抄一份**，不合并两者。
3. **取不到就留空并记下原因**：宁可覆盖率为 0，也不替角色编造认知（`DEFER_REASONS` 是本单的边界自证）。
4. **默认关**：任何额外取数与落痕都在 `AGENT_FLAGS["cognitive_projection_shadow"]=False` 后面；
   关掉时本模块只用 state 里已有的数据，**零额外查询、零库写、输出逐字节不变**。
"""
from __future__ import annotations

import json
import logging

_logger = logging.getLogger("agent.workspace_projection")

# 影子闸键名（登记在 app/flags/agent_flags.py 与 docs/feature-flags.md，双向一致由守卫核）
SHADOW_FLAG = "cognitive_projection_shadow"
SHADOW_ROUTE = "cognitive_projection"

# 读数覆盖的投影面（与 CognitiveWorkspace 的字段一一对应）
PROJECTED_FIELDS = (
    "identity", "focus", "current_state", "observations", "world", "working_state",
    "active_topics", "goal", "active_need", "open_loops", "constraints",
    "pending_commitments", "candidate_actions", "last_decision", "decision_contract",
)

# 本单刻意留空的字段与原因——读数以这个形式自证"是边界不是遗漏"
DEFER_REASONS = {
    "goal": "长期 topics/goals ≠ 本轮选中的目标（派单红线）⇒ ②a 不设置",
    "active_topics": "Topic Tracker 只给文本出口（app/agent/topic_tracker.py:335/:372），改它被本单禁止 ⇒ 等结构化出口",
    "open_loops": "没有可复用的既有只读结构化接口，本单不新查库 ⇒ 留空",
    "active_need": "同上（情绪/信息缺口没有统一只读出口）",
    "constraints": "约束散在各闸门（state_guard 等），②a 不新建口径",
    "pending_commitments": "承诺读取属 ProspectiveIntent 侧，接它＝新查库 ⇒ 本单不接",
    "candidate_actions": "属 A28-②b：候选枚举与契约只在 decision_contract_shadow 打开时产生（默认关）",
    "decision_contract": "A28-②b 契约本体，默认关 ⇒ 不建不写",
}

# World State 只投"真正需要"的几路（**不是整份快照**；两派生视图里的锚点文本明确排除）
PROJECTION_ROUTES = (
    "user_facts",
    "world_facts",
    "character_current_status",
    "life_states",
    "working_state",
)
MAX_ITEMS_PER_ROUTE = 3

# 逐条只抄这几个键：source / epistemic_status / authority / freshness / as_of 的载体全保留，
# 渲染字段（`line`、`_view_text` 那串喂给模型的文本）一律不抄——投影要的是事实形状，不是文案。
KEEP_ITEM_KEYS = (
    "predicate", "kind", "value", "epistemic_status", "actor", "subject_type", "subject_id",
    "source_store", "source_table", "asserted_at", "fresh_until", "fresh_at_as_of",
    "is_authoritative", "as_of", "age_hours", "key", "label",
)


def _compact(item) -> dict:
    """按白名单抄一条明细（非白名单的渲染文本一律不进 Workspace；None 值不占位）。"""
    if not isinstance(item, dict):
        return {}
    out = {}
    for k in KEEP_ITEM_KEYS:
        if k not in item:
            continue
        v = item[k]
        if v is None or v == "":
            continue
        out[k] = v if isinstance(v, (str, int, float, bool, list, dict)) else str(v)
    return out


def _compact_route(name: str, route: dict) -> dict:
    """一条路的投影：保留权威源标签与 as_of 口径，items 限量。缺路/坏路返回空 dict。"""
    if not isinstance(route, dict):
        return {}
    items = route.get("items")
    if not isinstance(items, list):
        items = []
    return {
        "route": name,
        "kind": route.get("kind"),
        "source_module": route.get("source_module"),
        "source_at": route.get("source_at"),
        "authoritative_source": route.get("authoritative_source"),
        "as_of_basis": route.get("as_of_basis"),
        "status": route.get("status"),
        "count": route.get("count"),
        "items": [_compact(x) for x in items[:MAX_ITEMS_PER_ROUTE] if _compact(x)],
    }


def project_identity(ws, identity) -> bool:
    """投「我是谁」的快照：只抄调用方已经拿在手里的人设字段，不为此另查库。"""
    if ws is None or not isinstance(identity, dict):
        return False
    picked = {}
    for key in ("character_name", "user_name", "persona_summary", "bio"):
        val = identity.get(key)
        if val:
            picked[key] = str(val)[:300]
    if not picked:
        return False
    ws.identity = picked
    return True


def project_focus(ws, perception) -> bool:
    """投「当前关注」：perceive 节点已经写过 `ws.focus` 时**不覆盖**（那是原始来源，投影靠后）。"""
    if ws is None or getattr(ws, "focus", None):
        return False
    topic = None
    if isinstance(perception, dict):
        topic = perception.get("topic")
    if not topic:
        return False
    ws.focus = str(topic)
    return True


def project_current_state(ws, current_state) -> dict | None:
    """投 Current State：**用结构化 entries**（`{"entries":[{key,label,value,source}],"empty":bool}`）。

    刻意不做两件事：不把 `current_state_anchor()` 那段渲染文本当来源（它继续服务 Prompt 渲染）；
    不把 entries 拼成一段大字符串塞进 Workspace。
    """
    if ws is None or not isinstance(current_state, dict):
        return None
    entries = current_state.get("entries")
    if not isinstance(entries, list) or not entries:
        return None
    picked = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        row = {}
        for k in ("key", "label", "value", "source"):
            if e.get(k) not in (None, ""):
                row[k] = str(e[k])
        if row.get("value"):
            picked.append(row)
    if not picked:
        return None
    ws.current_state = {
        "entries": picked,
        "source": "app/memory/current_state.get_current_user_state",
        "empty": False,
    }
    return ws.current_state


def project_world(ws, world) -> dict | None:
    """投 World State：**只投白名单路、每路限量条数**（不 dump 整份快照）。"""
    if ws is None or not isinstance(world, dict):
        return None
    routes = world.get("routes")
    if not isinstance(routes, dict):
        return None
    picked = {}
    for name in PROJECTION_ROUTES:
        one = _compact_route(name, routes.get(name) or {})
        if one.get("items"):
            picked[name] = one
    if not picked:
        return None
    ws.world = {
        "routes": picked,
        "as_of": world.get("as_of"),
        "snapshot_version": world.get("version"),
        "source": "app/events/world_state.world_state_snapshot",
    }
    return ws.world


def project_working_state(ws, world) -> bool:
    """Working State 经 World State 的 `working_state` 路抄一份到**独立字段**。

    不合并：本字段只是投影，持久化的 Working State 行与它的滚动语义一字未动
    （唯一事实源仍是 `app/application/working_state_service.py:41 get_latest`）。
    """
    if ws is None or not isinstance(world, dict):
        return False
    route = ((world.get("routes") or {}).get("working_state") or {})
    items = route.get("items") if isinstance(route, dict) else None
    if not isinstance(items, list) or not items:
        return False
    ws.working_state = {
        "items": [_compact(x) for x in items[:MAX_ITEMS_PER_ROUTE] if _compact(x)],
        "as_of": world.get("as_of"),
        "source": "app/events/world_state routes['working_state'] ← working_state_service.get_latest",
        "note": "投影副本，不与持久滚动工作记忆合并",
    }
    return bool(ws.working_state["items"])


def project_topics(ws, topics) -> bool:
    """Topic Tracker → `active_topics`：只接受**调用方注入的结构化列表**，本模块绝不自己查话题表。

    同时守住红线：不把长期 topics/goals 等同于 `ws.goal`（`goal` 本单恒空，原因见 DEFER_REASONS）。
    """
    if ws is None or not isinstance(topics, (list, tuple)):
        return False
    picked = []
    for t in topics:
        if isinstance(t, dict):
            name = t.get("topic") or t.get("name")
            if name:
                picked.append({k: v for k, v in t.items() if k in ("topic", "goal", "follow_up", "importance", "last_touched_at") and v not in (None, "")})
        elif t:
            picked.append({"topic": str(t)})
    if not picked:
        return False
    ws.active_topics = picked
    return True


def project_workspace(ws, *, current_state=None, world=None, topics=None,
                      identity=None, perception=None) -> dict:
    """把给到的结构化数据投进工作台，返回**本次投影报告**（纯函数：除 ws 外无任何副作用）。"""
    filled = {}
    if project_identity(ws, identity):
        filled["identity"] = "caller(state 内已有人设字段)"
    if project_focus(ws, perception):
        filled["focus"] = "app/agent/message_classifier.perceive → state['perception']"
    if project_current_state(ws, current_state) is not None:
        filled["current_state"] = "app/memory/current_state.get_current_user_state"
    if project_world(ws, world) is not None:
        filled["world"] = "app/events/world_state.world_state_snapshot"
    if project_working_state(ws, world):
        filled["working_state"] = "app/events/world_state routes['working_state']"
    if project_topics(ws, topics):
        filled["active_topics"] = "caller 注入（Topic Tracker 结构化出口待下一批）"
    report = coverage_report(ws, extra_filled=filled)
    if ws is not None:
        ws.projection = report
    return report


def _has_value(v) -> bool:
    """覆盖率判据：空 dict / 空 list / None / 空串 都算没填上（不把"有个键"当有数据）。"""
    if v is None or v == "" or v == {} or v == []:
        return False
    if isinstance(v, dict):
        if v.get("empty") is True:
            return False
        items = v.get("entries") or v.get("items") or v.get("routes")
        if items is not None:
            return bool(items)
        return bool(v)
    if isinstance(v, (list, tuple)):
        return bool(v)
    return True


def coverage_report(ws, *, extra_filled: dict | None = None) -> dict:
    """只读口径：本次投影填了哪些字段、每字段来源、覆盖率为 0 的清单＋原因。

    `observations` / `last_decision` 由 A28-S3 的既有写入点负责（本模块不重写），
    所以它们的来源标成 `nodes/runtime（A28-S3 既有写入点）`。
    """
    existing_sources = {
        "observations": "app/agent/nodes.py:218-235 ＋ app/agent/runtime.py:116-137（A28-S3 既有写入点）",
        "last_decision": "app/agent/nodes.py:663-666（A28-S3 既有写入点；Reflection≠Decision 的语义本单不改）",
        # A28-②b：这两项只有拨开影子闸后才会有值；没写就照常算覆盖 0（不谎报）
        "candidate_actions": "app/agent/decision_contract.py（②b 枚举，闸开时才有）",
        "decision_contract": "app/agent/decision_contract.py（②b 契约本体，闸开时才有）",
    }
    filled = dict(extra_filled or {})
    for name, src in existing_sources.items():
        if _has_value(getattr(ws, name, None) if ws is not None else None):
            filled.setdefault(name, src)
    empty = []
    for name in PROJECTED_FIELDS:
        if name in filled:
            continue
        empty.append({"field": name, "reason": DEFER_REASONS.get(name, "本轮没有对应数据（既有来源给不出值，不虚构）")})
    total = len(PROJECTED_FIELDS)
    return {
        "filled": filled,
        "filled_count": len(filled),
        "total_fields": total,
        "coverage": round(len(filled) / total, 4) if total else 0.0,
        "zero_coverage": empty,
    }


async def project_into_workspace(state: dict) -> dict | None:
    """Runtime 的唯一投影入口：**整体 fail-open**，任何失败都只是"少投几个字段"，绝不冒泡到主链路。

    默认关（`AGENT_FLAGS["cognitive_projection_shadow"]=False`）时：只用 state 里已有的数据
    （人设字段、感知结果、既有 observations）⇒ **零额外查询、零库写**。
    转 on 后才走既有只读接口取 Current State / World State，并落一条影子留痕供覆盖率读数。
    """
    ws = (state or {}).get("workspace")
    if ws is None:
        return None
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        shadow_on = bool(AGENT_FLAGS.get(SHADOW_FLAG, False))
    except Exception:
        shadow_on = False

    current_state = None
    world = None
    if shadow_on:
        character_id = state.get("character_id")
        user_id = state.get("user_id")
        if character_id and user_id:
            try:
                from app.memory.current_state import get_current_user_state
                current_state = await get_current_user_state(character_id=character_id, user_id=user_id)
            except Exception as e:  # 取数失败＝该字段覆盖 0，不影响主链路
                _logger.debug("Projection current_state failed char=%s: %s", character_id, e)
            try:
                from app.events.world_state import world_state_snapshot
                world = await world_state_snapshot(user_id=user_id, character_id=character_id)
            except Exception as e:
                _logger.debug("Projection world_state failed char=%s: %s", character_id, e)

    # identity 的取键口径（10-08 核实后改）：**只读装配阶段真写进 state 的那些键**——
    # `context/assembly.py` 写 `user_name`／`character_name`，`character_info` 带上 `self_statement`／`bio`
    # 两处装配点（registry 路径与 runtime 的 light 路径）形状一致 ⇒ 两条通道都能吃到。
    # 旧写法找的顶层键 `persona_summary`／`personality`／`bio` 全仓没有任何一处往 agent state 写，
    # 已从取值链删掉（留着＝「看着有兜底、实际永远空」）。刻意不为此多查一次库。
    cinfo = state.get("character_info") if isinstance(state.get("character_info"), dict) else {}
    identity = {
        "character_name": state.get("character_name"),
        "user_name": state.get("user_name"),
        "persona_summary": cinfo.get("self_statement"),
        "bio": cinfo.get("bio"),
    }
    report = project_workspace(
        ws,
        current_state=current_state,
        world=world,
        topics=(state.get("active_topics") if isinstance(state.get("active_topics"), (list, tuple)) else None),
        identity=identity,
        perception=state.get("perception"),
    )
    report["shadow_flag"] = shadow_on
    ws.projection = report

    if shadow_on:
        try:
            from app.agent.trace import enqueue_task_log
            enqueue_task_log(
                character_id=state.get("character_id"),
                user_id=state.get("user_id"),
                session_id=state.get("session_id"),
                trigger="agent",
                route=SHADOW_ROUTE,
                steps_json=json.dumps([report], ensure_ascii=False),
                status="ok",
            )
        except Exception as e:
            _logger.debug("Projection shadow trace failed: %s", e)
    return report


__all__ = [
    "DEFER_REASONS",
    "KEEP_ITEM_KEYS",
    "MAX_ITEMS_PER_ROUTE",
    "PROJECTION_ROUTES",
    "PROJECTED_FIELDS",
    "SHADOW_FLAG",
    "SHADOW_ROUTE",
    "coverage_report",
    "project_current_state",
    "project_focus",
    "project_identity",
    "project_into_workspace",
    "project_topics",
    "project_working_state",
    "project_workspace",
    "project_world",
]
