# -*- coding: utf-8 -*-
"""批 7 M2 · 私密边界（B1–B6）只读断言测试。

派单：``output/AMBRACE_批7M2_私密边界断言_派单_转发用_20261001.md``
设计稿：``output/AMBRACE_批7_关系心理其余面_详细设计_v1_20260929.md`` §2.5

**本单硬约束**（派单原文）：
  - 不建内容池、不落任何新表、不改任何行为；
  - 用**假数据注入法**（内存中的私密条目/桩对象），**绝不连生产库**；
  - 只允许新增本文件；不许改 ``backend/app/**``；不许 git 写；不许装依赖；
  - **不允许 xfail / skip**——若某条通道现在确实挡不住私密条目，就在汇报里如实列出
    「当前不成立」的清单与证据行号，交 Codex 决定。

**测试形制**（只读断言）：
  - **源码结构断言**：读 ``backend/app/**`` 的源文件，按设计稿点名的行号切片，断言
    「该处 WHERE / 签名 / 注入点符合设计稿描述」。这些断言**描述当前代码事实**，
    不试图执行任何生产路径、不需要 DB；
  - **内存桩对象断言**：构造 ``Memory`` 实例（``scope="private"``）但不 flush、不 commit、
    不 attach 到任何 session——纯 in-memory Python 对象，验证模型字段与归属键；
  - **函数签名断言**：``inspect.signature`` 检查 ``search_memories`` 等入口不含 scope 参数
    （与 ``docs/memory-scope-review.md`` R5「private 是个无人执行的承诺」一致）。

**为什么不做「私密条目不会出现在通道」的运行时断言**：
  设计稿 §2.5 表格已明写 ``Memory.scope`` **无读方**（R5）、``search_memories`` 签名无 scope
  参数、8 条注入通道 0 个带 scope 条件。若在此构造「插入 scope='private' 的记忆 → 调用检索 →
  断言它没出现」的运行时用例，**当前代码必然失败**（scope 不被执行，记忆会照常出现）。
  派单禁止 xfail，故本单把 B1–B6 落成**源码结构断言**：钉死「当前代码就是这样，
  scope 无执行语义」这一事实，任何未来给这些通道**新增** scope 谓词或**移除**既有谓词的改动
  都会撞红测试，从而强制走一次显式决策（对齐设计稿 §⑨ 问题 5 的三选一处置建议）。
  「私密条目现在确实挡不住」的清单在本文件末尾以 ``test_report_current_gaps`` 用例形式
  显式登记（用例本身通过，内容是清单断言，不是 xfail）。
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
from sqlalchemy import inspect as sa_inspect

from app.models.memory import Memory

# ─────────────────────────────────────────────────────────────────────────────
# 路径与源码读取工具
# ─────────────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "backend" / "app"


def _read(rel: str) -> str:
    """读 ``backend/app/<rel>`` 的源文件（UTF-8）。"""
    return (APP_ROOT / rel).read_text(encoding="utf-8")


def _lines(rel: str) -> list[str]:
    """按行切分源文件（保留原始行号对齐：返回 0-indexed 列表，行号 = idx+1）。"""
    return _read(rel).splitlines()


def _slice(rel: str, start: int, end: int, anchor: str | None = None, pad: int = 0) -> str:
    """取源码片段。给了 ``anchor`` 就**按内容定位**，``start``/``end`` 只当行号提示。

    为什么必须这样：绝对行号是 R4 类隐患，一次拆刀就能挪几百行。A22 ⑤（2026-10-03）实测
    删 legacy.py 351 行后，本文件三处切片集体误红而内容一字未动；更要紧的是 B1/B2 那 13 处
    是**否定式**断言（窗口里不许出现 ``scope`` 谓词）——行号一漂，窗口滑到无关代码上，
    否定断言就**空转通过**（静默假绿，比误红危险得多）。

    口径：全文搜 ``anchor``、取**离提示行最近**的那一处作窗口起点（高度沿用 end-start+1，
    ``pad`` 再上下各扩若干行）——这样既扛得住任意幅度的漂移，又能在文件里有多处相似代码时
    靠行号提示消歧。**找不到直接判失败**：那正是「这段代码被挪走/删掉了」的信号，
    绝不退回"随便取一段继续断言"。不给 anchor 时保持历史行为（纯行号）。
    """
    ls = _lines(rel)
    if anchor is None:
        return "\n".join(ls[max(0, start - 1 - pad):end + pad])
    height = max(1, end - start + 1)
    hits = [i for i, line in enumerate(ls) if anchor in line]
    if hits:
        i = min(hits, key=lambda k: abs(k - (start - 1)))
        return "\n".join(ls[max(0, i - pad):i + height + pad])
    raise AssertionError(
        "锚点消失：%r —— %s 全文已找不到它（行号提示 %d–%d），说明这段代码被挪走或删掉了。"
        "若确属有意改动，请同步更新本用例的 anchor 与行号提示（不要只把行号改了交差）。"
        % (anchor, rel, start, end))


# ─────────────────────────────────────────────────────────────────────────────
# B0 · 前置事实：Memory.scope 列存在且默认 "private"（R5 的字段面）
# ─────────────────────────────────────────────────────────────────────────────

def test_b0_memory_scope_column_exists_with_private_default():
    """``models/memory/__init__.py:37``：``scope`` 列声明存在，默认 ``"private"``。

    这是 B1–B6 所有断言的语义起点——「私密」这个词在 schema 上有承载列，
    但（如 R5 所述）全仓无读方。本用例只钉「列存在 + 默认值」这两个事实。
    """
    src = _slice("models/memory/__init__.py", 30, 45)
    assert "scope" in src, "Memory.scope 列声明消失（设计稿 §2.5 #1 前提被破坏）"
    assert 'default="private"' in src or "default='private'" in src, (
        "Memory.scope 的默认值不再是 'private'（设计稿 §2.5 #1 前提被破坏）"
    )
    # 模型实例层面的双保险：构造一个不入库的 Memory，验证默认值语义
    m = Memory(user_id=1, character_id=2, memory_type="event", content="x")
    # 注意：SQLAlchemy 的 default 在 flush 时才生效，未 flush 的实例 scope 为 None。
    # 这里断言的是「列存在且可赋值」，不是「默认值已生效」——默认值由上面的源码断言覆盖。
    m.scope = "private"
    assert m.scope == "private"


def test_b0_memory_has_dual_ownership_keys_not_null():
    """B6 前置：``Memory`` 表 ``user_id`` 与 ``character_id`` 双 NOT NULL。

    设计稿 §2.5 #10 + ``docs/memory-scope-review.md`` R1/R3/R4：私密条目的归属键必须
    **同时**有 ``character_id`` 与 ``user_id``，缺一即串号（家庭租户下 ``character_id``
    单键不是账号防线）。本用例钉死 schema 层面的双键约束。
    """
    mapper = sa_inspect(Memory)
    cols = {c.name: c for c in mapper.columns}
    assert "user_id" in cols, "Memory.user_id 列消失（B6 双键约束被破坏）"
    assert "character_id" in cols, "Memory.character_id 列消失（B6 双键约束被破坏）"
    assert cols["user_id"].nullable is False, (
        "Memory.user_id 不再是 NOT NULL（B6 双键约束被破坏，"
        "docs/memory-scope-review.md R1/R3/R4 的归属键前提失效）"
    )
    assert cols["character_id"].nullable is False, (
        "Memory.character_id 不再是 NOT NULL（B6 双键约束被破坏）"
    )


# ─────────────────────────────────────────────────────────────────────────────
# B1 · 不进检索：retrieve.py 的七处 WHERE / 入口
# ─────────────────────────────────────────────────────────────────────────────

# 设计稿 §2.5 表格 B1 行钉死的七处（行号 = 2026-09-29 磁盘实况，允许 ±5 行漂移）
B1_RETRIEVE_WHERE_SITES = [
    # (文件, 行号提示起, 行号提示止, 标签, 内容锚点)  —— 行号只是提示，实际按锚点定位
    ("memory/retrieve.py", 157, 166, "邻居", "Memory.created_at >= created - win,"),
    ("memory/retrieve.py", 254, 258, "专名 LIKE", "for s in literals]),"),
    ("memory/retrieve.py", 772, 777, "关键词 LIKE", "# P3-E"),
    ("memory/retrieve.py", 813, 820, "时间路", "Memory.created_at >= t_start,"),
    ("memory/retrieve.py", 636, 640, "向量", "hits = await vector_search("),
    ("memory/retrieve.py", 654, 657, "BM25", "return await bm25_search(character_id, q"),
    ("memory/retrieve.py", 338, 342, "回填", 'Memory.id.in_([r["id"] for r in results]),'),
]


@pytest.mark.parametrize("rel,start,end,label,anchor", B1_RETRIEVE_WHERE_SITES)
def test_b1_retrieve_where_sites_have_no_scope_predicate(rel, start, end, label, anchor):
    """B1：``retrieve.py`` 七处 WHERE / 入口**当前**不含 ``Memory.scope`` 谓词。

    这是「scope 无执行语义」（R5）的**源码事实**。本断言**描述当前代码**，
    不试图执行任何检索。任何给这七处**新增** scope 谓词的改动都会撞红——
    那是设计稿 §⑨ 问题 5 的「补执行」分支，需要走一次显式决策（另开单），
    不允许静默改。

    设计稿 §2.5 B1 行原文：「若复用 ``Memory`` 表则**必须写进既有 WHERE**
    （七处一处不能漏）」——本用例钉的是「现在一处都没写」这个事实。
    """
    snippet = _slice(rel, start, end, anchor)
    # 扩窗也按同一锚点定位（±5 行）：行号漂时两个窗口不会各自滑到别处
    widened = _slice(rel, start, end, anchor, pad=5)
    assert "Memory.scope" not in snippet and ".scope" not in snippet, (
        f"B1 通道「{label}」（{rel}:{start}-{end}）出现了 scope 谓词——"
        "这是「补执行」分支的改动，需走设计稿 §⑨ 问题 5 的显式决策，不允许静默改。"
    )
    # 扩窗断言：即使行号漂了 ±5，scope 也不应出现在附近
    assert "Memory.scope" not in widened, (
        f"B1 通道「{label}」附近（{rel}:{start-5}-{end+5}）出现了 Memory.scope——"
        "同上，需走显式决策。"
    )


def test_b1_search_memories_signature_has_no_scope_parameter():
    """B1：``search_memories`` 入口签名不含 ``scope`` 参数。

    设计稿 §2.5 #3 行：``memory/retrieve.py:580-591 search_memories`` 签名
    （参数：character_id/query/limit/queries/time_range/scene/exclude_sources/group_id，
    **无 scope、无 user_id 谓词**）。本断言钉死这个签名事实。
    """
    from app.memory.retrieve import search_memories
    sig = inspect.signature(search_memories)
    assert "scope" not in sig.parameters, (
        "search_memories 签名出现了 scope 参数——这是「补执行」分支的改动，"
        "需走设计稿 §⑨ 问题 5 的显式决策。"
    )
    # 设计稿点名的既有参数必须还在（防止有人借机重构签名）
    for expected in ("character_id", "query", "limit"):
        assert expected in sig.parameters, (
            f"search_memories 签名缺少既有参数 {expected}（设计稿 §2.5 #3 前提被破坏）"
        )


# ─────────────────────────────────────────────────────────────────────────────
# B2 · 不进二次加工：summary.py / extractor.py / core.py 取料
# ─────────────────────────────────────────────────────────────────────────────

B2_SECONDARY_SITES = [
    ("memory/summary.py", 96, 103, "摘要取料", "被隔离条不进摘要原料"),
    ("memory/summary.py", 196, 206, "身份画像取料", "身份画像取料同样排除被隔离条"),
    ("memory/extractor.py", 413, 413, "提取器写记忆 1", 'source="chat",sub_type=slot,source_id=source_id'),
    ("memory/extractor.py", 417, 417, "提取器写记忆 2", 'sub_type=("meta_guard" if _meta_downgrade'),
    ("memory/extractor.py", 450, 450, "提取器写记忆 3", 'content="关系: "+rel[:100]'),
    ("memory/core.py", 242, 247, "核心晋升取料", "Memory.is_core == True,"),
]


@pytest.mark.parametrize("rel,start,end,label,anchor", B2_SECONDARY_SITES)
def test_b2_secondary_processing_sites_have_no_scope_predicate(rel, start, end, label, anchor):
    """B2：摘要 / 身份画像 / 提取器 / 核心晋升的取料处**当前**不含 ``scope`` 谓词。

    设计稿 §2.5 B2 行原文：「摘要 ``summary.py:96-103``、身份画像 ``:196-206``、
    画像提取 ``extractor.py:413,417,450``、核心晋升 ``core.py:242-247`` 四处取料
    **全部排除**私密条目（否则会被写进对 AI 长期可见的置顶/画像行）」。

    当前事实：这四处**都没有** scope 谓词（R5 的二次加工面）。本断言钉死这个事实。
    """
    snippet = _slice(rel, start, end, anchor)
    # 扩窗同样按锚点定位，避免行号漂时窗口落到无关代码上（否定式断言会空转通过）
    widened = _slice(rel, start, end, anchor, pad=5)
    # extractor.py 的三处是 save_memory 调用，不是 WHERE——断言它们不传 scope 参数
    if "extractor" in rel:
        assert "scope=" not in snippet, (
            f"B2 通道「{label}」（{rel}:{start}-{end}）出现了 scope= 传参——"
            "这是「补执行」分支的改动，需走显式决策。"
        )
    else:
        assert "Memory.scope" not in snippet and ".scope" not in snippet, (
            f"B2 通道「{label}」（{rel}:{start}-{end}）出现了 scope 谓词——"
            "这是「补执行」分支的改动，需走显式决策。"
        )
        assert "Memory.scope" not in widened, (
            f"B2 通道「{label}」附近（{rel}:{start-5}-{end+5}）出现了 Memory.scope——同上。"
        )


# ─────────────────────────────────────────────────────────────────────────────
# B3 · 不进用户可见面：maintain.py 用户列表 / api/diary.py / api/phone*.py
# ─────────────────────────────────────────────────────────────────────────────

def test_b3_maintain_list_memories_has_no_scope_predicate():
    """B3：``memory/maintain.py:46-59 list_memories`` WHERE 不含 ``scope`` 谓词。

    设计稿 §2.5 #6 行：「用户侧列表不排除 private：``memory/maintain.py:46-59 list_memories``
    WHERE＝``is_archived + status + memory_type!="working_state" + 租户(user_ids) + character_id``」。
    当前事实：WHERE 里**没有** scope（「私密」条目照常出现在用户可见列表）。
    """
    snippet = _slice("memory/maintain.py", 40, 65)
    assert "Memory.scope" not in snippet and ".scope" not in snippet, (
        "B3 通道「用户列表」（memory/maintain.py:46-59）出现了 scope 谓词——"
        "这是「补执行」分支的改动，需走显式决策。"
    )
    # 设计稿点名的既有谓词必须还在（防止有人借机重构）
    assert "is_archived" in snippet, "list_memories 的 is_archived 谓词消失（设计稿前提被破坏）"
    assert "working_state" in snippet, "list_memories 的 working_state 排除消失（设计稿前提被破坏）"


def test_b3_diary_api_does_not_filter_by_memory_scope():
    """B3：``api/diary.py:35-53`` 读面不含 ``Memory.scope`` 谓词。

    设计稿 §2.5 B3 行：「API 读面（日记 ``api/diary.py:35-53``、手机 ``api/phone*.py``）
    不含私密条目」。当前事实：日记读面按 ``AIDiary.character_id`` 过滤，
    与 ``Memory`` 表无关（日记是独立表），更无 scope 谓词。
    """
    snippet = _slice("api/diary.py", 30, 60)
    assert "Memory.scope" not in snippet, (
        "B3 通道「日记 API」（api/diary.py:35-53）出现了 Memory.scope——"
        "日记表与 Memory 表无关，这是误接，需走显式决策。"
    )
    # 日记读面按 AIDiary.character_id 过滤（设计稿 §2.5 #8 行）
    assert "AIDiary.character_id" in snippet or "character_id" in snippet, (
        "日记 API 的 character_id 过滤消失（设计稿 §2.5 #8 前提被破坏）"
    )


@pytest.mark.parametrize("phone_file", ["api/phone.py", "api/phone_desktop.py", "api/phone_workflows.py"])
def test_b3_phone_apis_do_not_filter_by_memory_scope(phone_file):
    """B3：``api/phone*.py`` 三个读面不含 ``Memory.scope`` 谓词。

    设计稿 §2.5 B3 行：「API 读面（日记、手机 ``api/phone*.py``）不含私密条目」。
    当前事实：手机 API 读的是 ``PhoneSnapshot`` / ``PhoneDesktop`` 等独立表，
    与 ``Memory`` 表无关，更无 scope 谓词。
    """
    src = _read(phone_file)
    assert "Memory.scope" not in src, (
        f"B3 通道「手机 API」（{phone_file}）出现了 Memory.scope——"
        "手机 API 读的是独立表，这是误接，需走显式决策。"
    )


# ─────────────────────────────────────────────────────────────────────────────
# B4 · 唯一白名单注入口：section_memories.py / legacy.py
# ─────────────────────────────────────────────────────────────────────────────

def test_b4_section_memories_registration_point_exists():
    """B4：``agent/context/section_memories.py:283`` 是 ``memories`` 分区的注册点。

    设计稿 §2.5 #4 行：「捞 Memory 的注入通道（全部）：``agent/context/section_memories.py:283``
    （读 ``state["retrieved_memories"]``，源 ``:198``）」。本断言钉死这个注册点存在。
    """
    snippet = _slice("agent/context/section_memories.py", 275, 295)
    assert "register_section" in snippet, (
        "section_memories.py:283 附近的 register_section 调用消失（设计稿 §2.5 #4 前提被破坏）"
    )
    assert 'key="memories"' in snippet or "key='memories'" in snippet, (
        "memories 分区的注册 key 消失（设计稿 §2.5 #4 前提被破坏）"
    )


def test_b4_legacy_fallback_points_exist():
    """B4：``agent/context/legacy.py`` 的三处兜底注入点存在（:286 / :858-876 / :899）。

    设计稿 §2.5 #4 行：「legacy 内联兜底 ``agent/context/legacy.py:286``（retrieved）、
    ``:858-876``（life）、``:899``（shared_events）」。本断言钉死这三处存在。
    """
    # :286 附近——retrieved_memories 兜底
    s1 = _slice("agent/context/legacy.py", 280, 295,
                anchor="memory_lines = _build_retrieved_memory_lines(")
    assert "retrieved_memories" in s1 or "_build_retrieved_memory_lines" in s1, (
        "legacy.py:286 附近的 retrieved_memories 兜底消失（设计稿 §2.5 #4 前提被破坏）"
    )
    # :858-876 附近——life 注入
    s2 = _slice("agent/context/legacy.py", 850, 885, anchor="if _share and _trust >= 60:")
    assert "life" in s2.lower() or "source == \"life\"" in s2 or "source=='life'" in s2, (
        "legacy.py:858-876 附近的 life 注入消失（设计稿 §2.5 #4 前提被破坏）"
    )
    # :899 附近——shared_events 注入
    s3 = _slice("agent/context/legacy.py", 893, 910,
                anchor="recall_text as _shared_recall")
    assert "shared" in s3.lower() or "recall_text" in s3, (
        "legacy.py:899 附近的 shared_events 注入消失（设计稿 §2.5 #4 前提被破坏）"
    )


def test_b4_whitelist_outside_zero_injection_grep():
    """B4：白名单外零注入——全仓 ``agent/context/`` 下只有设计稿点名的文件读 ``retrieved_memories``。

    设计稿 §2.5 B4 行：「AI 自己要读时，经**唯一一个** append 型 section 注入；
    legacy 兜底路径同步（``legacy.py:286 / :858-876 / :899``）；**断言「白名单外零注入」**」。

    设计稿 §2.5 #4 行钉死的**全部** 8 条注入通道（白名单）：
      - ``section_memories.py:283``（读 ``state["retrieved_memories"]``）
      - ``section_core.py`` → ``memory/core.py:235 get_core_memories`` / ``:255 get_relationship_anchors``
      - ``section_overlay.py:544 life_share`` → select ``:152-159``（仅 ``source=="life"``）
      - ``section_working_state.py:148``
      - ``legacy.py:286``（retrieved）/ ``:858-876``（life）/ ``:899``（shared_events）

    本断言用 grep 形制：扫 ``agent/context/`` 下所有 .py，统计读 ``retrieved_memories``
    或 ``Memory`` 表的文件，断言只有白名单内的文件命中。
    """
    ctx_dir = APP_ROOT / "agent" / "context"
    # 设计稿 §2.5 #4 行钉死的全部注入通道（白名单）
    whitelist = {
        "section_memories.py",
        "section_core.py",
        "section_overlay.py",
        "section_working_state.py",
        "legacy.py",
    }
    hits: set[str] = set()
    for py in ctx_dir.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        # 读 retrieved_memories 或 from app.models.memory import Memory 的文件
        if "retrieved_memories" in src or re.search(r"from app\.models\.memory import.*Memory", src):
            hits.add(py.name)
    outside = hits - whitelist
    assert not outside, (
        f"B4 白名单外出现了 Memory 注入点：{sorted(outside)}——"
        "设计稿 §2.5 B4 行要求「白名单外零注入」，新增注入点需走显式决策。"
    )


# ─────────────────────────────────────────────────────────────────────────────
# B5 · 用户侧上锁不复用：api/privacy.py 只 gate 人读接口
# ─────────────────────────────────────────────────────────────────────────────

def test_b5_privacy_endpoints_gate_user_reads_only():
    """B5：``api/privacy.py:351 / :384`` 两个端点只 gate 用户读接口，AI 侧不受影响。

    设计稿 §2.5 #9 行：「用户侧上锁（gate 人、不 gate AI）：``api/privacy.py:351 GET status`` /
    ``:384 POST request``；**只 gate 用户读接口，AI 侧注入不受影响**（``agent/`` 全域无该 gate 的读点）」。

    本断言分两步：
      ① privacy.py 的两个端点存在（:351 GET status / :384 POST request）；
      ② ``agent/`` 全域不读 privacy 的 gate（grep ``privacy_lock`` / ``PrivacyRequest`` 零命中）。
    """
    src = _read("api/privacy.py")
    # ① 两个端点存在
    assert "@router.get" in src and "status" in src, (
        "api/privacy.py 的 GET status 端点消失（设计稿 §2.5 #9 前提被破坏）"
    )
    assert "@router.post" in src and "request" in src, (
        "api/privacy.py 的 POST request 端点消失（设计稿 §2.5 #9 前提被破坏）"
    )
    # ② agent/ 全域不读 privacy gate
    agent_dir = APP_ROOT / "agent"
    privacy_gate_refs: list[str] = []
    for py in agent_dir.rglob("*.py"):
        content = py.read_text(encoding="utf-8")
        if "privacy_lock" in content or "PrivacyRequest" in content:
            privacy_gate_refs.append(str(py.relative_to(APP_ROOT)))
    assert not privacy_gate_refs, (
        f"B5 违规：agent/ 全域出现了 privacy gate 的读点：{privacy_gate_refs}——"
        "设计稿 §2.5 #9 行明写「AI 侧注入不受影响」，新增读点需走显式决策。"
    )


def test_b5_privacy_lock_auto_unlock_threshold_exists():
    """B5 补充：``api/privacy.py:37 TRUST_OPEN_THRESHOLD=80`` + ``:417-437`` 自动关锁逻辑存在。

    设计稿 §2.5 B5 行：「注意 ``:37 TRUST_OPEN_THRESHOLD=80`` + ``:417-437`` 会自动关锁，
    把它当私密保证会失效」。本断言钉死这个「会自动关锁」的事实——防止有人把 privacy gate
    误当私密机制复用（B5 的核心禁令）。
    """
    src = _read("api/privacy.py")
    assert "TRUST_OPEN_THRESHOLD" in src, (
        "api/privacy.py 的 TRUST_OPEN_THRESHOLD 常量消失（设计稿 §2.5 B5 行前提被破坏）"
    )
    # 自动关锁逻辑（信任≥80 自动同意）
    assert re.search(r"TRUST_OPEN_THRESHOLD\s*=\s*80", src), (
        "TRUST_OPEN_THRESHOLD 的值不再是 80（设计稿 §2.5 B5 行前提被破坏）"
    )


# ─────────────────────────────────────────────────────────────────────────────
# B6 · 账号维度：tenant_service.py 家庭租户口径
# ─────────────────────────────────────────────────────────────────────────────

def test_b6_tenant_service_family_mode_documented():
    """B6：``application/tenant_service.py:86 / :126-133`` 家庭租户口径存在。

    设计稿 §2.5 #10 行 + ``docs/memory-scope-review.md`` R1/R3/R4：
    「家庭租户下 ``character_id`` 单键不是账号防线（``application/tenant_service.py:86,126-133``），
    而 curated / 身份画像 / AI 社交近况三条读路径缺 ``user_id`` 谓词 ⇒ 私密条目的归属键必须
    **同时**写 ``character_id`` 与 ``user_id``，缺一即串号」。

    本断言钉死 tenant_service 的两个关键函数存在：
      - ``tenant_scope_ids``（:86）：列表/范围查询的账号白名单；
      - ``tenant_character_ids``（:126-133）：本租户可见的角色 id 列表。
    """
    src = _read("application/tenant_service.py")
    assert "def tenant_scope_ids" in src, (
        "tenant_service.py 的 tenant_scope_ids 函数消失（设计稿 §2.5 #10 前提被破坏）"
    )
    assert "def tenant_character_ids" in src, (
        "tenant_service.py 的 tenant_character_ids 函数消失（设计稿 §2.5 #10 前提被破坏）"
    )
    # family 模式的关键字
    assert "family" in src.lower() or "TENANT_KEY_MODE_FAMILY" in src, (
        "tenant_service.py 的 family 模式关键字消失（设计稿 §2.5 #10 前提被破坏）"
    )


def test_b6_memory_instance_requires_both_keys():
    """B6 内存桩：构造 ``Memory`` 实例时，``user_id`` 与 ``character_id`` 必须同时提供。

    这是 B6 的「假数据注入法」面：不入库、不 flush，纯 in-memory Python 对象，
    验证模型层面的双键约束（与 ``test_b0_memory_has_dual_ownership_keys_not_null`` 的
    schema 断言互补）。
    """
    # 双键齐全——构造成功
    m_ok = Memory(user_id=1, character_id=2, memory_type="event", content="x", scope="private")
    assert m_ok.user_id == 1 and m_ok.character_id == 2 and m_ok.scope == "private"
    # 缺 user_id——SQLAlchemy 不拦（NOT NULL 是 DB 层约束），但语义上违规。
    # 这里断言的是「构造出来的对象 user_id 为 None」，即「缺键的对象可以被构造出来，
    # 但入库时会被 DB 拦」——这是 B6 双键纪律的内存面证据。
    m_missing = Memory(character_id=2, memory_type="event", content="x")
    assert m_missing.user_id is None, (
        "Memory 构造时 user_id 的缺省行为变化（B6 双键纪律的内存面前提被破坏）"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 汇总登记：「当前确实挡不住」的清单（派单要求③）
# ─────────────────────────────────────────────────────────────────────────────

def test_report_current_gaps():
    """登记「私密条目当前确实挡不住」的清单（派单汇报格式③）。

    本用例**本身通过**——它不是 xfail，而是把设计稿 §2.5 + ``docs/memory-scope-review.md``
    R5 的结论以断言形式显式登记：``Memory.scope`` 列存在但**全仓无读方**，
    B1–B6 的所有通道**当前都不执行 scope 过滤**。

    证据行号（与 ``docs/memory-scope-review.md`` R5 一致）：
      - ``models/memory/__init__.py:37``：scope 列声明（default="private"）；
      - ``memory/write.py:468``：写侧默认参数 scope="private"；
      - ``memory/retrieve.py:580-591``：search_memories 签名无 scope 参数；
      - ``memory/retrieve.py`` 七处 WHERE（:157-166 / :254-258 / :772-777 / :813-820 /
        :636-640 / :654-657 / :338-342）：0 个带 scope 条件；
      - ``memory/summary.py:96-103 / :196-206``、``memory/extractor.py:413,417,450``、
        ``memory/core.py:242-247``：0 个带 scope 条件；
      - ``memory/maintain.py:46-59``、``api/diary.py:35-53``、``api/phone*.py``：0 个带 scope 条件；
      - ``agent/context/section_memories.py:283``、``agent/context/legacy.py:286 / :858-876 / :899``：
        0 个带 scope 条件。

    结论：**B1–B6 当前全部不成立**（scope 无执行语义）。处置建议见设计稿 §⑨ 问题 5
    的三选一（删列 / 补执行 / 文档改口径），本单不自行动作。
    """
    # 全仓 grep：Memory.scope 的读方（非写方）零命中
    # 写方：models/memory/__init__.py:37（列声明）、memory/write.py（默认参数 + 落列）、
    #       application/working_state_service.py:173、life/preoccupations.py:100
    # 读方：全仓零命中（R5 的核心事实）
    write_side_files = {
        "models/memory/__init__.py",
        "memory/write.py",
        "application/working_state_service.py",
        "life/preoccupations.py",
    }
    scope_read_hits: list[str] = []
    for py in APP_ROOT.rglob("*.py"):
        rel = str(py.relative_to(APP_ROOT)).replace("\\", "/")
        if rel in write_side_files:
            continue
        content = py.read_text(encoding="utf-8")
        # 读方形制：**只匹配 Memory.scope**（不匹配 ToolPermission.scope 等其他表的 scope 列）。
        # R5 的核心事实是「Memory.scope 无读方」，不是「全仓无 scope 读方」。
        if re.search(r"Memory\.scope\s*[=!<>]", content):
            scope_read_hits.append(rel)
    assert not scope_read_hits, (
        f"Memory.scope 出现了读方：{scope_read_hits}——"
        "这与 docs/memory-scope-review.md R5「private 是个无人执行的承诺」矛盾，"
        "若属实则 B1–B6 的「当前不成立」清单需要重估。"
    )
    # 显式登记：B1–B6 当前全部不成立（scope 无执行语义）
    # 这不是 xfail，而是「清单断言」——用例本身通过，内容是「当前 gap 的显式登记」。
    current_gaps = {
        "B1": "retrieve.py 七处 WHERE 0 个带 scope 条件（:157-166 / :254-258 / :772-777 / :813-820 / :636-640 / :654-657 / :338-342）",
        "B2": "summary.py:96-103 / :196-206、extractor.py:413,417,450、core.py:242-247 0 个带 scope 条件",
        "B3": "maintain.py:46-59、api/diary.py:35-53、api/phone*.py 0 个带 scope 条件",
        "B4": "section_memories.py:283、legacy.py:286 / :858-876 / :899 0 个带 scope 条件",
        "B5": "privacy.py:351 / :384 只 gate 人读接口，AI 侧不受影响（不是私密机制）",
        "B6": "tenant_service.py:86 / :126-133 家庭租户口径存在，但 Memory.scope 无双键执行语义",
    }
    # 断言清单非空（即「当前确实有 gap」）——这是派单要求③的显式登记
    assert current_gaps, "B1–B6 的当前 gap 清单为空（与 R5 矛盾）"
    # 断言每条 gap 都有证据行号（防止清单退化成空话）
    for k, v in current_gaps.items():
        assert ":" in v or "行号" in v or ".py" in v, (
            f"B1–B6 gap 清单的 {k} 条缺少证据行号（派单要求③：逐条给 file:line 证据）"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 元守卫 · 本文件自己的取窗机制不许退回去（A22 ⑤ 之后的加固）
# ─────────────────────────────────────────────────────────────────────────────

def test_slice_anchors_are_mandatory_and_self_verifying():
    """B1/B2 每一处都必须带内容锚点；锚点消失必须响亮失败。

    为什么钉这条：这两批是**否定式**断言（窗口里不许出现 scope 谓词）。纯行号取窗时，
    一次拆刀把行号挪掉，窗口就落到无关代码上——否定断言**空转通过**（实测：提示行漂 +350
    后窗口里连那条 WHERE 判据都没有了，用例仍绿）。静默假绿比误红危险，所以从今往后
    这两批的站点必须声明 anchor，且 `_slice` 找不到 anchor 时要报错而不是随便取一段。
    """
    assert len(B1_RETRIEVE_WHERE_SITES) == 7 and len(B2_SECONDARY_SITES) == 6
    for row in B1_RETRIEVE_WHERE_SITES + B2_SECONDARY_SITES:
        rel, start, end, label, anchor = row
        assert anchor and anchor.strip(), "%s/%s 缺内容锚点（不许退回纯行号取窗）" % (rel, label)
        snippet = _slice(rel, start, end, anchor)
        assert anchor in snippet, "%s/%s 的锚点没落在自己取出的窗口里" % (rel, label)
    with pytest.raises(AssertionError, match="锚点消失"):
        _slice("memory/retrieve.py", 157, 166, "这段代码不可能存在_zzz(")
