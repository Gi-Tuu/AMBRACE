# -*- coding: utf-8 -*-
"""热点函数棘轮守卫（2026-10-10，来源＝外部工程评分报告的建议 3「CI 加复杂度上限」）。

为什么不直接开 Ruff 的 C901：全库当场红几百处，门禁就从"防新债"变成"刷噪音"，
下一次还是把规则关掉。本仓房式是**棘轮**——旧账认了，但不许继续长。

三条判据（算术全在纯函数里，合成用例直接喂假数据验牙；本仓同族教训＝"评测的算术必须是纯函数"）：
① **只降不升**：热点表里每个函数的（长度，近似圈复杂度）必须 ≤ 冻结值；真被拆短了**也红**，
   红话是"把上限下调到 N"——这一声红就是拆分的记账动作本身。
② **总量棘轮**：全库「长>LEN_MAX 或 CC>CC_MAX」的函数总数必须 ≤ OVER_MAX（降了也红，催下调）。
   不用"新增即红"是因为 CC>35 一下圈进几十个短而绕的函数，那种写法第一天就红成一堵墙。
③ **反向钉**：必须真扫到 ≥3000 个模块级函数，且热点表每一行都在实测里命中——
   扫描器空跑＝①②都是装饰（本仓同族前科：C1 的子进程解码、C2 的 cat-file 空扫）。

复杂度是 **AST 近似**（数 If/For/While/Try/推导式 ＋ BoolOp 值数−1），与那份外部报告同一口径，
不是商用工具原值；所以钉的是"别再变长"，不是"复杂度等于几"。
"""
import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
LEN_MAX, CC_MAX = 150, 35
OVER_MAX = 44          # 2026-10-10 实测的超标函数总数（只降不升）
FUNC_FLOOR = 3000      # 同日实测 3171 个模块级函数；掉下这个数＝扫描面塌了

