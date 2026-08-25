"""overlay sections（步骤5）：模板之外的追加指令块。

从 ``context_builder.build_context_legacy`` 迁出，逻辑与旧版完全一致（零行为变化）。
本文件承载：weave_full / lorebook / life_share / shared_memory / search_capability /
group_dynamics / image_gen / reasoning_instruction / lang_instruction / continue_payload
（追加 system 块；每个 builder 返回 content 列表）。

注：对 ``context_builder`` 的 import 一律放在函数内（惰性）——因 context_builder 顶层
import 本包的子模块（section_*）会先触发本包 ``__init__``，若在顶层再回引 context_builder
会造成 import 循环。
"""
from __future__ import annotations

import logging

from app.agent.context.sections import ContextSection, register_section, TARGET_APPEND

_logger = logging.getLogger("agent.context.section_overlay")


# ------------------------------------------------------------------ weave_full 织库全注入（角色设置-社交开关，2026-08-12）
# 开启后把该角色织库卡片注入上下文（卡片为 LLM 整理后的全景记忆，为未来「全注入对话」提供结构化数据）

async def weave_full_section(state: dict, ctx: dict) -> list[str]:
    """weave_full 分区：织库全注入（append 块；有卡片返回 1 条，否则空列表）。"""
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.proactive_settings import ProactiveSettings as _PS
        from app.models.weave_card import WeaveCard, WeaveCardCharacter
        from sqlalchemy import or_ as _or_
        from app.agent.context_builder import _clip_text_to_quota, _SECTION_QUOTA_TOKENS

        async with async_session_factory() as db:
            _ps_row = (
                await db.execute(select(_PS).where(_PS.character_id == state["character_id"]))
            ).scalar_one_or_none()
            _full_inject = bool(getattr(_ps_row, "weave_full_inject_enabled", False)) if _ps_row else False
            _cards = []
            if _full_inject:
                _cards = (
                    await db.execute(
                        select(WeaveCard)
                        .where(
                            _or_(
                                WeaveCard.character_id == state["character_id"],
                                WeaveCard.id.in_(
                                    select(WeaveCardCharacter.card_id).where(
                                        WeaveCardCharacter.character_id == state["character_id"]
                                    )
                                ),
                            ),
                            WeaveCard.is_stale.is_(False),
                        )
                        .order_by(WeaveCard.importance.desc())
                        .limit(ctx["trim"]["weave_limit"])
                    )
                ).scalars().all()
        if _cards:
            _lines = [f"- 【{c.title}】[记录于 {str(c.created_at)[:10]}] {c.summary[:120]}" for c in _cards]
            _weave_full = _clip_text_to_quota(
                "【全景记忆·织库】以下是你们之间重要经历的全景卡片（全注入对话已开启，按重要度排序）：\n"
                + "\n".join(_lines),
                _SECTION_QUOTA_TOKENS["weave_full"],
            )
            return [_weave_full]
    except Exception as e:
        _logger.warning("weave full inject failed: %s", e)
    return []


# ------------------------------------------------------------------ lorebook（P1-2，2026-08-16）
# 用户消息命中关键词 → 确定性注入（受配额裁剪，防注入膨胀）

async def lorebook_section(state: dict, ctx: dict) -> list[str]:
    """lorebook 分区：关键词触发表注入（append 块；有命中返回 1 条，否则空列表）。"""
    try:
        from app.agent.context_builder import _clip_text_to_quota, _SECTION_QUOTA_TOKENS
        from app.memory.lorebook import load_matching_entries

        _lb_text_input = (state.get("user_message") or "").strip()
        _lb_hits = await load_matching_entries(state["character_id"], _lb_text_input)
        if _lb_hits:
            _lb_lines = [f"- 【{e.title}】{e.content[:150]}" for e in _lb_hits]
            _lb_inject = _clip_text_to_quota(
                "【设定·Lorebook】用户提到了相关设定，请按以下条目理解（这些是既定设定，不要与其冲突）：\n"
                + "\n".join(_lb_lines),
                _SECTION_QUOTA_TOKENS["lorebook"],
            )
            return [_lb_inject]
    except Exception as e:
        _logger.warning("Lorebook inject failed: %s", e)
    return []


# ------------------------------------------------------------------ 私·织库「AI 生活」注入（角色设置-社交「AI 生活分享」开关，2026-08-12）
# 信任机制与隐私上锁同源——trust≥60 有概率提及、≥70 高概率、<60 不提及（角色有权交流自己的私生活）

