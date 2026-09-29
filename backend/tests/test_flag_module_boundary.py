"""断点 #8 边界钉桩：memory 侧不再引用 app.agent.loop，且新旧两条导入路径拿到同一个 dict。

AGENT_FLAGS 本体已下沉到 app/flags/agent_flags.py（中立、零业务依赖）；app.agent.loop 只保留
兼容 re-export。这里钉三件事：①memory 目录干净 ②两条路径同一对象（flag_service 热改必须
对全体读者同时生效）③新模块自身不 import 业务模块，否则循环依赖会重新长回来。
"""
import ast
import pathlib

_APP = pathlib.Path(__file__).resolve().parents[1] / "app"
_MEMORY = _APP / "memory"
_FLAGS_MOD = _APP / "flags" / "agent_flags.py"


def _iter_py(root: pathlib.Path):
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def test_memory_目录不再引用_app_agent_loop():
    hits = [
        f"{p.relative_to(_APP)}:{lineno}"
        for p in _iter_py(_MEMORY)
        for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "app.agent.loop" in line
    ]
    assert hits == [], f"memory 侧仍引用 app.agent.loop（断点 #8 未斩断）：{hits}"


def test_新旧两条导入路径是同一个对象():
    from app.agent.loop import AGENT_FLAGS as via_loop
    from app.flags.agent_flags import AGENT_FLAGS as via_flags

    assert via_loop is via_flags, "re-export 退化成副本：flag_service 的热更新将只影响其中一个 dict"


def test_新模块不引用任何业务包():
    tree = ast.parse(_FLAGS_MOD.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
    leaked = {m for m in imported if m == "app" or m in {"agent", "memory", "application", "scheduling", "api"}}
    assert leaked == set(), f"agent_flags 反向依赖业务包，循环依赖会重新长回来：{leaked}"


def test_memory_侧引用确实落到新模块():
    refs = [
        p for p in _iter_py(_MEMORY)
        if "from app.flags.agent_flags import AGENT_FLAGS" in p.read_text(encoding="utf-8")
    ]
    assert refs, "memory 目录内没有任何文件改用 app.flags.agent_flags"


def test_旧别名上的赋值会镜像到规范模块():
    """仅 re-export 挡不住 `loop.AGENT_FLAGS = {...}` 这种改写：memory 侧必须同样看到新值，
    否则经旧别名拨开关（含既有测试的 monkeypatch.setattr）会静默失效。"""
    import app.agent.loop as loop
    from app.flags import agent_flags

    original = agent_flags.AGENT_FLAGS
    try:
        loop.AGENT_FLAGS = {"memory_temporal_recall": True}
        assert agent_flags.AGENT_FLAGS == {"memory_temporal_recall": True}
    finally:
        loop.AGENT_FLAGS = original

    assert agent_flags.AGENT_FLAGS is original
    from app.agent.loop import AGENT_FLAGS as via_loop

    assert via_loop is original
