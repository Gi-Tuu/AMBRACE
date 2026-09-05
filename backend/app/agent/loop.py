"""受控 Agent Loop（Phase B，2026-08-16）

把 [SEARCH] 的「二次生成」泛化为受控 decide→execute→observe 循环：
- decide：LLM 输出正文 + 动作标记（现有 generate_response / agent.ainvoke）
- execute：解析动作并执行工具（搜索；失败自动重试 1 次，单工具超时 30s）
- observe：工具结果注入为带标注上下文（【搜索结果】），条件满足再决策（补查）

统一限制（方案 5.3）：最多 2 次搜索 / 3 次 LLM 调用；节流或开关不通过、搜索失败、
超限 → 静默降级（剥离标记、不编造成功）。Feature Flag agent_loop_search=False 时
退回旧单次「搜索→二次生成」行为（search_rounds=1）。
"""
import asyncio
from typing import Awaitable, Callable

from app.agent import actions as _actions
from app.utils.logger import get_logger

_logger = get_logger("agent.loop")

# 统一限制（方案 5.3：max_steps=3 含最终回复 → 最多 2 次真实搜索）
MAX_LLM_STEPS = 3  # LLM 调用轮数上限（首轮 + 2 次再决策）
MAX_SEARCH_ROUNDS = MAX_LLM_STEPS - 1
MAX_RECALL_ROUNDS = 1  # Ariadne 模块 B：记忆二跳最多 1 次（防无限检索/拖慢回复；与 SEARCH 二跳同上限）

# 记忆二跳结果注入模板（observe；与 _SEARCH_RESULT_TEMPLATE 同位）
_RECALL_RESULT_TEMPLATE = (
    "【补充记忆】（你主动调取了更早/更相关的记忆，现在直接结合它们与已有上下文回复；"
    "不要说'我查了一下记忆'。若与上方记忆冲突，以时间更晚、认知状态为 FACT 的为准）：\n{result}"
)
TOOL_TIMEOUT_SEC = 30.0  # 单工具执行超时
SEARCH_RETRY = 1  # 只读工具失败自动重试次数（方案 5.2）