async def life_share_section(state: dict, ctx: dict) -> list[str]:
    """life_share 分区：AI 生活点滴注入（append 块；有内容返回 1 条，否则空列表）。"""
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.character_state import CharacterState as _CS
        from app.models.proactive_settings import ProactiveSettings as _PS
        import random as _rnd

        async with async_session_factory() as db:
            _cs_row = (
                await db.execute(select(_CS).where(_CS.character_id == state["character_id"]))
            ).scalar_one_or_none()
            _trust = int(getattr(_cs_row, "trust", 50) or 50) if _cs_row is not None else 50
            _ps_row2 = (
                await db.execute(select(_PS).where(_PS.character_id == state["character_id"]))
            ).scalar_one_or_none()
            _share = bool(getattr(_ps_row2, "life_share_enabled", True)) if _ps_row2 is not None else True
        _life_lines = []
        if _share and _trust >= 60:
            _prob = 0.60 if _trust >= 70 else 0.30
            if _rnd.random() < _prob:
                from app.models.memory import Memory as _MemL

                async with async_session_factory() as db:
                    _lives = (
                        await db.execute(
                            select(_MemL)
                            .where(
                                _MemL.user_id == state.get("user_id", 1),
                                _MemL.character_id == state["character_id"],
                                _MemL.source == "life",
                                _MemL.delete_at.is_(None),
                            )
                            .order_by(_MemL.importance.desc(), _MemL.created_at.desc())
                            .limit(2)
                        )
                    ).scalars().all()
                _life_lines = [
                    f"[记录于 {str(m.created_at)[:10]}] {(m.content or "").strip()[:100]}"
                    for m in _lives if (m.content or "").strip()
                ]
        if _life_lines:
            return [
                "【AI 生活】你最近的生活点滴（可以自然提起，不必刻意说明）：\n- "
                + "\n- ".join(_life_lines)
            ]
    except Exception as e:
        _logger.warning("life share inject failed: %s", e)
    return []


# ------------------------------------------------------------------ Shared Memory（Phase C，2026-08-14）
# 共同经历注入（AI 自然引用，防编造：只从记录检索）

async def shared_memory_section(state: dict, ctx: dict) -> list[str]:
    """shared_memory 分区：共同经历注入（append 块；有记录返回 1 条，否则空列表）。"""
    try:
        from app.db.database import async_session_factory

        async with async_session_factory() as db:
            from app.memory.shared_events import recall_text as _shared_recall
            _shared = await _shared_recall(db, state["user_id"], state["character_id"], limit=2)
        if _shared:
            return ["【共同经历】你们一起经历过的特别时刻（可以自然提起，不要生硬复述）：\n" + _shared]
    except Exception as e:
        _logger.warning("shared recall inject failed: %s", e)
    return []


# ------------------------------------------------------------------ AI 自主搜索能力（2026-08-16）
# browser_mcp 插件启用时，允许 LLM 输出 [SEARCH] 标记查证

async def search_capability_section(state: dict, ctx: dict) -> list[str]:
    """search_capability 分区：搜索能力注入 + 强意图兜底（append 块；0/1/2 条）。"""
    _blocks: list[str] = []
    try:
        import sys as _sys
        if _sys.modules.get("ai_plugin_browser_mcp") is not None:
            _blocks.append(
                "【搜索能力】如果你遇到不懂的知识、不确定的事实、或想查证具体做法（例如：这个梗是什么意思、"
                "怎么劝对象少打游戏、头发油怎么办、怎么写情书），可以在回复中输出 "
                "[SEARCH]你想搜索的内容[/SEARCH]（系统会自动搜索并把结果告诉你，再基于结果回复）。\n"
                "使用原则：只在真需要查证时用（一轮最多 1 次），不要编造你不确定的信息；"
                "不需要查证时绝对不要输出该标记。"
            )
            # 强意图兜底：用户明确要求搜索/查证时，追加本轮提醒确保输出标记
            _um = (state.get("user_message") or "").strip()
            _search_intent = any(k in _um for k in (
                "查查", "搜搜", "查一下", "搜一下", "上网查", "去查", "去搜", "百度一下",
                "帮我查", "帮我搜", "查查资料", "搜一搜", "查一下资料", "查查这个", "这个是什么梗",
            )) or bool(__import__("re").search(r"(?:查|搜|百度|谷歌|上网|看看|知乎).{0,4}(?:什么|怎么|为什么|是谁|是啥|一下|一查|一搜|梗|新闻|信息|做法|方法)", _um))
            if _search_intent:
                _blocks.append(
                    "【本轮提醒】用户刚才明确要求你去搜索/查证，请务必在本轮回复末尾另起一行输出 "
                    "[SEARCH]你想搜索的内容[/SEARCH] 标记（说“我去搜”不算数——系统只认标记，"
                    "检测到标记才会真正搜索并带着结果回来）。正文照常自然回应（如“等着，我去查查”）。"
                )
    except Exception:
        pass
    return _blocks


