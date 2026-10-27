"""0-4 评测器「冻结时钟」选项的守卫（2026-10-26，RC1 归因后补）。

背景：装配里的「现在」取本机时钟（`section_world._compute_current_time_str`），跨天复算同一基线会漂移：
同一份代码/题面/模型，10-05 02:51 跑出 AR_gold(认证)=84.5，10-26 跑出 75.9。
`--now` 只在评测进程内冻结 `datetime.now`/`date.today`，**默认不冻结**（旧行为逐字节不变）。
"""
import datetime as dt
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "diagnostics" / "memory_action_eval.py"
DATASET = REPO / "scripts" / "diagnostics" / "memory_action_cases_zh.jsonl"


def _load_eval():
    spec = importlib.util.spec_from_file_location("_memact_eval_clock_under_test", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ev = _load_eval()


def test_冻结后_now_返回固定时点且构造对象仍是原生类型():
    _base, real_dt, real_date = ev._freeze_datetime_module("2026-10-05 02:51")
    try:
        now = dt.datetime.now()
        assert (now.year, now.month, now.day, now.hour, now.minute) == (2026, 10, 5, 2, 51)
        # 构造出来的对象必须是原生类型（SQLite/SQLAlchemy 对精确类型敏感，子类会被拒）
        assert type(dt.datetime(2026, 1, 1)) is real_dt
        assert type(dt.date(2026, 1, 1)) is real_date
        assert dt.date.today() == real_date(2026, 10, 5)
    finally:
        ev._restore_datetime_module(real_dt, real_date)


def test_恢复后时钟回到本机():
    assert dt.datetime.now().year >= 2025


def test_无法解析的_now_直接报错():
    try:
        ev._freeze_datetime_module("不是时间")
    except SystemExit as exc:
        assert "--now 无法解析" in str(exc.code)
    else:
        raise AssertionError("非法 --now 必须拒绝")


def test_cli_加_now_可跑通单题_确定性路零计费():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--dataset", str(DATASET), "--judges", "J3",
         "--configs", "baseline", "--mode", "score", "--sparse-only", "--limit", "1",
         "--now", "2026-10-05 02:51", "--print-metrics"],
        cwd=str(REPO / "backend"), capture_output=True, text=True, timeout=240,
        encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert proc.returncode == 0, proc.stderr[-600:]
    assert "[时钟冻结]" in proc.stdout
    assert "评测时钟：已冻结为" in proc.stdout
