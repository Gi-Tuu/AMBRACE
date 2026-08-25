# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.user_memo` -> `app.models.life.user_memo.py`
from app.models.life.user_memo import UserMemo

__all__ = [
    "UserMemo",
]
