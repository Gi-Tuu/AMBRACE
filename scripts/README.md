# scripts/ 目录说明（F0 归类，2026-08-31）

## 根目录（常驻入口/运维工具，勿随意移动——AGENTS.md 与控制台引用）
- `server_manager.py` 服务器管理统一入口（status/repair/start/stop/restart；排查手册见文件头）
- `watchdog.py` 服务器守护进程（pythonw 无窗口）
- `backup.py` 每日备份（源码+sqlite+配置 → backups/YYYYMMDD.zip，顺带清理轮转日志）
- `edit.py` 文件编辑统一入口（patch.json 内容匹配替换，自动保行尾 + py_compile）
- `verify.py` 一键验证（ruff + py_compile + pytest + flutter analyze/test [+ --smoke]）
- `smoke_test.py` 接口冒烟
- `init_db.py` 初始化数据库
- `download_models.py` 下载本地模型（bge-m3 / whisper）
- `make_release.py` / `publish_plugin.py` 发布相关
- `scan_cn.py` i18n 硬编码中文扫描（改动自检用）
- `token_budget_report.py` token 消耗报表（配合 docs/token-budget.md）
- `get_server_info.py` 服务器信息
- `dev_account_activity.py` 开发观察工具
- `setup_local_vlm.py` / `generate_pet_sprites.py` / `import_pet_assets.py` 资产与环境准备

## diagnostics/（诊断与评测，可反复运行）
认知健康/生活事件一致性检查、行为/检索基准评测（evaluate_*）。

## oneoff/（一次性脚本，跑完归档，不再维护）
历史数据回填（backfill_*）、一次性修复（fix_group_memory_speaker）、一次性重建（rebuild_embeddings_m3）。
