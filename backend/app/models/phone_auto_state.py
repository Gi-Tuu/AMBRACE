# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.phone_auto_state` -> `app.models.device.phone_auto_state.py`
from app.models.device import PhoneAutoState

__all__ = [
    "PhoneAutoState",
]
