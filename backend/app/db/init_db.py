"""建库与轻量迁移（F1 拆分，2026-08-31）：init_db = create_all + 索引 ensure + 种子/一次性回填。

3.8 收敛版（2026-09-03）：原 87 处手工补列（86 条 ADD COLUMN）已全部删除——补列已由
bootstrap 迁移（6d39454c2517，down_revision=d3e4f5a6b7c8）以 ``has_column`` 守卫等价固化进链。
启动顺序（main.py）：本文件先执行、Alembic 对齐随后，因此：
- 全新空库 / 当前 schema 库：create_all 已含全部列，回填/种子直接执行（与收敛前行为一致）；
- 远古库（pre-alembic 旧库）：首次启动列未就绪时，下方回填/种子经 ``_table_cols`` 列在位守卫
  跳过（不崩），由随后的 upgrade head 整链重放补列；幂等回填/种子（只填 NULL / INSERT OR
  IGNORE）在下次启动自动补齐——结构当次即齐，数据回填一次性延迟一个重启周期。
本文件不再承载手工补列（加列）DDL。保留的非 DDL 部分：create_all、高频表索引 ensure、
幂等种子与一次性回填（关系默认值 / is_admin 种子与一致性自愈 / 遗忘曲线与复习排期回填 /
importance 百分比换算 / pets 归属回填 / P1-1 开关一次性置 1 / platform_profiles 动态种子）。
例外保留的结构迁移：character_states.control→anger 改名（值反转）——版本链无等价改名迁移、
bootstrap 亦未固化 anger 列，远古库（有 control 无 anger）依赖此处完成语义迁移，勿随补列收敛删除。
"""
from app.config import settings
from app.db.engine import engine
from app.utils.logger import get_logger

# P3-12（2026-09-17）：本文件原有 12 处 print 改为 logging（启动期迁移/种子/回填日志统一进 app.log，
# 并随时间轮转；print 在服务化部署里无级别、无落地、无法过滤）。
_logger = get_logger("db.init_db")

# P1-1 一次性迁移哨兵（2026-08-27 用户拍板全量开启）：
# 存量 0→1 只执行一次，之后用户手动关闭的角色开关不会被重启重置。
_MIGRATION_LIFE_V2_FLAGS = "migration_life_v2_flags_20260827"


async def _table_cols(conn, table: str) -> set[str]:
    """当前库中该表已有的列名集合（表不存在返回空集）——回填/种子的「列在位」守卫。"""
    from sqlalchemy import text as _text
    try:
        rows = (await conn.execute(_text(f"PRAGMA table_info({table})"))).fetchall()
        return {c[1] for c in rows}
    except Exception:
        return set()


async def _migrate_ai_character_loop_flags(conn) -> None:
    """P1-1：老角色「认知循环 / 记忆 v2.1」开关一次性迁移为默认开(1)。

    3.8 收敛版：加列（缺失 → ALTER ADD COLUMN DEFAULT 1）已由 bootstrap 迁移承接，
    本函数只保留非 DDL 的「存量 0→1 + 哨兵」：
    - 列未就绪（远古库首次启动，bootstrap 稍后补列）→ 本次跳过且**不写哨兵**，
      下次启动列已在位时补迁移；
    - 哨兵保证 UPDATE 只执行一次：之后用户显式关闭的开关保留，不会被重启重置。
    """
    from sqlalchemy import text as _text
    _names = await _table_cols(conn, "ai_characters")
    if not {"cognitive_loop_enabled", "memory_v2_enabled"} <= _names:
        return
    _sent = None
    try:
        _sent = (await conn.execute(
            _text("SELECT 1 FROM runtime_flags WHERE key=:k"), {"k": _MIGRATION_LIFE_V2_FLAGS}
        )).fetchone()
    except Exception:
        _sent = None  # runtime_flags 表不存在（极端旧库）→ 按未迁移处理
    if _sent is None:
        for _col in ("cognitive_loop_enabled", "memory_v2_enabled"):
            # 列已存在且未做过全量开启迁移：存量 0 → 1（一次性）
            await conn.execute(_text(f"UPDATE ai_characters SET {_col} = 1 WHERE {_col} = 0"))
        try:
            await conn.execute(_text(
                "INSERT OR IGNORE INTO runtime_flags(key, enabled, updated_at) "
                "VALUES(:k, 1, datetime('now'))"
            ), {"k": _MIGRATION_LIFE_V2_FLAGS})
        except Exception:
            pass


