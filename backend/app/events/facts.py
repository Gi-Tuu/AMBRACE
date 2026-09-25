"""World State 查询层（World & Cognition P4，2026-08-15）

事件 → 规则折叠 → 世界事实（当前状态，event-sourced 物化视图，不是新数据库）：

- assert_fact：断言新事实；同 subject+predicate 的 active 事实自动 supersede（新替旧）
- get_active_facts / get_character_view：按 audience 可见性 + 过期时间过滤查询
- fold_status_update：聊天【状态更新】标记 → 角色当前状态事实（FACT，TTL 12h）
- fold_activity：life.activity_completed 事件 → 角色最近活动事实

范围（2026-08-15 用户拍板）：只做世界状态记忆增强（让 AI 记得"正在进行/刚发生的事"），
不做内容审查/违规重生成类拦截，保持角色扮演自由度。
"""
import json
import re
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db.database import async_session_factory
from app.events.schema import EPISTEMIC_FACT, EPISTEMIC_UNVERIFIED
from app.models.memory import WorldFact
from app.utils.logger import get_logger
from app.utils.timeutil import now_naive_utc as _now_naive

_logger = get_logger("events.facts")

PUBLIC_AUDIENCE = "public"
MAX_FACTS_PER_CHAR = 12  # 每 (用户, 角色) 活跃事实上限，超出按最旧淘汰
STATUS_FRESH_HOURS = 12  # status 类瞬时状态注入新鲜度窗口（2026-08-16：防 stale 状态反复注入）
# C2-②（2026-09-10）：各瞬时谓词分别定窗——位置比状态稳定，但返程/出行后 72h 必须失效
# （防旧城市常驻）；心情与状态同寿命。activity 不入表：它由 fold_activity 写 3 天 TTL 兜底。
LOCATION_FRESH_HOURS = 72
MOOD_FRESH_HOURS = 12
_TRANSIENT_FRESH_HOURS = {
    "status": STATUS_FRESH_HOURS,
    "mood": MOOD_FRESH_HOURS,
    "location": LOCATION_FRESH_HOURS,
}

# ── Ariadne 模块F：Curated Knowledge（2026-09-04）──
KIND_STATUS = "status"                 # 瞬时状态事实（既有语义，默认）
KIND_FACT = "fact"                     # 稳定事实（用户硬档案/世界设定）
KIND_CONSTRAINT = "constraint"         # 人格铁律/硬约束（无条件注入）
KIND_PREFERENCE = "preference_profile" # 长期偏好画像
KIND_RELATION_BASE = "relationship_baseline"  # 关系基线
CURATED_KINDS = {KIND_FACT, KIND_CONSTRAINT, KIND_PREFERENCE, KIND_RELATION_BASE}
TRANSIENT_PREDICATES = {"status", "activity", "location", "mood"}  # 既有瞬时谓词

VERIFY_UNVERIFIED = "unverified"
VERIFY_MACHINE = "machine-confirmed"
VERIFY_HUMAN = "human-reviewed"

# 每类 curated 无条件注入的条数上限（确定性供给，不走向量）；constraint 单独放宽
CURATED_TOPN_PER_KIND = 4
CURATED_CONSTRAINT_TOPN = 8

# 修正历史只读查询单次返回的版本上限（2026-09-16）：status/activity 槽位逐轮 supersede，
# 不设上限会随会话量放大成无界载荷；超出只回最近 N 版（当前值恒在最新版内）。
FACT_HISTORY_MAX_VERSIONS = 50

# TODO（Ariadne 模块F 一期裁剪，2026-09-04 拍板，不实现）：
# - kind 作用域列（global/user/character 级）：二期，一期只上 verify_state；
# - 插件 `knowledge:write` 权限：二期；
# - lifecycle（draft/stable/deprecated）：二期，一期 verify_state 够用。


