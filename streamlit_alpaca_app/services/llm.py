from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import math
import threading
import time
import re
from typing import Any, Protocol, runtime_checkable

import requests

from .secrets import resolve_secret_value, runtime_env_value
from .llm_execution import execution_effort, provider_post, preserves_literal_output


class LLMAPIError(RuntimeError):
    pass


class LLMDeadlineExceeded(LLMAPIError, TimeoutError):
    pass


class LLMProviderUnavailable(LLMAPIError):
    """Terminal account state; completed research remains resumable."""


_REQUEST_DEADLINE: ContextVar[float | None] = ContextVar("llm_request_deadline", default=None)


@contextmanager
def llm_request_deadline(deadline_monotonic: float | None):
    """Share a caller's deadline without changing a client used by other threads."""
    current = _REQUEST_DEADLINE.get()
    deadline = min(current, deadline_monotonic) if current is not None and deadline_monotonic is not None else (
        current if deadline_monotonic is None else deadline_monotonic
    )
    token = _REQUEST_DEADLINE.set(deadline)
    try:
        if deadline is not None:
            _remaining_request_timeout(deadline)
        yield
        if deadline is not None:
            _remaining_request_timeout(deadline)
    finally:
        _REQUEST_DEADLINE.reset(token)


def _request_deadline(timeout_seconds: float) -> float:
    deadline = time.monotonic() + timeout_seconds
    caller_deadline = _REQUEST_DEADLINE.get()
    return min(deadline, caller_deadline) if caller_deadline is not None else deadline


def _remaining_request_timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LLMDeadlineExceeded("LLM request deadline expired.")
    return remaining


def remaining_request_timeout(maximum_seconds: float) -> float:
    """Bound nested acquisition by the same caller clock as its model work."""
    deadline = _REQUEST_DEADLINE.get()
    return min(float(maximum_seconds), _remaining_request_timeout(deadline)) if deadline is not None else float(maximum_seconds)


class LLMJSONParseError(LLMAPIError):
    def __init__(self, message: str, *, raw_content: str = "") -> None:
        super().__init__(message)
        self.raw_content = raw_content


class LLMJSONResult(dict[str, Any]):
    """Keep provider identity and usage outside the model's schema fields."""

    def __init__(self, value: dict[str, Any], response: dict[str, Any], *, repair_response: dict[str, Any] | None = None) -> None:
        super().__init__(value)
        self.response_metadata = {
            "provider_reported_model": str(response.get("model") or ""),
            "usage": response.get("usage") or {},
            "finish_reason": (response.get("choices") or [{}])[0].get("finish_reason"),
            "json_repair": {
                "used": repair_response is not None,
                "provider_reported_model": str((repair_response or {}).get("model") or ""),
                "usage": (repair_response or {}).get("usage") or {},
                "finish_reason": ((repair_response or {}).get("choices") or [{}])[0].get("finish_reason"),
            },
        }


def _deepseek_response_error(response: requests.Response, *, operation: str = "request") -> LLMAPIError:
    status_code = int(response.status_code)
    response_text = _clean(response.text)[:400]
    provider_code = ""
    provider_message = ""
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            provider_code = _clean(error.get("code")).lower()
            provider_message = _clean(error.get("message"))

    balance_failure = (
        status_code == 402
        or provider_code == "insufficient_balance"
        or "insufficient balance" in provider_message.lower()
        or "insufficient balance" in response_text.lower()
    )
    if balance_failure:
        return LLMAPIError(
            "DeepSeek account has insufficient funds. Add credit in DeepSeek billing before retrying "
            f"the pipeline. Provider response: HTTP {status_code}"
            + (f" ({provider_code})" if provider_code else "")
            + "."
        )

    detail = provider_message or response_text or "No provider error details were returned."
    return LLMAPIError(f"DeepSeek {operation} failed: HTTP {status_code}. {detail}")


# Applied at request time by every provider adapter, including direct callers.
NARRATIVE_STYLE_RULE = (
    "Use an 80% ASD-STE100-inspired Simplified Technical English style for all text that people will read. "
    "Write as if explaining the answer aloud to a smart person who is unfamiliar with the field. Lead with the answer. "
    "Use ordinary words, active voice and short, connected sentences. Explain how and why things connect. "
    "Replace jargon with what it means. When a technical term is necessary, explain it on first use. "
    "Do not copy the sources' style or specialist shorthand. Use the same term for the same thing. "
    "Choose the few details that answer the question; research can be broad without making the answer long. "
    "Follow the requested length and format. An ordinary question usually needs two to four short paragraphs. "
    "Use lists or tables when they help. Cite sources inline with useful links. "
    "Preserve the meaning, names, numbers, dates, units and quotations of facts you include. "
    "Distinguish reported facts from your interpretation and material unknowns. "
    "Omit filler, repeated conclusions, generic caveats and unrequested next steps. Keep internal research notes private. "
    "Do not change URLs, identifiers, JSON keys, code, formulas or other machine-readable fields. "
    "Apply this preference to answers, titles, summaries, explanations, progress messages, reports, knowledge pages and audio scripts. "
    "It is a writing preference, not a numeric compliance score or a limit on research depth."
)


# Registry of user-facing system prompts.
# key -> {"name": str, "file": str, "default": str}
_PROMPT_REGISTRY: dict[str, dict[str, str]] = {}
# Runtime overrides loaded from the database.
_PROMPT_OVERRIDES: dict[str, str] = {}
_OVERRIDES_LOADED = False
_OVERRIDES_SCOPE = ""
_OVERRIDES_CHECKED_AT = 0.0
_OVERRIDES_LOCK = threading.RLock()


def _prompt_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def register_narrative_prompt(name: str, file: str, prompt: str, *, group: str = "") -> str:
    """Register a user-facing system prompt. Returns the key for use with get_prompt()."""
    key = _prompt_key(name)
    _PROMPT_REGISTRY[key] = {"name": name, "file": file, "default": prompt, "group": group}
    return key


