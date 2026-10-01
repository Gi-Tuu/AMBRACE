"""消息形态约束纯函数（A4 批 8 块 C，M0＋M1）——零 IO、零副作用、可逐例对照旧表达式。

背景（详细设计 §2.3）：本仓真实的渲染面只有「通知 / 气泡 / 群聊预览」三个，其中**只有通知面**
有约束价值（气泡可滚动，裁剪反而丢信息）。此前私聊通知的 50 字预览在
``application/chat/io.py`` 与 ``scheduling/scheduler.py`` 各写了一份逐字重复的
``content[:50] + ("…" if len(content) > 50 else "")``（两份真相必然漂移），本模块把它收口成
单一真源，并集中承载「按载体」的上限口径。

三条纪律（照 ``agent/nodes.py`` 的 ``WECHAT_CHANNEL_HINT`` 先例）：
①上限常量集中在本模块一处，供测试断言；②flag 关 ⇒ 输出与旧表达式逐字节相同；
③形态提示只进 LLM 上下文，不落库、不进记忆。
"""
import re

# 通知面预览上限**现值**：与收口前两处 [:50] 逐字节一致（M0 零行为的锚点）。
NOTIFY_PREVIEW_LIMIT = 50
# 主动消息留痕列 content[:500] 的天花板（scheduling/scheduler.py 落日志时已截过一次）。
LOG_CONTENT_CEILING = 500

# 按载体的上限口径（§2.3(2)）。notify 取现值 50：设计目标 40 属于「必须实测调参、不接受照抄」
# 的数值（§5 块 C），要等 GET /stats/notify-shape 的读数＋真机复核后才动，故此处不预设。
# bubble / group_preview = None ⇒ 不裁剪（展示侧裁剪会丢信息）。
MSG_FORM_LIMITS: dict[str, int | None] = {
    "notify": NOTIFY_PREVIEW_LIMIT,
    "bubble": None,
    "group_preview": None,
}

# 通知正文长度分桶口径（§5 块 C 建档用，边界含端点：≤50 / 51–100 / 101–200 / 201–500 / >500）
BUCKET_EDGES = (NOTIFY_PREVIEW_LIMIT, 100, 200, LOG_CONTENT_CEILING)
BUCKET_LABELS = ("le_50", "51_100", "101_200", "201_500", "gt_500")

# 句末判定与 agent/response_parser.py::split_response 同源（只取「句末切一刀」这一条规则，
# 不新写分词、不复用整条拆气泡逻辑——那套还会剥标记/拆表情行，与通知体无关）。
_SENTENCE_END = re.compile(r"(?<=[。！？])")

_SHAPE_FIT_FLAG = "message_shape_notify_limit"


def notify_shape_flag_on() -> bool:
    """本批形态约束的总闸（flag 默认关 ⇒ 逐字节旧行为）。"""
    from app.flags.agent_flags import AGENT_FLAGS
    return bool(AGENT_FLAGS.get(_SHAPE_FIT_FLAG, False))


def notify_preview(content: str, limit: int = NOTIFY_PREVIEW_LIMIT) -> str:
    """通知正文预览：前 limit 字，超出补省略号（唯一真源，两处调用点共用）。

    长度按 **Unicode 字符**（Python str）计，不做 UTF-8 字节宽度换算——与收口前的
    ``content[:50]`` 表达式口径逐字节一致。
    """
    return content[:limit] + ("…" if len(content) > limit else "")


def first_sentence(text: str) -> str:
    """取首句（无句末标点时整段视为一句）。"""
    for piece in _SENTENCE_END.split(text):
        if piece:
            return piece
    return text


def fit_to_form(text: str, form: str, enabled: bool, limit: int | None = None) -> str:
    """按载体裁剪文本；关闸 / 未知载体 / 该载体不设上限 / 本已达标 ⇒ **原样返回**。

    超限时的次序：先取首句（首句达标就用首句），首句仍超限才按上限硬截并加省略号
    （与旧 [:50]+"…" 同一视觉形态）。
    """
    if not enabled:
        return text
    cap = MSG_FORM_LIMITS.get(form) if limit is None else limit
    if not cap or len(text) <= cap:
        return text
    head = first_sentence(text)
    if head and len(head) <= cap:
        return head
    return notify_preview(text, cap)


