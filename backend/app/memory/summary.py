"""记忆置顶摘要：按类型 LLM 概括最近记忆（6 小时节流 + A41「有新事实即失效」）"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, true

from app.db.database import async_session_factory
from app.models.memory import Memory
from app.utils.logger import get_logger
from app.utils.timeutil import now_naive_utc, to_naive_utc
from app.memory.constants import SUMMARY_TTL_HOURS, _TYPE_CN

_logger = get_logger("memory.summary")


def _perception_isolate_on() -> bool:
    """批 0-2 M2（2026-09-28）：隔离禁令总闸，默认关（关＝逐字节旧查询）。异常回落 False（R8：退得干净）。"""
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("perception_isolate", False))
    except Exception:
        return False


def _not_quarantined_clause():
    """摘要原料来源排除（禁令 2）：被隔离条不进置顶摘要 / 身份画像取料。

    专治「二阶放大」（方案路径 C）：一条被感知污染的记忆若被凝成置顶摘要 / 身份画像，就会以
    ``source="summary"`` 的名义长期回注 prompt，比单条记忆影响面大得多。
    口径：**只用既有列**（source / epistemic_status）加 WHERE 条件，不改生成逻辑、prompt、条数上限；
    谓句与 M0 纯函数 ``is_quarantined`` 同判（来源 perception 且认知状态非 FACT），
    NULL / 大小写与首尾空白差异用 SQL 函数归一（列值本由应用层写入，这里只是防御脏数据）。
    flag 关 ⇒ 返回永真条件 ⇒ 查询结果逐字节不变。
    """
    if not _perception_isolate_on():
        return true()
    from app.memory.perception_tier import FACT_STATUS, PERCEPTION_SOURCE

    src = func.lower(func.trim(func.coalesce(Memory.source, "")))
    st = func.upper(func.trim(func.coalesce(Memory.epistemic_status, "")))
    return or_(src != PERCEPTION_SOURCE, st == FACT_STATUS)


_OVERRIDE_NOW = None


def _newest_pinned(rows):
    """同组多条置顶时确定性选出「该更新的那条」：时间最新，时间相同取 id 最大者。

    A31 根因 2 的落点：原先「节流看 max 时间、重写写 existing[0]」，而 existing 查询无 order_by
    ⇒ SQLite 按 rowid 升序返回 ⇒ 写入的是**最旧**那条（前端不显示的条）。选条与写条必须同一条。
    id 最大作同时间 tie-break，与前端「遍历后写覆盖」最终留下的那条同向。
    """
    if not rows:
        return None
    return max(rows, key=lambda m: (to_naive_utc(m.updated_at or m.created_at) or datetime.min, m.id))


def _demote_other_pins(existing, keep_id: int) -> int:
    """收口：同组其余旧置顶降级（保留行、不物理删，可追溯）。返回降级条数。

    对象由调用方的 session 载入，改属性即被跟踪，随该 session 的 commit 一起落库。
    """
    demoted = 0
    for r in existing:
        if r.id != keep_id and r.is_pinned:
            r.is_pinned = False
            demoted += 1
    return demoted


def _bucket_of(sub_type) -> str:
    # 与 DB 部分唯一索引 ux_memories_pinned_active 同口径的桶名（迁移 d1a2b3c4e5f6）：
    # sub_type 为 NULL／空／summary 一律折成空串（普通摘要桶），其余按自身值分桶。
    return "" if sub_type in (None, "", "summary") else sub_type


async def _release_bucket_pins(db, character_id: int, memory_type: str, sub_type) -> int:
    # 插入新置顶前，把同桶仍挂着的置顶一律放开（只看 is_pinned／is_archived，不看 status）。
    # 2026-10-09 现场（用户报「印象重新生成失败」）：existing 查询带 status == active
    # （current_facts_active_only 默认开），而 DB 的部分唯一索引只认 is_pinned=1 AND is_archived=0
    # ⇒ 一条 status=stale 的旧置顶对代码不可见、对索引可见 ⇒ 看不见就走 INSERT ⇒ 撞唯一索引
    # （sqlite3.IntegrityError: UNIQUE constraint failed: index ux_memories_pinned_active），
    # 该桶的印象／画像重生成永久失败（生产实测 char 6 的 user_info 两桶、char 13 的 summary 桶都红）。
    # 本函数按索引口径放开它们，让代码与约束不再打架；只改 is_pinned，不物理删行、不动内容。
    want = _bucket_of(sub_type)
    rows = (await db.execute(
        select(Memory).where(
            Memory.character_id == character_id,
            Memory.memory_type == memory_type,
            Memory.is_pinned == True,  # noqa: E712
            Memory.is_archived == False,  # noqa: E712
        )
    )).scalars().all()
    released = 0
    for r in rows:
        if r.is_pinned and _bucket_of(r.sub_type) == want:
            r.is_pinned = False
            released += 1
    return released


def _rel_time(dt, now=None) -> str:
    """相对时间中文描述（今天/昨天/N天前/周前/月前/很久以前）"""
    if dt is None:
        return "时间不明"
    dt = dt.replace(tzinfo=None) if dt.tzinfo else dt
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    days = (now - dt).total_seconds() / 86400.0
    if days < 1:
        return "今天"
    if days < 2:
        return "昨天"
    if days < 7:
        return f"{int(days)}天前"
    if days < 30:
        return f"{int(days // 7)}周前"
    if days < 365:
        return f"{int(days // 30)}个月前"
    return "很久以前"


# ── A41（2026-10-10，A37 批 4）：摘要「按事实淘汰」（审计 C30/C32/C34 的正修法）──
# 旧口径只按时间淘汰：置顶摘要写完就要等满 6h（身份画像 24h），期间哪怕又落了一批新记忆也不会重写
# ⇒ 陈旧快照被 `source="summary"` 的名义长期回注 prompt（二阶放大，比单条记忆影响面大）。
# 新增失效路径：**原料里已有比本摘要更新的事实 ⇒ 判过期**，不必等满 TTL。
# 成本护栏（无 flag，改数值即改常量）：
#   · 条数门槛 STALE_NEW_MATERIAL_MIN——零碎写一条就重写等于把 LLM 当轮询打；
#   · 重写地板 *_REWRITE_FLOOR——两次重写之间的最小间隔，最坏情况成本有上界
#     （身份画像每 5 分钟被 scheduler 问一次，没有地板就会退化成「每 5 分钟一次 LLM」）。
# 无新原料时逐字走旧判据（`_pin_still_usable` 的 `return age < ttl` 分支）。
STALE_NEW_MATERIAL_MIN = 3
SUMMARY_REWRITE_FLOOR = timedelta(hours=1)     # 置顶摘要：6h TTL ⇒ 最快 1h 一次
IDENTITY_REWRITE_FLOOR = timedelta(hours=6)    # 身份画像：24h TTL ⇒ 最快 6h 一次


def _pin_still_usable(last: datetime, *, now: datetime, ttl: timedelta,
                      new_material: int, floor: timedelta) -> bool:
    """这条置顶是否可以沿用（True＝不重写）。

    迁移前语义在 ``new_material == 0`` 时逐字保留：``now - last < ttl`` 就沿用。
    迁移后多一条按事实淘汰：新原料够数且已过地板 ⇒ 即便 TTL 没走完也判过期。
    """
    age = now - last
    if new_material >= STALE_NEW_MATERIAL_MIN and age >= floor:
        return False
    return age < ttl


async def _new_material_count(db, clauses, since) -> int:
    """``since`` 之后新写入的原料条数（只数不取内容，WHERE 由调用方按自家取料条件传进来）。

    与生成侧的取料查询**同口径**（含 ``_active_status_clause`` / ``_not_quarantined_clause``）——
    数错了方向必须偏向「少数 ⇒ 不重写」，否则成本护栏会被绕过。
    """
    row = await db.execute(
        select(func.count()).select_from(Memory).where(Memory.created_at > since, *clauses)
    )
    return int(row.scalar() or 0)


async def summarize_memories(character_id: int, memory_type: str, force: bool = False) -> dict:
    """生成/更新某类型的置顶摘要记忆（is_pinned=1）。默认 6 小时内不重复生成；force=True 强制重新生成。

    A41：6h 之内若同类型原料又落了 ≥ ``STALE_NEW_MATERIAL_MIN`` 条比本摘要更新的记忆，则**按事实
    淘汰**、最早隔 ``SUMMARY_REWRITE_FLOOR`` 重写一次（不再必须等满 6h）。
    """
    from datetime import datetime, timezone, timedelta
    from app.agent.llm_client import chat_completion
    from app.models.character import AICharacter

    label = _TYPE_CN.get(memory_type, memory_type)
    from app.memory.service import _active_status_clause  # #70-C：失效记忆不进摘要（flag 关=永真）
    async with async_session_factory() as db:
        # 已有置顶摘要 → 节流判断
        q = select(Memory).where(
            Memory.character_id == character_id,
            Memory.memory_type == memory_type,
            Memory.is_pinned == True,  # noqa: E712
            Memory.is_archived == False,  # noqa: E712
            _active_status_clause(),
        )
        if memory_type == "user_info":
            # A31 根因 4：普通「印象」摘要与 identity 身份画像分桶，互不污染
            # （画像由 summarize_identity 独立管理；混在一个桶会让画像顶掉印象位、并互相误降级）
            q = q.where(or_(Memory.sub_type == "summary", Memory.sub_type.is_(None)))
        existing = (await db.execute(q)).scalars().all()
        newest = _newest_pinned(existing)
        if newest is not None and not force:
            last = to_naive_utc(newest.updated_at or newest.created_at)
            if isinstance(last, datetime):
                # A41：先数「比这条摘要更新的原料」，够数且过了重写地板 ⇒ 按事实淘汰（不等满 6h）
                material = [
                    Memory.character_id == character_id,
                    Memory.memory_type == memory_type,
                    Memory.is_pinned == False,  # noqa: E712
                    Memory.is_archived == False,  # noqa: E712
                    _active_status_clause(),
                    _not_quarantined_clause(),
                ]
                if _pin_still_usable(last, now=now_naive_utc(),
                                     ttl=timedelta(hours=SUMMARY_TTL_HOURS),
                                     new_material=await _new_material_count(db, material, last),
                                     floor=SUMMARY_REWRITE_FLOOR):
                    return {"generated": False, "memory_id": newest.id, "reason": "throttled"}

        # 最近 20 条该类型非摘要记忆
        result = await db.execute(
            select(Memory)
            .where(
                Memory.character_id == character_id,
                Memory.memory_type == memory_type,
                Memory.is_pinned == False,
                Memory.is_archived == False,
                _active_status_clause(),
                _not_quarantined_clause(),  # 批 0-2 M2 禁令 2：被隔离条不进摘要原料（关=永真）
            )
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
            .limit(20)
        )
        memories = result.scalars().all()
        if not memories:
            return {"generated": False, "memory_id": None, "reason": "no_memories"}

        char = await db.get(AICharacter, character_id)
        char_name = char.name if char else "AI"
        # 2026-08-08 时间逻辑修复：每条记忆标注相对发生时间，防止把旧事写成"今天/最近"
        contents = "\n".join(f"- {m.content[:100]}（{_rel_time(m.created_at)}）" for m in memories)
        prompt = (
            f"你是{char_name}。以下是关于用户的若干条记忆条目（{label}类），括号内是各条目发生的时间：\n{contents}\n\n"
            f"请用1-2句话概括出当下最重要、最值得记住的内容，作为该类型的置顶提炼，"
            f"要求信息凝练、口语化、以AI自己的视角。忠实于记忆条目本身，不要引入条目中没有的性别代词或异性恋假设。"
            f"时间规则（重要）：必须按条目标注的时间准确描述——标注'很久以前'或'N个月前/N周前'的旧事，"
            f"禁止使用'今天/昨天/最近'等时间词；时间不明确时用'之前/某次'等中性表述；不要编造具体日期。"
            f"直接输出概括内容，不要加序号和引号。"
        )
        response = await chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=200,
            task="memory", user_id=(char.user_id if char else 1),
        )
        summary = (response or "").strip().strip('"').strip("'")
        if len(summary) < 4:
            return {"generated": False, "memory_id": None, "reason": "empty_output"}

        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        owner_user_id = char.user_id if char else 0
        if newest is not None:
            target = newest
            target.content = summary
            target.importance = 100.0
            target.user_id = owner_user_id
            target.updated_at = now_naive
            demoted = _demote_other_pins(existing, target.id)  # A31 根因 1：同组旧置顶收口
            await db.commit()
            if demoted:
                _logger.info("Pinned summary for char=%d type=%s: demoted %d stale pins",
                             character_id, memory_type, demoted)
            return {"generated": True, "memory_id": target.id}
        # A49：插入前按索引口径放开同桶旧置顶——被 status 过滤掉的 stale 置顶也在内，
        # 否则会撞 ux_memories_pinned_active（用户 10-09 报的「印象重新生成失败」）。
        released = await _release_bucket_pins(db, character_id, memory_type, "summary")
        if released:
            _logger.info("Pinned summary char=%d type=%s: released %d stale/invisible pins",
                         character_id, memory_type, released)
        mem = Memory(
            user_id=owner_user_id, character_id=character_id, memory_type=memory_type,
            sub_type="summary", source="summary", content=summary,
            importance=100.0, is_pinned=True,
        )
        db.add(mem)
        await db.commit()
        await db.refresh(mem)
        _logger.info("Pinned summary generated for char=%d type=%s: %.40s", character_id, memory_type, summary)
        return {"generated": True, "memory_id": mem.id}


# ── 记忆架构 v2.1 Phase 5：身份画像提炼（复用置顶摘要模式，24h 节流）──
IDENTITY_TTL_HOURS = 24
IDENTITY_SUB_TYPE = "identity"


async def summarize_identity(character_id: int, user_id: int, force: bool = False) -> dict:
    """生成/刷新「身份画像」置顶记忆（memory_type=user_info, sub_type=identity, is_pinned=1）。

    输入：user_info 类记忆 + 意义记忆（why_it_matters 非空）最近 20 条；
    输出：1-2 句 AI 第一人称的长期用户画像（价值/动机/性格模式），24 小时节流。

    A41：同样带「新事实即失效」——两路原料合起来 ≥ ``STALE_NEW_MATERIAL_MIN`` 条比本画像更新时，
    最早隔 ``IDENTITY_REWRITE_FLOOR`` 重写（scheduler 每 5 分钟问一次，靠地板挡住高频重写）。
    """
    from datetime import datetime, timezone, timedelta
    from app.agent.llm_client import chat_completion
    from app.models.character import AICharacter

    # 记忆架构 v2.1 开关：关闭时不生成/刷新身份画像（灰度语义）
    from app.memory.flags import memory_v2_enabled
    if not await memory_v2_enabled(character_id):
        return {"generated": False, "memory_id": None, "reason": "disabled"}

    async with async_session_factory() as db:
        from app.memory.service import _active_status_clause  # #70-C：失效记忆不进摘要（flag 关=永真）
        existing_result = await db.execute(
            select(Memory).where(
                Memory.character_id == character_id,
                Memory.memory_type == "user_info",
                Memory.sub_type == IDENTITY_SUB_TYPE,
                Memory.is_pinned == True,
                Memory.is_archived == False,
                _active_status_clause(),
            )
        )
        existing = existing_result.scalars().all()
        newest = _newest_pinned(existing)
        if newest is not None and not force:
            last = to_naive_utc(newest.updated_at or newest.created_at)
            if isinstance(last, datetime):
                # A41：画像原料＝user_info 条 + 意义记忆条，两路各自数「比这条更新的事实」
                base = [Memory.character_id == character_id,
                        Memory.is_archived == False,  # noqa: E712
                        _active_status_clause(),
                        _not_quarantined_clause()]
                new_material = await _new_material_count(
                    db, [*base, Memory.is_pinned == False, Memory.memory_type == "user_info"], last)  # noqa: E712
                new_material += await _new_material_count(
                    db, [*base, Memory.why_it_matters.is_not(None)], last)
                if _pin_still_usable(last, now=now_naive_utc(),
                                     ttl=timedelta(hours=IDENTITY_TTL_HOURS),
                                     new_material=new_material, floor=IDENTITY_REWRITE_FLOOR):
                    return {"generated": False, "memory_id": newest.id, "reason": "throttled"}

        rows = (await db.execute(
            select(Memory)
            .where(
                Memory.character_id == character_id,
                Memory.is_archived == False,
                Memory.is_pinned == False,
                Memory.memory_type == "user_info",
                _active_status_clause(),
                _not_quarantined_clause(),  # 批 0-2 M2 禁令 2：身份画像取料同样排除被隔离条
            )
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
            .limit(20)
        )).scalars().all()
        meaning_rows = (await db.execute(
            select(Memory)
            .where(
                Memory.character_id == character_id,
                Memory.is_archived == False,
                Memory.why_it_matters.is_not(None),
                _active_status_clause(),
                _not_quarantined_clause(),  # 同上（意义记忆也是画像原料）
            )
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
            .limit(20)
        )).scalars().all()
        if not rows and not meaning_rows:
            return {"generated": False, "memory_id": None, "reason": "no_memories"}

        char = await db.get(AICharacter, character_id)
        char_name = char.name if char else "AI"
        parts = [f"- {m.content[:100]}（{_rel_time(m.created_at)}）" for m in rows]
        parts += [f"- {m.content[:100]}（意义：{m.why_it_matters[:60]}；{_rel_time(m.created_at)}）" for m in meaning_rows]
        contents = "\n".join(parts[:24])
        prompt = (
            f"你是{char_name}。以下是你长期观察用户积累的信息（印象 + 意义），括号内是发生时间：\n{contents}\n\n"
            "请用1-2句话概括用户的长期身份画像：他/她是什么样的人、最看重什么、行为模式如何。"
            "要求凝练、口语化、以AI自己的视角，忠实于条目，不要引入条目中没有的性别代词或异性恋假设。"
            "时间规则：身份画像是长期总结，避免使用'今天/最近'等短时间词；旧条目用'之前/以往'等中性表述。"
            "直接输出概括内容，不要加序号和引号。"
        )
        response = await chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=200,
            task="memory", user_id=user_id,
        )
        summary = (response or "").strip().strip('"').strip("'")
        if len(summary) < 4:
            return {"generated": False, "memory_id": None, "reason": "empty_output"}

        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        owner_user_id = char.user_id if char else 0
        if newest is not None:
            target = newest
            target.content = summary
            target.importance = 100.0
            target.user_id = owner_user_id
            target.updated_at = now_naive
            demoted = _demote_other_pins(existing, target.id)  # 只收 identity 桶，不碰普通摘要
            await db.commit()
            if demoted:
                _logger.info("Identity summary for char=%d: demoted %d stale pins", character_id, demoted)
            return {"generated": True, "memory_id": target.id}
        # A49：身份画像同款——插入前按索引口径放开 identity 桶的旧置顶。
        released = await _release_bucket_pins(db, character_id, "user_info", IDENTITY_SUB_TYPE)
        if released:
            _logger.info("Identity summary char=%d: released %d stale/invisible pins", character_id, released)
        mem = Memory(
            user_id=owner_user_id, character_id=character_id, memory_type="user_info",
            sub_type=IDENTITY_SUB_TYPE, source="summary", content=summary,
            importance=100.0, is_pinned=True,
        )
        db.add(mem)
        await db.commit()
        await db.refresh(mem)
        _logger.info("Identity summary generated for char=%d: %.40s", character_id, summary)
        return {"generated": True, "memory_id": mem.id}