# 冻结基线（2026-10-10 实测；复算脚本＝仓库外 _measure_hot_ratchet.py，同口径）
# (相对 backend/app 的路径, 函数名, 长度上限, 圈复杂度上限)
HOT_RATCHET = [
    ("agent/context/assembly.py", "assemble_context", 877, 198),
    # 2026-10-12 A50 批 1：写后副动作（auto dedup→链→意义→事件→插件 hook→BM25 失效）机械抽成
    # 同文件私有协程 _run_created_memory_hooks（实测 93 行/CC 10，两项都低于阈值⇒OVER_MAX 仍 44）。
    # 纯搬运、零行为变化（AST 逐语句对照 8/8 逐字一致），所以只把上限下调到实测值。
    # 2026-10-12 A50 批 2：新建回执尾段（待核降级→感知打标留痕→create+provenance）机械抽成同文件
    # 私有函数 _emit_created_memory_receipts（实测 95 行/CC 11，两项都低于阈值⇒OVER_MAX 仍 44）。
    # 纯搬运、零行为变化（AST 逐语句对照：三段 unparse 逐字一致），故只把上限下调到实测值。
    # 长度 420 仍高于下面的 400 扫描地板，故地板不动。
    # 2026-10-12 A50 批 3：感知来源标注段（`if _perception_tag_on():` 完整 If 节点含原 try/except）
    # 机械抽成同文件私有协程 _tag_perception_source（实测 58 行/CC 8，两项都低于阈值⇒OVER_MAX 仍 44）。
    # 纯搬运、零行为变化（原 If 节点 unparse 与 helper 内逐字一致），故只把上限下调到实测值；
    # 405 仍 ≥ 400 扫描地板，地板与总量/反向钉都不动。
    # 2026-10-12 A50 批 4：新建记忆准备段（pct 归一化→归属收口→准入闸门→影子埋点→Memory 构造→plan 标注）
    # 连续 14 条语句机械抽成同文件私有协程 _prepare_created_memory（实测 117 行/CC 16，两项都低于
    # 阈值⇒OVER_MAX 仍 44）。纯搬运、零行为变化（14 条语句 unparse 与原块逐字一致），只下调上限；
    # 335 已低于 400，故下面「扫描器必须真的在扫」那一档的 save_memory floor 同步由 400 改为 300。
    # 2026-10-12 A50 批 6：写入前过滤段（元信息闸门／台词原文／逐字 AI 台词 + 来源消息 sender 缓存）
    # 三条连续语句机械抽成同文件私有协程 _prefilter_memory_write（实测 92 行/CC 19，两项都低于
    # 阈值⇒OVER_MAX 仍 44）。判据与三条拒收逐字未变，唯一改写是三处 `return None` → 返回
    # `(keep, _src_msg_sender)` 的运输契约（元信息那条返回 `(False, None)`：它的早退在
    # `_src_msg_sender = None` 之前，引用未绑定名会 UnboundLocalError）。故只把上限下调到实测值；
    # 290 已低于 300，故那一档的 save_memory 长度地板改为「存在性＋冻结值精确对照」（见 LENGTH_PINS）。
    # 2026-10-12 A50 批 7：写入前向量语义查重（试算嵌入→find_similar_memory→跨源禁令→三重保护→
    # 强化/回执/obs_event）连续 3 条语句机械抽成同文件私有协程 _vector_dedup_on_write（实测 84 行/
    # CC 14，两项都低于阈值⇒OVER_MAX 仍 44）。纯搬运、零行为变化，唯一改写是返回值运输契约：
    # 命中 `(m, embedding)`、未命中 `(None, embedding)`（未命中时算出的 embedding 必须带回给后面的
    # 写向量/hooks，清 None 会触发重新求嵌入，重算失败才可能无向量），调用点用 `is not None` 早退。
    # 2026-10-12 A50 批 8：2) 字符级查重（>=0.72 / 最近 30 条）＋ 3) 24h 同主题合并（严格 >0.6 / 50 条）
    # 两段连续语句（共享同一份 `b = content.strip()[:80]` 与同一个局部导入 `SequenceMatcher`）整体机械抽成
    # 同文件私有协程 `_text_topic_dedup_on_write`（实测 139 行/CC 24，两项都低于阈值⇒OVER_MAX 仍 44）。
    # 判据／SQL 过滤／order+limit／阈值／24h 边界／两处状态子句差异（字符档现行闸 vs 主题档怀旧面）／
    # 置顶·锁·归档·跨源保护／importance·strength·derived 合并逐字未变，唯一改写是返回值运输契约：
    # 命中返回原 Memory（两条 `return m`/`return _m` 原样）、未命中返回 None，调用点 `is not None` 早退；
    # 这两段不读也不改 embedding 故不运输它。故只把上限下调到实测值。
    ("memory/write.py", "save_memory", 188, 11),
    ("memory/write.py", "_vector_dedup_on_write", 84, 14),
    ("memory/write.py", "_text_topic_dedup_on_write", 139, 24),
    # 2026-10-12 A50 批10：检索三路召回（三闭包 + 三路 gather 并发 + 命中打戳）整段机械抽成同文件
    # 私有协程 _recall_three_routes（实测 85 行/CC 21，两项都低于阈值⇒OVER_MAX 仍 44）。
    # 9 条语句 unparse 与原 S1-S4 逐字一致，caller 调用点前后 32 条顶层语句逐字未变；唯一改写是
    # 返回值运输契约（多返回 entity_rows/entity_meta/_entity_ids，debug 仍按引用原地打戳保键序）。
    # 372 仍 ≥ 300 扫描地板，故 LENGTH_PINS 那一把的 300 地板、总量与反向钉都不动。
    # 2026-10-12 A50 批11：三路候选合并＋稀疏回填＋计数（dense 合并→sparse 计数＋DB 回填→entity 合并→
    # candidate_count/_dense_has/_sparse_has/_no_candidates）整段机械抽成同文件私有协程
    # _merge_route_candidates（实测 80 行/CC 15，两项都低于阈值⇒OVER_MAX 仍 44）。
    # 10 条语句 unparse 与原 S6-S9 逐字一致，caller 调用点前后顶层语句逐字未变；唯一改写是
    # 返回值运输契约（results/hit_count 由 caller 初始化后**按原对象**传入并原样带回，
    # 不另造第二份集合；async_session_factory 由 caller 显式传入）。
    # 2026-10-13 A50 批12：关键词兜底（LIKE SQL/session/list 构造/candidate_count 改写）与时间召回
    # （flag try/except→区间查询→id 去重追加→trace 打戳）**两段各自**机械抽成同文件私有协程
    # _keyword_fallback（实测 49 行/CC 2）与 _append_temporal_hits（实测 58 行/CC 8），
    # 两项都低于阈值⇒OVER_MAX 仍 44。各自块内语句 unparse 与原两段分支逐字一致（各 2/2），
    # caller 两个 `if` 分支里各只剩一条 await 调用、调用点前后顶层语句逐字未变；唯一改写是
    # 返回值运输契约（前者返 (results, candidate_count)、后者原样返回传进去的 results 对象）。
    # 288 已低于 300，故下面「扫描器必须真的在扫」那一档的 search_memories 长度地板改为
    # 「存在性＋冻结值精确对照」（与批 6 的 save_memory 同一模式，见 LENGTH_PINS）。
    # 2026-10-13 A50 批13：排序成形段（`if results:` 分支体＝peak flag 局部 try/except → debug/非 debug
    # 两条 _rerank 路径 → peak_cutoff＋类型均衡＋去 _score → 邻居补空缺槽与 neighbor_block 打戳）与
    # 插件扩展段（整段 memory_search hook try）**各一条**机械抽成同文件私有协程
    # _rank_and_shape（实测 53 行/CC 13）与 _apply_search_plugins（实测 55 行/CC 14），两项都低于阈值
    # ⇒OVER_MAX 仍 44。两段块内语句 unparse 与原两段逐字一致（排序段 5/5、插件段 try 体 3/3），
    # caller 两个位置各只剩一条 await 调用、其余顶层语句逐字未变；唯一改写是返回值运输契约
    # （两者都原样返回传进去的 results；debug 仍按引用原地更新）。288→240 且 CC 59→34，
    # 故 HOT_RATCHET 与 FROZEN_VALUES 两处一起下调到实测值；240 仍 > 扫描地板 LEN_MAX=150
    # ⇒总量棘轮与反向钉不动。
    # 2026-10-13 A50 批14：RRF 融合段（relevance_bonus 初始化 → debug["rrf_top"] 初始化 → 整段 try）
    # 与检索 trace 段（debug 汇总 if → 整段 enqueue_task_log try）**各一组连续节点**机械抽成同文件
    # 私有**函数** _fuse_route_relevance（实测 39 行/CC 7）与 _emit_search_trace（实测 81 行/CC 16），
    # 两项都低于阈值⇒OVER_MAX 仍 44。两组块内语句 unparse 与原两段逐字一致（RRF 3/3、trace 2/2），
    # caller 两个位置各只剩一条同步调用、其余顶层语句逐字未变（_latency_ms 仍在原行、_final 投影与
    # return _final 不动）；唯一改写是返回值运输契约（前者返 relevance_bonus，后者显式返回 None）。
    # _rrf（caller 局部 import）按 kwarg 显式传入，import 时点不搬家。240→197 且 CC 34→13，
    # 故 HOT_RATCHET 与 FROZEN_VALUES 两处一起下调到实测值；197 仍 > 扫描地板 LEN_MAX=150
    # ⇒总量棘轮与反向钉不动。
    ("memory/retrieve.py", "search_memories", 197, 13),
    # 2026-10-13 A50 批16：用户轮次确定性信号五块（承诺兑现结算 → 安慰词减重 → 复习成功判定 →
    # 情绪关怀登记 → 用户时间承诺解析）连续 5 个节点机械抽成同文件私有协程
    # _run_user_turn_signals（实测 65 行/CC 11，两项都低于阈值⇒OVER_MAX 仍 44）。
    # 五块块内语句 unparse 与搬前逐字一致（5/5），caller 只多一条 await 调用且位置仍在
    # 冷战拦截之后、_cs_snapshot/state 构造与 agent.ainvoke 之前；唯一改写是入参运输
    # （6 名全部 keyword-only、无默认值、无绑定副本；async_session_factory / spawn_background /
    # _logger 仍是 chat_service 裸全局晚绑定）。417→369 且 CC 82→72，故把上限下调到实测值；
    # 369 仍 > 300 长度地板，故 LENGTH_PINS、总量与反向钉都不动。
    # 2026-10-13 A50 批17：受控工具三阶段（记忆二跳 S3 / 自主搜索环＋else 剥标 S4 / MCP 工具 S5）
    # 各一整段机械抽成同文件私有协程 _run_recall_second_hop（实测 42 行/CC 7）、
    # _run_search_stage（60/10）、_run_mcp_stage（46/10），三项都低于阈值⇒OVER_MAX 仍 44
    # （core 252/48 仍超标，占位不变）。搬运体 unparse 与搬前逐字一致（C1 try 体 1、C2 if/else
    # 共 4、C3 try 体 1），caller 除三处「一条 await＋搬运赋值」外 37 条顶层语句逐字未变；
    # `not _is_stream` 两个守卫与 C1 的 full_text 回写尾行仍留在 caller 原位。
    # 唯一改写＝入参/返回值运输（C1 返 (final_state, _trace_llm_calls)、C2 返五元组、
    # C3 返 (final_state, full_text)），裸全局晚绑定与函数内 import 时点不变。
    # 369→252 且 CC 72→48，跌破 300 长度地板 ⇒ 同批把第三把地板改 None 并加精确冻结钉（同批 6/12）。
    # 2026-10-13 A50 批18：收尾五阶段（生图标记提取 D1 / Task Trace 留痕 D2a / Phase H 任务化 D2b /
    # 日历备忘＋AI 承诺标记 E / 可见文本清洗与降级 F2）各整段机械抽成同文件私有函数或协程
    # _extract_gen_image_stage（实测 17 行/CC 4，同步——该段零 IO 零 await）、
    # _emit_chat_trace（24/2）、_schedule_chat_task（17/4）、_run_note_and_timer_stage（47/10）、
    # _finalize_visible_text（38/11），五项都低于阈值⇒OVER_MAX 仍 44（core 164/22 长度仍 >150，
    # 继续占全库超标名额，占位数不变）。搬运体 unparse 与搬前逐字一致（D1 三条、D2a 一条、D2b 一条、
    # E 七条、F2 六条，共 18/18），caller 只多出五条薄编排且其余 23 条顶层语句逐字同序；
    # 唯一改写＝入参/返回值运输（D1/E/F2 返三元组与 (full_text, final_state)，D2a/D2b 返 None），
    # json/time/_agent_actions/spawn_background/_logger 等裸全局晚绑定与函数内 import 时点不变。
    # 252→164 且 CC 48→22（地图估的 ≈116 是静态模拟值，实测以本行为准）；第三把地板仍是 None＋
    # 精确冻结钉（164>150 尚未跌破长度地板，不改地板口径），总量与反向钉一律不动。
    ("application/chat_service.py", "_run_agent_core", 164, 22),
    # 2026-10-13 A50 批16 新切口（同文件私有协程，行 369/CC 11 见上；显式登记防「改了不记账」）
    ("application/chat_service.py", "_run_user_turn_signals", 65, 11),
    # 2026-10-13 A50 批17 新切口三个（同文件私有协程；显式登记防「改了不记账」）
    ("application/chat_service.py", "_run_recall_second_hop", 42, 7),
    ("application/chat_service.py", "_run_search_stage", 60, 10),
    ("application/chat_service.py", "_run_mcp_stage", 46, 10),
    # 2026-10-13 A50 批18 新切口五个（四个私有协程＋一个纯函数；显式登记防「改了不记账」）
    ("application/chat_service.py", "_extract_gen_image_stage", 17, 4),
    ("application/chat_service.py", "_emit_chat_trace", 24, 2),
    ("application/chat_service.py", "_schedule_chat_task", 17, 4),
    ("application/chat_service.py", "_run_note_and_timer_stage", 47, 10),
    ("application/chat_service.py", "_finalize_visible_text", 38, 11),
    ("application/chat/streaming.py", "send_and_receive_stream", 332, 44),
    ("scheduling/message_generator.py", "generate_proactive_event", 263, 45),
    ("main.py", "lifespan", 260, 31),
    # 2026-10-11 批 61：编纂落库那一腿摘成模块级 `_write_curated_item`（热点只许变短），实测 250/74。
    ("memory/extractor.py", "extract_single", 250, 74),
    ("api/games.py", "_resume_ai_turns", 243, 55),
    ("agent/nodes.py", "generate_response", 222, 51),
    ("api/chat.py", "websocket_chat", 212, 40),
    ("agent/llm_client.py", "chat_completion", 189, 48),
    ("agent/runtime.py", "build_light_social_context", 168, 45),
    ("memory/meaning.py", "run_meaning_extraction", 147, 51),
    ("memory/retrieve.py", "_rerank", 147, 52),
    ("scheduling/message_generator.py", "_run_two_rounds", 128, 45),
    ("memory/dedup.py", "deduplicate_memories", 114, 48),
    ("life/decision.py", "decide", 112, 49),
]


