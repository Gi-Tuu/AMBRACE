# -*- coding: utf-8 -*-
"""情绪→驱力调制纯函数域（A4 批 7 / T7 **M0**，2026-09-29）：愉悦度/唤醒度派生读数 + 情绪乘子表 + 性格偏置表。

边界（强约束——M0 的定义就是「零行为」）：
- **零 IO、零 ORM、零 DB、零 flag、零网络**：入参只有基础类型（八维快照 dict / 带属性的行对象 /
  str / float），出参只有 float 与 dict；``valence``/``arousal`` 是**派生读数**——只由八维现算，
  **不落列、不建新表、不进 prompt**（设计 §3.1 首段）。
- **不改 ``drives.py`` 任何常量字面值**（设计 §④ #2）：本模块只产出「再乘一个有封顶的乘子」所需的
  数值，候选选取（``top_candidate_drive``）、intent↔drive 映射、两档释放一概不碰。
- 唯一带外部可见效果的出口是 ``shadow_trace_modulation``——它只做 **INFO 日志 +
  ``memory.observability.obs_event`` 留痕**（设计 §3.4(b)「三处现成位，不新开表」），
  **不写业务表、不改任何返回值、异常一律吞掉**（观测层不得把业务拖下水）。

口径来源：``output/AMBRACE_批7_关系心理其余面_详细设计_v1_20260929.md``
  §3.1 乘子形状（**只有三条规则，不做全 6×2 矩阵**，防调参面爆炸）与「最终再夹一道总闸
  [×0.80, ×1.25]」；§3.3 性格偏置「值域 [−0.10, +0.10]、认不出的词 ⇒ 0.0、禁止默认加偏置」；
  §④ #5「不跟渴望分抢情绪解释」——本模块**不读** ``desire`` / ``possessiveness`` / ``fatigue``。

⚠️ 下面所有数值都是**初值**，必须实测回标定（同 ``drives.py`` 文件头的告诫）。M0 阶段没有任何调用方
传入快照（``emotion_snapshot`` 缺省 ⇒ 乘子恒 1.0 ⇒ 与今天逐字节等价），真接线属 M1。
"""
from __future__ import annotations

from app.domain.relational.drives import (
    DRIVE_AFFECTION,
    DRIVE_ALL_KEYS,
    DRIVE_CONCERN,
    DRIVE_CURIOSITY,
    DRIVE_INTIMACY,
    DRIVE_LONGING,
    DRIVE_SHARING,
)
from app.utils.logger import get_logger

_logger = get_logger("relational.emotion_modulation")

# ── 派生读数（§3.1：两轴各自取八维的哪几维）────────────────────────────────
# valence＝(mood+comfort)/2/100：两维在 _DRIFT_RULES 里都是 to50 ⇒ 语义中性可比。
# arousal＝(sensitivity+body_temp+anger)/3/100：sensitivity 是唯一不参与弹簧的线性维，保留其原义。
# 刻意排除 desire/possessiveness/fatigue（已各有去处：decision 权重 / state_triggers 规则 /
# _DRIFT_RULES 疲劳特例）——§④ #5。
AXIS_VALENCE = "valence"
AXIS_AROUSAL = "arousal"
AXIS_NONE = "none"

_VALENCE_DIMS: tuple[str, ...] = ("mood", "comfort")
_AROUSAL_DIMS: tuple[str, ...] = ("sensitivity", "body_temp", "anger")
_EIGHT_DIMS: tuple[str, ...] = _VALENCE_DIMS + _AROUSAL_DIMS

_DIM_FLOOR = 0.0
_DIM_CEIL = 100.0
# 脏值/缺维回落：按**中性 50** 起算 ⇒ 读数 0.5 ⇒ 乘子恰为 1.0（读不到情绪就等于不调制，不猜）
_DIM_NEUTRAL = 50.0
_AXIS_NEUTRAL = 0.5