def get_prompt(key: str) -> str:
    """Resolve the active prompt for a registered key (override or default)."""
    load_prompt_overrides()
    override = _PROMPT_OVERRIDES.get(key)
    if override is not None:
        return override
    info = _PROMPT_REGISTRY.get(key)
    if info:
        return info["default"]
    return key


def list_narrative_prompts() -> list[dict[str, str]]:
    """Return all registered prompts with their active (override or default) values."""
    entries = []
    for key, info in _PROMPT_REGISTRY.items():
        override = _PROMPT_OVERRIDES.get(key)
        entries.append({
            "key": key,
            "name": info["name"],
            "file": info["file"],
            "default": info["default"],
            "prompt": override if override is not None else info["default"],
            "is_override": override is not None,
            "group": info.get("group", ""),
        })
    return entries


# --- Config parameter registry (numeric limits, grouped with prompts) ---
# key -> {"name": str, "group": str, "default": int|float, "description": str}
_PARAM_REGISTRY: dict[str, dict[str, Any]] = {}


def register_config_param(name: str, group: str, default: int | float | str, description: str, *, choices: tuple[str, ...] = ()) -> str:
    """Register a tunable config parameter. Returns the key for use with get_config_param()."""
    key = f"param:{_prompt_key(name)}"
    _PARAM_REGISTRY[key] = {"name": name, "group": group, "default": default, "description": description, "choices": choices}
    return key


def get_config_param(key: str) -> int | float | str:
    """Resolve the active value for a registered config parameter."""
    override = _PROMPT_OVERRIDES.get(key)
    if override is not None:
        try:
            info = _PARAM_REGISTRY.get(key, {})
            default = info.get("default", 0)
            return type(default)(override)
        except (ValueError, TypeError):
            pass
    info = _PARAM_REGISTRY.get(key)
    if info:
        return info["default"]
    return 0


def list_config_params() -> list[dict[str, Any]]:
    """Return all registered config parameters with active values, grouped."""
    entries = []
    for key, info in _PARAM_REGISTRY.items():
        override = _PROMPT_OVERRIDES.get(key)
        active = info["default"]
        is_override = False
        if override is not None:
            try:
                active = type(info["default"])(override)
                is_override = True
            except (ValueError, TypeError):
                pass
        entries.append({
            "key": key,
            "name": info["name"],
            "group": info["group"],
            "default": info["default"],
            "value": active,
            "description": info["description"],
            "is_override": is_override,
            "choices": list(info.get("choices", ())),
        })
    return entries


def _narrative_style_rule_key() -> str:
    return "__narrative_style_rule__"


def get_active_narrative_style_rule() -> str:
    """Return the active narrative style rule (override or default)."""
    load_prompt_overrides()
    return _PROMPT_OVERRIDES.get(_narrative_style_rule_key(), NARRATIVE_STYLE_RULE)


def llm_settings_scope() -> str:
    track = _env_value("", "APP_TRACK").lower() or "local"
    return {"dev": "development", "prod": "production"}.get(track, track)


def save_prompt_override_changes(changes: dict[str, str | None]) -> bool:
    """Commit only submitted keys. Empty scoped values mean reset to code default."""
    from .pipeline_store import _db_connect

    scope = llm_settings_scope()
    conn = _db_connect()
    if conn is None:
        return False
    try:
        with conn:
            with conn.cursor() as cur:
                for key, value in changes.items():
                    cur.execute(
                        "INSERT INTO llm_prompt_overrides (key, value, updated_at) VALUES (%s, %s, NOW()) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
                        (f"{scope}::{key}", value.strip() if value is not None else ""),
                    )
        # Invalidate only after a successful commit; failed writes never change active settings.
        with _OVERRIDES_LOCK:
            global _OVERRIDES_LOADED
            _OVERRIDES_LOADED = False
        load_prompt_overrides(force=True)
        return True
    except Exception:
        return False
    finally:
        conn.close()


def load_prompt_overrides(*, force: bool = False) -> bool:
    """Refresh a process snapshot at most every 30 seconds; retry failed loads."""
    global _OVERRIDES_LOADED, _OVERRIDES_SCOPE, _OVERRIDES_CHECKED_AT
    scope = llm_settings_scope()
    with _OVERRIDES_LOCK:
        now = time.monotonic()
        if not force and scope == _OVERRIDES_SCOPE and now - _OVERRIDES_CHECKED_AT < (30 if _OVERRIDES_LOADED else 5):
            return _OVERRIDES_LOADED
        _OVERRIDES_CHECKED_AT = now
        if scope != _OVERRIDES_SCOPE:
            _PROMPT_OVERRIDES.clear()
            _OVERRIDES_LOADED = False
            _OVERRIDES_SCOPE = scope
        from .pipeline_store import _db_connect

        conn = _db_connect()
        if conn is None:
            _OVERRIDES_LOADED = False
            return False
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT key, value FROM llm_prompt_overrides WHERE key NOT LIKE %s OR key LIKE %s",
                                ("%::%", f"{scope}::%"))
                    rows = cur.fetchall()
            active = {key: value for key, value in rows if "::" not in key}
            for key, value in rows:
                if key.startswith(f"{scope}::"):
                    local_key = key[len(scope) + 2:]
                    if value:
                        active[local_key] = value
                    else:
                        active.pop(local_key, None)
            _PROMPT_OVERRIDES.clear()
            _PROMPT_OVERRIDES.update(active)
            _OVERRIDES_LOADED = True
            return True
        except Exception:
            _OVERRIDES_LOADED = False
            return False
        finally:
            conn.close()


def apply_narrative_style(system_prompt: str) -> str:
    """Generate plain language at source without modifying model output."""
    if preserves_literal_output():
        return system_prompt
    return (
        f"{system_prompt}\n\nGlobal language policy (takes precedence over other prose-style instructions; "
        "preserve task, evidence, and output-schema requirements):\n"
        f"{get_active_narrative_style_rule()}"
    )


