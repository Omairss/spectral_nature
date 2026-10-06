from __future__ import annotations

import pytest

from services import llm
from services import secrets


class _FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {
            "choices": [
                {
                    "message": {
                        "reasoning_content": "Checked the evidence before answering.",
                        "content": '{"answer_markdown":"Done","confidence":"high","limitations":[],"used_tool_call_ids":[]}',
                    }
                }
            ]
        }


class _FakeSession:
    def __init__(self) -> None:
        self.payload = None

    def post(self, url, *, headers, json, timeout):
        del headers, timeout
        self.url = url
        self.payload = json
        return _FakeResponse()


class _HeaderCapturingSession(_FakeSession):
    def __init__(self) -> None:
        super().__init__()
        self.headers = None

    def post(self, url, *, headers, json, timeout):
        self.headers = dict(headers)
        return super().post(url, headers=headers, json=json, timeout=timeout)


class _SequenceResponse:
    status_code = 200
    text = ""

    def __init__(self, content: str) -> None:
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class _SequenceSession:
    def __init__(self, contents: list[str]) -> None:
        self.contents = list(contents)
        self.payloads: list[dict[str, object]] = []

    def post(self, url, *, headers, json, timeout):
        del url, headers, timeout
        self.payloads.append(json)
        return _SequenceResponse(self.contents.pop(0))


class _InsufficientBalanceResponse:
    status_code = 402
    text = '{"error":{"message":"Insufficient Balance","type":"unknown_error","code":"insufficient_balance"}}'

    def json(self):
        return {
            "error": {
                "message": "Insufficient Balance",
                "type": "unknown_error",
                "code": "insufficient_balance",
            }
        }


class _InsufficientBalanceSession:
    def post(self, url, *, headers, json, timeout):
        del url, headers, json, timeout
        return _InsufficientBalanceResponse()


def test_deepseek_client_uses_json_object_and_captures_reasoning():
    session = _FakeSession()
    client = llm.DeepSeekChatJSONClient(
        llm.LLMConfig(
            provider="deepseek",
            api_key="test",
            model="deepseek-reasoner",
            base_url="https://api.deepseek.com",
            reasoning_effort="high",
        ),
        session=session,
    )

    result = client.generate_json(
        system_prompt="System",
        user_prompt="User",
        schema_name="zopedia_agent_final",
        schema={"type": "object", "properties": {}, "required": []},
    )

    assert session.url == "https://api.deepseek.com/chat/completions"
    assert session.payload["response_format"] == {"type": "json_object"}
    assert "temperature" not in session.payload
    assert "reasoning_effort" not in session.payload
    assert result["answer_markdown"] == "Done"
    assert result["__reasoning_content"] == "Checked the evidence before answering."


def test_deepseek_client_repairs_malformed_json_once():
    session = _SequenceSession(
        [
            "answer_markdown: Done\nconfidence: high",
            '{"answer_markdown":"Done","confidence":"high","limitations":[]}',
        ]
    )
    client = llm.DeepSeekChatJSONClient(
        llm.LLMConfig(
            provider="deepseek",
            api_key="test",
            model="deepseek-chat",
            base_url="https://api.deepseek.com",
        ),
        session=session,
    )

    result = client.generate_json(
        system_prompt="System",
        user_prompt="User",
        schema_name="zopedia_agent_final",
        schema={"type": "object", "properties": {}, "required": []},
    )

    assert result["answer_markdown"] == "Done"
    assert len(session.payloads) == 2
    assert "Repair the model output" in session.payloads[1]["messages"][0]["content"]


def test_deepseek_client_retries_empty_success_response_once():
    session = _SequenceSession(
        [
            "",
            '{"answer_markdown":"Recovered","confidence":"medium","limitations":[]}',
        ]
    )
    client = llm.DeepSeekChatJSONClient(
        llm.LLMConfig(
            provider="deepseek",
            api_key="test",
            model="deepseek-reasoner",
            base_url="https://api.deepseek.com",
        ),
        session=session,
    )

    result = client.generate_json(
        system_prompt="System",
        user_prompt="User",
        schema_name="zopedia_agent_final",
        schema={"type": "object", "properties": {}, "required": []},
    )

    assert result["answer_markdown"] == "Recovered"
    assert len(session.payloads) == 2
    assert session.payloads[0] == session.payloads[1]