# ------------------------------------------------------------------ 家庭群聊动态（Phase 3，2026-08-15）
# 角色可回忆所在群最近发生的事；数据源 = chat_group_messages 共享表（零额外 LLM）

async def group_dynamics_section(state: dict, ctx: dict) -> list[str]:
    """group_dynamics 分区：家庭群聊动态注入（append 块；有内容返回 1 条，否则空列表）。"""
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.chat_group import ChatGroup as _CG, ChatGroupMember as _CGM, ChatGroupMessage as _CGMsg
        from app.models.character import AICharacter

        async with async_session_factory() as db:
            _gids = (
                await db.execute(
                    select(_CGM.group_id).where(_CGM.character_id == state["character_id"])
                )
            ).scalars().all()
            _group_lines = []
            if _gids:
                _grows = (await db.execute(
                    select(_CG.id, _CG.name).where(_CG.id.in_(set(_gids)))
                )).all()
                _gname = {row[0]: (row[1] or "家庭群聊") for row in _grows}
                for _gid in _gids:
                    _msgs = (await db.execute(
                        select(_CGMsg)
                        .where(_CGMsg.group_id == _gid)
                        .order_by(_CGMsg.id.desc())
                        .limit(4)
                    )).scalars().all()
                    if not _msgs:
                        continue
                    _member_ids = (await db.execute(
                        select(_CGM.character_id).where(_CGM.group_id == _gid)
                    )).scalars().all()
                    _names = {}
                    if _member_ids:
                        _nrows = (await db.execute(
                            select(AICharacter.id, AICharacter.name).where(AICharacter.id.in_(_member_ids))
                        )).all()
                        _names = {r[0]: r[1] for r in _nrows}
                    _lines = []
                    for _m in reversed(_msgs):
                        _who = _names.get(_m.character_id, "用户") if _m.character_id else "用户"
                        _mtag = ""
                        try:
                            if _m.created_at is not None:
                                from app.utils.timeutil import shift_utc_naive
                                _mtag = f" {shift_utc_naive(_m.created_at, 8):%m-%d %H:%M}"
                        except Exception:
                            _mtag = ""
                        _lines.append(f"[{_who}{_mtag}] {(_m.content or '')[:60]}")
                    _group_lines.append(f"【{_gname.get(_gid, '家庭群聊')}】" + "；".join(_lines))
            if _group_lines:
                return ["【群聊动态】你在家庭群聊里和大家聊过的事（可以自然提起，不要生硬复述）：\n- " + "\n- ".join(_group_lines)]
    except Exception as e:
        _logger.warning("group recall inject failed: %s", e)
    return []


# ------------------------------------------------------------------ 生图开关（角色级）
# 开启时注入"聊天内AI发图"指令，LLM 按需输出 [GEN_IMAGE] 标记

