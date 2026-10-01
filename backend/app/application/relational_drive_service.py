# -*- coding: utf-8 -*-
"""关系驱力水位仓储层（A4 批 3 / T1 M1b1，2026-09-27）：relational_drives 的唯一读写口。

定位：``app/domain/relational`` 是纯算法（零 IO），本模块负责把它的结果落库——「取行 →
调纯函数 → 写回列」。三处钩子（settle 时机、开口释放、回复全额释放）与影子改判留痕属
下一单 M1b2，**本文件当前零调用方**。

硬约束（派单 §2.2）：
- flag ``relational_drive_shadow`` 关 ⇒ 每个入口首行即返回：不查库、不写库（逐字节旧行为）；
- 用调用方传入的 AsyncSession，不自开 session；写操作只 ``add``/``flush``，**是否 commit 由
  调用方决定**（既有 application 层里「自持 session 才 commit」，本层不持 session 故不 commit）；
- 异常一律向上抛（fail-open 属钩子侧的事，本层不吞）；import 期零 IO。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.domain.relational import drives
from app.domain.proactivity import pacing
from app.models.character import RelationalDrive
from app.utils.logger import get_logger
from app.utils.timeutil import now_naive_utc

# 影子态总闸键（登记在 app/agent/loop.py:AGENT_FLAGS）
FLAG_KEY = "relational_drive_shadow"

# ── A4 批 7 M1（2026-09-30）：情绪→驱力**单向**调制的三态档位 ──────────────────
# 登记在 app/flags/agent_flags.py；取值 off / shadow / on，**默认 off**。
# ⚠️ 非 bool 键 ⇒ 启动加载器跳过 DB 覆盖（先例 fact_lifecycle_policy），档位＝代码默认值，
#    改档需改默认值后重启；回退＝改回 "off" 重启。
MODULATION_FLAG_KEY = "emotion_drive_modulation"
MODULATION_OFF = "off"
MODULATION_SHADOW = "shadow"
MODULATION_ON = "on"
_MODULATION_MODES = (MODULATION_OFF, MODULATION_SHADOW, MODULATION_ON)

# 灰度判据**复用** domain/proactivity/pacing.py 的现成件（白名单词典 + 稳定分桶），
# 不发明第二套分桶、不改 pacing 任何常量。
_MODULATION_GRAY_CHARS = pacing.OUTREACH_PACING_GRAY_CHARS   # frozenset({13})


def modulation_mode() -> str:
    """读三态档位。**认不出的值（含脏值/空串/大小写异常）一律落 off（最保守）**；
    连读 flag 都失败也按 off——观测/调制层出错绝不能改变水位行为。"""
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        raw = AGENT_FLAGS.get(MODULATION_FLAG_KEY, MODULATION_OFF)
    except Exception:
        return MODULATION_OFF
    value = str(raw).strip().lower()
    return value if value in _MODULATION_MODES else MODULATION_OFF


def _gray_hit(character_id, user_id) -> bool:
    """白名单角色 ∧ 稳定比例桶（复用 pacing.pacing_gray_hit，fail-closed 到不生效）。"""
    try:
        return bool(pacing.pacing_gray_hit(character_id, None, chars=_MODULATION_GRAY_CHARS))
    except Exception:
        return False


async def _fetch_emotion_snapshot(db, character_id):
    """**只读**取八维情绪快照（用调用方传入的 session，不自开）。

    任何异常一律吞掉 ⇒ 返回 None ⇒ **本次不调制**：绝不「失败即生效」或「失败即清零」。
    """
    try:
        from app.models.character import CharacterState
        row = (await db.execute(
            select(CharacterState).where(CharacterState.character_id == int(character_id))
        )).scalar_one_or_none()
        return row  # 八维 ORM 行可直接交给 emotion_modulation.read_axes 派生 valence/arousal
    except Exception:
        return None


async def _fetch_bias_vector(db, character_id):
    """只读取性格偏置向量（personality / chat_style → 偏置，硬封顶 ±10% 由域层保证）。异常 ⇒ None。"""
    try:
        from app.models.character import AICharacter
        row = (await db.execute(
            select(AICharacter).where(AICharacter.id == int(character_id))
        )).scalar_one_or_none()
        if row is None:
            return None
        from app.domain.relational import emotion_modulation as emod
        return emod.personality_bias_vector(
            getattr(row, "personality", None), getattr(row, "chat_style", None))
    except Exception:
        return None


def _trace_shadow(drive_key, snapshot, character_id, new_level, bias_vector=None) -> None:
    """shadow 档的只留痕出口（不写业务表、不改返回值）。异常自吞。"""
    try:
        from app.domain.relational import emotion_modulation as emod
        emod.shadow_trace_modulation(
            drive_key, snapshot, bias_vector=bias_vector,
            character_id=character_id, new_level=new_level,
        )
    except Exception:
        return

# 首次 settle 才建的「最小集合」：只建能进主动候选的 5 个驱力。intimacy 永不参与定调
# （口径见 drives.DRIVE_CANDIDATE_KEYS），给它建行＝给没人读的键留垃圾行；缺行的键由
# load_levels 按 0.0 补齐，所以「没行」不等于「水位丢失」。
_LAZY_INIT_KEYS: tuple[str, ...] = drives.DRIVE_CANDIDATE_KEYS


def shadow_enabled() -> bool:
    """影子态总闸（缺省关；连读 flag 都失败也按关——观测层不得把业务拖下水）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get(FLAG_KEY, False))
    except Exception:
        return False


