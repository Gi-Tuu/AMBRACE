# -*- coding: utf-8 -*-
"""批 0-11（2026-09-28，雷达 44）：专名确定性匹配（人名 / 昵称 / 关系称谓）。

【为什么补第三路】现网召回＝向量（bge-m3）＋关键词（BM25）两路 RRF 融合，两路都吃「词形」。
而专名恰恰最容易字面错开：
- 「我妈最近怎么样」↔ 记忆写「母亲住院了」；
- 「mike 爱吃什么」↔ 记忆写「MIKE 只吃辣的」；
- 「阿明养的那只」↔ 记忆写「小明养了只猫」。
Mem0 的 multi-signal 检索（语义 / 关键词 / 实体三路并行评分后融合）就是补这一路；该报告为厂商
自评、增益需自测（见 docs/coze-memory-research-radar.md §2-E），所以本模块**只做确定性部分**：
字符串 + 正则 + 字典，零模型、零外网、零新依赖，抽出的 id 交回 `retrieve.py` 并入既有 RRF 与
`_rerank`——不另开排序体系、不插队、不剔除任何已有候选。

【边界】本模块不读 flag、不碰库（门控与查询都在 retrieve.py），只做两件事：
1. 从查询里抽「专名词面」——抽不出来就返回空列表，调用方连查询都不发（零行为）；
2. 判定正文是否真的含该词面（归一化后子串）并给出**命中理由**（哪个词面、走哪条途径、是否
   只在全/半角或大小写折叠后才对上）。
刻意不做模糊匹配 / 拼音 / 编辑距离：宁可漏，不可误召（专名一路的价值就在「确定」）。
"""
from __future__ import annotations

import re
import unicodedata

# ---- 体积钳制（防长句抽出一堆词面把候选池灌满、把 SQL 拖成一长串 OR）----
ENTITY_TERMS_MAX = 4      # 单轮最多用几个词面去匹配
ENTITY_TERM_MIN_LEN = 2   # 词面最小长度（单字「妈/猫」噪音大，一律不抽）
ENTITY_TERM_MAX_LEN = 12  # 词面最大长度（引号里的整段话不算专名）
ENTITY_LITERALS_MAX = 16  # 交给 SQL 粗筛的词面形状总数上限

_CJK = "一-鿿㐀-䶿"

# ---- 关系称谓字典（人名走正则，称谓走字典；单字称谓不收录，见 ENTITY_TERM_MIN_LEN）----
_RELATION_TERMS: tuple[str, ...] = (
    # 直系 / 旁系亲属（「我妈」这类口语指代是最高频的查询形状，必须进字典）
    "我妈", "我爸", "我哥", "我姐", "我弟", "我妹", "我儿子", "我女儿",
    "妈妈", "母亲", "老妈", "爸爸", "父亲", "老爸", "爸妈", "父母",
    "哥哥", "嫂子", "弟弟", "妹妹", "姐姐", "姐夫", "妹夫", "弟媳",
    "儿子", "儿媳", "女儿", "女婿", "爷爷", "奶奶", "外公", "外婆",
    "姥姥", "姥爷", "祖父", "祖母", "外祖父", "外祖母", "叔叔", "阿姨",
    "舅舅", "姑姑", "婶婶", "姑父", "姨父", "堂哥", "堂姐", "堂弟",
    "堂妹", "表哥", "表姐", "表弟", "表妹", "侄子", "侄女", "外甥",
    "家长", "家人", "亲戚",
    # 伴侣 / 前任
    "老公", "老婆", "丈夫", "妻子", "先生", "太太", "爱人",
    "男朋友", "女朋友", "男友", "女友", "对象", "恋人", "伴侣", "前任", "未婚夫", "未婚妻",
    # 社交角色
    "同事", "领导", "上司", "上级", "下属", "老板", "老师", "学生", "同学",
    "朋友", "好友", "闺蜜", "哥们", "兄弟", "姐妹", "邻居", "室友", "队友",
    "网友", "客户", "师傅", "师父", "徒弟", "玩伴",
    # 宠物（陪伴场景里宠物按家人指代，属关系称谓一族）
    "宠物", "毛孩子", "小猫", "猫咪", "猫猫", "小狗", "狗狗", "狗子",
)

