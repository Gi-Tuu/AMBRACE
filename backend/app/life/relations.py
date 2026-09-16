"""AI Life 亲属/重要人物守卫（批次三 P0-5 止血，2026-09-16）。

问题：活动内容/记忆模板反复凭空生成「陪妈妈逛菜市场、陪母亲散步、和家人晚饭」，
导致 sam 凭空多出同住母亲。

止血策略（改动尽量小，不引入新表/新服务）：
- 活动模板**只能引用用户明确设定过的人物**：名单里没有的亲属称谓一律判为凭空生成。
- 家人/亲属名单**缺省为空** → 角色没有任何「明确设定过的家人」时，绝不生成任何亲属
  相关活动与记忆。
- 唯一默认可信的是「用户本人」：``resolve_known_people`` 返回用户昵称/用户名，以及
  角色设定里明确写出的关系称谓（如 relationship_summary 写了「女朋友/老公」，则允许
  该称谓，避免误伤对用户的正常指代）。
- 已生成错误记忆不删（归批次一/数据清理）。
- 扩展点：将来接入「用户明确设定的家人」（如新列/关系配置）时，只需在此返回真实名单，
  守卫即自动放行。
"""
from __future__ import annotations

# 亲属/家人称谓（多字优先，规避「母校/干妈」等误伤由显式词表规避）
_RELATION_TERMS: tuple[str, ...] = (
    "妈妈", "老妈", "母亲", "老父亲", "父亲", "爸爸", "老爸",
    "爷爷", "奶奶", "外公", "外婆", "姥姥", "姥爷",
    "祖父", "祖母", "外祖父", "外祖母",
    "哥哥", "姐姐", "弟弟", "妹妹", "兄长",
    "儿子", "女儿", "老婆", "老公", "媳妇", "女婿",
    "嫂子", "姐夫", "堂哥", "堂姐", "表哥", "表姐",
    "叔叔", "阿姨", "舅舅", "姨妈", "姑姑", "婶婶",
    "家人", "亲戚", "亲属", "家父", "家母",
    "令堂", "令尊",
)

# 对「用户本人」的合法指代：只有角色设定里明确写过，才视为用户设定过的人物
_PARTNER_TERMS: tuple[str, ...] = (
    "老公", "老婆", "媳妇", "丈夫", "妻子", "男朋友", "女朋友",
    "对象", "恋人", "伴侣", "未婚夫", "未婚妻",
)


def mentions_unspecified_relation(content: str, known: list[str] | None = None) -> bool:
    """内容是否提及「未明确设定」的亲属/家人。

    - ``known`` 为空（默认）→ 任何亲属词都视为凭空生成，返回 True。
    - ``known`` 非空 → 仅当提及的称谓不在名单内才判 True（名单内属用户真实设定）。
    """
    if not content:
        return False
    known = [k for k in (known or []) if k]
    for term in _RELATION_TERMS:
        if term in content:
            # 命中亲属词：若它恰好是用户明确设定的人物名/称谓，则放行
            if any(term == k or term in k for k in known):
                continue
            return True
    return False


async def resolve_known_people(db, character_id: int) -> list[str]:
    """读取角色「明确设定过的重要人物」名单。

    当前唯一可信来源：
    - 用户本人（昵称 / 用户名）；
    - 角色 ``relationship_summary`` / ``relation_type`` 中明确写出的关系称谓
      （如「你们的对象/伴侣关系」→ 允许「老公/老婆」等对用户的指代）。

    家人/亲属：**没有任何数据源时默认空列表** → 守卫拒绝一切凭空亲属。
    后续接入真实家人设定时，仅在此返回真实名单即可，调用方（activity.py / life_loop.py）无需改动。
    查询失败静默返回 []（止血优先：宁可少写，不可编造）。
    """
    people: list[str] = []
    try:
        from app.models.character import AICharacter

        char = await db.get(AICharacter, character_id)
        if char is None:
            return []
        user_id = getattr(char, "user_id", None)
        if user_id:
            try:
                from app.models.user import User

                user = await db.get(User, user_id)
                if user is not None:
                    for v in (getattr(user, "nickname", None), getattr(user, "username", None)):
                        if v and str(v).strip():
                            people.append(str(v).strip())
            except Exception:
                pass
        rel_text = f"{getattr(char, 'relationship_summary', '') or ''} {getattr(char, 'relation_type', '') or ''}"
        for term in _PARTNER_TERMS:
            if term in rel_text:
                people.append(term)
    except Exception:
        return []
    return people