def _level_of(value) -> float:
    """脏水位（NULL / 非数字）按 0 处理：读一行坏数据不该拖垮整条链路。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _resolve_now(now: datetime | None) -> datetime:
    return now if now is not None else now_naive_utc()


async def _fetch_rows(db, character_id: int, user_id: int) -> list:
    return list((await db.execute(
        select(RelationalDrive).where(
            RelationalDrive.character_id == character_id,
            RelationalDrive.user_id == user_id,
        )
    )).scalars())


async def _fetch_row(db, character_id: int, user_id: int, drive_key: str):
    return (await db.execute(
        select(RelationalDrive).where(
            RelationalDrive.character_id == character_id,
            RelationalDrive.user_id == user_id,
            RelationalDrive.drive_key == drive_key,
        )
    )).scalar_one_or_none()


def _as_six(levels: dict[str, float]) -> dict[str, float]:
    """按六驱力固定键补齐（缺行＝0.0），并丢掉库里多出来的未知键。"""
    return {key: _level_of(levels.get(key)) for key in drives.DRIVE_ALL_KEYS}


async def load_levels(db, character_id: int, user_id: int) -> dict[str, float]:
    """该（角色, 用户）当前六驱力水位；缺行的键按 0.0。**flag 关 ⇒ 返回 {}，一次库都不查。**"""
    if not shadow_enabled():
        return {}
    rows = await _fetch_rows(db, character_id, user_id)
    return _as_six({row.drive_key: row.level for row in rows})


async def settle(db, character_id: int, user_id: int, now=None) -> dict[str, float]:
    """懒结算该（角色, 用户）已有行：算出 [游标, now) 的增量并写回 level / last_settled_at。

    - 首次 settle（该角色该用户一行都没有）才建一张「最小集合」（见 ``_LAZY_INIT_KEYS``，
      level=0.0、游标＝now），此后不再一次建全六键；
    - 幂等：增量只按游标算，同一 now 重复调用第二次起增量为 0；
    - 返回值与 load_levels 同形（六键、缺行 0.0）；**flag 关 ⇒ 返回 {} 且不查库不写库**。
    """
    if not shadow_enabled():
        return {}
    moment = _resolve_now(now)
    rows = await _fetch_rows(db, character_id, user_id)
    by_key = {row.drive_key: row for row in rows}
    if not by_key:
        for key in _LAZY_INIT_KEYS:
            fresh = RelationalDrive(
                character_id=character_id, user_id=user_id, drive_key=key,
                level=0.0, last_settled_at=moment,
            )
            db.add(fresh)
            by_key[key] = fresh
        await db.flush()
    # ── 批 7 M1：三档取数（off 档到此为止，连 emotion_modulation 都不碰）──
    mode = modulation_mode()
    snapshot = None
    bias_vec = None
    if mode != MODULATION_OFF:
        try:
            snapshot = await _fetch_emotion_snapshot(db, character_id)
            # on 档只对「白名单角色 ∧ 稳定比例桶」生效；桶外 ⇒ 退回不调制（与 off 同）
            if mode == MODULATION_ON and not _gray_hit(character_id, user_id):
                snapshot = None
            elif snapshot is not None:
                bias_vec = await _fetch_bias_vector(db, character_id)
        except Exception:
            # 纵深防御（2026-09-30 Codex 复核补）：取数助手内部已吞错，这里再兜一层 ——
            # 调制层任何异常都不得改变水位行为（既不报错、也不清零）。
            snapshot = None
            bias_vec = None

    settled: dict[str, float] = {}
    for key, row in by_key.items():
        if mode == MODULATION_ON and snapshot is not None:
            new_level, new_cursor = drives.settle_level(
                _level_of(row.level), key, row.last_settled_at, moment, snapshot
            )
        else:
            # off 与 shadow 档：不传快照 ⇒ 乘子恒 1.0 ⇒ 落库与改动前逐字节相同
            new_level, new_cursor = drives.settle_level(
                _level_of(row.level), key, row.last_settled_at, moment
            )
        row.level = new_level
        row.last_settled_at = new_cursor
        settled[key] = new_level
        if mode == MODULATION_SHADOW and snapshot is not None:
            try:
                _trace_shadow(key, snapshot, character_id, new_level, bias_vec)
            except Exception:
                pass  # 留痕绝不阻塞结算（纵深防御）
    await db.flush()
    return _as_six(settled)


async def release_open(db, character_id: int, user_id: int, drive_key: str, now=None) -> None:
    """开口确认发出后的**部分释放**：水位写成剩余量，记本次释放比例。

    坑位提醒：``drives.release_open`` 返回的是**释放后剩下的水位**（不是释放量），直接写回。
    只动 level 与 last_released_ratio：游标不动（增量照旧按游标算）、``last_released_at``
    不动（「上次被用户互动释放」的时刻只由全额释放写）。``now`` 为与 release_full 对称的
    派单签名，本函数不使用。行不存在 ⇒ 什么都不做。
    """
    if not shadow_enabled():
        return
    row = await _fetch_row(db, character_id, user_id, drive_key)
    if row is None:
        return
    row.level = drives.release_open(_level_of(row.level), drive_key)
    row.last_released_ratio = drives.DRIVE_OPEN_RELEASE_RATIO.get(drive_key, 0.0)
    await db.flush()


async def release_full(db, character_id: int, user_id: int, drive_key: str, now=None) -> None:
    """用户真实回复后的**全额释放**：水位归零、记释放时刻与比例 1.0。行不存在 ⇒ 不建行。"""
    if not shadow_enabled():
        return
    row = await _fetch_row(db, character_id, user_id, drive_key)
    if row is None:
        return
    row.level = drives.release_full(_level_of(row.level))
    row.last_released_at = _resolve_now(now)
    row.last_released_ratio = 1.0
    await db.flush()



# ══════════════════ A4 批 3 / T1「M2a 两档释放」（2026-10-01）══════════════════
# 设计：output/AMBRACE_批3M2_两档释放接线_详细设计_v1_20261001.md（§3.1 / §3.2 / §5）
# 口径：**只改水位的记账轨迹，不改任何一条消息的内容与条数**；关闸 ⇒ 一次 SELECT 都不发。
# 用户 10-01 拍板：P1 开口比例可配、P2 归属窗 24h、P3 对称释放、P5 **拆两把键**。
V1_OPEN_FLAG_KEY = "relational_drive_open_v1"
V1_FULL_FLAG_KEY = "relational_drive_full_v1"

# 全额释放的「归属窗」：主动消息与这次用户发言相隔超过它就不再认领（设计 §3.2 C3 / §9 P2）。
REPLY_ATTRIBUTION_WINDOW_HOURS = 24.0
_ATTRIBUTION_WINDOW_SECONDS = REPLY_ATTRIBUTION_WINDOW_HOURS * 3600.0

_logger = get_logger("relational_drive_service")


def _v1_enabled(kind: str) -> bool:
    """读生效键（kind=open/full）；认不出 / 读不到一律按关。"""
    key = V1_OPEN_FLAG_KEY if kind == "open" else V1_FULL_FLAG_KEY
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        return bool(AGENT_FLAGS.get(key, False))
    except Exception:
        return False


def release_enabled(kind: str, character_id: int | None = None) -> bool:
    """生效闸＝**对应 v1 键 ∧ 影子键 ∧ 角色命中灰度白名单**（设计 §5.2 双层灰度）。

    release_open / release_full 在 shadow_enabled() 为假时会静默早退，故只开 v1 未开 shadow 时
    打一条 WARNING（把「拨了开关什么都没发生」的坑留在日志里，不抛错）。
    character_id 缺省 / 不在白名单 ⇒ **fail-closed 到不生效**（灰度件复用 pacing，不发明分桶）。
    """
    if not _v1_enabled(kind):
        return False
    if not shadow_enabled():
        _logger.warning(
            "relational_drive_%s_v1 is ON but relational_drive_shadow is OFF; release is a no-op", kind,
        )
        return False
    if character_id is None:
        return False
    return _gray_hit(character_id, None)


def _open_ratio(drive_key: str) -> float:
    """开口释放比例：优先用「可配覆盖」（settings JSON），否则回落域层常量表（设计 §9 P1）。"""
    try:
        from app.config import settings
        raw = getattr(settings, "relational_drive_open_release_ratios", "") or ""
        if str(raw).strip():
            import json as _json
            data = _json.loads(raw)
            if isinstance(data, dict) and drive_key in data:
                return min(max(float(data[drive_key]), 0.0), 1.0)
    except Exception:
        pass
    return float(drives.DRIVE_OPEN_RELEASE_RATIO.get(drive_key, 0.0))


async def _recent_sent_outreach(db, character_id: int, session_id):
    """本会话最近一条已发送的主动消息（含 extra_meta）；用于 C2/C3 与对称释放。

    口径：只按 (character_id, session_id) 收口 —— 会话本身归属该用户，跨会话 / 跨用户天然不串味
    （设计 §3.3 N2）；ProactiveMessageLog 表上没有 user_id 列，用户归属由驱动行 _fetch_row 保证。
    """
    try:
        if session_id is None:
            return None
        from app.models.character import ProactiveMessageLog
        return (await db.execute(
            select(ProactiveMessageLog).where(
                ProactiveMessageLog.character_id == int(character_id),
                ProactiveMessageLog.session_id == int(session_id),
            ).order_by(ProactiveMessageLog.created_at.desc()).limit(1)
        )).scalar_one_or_none()
    except Exception:
        return None


def _intent_of(row) -> str | None:
    """从主动消息行读 intent（脏 JSON / 缺键 / 空值一律 None）。"""
    try:
        import json as _json
        data = _json.loads(getattr(row, "extra_meta", None) or "{}")
        intent = (data or {}).get("intent")
        if intent is None:
            return None
        text = str(intent).strip()
        return text or None
    except Exception:
        return None


async def apply_open_release(db, character_id: int, user_id: int, intent, *, now=None) -> dict | None:
    """开口释放编排（设计 §3.1）：闸关 ⇒ 返回 None 且一次 SELECT 都不发；闸开 ⇒ settle → intent 反查 → release_open。

    顺序写死 settle → release_open、同一 session 同一次 commit（commit 由调用方负责）。
    """
    if not release_enabled("open", character_id):
        return None
    drive_key = drives.INTENT_TO_DRIVE.get(str(intent).strip()) if intent else None
    if not drive_key:
        return None
    row = await _fetch_row(db, character_id, user_id, drive_key)
    if row is None:
        return None
    await settle(db, character_id, user_id, now=now)
    level_before = _level_of(row.level)
    ratio = _open_ratio(drive_key)
    row.level = drives.release_open(level_before, drive_key, ratio=ratio)
    row.last_released_ratio = ratio
    await db.flush()
    return {"drive": drive_key, "level_before": level_before, "level_after": _level_of(row.level), "ratio": ratio}


async def apply_reply_release(db, character_id: int, user_id: int, session_id, *, now=None) -> dict | None:
    """用户发言后的**全额释放**编排（设计 §3.2 ＋ P3 对称释放）。

    合并口径：取本会话**最近一条已发送的主动消息**，①带可反查 intent；②在归属窗（24h）内；
    ③晚于该 drive 行的 last_released_at（幂等闸）。三条齐 ⇒ settle → release_full。
    因为永远取「最近一条」，C3 的「中间不被下一条主动消息夹断」自动成立；用户主动开口（并非回应某条）
    走同一规则 —— 这就是 P3 的对称释放。
    """
    if not release_enabled("full", character_id):
        return None
    msg = await _recent_sent_outreach(db, character_id, session_id)
    if msg is None or getattr(msg, "created_at", None) is None:
        return None
    intent = _intent_of(msg)
    drive_key = drives.INTENT_TO_DRIVE.get(intent) if intent else None
    if not drive_key:
        return None
    now = now or now_naive_utc()
    sent_at = msg.created_at
    try:
        if (now - sent_at).total_seconds() > _ATTRIBUTION_WINDOW_SECONDS:
            return None
    except Exception:
        return None
    row = await _fetch_row(db, character_id, user_id, drive_key)
    if row is None:
        return None
    last_released = getattr(row, "last_released_at", None)
    if last_released is not None and sent_at <= last_released:
        return None  # 这条主动消息已经被释放过（幂等闸）
    await settle(db, character_id, user_id, now=now)
    level_before = _level_of(row.level)
    await release_full(db, character_id, user_id, drive_key, now=now)
    return {"drive": drive_key, "level_before": level_before, "level_after": 0.0, "ratio": 1.0}
