# -*- coding: utf-8 -*-
"""BM25 稀疏检索索引（2026-08-23 检索增强：jieba 搜索引擎模式 + 字符 n-gram）

========================= 设计说明（分词 / 索引 / 融合） =========================

【背景】
向量优先（bge-m3 + ChromaDB cosine）擅长语义相似，但对「精确词 / 短词」召回弱：当查询含
某个中文词根而向量嵌入没有落到该词根时，向量路可能召回不了，仅靠 SQL LIKE 逐字匹配又过于
粗暴。此处新增 BM25 关键词路，与向量路按 memory_id 混合（hybrid）互补召回。

【分词】（关键实测：jieba 精确分词漏召回）
验证过的事实：jieba 精确模式把「想画画」切成 ['想','画画']、「用户喜欢画水彩」切成
['用户','喜欢','画','水彩'] —— 两者无共享词元（'画画' vs '画'），纯 jieba 时 BM25 得分为 0，
会漏召回。因此：
1. 用 jieba.cut_for_search（搜索引擎模式），保留子词（如「水彩」能出「水彩」）；但仍无法让
   '画画' 与 '画' 共享，故继续：
2. 补充「字符 1-2 元 n-gram」：把每篇记忆/查询的中文连续字符切成 1 字与 2 字窗口。「想画画」
    的 1 字窗含 '画'，2 字窗含 '想画'/'画画'；「画水彩」的 1 字窗含 '画'。这样 '画' 成为两测
    共享词元，BM25 命中 —— 这正是改善精确/短词召回的核心。
3. 过滤：去停用词（高频虚词单字，如 的/了/在/和）、纯标点/空白、单英文字母/数字噪音；
    每篇文档内词元至多计 1 次（set 去重），避免「jieba 词 + n-gram 同词根」双重计数夸大权重。
    最小词长 2，但中文单字例外（如『画』有意义，必须保留）。

【索引】
角色级索引（BM25Okapi）+ 进程内 LRU 缓存（character_id → (memory_ids, docs_tokenized, bm25,
built_at)），上限 _BM25_CACHE_MAX=20 角色、TTL _BM25_TTL_SECONDS=15 分钟（兜底）。懒构建：
首次检索某角色时从 SQLite memories 表读「is_archived=0 且 delete_at is null」的活跃记忆
content 建索引；写入/改内容/删除记忆会显式 invalidate(character_id) 立即失效，TTL 仅作兜底。
复用 app.memory.service 的 async_session_factory（与主检索路径同一连接；测试中 monkeypatch
memsvc.async_session_factory 即隔离临时库），避免 import 环。

【持久化（2026-08-23 深化）】
为避免进程重启后首次检索重新分词建索引（懒构建约几百 ms，主要在 jieba 逐篇分词），角色索引
落盘到 backend/data/bm25_cache/<character_id>.json（项目自有缓存，非用户数据；失败静默不阻塞）。
格式自包含：记录版本戳、jieba 版本、分词参数指纹（min_word_len/ngram/停用词）。重启后首次
检索先尝试从盘加载——版本与参数指纹一致则直接由已分词文档重建 BM25Okapi（省去分词，仅剩
O(N) 的 idf 计算），否则静默回退懒构建。记忆写入/改内容/删除（invalidate）时删除对应缓存文件；
持久化全程异常静默并回退懒构建，不影响主链路。

【融合】
search_memories 中向量（dense）与 BM25（sparse）两路并行召回，按 memory_id 合并去重后
共入 _rerank（importance/时效/置顶/话题/多路命中加权）。2026-08-23 深化：新增 RRF 融合
（见 app.memory.rrf）——dense/sparse 各按 rank 归一化，融合出 relevance_bonus 注入 _rerank，
RRF 异常时静默退化为纯合并。trace 的 route 标记 hybrid/dense/sparse；BM25 路异常静默
（返回 []），不影响向量主链路与 LIKE 兜底。
"""

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from pathlib import Path

import jieba
from rank_bm25 import BM25Okapi
from sqlalchemy import select

from app.models.memory import Memory
from app.utils.logger import get_logger

_logger = get_logger("memory.bm25")

# ---- 索引缓存参数 ----
_BM25_CACHE_MAX = 20            # 进程内最多缓存 20 个角色索引（LRU）
_BM25_TTL_SECONDS = 15 * 60     # 15 分钟 TTL（兜底；写入/删除路径显式 invalidate）