# ── 情绪乘子表（§3.1 表，硬封顶）──────────────────────────────────────────
# 每条规则：受哪一轴影响 / 哪一侧才生效（"low"＝该轴偏低才调制，"high"＝偏高才调制）/ 幅度。
# 中性（读数 0.5）⇒ 恰 1.0；另一侧不调制（也恒 1.0）——单边形状，避免「全域下压导致主动消息变少」。
EMOTION_MULTIPLIER_FLOOR = 0.80  # 总闸下限（§3.1「最终再夹一道总闸」；亦即 affection 的唯一下压位）
EMOTION_MULTIPLIER_CEIL = 1.25   # 总闸上限（longing/concern 的上浮封顶即取此值）
EMOTION_MULTIPLIER_BOUNDS: tuple[float, float] = (EMOTION_MULTIPLIER_FLOOR, EMOTION_MULTIPLIER_CEIL)

EMOTION_MULTIPLIER_RULES: dict[str, dict[str, object]] = {
    # 不舒服的时候更惦记那个人（与 decision.py 既存 sadness 同向，但输出面正交，见 §3.1「双通道说明」）
    DRIVE_LONGING: {"axis": AXIS_VALENCE, "side": "low", "span": +0.25},
    DRIVE_CONCERN: {"axis": AXIS_VALENCE, "side": "low", "span": +0.25},
    # 兴奋/敏锐时更想把事讲出去
    DRIVE_SHARING: {"axis": AXIS_AROUSAL, "side": "high", "span": +0.20},
    DRIVE_CURIOSITY: {"axis": AXIS_AROUSAL, "side": "high", "span": +0.20},
    # 全表**唯一**一条负向：情绪差时「不想翻旧账式的温情」
    DRIVE_AFFECTION: {"axis": AXIS_VALENCE, "side": "low", "span": -0.20},
    # intimacy 不参与任何调制（批 3 的禁区：它本就永不进候选）
    DRIVE_INTIMACY: {"axis": AXIS_NONE, "side": "none", "span": 0.0},
}

# ── 性格偏置表（§3.3：一张词表、一个纯函数、两个封顶）────────────────────
BIAS_UNIT = 0.05           # 单组关键词命中一次 = ±5%
BIAS_HARD_CAP = 0.10       # 硬封顶 ±10%（叠加情绪乘子后总偏离仍 ≤ 25%，靠总闸保证）
PERSONALITY_BIAS_BOUNDS: tuple[float, float] = (-BIAS_HARD_CAP, +BIAS_HARD_CAP)
BIAS_MULTIPLIER_BOUNDS: tuple[float, float] = (1.0 - BIAS_HARD_CAP, 1.0 + BIAS_HARD_CAP)

# 载体：``drive_key -> (上抬词组, 下压词组)``。**只在本处定义**（单一事实源）。
# 与 ``application/character_state_service.py`` 的 ``_derive_persona_baseline`` 内联词表是
# **「同形不同表」**——那张调情绪基线、这张调驱力增速，本批刻意**不合并**（合并会改情绪基线行为，属越界）。
# 词表只收中文：非中文性格文本 ⇒ 一个都不命中 ⇒ 恒 0（设计 §3.3「认不出的默认」，禁止猜）。
PERSONALITY_DRIVE_BIAS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    DRIVE_LONGING: (
        ("黏人", "粘人", "念旧", "恋旧", "长情", "多情", "依恋", "舍不得", "牵挂", "重感情"),
        ("独立", "自律", "慢热", "疏离", "边界感", "淡漠", "心大", "没心没肺"),
    ),
    DRIVE_CONCERN: (
        ("细心", "体贴", "周到", "爱操心", "关怀", "护短", "碎碎念"),
        ("粗心", "马虎", "大大咧咧", "神经大条", "心大", "甩手掌柜"),
    ),
    DRIVE_AFFECTION: (
        ("撒娇", "亲昵", "热情", "温柔", "甜美", "软萌", "会照顾人", "黏黏糊糊"),
        ("高冷", "冷淡", "矜持", "冰山", "克制", "端着", "生人勿近"),
    ),
    DRIVE_SHARING: (
        ("外向", "话痨", "健谈", "开朗", "活泼", "能说", "爱分享", "唠嗑", "藏不住事"),
        ("内向", "沉默", "寡言", "腼腆", "社恐", "不爱说话", "闷葫芦"),
    ),
    DRIVE_CURIOSITY: (
        ("好奇", "爱探索", "求知", "兴趣广泛", "尝鲜", "新鲜感", "爱琢磨", "八卦"),
        ("守旧", "古板", "循规蹈矩", "按部就班", "提不起兴趣", "无趣"),
    ),
    # intimacy 恒 0：与情绪乘子同调（永不参与调制），保持批 3 禁区完整
    DRIVE_INTIMACY: ((), ()),
}

