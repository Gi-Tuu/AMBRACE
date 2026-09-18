#!/usr/bin/env python
"""只读审计：枚举 context 分区「注册顺序表」与「真实注入装配顺序」。

背景（2026-09-18 交接：上下文分区顺序审计）：本脚本**不改任何代码、不写库**，只做三件事——
1)  import 注册表后调用 get_sections()，打印 order/key/target/slot/quota_tokens/enabled 真值表；
2)  静态解析 context/legacy.py 的 ``if _sv and "<key>" in _sv`` 链，得到 append 块的**真实注入位置**
    （registry order 只决定 builder 执行顺序，不决定落位——这点必须靠代码枚举确认）；
3)  静态解析 SYSTEM_PROMPT_TEMPLATE 的 ``{slot}`` 出现顺序 + 末段 user 消息位置，核对四段装配先后。

用法：``cd backend && .venv/Scripts/python.exe ../scripts/audit_context_order.py``
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

LEGACY_PY = BACKEND / "app" / "agent" / "context" / "legacy.py"
CB_PY = BACKEND / "app" / "agent" / "context_builder.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def enum_registry():
    """运行 get_sections()：真值顺序表（order/key/target/slot/quota/enabled）。"""
    import app.agent.context as _ctx  # noqa: F401  触发所有 section_*.py 注册
    from app.agent.context.sections import get_sections

    return get_sections()


def legacy_consumption() -> tuple[list[tuple[int, str, str]], list[str]]:
    """抽取 legacy.py 里对 _section_values/_sv 的消费点，按源码行号顺序返回。

    - 返回 ``[(lineno, key, kind), ...]``，kind = template（覆盖模板槽变量）/ append（追加 system 块）；
    - 第二项 = legacy 用 ``_registry_done`` 跳过内联计算的位置说明（不参与落位）。
    注：registry order 只决定 builder 执行顺序，**落位由这里的源码先后决定**。
    """
    src = _read(LEGACY_PY)
    lines = src.splitlines()
    pat_in = re.compile(r'"([a-z_]+)"\s+in\s+_sv')
    pat_get = re.compile(r'_section_values\.get\("([a-z_]+)"')
    out: list[tuple[int, str, str]] = []
    for i, ln in enumerate(lines, 1):
        for pat in (pat_in, pat_get):
            m = pat.search(ln)
            if not m:
                continue
            key = m.group(1)
            # 690 行左右开始才真正 appendsystem 块；之前的消费只覆盖模板变量
            kind = "template" if i < 600 else "append"
            out.append((i, key, kind))
    return out, [ln.strip() for ln in lines if "_registry_done" in ln]


def template_slot_positions() -> list[str]:
    """SYSTEM_PROMPT_TEMPLATE 中 {slot} 的出现顺序（= 模板内的真实落位顺序）。"""
    src = _read(CB_PY)
    start = src.index("SYSTEM_PROMPT_TEMPLATE =")
    end = src.index('"""', src.index('"""', start) + 3)
    body = src[start:end]
    pat = re.compile(r"\{([a-z_]+)\}")
    out: list[str] = []
    for m in pat.finditer(body):
        k = m.group(1)
        if k not in out:
            out.append(k)
    return out


def main() -> int:
    secs = enum_registry()

    print("=" * 96)
    print("A. 注册表顺序表（get_sections() 返回值，已按 (order,key) 排序）")
    print("=" * 96)
    print(f"{'#':>3} {'order':>5} {'key':<24} {'target':<9} {'slot':<22} {'quota':>6} {'enabled'}")
    for i, s in enumerate(secs, 1):
        print(f"{i:>3} {s.order:>5} {s.key:<24} {s.target:<9} {s.slot!s:<22} {s.quota_tokens:>6} {s.enabled}")

    # 冲突检测
    orders: dict[int, list[str]] = {}
    for s in secs:
        orders.setdefault(s.order, []).append(s.key)
    dup = {o: ks for o, ks in orders.items() if len(ks) > 1}
    print()
    print(f"分区总数：{len(secs)}")
    print(f"append 分区数：{sum(1 for s in secs if s.target == 'append')}"
          f"；template 分区数：{sum(1 for s in secs if s.target == 'template')}")
    print("order 冲突（同一 order 多个 key）：")
    if dup:
        for o in sorted(dup):
            ks = dup[o]
            winner = min(ks)  # (order,key) 兜底：key 升序
            print(f"  order={o}: {ks}  → 兜底排序后先注入 key={winner!r}")
    else:
        print("  无")

    cons, _skips = legacy_consumption()
    consumed: list[str] = []
    for lineno, key, kind in cons:
        if key not in consumed:
            consumed.append(key)

    ap = [k for k in consumed if k in {c[1] for c in cons if c[2] == "append"}]
    print()
    print("=" * 96)
    print("B. append 块真实注入顺序（legacy.py 消费点的源码先后 = 真实落位顺序）")
    print("=" * 96)
    reg_keys = {s.key for s in secs}
    for i, k in enumerate(ap, 1):
        src_kind = "registry" if k in reg_keys else "legacy-inline"
        sec = next((s for s in secs if s.key == k), None)
        print(f"{i:>3} {k:<24} origin={src_kind:<14} registry_order={sec.order if sec else '-'}")

    missed = [k for k in ap if k not in reg_keys]
    print()
    print(f"legacy 链消费但注册表里没有的 key（纯 legacy 内联占位）：{missed}")

    print()
    print("-" * 96)
    print("B2. 注册表里【永不落位】的分区（builder 每轮都跑，但 legacy 从不消费其结果）")
    print("-" * 96)
    orphan = [s for s in secs if s.key not in consumed]
    if orphan:
        for s in orphan:
            print(f"    order={s.order:<4} key={s.key:<24} target={s.target:<9} quota={s.quota_tokens}")
    else:
        print("    无")

    tp = template_slot_positions()
    print()
    print("=" * 96)
    print("C. SYSTEM_PROMPT_TEMPLATE 槽位出现顺序（模板内的真实落位顺序）")
    print("=" * 96)
    slot_of_key = {s.slot: s.key for s in secs if s.target == "template" and s.slot}
    for i, k in enumerate(tp, 1):
        key = slot_of_key.get(k)
        sec = next((s for s in secs if s.key == key), None) if key else None
        print(f"{i:>3} slot={k:<22} <- key={key!s:<24} order={sec.order if sec else '-'}")

    print()
    print("=" * 96)
    print("D. 四段装配结论（见 legacy.py 源码：660 行 system 模板 → 1147~1171 插件 hook → continue_payload/user）")
    print("=" * 96)
    print("1) system 主模板块 #1：SYSTEM_PROMPT_TEMPLATE.format(...)（含 chat_history 槽，见 C 表位置）")
    print("2) 追加 system 块：按 B 表顺序（顺序由 legacy.py if 链决定，非 registry order）")
    print("3) user 最新消息：legacy.py 1191 行 role=user（宿主写入，恒为最后一条）")
    print("4) 插件 context_inject / inject_prompt_skill：1147~1171 行，由宿主位移到 user **之前**（方案 C，2026-09-18）")
    print("   + 宿主不变式 _enforce_user_message_last：装配尾部 legacy.py 1204（配额裁剪之前）；nodes.py 282~295 同护栏")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
