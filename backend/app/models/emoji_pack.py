# -*- coding: utf-8 -*-
# AMBRACE step7: models domain subpackage (Grouping Strategy A)

# Compatibility shim: old module path `app.models.emoji_pack` -> `app.models.life.emoji_pack.py`
from app.models.life.emoji_pack import UserEmojiPack, UserCustomEmoji

__all__ = [
    "UserEmojiPack",
    "UserCustomEmoji",
]
