"""memory.write（F4 拆分，2026-08-31 自 service.py 迁入）。

接缝已摘除（2026-09-02）：改为顶部/函数顶显式 import（text_embedding/add_memory/
find_similar_memory/bm25_invalidate/async_session_factory 等可被 monkeypatch(service, ...)
的名字于函数顶延迟 import 自 app.memory.service，调用时解析；其余稳定名字模块级 import）。
不再经 _sync_seams 把 service 命名空间同步进 globals。
"""
import json

from sqlalchemy import select

from app.models.memory import Memory
from app.memory.perception_tier import (
    PERCEPTION_SOURCE,
    SNAPSHOT_MAX_ROWS,
    SNAPSHOT_WINDOW_MINUTES,
    has_snapshot_tag,
    snapshot_overlap,
)
from app.memory.service import (
    _initial_strength,
    _logger,
    _normalize_importance,
    _now_naive,
)


def _write_dedup_active_only_on() -> bool:
    """P1-2（2026-09-28）：写路径查重只认现行（active）——新 flag，默认关（关＝逐字节旧行为）。"""
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("write_dedup_active_only", False))
    except Exception:
        return False


def _perception_tag_on() -> bool:
    """批 0-2 M1a（2026-09-28）：感知来源打标 / 跨来源禁合并总闸，默认关（关＝逐字节旧行为）。

    任何异常回落 False——开关读不到就等于没接线，绝不因为读开关而改变写入结果。
    """
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("perception_source_tag", False))
    except Exception:
        return False


# 断点 #5（2026-09-29）→ P0 第 4 步（2026-09-29）：感知派生条的归属**落库列也写这个值**。
# 第 1~3 步它只用于准入判定与回执留痕（该列刻意留空），结果「perception」从未真正可 SQL 查询；
# 本步起感知条把判定结果写进 memories.speaker_type（列宽 String(10)，恰好容纳 10 字符）。
PERCEPTION_SENDER = "perception"


def _is_perception_source(source) -> bool:
    """来源是否感知派生（纯判定）：归一比较，脏输入（非字符串 / None / 空白）一律 False。"""
    return isinstance(source, str) and source.strip().lower() == PERCEPTION_SOURCE


def perception_actor_column(spk_type, source):
    """P0 第 4 步·写入侧归一（纯函数、零 IO）：感知条把判定结果写进 ``speaker_type`` 列。

    只填「判定为空」的那一档（``None → perception``）：
    - 调用方显式给的归属（``user`` / ``character`` / ``system`` / ``tool``）原样返回；
    - 非感知条（``source`` 不是 perception）逐字节原样返回——本函数对它们恒等。

    为什么只在 ``None`` 时补：判据链里感知排在显式归属之前（``_resolve_admission_sender``），
    但落库列若覆盖显式入参会让「调用方说的话」凭空消失，且双跑判据要求差异**只能是**
    ``NULL→perception``（越界的差异一律视为回归）。
    """
    return PERCEPTION_SENDER if spk_type is None and _is_perception_source(source) else spk_type


def _cross_source_merge_blocked(incoming_source, candidate_source) -> bool:
    """跨来源禁合并（批 0-2 方案 §2.2 禁令 3 的收窄版，纯判定 + flag 门控）。

    口径（**只在感知 ↔ 非感知之间禁止**，不扩大为「必须同来源才准合并」）：
    候选行与待写行来源不同，且其中一方是 ``perception`` ⇒ True（跳过该候选，不并条）。
    同来源照旧合并；两边都不是感知照旧合并；flag 关恒 False＝逐字节旧行为。

    为什么必须与打标同批生效：感知条若被「顺手并」进一条聊天记忆，它就``以 chat 名义获得长期
    身份``，来源证据从此丢失、打标白做（方案风险 R2）。
    """
    if not _perception_tag_on():
        return False
    a = (incoming_source or "").strip().lower()
    b = (candidate_source or "").strip().lower()
    return a != b and PERCEPTION_SOURCE in (a, b)


async def _recent_snapshots(db, user_id: int | None) -> list[tuple[str | None, str]]:
    """取「本轮感知语料」：该用户最近快照的 (来源通道, 正文) 列表（写路径打标的比对素材）。

    窗口/条数**只用** ``app/memory/perception_tier`` 的常量（与注入侧 ``app/device/port.py:37-38``
    同源，对齐断言见 tests/test_perception_tier.py），本函数不写魔法数；复用调用方已开的会话，
    不另开 session。异常不在此吞（由调用方 fail-open）。

    M2 起同时取 ``source``（值空间 accessibility / clipboard / media，见 ``models/device``）：
    打标命中时把**命中的那条快照的通道**记进记忆的 ``sub_type``，用于按通道观测与后续粒度。
    """
    if user_id is None:
        return []
    from datetime import timedelta

    from app.models.device import PhoneSnapshot

    cutoff = _now_naive() - timedelta(minutes=SNAPSHOT_WINDOW_MINUTES)
    rows = (await db.execute(
        select(PhoneSnapshot.source, PhoneSnapshot.content)
        .where(PhoneSnapshot.user_id == user_id, PhoneSnapshot.created_at >= cutoff)
        .order_by(PhoneSnapshot.created_at.desc(), PhoneSnapshot.id.desc())
        .limit(SNAPSHOT_MAX_ROWS)
    )).all()
    return [(r[0], r[1]) for r in rows if r[1]]


def _matched_snapshot_channel(corpus: list[tuple[str | None, str]], content: str) -> str | None:
    """命中的那条快照的通道（纯函数，不查库）：正文按主判据命中的第一条；
    仅标签残留命中（无一条正文重合）时取**最新一条**的通道——语料已按时间倒序。"""
    for src, text in corpus:
        if snapshot_overlap(content, [text]):
            return src
    return corpus[0][0] if corpus else None


