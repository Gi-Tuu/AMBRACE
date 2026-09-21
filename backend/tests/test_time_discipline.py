"""P2-2 时间口径守卫（防回潮棘轮）：禁止新增「裸 aware」写法。

约定：全项目时间一律存 UTC naive（唯一写库入口 app/utils/timeutil.now_naive_utc）。
裸 aware 的 ``datetime.now(timezone.utc)`` 两类危害：
1) 与 naive 列 / naive 值直接比较或相减 → Python 侧抛 TypeError
   （can't subtract offset-naive and offset-aware datetimes），常被上层 except 吞掉，
   表现为功能静默失效（节流/配额判断悄悄不生效）。
2) SQLite DATETIME 绑定会静默丢弃 tzinfo（当前时刻巧合等价、不报错），
   一旦换 Postgres/MySQL 或走裸 SQL/JSON 序列化即口径漂移，问题不可追踪。

判定：某行产出 aware 值（``datetime.now(timezone.utc)`` 及其变体：``now(UTC)`` /
``now(datetime.UTC)`` / ``now(tz=timezone.utc)`` / ``utcfromtimestamp(...)`` /
``fromtimestamp(ts, tz=...)``），且同行既没有 ``.replace(tzinfo=None)``
（归一）也不是 ``.isoformat()``（只输出字符串、不入库）→ 视为违规。
naive 值先显式提升为 aware（``ts.replace(tzinfo=timezone.utc)``）再全程 aware 运算
的写法不在豁免之列，需逐点登记理由（见 KEEP_AWARE）。

层级：
- KEEP_AWARE：永久豁免（已核对确认「全程 aware 自洽 / 仅输出」的点，附理由）
- LEGACY_AWARE：存量待拍板清单，只减不增；每清理一处就删掉对应行
"""
import re
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

# P3-⑦（2026-09-19）：覆盖面扩充——只要产出 aware 的写法都算，避免换个马甲就绕开守卫
# （当前 app 下这些变体均为 0 处，属防御性棘轮）。
_UTC_TZ = r"(?:datetime\.timezone\.utc|timezone\.utc|datetime\.UTC|UTC)"
AWARE_NOW = re.compile(
    rf"datetime\.now\(\s*(?:tz\s*=\s*)?{_UTC_TZ}\s*\)"      # now(UTC) / now(datetime.UTC) / now(tz=timezone.utc)
    rf"|datetime\.fromtimestamp\([^)]*,[^)]*\)"              # fromtimestamp(ts, tz=...) 带 tz 参数即 aware
)
DEPRECATED_UTCNOW = re.compile(r"datetime\.utcnow\(\s*\)|datetime\.utcfromtimestamp\s*\(")
EXEMPT_MARKS = (".replace(tzinfo=None)", ".isoformat()")

# 整文件豁免：timeutil 自身是口径定义处（astimezone 只求本地小时，不写库）
EXEMPT_FILES = frozenset({"utils/timeutil.py"})

# (相对 app/ 的路径, 去掉缩进与行尾注释后的源码行) -> 保持 aware 的理由
KEEP_AWARE: dict[tuple[str, str], str] = {
    ("api/system.py",
     'return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}'):
        "仅 /health 输出 JSON 时间戳，不入库",
    ("api/marketplace.py",
     '"last_refresh_at": datetime.now(timezone.utc).isoformat(),'):
        "缓存 meta 落盘为 ISO 字符串（带 +00:00），不是 naive 列",
    ("api/marketplace.py",
     "if (datetime.now(timezone.utc) - last).total_seconds() < interval_s:"):
        "last 由上一行 fromisoformat 得到（源串恒带 +00:00 故恒 aware），aware-aware 相减口径自洽",
    ("api/scheduler.py",
     "now = datetime.now(timezone.utc)"):
        "list_timers：naive 列先显式提升为 aware 再比较/相减，全程 aware 且不写库",
}

