# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.relationship_event` -> `app.models.character.relationship_event.py`
from app.models.character.relationship_event import RelationshipEvent

__all__ = [
    "RelationshipEvent",
]
