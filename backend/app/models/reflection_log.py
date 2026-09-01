# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.reflection_log` -> `app.models.memory.reflection_log.py`
from app.models.memory import ReflectionLog

__all__ = [
    "ReflectionLog",
]
