# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.processed_extraction` -> `app.models.memory.processed_extraction.py`
from app.models.memory import ProcessedExtraction

__all__ = [
    "ProcessedExtraction",
]
