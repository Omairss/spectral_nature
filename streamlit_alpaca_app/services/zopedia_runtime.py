from __future__ import annotations

from typing import Any

from . import llm as llm_service
from .llm import (
    AzureOpenAIChatJSONClient,
    DeepSeekChatJSONClient,
    OpenAIChatJSONClient,
)


ZopediaLLMClient = OpenAIChatJSONClient | AzureOpenAIChatJSONClient | DeepSeekChatJSONClient


def _clean(value: object) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() == "nan" else text


def load_zopedia_llm_client(
    *,
    surface: str,
    env_prefix: str = "",
    fallback_to_default: bool = False,
) -> ZopediaLLMClient | None:
    """Load the app model client through the Zopedia-native boundary.

    `surface` is required on purpose. It makes every model caller name the
    product or pipeline layer that owns the call, which keeps random LLM
    shortcuts from creeping back into feature code.
    """
    clean_surface = _clean(surface)
    if not clean_surface:
        raise ValueError("surface is required for Zopedia LLM access")

    try:
        client = llm_service.load_llm_client(env_prefix=env_prefix)
    except TypeError:
        # Some tests monkeypatch the lower-level loader with a no-arg lambda.
        client = llm_service.load_llm_client()
    if client is None and fallback_to_default and env_prefix:
        client = llm_service.load_llm_client()
    if client is not None:
        try:
            setattr(client, "_zopedia_surface", clean_surface)
        except Exception:
            pass
    return client


def zopedia_surface_from_client(llm_client: Any) -> str:
    return _clean(getattr(llm_client, "_zopedia_surface", ""))


__all__ = [
    "ZopediaLLMClient",
    "load_zopedia_llm_client",
    "zopedia_surface_from_client",
]