# ── 批 0-8 写入来源链条（provenance）标注（2026-09-28，雷达 13 ＋ 簇5）──────────────
# 目标：把「这条记忆是**怎么来的**」标全，为后续治理与评测留证据。硬约束＝**不建表、不加列、
#       不改任何判据与阈值**——既有判据一个字不动，产物只进 `memory_write_receipts.detail_json`。
#
# 为什么不能复用既有列承载（派单要求「能承载就复用」的核对结论）：
#   - ``sub_type``：值空间是「槽位 / 画像子类型」，且被 ``core._core_category`` 当作**晋升 is_core
#     的判据**（identity/preference/commitment 低门槛那条路）；往里塞生产者类别会直接改变晋升结果。
#   - ``source``：检索/注入/隔离都在读它（``perception_tier.is_quarantined``），改写同样伤语义。
#   - ``node_type`` / ``chain_id`` / ``why_it_matters``：各有既有含义（链结构 / 意义提炼）。
#   ⇒ 结论：走「纯回执增强」——在**已有的那条 create 回执**上加一个 ``provenance`` 键
#     （不新增行、不新增列；merge 回执同理补记——否则「并进来的是什么」手上只有 kind/sim）。
#
# 为什么不新增 flag（派单允许「零语义变化可不加，需写明理由」）：
#   1) 只加 JSON 键 ⇒ ``memories`` 行一字未动，检索 / 注入 / 查重 / 晋升判据与阈值零变化；
#   2) 发射口 ``emit_memory_receipt`` 首行即受既有 flag ``memory_write_receipt``（**默认关**）闸控，
#      关＝零写入 ⇒ 默认已是逐字节旧行为，再加一层闸只会多出「两个开关」的口径漂移；
#   3) 本单文件隔离不允许改 ``AGENT_FLAGS`` 硬编码默认表 / ``application/flag_catalog.py``，
#      新键登记不进默认表与目录、运行期也热切不了（与 M4 准入闸门的处置同一口径）。
#
# 取值空间**以生产库实测为准**（2026-09-28 只读 ``select source, sub_type, count(*) … group by``，
# 前几名：chat/extracted 4183、chat/relationship 1707、life/life_event 1232、moment/moment 806、
# chat/status 778、game/game_summary 726、chat/(空) 375、diary/diary 279、pet/(空) 253、
# system/(空) 84、group/group 78、summary/summary 48、global_sync、privacy_request、reflection…）。
#
# 只读查询口径（留证怎么用，全部 ``mode=ro``，不写库）：
#   -- 某条记忆的来路（route / 生产者 / 证据锚点 / 本轮生效的改写）
#   SELECT r.action, r.reason, r.detail_json
#     FROM memory_write_receipts r WHERE r.memory_id = ? ORDER BY r.id;
#   -- 按来源链条聚合看占比（SQLite json_extract，detail 里那条键就叫 provenance）
#   SELECT json_extract(r.detail_json, '$.provenance.route') AS route, count(*)
#     FROM memory_write_receipts r WHERE r.action = 'create' GROUP BY route ORDER BY 2 DESC;
#   -- 晋升审计（哪条记忆、什么时候、因为什么进了核心）见 memory/core.py 同名说明块
_PROVENANCE_VERSION = 1
_PROVENANCE_ROUTE_KEYS = (
    "perception", "extracted", "model_marked", "self_narrative",
    "digest", "system_event", "cross_role_sync", "unregistered",
)
# AI 自述族：角色自己产出的内容（日记 / 自述 / 生活引擎 / 复盘 / 朋友圈 / 状态与状态触发）
_SELF_NARRATIVE_SOURCES = frozenset({
    "diary", "bio", "life", "reflection", "moment", "status", "state_eval", "state_trigger",
})
# 摘要族：对既有记忆 / 对局的二次概括（置顶摘要、游戏小结）
_DIGEST_SOURCES = frozenset({"summary", "game"})
# 离散系统事件族：一次一件事、不参与语义合并（宠物、群聊流水、隐私审批回执）
_SYSTEM_EVENT_SOURCES = frozenset({"pet", "group", "system", "privacy_request"})
# 跨角色事实同步（memory/cross_char_sync.py：用户级事实镜像到各角色）
_CROSS_SYNC_SOURCES = frozenset({"global_sync"})


def _extractor_slot_names() -> frozenset:
    """提取器可能写进 ``sub_type`` 的槽名——单一事实源取 ``user_facts.MUTABLE_SLOTS``（防两处漂移）。

    读不到就返回空集：归类面缺槽名时，这些行只会落到 ``extracted``（chat 有子类型 ⇒ 仍算提取器），
    不会误判成别的生产者，属可接受的粗化。
    """
    try:
        from app.memory.user_facts import MUTABLE_SLOTS
        return frozenset(MUTABLE_SLOTS)
    except Exception:
        return frozenset()


def _provenance_route(source, sub_type) -> str:
    """「怎么来的」＝生产者类别（纯判定，只读入参，不碰库、不改任何写入取值）。

    ``chat`` 一支按 ``sub_type`` 有无劈开：**有子类型**＝提取器（``extractor.extract_single`` 的
    槽位 / 画像子类型 / RELATIONSHIP / meta_guard 都会写子类型）；**空**＝【记忆：】标记路径与
    关键词规则 ``extract_info_from_message``（两者在今天**无法区分**，同一落点：source=chat 且
    sub_type 为空——如实并成 ``model_marked``，不硬造第三个桶）。
    """
    s = (source or "").strip().lower()
    if s == PERCEPTION_SOURCE:
        return "perception"
    if s in _SELF_NARRATIVE_SOURCES:
        return "self_narrative"
    if s in _DIGEST_SOURCES:
        return "digest"
    if s in _SYSTEM_EVENT_SOURCES:
        return "system_event"
    if s in _CROSS_SYNC_SOURCES:
        return "cross_role_sync"
    if s == "chat":
        return "extracted" if (sub_type or "").strip() else "model_marked"
    # 其余（含 source 为空）＝**未登记**：新增写入方自动暴露在这里，而不是被塞进某个既有桶蒙混过去
    return "unregistered"


def _provenance_evidence_kind(source, source_id, derived_from_ids) -> str:
    """证据锚点类型：这条凭什么落库（可回溯到哪个对象上）。"""
    s = (source or "").strip().lower()
    if s == PERCEPTION_SOURCE:
        return "phone_snapshot"
    if source_id is not None:
        return "chat_message" if s == "chat" else "record"
    if derived_from_ids:
        return "derived_memory"
    return "none"


def _derive_provenance(*, source, sub_type, memory_type, actor, source_id,
                       epistemic_status, derived_from_ids=None, node_type=None,
                       gates=None, stored_sub_type=None) -> dict:
    """组装来源链条（纯函数，零 IO）。

    只新增键、不解释键：``route``（怎么来的）＋ ``actor``（谁产生的）＋ ``evidence``（凭什么）＋
    ``gates``（本轮真实生效的改写）＋ ``stored``（落库时那几列的原值，便于一条回执看全链条）。
    ``actor`` 取**准入闸门判定后**的归属（与 ``memories.speaker_type`` 可以不同——闸门解析出的
    归属今天不落任何列，这里顺带留一份）。``stored_sub_type`` 传入行内存的 sub_type，
    以便看出 L4 计划回忆化把 ``extracted`` 改名成 ``plan`` 这类改写。
    """
    _slots = _extractor_slot_names()
    _st = (sub_type or "").strip()
    return {
        "v": _PROVENANCE_VERSION,
        "route": _provenance_route(source, sub_type),
        "slot": _st if _st in _slots else None,
        "actor": (actor or "unset"),
        "evidence": {
            "kind": _provenance_evidence_kind(source, source_id, derived_from_ids),
            "source_id": source_id,
            "derived_from_ids": list(derived_from_ids or []),
            "node_type": node_type,
        },
        "gates": list(gates or []),
        "stored": {
            "source": source,
            "sub_type": stored_sub_type if stored_sub_type is not None else sub_type,
            "memory_type": memory_type,
            "epistemic_status": epistemic_status,
        },
    }


def _write_provenance(*, source, sub_type, memory_type, actor, source_id,
                      epistemic_status, derived_from_ids=None, node_type=None,
                      gates=None, stored_sub_type=None) -> dict | None:
    """来源链条留痕的**失败隔离**外壳：推导万一出错 ⇒ 返回 None（照旧不带该键）。

    留痕永远不得改变写入结果——判据、取值、返回值一律不动。
    """
    try:
        return _derive_provenance(
            source=source, sub_type=sub_type, memory_type=memory_type, actor=actor,
            source_id=source_id, epistemic_status=epistemic_status,
            derived_from_ids=derived_from_ids, node_type=node_type,
            gates=gates, stored_sub_type=stored_sub_type,
        )
    except Exception as _e:
        _logger.warning("provenance derive failed (fail-open): src=%s err=%s", source, _e)
        return None


