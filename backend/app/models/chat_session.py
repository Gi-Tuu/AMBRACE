# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.chat_session` -> `app.models.chat.session.py`
from app.models.chat.session import ChatSession

__all__ = [
    "ChatSession",
]
