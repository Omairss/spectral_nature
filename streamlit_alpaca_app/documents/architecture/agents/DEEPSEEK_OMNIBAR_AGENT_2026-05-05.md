# DeepSeek Shared LLM Adapter

Date: 2026-05-05

## Scope

DeepSeek is a shared LLM provider, not a Zopedia-only override. Zopedia, AQL, API, UI, and jobs should enter through the same `LLM_*` runtime boundary.

## Runtime Env

- `LLM_PROVIDER=deepseek`
- `LLM_MODEL=deepseek-reasoner`
- `LLM_BASE_URL=https://api.deepseek.com`
- `LLM_API_KEY_SECRET_NAME=deepseek-api-key`
- `LLM_TIMEOUT_SECONDS=480`

The API key is stored in Azure Key Vault as `deepseek-api-key`.

## Adapter Behavior

- DeepSeek uses `response_format={"type":"json_object"}` plus an explicit schema instruction in the prompt.
- The adapter captures `message.reasoning_content` into `__reasoning_content`.
- Interactive agents emit that provider trace as `model_reasoning_trace`.
- `app.py` renders the trace inside the existing collapsible Thinking Trace expander.
- OpenAI/Azure-style `reasoning_effort` settings are not forwarded to DeepSeek.
- Tool calls run through a bounded heartbeat wrapper. Slow live-search calls emit `tool_heartbeat` progress events and degrade with `tool_timeout` instead of leaving the UI on a static "Checking..." state.
- Hidden prefetch fallbacks also run through a bounded wrapper. If keyword extraction for retained-evidence search is slow, the agent emits preparation heartbeat/timeout events and continues to visible evidence collection.

## Constraints

DeepSeek reasoning mode exposes reasoning traces, but it does not use the exact same strict JSON schema response contract as OpenAI/Azure. Keep provider-specific behavior inside `services.llm.DeepSeekChatJSONClient` instead of branching across agent code.

Do not add `OMNIBAR_AGENT_*` provider overrides. Provider selection belongs to the global LLM layer unless a temporary experiment is explicitly documented.
