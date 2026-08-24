"""LLM 回复解析器：提取记忆、自述更新、状态更新，拆分长回复"""
import re
from app.utils.logger import get_logger

_logger = get_logger("agent.response_parser")

# ── 个人信息提取正则 ──
_INFO_PATTERNS = [
    (r"(?:我叫|我是|名字叫|呼唤我|喊我|可以叫我)\s*(\S{1,8})", "user_info", "用户的名字"),
    (r"(?:我今年|年龄|岁数|我\s*\d{1,3}\s*岁)", "user_info", "用户的年龄"),
    (r"(?:我(?:喜欢|爱吃|爱喝|最爱|超爱|特别喜欢|好喜欢|比较喜欢)\s*(\S+?)(?:呢|啊|呀|哈|的|$))", "preference", "用户的喜好"),
    (r"(?:我(?:讨厌|不喜欢|受不了|害怕|反感|吃不了|不能吃)\s*(\S+?)(?:呢|啊|呀|哈|的|$))", "preference", "用户的厌恶"),
    (r"(?:我住在|我家在|我来自|我是.*?人|我的家在)\s*(\S+?)(?:的|呢|啊|$)", "user_info", "用户所在地"),
    (r"(?:我在|我从事|我的工作|我是.*?(?:学生|老师|工程师|设计师|程序员|医生|护士|律师|经理|销售|运营|产品))", "user_info", "用户的职业"),
    (r"(?:我想要|我想去|我想吃|我想买|打算去|准备去)\s*(\S+)(?:呢|啊|哈|的|$)", "preference", "用户的愿望"),
    (r"(?:好喜欢|好爱|爱上|喜欢上)\s*(\S{1,6})", "user_info", "用户的情感"),
    (r"(?:我今天|昨天|前天|刚才|刚刚)\s*(\S+)", "event", "用户的活动"),
    (r"(?:我叫|我是|名字叫|呼唤我|记得我叫|记得我)\s*(\S{1,8})\s*(?:啊|呢|吗|哈|的|$)", "user_info", "用户的名字"),
]

_EMOTIONAL_KEYWORDS = [
    "生气", "伤心", "难过", "愤怒", "委屈", "哭", "气", "讨厌",
    "恨你", "混蛋", "滚开", "不想理你", "分手", "绝交",
    "开心", "幸福", "感动", "爱", "喜欢", "想你",
]


def extract_info_from_message(text: str) -> list[dict]:
    """从消息中用正则提取个人信息"""
    memories = []
    for pattern, mem_type, title_prefix in _INFO_PATTERNS:
        if re.search(pattern, text):
            memories.append({
                "type": mem_type,
                "title": title_prefix,
                "content": text[:100],
                "importance": 2,
            })
    return memories


# 独立表情行：emoji 开头 + 空格 + 短名称（AI 适机发表的"表情包"消息）
_EMOJI_LINE_RE = re.compile(
    r"^[\U0001F000-\U0001FAFF\U0001F300-\U0001F5FF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u2764]+\s*\S{1,10}$"
)


def _is_emoji_line(line: str) -> bool:
    """识别独立表情行（如 '😹 猫猫笑哭'），用于把 AI 的表情拆成单独消息块"""
    return bool(line.strip()) and bool(_EMOJI_LINE_RE.match(line.strip()))


# 非对话文本（动作/神态括号块）识别：全角/半角、成对、无嵌套、<=80 字（2026-08-11 由 40 调高）
_STAGE_BLOCK_RE = re.compile(r"([（(][^（）()]{1,80}[）)])")


