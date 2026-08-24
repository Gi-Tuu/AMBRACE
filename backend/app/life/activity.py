"""AI 生活活动：定义 / 权限检查 / 效用决策 / 执行器（防编造：log 先写，成功后才落记忆）

2026-08-12 Life Engine v2（Phase 1：rest/organize_memory/reflect/social_prepare；Phase 2：browse/create/learn + 产物库）
离线权限规则（已拍板）：活动执行前查 tool_permissions，仅 allow 能力可用；ask 跳过记日志；forbid 禁用。
"""
import json
from app.utils.logger import get_logger
import random
import time as _time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.agent.llm_client import chat_completion
from app.memory.service import save_memory
from app.models.life import LifeActivityLog, LifeArtifact
from app.models.tool_permission import ToolPermission

_logger = get_logger("life.activity")


def _today_cn() -> str:
    """北京时间当天（YYYY年M月D日），与 context_builder 时间口径一致"""
    now = datetime.now(timezone(timedelta(hours=8)))
    return f"{now.year}年{now.month}月{now.day}日"

# 活动定义：needs=满足的需求、energy_cost（rest 为负=恢复）、llm=LLM 调用次数、scopes=依赖能力（空=无工具）
ACTIVITIES: dict[str, dict] = {
    "rest": {
        "label": "休息", "needs": ["relaxation"], "energy_cost": -10, "llm": 0, "scopes": [],
        "mood_delta": 5, "memory_importance": 1, "sub_type": "life_event",
    },
    "organize_memory": {
        "label": "整理记忆", "needs": ["reflection", "productivity"], "energy_cost": 8, "llm": 1, "scopes": [],
        "mood_delta": 3, "memory_importance": 3, "sub_type": "life_event",
    },
    "reflect": {
        "label": "反思", "needs": ["reflection"], "energy_cost": 5, "llm": 1, "scopes": [],
        "mood_delta": 6, "memory_importance": 3, "sub_type": "reflection",
    },
    "social_prepare": {
        "label": "社交准备", "needs": ["social"], "energy_cost": 5, "llm": 1, "scopes": [],
        "mood_delta": 2, "memory_importance": 3, "sub_type": "life_event",
    },
    "browse": {
        "label": "浏览探索", "needs": ["curiosity", "learning"], "energy_cost": 6, "llm": 1, "scopes": ["browser"],
        "mood_delta": 4, "memory_importance": 3, "sub_type": "note",
    },
    "create": {
        "label": "创作", "needs": ["creativity"], "energy_cost": 8, "llm": 1, "scopes": [],
        "mood_delta": 5, "memory_importance": 4, "sub_type": "life_event",
    },
    "learn": {
        "label": "学习", "needs": ["learning", "curiosity"], "energy_cost": 6, "llm": 1, "scopes": ["browser"],
        "mood_delta": 3, "memory_importance": 3, "sub_type": "note",
    },
}

# 时段加成：morning 重整理、evening 重创作/社交
PHASE_BONUS: dict[str, dict[str, float]] = {
    "morning": {"organize_memory": 1.3, "reflect": 1.1},
    "afternoon": {},
    "evening": {"social_prepare": 1.3, "reflect": 1.2, "create": 1.4},
    "sleep": {},
}


def activity_score(name: str, needs: dict[str, int], energy: int, phase: str,
                   interest_bonus: float = 0.0) -> float:
    """活动效用分（纯函数）：需求匹配 + energy 门槛 + 时段加成 + 兴趣加成 + 随机 0.8-1.2"""
    act = ACTIVITIES[name]
    if energy < act["energy_cost"]:
        return 0.0
    score = 0.0
    for n in act["needs"]:
        score += int(needs.get(n, 50)) * 0.5
    score *= PHASE_BONUS.get(phase, {}).get(name, 1.0)
    score += interest_bonus
    score *= random.uniform(0.8, 1.2)
    return score


