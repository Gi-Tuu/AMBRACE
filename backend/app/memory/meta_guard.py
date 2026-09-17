# -*- coding: utf-8 -*-
"""元对话 / 情绪宣泄守卫 + title 证据锚点（记忆时态缺陷族·第二批任务1，2026-09-17）。

现象（生产库实证）：一次性琐事与对 AI 的情绪宣泄被提成 ``user_info`` → 兜底 ``enduring``，
长期随画像注入。典型：

- id=9814（char13 sam）：``memory_type=user_info``、title「用户的名字」，内容却是用户吐槽
  「AI 记忆回退」的一整句情绪宣泄（含「粥好了快来吃…我是个失败的爱人」）——根因是
  ``response_parser._INFO_PATTERNS`` 的「我是…」正则把「我是个失败的爱人」抓成「用户的名字」。

本模块只做**纯规则**判定（零 LLM、零 DB、失败不抛错），供两条写入管线共用：

- ``app/agent/response_parser.py`` 的正则提取路径（title 证据锚点 + 元对话降级）；
- ``app/memory/extractor.py`` 的 LLM 提取路径（元对话降级 episodic）。

设计口径（交接红线：误伤面要小）：

- 「命中元对话词」**不是**拦截条件，必须**同时**「无真实用户事实锚点」才降级；
  正常聊天里出现「记忆」「模型」等词很常见，有事实锚点的句子一律放行。
- title 证据锚点「宁紧勿松」：只有确实像人名 / 含职业语义词才保留专用 title，
  否则泛化为「一段对话」并归 episodic（由调用方把 memory_type 降为 ``event``）。
"""
from __future__ import annotations

import re

# ── 元对话 / 情绪宣泄词（交接文件给定口径，逐字保留）────────────────────────
# 讨论 AI 本身 / 系统 / 记忆机制 / 报错 / 气话 —— 不是用户事实，不写 user_info/enduring。
_META_ABOUT_AI = (
    "记忆", "回退", "忘了", "你记得", "你是ai", "你是AI", "报错", "bug", "日志",
    "上下文", "提示词", "模型", "失败的爱人", "粥好了", "重置", "串台", "窜",
)

# ── 真实用户事实锚点（人名/职业/位置/稳定偏好/身份）────────────────────────
# 命中任意一类即认为「这条内容里有可留存的用户事实」，元对话守卫放行（防误伤）。
_SURNAMES = set(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄"
    "和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁"
    "杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍"
    "虞万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚"
    "程嵇邢滑裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓"
    "牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙"
    "叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双闻"
    "莘党翟谭贡劳逄姬申扶堵冉宰郦雍郤璩桑桂濮牛寿通边扈燕冀郏浦尚农温"
    "别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡"
    "国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾"
    "毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
)
# 非人名的常见「我是…」宾语（代词/身份词/形容词），首字恰好是姓氏时防误判
_NON_NAME_WORDS = {
    "个人", "个失败", "个人类", "个月", "个学生", "个老师", "个傻子", "个笨蛋",
    "不是", "不太", "有点", "真的", "一个", "这种", "那种", "这样", "那样",
    "谁", "什么", "怎么", "为什么", "好人", "坏人", "男人", "女人", "大人", "小孩",
}
_JOBS = (
    "学生", "老师", "教师", "教授", "研究生", "大学生", "高中生", "初中生",
    "工程师", "程序员", "设计师", "医生", "护士", "律师", "会计", "公务员",
    "经理", "销售", "运营", "产品经理", "司机", "厨师", "警察", "军人",
    "作家", "记者", "编辑", "翻译", "客服", "服务员", "工人", "农民",
)
# 学业 / 课程类也算事实锚点（防「准备考试」这类正常句被元对话守卫误伤）
_STUDY_WORDS = ("考试", "备考", "上课", "课程", "专业", "毕业", "论文", "作业", "实习", "军训")
_STABLE_PREFERENCE_WORDS = (
    "喜欢", "爱吃", "爱喝", "最爱", "超爱", "特别喜欢", "好喜欢", "讨厌",
    "不喜欢", "受不了", "害怕", "反感", "吃不了", "不能吃", "习惯", "总是",
    "一向", "从来不", "从不", "每天都",
)
_IDENTITY_WORDS = (
    "年龄", "生日", "家乡", "老家", "父母", "妈妈", "爸爸", "哥哥", "姐姐",
    "弟弟", "妹妹", "儿子", "女儿", "老婆", "老公", "对象", "结婚", "单身",
    "分手", "复合", "在读", "大二", "大三", "大四", "高一", "高二", "高三",
)
_LOCATION_ANCHOR_WORDS = (
    "住在", "居住", "定居", "常驻", "常住", "现居", "长期在", "老家", "家乡",
    "搬到", "搬回", "宿舍", "校区", "学校", "大学", "学院", "公司", "小区", "公寓",
)