# ---- 索引持久化参数（2026-08-23 深化）----
_INDEX_VERSION = 1              # 落盘格式版本戳（升版则旧缓存失效、回退懒构建）
_BM25_PERSIST_ENABLED = True    # 是否启用落盘；测试可置 False 或覆盖 _persist_root 隔离到临时目录
_persist_root: "Path | None" = None   # None → 默认 backend/data/bm25_cache（可被测试覆盖）

# ---- 分词参数 ----
_MIN_WORD_LEN = 2               # 词元最小长度（中文单字例外，见 _is_meaningful_token）
_NGRAM_SIZES = (1, 2)           # 字符 n-gram 补充（1-2 字符）
# 高频虚词 / 单字停用（过滤噪音；保留 去/要/会/到 等实义动词以保召回）
_STOPWORDS = {
    "的", "了", "是", "在", "有", "和", "就", "不", "都", "也", "很",
    "你", "我", "他", "她", "它", "这", "那", "与", "及", "或", "但",
    "而", "且", "以", "于", "之", "为", "对", "从", "被", "把", "让",
    "向", "等", "地", "得", "着", "过", "呢", "吗", "啊", "吧", "其",
    "自己", "一个", "什么", "怎么", "这样", "那样",
}

class _IndexEntry:
    """角色级索引缓存条目：memory_ids 与 tok_docs/bm25 对齐。"""
    __slots__ = ("memory_ids", "tok_docs", "bm25", "built_at")

    def __init__(self, memory_ids, tok_docs, bm25, built_at):
        self.memory_ids = memory_ids
        self.tok_docs = tok_docs    # 与 memory_ids 对齐的分词后文档（供共享词元判定/观测）
        self.bm25 = bm25            # BM25Okapi 实例（空语料时为 None）
        self.built_at = built_at    # time.monotonic() 构建时间


# character_id -> _IndexEntry（LRU，容量上限 _BM25_CACHE_MAX）
_cache: "OrderedDict[int, _IndexEntry]" = OrderedDict()


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x4E00 <= o <= 0x9FFF    # CJK 统一表意
        or 0x3400 <= o <= 0x4DBF  # CJK 扩展 A
        or 0xF900 <= o <= 0xFAFF  # CJK 兼容
    )


def _only_punct_ws(tok: str) -> bool:
    """纯标点/空白/符号（非 CJK 且非字母数字）→ True"""
    return all(not (_is_cjk(c) or c.isalnum()) for c in tok)


def _is_meaningful_token(tok: str) -> bool:
    """词元长度过滤：>= _MIN_WORD_LEN；中文单字例外（有意义）；单字符非中文剔除。"""
    if len(tok) < _MIN_WORD_LEN:
        # 仅保留「中文单字」作为最小词元（如『画』），单英文字母/数字视为噪音
        return len(tok) == 1 and _is_cjk(tok[0])
    return True


def tokenize(text: str) -> list:
    """分词：jieba.cut_for_search + 字符 1-2 元 n-gram；去停用/标点/空白/单字符噪音；词元去重。

    返回 token 列表（每个索引词元至多计 1 次，避免同词根双重计数）。纯函数、可单测。
    """
    if not text:
        return []
    tokens: list = []
    seen: set = set()

    def _add(tok: str) -> None:
        tok = tok.strip().lower()
        if not tok or tok in _STOPWORDS or _only_punct_ws(tok):
            return
        if not _is_meaningful_token(tok):
            return
        if tok in seen:
            return
        seen.add(tok)
        tokens.append(tok)

    # 1) 搜索引擎模式分词（保留长词与子词）
    for w in jieba.cut_for_search(text):
        _add(w)
    # 2) 字符 1-2 元 n-gram（只取中文连续字符；非中文走不了这步）
    chars = [c for c in text if _is_cjk(c)]
    for n in _NGRAM_SIZES:
        for i in range(len(chars) - n + 1):
            _add("".join(chars[i:i + n]))
    return tokens


def _build_corpus(docs: list):
    """在独立线程内完成「逐篇分词 + 构建 BM25」。"""
    tok_docs = [tokenize(d) for d in docs]
    bm25 = BM25Okapi(tok_docs) if tok_docs else None
    return tok_docs, bm25


# ---------------- 持久化（2026-08-23 深化）----------------

