# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.state_trigger_log` -> `app.models.character.state_trigger_log.py`
from app.models.character import StateTriggerLog

__all__ = [
    "StateTriggerLog",
]
