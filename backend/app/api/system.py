"""系统状态 API + API 自主配置——F5 瘦身后只留 FastAPI 壳：收参 → 调 application 服务 → 返回。

业务体在 app/application/system.py（F5-c，2026-08-31 迁入）；本文件保留路由与参数依赖
注入 + health/WebSocket 两个纯传输端点（门面重导出已随 F8 删旧移除，历史 import 路径
请改指 app.application.system）。
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, WebSocket

from app.application import system as _svc
from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.utils.logger import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/v1/system", tags=["System"])
_logger = get_logger("api.system")


@router.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/ready")
async def ready_check():
    """就绪检查（P2-4）：数据库可连接 + 向量模型可用。任一失败返回 503 + 明细。"""
    return await _svc.ready_check()


@router.get("/status")
async def system_status():
    """服务器运行状态（含局域网 IP 与图片理解配置状态，便于部署者填手机端服务器地址）"""
    return await _svc.system_status()


# ── API 自主配置（BYOK：通用 LLM 用户级覆盖，OpenAI 兼容端点）──

@router.get("/api-config")
async def get_api_config(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """读取用户级 API 配置（api_key 不回传明文）"""
    return await _svc.get_api_config(db, user_id)


@router.put("/api-config")
async def update_api_config(data: dict, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """写入用户级 API 配置（BYOK：聊天主链路启用后优先于服务器默认）"""
    return await _svc.update_api_config(db, data, user_id)


# ── 服务器级全局 API 配置（开源部署：填一次全局 key，代码/.env 零密钥；仅主账号 user_id=1 可读写）──

@router.get("/api-config/server")
async def get_server_api_config(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """读取服务器级全局 API 配置（api_key 不回传明文）"""
    return await _svc.get_server_api_config(db, user_id, lang)


@router.put("/api-config/server")
async def update_server_api_config(data: dict, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """写入服务器级全局 API 配置（影响所有未配 BYOK 的调用；仅主账号）"""
    return await _svc.update_server_api_config(db, data, user_id, lang)


# ── 任务专用 LLM 配置（按用途指定模型 + 密钥池 + 连接测试；P1②，2026-08-12）──

@router.get("/api-config/tasks")
async def get_task_llm_catalog(user_id: int = Depends(get_current_user_id)):
    """任务目录（供前端渲染任务选择）"""
    return await _svc.get_task_llm_catalog(user_id)


@router.get("/api-config/task/{task}")
async def get_task_api_config(task: str, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """读取用户级任务 LLM 配置（api_key 不回传明文）"""
    return await _svc.get_task_api_config(db, task, user_id)


@router.put("/api-config/task/{task}")
async def update_task_api_config(task: str, data: dict, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """写入用户级任务 LLM 配置（upsert）"""
    return await _svc.update_task_api_config(db, task, data, user_id)


@router.get("/api-config/task/server/{task}")
async def get_server_task_api_config(task: str, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """读取服务器级任务 LLM 配置（仅主账号）"""
    return await _svc.get_server_task_api_config(db, task, user_id, lang)


@router.put("/api-config/task/server/{task}")
async def update_server_task_api_config(task: str, data: dict, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """写入服务器级任务 LLM 配置（仅主账号；影响所有用户的该任务调用）"""
    return await _svc.update_server_task_api_config(db, task, data, user_id, lang)


@router.post("/api-config/test")
async def test_api_connection(body: dict, user_id: int = Depends(get_current_user_id)):
    """连接测试：最小请求校验 {base_url, api_key, model}；api_key 为空则用服务器级全局配置"""
    return await _svc.test_api_connection(body, user_id)


# ── 生图服务器级全局配置（开源部署：填一次，key 不进 .env；仅主账号 user_id=1 可读写）──

@router.get("/image-gen-config/server")
async def get_image_gen_server_config(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """读取服务器级生图配置（api_key 不回传明文）"""
    return await _svc.get_image_gen_server_config(db, user_id, lang)


@router.put("/image-gen-config/server")
async def update_image_gen_server_config(data: dict, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """写入服务器级生图配置（影响聊天内 AI 发图与 /images 接口；仅主账号）"""
    return await _svc.update_image_gen_server_config(db, data, user_id, lang)


# ── 识图（图片理解）服务器级全局配置（开源部署：填一次，key 不进 .env；仅主账号 user_id=1 可读写）──

@router.get("/vlm-config/server")
async def get_vlm_server_config(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """读取服务器级识图配置（api_key 不回传明文）"""
    return await _svc.get_vlm_server_config(db, user_id, lang)


@router.put("/vlm-config/server")
async def update_vlm_server_config(data: dict, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """写入服务器级识图配置（影响聊天/手机感知等图片理解；仅主账号）"""
    return await _svc.update_vlm_server_config(db, data, user_id, lang)


# ── 语音大模型服务器级全局配置（开源部署：填一次，key 不进 .env；仅主账号 user_id=1 可读写）──

@router.get("/speech-config/server")
async def get_speech_server_config(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """读取服务器级语音大模型配置（api_key 不回传明文）"""
    return await _svc.get_speech_server_config(db, user_id, lang)


@router.put("/speech-config/server")
async def update_speech_server_config(data: dict, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """写入服务器级语音大模型配置（仅主账号）"""
    return await _svc.update_speech_server_config(db, data, user_id, lang)


@router.post("/speech-preview")
async def speech_preview(data: dict, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """音色试听：用固定基础文案合成当前音色/语速/语调，返回音频 URL（角色编辑页试听用）"""
    return await _svc.speech_preview(data, user_id, lang)


@router.get("/updates")
async def get_updates():
    """更新公告：解析 docs/changelog.md，按天折叠（最新在前），供 app 内「更新公告」页展示"""
    return await _svc.get_updates()


# ── LLM token 用量与免费额度（2026-08-11：用量落库展示；总量仅主账号可设）──
@router.get("/llm-usage")
async def get_llm_usage(user_id: int = Depends(get_current_user_id)):
    """token 用量统计：今日/近7天/本月/累计 + 按模型汇总 + 剩余额度（#68 P6 组聚合）"""
    return await _svc.get_llm_usage(user_id)