# ── M4 写入准入闸门（flag `memory_admission_gate`，默认 False）──
# 以下判定全部确定性、零 LLM；flag 关 = 逐字节现状（查询/建行参数与接线前完全一致）。
# 注意：本 flag 未登记进 app/agent/loop.py 的 AGENT_FLAGS 硬编码默认表（本批文件隔离只允许
# 改 facts.py / memory/write.py），运行时要开只能由拥有 loop.py 的批次补登记。
DEFAULT_REVIEW_DAYS = 30  # 身份/职业类事实统一 review 窗口（不永不过期）
# 「长期偏好/setting」类不得标 status：命中则归到长期层（#73 反例 = 长期亲密偏好误标 status 且永久化）
_DURABLE_STATUS_PREDICATES = {"setting", "preference", "preference_profile"}
# 身份/职业类谓词同样是长期事实（不得落 status 瞬时层吃 12h TTL）
_IDENTITY_PREDICATES = {
    "role", "identity", "job", "occupation", "title", "profession",
    "position", "work", "school", "major", "relationship",
}
# 语义查重阈值（依据见 _same_fact_text docstring）
_WORLD_FACT_SIMILARITY = 0.9
_MIN_CORE_LEN = 6           # 通用文本「前缀包含」合并的最短核心长度
_MIN_IDENTITY_CORE_LEN = 4  # 身份值「前缀包含」合并的最短长度
# C13（2026-09-25）「共享核心前缀」合并阈值。依据（生产库实测）：char13 的 68 条 active curated
# 里绝大多数是同一个核心「我是用户的老公」+ 各自补充（同住/照顾饮食起居/亲密主导…）。这一族是
# 「共享长核心 + 各自后缀」形态——互不包含、相似度只有 0.4~0.64，故既有三条判据只命中 4 对。
# 实测本条取 LCP>=7 且 >=短串 30% 时命中 178 对（恰好收敛那族），且不误并
# 「用户腰不能压…」/「用户腰部有伤…」（LCP 仅 3 字）、「喜欢咖啡」/「喜欢喝茶」（LCP 2 字）。
_CORE_PREFIX_MIN_LEN = 7        # 共享核心前缀最短长度（归一化后）
_CORE_PREFIX_MIN_FRAC = 0.30    # 该前缀至少要占短串的这个比例
# C13b（2026-09-25）：上述前缀还必须停在「子句边界」上，否则「同模板不同宾语」会被误并
# （实测「用户平时喜欢喝美式咖啡」vs「用户平时喜欢喝拿铁咖啡」LCP=7、占比 63.6% ⇒ 误判同一条）。
# 边界字符 = 归一化会剔除的空白/中英文标点 + 常见成对/连接符（用于看前缀后一个原文字符）。
_CLAUSE_BOUNDARY_CHARS = set(
    " \u3000\t\r\n，。！？、；：,.!?;:（）()【】[]「」『』“”‘’\"'~～-—"
)

# 开发运维 / 代理发言黑名单（用户拍板默认过滤：不进 world_facts、不进人物记忆）。
# 与 app/memory/write.py 的同名常量必须保持一致（两文件各自独立持有，避免模块级循环依赖）。
_META_NOISE_KEYWORDS = (
    "弃号", "组网", "换模型", "mcp 接入", "mcp接入", "重构", "修bug", "修 bug",
    "部署上线", "迁移数据", "数据迁移", "回滚版本",
)


