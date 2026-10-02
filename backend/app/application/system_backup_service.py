"""备份触发与下载应用服务（A22 第四刀，2026-10-02）。

本模块自 ``app/application/system.py`` 逐字节搬入。边界＝**备份触发与下载**。

跨块调用约定：``_require_server_admin`` / ``_audit`` / ``_load_backup_module`` 仍按 tests 与
调用方的既有口径挂在 ``system.py`` 上，故一律在函数内 ``from app.application import system as
_sys`` 后走 ``_sys.<name>``（放顶层会与 system.py 的重导出成环；放模块顶层还会让打在 system
上的桩静默失效）。
"""
from datetime import datetime

from fastapi import HTTPException

from app.i18n import tr_lang
from app.utils.logger import get_logger

_logger = get_logger("application.system")


def _load_backup_module():
    """按文件路径加载 scripts/backup.py（repo 根不一定在 sys.path，故显式按路径导入）。

    返回的模块带 .BACKUP_ROOT / .do_backup()，与脚本命令行同一实现（单一数据源）。
    """
    from pathlib import Path as _P
    import importlib.util as _ilu
    path = _P(__file__).resolve().parents[3] / "scripts" / "backup.py"
    spec = _ilu.spec_from_file_location("ambrace_backup", str(path))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _backup_info(zip_path: str) -> dict:
    import os as _os
    size = _os.path.getsize(zip_path) if _os.path.isfile(zip_path) else 0
    created = _os.path.getmtime(zip_path) if _os.path.isfile(zip_path) else 0
    return {
        "path": _os.path.basename(zip_path),
        "size": size,
        "created_at": datetime.fromtimestamp(created).isoformat() if created else None,
    }


async def trigger_backup(
    user_id: int,
    lang: str,
):
    """触发一次备份（数据库 + 配置 + 源码快照），仅服务器控制台管理员。

    当天已有备份（如多次调用）则直接返回现有文件信息；返回 {path, size, created_at}。
    """
    from app.application import system as _sys
    await _sys._require_server_admin(user_id, lang)
    import os as _os
    mod = _sys._load_backup_module()
    try:
        # do_backup：运行中库用 SQLite backup API 安全复制，并做日志轮换 / 过期备份清理
        mod.do_backup()
    except Exception as e:
        _logger.error("backup triggered failed: %s", e)
        raise HTTPException(status_code=500, detail=tr_lang(lang, "backup_failed"))
    today = mod.backup_day_key()  # 批 2b：与生产端同源（应用本地时区），勿再自行 datetime.now()
    zip_path = _os.path.join(mod.BACKUP_ROOT, f"{today}.zip")
    if not _os.path.isfile(zip_path):
        raise HTTPException(status_code=500, detail=tr_lang(lang, "backup_failed"))
    info = {"status": "ok", **_backup_info(zip_path)}
    await _sys._audit(None, user_id, "server.backup.trigger", "backup", None,
                      {"path": info.get("path"), "size": info.get("size")})
    return info


async def download_backup(
    user_id: int,
    lang: str,
):
    """下载当天 / 最近一份备份 zip（仅服务器控制台管理员）；文件名用 ascii 安全名。

    纯读动作（无状态变更），按契约 §1.5「写动作覆盖面」不进审计表。
    """
    from app.application import system as _sys
    await _sys._require_server_admin(user_id, lang)
    import os as _os
    from fastapi.responses import FileResponse
    mod = _sys._load_backup_module()
    candidate = None
    today = mod.backup_day_key()  # 批 2b：同 trigger_backup，取「应用本地时区」的今天
    today_zip = _os.path.join(mod.BACKUP_ROOT, f"{today}.zip")
    if _os.path.isfile(today_zip):
        candidate = today_zip
    else:
        try:
            zips = [f for f in _os.listdir(mod.BACKUP_ROOT) if f.endswith(".zip")]
            if zips:
                zips.sort(reverse=True)
                candidate = _os.path.join(mod.BACKUP_ROOT, zips[0])
        except Exception as e:
            _logger.warning("backup download list failed: %s", e)
            candidate = None
    if not candidate or not _os.path.isfile(candidate):
        raise HTTPException(status_code=404, detail=tr_lang(lang, "backup_not_found"))
    ascii_name = "ambrace-backup-" + _os.path.basename(candidate).replace(".zip", "") + ".zip"
    return FileResponse(candidate, media_type="application/zip", filename=ascii_name)
