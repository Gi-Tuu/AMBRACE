# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.timeline_event` -> `app.models.life.timeline_event.py`
from app.models.life import TimelineEvent

__all__ = [
    "TimelineEvent",
]
