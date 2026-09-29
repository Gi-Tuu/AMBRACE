# -*- coding: utf-8 -*-
"""模型自写记忆「依据校验」影子档测试（2026-09-29，方案 B 第一步）。

钉住的三条底线：
1. 判据 ``user_evidence_absent`` 是纯函数：命中→False、无交集→True、空/脏/超长输入→False 且不抛；
2. 挂点（``agent/nodes.py`` 标记写入循环）**只判定 + 只留痕**：flag 开时 ``save_memory`` 收到的参数
   与 flag 关时逐字段相同（不改 speaker、不改 epistemic_status、不拒收、不删条）；
3. 任何异常 fail-open：判据抛错 ⇒ 写入照旧、状态照旧、不留 absent 痕。
"""
import asyncio
import logging

from app.agent.loop import AGENT_FLAGS
from app.application.flag_catalog import FLAG_CATALOG
from app.memory.marker_evidence import user_evidence_absent

UID, CID = 4, 11


# ───────────────────────── 判据：纯函数 ─────────────────────────

def test_判据_片段命中_判有依据():
    assert user_evidence_absent("用户喜欢喝美式咖啡", "我最近超爱喝美式咖啡") is False


def test_判据_完全无交集_判无依据():
    # 用户只说「今天加班到十点」，模型自写「养了一只柯基」⇒ 无依据（影子期只留痕）
    assert user_evidence_absent("养了一只柯基", "今天加班到十点好累") is True


def test_判据_用户消息为空_不降级():
    assert user_evidence_absent("喜欢下雨天", "") is False
    assert user_evidence_absent("喜欢下雨天", "   ") is False
    assert user_evidence_absent("喜欢下雨天", "！！！") is False


def test_判据_内容为空或凑不出片段_不降级():
    assert user_evidence_absent("", "我今天加班") is False
    assert user_evidence_absent("    ", "我今天加班") is False
    assert user_evidence_absent("……、「」", "我今天加班") is False
    assert user_evidence_absent("猫", "我家猫今天很乖") is False  # 归一化后不足 2 字片段


def test_判据_脏输入不抛且判有依据():
    for bad in (None, 123, 3.14, ["咖啡"], {"a": 1}, b"\xe5\x92\x96\xe5\x95\xa1", object()):
        assert user_evidence_absent(bad, "我今天加班") is False
        assert user_evidence_absent("用户喜欢咖啡", bad) is False


def test_判据_全半角与标点差异仍能命中():
    assert user_evidence_absent("喜欢 ＡＢＣ 咖啡！", "abc 和咖啡都不好喝") is False
    assert user_evidence_absent("记 得：用户 爱 喝 美式", "我爱喝美式") is False


def test_判据_大小写差异仍能命中():
    assert user_evidence_absent("用户在用 iPhone", "我的 IPHONE 摔了") is False


def test_判据_只差一字不算命中():
    # 单字重叠不构成 ≥2 字片段 ⇒ 判无依据（阈值口径：片段长度 ≥2）
    assert user_evidence_absent("咖啡过敏", "他喜欢喝茶") is True
    # 反例：只差 1 字但另有 2 字片段命中 ⇒ 判有依据（宁漏判不误判）
    assert user_evidence_absent("喜欢喝美式咖啡", "喜欢喝美式奶茶") is False


def test_判据_超长输入不抛且判有依据():
    assert user_evidence_absent("咖" * 20000, "咖啡因过敏") is False
    assert user_evidence_absent("咖啡因过敏", "谢" * 20000) is False


def test_判据_不mutate入参且可重复调用():
    c, u = "用户喜欢喝美式咖啡", "我喜欢喝美式"
    assert user_evidence_absent(c, u) is False
    assert (c, u) == ("用户喜欢喝美式咖啡", "我喜欢喝美式")
    assert user_evidence_absent(c, u) is False


def test_判据_同轮多话题_宽松命中():
    long_msg = "周末想去爬山，对了最近在读《百年孤独》，还有明天要出差"
    assert user_evidence_absent("用户在读百年孤独", long_msg) is False


# ───────────────────────── flag 登记 ─────────────────────────

def test_flag_默认关且已登记两处代码面():
    assert AGENT_FLAGS["marker_requires_user_evidence"] is False
    assert "marker_requires_user_evidence" in FLAG_CATALOG
    meta = FLAG_CATALOG["marker_requires_user_evidence"]
    assert meta["group"] == "memory" and meta["visible"] is False


# ───────────────────────── 挂点：影子档接线 ─────────────────────────

class _FakeMemory:
    id = 991