def _merge_receipt_detail(kind, target, *, source, sub_type, memory_type, source_id,
                          speaker_type, epistemic_status, derived_from_ids, node_type,
                          content, extra: dict | None = None) -> dict:
    """并入（merge）回执的补记（批 0-8）。

    旧回执只带 ``kind`` / ``sim``：看不出「并进来的是哪句话、由谁经哪条路写的、并进了哪一条」——
    并入是新内容**唯一**的留痕现场（不产生新行，落库后那句话就消失了）。这里补三段，既有键一字不动：
    ``incoming``＝未落库那句话的写法参数；``into``＝被并到的旧行的来源三列（跨来源并条的证据面）；
    ``provenance``＝那句话自己的来源链条（``stored`` 即 incoming，因为本路没有新行可存）。
    """
    _det = {"kind": kind}
    if extra:
        _det.update(extra)
    _det["incoming"] = {
        "content_excerpt": (content or "")[:60],
        "memory_type": memory_type,
        "source": source,
        "sub_type": sub_type,
        "speaker_type": speaker_type,
        "epistemic_status": epistemic_status,
    }
    if target is not None:
        _det["into"] = {
            "memory_id": target.id,
            "source": target.source,
            "sub_type": target.sub_type,
            "memory_type": target.memory_type,
        }
    _prov = _write_provenance(
        source=source, sub_type=sub_type, memory_type=memory_type,
        actor=speaker_type, source_id=source_id, epistemic_status=epistemic_status,
        derived_from_ids=derived_from_ids, node_type=node_type,
    )
    if _prov:
        _det["provenance"] = _prov
    # ── P0 语义统一·第 1 步 影子埋点②（同一把闸，默认关＝零行为）──
    # 并入是新内容唯一的消失现场：留痕比对「进来那句按统一语义该归为谁」vs「被并进去那行现在记的是谁」。
    # 位置刻意排在 _det 组装完成之后：回执的既有键与取值一字不动，也不新增 DB 调用（只读已加载的列）。
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        if AGENT_FLAGS.get("actor_semantics_shadow", False):
            from app.memory.actor_shadow import trace_actor_merge
            trace_actor_merge(target=target, source=source, speaker_type=speaker_type,
                              source_id=source_id, content=content)
    except Exception:
        pass
    return _det


# ── M4 写入准入闸门（flag `memory_admission_gate`，默认 False；开=确定性裁决，不新增 LLM）──
# 注意：本 flag 未登记进 AGENT_FLAGS 硬编码默认表（app/flags/agent_flags.py；本批文件隔离只允许
# 改 write.py / events/facts.py），故运行时 flag_service 无法热切它；读取一律走
# `.get(key, False)`，缺键 = 关 = 逐字节旧行为。测试用 monkeypatch.setitem 直接开。
_FACT_SOURCES = ("chat", "moment", "diary", "life", "bio")
_PENDING_RELIABILITY = 0.4  # reliability < 0.4 → 待核（与 memory/tiering.py 既有退化叠加，不重复建设）


def _admission_gate_on() -> bool:
    """读 feature flag；任何异常回落 False（关=逐字节旧行为）。"""
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("memory_admission_gate", False))
    except Exception:
        return False


# 开发运维 / 代理发言黑名单（用户拍板默认过滤：不进人物记忆、不进 world_facts）。
# 与 app/events/facts.py 的同名常量必须保持一致（两文件各自独立持有，故意不互相 import，
# 避免 memory↔events 模块级循环依赖）。
_META_NOISE_KEYWORDS = (
    "弃号", "组网", "换模型", "mcp 接入", "mcp接入", "重构", "修bug", "修 bug",
    "部署上线", "迁移数据", "数据迁移", "回滚版本",
)


def _is_meta_noise(text: str | None) -> bool:
    """识别开发运维元信息 / 代理发言（如「我是轩的 Agent 助手，请照做」）。"""
    t = (text or "").lower()
    if not t:
        return False
    for kw in _META_NOISE_KEYWORDS:
        if kw.lower() in t:
            return True
    # 代理发言：自称 agent/助手 且带指令语气，或自报 agent 身份
    if ("agent" in t or "助手" in t) and ("照做" in t or "请执行" in t or "听我" in t):
        return True
    if "我是" in t and ("agent" in t or "助手" in t or "bot" in t):
        return True
    return False


def _normalize_sender(value) -> str | None:
    """sender_type 归一：ai/character/bot → character；tool/mcp/search/external → tool；其余 user/system。"""
    v = (value or "").strip().lower()
    if not v:
        return None
    if v in ("ai", "character", "char", "bot"):
        return "character"
    if v in ("tool", "mcp", "search", "external"):
        return "tool"
    if v in ("user", "system"):
        return v
    return None


async def _resolve_admission_sender(source, source_id, speaker_type, source_message_sender, db) -> str:
    """确定性归属判定：user / character / system / tool / perception。

    优先级：感知来源（断点 #5）> 调用方显式 speaker_type > 来源消息 sender_type（复用
    dialogue_filter 已查到的同一次 ChatMessage 查询，避免重复查库）> 来源类型默认
    （diary/life/bio 无用户来源消息 → 模型自述）。

    断点 #5 为什么必须排在最前：屏幕内容不是用户亲口陈述，也绝不是角色自述。打标只改
    ``source``，调用方传进来的 ``speaker_type`` 与来源消息的 ``sender_type`` 都还可能是
    ``user``——判据放在后面就永远回落成「用户陈述」（且打标后 source 已不是 chat，
    连 diary/life/tool 那几档都命中不了，直接掉到兜底 ``return "user"``）。
    flag ``perception_source_tag`` 关 ⇒ 本分支整体不生效＝逐字节旧行为。
    """
    if _perception_tag_on() and _is_perception_source(source):
        return PERCEPTION_SENDER
    st = _normalize_sender(speaker_type)
    if st:
        return st
    ms = _normalize_sender(source_message_sender)
    if ms:
        return ms
    if source == "chat" and source_id is not None:
        try:
            from app.models.chat import ChatMessage
            msg = await db.get(ChatMessage, source_id)
            if msg is not None and msg.sender_type:
                s = _normalize_sender(msg.sender_type)
                if s:
                    return s
        except Exception:
            pass
    if source in ("diary", "life", "bio"):
        return "character"  # 无来源消息的模型推断 / 生活自述
    if source and str(source).startswith(("mcp", "tool", "search")):
        return "tool"
    return "user"


def admit_memory(source, sender_type, epistemic_status, reliability, memory_type):
    """M4 准入闸门确定性裁决（纯函数、零 IO，不新增 LLM）。

    返回 (最终 epistemic_status, 是否待核)：
    - 角色说的（character/ai）/ 无来源模型推断（日记、生活自述）→ 非 FACT（缺省 INFERRED）+ 待核；
    - 外部工具（MCP/搜索）→ UNVERIFIED（现状）；
    - 系统事件 → 保留抽取器标注，缺省按来源推断（不额外降级）；
    - 用户陈述（或未知归属，含感知派生 perception）→ 照旧（FACT），仅当 reliability < 0.4 时进待核。

    只降不升：已是更细的标注（PLANNED/FICTIONAL/INFERRED/UNVERIFIED）时保留不覆盖。
    待核语义复用既有 UNVERIFIED/INFERRED（不新增 status 枚举），差异只体现在「检索权重」与
    「是否可晋升核心」两处（核心侧由 save_memory 跳过 maybe_promote_core 承担）。
    """
    st = (sender_type or "").strip().lower()
    base = epistemic_status or ("FACT" if source in _FACT_SOURCES else "UNVERIFIED")
    if st in ("character", "ai"):
        return ("INFERRED" if base in ("", "FACT") else base), True
    if st in ("tool", "mcp", "search", "external"):
        return "UNVERIFIED", False
    if st == "system":
        return base, False
    try:
        _rel = None if reliability is None else float(reliability)
    except (TypeError, ValueError):
        _rel = None
    if _rel is not None and _rel < _PENDING_RELIABILITY:
        return ("UNVERIFIED" if base == "FACT" else base), True
    return base, False


