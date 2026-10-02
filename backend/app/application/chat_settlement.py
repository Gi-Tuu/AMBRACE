"""回合结算层：关系温度、状态与自述、关系驱力、念头池的回合末结算。

本模块自 ``application/chat_service.py`` 逐字节搬入（A20 批 5 第二刀，2026-10-02）。
边界＝**关系温度 / 状态与自述 / 关系驱力 / 念头池的回合结算**；不生成消息、不发消息。

机械改写只有两类（判定/文案/返回值一字未改）：

1. 缩进（原样为 0，未动）。
2. 被 tests/ 按 chat_service 打桩的名字，与本组用到的 chat_service 模块级常量，改走 ``_cs.<name>``
   （函数体内 ``from app.application import chat_service as _cs``，**不得提到顶层**——顶层会成环）：
   ``async_session_factory``（``setattr(chat_service, "async_session_factory", …)`` 实测 9 处）、
   ``spawn_background``（3 处）、``SELF_STATEMENT_MAX_LEN``（chat_service 的模块级常量，只能回指取，
   具名 import 会与本模块对 chat_service 的函数内 import 争先后）。
   本模块**不得**具名 import 这三名，否则桩静默失效 ⇒ 退化成真查库 / 真起后台任务。

**本组零兄弟互调**（8 个函数互相之间没有调用点，只有 ``_settle_thought_pool_turn`` 的 docstring 拿
``_settle_relational_drive`` 当口径参照），故本模块没有 ``_cs.<兄弟函数>`` 回指；``_trigger_state_eval``
等仍由留在 chat_service 的 ``_run_post_processing`` 调用，解析点在 chat_service 自身命名空间。

``_bump_relationship`` 函数体内自带 ``from app.db.database import async_session_factory``（搬家前就是局部
import、遮蔽模块级同名），逐字节保留 ⇒ 它不经 ``_cs``，打桩对它无效（与搬前行为一字一致）。
``_initial_bio_done``（进程内初始自述去重集合）随 ``_generate_initial_bio`` 一起搬入，是本模块模块级状态；
库内除本函数外无人引用。

其余依赖（模型/SQLAlchemy/标准库）按原模块原名直接 import；logger 名沿用 ``services.chat``（D-1 口径）。
chat_service 侧保留 8 个具名重导出，tests 的打桩面（22 名 / 86 处）零迁移。
"""
from sqlalchemy import select

from app.models.character import AICharacter
from app.utils.logger import get_logger

_logger = get_logger("services.chat")


async def _save_bio_update(character_id: int, bio_text: str, user_id: int):
    """保存自述更新到角色表（写入独立自述字段，不覆盖用户提供的背景信息 bio）"""
    from app.application import chat_service as _cs  # 被桩依赖与本组常量在 chat_service 命名空间现取
    if not bio_text:
        return
    try:
        async with _cs.async_session_factory() as db:
            result = await db.execute(select(AICharacter).where(AICharacter.id == character_id))
            char = result.scalar_one_or_none()
            if char:
                char.self_statement = bio_text[:_cs.SELF_STATEMENT_MAX_LEN]
                await db.flush()
                await db.commit()
                _logger.info("Bio updated for character %d: %.60s", character_id, bio_text)
    except Exception as e:
        _logger.warning("Bio update failed: %s", e)


# 进程内去重：已生成过初始自述的角色不再重复触发
_initial_bio_done: set[int] = set()


async def _generate_initial_bio(character_id: int, user_id: int) -> None:
    """角色无自述时，用 LLM 依据人格/风格/关系生成初始自述（异步、失败静默）"""
    from app.application import chat_service as _cs  # 被桩依赖与本组常量在 chat_service 命名空间现取
    if character_id in _initial_bio_done:
        return
    try:
        from app.agent.llm_client import chat_completion, get_user_llm_config
        async with _cs.async_session_factory() as db:
            result = await db.execute(select(AICharacter).where(AICharacter.id == character_id))
            char = result.scalar_one_or_none()
            if not char or (char.self_statement and char.self_statement.strip()):
                _initial_bio_done.add(character_id)
                return
            cfg = await get_user_llm_config(user_id)
            prompt = (
                "你是角色设定撰写助手。请以第一人称写一段简短自述（60 字以内），"
                "说明这个角色是谁、性格特点、与用户的关系。只输出自述正文，不要任何前缀或解释。\n"
                f"角色名：{char.name or 'AI'}\n"
                f"人格：{char.personality or '暂无'}\n"
                f"聊天风格：{char.chat_style or '暂无'}\n"
                f"与用户的关系：{char.relationship_summary or '普通朋友'}（类型：{char.relation_type or '朋友'}）"
            )
            text = (await chat_completion(
                [{"role": "user", "content": prompt}],
                max_tokens=200, task="card", **(cfg or {}),
            ) or "").strip().strip('\"「」”')
            if text:
                char.self_statement = text[:_cs.SELF_STATEMENT_MAX_LEN]
                await db.flush()
                await db.commit()
                _logger.info("Initial bio generated for character %d: %.60s", character_id, text)
            _initial_bio_done.add(character_id)
    except Exception as e:
        _logger.warning("Initial bio generation failed char=%d: %s", character_id, e)


