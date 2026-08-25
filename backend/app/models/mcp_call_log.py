# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.mcp_call_log` -> `app.models.mcp.call_log.py`
from app.models.mcp.call_log import McpCallLog

__all__ = [
    "McpCallLog",
]
