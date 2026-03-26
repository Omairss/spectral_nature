from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any

import requests

from .secrets import resolve_secret_value


class LLMAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    api_key: str
    model: str
    base_url: str
    deployment: str = ""
    api_version: str = ""
    timeout_seconds: int = 60
    temperature: float = 0.2
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
    timeout_seconds = max(int(_clean(os.getenv("LLM_TIMEOUT_SECONDS")) or "60"), 10)
    default_temperature = "1" if provider == "azure_openai" else "0.2"
    temperature = float(_clean(os.getenv("LLM_TEMPERATURE")) or default_temperature)

    return LLMConfig(
        provider=provider,
        api_key=api_key,
        model=model,
        base_url=base_url.rstrip("/"),
        deployment=deployment,
        api_version=api_version,
        timeout_seconds=timeout_seconds,
        temperature=temperature,
        embedding_model=_clean(os.getenv("EMBEDDING_MODEL")) or "text-embedding-3-small",
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

        response = self.session.post(
            f"{self.config.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
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
        try:
            data = json.loads(raw)
        except Exception as exc:
            raise LLMAPIError(f"LLM returned non-JSON content: {raw[:400]}") from exc
        if not isinstance(data, dict):
            raise LLMAPIError("LLM JSON payload must be an object.")
        return data


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

        response = self.session.post(
            f"{self.config.base_url}/chat/completions",
            headers={
                "api-key": self.config.api_key,
                "Content-Type": "application/json",
            },
            params={"api-version": self.config.api_version} if self.config.api_version else None,
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
        try:
            data = json.loads(raw)
        except Exception as exc:
            raise LLMAPIError(f"LLM returned non-JSON content: {raw[:400]}") from exc
        if not isinstance(data, dict):
            raise LLMAPIError("LLM JSON payload must be an object.")
        return data


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
        if not config.deployment:
            raise LLMAPIError("Missing Azure OpenAI deployment.")
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
                "model": self.config.deployment,
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
        return AzureOpenAIEmbeddingClient(config)
    raise LLMAPIError(f"Unsupported LLM provider: {config.provider}")


__all__ = [
    "AzureOpenAIEmbeddingClient",
    "AzureOpenAIChatJSONClient",
    "OpenAIEmbeddingClient",
    "LLMAPIError",
    "LLMConfig",
    "OpenAIChatJSONClient",
    "load_embedding_client",
    "load_llm_client",
]
