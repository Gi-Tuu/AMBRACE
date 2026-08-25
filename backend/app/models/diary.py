# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.diary` -> `app.models.life.diary.py`
from app.models.life.diary import AIDiary

__all__ = [
    "AIDiary",
]