def notify_body(content: str) -> str:
    """发送侧唯一入口：flag 关 ⇒ ``notify_preview`` 逐字节旧行为；开 ⇒ 按 notify 载体裁剪。"""
    if notify_shape_flag_on():
        return fit_to_form(content, "notify", True)
    return notify_preview(content)


def compose_notify_shape_messages(
    messages: list, *, notify_surface: bool, limit: int | None = None
) -> list:
    """把「这条将以通知面送达、请压缩成一句」拼进本轮 LLM 上下文。

    - ``notify_surface`` 沿用 ``scheduling/scheduler.py`` 里已有的 ``pushed`` 判定（离线 ⇒ 走通知面），
      本批不新造判定；
    - 非通知面或 flag 关 ⇒ **返回原列表对象本身**（逐字节旧 prompt，可由测试钉住）；
    - 开 ⇒ 在最后一条 system 之后插一条 system（无 system 时插到列表头），**不改传入列表**、
      不落库、不进记忆（提示文本只存在于返回的上下文里）。
    """
    if not notify_surface or not notify_shape_flag_on():
        return messages
    from app.agent.nodes import NOTIFY_SHAPE_HINT

    cap = MSG_FORM_LIMITS.get("notify") if limit is None else limit
    hint = NOTIFY_SHAPE_HINT.format(limit=cap if cap else NOTIFY_PREVIEW_LIMIT)
    idx = 0
    for i, msg in enumerate(messages):
        if isinstance(msg, dict) and msg.get("role") == "system":
            idx = i + 1
    return [*messages[:idx], {"role": "system", "content": hint}, *messages[idx:]]


def length_bucket(length: int) -> str:
    """单条长度落在哪个桶（边界含端点，与 BUCKET_EDGES 一致）。"""
    for edge, label in zip(BUCKET_EDGES, BUCKET_LABELS[:-1], strict=True):
        if length <= edge:
            return label
    return BUCKET_LABELS[-1]


def percentile(sorted_lengths: list[int], q: float) -> int:
    """P50/P90/P99（最近秩口径：取 ceil(q·n) 那一个）；空样本返回 0。"""
    n = len(sorted_lengths)
    if not n:
        return 0
    rank = int(min(n, max(1, -(-(q * n) // 1))))  # ceil(q*n)，夹在 [1, n]
    return sorted_lengths[rank - 1]


def summarize_lengths(lengths: list[int], *, ceiling: int = LOG_CONTENT_CEILING) -> dict:
    """通知正文长度分布读数（M0 建档口径，纯计算、零 IO）。

    ``ceiling_hit`` 是本读数的诚实边界：留痕列写入前已被 ``content[:500]`` 截过，
    落在天花板上的条数即「被截顶」的证据，此时 P99 不可信、``gt_500`` 必为 0。
    """
    data = sorted(lengths)
    n = len(data)
    buckets = dict.fromkeys(BUCKET_LABELS, 0)
    for item in data:
        buckets[length_bucket(item)] += 1
    ratios = {k: (round(v / n, 4) if n else 0.0) for k, v in buckets.items()}
    hit = sum(1 for item in data if item >= ceiling)
    return {
        "samples": n,
        "buckets": buckets,
        "ratios": ratios,
        # 通知预览会截掉的下限：原始正文超 50 字的占比（比 body 分布本身更有信息量）
        "over_preview_limit": (round(1 - ratios["le_50"], 4) if n else 0.0),
        "ceiling_hit": {"count": hit, "ratio": (round(hit / n, 4) if n else 0.0)},
        "percentiles": {
            "p50": percentile(data, 0.50),
            "p90": percentile(data, 0.90),
            "p99": percentile(data, 0.99),
        },
    }
