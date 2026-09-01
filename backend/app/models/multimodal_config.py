# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.multimodal_config` -> `app.models.config.multimodal_config.py`
from app.models.config import MultimodalConfig

__all__ = [
    "MultimodalConfig",
]
