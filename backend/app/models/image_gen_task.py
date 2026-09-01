# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.image_gen_task` -> `app.models.life.image_gen_task.py`
from app.models.life import ImageGenTask

__all__ = [
    "ImageGenTask",
]
