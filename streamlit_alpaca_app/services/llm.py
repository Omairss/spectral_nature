from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Any

import requests

from .secrets import resolve_secret_value


class LLMAPIError(RuntimeError):
    pass


# Words/phrases the LLM must never use in user-facing narrative.
# These are matched case-insensitively and replaced with plain alternatives or removed.
_JARGON_REPLACEMENTS: list[tuple[str, str]] = [
    (r"\bidiosyncratic(?:ally)?\b", "stock-specific"),
    (r"\bnuanced\b", ""),
    (r"\bmultifaceted\b", ""),
    (r"\brobust\b", "strong"),
    (r"\bgranular\b", "detailed"),
    (r"\blocalized\b", ""),
    (r"\bleverag(?:e|ing|ed)\b", "use"),
    (r"\bsynerg(?:y|ies|istic)\b", ""),
    (r"\bparadigm\b", "model"),
    (r"\bactionable\b", "useful"),
    (r"\bholistic\b", "overall"),
    (r"\bbespoke\b", "custom"),
    (r"\bcutting-edge\b", ""),
    (r"\bbest-in-class\b", ""),
    (r"\bgame-changer\b", ""),
    (r"\bdeep.dive\b", "look"),
    (r"\bsurfacing\b", "showing"),
    (r"\bunpack(?:ing|ed)?\b", "explain"),
]

# Style rule for all system prompts that generate user-facing narrative.
# Import this and include it in any prompt that writes text shown to users.
NARRATIVE_STYLE_RULE = (
    "Write like a trader speaking plainly — short sentences, real numbers, no jargon. "
    "Never use: idiosyncratic, nuanced, multifaceted, robust, granular, leverage (as a verb), "
    "synergy, paradigm, actionable, holistic, bespoke, cutting-edge, game-changer, deep-dive, "
    "unpack, or surface (as a verb meaning 'explain'). "
    "If cause is unclear, say 'no clear catalyst confirmed' — do not invent a reason."
)


def strip_jargon(text: str) -> str:
    """Remove or replace known jargon words from any user-facing narrative string."""
    if not text:
        return text
    result = text
    for pattern, replacement in _JARGON_REPLACEMENTS:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    # Collapse double spaces left by empty replacements
    result = re.sub(r"  +", " ", result).strip()
    return result


def _strip_jargon_from_dict(obj: Any) -> Any:
    """Recursively apply strip_jargon to all string values in a dict/list structure."""
    if isinstance(obj, str):
        return strip_jargon(obj)
    if isinstance(obj, dict):
        return {k: _strip_jargon_from_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_jargon_from_dict(item) for item in obj]
    return obj


# Registry of user-facing system prompts.
# key -> {"name": str, "file": str, "default": str}
_PROMPT_REGISTRY: dict[str, dict[str, str]] = {}
# Runtime overrides loaded from the database.
_PROMPT_OVERRIDES: dict[str, str] = {}
_OVERRIDES_LOADED = False


def _prompt_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def register_narrative_prompt(name: str, file: str, prompt: str, *, group: str = "") -> str:
    """Register a user-facing system prompt. Returns the key for use with get_prompt()."""
    key = _prompt_key(name)
    _PROMPT_REGISTRY[key] = {"name": name, "file": file, "default": prompt, "group": group}
    return key


def get_prompt(key: str) -> str:
    """Resolve the active prompt for a registered key (override or default)."""
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


def set_narrative_prompt_override(key: str, value: str | None) -> None:
    """Set or clear a prompt override in memory. Call save_prompt_overrides() to persist."""
    if value is None or value.strip() == _PROMPT_REGISTRY.get(key, {}).get("default", "").strip():
        _PROMPT_OVERRIDES.pop(key, None)
    else:
        _PROMPT_OVERRIDES[key] = value.strip()


# --- Config parameter registry (numeric limits, grouped with prompts) ---
# key -> {"name": str, "group": str, "default": int|float, "description": str}
_PARAM_REGISTRY: dict[str, dict[str, Any]] = {}


def register_config_param(name: str, group: str, default: int | float, description: str) -> str:
    """Register a tunable config parameter. Returns the key for use with get_config_param()."""
    key = f"param:{_prompt_key(name)}"
    _PARAM_REGISTRY[key] = {"name": name, "group": group, "default": default, "description": description}
    return key


def get_config_param(key: str) -> int | float:
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
        })
    return entries


def set_config_param_override(key: str, value: int | float | None) -> None:
    """Set or clear a config parameter override. Call save_prompt_overrides() to persist."""
    info = _PARAM_REGISTRY.get(key)
    if info and (value is None or value == info["default"]):
        _PROMPT_OVERRIDES.pop(key, None)
    elif value is not None:
        _PROMPT_OVERRIDES[key] = str(value)


def _narrative_style_rule_key() -> str:
    return "__narrative_style_rule__"


def get_active_narrative_style_rule() -> str:
    """Return the active narrative style rule (override or default)."""
    return _PROMPT_OVERRIDES.get(_narrative_style_rule_key(), NARRATIVE_STYLE_RULE)


