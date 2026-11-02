# -*- coding: utf-8 -*-
"""M2 2a flag is registered and off by default; old path untouched."""
from app.flags.agent_flags import AGENT_FLAGS
from app.application.flag_catalog import catalog_for


def test_memory_source_proof_v1_exists_and_is_default_off():
    assert AGENT_FLAGS["memory_source_proof_v1"] is False


def test_flag_catalog_has_metadata():
    rows = {item["key"]: item
            for group in catalog_for(AGENT_FLAGS.keys(), "zh")
            for item in group["items"]}
    assert "memory_source_proof_v1" in rows
    assert rows["memory_source_proof_v1"]["title"]


def test_no_runtime_override_written_by_2a():
    # 2a 只注册键；不写 runtime_flags（本测试沙箱库为空即证明默认关）
    from app.flags.agent_flags import AGENT_FLAGS as live
    assert live["memory_source_proof_v1"] is False