async def image_gen_section(state: dict, ctx: dict) -> list[str]:
    """image_gen 分区：生图指令 + 强意图兜底 + 主动生图概率兜底（append 块；0/1/2/3 条）。"""
    _blocks: list[str] = []
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.proactive_settings import ProactiveSettings

        async with async_session_factory() as db:
            _ps = await db.execute(
                select(ProactiveSettings).where(ProactiveSettings.character_id == state["character_id"])
            )
            _psobj = _ps.scalar_one_or_none()
            if _psobj is not None and _psobj.image_gen_enabled:
                _active_img = bool(getattr(_psobj, "active_image_gen_enabled", False))
                if _active_img:
                    _img_content = (
                        "【生图指令】你可以在合适的时机主动生成图片分享（比如描绘眼前场景、用画面表达心情、送对方一张小画、情绪到位时配图），"
                        "也可以在用户要求画图／生成图片／配图／自拍时画图。需要发图时，在回复末尾另起一行输出标记 [GEN_IMAGE] 画面描述 [/GEN_IMAGE]，画面描述写清主体、风格、颜色等供生图服务使用；"
                        "不要过于频繁（每次会话最多 1-2 次），没有合适的画面灵感时不要强行输出。"
                        "当用户明确要求你生成图片、画图、自拍、配图时，必须输出 [GEN_IMAGE] 标记，绝不能只回复文字假装发了图。"
                        "示例：用户说“给我画只猫”→ 正文回复“行，等着。”后另起一行输出 [GEN_IMAGE] 一只橘色小猫坐在窗台上，插画风格，暖色调 [/GEN_IMAGE]。\n"
                        "发图时同时输出图片消息文案：在 [GEN_IMAGE] 标记前另起一行输出 [IMG_TEXT] 符合你性格的一句话（12字内，如“……就这一张。”）[/IMG_TEXT]，不要用“给你画好啦～”这种通用口吻。"
                    )
                else:
                    _img_content = (
                        "【生图指令】当用户要求你画图／生成图片／配图／自拍（如“画一只猫”“给我画张图”“生成你的自拍”）时，"
                        "必须在回复末尾另起一行输出标记 [GEN_IMAGE] 画面描述 [/GEN_IMAGE]，画面描述写清主体、风格、颜色等供生图服务使用；"
                        "正文可以自然衔接（如“等着。”），绝不能只回复文字假装发了图。"
                        "用户没有要求画图时不要输出该标记。\n"
                        "发图时同时输出图片消息文案：在 [GEN_IMAGE] 标记前另起一行输出 [IMG_TEXT] 符合你性格的一句话（12字内，如“……就这一张。”）[/IMG_TEXT]，不要用“给你画好啦～”这种通用口吻。"
                    )
                _blocks.append(_img_content)
                # 强意图兜底：用户消息含明确画图/自拍意图时，追加本轮提醒，确保 LLM 输出标记
                _um = (state.get("user_message") or "").strip()
                _img_intent = (
                    ("自拍" in _um) or ("配图" in _um)
                    or bool(__import__("re").search(r"(?:画|生成|做|来|发).{0,8}(?:图|图片|照片|壁纸|头像|图集)", _um))
                    or bool(__import__("re").search(r"(?:给我|帮我|给我画|帮我画).{0,10}(?:图|画|照片|自拍)", _um))
                )
                if _img_intent:
                    _blocks.append(
                        "【本轮提醒】用户刚才明确要求生成图片／自拍／画图，请务必在本轮回复末尾另起一行输出 [GEN_IMAGE] 画面描述 [/GEN_IMAGE] 标记，"
                        "正文照常对话并自然衔接（如“等着。”）；自拍类画面描述可参考上面的角色外貌人设。"
                    )
                # 主动生图概率兜底（2026-08-14）：开关开启 + 用户未明确要求 + 距上次生图任务 >= 4h + 随机 30% → 注入本轮提醒
                elif _active_img:
                    try:
                        from app.models.image_gen_task import ImageGenTask as _ImgTask
                        from datetime import datetime, timezone
                        async with async_session_factory() as _dbg:
                            _last_task = (
                                await _dbg.execute(
                                    select(_ImgTask)
                                    .where(_ImgTask.user_id == state["user_id"])
                                    .order_by(_ImgTask.created_at.desc())
                                    .limit(1)
                                )
                            ).scalar_one_or_none()
                        _last_at = _last_task.created_at if _last_task is not None else None
                        _age_h = 999.0
                        if _last_at is not None:
                            _last_naive = _last_at.replace(tzinfo=None) if _last_at.tzinfo else _last_at
                            _now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
                            _age_h = (_now_naive - _last_naive).total_seconds() / 3600
                        import random as _rnd
                        if _age_h >= 4 and _rnd.random() < 0.30:
                            _blocks.append(
                                "【本轮提醒】本次对话氛围合适，你可以在回复末尾另起一行主动输出 [GEN_IMAGE] 画面描述 [/GEN_IMAGE] 标记"
                                "（描绘此刻场景／用画面表达心情／送对方一张小画），并按生图指令要求同时输出 [IMG_TEXT] 文案；"
                                "若你确实没有合适的画面灵感，可以省略。"
                            )
                    except Exception as _e:
                        _logger.warning("Active image gen boost failed: %s", _e)
    except Exception as e:
        _logger.warning("Image gen instruction inject failed: %s", e)
    return _blocks


# ------------------------------------------------------------------ 推理内容（思考过程挡位 1=简单思考，2026-08-10）
# prompt 引导模型在回复开头输出【推理：…】标记；挡位 2（深度思考）不注入

