"""Key-free local transport, offline configuration and exclusive routing."""
import importlib
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import Mock

import click
from click.testing import CliRunner
from pydantic import BaseModel
import pytest
import requests

from pdd.local_llm import (
    LocalLLMConfig, LocalLLMError, get_local_llm_config,
    invoke_local_llm, local_llm_environment, local_llm_estimate,
)


@pytest.fixture(autouse=True)
def isolated_local_settings(monkeypatch, tmp_path):
    """Never use the developer's endpoint or ambient keys in unit tests."""
    for key in tuple(os.environ):
        if key.startswith("PDD_LOCAL_LLM_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PDD_LOCAL_LLM_ENABLED", "1")
    monkeypatch.setenv("PDD_LOCAL_LLM_BASE_URL", "http://127.0.0.1:8080")


def completion(content="answer", reason="stop", **message):
    return {"choices": [{"message": {"content": content, **message}, "finish_reason": reason}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}}


@pytest.fixture
def http_server():
    """Real HTTP protocol shared by native macOS and Linux/container servers."""
    calls = []
    replies = [(200, {"data": [{"id": "local-gguf"}]}), (200, completion())]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            calls.append((self.command, self.path, dict(self.headers), json.loads(body) if body else None))
            status, payload = replies.pop(0)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if status == 302:
                self.send_header("Location", "https://must-never-contact.invalid/v1")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        do_POST = do_GET

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls, replies
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("address,expected", [
    ("http://127.0.0.1:8080", "http://127.0.0.1:8080/v1"),
    ("http://192.168.1.20:8080/v1/", "http://192.168.1.20:8080/v1"),
    ("http://[::1]:8080", "http://[::1]:8080/v1"),
    ("https://linux-box.local/api/v1", "https://linux-box.local/api/v1"),
])
def test_portable_addresses(address, expected):
    assert LocalLLMConfig(address).base_url == expected


@pytest.mark.parametrize("address", [
    "", "file:///tmp/model", "http://0.0.0.0:8080", "http://[::]:8080",
    "http://user:secret@host", "http://host?key=secret", "http://host#fragment",
    "http://host:0", "http://host:99999", "http://host/v1/chat/completions",
    "http://host/v1/models", "http://some host", None,
])
def test_bad_address_fails_closed(address):
    with pytest.raises(LocalLLMError):
        LocalLLMConfig(address)


@pytest.mark.parametrize("settings", [
    {"max_tokens": 0}, {"max_tokens": None}, {"max_tokens": True},
    {"timeout": -1}, {"timeout": float("nan")}, {"model": ""},
    {"context_window": -2}, {"temperature": 3}, {"enable_thinking": "false"},
    {"api_key_env": "a secret value"},
])
def test_invalid_settings(settings):
    with pytest.raises(LocalLLMError):
        LocalLLMConfig("http://localhost:8080", **settings)


def test_project_discovery_overrides_and_child_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("PDD_LOCAL_LLM_BASE_URL")
    (tmp_path / ".pddrc").write_text("local_llm:\n  base_url: http://linux-box:8080\n  model: yaml-model\n")
    subdir = tmp_path / "src"
    subdir.mkdir()
    monkeypatch.chdir(subdir)
    assert get_local_llm_config().model == "yaml-model"
    local_dir = tmp_path / ".pdd"
    local_dir.mkdir()
    (local_dir / "local_llm.json").write_text(json.dumps({"base_url": "http://mac:8080", "model": "json-model"}))
    assert get_local_llm_config().model == "json-model"
    explicit = tmp_path / "explicit.json"
    explicit.write_text(json.dumps({"base_url": "http://explicit:8080", "model": "explicit"}))
    monkeypatch.setenv("PDD_LOCAL_LLM_CONFIG", str(explicit))
    assert get_local_llm_config().model == "explicit"
    monkeypatch.setenv("PDD_LOCAL_LLM_MODEL", "env-model")
    config = get_local_llm_config()
    assert config.model == "env-model"
    for key, value in local_llm_environment(config).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PDD_LOCAL_LLM_CONFIG", "/does-not-exist")
    assert get_local_llm_config() == config
    monkeypatch.setenv("PDD_LOCAL_LLM_ENABLED", "0")
    assert get_local_llm_config() is None


