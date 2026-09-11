"""思考过程上屏规范（2026-09-10）：第一人称（自称「我」、称对方用昵称）+ 穿帮边界。

挡位 1（正文开头【推理：…】）与挡位 2（原生 reasoning_content）共用同一条指令与同一条
上屏归一管线：
- `reasoning_instructions_for(level, name=..., user=...)`：注入侧（0 关=空 / 1=1 条 / 2=2 条）
- `normalize_reasoning_for_display(raw, character_name, user_name)`：上屏侧（对称人称归一 +
  自我编排标签剥离 + 系统/技术/数值穿帮句剔除）
"""
import re

# 给「正文开头【推理：…】」与思考过程的统一指令（{name}/{user} 为占位，注入时填角色名与对方昵称）
REASONING_INSTRUCTION = (
    "【内心活动指令】无论是正文开头的【推理：……】行，还是你自己的思考过程，"
    "都是你这个角色此刻的内心活动，会展示给对方看。\n"
    "人称是硬性要求：\n"
    "- 称呼你自己，一律用第一人称「我」，禁止出现你自己的名字（{name}），"
    "也不要用「AI/助手/角色/TA」来指代你自己；\n"
    "- 称呼对方，一律用 TA 的昵称（{user}）或「你/他/她/TA」，禁止使用「用户」这个词。\n"
    "可以写你察觉到的情绪、你想起的记忆、你的判断和接下来打算怎么回（这些都保留），"
    "口语、自然，允许括号与小动作/画面感；不要列点、不要官方腔。\n"
    "禁止把后台的东西写进思考：系统注入/天气注入规则、策略与长度标签、生图(GEN_IMAGE)等工具决策、"
    "饱食度/百分比/token/配额等数值、任何系统提示或技术字段。\n"
    "对比——错误（名字自称 + 叫对方「用户」）：【推理：用户说回来了，sam应该回应，sam把肉盛出来，问他吃没。】\n"
    "正确（自称「我」、称对方昵称）：【推理：（轩刚好回来，肉也焖好了。我嘴上不饶他，手已经把肉盛出来，顺口问一句吃没就行。）】\n"
    "正文开头若输出【推理：……】，请单独成行、置于正文之前；回复极短或无需内心活动时可省略本行。"
)

# 挡位 2 专用补充（原生 thinking 通道未必遵守正文标记，再补一条针对「思考过程本身」的约束）
REASONING_FIRSTPERSON_HINT = (
    "注意：你接下来的内部思考(reasoning)也必须用第一人称「我」称呼你自己、用对方昵称（{user}）或你/他/TA 称呼对方，"
    "不要写你自己的名字、不要写「用户」二字；并且不要在思考里出现系统注入规则、工具/技术字段名或任何数值计量"
    "（如百分比、饱食度、token）。"
)


def reasoning_instructions_for(level: int, *, name: str = "", user: str = "") -> list[str]:
    """返回应注入的思考相关 system 指令（0 关=空；1=内心活动指令；2=内心活动指令+第一人称补充）。"""
    def _fmt(t: str) -> str:
        return t.replace("{name}", name or "（你的名字）").replace("{user}", user or "你/TA")

    try:
        lv = int(level or 0)
    except (TypeError, ValueError):
        return []
    if lv == 1:
        return [_fmt(REASONING_INSTRUCTION)]
    if lv == 2:
        return [_fmt(REASONING_INSTRUCTION), _fmt(REASONING_FIRSTPERSON_HINT)]
    return []


# ── A. 真正的后台/技术/数值穿帮：命中即整句剔除（正常的「他说/我应该/首先」不在此列，保留）──
_REASONING_LEAK_LINE = [
    r"天气注入", r"以当前注入为准", r"注入为准", r"本轮提醒", r"上下文说", r"按规则",
    r"系统提示", r"prompt", r"GEN_IMAGE", r"tool_call", r"extra_body", r"max_tokens",
    r"\bMCP\b", r"\btimer\b", r"extra_capabilit", r"饱食度", r"\d+\s*%", r"token", r"配额|额度",
]
_LEAK_RE = re.compile("|".join(_REASONING_LEAK_LINE), re.IGNORECASE)

# ── B. 自我编排标签：只去标签前缀、保留其后内容（不删整句）──
_LABEL_PREFIX_RE = re.compile(r"(策略|规划建议|回复长度)\s*[：:]\s*")
# 备选项按「长串/具体值优先」排列：中长/中等 必须先于 中、很短/短 单调即可，
# 否则「长度：中长」会被 中 吃成「我正常回」+ 残留「长」。
_LEN_RE = re.compile(r"长度\s*[：:]\s*(很短|偏长|中长|中等|短|中|长)")
_LEN_MAP = {
    "短": "我回短点", "很短": "我回短点", "中": "我正常回", "中等": "我正常回",
    "中长": "我回长一点", "偏长": "我回长一点", "长": "我回长一点",
}

# 角色名后紧跟的、表「自称主语」的谓语/动作起头（命中才把名字替换成「我」，保守，防误伤）
_SELF_PREDICATE = (
    r"应该|应当|要|得|先|再|也|还|会|想|打算|决定|可以|可能|不能|别|不|把|被|给|跟|对|问|"
    r"去|来|是|在|就|这|那|心里|嘴上|嘴里|手|脚|眼|脸|准备|试着|尽量|干脆|顺手|故意|有点|一下|一边"
)


