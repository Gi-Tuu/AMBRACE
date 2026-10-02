"""账号上下文预算档位与预算面板读数应用服务（A22 第三刀，2026-10-02）。

本模块自 ``app/application/system.py`` 逐字节搬入。边界＝账号上下文预算档位与预算面板。

回指纪律：``_audit`` 仍驻留 system.py，``_cost_estimate`` 物理住在 usage_service 但
**tests 在 system 模块上替换它**（system.py 重导出块的注释已写明），故这些调用点一律在函数内
``from app.application import system as _sys`` 后走 ``_sys.<name>``（放顶层会与重导出成环，
且桩会静默失效）。其余 usage 侧纯函数/常量直接从 usage_service 顶层 import，调用点保持裸名。
"""
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import tr_lang
from app.utils.logger import get_logger
from app.application.usage_service import (
    BREAKDOWN_DEFAULT_SAMPLES, _PANEL_DEFAULT_DAYS, _CLIP_ROUTE, _CLIP_WINDOW_HOURS,
    _USAGE_ROUTE, _SECTION_ROUTE, _unknown_usage, _empty_breakdown, _clamp_breakdown_samples,
    _aggregate_section_breakdown, usage_panel, _blank_usage_panel,
)

_logger = get_logger("application.system")


# ── 上下文预算读数（P2b，2026-09-24：App「导出诊断信息」的 P2a 读数端）──


async def read_account_context_budget_tier(user_id: int, db: AsyncSession | None = None) -> str | None:
    """读本账号的上下文预算档位**原值**（users.context_budget_tier 的唯一存储读端）。

    - 未设置（列 NULL）/ 空串 / 账号不存在 → None（= 标准档 = 现状逐字节旧行为）；
    - 本函数**不吞异常**：调用方各有自己的 fail-open 口径——对话装配链
      （context_builder._resolve_account_budget_tier）异常退回标准档；读数端按「不可用」上报
      （get_context_budget 的 tier_source），因为这里区分「没配」与「读不到」是有信息量的。
    - ``db=None`` 时自开会话（装配链路上没有现成会话）。
    """
    from app.models.user import User

    async def _one(session) -> str | None:
        value = (await session.execute(
            select(User.context_budget_tier).where(User.id == user_id)
        )).scalar_one_or_none()
        return value.strip() if isinstance(value, str) and value.strip() else None

    if db is not None:
        return await _one(db)
    from app.db.database import async_session_factory

    async with async_session_factory() as session:
        return await _one(session)


async def set_context_budget_tier(
    user_id: int,
    db: AsyncSession,
    data: dict | None,
    lang: str = "zh",
) -> dict:
    """设置本账号上下文预算档位（S2 M0，2026-09-27；账号级偏好，只影响自己的装配预算）。

    写入侧刻意不报错打断（档位是可调项不是校验题）：任何输入先过
    ``context_builder.normalize_context_budget_tier``——合法名归一、越界数值夹到最近的合法档、
    认不出的一律退回 standard。账号不存在才 404（系统边界，用户可感知）。

    生效时机：下一轮装配（每轮入口重新读库，无缓存 ⇒ 改完即生效，不需重启、不需重连）。
    """
    from app.agent import context_builder as _cb
    from app.application import system as _sys
    from app.models.user import User

    tier = _cb.normalize_context_budget_tier((data or {}).get("tier"))
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "user_not_found"))
    _before = {"context_budget_tier": target.context_budget_tier}
    target.context_budget_tier = tier
    await _sys._audit(db, user_id, "server.context_budget_tier.update", "user:%d" % user_id,
                      _before, {"context_budget_tier": tier})
    await db.commit()
    return {
        "status": "ok",
        "tier": tier,
        "tier_budget_tokens": _cb.context_budget_tier_tokens(tier),
        "previous_tier": _before["context_budget_tier"] or _cb.CONTEXT_BUDGET_TIER_DEFAULT,
    }


