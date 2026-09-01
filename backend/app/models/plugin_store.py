# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.plugin_store` -> `app.models.plugin.plugin_store.py`
from app.models.plugin import PluginStore

__all__ = [
    "PluginStore",
]
