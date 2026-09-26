# -*- coding: utf-8 -*-
"""回归：启动时跑 alembic 不得让应用日志静默（2026-09-25 实测事故）。

事故现象：服务进程活着、8000 端口在听，但 `backend/data/logs/app.log` 与
`server_stderr.log` 从「Alembic 对齐那一步」起完全停写（实测 6 小时零新增），
导致后台任务（含记忆评星）到底有没有在跑都无法从日志判断。

根因：`backend/alembic/env.py` 调 `logging.config.fileConfig(alembic.ini)`：
① 把 root logger 的 handlers 换成 console、level 压到 WARN（alembic.ini [logger_root]）；
② 默认 `disable_existing_loggers=True`，把已存在的应用 logger 全部置 `disabled`。

修法：env.py 显式传 `disable_existing_loggers=False`（去根因）＋ `app/db/migrate.py` 的
`_alembic_logging_guard` 快照/还原（兜底，防 alembic.ini 日后再被改坏）。
本用例在临时库上真跑一次对齐（空库 ⇒ stamp 分支，仍会加载 env.py），断言日志设施被逐项还原。
"""
import logging

from alembic.script import ScriptDirectory

from app.config import settings
from app.db import migrate


def test_alembic_run_restores_logging(tmp_path, monkeypatch):
    db_file = tmp_path / "alembic_logging_guard.db"
    monkeypatch.setattr(
        settings, "database_url",
        f"sqlite+aiosqlite:///{db_file.as_posix()}",
    )

    root = logging.getLogger()
    app_logger = logging.getLogger("app")
    before_handlers = list(root.handlers)
    before_root_level = root.level
    before_app_disabled = app_logger.disabled
    before_app_level = app_logger.level
    before_app_propagate = app_logger.propagate
    # 让前置状态明确：应用 logger 处于「可用」态
    app_logger.disabled = False
    app_logger.setLevel(logging.INFO)

    action = migrate._ensure_alembic_revision_sync()

    head = ScriptDirectory.from_config(migrate._alembic_config()).get_current_head()
    assert action == f"stamped:{head}", action

    assert list(root.handlers) == before_handlers, "root handlers 必须原样还原（否则 app.log 停写）"
    assert root.level == before_root_level, "root level 必须原样还原（否则 INFO 被 WARN 压掉）"
    assert app_logger.disabled is False, "应用 logger 不得被 fileConfig 禁用"
    assert app_logger.level == logging.INFO, "应用 logger level 必须原样还原"
    assert app_logger.propagate == before_app_propagate

    # 收尾还原到前置状态（不留副作用给其它用例）
    app_logger.disabled = before_app_disabled
    app_logger.setLevel(before_app_level)
