"""通用 OpenAI 兼容 LLM 客户端封装（服务器级全局配置 + 用户级 BYOK + .env 兜底）"""
import asyncio
from app.utils.async_tasks import spawn_background
import time as _time

from openai import AsyncOpenAI

from app.config import settings
from app.utils.logger import get_logger

_logger = get_logger("agent.llm")

# 任务常量：按用途指定模型（P1②，2026-08-12）；chat=聊天主链路（沿用 api_configs 表）
TASK_CHAT = "chat"
TASK_MEMORY = "memory"
TASK_CARD = "card"
TASK_EMOTION = "emotion"
TASK_STATUS = "status"
TASK_REVIEW = "review"
TASK_MESSAGE = "message"
TASK_DIARY = "diary"
TASK_TIMELINE = "timeline"
TASK_REFLECTION = "reflection"  # Phase J：每日复盘（2026-08-16）
TASK_PLUGIN_AI = "plugin_ai"  # 48b：角色开放成 API（chat_completion task 记账归因；48c 插件 chat 同字符串）

# 任务目录（供 API 配置页渲染；chat 主链路走现有 api_configs，不在此列）
TASK_LLM_CATALOG: list[dict] = [
    {"task": TASK_MEMORY, "name": "记忆处理", "desc": "记忆提取/摘要/意义/去重/对话摘要"},
    {"task": TASK_CARD, "name": "织库卡片", "desc": "全景记忆卡片编排"},
    {"task": TASK_EMOTION, "name": "情绪关怀", "desc": "主动情绪关心"},
    {"task": TASK_STATUS, "name": "状态评估", "desc": "角色状态更新/状态触发消息"},
    {"task": TASK_REVIEW, "name": "记忆复习", "desc": "情境记忆复习"},
    {"task": TASK_MESSAGE, "name": "主动消息", "desc": "问候/生日/纪念日/节日消息"},
    {"task": TASK_DIARY, "name": "日记生成", "desc": "角色每日日记"},
    {"task": TASK_TIMELINE, "name": "时光轴", "desc": "时光里程碑生成"},
]

# 密钥池轮换状态：raw key 串 -> 下一轮下标（单进程，GIL 保护足够）
_key_pool_index: dict[str, int] = {}


def _split_api_keys(raw: str | None) -> list[str]:
    """解析密钥池：支持逗号/分号/换行/竖线分隔，或 JSON 数组字符串"""
    if not raw:
        return []
    s = raw.strip()
    if s.startswith("["):
        import json as _json
        try:
            arr = _json.loads(s)
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()]
        except Exception:
            pass
    parts = [p.strip() for p in s.replace("\n", ",").replace("|", ",").split(",") if p.strip()]
    return parts


def _pick_api_key(raw: str | None) -> str | None:
    """密钥池轮换取一个 Key；单 Key 直接返回"""
    keys = _split_api_keys(raw)
    if not keys:
        return None
    if len(keys) == 1:
        return keys[0]
    idx = _key_pool_index.get(raw, 0)
    _key_pool_index[raw] = idx + 1
    return keys[idx % len(keys)]

# api_configs 表中 user_id=0 表示"服务器级全局配置"（开源部署：填一次，代码/.env 零密钥）
SERVER_CONFIG_UID = 0
# user_id=-1 表示"服务器级降级备用配置"（如 DeepSeek 兜底）：仅 LLM 审核/主端点失败时自动切换，不显示在 API 配置页
SERVER_FALLBACK_UID = -1

# 客户端缓存：按 (base_url, api_key) 键缓存，支持多端点切换
_clients: dict[tuple[str, str], AsyncOpenAI] = {}


def _client_key(base_url: str, api_key: str) -> tuple[str, str]:
    return (base_url, api_key)


