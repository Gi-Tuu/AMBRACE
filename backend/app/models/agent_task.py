# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.agent_task` -> `app.models.agent.task.py`
from app.models.agent.task import AgentTask

__all__ = [
    "AgentTask",
]
