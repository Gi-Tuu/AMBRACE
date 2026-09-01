# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.chat_message` -> `app.models.chat.message.py`
from app.models.chat import ChatMessage

__all__ = [
    "ChatMessage",
]
