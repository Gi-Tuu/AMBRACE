# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.stage_memory` -> `app.models.memory.stage_memory.py`
from app.models.memory import StageMemory

__all__ = [
    "StageMemory",
]