# Feature Flag（2026-08-17 开源包基线：全部默认开启；各 Flag 作用/前值/回滚方法见 docs/feature-flags.md）：
# agent_loop_search 开=受控搜索循环（最多补查 1 次、失败静默降级）；关=退回旧单次二次生成；
# agent_loop_scheduler 开=arbiter 主动任务写 AgentTask trace（含 10% 角色 route=scheduler_gray 对比标记 + 灰度角色真实任务记录）；
# agent_loop_chat 开=主链路日历/备忘等本地工具经统一执行入口 execute_tool；
# agent_tool_events 开=工具执行联动织库增量（tool.executed 订阅）；
# agent_trace_group 开=群聊角色回应写 AgentTask trace（只写不读可观测）；
# agent_daily_reflection 开=周复盘（每 7 天 1 次，2026-08-17 转全量）；
# agent_reflection_inject 开=主动消息注入最近复盘（反思驱动）；
# agent_context_trim 开=认知注入按角色热度裁剪（低频角色缩小日摘要/织库）；
# agent_loop_group_chat 开=群聊回应走统一 Runtime（逐角色 build_context 注入世界认知，知识不串线）；关=旧单次 JSON 链路（Phase E，2026-08-18 全量开启）；
# agent_loop_social 开=渠道/插件主动候选走统一 Runtime（世界认知注入 + 防 hint 污染记忆）；关=旧裸生成链路（Phase E，2026-08-18 全量开启；X5 渠道化时按渠道语义改名，行为不变）；
# agent_social_light_context 开=群聊/渠道社交短回复走轻量上下文（跳过完整世界认知，单次 prompt ≈-64%；F1/F2，2026-08-18 全量开启）；关=全量 build_context（回退）
AGENT_FLAGS = {
    "agent_loop_search": True,
    "agent_loop_scheduler": True,  # 2026-08-17 全量基线（开源包）：主动任务 trace + 10% 灰度 route 对比
    "agent_loop_chat": True,
    "agent_tool_events": True,  # 2026-08-17 全量基线（开源包）：工具执行联动织库增量
    "agent_trace_group": True,
    "agent_daily_reflection": True,  # 2026-08-17 全量基线（开源包）：周复盘
    "agent_reflection_inject": True,
    "agent_context_trim": True,  # 认知注入按角色热度裁剪（2026-08-16）
    "agent_daily_memory_maintenance": True,  # 日终记忆维护（P0-5，2026-08-16：日摘要补生成+去重+置顶摘要补生成，默认开）
    "agent_loop_group_chat": True,  # Phase E（2026-08-18）：群聊回应走统一 Runtime（2026-08-18 用户拍板全量体验；回退改 False 重启即恢复旧链路）
    "agent_loop_social": True,  # Phase E（2026-08-18）：渠道/插件主动候选走统一 Runtime（X5 渠道化改名；回退改 False 重启即恢复旧链路）
    "agent_social_light_context": True,
    "weave_3d": True,  # 织网 3D（P2 转默认开；仅客户端画布读它选 2D/3D 视图；低端机客户端自动降级 2.5D）  # F1/F2（2026-08-18 用户拍板全量开启）：群聊/渠道社交短回复走轻量上下文（单次 prompt ≈-64%；回退改 False 重启即恢复全量 build_context）。与 agent_loop_group_chat/agent_loop_social 正交：前者管走不走 Runtime，后者管 Runtime 内是否用轻量上下文
    "proactive_naturalness_score": True,  # #28 ①（2026-08-24）：低优先主动消息自然度评分——生成后按规则评分，低于阈值重试 1 次/仍低则降级跳过；关=纯现状
    "proactive_user_rhythm": True,  # #28 ②（2026-08-24）：用户作息学习——从聊天/主动日志推断活跃时段，低优先主动消息在时段外降优先级/推迟；关=纯现状
    # 群聊游戏 Phase 1（2026-08-26）：只加不改既有 flag。总开关=群聊游戏；各游戏开关；主记忆摘要指针；AI 自动回合。
    "group_chat_games": True,        # 游戏总开关（关=游戏入口/API 不展示，可回退）
    "game_undercover": True,         # 谁是卧底
    "game_truth_or_dare": True,      # 真心话大冒险
    "game_twenty_q": True,           # 猜词20问
    "game_werewolf": True,           # 狼人杀（Phase 2）
    "game_liars_bar": True,          # 骗子酒馆（Phase 2）
    "game_turtle_soup": True,        # 海龟汤（Phase 2）
    "game_memory_bridge": True,      # 主记忆摘要指针（关=游戏详情只存游戏库）
    "game_ai_autoplay": True,        # AI 自动回合（关=需手动触发 AI 行动，调试用）
    # ── M1 记忆 P0（2026-08-31，docs/执行方案_记忆与生成_20260831.md S1）──
    "recall_top5": True,       # S1：主路召回出口 5 条（关=回退旧 3 条；rerank 后截断前做类型多样性重排）
    "memory_temporal_recall": False,  # Ariadne 模块 A（2026-09-03）：时间维度确定性检索路（默认关=零行为变化；开=用户原话解析出时间区间时补一条确定性时间路召回，与语义路合并重排）
    "memory_recall_second_hop": False,  # Ariadne 模块 B（2026-09-04）：按需二跳联想检索（默认关=只剥离 [RECALL] 标记零行为变化；开=非流式路径镜像 run_search_loop：首轮输出 [RECALL]查询词[/RECALL] → 本地检索 → 注入【补充记忆】→ 再生成 1 次；流式只剥离不中途二跳）
    "memory_recall_hop_limit": 6,  # Ariadne 模块 B：二跳召回条数（runtime_flag 表只支持 bool 覆盖，本项为硬编码默认值；调小可回退 4）
    "memory_story_assemble": False,  # Ariadne 模块 C（2026-09-04）：沿链半故事化组装（默认关；链建链器另案——空 index 时即使开 flag 也走原路径逐字节等价；建链器落地后开=成链小块注入）
    # ── B1-② 记忆链条建链器（2026-09-04，方案 §10-§18，阶段 C0-C5）──
    # memory_chain_builder 开=写入后异步挂链（chain_id/parent_id/node_type，零额外 LLM，
    #   复用 save_memory 已算 embedding，只对 event/insight）；关=不挂链（回归保护）。
    # memory_chain_expand 开=检索命中沿链补≤2 相邻节点（降权 0.9、受 token 配额与 5 轮去重约束）；关=原注入。
    # proactive_outreach_v2（B1-③，2026-09-04，方案 §1-§9）=主动消息自然化总开关——
    #   开=run_tick 汇总层按「闲置分级+素材前提+避开最近意图」选接触意图，message_generator 走意图分支；
    #   关=意图不参与、走旧链路逐字节等价（默认关，可灰度/一键回退）。
    "memory_chain_builder": False,
    "memory_chain_expand": False,
    "proactive_outreach_v2": False,
    "memory_peak_cutoff": False,  # Ariadne 模块 D（2026-09-04）：自然收敛替代硬截断（默认关；开=按 rerank 分数断档/地板收敛，弃权/弱相关场景条数自然减少；阈值经模块 E v2 标定）
    "recall_diversify": True,  # S1：按类型多样性重排（每类先取 2 条一轮再按原序补齐；关=纯 _ranked[:limit]）
    # ── Life Loop v1.1（2026-08-26；2026-08-27 用户拍板全量开启）──
    "life_loop_enabled": True,            # 主开关：30min 行为决策循环
    "life_loop_visible": True,            # 允许自主行为产生面向用户输出
    "life_loop_llm": True,                # 允许 LLM 生成生活文案（每角色每日≤2次）
    "life_chat_driven_enabled": True,     # 聊天→生活意图链路
    "review_daily_plus": True,            # M1-S7（2026-08-31）：主动复习日额度 3→4（关=回退 3；90min 间隔不变）
    "memory_tiered_decay": False,         # M2-S2（2026-08-31）：分层衰减——高置信持久/低置信加速/跌破阈值冷归档。默认关（灰度开关，开启前先跑 scripts/diagnostics/memory_tiering_snapshot.py 快照）；关=逐字节现状
    "marker_recovery": True,              # M2-S5（2026-08-31）：标记截断保底——A 通道标记被截断时本条源消息立即走通道 B 提取（写侧查重防重复）；关=仅批量补提
    # ── X3 Provider 端口（2026-08-31，docs/执行方案_扩展化_20260831.md 批次 X3）──
    # provider_registry 开=LLM/TTS 经 app/providers 注册口解析实现（内置 openai_compatible/dashscope 为默认实现，
    # 插件可经 sdk.register_provider 注册并以配置 provider 字段选中）；关=直连内置实现（与旧链路逐字节一致）。
    "provider_registry": True,
    # ── M3-a 工作记忆（2026-09-01，docs/设计_M3工作记忆_20260901.md）──
    # working_state_enabled 开=turn 结束后异步评估/滚动覆盖 working_state 行（写入链路，fail-open）；
    # 关=完全跳过（无行产生；注入为 M3-b 另行灰度）。默认关（快照脚本可回滚）。
    "working_state_enabled": True,  # 2026-09-01 用户拍板：开启数据积累（注入仍为 M3-b 未灰度）
    "life_home_worldmap_enabled": True,   # 小家大地图（§11）
    # ── 生命感增强 v1（#63，2026-08-27；全部默认关，可独立回退）──
    "reply_delay_enabled": True,         # 机制2：动态回复延迟（用户主动消息才生效）
    "spring_emotion_enabled": True,      # 机制1：弹簧-阻尼情绪（4 维 + 人格基线）
    "life_share_enabled": True,          # 机制4：活动完成自然分享（arpiter 门控 + 配额）
    "preoccupation_enabled": True,       # 机制5：心事微澜（复用 Memory.sub_type）
    # ── #70 方案A：记忆分层检索与注入（2026-08-30；独立可回滚）──
    # memory_tiered_inject 开=Top1 L2(240)/其余 L0 分层注入 + L1 桥接 + L0 参与向量；关=统一 150 字旧链路（逐字节一致）。
    "memory_tiered_inject": False,
    # ── #70 方案B：检索轨迹可观察（2026-08-30；独立可回滚）──
    # memory_trace_debug 开=memory_search trace 补 query/派生/各路命中/RRF/rerank 分数/最终注入（只多写 trace，低风险默认开）；
    # 关=检索/排序/trace 与现状逐字节一致（回归保护）。
    "memory_trace_debug": True,
    # ── #70 方案C：记忆取代链 + 级联失效（M1/M2）+ 冷归档/purge（2026-08-30；独立可回滚）──
    # memory_supersede 开=superseded/stale 状态激活，双通道（SQLite+Chroma）过滤，读取点按状态分流；
    # 关=所有读取/注入/统计与现状逐字节一致（回归保护）。禁止默认 True（误取代比不取代更伤）。
    "memory_supersede": False,
    # Ariadne 模块F（2026-09-04）：Curated Knowledge 编纂知识层（world_facts 加 kind 分治 + 确定性注入）
    "curated_knowledge": False,
    # Ariadne 模块G（2026-09-04）：前瞻意图。enabled=写入（extractor 便车落表）；
    # trigger=触发（时间型 Scheduler 提起 + 线索型 context 注入）。两段灰度：先开 enabled 攒数据，再开 trigger。
    "prospective_intent_enabled": False,
    "prospective_intent_trigger": False,
    # ── §20 跨角色用户事实（2026-09-04，默认关=零行为变化；bool 可 runtime 热更）──
    # global_user_facts：用户级可变事实层总开关——开=GPS/跨角色事实写入 + [USER NOW] 注入分区；
    #   关=不写/不读 user_facts（抽取出原路径、注入空）。
    "global_user_facts": False,
    # cross_char_fact_sync：跨角色对齐——开=角色构建上下文前惰性对齐 + 每日 sweep，把同槽旧值
    #   per-char 记忆标 stale（复用 #70，不删可追溯）；关=不对齐。
    "cross_char_fact_sync": False,
    # cross_char_fact_projection：变化投影——开=对齐时按模板投影一条 global_sync 记忆进记忆本
    #   （零 LLM，skip_dedup）；关=只标 stale + 靠 [USER NOW] 注入（默认推荐关）。
    "cross_char_fact_projection": False,
    # ── 一机多主 / 渠道绑定 per-账号化（2026-09-05，交接拍板，默认关=回落旧路径）──
    # channel_binding_v2 开=渠道绑定读 channel_bindings 新表（租户隔离，读写走 ChannelBindingService）；
    #   关=渠道插件/读取层回落旧全局 config allowed_character_ids 串（单主部署语义等价，零行为变化）。
    #   新表/新列迁移幂等常驻（alembic a7b8c9d0e1f2），关 flag 即全链路回退、无数据删除。
    "channel_binding_v2": False,
}

