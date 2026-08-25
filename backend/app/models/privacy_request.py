# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.privacy_request` -> `app.models.user.privacy_request.py`
from app.models.user.privacy_request import PrivacyRequest

__all__ = [
    "PrivacyRequest",
]
