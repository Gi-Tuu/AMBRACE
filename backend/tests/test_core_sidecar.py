# -*- coding: utf-8 -*-
"""M8 sidecar launcher：工具数期望、readyz、shutdown（用假 Core HTTP 服务）。"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.core_sidecar import CoreSidecar, SidecarConfig, find_free_port, generate_service_key, resolve_llm_env


class _FakeCore(BaseHTTPRequestHandler):
    tools = 0
    shutdown_called = False

    def log_message(self, *args):
        pass

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/readyz":
            self._send(200, {"status": "ok"})
        elif self.path == "/v1/tools":
            self._send(200, [{"name": f"t{i}", "source": "openapi"} for i in range(self.tools)])
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/v1/shutdown":
            type(self).shutdown_called = True
            self._send(202, {"status": "shutting_down"})
        else:
            self._send(404, {"error": "not found"})


def _serve(tools: int):
    _FakeCore.tools = tools
    _FakeCore.shutdown_called = False
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeCore)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_expected_tools_default_zero():
    cfg = SidecarConfig()
    assert cfg.expected_tools == 0
    assert cfg.env()["OPENAPI_ALLOW_WRITES"] == "false"


def test_expected_tools_four_when_writes_enabled():
    cfg = SidecarConfig(allow_writes=True)
    assert cfg.expected_tools == 4
    assert cfg.env()["OPENAPI_ALLOW_WRITES"] == "true"


def test_wait_ready_accepts_zero_tools():
    srv = _serve(0)
    cfg = SidecarConfig(port=srv.server_address[1])
    sc = CoreSidecar(cfg)
    assert sc.wait_ready(timeout=3) is True
    srv.shutdown()


def test_wait_ready_requires_four_when_enabled():
    srv = _serve(0)
    cfg = SidecarConfig(port=srv.server_address[1], allow_writes=True)
    sc = CoreSidecar(cfg)
    assert sc.wait_ready(timeout=1) is False
    srv.shutdown()


def test_stop_calls_shutdown_endpoint():
    srv = _serve(0)
    cfg = SidecarConfig(port=srv.server_address[1])
    sc = CoreSidecar(cfg)
    sc._post("/v1/shutdown", {})
    assert _FakeCore.shutdown_called is True
    srv.shutdown()


def test_external_mode_does_not_spawn():
    srv = _serve(0)
    cfg = SidecarConfig(port=srv.server_address[1], start_mode="external")
    sc = CoreSidecar(cfg).start()
    assert sc.proc is None
    assert sc.wait_ready(timeout=3) is True
    srv.shutdown()


def test_key_and_port_helpers():
    assert len(generate_service_key()) >= 40
    p = find_free_port()
    assert 1024 < p < 65536


def test_llm_env_priority():
    env = {
        "CORE_LLM_API_KEY": "core-key", "OPENAI_API_KEY": "openai-key",
        "LLM_API_KEY": "legacy-key", "DEEPSEEK_API_KEY": "deepseek-key",
        "CORE_LLM_BASE_URL": "https://core.example/v1",
        "OPENAI_BASE_URL": "https://openai.example/v1",
        "LLM_BASE_URL": "https://legacy.example/v1",
        "DEEPSEEK_BASE_URL": "https://deepseek.example/v1",
        "LLM_MODEL": "m-legacy", "CORE_LLM_MODEL": "m-core",
        "LLM_STRONG_MODEL": "m-strong",
    }
    key, base, model, strong = resolve_llm_env(env)
    assert (key, base, model, strong) == ("core-key", "https://core.example/v1", "m-core", "m-strong")
    env.pop("CORE_LLM_API_KEY"); env.pop("CORE_LLM_BASE_URL"); env.pop("CORE_LLM_MODEL")
    assert resolve_llm_env(env)[0] == "openai-key"
    env.pop("OPENAI_API_KEY")
    assert resolve_llm_env(env)[0] == "legacy-key"
    env.pop("LLM_API_KEY")
    assert resolve_llm_env(env)[0] == "deepseek-key"


def test_llm_env_rejects_anthropic():
    try:
        resolve_llm_env({"OPENAI_BASE_URL": "https://api.anthropic.com", "LLM_MODEL": "claude-3"})
    except ValueError as e:
        assert "OpenAI-compatible" in str(e)
    else:
        raise AssertionError("anthropic should be rejected")