# 搜索结果注入模板（与旧文案唯一差异：第 3 点允许结果不足时补查 1 次）
_SEARCH_RESULT_TEMPLATE = (
    "【搜索结果】（你已经搜索完成，现在直接基于这些真实信息回复；不要说自己去'搜索了'）。\n"
    "{result}\n\n"
    "注意：1. 如果结果有用，自然引用回答用户；2. 如果结果与问题无关或质量差，说明没查到靠谱的并给出你自己的看法（例如'网上说法不太靠谱，我估计…'）；"
    "3. 你已经搜索完成，绝不要说'我去搜一下/等着我去查'这类话；如果这次结果仍不够或与问题无关，可以再输出一次 [SEARCH] 补充查询（最多再查 1 次），否则不要再输出 [SEARCH] 标记。"
    "4. 网络信息属未证实来源（Observation: UNVERIFIED），涉及事实/数字/做法请谨慎转述，不确定就说明是'网上说法'。"
)


async def _execute_search_tool(user_id: int, query: str, run_search: Callable[[str], Awaitable[str]]) -> dict:
    """经统一工具执行入口调用搜索（Phase E：权限三档 + 工具生命周期钩子 + 幂等重试 + 异常隔离）。

    - 单工具超时 30s 由本层 wait_for 保证（超时 → execute_tool 捕获为 error）；
    - forbid → blocked（搜索被权限拦截）；ask → search 为只读低风险自动放行（不挂起询问）；
    - run_search 返回空串不算异常，空结果重试由 run_search_loop 外层控制（SEARCH_RETRY）。
    """
    from app.agent import tools as _tools
    from app.agent.tool_runner import execute_tool
    _spec = _tools.get_tool("search")
    if _spec is None:
        return {"status": "error", "error": "search tool not registered"}
    _exec_spec = _tools.ToolSpec(
        name=_spec.name,
        description=_spec.description,
        action_type=_spec.action_type,
        risk_level=_spec.risk_level,
        rate_limit=_spec.rate_limit,
        idempotent=_spec.idempotent,
        scope=_spec.scope,
        ask_auto_allow=_spec.ask_auto_allow,
        epistemic_status=_spec.epistemic_status,
        provenance=_spec.provenance,
        execute=lambda payload: asyncio.wait_for(run_search(payload.get("query") or ""), timeout=TOOL_TIMEOUT_SEC),
    )
    return await execute_tool(_exec_spec, {"query": query}, user_id=user_id, character_id=None, session_id=None)