# ---- 别名组（组内互为等价，命中任一即算命中；只做「同一所指的不同说法」，不做近义泛化）----
_ALIAS_GROUPS: tuple[tuple[str, ...], ...] = (
    ("妈妈", "母亲", "老妈", "我妈"),
    ("爸爸", "父亲", "老爸", "我爸"),
    ("爸妈", "父母", "双亲"),
    ("男朋友", "男友"),
    ("女朋友", "女友", "对象", "恋人", "伴侣"),
    ("老公", "丈夫", "先生"),
    ("老婆", "妻子", "太太", "爱人"),
    ("哥哥", "兄长"),
    ("姐姐", "姊姊"),
    ("爷爷", "祖父"),
    ("奶奶", "祖母"),
    ("外公", "外祖父", "姥爷"),
    ("外婆", "外祖母", "姥姥"),
    ("上司", "领导", "上级", "老板"),
    ("哥们", "兄弟", "铁哥们"),
    ("小猫", "猫咪", "猫猫"),
    ("小狗", "狗狗", "狗子", "毛孩子"),
)

# ---- 形状像专名、但不是专名的高频词（一律不当实体，防「今天/外卖」这类误召）----
_STOP_TERMS: frozenset[str] = frozenset({
    "什么", "怎么", "为什么", "哪些", "哪个", "哪里", "多少", "几点", "时候",
    "东西", "问题", "地方", "消息", "照片", "图片", "视频", "语音", "聊天",
    "计划", "安排", "外卖", "快递", "手机", "电脑", "网络", "天气", "工作",
    "学习", "学校", "公司", "家里", "身边", "别人", "人家", "大家", "我们",
    "你们", "他们", "她们", "它们", "自己", "个人", "一下", "今天", "明天",
    "昨天", "现在", "以后", "以前", "上次", "下次", "全部", "所有",
})

# 拉丁词面停用（大小写已折叠，故一律小写比对）
_STOP_LATIN: frozenset[str] = frozenset({
    "the", "and", "for", "you", "are", "was", "with", "this", "that", "ok",
    "hi", "app", "pdf", "id", "com", "www", "http", "https", "vs", "etc",
})

# 引号 / 书名号：里面的整段视为专名（用户自己划出来的边界，精度最高）
_QUOTE_PAIRS: tuple[tuple[str, str], ...] = (
    ("「", "」"), ("『", "』"), ("“", "”"), ("‘", "’"), ("《", "》"), ("【", "】"),
)

# 人名正则的前置介词（「和小林」「叫阿明」这类没打引号的人名只能靠介词锚定，只取两字）
_PERSON_RE = re.compile(rf"[和跟与对让帮叫问]([{_CJK}]{{2}})")
_LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{1,15}")

# 光杆昵称（作主语时前面没有介词可锚）：只认「小/阿/老 + 一个字」与「一个字 + 哥/姐/弟/妹」
# 两种汉语里几乎专属于人名的形状。**刻意只吃一个字**：贪婪到两个字会把「阿明养的那只」抽成
# 「阿明养」、把「推荐明哥」抽成「荐明哥」；而两字前缀形（「小芳」）本就能作为三字名（「小芳芳」）
# 的子串命中，所以收窄不损召回。姓氏单名（「老王」认，「王芳」不认）同样是有意的保守边界：
# 没有分词/词性标注时，「姓 + 名」与「形容词 + 名词」在字面上无法区分，宁可漏也不误召。
_AFFIX_HEAD_RE = re.compile(rf"([小阿老][{_CJK}])")
_AFFIX_TAIL_RE = re.compile(rf"([{_CJK}][哥姐弟妹])")
# 前后缀形状里的高频通用词：命中它们只会带来误召（「小时/老师/老人」都不是专名）
_AFFIX_NOISE: frozenset[str] = frozenset({
    "小时", "学校", "小区", "小学", "小孩", "小姐", "小心", "小事", "小说", "小票",
    "小费", "小家伙", "小憩", "小编", "小径", "小睡", "小偷", "小额", "小数", "小路",
    "阿拉", "阿胶", "阿谀", "阿弥陀佛",
    "老师", "老板", "老人", "老年", "老兵", "老家", "老虎", "老鼠", "老实", "老练",
    "老套", "老屋", "老友", "老大", "老小", "老手", "老规矩",
})

# 正则抽出来的人名候选里出现这些字 ⇒ 抽到的是短语不是名字（「我们说」「他聊」一类粘连）
_BAD_CHARS: frozenset[str] = frozenset("的了着过是在有和与也都很就还又没不你我他她它们这那要起去来到")