def _get_persist_root() -> "Path | None":
    """落盘根目录：被覆盖时用之，否则默认 backend/data/bm25_cache；禁用时返回 None。

    测试可 monkeypatch bm25._persist_root 到临时目录，隔离生产缓存目录。
    """
    if not _BM25_PERSIST_ENABLED:
        return None
    if _persist_root is not None:
        return _persist_root
    try:
        from app.config import settings
        return Path(settings.PROJECT_ROOT) / "data" / "bm25_cache"
    except Exception:
        return None


def _tokenizer_fingerprint() -> str:
    """分词参数指纹：对 min_word_len / ngram / 停用词集合做稳定哈希。

    落盘格式自包含：加载时校验指纹与 jieba 版本，不一致（参数被改）则视为缓存失效、回退懒构建。
    """
    payload = json.dumps({
        "min_word_len": _MIN_WORD_LEN,
        "ngram_sizes": list(_NGRAM_SIZES),
        "stopwords": sorted(_STOPWORDS),
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _persist_entry(character_id: int, entry: "_IndexEntry") -> None:
    """角色索引落盘：只写 memory_ids + 已分词文档 + 元信息；异常静默（失败不阻塞主链路）。

    重建 BM25Okapi 只依赖 tok_docs（rank-bm25 的 idf/avgdl 由语料 O(N) 算出，成本极低），
    故落盘省去的是逐篇 jieba 分词（几百 ms 的主要来源）。原子写（临时文件+rename）防损坏。
    """
    try:
        root = _get_persist_root()
        if root is None:
            return
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{character_id}.json"
        data = {
            "version": _INDEX_VERSION,
            "character_id": character_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "jieba_version": getattr(jieba, "__version__", ""),
            "tokenizer": {
                "fingerprint": _tokenizer_fingerprint(),
                "min_word_len": _MIN_WORD_LEN,
                "ngram_sizes": list(_NGRAM_SIZES),
            },
            "memory_ids": list(entry.memory_ids),
            "tok_docs": [list(d) for d in entry.tok_docs],
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        _logger.debug("BM25 persist failed char=%d: %s", character_id, e)


def _remove_persisted(character_id: int) -> None:
    """删除某角色落盘缓存（记忆写入/改内容/删除后调用）；异常静默。"""
    try:
        root = _get_persist_root()
        if root is None:
            return
        path = root / f"{character_id}.json"
        if path.exists():
            path.unlink()
    except Exception as e:
        _logger.debug("BM25 persist remove failed char=%d: %s", character_id, e)


def _load_persisted(character_id: int) -> "_IndexEntry | None":
    """尝试从盘加载角色索引；版本/指纹/jieba 版本不一致、损坏、缺文件等一律静默返回 None。

    返回的 entry.built_at 设为当前单调时钟（视为新构建，走正常 TTL/LRU 生命周期）。
    """
    try:
        if not _BM25_PERSIST_ENABLED:
            return None
        root = _get_persist_root()
        if root is None:
            return None
        path = root / f"{character_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != _INDEX_VERSION:
            return None
        if data.get("jieba_version") != getattr(jieba, "__version__", ""):
            return None
        tok = data.get("tokenizer") or {}
        if tok.get("fingerprint") != _tokenizer_fingerprint():
            return None
        memory_ids = list(data.get("memory_ids") or [])
        tok_docs = [list(d) for d in (data.get("tok_docs") or [])]
        if len(memory_ids) != len(tok_docs):
            return None
        bm25 = BM25Okapi(tok_docs) if tok_docs else None
        return _IndexEntry(memory_ids, tok_docs, bm25, time.monotonic())
    except Exception as e:
        _logger.debug("BM25 persist load failed char=%d: %s", character_id, e)
        return None


async def _build_index(character_id: int) -> "_IndexEntry | None":
    """懒构建：读该角色活跃记忆（is_archived=0 且 delete_at is null）content 建索引。

    DB/分词异常静默返回 None（不影响主链路）。复用 app.memory.service 的 async_session_factory，
    使测试里对 memsvc.async_session_factory 的 monkeypatch 自动生效（隔离临时库）。
    """
    try:
        from app.memory.service import async_session_factory as _factory
        async with _factory() as db:
            rows = (await db.execute(
                select(Memory).where(
                    Memory.character_id == character_id,
                    Memory.is_archived == False,      # noqa: E712
                    Memory.delete_at.is_(None),
                ).order_by(Memory.id.asc())
            )).scalars().all()
        memory_ids: list = []
        docs: list = []
        for m in rows:
            c = (m.content or "").strip()
            if c:
                memory_ids.append(m.id)
                docs.append(c)
        if not memory_ids:
            return _IndexEntry([], [], None, time.monotonic())
        tok_docs, bm25 = await asyncio.to_thread(_build_corpus, docs)
        return _IndexEntry(memory_ids, tok_docs, bm25, time.monotonic())
    except Exception as e:
        _logger.warning("BM25 index build failed char=%d: %s", character_id, e)
        return None


def _get_cached(character_id: int) -> "_IndexEntry | None":
    entry = _cache.get(character_id)
    if entry is None:
        return None
    if time.monotonic() - entry.built_at > _BM25_TTL_SECONDS:
        _cache.pop(character_id, None)   # 惰性过期
        return None
    _cache.move_to_end(character_id)     # LRU 触达
    return entry


def _store_in_cache(character_id: int, entry: "_IndexEntry") -> None:
    """入 LRU 缓存并移到最后，超容量从最久未用开始淘汰。"""
    _cache[character_id] = entry
    _cache.move_to_end(character_id)
    while len(_cache) > _BM25_CACHE_MAX:
        _cache.popitem(last=False)


async def _get_index(character_id: int) -> "_IndexEntry | None":
    entry = _get_cached(character_id)
    if entry is not None:
        return entry
    # 2026-08-23 深化：重启后首次检索先尝试从盘加载（省去重新分词）；失败静默回退懒构建。
    entry = _load_persisted(character_id)
    if entry is not None:
        _store_in_cache(character_id, entry)
        return entry
    entry = await _build_index(character_id)
    if entry is None:
        return None
    _store_in_cache(character_id, entry)
    _persist_entry(character_id, entry)
    return entry


async def search(character_id: int, query: str, top_k: int = 5) -> list:
    """BM25 稀疏检索：返回 [(memory_id, score)]，按分数降序取 top_k。

    - 「命中」= 至少与一个查询词元共享（词元集交集），**不依赖 BM25 分数是否为 0**——
      rank_bm25 的 idf 在小语料上对「出现于恰好一半文档」的词会取 0（log(1)），若仅按
      score!=0 过滤会漏召回；故用词元共享判定命中，再用 BM25 分排序；
    - 排序：命中词元越罕见（idf 越大）分越高，命中词元均为常见词（idf≈0）则并列 0 分，
      此时保留文档原始顺序（后续 _rerank 会按 importance/时效/话题/相关性再排）；
    - 异常静默返回 []（不影响主链路）。
    """
    try:
        entry = await _get_index(character_id)
        if entry is None or entry.bm25 is None:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        q_set = set(q_tokens)
        scores = entry.bm25.get_scores(q_tokens)
        matched: list = []
        for i, (mid, doc_tokens) in enumerate(zip(entry.memory_ids, entry.tok_docs)):
            if q_set.intersection(doc_tokens):
                matched.append((mid, float(scores[i])))
        matched.sort(key=lambda x: x[1], reverse=True)
        return matched[:top_k]
    except Exception as e:
        _logger.warning("BM25 search failed char=%d: %s", character_id, e)
        return []


def invalidate(character_id: int) -> None:
    """使某角色索引失效（记忆写入/改内容/删除后调用；下次检索懒重建），并删除落盘缓存。"""
    _cache.pop(character_id, None)
    _remove_persisted(character_id)


def clear_cache() -> None:
    """清空全部索引缓存（内存 + 落盘；测试/运维用，跨测试复用 character_id 时确保全新）。

    2026-08-23 深化：索引已落盘，故不仅清进程内 _cache，也删除持久化目录下的缓存文件，
    避免跨测试/跨库复用同一 character_id 时读到上一库的旧索引（此前仅清内存会因磁盘残留而污染）。
    生产链路不调用 clear_cache（仅测试/运维），失败静默。
    """
    _cache.clear()
    try:
        root = _get_persist_root()
        if root is not None and root.is_dir():
            for f in root.glob("*.json"):
                try:
                    f.unlink()
                except Exception:
                    pass
    except Exception:
        pass


def cache_size() -> int:
    """观测/测试用：当前缓存角色数。"""
    return len(_cache)