def _admission_gate_on() -> bool:
    """读 feature flag；任何异常回落 False（关=逐字节旧行为）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("memory_admission_gate", False))
    except Exception:
        return False


def _is_meta_noise(text: str | None) -> bool:
    """识别开发运维元信息 / 代理发言（如「我是轩的 Agent 助手，请照做」）。"""
    t = (text or "").lower()
    if not t:
        return False
    for kw in _META_NOISE_KEYWORDS:
        if kw.lower() in t:
            return True
    if ("agent" in t or "助手" in t) and ("照做" in t or "请执行" in t or "听我" in t):
        return True
    if "我是" in t and ("agent" in t or "助手" in t or "bot" in t):
        return True
    return False


def _norm_fact_text(text: str | None) -> str:
    """事实文本归一键（去空白/标点 + 小写）：复用前瞻意图的 normalize_intent_text（纯函数、零 IO）。"""
    try:
        from app.scheduling.prospective_intent import normalize_intent_text
        return normalize_intent_text(text)
    except Exception:
        return re.sub(r"[\s，。！？、,.!?;；:：\"'“”‘’()（）\[\]【】~～\-—]", "", (text or "").lower())


_CLAUSE_SPLIT_RE = re.compile(r"[，。；;、！？!?]")
# 用户身份/职业自述：「用户是设计师」「用户的职业是设计师」「对方是个老师」
_USER_IDENTITY_RE = re.compile(
    r"^(?:用户|对方)(?:是一名|是一个|是一位|是个|的职业是|的工作是|的职位是|的身份是|为|是)"
    r"\s*(?P<val>.+)$"
)


def _claim_core(text: str | None) -> str:
    """截到第一个分句（身份值只取主干，避免把附加说明算进值里）：「用户是设计师，从事设计工作」→「用户是设计师」。"""
    return _CLAUSE_SPLIT_RE.split((text or "").strip(), 1)[0].strip()


def _user_identity_value(text: str | None) -> str | None:
    """抽取「用户是X」类身份主张的值 X（归一化后）；非身份自述返回 None。纯函数。"""
    m = _USER_IDENTITY_RE.match(_claim_core(text))
    if not m:
        return None
    return _norm_fact_text(m.group("val")) or None


def _is_user_identity_claim(text: str | None) -> bool:
    return _user_identity_value(text) is not None


def _same_identity_value(va: str | None, vb: str | None) -> bool:
    """同一身份主张（含粒度差：「设计师」 vs 「设计师助理」 视为不同值，仅前缀相等才算同一条）。"""
    if not va or not vb:
        return False
    if va == vb:
        return True
    short, long_ = (va, vb) if len(va) <= len(vb) else (vb, va)
    return len(short) >= _MIN_IDENTITY_CORE_LEN and long_.startswith(short)


def _prefix_ends_at_clause_boundary(text: str | None, k: int) -> bool:
    """归一化公共前缀（前 k 个字符）在原文中是否正好停在子句边界。

    把第 k 个归一化字符反查回原文下标 p，看 p 的下一个原文字符：空白/中英文标点，或前缀
    已吃掉整串（nxt 为空）⇒ 成立。逐字符归一化与整串归一化长度不一致时反查不可靠，保守判
    否（宁可漏也不误并）。纯函数、零 IO。
    """
    s = text or ""
    norm_pos: list[int] = []
    for i, ch in enumerate(s):
        norm_pos.extend([i] * len(_norm_fact_text(ch)))
    if len(norm_pos) != len(_norm_fact_text(s)) or not 1 <= k <= len(norm_pos):
        return False
    p = norm_pos[k - 1]
    nxt = s[p + 1] if p + 1 < len(s) else ""
    return nxt == "" or nxt in _CLAUSE_BOUNDARY_CHARS


def _same_fact_text(a: str | None, b: str | None) -> bool:
    """非身份类事实的同义判定（确定性、零 IO、零 LLM）。

    阈值选择依据（生产库实测）：
    - 归一化全等：零误并，但只拦得住逐字重复；
    - 「前缀包含」（短串 >= 6 字）：拦「我是用户的老公」⊂「我是用户的老公，关系稳定」这类
      「短核心 + 附加说明」——生产库 14 条 relationship_baseline 正是这个形态，纯全等/0.9 相似度
      都拦不住；用前缀而非任意子串包含，可保证「喜欢咖啡」/「喜欢喝茶」永不互并；
    - SequenceMatcher >= 0.9：沿用 application/characters.py 既有先例 _WORLD_FACT_SIMILAR_THRESHOLD=0.9
      （比前瞻意图写入期的 0.95 略松），只并近乎逐字重复者，不吞并不同粒度的事实。
    - 共享核心前缀（C13，2026-09-25）：归一化后最长公共前缀 >= _CORE_PREFIX_MIN_LEN 且
      >= _CORE_PREFIX_MIN_FRAC × 短串长度 → 视同一条，专治上面三条漏掉的「共享长核心 + 各自后缀」族。
      C13b（2026-09-25）追加必要条件：该前缀还得停在子句边界（任一边成立即可），否则
      「同模板不同宾语」（喜欢喝美式咖啡 / 喜欢喝拿铁咖啡）会被这一条误并。
    不引入 embedding 向量查重：写入侧禁用额外 LLM/向量推理（本批确定性约束）。
    """
    na, nb = _norm_fact_text(a), _norm_fact_text(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(short) >= _MIN_CORE_LEN and long_.startswith(short):
        return True
    shared = 0
    for ca, cb in zip(na, nb):
        if ca != cb:
            break
        shared += 1
    if shared >= _CORE_PREFIX_MIN_LEN and shared >= _CORE_PREFIX_MIN_FRAC * len(short):
        if (_prefix_ends_at_clause_boundary(a, shared)
                or _prefix_ends_at_clause_boundary(b, shared)):
            return True
    try:
        from app.scheduling.prospective_intent import similar_intent_text
        return similar_intent_text(na, nb, _WORLD_FACT_SIMILARITY)
    except Exception:
        return False


def _same_curated_value(a: str | None, b: str | None) -> bool:
    """curated 行是否同义（身份主张走值比较，其余走文本相似）。"""
    va, vb = _user_identity_value(a), _user_identity_value(b)
    if va is not None and vb is not None:
        return _same_identity_value(va, vb)
    return _same_fact_text(a, b)


def _conflicting_user_identity(a: str | None, b: str | None) -> bool:
    """同主语（用户）、不同值 → 身份画像冲突（需进裁决，而非直接 machine-confirmed + 永不过期）。

    只识别「用户是X」式显式自述，不做角色自述/关系措辞的推断（避免把
    「我是用户的老公」与「我是sam，用户的老公」这类同义改写误判成矛盾）。
    """
    va, vb = _user_identity_value(a), _user_identity_value(b)
    if va is None or vb is None:
        return False
    return not _same_identity_value(va, vb)


async def _find_identity_conflict(db, *, character_id: int, text: str | None):
    """查与该身份主张冲突的 active 稳定画像行（跨 kind；#87 fact 与 #97 constraint 同键即此路径）。"""
    if not _is_user_identity_claim(text):
        return None
    rows = (await db.execute(
        select(WorldFact).where(
            WorldFact.character_id == character_id,
            WorldFact.status == "active",
        ).order_by(WorldFact.id.desc()).limit(200)
    )).scalars().all()
    return next((r for r in rows if _conflicting_user_identity(r.object_value, text)), None)


