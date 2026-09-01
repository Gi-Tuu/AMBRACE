# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.proactive_settings` -> `app.models.character.proactive_settings.py`
from app.models.character import ProactiveSettings, HolidayPreference, ProactiveMessageLog, ProactiveTriggerLog

__all__ = [
    "ProactiveSettings",
    "HolidayPreference",
    "ProactiveMessageLog",
    "ProactiveTriggerLog",
]