def test_absent_configuration_preserves_existing_mode(monkeypatch):
    monkeypatch.delenv("PDD_LOCAL_LLM_ENABLED")
    monkeypatch.delenv("PDD_LOCAL_LLM_BASE_URL")
    assert get_local_llm_config() is None


@pytest.mark.parametrize("catalog_setting", [None, "false"])
def test_fresh_import_does_not_download_model_catalog(catalog_setting):
    """Endpoint configuration closes import-time LiteLLM catalog networking."""
    env = dict(os.environ)
    if catalog_setting is None:
        env.pop("LITELLM_LOCAL_MODEL_COST_MAP", None)
    else:
        env["LITELLM_LOCAL_MODEL_COST_MAP"] = catalog_setting
    env.pop("PDD_LOCAL_ONLY", None)
    probe = '''
import socket
attempts = []
def deny_lookup(*args, **kwargs):
    attempts.append(args[0])
    raise OSError("network forbidden by startup regression")
socket.getaddrinfo = deny_lookup
import pdd
from pdd.core.cloud import CloudConfig
assert not CloudConfig.is_cloud_enabled()
assert not attempts, attempts
'''
    result = subprocess.run([sys.executable, "-c", probe], env=env,
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("value", ["{", "[]", "{}", '{"api_key":"secret"}'])
def test_malformed_explicit_settings_do_not_fall_back(monkeypatch, value):
    monkeypatch.delenv("PDD_LOCAL_LLM_BASE_URL")
    monkeypatch.setenv("PDD_LOCAL_LLM_CONFIG_JSON", value)
    with pytest.raises(LocalLLMError):
        get_local_llm_config()


def test_real_http_no_ambient_auth_or_proxy(http_server, monkeypatch, tmp_path):
    base, calls, _ = http_server
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-forward")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "do-not-forward-either")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    netrc = tmp_path / "netrc"
    netrc.write_text("machine 127.0.0.1 login private password never-forward\n")
    monkeypatch.setenv("NETRC", str(netrc))
    result = invoke_local_llm(LocalLLMConfig(base), [{"role": "user", "content": "hi"}], temperature=.6)
    assert result["result"] == "answer"
    assert result["cost"] == 0
    assert result["model_name"] == "local-gguf"
    assert [call[1] for call in calls] == ["/v1/models", "/v1/chat/completions"]
    assert all("Authorization" not in call[2] for call in calls)
    assert calls[1][3]["model"] == "local-gguf"


def test_optional_dedicated_key_and_output_caps(http_server, monkeypatch):
    base, calls, replies = http_server
    replies[:] = [(200, completion(reasoning_content="separate thinking"))]
    monkeypatch.setenv("PDD_LOCAL_LLM_API_KEY", "synthetic-local-key")
    result = invoke_local_llm(LocalLLMConfig(base, model="pinned", temperature=.6, enable_thinking=False),
                              [{"role": "user", "content": "hi"}], temperature=0, max_output_tokens=200)
    assert result["thinking_output"] == "separate thinking"
    assert calls[0][2]["Authorization"] == "Bearer synthetic-local-key"
    assert calls[0][3]["max_tokens"] == 200
    assert calls[0][3]["temperature"] == .6
    assert calls[0][3]["chat_template_kwargs"] == {"enable_thinking": False}


class Answer(BaseModel):
    count: int


@pytest.mark.parametrize("typed", [True, False])
def test_validated_structured_output_and_batch(http_server, typed):
    base, calls, replies = http_server
    replies[:] = [(200, completion('{"count":2}')), (200, completion('{"count":3}'))]
    group = [{"role": "user", "content": "count"}]
    kwargs = {"output_pydantic": Answer} if typed else {"output_schema": Answer.model_json_schema()}
    result = invoke_local_llm(LocalLLMConfig(base, model="pinned"), [group, group],
                              temperature=.6, use_batch_mode=True, **kwargs)
    expected = [Answer(count=2), Answer(count=3)] if typed else [{"count": 2}, {"count": 3}]
    assert result["result"] == expected
    assert result["usage"]["total_tokens"] == 12
    assert calls[0][3]["response_format"]["type"] == "json_schema"


@pytest.mark.parametrize("response", [
    {}, {"choices": []}, completion("", None), completion("answer", None),
    completion(""), completion(None, reasoning_content="thinking"),
    completion("partial", "length"), completion("tool", "tool_calls"),
    completion("tool", tool_calls=[{}]), completion("answer", reasoning_content=[1]),
])
def test_incomplete_output_rejected(http_server, response):
    base, calls, replies = http_server
    replies[:] = [(200, response)]
    with pytest.raises(LocalLLMError):
        invoke_local_llm(LocalLLMConfig(base, model="pinned"), [{"role": "user", "content": "hi"}], temperature=.6)
    assert len(calls) == 1


@pytest.mark.parametrize("typed", [True, False])
def test_invalid_schema_never_echoes_answer(http_server, typed):
    base, _, replies = http_server
    replies[:] = [(200, completion('{"count":"PRIVATE_RESPONSE"}'))]
    kwargs = {"output_pydantic": Answer} if typed else {"output_schema": Answer.model_json_schema()}
    with pytest.raises(LocalLLMError) as caught:
        invoke_local_llm(LocalLLMConfig(base, model="pinned"), [{"role": "user", "content": "hi"}], temperature=.6, **kwargs)
    assert "PRIVATE_RESPONSE" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_schema_validation_does_not_download_external_refs(http_server, monkeypatch):
    """Raw schemas cannot open a second network path during local validation."""
    import urllib.request
    base, calls, replies = http_server
    replies[:] = [(200, completion('{"count":2}'))]
    fetch = Mock(side_effect=AssertionError("schema network forbidden"))
    monkeypatch.setattr(urllib.request, "urlopen", fetch)
    with pytest.raises(LocalLLMError, match="structured-output validation"):
        invoke_local_llm(LocalLLMConfig(base, model="pinned"),
                         [{"role": "user", "content": "count"}], temperature=.6,
                         output_schema={"$ref": "https://never-fetch.invalid/schema.json"})
    fetch.assert_not_called()
    assert len(calls) == 1


@pytest.mark.parametrize("status", [302, 401, 500])
def test_error_or_redirect_no_fallback(http_server, status):
    base, calls, replies = http_server
    replies[:] = [(status, {"error": "PRIVATE_RESPONSE"})]
    with pytest.raises(LocalLLMError, match=f"HTTP {status}") as caught:
        invoke_local_llm(LocalLLMConfig(base), [{"role": "user", "content": "hi"}], temperature=.6)
    assert "PRIVATE_RESPONSE" not in str(caught.value)
    assert len(calls) == 1


def test_model_discovery_ambiguity(http_server):
    base, calls, replies = http_server
    replies[:] = [(200, {"data": [{"id": "one"}, {"id": "two"}]})]
    with pytest.raises(LocalLLMError, match="exactly one"):
        invoke_local_llm(LocalLLMConfig(base), [{"role": "user", "content": "hi"}], temperature=.6)
    assert len(calls) == 1


def test_offline_estimate_and_conservative_limits(monkeypatch):
    monkeypatch.setattr(requests, "Session", Mock(side_effect=AssertionError("network forbidden")))
    messages = [{"role": "user", "content": "hello"}]
    estimate = local_llm_estimate(LocalLLMConfig("http://localhost:8080"), messages, command="generate")
    assert estimate["provider_call_made"] is False
    assert estimate["estimated_cost"] == 0
    assert estimate["estimate_basis"] == "utf8_bytes_upper_bound"
    with pytest.raises(LocalLLMError, match="input-token"):
        invoke_local_llm(LocalLLMConfig("http://localhost:8080"), messages, temperature=.6, max_input_tokens=1)
    with pytest.raises(LocalLLMError, match="context bound"):
        invoke_local_llm(LocalLLMConfig("http://localhost:8080", context_window=32), messages, temperature=.6)


def test_llm_invoke_exclusive_over_cloud_catalog_keys_and_router(http_server, monkeypatch):
    llm = importlib.import_module("pdd.llm_invoke")
    base, _, _ = http_server
    monkeypatch.setenv("PDD_LOCAL_LLM_BASE_URL", base)
    monkeypatch.setenv("PDD_JWT_TOKEN", "must-not-use")
    monkeypatch.setenv("PDD_ENABLE_TASK_ROUTING", "1")
    for name in ("_llm_invoke_cloud", "_select_model_candidates", "_ensure_api_key", "_select_task_route"):
        monkeypatch.setattr(llm, name, Mock(side_effect=AssertionError(name)))
    result = llm.llm_invoke(prompt="Hello {name}", input_json={"name": "local"}, use_cloud=True)
    assert result["result"] == "answer"
    assert result["provider"] == "llama.cpp"


def test_llm_invoke_failure_does_not_select_another_provider(monkeypatch):
    llm = importlib.import_module("pdd.llm_invoke")
    monkeypatch.setattr(requests.Session, "request", Mock(side_effect=requests.ConnectionError("PRIVATE_REQUEST")))
    monkeypatch.setattr(llm, "_select_model_candidates", Mock(side_effect=AssertionError("fallback")))
    with pytest.raises(LocalLLMError, match="No provider fallback") as caught:
        llm.llm_invoke(messages=[{"role": "user", "content": "hi"}], use_cloud=True)
    assert "PRIVATE_REQUEST" not in str(caught.value)


def test_llm_invoke_offline_estimate(monkeypatch):
    llm = importlib.import_module("pdd.llm_invoke")
    monkeypatch.setattr(requests, "Session", Mock(side_effect=AssertionError("network")))
    with pytest.raises(llm.EstimateOnlyResult) as caught:
        llm.llm_invoke(messages=[{"role": "user", "content": "hi"}], estimate_only=True)
    assert caught.value.estimate["provider_call_made"] is False


def test_cloud_and_agentic_routes_fail_before_credentials(monkeypatch, tmp_path):
    cloud = importlib.import_module("pdd.core.cloud")
    agentic = importlib.import_module("pdd.agentic_common")
    monkeypatch.setenv("PDD_JWT_TOKEN", "must-not-use")
    for module, name in ((cloud, "_get_cached_jwt"), (cloud, "device_flow_get_token"),
                         (agentic, "get_available_agents"), (agentic, "_find_cli_binary")):
        monkeypatch.setattr(module, name, Mock(side_effect=AssertionError(name)))
    assert cloud.CloudConfig.is_cloud_enabled() is False
    assert cloud.CloudConfig.get_jwt_token() is None
    result = agentic.run_agentic_task("synthetic", tmp_path)
    assert not result.success
    assert "tool-capable" in result.output_text
    result = agentic.run_exact_agentic_task("synthetic", tmp_path, provider="openai", model="gpt-test")
    assert not result.success


def test_cli_automatically_pins_local_and_restores_environment(monkeypatch):
    cli_module = importlib.import_module("pdd.core.cli")
    monkeypatch.delenv("PDD_FORCE_LOCAL", raising=False)
    monkeypatch.setattr(cli_module, "auto_update", Mock(side_effect=AssertionError("update network")))

    @click.command("local-route-probe")
    @click.pass_context
    def probe(ctx):
        assert ctx.obj["local"]
        assert os.environ["PDD_FORCE_LOCAL"] == "1"
        assert json.loads(os.environ["PDD_LOCAL_LLM_CONFIG_JSON"])["base_url"].endswith("/v1")

    monkeypatch.setitem(cli_module.cli.commands, "local-route-probe", probe)
    result = CliRunner().invoke(cli_module.cli, ["--no-core-dump", "local-route-probe"])
    assert result.exit_code == 0, result.output
    assert "PDD_FORCE_LOCAL" not in os.environ
    assert "PDD_LOCAL_LLM_CONFIG_JSON" not in os.environ