# 昵称前后缀（「阿明 / 小明 / 老明 / 明哥」一族互推；只对**非字典词**的专名生效）
_NICK_PREFIXES: tuple[str, ...] = ("小", "阿", "老")
_NICK_SUFFIXES: tuple[str, ...] = ("哥", "姐", "弟", "妹", "总", "叔", "姨")

# ---- 模块加载期一次成型的查表结构 ----
_RELATION_SORTED: tuple[str, ...] = tuple(sorted(_RELATION_TERMS, key=lambda t: (-len(t), t)))
_ALIAS_BY_TERM: dict[str, tuple[str, ...]] = {
    term: group for group in _ALIAS_GROUPS for term in group
}
# 字典词（称谓 + 别名组成员）：昵称派生对它们一律关闭，避免「妈妈」派生出「小妈」
_LEXICON: frozenset[str] = frozenset(_RELATION_TERMS) | frozenset(_ALIAS_BY_TERM)


def normalize(text: str) -> str:
    """归一：NFKC（全角→半角、兼容字→通用字）+ casefold + 连续空白压成单空格。

    只服务「判等 / 重合」，**不改变任何落库内容**（与 perception_tier / lifecycle_policy 同口径）。
    """
    return " ".join(unicodedata.normalize("NFKC", text or "").casefold().split())


def to_fullwidth(text: str) -> str:
    """半角 ASCII → 全角（NFKC 的逆映射，只处理可见字符与空格）。

    用途：SQL 粗筛要多备一种「形状」。库里若写的是「ＭＩＫＥ」，用 `%mike%` 粗筛捞不到，
    补一个全角词面才捞得进来，最终判定仍由 `normalize` 做（折叠后相等）。
    """
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0x21 <= code <= 0x7E:
            out.append(chr(code + 0xFEE0))
        elif ch == " ":
            out.append("\u3000")
        else:
            out.append(ch)
    return "".join(out)


def _is_cjk_only(text: str) -> bool:
    return bool(text) and all("一" <= c <= "鿿" or "㐀" <= c <= "䶿" for c in text)


def _usable(term: str, *, guarded: bool = False) -> bool:
    """词面是否可用：长度合规 + 非停用词；``guarded`` 时再加一道粘连字检查。

    只有**正则**抽出来的候选需要 guarded（介词后两字极易粘成短语）；字典词与引号内整段
    本身边界清楚，不该被粘连字表误杀——「我妈」里的「我」正是所有格，不是噪音。
    """
    if not term or term in _STOP_TERMS or term in _STOP_LATIN:
        return False
    if not (ENTITY_TERM_MIN_LEN <= len(term) <= ENTITY_TERM_MAX_LEN):
        return False
    return not (guarded and _is_cjk_only(term) and any(c in _BAD_CHARS for c in term))


