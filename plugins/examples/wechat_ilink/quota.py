# -*- coding: utf-8 -*-
"""iLink 下发配额闸门（§8.4，PR3 最关键的正确性模块）。

【命名空间】渠道插件顶层模块名易撞，本插件沿用包内相对导入/文件名，新渠道须用包内相对导入或模块名前缀（registry 加载器保持现状）。

规则（§2 一手核实 + §8.4）：
- 收到一条入站（用户主动发）→ 开新窗口：``window_started_at = now``、``out_count_in_window = 0``；
- 每次出站前 ``acquire``：窗口未超 24h 且已用 >= N → 拒绝（allowed=False）；否则计数 +1 放行；
- 窗口超过 24h 且期间无新入站 → 不允许主动下发（用户不在互动窗口内），返回 False；
- 回复本身占 1 条（入站回复在窗口尚未耗尽时取第 1 条额度）。

并发安全（P0-2，绝不能超发 10 条/24h）：计数「读取 + 更新」必须在**同一事务 / 行锁**内完成。
- 临时 SQLite：单写语义（同一个 aiosqlite 连接写串行化），无需额外显式锁；
- 生产 PostgreSQL：读取 binding 行时必须 ``select(...).with_for_update()`` 行锁，保证
  read-modify-write 原子（inbound 消费器在加载 binding 行时已按此要求执行；本闸门按
  「调用方在同一事务内传入 binding 行、本方法内 read+write、调用方再 commit」设计）。

本文件零 app 依赖、只操作 duck-typed ``binding``（要求实现属性 ``window_started_at``、
``out_count_in_window``，可选 ``last_inbound_at``），可独立单测（不碰 DB/网络）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# 用户最后一条入站起的互动窗口（官方限制：用户发起后 24h 内最多下发 N 条）。
WINDOW = timedelta(hours=24)


@dataclass
class QuotaDecision:
    """一次配额裁决结果。``allowed`` 决定是否放行；``remaining`` 为窗口内剩余可下发条数。"""
    allowed: bool
    remaining: int
    reason: str = ""


class QuotaGate:
    """24h/N 配额闸门（N 默认 10，最小 1）。

    状态存放在 ``binding``（ORM 行或测试用的轻量对象）上：``window_started_at`` 与
    ``out_count_in_window``。本闸门不 commit——由调用方在事务内加载行、调用、再 commit，
    确保计数读写与落库同事务。
    """

    def __init__(self, quota_per_24h: int = 10):
        self.n = max(1, int(quota_per_24h))

    def on_inbound(self, binding) -> None:
        """用户发来一条 → 重置窗口（在 binding 上原地改，由调用方 commit）。

        语义：无论此前窗口用了多少，只要用户新发一条，开新窗口、配额归零。
        """
        binding.window_started_at = datetime.now()
        binding.out_count_in_window = 0
        binding.last_inbound_at = datetime.now()

    def can_acquire(self, binding, now: datetime | None = None) -> QuotaDecision:
        """只读裁决（不计数）：是否允许下发一条。

        - 无互动窗口（从未在 24h 内发过消息 / 窗口已过期）→ 拒绝（no_active_window）；
        - 窗口内已用满 N 条 → 拒绝（exhausted）；
        - 否则放行（remaining > 0）。
        """
        now = now or datetime.now()
        start = binding.window_started_at
        if start is None or now - start > WINDOW:
            return QuotaDecision(False, 0, "no_active_window")
        used = int(binding.out_count_in_window or 0)
        remaining = self.n - used
        if remaining <= 0:
            return QuotaDecision(False, 0, "exhausted")
        return QuotaDecision(True, remaining)

    def acquire(self, binding, now: datetime | None = None) -> QuotaDecision:
        """尝试取得一次下发额度：裁决 + 放行时计数 +1（read+write 在同一事务内完成）。"""
        d = self.can_acquire(binding, now)
        if d.allowed:
            binding.out_count_in_window = int(binding.out_count_in_window or 0) + 1
        used = int(binding.out_count_in_window or 0)
        return QuotaDecision(d.allowed, max(0, self.n - used), d.reason)