# 存量裸 aware：P2-2 起逐条清理。**2026-09-21（.legacy_ 批）31 条全部归一完毕，清单清空**
# （scheduling/* 20 条 → life/application/memory/agent 11 条；统一走 timeutil.now_naive_utc /
#  to_naive_utc）。棘轮继续生效：新增裸 aware 立即红，本清单只允许保持为空或按需登记理由。
LEGACY_AWARE: frozenset[tuple[str, str]] = frozenset()


def _scan_app() -> tuple[list[str], set[tuple[str, str]]]:
    """返回 (违规清单, 现存 aware 写法集合)。"""
    offenders: list[str] = []
    live: set[tuple[str, str]] = set()
    for py in sorted(APP_ROOT.rglob("*.py")):
        rel = py.relative_to(APP_ROOT).as_posix()
        lines = py.read_text(encoding="utf-8").splitlines()
        for lineno, raw in enumerate(lines, 1):
            code = raw.split("#", 1)[0]
            if not (AWARE_NOW.search(code) or DEPRECATED_UTCNOW.search(code)):
                continue
            stripped = code.strip()
            if rel in EXEMPT_FILES:
                # 整文件豁免（口径定义处）：既不计入 live 清单，也不参与违规判定
                continue
            live.add((rel, stripped))
            if DEPRECATED_UTCNOW.search(code):
                offenders.append(f"{rel}:{lineno} 已废弃的 utcnow()/utcfromtimestamp()（口径含糊）")
            elif any(mark in code for mark in EXEMPT_MARKS):
                continue
            elif (rel, stripped) in KEEP_AWARE or (rel, stripped) in LEGACY_AWARE:
                continue
            else:
                offenders.append(f"{rel}:{lineno} 裸 aware -> {stripped}")
    return offenders, live


def test_无新增裸_aware_时间写法():
    """写库/比较 naive 列必须走 now_naive_utc()；LEGACY_AWARE 存量清单只减不增。"""
    offenders, _ = _scan_app()
    assert not offenders, (
        "发现未归一的 aware 时间写法（应改用 app/utils/timeutil.now_naive_utc()，"
        "或同行显式 .replace(tzinfo=None)）：\n" + "\n".join(offenders)
    )


def test_守卫正则覆盖面自检():
    """P3-⑦：正则写错会「恒不命中」，守卫形同虚设——这里用样例字符串自检覆盖面。"""
    aware_samples = [
        "datetime.now(timezone.utc)",
        "datetime.now( UTC )",
        "datetime.now(datetime.timezone.utc)",
        "datetime.now(UTC)",
        "datetime.now(datetime.UTC)",
        "datetime.now(tz=timezone.utc)",
        "datetime.now(tz=UTC)",
        "datetime.utcfromtimestamp(0)",
        "datetime.utcfromtimestamp(ts)",
        "datetime.fromtimestamp(ts, timezone.utc)",
        "datetime.fromtimestamp(ts, tz=timezone.utc)",
        "now = datetime.now(timezone.utc) - timedelta(hours=1)",
    ]
    for s in aware_samples:
        assert AWARE_NOW.search(s) or DEPRECATED_UTCNOW.search(s), f"守卫正则未命中（应判为 aware）：{s}"

    # 反例：naive / 无关写法不得误命中，否则守卫会变成噪音
    naive_samples = [
        "datetime.now()",
        "datetime.now(naive)",
        "datetime.fromtimestamp(ts)",
        "now = now_naive_utc()",
        "ts = datetime.strptime(s, '%Y-%m-%d')",
    ]
    for s in naive_samples:
        assert not (AWARE_NOW.search(s) or DEPRECATED_UTCNOW.search(s)), f"守卫正则误命中（应为非 aware）：{s}"


def test_豁免清单无失效条目():
    """豁免/存量条目若已被改写或删除，提示收敛清单，避免清单越积越长。"""
    _, live = _scan_app()
    stale = sorted({*KEEP_AWARE, *LEGACY_AWARE} - live)
    assert not stale, f"清单存在失效条目（对应源码行已不存在，请删除）：{stale}"
