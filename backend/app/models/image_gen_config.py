# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.image_gen_config` -> `app.models.life.image_gen_config.py`
from app.models.life.image_gen_config import ImageGenConfig

__all__ = [
    "ImageGenConfig",
]
