"""scheduling 层（F 线重构目标骨架，F2 迁入）：定时触发器——只负责"几点触发哪个用例"，不含任何人格决策。

现 scheduler 收敛后：决策逻辑归 domain/proactivity，本层只剩 APScheduler 注册与 cron。
"""