def _split_by_stage_blocks(text: str) -> list[str]:
    """非对话文本（括号块）作为气泡分开点（2026-08-08 用户建议）：
    - 前导括号（该段开头）→ 归属本气泡开头（前端显示上方小字 + ↓）；
    - 其余括号 → 拼到前一段末尾并结束该段（前端显示下方小字 + ↑），其后文本进入新气泡；
    - 连续多个括号合并到同一段末尾；无括号文本原样返回。"""
    if not text or not re.search(r"[（(]", text):
        return [text] if text.strip() else []
    parts = _STAGE_BLOCK_RE.split(text)
    chunks: list[str] = []
    cur = ""
    for part in parts:
        if not part:
            continue
        if _STAGE_BLOCK_RE.fullmatch(part.strip()):
            if cur.strip():
                cur += part  # 括号归属前一段末尾 → 该气泡结束
                chunks.append(cur)
                cur = ""
            else:
                cur = part  # 前导括号：留在本气泡开头（上方小字）
        else:
            cur += part
    if cur.strip():
        chunks.append(cur)
    return [c.strip() for c in chunks if c.strip()]


# ── 增量流式语义切块（SSE 真流式链路）─────────────────────────────
# 开/闭括号配对：用于判断「未闭合括号」并保持块边界单调（不闪退、不把块切进标记/神态块中间）。
_OPEN_MAP = {"【": "】", "[": "]", "（": "）", "(": ")"}

# 展示层需剥离的正文标记（与 parse_response / actions 同源；仅剥离「已闭合」标记）
_DISPLAY_STRIP_PATTERNS = [
    re.compile(r"[\[【]\s*(?:策略|推理|记忆|自述更新|自述删除|状态更新)\s*[：:][^\]】]*[\]】]"),
]


def _find_hold_index(raw: str) -> int:
    """返回最后一个未闭合开括号的位置（从该位置起需要 hold，保证展示文本单调）。

    把 `【` `[` `（` `(` 视为可能开始标记/神态块的开括号，若尾部留有未闭合开括号，
    则该开括号及其后文本暂不进入展示层（等待闭合），避免块被切进括号中间。
    """
    stack: list[tuple[int, str]] = []
    for i, ch in enumerate(raw):
        if ch in _OPEN_MAP:
            stack.append((i, ch))
        elif ch in ("】", "]", "）", ")"):
            if stack and _OPEN_MAP[stack[-1][1]] == ch:
                stack.pop()
    if stack:
        return stack[-1][0]
    return len(raw)


def strip_stream_display(text: str) -> str:
    """剥离展示层不需要的正文标记（推理/记忆/自述/状态/策略 + 工具动作标记）。

    仅作用于「已闭合」标记（未闭合部分由 _find_hold_index 提前 hold），返回干净展示文本。
    """
    if not text:
        return text
    out = text
    for pat in _DISPLAY_STRIP_PATTERNS:
        out = pat.sub("", out)
    # 工具动作标记（SEARCH/GEN_IMAGE/IMG_TEXT/CAL_NOTE/MEMO/timer）统一剥离
    try:
        from app.agent.actions import strip_actions
        out = strip_actions(out)
    except Exception:
        pass
    return out