async def offline_scope_allowed(db, user_id: int, scope: str) -> bool:
    """离线活动权限：仅 allow 可用（例外优先，无则全局默认）"""
    rows = (
        await db.execute(
            select(ToolPermission).where(
                ToolPermission.user_id == user_id,
                ToolPermission.scope.in_([scope, "__global__"]),
            )
        )
    ).scalars().all()
    per_scope = {r.scope: r.level for r in rows}
    level = per_scope.get(scope) or per_scope.get("__global__") or "allow"
    return level == "allow"


async def _interest_bonus_map(db, character_id: int) -> dict[str, float]:
    """角色兴趣等级 → 活动加成映射（browse/learn/create 对应 探索/学习/创作 兴趣桶）"""
    from app.models.life import LifeInterest
    rows = (
        await db.execute(
            select(LifeInterest).where(LifeInterest.character_id == character_id)
        )
    ).scalars().all()
    lv = {r.name: r.level for r in rows}
    return {
        "browse": lv.get("探索", 0) / 100 * 30,
        "learn": lv.get("学习", 0) / 100 * 30,
        "create": lv.get("创作", 0) / 100 * 30,
    }


async def _pick_activity(db, user_id: int, character, needs: dict[str, int],
                         energy: int, phase: str) -> str | None:
    """选择可执行活动（权限过滤 + 兴趣加成 + 最高效用），无则 None（休息）"""
    bonuses = await _interest_bonus_map(db, character.id)
    candidates = []
    for name in ACTIVITIES:
        if name == "rest":
            continue
        act = ACTIVITIES[name]
        ok = True
        for scope in act["scopes"]:
            if not await offline_scope_allowed(db, user_id, scope):
                ok = False
                break
        if not ok:
            continue
        sc = activity_score(name, needs, energy, phase, bonuses.get(name, 0.0))
        if sc > 0:
            candidates.append((name, sc))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