async def run_recall_loop(
    final_state: dict,
    *,
    user_id: int,
    character_id: int,
    gate: Callable[[], object] | None = None,
    tz_offset_min: int | None = None,
) -> tuple[dict, list[dict]]:
    """记忆二跳受控循环（Ariadne 模块 B，2026-09-04）：decide → [RECALL] → 本地记忆检索 → observe 注入 → 再决策 1 次。

    ``tz_offset_min``（F-3，2026-09-04）：用户本地时区分钟偏移，透传给
    ``parse_time_range``。「时间=YYYY-MM」走绝对自然月不受时区影响（二跳绝对月路径不改），
    透传仅与主检索相对时间口径保持一致；None 时回退 UTC（零行为变化）。

    - 镜像 run_search_loop（零新框架）；非流式专用——流式路径由调用方仅做标记剥离（与 SEARCH 同策略）；
    - flag ``memory_recall_second_hop`` 默认关：不检索、不注入、只剥离标记（零行为变化）；
    - 标记内轻量时间语法「时间=YYYY-MM；查询」→ parse_time_range 解析（失败回退纯语义）；
    - 查询失败/无命中/gate 不通过 → 剥离标记静默降级（不编造「想起来了」）；二跳触发/命中写 trace 步骤；
    - gate 支持 sync/async callable（角色 memory_v2_enabled 关闭时不开放二跳）。
    """
    import re as _re
    steps: list[dict] = []
    try:
        for _ in range(MAX_RECALL_ROUNDS + 1):  # 最多解析→补查一轮，第二轮只清理
            clean, q = _actions.extract_recall(final_state.get("ai_response") or "")
            if not q:
                final_state["ai_response"] = clean
                break
            if not bool(AGENT_FLAGS.get("memory_recall_second_hop", False)):
                final_state["ai_response"] = clean
                break
            if gate is not None:
                _ok = gate()
                if asyncio.iscoroutine(_ok):
                    _ok = await _ok
                if not _ok:
                    final_state["ai_response"] = clean
                    break
            # 拆「时间=YYYY-MM；查询」轻量语法（解析失败回退纯语义，绝不猜）
            t_range = None
            qq = q
            tm = _re.match(r"\s*时间\s*[=:]\s*([0-9]{4}[-年/.][0-9]{1,2})[；;，,\s]+(.*)", q, _re.S)
            if tm:
                from app.memory.time_query import parse_time_range
                # F-3：透传用户时区偏移（「时间=YYYY-MM」绝对自然月不受影响，与主检索口径一致）
                t_range = parse_time_range(tm.group(1), tz_offset_min=tz_offset_min)
                qq = tm.group(2).strip() or q
            from app.memory import search_memories
            _hop_limit = int(AGENT_FLAGS.get("memory_recall_hop_limit", 6) or 6)
            hits = await search_memories(
                character_id=character_id,
                query=qq,
                limit=_hop_limit,
                time_range=t_range,
                trace_meta={"user_id": user_id, "trigger": "recall_second_hop"},
            )
            steps.append({"action": "RECALL", "query": qq[:80], "n": len(hits)})
            if not hits:
                # 没查到：不再二跳，用首轮正文（不编造「想起来了」）
                final_state["ai_response"] = clean
                break
            # 命中即复习（与第一跳一致，24h 防抖在 reinforce_memories 内）；失败不影响注入
            try:
                from app.memory.service import reinforce_memories
                from app.memory.constants import REINFORCE_FACTOR_RETRIEVE, REINFORCE_DEBOUNCE_HOURS
                await reinforce_memories(
                    [h["id"] for h in hits],
                    factor=REINFORCE_FACTOR_RETRIEVE,
                    debounce_hours=REINFORCE_DEBOUNCE_HOURS,
                )
            except Exception:
                pass
            from app.memory.format import format_memory_line
            block = "\n".join(format_memory_line(h, include_speaker=True) for h in hits)
            final_state["context_messages"] = final_state.get("context_messages") or []
            final_state["context_messages"] = final_state["context_messages"] + [{
                "role": "system",
                "content": _RECALL_RESULT_TEMPLATE.format(result=block),
            }]
            final_state["ai_response"] = ""
            from app.agent.nodes import generate_response as _regen
            final_state = await _regen(final_state)  # 主模型再生成 1 次（与 SEARCH 二跳同成本）
            # 下一轮循环开头 extract_recall 负责识别再次调取标记（超上限时兜底剥离，幂等）
        # 兜底：最后一次剥离（幂等）
        final_state["ai_response"] = _actions.extract_recall(final_state.get("ai_response") or "")[0]
    except Exception as e:
        _logger.warning("Agent recall loop failed char=%s: %s", character_id, e)
        try:
            final_state["ai_response"] = _actions.extract_recall(final_state.get("ai_response") or "")[0]
        except Exception:
            pass
    return final_state, steps


