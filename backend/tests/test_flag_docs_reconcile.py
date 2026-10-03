# -*- coding: utf-8 -*-
"""开关文档对账棘轮（2026-10-03 立，起因＝台账 §二 整节漂了十几天没人发现）。

钉四件事，全部只依赖**代码字典**与**仓内文档**（不碰生产库，测试沙箱里也成立）：

1. `docs/feature-flags.md` §二 表内的键集合与 `AGENT_FLAGS` 字典**双向一致**（漏登＝热切能改但清单看不到；
   多登＝文档写了个不存在的键，下次照它操作会踩空）。
2. §二 每节标题里声明的数量 **等于** 该节实际表内行数（历史上「A 9→10、F 28→29」这类标注数一漂就没人再信）。
3. `docs/plans.md` §二 声明的注册键数／默认开／默认关／非布尔数 **等于** 从字典现算的值。
4. plans §二 的**旧计数只许出现在 `<details>` 留档段里**（正文当现状读的那部分不许留 09-24 的"85 项"这类数）。

生产库侧的数字（67 ON／18 冗余行／16 从未拨过）**这里核不了**——测试跑的是沙箱库，
`runtime_flags` 是空的；那几个数的口径写在 plans §二「口径」行里，靠人工只读复测维护。
"""
from pathlib import Path

import pytest

from app.flags.agent_flags import AGENT_FLAGS

DOCS = Path(__file__).resolve().parents[2] / "docs"
FF = DOCS / "feature-flags.md"
PLANS = DOCS / "plans.md"


def _between(text: str, start: str, end: str) -> str:
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


@pytest.fixture(scope="module")
def ff_sec2():
    if not FF.is_file():
        pytest.skip("脱敏快照无 docs/feature-flags.md（CI）；该棘轮只在内部全仓生效")
    return _between(FF.read_text(encoding="utf-8"), "## 二、", "## 三、")


def _table_rows(block: str):
    """返回 (节标号, 声明数量, [键名…]) 的列表；只认 `### X. 名称（N 个…）` 这类标题。"""
    out = []
    cur = None
    for line in block.split("\n"):
        if line.startswith("### "):
            head = line[4:]
            name = head.split("（")[0].strip()
            paren = head.split("（")[1].split("）")[0] if "（" in head else ""
            num = ""
            for ch in paren:
                if ch.isdigit():
                    num += ch
                elif num:
                    break
            cur = (name, int(num) if num else None, [])
            out.append(cur)
        elif cur is not None and line.startswith("|"):
            cell = line.strip().strip("|").split("|")[0].strip()
            if cell.startswith("`") and cell.endswith("`"):
                cur[2].append(cell[1:-1])
    return out


def test_开关总表键集与字典双向一致(ff_sec2):
    sections = _table_rows(ff_sec2)
    assert sections, "§二 没解析出任何小节标题（文档结构被改，本棘轮会瞎掉——先修解析再改文档）"
    keys = [k for _, _, ks in sections for k in ks]
    assert len(keys) == len(set(keys)), "同一个键在总表里出现多次：%s" % (
        sorted({k for k in keys if keys.count(k) > 1}))
    missing = sorted(set(AGENT_FLAGS) - set(keys))
    extra = sorted(set(keys) - set(AGENT_FLAGS))
    assert not missing, "字典里有、总表漏登：%s（漏这里＝热切能改但清单看不到）" % missing
    assert not extra, "总表里有、字典里已没有：%s（照着它操作会踩空）" % extra
    assert len(keys) == len(AGENT_FLAGS)


def test_各节声明数量等于该节实际行数(ff_sec2):
    bad = []
    for name, declared, keys in _table_rows(ff_sec2):
        if declared is None:
            bad.append((name, "标题里没写数量", len(keys)))
        elif declared != len(keys):
            bad.append((name, declared, len(keys)))
    assert not bad, "§二 节标题的标注数与表内行数对不上：%s" % bad


def test_台账二节声明的字典计数与实测一致():
    if not PLANS.is_file():
        pytest.skip("脱敏快照无 docs/plans.md（CI）")
    plans = PLANS.read_text(encoding="utf-8")
    sec2 = _between(plans, "## 二、", "## 三、")
    line = next(l for l in sec2.split("\n") if l.startswith("| 注册键 |"))
    bools = [v for v in AGENT_FLAGS.values() if isinstance(v, bool)]
    nonbool = len(AGENT_FLAGS) - len(bools)
    want = (len(AGENT_FLAGS), sum(1 for v in bools if v), sum(1 for v in bools if not v), nonbool)
    got = tuple(int(x) for x in __import__("re").findall(r"\*?\*(\d+)\*?\*", line))
    assert len(got) >= 4, "注册键那行解析不出四个数（格式被改）：%s" % line
    assert got[:4] == want, "台账 §二 的字典计数漂了：文档 %s ≠ 实测 注册%d／默认开%d／默认关%d／非布尔%d" % (
        got[:4], want[0], want[1], want[2], want[3])


def test_旧计数只许待在details留档段():
    if not PLANS.is_file():
        pytest.skip("脱敏快照无 docs/plans.md（CI）")
    sec2 = _between(PLANS.read_text(encoding="utf-8"), "## 二、", "## 三、")
    stale = ("85 项", "84 布尔", "48 行", "68 行", "66 overrides", "没有行＝默认关")
    if "<details>" not in sec2:
        assert not [s for s in stale if s in sec2], "留档段被删了但旧计数还留在正文"
        return
    body = sec2[:sec2.index("<details>")]
    hits = [s for s in stale if s in body]
    assert not hits, "正文（当现状读的部分）里还留着旧计数/旧结论：%s——请挪进 <details> 留档段或按实测重写" % hits


def test_台账二节声明了唯一真源与棘轮自身():
    if not PLANS.is_file():
        pytest.skip("脱敏快照无 docs/plans.md（CI）")
    sec2 = _between(PLANS.read_text(encoding="utf-8"), "## 二、", "## 三、")
    head_line = sec2.split("\n")[0]
    import re
    assert re.search(r"唯一真源\s*＝?\s*\[?feature-flags\.md", head_line), (
        "§二 标题必须写明逐键明细的唯一真源＝feature-flags.md（两处各列一遍必漂）")
    assert "feature-flags.md" in sec2, "正文里也要指向 feature-flags.md"
    assert "test_flag_docs_reconcile" in sec2, "§二 必须声明本棘轮的存在，否则后人不知道改文档会让测试红"