async def run_activity(db, user_id: int, character, phase: str, needs: dict[str, int],
                       energy: int, intensity: str) -> LifeActivityLog | None:
    """执行一次活动：log(started) → 执行 → save_memory → log(completed)。失败/跳过返回 None"""
    prob = {"low": 0.15, "medium": 0.25, "high": 0.40}.get(intensity, 0.15)
    name = await _pick_activity(db, user_id, character, needs, energy, phase)
    if name is None:
        return None
    if random.random() > prob:
        return None  # 概率未命中：本次发呆（休息），不写活动日志
    act = ACTIVITIES[name]

    log = LifeActivityLog(character_id=character.id, activity_type=name, status="started",
                          input_json=json.dumps({"phase": phase}, ensure_ascii=False),
                          energy_cost=act["energy_cost"])
    db.add(log)
    await db.commit()
    await db.refresh(log)

    satisfied = {n: random.randint(10, 20) for n in act["needs"]}
    try:
        content = ""
        artifact_id = None
        trace = None
        if name == "rest":
            content = f"{character.name}休息了一会儿，放空自己，缓了缓神。"
        else:
            content, trace = await _generate_content(db, user_id, character, name)
        if not content:
            content = f"{character.name}完成了「{act['label']}」，感觉不错。"
        # AI 日程（Phase B-2）：reflect 活动顺手生成 [SCHEDULE] 标记 → 落库并剥离标记
        if name == "reflect":
            try:
                from app.life.schedule import create_schedule, extract_schedule_mark
                content, _sched = extract_schedule_mark(content)
                if _sched:
                    await create_schedule(
                        db, user_id, character.id, _sched["title"],
                        _sched["start_time"], _sched["end_time"],
                        priority=2, source="ai_generated",
                    )
                    _logger.info("schedule created from reflect: char=%d %s", character.id, _sched["title"])
            except Exception as e:
                _logger.warning("schedule from reflect failed: %s", e)
        # 产物落库（Phase 2：create/browse/learn 生成可展示成果；rest/organize/reflect/social_prepare 无）
        if name in ("create", "browse", "learn"):
            artifact = await _create_artifact(db, user_id, character, name, content, satisfied)
            artifact_id = artifact.id if artifact is not None else None
        # 兴趣成长 + 目标推进（Phase 3）：活动完成 → 对应兴趣 +delta、对应类型目标 progress+1
        try:
            _iname = {"browse": "探索", "learn": "学习", "create": "创作"}.get(name)
            if _iname:
                from app.life.interest import touch_interest
                await touch_interest(db, character.id, _iname, delta=10, source=name)
            from app.life.goal import advance_goal
            await advance_goal(db, character.id, name)
        except Exception as e:
            _logger.warning("life interest/goal hook failed: %s", e)
        # 记忆（Life Event）→ source=life，私·织库候选
        mem = await save_memory(
            user_id=user_id, character_id=character.id,
            memory_type="event", content=content[:500],
            importance=act["memory_importance"], sub_type=act["sub_type"], source="life",
            speaker_type="character", speaker_id=character.id,
            epistemic_status="FACT",
        )
        log.status = "completed"
        log.output_json = json.dumps({
            "summary": content[:200], "satisfied": satisfied,
            "artifact_id": artifact_id, "trace": trace,
        }, ensure_ascii=False)
        log.memory_id = mem.id if mem is not None else None
        log.completed_at = datetime.now()
        await db.commit()
        _logger.info("life activity done: char=%d act=%s mem=%s artifact=%s",
                     character.id, name, log.memory_id, artifact_id)
        # 事件发布（2026-08-14 演进规划 v2 Phase A）：活动完成广播，订阅者负责朋友圈联动等
        try:
            from app.events import publish
            from app.events.schema import make_event
            _evt = make_event(
                "life.activity_completed",
                speaker={"type": "character", "id": character.id},
                target={"type": "user", "id": user_id},
                audience=[character.id, user_id],
                provenance={"origin": "life_event"},
                data={
                    "user_id": user_id,
                    "character_id": character.id,
                    "activity_type": name,
                    "memory_id": mem.id if mem is not None else None,
                    "artifact_id": artifact_id,
                    "summary": (content or "")[:200],
                    "importance": act["memory_importance"],
                },
            )
            publish("life.activity_completed", _evt)
        except Exception as e:
            _logger.warning("life event publish failed: %s", e)
        return log
    except Exception as e:
        _logger.warning("life activity failed: char=%d act=%s: %s", character.id, name, e)
        log.status = "failed"
        log.output_json = json.dumps({"error": str(e)[:200]}, ensure_ascii=False)
        await db.commit()
        return None


async def _create_artifact(db, user_id: int, character, name: str,
                          content: str, satisfied: dict) -> LifeArtifact | None:
    """活动产物落库（Phase 2）：create 优先生图（image_gen=allow 且服务可用），否则纯文字；browse/learn 为笔记。"""
    if name == "create":
        image_url = None
        # 权限 + 生图服务可用 → 尝试生图；失败静默降级纯文字
        if await offline_scope_allowed(db, user_id, "image_gen"):
            try:
                from app.services.image_gen_service import (
                    check_daily_limit, create_image_gen_task, run_image_gen_task,
                )
                if not await check_daily_limit(user_id):
                    task = await create_image_gen_task(
                        user_id, content[:200], character_id=character.id,
                    )
                    image_url = await run_image_gen_task(task.id)
            except Exception as e:
                _logger.warning("life create image gen failed: %s", e)
                image_url = None
        if image_url:
            artifact = LifeArtifact(
                user_id=user_id, character_id=character.id, type="image",
                title=f"{character.name}的作品", content_url=image_url,
                metadata_json=json.dumps({"prompt": content[:300]}, ensure_ascii=False),
                source_activity="create",
            )
        else:
            artifact = LifeArtifact(
                user_id=user_id, character_id=character.id, type="text",
                title=f"{character.name}的创作", content_text=content[:500],
                metadata_json=json.dumps({"satisfied": satisfied}, ensure_ascii=False),
                source_activity="create",
            )
    else:
        artifact = LifeArtifact(
            user_id=user_id, character_id=character.id, type="note",
            title="浏览/学习笔记" if name == "browse" else "学习笔记",
            content_text=content[:500],
            metadata_json=json.dumps({"satisfied": satisfied}, ensure_ascii=False),
            source_activity=name,
        )
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return artifact


