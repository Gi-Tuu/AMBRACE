# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.douyin` -> `app.models.social.douyin.py`
from app.models.social.douyin import DouyinAccount, DouyinPost, DouyinComment, DouyinPending, DouyinViewedNote

__all__ = [
    "DouyinAccount",
    "DouyinPost",
    "DouyinComment",
    "DouyinPending",
    "DouyinViewedNote",
]