def _cc_of(node) -> int:
    cc = 1
    for x in ast.walk(node):
        if isinstance(x, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.comprehension)):
            cc += 1
        elif isinstance(x, ast.BoolOp):
            cc += len(x.values) - 1
    return cc


def _scan():
    """IO 只在这一层：返回 {(相对路径, 函数名): (长度, 近似圈复杂度)}。"""
    out = {}
    for p in sorted(APP.rglob("*.py")):
        rel = p.relative_to(APP).as_posix()
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[(rel, n.name)] = ((n.end_lineno or n.lineno) - n.lineno + 1, _cc_of(n))
    return out


def judge_hot(measured: dict, ratchet: list) -> list:
    """纯函数：热点表 vs 实测量。返回违规说明（空列表＝绿）。不读文件、不读时钟。"""
    out = []
    for rel, name, len_max, cc_max in ratchet:
        cur = measured.get((rel, name))
        if cur is None:
            out.append("%s::%s 找不到了（已拆／改名？）⇒ 删掉这一行或换成新切口" % (rel, name))
            continue
        ln, cc = cur
        if ln > len_max or cc > cc_max:
            out.append("%s::%s 现长 %d/CC %d ＞ 上限 %d/%d＝变长了" % (rel, name, ln, cc, len_max, cc_max))
        elif ln < len_max or cc < cc_max:
            out.append("%s::%s 已变短（%d/CC %d）⇒ 把上限从 %d/%d 下调到 %d/%d"
                       % (rel, name, ln, cc, len_max, cc_max, ln, cc))
    return out


