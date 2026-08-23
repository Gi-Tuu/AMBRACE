"""数据库连接与会话管理"""
import os
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings

# 首次部署时 backend/data/sqlite/ 尚不存在，SQLite 会报 unable to open database file
if settings.database_url.startswith("sqlite"):
    _db_file = settings.database_url.split("///", 1)[-1].split("?", 1)[0]
    if _db_file and _db_file != ":memory:":
        os.makedirs(Path(_db_file).resolve().parent, exist_ok=True)

# 创建异步引擎
engine = create_async_engine(
    settings.database_url,
    echo=False,
    poolclass=NullPool,          # SQLite 不需要连接池
    connect_args={"check_same_thread": False},
)

# 异步会话工厂
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:
    """FastAPI 依赖注入：获取数据库会话"""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """创建所有表（测试/初始化用）"""
    import app.models  # noqa: F401  # 确保所有模型注册到 Base.metadata
    from app.models.base import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 轻量迁移：chat_messages 补充 image_url 列（SQLite 的 create_all 不会给已存在表加列）
        from sqlalchemy import text as sa_text
        cols = (await conn.execute(sa_text("PRAGMA table_info(chat_messages)"))).fetchall()
        if cols and "image_url" not in [c[1] for c in cols]:
            await conn.execute(sa_text("ALTER TABLE chat_messages ADD COLUMN image_url VARCHAR(500)"))
            print("[migrate] chat_messages.image_url added")
        # ai_moments 补列：图片 URL + 图片描述（用户朋友圈带图）
        mcols = (await conn.execute(sa_text("PRAGMA table_info(ai_moments)"))).fetchall()
        if mcols:
            mnames = [c[1] for c in mcols]
            if "image_url" not in mnames:
                await conn.execute(sa_text("ALTER TABLE ai_moments ADD COLUMN image_url VARCHAR(500)"))
                print("[migrate] ai_moments.image_url added")
            if "image_desc" not in mnames:
                await conn.execute(sa_text("ALTER TABLE ai_moments ADD COLUMN image_desc TEXT"))
                print("[migrate] ai_moments.image_desc added")
        # douyin_pending 补列：随机执行队列时间（2026-08-09）
        dp_cols = (await conn.execute(sa_text("PRAGMA table_info(douyin_pending)"))).fetchall()
        if dp_cols and "execute_at" not in [c[1] for c in dp_cols]:
            await conn.execute(sa_text("ALTER TABLE douyin_pending ADD COLUMN execute_at DATETIME"))
            print("[migrate] douyin_pending.execute_at added")
        # douyin_pending/douyin_comments 补列：粉丝标记（回复额度 60/40 拆分，2026-08-09）
        if dp_cols and "is_fan" not in [c[1] for c in dp_cols]:
            await conn.execute(sa_text("ALTER TABLE douyin_pending ADD COLUMN is_fan BOOLEAN DEFAULT 0"))
            print("[migrate] douyin_pending.is_fan added")
        dc_cols = (await conn.execute(sa_text("PRAGMA table_info(douyin_comments)"))).fetchall()
        if dc_cols and "is_fan" not in [c[1] for c in dc_cols]:
            await conn.execute(sa_text("ALTER TABLE douyin_comments ADD COLUMN is_fan BOOLEAN DEFAULT 0"))
            print("[migrate] douyin_comments.is_fan added")
        # ai_characters 补列：生日（YYYY-MM-DD）
        ccols = (await conn.execute(sa_text("PRAGMA table_info(ai_characters)"))).fetchall()
        if ccols and "birthday" not in [c[1] for c in ccols]:
            await conn.execute(sa_text("ALTER TABLE ai_characters ADD COLUMN birthday VARCHAR(10)"))
            print("[migrate] ai_characters.birthday added")
        # ai_characters 补列：关系网（关系类型 + 是否用户对象）
        if ccols:
            rnames = [c[1] for c in ccols]
            if "relation_type" not in rnames:
                await conn.execute(sa_text("ALTER TABLE ai_characters ADD COLUMN relation_type VARCHAR(30)"))
                print("[migrate] ai_characters.relation_type added")
            if "is_partner" not in rnames:
                await conn.execute(sa_text("ALTER TABLE ai_characters ADD COLUMN is_partner BOOLEAN DEFAULT 0"))
                print("[migrate] ai_characters.is_partner added")
        # proactive_settings 补列：织库全注入对话开关（2026-08-12）
        ps_cols = (await conn.execute(sa_text("PRAGMA table_info(proactive_settings)"))).fetchall()
        if ps_cols and "weave_full_inject_enabled" not in [c[1] for c in ps_cols]:
            await conn.execute(sa_text("ALTER TABLE proactive_settings ADD COLUMN weave_full_inject_enabled BOOLEAN DEFAULT 0"))
            print("[migrate] proactive_settings.weave_full_inject_enabled added")
        # proactive_settings 补列：可配置免打扰时段（2026-08-12；dnd_enabled 关闭时沿用硬编码 0-7 点静默）
        ps_cols = (await conn.execute(sa_text("PRAGMA table_info(proactive_settings)"))).fetchall()
        if ps_cols:
            ps_names = [c[1] for c in ps_cols]
            if "dnd_enabled" not in ps_names:
                await conn.execute(sa_text("ALTER TABLE proactive_settings ADD COLUMN dnd_enabled BOOLEAN DEFAULT 0"))
                print("[migrate] proactive_settings.dnd_enabled added")
        # world_facts 补列：权威事实层（P1-3，2026-08-16）——作者来源 + 不可动摇标记
        wf_cols = (await conn.execute(sa_text("PRAGMA table_info(world_facts)"))).fetchall()
        if wf_cols:
            wf_names = [c[1] for c in wf_cols]
            if "author" not in wf_names:
                await conn.execute(sa_text("ALTER TABLE world_facts ADD COLUMN author VARCHAR(20) DEFAULT 'system'"))
                print("[migrate] world_facts.author added")
            if "is_authoritative" not in wf_names:
                await conn.execute(sa_text("ALTER TABLE world_facts ADD COLUMN is_authoritative BOOLEAN DEFAULT 0"))
                print("[migrate] world_facts.is_authoritative added")
        # proactive_settings 补列：免打扰时段 + 查岗/管制（P1 修复：原误嵌套在 world_facts 块内）
        ps2_cols = (await conn.execute(sa_text("PRAGMA table_info(proactive_settings)"))).fetchall()
        if ps2_cols:
            ps2_names = [c[1] for c in ps2_cols]
            for _col, _ddl in (
                ("dnd_start", "ALTER TABLE proactive_settings ADD COLUMN dnd_start VARCHAR(5) DEFAULT '00:00'"),
                ("dnd_end", "ALTER TABLE proactive_settings ADD COLUMN dnd_end VARCHAR(5) DEFAULT '07:00'"),
                ("check_in_enabled", "ALTER TABLE proactive_settings ADD COLUMN check_in_enabled BOOLEAN DEFAULT 0"),
                ("control_enabled", "ALTER TABLE proactive_settings ADD COLUMN control_enabled BOOLEAN DEFAULT 0"),
            ):
                if _col not in ps2_names:
                    await conn.execute(sa_text(_ddl))
                    print(f"[migrate] proactive_settings.{_col} added")
        # memories 补列：说话人归属 + 认知状态（World & Cognition P0，2026-08-15）
        mem_cols = (await conn.execute(sa_text("PRAGMA table_info(memories)"))).fetchall()
        if mem_cols:
            _mem_names = [c[1] for c in mem_cols]
            if "speaker_id" not in _mem_names:
                await conn.execute(sa_text("ALTER TABLE memories ADD COLUMN speaker_id INTEGER"))
                print("[migrate] memories.speaker_id added")
            if "speaker_type" not in _mem_names:
                await conn.execute(sa_text("ALTER TABLE memories ADD COLUMN speaker_type VARCHAR(10)"))
                print("[migrate] memories.speaker_type added")
            if "epistemic_status" not in _mem_names:
                await conn.execute(sa_text("ALTER TABLE memories ADD COLUMN epistemic_status VARCHAR(12)"))
                print("[migrate] memories.epistemic_status added")
            if "is_core" not in _mem_names:
                await conn.execute(sa_text("ALTER TABLE memories ADD COLUMN is_core BOOLEAN DEFAULT 0"))
                print("[migrate] memories.is_core added")
            if "core_category" not in _mem_names:
                await conn.execute(sa_text("ALTER TABLE memories ADD COLUMN core_category VARCHAR(20)"))
                print("[migrate] memories.core_category added")
            if "confirmation_count" not in _mem_names:
                await conn.execute(sa_text("ALTER TABLE memories ADD COLUMN confirmation_count INTEGER DEFAULT 0"))
                print("[migrate] memories.confirmation_count added")
        # proactive_storyline_items 补列：深度思考过程（气泡折叠展示，2026-08-15）
        psli_cols = (await conn.execute(sa_text("PRAGMA table_info(proactive_storyline_items)"))).fetchall()
        if psli_cols and "reasoning" not in [c[1] for c in psli_cols]:
            await conn.execute(sa_text("ALTER TABLE proactive_storyline_items ADD COLUMN reasoning TEXT"))
            print("[migrate] proactive_storyline_items.reasoning added")
        # memories 补列：提及但已离开的角色名（角色删除后标记离开，2026-08-13）
        mem_cols = (await conn.execute(sa_text("PRAGMA table_info(memories)"))).fetchall()
        if mem_cols and "departed_names" not in [c[1] for c in mem_cols]:
            await conn.execute(sa_text("ALTER TABLE memories ADD COLUMN departed_names VARCHAR(255)"))
            print("[migrate] memories.departed_names added")
        # chat_group_messages 补列：@我的才弹通知（用户 @ 角色后回应 notify_user=1，2026-08-15）
        cgm_cols = (await conn.execute(sa_text("PRAGMA table_info(chat_group_messages)"))).fetchall()
        if cgm_cols and "notify_user" not in [c[1] for c in cgm_cols]:
            await conn.execute(sa_text("ALTER TABLE chat_group_messages ADD COLUMN notify_user INTEGER DEFAULT 0"))
            print("[migrate] chat_group_messages.notify_user added")
        # state_trigger_logs 补列：冷战细化（怒气/哄好分级/软化次数/别扭期，2026-08-15）
        stl_cols = (await conn.execute(sa_text("PRAGMA table_info(state_trigger_logs)"))).fetchall()
        if stl_cols:
            _stl_names = [c[1] for c in stl_cols]
            if "anger_at_trigger" not in _stl_names:
                await conn.execute(sa_text("ALTER TABLE state_trigger_logs ADD COLUMN anger_at_trigger INTEGER DEFAULT 0"))
                print("[migrate] state_trigger_logs.anger_at_trigger added")
            if "soothe_level" not in _stl_names:
                await conn.execute(sa_text("ALTER TABLE state_trigger_logs ADD COLUMN soothe_level INTEGER DEFAULT 0"))
                print("[migrate] state_trigger_logs.soothe_level added")
            if "soothe_count" not in _stl_names:
                await conn.execute(sa_text("ALTER TABLE state_trigger_logs ADD COLUMN soothe_count INTEGER DEFAULT 0"))
                print("[migrate] state_trigger_logs.soothe_count added")
            if "stubborn" not in _stl_names:
                await conn.execute(sa_text("ALTER TABLE state_trigger_logs ADD COLUMN stubborn INTEGER DEFAULT 0"))
                print("[migrate] state_trigger_logs.stubborn added")
        # memories 补列：矛盾计数 + 可靠度（World & Cognition P5，2026-08-15）
        mem_cols5 = (await conn.execute(sa_text("PRAGMA table_info(memories)"))).fetchall()
        if mem_cols5:
            _mem_names5 = [c[1] for c in mem_cols5]
            if "contradiction_count" not in _mem_names5:
                await conn.execute(sa_text("ALTER TABLE memories ADD COLUMN contradiction_count INTEGER DEFAULT 0"))
                print("[migrate] memories.contradiction_count added")
            if "reliability_score" not in _mem_names5:
                await conn.execute(sa_text("ALTER TABLE memories ADD COLUMN reliability_score FLOAT"))
                print("[migrate] memories.reliability_score added")
        # memories 补列：记忆链条绑定（2026-08-20）——链级级联 + 内容版本
        _memcols_chain = (await conn.execute(sa_text("PRAGMA table_info(memories)"))).fetchall()
        if _memcols_chain:
            _chain_names = [c[1] for c in _memcols_chain]
            for _col, _ddl in (
                ("chain_id", "ALTER TABLE memories ADD COLUMN chain_id VARCHAR(64)"),
                ("parent_id", "ALTER TABLE memories ADD COLUMN parent_id INTEGER"),
                ("node_type", "ALTER TABLE memories ADD COLUMN node_type VARCHAR(20)"),
                ("version", "ALTER TABLE memories ADD COLUMN version INTEGER DEFAULT 0"),
            ):
                if _col not in _chain_names:
                    await conn.execute(sa_text(_ddl))
                    print(f"[migrate] memories.{_col} added")
        # ai_characters 补列：自定义声色（音色/语速/语调，2026-08-11）
        if ccols:
            vnames = [c[1] for c in ccols]
            if "voice" not in vnames:
                await conn.execute(sa_text("ALTER TABLE ai_characters ADD COLUMN voice VARCHAR(50)"))
                print("[migrate] ai_characters.voice added")
            if "voice_rate" not in vnames:
                await conn.execute(sa_text("ALTER TABLE ai_characters ADD COLUMN voice_rate FLOAT"))
                print("[migrate] ai_characters.voice_rate added")
            if "voice_pitch" not in vnames:
                await conn.execute(sa_text("ALTER TABLE ai_characters ADD COLUMN voice_pitch FLOAT"))
                print("[migrate] ai_characters.voice_pitch added")
            # ai_characters 补列：所在时区（朋友圈时间按作者地区显示，2026-08-12）
            if "timezone_offset" not in vnames:
                await conn.execute(sa_text("ALTER TABLE ai_characters ADD COLUMN timezone_offset INTEGER"))
                print("[migrate] ai_characters.timezone_offset added")
            # ai_characters 补列：自述（AI 形成；背景信息 bio 归用户，AI 不覆盖，2026-08-14）
            if "self_statement" not in vnames:
                await conn.execute(sa_text("ALTER TABLE ai_characters ADD COLUMN self_statement TEXT"))
                print("[migrate] ai_characters.self_statement added")
        # 初始化关系数据：默认关系类型为朋友（对象关系由用户在"关系网"页面自行设置）
        await conn.execute(sa_text("UPDATE ai_characters SET relation_type='朋友' WHERE relation_type IS NULL"))
        print("[migrate] relationships initialized")
        # scheduled_events 补列：承诺方（ai=AI承诺/user=用户承诺，2026-08-14 定时承诺修复）
        se_cols = (await conn.execute(sa_text("PRAGMA table_info(scheduled_events)"))).fetchall()
        if se_cols and "owner" not in [c[1] for c in se_cols]:
            await conn.execute(sa_text("ALTER TABLE scheduled_events ADD COLUMN owner VARCHAR(10) DEFAULT 'ai'"))
            print("[migrate] scheduled_events.owner added")
        # pet_activities 补列：执行者（user/ai，小手机宠物应用过滤 AI 照顾记录，2026-08-14）
        pa_cols = (await conn.execute(sa_text("PRAGMA table_info(pet_activities)"))).fetchall()
        if pa_cols and "actor" not in [c[1] for c in pa_cols]:
            await conn.execute(sa_text("ALTER TABLE pet_activities ADD COLUMN actor VARCHAR(10) DEFAULT 'user'"))
            print("[migrate] pet_activities.actor added")
        # user_workflows 补列：画布（nodes/edges 分支，2026-08-14 方案 C）
        uw_cols = (await conn.execute(sa_text("PRAGMA table_info(user_workflows)"))).fetchall()
        if uw_cols and "graph" not in [c[1] for c in uw_cols]:
            await conn.execute(sa_text("ALTER TABLE user_workflows ADD COLUMN graph TEXT"))
            print("[migrate] user_workflows.graph added")
        # plugins 补列：插件类型（48c 配置驱动零代码模板；sync_plugins_db 时从 manifest 回填）
        pl_cols = (await conn.execute(sa_text("PRAGMA table_info(plugins)"))).fetchall()
        if pl_cols and "type" not in [c[1] for c in pl_cols]:
            await conn.execute(sa_text("ALTER TABLE plugins ADD COLUMN type VARCHAR(20) DEFAULT 'http'"))
            print("[migrate] plugins.type added")
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
        print("[migrate] plugin_stores ensured")
        # calendar_notes/memo_notes 补列：记录者署名（2026-08-14）
        cn_cols = (await conn.execute(sa_text("PRAGMA table_info(calendar_notes)"))).fetchall()
        if cn_cols and "author" not in [c[1] for c in cn_cols]:
            await conn.execute(sa_text("ALTER TABLE calendar_notes ADD COLUMN author VARCHAR(50)"))
            print("[migrate] calendar_notes.author added")
        mn_cols = (await conn.execute(sa_text("PRAGMA table_info(memo_notes)"))).fetchall()
        if mn_cols and "author" not in [c[1] for c in mn_cols]:
            await conn.execute(sa_text("ALTER TABLE memo_notes ADD COLUMN author VARCHAR(50)"))
            print("[migrate] memo_notes.author added")
        # proactive_settings 补列：记忆复习子开关（2026-08-07，默认开启）
        ps_cols = (await conn.execute(sa_text("PRAGMA table_info(proactive_settings)"))).fetchall()
        if ps_cols and "memory_review_enabled" not in [c[1] for c in ps_cols]:
            await conn.execute(sa_text("ALTER TABLE proactive_settings ADD COLUMN memory_review_enabled BOOLEAN DEFAULT 1"))
            print("[migrate] proactive_settings.memory_review_enabled added")
        # memories 补列：置顶摘要标记（记忆本置顶内容条）
        memcols = (await conn.execute(sa_text("PRAGMA table_info(memories)"))).fetchall()
        if memcols and "is_pinned" not in [c[1] for c in memcols]:
            await conn.execute(sa_text("ALTER TABLE memories ADD COLUMN is_pinned BOOLEAN DEFAULT 0"))
            print("[migrate] memories.is_pinned added")
        # proactive_settings 补列：记忆复习子开关（2026-08-07，默认开启）
        ps_cols = (await conn.execute(sa_text("PRAGMA table_info(proactive_settings)"))).fetchall()
        if ps_cols and "memory_review_enabled" not in [c[1] for c in ps_cols]:
            await conn.execute(sa_text("ALTER TABLE proactive_settings ADD COLUMN memory_review_enabled BOOLEAN DEFAULT 1"))
            print("[migrate] proactive_settings.memory_review_enabled added")
        # memories 补列：置顶摘要标记（记忆本置顶内容条）
        if memcols and "decay_base_at" not in [c[1] for c in memcols]:
            await conn.execute(sa_text("ALTER TABLE memories ADD COLUMN decay_base_at DATETIME"))
            print("[migrate] memories.decay_base_at added")
        if memcols and "delete_at" not in [c[1] for c in memcols]:
            await conn.execute(sa_text("ALTER TABLE memories ADD COLUMN delete_at DATETIME"))
            print("[migrate] memories.delete_at added")
        # memories 补列：艾宾浩斯遗忘曲线字段（2026-08-05）
        if memcols and "strength_days" not in [c1[1] for c1 in memcols]:
            await conn.execute(sa_text("ALTER TABLE memories ADD COLUMN strength_days FLOAT"))
            print("[migrate] memories.strength_days added")
        if memcols and "last_reinforce_at" not in [c1[1] for c1 in memcols]:
            await conn.execute(sa_text("ALTER TABLE memories ADD COLUMN last_reinforce_at DATETIME"))
            print("[migrate] memories.last_reinforce_at added")
        if memcols and "review_count" not in [c1[1] for c1 in memcols]:
            await conn.execute(sa_text("ALTER TABLE memories ADD COLUMN review_count INTEGER DEFAULT 0"))
            print("[migrate] memories.review_count added")
        if memcols and "next_review_at" not in [c1[1] for c1 in memcols]:
            await conn.execute(sa_text("ALTER TABLE memories ADD COLUMN next_review_at DATETIME"))
            print("[migrate] memories.next_review_at added")
        if memcols and "is_locked" not in [c1[1] for c1 in memcols]:
            await conn.execute(sa_text("ALTER TABLE memories ADD COLUMN is_locked BOOLEAN DEFAULT 0"))
            print("[migrate] memories.is_locked added")
        if memcols and "ai_rated" not in [c1[1] for c1 in memcols]:
            await conn.execute(sa_text("ALTER TABLE memories ADD COLUMN ai_rated BOOLEAN DEFAULT 0"))
            print("[migrate] memories.ai_rated added")
        # 存量回填：遗忘起点 = decay_base_at（无则 created_at）；S 按旧 importance 反推（幂等：只填 NULL）
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
        print("[migrate] memories forgetting-curve backfill done")

        # proactive_message_logs 补列：extra_meta（记忆复习关联 memory_id 等）
        pmcols = (await conn.execute(sa_text("PRAGMA table_info(proactive_message_logs)"))).fetchall()
        if pmcols and "extra_meta" not in [c1[1] for c1 in pmcols]:
            await conn.execute(sa_text("ALTER TABLE proactive_message_logs ADD COLUMN extra_meta TEXT"))
            print("[migrate] proactive_message_logs.extra_meta added")

        # users 补列：界面语言（i18n，2026-08-06）
        ucols = (await conn.execute(sa_text("PRAGMA table_info(users)"))).fetchall()
        if ucols and "lang" not in [c1[1] for c1 in ucols]:
            await conn.execute(sa_text("ALTER TABLE users ADD COLUMN lang VARCHAR(10) DEFAULT 'zh'"))
            print("[migrate] users.lang added")
        if ucols and "ai_social_enabled" not in [c1[1] for c1 in ucols]:
            await conn.execute(sa_text("ALTER TABLE users ADD COLUMN ai_social_enabled BOOLEAN DEFAULT 1"))
            print("[migrate] users.ai_social_enabled added")

        # pets 补列：AI 提醒照顾时间（宠物关怀 Phase 2）
        petcols = (await conn.execute(sa_text("PRAGMA table_info(pets)"))).fetchall()
        if petcols and "last_remind_at" not in [c1[1] for c1 in petcols]:
            await conn.execute(sa_text("ALTER TABLE pets ADD COLUMN last_remind_at DATETIME"))
            print("[migrate] pets.last_remind_at added")
        # pets 归属标签（2026-08-07）：存量用户宠物显式标 owner_type='user'（AI 养宠 Phase 3 预留字段落地）
        if petcols and "owner_type" in [c1[1] for c1 in petcols]:
            await conn.execute(sa_text(
                "UPDATE pets SET owner_type = 'user', owner_id = user_id "
                "WHERE owner_type IS NULL OR owner_type = ''"
            ))
            print("[migrate] pets.owner_type backfilled ('user' for legacy)")

        # proactive_settings 补列：状态触发事件开关（v1）
        pscols = (await conn.execute(sa_text("PRAGMA table_info(proactive_settings)"))).fetchall()
        if pscols and "image_gen_enabled" not in [c1[1] for c1 in pscols]:
            await conn.execute(sa_text("ALTER TABLE proactive_settings ADD COLUMN image_gen_enabled BOOLEAN DEFAULT 0"))
            print("[migrate] proactive_settings.image_gen_enabled added")
        # proactive_settings 补列：主动生图（生图子开关，AI 自发发图，2026-08-09）
        if pscols and "active_image_gen_enabled" not in [c1[1] for c1 in pscols]:
            await conn.execute(sa_text("ALTER TABLE proactive_settings ADD COLUMN active_image_gen_enabled BOOLEAN DEFAULT 0"))
            print("[migrate] proactive_settings.active_image_gen_enabled added")
        if pscols and "state_trigger_enabled" not in [c1[1] for c1 in pscols]:
            await conn.execute(sa_text("ALTER TABLE proactive_settings ADD COLUMN state_trigger_enabled BOOLEAN DEFAULT 1"))
            print("[migrate] proactive_settings.state_trigger_enabled added")
        # proactive_settings 补列：冷战断联开关（v4）
        if pscols and "cold_war_enabled" not in [c1[1] for c1 in pscols]:
            await conn.execute(sa_text("ALTER TABLE proactive_settings ADD COLUMN cold_war_enabled BOOLEAN DEFAULT 1"))
            print("[migrate] proactive_settings.cold_war_enabled added")
        # api_configs 补列：provider（深度思考开关厂商适配，2026-08-10）
        ac_cols = (await conn.execute(sa_text("PRAGMA table_info(api_configs)"))).fetchall()
        if ac_cols and "provider" not in [c1[1] for c1 in ac_cols]:
            await conn.execute(sa_text("ALTER TABLE api_configs ADD COLUMN provider VARCHAR(30)"))
            print("[migrate] api_configs.provider added")
        # proactive_settings 迁移：show_reasoning_enabled(bool) → reasoning_level(int 0/1/2)（2026-08-10）
        if pscols and "reasoning_level" not in [c1[1] for c1 in pscols]:
            if "show_reasoning_enabled" in [c1[1] for c1 in pscols]:
                await conn.execute(sa_text("ALTER TABLE proactive_settings RENAME COLUMN show_reasoning_enabled TO reasoning_level"))
                print("[migrate] proactive_settings.show_reasoning_enabled renamed to reasoning_level")
            else:
                await conn.execute(sa_text("ALTER TABLE proactive_settings ADD COLUMN reasoning_level INTEGER DEFAULT 0"))
                print("[migrate] proactive_settings.reasoning_level added")
        # proactive_settings 补列：隐私总开关（2026-08-10，默认开）
        if pscols and "privacy_enabled" not in [c1[1] for c1 in pscols]:
            await conn.execute(sa_text("ALTER TABLE proactive_settings ADD COLUMN privacy_enabled BOOLEAN DEFAULT 1"))
            print("[migrate] proactive_settings.privacy_enabled added")
        if pscols and "show_tools_enabled" not in [c1[1] for c1 in pscols]:
            await conn.execute(sa_text("ALTER TABLE proactive_settings ADD COLUMN show_tools_enabled BOOLEAN DEFAULT 0"))
            print("[migrate] proactive_settings.show_tools_enabled added")
        # proactive_settings 补列：聊天页心情标识开关（状态组子开关，纯展示）
        if pscols and "mood_badge_enabled" not in [c1[1] for c1 in pscols]:
            await conn.execute(sa_text("ALTER TABLE proactive_settings ADD COLUMN mood_badge_enabled BOOLEAN DEFAULT 1"))
            print("[migrate] proactive_settings.mood_badge_enabled added")
        # proactive_settings 补列：隐私上锁开关（日记/小手机查看需向 AI 申请，2026-08-07）
        if pscols and "privacy_lock_enabled" not in [c1[1] for c1 in pscols]:
            await conn.execute(sa_text("ALTER TABLE proactive_settings ADD COLUMN privacy_lock_enabled BOOLEAN DEFAULT 1"))
            print("[migrate] proactive_settings.privacy_lock_enabled added")
        # proactive_settings 补列：朋友圈评论/回复子开关（朋友圈开关下，2026-08-07）
        if pscols and "moments_comment_enabled" not in [c1[1] for c1 in pscols]:
            await conn.execute(sa_text("ALTER TABLE proactive_settings ADD COLUMN moments_comment_enabled BOOLEAN DEFAULT 1"))
            print("[migrate] proactive_settings.moments_comment_enabled added")

        # character_states：控制力 control 列 -> 怒气值 anger（2026-08-05，语义方向反转：anger=100-control）
        sccols = (await conn.execute(sa_text("PRAGMA table_info(character_states)"))).fetchall()
        if sccols:
            scnames = [c1[1] for c1 in sccols]
            if "anger" not in scnames and "control" in scnames:
                await conn.execute(sa_text("ALTER TABLE character_states RENAME COLUMN control TO anger"))
                await conn.execute(sa_text("UPDATE character_states SET anger = 100 - anger"))
                print("[migrate] character_states.control -> anger (value inverted)")

        # character_states 补列：最近互动（评估）时间（疲劳休息判定用；drift 写库会刷新 updated_at，故单独一列）
        if sccols and "last_activity_at" not in scnames:
            await conn.execute(sa_text("ALTER TABLE character_states ADD COLUMN last_activity_at DATETIME"))
            await conn.execute(sa_text("UPDATE character_states SET last_activity_at = updated_at"))
            print("[migrate] character_states.last_activity_at added (backfill=updated_at)")

        # 旧数据 importance 按 1-5 迁移为百分比（×20，上限 120%）
        one = (await conn.execute(sa_text("SELECT COUNT(*) FROM memories WHERE importance <= 5 AND is_archived = 0"))).scalar()
        if one and one > 0:
            await conn.execute(sa_text("UPDATE memories SET importance = importance * 20 WHERE importance <= 5 AND is_archived = 0"))
            await conn.execute(sa_text("UPDATE memories SET decay_base_at = created_at WHERE decay_base_at IS NULL"))
            print(f"[migrate] memories.importance scaled to pct (rows={one})")

        # ai_characters 补列：认知循环开关（v2.1，默认关，灰度用）
        accols = (await conn.execute(sa_text("PRAGMA table_info(ai_characters)"))).fetchall()
        if accols and "cognitive_loop_enabled" not in [c1[1] for c1 in accols]:
            await conn.execute(sa_text("ALTER TABLE ai_characters ADD COLUMN cognitive_loop_enabled BOOLEAN DEFAULT 0"))
            print("[migrate] ai_characters.cognitive_loop_enabled added")

        # 记忆架构 v2.1（2026-08-08）：memories.why_it_matters / conversation_topics.goal+progress / ai_characters.memory_v2_enabled / relationship_events 表
        m2cols = (await conn.execute(sa_text("PRAGMA table_info(memories)"))).fetchall()
        if m2cols and "why_it_matters" not in [c1[1] for c1 in m2cols]:
            await conn.execute(sa_text("ALTER TABLE memories ADD COLUMN why_it_matters TEXT"))
            print("[migrate] memories.why_it_matters added")
        tcols = (await conn.execute(sa_text("PRAGMA table_info(conversation_topics)"))).fetchall()
        if tcols and "goal" not in [c1[1] for c1 in tcols]:
            await conn.execute(sa_text("ALTER TABLE conversation_topics ADD COLUMN goal BOOLEAN DEFAULT 0"))
            await conn.execute(sa_text("ALTER TABLE conversation_topics ADD COLUMN progress VARCHAR(50)"))
            print("[migrate] conversation_topics.goal/progress added")
        a2cols = (await conn.execute(sa_text("PRAGMA table_info(ai_characters)"))).fetchall()
        if a2cols and "memory_v2_enabled" not in [c1[1] for c1 in a2cols]:
            await conn.execute(sa_text("ALTER TABLE ai_characters ADD COLUMN memory_v2_enabled BOOLEAN DEFAULT 0"))
            print("[migrate] ai_characters.memory_v2_enabled added")
            print("[migrate] ai_characters.cognitive_loop_enabled added")

        # users 补列：位置信息 + 本地时区（2026-08-08 时间感知/位置设置）
        ucols = (await conn.execute(sa_text("PRAGMA table_info(users)"))).fetchall()
        if ucols:
            _unames = [c1[1] for c1 in ucols]
            for _col, _ddl in (
                ("location_enabled", "BOOLEAN DEFAULT 0"),
                ("location_gps_enabled", "BOOLEAN DEFAULT 0"),
                ("user_location", "VARCHAR(100)"),
                ("ai_location", "VARCHAR(100)"),
                ("location_follow", "BOOLEAN DEFAULT 0"),
                ("timezone_offset_minutes", "INTEGER"),
                ("location_lat", "REAL"),
                ("location_lng", "REAL"),
                ("location_city", "VARCHAR(100)"),
            ):
                if _col not in _unames:
                    await conn.execute(sa_text(f"ALTER TABLE users ADD COLUMN {_col} {_ddl}"))
                    print(f"[migrate] users.{_col} added")

        # character_states 补列：关系标量（v2.1 认知架构，0-100，默认 50；长期不互动衰减）
        if sccols:
            _scnames = [c1[1] for c1 in sccols]
            for _col in ("trust", "attachment", "curiosity"):
                if _col not in _scnames:
                    await conn.execute(sa_text(f"ALTER TABLE character_states ADD COLUMN {_col} INTEGER DEFAULT 50"))
                    print(f"[migrate] character_states.{_col} added")


        # 社交交互层 v2（2026-08-10 拍板实施）：platform_profiles 默认档案（幂等；app/douyin 两档）
        pfcols = (await conn.execute(sa_text("PRAGMA table_info(platform_profiles)"))).fetchall()
        if pfcols:
            await conn.execute(sa_text(
                "INSERT OR IGNORE INTO platform_profiles "
                "(platform, visibility, relationship_level, memory_access, tone, content_style, enabled) VALUES "
                "('app', 'private', 'general', 'full', 'private', '', 1), "
                "('douyin', 'public', 'general', 'limited', 'social', 'creative', 1)"
            ))
            print("[migrate] platform_profiles seeded (app/douyin)")
            # platform_profiles 补列：公开记忆收紧开关（2026-08-12；off=现状 / relationship=额外排relationship）
            pp_cols = (await conn.execute(sa_text("PRAGMA table_info(platform_profiles)"))).fetchall()
            if pp_cols and "memory_restrict" not in [c[1] for c in pp_cols]:
                await conn.execute(sa_text("ALTER TABLE platform_profiles ADD COLUMN memory_restrict VARCHAR(10) DEFAULT \'off\'"))
                print("[migrate] platform_profiles.memory_restrict added")

            # Life Engine（2026-08-12）：织库双域 domain + AI 离线生活设置列
            wc_cols = (await conn.execute(sa_text("PRAGMA table_info(weave_cards)"))).fetchall()
            if wc_cols and "domain" not in [c[1] for c in wc_cols]:
                await conn.execute(sa_text("ALTER TABLE weave_cards ADD COLUMN domain VARCHAR(10) DEFAULT 'shared'"))
                print("[migrate] weave_cards.domain added")
            ps_cols2 = (await conn.execute(sa_text("PRAGMA table_info(proactive_settings)"))).fetchall()
            if ps_cols2 and "life_enabled" not in [c[1] for c in ps_cols2]:
                await conn.execute(sa_text("ALTER TABLE proactive_settings ADD COLUMN life_enabled BOOLEAN DEFAULT 1"))
                await conn.execute(sa_text("ALTER TABLE proactive_settings ADD COLUMN life_intensity VARCHAR(10) DEFAULT 'low'"))
                await conn.execute(sa_text("ALTER TABLE proactive_settings ADD COLUMN life_share_enabled BOOLEAN DEFAULT 1"))
                print("[migrate] proactive_settings life fields added")
        # llm_usage 补列：用途归因 task（审计 P1-07，2026-08-15）
        lu_cols = (await conn.execute(sa_text("PRAGMA table_info(llm_usage)"))).fetchall()
        if lu_cols and "task" not in [c[1] for c in lu_cols]:
            await conn.execute(sa_text("ALTER TABLE llm_usage ADD COLUMN task VARCHAR(30)"))
            print("[migrate] llm_usage.task added")
        # life_states 补列：角色自定义房间布局 JSON（小家 v3.2 家具自由摆放，2026-08-18）
        ls_cols = (await conn.execute(sa_text("PRAGMA table_info(life_states)"))).fetchall()
        if ls_cols and "home_layout_json" not in [c[1] for c in ls_cols]:
            await conn.execute(sa_text("ALTER TABLE life_states ADD COLUMN home_layout_json TEXT"))
            print("[migrate] life_states.home_layout_json added")
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
        print("[migrate] high-frequency table indexes ensured")