def validate_llm_setting_changes(changes: dict[str, Any]) -> dict[str, str | None]:
    """Validate the admin patch before any database mutation."""
    result: dict[str, str | None] = {}
    for key, value in changes.items():
        if key in _PARAM_REGISTRY:
            default = _PARAM_REGISTRY[key]["default"]
            choices = _PARAM_REGISTRY[key].get("choices", ())
            if isinstance(default, str) and value is not None:
                if not isinstance(value, str) or not value.strip() or (choices and value not in choices):
                    raise ValueError(f"Invalid value for {_PARAM_REGISTRY[key]['name']}.")
            elif value is not None:
                number = float(value)
                if not math.isfinite(number) or number < 0 or (isinstance(default, int) and not number.is_integer()):
                    raise ValueError(f"Invalid value for {_PARAM_REGISTRY[key]['name']}.")
                value = str(type(default)(number))
        elif key in _PROMPT_REGISTRY or key == _narrative_style_rule_key():
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 100_000):
                raise ValueError("Prompts must contain between 1 and 100,000 characters.")
        elif key.startswith("runtime:"):
            provider, field = key.removeprefix("runtime:").split(":", 1) if key.count(":") == 2 else ("", "")
            config = load_llm_config(require_api_key=False)
            if config is None or provider != config.provider or field not in {"model", "temperature", "timeout_seconds", "reasoning_effort", "thinking_mode"}:
                raise ValueError("Unsupported runtime setting.")
            if field == "model":
                if provider == "azure_openai":
                    raise ValueError("Azure deployment selection is managed by the deployment configuration.")
                if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 200):
                    raise ValueError("Enter a model name.")
            elif field in {"temperature", "timeout_seconds"} and value is not None:
                number = float(value)
                low, high = (0, 2) if field == "temperature" else (10, 1800)
                if not math.isfinite(number) or not low <= number <= high or (field == "timeout_seconds" and not number.is_integer()):
                    raise ValueError(f"Invalid {field}.")
                value = str(int(number) if field == "timeout_seconds" else number)
            elif field == "reasoning_effort" and value is not None and value not in {"", "minimal", "low", "medium", "high", "max", "xhigh", "ultra"}:
                raise ValueError("Invalid reasoning effort.")
            elif field == "thinking_mode" and (provider != "deepseek" or value not in {None, "enabled", "disabled"}):
                raise ValueError("Invalid thinking mode.")
        else:
            raise ValueError(f"Unknown LLM setting: {key}")
        default = (_PARAM_REGISTRY.get(key) or _PROMPT_REGISTRY.get(key) or {}).get("default")
        if key == _narrative_style_rule_key():
            default = NARRATIVE_STYLE_RULE
        if value is not None and default is not None and str(value).strip() == str(default).strip():
            value = None
        result[key] = None if value is None else str(value).strip()
    return result


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    api_key: str
    model: str
    base_url: str
    deployment: str = ""
    embedding_deployment: str = ""
    api_version: str = ""
    timeout_seconds: int = 480
    temperature: float = 0.2
    reasoning_effort: str = ""
    thinking_mode: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536


@runtime_checkable
class JSONLLMClient(Protocol):
    """Provider-neutral model boundary used by AQL/Zopedia orchestration."""

    config: LLMConfig

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]: ...


@runtime_checkable
class EmbeddingClient(Protocol):
    config: LLMConfig

    def generate_embeddings(self, texts: list[str]) -> list[list[float]]: ...


def embedding_space(client: Any) -> str:
    config = client.config
    return f"{config.provider}:{config.base_url}:{config.embedding_deployment}:{config.embedding_model}:{config.embedding_dimensions}"


def _clean(value: object) -> str:
    return str(value or "").strip()


def _prefixed_env(prefix: str, name: str) -> str:
    return f"{prefix}{name}" if prefix else name


def _env_value(prefix: str, name: str, *fallback_names: str) -> str:
    candidates = [_prefixed_env(prefix, name)]
    candidates.extend(_prefixed_env(prefix, item) for item in fallback_names)
    if prefix:
        candidates.append(name)
        candidates.extend(fallback_names)
    for candidate in candidates:
        value = _clean(os.getenv(candidate))
        if value:
            return value
    for candidate in candidates:
        value = _clean(runtime_env_value(candidate))
        if value:
            return value
    return ""


def _process_env_value(prefix: str, name: str, *fallback_names: str) -> str:
    candidates = [_prefixed_env(prefix, name)]
    candidates.extend(_prefixed_env(prefix, item) for item in fallback_names)
    if prefix:
        candidates.append(name)
        candidates.extend(fallback_names)
    for candidate in candidates:
        value = _clean(os.getenv(candidate))
        if value:
            return value
    return ""


def _resolve_api_key(env_prefix: str = "") -> str:
    if env_prefix:
        direct = resolve_secret_value(
            [_prefixed_env(env_prefix, "LLM_API_KEY"), _prefixed_env(env_prefix, "OPENAI_API_KEY")],
            secret_name_env=_prefixed_env(env_prefix, "LLM_API_KEY_SECRET_NAME"),
        )
        if direct:
            return direct
        direct = resolve_secret_value(
            [_prefixed_env(env_prefix, "OPENAI_API_KEY")],
            secret_name_env=_prefixed_env(env_prefix, "OPENAI_API_KEY_SECRET_NAME"),
        )
        if direct:
            return direct
    direct = resolve_secret_value(
        ["LLM_API_KEY", "OPENAI_API_KEY"],
        secret_name_env="LLM_API_KEY_SECRET_NAME",
    )
    if direct:
        return direct
    return resolve_secret_value(
        ["OPENAI_API_KEY"],
        secret_name_env="OPENAI_API_KEY_SECRET_NAME",
    )


def _normalize_azure_base_url(base_url: str) -> str:
    cleaned = _clean(base_url).rstrip("/")
    if not cleaned:
        return ""
    if cleaned.endswith("/openai/v1"):
        return cleaned
    if "/openai/v1/" in cleaned:
        return cleaned.rstrip("/")
    return f"{cleaned}/openai/v1"