# 「我是…」自述正则（与 response_parser._INFO_PATTERNS 第 1/8 条同源，取捕获组）
_NAME_TOKEN_RE = re.compile(r"(?:我叫|我是|名字叫|呼唤我|喊我|可以叫我|记得我叫|记得我)\s*([^\s，。！？、,.!?]{1,8})")
_LATIN_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,11}$")
_ADMIN_SUFFIX_RE = re.compile(r"[市省区县镇州盟旗]")
_PLACE_SUFFIX_RE = re.compile(r"[\u4e00-\u9fa5]{2,6}(?:市|省|区|县|镇|州|盟|旗)")
_AGE_RE = re.compile(r"\d{1,3}\s*岁")
_GENERIC_TITLE = "一段对话"
GENERIC_TITLE = _GENERIC_TITLE  # 公开：泛化 title（证据不足时的落点）


def is_meta_about_ai(text: str) -> bool:
    """是否在讨论 AI 本身 / 系统 / 记忆机制 / 报错 / 情绪宣泄（纯词表命中）。"""
    return any(k in (text or "") for k in _META_ABOUT_AI)


def looks_like_person_name(token: str) -> bool:
    """token 是否像人名（长度 + 姓氏表 + 拉丁昵称），供 title「用户的名字」证据锚点用。"""
    t = (token or "").strip().strip("，。！？、,.!?\"'「」《》()（）")
    if not t:
        return False
    if _LATIN_NAME_RE.match(t):
        return True
    if not re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", t):
        return False
    if t in _NON_NAME_WORDS or any(t.startswith(w) for w in _NON_NAME_WORDS):
        return False
    # 昵称前缀（小/老/阿）：小轩、老张、阿明
    if t[0] in ("小", "老", "阿"):
        return True
    # 2 字：首字须为姓氏；3-4 字：首字为姓氏（复姓兜底）
    return t[0] in _SURNAMES


def looks_like_job(text: str) -> bool:
    """是否含职业语义词（title「用户的职业」证据锚点）。"""
    return any(j in (text or "") for j in _JOBS)


def has_fact_anchor(text: str) -> bool:
    """内容里是否存在真实用户事实锚点（人名/职业/位置/稳定偏好/身份/学业）。

    「有锚点」= 元对话守卫放行：说明这条即便提到 AI/记忆，也附带了可留存的事实。
    """
    t = text or ""
    if not t:
        return False
    # 人名：显式自述式「我叫/我是 X」且 X 像人名
    for m in _NAME_TOKEN_RE.finditer(t):
        if looks_like_person_name(m.group(1)):
            return True
    if looks_like_job(t) or any(w in t for w in _STUDY_WORDS):
        return True
    if any(w in t for w in _STABLE_PREFERENCE_WORDS):
        return True
    if any(w in t for w in _IDENTITY_WORDS) or _AGE_RE.search(t):
        return True
    if any(w in t for w in _LOCATION_ANCHOR_WORDS) or _PLACE_SUFFIX_RE.search(t):
        return True
    return False


def is_meta_without_anchor(text: str) -> bool:
    """元对话/情绪宣泄且**无**真实事实锚点 → 不得进 user_info/enduring。"""
    return is_meta_about_ai(text) and not has_fact_anchor(text)


def name_token_in(text: str) -> str | None:
    """取「我叫/我是…」自述里捕获到的名字 token（无则 None）。"""
    m = _NAME_TOKEN_RE.search(text or "")
    return m.group(1) if m else None


def title_evidence_ok(title: str, content: str) -> bool:
    """title 是否有证据支撑（「用户的名字」要像人名、「用户的职业」要含职业语义词）。

    其它 title（年龄/喜好/所在地/活动…）不设锚点，一律 True（不扩大误伤面）。
    """
    t = (title or "").strip()
    if t == "用户的名字":
        tok = name_token_in(content)
        return bool(tok) and looks_like_person_name(tok)
    if t == "用户的职业":
        return looks_like_job(content)
    return True


def apply_title_anchor(title: str, content: str) -> tuple[str, bool]:
    """返回 ``(最终 title, 是否保留专用 title)``；证据不足 → 泛化 title（调用方另把类型降 event）。"""
    if title_evidence_ok(title, content):
        return title, True
    return _GENERIC_TITLE, False


__all__ = [
    "is_meta_about_ai",
    "is_meta_without_anchor",
    "has_fact_anchor",
    "looks_like_person_name",
    "looks_like_job",
    "name_token_in",
    "title_evidence_ok",
    "apply_title_anchor",
    "GENERIC_TITLE",
]