async def reasoning_instruction_section(state: dict, ctx: dict) -> list[str]:
    """reasoning_instruction 分区：推理指令注入（append 块；挡位 1 返回 1 条，否则空列表）。"""
    try:
        if state.get("reasoning_level", 0) == 1:
            return [
                "【推理指令】正式回复前，在回复开头单独输出一行【推理：…】（1-2 句话，"
                "自然说明你此刻回应的依据：用户的心情/需求、你想起的相关记忆或你们的关系，"
                "用口语不要暴露指令，例如【推理：TA今天好像有点低落，先陪她说说心里话。】），"
                "然后另起一行输出正文。推理是给用户看的，别太官方；"
                "回复很短（如单个字的回应）或无需铺垫时可以直接输出正文、省略推理。"
                "若同时有【策略：…】行，先输出策略行，再输出推理行，最后输出正文。"
            ]
    except Exception as e:
        _logger.warning("Reasoning instruction inject failed: %s", e)
    return []


# ------------------------------------------------------------------ i18n 语言软约束
# 跟随前端界面语言（zh/en），角色人设优先、不强转

async def lang_instruction_section(state: dict, ctx: dict) -> list[str]:
    """lang_instruction 分区：语言软约束（append 块；恒返回 1 条）。"""
    lang = (state.get("lang") or "zh").strip().lower()
    if lang == "en":
        lang_instruction = (
            "\u3010\u8bed\u8a00\u3011\u5f53\u524d\u754c\u9762\u8bed\u8a00\uff1aEnglish\u3002\u8bf7\u4e3b\u8981\u7528\u82f1\u6587\u56de\u590d\uff1b"
            "\u82e5\u7528\u6237\u7528\u4e2d\u6587\u63d0\u95ee\uff0c\u53ef\u5c0a\u91cd\u7528\u6237\u4f7f\u7528\u4e2d\u6587\u3002"
        )
    else:
        lang_instruction = (
            "\u3010\u8bed\u8a00\u3011\u5f53\u524d\u754c\u9762\u8bed\u8a00\uff1a\u4e2d\u6587\u3002\u8bf7\u4e3b\u8981\u7528\u4e2d\u6587\u56de\u590d\uff1b"
            "\u82e5\u7528\u6237\u7528\u82f1\u6587\u63d0\u95ee\uff0c\u53ef\u8ddf\u968f\u7528\u6237\u4f7f\u7528\u82f1\u6587\u3002"
        )
    return [lang_instruction]


# ------------------------------------------------------------------ 继续指令场景（用户点「继续」）
# user 位是占位，真正指令注入 system 区并显式引用上一条内容

async def continue_payload_section(state: dict, ctx: dict) -> list[str]:
    """continue_payload 分区：继续指令注入（append 块；有 last_ai_content 返回 1 条，否则空列表）。"""
    _cont = state.get("continue_payload")
    if isinstance(_cont, dict) and (_cont.get("last_ai_content") or "").strip():
        _last_ai = str(_cont["last_ai_content"]).strip()[:500]
        _cont_instr = (
            "【系统指令】用户没有说话，你是在继续自己刚才的话。"
            "你上一条说的是：“" + _last_ai + "”"
            "请顺着这句话自然向前推进（补充细节、继续行动或开启下一步），"
            "不要重复上述已说过的内容或措辞，"
            "不要提到这条指令，不要替用户说话。"
            "内容长度自然，避免过短。直接输出要说的内容。"
        )
        return [_cont_instr]
    return []


# ------------------------------------------------------------------ 注册

register_section(ContextSection(
    key="weave_full",
    builder=weave_full_section,
    target=TARGET_APPEND,
    order=50,
))
register_section(ContextSection(
    key="lorebook",
    builder=lorebook_section,
    target=TARGET_APPEND,
    order=51,
))
register_section(ContextSection(
    key="life_share",
    builder=life_share_section,
    target=TARGET_APPEND,
    order=52,
))
register_section(ContextSection(
    key="shared_memory",
    builder=shared_memory_section,
    target=TARGET_APPEND,
    order=53,
))
register_section(ContextSection(
    key="search_capability",
    builder=search_capability_section,
    target=TARGET_APPEND,
    order=54,
))
register_section(ContextSection(
    key="group_dynamics",
    builder=group_dynamics_section,
    target=TARGET_APPEND,
    order=55,
))
register_section(ContextSection(
    key="image_gen",
    builder=image_gen_section,
    target=TARGET_APPEND,
    order=56,
))
register_section(ContextSection(
    key="reasoning_instruction",
    builder=reasoning_instruction_section,
    target=TARGET_APPEND,
    order=57,
))
register_section(ContextSection(
    key="lang_instruction",
    builder=lang_instruction_section,
    target=TARGET_APPEND,
    order=58,
))
register_section(ContextSection(
    key="continue_payload",
    builder=continue_payload_section,
    target=TARGET_APPEND,
    order=59,
))
