# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.tool_permission` -> `app.models.agent.tool_permission.py`
from app.models.agent import ToolPermission, PendingPermissionAction

__all__ = [
    "ToolPermission",
    "PendingPermissionAction",
]
