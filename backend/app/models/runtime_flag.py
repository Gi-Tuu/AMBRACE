# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.runtime_flag` -> `app.models.config.runtime_flag.py`
from app.models.config import RuntimeFlag

__all__ = [
    "RuntimeFlag",
]