def set_narrative_style_rule_override(value: str | None) -> None:
    key = _narrative_style_rule_key()
    if value is None or value.strip() == NARRATIVE_STYLE_RULE.strip():
        _PROMPT_OVERRIDES.pop(key, None)
    else:
        _PROMPT_OVERRIDES[key] = value.strip()


def save_prompt_overrides() -> bool:
    """Persist all prompt overrides to the database. Returns True on success."""
    try:
        from .pipeline_store import _db_connect
    except ImportError:
        return False
    conn = _db_connect()
    if conn is None:
        return False
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS llm_prompt_overrides ("
                    "  key TEXT PRIMARY KEY,"
                    "  value TEXT NOT NULL,"
                    "  updated_at TIMESTAMPTZ DEFAULT NOW()"
                    ")"
                )
                cur.execute("DELETE FROM llm_prompt_overrides")
                for key, value in _PROMPT_OVERRIDES.items():
                    cur.execute(
                        "INSERT INTO llm_prompt_overrides (key, value) VALUES (%s, %s)",
                        (key, value),
                    )
        return True
    except Exception:
        return False
    finally:
        conn.close()


def load_prompt_overrides() -> None:
    """Load prompt overrides from the database into memory."""
    global _OVERRIDES_LOADED
    if _OVERRIDES_LOADED:
        return
    _OVERRIDES_LOADED = True
    try:
        from .pipeline_store import _db_connect
    except ImportError:
        return
    conn = _db_connect()
    if conn is None:
        return
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT key, value FROM llm_prompt_overrides"
                )
                for row in cur.fetchall():
                    _PROMPT_OVERRIDES[row[0]] = row[1]
    except Exception:
        pass
    finally:
        conn.close()


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
    embedding_model: str = "text-embedding-3-small"


def _clean(value: object) -> str:
    return str(value or "").strip()


def _resolve_api_key() -> str:
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
    if effort in {"minimal", "low", "medium", "high"}:
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
            raise LLMAPIError(f"LLM returned non-JSON content: {text[:400]}") from exc
    if not isinstance(parsed, dict):
        raise LLMAPIError("LLM JSON payload must be an object.")
    return parsed


