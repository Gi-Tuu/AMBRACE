import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
BROAD_MAX = 1640
SWALLOW_MAX = 439


def _counts():
    files = broad = swallow = 0
    for p in APP.rglob("*.py"):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        files += 1
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for h in node.handlers:
                typ = h.type
                is_broad = typ is None or (
                    isinstance(typ, ast.Name) and typ.id in ("Exception", "BaseException")
                )
                if not is_broad:
                    continue
                broad += 1
                body = h.body
                if len(body) == 1 and isinstance(body[0], (ast.Pass, ast.Continue)):
                    swallow += 1
                elif (
                    len(body) == 1
                    and isinstance(body[0], ast.Return)
                    and isinstance(body[0].value, ast.Constant)
                    and body[0].value.value in (None, False, True, "", 0)
                ):
                    swallow += 1
    return files, broad, swallow


def test_静默兜底棘轮不许继续增长():
    """新代码不得再增加宽异常/单语句静默；减少后请同步下调基线。"""
    files, broad, swallow = _counts()
    assert files >= 450, f"扫描面塌了：{files} 文件"
    assert broad <= BROAD_MAX, f"宽异常从 {BROAD_MAX} 涨到 {broad}：新兜底必须改或登记"
    assert swallow <= SWALLOW_MAX, f"单语句静默从 {SWALLOW_MAX} 涨到 {swallow}：新兜底必须改或登记"
