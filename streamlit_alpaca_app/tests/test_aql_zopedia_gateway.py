from __future__ import annotations

from dataclasses import dataclass

import pytest

from services import aql_zopedia_gateway, pipeline_store


@dataclass(frozen=True)
class _FakeConfig:
    provider: str = "deepseek"
    model: str = "deepseek-reasoner"
    deployment: str = ""
    timeout_seconds: int = 480


class _FakeLLM:
    config = _FakeConfig()

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def generate_json(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.fail:
            raise RuntimeError("provider exhausted")
        return {"ok": True, "summary": "Gateway call completed.", "__reasoning_content": "internal"}


def test_gateway_records_success_without_prompt_text(monkeypatch):
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(pipeline_store, "record_model_call", lambda **kwargs: captured.append(kwargs) or True)

    llm = _FakeLLM()
    result = aql_zopedia_gateway.generate_json_via_aql_zopedia_gateway(
        llm_client=llm,
        surface="test.surface",
        purpose="unit_success",
        call_type="utility",
        system_prompt="system prompt that must not be stored",
        user_prompt="user prompt that must not be stored",
        schema_name="gateway_test",
        schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}, "summary": {"type": "string"}},
            "required": ["ok", "summary"],
            "additionalProperties": False,
        },
        metadata={"prompt": "secret prompt text", "safe_id": "abc"},
    )

    assert result["ok"] is True
    assert "__reasoning_content" not in result
    assert len(captured) == 1
    event = captured[0]
    assert event["surface"] == "test.surface"
    assert event["purpose"] == "unit_success"
    assert event["call_type"] == "utility"
    assert event["provider"] == "deepseek"
    assert event["requested_model"] == "deepseek-reasoner"
    assert event["resolved_model"] == "deepseek-reasoner"
    assert event["status"] == "success"
    assert event["prompt_hash"]
    assert event["schema_hash"]
    metadata = event["metadata"]
    assert metadata["prompt"] == "<redacted>"
    assert metadata["safe_id"] == "abc"
    assert metadata["response_keys"] == ["ok", "summary"]
    assert "system prompt that must not be stored" not in str(metadata)
    assert "user prompt that must not be stored" not in str(metadata)


def test_gateway_records_failure_and_reraises(monkeypatch):
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(pipeline_store, "record_model_call", lambda **kwargs: captured.append(kwargs) or True)

    with pytest.raises(RuntimeError, match="provider exhausted"):
        aql_zopedia_gateway.generate_json_via_aql_zopedia_gateway(
            llm_client=_FakeLLM(fail=True),
            surface="test.surface",
            purpose="unit_failure",
            call_type="utility",
            system_prompt="system",
            user_prompt="user",
            schema_name="gateway_test",
            schema={"type": "object", "properties": {}, "additionalProperties": False},
        )

    assert len(captured) == 1
    event = captured[0]
    assert event["status"] == "failure"
    assert event["error_type"] == "RuntimeError"
    assert "provider exhausted" in event["error_summary"]
