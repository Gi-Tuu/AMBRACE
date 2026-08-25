"""用户画像：注入所有 LLM 场景，明确用户性别/对象/与各角色的关系，消除刻板印象"""
from sqlalchemy import select
from app.db.database import async_session_factory
from app.models.user import User
from app.models.character import AICharacter

_GENDER_CN = {"male": "男", "female": "女", "男": "男", "女": "女"}


def gender_cn(gender: str | None) -> str:
    return _GENDER_CN.get((gender or "").strip().lower(), "未设置")


async def build_user_profile_text(user_id: int = 1) -> str:
    """构建用户画像文本（昵称/性别/对象/与各 AI 角色的关系）"""
    async with async_session_factory() as db:
        user = await db.get(User, user_id)
        chars_result = await db.execute(
            select(AICharacter).where(AICharacter.user_id == user_id)
        )
        chars = list(chars_result.scalars().all())

    lines = []
    name = (user.nickname or user.username) if user else "用户"
    gender = gender_cn(user.gender) if user else "未设置"
    lines.append(f"用户昵称: {name}")
    lines.append(f"用户性别: {gender}")

    partners = [c for c in chars if c.is_partner]
    if partners:
        p = partners[0]
        lines.append(f"用户的对象: {p.name}（{gender_cn(p.gender)}）")

    rel_parts = []
    for c in chars:
        if not c.is_active:
            continue
        if c.is_partner:
            rel_parts.append(f"{c.name}=对象/伴侣")
        else:
            rel_parts.append(f"{c.name}={c.relation_type or '朋友'}")
    if rel_parts:
        lines.append("用户与AI好友的关系: " + "；".join(rel_parts))

    return "\n".join(lines)


async def build_user_notes_text(user_id: int = 1) -> str:
    """用户备忘录 + 最近日记文本（供聊天 context 注入，控制 token 上限）"""
    from app.models.user_memo import UserMemo
    from app.models.user_diary import UserDiary
    async with async_session_factory() as db:
        memos = (await db.execute(
            select(UserMemo).where(UserMemo.user_id == user_id)
            .order_by(UserMemo.updated_at.desc()).limit(10)
        )).scalars().all()
        diaries = (await db.execute(
            select(UserDiary).where(UserDiary.user_id == user_id)
            .order_by(UserDiary.diary_date.desc()).limit(3)
        )).scalars().all()

    lines = []
    if memos:
        lines.append("用户的备忘录：")
        for m in memos:
            lines.append(f"- [记录于 {str(m.updated_at)[:10]}] {m.title or '未命名'}：{m.content[:200]}")
    else:
        lines.append("用户的备忘录：无")
    if diaries:
        lines.append("用户的最近日记：")
        for d in diaries:
            lines.append(f"- {d.diary_date}：{d.content[:400]}")
    else:
        lines.append("用户的最近日记：无")
    return "\n".join(lines)


async def build_role_prompt_block(char, user_id: int = 1) -> str:
    """统一身份块：性格 + 聊天风格 + 角色性别 + 用户性别/对象 + 关系行。

    供状态触发/剧情线/八维状态评估/生日节日消息/主动通道（情绪关怀/记忆复习/
    宠物提醒等）独立 LLM 场景注入，明确"用户的对象是谁、自己是不是用户的对象"，
    防止身份/对象混淆；并始终带角色性格与聊天风格，保证主动消息同人格。
    """
    lines = []
    lines.append(f"你的性格：{char.personality or '友善'}")
    lines.append(f"你的聊天风格：{char.chat_style or '自然'}")
    lines.append(f"你的性别：{gender_cn(char.gender)}")

    async with async_session_factory() as db:
        user = await db.get(User, user_id)
        partners = (
            await db.execute(select(AICharacter).where(
                AICharacter.user_id == user_id, AICharacter.is_partner == True
            ))
        ).scalars().all()
    uname = (user.nickname or user.username) if user else "用户"
    ugender = gender_cn(user.gender) if user else "未设置"
    lines.append(f"用户昵称：{uname}（性别：{ugender}）")
    if partners:
        p = partners[0]
        if p.id == char.id:
            lines.append(f"用户的对象是你（{gender_cn(p.gender)}）——你是用户的爱人/伴侣")
        else:
            lines.append(f"用户的对象是{p.name}（{gender_cn(p.gender)}）——{p.name}是用户的爱人，不是你本人")
    else:
        lines.append("用户当前没有登记对象")
    if char.is_partner:
        rel = f"你和用户是对象/伴侣关系。{char.relationship_summary or ''}".strip()
        lines.append(f"你的角色定位：{rel}")
    else:
        rt = char.relation_type or "朋友"
        lines.append(f"你的角色定位：{rt}（不是用户的对象）。{char.relationship_summary or ''}".strip())
    return "\n".join(lines)


async def get_user_nickname(user_id: int = 1) -> str:
    """获取用户昵称（取不到返回通用词"用户"）"""
    async with async_session_factory() as db:
        user = await db.get(User, user_id)
    return (user.nickname or user.username) if user else "用户"


async def build_relation_line(char) -> str:
    """角色与用户的关系描述（供日记/朋友圈等 prompt 单行注入）"""
    if char.is_partner:
        summary = char.relationship_summary or ""
        return f"你和用户是对象/伴侣关系。{summary}".strip()
    rt = char.relation_type or "朋友"
    summary = char.relationship_summary or ""
    return f"你和用户的关系：{rt}。{summary}".strip()