async def run_search_loop(
    final_state: dict,
    *,
    user_id: int,
    character_id: int,
    run_search: Callable[[str], Awaitable[str]],
    throttle: Callable[[int], bool],
    inject_enabled: Callable[[], bool],
    save_history: Callable[[int, str], Awaitable[None]],
    max_steps: int | None = None,
) -> tuple[dict, list[dict]]:
    """受控搜索循环：decide → 执行 SEARCH → observe（注入结果）→ 条件再决策。

    - final_state 已含首轮 LLM 输出（agent.ainvoke 结果）；本函数处理其后所有 [SEARCH] 动作；
    - 返回 (final_state, steps)：steps 为每轮搜索执行摘要（供 Task Trace）；
    - 节流/开关不通过、搜索失败 → 剥离标记静默降级（不编造成功）；
    - 超过搜索轮数上限 LLM 仍输出 [SEARCH] → 剥离标记直接返回。
    """
    steps: list[dict] = []
    rounds = MAX_SEARCH_ROUNDS
    if not AGENT_FLAGS.get("agent_loop_search", True):
        rounds = 1  # Feature Flag 关：退回旧单次二次生成
    if max_steps is not None:
        rounds = max(1, min(max_steps - 1, MAX_SEARCH_ROUNDS))
    try:
        round_no = 1
        while round_no <= rounds:
            clean, query = _actions.extract_search(final_state.get("ai_response") or "")
            if not query:
                final_state["ai_response"] = clean
                break
            # 节流 / 搜索注入开关门禁（与旧行为一致）
            if not (throttle(user_id) and inject_enabled()):
                final_state["ai_response"] = clean
                break
            _logger.info("AI web search char=%d round=%d query=%s", character_id, round_no, query[:60])
            # 执行搜索（Phase E：统一工具执行入口 execute_tool——权限三档 + 生命周期钩子 + 异常隔离；
            # 空结果重试 1 次由本层控制，单工具超时 30s）
            result = ""
            blocked = False
            for attempt in range(SEARCH_RETRY + 1):
                _res = await _execute_search_tool(user_id, query, run_search)
                if _res.get("status") == "blocked":
                    blocked = True
                    _logger.info("AI web search blocked round=%d query=%s: %s", round_no, query[:60], _res.get("error"))
                    break
                result = (_res.get("result") or "") if _res.get("status") == "ok" else ""
                if result:
                    break
            steps.append({"action": "SEARCH", "query": query[:80], "ok": bool(result), "round": round_no})
            if blocked or not result:
                _logger.warning("AI web search %s round=%d query=%s: 降级为剥离标记", "blocked" if blocked else "failed", round_no, query[:60])
                final_state["ai_response"] = clean
                break
            # observe：落浏览记录 + 注入结果 → 再决策（允许补查）
            try:
                await save_history(character_id, query)
            except Exception as e:
                _logger.warning("AI search history save failed: %s", e)
            final_state["context_messages"] = final_state.get("context_messages") or []
            final_state["context_messages"] = final_state["context_messages"] + [{
                "role": "system",
                "content": _SEARCH_RESULT_TEMPLATE.format(result=result),
            }]
            final_state["ai_response"] = ""
            from app.agent.nodes import generate_response as _regen
            final_state = await _regen(final_state)
            # 不再立即剥离：下一轮循环开头的 extract_search 负责识别补查标记；
            # 无补查时下一轮以 clean 退出；超限时由循环后的兜底剥离（幂等）。
            round_no += 1
        # 超限/退出兜底：最后一次剥离（幂等）
        final_state["ai_response"] = _actions.extract_search(final_state.get("ai_response") or "")[0]
    except Exception as e:
        _logger.warning("Agent search loop failed: %s", e)
        try:
            final_state["ai_response"] = _actions.extract_search(final_state.get("ai_response") or "")[0]
        except Exception:
            pass
    return final_state, steps
