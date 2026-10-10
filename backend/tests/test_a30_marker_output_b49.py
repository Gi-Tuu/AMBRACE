# -*- coding: utf-8 -*-
"""A30 批 3（2026-10-10 批 49）守卫：标记区的「产出义务」＋「重复性时间约定走 [CAL_NOTE]」。

钉三件事（对应批 48 那两轮 30 次计费的归因）：
① **产出义务**——只在正文里说「好，记下了」而不带标记＝这件事在系统里没发生。
   来历＝两轮 20 条 E3 里 **17 条是「整条标记没产出」**，唯一存了全文的首跑通篇是
   `【策略：简短回应】`＋口语确认（"行，给你空着""知道啦，早更新过了"）＝模型认定自己已经记住了，
   于是不再产出机器可读标记。动的是提示词，不是解析器。
② **重复性的时间约定归 [CAL_NOTE]**——来历＝j1t03 那类「每逢某几天固定不安排某事」被模型
   当长期事实写进【记忆：】，判据要 CAL_NOTE；3 条「只走了另一条通道」里最典型的一条。
   这不是模型错，是提示词没区分"可复用的时间事实"与"要上日历的那一条"。
③ **只加不改**——两句新约束都不移动批 2 钉过的任何一条（形状段、「不写日期」作用域、
   其它标记的日期/时长要求），也不许把评测题的词写进提示词。

判据抽成纯函数 `check_marker_zone()` ＋合成用例（撤句／写两份／挪位／CAL_NOTE 少一截，
四种都得被说出来）——与批 47 棘轮同一刀法：只断言"眼下是对的"不算有牙。
"""
from pathlib import Path

import pytest

from app.agent.context_builder import SYSTEM_PROMPT_TEMPLATE

REPO = Path(__file__).resolve().parents[2]
CONTEXT_BUILDER = REPO / "backend" / "app" / "agent" / "context_builder.py"

DUTY_MARK = "不是写给用户看的句子"            # ① 的指纹短语
DUTY_CONSEQ = "等于这件事在系统里没发生"      # ① 的后果句：没有代价的约束等于没约束
CAL_RECUR = "重复发生的时间约定"              # ② 的指纹短语
CAL_CONSEQ = "系统不会上日历"                  # ② 的后果句
_FIRST_MARKER = "【记忆：内容】"               # ①必须排在这一条之前（总原则先于具体形状）

# 评测题专有名词（与 `test_a30_write_shape_b2.py` 同一批词，另加"带课"：
# 第一版草稿把 j1t03 的场景原句写进了提示词＝为过题污染提示词，这条正向锚就是把那次自我纠正钉住）
_CASE_WORDS = ("朵朵", "煤球", "城西", "城北", "拆迁", "画室", "接送牌", "橘猫",
               "疫苗", "忌口", "无辣不欢", "第三针", "带课", "阿柯", "表弟")


def marker_zone(template: str) -> str:
    """取「## 输出标记」到「## 当前世界状态」之间那一段（与批 2 同一口径，不另起一份边界定义）。"""
    return template.split("## 输出标记", 1)[1].split("## 当前世界状态", 1)[0]


def check_marker_zone(zone: str) -> list:
    """返回违规描述列表（空＝两处约束都到位）。纯函数，可喂合成样本。"""
    problems = []
    if zone.count(DUTY_MARK) != 1 or zone.count(DUTY_CONSEQ) != 1:
        problems.append("产出义务那条缺席或写了两份（写两份＝迟早两边各改一半）")
    lines = [ln for ln in zone.splitlines() if ln.strip()]
    i_duty = next((i for i, ln in enumerate(lines) if DUTY_MARK in ln), None)
    i_marker = next((i for i, ln in enumerate(lines) if ln.startswith(_FIRST_MARKER)), None)
    if i_duty is None or i_marker is None or i_duty > i_marker:
        problems.append("产出义务那条没排在所有标记条目之前（挪进某一条内部＝只约束那一条）")
    cal = next((ln for ln in lines if ln.startswith("[CAL_NOTE]")), "")
    if CAL_RECUR not in cal or CAL_CONSEQ not in cal:
        problems.append("[CAL_NOTE] 没收编重复性的时间约定（模型只会把这类当长期事实写进【记忆：】）")
    if "日期可省" not in cal:
        problems.append("[CAL_NOTE] 原有的日期要求被改掉或删除了")
    if zone.count("不写日期") != 1:
        problems.append("「不写日期」在标记区不止一份＝批 2 钉过的作用域又糊了")
    return problems