# 真实浏览兜底话题池（兴趣/目标缺失时随机挑一个探索方向）
_BROWSE_FALLBACK_KEYWORDS = [
    "如何让日常生活更有仪式感",
    "最近流行的手机摄影技巧",
    "适合下雨天的治愈小事",
    "整理房间的实用方法",
    "新手入门做菜的基础知识",
]


async def _browse_keyword(db, character_id: int) -> str:
    """浏览搜索词：优先兴趣（探索/学习桶），其次 active 目标标题，兜底话题池随机"""
    try:
        from sqlalchemy import select as _sel
        from app.models.life import LifeInterest, LifeGoal
        rows = (
            await db.execute(
                _sel(LifeInterest)
                .where(LifeInterest.character_id == character_id)
                .order_by(LifeInterest.level.desc())
                .limit(2)
            )
        ).scalars().all()
        names = [r.name for r in rows if r.level >= 20]
        if names:
            return f"{names[0]} 入门"
        goals = (
            await db.execute(
                _sel(LifeGoal)
                .where(LifeGoal.character_id == character_id, LifeGoal.status == "active")
                .order_by(LifeGoal.priority.desc())
                .limit(1)
            )
        ).scalars().all()
        if goals and (goals[0].title or "").strip():
            return goals[0].title.strip()[:50]
    except Exception:
        pass
    return random.choice(_BROWSE_FALLBACK_KEYWORDS)


async def _real_note_llm(character, name: str, keyword: str, title: str, text: str) -> str:
    """基于真实浏览材料生成自然笔记（LLM；失败返回空串由上层直拼兜底）"""
    act_label = "浏览" if name == "browse" else "学习"
    try:
        from app.agent.llm_client import chat_completion
        return await chat_completion(
            messages=[
                {"role": "system", "content": (
                    f"你是{character.name}，一个有自己生活的角色。你刚真实{act_label}了一个网页，"
                    "基于下面的真实内容写一篇自己的小笔记（40-90 字，第一人称，真诚具体，"
                    "不要提'AI'，不要编造材料里没有的细节）。"
                    f"现在是{_today_cn()}（你的当下时间）。写笔记时禁止使用「今天/昨天/刚才/最近」等相对时间词，"
                    "涉及时间一律写具体日期（YYYY年M月D日）。"
                )},
                {"role": "user", "content": f"搜索主题：{keyword}\n网页标题：{title}\n内容摘要：{text[:400]}"},
            ],
            temperature=0.9,
            max_tokens=160,
            task="life_tick",
        ) or ""
    except Exception:
        return ""


async def _real_browse(db, user_id: int, character, name: str) -> tuple[str, dict] | None:
    """真实浏览：browser_mcp 搜索 → 打开第一个结果 → 生成真实笔记。
    返回 (content, trace)；失败/无结果/无插件返回 None（上层降级 LLM 模式）。"""
    try:
        import sys as _sys
        mod = _sys.modules.get("ai_plugin_browser_mcp")
        if mod is None or not hasattr(mod, "search_web") or not hasattr(mod, "browse"):
            return None
        keyword = await _browse_keyword(db, character.id)
        if not keyword:
            return None
        _t0 = _time.monotonic()
        res = await mod.search_web(keyword)
        results = (res or {}).get("results") or []
        if not results:
            return None
        first = results[0]
        url = str(first.get("url") or "").strip()
        title = str(first.get("title") or keyword).strip()[:120]
        if not url:
            return None
        page = await mod.browse(url)
        text = str((page or {}).get("text") or first.get("snippet") or "").strip()
        duration = max(1, int(_time.monotonic() - _t0))
        content = await _real_note_llm(character, name, keyword, title, text)
        if not content:
            content = f"我看了「{title}」，记了一点：{text[:80]}"
        trace = {"url": url, "title": title, "duration_sec": duration, "source": "mcp"}
        _logger.info("life real browse: char=%d act=%s url=%.60s dur=%ds", character.id, name, url, duration)
        return content, trace
    except Exception as e:
        _logger.warning("life real browse failed (fallback llm): %s", e)
        return None