def _safe_json(s, default=None):
    """安全解析 JSON 数组（失败回落 default）。"""
    if default is None:
        default = []
    try:
        v = json.loads(s or "[]")
        return v if isinstance(v, list) else default
    except Exception:
        return default


def _naive_utc(dt):
    """把（可能带 tz 的）datetime 归一化为 naive UTC，供与 _now_naive() 比较。"""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _predicate_fresh(asserted_at: datetime | None, now: datetime, hours: int) -> bool:
    """瞬时事实是否在指定新鲜窗口内（naive UTC 比较；asserted_at 缺失视为不新鲜，保守不注入）"""
    if asserted_at is None:
        return False
    at = asserted_at.replace(tzinfo=None) if asserted_at.tzinfo else asserted_at
    return (now - at) <= timedelta(hours=hours)


def _status_fresh(asserted_at: datetime | None, now: datetime) -> bool:
    """status 事实是否在新鲜窗口内（兼容包装：等价于 _predicate_fresh(..., STATUS_FRESH_HOURS)）"""
    return _predicate_fresh(asserted_at, now, STATUS_FRESH_HOURS)


def _latest_facts_by_predicate(facts: list, predicates: set) -> list:
    """同 predicate 只保留最新一条（入参已按 asserted_at 倒序）；矛盾状态只注入最新，避免场景错乱（2026-08-16）"""
    seen = set()
    out = []
    for f in facts:
        p = getattr(f, "predicate", None)
        if p in predicates:
            if p in seen:
                continue
            seen.add(p)
        out.append(f)
    return out


def audience_list(audience: list) -> str:
    """audience（[(type,id)...] 或 ["public"]）→ 存储 JSON 字符串。"""
    items = []
    for a in audience or []:
        items.append(a if isinstance(a, str) else f"{a[0]}:{a[1]}")
    return json.dumps(items, ensure_ascii=False)


def audience_visible(stored: str | None, viewer_type: str, viewer_id: int) -> bool:
    """可见性判定：public 所有人可见；否则 viewer 在列表中可见。纯函数。

    viewer_type 归一化：character/char 统一为 char（audience 存储用短名）。
    """
    if not stored:
        return False
    try:
        items = json.loads(stored)
    except Exception:
        return False
    if PUBLIC_AUDIENCE in items:
        return True
    t = "char" if viewer_type in ("character", "char") else viewer_type
    return f"{t}:{viewer_id}" in items


def fact_text(fact: WorldFact) -> str:
    """事实 → 注入文本行（认知状态前缀：FACT 默认不标，其余显式标注）。"""
    prefix = ""
    if getattr(fact, "is_authoritative", False):
        prefix = "[权威] "
    elif fact.epistemic_status and fact.epistemic_status != EPISTEMIC_FACT:
        prefix = f"[{fact.epistemic_status}] "
    _t = getattr(fact, "asserted_at", None) or getattr(fact, "created_at", None)
    _time_tag = f"[记录于 {str(_t)[:10]}] " if _t else ""
    return f"- {_time_tag}{prefix}{fact.object_value[:120]}"


