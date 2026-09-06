# -*- coding: utf-8 -*-
"""wechat_ilink_messages 表结构幂等自愈（2026-09-06 待排期清理包 A）。

背景（2026-09-05 续29 运维修复的固化）：旧版部署的流水表是全量 UNIQUE(binding_id, ilink_msg_id)
（约束 uq_wechat_ilink_msg），而 models.py 早已是 partial unique（WHERE ilink_msg_id != ''）——
出站流水以空 ilink_msg_id 落多条 out 行时撞全量唯一 → relay 500。生产已手工重建，本模块把
同款幂等自愈写进代码，防其它部署/旧库复现。

设计：
- 自愈 = 检测（无表/已是 partial → 跳过）→ 反射重建（列结构原样、去全量唯一、按 models 目标
  建 partial unique `uq_wechat_ilink_msg_in` + binding_id/character_id/created_at 索引）→ 数据全量搬运；
- 进程内 once 标记：每个进程生命周期最多执行一轮探测（重启/重载后重新探测一次，幂等无副作用）；
- fail-open：任何异常只记日志不抛出（绝不阻断 relay/poll 主链路）；
- 由 inbound.poll_once 与 routes.bridge_relay_impl / bridge_delivery_impl 入口调用（插件自洽，
  内核 migrate 不建插件表）。
"""
from __future__ import annotations

_HEAL_DONE = False

_TARGET_COLUMNS = (  # (name, ddl 片段)；id 由反射保持 PK
    "binding_id", "character_id", "ilink_msg_id", "context_token",
    "direction", "content", "quota_charged", "status", "created_at",
)


def _log(msg: str) -> None:
    try:
        from app.plugins import sdk  # noqa: PLC0415
        sdk.log("wechat_ilink schema_heal: %s", msg)
    except Exception:
        pass


async def ensure_messages_schema(session_factory=None) -> str:
    """幂等自愈入口。返回动作：no_table / ok(partial 已是目标形态) / rebuilt(重建过) / error。

    session_factory 缺省用 app.db.database.async_session_factory（测试可注入）。
    """
    global _HEAL_DONE
    if _HEAL_DONE:
        return "ok"
    from sqlalchemy import text  # noqa: PLC0415

    if session_factory is None:
        from app.db.database import async_session_factory as session_factory  # noqa: PLC0415
    try:
        async with session_factory() as db:
            exists = (await db.execute(text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wechat_ilink_messages'"
            ))).first()
            if exists is None:
                return "no_table"  # 不置 once：表可能稍后由 create_all 建立，保持可重探

            # 目标形态判定：uq_wechat_ilink_msg_in 存在且 sql 含 WHERE（partial）
            row = (await db.execute(text(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_wechat_ilink_msg_in'"
            ))).first()
            if row is not None and row[0] and "WHERE" in str(row[0]).upper():
                _HEAL_DONE = True
                return "ok"

            # 反射列结构（保持数据；id 保持 INTEGER PRIMARY KEY）
            cols = (await db.execute(text("PRAGMA table_info(wechat_ilink_messages)"))).fetchall()
            if not cols:
                return "no_table"
            col_defs, col_names = [], []
            for _cid, name, ctype, notnull, dflt, _pk in cols:
                col_names.append(name)
                if name == "id":
                    col_defs.append('"id" INTEGER NOT NULL PRIMARY KEY')
                    continue
                d = f'"{name}" {ctype or "TEXT"}'
                if notnull:
                    d += " NOT NULL"
                if dflt is not None:
                    d += f" DEFAULT {dflt}"
                col_defs.append(d)

            await db.execute(text("ALTER TABLE wechat_ilink_messages RENAME TO wechat_ilink_messages_heal_old"))
            await db.execute(text(
                f'CREATE TABLE wechat_ilink_messages ({",".join(col_defs)})'
            ))
            await db.execute(text(
                f'INSERT INTO wechat_ilink_messages ({",".join(chr(34) + n + chr(34) for n in col_names)})'
                f' SELECT {",".join(chr(34) + n + chr(34) for n in col_names)}'
                f' FROM wechat_ilink_messages_heal_old'
            ))
            await db.execute(text("DROP TABLE wechat_ilink_messages_heal_old"))
            # models 目标索引（partial 唯一 + 常用查询索引；已存在则忽略）
            for ddl in (
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_wechat_ilink_msg_in ON wechat_ilink_messages"
                " (binding_id, ilink_msg_id) WHERE ilink_msg_id != ''",
                "CREATE INDEX IF NOT EXISTS ix_wechat_ilink_messages_binding_id"
                " ON wechat_ilink_messages (binding_id)",
                "CREATE INDEX IF NOT EXISTS ix_wechat_ilink_messages_character_id"
                " ON wechat_ilink_messages (character_id)",
                "CREATE INDEX IF NOT EXISTS ix_wechat_ilink_messages_created_at"
                " ON wechat_ilink_messages (created_at)",
            ):
                try:
                    await db.execute(text(ddl))
                except Exception:
                    pass
            await db.commit()
        _HEAL_DONE = True
        _log("wechat_ilink_messages 旧全量唯一已重建为 partial unique（数据完整保留）")
        return "rebuilt"
    except Exception as e:  # noqa: BLE001 - fail-open：绝不阻断主链路
        _HEAL_DONE = True
        _log(f"自愈失败（忽略，主链路继续）: {e}")
        return "error"


def reset_heal_flag() -> None:
    """测试隔离用：重置 once 标记。"""
    global _HEAL_DONE
    _HEAL_DONE = False
