"""断点 #10 边界钉桩：基础设施不得倒挂依赖上层（db→agent、utils→models）。

依赖图矩阵曾记录 db→agent=3、utils→models=4。本测试把这 7 处逐一钉死：
- db→agent：3 处 AGENT_FLAGS 已改从中立模块 app/flags/agent_flags.py 导入（该模块零业务依赖），
  db 侧对 app.agent 的引用归零——故对 db 采用「零容忍」，任何 app.agent.* 导入都判违规。
- utils→models：4 处均为真实 ORM / metadata 数据访问，无法下沉为常量/类型，改注入需动白名单外
  调用方（越界），故按文件计数豁免（基线合计 4，只增不减：新增一行即超阈值判违规）。

扫描用 AST 而非正则：只统计真正的 import 语句，注释/字符串里的同名字样不误计（比 rg 更稳）。
"""
import ast
import pathlib

_APP = pathlib.Path(__file__).resolve().parents[1] / "app"
_DB = _APP / "db"
_UTILS = _APP / "utils"
_VECTOR_STORE = _DB / "vector_store.py"

# utils 侧对 app.models.* 的逐文件豁免（键=utils 内相对文件名，值=允许的 import 语句数）。
# 理由逐条写明；这些边是「工具操作数据模型」的正常依赖，非可斩断的倒挂。
UTILS_MODELS_ALLOWLIST = {
    # select(UserDndSettings)：免打扰时段查询的 ORM 目标；改注入需同步改 3 个白名单外调用方
    # （domain/emotion/care.py、scheduling/memory_review.py、scheduling/pet_care.py），故保留顶层 import。
    "dnd.py": 1,
    # 凭据 metadata 探测：`import app.models`（触发全部模型注册进 Base.metadata）+
    # `from app.models.base import Base`（读 Base.metadata 扫 EncryptedString 列）。
    # app/models/* 反向 import 本模块的 EncryptedText，故此处的 models 依赖必须是「函数内延迟 import」，
    # 提到顶层会形成 import 环——属必要运行期依赖，保留。
    "credential_crypto.py": 2,
    # select(User)：读 timezone_offset_minutes 的 ORM 目标，已是函数内延迟 import，真实数据访问。
    "usertz.py": 1,
}


def _iter_py(root: pathlib.Path):
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imports_under(path: pathlib.Path, prefix: str) -> list[str]:
    """返回 path 文件内所有导入模块名命中 prefix（app.<pkg>[.*]）的 'relpath:lineno:语句' 列表。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    rel = path.name
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == prefix or alias.name.startswith(prefix + "."):
                    hits.append(f"{rel}:{node.lineno}:import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == prefix or node.module.startswith(prefix + "."):
                names = ", ".join(a.name for a in node.names)
                hits.append(f"{rel}:{node.lineno}:from {node.module} import {names}")
    return hits


def test_db_目录不得引用_app_agent():
    """零容忍：db 侧对 app.agent.* 的引用必须为 0（3 处已改从中立模块导入）。"""
    hits = [h for p in _iter_py(_DB) for h in _imports_under(p, "app.agent")]
    assert hits == [], f"db 侧仍引用 app.agent（断点 #10 db→agent 倒挂未斩断）：{hits}"


def test_utils_对_app_models_引用不得超过豁免基线():
    """棘轮：逐文件统计 utils 对 app.models.* 的 import 数，只允许 ≤ 豁免值；新增即失败。"""
    actual: dict[str, int] = {}
    details: dict[str, list[str]] = {}
    for p in _iter_py(_UTILS):
        hits = _imports_under(p, "app.models")
        if hits:
            actual[p.name] = len(hits)
            details[p.name] = hits

    violations = []
    for name, cnt in actual.items():
        allowed = UTILS_MODELS_ALLOWLIST.get(name, 0)
        if cnt > allowed:
            violations.extend(details[name])
    assert not violations, (
        "utils 侧出现超出豁免基线的 app.models 引用（断点 #10 utils→models 倒挂增长）。"
        f"豁免上限={UTILS_MODELS_ALLOWLIST} 实际命中={violations}"
    )


def test_db_侧引用确实落到中立_flags_模块():
    """正向确认：vector_store 的 3 处 flag 读取改从中立模块 app.flags.agent_flags 导入。"""
    src = _VECTOR_STORE.read_text(encoding="utf-8")
    assert src.count("from app.flags.agent_flags import AGENT_FLAGS") == 3, (
        "vector_store 应恰有 3 处从 app.flags.agent_flags 导入 AGENT_FLAGS（memory_supersede/"
        "current_facts_active_only/vector_user_scope）"
    )
