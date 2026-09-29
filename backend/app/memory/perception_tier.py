"""感知分层判据（批 0-2 / M0；**零行为** ＝ 本模块 M0 无任何生产调用方）。

把「一条记忆是不是手机感知派生、该不该被隔离在长期画像之外」从散落在写入侧的隐性判断，
收敛成**一组纯数据 + 纯函数（零 IO、零副作用、不 import DB、不读写任何表）**，风格照
``app/memory/lifecycle_policy.py``。

M0 只交付**能力**，不接线：

- 本模块在 M0 是**死代码**（除单测与只读盘点脚本外无调用点）；接线在 **M1**
  （``memory/write.py`` 准入段做打标）与 **M2**（晋升/摘要/召回侧执行隔离四禁令）；
- **不新增任何 flag**（灰度开关属 M1：隔离谓句届时按「flag 开 且 命中本判据」的形式接线）；
- 不建表、不迁移、不加列（口径来源：隔离靠既有 ``memories.source`` + ``memories.epistemic_status``）。

判据与边界（诚实写明）：

1. :func:`is_quarantined` 是**唯一**的隔离谓句 —— 未获用户认可的感知条才隔离，
   用户认可（``epistemic_status`` 升到 ``FACT``）后**自动**脱隔，来源永远保持 ``perception``（证据不丢）。
2. :func:`snapshot_overlap` 是**保守**判据：只认「同一条快照正文与待写正文重合 ≥6 字」这一档，
   **改写幅度大的复述判不出来（漏标）**。取舍依据：漏标的后果只是「少一条被隔离」，
   误标的后果是「用户亲口说过的话被隔离、永不进画像」——后者不可接受，
   因此判据一律往「更难命中」一侧靠（重合数不跨快照累加、阈值只加不减）。
"""
from __future__ import annotations

import re
from collections.abc import Iterable

# 感知派生条的来源值（新增来源必须同时登记 SOURCE_META，见 app/memory/sources.py；风险 R7）
PERCEPTION_SOURCE = "perception"

# 取「本轮感知语料」的窗口/条数：**必须与注入侧同口径**，否则打标看到的和模型看到的不是同一批快照。
# 唯一事实源在 backend/app/device/port.py:37-38（PERCEPTION_MAX_AGE_MINUTES / PERCEPTION_LIMIT）；
# 注入段 backend/app/agent/context/section_phone.py 不自定义窗口/条数，只渲染 port 的记录
# （其中 :31 的 _MAX_CHARS=500 是单区总长截断，与窗口/条数无关）。对齐断言见 tests/test_perception_tier.py。
SNAPSHOT_WINDOW_MINUTES = 30
SNAPSHOT_MAX_ROWS = 8

# 高置信状态：用户明确认可后置为该值（既有列，见 app/models/memory/__init__.py 的 epistemic_status）
FACT_STATUS = "FACT"

# 快照标签残留（注入区每行形如 `[屏幕 3分钟前] 正文`，见 section_phone.py:84）：仅作辅助判据
_TAG_RESIDUE = re.compile(r"\[[^\[\]]*\d+\s*分钟前\]")

# 「长词」的最小长度：低于此长度的片段噪声太大，不参与重合计数
_MIN_TOKEN_LEN = 4
# 命中的最小重合数：min_len=4 时重合 3 段 ⇔ 连续重合 ≥6 字
_MIN_OVERLAP = 3

# 参与切词的字符：中日韩汉字 + ASCII 字母数字（其余按分隔符切开，跨标点不拼接）
_WORD_RUN = re.compile(r"[0-9a-zA-Z\u4e00-\u9fff]+")


def _as_text(value: object) -> str:
    """安全取文本：非字符串一律视为空（脏输入不抛异常，也不做隐式 str 转换）。"""
    return value if isinstance(value, str) else ""


def _norm(text: str) -> str:
    """归一：casefold。只服务「判等/重合」，不改变任何落库内容。"""
    return (text or "").casefold()