# 留痕指标名（obs_event 的 route / INFO 日志的检索前缀）
SHADOW_METRIC = "emotion_drive_modulation"
_NOTE_MAX_CHARS = 160


def _to_float(value, default: float = 0.0) -> float:
    """脏值（None / 非数字 / 怪类型）按 ``default`` 处理——调制层绝不因一份坏快照抛错。"""
    try:
        if value is None or isinstance(value, bool):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _dim(raw: dict | object, key: str) -> float:
    """取一维并夹到 [0,100]：缺维/脏值 ⇒ 中性 50（＝不调制）。"""
    if isinstance(raw, dict):
        value = raw.get(key)
    else:
        value = getattr(raw, key, None)
    return _clamp(_to_float(value, _DIM_NEUTRAL), _DIM_FLOOR, _DIM_CEIL)


def derive_readings(snapshot) -> dict[str, float]:
    """八维快照 → ``{"valence": 0-1, "arousal": 0-1}``（**派生读数，只现算、不落库**）。

    - ``snapshot`` 可是 ORM 行对象（有 ``mood`` 等属性）或 dict；
    - 缺任一维 / 脏值 ⇒ 该维按 50 起算 ⇒ 读数偏向中性，不放大调制；
    - 入参为 None ⇒ 两轴都 0.5（不调制）。
    """
    if snapshot is None:
        return {AXIS_VALENCE: _AXIS_NEUTRAL, AXIS_AROUSAL: _AXIS_NEUTRAL}
    valence = sum(_dim(snapshot, key) for key in _VALENCE_DIMS) / len(_VALENCE_DIMS) / _DIM_CEIL
    arousal = sum(_dim(snapshot, key) for key in _AROUSAL_DIMS) / len(_AROUSAL_DIMS) / _DIM_CEIL
    return {
        AXIS_VALENCE: _clamp(valence, 0.0, 1.0),
        AXIS_AROUSAL: _clamp(arousal, 0.0, 1.0),
    }


def read_axes(snapshot) -> tuple[float, float]:
    """把「任意一份快照」归一为 ``(valence, arousal)``，两值恒在 [0,1]。

    接受的形态（M1 接线时由调用方挑一种传，M0 谁都不传）：
    1. ``None`` ⇒ ``(0.5, 0.5)``（不调制）；
    2. ``{"valence": v, "arousal": a}`` 或带这两个属性的对象 ⇒ 直接取（脏值 ⇒ 0.5）；
    3. 二元序列 ``(v, a)``（设计 §3.1 硬约束 1 的写法）；
    4. 八维快照（dict 或 ORM 行）⇒ 现算派生读数。
    """
    if snapshot is None:
        return _AXIS_NEUTRAL, _AXIS_NEUTRAL
    if isinstance(snapshot, dict):
        if AXIS_VALENCE in snapshot or AXIS_AROUSAL in snapshot:
            return (
                _clamp(_to_float(snapshot.get(AXIS_VALENCE), _AXIS_NEUTRAL), 0.0, 1.0),
                _clamp(_to_float(snapshot.get(AXIS_AROUSAL), _AXIS_NEUTRAL), 0.0, 1.0),
            )
        if any(key in snapshot for key in _EIGHT_DIMS):
            readings = derive_readings(snapshot)
            return readings[AXIS_VALENCE], readings[AXIS_AROUSAL]
        return _AXIS_NEUTRAL, _AXIS_NEUTRAL
    if isinstance(snapshot, (tuple, list)):
        if len(snapshot) != 2:
            return _AXIS_NEUTRAL, _AXIS_NEUTRAL
        return (
            _clamp(_to_float(snapshot[0], _AXIS_NEUTRAL), 0.0, 1.0),
            _clamp(_to_float(snapshot[1], _AXIS_NEUTRAL), 0.0, 1.0),
        )
    if hasattr(snapshot, AXIS_VALENCE) or hasattr(snapshot, AXIS_AROUSAL):
        return (
            _clamp(_to_float(getattr(snapshot, AXIS_VALENCE, None), _AXIS_NEUTRAL), 0.0, 1.0),
            _clamp(_to_float(getattr(snapshot, AXIS_AROUSAL, None), _AXIS_NEUTRAL), 0.0, 1.0),
        )
    if hasattr(snapshot, _VALENCE_DIMS[0]):
        readings = derive_readings(snapshot)
        return readings[AXIS_VALENCE], readings[AXIS_AROUSAL]
    return _AXIS_NEUTRAL, _AXIS_NEUTRAL


