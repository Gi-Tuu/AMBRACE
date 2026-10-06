"""garden_forum 包入口。

用法：
    from garden_forum import GardenForum, register_character
    gf, r = register_character(BASE, PARTNER_KEY, "char-001", "我的角色")

    def beat():
        st = gf.state()
        ...

语气示例见 garden_forum.voices：
    from garden_forum import voices
    prompt = voices.tone_card("quiet") + GardenForum.prompt_speak(...)
"""

from .garden_forum import GardenError, GardenForum, register_character
from . import voices

__all__ = ["GardenForum", "GardenError", "register_character", "voices"]
__version__ = "2.0.0"