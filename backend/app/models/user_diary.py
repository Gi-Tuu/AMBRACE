# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.user_diary` -> `app.models.life.user_diary.py`
from app.models.life import UserDiary

__all__ = [
    "UserDiary",
]
