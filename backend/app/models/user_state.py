# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.user_state` -> `app.models.user.user_state.py`
from app.models.user.user_state import UserState

__all__ = [
    "UserState",
]