def test_deepseek_client_explains_insufficient_account_funds():
    client = llm.DeepSeekChatJSONClient(
        llm.LLMConfig(
            provider="deepseek",
            api_key="test",
            model="deepseek-reasoner",
            base_url="https://api.deepseek.com",
        ),
        session=_InsufficientBalanceSession(),
    )

    with pytest.raises(llm.LLMAPIError, match="DeepSeek account has insufficient funds") as exc_info:
        client.generate_json(
            system_prompt="System",
            user_prompt="User",
            schema_name="zopedia_agent_final",
            schema={"type": "object", "properties": {}, "required": []},
        )

    message = str(exc_info.value)
    assert "Add credit in DeepSeek billing before retrying the pipeline" in message
    assert "HTTP 402 (insufficient_balance)" in message


def test_load_llm_client_supports_global_deepseek(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "test")
    monkeypatch.setenv("LLM_MODEL", "deepseek-reasoner")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "high")

    client = llm.load_llm_client()

    assert isinstance(client, llm.DeepSeekChatJSONClient)
    assert client.config.model == "deepseek-reasoner"
    assert client.config.reasoning_effort == ""


def test_load_llm_client_supports_unauthenticated_local_runtime(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "local")
    monkeypatch.setenv("LLM_MODEL", "qwen-local")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9000/v1")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(llm, "_resolve_api_key", lambda env_prefix="": "")

    client = llm.load_llm_client()

    assert isinstance(client, llm.LocalChatJSONClient)
    assert isinstance(client, llm.JSONLLMClient)
    assert client.config.model == "qwen-local"
    assert client.config.base_url == "http://127.0.0.1:9000/v1"


def test_local_runtime_omits_authorization_header_without_api_key():
    session = _HeaderCapturingSession()
    client = llm.LocalChatJSONClient(
        llm.LLMConfig(
            provider="local",
            api_key="",
            model="qwen-local",
            base_url="http://127.0.0.1:9000/v1",
        ),
        session=session,
    )

    result = client.generate_json(
        system_prompt="System",
        user_prompt="User",
        schema_name="local_result",
        schema={"type": "object", "properties": {}, "required": []},
    )

    assert session.url == "http://127.0.0.1:9000/v1/chat/completions"
    assert session.headers == {"Content-Type": "application/json"}
    assert result["answer_markdown"] == "Done"


def test_load_llm_config_reads_generated_deployment_provider_with_secret(monkeypatch, tmp_path):
    generated_dir = tmp_path / "infra" / ".generated"
    generated_dir.mkdir(parents=True)
    (generated_dir / "deployment.local.env").write_text(
        "\n".join(
            [
                "KEYVAULT_NAME=local-generated-vault",
                "LLM_PROVIDER=deepseek",
                "LLM_MODEL=deepseek-reasoner",
                "LLM_DEPLOYMENT=deepseek-reasoner",
                "LLM_BASE_URL=https://api.deepseek.com",
                "LLM_API_KEY_SECRET_NAME=deepseek-api-key",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_resolve_secret_value(env_names, *, secret_name_env=None, default_secret_name=None, placeholders=None):
        del env_names, secret_name_env, default_secret_name, placeholders
        return "deepseek-test-key"

    monkeypatch.setattr(secrets, "APP_ROOT", tmp_path)
    monkeypatch.setattr(llm, "resolve_secret_value", fake_resolve_secret_value)
    for key in (
        "DEPLOYMENT_ENV_FILE",
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_DEPLOYMENT",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "LLM_API_KEY_SECRET_NAME",
        "OPENAI_API_KEY_SECRET_NAME",
    ):
        monkeypatch.delenv(key, raising=False)

    config = llm.load_llm_config()

    assert config is not None
    assert config.provider == "deepseek"
    assert config.model == "deepseek-reasoner"
    assert config.base_url == "https://api.deepseek.com"
    assert config.api_key == "deepseek-test-key"
