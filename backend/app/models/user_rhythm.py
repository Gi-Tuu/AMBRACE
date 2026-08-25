# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.user_rhythm` -> `app.models.life.user_rhythm.py`
from app.models.life.user_rhythm import UserRhythm

__all__ = [
    "UserRhythm",
]
