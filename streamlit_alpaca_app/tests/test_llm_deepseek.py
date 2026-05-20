from __future__ import annotations

from services import llm


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
        schema_name="omnibar_agent_final",
        schema={"type": "object", "properties": {}, "required": []},
    )

    assert session.url == "https://api.deepseek.com/chat/completions"
    assert session.payload["response_format"] == {"type": "json_object"}
    assert "temperature" not in session.payload
    assert "reasoning_effort" not in session.payload
    assert result["answer_markdown"] == "Done"
    assert result["__reasoning_content"] == "Checked the evidence before answering."


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
