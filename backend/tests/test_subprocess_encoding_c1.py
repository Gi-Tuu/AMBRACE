# -*- coding: utf-8 -*-
"""C1 守卫：测试里凡「用子进程跑脚本再断言其输出」，必须显式钉死 UTF-8（`encoding` ＋ `env`）。

为什么钉这条：10-04 第 99 棒 CI 红 2 例，报错是 `'NoneType' object has no attribute 'split'`，
看着像被测脚本坏了，实际是**测试自己的解码假设**——只写 `text=True` 时父进程按本机 ANSI 码页解码、
子进程按自己的码页编码，解码异常在读管线的线程里被吞掉 ⇒ stdout/stderr 变 None。
本机是中文码页＋UTF-8 环境，同一份测试本地全绿，所以这类债只有换机器才会现形。

两条纪律（缺一条这守卫就是摆设）：
- **反向计数**：断言扫描真的看到 ≥7 处调用。守卫最怕"一段都扫不到却报绿"（10-06 变异电池当场演示过）。
- **自证有牙**：拿一段"只写 text=True"的桩代码喂给同一个检查器，必须报出来。
"""
import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent
# 2026-10-08 C1 收口时的实测数量；新增调用只会更多，少于这个数说明扫描口径坏了
FLOOR = 7


def find_unpinned(source: str, filename: str = "<inline>") -> list[tuple[int, list[str]]]:
    """返回「用了 text=True 却没同时钉 encoding＋env」的子进程调用（行号, 现有关键字）。"""
    bad = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", "")
        if name not in ("run", "check_output", "Popen"):
            continue
        kw = {k.arg for k in node.keywords if k.arg}
        if not kw:                       # 位置参数形式不在本守卫口径内（现有代码无此写法）
            continue
        if "text" in kw and not {"encoding", "env"} <= kw:
            bad.append((node.lineno, sorted(kw)))
    return bad


def test_测试里的_text_true_子进程必须钉_encoding_与_env():
    offenders = []
    seen = 0
    for p in sorted(TESTS.glob("test_*.py")):
        src = p.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in ("run", "check_output", "Popen"):
                kw = {k.arg for k in node.keywords if k.arg}
                if "text" in kw:
                    seen += 1
        offenders.extend((p.name, line, kws) for line, kws in find_unpinned(src, p.name))
    assert offenders == [], f"这些调用没钉死编码：{offenders}"
    assert seen >= FLOOR, f"只扫到 {seen} 处 text=True 子进程调用（应 ≥{FLOOR}）——扫描口径坏了，本守卫没牙"


def test_守卫自己得有牙_桩代码必须被报出来():
    stub = (
        "import subprocess\n"
        "r = subprocess.run(['python', '-c', 'print(1)'], capture_output=True, text=True)\n"
    )
    bad = find_unpinned(stub)
    assert len(bad) == 1 and bad[0][1] == ["capture_output", "text"], bad

    ok_stub = (
        "import subprocess\n"
        "r = subprocess.run(['python', '-c', 'print(1)'], capture_output=True, text=True,\n"
        "                   encoding='utf-8', errors='replace', env={'PYTHONIOENCODING': 'utf-8'})\n"
    )
    assert find_unpinned(ok_stub) == []


def test_位置参数写法不在口径内_这点必须写明():
    """`subprocess.run(cmd, True)` 这类不会命中本守卫——现有测试里没有此写法，日后要一起管就得扩这里。"""
    positional = "import subprocess\nsubprocess.run(['python', '-c', 'print(1)'], True)\n"
    assert find_unpinned(positional) == []
