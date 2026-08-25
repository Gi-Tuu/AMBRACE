"""记忆注入行公共格式化（X-1，2026-08-18）：统一「- [记录于 YYYY-MM-DD] [认知前缀] 内容[:max_len]」结构。

四处注入点共用本函数，避免格式漂移（此前主链路 context_builder / 主动消息 message_generator /
最近情绪事件 persona / Shared Memory recall_text 各自拼写，截断长度、前缀顺序、[记录于] 位置均不同）：

- context_builder._format_memory_line（已迁移）→ format_memory_line(m)（max_len=150，prefix="- "）
- message_generator 最近记忆 → format_memory_line(m, max_len=80)
- persona 最近情绪事件 → format_memory_line(m, prefix="", max_len=150)
- shared_events.recall_text → format_memory_line(m, max_len=120)

行为等价替换为主：各调用点传入的字段决定是否出现认知前缀/纠正后缀，截断长度与内容不变。
"""
from __future__ import annotations


def epistemic_prefix(status) -> str:
    """记忆认知状态标注前缀（World & Cognition P3）：FACT 是默认事实不标；
    INFERRED/PLANNED/UNVERIFIED 显式标注，让模型区分事实/推断/计划。"""
    if not status or status == "FACT":
        return ""
    return f"[{status}] "


def format_memory_line(m: dict, max_len: int = 150, prefix: str = "- ", include_speaker: bool = False) -> str:
    """记忆注入行格式化（纯函数，X-1/M-P1-2）：
    `{prefix}[记录于 YYYY-MM-DD] [认知前缀][说话人] 内容[:max_len]`；
    被用户纠正过的记忆（contradiction_count>0）行尾追加「（你后来纠正过，以你最新说法为准）」，
    提醒模型错误事实已纠正、以最新说法为准（追加在截断之后）。

    - m：记忆字典（兼容检索结果 dict / ORM 转 dict）；字段缺失时对应标注自然省略。
    - max_len：内容截断长度（默认 150，与主链路一致；message_generator 传 80、shared_events 传 120）。
    - prefix：行首前缀（默认 "- "；persona 最近情绪事件无前缀传 ""）。
    - include_speaker：是否附带说话人标注（X-2，2026-08-18：主链路 context_builder 记忆注入区已启用；
      主动消息/Shared Memory/persona 三处保持 False 不改变既有行为；默认 False）。
    """
    mem_text = m.get("content", "") or m.get("title", "")
    if not mem_text:
        return ""
    _pre = epistemic_prefix(m.get("epistemic_status"))
    _rs = m.get("reliability_score")
    if not _pre and _rs is not None and _rs < 0.4:
        _pre = "[UNVERIFIED] "
    _rec = str(m.get("created_at") or "")[:10]
    _rec_tag = f"[记录于 {_rec}] " if _rec else ""
    _sp_tag = ""
    if include_speaker:
        _sp = str(m.get("speaker_type") or "").strip()
        if _sp == "user":
            _sp_tag = "[你说的] "
        elif _sp == "character":
            _sp_tag = "[TA说的] "
        elif _sp == "system":
            _sp_tag = "[系统说的] "
    _cc = m.get("contradiction_count") or 0
    _cc_tag = "（你后来纠正过，以你最新说法为准）" if _cc > 0 else ""
    return f"{prefix}{_rec_tag}{_pre}{_sp_tag}{mem_text[:max_len]}{_cc_tag}"
