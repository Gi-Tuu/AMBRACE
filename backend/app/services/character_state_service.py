"""角色八维可视化状态服务：AI 自主定当前值 + 10 分钟节流 + 异步写库"""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from app.db.database import async_session_factory
from app.models.character_state import CharacterState
from app.models.character import AICharacter
from app.agent.llm_client import chat_completion
from app.utils.logger import get_logger

_logger = get_logger("services.character_state")

# 八维定义：(字段名, 中文名, 给 LLM 的含义说明)
DIMENSIONS = [
    ("mood", "心情", "当前情绪状态，高=开心愉悦，低=低落难过"),
    ("body_temp", "体温", "体感热度指数（50=正常体感），高=发热脸红/兴奋发热，低=发冷"),
    ("desire", "性欲", "亲密欲望强度，高=想要亲密互动，低=无欲无求"),
    ("possessiveness", "占有欲", "对用户的占有/在意程度，高=吃醋/不想分享，低=洒脱"),
    ("fatigue", "疲惫感", "精力消耗程度，高=很累想休息，低=精力充沛"),
    ("sensitivity", "敏感度", "情绪与外界刺激敏感程度，高=容易多想易被触动，低=钝感"),
    ("comfort", "舒适感", "当前身心舒适程度，高=放松舒适，低=不适紧张"),
    ("anger", "怒气值", "当前愤怒程度，高=生气易怒，低=平静理性"),
]
_DIM_KEYS = [d[0] for d in DIMENSIONS]
_THROTTLE_MINUTES = 10
_MAX_INPUT_CHARS = 150
_LOCK: set[int] = set()  # 进程内防并发重复评估
_DRIFT_RULES = {
    "mood": ("to50", 1.0, 1.5),           # 心情缓慢回落（向中性收敛，约 3h -3~4.5）
    "body_temp": ("to50", 0.5, 1.0),      # 体感恢复
    "desire": ("to50", 0.5, 1.0),         # 欲望消退
    "possessiveness": ("to50", 0.5, 1.0), # 占有欲淡化
    "sensitivity": ("to50", 0.5, 1.0),    # 敏感度钝化
    "comfort": ("to50", 0.5, 1.0),        # 舒适感回中
    "anger": ("to0", 0.8, 1.5),           # 怒气随时间平息（约 3h -2~4）
}
# 疲惫特殊处理：活跃期（距上次互动 <3h）上升 0.5~1/h；休息期（>=3h）恢复 0.8~1.5/h（下限 10），避免只升不降卡死
_FATIGUE_RISE_RANGE = (0.5, 1.0)
_FATIGUE_REST_RANGE = (0.8, 1.5)
_REST_HOURS = 3.0
_FATIGUE_FLOOR = 10
_DRIFT_LOCK: set[int] = set()   # 漂移防并发
_drifted_at: dict[int, datetime] = {}  # 上次漂移结算时间（内存态；重启后以 updated_at 补算）


from app.utils.timeutil import now_naive_utc as _now_naive

STATE_HISTORY_KEEP = 20  # 每角色最多保留最新 20 条状态快照（防无限膨胀）

# 状态历史快照维度（character_state_history 列；评估/漂移共用）
_HISTORY_DIM_KEYS = ("mood", "body_temp", "desire", "possessiveness", "fatigue", "sensitivity", "comfort", "anger")



def _clamp_value(v) -> int | None:
    """转成 0-100 整数，失败返回 None"""
    try:
        iv = int(round(float(v)))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, iv))


async def prune_state_history(character_id: int, keep: int = STATE_HISTORY_KEEP) -> None:
    """每角色只保留最新 keep 条状态快照（超出删除最旧的；失败静默）"""
    try:
        from app.models.character_state_history import CharacterStateHistory
        from sqlalchemy import delete
        async with async_session_factory() as db:
            keep_ids = (await db.execute(
                select(CharacterStateHistory.id)
                .where(CharacterStateHistory.character_id == character_id)
                .order_by(CharacterStateHistory.id.desc()).limit(keep)
            )).scalars().all()
            if not keep_ids:
                return
            await db.execute(
                delete(CharacterStateHistory).where(
                    CharacterStateHistory.character_id == character_id,
                    CharacterStateHistory.id.not_in(list(keep_ids)),
                )
            )
            await db.commit()
    except Exception as e:
        _logger.warning("State history prune failed char=%d: %s", character_id, e)