def _normalized_reasoning_effort(value: object) -> str:
    effort = _clean(value).lower()
    if effort in {"", "none", "off", "disabled", "false", "0"}:
        return ""
    if effort in {"minimal", "low", "medium", "high", "max", "xhigh", "ultra"}:
        return effort
    return ""


def _supports_reasoning_effort_retry(status_code: int, response_text: str) -> bool:
    if status_code not in {400, 422}:
        return False
    lowered = _clean(response_text).lower()
    if "reasoning_effort" not in lowered:
        return False
    return any(
        token in lowered
        for token in (
            "unknown parameter",
            "not supported",
            "extra inputs are not permitted",
            "invalid",
            "unrecognized",
        )
    )


def _extract_first_json_object(raw: str) -> dict[str, Any]:
    text = _clean(raw)
    if not text:
        raise LLMAPIError("LLM returned empty content.")

    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()

    try:
        parsed = json.loads(text)
    except Exception:
        decoder = json.JSONDecoder()
        stripped = text.lstrip()
        try:
            parsed, _ = decoder.raw_decode(stripped)
        except Exception as exc:
            raise LLMJSONParseError(f"LLM returned non-JSON content: {text[:400]}", raw_content=text) from exc
    if not isinstance(parsed, dict):
        raise LLMJSONParseError("LLM JSON payload must be an object.", raw_content=text)
    return parsed


def _chat_message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(_clean(item.get("text")))
        return "\n".join(part for part in text_parts if part).strip()
    return _clean(content)


def _json_repair_messages(
    *,
    schema_name: str,
    schema: dict[str, Any],
    raw_content: str,
) -> list[dict[str, str]]:
    schema_text = json.dumps(schema, sort_keys=True)
    raw_text = str(raw_content or "").strip()
    return [
        {
            "role": "system",
            "content": (
                "Repair the model output into exactly one valid JSON object. "
                "Use only information already present in the model output. "
                f"The repaired object must match schema `{schema_name}`. "
                "Preserve existing string values, numbers, URLs and array values exactly. Do not rewrite prose. Do not add explanations, markdown, or fallback prose."
            ),
        },
        {
            "role": "user",
            "content": (
                f"JSON Schema:\n{schema_text}\n\n"
                "Model output to repair:\n"
                f"{raw_text}"
            ),
        },
    ]


def load_llm_config(env_prefix: str = "", *, require_api_key: bool = True) -> LLMConfig | None:
    load_prompt_overrides()
    process_provider = _process_env_value(env_prefix, "LLM_PROVIDER")
    config_value = _process_env_value if process_provider else _env_value
    provider = (process_provider or _env_value(env_prefix, "LLM_PROVIDER") or "openai").lower()
    if provider in {"", "none", "disabled", "off"}:
        return None

    api_key = _resolve_api_key(env_prefix) if require_api_key else ""
    if require_api_key and not api_key and provider != "local":
        return None

    default_models = {
        "deepseek": "deepseek-chat",
        "local": "local-model",
    }
    default_model = default_models.get(provider, "gpt-4.1-mini")
    model = config_value(env_prefix, "LLM_MODEL", "OPENAI_MODEL") or default_model
    model = _PROMPT_OVERRIDES.get(f"runtime:{provider}:model", model)
    deployment = config_value(env_prefix, "LLM_DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT") or model
    if provider == "azure_openai":
        base_url = _normalize_azure_base_url(
            config_value(env_prefix, "LLM_BASE_URL", "AZURE_OPENAI_ENDPOINT")
        )
        if not base_url:
            return None
        api_version = config_value(env_prefix, "AZURE_OPENAI_API_VERSION")
    elif provider == "deepseek":
        base_url = config_value(env_prefix, "LLM_BASE_URL", "OPENAI_BASE_URL") or "https://api.deepseek.com"
        api_version = ""
    elif provider == "local":
        base_url = config_value(env_prefix, "LLM_BASE_URL", "OPENAI_BASE_URL") or "http://127.0.0.1:8000/v1"
        api_version = ""
    else:
        base_url = config_value(env_prefix, "LLM_BASE_URL", "OPENAI_BASE_URL") or "https://api.openai.com/v1"
        api_version = ""
    timeout_seconds = max(int(config_value(env_prefix, "LLM_TIMEOUT_SECONDS") or "480"), 10)
    default_temperature = "1" if provider == "azure_openai" else "0.2"
    temperature = float(config_value(env_prefix, "LLM_TEMPERATURE") or default_temperature)
    reasoning_effort = _normalized_reasoning_effort(
        config_value(env_prefix, "LLM_REASONING_EFFORT", "OPENAI_REASONING_EFFORT")
    )
    if provider == "local":
        reasoning_effort = ""
    timeout_seconds = int(_PROMPT_OVERRIDES.get(f"runtime:{provider}:timeout_seconds", timeout_seconds))
    temperature = float(_PROMPT_OVERRIDES.get(f"runtime:{provider}:temperature", temperature))
    reasoning_effort = _PROMPT_OVERRIDES.get(f"runtime:{provider}:reasoning_effort", reasoning_effort)
    thinking_mode = _PROMPT_OVERRIDES.get(
        f"runtime:{provider}:thinking_mode", config_value(env_prefix, "LLM_THINKING_MODE")
    )
    if provider == "deepseek" and not thinking_mode:
        thinking_mode = "disabled" if model == "deepseek-chat" else "enabled"
    embedding_model = config_value(env_prefix, "EMBEDDING_MODEL") or "text-embedding-3-small"
    embedding_deployment = config_value(env_prefix, "EMBEDDING_DEPLOYMENT", "AZURE_OPENAI_EMBEDDING_DEPLOYMENT")

    return LLMConfig(
        provider=provider,
        api_key=api_key,
        model=model,
        base_url=base_url.rstrip("/"),
        deployment=deployment,
        embedding_deployment=embedding_deployment,
        api_version=api_version,
        timeout_seconds=timeout_seconds,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        thinking_mode=thinking_mode,
        embedding_model=embedding_model,
    )