@router.put("/llm-usage/limit")
async def update_llm_usage_limit(body: dict, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """设置免费额度总量（tokens，仅主账号；0=清除总额设置）"""
    return await _svc.update_llm_usage_limit(body, user_id, lang)


# ── 运行时 Feature Flag 开关（2026-08-18：DB 覆盖 + API 热更新，无需重启）──

@router.get('/feature-flags')
async def get_feature_flags(user_id: int = Depends(get_current_user_id), lang: str = Header(default='zh')):
    '''读取全部运行时 Feature Flag（主账号）；source: db=DB 覆盖 / default=硬编码默认'''
    return await _svc.get_feature_flags(user_id, lang)


@router.put('/feature-flags/{key}')
async def update_feature_flag(key: str, data: dict, user_id: int = Depends(get_current_user_id), lang: str = Header(default='zh')):
    '''切换 Feature Flag（主账号）：写 DB + 热更新内存立即生效；未知 key 返回 404'''
    return await _svc.update_feature_flag(key, data, user_id, lang)


# ── 备份一键导出（#54，2026-08-23：仅主账号；复用 scripts/backup.do_backup）──

@router.post("/backup")
async def trigger_backup(user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """触发一次备份（数据库 + 配置 + 源码快照），仅主账号"""
    return await _svc.trigger_backup(user_id, lang)


@router.get("/backup/download")
async def download_backup(user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """下载当天 / 最近一份备份 zip（仅主账号）；文件名用 ascii 安全名。"""
    return await _svc.download_backup(user_id, lang)


# ── 用户级通知 WebSocket（#55 Android 前台服务保活：后台 isolate 维持长连接实时收推送）──
# 与 chat.py 的会话级 WS 不同：以 user_id 为粒度广播「新 AI 消息 / 主动消息」事件。
# 纯传输端点（连接生命周期+鉴权），业务在 app/ws/notify_manager——F5-c 不迁，留壳。

@router.websocket("/notifications/ws")
async def notifications_ws(websocket: WebSocket):
    """用户级通知长连接（?token= 鉴权）。

    服务端把主动消息 / 新 AI 消息事件实时推给该用户的所有连接（主 isolate + 后台 isolate），
    客户端可发 {"type": "ping"} 维持连接，服务端回 {"type": "pong"}。
    """
    from jose import jwt, JWTError
    from app.auth.config import auth_settings as _as

    token = websocket.query_params.get("token", "")
    try:
        payload = jwt.decode(token, _as.secret_key, algorithms=[_as.algorithm])
        ws_user_id = payload.get("user_id")
    except JWTError:
        ws_user_id = None
    if ws_user_id is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    from app.ws.notify_manager import register, unregister
    _uid = int(ws_user_id)
    await register(_uid, websocket)
    try:
        # 连接建立即上报一次，客户端可用它确认在线状态
        await websocket.send_json({"type": "connected"})
        while True:
            data = await websocket.receive_json()
            if isinstance(data, dict) and data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except Exception:
        pass
    finally:
        await unregister(_uid, websocket)