class IncrementalResponseChunker:
    """增量语义切块器：边流式接收 LLM 增量，边按句子/情绪边界切出「完整块」。

    行为与 split_response 尽量对齐（句末标点 + 3 句/80 字成块；情绪态整段不拆），
    同时保证：
    - 块边界单调（不闪退）：未闭合的 `【`/`[`/`（`/`(` 及其后文本暂不入块；
    - 生成结束调用 flush() 冲刷最后一块；
    - 块文本为「剥离展示标记后」的干净正文（与落库正文一致）。
    """

    def __init__(self, emotional_state: str = "", max_sentences: int = 3, max_len: int = 80):
        self._raw = ""
        self._clean = ""
        self._block = ""
        self.disp_delta = ""  # 本次 feed 产生的增量展示文本（供上游 typewriter 推送）
        self._emotional = emotional_state in ("angry", "sad", "upset")
        self._max_s = int(max_sentences or 3)
        self._max_len = int(max_len or 80)

    @property
    def clean_text(self) -> str:
        """当前累计的干净展示文本（所有标记已剥离，供生成结束落库）。"""
        return self._clean

    def feed(self, delta: str) -> list[str]:
        """喂入增量文本，返回本轮切出的完整块（可能为空）；增量展示文本存 disp_delta。"""
        self.disp_delta = ""
        if not delta:
            return []
        self._raw += delta
        self.disp_delta = self._recompute_clean()
        if not self.disp_delta:
            return []
        if self._emotional:
            self._block += self.disp_delta
            return []
        self._block += self.disp_delta
        return self._emit_blocks()

    def _recompute_clean(self) -> str:
        """按未闭合括号定位 hold 点，剥离已闭合标记，返回本次新增的干净展示文本。"""
        hold = _find_hold_index(self._raw)
        clean = strip_stream_display(self._raw[:hold])
        if not clean.startswith(self._clean):
            # 防御：理论上不会发生（committed 前缀只增长），发生则丢弃本次增量，保证单调
            self._clean = clean
            return ""
        new = clean[len(self._clean):]
        self._clean = clean
        return new

    def _emit_blocks(self) -> list[str]:
        """把积累块按句子边界切出完整块（3 句/80 字规则），剩余留在 _block。"""
        if not self._block:
            return []
        blocks, rem = self._split_sentences(self._block)
        self._block = rem
        return blocks

    def _split_sentences(self, buf: str) -> tuple[list[str], str]:
        sents = re.split("(?<=[。！？!?；;…])", buf)
        incomplete = sents[-1] if sents else ""
        complete = sents[:-1]
        out: list[str] = []
        acc = ""
        acc_len = 0
        cnt = 0
        for s in complete:
            s = s.strip()
            if not s:
                continue
            acc += s
            acc_len += len(s)
            cnt += 1
            if cnt >= self._max_s or acc_len > self._max_len:
                out.append(acc)
                acc = ""
                acc_len = 0
                cnt = 0
        rem = (acc + incomplete).strip() if acc else incomplete.strip()
        return out, rem

    def flush(self) -> list[str]:
        """流结束：冲刷最后一块（含残余缓冲）。"""
        if self._emotional:
            text = self._clean.strip()
            self._reset()
            return [text] if text else []
        # 处理可能残余的完整块 + 强制冲刷不足一块的剩余
        blocks, rem = self._split_sentences(self._block)
        out = list(blocks)
        if rem:
            out.append(rem)
        self._reset()
        return [b for b in out if b.strip()]

    def _reset(self) -> None:
        self._raw = ""
        self._clean = ""
        self._block = ""
        self.disp_delta = ""


def split_response(text: str, emotional_state: str = "") -> list[str]:
    """将长回复拆分成多条消息块（chunked 回复用）；独立表情行单独成块；
    普通态下括号块（动作/神态）作为新的气泡分开点（情绪态保持整段连贯不拆）"""
    text = text.strip()
    if not text:
        return [text]
    # 先抽出独立表情行，其余正文按原逻辑拆分
    emoji_lines = []
    body_parts = []
    for ln in text.split("\n"):
        if _is_emoji_line(ln):
            emoji_lines.append(ln.strip())
        else:
            body_parts.append(ln)
    body = "\n".join(body_parts).strip()
    if not body:
        return emoji_lines if emoji_lines else [text]

    is_emo = False
    if emotional_state in ("angry", "sad", "upset"):
        is_emo = True
    # 情绪判定基于剥离括号块后的正文（避免"（气喘吁吁）"的"气"误触发情绪态导致整段不拆）
    _body_no_stage = _STAGE_BLOCK_RE.sub("", body)
    if any(kw in _body_no_stage for kw in _EMOTIONAL_KEYWORDS) or len(body) > 200:
        is_emo = True
    if is_emo:
        chunks = [body]
    else:
        sentences = re.split("(?<=[。！？])", body)
        sentences = [s.strip() for s in sentences if s.strip()]
        if not sentences:
            chunks = [body]
        else:
            chunks = []; cur = []; clen = 0
            for s in sentences:
                cur.append(s); clen += len(s)
                if len(cur) >= 3 or clen > 80:
                    chunks.append("".join(cur)); cur = []; clen = 0
            if cur: chunks.append("".join(cur))
            if not chunks:
                chunks = [body]
        # 2026-08-08：括号块作为气泡分开点（仅普通态；情绪态已整段返回不拆）
        _final = []
        for _c in chunks:
            _final.extend(_split_by_stage_blocks(_c))
        chunks = _final or chunks
    # 表情行追加到正文之后（AI 先说句话再发表情）
    return chunks + emoji_lines


