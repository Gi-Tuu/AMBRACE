# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.user_workflow` -> `app.models.life.user_workflow.py`
from app.models.life.user_workflow import UserWorkflow

__all__ = [
    "UserWorkflow",
]