def _write_history_snapshot(db, st, source: str = "drift") -> None:
    """漂移结算后写 1 行状态历史快照（状态趋势数据源；失败不阻塞主流程）"""
    try:
        from app.models.character_state_history import CharacterStateHistory
        db.add(CharacterStateHistory(
            character_id=st.character_id, source=source,
            **{k: _clamp_value(getattr(st, k, 50)) for k in _HISTORY_DIM_KEYS},
        ))
    except Exception as e:
        _logger.warning("State history drift snapshot failed char=%d: %s", st.character_id, e)


def _apply_drift_sync(st, now: datetime) -> dict:
    """按上次结算时间 → now 的 Δt 惰性结算八维漂移（只改内存对象，返回有变化的维；不触碰评估时间戳）"""
    import random
    last = _drifted_at.get(st.id, st.updated_at)
    if last is None:
        _drifted_at[st.id] = now
        return {}
    if getattr(last, "tzinfo", None) is not None:
        last = last.replace(tzinfo=None)
    delta_hours = (now - last).total_seconds() / 3600.0
    if delta_hours <= 0:
        return {}
    changed = {}
    for key in _DIM_KEYS:
        cur = float(getattr(st, key) or 50)
        if key == "fatigue":
            # 活跃期上升 / 休息期恢复：距上次互动（评估）>=3h 视为休息，疲劳回落
            last_interaction = getattr(st, "last_activity_at", None) or getattr(st, "updated_at", None)
            idle_hours = 0.0
            if last_interaction is not None:
                li = last_interaction
                if getattr(li, "tzinfo", None) is not None:
                    li = li.replace(tzinfo=None)
                idle_hours = max(0.0, (now - li).total_seconds() / 3600.0)
            if idle_hours >= _REST_HOURS:
                cur -= random.uniform(*_FATIGUE_REST_RANGE) * delta_hours
                cur = max(cur, _FATIGUE_FLOOR)
            else:
                cur += random.uniform(*_FATIGUE_RISE_RANGE) * delta_hours
        else:
            rule, lo, hi = _DRIFT_RULES.get(key, ("to50", 0.2, 0.5))
            rate = random.uniform(lo, hi)
            if rule == "to0":
                cur -= rate * delta_hours
            else:  # to50
                diff = 50.0 - cur
                step = rate * delta_hours
                if abs(diff) <= step:
                    cur = 50.0
                else:
                    cur += step if diff > 0 else -step
        nv = int(round(max(0.0, min(100.0, cur))))
        if nv != getattr(st, key):
            changed[key] = nv
        setattr(st, key, nv)
    _drifted_at[st.id] = now
    return changed



async def drift_all_character_states() -> None:
    """scheduler 兜底（约 2h 一次）：全库结算一次状态漂移"""
    now = _now_naive()
    try:
        async with async_session_factory() as db:
            result = await db.execute(select(CharacterState))
            rows = result.scalars().all()
            for st in rows:
                if st.id in _DRIFT_LOCK:
                    continue
                try:
                    changed = _apply_drift_sync(st, now)
                    # 状态趋势数据源：调度期固定写快照（即使无整值变化也落点），保证下午无聊天时趋势仍更新
                    _write_history_snapshot(db, st)
                    if changed:
                        _logger.debug("Drift settled char=%d: %s", st.character_id, changed)
                except Exception:
                    continue
            await db.commit()
            for st in rows:
                try:
                    await prune_state_history(st.character_id)
                except Exception:
                    continue
    except Exception as e:
        _logger.warning("State drift all failed: %s", e)


