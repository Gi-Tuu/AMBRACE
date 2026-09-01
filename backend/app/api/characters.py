"""AI 角色管理 API——F5 瘦身后只留 FastAPI 壳：收参 → 调 application 服务 → 返回。

业务体在 app/application/characters.py（F5-b，2026-08-31 迁入）；本文件保留路由与
参数依赖注入，并对历史顶层名字做门面重导出保旧 import 路径兼容（F8 删旧时移除）。
"""
from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.application import characters as _svc
from app.application.characters import (  # F5 门面重导出（历史名字保兼容，F8 删旧时移除）
    _LorebookUpsert,
    _WorldFactCreate,
    _build_system_prompt,  # noqa: F401
    _generate_greeting_text,  # noqa: F401
    _get_owned_character,  # noqa: F401
    _log_to_goal,  # noqa: F401
    _steps_returned_count,  # noqa: F401
    _summarize_task_logs,  # noqa: F401
)
from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.schemas.character import (
    CharacterCreate,
    CharacterListResponse,
    CharacterResponse,
    CharacterUpdate,
)

router = APIRouter(prefix="/api/v1/characters", tags=["Characters"])


@router.post("", response_model=CharacterResponse, status_code=status.HTTP_201_CREATED)
async def create_character(
    data: CharacterCreate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """创建新 AI 角色"""
    return await _svc.create_character(db, data, user_id)


@router.post("/{character_id}/generate-greeting")
async def generate_greeting(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """为角色生成一句符合人设的开场白（LLM），写回 greeting_message（主账号/本人）"""
    return await _svc.generate_greeting(db, character_id, user_id, lang)


@router.get("", response_model=CharacterListResponse)
async def list_characters(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """获取当前用户的 AI 角色列表"""
    return await _svc.list_characters(db, user_id)


@router.get("/{character_id}/states")
async def get_character_states(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """获取角色八维可视化状态（心情/体温/性欲/占有欲/疲惫感/敏感度/舒适感/怒气值，0-100）"""
    return await _svc.get_character_states(db, character_id, user_id, lang)


@router.get("/{character_id}", response_model=CharacterResponse)
async def get_character(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """获取单个 AI 角色详情"""
    return await _svc.get_character(db, character_id, user_id, lang)


@router.put("/{character_id}", response_model=CharacterResponse)
async def update_character(
    character_id: int,
    data: CharacterUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """修改 AI 角色信息"""
    return await _svc.update_character(db, character_id, data, user_id, lang)


@router.delete("/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_character(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """删除 AI 角色（硬删除：清空全部关联数据 + 删除角色行，用户要求"删除角色=完全清除"）"""
    await _svc.delete_character(db, character_id, user_id, lang)


@router.get("/{character_id}/emotion-timeline")
async def get_emotion_timeline(
    character_id: int,
    days: int = 7,
    dimension: str | None = None,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """状态情绪记忆时间线（只读，零 LLM）：情绪记忆 + 状态触发日志 + 剧情线事件三源合并"""
    return await _svc.get_emotion_timeline(db, character_id, user_id, lang, days, dimension)


@router.get("/{character_id}/agent-mind")
async def get_agent_mind(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """AI 内心世界（Phase J/P1，2026-08-16）：最近复盘 + 任务记录 + 工具使用轨迹"""
    return await _svc.get_agent_mind(db, character_id, user_id)


@router.get("/{character_id}/lorebook")
async def list_lorebook(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """Lorebook 条目列表（P1-2）：角色拥有的关键词触发设定"""
    return await _svc.list_lorebook(db, character_id, user_id, lang)


@router.post("/{character_id}/lorebook")
async def create_lorebook(
    character_id: int,
    data: _LorebookUpsert,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """新建 Lorebook 条目（P1-2；P2-5 Journal 化：每角色条目上限防失控）"""
    return await _svc.create_lorebook(db, character_id, data, user_id, lang)


@router.put("/{character_id}/lorebook/{entry_id}")
async def update_lorebook(
    character_id: int,
    entry_id: int,
    data: _LorebookUpsert,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """更新 Lorebook 条目（P1-2）"""
    return await _svc.update_lorebook(db, character_id, entry_id, data, user_id, lang)


@router.delete("/{character_id}/lorebook/{entry_id}")
async def delete_lorebook(
    character_id: int,
    entry_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """删除 Lorebook 条目（P1-2）"""
    return await _svc.delete_lorebook(db, character_id, entry_id, user_id, lang)


@router.get("/{character_id}/world-facts")
async def list_world_facts(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """世界事实列表（P1-3）：活跃事实，含作者与权威标记（用户定义的不可动摇设定优先展示）"""
    return await _svc.list_world_facts(db, character_id, user_id, lang)


@router.post("/{character_id}/world-facts")
async def create_world_fact(
    character_id: int,
    data: _WorldFactCreate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """创建用户定义的权威世界设定（P1-3）：不可动摇事实，AI 推断不能覆盖"""
    return await _svc.create_world_fact(db, character_id, data, user_id, lang)


@router.delete("/{character_id}/world-facts/{fact_id}")
async def delete_world_fact(
    character_id: int,
    fact_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """删除世界事实（P1-3）：仅用户自己创建的权威设定可删（系统/聊天折叠事实不可删，防误操作）"""
    return await _svc.delete_world_fact(db, character_id, fact_id, user_id, lang)


@router.get("/{character_id}/state-history")
async def get_state_history(
    character_id: int,
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """八维状态历史快照（Phase 2）：每次聊天评估后的状态，按时间倒序，供情绪曲线/蛛网对比"""
    return await _svc.get_state_history(db, character_id, user_id, lang, days)


@router.get("/{character_id}/memory-trace")
async def get_memory_trace(
    character_id: int,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """#70-B：最近的记忆检索轨迹（只读），供调试面板查看「AI 这一轮想起了什么、为什么」"""
    return await _svc.get_memory_trace(db, character_id, user_id, lang, limit)