async def save_memory(
    user_id: int,
    character_id: int,
    memory_type: str,
    content: str,
    title: str = "",
    importance: int = 2,
    sub_type: str | None = None,
    source: str | None = None,
    related_memory_id: int | None = None,
    source_id: int | None = None,
    group_id: int | None = None,  # 群聊归属（P3-3 按群节流；None=非群聊/旧数据）
    scope: str = "private",
    skip_dedup: bool = False,
    speaker_id: int | None = None,
    speaker_type: str | None = None,
    epistemic_status: str | None = None,
    reliability: float | None = None,  # M4 准入闸门：可靠度（<0.4 → 待核）；None=未评（按全信）
    chain_id: str | None = None,
    parent_id: int | None = None,
    node_type: str | None = None,
    derived_from_ids: list[int] | None = None,  # #70-C M2：本记忆派生自哪些记忆 id（默认 None -> '[]'）
):
    """保存带证据的记忆；仅同来源事件、同内容及同归属的重放保持幂等。
    相似、否定、不同事件和主体均独立保留。skip_dedup 显式跳过重放幂等。
    台词过滤丢弃返回 None。"""

    from datetime import timedelta
    from app.memory.service import (
        add_memory,
        async_session_factory,
        bm25_invalidate,
        text_embedding,
    )
    from app.memory.receipt import (
        emit_memory_receipt,
        ACTION_REJECT,
    )
    async with async_session_factory() as db:
        # 写入前过滤（2026-10-12 A50 批 6：机械抽出到同文件私有协程 `_prefilter_memory_write`）。
        # 元信息拦截／台词原文／逐字 AI 台词三条拒收的判据、reason 与 payload、日志行、异常边界逐字不变；
        # 调用点仍在 `async_session_factory` 的会话里、感知打标之前，await 点仍是本段唯一挂起点；
        # 拒收一律不写库、不 commit，直接退出本 async with（靠上下文退出回滚）。
        keep, _src_msg_sender = await _prefilter_memory_write(
            db=db, content=content, character_id=character_id, memory_type=memory_type,
            sub_type=sub_type, source=source, source_id=source_id,
            emit_memory_receipt=emit_memory_receipt, ACTION_REJECT=ACTION_REJECT,
        )
        if not keep:
            return None
        # ── 批 0-2 M1a：感知来源打标（flag `perception_source_tag`，默认关＝逐字节旧行为）──
        # 位置刻意排在「台词过滤 / 逐字拦截」之后：打标只改写来源归属，不得抢在既有拦截分支前面
        # 改变它们的生效条件。判据**只标注、不拒收**——误标的代价（用户真说过的话被隔离）不可接受，
        # 漏标的代价只是「少一条被隔离」，所以宁松勿紧、且任何异常一律 fail-open 按旧行为落库。
        _perception_tagged = False
        _source_before_tag = source
        _sub_type_before_tag = sub_type
        _speaker_type_before_tag = speaker_type
        _speaker_id_before_tag = speaker_id
        # 感知来源标注阶段（2026-10-12 A50 批 3：机械抽出到同文件私有协程
        # `_tag_perception_source`）。整段判据／改写字段／fail-open 回退／日志边界逐字不变；
        # 调用点仍在「台词过滤 + 逐字拦截」之后、写入前查重之前，await 点仍是本段唯一挂起点。
        source, epistemic_status, _perception_tagged, sub_type, speaker_type, speaker_id = (
            await _tag_perception_source(
                db=db, user_id=user_id, character_id=character_id, content=content,
                source=source, epistemic_status=epistemic_status, sub_type=sub_type,
                speaker_type=speaker_type, speaker_id=speaker_id,
                _perception_tagged=_perception_tagged,
                _source_before_tag=_source_before_tag, _sub_type_before_tag=_sub_type_before_tag,
                _speaker_type_before_tag=_speaker_type_before_tag,
                _speaker_id_before_tag=_speaker_id_before_tag,
            )
        )
        # Similarity is only retrieval evidence, never a reason to discard input.
        embedding = None

        # 新建记忆的准备阶段（2026-10-12 A50 批 4：机械抽出到同文件私有协程
        # `_prepare_created_memory`）。归属收口／准入闸门／影子埋点／Memory 构造／plan 标注
        # 逐字不变；调用点仍在写入前查重之后、`db.add(memory)` 之前，await 点仍是本段唯一挂起点，
        # `db.add`/flush/commit/refresh 与两把已验收 helper 在调用方不动。
        memory, pct, _spk_type, _spk, _epi, _pending, _sub_type_before_plan = (
            await _prepare_created_memory(
                db=db, timedelta=timedelta, user_id=user_id, character_id=character_id,
                memory_type=memory_type, content=content, title=title, importance=importance,
                sub_type=sub_type, source=source, related_memory_id=related_memory_id,
                source_id=source_id, group_id=group_id, scope=scope, speaker_id=speaker_id,
                speaker_type=speaker_type, epistemic_status=epistemic_status,
                reliability=reliability, chain_id=chain_id, parent_id=parent_id,
                node_type=node_type, derived_from_ids=derived_from_ids,
                _src_msg_sender=_src_msg_sender,
            )
        )
        from app.memory.write_identity import persist_candidate
        memory, created = await persist_candidate(db, memory, skip_dedup=skip_dedup)
        if not created:
            return memory

        # 新建记忆的回执尾段（2026-10-12 A50 批 2：机械抽出到同文件私有函数
        # `_emit_created_memory_receipts`）。三个原独立 try/except、pending→perception→create
        # 的顺序、来源链字段与取值逐字不变；调用点仍在 flush/commit/refresh 之后、Chroma
        # 写入之前，同步调用，不新增 await、不改事务边界与返回语义。
        _emit_created_memory_receipts(
            memory=memory, character_id=character_id, memory_type=memory_type,
            sub_type=sub_type, source=source, source_id=source_id,
            derived_from_ids=derived_from_ids, node_type=node_type,
            emit_memory_receipt=emit_memory_receipt,
            _epi=_epi, _spk=_spk, _spk_type=_spk_type, _pending=_pending,
            _perception_tagged=_perception_tagged,
            _source_before_tag=_source_before_tag,
            _speaker_type_before_tag=_speaker_type_before_tag,
            _sub_type_before_plan=_sub_type_before_plan,
        )

        # 生成向量并存入 ChromaDB（复用查重阶段算好的嵌入，避免重复推理）
        try:
            if embedding is None:
                embedding = await text_embedding(content)
            await add_memory(
                memory_id=memory.id,
                character_id=character_id,
                memory_type=memory_type,
                content=content,
                embedding=embedding,
                importance=importance,
            )
        except Exception as e:
            _logger.warning("向量存储失败（待对账）: memory_id=%s %s", memory.id, e)

        # P1：核心记忆自动晋升（高重要+多次确认 / 高价值类型 → is_core；失败静默）
        try:
            if not _pending:  # M4：待核记忆不参与 is_core 晋升（检索强降权 + 不进核心注入）
                from app.memory.core import maybe_promote_core
                await maybe_promote_core(memory.id, pct, sub_type, memory_type)
        except Exception:
            pass

        # 写后副动作（2026-10-12 A50 批 1：机械抽出到同文件私有协程 `_run_created_memory_hooks`）。
        # 六件事的顺序、try/except 作用域、参数取值、局部 import 与后台任务名逐字不变；
        # 调用点仍在 `async_session_factory` 的上下文里，返回的仍是原 memory 对象。
        await _run_created_memory_hooks(
            memory=memory, user_id=user_id, character_id=character_id,
            source=source, embedding=embedding, bm25_invalidate=bm25_invalidate,
        )
        return memory


