# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.storyline_event` -> `app.models.character.storyline_event.py`
from app.models.character import StorylineEvent

__all__ = [
    "StorylineEvent",
]