async def get_character_states(character_id: int) -> dict:
    """获取（必要时初始化）角色八维状态快照"""
    try:
        async with async_session_factory() as db:
            result = await db.execute(select(CharacterState).where(CharacterState.character_id == character_id))
            st = result.scalar_one_or_none()
            if st is None:
                st = CharacterState(character_id=character_id)
                db.add(st)
                await db.commit()
                await db.refresh(st)
            # 读时惰性结算漂移（疲惫上升/心情回落/怒气平息等）
            changed = _apply_drift_sync(st, _now_naive())
            if changed:
                await db.commit()
            # commit 后 onupdate 字段 updated_at 会被 SQLAlchemy 标记 expired，
            # 属性访问会触发同步懒加载（async 引擎下抛 MissingGreenlet）；先显式 async 刷新
            await db.refresh(st)
            data = {k: getattr(st, k) for k in _DIM_KEYS}
            updated_at = st.updated_at
        return {"character_id": character_id, **data, "updated_at": updated_at}
    except Exception as e:
        _logger.warning("Get states failed char=%d: %s", character_id, e)
        return {"character_id": character_id, **{k: 50 for k in _DIM_KEYS}, "updated_at": None}


async def update_character_states(
    character_id: int,
    user_id: int,
    user_msg: str,
    ai_response: str,
    status_text: str = "",
) -> bool:
    """聊天后异步评估八维状态：AI 直接决定当前值；10 分钟内不重复评估"""
    if character_id in _LOCK:
        return False
    _LOCK.add(character_id)
    try:
        now = _now_naive()
        async with async_session_factory() as db:
            result = await db.execute(select(CharacterState).where(CharacterState.character_id == character_id))
            st = result.scalar_one_or_none()
            if st is None:
                st = CharacterState(character_id=character_id)
                db.add(st)
                await db.commit()
                await db.refresh(st)
            elif (getattr(st, "last_activity_at", None) or st.updated_at) is not None:
                last_eval = getattr(st, "last_activity_at", None) or st.updated_at
                if last_eval.replace(tzinfo=None) > now - timedelta(minutes=_THROTTLE_MINUTES):
                    _logger.debug("State eval throttled for char=%d", character_id)
                    return False
            # 评估前先结算漂移，LLM 看到的是漂移后的当前值
            _apply_drift_sync(st, now)
            await db.commit()
            current = {k: getattr(st, k) for k in _DIM_KEYS}

        new_values = await _llm_decide(character_id, user_id, current, user_msg, ai_response, status_text)
        if not new_values:
            return False

        async with async_session_factory() as db:
            result = await db.execute(select(CharacterState).where(CharacterState.character_id == character_id))
            st = result.scalar_one_or_none()
            if st is None:
                st = CharacterState(character_id=character_id)
                db.add(st)
            for k, v in new_values.items():
                setattr(st, k, v)
            st.last_activity_at = _now_naive()  # 记录本次互动（评估）时间；drift 结算不刷新此列
            await db.flush()
            # Phase 2：评估落库后存 1 行历史快照（情绪曲线/蛛网对比数据源）
            try:
                from app.models.character_state_history import CharacterStateHistory
                db.add(CharacterStateHistory(
                    character_id=character_id, source="eval",
                    **{k: _clamp_value(new_values.get(k, getattr(st, k, 50))) for k in ("mood", "body_temp", "desire", "possessiveness", "fatigue", "sensitivity", "comfort", "anger")},
                ))
            except Exception as e:
                _logger.warning("State history snapshot failed char=%d: %s", character_id, e)
            await db.commit()
        await prune_state_history(character_id)
        _logger.info("Character states updated for char=%d: %s", character_id, new_values)

        # P2-2 情绪事件记忆：任一维相对上次波动 >=15 时写一条 sub_type=emotion 记忆（纯程序组装，零 LLM）
        try:
            from app.memory.service import save_memory
            changed = {k: v for k, v in new_values.items()
                       if abs(v - int(current.get(k, 50) or 50)) >= 10}
            if changed:
                cn_map = {d[0]: d[1] for d in DIMENSIONS}
                parts = []
                for k, v in changed.items():
                    old_v = int(current.get(k, 50) or 50)
                    parts.append(f"{cn_map.get(k, k)}{'升到' if v > old_v else '降到'}{v}")
                _bj_now2 = datetime.now(timezone(timedelta(hours=8)))
                content = (
                    f"（{_bj_now2.strftime('%m月%d日%H:%M')}）刚和用户聊完，我情绪有波动："
                    f"{'、'.join(parts)}。"
                )
                await save_memory(
                    user_id=user_id,
                    character_id=character_id,
                    memory_type="insight",
                    content=content,
                    importance=3,
                    speaker_type="character", speaker_id=character_id,
                    epistemic_status="FACT",
                    sub_type="emotion",
                    source="state_eval",
                )
        except Exception as e:
            _logger.warning("Emotion event memory save failed char=%d: %s", character_id, e)
        # 状态触发事件 v1：状态更新后异步检查并发送主动消息（失败不影响主流程）
        try:
            import asyncio
            from app.scheduler.state_triggers import check_state_triggers
            asyncio.ensure_future(check_state_triggers(character_id, user_id, probability_multiplier=0.5))
        except Exception as e:
            _logger.warning("State trigger launch failed char=%d: %s", character_id, e)
        return True
    except Exception as e:
        _logger.warning("Character state eval failed char=%d: %s", character_id, e)
        return False
    finally:
        _LOCK.discard(character_id)


