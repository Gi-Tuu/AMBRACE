# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.task_llm_config` -> `app.models.agent.task_llm_config.py`
from app.models.agent import TaskLlmConfig

__all__ = [
    "TaskLlmConfig",
]
