# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.shared_event` -> `app.models.memory.shared_event.py`
from app.models.memory import SharedEvent

__all__ = [
    "SharedEvent",
]