def _overlaps(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    """候选词面在原文里的位置是否与已采纳的词面重叠（重叠 ⇒ 是长词的碎片，丢弃）。"""
    return any(start < e and end > s for s, e in spans)


def _is_noise_affix(term: str) -> bool:
    """前后缀形状是否其实是高频通用词（「小时/老师/老人」），或整词就在称谓字典里。"""
    return term in _LEXICON or any(noise in term for noise in _AFFIX_NOISE)


def extract_terms(query: str) -> list[str]:
    """查询 → 专名词面列表（已归一、精度优先、去重、钳到 ENTITY_TERMS_MAX）。抽不出＝返回 []。

    六条来源按精度从高到低：①引号/书名号整段 ②关系称谓字典（最长优先，命中即占位，
    所以「女朋友」命中后不会再抽出「女朋」碎片）③拉丁词面（英文名/昵称）④介词锚定的两字人名
    ⑤⑥「小/阿/老 + X」「X + 哥/姐/弟/妹」两种昵称形状。先采纳者占位，后来的重叠碎片一律丢弃。
    """
    text = normalize(query)
    if not text:
        return []
    spans: list[tuple[int, int]] = []
    out: list[str] = []

    def _take(term: str, start: int, end: int, *, guarded: bool = False, affix: bool = False) -> None:
        if len(out) >= ENTITY_TERMS_MAX or not _usable(term, guarded=guarded):
            return
        if affix and _is_noise_affix(term):
            return
        if _overlaps(spans, start, end) or term in out:
            return
        out.append(term)
        spans.append((start, end))

    for open_ch, close_ch in _QUOTE_PAIRS:
        pos = text.find(open_ch)
        while pos >= 0 and len(out) < ENTITY_TERMS_MAX:
            end = text.find(close_ch, pos + len(open_ch))
            if end < 0:
                break
            _take(text[pos + len(open_ch):end].strip(), pos + len(open_ch), end)
            pos = text.find(open_ch, end + len(close_ch))

    for term in _RELATION_SORTED:
        if len(out) >= ENTITY_TERMS_MAX:
            break
        idx = text.find(term)
        if idx >= 0:
            _take(term, idx, idx + len(term))

    for m in _LATIN_RE.finditer(text):
        _take(m.group(0), m.start(), m.end())

    for m in _PERSON_RE.finditer(text):
        _take(m.group(1), m.start(1), m.end(1), guarded=True)

    for regex in (_AFFIX_HEAD_RE, _AFFIX_TAIL_RE):
        for m in regex.finditer(text):
            _take(m.group(1), m.start(1), m.end(1), guarded=True, affix=True)

    return out


def _nickname_family(term: str) -> list[str]:
    """昵称前后缀派生：阿明 / 小明 / 老明 / 明哥 互推（核心字 1~2 个汉字）。

    只对**非字典词**的专名生效。派生出的每个词面仍过 `_usable`，且核心字单独不成词面
    （单字 LIKE 会命中「明白」「姑娘」这类噪音），所以永远不会产出单字粗筛词。
    """
    if term in _LEXICON or not _is_cjk_only(term) or not (2 <= len(term) <= 3):
        return []
    if term[0] in _NICK_PREFIXES:
        core = term[1:]
    elif term[-1] in _NICK_SUFFIXES:
        core = term[:-1]
    else:
        return []
    forms = ([core] if len(core) >= 2 else [])
    forms += [p + core for p in _NICK_PREFIXES]
    forms += [core + s for s in _NICK_SUFFIXES]
    return [f for f in forms if f != term and _usable(f)]


def expand_terms(terms: list[str]) -> dict[str, tuple[str, str]]:
    """词面 → {变体: (来源词面, 途径)}（有序、去重、先到先得）。

    途径：`surface`（原词面）/ `alias`（别名组）/ `nickname`（昵称前后缀派生）。
    顺序即优先级：同一变体被多条途径产出时保留先出现的那条，命中理由才可解释。
    """
    table: dict[str, tuple[str, str]] = {}
    for term in terms:
        cands: list[tuple[str, str]] = [(term, "surface")]
        group = _ALIAS_BY_TERM.get(term)
        if group:
            cands += [(a, "alias") for a in group if a != term]
        cands += [(n, "nickname") for n in _nickname_family(term)]
        for variant, via in cands:
            if variant and variant not in table:
                table[variant] = (term, via)
    return table


def pull_literals(terms: list[str]) -> list[str]:
    """SQL 粗筛用的词面形状：每个变体备「原形 / 归一半角 / 全角」三种，去重后钳制总数。

    只是**粗筛**（LIKE 捞候选），命中与否一律由 `hit_reasons` 在内存里判定。
    """
    out: list[str] = []
    seen: set[str] = set()
    for variant in expand_terms(terms):
        for shape in (variant, normalize(variant), to_fullwidth(normalize(variant))):
            if not shape or shape in seen:
                continue
            seen.add(shape)
            out.append(shape)
            if len(out) >= ENTITY_LITERALS_MAX:
                return out
    return out


def hit_reasons(content: str, terms: list[str], *, cap: int = 2) -> list[dict]:
    """正文命中了哪个词面、走哪条途径——供 debug/trace 解释「为什么把这条捞上来」。

    返回形如 `[{"term": 查询里的词面, "via": surface|alias|nickname, "literal": 实际对上的词面,
    "folded": 是否只有折叠（全/半角、大小写、空白）后才对上}]`；不命中返回 []。
    每个查询词面最多记一条理由（surface > alias > nickname），总条数钳到 `cap`。
    """
    text = normalize(content or "")
    if not text:
        return []
    out: list[dict] = []
    for term in terms:
        for variant, (src, via) in expand_terms([term]).items():
            key = normalize(variant)
            if key and key in text:
                out.append({"term": src, "via": via, "literal": variant,
                            "folded": variant not in (content or "")})
                break
        if len(out) >= cap:
            break
    return out
