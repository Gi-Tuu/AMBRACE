# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.character_state_history` -> `app.models.character.state_history.py`
from app.models.character import CharacterStateHistory

__all__ = [
    "CharacterStateHistory",
]
