# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.phone_desktop` -> `app.models.device.phone_desktop.py`
from app.models.device import PhoneDesktop, PhoneLayout, CalendarNote, BrowserHistory, MemoNote

__all__ = [
    "PhoneDesktop",
    "PhoneLayout",
    "CalendarNote",
    "BrowserHistory",
    "MemoNote",
]
