# -*- coding: utf-8 -*-
"""网关账号落盘（扫码绑定下放手机，2026-09-12）。

把 App 扫码 confirmed 拿到的凭据写成 openclaw 网关账号文件（~/.openclaw/openclaw-weixin/），
等价于「openclaw channels login」的落盘动作（同一协议、同一字段结构，实测账号文件
{token, savedAt, baseUrl, userId} + accounts.json 索引）。网关**启动时**发现账号（无热加载，
源码核实），写盘后需重启网关一次才开始拉取新 bot 消息（由调用方提示，不自动杀网关进程）。

设计口径：
- **只加不改**：已注册的 account_id 不覆盖（防旧凭据被扫码轮换意外冲掉——重绑定走 /rebind）；
- best-effort：任何读写失败返回 False 并记日志，绝不影响绑定主流程（凭据已加密落拥爱库）；
- 路径可注入（state_dir）便于测试隔离；env MULTIBOT_WX_DIR 优先（与 available_bots 同口径）。

【命名空间】渠道插件顶层模块名易撞，沿用包内相对导入/文件名（与 available_bots 同理）。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_OPENCLAW_STATE_DIR = Path.home() / ".openclaw" / "openclaw-weixin"


def _state_dir(state_dir: Path | None = None) -> Path:
    if state_dir:
        return Path(state_dir)
    env = os.environ.get("MULTIBOT_WX_DIR")
    return Path(env) if env else DEFAULT_OPENCLAW_STATE_DIR


def list_account_ids(state_dir: Path | None = None) -> list[str]:
    """accounts.json 索引里的 account_id 列表（损坏/缺失按空处理）。"""
    idx = _state_dir(state_dir) / "accounts.json"
    try:
        ids = json.loads(idx.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(ids, list):
        return []
    return [str(a).strip() for a in ids if str(a).strip()]


def read_local_bot_tokens(state_dir: Path | None = None, limit: int = 10) -> list[str]:
    """本机已登录 bot token（最新注册在前，最多 limit 个）——取码时作 local_token_list 上报，
    对齐腾讯官方 getLocalBotTokenList（上游据此判定扫码 bot 是否已连接过 → binded_redirect）。"""
    base = _state_dir(state_dir)
    ids = list_account_ids(state_dir)
    tokens: list[str] = []
    for aid in reversed(ids):
        if len(tokens) >= limit:
            break
        try:
            data = json.loads((base / "accounts" / f"{aid}.json").read_text(encoding="utf-8"))
            token = str(data.get("token") or "").strip()
            if token:
                tokens.append(token)
        except Exception:
            continue
    return tokens


def account_registered(account_id: str, state_dir: Path | None = None) -> bool:
    """该 account_id 是否已在网关账号索引里。"""
    return str(account_id or "").strip() in list_account_ids(state_dir)


def register_account(
    account_id: str,
    token: str,
    baseurl: str = "",
    user_id: str = "",
    state_dir: Path | None = None,
) -> bool:
    """把扫码 confirmed 凭据写成网关账号文件并登记索引（幂等：已存在则跳过返回 True）。

    文件结构与网关自登录一致：accounts/{id}.json = {token, savedAt(ISO UTC), baseUrl, userId}。
    写盘策略：tmp + replace 原子替换；索引先读后写（并发窗口由单实例部署 + GIL 兜底）。
    返回是否「网关侧已就绪」（含此前已注册）；False = 写盘失败（调用方提示需人工 openclaw login）。
    """
    aid = str(account_id or "").strip()
    token = str(token or "").strip()
    if not aid or not token:
        return False
    base = _state_dir(state_dir)
    acc_dir = base / "accounts"
    try:
        acc_dir.mkdir(parents=True, exist_ok=True)
        ids = list_account_ids(state_dir)
        if aid in ids:
            return True
        acc_file = acc_dir / f"{aid}.json"
        payload = {
            "token": token,
            "savedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "baseUrl": (str(baseurl or "").strip() or "https://ilinkai.weixin.qq.com"),
            "userId": str(user_id or "").strip(),
        }
        tmp = acc_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, acc_file)
        ids.append(aid)
        tmp_idx = base / "accounts.json.tmp"
        tmp_idx.write_text(json.dumps(ids, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_idx, base / "accounts.json")
        return True
    except Exception:
        try:
            from app.plugins import sdk  # noqa: PLC0415

            sdk.log("wechat_ilink 网关账号落盘失败 account=%s（不影响绑定，需人工 openclaw login）", aid)
        except Exception:
            pass
        return False