async def _backfill_channel_bindings_from_global_config(conn) -> None:
    """P3-1（2026-09-18）：create_all 建表路径等价于 alembic a7b8c9d0e1f2 的渠道回填。

    走 create_all 建表的部署（本机默认：init_db 启动期补表，与 alembic 链并存）不会跑
    a7b8c9d0e1f2 那段回填 → channel_bindings 表存在但为空。此处补一次等价回填：将旧全局
    config（plugins.config_json.allowed_character_ids，单选）在「该渠道绑定表为空」时搬进
    channel_bindings。

    与 alembic 路径逐字段等价（INSERT 字段/字面量完全一致）；硬约束：
    - 幂等：只在「该渠道绑定表为空」时搬，已有任意一行 → 整渠道跳过；
    - fail-open：单渠道异常只记 _logger.warning，绝不阻塞启动、不影响其他渠道；
    - 不动 alembic 迁移、不动任何 flag、不改读取端（reader 兜底保留）。
    """
    from sqlalchemy import text as sa_text

    try:
        cb_cols = await _table_cols(conn, "channel_bindings")
        if "channel" not in cb_cols:
            return  # 表尚不存在（远古库首次启动）→ 跳过，下次启动补齐
        plug_cols = await _table_cols(conn, "plugins")
        if "config_json" not in plug_cols or "name" not in plug_cols:
            return
    except Exception:
        return

    for plugin_name, channel in (("wechat_ilink", "wechat"), ("douyin_mcp", "douyin")):
        try:
            already = (await conn.execute(sa_text(
                "SELECT 1 FROM channel_bindings WHERE channel = :ch LIMIT 1"
            ), {"ch": channel})).first()
            if already is not None:
                continue  # 该渠道任一租户已写入 → 整渠道跳过（幂等，与 alembic 同判据）

            prow = (await conn.execute(sa_text(
                "SELECT config_json FROM plugins WHERE name = :n"
            ), {"n": plugin_name})).first()
            if prow is None:
                continue

            cfg: dict = {}
            try:
                import json
                cfg = json.loads(prow[0] or "{}")
            except Exception:
                cfg = {}

            raw = cfg.get("allowed_character_ids", "")
            raw = ",".join(str(x) for x in raw) if isinstance(raw, list) else str(raw or "")
            ids: list[int] = []
            for _tok in raw.split(","):
                if not _tok.strip().isdigit():
                    continue
                try:
                    ids.append(int(_tok))
                except ValueError:
                    continue  # '³' 等 Unicode 数字 isdigit() 为 True 但 int() 会抛，跳过即可

            for cid in ids[:1]:  # 旧模型本就单选，只搬第一条（与 alembic 一致）
                char_cols = await _table_cols(conn, "ai_characters")
                if "user_id" not in char_cols:
                    chrow = None
                else:
                    chrow = (await conn.execute(sa_text(
                        "SELECT user_id FROM ai_characters WHERE id = :cid"
                    ), {"cid": cid})).first()
                if chrow is None:
                    continue
                owner = int(chrow[0] or 0)
                if owner <= 0:
                    continue
                user_cols = await _table_cols(conn, "users")
                urow = None
                if "parent_id" in user_cols:
                    urow = (await conn.execute(sa_text(
                        "SELECT parent_id FROM users WHERE id = :uid"
                    ), {"uid": owner})).first()
                tenant = int(urow[0]) if (urow is not None and urow[0]) else owner
                await conn.execute(sa_text(
                    "INSERT INTO channel_bindings (channel, tenant_id, owner_user_id, bot_account_id,"
                    " bot_label, character_id, enabled, extra_json)"
                    " VALUES (:ch, :t, :o, 'default', '', :cid, 1, '{}')"
                ), {"ch": channel, "t": tenant, "o": owner, "cid": cid})
            _logger.info("[migrate] channel_bindings backfilled for %s (create_all path)", channel)
        except Exception as exc:  # fail-open：单渠道异常不影响启动与其他渠道
            _logger.warning("[migrate] channel_bindings backfill skipped for %s: %s", channel, exc)


