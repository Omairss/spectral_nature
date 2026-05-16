# DeepSeek Omnibar Agent Adapter

Date: 2026-05-05

## Scope

The omnibar / research agent can use a provider-specific LLM runtime through the `OMNIBAR_AGENT_` env prefix. This keeps the rest of the app on the default `LLM_*` provider while testing DeepSeek reasoning traces in one interactive agent.

## Runtime Env

- `OMNIBAR_AGENT_LLM_PROVIDER=deepseek`
- `OMNIBAR_AGENT_LLM_MODEL=deepseek-reasoner`
- `OMNIBAR_AGENT_LLM_BASE_URL=https://api.deepseek.com`
- `OMNIBAR_AGENT_LLM_API_KEY_SECRET_NAME=deepseek-api-key`
- `OMNIBAR_AGENT_LLM_TIMEOUT_SECONDS=480`
- Optional: `OMNIBAR_AGENT_SYNTHESIS_LLM_MODEL=deepseek-chat` lets the agent keep `deepseek-reasoner` for planning/reasoning traces while using the faster chat model for final answer synthesis.

The API key is stored in Azure Key Vault as `deepseek-api-key`.

## Adapter Behavior

- DeepSeek uses `response_format={"type":"json_object"}` plus an explicit schema instruction in the prompt.
- The adapter captures `message.reasoning_content` into `__reasoning_content`.
- `services.omnibar_agent` emits that provider trace as `model_reasoning_trace`.
- `app.py` renders the trace inside the existing collapsible Thinking Trace expander.
- Final answer synthesis can use a scoped synthesis model override. This is useful after deterministic bootstrap has already collected evidence and the remaining step is writing/compression rather than tool planning.
- Tool calls run through a bounded heartbeat wrapper. Slow live-search calls emit `tool_heartbeat` progress events and degrade with `tool_timeout` instead of leaving the UI on a static "Checking..." state.
- Hidden prefetch fallbacks also run through a bounded wrapper. If keyword extraction for retained-evidence search is slow, the agent emits preparation heartbeat/timeout events and continues to visible evidence collection.

## Constraints

DeepSeek reasoning mode exposes reasoning traces, but it does not use the exact same strict JSON schema response contract as OpenAI/Azure. Keep provider-specific behavior inside `services.llm.DeepSeekChatJSONClient` instead of branching across agent code.