class OpenAIChatJSONClient:
    def __init__(self, config: LLMConfig, *, session: requests.Session | None = None) -> None:
        if not config.api_key:
            raise LLMAPIError("Missing LLM API key.")
        self.config = config
        self.session = session or requests.Session()

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        deadline = _request_deadline(self.config.timeout_seconds)
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": apply_narrative_style(system_prompt)},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.config.temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                },
            },
        }
        if execution_effort(self.config.reasoning_effort):
            payload["reasoning_effort"] = execution_effort(self.config.reasoning_effort)
            payload.pop("temperature", None)
        request_url = f"{self.config.base_url}/chat/completions"
        request_headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        import requests as _requests_mod
        _max_timeout_retries = 2
        for _attempt in range(_max_timeout_retries):
            try:
                response = provider_post(self, url=
                    request_url,
                    headers=request_headers,
                    json=payload,
                    timeout=_remaining_request_timeout(deadline),
                )
                break
            except (
                _requests_mod.exceptions.ReadTimeout,
                _requests_mod.exceptions.ConnectionError,
                _requests_mod.exceptions.ChunkedEncodingError,
            ):
                if _attempt < _max_timeout_retries - 1:
                    continue
                raise
        if response.status_code != 200 and payload.get("reasoning_effort") and _supports_reasoning_effort_retry(response.status_code, response.text):
            payload = dict(payload)
            payload.pop("reasoning_effort", None)
            response = provider_post(self, url=
                request_url,
                headers=request_headers,
                json=payload,
                timeout=_remaining_request_timeout(deadline),
            )
        if response.status_code != 200:
            raise LLMAPIError(f"LLM request failed status={response.status_code}: {response.text[:400]}")
        try:
            parsed = response.json()
        except Exception as exc:
            raise LLMAPIError(f"LLM returned invalid JSON: {exc}") from exc

        choices = parsed.get("choices") or []
        if not choices:
            raise LLMAPIError("LLM returned no choices.")
        message = choices[0].get("message") or {}
        refusal = _clean(message.get("refusal"))
        if refusal:
            raise LLMAPIError(f"LLM refused request: {refusal}")

        raw = _chat_message_text(message)
        if not raw:
            raise LLMAPIError("LLM returned empty content.")
        repair_parsed = None
        try:
            parsed_object = _extract_first_json_object(raw)
        except LLMJSONParseError as exc:
            repair_payload = dict(payload)
            if self.config.provider == "deepseek":
                repair_payload["thinking"] = {"type": "disabled"}
                repair_payload.pop("reasoning_effort", None)
            repair_payload["messages"] = _json_repair_messages(
                schema_name=schema_name,
                schema=schema,
                raw_content=exc.raw_content,
            )
            repair_response = provider_post(self, operation="json_repair", url=
                request_url,
                headers=request_headers,
                json=repair_payload,
                timeout=_remaining_request_timeout(deadline),
            )
            if repair_response.status_code != 200:
                raise LLMAPIError(
                    f"LLM JSON repair failed status={repair_response.status_code}: {repair_response.text[:400]}"
                ) from exc
            try:
                repair_parsed = repair_response.json()
            except Exception as repair_exc:
                raise LLMAPIError(f"LLM JSON repair returned invalid JSON: {repair_exc}") from exc
            repair_choices = repair_parsed.get("choices") or []
            if not repair_choices:
                raise LLMAPIError("LLM JSON repair returned no choices.") from exc
            repair_message = repair_choices[0].get("message") or {}
            repair_refusal = _clean(repair_message.get("refusal"))
            if repair_refusal:
                raise LLMAPIError(f"LLM JSON repair refused request: {repair_refusal}") from exc
            repair_raw = _chat_message_text(repair_message)
            if not repair_raw:
                raise LLMAPIError("LLM JSON repair returned empty content.") from exc
            parsed_object = _extract_first_json_object(repair_raw)
        _remaining_request_timeout(deadline)
        return LLMJSONResult(parsed_object, parsed, repair_response=repair_parsed)