def get_llm_client(api_key: str | None = None, base_url: str | None = None) -> AsyncOpenAI:
    """获取 LLM 客户端（首次调用时初始化，绕过系统代理）"""
    key = api_key or (settings.llm_api_key or settings.deepseek_api_key)
    url = base_url or (settings.llm_base_url or settings.deepseek_base_url)
    ck = _client_key(url, key)
    if ck not in _clients:
        import httpx
        # 显式超时（读取 60s / 连接 10s）：防止 VPN 切换等场景连接被静默丢弃后无限挂起
        # 不复用 keepalive 连接：每次请求新建连接，规避僵尸连接复用导致的 LLM 调用卡死
        _http_client = httpx.AsyncClient(
            proxy=None,
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=0, max_connections=8),
            transport=httpx.AsyncHTTPTransport(retries=1),
        )
        _clients[ck] = AsyncOpenAI(
            api_key=key,
            base_url=url,
            http_client=_http_client,
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
    return _clients[ck]


def _client_via_registry(cfg: dict, picked_key: str) -> AsyncOpenAI:
    """X3（2026-08-31）：经 provider 注册表取 LLM 客户端（flag provider_registry，默认开）。

    - 开：resolve_provider("llm") → 工厂收运行时 {api_key, base_url}（密钥不进注册表）；
      cfg["provider"] 与注册名精确匹配可选中插件 provider，未匹配走内置 openai_compatible；
    - 注册表无命中/工厂异常/工厂返回 None → 回退内置直连 get_llm_client（fail-open，
      与旧链路逐字节一致；工厂体晚绑定本模块，monkeypatch 接缝不变）。
    """
    try:
        from app.agent.loop import AGENT_FLAGS as _af
        flag_on = bool(_af.get("provider_registry", True))
    except Exception:
        flag_on = True
    if not flag_on:
        return get_llm_client(api_key=picked_key, base_url=cfg.get("base_url"))
    try:
        from app.providers.registry import resolve_provider
        hit = resolve_provider("llm", {"provider": cfg.get("provider")})
        if hit is not None:
            _name, factory = hit
            client = factory({"api_key": picked_key, "base_url": cfg.get("base_url")})
            if client is not None:
                return client
    except Exception as e:
        _logger.warning("provider registry llm resolve failed, fallback to builtin: %s", e)
    return get_llm_client(api_key=picked_key, base_url=cfg.get("base_url"))


async def get_user_llm_config(user_id: int | None) -> dict | None:
    """读取用户级 BYOK 配置（api_configs 表，enabled=True 时生效）；无则 None"""
    if not user_id:
        return None
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.config import ApiConfig
        async with async_session_factory() as db:
            result = await db.execute(
                select(ApiConfig).where(ApiConfig.user_id == user_id)
            )
            cfg = result.scalar_one_or_none()
            if cfg and cfg.enabled and (cfg.base_url or cfg.api_key):
                return {
                    "base_url": cfg.base_url,
                    "api_key": cfg.api_key,
                    "model": cfg.model,
                    "provider": getattr(cfg, "provider", None),
                }
    except Exception as e:
        _logger.warning("get_user_llm_config failed user=%s: %s", user_id, e)
    return None


async def get_server_llm_config() -> dict | None:
    """读取服务器级全局 LLM 配置（api_configs user_id=0，enabled=True）；无则 None"""
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.config import ApiConfig
        async with async_session_factory() as db:
            result = await db.execute(
                select(ApiConfig).where(ApiConfig.user_id == SERVER_CONFIG_UID)
            )
            cfg = result.scalar_one_or_none()
            if cfg and cfg.enabled and (cfg.base_url or cfg.api_key):
                return {
                    "base_url": cfg.base_url,
                    "api_key": cfg.api_key,
                    "model": cfg.model,
                    "provider": getattr(cfg, "provider", None),
                }
    except Exception as e:
        _logger.warning("get_server_llm_config failed: %s", e)
    return None


async def get_task_llm_config(user_id: int | None, task: str, character_id: int | None = None) -> dict | None:
    """任务专用 LLM 配置：用户级(task) → 服务器级(task) → None（调用方回退 chat 配置）。

    character_id：角色绑定的「我的 LLM」配置（#68），解析优先级见 resolve_character_llm_config。
    """
    if not task or task == TASK_CHAT:
        return None
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.agent import TaskLlmConfig
        async with async_session_factory() as db:
            cfg = None
            if user_id:
                result = await db.execute(
                    select(TaskLlmConfig).where(
                        TaskLlmConfig.user_id == user_id, TaskLlmConfig.task == task,
                    )
                )
                cfg = result.scalar_one_or_none()
            if cfg is None or not (cfg.enabled and (cfg.base_url or cfg.api_key)):
                result = await db.execute(
                    select(TaskLlmConfig).where(
                        TaskLlmConfig.user_id == SERVER_CONFIG_UID, TaskLlmConfig.task == task,
                    )
                )
                cfg = result.scalar_one_or_none()
            if cfg and cfg.enabled and (cfg.base_url or cfg.api_key):
                return {
                    "base_url": cfg.base_url,
                    "api_key": cfg.api_key,
                    "model": cfg.model,
                    "provider": getattr(cfg, "provider", None),
                }
    except Exception as e:
        _logger.warning("get_task_llm_config failed task=%s user=%s: %s", task, user_id, e)
    return None


async def get_fallback_llm_config() -> dict | None:
    """读取服务器级降级备用 LLM 配置（api_configs user_id=-1，enabled=True）；无则 None"""
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.config import ApiConfig
        async with async_session_factory() as db:
            result = await db.execute(
                select(ApiConfig).where(ApiConfig.user_id == SERVER_FALLBACK_UID)
            )
            cfg = result.scalar_one_or_none()
            if cfg and cfg.enabled and (cfg.base_url or cfg.api_key):
                return {
                    "base_url": cfg.base_url,
                    "api_key": cfg.api_key,
                    "model": cfg.model,
                    "provider": getattr(cfg, "provider", None),
                }
    except Exception as e:
        _logger.warning("get_fallback_llm_config failed: %s", e)
    return None


async def _resolve_llm_config(api_key: str | None = None, base_url: str | None = None,
                              model: str | None = None,
                              provider: str | None = None,
                              user_id: int | None = None,
                              task: str | None = None,
                              character_id: int | None = None) -> dict:
    """解析生效配置：任务专用(用户级→服务器级) > 显式传入(用户BYOK) > 服务器级DB配置 > .env 兜底

    character_id：角色绑定的「我的 LLM」配置（#68），解析优先级见 resolve_character_llm_config。
    """
    # BYOK 安全（2026-08-16 审计）：base_url 与 api_key 必须成对，单独 base_url 时丢弃（防服务器 key 发往任意端点）
    if base_url and not api_key:
        base_url = None
    explicit = bool(api_key or base_url)
    config_id: int | None = None

    # 1. 任务专用（非 chat）：用户级→服务器级（保持「无显式 BYOK 时生效」的既有语义）
    task_found = False
    if not explicit and task and task != TASK_CHAT:
        tcfg = await get_task_llm_config(user_id, task, character_id=character_id)
        if tcfg:
            api_key = tcfg.get("api_key") or api_key
            base_url = tcfg.get("base_url") or base_url
            model = tcfg.get("model") or model
            provider = tcfg.get("provider") or provider
            explicit = bool(api_key or base_url)
            task_found = True

    # 2-4. 新 user_llm_configs 链（#68 P0）：角色绑定 > 用户默认 > 主账号共享默认（仅子账号）。
    #      优先级高于显式 api_configs BYOK；未命中任何新配置时回退原链路（兼容既有 BYOK 行为）。
    if user_id and not task_found:
        from app.application.llm_config_service import (
            resolve_character_llm_config,
            resolve_user_default_config,
            resolve_family_default_config,
        )
        cfg_dict = await resolve_character_llm_config(character_id, user_id)
        if cfg_dict is None:
            cfg_dict = await resolve_user_default_config(user_id)
        if cfg_dict is None:
            cfg_dict = await resolve_family_default_config(user_id)
        if cfg_dict:
            api_key = cfg_dict.get("api_key") or api_key
            base_url = cfg_dict.get("base_url") or base_url
            model = cfg_dict.get("model") or model
            provider = cfg_dict.get("provider") or provider
            config_id = cfg_dict.get("config_id") or config_id
            explicit = bool(api_key or base_url)

    if not explicit:
        srv = await get_server_llm_config()
        if srv:
            api_key, base_url, model = srv.get("api_key"), srv.get("base_url"), srv.get("model")
            provider = srv.get("provider")
    api_key = api_key or (settings.llm_api_key or settings.deepseek_api_key)
    base_url = base_url or (settings.llm_base_url or settings.deepseek_base_url)
    return {"api_key": api_key, "base_url": base_url, "model": model, "provider": provider, "config_id": config_id}


async def load_character_reasoning_level(character_id: int | None) -> int:
    """读取角色「思考过程」挡位：0=关闭 / 1=简单思考 / 2=深度思考。

    与聊天主链路（chat_service._load_reasoning_level）同源；主动通道统一用它，
    保证主动消息与私聊的思考设置一致。无记录/失败返回 0。"""
    if not character_id:
        return 0
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.character import ProactiveSettings
        async with async_session_factory() as db:
            row = (await db.execute(
                select(ProactiveSettings.reasoning_level)
                .where(ProactiveSettings.character_id == character_id)
            )).scalar_one_or_none()
        return int(row or 0)
    except Exception:
        return 0


async def chat_completion(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    thinking: bool = False,
    include_reasoning: bool = False,
    api_key: str | None = None,
    base_url: str | None = None,
    provider: str | None = None,
    task: str | None = None,
    user_id: int | None = None,
    character_id: int | None = None,
) -> str | tuple[str, str]:
    """调用 LLM 获取聊天回复（通用 OpenAI 兼容，三级配置回退）

    Args:
        messages: OpenAI 格式的消息列表 [{"role": ..., "content": ...}]
        model: 模型名称（None = 用户BYOK/服务器级DB/服务器 .env 依次取）
        temperature: 温度参数 (0.0-2.0)
        max_tokens: 最大输出 token 数
        include_reasoning: True 时开启深度思考，返回 (content, reasoning_content) 元组；
            False（默认）行为不变返回纯文本字符串
        api_key/base_url: 显式覆盖（如用户级 BYOK；None = 服务器级DB -> .env）
        character_id: 角色 id（角色绑定的「我的 LLM」配置，#68，解析优先级见 resolve_character_llm_config）
    """
    cfg = await _resolve_llm_config(
        api_key=api_key, base_url=base_url, model=model, provider=provider,
        user_id=user_id, task=task, character_id=character_id,
    )
    picked_key = _pick_api_key(cfg["api_key"])  # 密钥池轮换（多 Key 按请求切换）
    if not picked_key:
        raise RuntimeError(
            "未配置 LLM API Key：请在管理端配置服务器级 API（PUT /api/v1/system/api-config/server）"
            "或在 .env 设置 LLM_API_KEY/DEEPSEEK_API_KEY"
        )
    client = _client_via_registry(cfg, picked_key)
    model_name = cfg["model"] or (settings.llm_model_name or settings.llm_model)
    _logger.info("LLM call: model=%s provider=%s temp=%s max_tokens=%d thinking=%s content_preview=%.80s",
                 model_name, cfg.get("provider"), temperature, max_tokens, thinking,
                 messages[-1].get("content", "") if messages else "")
    kwargs = dict(
        model=model_name,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    # 深度思考开关厂商适配表（2026-08-10）：不同网关开启思考的参数不同；
    # provider 优先，回退 base_url 匹配；未知厂商返回 None（跳过开关，读不到 reasoning 时优雅降级）
    def _reasoning_extra_body(provider: str | None, base_url: str) -> dict | None:
        p = (provider or "").lower()
        url = (base_url or "").lower()
        if "deepseek" in p or "deepseek" in url:
            return {"thinking": {"type": "enabled"}}
        if "dashscope" in p or "qwen" in p or "aliyun" in url:
            return {"enable_thinking": True}
        if "kimi" in p or "moonshot" in p:
            return {"thinking": True}
        if "zhipu" in p or "glm" in p or "bigmodel" in url:
            return {"thinking": {"type": "enabled"}}
        return None  # OpenAI/硅基流动等：默认思考或未知，不传开关

    # DeepSeek 网关默认关闭思考省 token；其他端点默认不思考，无需 disabled
    use_deepseek_body = "deepseek" in (cfg["base_url"] or "").lower()
    if include_reasoning:
        _reason_eb = _reasoning_extra_body(cfg.get("provider"), cfg["base_url"])
        if _reason_eb:
            kwargs["extra_body"] = _reason_eb
    elif not thinking and use_deepseek_body:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    # 单次调用硬超时（2026-08-11）：网关偶发挂起时自动失败走重试，防止吃掉任务导致聊天无响应
    _call_timeout = 90.0

    async def _create(**kw):
        _t0 = _time.monotonic()
        try:
            return await asyncio.wait_for(client.chat.completions.create(**kw), timeout=_call_timeout)
        except asyncio.TimeoutError:
            _logger.error("LLM create TIMEOUT after %.1fs (timeout=%ss)", _time.monotonic()-_t0, _call_timeout)
            raise
        except Exception:
            _logger.warning("LLM create FAILED after %.1fs: %r", _time.monotonic()-_t0, _e_str())
            raise

    def _e_str():
        import traceback
        return traceback.format_exc(limit=3)

    def _is_content_blocked(exc: BaseException) -> bool:
        """识别网关内容安全审核拒绝（如百炼 DataInspectionFailed / inappropriate content）"""
        s = str(exc)
        return any(k in s for k in (
            "data_inspection_failed", "DataInspectionFailed", "inappropriate content",
            "content_policy", "ContentFilter", "sensitive content",
        ))

    async def _fallback_create():
        """内容审核拒绝/主端点确定性失败时：优先 DB 备用配置（user_id=-1），其次 .env deepseek 兜底"""
        try:
            from app.memory.observability import obs_event
            obs_event(None, "fb_llm_fallback_create", {"model": str(model_name)[:40]})
        except Exception:
            pass
        fb_cfg = await get_fallback_llm_config()
        if fb_cfg and fb_cfg.get("api_key"):
            _fkey, _furl, _fmodel = fb_cfg["api_key"], fb_cfg["base_url"], fb_cfg["model"]
        else:
            _fkey, _furl = settings.deepseek_api_key, settings.deepseek_base_url
            _fmodel = settings.llm_model_name or settings.llm_model or "deepseek-chat"
        if not _fkey:
            raise RuntimeError(
                "内容触发了模型服务商的安全审核（400 DataInspectionFailed），"
                "且未配置降级备用模型；建议换个说法，或在 API 配置中切换其他模型后重试"
            )
        if "deepseek" in (cfg["base_url"] or "").lower():
            raise RuntimeError(
                "内容触发了模型服务商的安全审核（400 DataInspectionFailed），"
                "建议换个说法后再试"
            ) from None
        fb = get_llm_client(api_key=_fkey, base_url=_furl or settings.deepseek_base_url)
        _logger.warning("LLM content blocked by %s, fallback to model=%s", model_name, _fmodel)
        return await asyncio.wait_for(fb.chat.completions.create(
            model=_fmodel, messages=messages,
            temperature=temperature, max_tokens=max_tokens,
        ), timeout=_call_timeout)

    try:
        response = await _create(**kwargs)
    except Exception as e1:
        # 内容审核拒绝（确定性问题）：不重试同端点，直接走降级/明确报错
        if _is_content_blocked(e1):
            response = await _fallback_create()
        else:
            # 重试 1：兼容不支持 thinking 参数的服务（去掉 extra_body 后重试）
            if "extra_body" in kwargs:
                kwargs.pop("extra_body")
            try:
                response = await _create(**kwargs)
            except Exception as e2:
                if _is_content_blocked(e2):
                    response = await _fallback_create()
                else:
                    # 重试 2：重建客户端（清空连接池）再试，规避僵尸 keepalive 连接复用导致的无限挂起
                    try:
                        from app.memory.observability import obs_event
                        obs_event(None, "fb_llm_retry_rebuild", {"error": str(e2)[:80]})
                    except Exception:
                        pass
                    _clients.pop(_client_key(cfg["base_url"], picked_key), None)
                    client = _client_via_registry(cfg, picked_key)
                    response = await _create(**kwargs)
    message = response.choices[0].message
    content = message.content or ""
    resp_len = len(content)
    finish_reason = response.choices[0].finish_reason

    # usage 统计（含 reasoning token，便于核算费用）
    try:
        u = response.usage
        if u is not None:
            reasoning = getattr(getattr(u, "completion_tokens_details", None), "reasoning_tokens", None)
            # A4 批 5 / T6 成本与缓存护栏 M0 项 2(a)（2026-09-27）：把上游 usage 里**已返回但原先没采**
            # 的缓存字段一并打出来（命中数/未命中数/已缓存 prompt token），否则「缓存到底省了多少」
            # 无从观测。上游没这些字段时打 cache=-（不凭空造 0）。纯日志，不改请求/返回/记账。
            _logger.info(
                "LLM usage: prompt=%s completion=%s total=%s reasoning=%s cache=%s",
                u.prompt_tokens, u.completion_tokens, u.total_tokens, reasoning,
                _usage_cache_text(u),
            )
            _record_usage_async(cfg.get("provider"), model_name,
                                u.prompt_tokens or 0, u.completion_tokens or 0, reasoning or 0,
                                task=task,
                                user_id=user_id,
                                config_id=cfg.get("config_id"),
                                group_owner_id=await _resolve_group_owner_id(user_id))
    except Exception as e:
        _logger.warning("LLM usage log failed: %s", e)

    if finish_reason == "length":
        _logger.warning("LLM response TRUNCATED: %d chars, finish_reason=length (max_tokens=%d)", resp_len, max_tokens)
    else:
        _logger.info("LLM response: %d chars, finish_reason=%s", resp_len, finish_reason)
    if not include_reasoning:
        return content
    reasoning = getattr(message, "reasoning_content", None) or ""
    reasoning = reasoning.strip() if isinstance(reasoning, str) else ""
    if reasoning:
        _logger.info("LLM reasoning captured: %d chars", len(reasoning))
    return content, reasoning


async def _resolve_group_owner_id(user_id: int | None) -> int | None:
    """解析用量归组的家庭根账号：子账号=根账号，独立主账号=自己；失败/无 user_id 回退 user_id。"""
    if not user_id:
        return None
    try:
        from app.db.database import async_session_factory
        from app.application.family_service import get_family_root_id
        async with async_session_factory() as db:
            root = await get_family_root_id(db, user_id)
        return int(root) if root else int(user_id)
    except Exception:
        return int(user_id)


def _record_usage_async(provider: str | None, model: str | None,
                        prompt_tokens: int, completion_tokens: int,
                        reasoning_tokens: int, task: str | None = None,
                        user_id: int | None = None,
                        config_id: int | None = None,
                        group_owner_id: int | None = None,
                        estimated: bool = False) -> None:
    """异步落库单次 LLM 用量（后台任务，失败仅告警不影响主流程）

    T6-M0 项 2(b)（2026-09-27）：``estimated=True`` 表示 prompt/completion 是**估算值**
    （流式上游不回 usage）。llm_usage 表没有估算标记列、本单禁止改表，所以标记落在两处观测：
    调用处日志 + 既有 obs 通道（agent_task_logs.steps_json，route=usage_estimated，
    明细里 estimated=true），使估算行可追溯、不与实测行混读。obs 写入 fire-and-forget、
    失败静默，不影响记账主路径。
    """
    async def _do() -> None:
        try:
            from app.db.database import async_session_factory
            from app.models.agent import LlmUsage
            async with async_session_factory() as db:
                db.add(LlmUsage(
                    provider=(provider or "")[:30] or None,
                    model=(model or "")[:50] or None,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                    reasoning_tokens=reasoning_tokens,
                    task=(task or "")[:30] or None,
                    user_id=user_id,
                    config_id=config_id,
                    group_owner_id=group_owner_id,
                ))
                await db.commit()
        except Exception as e:  # 用量统计失败不影响回复
            _logger.warning("usage record failed: %s", e)

    if estimated:
        try:
            from app.memory.observability import obs_event
            obs_event(None, "usage_estimated", {
                "estimated": True,
                "model": model,
                "task": task,
                "prompt_tokens_est": prompt_tokens,
                "completion_tokens_est": completion_tokens,
            })
        except Exception:
            pass  # 观测失败不影响记账

    try:
        spawn_background(_do())
    except Exception as e:
        _logger.warning("usage task spawn failed: %s", e)


# SSE 流式无 usage 时的估算口径：与 context_builder 保持一致（2 字符 ≈ 1 token，中文保守值）
_EST_CHARS_PER_TOKEN = 2


def _estimate_completion_tokens(text: str) -> int:
    """估算 completion token 数（近似值）：按 2 字符 ≈ 1 token 折算；空文本返回 0。"""
    if not text:
        return 0
    return max(1, len(text) // _EST_CHARS_PER_TOKEN)


def _estimate_prompt_tokens(messages: list | None) -> int:
    """估算 prompt token 数（T6-M0 项 2(b)）：把进上下文的文本按同口径折算。

    只用于「上游不回 usage」时的记账观测，调用处必须带 estimated=True（绝不冒充实测值）。
    多模态消息只取 text 段——图片二进制/URL 不参与折算（本仓硬约束：图片不进 deepseek）。
    失败返回 0（fail-open，观测不得让调用失败）。
    """
    try:
        parts: list[str] = []
        for m in messages or []:
            content = m.get("content") if isinstance(m, dict) else None
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for seg in content:
                    if isinstance(seg, dict) and isinstance(seg.get("text"), str):
                        parts.append(seg["text"])
        return _estimate_completion_tokens("".join(parts))
    except Exception as e:
        _logger.warning("prompt token estimate failed: %s", e)
        return 0


# 上游 usage 里与「缓存命中」相关的既有字段（本仓上游 = openai 兼容客户端，extra=allow 透传厂商字段）：
# - prompt_cache_hit_tokens / prompt_cache_miss_tokens：DeepSeek 兼容端点在 usage 顶层返回
# - prompt_tokens_details.cached_tokens：OpenAI / 百炼兼容口径
# 只解析这三处**真实存在**的字段，不猜别的名（缺失即不打，见 _usage_cache_text）。
_USAGE_CACHE_TOP_KEYS = ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens")


def _usage_cache_fields(usage) -> dict:
    """从上游 usage 提取缓存相关字段（T6-M0 项 2(a)，纯观测）。

    返回 {字段名: 值}；上游没返回的字段**不出现**在结果里（不凭空补 0）。整体 fail-open：
    任何异常都只回已取到的部分，不影响记账主路径。
    """
    out: dict = {}
    try:
        if usage is None:
            return out
        extra = getattr(usage, "model_extra", None)
        if not isinstance(extra, dict):
            extra = {}
        for key in _USAGE_CACHE_TOP_KEYS:
            val = getattr(usage, key, None)
            if val is None:
                val = extra.get(key)
            if val is not None:
                out[key] = val
        details = getattr(usage, "prompt_tokens_details", None)
        if details is None:
            details = extra.get("prompt_tokens_details")
        cached = None
        if isinstance(details, dict):
            cached = details.get("cached_tokens")
        elif details is not None:
            cached = getattr(details, "cached_tokens", None)
        if cached is not None:
            out["cached_tokens"] = cached
    except Exception as e:
        _logger.warning("usage cache fields parse failed: %s", e)
    return out


def _usage_cache_text(usage) -> str:
    """把缓存字段渲染成日志片段（cache=k=v,...）；上游未返回则 '-'（表示无此维度，非 0 命中）。"""
    fields = _usage_cache_fields(usage)
    if not fields:
        return "-"
    return ",".join(f"{k}={fields[k]}" for k in sorted(fields))


async def chat_completion_stream(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    thinking: bool = False,
    api_key: str | None = None,
    base_url: str | None = None,
    provider: str | None = None,
    task: str | None = None,
    user_id: int | None = None,
    character_id: int | None = None,
):
    """流式调用 LLM（OpenAI 兼容，stream=True），逐 delta 产出文本增量。

    配置解析/深度思考开关与 chat_completion 一致；供语音实时链路等「边生成边处理」场景使用。
    网络/模型异常直接抛出，由调用方降级（语音链路降级纯文字）。
    """
    cfg = await _resolve_llm_config(
        api_key=api_key, base_url=base_url, model=model, provider=provider,
        user_id=user_id, task=task, character_id=character_id,
    )
    picked_key = _pick_api_key(cfg["api_key"])
    if not picked_key:
        raise RuntimeError(
            "未配置 LLM API Key：请在管理端配置服务器级 API（PUT /api/v1/system/api-config/server）"
            "或在 .env 设置 LLM_API_KEY/DEEPSEEK_API_KEY"
        )
    client = _client_via_registry(cfg, picked_key)
    model_name = cfg["model"] or (settings.llm_model_name or settings.llm_model)
    _logger.info("LLM stream call: model=%s provider=%s temp=%s max_tokens=%d content_preview=%.80s",
                 model_name, cfg.get("provider"), temperature, max_tokens,
                 messages[-1].get("content", "") if messages else "")
    kwargs = dict(
        model=model_name,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
    )
    # DeepSeek 网关默认关闭思考省 token（与 chat_completion 一致）
    use_deepseek_body = "deepseek" in (cfg["base_url"] or "").lower()
    if not thinking and use_deepseek_body:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    _call_timeout = 90.0
    stream = await asyncio.wait_for(
        client.chat.completions.create(**kwargs), timeout=_call_timeout
    )
    # usage 统计（SSE 流式遗留#57）：优先取流式末尾 chunk 的 usage 字段；
    # 若供应商不返回 usage，按各 delta 累计文本估算 completion tokens（近似）并注明估算。
    _last_usage = None
    _est_parts: list[str] = []
    async for chunk in stream:
        _u = getattr(chunk, "usage", None)
        if _u is not None:
            _last_usage = _u
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        piece = getattr(delta, "content", None)
        if piece:
            _est_parts.append(piece)
            yield piece
    try:
        if _last_usage is not None:
            _reasoning = getattr(getattr(_last_usage, "completion_tokens_details", None), "reasoning_tokens", None)
            # T6-M0 项 2(a)：流式同样补缓存维度（口径与非流式一致；上游未返回则 cache=-）
            _logger.info(
                "LLM stream usage: prompt=%s completion=%s total=%s reasoning=%s cache=%s",
                _last_usage.prompt_tokens, _last_usage.completion_tokens, _last_usage.total_tokens, _reasoning,
                _usage_cache_text(_last_usage),
            )
            _record_usage_async(cfg.get("provider"), model_name,
                                _last_usage.prompt_tokens or 0, _last_usage.completion_tokens or 0,
                                _reasoning or 0, task=task,
                                user_id=user_id,
                                config_id=cfg.get("config_id"),
                                group_owner_id=await _resolve_group_owner_id(user_id))
        else:
            # T6-M0 项 2(b)（2026-09-27）：上游 SSE 不回 usage 时，prompt 侧原来硬记 0，
            # 使「一次流式调用的成本」被系统性低估成只剩输出（护栏/预算判断全失真）。
            # 改为按既有同口径估算输入 token（字符数 / _EST_CHARS_PER_TOKEN，与
            # context_builder._EST_CHARS_PER_TOKEN 一致），并显式标 estimated=True：
            # 估算值绝不冒充实测值（库里无估算标记列、本单禁止改表，故标记落在日志 + 既有 obs 明细）。
            _est_prompt = _estimate_prompt_tokens(messages)
            _est_tokens = _estimate_completion_tokens("".join(_est_parts))
            _logger.warning(
                "LLM stream usage: NO usage in stream, estimated=true prompt=%d completion=%d (chars/%d, approx)",
                _est_prompt, _est_tokens, _EST_CHARS_PER_TOKEN,
            )
            _record_usage_async(cfg.get("provider"), model_name, _est_prompt, _est_tokens, 0, task=task,
                                user_id=user_id,
                                config_id=cfg.get("config_id"),
                                group_owner_id=await _resolve_group_owner_id(user_id),
                                estimated=True)
    except Exception as e:
        _logger.warning("LLM stream usage log failed: %s", e)
