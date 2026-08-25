# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.marketplace_config` -> `app.models.config.marketplace_config.py`
from app.models.config.marketplace_config import MarketplaceConfig

__all__ = [
    "MarketplaceConfig",
]
