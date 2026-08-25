# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.world_fact` -> `app.models.memory.world_fact.py`
from app.models.memory.world_fact import WorldFact

__all__ = [
    "WorldFact",
]
