# -*- coding: utf-8 -*-
"""2026-08-23 体验修复测试：自述分段 / 召回候选命中数 / 任务记录兜底 / 工具成功率口径 / 主动备忘提示词。

均为纯函数/源码契约测试（不碰真实 DB/LLM），守护本轮五处体验修复。
"""


def test_bug1_合并用段落分隔():
    from app.memory.extractor import _merge_profile_text
    out = _merge_profile_text("我喜欢夏天", "最怕打雷", 500)
    assert "\n\n" in out          # 多段自述按段落分隔
    assert "；" not in out        # 不再用分号连成一整段
    # 新值更长 → 替换（不变）
    assert _merge_profile_text("短", "这是新的完整长表述内容", 500) == "这是新的完整长表述内容"


def test_bug2_memory_search_trace语义():
    import inspect
    from app.memory import service as memsvc
    src = inspect.getsource(memsvc.search_memories)
    assert '"hit_count": candidate_count' in src   # 召回候选命中数（合并去重后的候选池）
    assert '"returned": len(results)' in src       # 实际返回条数


def test_bug3_建任务阈值降到1个动作():
    import inspect
    from app.services import chat_service
    src = inspect.getsource(chat_service)
    assert "len(_all_steps) >= 1" in src           # ≥1 个明确工具/备忘动作即建任务


def test_bug3_log_to_goal纯函数():
    from app.api.characters import _log_to_goal
    assert _log_to_goal('[{"action": "SEARCH", "query": "天气"}]', "x") == "SEARCH"
    assert _log_to_goal('{"query": "用户喜欢什么"}', "x") == "记忆召回：用户喜欢什么"
    assert _log_to_goal(None, "备用") == "备用"
    assert _log_to_goal("bad json", "备用") == "备用"


def test_bug4_status口径统一():
    from app.agent.status import classify, is_failed
    assert classify("ok") == "success"
    assert classify("done") == "success"
    assert classify("success") == "success"
    assert classify("error") == "failed"
    assert classify("failed") == "failed"
    assert classify("partial") == "partial"
    assert classify("blocked") == "blocked"
    assert classify(None) == "unknown"
    # blocked=拦截未执行，不是失败，不计入成功率分母
    assert is_failed("blocked") is False
    assert is_failed("ok") is False
    assert is_failed("error") is True


def test_bug5_主链路提示词引导备忘():
    from app.agent.context_builder import SYSTEM_PROMPT_TEMPLATE
    assert "[MEMO]内容[/MEMO]" in SYSTEM_PROMPT_TEMPLATE
    assert "用户交代要记住的事/要点" in SYSTEM_PROMPT_TEMPLATE


def test_bug5_api提示词不再禁止备忘():
    from app.services.character_chat_api import API_SYSTEM_PROMPT_TEMPLATE
    assert "[MEMO]" in API_SYSTEM_PROMPT_TEMPLATE
    assert "禁止输出除备忘以外" in API_SYSTEM_PROMPT_TEMPLATE


def test_bug5_MEMO正则匹配输出格式():
    from app.agent.actions import extract_memo
    assert extract_memo("好的[MEMO]记得带伞[/MEMO]") == "记得带伞"
    assert extract_memo("没有标记") is None
    assert extract_memo("[MEMO][/MEMO]") is None


def test_self_statement_len_上限():
    from app.memory.extractor import SELF_STATEMENT_MAX_LEN, _merge_profile_text
    assert SELF_STATEMENT_MAX_LEN == 200              # 正文 ≤200 字
    # 合并结果整体截断到 200（不超过上限），分段保留
    out = _merge_profile_text("字" * 160, "字" * 80, SELF_STATEMENT_MAX_LEN)
    assert len(out) <= 200
    assert "\n\n" in out                             # 追加仍用空行分段
    # 新值超长时截断到上限
    assert len(_merge_profile_text(None, "字" * 600, SELF_STATEMENT_MAX_LEN)) == 200


def test_self_statement_写入点统一用上限():
    import inspect
    from app.services import chat_service
    src = inspect.getsource(chat_service)
    assert "bio_text[:SELF_STATEMENT_MAX_LEN]" in src
    assert "text[:SELF_STATEMENT_MAX_LEN]" in src
    from app.memory import extractor
    esrc = inspect.getsource(extractor)
    assert "_merge_profile_text(c.self_statement, bio_val, SELF_STATEMENT_MAX_LEN)" in esrc
