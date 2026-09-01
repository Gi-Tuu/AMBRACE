# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.user_dnd` -> `app.models.user.user_dnd.py`
from app.models.user import UserDndSettings

__all__ = [
    "UserDndSettings",
]