def judge_count(n_over: int, over_max: int) -> str:
    """纯函数：总量棘轮。上升＝新债；下降＝基线该下调（也红，逼着记账）。"""
    if n_over > over_max:
        return "全库超标函数从 %d 涨到 %d ⇒ 新写的长函数要么当场拆，要么进热点表" % (over_max, n_over)
    if n_over < over_max:
        return "超标数已从 %d 降到 %d ⇒ 把 OVER_MAX 下调到 %d，别让旧基线掩盖进展" % (over_max, n_over, n_over)
    return ""


# 「扫描器必须真的在扫」那一档的钉（2026-10-12 A50 批 6 起）：floor=None 的两把改成「冻结值精确对照」，
# 冻结值**写死在这里、不从 HOT_RATCHET 派生**——否则「改表即放行」，钉就白钉了。
LENGTH_PINS = (
    # (相对路径, 函数名, 长度地板)；None = 不设长度地板，改判「与冻结值 (长度, CC) 精确相等」
    ("memory/write.py", "save_memory", None),
    ("memory/retrieve.py", "search_memories", None),
    ("application/chat_service.py", "_run_agent_core", None),
)
FROZEN_VALUES = {
    # save_memory 批 8 实测 188 行/CC 11（批7 264/33、原 290/45）。钉死它：既要防"扫描面塌了/口径漂移"，
    # 也要防"有人把地板删了凑绿"——改 HOT_RATCHET 那一行不会让这一把跟着变绿。
    ("memory/write.py", "save_memory"): (188, 11),
    # search_memories 批 14 实测 197 行/CC 13（批13 240/34、批12 288/59、批11 331/67、批10 372/81）。同理钉死。
    ("memory/retrieve.py", "search_memories"): (197, 13),
    # _run_agent_core 批 18 实测 164 行/CC 22（批17 252/48、批16 369/72、原 417/82）。300 长度地板
    # 随批 17 拆到 252 失效，改同一模式：地板 None + 精确冻结钉（164 仍 > 扫描地板 LEN_MAX=150，
    # 故仍占全库超标名额，OVER_MAX 不动）。
    ("application/chat_service.py", "_run_agent_core"): (164, 22),
}


