# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.character_state` -> `app.models.character.state.py`
from app.models.character.state import CharacterState

__all__ = [
    "CharacterState",
]
