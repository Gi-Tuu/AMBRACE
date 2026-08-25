# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.phone_snapshot` -> `app.models.device.phone_snapshot.py`
from app.models.device.phone_snapshot import PhoneSnapshot, CheckInRequest

__all__ = [
    "PhoneSnapshot",
    "CheckInRequest",
]