class AzureOpenAIChatJSONClient:
    def __init__(self, config: LLMConfig, *, session: requests.Session | None = None) -> None:
        if not config.api_key:
            raise LLMAPIError("Missing LLM API key.")
        if not config.base_url:
            raise LLMAPIError("Missing Azure OpenAI endpoint.")
        if not config.deployment:
            raise LLMAPIError("Missing Azure OpenAI deployment.")
        self.config = config
        self.session = session or requests.Session()

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        deadline = _request_deadline(self.config.timeout_seconds)
        payload = {
            "model": self.config.deployment,
            "messages": [
                {"role": "system", "content": apply_narrative_style(system_prompt)},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.config.temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        if execution_effort(self.config.reasoning_effort):
            payload["reasoning_effort"] = execution_effort(self.config.reasoning_effort)

        request_url = f"{self.config.base_url}/chat/completions"
        request_headers = {
            "api-key": self.config.api_key,
            "Content-Type": "application/json",
        }
        request_params = {"api-version": self.config.api_version} if self.config.api_version else None
        import requests as _requests_mod
        _max_timeout_retries = 2
        for _attempt in range(_max_timeout_retries):
            try:
                response = provider_post(self, url=
                    request_url,
                    headers=request_headers,
                    params=request_params,
                    json=payload,
                    timeout=_remaining_request_timeout(deadline),
                )
                break
            except (
                _requests_mod.exceptions.ReadTimeout,
                _requests_mod.exceptions.ConnectionError,
                _requests_mod.exceptions.ChunkedEncodingError,
            ):
                if _attempt < _max_timeout_retries - 1:
                    continue
                raise
        if response.status_code != 200 and payload.get("reasoning_effort") and _supports_reasoning_effort_retry(response.status_code, response.text):
            payload = dict(payload)
            payload.pop("reasoning_effort", None)
            response = provider_post(self, url=
                request_url,
                headers=request_headers,
                params=request_params,
                json=payload,
                timeout=_remaining_request_timeout(deadline),
            )
        if response.status_code != 200:
            raise LLMAPIError(f"LLM request failed status={response.status_code}: {response.text[:400]}")
        try:
            parsed = response.json()
        except Exception as exc:
            raise LLMAPIError(f"LLM returned invalid JSON: {exc}") from exc

        choices = parsed.get("choices") or []
        if not choices:
            raise LLMAPIError("LLM returned no choices.")
        message = choices[0].get("message") or {}
        refusal = _clean(message.get("refusal"))
        if refusal:
            raise LLMAPIError(f"LLM refused request: {refusal}")
        raw = _chat_message_text(message)
        if not raw:
            raise LLMAPIError("LLM returned empty content.")
        repair_parsed = None
        try:
            parsed_object = _extract_first_json_object(raw)
        except LLMJSONParseError as exc:
            repair_payload = dict(payload)
            if self.config.provider == "deepseek":
                repair_payload["thinking"] = {"type": "disabled"}
                repair_payload.pop("reasoning_effort", None)
            repair_payload["messages"] = _json_repair_messages(
                schema_name=schema_name,
                schema=schema,
                raw_content=exc.raw_content,
            )
            repair_response = provider_post(self, operation="json_repair", url=
                request_url,
                headers=request_headers,
                params=request_params,
                json=repair_payload,
                timeout=_remaining_request_timeout(deadline),
            )
            if repair_response.status_code != 200:
                raise LLMAPIError(
                    f"LLM JSON repair failed status={repair_response.status_code}: {repair_response.text[:400]}"
                ) from exc
            try:
                repair_parsed = repair_response.json()
            except Exception as repair_exc:
                raise LLMAPIError(f"LLM JSON repair returned invalid JSON: {repair_exc}") from exc
            repair_choices = repair_parsed.get("choices") or []
            if not repair_choices:
                raise LLMAPIError("LLM JSON repair returned no choices.") from exc
            repair_message = repair_choices[0].get("message") or {}
            repair_refusal = _clean(repair_message.get("refusal"))
            if repair_refusal:
                raise LLMAPIError(f"LLM JSON repair refused request: {repair_refusal}") from exc
            repair_raw = _chat_message_text(repair_message)
            if not repair_raw:
                raise LLMAPIError("LLM JSON repair returned empty content.") from exc
            parsed_object = _extract_first_json_object(repair_raw)
        _remaining_request_timeout(deadline)
        return LLMJSONResult(parsed_object, parsed, repair_response=repair_parsed)


class DeepSeekChatJSONClient:
    def __init__(self, config: LLMConfig, *, session: requests.Session | None = None) -> None:
        if not config.api_key:
            raise LLMAPIError("Missing DeepSeek API key.")
        if not config.base_url:
            raise LLMAPIError("Missing DeepSeek base URL.")
        self.config = config
        self.session = session or requests.Session()

    @property
    def provider_label(self) -> str:
        return "DeepSeek"

    def _response_error(self, response: requests.Response, *, operation: str = "request") -> LLMAPIError:
        return _deepseek_response_error(response, operation=operation)

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        deadline = _request_deadline(self.config.timeout_seconds)
        json_instruction = (
            f"Return only one valid JSON object for schema `{schema_name}`. "
            "Do not wrap it in markdown. The JSON object must match this JSON Schema:\n"
            f"{json.dumps(schema, sort_keys=True)}"
        )
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": apply_narrative_style(system_prompt)},
                {"role": "user", "content": f"{user_prompt}\n\n{json_instruction}"},
            ],
            "response_format": {"type": "json_object"},
        }
        if self.config.provider == "deepseek":
            effort_override = execution_effort()
            effort = effort_override or self.config.reasoning_effort
            thinking = "disabled" if effort_override == "none" else (
                "enabled" if effort_override else self.config.thinking_mode or (
                    "disabled" if self.config.model == "deepseek-chat" else "enabled"))
            payload["thinking"] = {"type": thinking}
            if thinking == "enabled" and effort:
                payload["reasoning_effort"] = {
                    "minimal": "low", "medium": "high", "xhigh": "high", "ultra": "max",
                }.get(effort, effort)
            if thinking == "disabled":
                payload["temperature"] = self.config.temperature
        elif not self.config.model.startswith("deepseek-reasoner"):
            payload["temperature"] = self.config.temperature

        request_url = f"{self.config.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        import requests as _requests_mod
        message: dict[str, Any] = {}
        raw = ""
        for attempt in range(2):
            try:
                response = provider_post(self, url=
                    request_url,
                    headers=headers,
                    json=payload,
                    timeout=_remaining_request_timeout(deadline),
                )
            except (
                _requests_mod.exceptions.ReadTimeout,
                _requests_mod.exceptions.ConnectionError,
                _requests_mod.exceptions.ChunkedEncodingError,
            ):
                if attempt == 0:
                    continue
                raise
            if response.status_code != 200:
                raise self._response_error(response)
            try:
                parsed = response.json()
            except Exception as exc:
                raise LLMAPIError(f"{self.provider_label} returned invalid JSON: {exc}") from exc

            choices = parsed.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                raw = _chat_message_text(message)
                if raw:
                    break
            if attempt == 0:
                continue
            if not choices:
                raise LLMAPIError(f"{self.provider_label} returned no choices.")
            raise LLMAPIError(f"{self.provider_label} returned empty content.")
        repair_parsed = None
        try:
            parsed_object = _extract_first_json_object(raw)
        except LLMJSONParseError as exc:
            repair_payload = dict(payload)
            if self.config.provider == "deepseek":
                repair_payload["thinking"] = {"type": "disabled"}
                repair_payload.pop("reasoning_effort", None)
            repair_payload["messages"] = _json_repair_messages(
                schema_name=schema_name,
                schema=schema,
                raw_content=exc.raw_content,
            )
            repair_response = provider_post(self, operation="json_repair", url=
                request_url,
                headers=headers,
                json=repair_payload,
                timeout=_remaining_request_timeout(deadline),
            )
            if repair_response.status_code != 200:
                raise self._response_error(repair_response, operation="JSON repair") from exc
            try:
                repair_parsed = repair_response.json()
            except Exception as repair_exc:
                raise LLMAPIError(f"{self.provider_label} JSON repair returned invalid JSON: {repair_exc}") from exc
            repair_choices = repair_parsed.get("choices") or []
            if not repair_choices:
                raise LLMAPIError(f"{self.provider_label} JSON repair returned no choices.") from exc
            repair_message = repair_choices[0].get("message") or {}
            repair_raw = _chat_message_text(repair_message)
            if not repair_raw:
                raise LLMAPIError(f"{self.provider_label} JSON repair returned empty content.") from exc
            parsed_object = _extract_first_json_object(repair_raw)
        result = parsed_object
        reasoning_content = _clean(message.get("reasoning_content"))
        if reasoning_content:
            result["__reasoning_content"] = reasoning_content
        _remaining_request_timeout(deadline)
        return LLMJSONResult(result, parsed, repair_response=repair_parsed)


