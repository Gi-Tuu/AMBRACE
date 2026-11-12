# -*- coding: utf-8 -*-
"""M10 合同：直写记忆入口加固（分级注册表 + 插件来源白名单，flag 默认关）。

覆盖：flag 关=历史字面量、flag 开=统一 INFERRED、插件来源规范化、
「四处直写点」注册表棘轮（新直写点必须先登记）、调用点接线棘轮、心事创建端到端两级取值。
"""
import asyncio
from pathlib import Path

from sqlalchemy import text

APP = Path(__file__).resolve().parents[1] / "app"


def test_flag_off_returns_historical_literals(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import write_gate

    monkeypatch.setitem(AGENT_FLAGS, write_gate.FLAG, False)
    assert write_gate.epistemic_for("preoccupation") == "FACT"
    assert write_gate.epistemic_for("summary") is None
    assert write_gate.epistemic_for("working_state") == "FACT"


def test_flag_on_grades_all_sites(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import write_gate

    monkeypatch.setitem(AGENT_FLAGS, write_gate.FLAG, True)
    for site in ("preoccupation", "summary", "working_state"):
        assert write_gate.epistemic_for(site) == "INFERRED", site
    try:
        write_gate.epistemic_for("未登记的直写点")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("未登记直写点必须 fail-fast")


def test_plugin_kwargs_normalized(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import write_gate

    monkeypatch.setitem(AGENT_FLAGS, write_gate.FLAG, False)
    assert write_gate.normalize_plugin_kwargs({"source": "chat"}) == {"source": "chat"}
    monkeypatch.setitem(AGENT_FLAGS, write_gate.FLAG, True)
    assert write_gate.normalize_plugin_kwargs({"source": "chat"})["source"] == "plugin"
    assert write_gate.normalize_plugin_kwargs({})["source"] == "plugin"


def test_registry_matches_actual_direct_write_sites():
    """棘轮：app 内「直接构造 Memory」的位置必须与注册表一致（新增绕过点会在这里红）。"""
    from app.memory import write_gate

    found: dict[str, int] = {}
    for py in sorted(APP.rglob("*.py")):
        src = py.read_text(encoding="utf-8")
        n = src.count("add(Memory(") + src.count("= Memory(")
        if n:
            found[py.relative_to(APP).as_posix()] = n
    # write.py 的 `memory = Memory(` 是 save_memory 正规入口，不算绕过
    found.pop("memory/write.py", None)
    assert found == write_gate.REGISTERED_SITES, (
        f"直写点与注册表不一致：实际 {found} vs 注册 {write_gate.REGISTERED_SITES}")


def test_call_sites_use_gate():
    from app.memory import write_gate

    pre = (APP / "life" / "preoccupations.py").read_text(encoding="utf-8")
    summ = (APP / "memory" / "summary.py").read_text(encoding="utf-8")
    ws = (APP / "application" / "working_state_service.py").read_text(encoding="utf-8")
    sdk = (APP / "plugins" / "sdk.py").read_text(encoding="utf-8")
    assert pre.count('_gate.epistemic_for("preoccupation")') == 1
    assert summ.count('_gate.epistemic_for("summary")') == 4, "新建+覆盖分支都接线（B16a 后 4 处：印象/画像 × 新建/覆盖）"
    assert ws.count('_gate.epistemic_for("working_state")') == 1
    assert "normalize_plugin_kwargs" in sdk, "插件写记忆必须过来源规范化"
    assert write_gate.FLAG == "memory_write_gate_v1"


def test_preoccupation_integration_two_modes(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import write_gate
    from app.life import preoccupations as pre
    from app.db.database import async_session_factory

    uid, cid = 9921, 9931

    async def seed():
        async with async_session_factory() as db:
            await db.execute(text(
                "INSERT OR IGNORE INTO users(id,username,nickname,password_hash) "
                "VALUES (:u,:n,'n','x')"), {"u": uid, "n": f"m10_{uid}"})
            await db.execute(text(
                "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,"
                "talkativeness_locked,is_active,cognitive_loop_enabled,memory_v2_enabled) "
                "VALUES (:c,:u,'c',0,0,1,0,0)"), {"c": cid, "u": uid})
            await db.commit()

    async def make(content: str):
        async with async_session_factory() as db:
            ok = await pre.create_preoccupation(
                db, user_id=uid, character_id=cid, content=content)
            await db.commit()
            row = (await db.execute(text(
                "SELECT epistemic_status FROM memories WHERE character_id=:c "
                "AND content=:t ORDER BY id DESC LIMIT 1"),
                {"c": cid, "t": content})).mappings().first()
        return ok, (row["epistemic_status"] if row else None)

    monkeypatch.setitem(AGENT_FLAGS, write_gate.FLAG, False)
    asyncio.run(seed())
    ok1, epi1 = asyncio.run(make("M10 心事-关档"))
    monkeypatch.setitem(AGENT_FLAGS, write_gate.FLAG, True)
    ok2, epi2 = asyncio.run(make("M10 心事-开档"))

    assert ok1 and ok2, (ok1, ok2)
    assert epi1 == "FACT", f"flag 关必须是历史字面量 FACT，实际 {epi1}"
    assert epi2 == "INFERRED", f"flag 开应降级为 INFERRED，实际 {epi2}"