async def _prefilter_memory_write(*, db, content, character_id, memory_type, sub_type,
                                  source, source_id, emit_memory_receipt,
                                  ACTION_REJECT) -> tuple[bool, str | None]:
    """写入前的**过滤**阶段（2026-10-12 A50 批 6，自 ``save_memory`` 开头段机械抽出）。

    做的事照旧：① M4 准入闸门拦开发运维元信息／代理发言 ② 聊天来源拦「台词原文」③ 与来源消息
    （或同会话紧随的 AI 回复）逐字一致时拦掉。纯搬运、零行为变化——三条拒收的 reason 文案与 payload、
    日志行、异常边界一个都没动。

    **运输契约**（本切口唯一不是 AST 逐字照搬的部分，且是修正后的写法）：
    - 返回 ``(keep, _src_msg_sender)``：``keep`` 是**明确布尔**——``True`` 放行、``False`` 三条拒收之一，
      调用点立刻 ``return None``。不得用 sender 的 ``None`` 同时表达放行与拒收：放行时 sender
      也可能为空，调用方仍继续新建并返回 Memory，必须用明确的 keep 布尔区分；
    - 元信息那条早退发生在 ``_src_msg_sender = None`` **之前**（初始化位置照旧、不擅自提前），所以它
      返回 ``(False, None)`` 而**不是**引用未绑定名——否则 UnboundLocalError；
    - 后两条拒收与末尾放行都带上 ``_src_msg_sender``：它是唯一的跨段变量（供批 4 的
      ``_prepare_created_memory`` 复用同一次查询），必须显式运输，不得重查。

    为什么能机械抽（成立条件也是以后不许乱改的约束）：
    - 整段是 ``save_memory`` 里紧跟 ``async with`` 的**连续三条语句**（If／Assign／If），块内**零**
      commit／零 ``db.add``／零 ORM 写，事务时点与落库时机一个都没动；
    - ``emit_memory_receipt``／``ACTION_REJECT`` **必须由调用点传入**：二者在 ``save_memory`` 顶从
      ``app.memory.receipt`` 延迟 import，测试 monkeypatch 的正是那个模块的属性；helper 里重新 import
      会改变依赖解析时点，故保留调用方原来取得的对象，不改成模块级 import 或默认参数；
    - ``looks_like_raw_dialogue``／``ChatMessage`` 两条**块内局部 import 随块**（测试 patch 的正是
      ``app.memory.dialogue_filter`` 与 ``app.models.chat``），不得上提到模块级；
    - ``_admission_gate_on``／``_is_meta_noise``／``select``／``_logger`` **继续按模块全局晚绑定**
      （不得提成形参、不得在模块顶层缓存）；
    - 异常边界照旧且**不得顺手修**：``except Exception: pass`` 仍只包住逐字比对那一段（查消息失败
      ⇒ fail-open 放行，且**已取到的 sender 保留**）；元信息与台词原文两条的 logger／回执异常原本就在
      try 之外、会照旧上抛，逐字那条的 logger／回执异常原被这个 try 吞掉后**继续放行**——三处既有行为
      抽出后原样保留，不许「顺手」补 try；
    - 长度 60 上限、``source_id is None`` 短路、``sub_type is None`` 才取下一条、同会话＋``id >``＋
      升序＋limit 1、首尾逐字比较三条判据一个都不许改。
    """
    # ── M4 准入闸门：开发运维元信息 / 代理发言拦截（flag 关=零行为）──
    if _admission_gate_on() and _is_meta_noise(content):
        _logger.info("Memory dropped: meta-noise/agent-speak (char=%d type=%s): %.40s",
                     character_id, memory_type, content)
        emit_memory_receipt(
            character_id, None, ACTION_REJECT, reason="meta-noise/agent-speak blocked",
            detail={"memory_type": memory_type, "sub_type": sub_type, "source": source},
        )
        return (False, None)
    # 聊天来源记忆拦截"台词原文"（提取器/【记忆】标记路径都可能在来源消息为 AI 台词时误抄）：
    # 命中对话特征，或与源消息（AI 回复）逐字一致 → 直接丢弃，不落库。
    _src_msg_sender = None  # M4：缓存来源消息的 sender_type，供准入闸门复用同一次查询
    if source == "chat":
        from app.memory.dialogue_filter import looks_like_raw_dialogue
        if looks_like_raw_dialogue(content):
            _logger.info("Memory dropped: raw dialogue (char=%d type=%s sub=%s): %.40s",
                         character_id, memory_type, sub_type, content)
            emit_memory_receipt(
                character_id, None, ACTION_REJECT, reason="raw dialogue dropped",
                detail={"memory_type": memory_type, "sub_type": sub_type, "source": source},
            )
            return (False, _src_msg_sender)
        if source_id is not None and len(content) <= 60:
            try:
                from app.models.chat import ChatMessage
                msg = await db.get(ChatMessage, source_id)
                if msg is not None:
                    _src_msg_sender = msg.sender_type  # M4：复用，勿再查一次
                    cands = []
                    if msg.sender_type == "ai":
                        cands.append(msg)
                    elif sub_type is None:
                        # 标记路径（【记忆：...】）的 source_id 指向用户消息：
                        # 再取同会话紧随的 AI 回复，防止把 AI 台词/自我介绍当用户信息落库
                        nxt = (await db.execute(
                            select(ChatMessage).where(
                                ChatMessage.session_id == msg.session_id,
                                ChatMessage.id > msg.id,
                                ChatMessage.sender_type == "ai",
                            ).order_by(ChatMessage.id.asc()).limit(1)
                        )).scalar_one_or_none()
                        if nxt is not None:
                            cands.append(nxt)
                    for cand in cands:
                        raw = (cand.content or "").strip().strip("“”「」『』...")
                        c = content.strip().strip("“”「」『』...")
                        if raw and c and (raw == c or raw.startswith(c) or raw.endswith(c)):
                            _logger.info("Memory dropped: verbatim AI line (char=%d type=%s): %.40s",
                                         character_id, memory_type, content)
                            emit_memory_receipt(
                                character_id, None, ACTION_REJECT, reason="verbatim AI line dropped",
                                detail={"memory_type": memory_type, "sub_type": sub_type, "source": source},
                            )
                            return (False, _src_msg_sender)
            except Exception:
                pass
    return (True, _src_msg_sender)