async def assert_fact(
    *, subject_type: str, subject_id: int, predicate: str, object_value: str,
    user_id: int, character_id: int,
    audience: list | None = None,
    epistemic_status: str = EPISTEMIC_FACT,
    confidence: float = 1.0,
    source: str | None = None,
    source_event_id: str | None = None,
    ttl_minutes: int | None = None,
    author: str = "system",
    is_authoritative: bool = False,
    kind: str = KIND_STATUS,
) -> int | None:
    """断言世界事实：旧 active 同键事实 supersede → 插入新事实 → 活跃上限淘汰。失败静默返回 None。

    kind 默认 KIND_STATUS（瞬时状态语义，调用方无需改）；Ariadne 模块F 的 curated 走
    assert_curated，不经过本函数（不受 12 条上限与 12h 新鲜窗影响）。
    """
    try:
        now = _now_naive()
        gate = _admission_gate_on()
        stale_after = None
        async with async_session_factory() as db:
            if gate:
                # ── M4 准入闸门（flag 关=零行为）──
                # 1) 开发运维元信息 / 代理发言不进 world_facts（用户显式设定除外：用户即权威）
                if author != "user" and not is_authoritative and _is_meta_noise(object_value):
                    _logger.info("assert_fact blocked meta-noise char=%d pred=%s: %.40s",
                                 character_id, predicate, object_value)
                    return None
                _p = (predicate or "").strip().lower()
                # 2) preference/setting / 身份职业类不得标 status（长期信息误标瞬时状态 → 永久化或被 TTL 抹掉）
                if kind == KIND_STATUS:
                    if _p in _DURABLE_STATUS_PREDICATES:
                        kind = KIND_PREFERENCE
                    elif _p in _IDENTITY_PREDICATES:
                        kind = KIND_FACT
                # 3) kind='status' 必须有 TTL：兜底取该谓词新鲜窗（location 72h / 其余 12h）
                if kind == KIND_STATUS and ttl_minutes is None:
                    ttl_minutes = _TRANSIENT_FRESH_HOURS.get(_p, STATUS_FRESH_HOURS) * 60
                # 4) 长期层给复核窗口，不永不过期
                if kind != KIND_STATUS:
                    stale_after = now + timedelta(days=DEFAULT_REVIEW_DAYS)
                # 5) 机器写入的用户身份/职业矛盾 → 进 UNVERIFIED 裁决（不直接判 FACT）
                if not is_authoritative and _is_user_identity_claim(object_value):
                    if await _find_identity_conflict(db, character_id=character_id,
                                                     text=object_value) is not None:
                        epistemic_status = EPISTEMIC_UNVERIFIED
                        _logger.info("assert_fact identity conflict char=%d pred=%s: %.40s",
                                     character_id, predicate, object_value)
            old = (await db.execute(
                select(WorldFact).where(
                    WorldFact.user_id == user_id,
                    WorldFact.character_id == character_id,
                    WorldFact.subject_type == subject_type,
                    WorldFact.subject_id == subject_id,
                    WorldFact.predicate == predicate,
                    WorldFact.status == "active",
                )
            )).scalars().all()
            aud = audience_list(audience) if audience is not None else json.dumps(
                [f"user:{user_id}", f"char:{character_id}"], ensure_ascii=False)
            expires_at = (now + timedelta(minutes=ttl_minutes)) if ttl_minutes else None
            f = WorldFact(
                user_id=user_id, character_id=character_id,
                subject_type=subject_type, subject_id=subject_id,
                predicate=predicate, object_value=object_value[:200],
                confidence=max(0.0, min(1.0, float(confidence))),
                epistemic_status=epistemic_status,
                audience=aud, source=source, source_event_id=source_event_id,
                expires_at=expires_at,
                kind=kind if gate else KIND_STATUS,  # 闸门关=不传 kind（与接线前逐字节一致）
                stale_after=stale_after,
                author=author, is_authoritative=is_authoritative,
            )
            db.add(f)
            await db.flush()
            for o in old:
                o.status = "superseded"
                o.superseded_by = f.id
                o.superseded_at = now
            active = (await db.execute(
                select(WorldFact).where(
                    WorldFact.user_id == user_id,
                    WorldFact.character_id == character_id,
                    WorldFact.status == "active",
                    WorldFact.kind == KIND_STATUS,  # Ariadne 模块F：curated 不参与 12 条上限淘汰
                ).order_by(WorldFact.asserted_at.asc())
            )).scalars().all()
            if len(active) > MAX_FACTS_PER_CHAR:
                for extra in active[:len(active) - MAX_FACTS_PER_CHAR]:
                    extra.status = "superseded"
                    extra.superseded_at = now
                    if gate:
                        extra.superseded_by = f.id  # 禁止裸 supersede：停用必须带取代链链接
            await db.commit()
            return f.id
    except Exception as e:
        _logger.warning("assert_fact failed %s/%s/%s: %s", subject_type, subject_id, predicate, e)
        return None


def merge_curated_evidence(
    rep_row, *, sources=None, links=None,
    verify_state=None, stale_after=None, confidence=None,
):
    """把来路证据并入簇代表行（原地更新 rep_row 的 5 个证据列），供 assert_curated 与 C14b 清账共用。

    归并语义与 assert_curated 的 same 分支逐字一致（纯字段归并、不 commit，由调用方落库）：
    - sources_json：与 rep 现值取并集（按元素去重，保留 rep 原序 + 追加新元素）；
    - links_json：取并集（set 去重后排序）；
    - verify_state：按「未确认 < 机器确认 < 人工确认」只升不降；
    - stale_after：给值即覆盖（谁的代表来路更晚由调用方决定后再传入）；
    - confidence：取 max。
    rep_row 只需暴露这 5 个列属性（ORM 行或 SimpleNamespace 均可）；来路值以已解析的
    sources/links 列表与标量 verify_state/stale_after/confidence 传入。
    """
    old_src = _safe_json(rep_row.sources_json)
    merged = old_src + [s for s in (sources or []) if s not in old_src]
    rep_row.sources_json = json.dumps(merged, ensure_ascii=False)
    rep_row.links_json = json.dumps(
        sorted(set(_safe_json(rep_row.links_json)) | set(links or [])), ensure_ascii=False)
    # 人工确认 > 机器确认 > 未确认（只升不降）
    rank = {VERIFY_UNVERIFIED: 0, VERIFY_MACHINE: 1, VERIFY_HUMAN: 2}
    if verify_state is not None and rank.get(verify_state, 0) > rank.get(rep_row.verify_state, 0):
        rep_row.verify_state = verify_state
    if stale_after is not None:
        rep_row.stale_after = stale_after
    if confidence is not None:
        rep_row.confidence = max(float(rep_row.confidence or 0), float(confidence))
    return rep_row


