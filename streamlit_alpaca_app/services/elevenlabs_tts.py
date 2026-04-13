from __future__ import annotations

from dataclasses import dataclass
import os

import requests

from .secrets import resolve_secret_value


class ElevenLabsTTSAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class ElevenLabsTTSConfig:
    api_key: str
    voice_id: str
    model_id: str
    output_format: str
    base_url: str = "https://api.elevenlabs.io"
    timeout_seconds: int = 60


def _clean(value: object) -> str:
    return str(value or "").strip()


def load_elevenlabs_tts_config() -> ElevenLabsTTSConfig | None:
    api_key = resolve_secret_value(
        ["ELEVENLABS_API_KEY"],
        secret_name_env="ELEVENLABS_API_KEY_SECRET_NAME",
        default_secret_name="elevenlabs-api-key",
    )
    voice_id = resolve_secret_value(
        ["ELEVENLABS_VOICE_ID"],
        secret_name_env="ELEVENLABS_VOICE_ID_SECRET_NAME",
        default_secret_name="elevenlabs-voice-id",
    )
    if not api_key or not voice_id:
        return None

    base_url = _clean(os.getenv("ELEVENLABS_BASE_URL")) or "https://api.elevenlabs.io"
    model_id = _clean(os.getenv("ELEVENLABS_MODEL_ID")) or "eleven_multilingual_v2"
    output_format = _clean(os.getenv("ELEVENLABS_OUTPUT_FORMAT")) or "mp3_44100_128"
    timeout_seconds = max(int(_clean(os.getenv("ELEVENLABS_TIMEOUT_SECONDS")) or "60"), 10)

    return ElevenLabsTTSConfig(
        api_key=api_key,
        voice_id=voice_id,
        model_id=model_id,
        output_format=output_format,
        base_url=base_url.rstrip("/"),
        timeout_seconds=timeout_seconds,
    )


def audio_mime_type(output_format: object) -> str:
    codec = _clean(output_format).split("_", 1)[0].lower()
    if codec == "wav":
        return "audio/wav"
    if codec in {"ulaw", "mulaw"}:
        return "audio/basic"
    return "audio/mpeg"


def audio_file_extension(output_format: object) -> str:
    codec = _clean(output_format).split("_", 1)[0].lower()
    if codec == "wav":
        return "wav"
    if codec in {"ulaw", "mulaw"}:
        return "ulaw"
    return "mp3"


class ElevenLabsTTSClient:
    def __init__(self, config: ElevenLabsTTSConfig, *, session: requests.Session | None = None) -> None:
        if not config.api_key:
            raise ElevenLabsTTSAPIError("Missing ElevenLabs API key.")
        if not config.voice_id:
            raise ElevenLabsTTSAPIError("Missing ElevenLabs voice id.")
        self.config = config
        self.session = session or requests.Session()

    def synthesize(self, text: str) -> bytes:
        normalized_text = " ".join(str(text or "").split()).strip()
        if not normalized_text:
            raise ElevenLabsTTSAPIError("Cannot synthesize empty text.")

        response = self.session.post(
            f"{self.config.base_url}/v1/text-to-speech/{self.config.voice_id}",
            params={"output_format": self.config.output_format},
            headers={
                "xi-api-key": self.config.api_key,
                "Content-Type": "application/json",
            },
            json={
                "text": normalized_text,
                "model_id": self.config.model_id,
            },
            timeout=self.config.timeout_seconds,
        )
        if response.status_code != 200:
            raise ElevenLabsTTSAPIError(
                f"ElevenLabs TTS failed status={response.status_code}: {response.text[:400]}"
            )
        if not response.content:
            raise ElevenLabsTTSAPIError("ElevenLabs returned empty audio content.")
        return response.content


__all__ = [
    "audio_file_extension",
    "ElevenLabsTTSAPIError",
    "ElevenLabsTTSClient",
    "ElevenLabsTTSConfig",
    "audio_mime_type",
    "load_elevenlabs_tts_config",
]