class LocalChatJSONClient(DeepSeekChatJSONClient):
    """JSON client for a local chat-completions-compatible inference server.

    Local inference is intentionally a wire-protocol adapter, not an SDK
    dependency. Authentication is optional and controlled by ``LLM_API_KEY``.
    """

    def __init__(self, config: LLMConfig, *, session: requests.Session | None = None) -> None:
        if not config.base_url:
            raise LLMAPIError("Missing local LLM base URL.")
        self.config = config
        self.session = session or requests.Session()

    @property
    def provider_label(self) -> str:
        return "Local LLM"

    def _response_error(self, response: requests.Response, *, operation: str = "request") -> LLMAPIError:
        detail = _clean(response.text)[:400] or "No server error details were returned."
        return LLMAPIError(f"Local LLM {operation} failed: HTTP {int(response.status_code)}. {detail}")


def _embedding_post(session: requests.Session, *, config: LLMConfig, deadline: float, **kwargs: Any) -> requests.Response:
    for attempt in range(4):
        try:
            response = provider_post(config=config, session=session, operation="embedding", timeout=_remaining_request_timeout(deadline), **kwargs)
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ReadTimeout,
        ):
            if attempt == 3:
                raise
            continue
        if response.status_code not in {429, 500, 502, 503, 504} or attempt == 3:
            return response
        try:
            delay = max(float(response.headers.get("retry-after", 1)), 0.1)
        except ValueError:
            delay = 1.0
        if delay >= _remaining_request_timeout(deadline):
            return response
        time.sleep(delay)
    raise LLMAPIError("Embedding request retries exhausted.")


def _generate_openai_embedding_vectors(
    *, config: LLMConfig, session: requests.Session, texts: list[str],
    model: str, headers: dict[str, str], params: dict[str, str] | None = None,
) -> list[list[float]]:
    """Embed complete inputs within provider limits, preserving input order.

    Long inputs use token-weighted, unit-normalized chunk vectors. Short inputs
    retain the provider vector unchanged. Chunking is a transport representation;
    it does not select or discard research content.
    """
    import tiktoken

    clean_texts = [str(text or "").strip() for text in list(texts or []) if str(text or "").strip()]
    if not clean_texts:
        return []
    deadline = _request_deadline(config.timeout_seconds)
    requested_dimensions = getattr(config, "embedding_dimensions", None)
    dimensions = requested_dimensions
    # OpenAI's text embedding models use cl100k_base and allow 8,192 tokens per
    # input, 300,000 tokens and 2,048 inputs per request. Leave one token margin.
    encoding = tiktoken.get_encoding("cl100k_base")
    chunks = []
    for index, text in enumerate(clean_texts):
        tokens = encoding.encode(text, disallowed_special=())
        chunks.extend((index, tokens[start:start + 8191]) for start in range(0, len(tokens), 8191))
    parts: list[list[tuple[int, list[float]]]] = [[] for _ in clean_texts]
    start = 0
    while start < len(chunks):
        end, token_count = start, 0
        while end < len(chunks) and end - start < 2048 and token_count + len(chunks[end][1]) <= 300000:
            token_count += len(chunks[end][1])
            end += 1
        batch = chunks[start:end]
        payload = {"model": model, "input": [tokens for _, tokens in batch], "encoding_format": "float"}
        if requested_dimensions is not None and config.embedding_model.startswith("text-embedding-3"):
            payload["dimensions"] = requested_dimensions
        response = _embedding_post(
            session, config=config, deadline=deadline, url=f"{config.base_url}/embeddings",
            headers=headers, params=params, json=payload,
        )
        if response.status_code != 200:
            raise LLMAPIError(f"Embedding request failed status={response.status_code}: {response.text[:400]}")
        try:
            parsed = response.json()
        except Exception as exc:
            raise LLMAPIError(f"Embedding response returned invalid JSON: {exc}") from exc
        data = parsed.get("data") or []
        if not isinstance(data, list):
            raise LLMAPIError("Embedding response missing data list.")
        vectors = _ordered_embedding_vectors(data, len(batch), dimensions)
        dimensions = len(vectors[0])
        for (index, tokens), vector in zip(batch, vectors, strict=True):
            parts[index].append((len(tokens), vector))
        start = end
    result = []
    for input_parts in parts:
        if len(input_parts) == 1:
            result.append(input_parts[0][1])
            continue
        total = sum(weight for weight, _ in input_parts)
        vector = [math.fsum(weight * values[i] for weight, values in input_parts) / total
                  for i in range(len(input_parts[0][1]))]
        norm = math.sqrt(math.fsum(value * value for value in vector))
        if not math.isfinite(norm) or norm <= 0:
            raise LLMAPIError("Embedding chunks produced an invalid combined vector.")
        result.append([value / norm for value in vector])
    return result


class OpenAIEmbeddingClient:
    def __init__(self, config: LLMConfig, *, session: requests.Session | None = None) -> None:
        if not config.api_key:
            raise LLMAPIError("Missing LLM API key.")
        self.config = config
        self.session = session or requests.Session()

    def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return _generate_openai_embedding_vectors(
            config=self.config, session=self.session, texts=texts,
            model=self.config.embedding_model,
            headers={"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"},
        )