async def assert_curated(
    db, *, character_id: int, user_id: int, kind: str,
    object_value: str, predicate: str = "curated",
    subject_type: str = "character", subject_id: int | None = None,
    audience: list[str] | None = None, source: str | None = None,
    source_event_id: str | None = None, confidence: float = 1.0,
    verify_state: str = VERIFY_MACHINE, sources: list[dict] | None = None,
    links: list[str] | None = None, stale_after: datetime | None = None,
    epistemic: str = EPISTEMIC_FACT,
) -> WorldFact | None:
    """写入/更新一条 curated 长期知识（独立于 12 条上限与 12h 新鲜窗）。

    同 (character_id, kind, predicate, object_value) 已存在 active 行 → 更新（不新增重复）。
    调用方负责 commit（与 assert_fact 一致，不内部提交）。
    M4 闸门开时（flag `memory_admission_gate`）额外做：元信息拦截（命中返回 None）、语义查重
    （同义 → 合并到既有权威记录、不新增行）、身份/职业矛盾 → UNVERIFIED 进裁决、身份类统一
    review 窗口（不永不过期）。闸门关 = 以上全部短路，逐字节旧行为。
    """
    if kind not in CURATED_KINDS:
        raise ValueError(f"assert_curated: bad kind {kind!r}")
    obj = (object_value or "").strip()
    if not obj:
        raise ValueError("assert_curated: empty object_value")
    sid = subject_id if subject_id is not None else character_id
    aud = audience or ["public"]
    gate = _admission_gate_on()

    # ── M4 准入闸门（flag 关=零行为）──
    if gate and _is_meta_noise(obj):
        _logger.info("assert_curated blocked meta-noise char=%d kind=%s: %.40s", character_id, kind, obj)
        return None
    # kind='status'（瞬时层）不可能进本函数：CURATED_KINDS 已在上面排除，长期偏好/setting
    # 归长期层由 assert_fact 的 _DURABLE_STATUS_PREDICATES 收口。

    # 同键 active 行 → 更新（保守合并：verify 只升不降，sources 取并集）
    existing = (await db.execute(
        select(WorldFact).where(
            WorldFact.character_id == character_id,
            WorldFact.status == "active",
            WorldFact.kind == kind,
            WorldFact.predicate == predicate,
        ).order_by(WorldFact.id.desc())
    )).scalars().all()
    if gate:
        # 查重池放宽到该角色全部 active curated 层（跨 kind）：#87 fact 与 #97 constraint 同义即此路径
        pool = (await db.execute(
            select(WorldFact).where(
                WorldFact.character_id == character_id,
                WorldFact.status == "active",
                WorldFact.kind.in_(tuple(CURATED_KINDS)),
            ).order_by(WorldFact.id.desc()).limit(200)
        )).scalars().all()
        # 同义 → supersede 到既有权威记录（不新增行；不改既有行的 kind，避免层间来回翻）
        same = next((r for r in pool if _same_curated_value(r.object_value, obj)), None)
    else:
        same = next((r for r in existing if (r.object_value or "").strip() == obj), None)
    if same is not None:
        merge_curated_evidence(
            same, sources=sources, links=links, verify_state=verify_state,
            stale_after=stale_after, confidence=confidence,
        )
        db.add(same)
        return same

    row_verify = verify_state
    row_epistemic = epistemic
    row_authoritative = True
    row_stale = stale_after
    if gate:
        conflict = next((r for r in pool if _conflicting_user_identity(r.object_value, obj)), None)
        if conflict is not None:
            # 身份/职业矛盾 → 写 UNVERIFIED 进裁决，不允许直接 machine-confirmed + 永不过期
            row_verify = VERIFY_UNVERIFIED
            row_epistemic = EPISTEMIC_UNVERIFIED
            row_authoritative = False
            row_stale = _now_naive() + timedelta(days=DEFAULT_REVIEW_DAYS)
            _logger.info("assert_curated identity conflict char=%d kind=%s new=%.30s old#%s=%.30s",
                         character_id, kind, obj, getattr(conflict, "id", None), conflict.object_value)
        elif _is_user_identity_claim(obj) and row_stale is None:
            # 身份/职业类事实统一 review 窗口（不永不过期）
            row_stale = _now_naive() + timedelta(days=DEFAULT_REVIEW_DAYS)

    row = WorldFact(
        user_id=user_id, character_id=character_id,
        subject_type=subject_type, subject_id=sid,
        predicate=predicate, object_value=obj[:1000],
        status="active", confidence=confidence, epistemic_status=row_epistemic,
        audience=json.dumps(aud, ensure_ascii=False), author="system",
        is_authoritative=row_authoritative, source=source, source_event_id=source_event_id,
        kind=kind, verify_state=row_verify,
        sources_json=json.dumps(sources or [], ensure_ascii=False),
        links_json=json.dumps(links or [], ensure_ascii=False),
        stale_after=row_stale,
    )
    db.add(row)
    return row


