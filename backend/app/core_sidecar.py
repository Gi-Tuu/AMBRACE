# -*- coding: utf-8 -*-
"""M8 Core sidecar launcher（standard library only）。

职责：
- 选空闲端口、生成 per-install service key；
- 以子进程启动 `python -m yai_core.server`（或 console script），注入 env；
- 等 `/readyz` + `/v1/tools` 工具数达到期望值（write 关闭=0，开启=4）；
- 收集 stdout/stderr（轮转交给外部日志系统或按大小切）；
- 关闭：POST /v1/shutdown（service-key）+ 等待，超时 terminate/kill。

契约：docs/yai/m8-packaging-contract.md v1.0
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from dataclasses import dataclass, field
from pathlib import Path


def find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def generate_service_key() -> str:
    return secrets.token_urlsafe(32)


def resolve_llm_env(env: dict | None = None) -> tuple[str, str, str, str]:
    """按优先级解析模型 env：CORE_LLM_* > OPENAI_* > legacy LLM_* > DEEPSEEK_*。

    返回 (api_key, base_url, model, strong_model)。Anthropic 原生协议在 sidecar v1 不支持。
    """
    e = env or os.environ

    def pick(*names: str) -> str:
        for n in names:
            v = e.get(n, "").strip()
            if v:
                return v
        return ""

    key = pick("CORE_LLM_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY", "DEEPSEEK_API_KEY")
    base = pick("CORE_LLM_BASE_URL", "OPENAI_BASE_URL", "LLM_BASE_URL", "DEEPSEEK_BASE_URL")
    model = pick("CORE_LLM_MODEL", "LLM_MODEL", "OPENAI_MODEL", "DEEPSEEK_MODEL")
    strong = pick("CORE_LLM_STRONG_MODEL", "LLM_STRONG_MODEL")
    probe = f"{base} {model}".lower()
    if "anthropic" in probe or model.lower().startswith("claude"):
        raise ValueError("sidecar v1 仅支持 OpenAI-compatible 模型（Anthropic 原生协议不支持）")
    return key, base, model, strong


@dataclass
class SidecarConfig:
    ambrace_base_url: str = "http://127.0.0.1:8000"
    host: str = "127.0.0.1"
    port: int = 0
    service_key: str = field(default_factory=generate_service_key)
    ambrace_service_token: str = ""
    allow_writes: bool = False
    tool_timeout: float = 60.0
    python: str = sys.executable
    core_cmd: tuple[str, ...] = ("-m", "yai_core.server")
    log_dir: str = ""
    cwd: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_strong_model: str = ""
    git_commit: str = ""
    offline: bool = False
    start_mode: str = "local"  # local=launcher 拉起；external=compose 已起，只连

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def expected_tools(self) -> int:
        # 安全默认：不开启写工具时 AMBRACE 4 个 POST 工具一个都不会注册（0 是合法状态）
        return 4 if self.allow_writes else 0

    def env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update({
            "YAI_SERVICE_KEY": self.service_key,
            "OPENAPI_SPEC_URL": f"{self.ambrace_base_url}/openapi.json",
            "OPENAPI_BASE_URL": self.ambrace_base_url,
            "OPENAPI_AUTH_TOKEN": self.ambrace_service_token,
            "OPENAPI_ALLOW_WRITES": "true" if self.allow_writes else "false",
            "YAI_TOOL_TIMEOUT": str(self.tool_timeout),
        })
        if self.llm_api_key:
            env["OPENAI_API_KEY"] = self.llm_api_key
        if self.llm_base_url:
            env["OPENAI_BASE_URL"] = self.llm_base_url
        if self.llm_model:
            env["LLM_MODEL"] = self.llm_model
        if self.llm_strong_model:
            env["LLM_STRONG_MODEL"] = self.llm_strong_model
        if self.git_commit:
            env["YAI_GIT_COMMIT"] = self.git_commit
        if self.offline:
            env["YAI_SIDECAR_OFFLINE"] = "1"
        return env


class CoreSidecar:
    def __init__(self, cfg: SidecarConfig):
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self.port = 0

    # ---------- lifecycle ----------
    def start(self) -> "CoreSidecar":
        if self.cfg.start_mode == "external":
            # compose 模式：Core 由 compose 提供，launcher 只负责探测/连接
            return self
        if self.cfg.port == 0:
            self.cfg.port = find_free_port(self.cfg.host)
        self.port = self.cfg.port
        log_dir = Path(self.cfg.log_dir or "logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        out = open(log_dir / "core-sidecar.out.log", "ab", buffering=0)
        err = open(log_dir / "core-sidecar.err.log", "ab", buffering=0)
        self._log_files = (out, err)
        cmd = [self.cfg.python, *self.cfg.core_cmd, "--host", self.cfg.host, "--port", str(self.port)]
        self.proc = subprocess.Popen(
            cmd, env=self.cfg.env(), cwd=self.cfg.cwd or None,
            stdout=out, stderr=err, stdin=subprocess.DEVNULL,
        )
        return self

    def wait_ready(self, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                return False  # 子进程已退出，别空等
            try:
                if self._get("/readyz").get("status") in ("ok", "ready", "degraded"):
                    tools = self._get("/v1/tools")
                    items = tools.get("tools", []) if isinstance(tools, dict) else tools
                    if len(items or []) == self.cfg.expected_tools:
                        return True
            except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
                pass
            time.sleep(0.25)
        return False

    def tools(self) -> list[dict]:
        data = self._get("/v1/tools")
        items = data.get("tools", []) if isinstance(data, dict) else data
        return [i for i in items if isinstance(i, dict)]

    def stop(self, timeout: float = 10.0) -> int | None:
        if self.proc is None:
            return None
        try:
            self._post("/v1/shutdown", {})
        except Exception:
            pass
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                return self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                return self.proc.wait(timeout=5)

    # ---------- http ----------
    def _request(self, method: str, path: str, body: dict | None = None, timeout: float = 5.0):
        req = urllib.request.Request(self.cfg.base_url + path, method=method)
        if self.cfg.service_key:
            req.add_header("X-Service-Key", self.cfg.service_key)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
            raw = r.read().decode() or "{}"
            return json.loads(raw)

    def _get(self, path: str):
        return self._request("GET", path)

    def _post(self, path: str, body: dict):
        return self._request("POST", path, body)


def main() -> int:
    """运维 CLI：local=拉起；external=只连 CORE_BASE_URL（compose）。"""
    mode = os.environ.get("CORE_START_MODE", "local")
    core_port = int(os.environ.get("CORE_PORT", "0") or 0)
    external_url = os.environ.get("CORE_BASE_URL", "")
    if mode == "external" and not external_url:
        print("CORE_START_MODE=external 需要 CORE_BASE_URL", file=sys.stderr)
        return 2
    host, port = "127.0.0.1", core_port
    if mode == "external":
        parsed = urlparse(external_url)
        host, port = parsed.hostname or "127.0.0.1", parsed.port or 80
    try:
        _llm = resolve_llm_env()
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 3
    cfg = SidecarConfig(
        host=host, port=port, start_mode=mode,
        service_key=os.environ.get("YAI_SERVICE_KEY") or os.environ.get("CORE_SERVICE_TOKEN", ""),
        ambrace_base_url=os.environ.get("AMBRACE_BASE_URL", "http://127.0.0.1:8000"),
        allow_writes=os.environ.get("OPENAPI_ALLOW_WRITES", "false").lower() in ("1", "true", "yes", "on"),
        ambrace_service_token=os.environ.get("AMBRACE_SERVICE_TOKEN", ""),
        log_dir=os.environ.get("CORE_SIDECAR_LOG_DIR", "logs"),
        python=os.environ.get("CORE_SIDECAR_PYTHON", sys.executable),
        llm_api_key=_llm[0], llm_base_url=_llm[1], llm_model=_llm[2], llm_strong_model=_llm[3],
        git_commit=os.environ.get("YAI_GIT_COMMIT", ""),
        offline=os.environ.get("YAI_SIDECAR_OFFLINE", "") == "1",
    )
    sidecar = CoreSidecar(cfg).start()
    if not sidecar.wait_ready(timeout=float(os.environ.get("CORE_SIDECAR_READY_TIMEOUT", "30"))):
        print(f"core sidecar failed to become ready on {cfg.base_url}", file=sys.stderr)
        sidecar.stop()
        return 1
    print(f"core sidecar ready: {cfg.base_url} (tools={cfg.expected_tools})")
    if cfg.start_mode == "external":
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            return 0
    try:
        code = sidecar.proc.wait() if sidecar.proc else 0
    except KeyboardInterrupt:
        code = sidecar.stop() or 0
    return int(code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