def emotion_multiplier(drive_key: str, emotion_snapshot=None) -> float:
    """该驱力在当前情绪下的增速乘子，恒 ∈ ``EMOTION_MULTIPLIER_BOUNDS`` = [0.80, 1.25]。

    - 快照缺省 / None / 读数为中性 0.5 ⇒ **恰 1.0**（＝与不调制逐字节等价）；
    - 未知 ``drive_key``、``intimacy`` ⇒ 1.0（不参与调制）；
    - 单边线性：从中性到极值走完 ``span``，另一侧不动（不做全 6×2 矩阵，防调参面爆炸）。
    """
    rule = EMOTION_MULTIPLIER_RULES.get(drive_key)
    if not rule or rule["axis"] == AXIS_NONE:
        return 1.0
    valence, arousal = read_axes(emotion_snapshot)
    reading = valence if rule["axis"] == AXIS_VALENCE else arousal
    side = rule["side"]
    if side == "low":
        deviation = max(0.0, _AXIS_NEUTRAL - reading) / _AXIS_NEUTRAL
    elif side == "high":
        deviation = max(0.0, reading - _AXIS_NEUTRAL) / _AXIS_NEUTRAL
    else:
        return 1.0
    multiplier = 1.0 + float(rule["span"]) * deviation
    return _clamp(multiplier, EMOTION_MULTIPLIER_FLOOR, EMOTION_MULTIPLIER_CEIL)


def personality_bias(personality, chat_style, drive_key: str) -> float:
    """中文关键词 → 该驱力的偏置，恒夹在 ``PERSONALITY_BIAS_BOUNDS`` = [−0.10, +0.10]。

    - 只吃 ``personality`` / ``chat_style`` 两列自由文本（零 LLM、零 DB）；
    - 一组关键词命中 = ±``BIAS_UNIT``；命中多个方向相反的词组则相互抵消；
    - **一个都不命中 ⇒ 0.0**（禁止「默认给某个词加偏置」；非中文文本因此恒 0）；
    - 未知 ``drive_key`` 与 ``intimacy`` ⇒ 0.0。
    """
    terms = PERSONALITY_DRIVE_BIAS.get(drive_key)
    if not terms:
        return 0.0
    text = f"{personality or ''} {chat_style or ''}".lower()
    if not text.strip():
        return 0.0
    up_terms, down_terms = terms
    up = sum(BIAS_UNIT for kw in up_terms if kw in text)
    down = sum(BIAS_UNIT for kw in down_terms if kw in text)
    return float(_clamp(up - down, -BIAS_HARD_CAP, +BIAS_HARD_CAP))


def personality_bias_vector(personality, chat_style) -> dict[str, float]:
    """六驱力偏置向量（固定键，与 ``drives.DRIVE_ALL_KEYS`` 同序），值域 [−0.10, +0.10]。"""
    return {key: personality_bias(personality, chat_style, key) for key in DRIVE_ALL_KEYS}