async def get_curated_facts(
    *, character_id: int, user_id: int,
    viewer_type: str = "character", viewer_id: int | None = None,
    user_text: str = "",
) -> dict[str, list[WorldFact]]:
    """按 kind 返回该角色可见的 curated 知识（确定性，不走向量、不衰减、不计 12 上限）。

    - constraint：无条件取 CURATED_CONSTRAINT_TOPN 条（人格铁律必须在场）；
    - 其余 kind：每类取 Top N「核心」（confidence/verify 高、asserted 新），
      若 user_text 命中其 links/内容关键词则优先提到最前（触发键确定性命中）。
    返回 {kind: [rows]}，供 context section 分块渲染。
    """
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(WorldFact).where(
                WorldFact.character_id == character_id,
                WorldFact.status == "active",
                WorldFact.kind.in_(tuple(CURATED_KINDS)),
            ).order_by(WorldFact.kind.asc(), WorldFact.confidence.desc(), WorldFact.asserted_at.desc())
        )).scalars().all()

    out: dict[str, list] = {k: [] for k in (KIND_CONSTRAINT, KIND_FACT, KIND_PREFERENCE, KIND_RELATION_BASE)}
    text = (user_text or "").lower()
    v_id = viewer_id if viewer_id is not None else character_id
    for r in rows:
        if not audience_visible(r.audience, viewer_type, v_id):  # 复用既有可见性判定（与 get_active_facts 同口径）
            continue
        out.setdefault(r.kind, []).append(r)

    def _trigger_hit(r: WorldFact) -> bool:
        if not text:
            return False
        links = [str(x).lower() for x in _safe_json(r.links_json)]
        return any(k and k in text for k in links) or (r.object_value or "").lower()[:12] in text

    result: dict[str, list] = {}
    for kind, items in out.items():
        cap = CURATED_CONSTRAINT_TOPN if kind == KIND_CONSTRAINT else CURATED_TOPN_PER_KIND
        hit = [r for r in items if _trigger_hit(r)]
        rest = [r for r in items if r not in hit]
        result[kind] = (hit + rest)[:cap]
    return result


def curated_line(r: WorldFact) -> str:
    """curated 事实 → 注入文本行（constraint 用更强的祈使语气前缀；到期 stale_after 加 [待复核]）。"""
    now = _now_naive()
    stale = " [待复核]" if (r.stale_after is not None and _naive_utc(r.stale_after) <= now) else ""
    prefix = {
        KIND_CONSTRAINT: "[铁律]",
        KIND_FACT: "[稳定事实]",
        KIND_PREFERENCE: "[长期偏好]",
        KIND_RELATION_BASE: "[关系基线]",
    }.get(r.kind, "[编纂知识]")
    return f"- {prefix}{stale} {r.object_value[:160]}"


async def get_active_facts(
    *, character_id: int, user_id: int,
    viewer_type: str = "character", viewer_id: int | None = None,
    limit: int = 8,
) -> list[WorldFact]:
    """当前可见的活跃事实（audience 过滤 + 过期过滤）。"""
    try:
        now = _now_naive()
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(WorldFact).where(
                    WorldFact.character_id == character_id,
                    WorldFact.user_id == user_id,
                    WorldFact.status == "active",
                ).order_by(WorldFact.is_authoritative.desc(), WorldFact.asserted_at.desc()).limit(limit * 4)  # 权威事实优先（P1-3）；预取放大防过期/不可见事实占位截断
            )).scalars().all()
        out = []
        v_id = viewer_id if viewer_id is not None else character_id
        for f in rows:
            if f.expires_at is not None:
                exp = f.expires_at.replace(tzinfo=None) if f.expires_at.tzinfo else f.expires_at
                if exp <= now:
                    continue
            # 瞬时状态新鲜度兜底（2026-08-16 起 status；C2-② 2026-09-10 泛化到 mood/location）：
            # 兼容无 TTL 旧数据——status/mood 12h、location 72h，超窗不注入。
            fresh_h = _TRANSIENT_FRESH_HOURS.get(f.predicate)
            if fresh_h is not None and not _predicate_fresh(f.asserted_at, now, fresh_h):
                continue
            if audience_visible(f.audience, viewer_type, v_id):
                out.append(f)
        # 矛盾瞬时事实只取最新（2026-08-16 status/activity；C2-① 2026-09-10 补 location/mood）：
        # 同 predicate 只注入最新一条，避免场景错乱/旧城市常驻。
        out = _latest_facts_by_predicate(out, {"status", "activity", "location", "mood"})
        # 权威事实稳定优先（P1-3）：同一角色多条事实时权威设定不被瞬时状态挤掉
        out = sorted(out, key=lambda f: (0 if getattr(f, "is_authoritative", False) else 1), reverse=False)
        return out[:limit]
    except Exception as e:
        _logger.warning("get_active_facts failed char=%d: %s", character_id, e)
        return []


