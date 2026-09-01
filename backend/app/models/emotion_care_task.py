# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.emotion_care_task` -> `app.models.agent.emotion_care_task.py`
from app.models.agent import EmotionCareTask

__all__ = [
    "EmotionCareTask",
]
