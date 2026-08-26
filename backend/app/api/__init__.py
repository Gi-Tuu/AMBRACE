from app.api.characters import router as character_router
from app.api.chat import router as chat_router
from app.api.memories import router as memory_router
from app.api.system import router as system_router
from app.api.scheduler import router as scheduler_router
from app.api.scheduler import proactive_router
from app.api.diary import router as diary_router
from app.api.moments import router as moments_router
from app.api.uploads import router as uploads_router
from app.api.relationships import router as relationships_router
from app.api.pets import router as pets_router
from app.api.phone import router as phone_router
from app.api.phone_desktop import router as phone_desktop_router
from app.api.timeline import router as timeline_router
from app.api.images import router as images_router
from app.api.user_states import router as user_states_router
from app.api.user_content import router as user_content_router
from app.api.ai_chats import router as ai_chats_router
from app.api.emojis import router as emojis_router
from app.api.privacy import router as privacy_router
from app.api.user_location import router as user_location_router
from app.api.plugins import router as plugins_router
from app.api.plugin_bridge import router as plugin_bridge_router  # 48a：插件桥 API
from app.api.marketplace import router as marketplace_router
from app.api.voice import router as voice_router
from app.api.weave import router as weave_router
from app.api.permissions import router as permissions_router
from app.api.platform_profiles import router as platform_profiles_router
from app.api.chat_groups import router as chat_groups_router
from app.api.life import router as life_router
from app.api.life_home import router as life_home_router
from app.api.phone_workflows import router as phone_workflows_router
from app.api.admin import router as admin_router
from app.api.mcp import router as mcp_router  # AMBRACE MCP 接入（Phase 1）
from app.api.games import router as games_router  # 群聊游戏 Phase 1

__all__ = [
    "character_router", "chat_router", "memory_router",
    "system_router", "admin_router", "scheduler_router", "proactive_router",
    "diary_router", "moments_router", "uploads_router", "relationships_router", "pets_router", "phone_router", "timeline_router", "images_router", "user_states_router", "user_content_router", "ai_chats_router", "emojis_router", "privacy_router", "user_location_router", "plugins_router", "plugin_bridge_router", "marketplace_router", "phone_desktop_router", "voice_router", "weave_router", "permissions_router", "platform_profiles_router", "chat_groups_router", "life_router", "life_home_router", "phone_workflows_router", "mcp_router", "games_router",
]
