# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.lorebook_entry` -> `app.models.memory.lorebook.py`
from app.models.memory import LorebookEntry

__all__ = [
    "LorebookEntry",
]