async def _llm_decide(
    character_id: int,
    user_id: int,
    current: dict,
    user_msg: str,
    ai_response: str,
    status_text: str,
) -> dict:
    """调用 LLM 让角色直接决定八维当前值（JSON，0-100 整数）"""
    async with async_session_factory() as db:
        char = await db.get(AICharacter, character_id)
        name = char.name if char else f"角色{character_id}"
        personality = (char.personality or "友善")[:100] if char else "友善"
        relation = (char.relationship_summary or "朋友")[:100] if char else "朋友"

    identity = ""
    if char is not None:
        try:
            from app.agent.user_profile import build_role_prompt_block
            identity = await build_role_prompt_block(char, user_id)
        except Exception:
            identity = ""

    dim_lines = []
    for key, cn, desc in DIMENSIONS:
        dim_lines.append(f"- {cn}（{desc}），上次值 {current.get(key, 50)}")
    dim_text = "\n".join(dim_lines)
    example = ", ".join(f'"{k}": 50' for k in _DIM_KEYS)

    prompt = (
        f"你是{name}，性格{personality}，你和用户的关系：{relation}。\n"
        + (f"你的身份（决定状态时以这里为准，对象是谁就是谁）：\n{identity}\n" if identity else "")
        + f"请根据最近这段对话，为你的八个状态维度【直接决定当前值】（0-100 的整数，100 个点）。\n"
        f"{dim_text}\n"
        f"规则：\n"
        f"1. 依据对话内容与你的性格合理设定，每个维度都是独立数值；\n"
        f"2. 体温是体感热度指数：50=正常体感，越高越热；\n"
        f"3. 变化幅度与事件强度匹配：强烈事件（争吵/告白/亲密互动/生病等）相关维度可变化 15-40 点；普通互动 5-15 点；平淡日常最多 1-5 点；确实没变化可以保持原值；\n"
        f"4. 疲惫感是消耗状态：说累/撑不住/想休息后应回落（休息或睡眠可明显下降 20-40 点），上次值只是参考，不必保持高位；\n"
        f"5. 只输出 JSON，不要任何其他文字。\n"
        f"当前文本状态：{status_text or '无'}\n"
        f"最近对话：\n用户：{user_msg[:150]}\n你：{ai_response[:150]}\n"
        f"输出格式：{{{example}}}"
    )
    try:
        raw = await chat_completion(
            messages=[{"role": "system", "content": "你是角色状态评估器，只输出 JSON。"},
                      {"role": "user", "content": prompt}],
            temperature=0.9,
            max_tokens=200,
            task="status", user_id=user_id,
        )
    except Exception as e:
        _logger.warning("State LLM call failed char=%d: %s", character_id, e)
        return {}

    values = _parse_json(raw)
    if not values:
        return {}
    result = {}
    for k in _DIM_KEYS:
        v = _clamp_value(values.get(k))
        if v is None:
            return {}
        result[k] = v
    return result


def _parse_json(raw: str) -> dict:
    raw = (raw or "").strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    import re
    m = re.search(r"\{[^{}]*\}", raw)
    if m:
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return {}