def parse_response(response: str, state: dict) -> dict:
    """解析 LLM 回复，提取记忆/自述/状态更新，返回更新后的 state"""
    # 0. 认知循环 v2.1：剥离规划策略行（【策略：…；长度：…】），正文解析逻辑不变
    _strategy_match = re.search(r"[\[【]\s*策略\s*[：:]\s*([^\]】]+)[\]】]", response)
    if _strategy_match:
        state["plan_strategy"] = _strategy_match.group(1).strip()
        response = re.sub(r"[\[【]\s*策略\s*[：:][^\]】]*[\]】]\s*", "", response).strip()
    else:
        state["plan_strategy"] = None

    # 0.5 推理内容（2026-08-10）：角色开启「思考过程」时模型在开头输出【推理：…】；
    #     解析进 state["reasoning"]（落库 extra_meta，前端气泡顶部展示），并从正文剥离
    _reasoning_match = re.match(r"[\[【]\s*推理\s*[：:]\s*([^\]】]+)[\]】]\s*", response)
    if _reasoning_match:
        state["reasoning"] = _reasoning_match.group(1).strip() or None
        response = response[_reasoning_match.end():].strip()
    # 无【推理】标记时不覆盖 state["reasoning"]：深度思考挡位（level 2）的
    # reasoning_content 已由 nodes.generate_response 写入，避免被清空

    # 1. 提取【记忆: ...】标记
    memory_pattern = r"[\[【]\s*记忆\s*[：:]\s*(.*?)[\]】]"
    memory_matches = re.findall(memory_pattern, response)

    # 清理回复文本
    text = re.sub(r"\s*[\[【]\s*记忆\s*[：:].*?[\]】]\s*", "", response).strip()
    state["ai_response"] = text

    # 2. 收集新记忆
    new_memories = []

    for mem_content in memory_matches:
        new_memories.append({
            "type": "user_info", "title": "", "content": mem_content.strip(), "importance": 2,
        })

    # 用户信息只从"用户消息"中提取；AI 回复文本含台词（如"爱上我了吧"），
    # 若也跑正则会把 AI 自己的话当成用户信息落库（身份混淆），2026-08-06 移除。
    if not new_memories:
        info_memories = extract_info_from_message(state.get("user_message", ""))
        new_memories.extend(info_memories)

    state["new_memories"] = new_memories or []
    state["should_update_memory"] = bool(new_memories)

    # 3. 检测自述更新
    bio_update = None
    bio_match = re.search(r"[「\[【]自述更新\s*[:：]\s*(.*?)[」\]】]", response)
    if bio_match:
        bio_update = bio_match.group(1).strip()
    bio_del = re.search(r"[「\[【]自述删除\s*[:：]\s*(.*?)[」\]】]", response)
    if bio_del:
        del_keyword = bio_del.group(1).strip()
        current_bio = state.get("character_info", {}).get("self_statement", "")
        if del_keyword and current_bio:
            new_bio = re.sub(re.escape(del_keyword), "", current_bio).strip().strip(",。，。")
            if new_bio != current_bio:
                bio_update = new_bio
    state["bio_update"] = bio_update
    # 剥离自述标记（2026-08-14：自述是内部动作，不入正文）
    response = re.sub(r"[「\[【]\s*自述(?:更新|删除)\s*[：:].*?[」\]】]\s*", "", response).strip()

    # 4. 检测状态更新（剥离标记：改为前端气泡下方小字，不入正文；P2-1：正则收敛到 app.agent.actions）
    from app.agent.actions import extract_status_update, strip_status_update
    state["status_update"] = extract_status_update(response)
    response = strip_status_update(response)

    # 更新正文：基于剥离记忆后的 text 再剥离自述/状态（第1步的 text 才是记忆清理后的正文）
    state["ai_response"] = re.sub(
        r"[「\[【]\s*(?:自述(?:更新|删除)|状态更新)\s*[：:].*?[」\]】]\s*", "", text
    ).strip()

    state["intent"] = "chat"
    return state
