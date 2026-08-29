# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.agent_task_log` -> `app.models.agent.task_log.py`
from app.models.agent.task_log import AgentTaskLog

__all__ = [
    "AgentTaskLog",
]
