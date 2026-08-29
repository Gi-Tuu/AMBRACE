# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.pet_activity` -> `app.models.pet.pet_activity.py`
from app.models.pet.pet_activity import PetActivity

__all__ = [
    "PetActivity",
]