async def _backfill_server_admin(conn) -> None:
    """账号独立 P1（2026-09-19）：server_admin 存量回填（与 Alembic c4d5e6f7a8b9 等价、幂等）。

    **口径（09-19 复核修正）**：本产品里「独立账号」默认都是家庭主账号（`parent_id IS NULL →
    is_admin=1`，见下方一致性自愈），所以**不能**按 `is_admin=1` 回填——那会把每个新注册账号
    都变成服务器控制台管理员，`require_server_admin` 闸门形同虚设（Qwen 只读审计 09-19 发现：
    生产库 13/13 账号全被授予）。权威来源＝`.env ADMIN_USER_IDS`（`settings.admin_user_ids`，
    其语义本就是「服务器级配置管理账号」）；名单为空/无效时，再退化为「最早创建的账号」
    做一次性引导，避免控制台彻底进不去。

    加列由 Alembic 承接（本文件 FREEZE 后禁止手工 DDL，见文末哨兵）；此处只做数据回填。
    列未就绪（远古库首次启动，bootstrap 稍后补列）→ 本次跳过，下次启动补齐。
    """
    from sqlalchemy import text as _text
    if "server_admin" not in await _table_cols(conn, "users"):
        return
    ids = [int(i) for i in (getattr(settings, "admin_user_ids", None) or []) if str(i).strip()]
    if ids:
        # 数字白名单拼接（来源是配置里的 int 列表，无注入面）
        await conn.execute(_text(
            "UPDATE users SET server_admin = 1 WHERE id IN (%s) AND server_admin = 0"
            % ",".join(str(i) for i in ids)
        ))
        _logger.info("[migrate] users.server_admin seeded from env ADMIN_USER_IDS=%s", ids)
    # 引导兜底：一个 server_admin 都没有 → 取最早账号（否则控制台永远进不去）
    has_any = (await conn.execute(_text(
        "SELECT id FROM users WHERE server_admin = 1 LIMIT 1"
    ))).first()
    if has_any is None:
        await conn.execute(_text(
            "UPDATE users SET server_admin = 1 WHERE id = (SELECT MIN(id) FROM users)"
        ))
        _logger.info("[migrate] users.server_admin bootstrapped to the oldest account")