async def _tag_perception_source(*, db, user_id, character_id, content, source,
                                 epistemic_status, sub_type, speaker_type, speaker_id,
                                 _perception_tagged, _source_before_tag,
                                 _sub_type_before_tag, _speaker_type_before_tag,
                                 _speaker_id_before_tag):
    """感知来源**标注**阶段（2026-10-12 A50 批 3，自 ``save_memory`` 中段机械抽出）。

    做的事照旧：flag ``perception_source_tag`` 开着且本轮感知语料非空时，用「快照时间标签残留
    ＋ 长词重合」两路判据把这条记忆改写成感知来源（``source=perception``、``epistemic_status=
    INFERRED``），显式撤掉调用方给的 speaker 两列，把命中那条快照的来源通道写进 ``sub_type``，
    并按原有级别与文案打日志。纯搬运、零行为变化——判据、改写字段、日志、fail-open 一个都没动。

    为什么能机械抽（成立条件也是以后不许乱改的约束）：
    - 整段只**读**调用点的十四个值（``db``／``user_id``／``character_id``／``content``／``source``／
      ``epistemic_status``／``sub_type``／``speaker_type``／``speaker_id``／``_perception_tagged``
      ＋四个 ``*_before_tag``）；``_corpus``／``_texts``／``_channel`` 是段内临时值（段外无人读），
      故只需把六个被改写的量按原序返回、由调用点解包回去；
    - ``_perception_tagged`` 与四个 ``*_before_tag`` 的**初始化留在调用点**（位置刻意排在台词
      过滤之后）：fail-open 回退要撤的就是它们，helper 只拿快照、不得自己重算；
    - ``_perception_tag_on``／``_recent_snapshots``／``has_snapshot_tag``／``snapshot_overlap``／
      ``_matched_snapshot_channel``／``PERCEPTION_SOURCE``／``_logger`` **必须继续按模块全局解析**
      （不得提成默认参数、不得模块级缓存）：既有测试 monkeypatch 的正是 ``app.memory.write`` 上
      这些属性，提前绑定会绕开接缝，让「patch 快照查询／patch 判据」那批用例失真；
    - 异常边界照旧且**不得顺手修**：``except`` 只撤 ``source``／``sub_type``／``speaker_type``／
      ``speaker_id`` 四列，不撤 ``epistemic_status`` 与 ``_perception_tagged``（改写途中抛错时后者
      会停在 True）——既有行为，抽出也不改；
    - 不新增查询／不新增开关／不改事务边界：``db`` 由调用点传入，复用已开的会话。
    """
    if _perception_tag_on():
        try:
            _corpus = await _recent_snapshots(db, user_id)
            _texts = [t for _s, t in _corpus]
            # 语料为空 ⇒ 不启用判据（本轮没有感知注入，保持旧行为、不报错）
            if _texts and (has_snapshot_tag(content) or snapshot_overlap(content, _texts)):
                source = PERCEPTION_SOURCE
                epistemic_status = "INFERRED"  # 感知＝推断，未经用户认可绝不是事实（方案 §2.3）
                _perception_tagged = True
                # 断点 #5：调用方给的 speaker 两列**显式撤掉**（连 user id 一起清空）——
                # 「这条来自屏幕」不等于「用户亲口说的」，也不该伪造 character。
                # P0 第 4 步起：清空之后由下方 `perception_actor_column` 把判定结果落成
                # ``speaker_type='perception'``（可 SQL 查询），speaker_id 仍留空（感知没有说话人 id）。
                speaker_type = None
                speaker_id = None
                # M2「sub_type 记通道」：把命中的那条快照的来源通道写进 sub_type
                # （accessibility / clipboard / media）。只在打标这一刻写，flag 关不写。
                _channel = _matched_snapshot_channel(_corpus, content)
                if _channel:
                    sub_type = _channel
                _logger.info("Memory tagged perception: char=%d user=%d from=%s channel=%s: %.40s",
                             character_id, user_id, _source_before_tag, sub_type, content)
        except Exception as _e:
            source = _source_before_tag  # fail-open：打标本身不出错，只降级为旧行为
            sub_type = _sub_type_before_tag  # 通道也只回退到打标前的值（同一次改写，一起撤）
            speaker_type = _speaker_type_before_tag  # 断点 #5：归属改写与来源改写同批撤销
            speaker_id = _speaker_id_before_tag
            _logger.warning("perception tagging failed (fail-open): char=%d user=%d err=%s",
                            character_id, user_id, _e)
    return source, epistemic_status, _perception_tagged, sub_type, speaker_type, speaker_id


def _emit_created_memory_receipts(
        *, memory, character_id, memory_type, sub_type, source, source_id,
        derived_from_ids, node_type, emit_memory_receipt, _epi, _spk, _spk_type,
        _pending, _perception_tagged, _source_before_tag, _speaker_type_before_tag,
        _sub_type_before_plan
):
    """新建记忆的**回执尾段**（2026-10-12 A50 批 2，自 ``save_memory`` 尾段机械抽出）。

    三件事顺序照旧：① M4 待核降级（``_pending``）② 批 0-2 感知打标留痕（``_perception_tagged``）
    ③ #70 M3 的 create 回执＋批 0-8 的 ``provenance`` 来源链。纯搬运，零行为变化——三段各自
    ``try/except`` 静默、字段与取值、``reason`` 文案、``_gates`` 的判定顺序一个都没动。

    为什么能机械抽（成立条件也是以后不许乱改的约束）：
    - 整段只依赖调用点的十七个值（``memory``／``character_id``／``memory_type``／``sub_type``／
      ``source``／``source_id``／``derived_from_ids``／``node_type``／``emit_memory_receipt``
      ＋ ``_epi``／``_spk``／``_spk_type``／``_pending``／``_perception_tagged``／
      ``_source_before_tag``／``_speaker_type_before_tag``／``_sub_type_before_plan``），
      不读写 ``db``、不新增查询、不产生返回值，所以 ``save_memory`` 的其余部分一字未变；
    - ``emit_memory_receipt`` **必须由调用点传入**：它在 ``save_memory`` 函数顶从
      ``app.memory.receipt`` 延迟 import，测试 monkeypatch 的正是那个模块属性；helper 里
      重新 import 会绕开这层接缝，让「patch 发射口」的那批用例失真；
    - 三个 ``ACTION_*`` 常量**继续留在本函数顶延迟 import**（与原口径一致：每次调用时从模块
      属性解析），只是 ``save_memory`` 自己不再用它们、故那边同批删掉；
    - 本函数**不读任何 flag**、不自己判定 pending／打标／plan 改名——那些判定结果一律由调用点
      算好后传入（原样如此，不得「顺手」搬进来）。
    """
    from app.memory.receipt import (
        ACTION_CREATE,
        ACTION_DOWNGRADE,
        ACTION_UPDATE,
    )
    # M4 准入闸门：待核留痕（复用 M3 回执；flag 关时 admit_memory 不触发，_pending=False）。
    # 不新增表：与 M3 共用 memory_write_receipts；该回执本身受 memory_write_receipt flag 闸控。
    if _pending:
        try:
            emit_memory_receipt(
                character_id, memory.id, ACTION_DOWNGRADE,
                reason="admission gate: pending review",
                detail={"epistemic_status": _epi, "source": source, "sender_type": _spk},
            )
        except Exception:
            pass

    # 批 0-2 M1a：感知打标留痕（复用 M3 回执表与发射口，不新造机制）。
    # 记下「原本要写成什么来源」，误标时可据此人工复核/一键纠正（方案 §四「纠」）。
    # 断点 #5：同时记归属改写；P0 第 4 步起「改写结果」与「落库列值」是同一个值
    # （speaker_type 列真写成 perception），故两键都取实际落库值，不留「说一套写一套」的痕。
    if _perception_tagged:
        try:
            emit_memory_receipt(
                character_id, memory.id, ACTION_UPDATE,
                reason="perception source tagged (screen-derived)",
                detail={"from_source": _source_before_tag, "to_source": PERCEPTION_SOURCE,
                        "epistemic_status": _epi, "memory_type": memory_type,
                        "sub_type": sub_type,
                        "speaker": PERCEPTION_SENDER, "speaker_type": _spk_type,
                        "from_speaker_type": _speaker_type_before_tag},
            )
        except Exception:
            pass

    # #70 M3 写入回执：新记忆落库成功（flag 开=异步写一条 create；关=零行为）
    # 批 0-8：同一条回执里补 `provenance`（来源链条）——既有四个键的语义与取值一字未动。
    # `gates` 只登记**本轮真实生效**的改写，供治理面复盘「这条为什么长成这样」：
    #   perception_tagged＝批 0-2 打标改了来源归属；admission_pending_review＝M4 待核降级；
    #   plan_renamed＝L4 把 sub_type 收敛成 plan（判定用的是改名前的 sub_type，故 route 不受影响）。
    try:
        _gates = []
        if _perception_tagged:
            _gates.append("perception_tagged")
        if _pending:
            _gates.append("admission_pending_review")
        if memory.sub_type != _sub_type_before_plan:
            _gates.append("plan_renamed")
        _prov = _write_provenance(
            source=source, sub_type=sub_type, memory_type=memory.memory_type,
            actor=_spk, source_id=source_id, epistemic_status=_epi,
            derived_from_ids=derived_from_ids, node_type=node_type,
            gates=_gates, stored_sub_type=memory.sub_type,
        )
        _detail = {
            "memory_type": memory.memory_type,
            "sub_type": memory.sub_type,
            "source": memory.source,
            "importance": float(memory.importance or 0),
        }
        if _prov:
            _detail["provenance"] = _prov
        emit_memory_receipt(
            character_id, memory.id, ACTION_CREATE,
            reason="new memory written",
            detail=_detail,
        )
    except Exception:
        pass