def _self_first_person(text: str, name: str | None) -> str:
    """名字自称 → 我（仅「名字+自称谓语」强模式；单字名/名字是代词时跳过）。

    英文名大小写不敏感（模型常把 Sam 写成 sam），中文名不受影响。
    """
    if not name:
        return text
    name = name.strip()
    if len(name) < 2 or name in ("我", "你", "TA", "他", "她", "它", "用户"):
        return text
    pat_subj = re.compile(
        r"(^|[，。；、\s/（(])" + re.escape(name) + r"(?=" + _SELF_PREDICATE + r")",
        re.IGNORECASE,
    )
    text = pat_subj.sub(lambda m: m.group(1) + "我", text)
    text = re.sub(re.escape(name) + r"自己", "我自己", text, flags=re.IGNORECASE)
    for body in ("心里", "嘴上", "手里", "身边", "脑海"):
        text = re.sub(re.escape(name) + body, "我" + body, text, flags=re.IGNORECASE)
    prev = None
    while prev != text:  # 连续情形多跑一轮直到稳定
        prev = text
        text = pat_subj.sub(lambda m: m.group(1) + "我", text)
    return text


def _addressee_by_nickname(text: str, nick: str | None) -> str:
    """「用户」→ 对方昵称；昵称缺失/就是「用户」时兜底为「你」。"""
    if "用户" not in text:
        return text
    repl = None
    if nick:
        n = nick.strip()
        if n and n != "用户":
            repl = n
    repl = repl or "你"
    return text.replace("用户", repl)


# 段末标点（真正会保留的）；\n 与 / 只当切段边界，不进输出
_SEG_TERM = "。！？!?"
_SEG_SPLIT_RE = re.compile(r"([。！？!?\n/])")
_SUBCLAUSE_SPLIT_RE = re.compile(r"[，,、；;]")


def _strip_leak_segments(text: str) -> str:
    """按句切段、段内按最小子句剔除穿帮，剩余子句用「，」重拼，各段直接拼接。

    与旧实现（整段命中 _LEAK_RE 即整段删）相比，这里只丢真正含穿帮词的子句，
    避免「他刚回来，肉也焖好了，天气注入说是晴以当前注入为准，我先把肉盛出来问他吃没。」
    这类逗号长句被整条清空、前端思考块凭空消失。

    已知取舍（有意为之，非缺陷）：
    - 段内重拼会把原有的「、」「；」统一成「，」，只改标点形态、不改语义；
    - 连续句末符（如「。！」）只保留第一个，后一个标点会被丢弃；
    - \\n 与 / 只作为切段边界，不保留在输出里。
    """
    kept_segments: list[str] = []
    buffer = ""

    def _render(buf: str) -> str:
        clauses = [c.strip() for c in _SUBCLAUSE_SPLIT_RE.split(buf) if c.strip()]
        return "，".join(c for c in clauses if not _LEAK_RE.search(c))

    for piece in _SEG_SPLIT_RE.split(text):
        if not piece:
            continue
        if piece in "。！？!?\n/":  # 段末符：收束当前段
            buf, buffer = buffer.strip(), ""
            if not buf:
                continue  # 连续句末符：该段只有标点没有正文，丢弃后一个标点
            seg = _render(buf)
            if seg:
                kept_segments.append(seg + piece if piece in _SEG_TERM else seg)
        else:
            buffer += piece
    tail = buffer.strip()  # 收尾：没有句末符结尾的残余正文
    if tail:
        seg = _render(tail)
        if seg:
            kept_segments.append(seg)
    return "".join(kept_segments)


# ── C2. 上屏归一后保守去重（P3-6，2026-09-11）──
# 相邻两句完全相同且每句 ≤12 字才合并为一句——源于模型正文自写一遍 + 长度标签转换
# （如「我回短点。」叠加「长度：短」→「我回短点。」）产生的「我回短点。我回短点。」。
# 长句 / 两个不同短句 / 其它标点语气一律不动（仅针对句末符分隔的相邻整句）。
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?])")


def _dedup_adjacent_short_sentences(text: str) -> str:
    """相邻完全相同的短句（每句 ≤12 字）合并为一句；不动长句、不改其它标点语气。"""
    if not text:
        return text
    parts = _SENT_SPLIT_RE.split(text)
    if len(parts) <= 1:
        return text
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        if out and out[-1] == p and len(p.rstrip("。！？!?")) <= 12:
            continue  # 相邻重复短句：丢弃后一句
        out.append(p)
    return "".join(out)


def normalize_reasoning_for_display(
    raw: str | None,
    character_name: str | None = None,
    user_name: str | None = None,
) -> str | None:
    """思考上屏前：对称人称归一（名字自称→我、用户→昵称）+ 去自我编排标签 + 剥系统/技术/数值穿帮句。

    名字/昵称缺失（None）时跳过对应替换、只做标签剥离与穿帮脱敏，不报错。
    归一后无内容返回 None（前端据此不渲染思考块）。
    """
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    # B. 长度标签先转口语，再剥其余标签前缀
    text = _LEN_RE.sub(lambda m: _LEN_MAP.get(m.group(1), ""), text)
    text = _LABEL_PREFIX_RE.sub("", text)
    # A. 对称人称归一
    text = _self_first_person(text, character_name)
    text = _addressee_by_nickname(text, user_name)
    # C. 按句切段、段内按最小子句剔除真穿帮（只丢含穿帮词的子句；取舍见 _strip_leak_segments）
    out = _strip_leak_segments(text)
    # P3-6：相邻重复短句保守去重（仅完全相同的短句合并，不动长句/其它标点）
    if out:
        out = _dedup_adjacent_short_sentences(out)
    return out or None
