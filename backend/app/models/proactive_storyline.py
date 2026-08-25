# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.proactive_storyline` -> `app.models.character.proactive_storyline.py`
from app.models.character.proactive_storyline import ProactiveStorylineItem

__all__ = [
    "ProactiveStorylineItem",
]
