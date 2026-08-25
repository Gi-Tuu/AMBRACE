# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.ai_chat` -> `app.models.chat.ai_chat.py`
from app.models.chat.ai_chat import AIChat

__all__ = [
    "AIChat",
]