async def _prepare_created_memory(*, db, timedelta, user_id, character_id, memory_type,
                                   content, title, importance, sub_type, source,
                                   related_memory_id, source_id, group_id, scope,
                                   speaker_id, speaker_type, epistemic_status,
                                   reliability, chain_id, parent_id, node_type,
                                   derived_from_ids, _src_msg_sender):
    """新建记忆的**准备阶段**（2026-10-12 A50 批 4，自 ``save_memory`` 中后段机械抽出）。

    做的事照旧：① P0 归属与认知状态（默认按来源推断 + 感知派生条不套默认 + 落成
    ``speaker_type`` 列）② M4 准入闸门（``admit_memory`` 确定性裁决，只降不升）
    ③ P0 影子埋点（只判定 + 只打一条 INFO，不改任何取值）④ 构造那条尚未落库的 ``Memory``
    （含衰减起点／强度／下次复习时间／派生列表 JSON）⑤ L4 计划类记忆落库时写 ``valid_to``
    并标 ``sub_type=plan``。纯搬运、零行为变化——判据、取值、日志级别、异常边界一个都没动。

    为什么能机械抽（成立条件也是以后不许乱改的约束）：
    - 整段是 ``save_memory`` 内的一段**连续**语句，**零** ``return``（没有要搬的早退分支），
      也**不写任何形参**（8 个新局部全是段内新建），所以只需把七个跨段变量按原序返回解包回去；
    - ``db`` 由调用点传入（复用已开会话）、``timedelta`` 也由调用点传入——它在
      ``save_memory`` 顶部做局部 import，**不得**为了好看提成模块级 import（那会把
      「调用时解析」的时点前移到导入期）；
    - ``Memory``／``json``／``_admission_gate_on``／``_is_perception_source``／
      ``perception_actor_column``／``_resolve_admission_sender``／``admit_memory``／
      ``_normalize_importance``／``_initial_strength``／``_now_naive``／``_logger``
      **必须继续按模块全局晚绑定**（不得提成默认参数或模块级缓存）：既有测试 monkeypatch 与
      直接取用的正是 ``app.memory.write`` 上这些属性，提前绑定会绕开接缝；
    - 四段异常语义照旧且**不得顺手修**：准入两段各自回落 + warning，影子与 plan 两段
      完全静默；**此处不得再加一层总包围 try**（那会把原本该冒的错吞掉、改变异常边界）；
    - 不新增查询／不新增开关／不改事务边界：``db.add`` 及 flush/commit/refresh 仍在调用方，
      临时 ``Memory`` 对象仍在本事务内构造、仍未 ``db.add``。
    """
    pct = _normalize_importance(importance)
    # P0：记忆归属与认知状态（默认按来源推断；调用方可显式覆盖）
    _spk_type = speaker_type
    _spk_id = speaker_id
    # 断点 #5 → P0 第 4 步：感知派生条**不套用「默认＝用户陈述」**，且判定结果直接落进列。
    # 本判定**常开**（不再受 perception_source_tag 门控）：生产该闸已开，打标命中时两列被
    # 清空、调用方直接写 source=perception 时也在此收口；其它来源的兜底路径一字未动。
    if _spk_type is None and _spk_id is None and not _is_perception_source(source):
        _spk_type = "user"  # 默认归属用户（多数记忆来自用户陈述）
        _spk_id = user_id
    _spk_type = perception_actor_column(_spk_type, source)
    _spk = _spk_type  # 准入闸门留痕用（角色推断时更新为实际归属）
    _epi = epistemic_status
    _pending = False
    if _admission_gate_on():
        # M4 准入闸门：确定性裁决（不新增 LLM）。显式 epistemic 也只降不升——抽取器给的
        # FACT 来自内容启发式（memory/speaker.py），不等于「来源消息是用户说的」。
        try:
            _spk = await _resolve_admission_sender(
                source, source_id, speaker_type, _src_msg_sender, db)
        except Exception as e:
            _spk = "user"
            _logger.warning("admission gate sender resolve failed: %s", e)
        try:
            _epi, _pending = admit_memory(source, _spk, _epi, reliability, memory_type)
        except Exception as e:
            _epi = epistemic_status  # 任何异常回落现状
            _pending = False
            _logger.warning("admission gate verdict failed: %s", e)
    if _epi is None:
        _epi = "FACT" if source in ("chat", "moment", "diary", "life", "bio") else "UNVERIFIED"
    # ── P0 语义统一·第 1 步 影子埋点①（flag actor_semantics_shadow，默认关＝零行为）──
    # 只判定 + 只打一条 INFO：「按统一语义该记在谁名下」vs「本轮实际会落成什么」。
    # 不查库、不写库、不改 _spk/_spk_type/_epi 任何取值，也不得插到任何判定之前。
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        if AGENT_FLAGS.get("actor_semantics_shadow", False):
            from app.memory.actor_shadow import trace_actor_write
            trace_actor_write(
                character_id=character_id, user_id=user_id, source=source,
                speaker_type=speaker_type, source_message_sender=_src_msg_sender,
                source_id=source_id, actual_speaker_type=_spk_type,
                actual_speaker_id=_spk_id, admission_actor=_spk,
            )
    except Exception:
        pass
    memory = Memory(
        user_id=user_id,
        character_id=character_id,
        memory_type=memory_type,
        title=title or None,
        content=content,
        scope=scope,
        importance=pct,
        reliability_score=reliability,  # M4：可靠度（<0.4 已被闸门降级为待核）
        sub_type=sub_type,
        source=source,
        related_memory_id=related_memory_id,
        source_id=source_id,
        group_id=group_id,
        chain_id=chain_id,
        parent_id=parent_id,
        node_type=node_type,
        speaker_id=_spk_id,
        speaker_type=_spk_type,
        epistemic_status=_epi,
        decay_base_at=_now_naive(),
        strength_days=_initial_strength(memory_type),
        last_reinforce_at=_now_naive(),
        next_review_at=_now_naive() + timedelta(days=_initial_strength(memory_type)),
        derived_from_ids=json.dumps(list(derived_from_ids or []), ensure_ascii=False, default=str),
    )
    # L4 提取侧（2026-09-09 主动复习回忆化）：计划类记忆落库时写 valid_to + 标 sub_type=plan。
    # flag review_plan_validity_extract 灰度默认关；关=零行为（不写不标，逐字节旧路径）。
    # 提取器（extractor.extract_single / 【记忆】标记路径）所有写入都经 save_memory，故在此单点收口；
    # 显式 sub_type（slot/status/relationship 等）不覆盖，只收敛默认/extracted 路径。
    _sub_type_before_plan = sub_type  # 批 0-8：留痕要能看出「被 L4 改名成 plan」这一步（不改判据）
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        if AGENT_FLAGS.get("review_plan_validity_extract", False):
            from app.memory.tense import classify_tense, plan_valid_until
            if classify_tense(memory) == "plan" and sub_type in (None, "extracted", "plan"):
                memory.sub_type = "plan"
                memory.valid_to = plan_valid_until(memory, _now_naive())
    except Exception:
        pass
    return memory, pct, _spk_type, _spk, _epi, _pending, _sub_type_before_plan


