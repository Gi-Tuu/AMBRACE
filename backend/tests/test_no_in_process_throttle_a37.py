# -*- coding: utf-8 -*-
"""A41（A37 批 4）静态守卫：发送/节流路径上的模块级可变容器必须登记理由。

来源：审计报告 `AMBRACE_异步通道现状同步审计_20261007.md` 不变量 **I8**——
「节流/去重状态不得只放进程内（重启或被 supervisor 重建即失效或突发）」。批 4 把四处判据迁到
`periodic_state` 台账 / 落盘队列，**剩下的**进程内容器一律要在本文件的白名单里逐条写清
「为什么可以留」。任何人新加一个模块级 dict/set/list/Lock，这里立刻变红。

判据口径（刻意窄、刻意可解释）：
1. 只扫 :data:`SCAN_FILES` 这份**显式清单**（发送口 + 节流口 + 台账本体 + 本批改过的模块），
   不做全仓扫描——全仓扫会把「常量表 / 路由注册表」一并算成噪声，噪声一多守卫就没人维护；
2. 只算**可变容器**：`dict` / `set` / `list` 字面量，以及 `dict() / set() / list() / defaultdict()
   / OrderedDict() / Counter() / deque()` 与 `asyncio.Lock() / Event() / RLock() / Condition()`；
   `frozenset()` / 元组 / 常量数字不算（改不动就不是状态载体）；
3. 命中即须登记，登记必须带**实质理由**（长度下限 + 不许出现 TODO/占位），白名单与实况**双向对齐**
   （登记了却找不到＝蒙混；找到了没登记＝新增）。

另外钉两条回归：迁移走的容器不许回来；已迁的模块必须真的在调台账／落盘队列的判据 API。

纪律：纯 AST + 文本，不 import 业务模块（零副作用、零建库），单文件毫秒级。
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]      # backend/

# 只算「可能是状态载体」的构造（可变容器 + 并发原语）
CONTAINER_CALLS = frozenset({
    "dict", "set", "list", "defaultdict", "OrderedDict", "Counter", "deque",
    "Lock", "Event", "RLock", "Condition",
})

# 显式扫描面：发送口 / 节流口 / 台账本体 / 本批改过的模块（路径相对 backend/）
SCAN_FILES = (
    "app/scheduling/periodic_state.py",       # 台账本体（持久判据的落盘处）
    "app/scheduling/scheduler.py",            # 所有定时通道的调度口
    "app/scheduling/arbiter.py",              # 30 秒 tick 的仲裁口（发送主闸）
    "app/scheduling/state_triggers.py",       # C09 状态触发（延迟发送）
    "app/scheduling/prospective_intent.py",   # C01/C22 承诺与话题
    "app/application/push_service.py",        # C42 推送（本批迁台账）
    "app/memory/extractor.py",                # C44 萃取（本批迁落盘队列）
    "app/memory/extract_queue.py",            # C44 的新载体（落盘队列本体）
    "app/memory/summary.py",                  # C34 置顶摘要/身份画像（本批改淘汰口径）
    "app/life/life_tick.py",                  # C36 生活拍（本批迁台账计数）
    "app/life/life_loop.py",                  # C37 生活环文案配额（本批迁台账计数）
    "app/events/bus.py",                      # C43 事件总线（本批补投递侧 TTL）
    "app/events/facts.py",                    # 新鲜度窗口的单一来源
    "app/events/world_state.py",              # 八路只读快照（本批补 fresh_until）
    "app/agent/workspace_projection.py",      # 快照 → Workspace 的投影口
    "app/application/working_state_service.py",
    "app/domain/proactivity/pacing.py",       # 频控常量与配额表
    "app/utils/async_tasks.py",               # spawn_background 的任务注册表
)

# 白名单：文件 → 容器名 → 为什么可以留在进程内（I8 的逐条交代）
WHITELIST: dict[str, dict[str, str]] = {
    "app/scheduling/periodic_state.py": {
        "_LOCKS": "按 key 的进程内写锁，只保护同一文件的一次读-改-写；权威状态在 JSON 台账里，"
                  "重启丢锁不丢判据（锁本身不是节流状态）",
        "_LOCAL_STAMPS": "台账写盘失败时的内存兜底副本，读侧仍以文件为准（counter/window 同规则）；"
                         "它的存在方向是『多记住一点』而不是『少记住』，不会因重启复活成 0",
        "_LOCAL_RETRY_AT": "同上：写失败时退避闸门的内存兜底，落盘成功后即弹出",
        "_LOCAL_COUNTERS": "本批新增的计数兜底（C36 生活拍 / C37 文案配额），与 _LOCAL_STAMPS 同规则；"
                           "真判据已落台账，重启后 counter() 取 max(文件, 内存) ⇒ 不复活为 0",
        "_LOCAL_MARKS": "本批新增的滑窗戳兜底（C42 推送），写盘失败时仍记账 ⇒ 宁可少放行也不补发",
    },
    "app/scheduling/arbiter.py": {
        "_rejected_log_cache": "rejected 触发**日志**的节流（REJECTED_LOG_THROTTLE_SECONDS），"
                               "只影响留痕密度，不参与是否发送；重启丢了最多多记几行日志",
        "_OUTREACH_SEND_TRACE": "A4 批 3 M0 观测暂存（intent/tier/materials 跨调用栈送到留痕点），"
                                "候选、频控、发送条数一律不读它；摘除即回到逐字节旧行为",
    },
    "app/scheduling/state_triggers.py": {
        "TREND_BOOST": "趋势增强的阈值表（只读常量），运行时不改写",
        "_TREND_MOVE": "趋势判据的位移常量表（模块加载期构造，运行期只读，不是节流状态）",
        "_last_snapshot": "八维趋势检测的上次采样快照：本 tick 现算现写、下 tick 比对；"
                          "重启后首轮不命中（需两次采样）⇒ 方向偏保守，且它不做发送节流、不补发历史",
        "RULES": "规则注册表（模块加载期构造、运行期只读）",
    },
    "app/scheduling/prospective_intent.py": {
        "_CUE_VARIANTS": "cue 词形变体表（只读常量表，B.3.3 收窄口径），不做开放式语义匹配",
    },
    "app/memory/extractor.py": {
        "_catchup_lock": "catchup 全表扫描的进程内重入保护（一次只允许一轮补采）；"
                         "节流判据与队列都已落盘 ⇒ 重启丢锁只会重跑一轮，不会丢片段",
    },
    "app/life/life_tick.py": {
        "_STEP": "强度 → 活动步长的常量映射表（判据本身已迁台账 _next_tick_ordinal）",
    },
    "app/events/facts.py": {
        "_TRANSIENT_FRESH_HOURS": "瞬时谓词新鲜窗（**单一来源**常量表，world_state 与 bus 都从这里取值）",
        "CURATED_KINDS": "策展 kind 集合（只读判定表，运行期不改写）",
        "TRANSIENT_PREDICATES": "瞬时谓词集合（只读判定表，新鲜窗判据的输入而非状态）",
        "_DURABLE_STATUS_PREDICATES": "长期状态谓词集合（只读分类表，不记任何时刻）",
        "_IDENTITY_PREDICATES": "身份谓词集合（只读分类表，不记任何时刻）",
        "_CLAUSE_BOUNDARY_CHARS": "子句切分标点集合（纯文本处理常量，与发送/节流无关）",
    },
    "app/events/world_state.py": {
        "AUTHORITATIVE_SOURCE": "八路权威源标签表（自述元数据，快照里回读给调用方，只读）",
        "AS_OF_BASIS": "逐路 as_of 口径登记表（自述元数据，只读）",
        "ROUTE_READERS": "逐路读点 file:line 登记表（诊断用，只读）",
        "_VIEW_FRESH_PREDICATE": "本批新增：四张瞬时视图 → 所借 facts 谓词的映射表（窗口数值仍取自 facts）",
        "_ROUTE_READERS_IMPL": "路名 → 读函数的分派表（模块加载期构造，运行期只读）",
        "__all__": "模块导出清单（只读元数据，不是状态载体）",
    },
    "app/agent/workspace_projection.py": {
        "DEFER_REASONS": "本单刻意留空字段的自述表（读数以该形式自证「是边界不是遗漏」），只读",
        "__all__": "模块导出清单（只读元数据，不是状态载体）",
    },
    "app/application/working_state_service.py": {
        "_eval_locks": "同角色评估串行化锁（W4，消「节流检查→LLM→写入」的并发双写窗口）；"
                       "节流判据读库里最新一行的 created_at（本就是持久事实），重启丢锁不复活配额",
    },
    "app/domain/proactivity/pacing.py": {
        "TYPE_DAILY_LIMITS": "每类型日限额常量表；实际计数走 arbiter 的**已发送**读数，不是进程内桶",
        "TYPE_MIX_COUNTED_TYPES": "混排计入类型表（只读常量；计数走 arbiter 的已发送读数，不落进程内）",
    },
    "app/utils/async_tasks.py": {
        "_BG_TASKS": "后台任务注册表（防 GC 回收）；任务本体在进程退出时本就该消失，"
                     "其持久判据已由本批迁到台账/落盘队列，不靠这张表恢复",
    },
}

# 迁移走的进程内态：这些名字再出现在扫描面上就是回归（A41 的四个落点）
MIGRATED_AWAY = {
    "_pending": "app/memory/extractor.py（C44 配对队列 → app/memory/extract_queue.py 落盘）",
    "_pending_ids": "app/memory/extractor.py（C44 在途占位 → 落盘队列的 inflight 桶）",
    "_rate_buckets": "app/application/push_service.py（C42 滑窗 → periodic_state 的 marks）",
    "_tick_count": "app/life/life_tick.py（C36 拍计数 → periodic_state 的 counter）",
    "_llm_copy_counts": "app/life/life_loop.py（C37 文案配额 → periodic_state 的 counter）",
}

# 已迁模块必须真的在调台账／队列的判据 API（正向钉住「迁移」而不是「换个 dict」）
LEDGER_CALLS = {
    "app/application/push_service.py": ("window_count", "window_add"),
    "app/life/life_tick.py": ("bump_counter",),
    "app/life/life_loop.py": ("counter", "bump_counter"),
    "app/memory/extractor.py": ("is_due", "mark_done"),
}


def _containers(rel_path: str) -> list[tuple[str, str]]:
    """该文件模块级的可变容器（名字, 构造名）。"""
    path = ROOT / rel_path
    assert path.is_file(), f"扫描面里的文件不存在（路径漂移了就先修清单）：{rel_path}"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    out: list[tuple[str, str]] = []
    for node in tree.body:
        names: list[str] = []
        value = None
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
            value = node.value
        if value is None:
            continue
        if isinstance(value, ast.Dict):
            kind = "dict"
        elif isinstance(value, ast.Set):
            kind = "set"
        elif isinstance(value, ast.List):
            kind = "list"
        elif isinstance(value, ast.Call):
            func = value.func
            kind = getattr(func, "attr", None) or getattr(func, "id", None) or ""
            if kind not in CONTAINER_CALLS:
                continue
        else:
            continue
        for name in names:
            out.append((name, kind))
    return out


def _assigned_names(rel_path: str) -> set[str]:
    """该文件模块级赋过值的全部名字（给 MIGRATED_AWAY 回归用）。"""
    tree = ast.parse((ROOT / rel_path).read_text(encoding="utf-8-sig"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _all_found() -> dict[str, list[tuple[str, str]]]:
    return {rel: _containers(rel) for rel in SCAN_FILES}


def test_扫描面里的文件都在树上():
    missing = [rel for rel in SCAN_FILES if not (ROOT / rel).is_file()]
    assert missing == [], f"扫描面漂移（先修清单再谈守卫）：{missing}"


def test_模块级可变容器必须逐条登记理由():
    unregistered = []
    for rel, found in _all_found().items():
        table = WHITELIST.get(rel, {})
        for name, kind in found:
            if name not in table:
                unregistered.append(f"{rel}:{name}（{kind}）")
    assert unregistered == [], (
        "新增/残留的进程内状态载体，先回答 I8：它是不是节流/去重判据？"
        "是 ⇒ 迁 periodic_state 台账或落盘队列；确实可以留 ⇒ 在白名单写清理由："
        f"{unregistered}"
    )


def test_白名单不许登记不存在或已消失的条目():
    found = {rel: {n for n, _k in items} for rel, items in _all_found().items()}
    stale = [f"{rel}:{name}" for rel, table in WHITELIST.items()
             for name in table if name not in found.get(rel, set())]
    assert stale == [], f"登记了实况里没有的名字（迁移完成后请把条目一起删掉）：{stale}"


def test_登记理由必须实质不许占位():
    weak = [f"{rel}:{name}={reason!r}" for rel, table in WHITELIST.items()
            for name, reason in table.items()
            if len(reason) < 14 or "TODO" in reason or "待补" in reason or reason == name]
    assert weak == [], f"理由写得太敷衍（I8 的交代不是走形式）：{weak}"


def test_迁移走的进程内容器不得复活():
    for rel in SCAN_FILES:
        names = _assigned_names(rel)
        for banned, note in MIGRATED_AWAY.items():
            assert banned not in names, (
                f"{rel} 里又出现了 {banned}（I8 违例）。该状态的正确归宿：{note}"
            )


def test_已迁模块必须真的调用台账判据API():
    for rel, apis in LEDGER_CALLS.items():
        src = (ROOT / rel).read_text(encoding="utf-8-sig")
        for api in apis:
            assert f"_ledger.{api}(" in src, (
                f"{rel} 没在调 periodic_state.{api} —— 迁移必须落到台账，不许换个进程内 dict 交差"
            )


def test_落盘队列是本批唯一的配对载体():
    """extractor 的配对队列必须是 app/memory/extract_queue.py（跨重启不丢的结构保证）。"""
    src = (ROOT / "app/memory/extractor.py").read_text(encoding="utf-8-sig")
    assert "from app.memory import extract_queue as _queue" in src
    assert "_queue.add(" in src and "_queue.take(" in src
    queue_src = (ROOT / "app/memory/extract_queue.py").read_text(encoding="utf-8-sig")
    assert "_QUEUE_FILE" in queue_src, "队列必须落盘（进程内 list 就是旧违例本身）"