async def init_db():
    """创建所有表（测试/初始化用）+ 幂等种子与一次性回填（列在位守卫，见模块 docstring）"""
    import app.models  # noqa: F401  # 确保所有模型注册到 Base.metadata
    from app.models.base import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        from sqlalchemy import text as sa_text

        # 初始化关系数据：默认关系类型为朋友（对象关系由用户在"关系网"页面自行设置）
        if "relation_type" in await _table_cols(conn, "ai_characters"):
            await conn.execute(sa_text("UPDATE ai_characters SET relation_type='朋友' WHERE relation_type IS NULL"))
            _logger.info("[migrate] relationships initialized")

        # plugin_stores 表：插件命名空间 KV（48a 桥 API store.set/get；幂等创建，存量库自动补建）
        await conn.execute(sa_text(
            "CREATE TABLE IF NOT EXISTS plugin_stores ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  plugin_name VARCHAR(100) NOT NULL,"
            "  user_id INTEGER NOT NULL,"
            "  key VARCHAR(128) NOT NULL,"
            "  value_json TEXT NOT NULL DEFAULT '{}',"
            "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
            "  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
            "  UNIQUE(plugin_name, user_id, key)"
            ")"
        ))
        _logger.info("[migrate] plugin_stores ensured")

        # 存量回填：遗忘起点 = decay_base_at（无则 created_at）；S 按旧 importance 反推（幂等：只填 NULL）
        _mem_cols = await _table_cols(conn, "memories")
        if {"decay_base_at", "last_reinforce_at", "strength_days", "next_review_at",
            "is_pinned", "is_locked"} <= _mem_cols:
            await conn.execute(sa_text(
                "UPDATE memories SET last_reinforce_at = COALESCE(decay_base_at, created_at) "
                "WHERE last_reinforce_at IS NULL"
            ))
            await conn.execute(sa_text(
                "UPDATE memories SET strength_days = MAX(3.0, MIN(180.0, importance / 120.0 * 90.0)) "
                "WHERE strength_days IS NULL AND is_archived = 0"
            ))
            await conn.execute(sa_text("UPDATE memories SET strength_days = 3.0 WHERE strength_days IS NULL"))
            # P1 主动复习排期回填：旧的高价值记忆按"遗忘起点+强度"排首次复习（幂等：只填 NULL）
            await conn.execute(sa_text(
                "UPDATE memories SET next_review_at = "
                "datetime(COALESCE(last_reinforce_at, created_at), "
                "'+' || CAST(ROUND(strength_days) AS INTEGER) || ' days') "
                "WHERE next_review_at IS NULL AND is_archived = 0 AND is_pinned = 0 "
                "AND COALESCE(is_locked, 0) = 0 AND importance >= 40.0"
            ))
            # 自愈：修复历史上被旧版拼接 SQL 写成整数的 next_review_at（如 2026+7=2033）
            await conn.execute(sa_text(
                "UPDATE memories SET next_review_at = "
                "datetime(COALESCE(last_reinforce_at, created_at), "
                "'+' || CAST(ROUND(strength_days) AS INTEGER) || ' days') "
                "WHERE next_review_at IS NOT NULL AND typeof(next_review_at) != 'text'"
            ))
            _logger.info("[migrate] memories forgetting-curve backfill done")

        # #46 主账号管理（选择型）：is_admin 一次性种子（幂等；加列已由 bootstrap 承接）
        # 仅在表中尚无任何 is_admin=1 时，从 settings.admin_user_ids（env）写入，避免覆盖 UI 管理结果
        if "is_admin" in await _table_cols(conn, "users"):
            _admin_count = (await conn.execute(sa_text("SELECT COUNT(*) FROM users WHERE is_admin = 1"))).scalar() or 0
            if _admin_count == 0:
                # 2026-08-24 修复：无任何主账号时自动引导。优先 env 指定且存在的用户；
                # 若 env 未指定或指定 id 不存在，则把最早注册用户（id 最小）设为主账号，避免「无主账号死锁」。
                _seed_ids = []
                for x in settings.admin_user_ids:
                    try:
                        _seed_ids.append(str(int(x)))
                    except Exception:
                        pass
                if _seed_ids:
                    _row = (await conn.execute(sa_text(
                        f"SELECT COUNT(*) FROM users WHERE id IN ({','.join(_seed_ids)})"
                    ))).scalar() or 0
                else:
                    _row = 0
                if _row == 0:
                    _first = (await conn.execute(sa_text("SELECT MIN(id) FROM users"))).scalar()
                    if _first:
                        await conn.execute(sa_text(f"UPDATE users SET is_admin = 1 WHERE id = {int(_first)}"))
                        _logger.info("[migrate] users.is_admin seeded to first user id=%s", _first)
                else:
                    await conn.execute(sa_text(f"UPDATE users SET is_admin = 1 WHERE id IN ({','.join(_seed_ids)})"))
                    _logger.info("[migrate] users.is_admin seeded from env ADMIN_USER_IDS=%s", settings.admin_user_ids)

        # #68 修订（2026-08-28）：is_admin 与 parent_id 一致性（幂等自愈）
        # 独立主账号（parent_id IS NULL）→ is_admin=1；子账号（parent_id IS NOT NULL）→ is_admin=0。
        # 仅当两列都存在时执行（远古库未跑到相应迁移前防御），每次启动修正不一致数据。
        _us_cols2 = await _table_cols(conn, "users")
        if {"is_admin", "parent_id"} <= _us_cols2:
            await conn.execute(sa_text(
                "UPDATE users SET is_admin = 1 WHERE parent_id IS NULL AND is_admin = 0"
            ))
            await conn.execute(sa_text(
                "UPDATE users SET is_admin = 0 WHERE parent_id IS NOT NULL AND is_admin = 1"
            ))
            _logger.info("[migrate] users.is_admin/parent_id consistency ensured")

        # 账号独立 P1（2026-09-19）：server_admin 存量回填（与 Alembic c4d5e6f7a8b9 等价、幂等）。
        await _backfill_server_admin(conn)

        # pets 归属标签（2026-08-07）：存量用户宠物显式标 owner_type='user'（AI 养宠 Phase 3 预留字段落地）
        if "owner_type" in await _table_cols(conn, "pets"):
            await conn.execute(sa_text(
                "UPDATE pets SET owner_type = 'user', owner_id = user_id "
                "WHERE owner_type IS NULL OR owner_type = ''"
            ))
            _logger.info("[migrate] pets.owner_type backfilled ('user' for legacy)")

        # character_states：控制力 control 列 -> 怒气值 anger（2026-08-05，语义方向反转：anger=100-control）
        # 例外保留的结构迁移：版本链无等价改名迁移、bootstrap 未固化 anger 列，
        # 远古库（有 control 无 anger）依赖此处完成语义迁移。
        _sc_cols = await _table_cols(conn, "character_states")
        if "anger" not in _sc_cols and "control" in _sc_cols:
            await conn.execute(sa_text("ALTER TABLE character_states RENAME COLUMN control TO anger"))
            await conn.execute(sa_text("UPDATE character_states SET anger = 100 - anger"))
            _logger.info("[migrate] character_states.control -> anger (value inverted)")

        # character_states 最近互动时间回填（疲劳休息判定用；幂等：只填 NULL；
        # drift 写库会刷新 updated_at，故单独一列、只补历史空值不覆盖现值）
        if "last_activity_at" in _sc_cols:
            await conn.execute(sa_text(
                "UPDATE character_states SET last_activity_at = updated_at WHERE last_activity_at IS NULL"
            ))
            _logger.info("[migrate] character_states.last_activity_at backfilled (=updated_at, NULL only)")

        # 旧数据 importance 按 1-5 迁移为百分比（×20，上限 120%）
        if "decay_base_at" in _mem_cols:
            one = (await conn.execute(sa_text("SELECT COUNT(*) FROM memories WHERE importance <= 5 AND is_archived = 0"))).scalar()
            if one and one > 0:
                await conn.execute(sa_text("UPDATE memories SET importance = importance * 20 WHERE importance <= 5 AND is_archived = 0"))
                await conn.execute(sa_text("UPDATE memories SET decay_base_at = created_at WHERE decay_base_at IS NULL"))
                _logger.info("[migrate] memories.importance scaled to pct (rows=%s)", one)

        # P1-1（2026-08-27 用户拍板全量开启）：老角色认知循环/记忆 v2.1 开关从默认关迁移为默认开。
        await _migrate_ai_character_loop_flags(conn)

        # 社交交互层 v2（2026-08-10 拍板实施）：platform_profiles 默认档案（幂等；X5 起按已注册渠道动态生成）
        if await _table_cols(conn, "platform_profiles"):
            try:
                from app.providers.channel import list_channels as _lc
                _ch_names = [c["name"] for c in _lc()]
            except Exception:
                _ch_names = []
            _seed_rows = ["('app', 'private', 'general', 'full', 'private', '', 1)"] + [
                f"('{n}', 'public', 'general', 'limited', 'social', 'creative', 1)" for n in _ch_names
            ]
            await conn.execute(sa_text(
                "INSERT OR IGNORE INTO platform_profiles "
                "(platform, visibility, relationship_level, memory_access, tone, content_style, enabled) VALUES "
                + ", ".join(_seed_rows)
            ))
            _logger.info("[migrate] platform_profiles seeded (app + %s channels)", len(_ch_names))

        # 高频表索引补充（审计 P1-05，2026-08-15）：消息/记忆/动态/评论/会话按查询路径建索引
        _idx_list = [
            ("idx_chat_messages_session_created", "chat_messages", "session_id, created_at"),
            ("idx_chat_messages_created", "chat_messages", "created_at"),
            ("idx_memories_char_user", "memories", "character_id, user_id"),
            ("idx_memories_char_created", "memories", "character_id, created_at"),
            ("idx_memories_importance", "memories", "importance"),
            # 记忆 P0（2026-08-18）：arbiter 30s tick 复习扫描 / 角色列表与过期清理的高频查询索引
            # （collect_review_events: WHERE is_archived=0 AND is_pinned=0 AND is_locked=0
            #   AND importance>=40 AND next_review_at<=now ORDER BY next_review_at）
            ("idx_memories_next_review", "memories", "next_review_at"),
            ("idx_memories_char_archived", "memories", "character_id, is_archived"),
            ("idx_moments_user_created", "ai_moments", "user_id, created_at"),
            ("idx_moments_char_created", "ai_moments", "character_id, created_at"),
            ("idx_moment_comments_moment", "moment_comments", "moment_id"),
            ("idx_chat_sessions_user", "chat_sessions", "user_id"),
            ("idx_chat_sessions_character", "chat_sessions", "character_id"),
            ("idx_weave_cards_character", "weave_cards", "character_id"),
            ("idx_scheduled_events_status_trigger", "scheduled_events", "status, trigger_at"),
        ]
        for _ix, _tb, _cols in _idx_list:
            await conn.execute(sa_text(f"CREATE INDEX IF NOT EXISTS {_ix} ON {_tb} ({_cols})"))
        _logger.info("[migrate] high-frequency table indexes ensured")

        # P3-1（2026-09-18）：create_all 建表路径渠道回填（等价 alembic a7b8c9d0e1f2）。
        # 纯数据回填（INSERT），非结构变更，置于 FREEZE 之前。幂等 + fail-open（见函数 docstring）。
        await _backfill_channel_bindings_from_global_config(conn)

        # 人工 DDL 冻结基线：本文件所有「加列 / 改表 / 建索引」语句都只能出现在下方 FREEZE 哨兵之前。
        # 3.8 收敛后本文件手工加列语句已归零（CI 基准 86 只减不增，防回潮）；上述例外（control→anger
        # 改名 / plugin_stores 建表 ensure）为存量语义迁移与表 ensure，非加列。新增 schema 变更只允许
        # 走 Alembic autogenerate 入链（见 docs/architecture.md「Schema 变更纪律（3.8）」）。
        # === 3.8 FREEZE: 此注释之后禁止新增任何结构变更的手工 SQL，一律改走 Alembic ===
