# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.vlm_config` -> `app.models.config.vlm_config.py`
from app.models.config.vlm_config import VlmConfig

__all__ = [
    "VlmConfig",
]
