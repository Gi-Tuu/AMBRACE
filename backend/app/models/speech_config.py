# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.speech_config` -> `app.models.config.speech_config.py`
from app.models.config.speech_config import SpeechConfig

__all__ = [
    "SpeechConfig",
]