class AzureOpenAIEmbeddingClient:
    def __init__(self, config: LLMConfig, *, session: requests.Session | None = None) -> None:
        if not config.api_key:
            raise LLMAPIError("Missing LLM API key.")
        if not config.base_url:
            raise LLMAPIError("Missing Azure OpenAI endpoint.")
        if not (config.embedding_deployment or config.embedding_model):
            raise LLMAPIError("Missing Azure OpenAI embedding deployment.")
        self.config = config
        self.session = session or requests.Session()

    def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return _generate_openai_embedding_vectors(
            config=self.config, session=self.session, texts=texts,
            model=self.config.embedding_deployment or self.config.embedding_model,
            headers={"api-key": self.config.api_key, "Content-Type": "application/json"},
            params={"api-version": self.config.api_version} if self.config.api_version else None,
        )


def _ordered_embedding_vectors(data: list[Any], count: int, dimensions: int | None) -> list[list[float]]:
    by_index = {
        item["index"]: item.get("embedding") for item in data if isinstance(item, dict) and "index" in item
    }
    if len(data) != count or set(by_index) != set(range(count)):
        raise LLMAPIError("Embedding response does not match the input batch.")
    vectors = [[float(value) for value in by_index[index]] for index in range(count)]
    dimensions = dimensions if dimensions is not None else len(vectors[0])
    if any(
        not vector or len(vector) != dimensions or not all(math.isfinite(value) for value in vector) for vector in vectors
    ):
        raise LLMAPIError("Embedding response has invalid vector dimensions or values.")
    return vectors


def load_llm_client(env_prefix: str = "") -> JSONLLMClient | None:
    config = load_llm_config(env_prefix=env_prefix)
    if config is None:
        return None
    if config.provider == "openai":
        return OpenAIChatJSONClient(config)
    if config.provider == "azure_openai":
        return AzureOpenAIChatJSONClient(config)
    if config.provider == "deepseek":
        return DeepSeekChatJSONClient(config)
    if config.provider == "local":
        return LocalChatJSONClient(config)
    raise LLMAPIError(f"Unsupported LLM provider: {config.provider}")


def load_embedding_client() -> OpenAIEmbeddingClient | AzureOpenAIEmbeddingClient | None:
    # Embeddings are a separate capability. Never send a chat provider's key to
    # another host or assume every OpenAI-compatible chat endpoint embeds text.
    provider = _env_value("", "EMBEDDING_PROVIDER").lower()
    chat = load_llm_config(require_api_key=False)
    if not provider and chat and chat.provider in {"openai", "azure_openai"}:
        provider = chat.provider
    if provider not in {"openai", "azure_openai"}:
        return None
    base = _env_value("", "EMBEDDING_BASE_URL")
    if not base and chat and provider == chat.provider:
        base = chat.base_url
    key = resolve_secret_value(["EMBEDDING_API_KEY"], secret_name_env="EMBEDDING_API_KEY_SECRET_NAME")
    if not key and chat and provider == chat.provider and base.rstrip("/") == chat.base_url.rstrip("/"):
        key = _resolve_api_key()
    if not key:
        return None
    model = _env_value("", "EMBEDDING_MODEL") or "text-embedding-3-small"
    deployment = _env_value("", "EMBEDDING_DEPLOYMENT", "AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
    if provider == "azure_openai" and (not base or not deployment):
        return None
    config = LLMConfig(
        provider=provider,
        api_key=key,
        model=model,
        embedding_model=model,
        embedding_deployment=deployment,
        base_url=(
            _normalize_azure_base_url(base)
            if provider == "azure_openai"
            else (base or "https://api.openai.com/v1").rstrip("/")
        ),
        api_version=_env_value("", "EMBEDDING_API_VERSION"),
        embedding_dimensions=int(_env_value("", "EMBEDDING_DIMENSIONS") or "1536"),
        timeout_seconds=int(_env_value("", "EMBEDDING_TIMEOUT_SECONDS") or "30"),
    )
    return AzureOpenAIEmbeddingClient(config) if provider == "azure_openai" else OpenAIEmbeddingClient(config)


def check_llm_readiness() -> dict[str, str]:
    """Check LLM and embedding runtime readiness. Returns a status dict.

    Designed to run once at startup or in an admin health panel so operators
    can see which capabilities are actually live (mistakes.md #15, #36).
    """
    status: dict[str, str] = {}
    config = load_llm_config()
    if config is None:
        status["llm"] = "unavailable — no LLM config (missing provider, API key, or endpoint)"
        status["embeddings"] = "unavailable — no LLM config"
        return status

    status["llm_provider"] = config.provider
    status["llm_model"] = config.model
    status["llm_deployment"] = config.deployment or "(none)"
    status["llm_base_url"] = config.base_url or "(none)"

    # Check LLM
    try:
        client = load_llm_client()
        if client is None:
            status["llm"] = "unavailable — load_llm_client returned None"
        else:
            status["llm"] = "configured"
    except Exception as exc:
        status["llm"] = f"error — {type(exc).__name__}: {exc}"

    try:
        emb_client = load_embedding_client()
        if emb_client is None:
            status["embeddings"] = "unavailable — configure EMBEDDING_PROVIDER, endpoint and credentials"
        else:
            status["embedding_model"] = emb_client.config.embedding_model
            status["embedding_deployment"] = emb_client.config.embedding_deployment or "(none)"
            status["embedding_provider"] = emb_client.config.provider
            status["embeddings"] = "configured"
    except Exception as exc:
        status["embeddings"] = f"error — {type(exc).__name__}: {exc}"

    return status


__all__ = [
    "AzureOpenAIEmbeddingClient",
    "AzureOpenAIChatJSONClient",
    "DeepSeekChatJSONClient",
    "EmbeddingClient",
    "OpenAIEmbeddingClient",
    "LLMAPIError",
    "LLMJSONParseError",
    "LLMConfig",
    "JSONLLMClient",
    "LocalChatJSONClient",
    "OpenAIChatJSONClient",
    "check_llm_readiness",
    "load_embedding_client",
    "load_llm_client",
]
