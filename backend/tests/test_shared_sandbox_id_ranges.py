# -*- coding: utf-8 -*-
"""共享沙箱 ID 号段互斥守卫（2026-11-08 测试债整改，全仓盘点后扩到全部共享面）。

背景：conftest 会话级沙箱库被多个测试文件共用；固定 9xxx 号段跨文件重叠时，
字母序先跑的文件会留下行，后跑文件的绝对计数断言会被撞成假红（已实测多组：
routes↔reauthorize、e2e↔mcp_loopback、mcp_stdio↔reallow↔why_g5、
perception↔restart↔routes_real、life_settle↔mcp_loopback、life_dead_fields↔m10）。

口径：下列**共享沙箱**文件里的四位整数 9xxx 字面量，视为该文件占用的 ID；
任意两文件不得有交集。使用每例克隆库（_dbclone）或自建引擎的文件不在此列——
它们的固定 ID 只在自己的库里有效，不会污染会话沙箱。

新增文件若写 9xxx 固定 ID：先在本文件登记并另取独占号段。
"""
import ast
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent

# 文件 → 独占号段（注释即约定，守卫只校验实际字面量互斥）
_SHARED_SANDBOX_FILES = (
    "test_pwa_attachment_contract.py",                 # 906x
    "test_arbiter_seam.py",                            # 907x
    "test_pwa_attachment_isolation.py",                # 910x
    "test_mcp_phase3.py",                              # 9101
    "test_memory_source_review_2b_rework9_primary.py", # 910x / 911x / 912x
    "test_memory_source_e2e_backup_2b.py",             # 915x / 916x / 917x
    "test_mcp_phase4.py",                              # 920x
    "test_memory_source_ws_m2b.py",                    # 930x / 931x / 932x
    "test_mcp_stdio_admin_gate_b1.py",                 # 940x
    "test_memory_source_reallow_m2b.py",               # 948x / 949x
    "test_memory_source_reauthorize_2b.py",            # 950x / 951x / 952x
    "test_memory_why_g5.py",                           # 955x / 956x
    "test_memory_source_perception_2b.py",             # 960x / 961x / 962x
    "test_memory_source_routes_real_service_m2b.py",   # 965x / 966x / 967x
    "test_memory_source_restart_m2b.py",               # 968x / 969x
    "test_garden_m3_oneshot_api.py",                   # 970x / 971x / 972x
    "test_memory_source_routes_m2b.py",                # 973x / 974x / 975x
    "test_life_settle_dt_m1.py",                       # 980x / 981x
    "test_mcp_loopback_p34.py",                        # 985x
    "test_life_state_dead_fields_m2b.py",              # 990x / 991x
    "test_memory_write_gate_m10.py",                   # 992x / 993x
    "test_memory_grading_m8.py",                       # 995x / 996x
)


def _sandbox_ids(path: Path) -> set[int]:
    """该文件里 9000–9999 的四位整数字面量（只看真字面量，不看注释/字符串）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and 9000 <= node.value <= 9998   # 9999 是多文件共用的「不存在的用户」哨兵，不占号段
    }


@pytest.mark.parametrize("name", _SHARED_SANDBOX_FILES)
def test_file_exists(name):
    assert (_TESTS_DIR / name).is_file(), f"号段登记表里的文件不存在：{name}"


def test_shared_sandbox_id_ranges_are_disjoint():
    occupied: dict[int, str] = {}
    clashes: list[str] = []
    for name in _SHARED_SANDBOX_FILES:
        for value in _sandbox_ids(_TESTS_DIR / name):
            owner = occupied.get(value)
            if owner and owner != name:
                clashes.append(f"{value} 同时被 {owner} 与 {name} 占用")
            occupied[value] = name
    assert not clashes, "共享沙箱 ID 号段重叠：\n" + "\n".join(clashes)
