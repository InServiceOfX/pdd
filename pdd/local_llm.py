"""Exclusive, key-optional HTTP transport for explicitly configured local LLMs.

No model catalog, provider credential discovery, automatic login, proxy or
cross-provider fallback is involved. macOS Metal and Linux CUDA servers expose
the same OpenAI-compatible protocol. Configuration discovery itself is offline.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
import math
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class LocalLLMError(RuntimeError):
    """The selected local endpoint could not complete the request safely."""


@dataclass(frozen=True)
class LocalLLMConfig:
    """Local routing settings; api_key_env is a variable name, never a key."""

    base_url: str
    model: str = "auto"
    max_tokens: int = 8192
    timeout: float = 600
    temperature: float | None = None
    context_window: int | None = None
    enable_thinking: bool | None = None
    api_key_env: str = "PDD_LOCAL_LLM_API_KEY"

    def __post_init__(self) -> None:
        try:
            parts = urlsplit(self.base_url)
            port = parts.port
            if (parts.scheme not in {"http", "https"} or not parts.hostname
                    or parts.username is not None or parts.password is not None
                    or parts.query or parts.fragment or port == 0
                    or any(c.isspace() for c in self.base_url)):
                raise ValueError
        except (TypeError, ValueError, AttributeError):
            raise LocalLLMError(
                "Local LLM base_url must be an HTTP(S) API address without "
                "credentials, query or fragment.") from None
        if parts.hostname in {"0.0.0.0", "::"}:
            raise LocalLLMError(
                "Use 127.0.0.1, ::1 or the server's LAN address; "
                "wildcard hosts are bind addresses.")
        path = parts.path.rstrip("/") or "/v1"
        if path.endswith(("/chat/completions", "/models")):
            raise LocalLLMError(
                "Local LLM base_url must name the API prefix, "
                "e.g. http://127.0.0.1:8080/v1.")
        object.__setattr__(self, "base_url", urlunsplit((parts.scheme, parts.netloc, path, "", "")))
        if not isinstance(self.model, str) or not self.model.strip():
            raise LocalLLMError("Local LLM model must be a server model id or 'auto'.")
        if self.max_tokens is None:
            raise LocalLLMError("Local LLM max_tokens must be a positive integer.")
        for name, value in (("max_tokens", self.max_tokens),
                            ("context_window", self.context_window)):
            if value is not None and not _positive_int(value):
                raise LocalLLMError(f"Local LLM {name} must be a positive integer.")
        if (type(self.timeout) not in (int, float) or not math.isfinite(self.timeout)
                or self.timeout <= 0):
            raise LocalLLMError("Local LLM timeout must be positive finite seconds.")
        if self.temperature is not None and (
                type(self.temperature) not in (int, float) or not math.isfinite(self.temperature)
                or not 0 <= self.temperature <= 2):
            raise LocalLLMError("Local LLM temperature must be in [0, 2].")
        if self.enable_thinking is not None and not isinstance(self.enable_thinking, bool):
            raise LocalLLMError("Local LLM enable_thinking must be boolean.")
        if (not isinstance(self.api_key_env, str)
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.api_key_env)):
            raise LocalLLMError(
                "Local LLM api_key_env must name an environment variable, not contain a key.")


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise LocalLLMError(f"Cannot read local LLM JSON configuration: {path}") from None
    if not isinstance(value, dict):
        raise LocalLLMError("Local LLM configuration must be an object.")
    return value


def _project_settings(cwd: Path) -> dict[str, Any] | None:
    for parent in (cwd, *cwd.parents):
        local_file = parent / ".pdd" / "local_llm.json"
        if local_file.is_file():
            return _read_json(local_file)
        pddrc = parent / ".pddrc"
        if pddrc.is_file():
            import yaml  # pylint: disable=import-outside-toplevel
            try:
                data = yaml.safe_load(pddrc.read_text(encoding="utf-8"))
            except (OSError, ValueError, yaml.YAMLError):
                raise LocalLLMError(f"Cannot read local LLM settings from {pddrc}") from None
            if isinstance(data, dict) and "local_llm" in data:
                value = data["local_llm"]
                if not isinstance(value, dict):
                    raise LocalLLMError(".pddrc local_llm must be a mapping.")
                return value
            return None
        if (parent / ".git").exists():
            break
    return None


def get_local_llm_config(cwd: Path | None = None) -> LocalLLMConfig | None:
    """Resolve explicit environment, project and user settings without networking."""
    enabled = os.environ.get("PDD_LOCAL_LLM_ENABLED", "").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return None
    if enabled not in {"", "1", "true", "yes", "on"}:
        raise LocalLLMError("PDD_LOCAL_LLM_ENABLED must be a boolean.")
    inline = os.environ.get("PDD_LOCAL_LLM_CONFIG_JSON")
    explicit = os.environ.get("PDD_LOCAL_LLM_CONFIG")
    if inline is not None:
        try:
            values = json.loads(inline)
        except ValueError:
            raise LocalLLMError("PDD_LOCAL_LLM_CONFIG_JSON must be valid JSON.") from None
        if not isinstance(values, dict):
            raise LocalLLMError("PDD_LOCAL_LLM_CONFIG_JSON must be an object.")
    elif explicit:
        values = _read_json(Path(explicit).expanduser())
    else:
        values = _project_settings((cwd or Path.cwd()).resolve())
        user_file = Path.home() / ".pdd" / "local_llm.json"
        if values is None and user_file.is_file():
            values = _read_json(user_file)
    configured = values is not None
    values = dict(values or {})
    for suffix, key, cast in (("BASE_URL", "base_url", str), ("MODEL", "model", str),
                              ("MAX_TOKENS", "max_tokens", int), ("TIMEOUT", "timeout", float)):
        name = "PDD_LOCAL_LLM_" + suffix
        if name in os.environ:
            configured = True
            try:
                values[key] = cast(os.environ[name])
            except ValueError:
                raise LocalLLMError(f"Invalid {name}.") from None
    if not configured and not enabled:
        return None
    if not values.get("base_url"):
        raise LocalLLMError(
            "Local LLM mode needs base_url or PDD_LOCAL_LLM_BASE_URL "
            "(e.g. http://127.0.0.1:8080/v1).")
    unknown = set(values) - {field.name for field in fields(LocalLLMConfig)}
    if unknown:
        raise LocalLLMError(
            "Unknown local LLM settings; use documented fields and api_key_env "
            "rather than a literal API key.")
    return LocalLLMConfig(**values)


def local_llm_environment(config: LocalLLMConfig) -> dict[str, str]:
    """Pin non-secret settings for nested commands in another working directory."""
    return {"PDD_LOCAL_LLM_CONFIG_JSON": json.dumps(asdict(config)),
            "PDD_LOCAL_LLM_ENABLED": "1", "PDD_FORCE_LOCAL": "1"}


def _input_bound(messages: list) -> int:
    return len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))


def _output_cap(config: LocalLLMConfig, override: int | None) -> int:
    if override is not None and not _positive_int(override):
        raise LocalLLMError("Local LLM output cap must be a positive integer.")
    return min(config.max_tokens, override) if override is not None else config.max_tokens


def local_llm_estimate(config: LocalLLMConfig, messages: list, *, command: str,
                       max_output_tokens: int | None = None) -> dict[str, Any]:
    """A zero-API-charge estimate; no model discovery or tokenizer download."""
    count = _input_bound(messages)
    cap = _output_cap(config, max_output_tokens)
    return {"estimate": True, "command": command, "model": config.model,
            "pricing_model": config.model, "input_tokens": count,
            "raw_input_tokens": count, "input_token_overhead": 0,
            "predicted_output_tokens": cap, "output_token_cap": cap,
            "output_estimation": "configured_output_ceiling",
            "estimate_basis": "utf8_bytes_upper_bound",
            "input_rate_per_million": 0.0, "output_rate_per_million": 0.0,
            "input_cost": 0.0, "output_cost": 0.0, "estimated_cost": 0.0, "total_cost": 0.0,
            "unknown_cost": False, "cost_known": True, "currency": "USD",
            "context_limit": config.context_window,
            "context_usage_percent": (
                (count + cap) / config.context_window * 100 if config.context_window else None),
            "call_type": "local_completion", "provider_call_made": False, "attempted_models": [],
            "cost_basis": "Local API charges only; hardware/electricity excluded."}


def _request(session: Any, config: LocalLLMConfig, method: str, endpoint: str,
             payload: dict | None = None) -> dict:
    import requests  # pylint: disable=import-outside-toplevel
    try:
        response = session.request(
            method, config.base_url + endpoint, json=payload,
            timeout=(min(10, config.timeout), config.timeout), allow_redirects=False)
        if not 200 <= response.status_code < 300:
            raise LocalLLMError(
                f"Local LLM at {config.base_url} returned HTTP {response.status_code}; "
                "no provider fallback.")
        result = response.json()
    except (requests.RequestException, ValueError):
        raise LocalLLMError(
            f"Local LLM request failed at {config.base_url}; "
            "check server availability and timeout. No provider fallback.") from None
    if not isinstance(result, dict):
        raise LocalLLMError("Local LLM returned a malformed response object.")
    return result


def _model(session: Any, config: LocalLLMConfig) -> str:
    if config.model != "auto":
        return config.model
    rows = _request(session, config, "GET", "/models").get("data", [])
    if not isinstance(rows, list):
        raise LocalLLMError("Local LLM model discovery returned an invalid model list.")
    models = {r.get("id") for r in rows
              if isinstance(r, dict) and isinstance(r.get("id"), str) and r["id"].strip()}
    if len(models) != 1:
        raise LocalLLMError(
            "Local LLM auto discovery requires exactly one model; "
            "configure its exact server model id.")
    return models.pop()


def _parse_completion(response: dict, output_pydantic: Any,
                      output_schema: dict | None) -> tuple[Any, str | None]:
    import jsonschema  # pylint: disable=import-outside-toplevel
    from referencing import Registry  # pylint: disable=import-outside-toplevel
    from referencing.exceptions import Unresolvable  # pylint: disable=import-outside-toplevel
    try:
        choice = response["choices"][0]
        message = choice["message"]
        content = message.get("content")
        reason = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        raise LocalLLMError("Local LLM returned malformed chat-completion output.") from None
    if reason == "length":
        raise LocalLLMError(
            "Local LLM output was truncated; increase max_tokens or adjust the "
            "model's thinking budget. No partial output accepted.")
    if reason != "stop" or message.get("tool_calls"):
        raise LocalLLMError(
            "Local LLM did not return a final answer; "
            "tool execution is not supported by prompt transport.")
    if not isinstance(content, str) or not content.strip():
        raise LocalLLMError(
            "Local LLM returned no final content (possibly reasoning only); "
            "check max_tokens and thinking settings.")
    thinking = message.get("reasoning_content") or None
    if thinking is not None and not isinstance(thinking, str):
        raise LocalLLMError("Local LLM returned malformed reasoning content.")
    try:
        if output_pydantic is not None:
            return output_pydantic.model_validate_json(content), thinking
        if output_schema is not None:
            value = json.loads(content)
            # jsonschema's default registry can retrieve remote $refs. An empty
            # explicit registry supports local refs without network retrieval.
            jsonschema.validate(value, output_schema, registry=Registry())
            return value, thinking
    except (ValueError, TypeError, jsonschema.ValidationError,
            jsonschema.SchemaError, Unresolvable):
        # Validation errors can contain the original answer. Never echo it here.
        raise LocalLLMError(
            "Local LLM final content failed structured-output validation.") from None
    return content, thinking


def invoke_local_llm(config: LocalLLMConfig, messages: list, *, temperature: float,
                     output_pydantic: Any = None, output_schema: dict | None = None,
                     use_batch_mode: bool = False, max_output_tokens: int | None = None,
                     max_input_tokens: int | None = None) -> dict[str, Any]:
    """Make bounded calls to this endpoint only; never retry on another provider."""
    import requests  # pylint: disable=import-outside-toplevel
    groups = messages if use_batch_mode else [messages]
    cap = _output_cap(config, max_output_tokens)
    if max_input_tokens is not None and not _positive_int(max_input_tokens):
        raise LocalLLMError("Local LLM input cap must be a positive integer.")
    if not groups:
        raise LocalLLMError("Local LLM request must contain messages.")
    for group in groups:
        if not isinstance(group, list) or not group or any(
                not isinstance(m, dict) or "role" not in m or "content" not in m for m in group):
            raise LocalLLMError("Local LLM messages must contain role/content objects.")
        count = _input_bound(group)
        if max_input_tokens is not None and count > max_input_tokens:
            raise LocalLLMError(
                "Local LLM request exceeds the conservative configured input-token bound.")
        if config.context_window and count + cap > config.context_window:
            raise LocalLLMError(
                "Local LLM request exceeds the conservative configured context bound; "
                "reduce input or output budget.")
    schema = output_pydantic.model_json_schema() if output_pydantic is not None else output_schema
    results, thoughts = [], []
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    with requests.Session() as session:
        session.trust_env = False
        key = os.environ.get(config.api_key_env)
        if key:
            session.headers["Authorization"] = "Bearer " + key
        model = _model(session, config)
        for group in groups:
            payload = {"model": model, "messages": group, "max_tokens": cap,
                       "temperature": (
                           config.temperature if config.temperature is not None else temperature),
                       "stream": False}
            if config.enable_thinking is not None:
                payload["chat_template_kwargs"] = {"enable_thinking": config.enable_thinking}
            if schema is not None:
                payload["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "pdd_output", "strict": True, "schema": schema}}
            response = _request(session, config, "POST", "/chat/completions", payload)
            result, thinking = _parse_completion(response, output_pydantic, output_schema)
            results.append(result)
            thoughts.append(thinking)
            response_usage = response.get("usage")
            for field in usage:
                reported = response_usage.get(field, 0) if isinstance(response_usage, dict) else 0
                if isinstance(reported, int) and not isinstance(reported, bool) and reported >= 0:
                    usage[field] += reported
    return {"result": results if use_batch_mode else results[0], "cost": 0.0,
            "model_name": model, "thinking_output": thoughts if use_batch_mode else thoughts[0],
            "finish_reason": "stop", "attempted_models": [model], "usage": usage,
            "provider": "llama.cpp", "base_url": config.base_url}