def judge_floor(got: dict, pins: tuple) -> list:
    """纯函数：长度地板 / 冻结值钉。返回违规说明（空列表＝绿）。不读文件、不读时钟。"""
    out = []
    for rel, name, floor in pins:
        cur = got.get((rel, name))
        if cur is None:
            out.append("%s::%s 不在扫描面上（空扫／改名／路径写错？）" % (rel, name))
            continue
        if floor is None:
            want = FROZEN_VALUES[(rel, name)]
            if cur != want:
                out.append("%s::%s 实测 %d/CC %d ≠ 冻结钉 %d/%d＝口径漂移或有人改测量"
                           % (rel, name, cur[0], cur[1], want[0], want[1]))
        elif cur[0] < floor:
            out.append("%s::%s 长度掉到 %d＜%d＝口径或扫描不对" % (rel, name, cur[0], floor))
    return out


def test_热点函数只许变短不许变长():
    assert judge_hot(_scan(), HOT_RATCHET) == [], judge_hot(_scan(), HOT_RATCHET)


def test_全库超标函数总数不许上升():
    over = [1 for (_rel, _name), (ln, cc) in _scan().items() if ln > LEN_MAX or cc > CC_MAX]
    assert judge_count(len(over), OVER_MAX) == "", judge_count(len(over), OVER_MAX)