async def get_character_view(character_id: int, user_id: int, limit: int = 6) -> str:
    """角色视角的世界状态文本（注入对话上下文用；失败静默返回空串）。"""
    facts = await get_active_facts(
        character_id=character_id, user_id=user_id,
        viewer_type="character", viewer_id=character_id, limit=limit,
    )
    if not facts:
        return ""
    return "\n".join(fact_text(f) for f in facts)


async def get_fact_history(
    *, character_id: int, user_id: int,
    subject_type: str, subject_id: int, predicate: str,
    limit: int = FACT_HISTORY_MAX_VERSIONS,
) -> dict:
    """事实「修正历史」只读查询（小增量 2026-09-16，零新表、零写）。

    返回某 (subject_type, subject_id, predicate) 槽的当前值 + 历史版本链：
    该槽 WorldFact 行（含 active / superseded），按 asserted_at 倒序（同刻按 id 倒序，保证确定序）；
    current = 版本链里 status=='active' 的那条（无则 None）。

    版本上限 ``FACT_HISTORY_MAX_VERSIONS``：status/activity 这类槽位会逐轮 supersede（每轮新增一行），
    不设上限会随会话量放大为无界载荷；超限时只回最近 N 版并置 ``truncated=True``（当前值恒在最新版内）。

    口径与既有世界事实接口一致：按 (character_id, user_id) 隔离（用户只看到自己名下的事实槽）；
    本函数只做 SELECT，不动任何写入裁决逻辑。
    """
    cap = max(1, int(limit))
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(WorldFact).where(
                WorldFact.character_id == character_id,
                WorldFact.user_id == user_id,
                WorldFact.subject_type == subject_type,
                WorldFact.subject_id == subject_id,
                WorldFact.predicate == predicate,
            ).order_by(WorldFact.asserted_at.desc(), WorldFact.id.desc())
            .limit(cap + 1)  # 多取一条判截断，免额外 COUNT
        )).scalars().all()
    truncated = len(rows) > cap
    rows = rows[:cap]
    versions = [{
        "id": r.id,
        "object_value": r.object_value,
        "status": r.status,
        "author": r.author,
        "source": r.source,
        "is_authoritative": bool(r.is_authoritative),
        "epistemic_status": r.epistemic_status,
        "asserted_at": r.asserted_at.isoformat() if r.asserted_at else None,
        "superseded_at": r.superseded_at.isoformat() if r.superseded_at else None,
        "superseded_by": r.superseded_by,
    } for r in rows]
    current = next((v for v in versions if v["status"] == "active"), None)
    return {"current": current, "versions": versions, "truncated": truncated}


async def fold_status_update(character_id: int, user_id: int, status_text: str) -> None:
    """聊天【状态更新】标记 → 角色当前状态事实（FACT，audience=[用户,角色]，TTL 12h）。"""
    text = (status_text or "").strip()
    if not text:
        return
    await assert_fact(
        subject_type="character", subject_id=character_id, predicate="status",
        object_value=text, user_id=user_id, character_id=character_id,
        audience=[("user", user_id), ("char", character_id)],
        epistemic_status=EPISTEMIC_FACT, confidence=0.9, source="chat_status",
        ttl_minutes=STATUS_FRESH_HOURS * 60,  # 瞬时状态 12h 自动过期（2026-08-16）
    )


async def fold_activity(character_id: int, user_id: int, activity_type: str, summary: str = "") -> None:
    """Life 活动完成 → 角色最近活动事实（FACT）。"""
    text = (summary or activity_type or "").strip()[:120]
    if not text:
        return
    await assert_fact(
        subject_type="character", subject_id=character_id, predicate="activity",
        object_value=text, user_id=user_id, character_id=character_id,
        audience=[("user", user_id), ("char", character_id)],
        epistemic_status=EPISTEMIC_FACT, confidence=0.85, source="life_activity",
        ttl_minutes=3 * 24 * 60,  # 活动事实 3 天过期，防旧活动内容常驻（2026-08-17）
    )