def personality_multiplier(drive_key: str, bias_vector=None) -> float:
    """偏置转乘子 ``1 + bias`` ⇒ [0.90, 1.10]。``bias_vector`` 缺省 ⇒ 恰 1.0（M0 不接线）。"""
    if not bias_vector:
        return 1.0
    bias = _clamp(_to_float(bias_vector.get(drive_key), 0.0), -BIAS_HARD_CAP, +BIAS_HARD_CAP)
    return _clamp(1.0 + bias, *BIAS_MULTIPLIER_BOUNDS)


def combined_multiplier(drive_key: str, emotion_snapshot=None, bias_vector=None) -> float:
    """§3.1 公式第 3、4 项的合流：情绪乘子 × 性格乘子，**再夹总闸 [0.80, 1.25]**。

    两个入参都缺省（或都是中性）⇒ 返回**恰 1.0**，这就是「缺省 ⇒ 与今天逐字节等价」的唯一支点。
    """
    multiplier = emotion_multiplier(drive_key, emotion_snapshot) * personality_multiplier(
        drive_key, bias_vector
    )
    return _clamp(multiplier, EMOTION_MULTIPLIER_FLOOR, EMOTION_MULTIPLIER_CEIL)


def modulation_note(
    drive_key: str,
    emotion_snapshot=None,
    bias_vector=None,
    multiplier: float | None = None,
) -> str:
    """影子留痕的一行文本对照（**纯函数**：同输入必同输出，不含时间戳/随机量，可复现）。

    格式与批 3 影子尾追 ``trigger_reason`` 同一形制（一个空格分隔的 kv 串，便于读端正则取数）。
    """
    valence, arousal = read_axes(emotion_snapshot)
    mult = combined_multiplier(drive_key, emotion_snapshot, bias_vector) if multiplier is None else multiplier
    bias = bias_vector.get(drive_key, 0.0) if isinstance(bias_vector, dict) else 0.0
    note = (
        f"emotion_mod drive={drive_key} mult={float(mult):.4f} "
        f"valence={valence:.4f} arousal={arousal:.4f} bias={_to_float(bias):+.4f}"
    )
    return note[:_NOTE_MAX_CHARS]


def shadow_trace_modulation(
    drive_key: str,
    emotion_snapshot=None,
    bias_vector=None,
    *,
    character_id: int | None = None,
    new_level: float | None = None,
) -> None:
    """M0 的「只留痕」出口：INFO 日志 + ``obs_event`` 各记一份量化对照，**返回值不经过它**。

    纪律（设计 §3.4(b) + 派单禁止项）：
    - 不写任何业务表（``obs_event`` 是 fire-and-forget 的 trace 队列，不是 level/snapshot 落库）；
    - 不落快照列、不新建表——读数只在 trace 里存一次；
    - 异常一律吞掉（连 flag 读取、连日志本身失败都不往上抛）：观测层出错绝不能拖垮水位结算；
    - 不修改入参（快照 dict / 偏置向量原样返回给调用方）。
    """
    try:
        valence, arousal = read_axes(emotion_snapshot)
        multiplier = combined_multiplier(drive_key, emotion_snapshot, bias_vector)
        note = modulation_note(drive_key, emotion_snapshot, bias_vector, multiplier)
        _logger.info("%s", note)
        from app.memory.observability import obs_event  # 延迟 import：本模块 import 期零副作用

        obs_event(
            character_id,
            SHADOW_METRIC,
            {
                "drive_key": drive_key,
                "multiplier": round(multiplier, 4),
                "valence": round(valence, 4),
                "arousal": round(arousal, 4),
                "bias": round(
                    bias_vector.get(drive_key, 0.0) if isinstance(bias_vector, dict) else 0.0, 4
                ),
                "new_level": None if new_level is None else round(float(new_level), 4),
            },
            kind="shadow",
        )
    except Exception as exc:  # noqa: BLE001 —— 留痕失败只降级为日志，不影响水位结算
        try:
            _logger.warning("emotion modulation shadow trace failed drive=%s: %s", drive_key, exc)
        except Exception:  # noqa: BLE001
            pass
