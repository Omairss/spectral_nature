from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import time
from typing import Any
import uuid

from . import pipeline_store
from .json_utils import to_jsonable
from .zopedia_runtime import zopedia_surface_from_client


_ALLOWED_CALL_TYPES = {
    "research_grade",
    "formatter_over_aql",
    "utility",
    "schema_repair",
    "admin_probe",
}
_SENSITIVE_METADATA_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "prompt",
    "secret",
    "system_prompt",
    "token",
    "user_prompt",
}


def _clean(value: object) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() == "nan" else text


def _hash_text(value: object) -> str:
    text = _clean(value)
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _hash_json(value: Any) -> str:
    try:
        text = json.dumps(to_jsonable(value), ensure_ascii=True, sort_keys=True, default=str)
    except Exception:
        text = str(value)
    return _hash_text(text)


def _client_config_value(llm_client: Any, name: str) -> str:
    config = getattr(llm_client, "config", None)
    if config is None:
        return ""
    return _clean(getattr(config, name, ""))


def _client_provider(llm_client: Any) -> str:
    provider = _client_config_value(llm_client, "provider")
    if provider:
        return provider.lower()
    name = type(llm_client).__name__.lower()
    if "deepseek" in name:
        return "deepseek"
    if "azure" in name:
        return "azure_openai"
    if "openai" in name:
        return "openai"
    return name or "unknown"


def _safe_metadata(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "<truncated>"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            safe_key = _clean(key)[:120]
            if not safe_key:
                continue
            lower_key = safe_key.lower()
            if lower_key in _SENSITIVE_METADATA_KEYS or any(part in lower_key for part in ("api_key", "secret", "token")):
                out[safe_key] = "<redacted>"
                continue
            out[safe_key] = _safe_metadata(item, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_safe_metadata(item, depth=depth + 1) for item in list(value)[:50]]
    if isinstance(value, str):
        text = value.strip()
        return text if len(text) <= 800 else text[:797].rstrip() + "..."
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _clean(value)[:800]


def _record_gateway_call(
    *,
    model_call_id: str,
    surface: str,
    purpose: str,
    call_type: str,
    llm_client: Any,
    status: str,
    started_at_utc: datetime,
    duration_ms: float,
    schema_name: str,
    prompt_hash: str,
    schema_hash: str,
    metadata: dict[str, Any],
    error: BaseException | None = None,
) -> None:
    requested_model = _client_config_value(llm_client, "model")
    deployment = _client_config_value(llm_client, "deployment")
    resolved_model = deployment or requested_model
    safe_metadata = {
        **dict(metadata),
        "client_surface": zopedia_surface_from_client(llm_client),
        "timeout_seconds": _client_config_value(llm_client, "timeout_seconds"),
    }
    pipeline_store.record_model_call(
        model_call_id=model_call_id,
        surface=surface,
        purpose=purpose,
        call_type=call_type,
        provider=_client_provider(llm_client),
        requested_model=requested_model,
        resolved_model=resolved_model,
        provider_reported_model="",
        status=status,
        started_at_utc=started_at_utc,
        duration_ms=duration_ms,
        error_type=type(error).__name__ if error is not None else "",
        error_summary=str(error) if error is not None else "",
        schema_name=schema_name,
        prompt_hash=prompt_hash,
        schema_hash=schema_hash,
        metadata=safe_metadata,
    )


def generate_json_via_aql_zopedia_gateway(
    *,
    llm_client: Any,
    surface: str,
    purpose: str,
    call_type: str,
    system_prompt: str,
    user_prompt: str,
    schema_name: str,
    schema: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a JSON model call through the enforceable AQL/Zopedia boundary.

    The gateway keeps prompt content out of telemetry, records provider/model
    routing, and makes every native model call declare its owning surface and
    purpose. It does not change model behavior or apply local narrative fixes.
    """
    clean_surface = _clean(surface)
    clean_purpose = _clean(purpose)
    clean_call_type = _clean(call_type).lower()
    clean_schema_name = _clean(schema_name)
    if not clean_surface:
        raise ValueError("surface is required for AQL/Zopedia model calls")
    if not clean_purpose:
        raise ValueError("purpose is required for AQL/Zopedia model calls")
    if clean_call_type not in _ALLOWED_CALL_TYPES:
        raise ValueError(f"unsupported AQL/Zopedia model call_type: {call_type}")
    if llm_client is None or not hasattr(llm_client, "generate_json"):
        raise ValueError("AQL/Zopedia model call requires an LLM client with generate_json")

    model_call_id = f"aqlzmc_{uuid.uuid4().hex}"
    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    prompt_hash = _hash_text(f"{system_prompt}\n\n{user_prompt}")
    schema_hash = _hash_json(schema)
    safe_metadata = _safe_metadata(metadata or {})
    if not isinstance(safe_metadata, dict):
        safe_metadata = {"metadata": safe_metadata}
    safe_metadata.update(
        {
            "model_call_id": model_call_id,
            "schema_name": clean_schema_name,
        }
    )

    try:
        payload = llm_client.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema_name=clean_schema_name,
            schema=schema,
        )
    except Exception as exc:
        _record_gateway_call(
            model_call_id=model_call_id,
            surface=clean_surface,
            purpose=clean_purpose,
            call_type=clean_call_type,
            llm_client=llm_client,
            status="failure",
            started_at_utc=started_at,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            schema_name=clean_schema_name,
            prompt_hash=prompt_hash,
            schema_hash=schema_hash,
            metadata=safe_metadata,
            error=exc,
        )
        raise

    if isinstance(payload, dict) and "__reasoning_content" in payload:
        payload = dict(payload)
        payload.pop("__reasoning_content", None)
    result_metadata = dict(safe_metadata)
    if isinstance(payload, dict):
        result_metadata["response_keys"] = sorted(_clean(key) for key in payload.keys())[:50]
    _record_gateway_call(
        model_call_id=model_call_id,
        surface=clean_surface,
        purpose=clean_purpose,
        call_type=clean_call_type,
        llm_client=llm_client,
        status="success",
        started_at_utc=started_at,
        duration_ms=(time.perf_counter() - t0) * 1000.0,
        schema_name=clean_schema_name,
        prompt_hash=prompt_hash,
        schema_hash=schema_hash,
        metadata=result_metadata,
    )
    return payload


__all__ = ["generate_json_via_aql_zopedia_gateway"]