async def get_context_budget(
    user_id: int,
    db: AsyncSession,
    breakdown_samples: object = BREAKDOWN_DEFAULT_SAMPLES,
) -> dict:
    """上下文预算读数：档位 + P2a 预留口径 + 本账号最近一轮实际占用/最近一次被裁记录（纯读）。

    预算段直接复用 app/agent/context_builder 的常量与纯函数（含私有的
    ``_effective_system_budget_tokens``）——**读端把同一套算式再抄一遍，常量一调两边就失真**，
    而这里要报的正是「装配时真正生效的那个数」，故按同包内部纯函数的既有约定直接调用而非复制实现。
    S2 起该数还带账号档位维：读端显式传 ``tier=``（不依赖装配链的 ContextVar，二者在
    请求上下文里本就不同）。

    占用段读 agent_task_logs 里 trigger='memory_obs' + route='system_total_chars' 的每轮留痕
    （T5 M0 项2），**只看当前用户自己的行**；无样本时 status='unknown'，不拿预算值冒充占用。
    裁剪段同理读 route='quota_clipped_sections'（P2a 要求 C：真发生裁剪才写）。
    Y2（2026-09-29）再加两块同样**纯读**的段：section_breakdown＝本账号最近 N 轮
    route='section_budget' 埋点按段聚合（N 越界夹到 [1, 50]，脏输入不报错）；
    cost_estimate＝按本档生效预算 × 单价区间估「一轮输入侧」费用，**价目表为空时如实
    unavailable**（见 _TOKEN_PRICE_RANGES 注释），不编数字。
    整段查库 fail-open：任何异常（含 steps_json 是坏 JSON）都退化为「预算段照出 + 无记录 + error
    文案」，绝不抛 500——一次诊断导出不该因为读不到观测流水而失败。
    """
    from app.agent import context_builder as _cb
    from app.application import system as _sys

    flag_enabled = _cb.context_budget_reserve_enabled()
    samples_limit = _clamp_breakdown_samples(breakdown_samples)
    stored_tier: str | None = None
    tier_error = ""
    try:
        stored_tier = await read_account_context_budget_tier(user_id, db)
    except Exception as e:
        tier_error = ("tier_query_failed: " + repr(e))[:200]
    # 档位三态：user=账号显式配置 / default=未配置（NULL，等价标准档）/ unavailable=读库失败
    tier = _cb.normalize_context_budget_tier(stored_tier)
    tier_source = ("user" if stored_tier else "default") if not tier_error else "unavailable"
    payload: dict = {
        "status": "ok",
        "total_quota_tokens": _cb.TOTAL_SYSTEM_QUOTA_TOKENS,
        "reserve_reply_tokens": _cb.REPLY_RESERVE_TOKENS,
        "reserve_tools_tokens": _cb.TOOL_DEFS_RESERVE_TOKENS,
        "floor_tokens": _cb.MIN_SYSTEM_BUDGET_TOKENS,
        # S2 档位段：当前档位 + 该档有效预算 + 可调档位表（档位 UI 在 App 下一批，这里只给词表；
        # 刻意不回中文 label —— 展示文案归客户端 i18n，服务端只给 key 与数值）
        "tier": tier,
        "tier_source": tier_source,
        "tier_stored": stored_tier,
        "tier_error": tier_error,
        "tier_budget_tokens": _cb.context_budget_tier_tokens(tier),
        "tier_ceiling_tokens": _cb.CONTEXT_BUDGET_TIER_CEILING_TOKENS,
        "tier_options": [
            {
                "key": key,
                "budget_tokens": _cb.context_budget_tier_tokens(key),
                "is_current": key == tier,
            }
            for key in (
                _cb.CONTEXT_BUDGET_TIER_STANDARD,
                _cb.CONTEXT_BUDGET_TIER_EXTENDED,
                _cb.CONTEXT_BUDGET_TIER_MAX,
            )
        ],
        "effective_budget_tokens": _cb._effective_system_budget_tokens(
            reserve_enabled=flag_enabled, tier=tier),
        "flag_enabled": flag_enabled,
        "last_usage": _unknown_usage(),
        "last_clip": None,
        "clip_count_24h": 0,
        # Y2 两段的兜底值：查库失败/无样本时照这个返回（估算段先按「无价目 + 生效预算」，
        # 命中本账号最近一次调用的 model 后在 try 里重算）
        "section_breakdown": _empty_breakdown(samples_limit),
        "cost_estimate": None,
        # 批8 块 D M0：本账号窗口用量面板（按用途/按渠道 + 占比，金额段照旧 unavailable）。
        # 兜底＝空结构（读数端不得因为面板取不到数而少一段），口径见 usage_panel docstring。
        "usage_panel": _blank_usage_panel(_PANEL_DEFAULT_DAYS),
        "error": "",
    }
    # 兜底＝按「生效预算 + 未知 model」算（无价目表时就是 unavailable）；try 里读到 model 后重算
    payload["cost_estimate"] = _sys._cost_estimate(
        payload["effective_budget_tokens"], None, None)
    try:
        import json

        from sqlalchemy import func
        from app.models.agent import AgentTaskLog
        from app.utils.timeutil import now_naive_utc

        conds = (
            AgentTaskLog.trigger == "memory_obs",
            AgentTaskLog.user_id == user_id,
        )
        since = now_naive_utc() - timedelta(hours=_CLIP_WINDOW_HOURS)
        counted = (await db.execute(
            select(func.count()).select_from(AgentTaskLog).where(
                *conds, AgentTaskLog.route == _CLIP_ROUTE, AgentTaskLog.created_at >= since)
        )).scalar()
        payload["clip_count_24h"] = int(counted or 0)

        def _detail(row) -> dict | None:
            # detail 只回标量字段：埋点里的 blocks 数组带的是用户上下文块头部原文（≤24 字 ×8 条），
            # 预算读数用不上，也不必再把它外流一次；解析不出 dict 按「无记录」处理。
            if not row or not row.steps_json:
                return None
            detail = json.loads(row.steps_json)
            if not isinstance(detail, dict):
                return None
            return {k: v for k, v in detail.items() if not isinstance(v, (list, dict))}

        clip_row = (await db.execute(
            select(AgentTaskLog).where(*conds, AgentTaskLog.route == _CLIP_ROUTE)
            .order_by(AgentTaskLog.id.desc()).limit(1)
        )).scalars().first()
        clip_detail = _detail(clip_row)
        if clip_detail is not None:
            payload["last_clip"] = {
                "id": clip_row.id,
                "character_id": clip_row.character_id,
                "created_at": clip_row.created_at.isoformat(sep=" ") if clip_row.created_at else None,
                "detail": clip_detail,
            }

        usage_row = (await db.execute(
            select(AgentTaskLog).where(*conds, AgentTaskLog.route == _USAGE_ROUTE)
            .order_by(AgentTaskLog.id.desc()).limit(1)
        )).scalars().first()
        usage_detail = _detail(usage_row)
        if usage_detail is None:
            # 无样本：如实报「未知」，并给出为什么未知（该账号还没聊过一轮 / 观测开关关着）
            payload["last_usage"] = _unknown_usage()
        else:
            chars = usage_detail.get("system_chars")
            chars = int(chars) if isinstance(chars, (int, float)) else None
            payload["last_usage"] = {
                "status": "ok",
                "id": usage_row.id,
                "character_id": usage_row.character_id,
                "created_at": usage_row.created_at.isoformat(sep=" ") if usage_row.created_at else None,
                "system_chars": chars,
                # 估算口径与装配侧一致：2 字符 ≈ 1 token（context_builder._EST_CHARS_PER_TOKEN）
                "est_tokens": (chars // _cb._EST_CHARS_PER_TOKEN) if chars is not None else None,
                "budget_tokens_at_turn": usage_detail.get("budget_tokens"),
                "reserve_on": usage_detail.get("reserve_on"),
            }

        # ── Y2 ①每层体量：最近 N 轮 section_budget 埋点按段聚合（只看自己的行）──
        load_rows = (await db.execute(
            select(AgentTaskLog).where(*conds, AgentTaskLog.route == _SECTION_ROUTE)
            .order_by(AgentTaskLog.id.desc()).limit(samples_limit)
        )).scalars().all()
        details: list[dict] = []
        for row in load_rows:
            if not row.steps_json:
                continue
            try:
                parsed = json.loads(row.steps_json)
            except Exception:
                continue  # 单条坏留痕跳过（不让一行坏 JSON 拖垮整段聚合）
            if isinstance(parsed, dict) and isinstance(parsed.get("sections"), list):
                details.append(parsed)
        payload["section_breakdown"] = _aggregate_section_breakdown(details, samples_limit)

        # ── Y2 ②费用估算：本账号最近一次调用的 model/provider 进价目表查区间 ──
        from app.models.agent import LlmUsage

        used = (await db.execute(
            select(LlmUsage).where(LlmUsage.user_id == user_id)
            .order_by(LlmUsage.id.desc()).limit(1)
        )).scalars().first()
        payload["cost_estimate"] = _sys._cost_estimate(
            payload["effective_budget_tokens"],
            used.model if used else None,
            used.provider if used else None,
        )

        # ── 批8 块 D M0 ③本账号窗口面板（按用途/按渠道 + 占比）──
        # 单独 try：面板读数失败只让这一段退回空结构，不污染上面已经取到的预算/占用/裁剪/估算段
        # （整段读数的 fail-open 取向同 Y2：一次诊断读数不该因为某段取不到而整体失败）。
        try:
            payload["usage_panel"] = await usage_panel(user_id, _PANEL_DEFAULT_DAYS)
        except Exception as e:
            _logger.warning("context budget panel failed user_id=%s: %s", user_id, e)
            payload["usage_panel"] = _blank_usage_panel(_PANEL_DEFAULT_DAYS)
            payload["usage_panel"]["error"] = "usage_panel_unavailable"
    except Exception as e:
        payload["error"] = ((payload["error"] + "; ") if payload["error"] else "") + (
            "clip_query_failed: " + repr(e))[:200]
    return payload
