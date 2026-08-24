from app.models.base import Base
from app.models.user import User
from app.models.character import AICharacter
from app.models.chat_session import ChatSession
from app.models.chat_message import ChatMessage
from app.models.memory import Memory
from app.models.daily_summary import DailySummary
from app.models.proactive_settings import ProactiveSettings, HolidayPreference, ProactiveMessageLog
from app.models.diary import AIDiary
from app.models.moment import AIMoment, MomentLike
from app.models.scheduled_event import ScheduledEvent
from app.models.proactive_storyline import ProactiveStorylineItem
from app.models.processed_extraction import ProcessedExtraction
from app.models.api_config import ApiConfig
from app.models.image_gen_task import ImageGenTask
from app.models.image_gen_config import ImageGenConfig
from app.models.vlm_config import VlmConfig
from app.models.speech_config import SpeechConfig
from app.models.multimodal_config import MultimodalConfig
from app.models.pet import Pet
from app.models.character_state import CharacterState
from app.models.state_trigger_log import StateTriggerLog
from app.models.storyline_event import StorylineEvent
from app.models.phone_snapshot import PhoneSnapshot
from app.models.phone_auto_state import PhoneAutoState
from app.models.timeline_event import TimelineEvent
from app.models.user_dnd import UserDndSettings
from app.models.user_state import UserState
from app.models.emotion_care_task import EmotionCareTask
from app.models.user_memo import UserMemo
from app.models.user_diary import UserDiary
from app.models.ai_chat import AIChat
from app.models.pet_activity import PetActivity
from app.models.emoji_pack import UserEmojiPack, UserCustomEmoji
from app.models.character_state_history import CharacterStateHistory
from app.models.privacy_request import PrivacyRequest
from app.models.reflection_log import ReflectionLog
from app.models.conversation_topic import ConversationTopic
from app.models.relationship_event import RelationshipEvent
from app.models.stage_memory import StageMemory
from app.models.plugin import Plugin
from app.models.plugin_store import PluginStore  # 48a：插件命名空间 KV 存储
from app.models.phone_desktop import PhoneDesktop, PhoneLayout, CalendarNote, BrowserHistory, MemoNote
from app.models.llm_usage import LlmUsage, LlmUsageLimit
from app.models.agent_task_log import AgentTaskLog
from app.models.agent_task import AgentTask
from app.models.user_workflow import UserWorkflow

__all__ = [
    "Base", "User", "AICharacter", "ChatSession", "ChatMessage",
    "Memory", "DailySummary", "ProactiveSettings", "HolidayPreference",
    "ProactiveMessageLog", "AIDiary", "AIMoment", "MomentLike", "ScheduledEvent", "ProactiveStorylineItem", "ProcessedExtraction", "ApiConfig", "ImageGenTask", "ImageGenConfig", "VlmConfig", "SpeechConfig", "MultimodalConfig", "Pet", "CharacterState", "PhoneSnapshot", "PhoneAutoState", "TimelineEvent", "StateTriggerLog", "StorylineEvent", "UserDndSettings", "UserState", "EmotionCareTask", "UserMemo", "UserDiary", "AIChat", "PetActivity", "UserEmojiPack", "UserCustomEmoji", "CharacterStateHistory", "ReflectionLog", "ConversationTopic", "PrivacyRequest", "RelationshipEvent", "StageMemory", "Plugin", "PhoneDesktop", "PhoneLayout", "CalendarNote", "BrowserHistory", "MemoNote",
    "LlmUsage", "LlmUsageLimit", "AgentTaskLog", "AgentTask", "UserWorkflow",
    "PluginStore",
    "WeaveCard", "WeaveCardCharacter", "WeaveCardMemory",
    "ToolPermission", "PendingPermissionAction",
]
from app.models.douyin import DouyinAccount, DouyinPost, DouyinComment, DouyinPending  # noqa: F401 模型注册
from app.models.social import PlatformProfile, SocialMemory  # noqa: F401 模型注册
from app.models.weave_card import WeaveCard, WeaveCardCharacter, WeaveCardMemory  # noqa: F401 模型注册
from app.models.tool_permission import ToolPermission, PendingPermissionAction  # noqa: F401 模型注册
from app.models.task_llm_config import TaskLlmConfig  # noqa: F401 模型注册
from app.models.marketplace_config import MarketplaceConfig  # noqa: F401 模型注册

from app.models.chat_group import ChatGroup, ChatGroupMember, ChatGroupMessage  # noqa: F401
from app.models.life import LifeState, LifeActivityLog, LifeArtifact, LifeInterest, LifeGoal, LifeSchedule  # noqa: F401
from app.models.shared_event import SharedEvent  # noqa: F401
from app.models.world_fact import WorldFact  # noqa: F401
from app.models.lorebook_entry import LorebookEntry  # noqa: F401
from app.models.runtime_flag import RuntimeFlag  # noqa: F401
from app.models.user_rhythm import UserRhythm  # noqa: F401
from app.models.browser import BrowserSnapshot  # noqa: F401