async def _save_status_update(character_id: int, status_text: str, user_id: int):
    """保存当前状态更新到角色表，同时存入记忆库

    架构地图断点 #9（2026-09-29）——「状态更新双写」口径核实与收敛（只读核实结论，勿凭猜测改动）：
      同一条现状更新在本函数落两个面，改动前两侧过期口径**数值不一致**：
      - WorldFact（现状权威面，本函数下方 fold_status_update）：写入时刻 + STATUS_FRESH_HOURS，
        常量在 app/events/facts.py:29 STATUS_FRESH_HOURS，数值 **12 小时**；
      - Memory（长期面，本函数下方 save_memory）：memory_type="insight"，改动前无 valid_to/TTL 列，
        只按艾宾浩斯保留率衰减 —— 初始强度 S 取 app/memory/constants.py:9 S_BY_TYPE["insight"]=**7.0 天**，
        保留率低于 app/memory/constants.py:3 DECAY_THRESHOLD_PCT=20.0 才进删除倒计时
        （长度 app/memory/constants.py:5 DECAY_COUNTDOWN_DAYS=3 天）⇒ **≈12.6 天**（7·ln6）。
      ⇒ 地图判定成立（会出现「记忆说有、事实已过期」）。**数值已于 2026-09-29 用户拍板统一到 12 小时**
        （断点 #9 收口批）：通用衰减档 S_BY_TYPE["insight"]=7.0 天**刻意不动**（改它牵连全部 insight），
        统一只作用在「状态派生条」这一个身份上（sub_type='status' 且 source='status'）。
      写入侧做法：Memory 侧带上与 WorldFact 同源的过期标记 —— valid_to 取
        facts.status_valid_to(now) 与事实行 expires_at 的**同一瞬间**，来源面登记 derived_from=world_fact。
        谁是权威面：WorldFact 管「现状」（新鲜窗，注入对话），Memory 管「长期」（衰减曲线，检索）。
      生效点（为何必须补读侧一步）：valid_to 本身**没有**读侧消费者 —— 该列只被
        memory/supersede.py::archive_cold_superseded（要求 status=superseded）、
        memory/maintain_plan_expiry.py::_plan_scan_window（扫描窗只含 event 命中计划词 /
        sub_type=plan / user_info+extracted，insight+status 不在窗内）以及只读治理统计
        memory/maintenance_schedule.py（flag fact_lifecycle_policy 默认关）读取；
        ⇒ 真正让 12h 生效的是 memory/retrieve.py::_rerank 出口剔除（flag status_memory_ttl 默认开，
        判据与数值单一来源 events/facts.status_memory_expired；关＝本函数改动前逐字节旧行为）。
    """
    from app.application import chat_service as _cs  # 被桩依赖与本组常量在 chat_service 命名空间现取
    if not status_text:
        return
    try:
        async with _cs.async_session_factory() as db:
            result = await db.execute(select(AICharacter).where(AICharacter.id == character_id))
            char = result.scalar_one_or_none()
            if char:
                char.current_status = status_text[:200]
                await db.flush()
                await db.commit()
                _logger.info("Status updated for character %d: %.60s", character_id, status_text)
        # 断点 #9：本轮基准时刻（WorldFact 的 expires_at 与 Memory 的 valid_to 取同一瞬间）
        from app.utils.timeutil import now_naive_utc
        _status_now = now_naive_utc()
        # 同时存入记忆
        _status_mem_id = None
        try:
            from app.memory import save_memory
            _status_mem = await save_memory(
                user_id=user_id,
                character_id=character_id,
                memory_type="insight",
                content=f"状态更新: {status_text[:200]}",
                importance=2,
                sub_type="status",
                source="status",
                speaker_type="character", speaker_id=character_id,
                epistemic_status="FACT",
            )
            _status_mem_id = getattr(_status_mem, "id", None)
        except Exception as e:
            _logger.warning("Failed to save status as memory: %s", e)
        # 断点 #9：把同源过期标记挂到 Memory 派生条上（只补 valid_to 一列；
        # 已有 valid_to 的行（如计划条被并入）不覆盖，内容/来源/衰减参数一律不动）
        if _status_mem_id is not None:
            try:
                from app.events.facts import STATUS_MEMORY_DERIVED_FROM, status_valid_to
                from app.models.memory import Memory
                _status_valid_to = status_valid_to(_status_now)
                async with _cs.async_session_factory() as db:
                    row = await db.get(Memory, _status_mem_id)
                    if row is not None and row.valid_to is None:
                        row.valid_to = _status_valid_to
                        await db.commit()
                        _logger.info(
                            "Status memory %d derived_from=%s valid_to=%s",
                            _status_mem_id, STATUS_MEMORY_DERIVED_FROM, _status_valid_to,
                        )
            except Exception as e:
                _logger.warning("Failed to stamp status memory expiry: %s", e)
        # 世界状态折叠（P4）：状态更新 → 当前世界事实（失败静默）
        try:
            from app.events.facts import fold_status_update
            await fold_status_update(character_id, user_id, status_text, now=_status_now)
        except Exception as e:
            _logger.warning("World fact fold status failed: %s", e)
    except Exception as e:
        _logger.warning("Status update failed: %s", e)


