# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.daily_summary` -> `app.models.memory.daily_summary.py`
from app.models.memory.daily_summary import DailySummary

__all__ = [
    "DailySummary",
]
