# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.chat_group` -> `app.models.chat.group.py`
from app.models.chat.group import ChatGroup, ChatGroupMember, ChatGroupMessage

__all__ = [
    "ChatGroup",
    "ChatGroupMember",
    "ChatGroupMessage",
]