def _trigger_state_eval(character_id: int, user_id: int, user_msg: str, ai_response: str, status_update):
    """异步触发八维状态评估（fire-and-forget，失败不影响聊天）"""
    from app.application import chat_service as _cs  # 被桩依赖与本组常量在 chat_service 命名空间现取
    try:
        from app.application.character_state_service import update_character_states
        _cs.spawn_background(update_character_states(
            character_id, user_id,
            user_msg or "", ai_response or "",
            status_update or "",
        ))
    except Exception as e:
                _logger.warning("State eval trigger failed: %s", e)


async def _bump_relationship(character_id: int, user_id: int) -> None:
    """认知循环 v2.1：用户主动发消息 → 关系标量小幅上升（信任+2、依恋+1，封顶 100；失败静默）"""
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.character import CharacterState
        async with async_session_factory() as db:
            st = (await db.execute(
                select(CharacterState).where(CharacterState.character_id == character_id)
            )).scalar_one_or_none()
            if st is None:
                return
            changed = False
            if int(st.trust or 50) < 100:
                st.trust = min(100, int(st.trust or 50) + 2)
                changed = True
            if int(st.attachment or 50) < 100:
                st.attachment = min(100, int(st.attachment or 50) + 1)
                changed = True
            if changed:
                await db.commit()
                _logger.info("Relationship bump char=%d trust=%d attachment=%d",
                             character_id, st.trust, st.attachment)
    except Exception as e:
        _logger.warning("Relationship bump failed char=%d: %s", character_id, e)


async def _settle_relational_drive(character_id: int, user_id: int) -> None:
    """A4 批3 M1b2（影子态）：回合末补一次关系驱力水位结算——只记账，不改本轮回复。

    commit 口径：钩子自开 session ⇒ 自己 commit（仓储层只 add/flush，不提交就把水位静默丢掉）。
    flag 关 ⇒ 先读内存闸直接返回，连 session 都不建立（零额外查询，逐字节旧行为）。
    """
    from app.application import chat_service as _cs  # 被桩依赖与本组常量在 chat_service 命名空间现取
    try:
        from app.application import relational_drive_service
        if not relational_drive_service.shadow_enabled():
            return
        async with _cs.async_session_factory() as db:
            await relational_drive_service.settle(db, character_id, user_id)
            await db.commit()
    except Exception as e:
        _logger.debug("Relational drive settle skipped char=%d: %s", character_id, e)