async def _run_created_memory_hooks(*, memory, user_id, character_id, source,
                                    embedding, bm25_invalidate):
    """新建记忆的**写后副动作**（2026-10-12 A50 批 1，自 ``save_memory`` 尾段机械抽出）。

    六件事顺序照旧：① auto dedup（节流 + 防重入）② 记忆链建链器 ③ 异步意义提炼
    ④ ``memory.written`` 事件总线 ⑤ 插件 ``memory_written`` hook ⑥ BM25 索引失效。
    纯搬运，零行为变化——判据、取值、任务名、事件与插件 payload 的字段一个都没动。

    为什么能机械抽（成立条件也是以后不许乱改的约束）：
    - 整段只依赖调用点的六个值（``memory``／``user_id``／``character_id``／``source``／
      ``embedding``／``bm25_invalidate``），不读写 ``db``、不改 save_memory 的其它局部状态、
      不产生返回值，所以抽出去后 save_memory 的其余部分与返回值一字未变；
    - ``bm25_invalidate`` **必须由调用点传入**：它在 save_memory 函数顶从 ``app.memory.service``
      延迟 import，测试 monkeypatch 的正是 service 上的那个属性；helper 里重新 import 会绕开
      这层接缝，让「patch 服务层」的那批测试（bm25 失效断言）失真。
    - 其余依赖（``_schedule_dedup``／``spawn_background``／``link_new_memory``／
      ``maybe_extract_meaning``／``publish``／``make_event``／``run_hook``）**继续留在函数内**
      做延迟 import：它们的 monkeypatch 接缝是「模块属性」，函数内解析才接得住，也不得为了
      好看提到模块顶层（有副作用的模块不许提前 import）。
    - 失败边界逐字保持：只有 ``_spawn_bg(_schedule_dedup(...))`` 是**裸调用**（异常照旧上抛），
      其余每段各自 try/except 静默；**此处不得再加一层总包围 try**——那会把原本该冒的错吞掉。
    """
    # auto dedup：节流 + 防重入（避免每次写记忆都触发全量 O(n^2) 比较，导致 CPU 打满）
    from app.memory.dedup import _schedule_dedup
    from app.utils.async_tasks import spawn_background as _spawn_bg
    _spawn_bg(_schedule_dedup(character_id), name=f"dedup-{character_id}")

    # B1-② 记忆链建链器（方案 §13.4，flag memory_chain_builder 默认关 = 零行为）：
    # _schedule_dedup 之后追加链节点，复用 save_memory 已算好的 embedding（避免重复 ONNX 推理）。
    try:
        from app.memory.chain_builder import link_new_memory, memory_chain_builder_enabled
        if memory_chain_builder_enabled():
            _spawn_bg(link_new_memory(memory.id, embedding), name=f"chain-{memory.id}")
    except Exception:
        pass

    # 记忆架构 v2.1：里程碑记忆（event/relationship 且重要度达标）→ 异步低频意义提炼（开关控制，失败静默）
    try:
        from app.memory.meaning import maybe_extract_meaning
        _spawn_bg(
            maybe_extract_meaning(
                character_id, user_id, memory.id, memory.memory_type, memory.sub_type,
                memory.content, float(memory.importance or 0),
            ),
            name=f"meaning-{memory.id}",
        )
    except Exception:
        pass

    # 织库增量补卡（2026-08-12 → 2026-08-14 改走事件总线）：发布 memory.written 事件，
    # 由 events/handlers 订阅者执行（importance≥60 才整理，source=life 进私·织库）
    try:
        from app.events import publish
        from app.events.schema import make_event
        _evt = make_event(
            "memory.written",
            speaker={"type": "system", "id": "memory_pipeline"},
            target={"type": "character", "id": character_id},
            audience=[character_id, user_id],
            provenance={"origin": "system_event"},
            data={
                "user_id": user_id,
                "character_id": character_id,
                "memory_id": memory.id,
                "memory_type": memory.memory_type,
                "sub_type": memory.sub_type,
                "source": source or "",
                "importance": float(memory.importance or 0),
            },
        )
        publish("memory.written", _evt)
    except Exception:
        pass


    # 插件 Hook：memory_written（记忆写入成功 → 插件副动作；异常隔离，不阻断主链路）
    try:
        from app.plugins.registry import run_hook
        await run_hook("memory_written", {
            "user_id": user_id,
            "character_id": character_id,
            "memory_id": memory.id,
            "memory_type": memory.memory_type,
            "sub_type": memory.sub_type,
            "content": memory.content,
            "importance": float(memory.importance or 0),
            "source": memory.source,
        })
    except Exception:
        pass

    # 检索增强（2026-08-23）：记忆已写入该角色 → 使 BM25 索引失效（下次检索懒重建）
    bm25_invalidate(character_id)


# ── P0 第 4 步：只读追溯（按来源消息反查 actor 归一结果）────────────────────────
# 跨表最小链：``chat_messages.id`` →（``domain_events.entity_id`` 的发起方）→（``memories.source_id`` 的落库列）。
# 三处**只 SELECT**，不写、不刷强度、不发事件。本函数存在的意义＝给「库里那条到底记成谁的」一个
# 可复核的答案；判据不另立一套，沿用 ``actor_shadow.judge_actor`` 的统一语义（第 1 步的事实源）。
TRACE_MAX_EVENT_ROWS = 20
TRACE_MAX_MEMORY_ROWS = 50


async def trace_actor_for_message(message_id, db=None) -> dict:
    """只读追溯：给定来源消息 id，返回「按统一语义该归给谁」＋库里三张表实际记了什么。

    返回（永不抛异常；出错时 ``error`` 记原因，其余字段按已取到的部分给）::

        {"message_id", "found", "message_sender_type", "event_actors",
         "memory_rows", "memory_speaker_types", "actor", "basis", "consistent", "error"}

    - ``actor`` / ``basis``：``judge_actor`` 的统一判定与依据档（纯判定拿不准时 ``actor=None``）；
    - ``consistent``：判定值是否与**全部**落库 ``speaker_type`` 相符（判定为 None 时不参与比对，置 None）；
    - ``db`` 传入则复用调用方会话（只读、不 commit、不替调用方关闭），否则自开会话、用完即关。
    """
    out = {"message_id": message_id, "found": False, "message_sender_type": None,
           "event_actors": [], "memory_rows": 0, "memory_speaker_types": [],
           "actor": None, "basis": "", "consistent": None, "error": None}
    if message_id is None:
        out["error"] = "message_id is None"
        return out
    try:
        from app.memory.actor_shadow import judge_actor

        owns = db is None
        if owns:
            from app.memory.service import async_session_factory
            db = async_session_factory()
        try:
            from app.models.chat import ChatMessage
            from app.models.domain_event import DomainEvent

            msg = await db.get(ChatMessage, message_id)
            if msg is not None:
                out["found"] = True
                out["message_sender_type"] = msg.sender_type

            out["event_actors"] = [
                r[0] for r in (await db.execute(
                    select(DomainEvent.actor_type)
                    .where(DomainEvent.entity_type == "chat_message",
                           DomainEvent.entity_id == message_id)
                    .order_by(DomainEvent.id.asc())
                    .limit(TRACE_MAX_EVENT_ROWS)
                )).all()
            ]

            mem_rows = (await db.execute(
                select(Memory.source, Memory.speaker_type)
                .where(Memory.source_id == message_id)
                .order_by(Memory.id.asc())
                .limit(TRACE_MAX_MEMORY_ROWS)
            )).all()
            out["memory_rows"] = len(mem_rows)
            out["memory_speaker_types"] = [r[1] for r in mem_rows]
            mem_source = mem_rows[0][0] if mem_rows else None
        finally:
            if owns:
                await db.close()

        j = judge_actor(source=mem_source, source_message_sender=out["message_sender_type"],
                        source_id=message_id if mem_rows else None)
        out["actor"], out["basis"] = j.actor, j.basis
        if not out["found"] and not out["memory_rows"]:
            # 两头都没有证据：如实说「查不到」，绝不臆造成 user（臆造等于把丢失点伪装成结论）
            out["actor"], out["basis"] = None, "not_found"
            return out
        if j.actor is not None and out["memory_speaker_types"]:
            out["consistent"] = all(v == j.actor for v in out["memory_speaker_types"])
        return out
    except Exception as e:
        out["error"] = f"{e.__class__.__name__}: {e}"
        return out