@pytest.fixture()
def zone() -> str:
    return marker_zone(SYSTEM_PROMPT_TEMPLATE)


def test_标记区两处新增都到位(zone):
    assert check_marker_zone(zone) == [], check_marker_zone(zone)


def test_产出义务那条只住在context_builder里():
    """全后端只许一份（别处再写一份分叉＝当场红），与批 2 的 `_SHAPE_MARK` 同一口径。"""
    hits = [p for p in (REPO / "backend" / "app").rglob("*.py")
            if DUTY_MARK in p.read_text(encoding="utf-8", errors="replace")]
    assert hits == [CONTEXT_BUILDER], "产出义务出现在多处：%s" % [str(p) for p in hits]


def test_新增的两句不含评测题词():
    """零泄题（方案 §六-7）在**本批新增内容**上的正向锚：只许写通用形状规则。"""
    new_lines = [ln for ln in marker_zone(SYSTEM_PROMPT_TEMPLATE).splitlines()
                 if DUTY_MARK in ln or CAL_RECUR in ln]
    assert len(new_lines) == 2, "两句都在？实际 %d 句" % len(new_lines)
    hit = [w for w in _CASE_WORDS for ln in new_lines if w in ln]
    assert not hit, "提示词写了评测题的场景＝为过题污染提示词：%s" % hit


def _drop_duty(z):
    return "\n".join(ln for ln in z.splitlines() if DUTY_MARK not in ln)


def _double_duty(z):
    line = next(ln for ln in z.splitlines() if DUTY_MARK in ln)
    return z.replace(line, line + "\n" + line, 1)


def _move_after_marker(z):
    line = next(ln for ln in z.splitlines() if DUTY_MARK in ln)
    marker = next(ln for ln in z.splitlines() if ln.startswith(_FIRST_MARKER))
    return z.replace(line + "\n", "", 1).replace(marker, marker + "\n" + line, 1)


def _strip_cal_clause(z):
    return "\n".join(ln.split("；" + CAL_RECUR)[0] if ln.startswith("[CAL_NOTE]") else ln
                     for ln in z.splitlines())


@pytest.mark.parametrize("mutate,want", [
    (_drop_duty, "产出义务那条缺席或写了两份"),
    (_double_duty, "产出义务那条缺席或写了两份"),
    (_move_after_marker, "产出义务那条没排在所有标记条目之前"),
    (_strip_cal_clause, "[CAL_NOTE] 没收编重复性的时间约定"),
], ids=["撤掉产出义务", "产出义务写两份", "产出义务挪到标记条目之后", "CAL_NOTE少重复性那一截"])
def test_四种破坏都得被说出来(zone, mutate, want):
    """合成用例＝这条判据真的有牙（只拿真模板断言，改坏了也照样绿的那类守卫在这里还债）。"""
    got = check_marker_zone(mutate(zone))
    assert any(want in p for p in got), "%s 之后判据说『没问题』：%s" % (want, got)


def test_两条新约束彼此独立(zone):
    """撤一条不许连累另一条：连累了＝两句其实写进了同一句，将来没法单独回滚。"""
    cal_only = check_marker_zone(_strip_cal_clause(zone))
    assert any("CAL_NOTE" in p for p in cal_only), cal_only
    assert all("产出义务" not in p for p in cal_only), "只撤 CAL_NOTE 那一截却把产出义务也报了"
    duty_only = check_marker_zone(_drop_duty(zone))
    assert any("产出义务" in p for p in duty_only), duty_only
    assert all("CAL_NOTE" not in p for p in duty_only), "只撤产出义务却连累了 CAL_NOTE 那条"