def _run(monkeypatch, content, user_msg, *, flag_on, probe=None, receipt="spy"):
    """跑一次标记写入段，返回 (save_memory 收到的参数列表, 回执调用列表)。"""
    import app.agent.llm_client as lc
    import app.memory
    import app.memory.marker_evidence as me_mod
    import app.memory.receipt as receipt_mod
    from app.agent import nodes as nodes_mod

    saved, receipts = [], []

    async def _fake_save(**kw):
        saved.append(kw)
        return _FakeMemory()

    def _spy_receipt(character_id, memory_id, action, *, reason="", detail=None):
        receipts.append({"character_id": character_id, "memory_id": memory_id,
                         "action": action, "reason": reason, "detail": detail})

    async def _fake_llm(**kw):
        return "好呀～"

    def _fake_parse(response, state):
        state["ai_response"] = response
        state["new_memories"] = [{"type": "user_info", "title": "", "sub_type": "health",
                                  "content": content, "importance": 3}]
        return state

    async def _fake_cfg(user_id):
        return None

    monkeypatch.setattr(app.memory, "save_memory", _fake_save)
    monkeypatch.setattr(receipt_mod, "emit_memory_receipt",
                        _spy_receipt if receipt == "spy" else receipt)
    if probe is not None:
        monkeypatch.setattr(me_mod, "user_evidence_absent", probe)
    monkeypatch.setattr(nodes_mod, "chat_completion", _fake_llm)
    monkeypatch.setattr(nodes_mod, "parse_response", _fake_parse)
    monkeypatch.setattr(lc, "get_user_llm_config", _fake_cfg)
    monkeypatch.setitem(AGENT_FLAGS, "marker_requires_user_evidence", flag_on)

    state = {"user_id": UID, "character_id": CID, "user_message": user_msg,
             "context_messages": [], "temperature": 0.8, "reasoning_level": 0,
             "new_memories": []}
    asyncio.run(nodes_mod.generate_response(state))
    return saved, receipts


NO_BASIS = ("养了一只柯基", "今天加班到十点好累")  # 无依据样本
HAS_BASIS = ("用户喜欢喝美式咖啡", "我最近超爱喝美式咖啡")  # 有依据样本


def test_flag关_逐字节旧行为_判据不被调用且无回执(monkeypatch):
    hits = []
    saved, receipts = _run(monkeypatch, *NO_BASIS, flag_on=False,
                           probe=lambda *a, **k: hits.append(a) or False)
    assert len(saved) == 1
    assert receipts == []
    assert hits == []  # flag 关：判据一次都不调（零比对、零 import 生效）
    assert saved[0]["speaker_type"] == "user" and saved[0]["epistemic_status"] == "FACT"


def test_flag开_无依据_状态不变但留回执(monkeypatch):
    saved, receipts = _run(monkeypatch, *NO_BASIS, flag_on=True)
    assert len(saved) == 1
    # 影子档：状态一字不改（仍按 speaker 规则 5 落 user/FACT；不降级、不拒收、不删条）
    assert saved[0]["speaker_type"] == "user"
    assert saved[0]["epistemic_status"] == "FACT"
    assert saved[0]["source"] == "chat" and saved[0]["sub_type"] == "health"
    assert len(receipts) == 1
    r = receipts[0]
    assert "marker_evidence=absent" in r["reason"]
    assert r["character_id"] == CID and r["memory_id"] == _FakeMemory.id
    assert r["detail"] == {"sub_type": "health", "speaker_type": "user",
                           "epistemic_status": "FACT", "content_preview": NO_BASIS[0]}


def test_flag开_有依据_不留痕(monkeypatch):
    saved, receipts = _run(monkeypatch, *HAS_BASIS, flag_on=True)
    assert len(saved) == 1 and receipts == []
    assert saved[0]["speaker_type"] == "user" and saved[0]["epistemic_status"] == "FACT"


def test_flag开关_写入参数逐字段相同(monkeypatch):
    """影子档的定义性断言：开与关，save_memory 收到的 kwargs 必须完全一致。"""
    saved_off, receipts_off = _run(monkeypatch, *NO_BASIS, flag_on=False)
    saved_on, receipts_on = _run(monkeypatch, *NO_BASIS, flag_on=True)
    assert saved_off == saved_on
    assert saved_on and receipts_off == [] and len(receipts_on) == 1


def test_flag开_判据抛错_fail_open写入照旧(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("判据内部炸了")

    saved, receipts = _run(monkeypatch, *NO_BASIS, flag_on=True, probe=_boom)
    assert len(saved) == 1  # 写入照旧
    assert saved[0]["speaker_type"] == "user" and saved[0]["epistemic_status"] == "FACT"
    assert receipts == []  # 抛错 ⇒ 不判为 absent，不留痕


def test_flag开_回执发射口抛错_不影响写入(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("回执写不进去")

    saved, receipts = _run(monkeypatch, *NO_BASIS, flag_on=True, receipt=_boom)
    assert len(saved) == 1
    assert saved[0]["speaker_type"] == "user" and saved[0]["epistemic_status"] == "FACT"
    assert receipts == []


def test_flag开_回执闸关时仍以INFO日志留痕(monkeypatch, caplog):
    """memory_write_receipt 闸关（默认）时回执不落库，影子数据靠 INFO 日志看见。"""
    import app.memory.receipt as receipt_mod

    monkeypatch.setitem(AGENT_FLAGS, "memory_write_receipt", False)
    with caplog.at_level(logging.INFO, logger="agent.nodes"):
        saved, _ = _run(monkeypatch, *NO_BASIS, flag_on=True,
                        receipt=receipt_mod.emit_memory_receipt)
    assert len(saved) == 1
    msgs = [r.getMessage() for r in caplog.records if "marker_evidence shadow" in r.getMessage()]
    assert msgs, "命中无依据必须打 INFO 日志（含 character_id / sub_type / absent 计数）"
    assert "char=11" in msgs[-1] and "sub_type=health" in msgs[-1] and "absent=1/1" in msgs[-1]
