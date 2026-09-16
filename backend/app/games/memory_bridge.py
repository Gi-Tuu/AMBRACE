"""游戏记忆桥接。

游戏结束时：
1. 写 game_memories（每 AI 角色一条，含可见流水+个人行动+模板总结）——逻辑隔离，不进主记忆检索；
2. 写主 memories 一条摘要指针（source="game", sub_type="game_summary", importance=35）：
   不含手牌/发言/他人身份，只记"玩了什么"，作为按需调取接口；
3. 每角色主记忆只保留最近 5 条 game_summary 指针，超出软删最旧（is_archived=True）。

群聊记忆跳过游戏消息在 api/chat_groups._save_group_memory 里过滤（msg_type != normal）。
"""
from __future__ import annotations

import json

from sqlalchemy import select

from app.utils.logger import get_logger

_logger = get_logger("games.memory_bridge")

_SUMMARY_KEEP = 5


async def finalize_game(db, session, engine) -> None:
    """游戏结束时调用。写游戏记忆库 + 主记忆摘要指针（每 AI 角色）。"""
    ai_players = [p for p in engine.players if p.player_type == "ai" and not p.is_spectator and p.character_id]
    from app.models.game import GameMemory

    # P1-8（2026-09-16）：统一角色第一人称——取各 AI 角色名，拼装时显式「我（角色名）」自指，
    # 避免跨角色库时「我」指代错乱（live #10515 在角色库里误用用户视角）。
    char_names: dict[int, str] = {}
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.character import AICharacter
        async with async_session_factory() as _ndb:
            rows = (await _ndb.execute(
                select(AICharacter.id, AICharacter.name)
                .where(AICharacter.id.in_(p.character_id for p in ai_players))
            )).all()
        char_names = {r[0]: r[1] for r in rows}
    except Exception as e:
        _logger.warning("game char names load failed: %s", e)

    for p in ai_players:
        view = engine.view_for(p.seat)
        pub_events = engine.public_events_for(p.seat)
        my_actions = engine.my_events(p.seat)
        summary = _build_summary_pointer(session, p, engine, char_names.get(p.character_id))
        try:
            db.add(GameMemory(
                session_id=session.id,
                character_id=p.character_id,
                my_role=p.role,
                my_word=view.private.get("word", ""),
                result="won" if _player_won(p, session, engine) else "lost",
                survived_rounds=int(session.round or 0),
                public_events_json=json.dumps(pub_events, ensure_ascii=False),
                my_actions_json=json.dumps(my_actions, ensure_ascii=False),
                summary=summary,
            ))
        except Exception as e:
            _logger.warning("game_memory write failed char=%d: %s", p.character_id, e)

    # 2. 主记忆摘要指针（每 AI 角色 1 条）
    for p in ai_players:
        summary = _build_summary_pointer(session, p, engine, char_names.get(p.character_id))
        try:
            from app.memory.service import save_memory
            await save_memory(
                user_id=session.user_id, character_id=p.character_id,
                memory_type="event", content=summary,
                importance=35.0, sub_type="game_summary",
                source="game", source_id=session.id,
                speaker_type="character", speaker_id=p.character_id,
                epistemic_status="FACT",
                skip_dedup=True,
            )
        except Exception as e:
            _logger.warning("game memory pointer failed char=%d: %s", p.character_id, e)

    # 3. 每角色主记忆最多保留 5 条 game_summary（超出软删最旧）
    for p in ai_players:
        try:
            await _trim_summary_pointers(db, p.character_id)
        except Exception as e:
            _logger.warning("game summary trim failed char=%d: %s", p.character_id, e)

    await db.commit()


async def _trim_summary_pointers(db, character_id: int) -> None:
    """保留该角色最近 _SUMMARY_KEEP 条 game_summary，其余软删（is_archived=True）。"""
    from app.models.memory import Memory
    rows = (await db.execute(
        select(Memory)
        .where(
            Memory.character_id == character_id,
            Memory.source == "game",
            Memory.sub_type == "game_summary",
            Memory.is_archived == False,  # noqa: E712
        )
        .order_by(Memory.id.desc())
        .execution_options(autoflush=False)  # B5：SELECT 不触发 autoflush（脏对象含未序列化引擎内存态时防误 flush）
    )).scalars().all()
    if len(rows) <= _SUMMARY_KEEP:
        return
    for r in rows[_SUMMARY_KEEP:]:
        r.is_archived = True
        db.add(r)


def _build_summary_pointer(session, player, engine, character_name: str | None = None) -> str:
    """零 LLM 模板：生成主记忆摘要指针（不含手牌/发言/他人身份）。

    P1-8（2026-09-16）：统一角色第一人称——以「我（角色名）」显式自指，消除跨角色库时
    「我」指代错乱（live #10515 在角色库里误用用户视角自称）。角色名缺失时回退「我」。
    """
    meta = engine.meta()
    # 参与方名单排除自己（自己用「我（角色名）」自指，避免重复且消除跨库指代歧义）
    names = "、".join(
        engine.name_of(p.seat) for p in engine.players
        if not p.is_spectator and getattr(p, "seat", None) != player.seat
    )
    won = _player_won(player, session, engine)
    result_text = "赢了" if won else "输了"
    me = f"我（{character_name}）" if character_name else "我"
    if session.game_type == "undercover":
        survived = "坚持到了最后" if player.alive else f"在第{int(session.round or 0)}轮被淘汰"
        return f"{me}和{names}一起玩了谁是卧底（{len(engine.players)}人局），{result_text}，{survived}。"
    if session.game_type == "truth_or_dare":
        return f"{me}和{names}玩了真心话大冒险，{result_text}。"
    if session.game_type == "twenty_q":
        return f"{me}和{names}玩了猜词20问，{result_text}。"
    if session.game_type == "werewolf":
        role_text = {"wolf": "狼人", "seer": "预言家", "villager": "村民"}.get(player.role, player.role)
        return f"{me}和{names}玩了狼人杀（{len(engine.players)}人局），抽到「{role_text}」，{result_text}。"
    if session.game_type == "liars_bar":
        return f"{me}和{names}玩了骗子酒馆，{result_text}。"
    if session.game_type == "turtle_soup":
        role_text = "当主持人" if player.role == "thinker" else "猜题"
        return f"{me}和{names}玩了海龟汤，{role_text}，{result_text}。"
    return f"{me}玩了{meta['name']}，{result_text}。"


def _player_won(player, session, engine) -> bool:
    from app.games.archive import _player_won as _pw
    return _pw(player, session, engine)
