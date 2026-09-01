# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.mcp_server` -> `app.models.mcp.server.py`
from app.models.mcp import MCPServer

__all__ = [
    "MCPServer",
]
