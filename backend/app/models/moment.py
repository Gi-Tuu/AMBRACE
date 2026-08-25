# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.moment` -> `app.models.life.moment.py`
from app.models.life.moment import AIMoment, MomentLike, MomentAILike, MomentComment, MomentReadMark

__all__ = [
    "AIMoment",
    "MomentLike",
    "MomentAILike",
    "MomentComment",
    "MomentReadMark",
]
