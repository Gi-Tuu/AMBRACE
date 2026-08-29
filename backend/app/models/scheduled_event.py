# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.scheduled_event` -> `app.models.life.scheduled_event.py`
from app.models.life.scheduled_event import ScheduledEvent

__all__ = [
    "ScheduledEvent",
]
