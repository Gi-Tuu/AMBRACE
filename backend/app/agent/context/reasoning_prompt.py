"""思考过程上屏规范（2026-09-10；2026-09-16 批次四任务 3 收紧口径）：第一人称内心独白 + 穿帮边界。

挡位 1（正文开头【推理：…】）与挡位 2（原生 reasoning_content）共用同一条指令与同一条
上屏归一管线：
- `reasoning_instructions_for(level, name=..., user=...)`：注入侧（0 关=空 / 1=1 条 / 2=2 条）
- `normalize_reasoning_for_display(raw, character_name, user_name)`：上屏侧（对称人称归一 +
  系统/技术/数值穿帮句剔除 + P1-5 元话语黑名单：策略/长度/我决定加图/本轮提醒/规则说 等
  「决策笔记·工单提纲·提示词回声」一律不上屏）

P1-5（2026-09-16）：用户反馈「思考没人味」——现场 meta.reasoning 里出现
「策略标记先 / 长度：短一点 / 我打算：短回复 / 规则说每回合只输出一行策略标记 / 本轮提醒说
可以发图」这类写给自己看的工单。口径统一为：思考只能是角色此刻的内心独白（看到什么、感觉
什么、想起什么、想怎么做），不能是流程说明、格式标签或提示词回声。
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
    "更不要把思考写成给自己下的工单：不准出现「策略/长度/篇幅/格式」「我决定加图/要不要发图/"
    "本轮提醒/规则说/系统提示/提示词/推理行/标记」这类决策笔记、格式标签与提示词回声——"
    "只写你此刻看到的、感觉到的、想起的、想做的，像人在心里嘀咕，不是交作业。\n"
    "对比——错误（名字自称 + 叫对方「用户」）：【推理：用户说回来了，sam应该回应，sam把肉盛出来，问他吃没。】\n"
    "正确（自称「我」、称对方昵称）：【推理：（轩刚好回来，肉也焖好了。我嘴上不饶他，手已经把肉盛出来，顺口问一句吃没就行。）】\n"
    "反例（工单式决策笔记，禁止）：【推理：策略：简短；长度：短；我决定加图；本轮提醒说可以出图。】\n"
    "反例（提示词回声，禁止）：【推理：规则说每回合只输出一行策略标记，格式是策略名加长度。】\n"
    "正例（第一人称内心独白）：【推理：他今天听着挺累，我先陪他说两句，别的先不追问。】\n"
    "正例（第一人称内心独白）：【推理：（他嘴上说没事，手指却一直抠着杯沿。算了，不戳穿，先给他倒杯热的。）】\n"
    "正文开头若输出【推理：……】，请单独成行、置于正文之前；回复极短或无需内心活动时可省略本行。\n"
    "内心活动只能写在【推理：……】这一行里：不要用中文括号（……）在正文里另写一段内心活动，"
    "也不要只输出一段括号思考而不给正文。\n"
    "但**正文里的动作、神态与场景小字必须照常写**，用中文括号放在动作发生的位置，"
    "例如（把你按在墙上）、（低头笑了一下）、（手已经托住你后腰）——它们是正文的画面感，"
    "不是内心活动：不要省略、不要改写进【推理】行、也不要因为图省事就少写。"
)

# 挡位 2 专用补充（原生 thinking 通道未必遵守正文标记，再补一条针对「思考过程本身」的约束）
REASONING_FIRSTPERSON_HINT = (
    "注意：你接下来的内部思考(reasoning)也必须用第一人称「我」称呼你自己、用对方昵称（{user}）或你/他/TA 称呼对方，"
    "不要写你自己的名字、不要写「用户」二字；并且不要在思考里出现系统注入规则、工具/技术字段名或任何数值计量"
    "（如百分比、饱食度、token）。\n"
    "内部思考是心里嘀咕，不是流程说明：不要写「策略/长度/篇幅/格式」标签，不要写「我决定加图/要不要发图/"
    "本轮提醒说/规则说」这类决策笔记与提示词回声，直接写你此刻的感受和想做的事。"
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


# ── A. 元话语/后台/技术/数值黑名单：命中即整句剔除（正常的「他说/我应该/首先」不在此列，保留）──
# 批次四任务 3（2026-09-16，P1-5）：原口径只把「系统注入/技术字段/数值」当穿帮，实测仍漏掉
# 「给自己下的工单」——现场 meta.reasoning 样本：「策略标记先」「输出要简短。我打算：短回复」
# 「规则说每回合只输出一行策略标记，格式是策略名加长度」「要不要发图？氛围合适可以发」。
# 用户口径：思考只能是角色第一人称内心独白。黑名单只收「不可能出现在内心独白里」的元话语
# （流程/格式/标签/提示词回声/工具决策），自然的情绪、动作、判断不受影响。
_REASONING_LEAK_LINE = [
    # A1. 决策笔记/工单标签：策略、长度、格式、回复规划
    r"策略", r"规划建议", r"回复长度", r"篇幅", r"长度", r"格式\s*[：:]",
    r"回复\s*[：:]", r"正文", r"内容大概", r"输出\s*[：:]", r"输出要", r"要输出",
    r"我(?:决定|打算|准备)\s*[：:]",
    r"(?:简短|简洁|短一点|短点|长一点|长点)(?:地)?(?:回应|回复|回答|说|回|就行|即可)?",
    r"(?:简短|简洁|短|长)(?:回复|回应)", r"文案",
    r"我(?:该|应当|应该|需要|得)(?:简短|简洁|简单|冷淡|冷静|放松|嘴硬|认真|温柔|直接|自然|克制)",
    r"语气\s*[：:]", r"语气(?:要|得|应该|需|放松|冷淡|冷|淡|软|硬|温柔|自然|平静|随意)",
    r"别腻歪", r"不腻歪", r"不要腻歪",
    r"别(?:啰嗦|唠叨|揪着|揪住|揪)", r"不要(?:啰嗦|唠叨)",
    r"回复内容", r"回应内容", r"我(?:发|说|写|回)一句", r"回复(?:要|需|应|得)",
    r"保持(?:冷漠|冷淡|冷静|简短|自然|克制|放松)", r"别(?:过度|太)", r"不用太长|不要?太长",
    # 全角冒号拆开后残留的标签头（「回应」「回复」「正文」「我该说」…整段即标签，无内容）
    r"^(?:回应|回复|回答|正文|输出|格式|策略|长度|篇幅|标记|规划建议|语气|文案|图|画面|图片|"
    r"我(?:该|可以|要|就)?(?:说|回|写|发)(?:一句)?|我(?:决定|打算|准备)|可以说)$",
    r"\d+\s*[-~～到至]\s*\d+\s*句", r"一两三句",
    r"^(?:很短|偏长|中长|中等|短|中|长|简短|简洁|短点|长点)$",
    # A2. 提示词/系统回声
    r"本轮提醒", r"系统提示", r"系统指令", r"提示词", r"规则说", r"规则要求", r"按规则",
    r"每回合", r"每个回合", r"这回合", r"格式说", r"指令说", r"提醒说",
    # A3. 标记/工具决策回声
    r"【推理", r"推理行", r"标记", r"动作小字", r"带点动作", r"加个?动作",
    r"GEN_IMAGE", r"tool_call",
    r"(?:要不要|可以|该不该|是否|决定|打算|准备).{0,6}(?:发图|加图|出图|配图|画图|生图|发个?图|配个?图|加张图|发张图)",
    r"氛围合适", r"配(?:个|张|一)?图",
    # A4. 既有后台/技术/数值穿帮（保留原口径）
    r"天气注入", r"以当前注入为准", r"注入为准", r"上下文说",
    r"prompt", r"extra_body", r"max_tokens", r"\bMCP\b", r"\btimer\b", r"extra_capabilit",
    r"饱食度", r"\d+\s*%", r"token", r"配额|额度",
]
_LEAK_RE = re.compile("|".join(_REASONING_LEAK_LINE), re.IGNORECASE)

# ── A'. 标记语法残片判定（P1-5）──
# 剔除元话语后，仍可能残留模型抄写的标记语法：「……】行」「【日常关心」「<短中长>】」
# 「可以加个[CAL_NOTE]？」。只认「不配对/空/元话语内容」的方括号 + 尖括号/书名号 +
# [CAL_NOTE] 这类全大写标签；正常的「想想【注意】这点」（成对、内容非元话语）必须保留
# （test_reasoning_marker_tolerance 的口径：取本行最后一个闭合即可）。
_BRACKET_PAIR_RE = re.compile(r"【([^【】]*)】")
_MARKER_ONLY_RE = re.compile(r"^[\s…\.。、，,·\-—<>=]*$")
_MARKER_META_RE = re.compile(r"推理|策略|长度|格式|标记|短中长|回应|回复")
_MARKER_TAG_RE = re.compile(r"\[[A-Za-z_]{2,}\]")


def _has_marker_residue(clause: str) -> bool:
    """子句是否含标记语法残片（未配对/空的【】、尖括号书名号、[CAL_NOTE] 式全大写标签）。"""
    if not clause:
        return False
    if re.search(r"[《》<>]", clause) or _MARKER_TAG_RE.search(clause):
        return True
    if clause.count("【") != clause.count("】"):
        return True
    for inner in _BRACKET_PAIR_RE.findall(clause):
        body = inner.strip()
        if _MARKER_ONLY_RE.match(body) or _MARKER_META_RE.search(body):
            return True
    return False

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
# 子句分隔符：逗号/顿号/分号 + 破折号 + 全角冒号（P1-5 现场形态：
# ①「我该简单叮嘱几句——腰别站着讲」不分破折号会连「腰别站着讲」一起丢；
# ②「回应：嫌早是吧」「回复内容：确认…」是全角冒号标签头，拆开后只丢标签、留内容。
# 只收全角「：」不收半角「:」——现场有「现在19:01」「15:42」这类时间，半角冒号拆开会把
# 时间切成「现在19，01」。
_SUBCLAUSE_SPLIT_RE = re.compile(r"[，,、；;—：]")


def _strip_leak_segments(text: str) -> str:
    """按句切段、段内按最小子句剔除黑名单内容，剩余子句用「，」重拼，各段直接拼接。

    与旧实现（整段命中 _LEAK_RE 即整段删）相比，这里只丢真正含元话语/穿帮词的子句，
    避免「他刚回来，肉也焖好了，天气注入说是晴以当前注入为准，我先把肉盛出来问他吃没。」
    这类逗号长句被整条清空、前端思考块凭空消失。

    P1-5（2026-09-16）：黑名单扩到「决策笔记/工单提纲/提示词回声」（_REASONING_LEAK_LINE A1~A3）。
    仍按**最小子句**剔除而非整行：模型常把工单与自然内心写在同一段（现场 12012：前半段
    「我该简短回应…输出策略标记」+ 后半段「他说忘了。意料之中…得让他早点收。」），整行剔除会
    把自然内心一起带走；子句级的保证是等价的——命中元话语的内容 100% 不上屏。

    已知取舍（有意为之，非缺陷）：
    - 段内重拼会把原有的「、」「；」统一成「，」，只改标点形态、不改语义；
    - 连续句末符（如「。！」）只保留第一个，后一个标点会被丢弃；
    - \\n 与 / 只作为切段边界，不保留在输出里。
    """
    kept_segments: list[str] = []
    buffer = ""

    def _render(buf: str) -> str:
        clauses = [c.strip() for c in _SUBCLAUSE_SPLIT_RE.split(buf) if c.strip()]
        # P1-5：剔除命中黑名单的子句与标记语法残片（「】」「【日常关心」「<短中长>】」）。
        # 注意不按「必须含汉字/字母/数字」过滤——纯标点子句里有收尾的「）」「」」，
        # 一刀切会把括号推理的右括号吃掉。
        return "，".join(
            c for c in clauses
            if not _LEAK_RE.search(c) and not _has_marker_residue(c)
        )

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
# 相邻两句完全相同且每句 ≤12 字才合并为一句——模型正文自写一遍或口语化重复时
# （如「好。好。」）产生的冗余句。长句 / 两个不同短句 / 其它标点语气一律不动（仅针对句末符分隔的相邻整句）。
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
    """思考上屏前：对称人称归一（名字自称→我、用户→昵称）+ 剥元话语/后台/技术/数值句。

    P1-5（2026-09-16）：原「长度标签转口语 + 标签前缀剥离」已废弃——「策略/长度/格式」本身就是
    要移除的工单元话语（用户 09-16 拍板），现在由 _REASONING_LEAK_LINE 统一按子句剔除，
    不再改写保留（旧行为会把「长度：短」转成「我回短点」留在思考里）。

    名字/昵称缺失（None）时跳过对应替换、只做黑名单剔除，不报错。
    归一后无内容返回 None（前端据此不渲染思考块）。
    """
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    # A. 对称人称归一
    text = _self_first_person(text, character_name)
    text = _addressee_by_nickname(text, user_name)
    # B. 按句切段、段内按最小子句剔除元话语/穿帮（只丢命中黑名单的子句；取舍见 _strip_leak_segments）
    out = _strip_leak_segments(text)
    # P3-6：相邻重复短句保守去重（仅完全相同的短句合并，不动长句/其它标点）
    if out:
        out = _dedup_adjacent_short_sentences(out)
    return out or None


# ── D. 括号推理剥离（2026-09-13 证据A：模型用中文括号把内心活动写在正文开头，
# 绕过【推理】标记路径；流式分块又把括号段与正文拆进不同块 → 用户看到「被气泡切开的括号」）──
# 保守判定（全部满足才判为推理）：正文以开括号起头 + 括号段内文 ≥15 字（动作小字一般 ≤10 字）
# + 段内含分析性特征词。句中/句末括号、短括号、动作描写一律保留（微信链路用户明确要求保留
# 「（摸摸你的头）」这类动作小字，勿收紧）。
_BRACKET_ANALYTIC_RE = re.compile(r"他|她|我|先|别|顺带|其实|话说|语气|别绕")
_BRACKET_MIN_INNER_LEN = 15
# 2026-09-15 真机反馈：亲密/日常场景的「动作小字」被误当推理吃掉过（真机：动作描写整体消失、剧情变干）。
# 判定改为「含身体动作/神态词 且 不含强分析标记」→ 一律当正文保留；只有强分析标记才是内心活动。
_BRACKET_ACTION_WORD_RE = re.compile(
    r"抱|搂|揽|亲|吻|摸|抚|揉|捏|拍|推|拉|攥|握|扣|压|按|抵|蹭|托|扶|拽|勾|牵|抬|低头|抬头|回头|转身|俯身|凑|贴|靠|"
    r"笑|叹|瞥|瞪|盯|看|望|伸手|解|掀|扯|脱|穿|捞|拥|枕|趴|躺|坐|站|跪|蹲|迈|退|走|递|端|拿|盛|倒|擦|"
    r"手|臂|掌|指|腰|肩|下巴|额头|唇|舌头|胸|腿"
)
_BRACKET_META_RE = re.compile(
    r"其实|别绕|顺带|话说|语气|应该|毕竟|是不是|要不要|估计|打算|想着|琢磨|意识到|看起来|"
    r"先别|别让|不能|怕|得让|怎么|怎么办|怎么回|怎么答|说什么|接什么"
)


def extract_leading_bracket_reasoning(text: str) -> tuple[str, str]:
    """识别「写在正文开头的中文括号内心活动」，返回 (可见正文, 应并入 reasoning 的片段)。

    - 文本不以开括号起头 → 原样返回 (text, "")；
    - 有配对闭括号：括号段 = 首个闭括号（含）之前的整段，rest 为其后正文；
      无闭括号（被截断，如 id 11692）：全文按候选处理、rest 为空；
    - 判为推理：返回 (rest, 括号段内文)；未判为：原样返回 (text, "")。
    """
    if not text:
        return text, ""
    s = text.strip()
    if not s or s[0] not in "（(":
        return text, ""
    close_idx = -1
    for i, ch in enumerate(s):
        if ch in "）)":
            close_idx = i
            break
    if close_idx >= 0:
        inner = s[1:close_idx].strip()
        rest = s[close_idx + 1:].strip()
    else:
        inner = s[1:].strip()
        rest = ""
    if _BRACKET_ACTION_WORD_RE.search(inner) and not _BRACKET_META_RE.search(inner):
        return text, ""          # 动作/神态描写＝正文画面感，保留（2026-09-15 真机反馈）
    if len(inner) < _BRACKET_MIN_INNER_LEN or not _BRACKET_ANALYTIC_RE.search(inner):
        return text, ""
    return rest, inner
