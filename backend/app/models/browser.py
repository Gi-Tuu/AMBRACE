# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.browser` -> `app.models.user.browser.py`
from app.models.user import BrowserSnapshot

__all__ = [
    "BrowserSnapshot",
]