async def _release_drive_on_reply(character_id: int, user_id: int, session_id: int | None = None) -> None:
    """A4 批 3 / T1 M2a（2026-10-01）：用户发言后做一次**全额释放**（只写水位，不改本轮回复）。

    设计 §3.2：判据全部在仓储层（最近一条已发送的主动消息 + 归属窗 24h + 幂等闸
    last_released_at）；本钩子只负责「自开 session、调一次、commit、异常静默」。
    flag 关 ⇒ 先读内存闸直接返回，连 session 都不建立（零额外查询，逐字节旧行为）。
    """
    from app.application import chat_service as _cs  # 被桩依赖与本组常量在 chat_service 命名空间现取
    try:
        from app.application import relational_drive_service
        if not relational_drive_service.release_enabled("full", character_id):
            return
        async with _cs.async_session_factory() as db:
            await relational_drive_service.apply_reply_release(db, character_id, user_id, session_id)
            await db.commit()
    except Exception as e:
        _logger.debug("Relational drive reply release skipped char=%d: %s", character_id, e)


async def _settle_thought_pool_turn(character_id: int, user_id: int, session_id: int | None = None) -> None:
    """批 4 M1-挂点（2026-10-01）：回合末扫本会话新增行 → F4/F5 抽念头入池（搭 T1 settle 挂点族）。

    设计 §2.1「抽取挂点·回合侧」：F4 进行中话题（``conversation_topics.status='进行中'`` 且
    ``last_touched_at`` 距今 ≥3 天）+ F5 新写且 ``epistemic_status ∈ {INFERRED, UNVERIFIED}`` 的事实。
    抽取判据复用既有纯函数（``domain/thought/extract.py``），本钩子只负责取数 + 调
    ``supply_thought_pool``，不重写任何规则。

    硬约束（派单）：
      - **先判 flag 再干活**：``thought_pool_shadow`` 关 ⇒ 首行返回，连 session 都不建（零 SQL）；
      - **异常隔离**：任何失败只记 DEBUG，绝不影响聊天回合主链路；
      - **不改发送链 / 不碰频控闸 / 不给念头池发送权**；
      - commit 口径与 ``_settle_relational_drive`` 同：钩子自开 session ⇒ 自己 commit；
      - 幂等键沿用既有 ⇒ 同话题/同事实重复扫不增行。
    """
    from app.application import chat_service as _cs  # 被桩依赖与本组常量在 chat_service 命名空间现取
    try:
        from app.application import thought_pool_service as _tps
        if not _tps.shadow_enabled():
            return                      # flag 关：首行返回，零 SQL（照 _settle_relational_drive 早退写法）
        from datetime import timedelta
        from app.domain.thought import extract as _ex
        from app.models.memory import ConversationTopic, Memory
        from app.utils.timeutil import now_naive_utc
        now = now_naive_utc()
        rows_by_face: dict[str, list[dict]] = {}
        async with _cs.async_session_factory() as db:
            # F4 用户钩子：进行中话题且 ≥3 天未动（idle_days 由 last_touched_at 现算）
            cutoff = now - timedelta(days=int(_ex.F4_IDLE_DAYS))
            topics = (await db.execute(
                select(ConversationTopic).where(
                    ConversationTopic.character_id == character_id,
                    ConversationTopic.user_id == user_id,
                    ConversationTopic.status == _ex.F4_ACTIVE_STATUS,
                    ConversationTopic.last_touched_at <= cutoff,
                ).order_by(ConversationTopic.last_touched_at.asc()).limit(20)
            )).scalars().all()
            f4 = [{
                "id": t.id, "topic": t.topic, "status": t.status,
                "idle_days": (now - t.last_touched_at).total_seconds() / 86400.0,
                "character_id": character_id, "user_id": user_id,
            } for t in topics if t.last_touched_at is not None]
            # F5 新事实余波：近 24h 新写且 epistemic_status ∈ {INFERRED, UNVERIFIED}
            facts = (await db.execute(
                select(Memory).where(
                    Memory.character_id == character_id,
                    Memory.user_id == user_id,
                    Memory.epistemic_status.in_(tuple(_ex.F5_EPISTEMIC_ACCEPT)),
                    Memory.created_at >= now - timedelta(hours=24),
                ).order_by(Memory.created_at.desc()).limit(20)
            )).scalars().all()
            f5 = [{
                "id": m.id, "value": m.content, "epistemic_status": m.epistemic_status,
                "character_id": character_id, "user_id": user_id,
            } for m in facts]
            if f4:
                rows_by_face[_ex.SRC_USER_HOOK] = f4
            if f5:
                rows_by_face[_ex.SRC_FACT] = f5
            if rows_by_face:
                await _tps.supply_thought_pool(db, rows_by_face, now=now)
                await db.commit()
    except Exception as e:
        _logger.debug("Thought pool turn supply skipped char=%s: %s", character_id, e)