def load_llm_config() -> LLMConfig | None:
    provider = (_clean(os.getenv("LLM_PROVIDER")) or "openai").lower()
    if provider in {"", "none", "disabled", "off"}:
        return None

    api_key = _resolve_api_key()
    if not api_key:
        return None

    model = _clean(os.getenv("LLM_MODEL")) or _clean(os.getenv("OPENAI_MODEL")) or "gpt-4.1-mini"
    deployment = _clean(os.getenv("LLM_DEPLOYMENT")) or _clean(os.getenv("AZURE_OPENAI_DEPLOYMENT")) or model
    if provider == "azure_openai":
        base_url = _normalize_azure_base_url(
            _clean(os.getenv("LLM_BASE_URL")) or _clean(os.getenv("AZURE_OPENAI_ENDPOINT"))
        )
        if not base_url:
            return None
        api_version = _clean(os.getenv("AZURE_OPENAI_API_VERSION"))
    else:
        base_url = _clean(os.getenv("LLM_BASE_URL")) or _clean(os.getenv("OPENAI_BASE_URL")) or "https://api.openai.com/v1"
        api_version = ""
    timeout_seconds = max(int(_clean(os.getenv("LLM_TIMEOUT_SECONDS")) or "480"), 10)
    default_temperature = "1" if provider == "azure_openai" else "0.2"
    temperature = float(_clean(os.getenv("LLM_TEMPERATURE")) or default_temperature)
    reasoning_effort = _normalized_reasoning_effort(
        _clean(os.getenv("LLM_REASONING_EFFORT")) or _clean(os.getenv("OPENAI_REASONING_EFFORT"))
    )
    embedding_model = _clean(os.getenv("EMBEDDING_MODEL")) or "text-embedding-3-small"
    embedding_deployment = _clean(os.getenv("EMBEDDING_DEPLOYMENT")) or _clean(
        os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
    )

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
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
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
        if self.config.reasoning_effort:
            payload["reasoning_effort"] = self.config.reasoning_effort

        request_url = f"{self.config.base_url}/chat/completions"
        request_headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        import requests as _requests_mod
        _max_timeout_retries = 2
        for _attempt in range(_max_timeout_retries):
            try:
                response = self.session.post(
                    request_url,
                    headers=request_headers,
                    json=payload,
                    timeout=self.config.timeout_seconds,
                )
                break
            except (_requests_mod.exceptions.ReadTimeout, _requests_mod.exceptions.ConnectionError):
                if _attempt < _max_timeout_retries - 1:
                    continue
                raise
        if response.status_code != 200 and payload.get("reasoning_effort") and _supports_reasoning_effort_retry(response.status_code, response.text):
            payload = dict(payload)
            payload.pop("reasoning_effort", None)
            response = self.session.post(
                request_url,
                headers=request_headers,
                json=payload,
                timeout=self.config.timeout_seconds,
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

        content = message.get("content")
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(_clean(item.get("text")))
            raw = "\n".join(part for part in text_parts if part).strip()
        else:
            raw = _clean(content)

        if not raw:
            raise LLMAPIError("LLM returned empty content.")
        return _strip_jargon_from_dict(_extract_first_json_object(raw))


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
        payload = {
            "model": self.config.deployment,
            "messages": [
                {"role": "system", "content": system_prompt},
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
        if self.config.reasoning_effort:
            payload["reasoning_effort"] = self.config.reasoning_effort

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
                response = self.session.post(
                    request_url,
                    headers=request_headers,
                    params=request_params,
                    json=payload,
                    timeout=self.config.timeout_seconds,
                )
                break
            except (_requests_mod.exceptions.ReadTimeout, _requests_mod.exceptions.ConnectionError):
                if _attempt < _max_timeout_retries - 1:
                    continue
                raise
        if response.status_code != 200 and payload.get("reasoning_effort") and _supports_reasoning_effort_retry(response.status_code, response.text):
            payload = dict(payload)
            payload.pop("reasoning_effort", None)
            response = self.session.post(
                request_url,
                headers=request_headers,
                params=request_params,
                json=payload,
                timeout=self.config.timeout_seconds,
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
        raw = _clean(message.get("content"))
        if not raw:
            raise LLMAPIError("LLM returned empty content.")
        return _strip_jargon_from_dict(_extract_first_json_object(raw))


class OpenAIEmbeddingClient:
    def __init__(self, config: LLMConfig, *, session: requests.Session | None = None) -> None:
        if not config.api_key:
            raise LLMAPIError("Missing LLM API key.")
        self.config = config
        self.session = session or requests.Session()

    def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        clean_texts = [str(text or "").strip() for text in list(texts or []) if str(text or "").strip()]
        if not clean_texts:
            return []
        response = self.session.post(
            f"{self.config.base_url}/embeddings",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.config.embedding_model,
                "input": clean_texts,
            },
            timeout=self.config.timeout_seconds,
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
        vectors: list[list[float]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            embedding = item.get("embedding") or []
            if isinstance(embedding, list):
                vectors.append([float(value) for value in embedding])
        return vectors


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
        clean_texts = [str(text or "").strip() for text in list(texts or []) if str(text or "").strip()]
        if not clean_texts:
            return []
        response = self.session.post(
            f"{self.config.base_url}/embeddings",
            headers={
                "api-key": self.config.api_key,
                "Content-Type": "application/json",
            },
            params={"api-version": self.config.api_version} if self.config.api_version else None,
            json={
                "model": self.config.embedding_deployment or self.config.embedding_model,
                "input": clean_texts,
            },
            timeout=self.config.timeout_seconds,
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
        vectors: list[list[float]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            embedding = item.get("embedding") or []
            if isinstance(embedding, list):
                vectors.append([float(value) for value in embedding])
        return vectors


def load_llm_client() -> OpenAIChatJSONClient | AzureOpenAIChatJSONClient | None:
    config = load_llm_config()
    if config is None:
        return None
    if config.provider == "openai":
        return OpenAIChatJSONClient(config)
    if config.provider == "azure_openai":
        return AzureOpenAIChatJSONClient(config)
    raise LLMAPIError(f"Unsupported LLM provider: {config.provider}")


def load_embedding_client() -> OpenAIEmbeddingClient | AzureOpenAIEmbeddingClient | None:
    config = load_llm_config()
    if config is None:
        return None
    if config.provider == "openai":
        return OpenAIEmbeddingClient(config)
    if config.provider == "azure_openai":
        if not config.embedding_deployment:
            return None
        return AzureOpenAIEmbeddingClient(config)
    raise LLMAPIError(f"Unsupported LLM provider: {config.provider}")


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

    # Check embeddings
    status["embedding_model"] = config.embedding_model or "(none)"
    status["embedding_deployment"] = config.embedding_deployment or "(none)"
    if not config.embedding_deployment:
        status["embeddings"] = (
            "disabled — EMBEDDING_DEPLOYMENT not set. "
            "Semantic retrieval will not work. Set EMBEDDING_DEPLOYMENT to enable."
        )
    else:
        try:
            emb_client = load_embedding_client()
            if emb_client is None:
                status["embeddings"] = "unavailable — load_embedding_client returned None"
            else:
                status["embeddings"] = "configured"
        except Exception as exc:
            status["embeddings"] = f"error — {type(exc).__name__}: {exc}"

    return status


__all__ = [
    "AzureOpenAIEmbeddingClient",
    "AzureOpenAIChatJSONClient",
    "OpenAIEmbeddingClient",
    "LLMAPIError",
    "LLMConfig",
    "OpenAIChatJSONClient",
    "check_llm_readiness",
    "load_embedding_client",
    "load_llm_client",
]
