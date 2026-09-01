# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.api_config` -> `app.models.config.api_config.py`
from app.models.config import ApiConfig

__all__ = [
    "ApiConfig",
]