def is_quarantined(source: object, epistemic_status: object) -> bool:
    """隔离谓句（纯函数）：``source == "perception"`` 且 ``epistemic_status != "FACT"``。

    - 只有**未获用户认可**的感知派生条返回 True；用户认可后自动脱隔；
    - 非感知来源恒 False（``chat`` / ``summary`` / ``None`` 等一律不受本谓句影响）；
    - ``None`` / 空串 / 大小写与首尾空白差异均安全（只归一比较，不改入参）。
    """
    return _norm(_as_text(source).strip()) == PERCEPTION_SOURCE and _norm(_as_text(epistemic_status).strip()) != _norm(FACT_STATUS)


def has_snapshot_tag(text: object) -> bool:
    """正文里是否残留快照时间标签（形如 ``[3 分钟前]`` / ``[屏幕 12分钟前]``）。

    纯字符串判据，只作**辅助**信号（主判据是 :func:`snapshot_overlap`）：标签格式属渲染细节，
    不承诺长期稳定（风险 R9），故不做格式断言、不加守卫之外的逻辑。空/脏输入返回 False。
    """
    return bool(_TAG_RESIDUE.search(_as_text(text)))


def _long_tokens(text: object, size: int) -> frozenset[str]:
    """切出「长词集合」：按非字词字符切段，段内滑窗取 size 元组（去重集合）。

    中文无空格 ⇒ 整句成为一个段，滑窗 size 元组；两个文本重合 N 段 ⇔ 连续重合 N+size-1 字。
    跨标点不拼接（更保守，误标更难发生）。
    """
    s = _norm(_as_text(text))
    if len(s) < size:
        return frozenset()
    out: set[str] = set()
    for run in _WORD_RUN.findall(s):
        if len(run) < size:
            continue
        for i in range(len(run) - size + 1):
            out.add(run[i:i + size])
    return frozenset(out)


def _thresholds(min_len: object, min_overlap: object) -> tuple[int, int]:
    """阈值兜底：非 int / 越界（<2 或 <1）一律退回默认值——脏值不会把判据放宽到比默认更松。"""
    try:
        size = int(min_len)  # type: ignore[arg-type]
        if size < 2:
            size = _MIN_TOKEN_LEN
    except (TypeError, ValueError):
        size = _MIN_TOKEN_LEN
    try:
        need = int(min_overlap)  # type: ignore[arg-type]
        if need < 1:
            need = _MIN_OVERLAP
    except (TypeError, ValueError):
        need = _MIN_OVERLAP
    return size, need


def snapshot_overlap(text: object, snapshot_texts: object, *,
                     min_len: int = _MIN_TOKEN_LEN, min_overlap: int = _MIN_OVERLAP) -> bool:
    """待写正文与「本轮感知语料」是否长词重合（打标主判据，纯函数）。

    判命中：存在**某一条**快照，其与 ``text`` 的重合长词数 ``>= min_overlap``
    （默认 ⇔ 连续重合 ≥6 字）。**不跨快照累加** —— 两条快照各撞几个词不算命中，
    否则常见词面会互相凑数、把用户真说过的话误标成感知。

    约定：
    - ``text`` 为空 / ``snapshot_texts`` 为空集合 → False（语料为空一律不启用判据）；
    - ``None`` 与非字符串元素安全跳过（不抛异常）；
    - **只读入参**：不修改 ``text``、不消费/改写 ``snapshot_texts`` 容器；
    - 非法阈值（非 int / 越界）退回默认值 —— 传脏值不会把判据放宽。
    """
    if not isinstance(snapshot_texts, Iterable) or isinstance(snapshot_texts, (str, bytes)):
        return False
    size, need = _thresholds(min_len, min_overlap)
    mine = _long_tokens(text, size)
    if not mine:
        return False
    for item in snapshot_texts:  # type: ignore[union-attr]
        other = _long_tokens(item, size)
        if other and len(mine & other) >= need:
            return True
    return False