async def _generate_content(db, user_id: int, character, name: str) -> tuple[str, dict | None]:
    """活动内容生成：browse/learn 优先真实浏览（browser=allow + 插件可用，失败静默降级）；
    返回 (content, trace)；trace 为真实浏览记录（含 URL），无则 None。"""
    if name in ("browse", "learn") and await offline_scope_allowed(db, user_id, "browser"):
        real = await _real_browse(db, user_id, character, name)
        if real is not None:
            return real
    prompts = {
        "organize_memory": (
            "你正在整理自己最近的记忆。回顾过去的几天与你重要的人相处的片段、做过的事、"
            "学到的道理，用一两句话自然地概括成一条自己的体会（30-60 字，第一人称，不要提'AI'）。"
        ),
        "reflect": (
            "现在是你独处反思的时刻。回想过去发生的事和与重要的人之间的互动，"
            "写一小段内心反思（40-80 字，第一人称，真诚自然，不要提'AI'）。"
            "如果明天你给自己安排了一件想做的事，可在内容最后另起一行输出 [SCHEDULE] YYYY-MM-DD HH:MM 标题 [/SCHEDULE]（最多 1 条，没有安排可省略）。"
        ),
        "social_prepare": (
            "想想下次和重要的人聊天时可以自然提起的一件事（来自你过去的生活或思考），"
            "用第一人称写成一句话（20-45 字，像朋友分享，不要提'AI'）。"
        ),
        "browse": (
            "你正在探索一个新话题（比如自然、城市、科技、美食中你正感兴趣的方向）。"
            "回顾你已有的知识与记忆，写下你探索这个话题时的新发现或新想法，"
            "像一篇小笔记（40-90 字，第一人称，真诚具体，不要提'AI'、不要编造具体网址或外部事实）。"
        ),
        "create": (
            "你正在创作一件作品。可以是一段短诗、一句文案、一个小故事片段或一幅画面构思。"
            "写出你的作品内容（20-80 字，第一人称或作品本身，真诚有画面感，不要提'AI'）。"
        ),
        "learn": (
            "你正在学习一件新事物（比如一项技能、一段历史、一种生活知识）。"
            "写下你学到的东西和你的一点感想（40-90 字，第一人称，具体但不要编造具体数据或外部事实，不要提'AI'）。"
        ),
    }
    prompt = prompts.get(name)
    if not prompt:
        return ""
    # 时间锚定（2026-08-17）：活动内容生成注入当下日期 + 禁相对时间词，防「今天」漂移
    prompt = prompt + f"（现在是{_today_cn()}，你的当下时间。内容中禁止使用「今天/昨天/刚才/最近/这几天/下周」等相对时间词，涉及时间写具体日期（YYYY年M月D日）；不确切的用「有一次/某天/之前」等中性表述。）"
    try:
        text = await chat_completion(
            messages=[
                {"role": "system", "content": f"你是{character.name}，一个有自己生活的角色。{prompt}"},
                {"role": "user", "content": "现在写下这句话。"},
            ],
            temperature=0.9,
            max_tokens=160,
            task="life_tick",
            user_id=user_id,
        )
        return (text or '').strip().strip(chr(34) + chr(39) + chr(96)).strip(), None
    except Exception as e:
        _logger.warning("life LLM gen failed: %s", e)
        return "", None
