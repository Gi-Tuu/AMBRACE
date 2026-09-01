# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.conversation_topic` -> `app.models.memory.conversation_topic.py`
from app.models.memory import ConversationTopic

__all__ = [
    "ConversationTopic",
]