def test_棘轮判据本身有牙():
    """合成用例直接喂假数据：变长／变短／消失／超标数上升，四种都必须被说出来。"""
    good = {("a.py", "f"): (100, 20)}
    tab = [("a.py", "f", 100, 20)]
    assert judge_hot(good, tab) == []
    assert judge_hot({("a.py", "f"): (101, 20)}, tab), "函数变长必须红"
    assert judge_hot({("a.py", "f"): (100, 21)}, tab), "复杂度变高必须红"
    assert judge_hot({("a.py", "f"): (90, 20)}, tab), "拆短了也要红（催下调基线）"
    assert judge_hot({}, tab), "热点行找不到（拆掉／改名）必须红，不许静默放行"
    assert judge_count(45, 44), "超标总数上升必须红"
    assert judge_count(43, 44), "超标总数下降也必须红（把基线记下来）"
    assert judge_count(44, 44) == ""


def test_扫描器必须真的在扫():
    """反向钉：本仓已被"看着在跑其实空扫"咬过两次（C1 子进程解码、C2 cat-file 空扫）。"""
    got = _scan()
    assert len(got) >= FUNC_FLOOR, "只扫到 %d 个模块级函数＝扫描面塌了（路径或解析出错）" % len(got)
    for rel, name, _l, _c in HOT_RATCHET:
        assert (rel, name) in got, "热点表里的 %s::%s 不在扫描面上" % (rel, name)
    # save_memory 的 300 长度地板已随批 6 拆短失效（批 7 再拆后实测 264，批 8 拆到 188）：改成
    # 「存在性＋冻结值精确对照」。search_memories 的 300 地板随批 12 拆到 288、批 13 拆到 240、
    # 批 14 再拆到 197 后同样失效，改为同一模式（并存两条，见 FROZEN_VALUES）。_run_agent_core 的
    # 300 地板随批 17 拆到 252 后同样失效，第三把也改 None＋冻结钉（三条并存）。
    # floor 不清零、总量/存在性反向钉都不动。
    assert judge_floor(got, LENGTH_PINS) == [], judge_floor(got, LENGTH_PINS)


def test_冻结值钉对空扫和伪短函数都有牙():
    """反例必须被说红：空扫（扫不到）与「伪短 save_memory」都不许悄悄放行。"""
    ok = {("memory/write.py", "save_memory"): (188, 11),
          ("memory/retrieve.py", "search_memories"): (197, 13),
          ("application/chat_service.py", "_run_agent_core"): (164, 22)}
    assert judge_floor(ok, LENGTH_PINS) == []
    assert judge_floor({}, LENGTH_PINS), "空扫（一个都没扫到）必须红"
    short = {**ok, ("memory/write.py", "save_memory"): (10, 1)}
    assert judge_floor(short, LENGTH_PINS), "伪短 save_memory（10 行/CC1）必须红，不许当绿放过"
    shrunk = {**ok, ("memory/retrieve.py", "search_memories"): (120, 20)}
    assert judge_floor(shrunk, LENGTH_PINS), "search_memories 掉到 120 行必须红"
    # 批 17 同款：_run_agent_core 的 300 地板改成冻结钉后，伪短/CC 漂移同样必须有牙
    core_short = {**ok, ("application/chat_service.py", "_run_agent_core"): (12, 1)}
    assert judge_floor(core_short, LENGTH_PINS), "_run_agent_core 伪短（12 行/CC1）必须红"
    core_cc = {**ok, ("application/chat_service.py", "_run_agent_core"): (164, 21)}
    assert judge_floor(core_cc, LENGTH_PINS), "_run_agent_core 的 CC 与冻结钉不符必须红"
    cc_shifted = {**ok, ("memory/write.py", "save_memory"): (188, 10)}
    assert judge_floor(cc_shifted, LENGTH_PINS), "CC 与冻结值不符也必须红（不是只钉长度）"
    renamed = {k: v for k, v in ok.items() if k != ("memory/write.py", "save_memory")}
    assert judge_floor(renamed, LENGTH_PINS), "热点函数改名/消失必须红"
