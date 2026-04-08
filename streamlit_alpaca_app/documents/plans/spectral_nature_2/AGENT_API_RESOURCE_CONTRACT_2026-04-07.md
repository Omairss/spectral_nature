# Agent API Resource Contract (2026-04-07)

## Goal

Define one shared `/v1/agent/*` contract that both:

- Streamlit homepage workspace, and
- iPhone app

can consume without duplicating orchestration logic.

This contract sits above the existing shared query layer and below any specific client UI.

## Design Rules

- Reuse existing auth, scope, and principal model from `api/main.py` and `services/api_auth.py`.
- Reuse existing structured-data outputs from `QueryService`, `QueryResponse`, `ChartModel`, and `DataProvenance`.
- Keep agent state explicit and user-visible: messages, runs, tool calls, artifacts, notes.
- Keep run execution async-first. Do not block mobile/UI clients on long tool chains.
- Keep artifacts first-class so clients can render charts/tables/code without reparsing chat text.
- Keep enum sets small and stable for iPhone use.

## Relationship To Existing API

Current reusable backend pieces already exist:

- auth and scopes: `api/main.py`, `services/api_auth.py`
- query/data boundary: `data_access/query_service.py`
- JSON-ready shared contracts: `data_access/contracts.py`
- MCP/JSON-RPC tool transport: `POST /v1/agent/rpc`

The new `/v1/agent/*` resources should reuse the same execution core as:

- `POST /v1/query`
- `POST /v1/dataset/{name}`
- `POST /v1/chart/{name}`
- `POST /v1/agent/rpc`

REST resources are for product clients like Streamlit and iPhone.
MCP/JSON-RPC remains for tool-native agents and machine integrations.

## Spectral Nature 2 Omnibar Companion Contract

Spectral Nature 2 should use one shared text bar as the default entrypoint for both:

- quick search / navigation
- agentic chat / analysis

Because not every omnibar request should create an agent session, this should sit beside the `/v1/agent/*` resources, not inside them.

Recommended endpoint:

- `POST /v1/omnibar/resolve`

Purpose:

- accept one text input
- classify intent
- return either quick-search results or the next agent action

Recommended request:

```json
{
  "query": "CPI impact on semis",
  "client_type": "streamlit",
  "session_id": null,
  "preferred_mode": "auto",
  "context_items": [
    {
      "kind": "symbol",
      "ref": "NVDA",
      "label": "NVDA"
    }
  ]
}
```

Stable enums:

- `preferred_mode`: `auto`, `search`, `agent`

Recommended response:

```json
{
  "intent": "agent",
  "confidence": 0.92,
  "query_echo": "CPI impact on semis",
  "search_results": [],
  "agent_action": {
    "session_id": "agsn_123",
    "suggested_message_blocks": [
      {
        "type": "text",
        "text": "CPI impact on semis"
      }
    ]
  }
}
```

Stable enums:

- `intent`: `search`, `navigate`, `agent`, `ambiguous`

Rules:

- exact ticker/entity/bundle/release match should usually return `navigate` or `search`
- natural-language analysis prompts should usually return `agent`
- short ambiguous input should return `ambiguous` with both search and agent options
- `resolve` should not mutate agent state by itself
- if the client chooses agent flow, it should then call the existing session/message endpoint

Recommended result item shape:

```json
{
  "result_id": "sr_123",
  "kind": "symbol",
  "ref": "NVDA",
  "label": "NVDA",
  "subtitle": "NVIDIA Corp.",
  "score": 0.98,
  "navigation_target": {
    "screen": "ticker",
    "params": {
      "symbol": "NVDA"
    }
  }
}
```

Stable enums for result `kind`:

- `symbol`
- `bundle_id`
- `attention_event`
- `macro_release`
- `chart`
- `dataset`
- `document`
- `session`

## Versioning

- Base path: `/v1/agent/*`
- Response style: simple top-level objects, matching current API style
- Timestamps: ISO 8601 UTC strings
- IDs: opaque strings
- Pagination: cursor-based where lists can grow

## Required Scopes

Add or reserve these scopes alongside the current auth model:

- `agent:session:read`
- `agent:session:write`
- `agent:run`
- `agent:artifact:read`
- `agent:note:read`
- `agent:note:write`

Scope guidance:

- homepage signed-in user: all of the above except admin scopes
- iPhone signed-in user: same as homepage user
- machine agent key: only the scopes it needs

## Canonical Resources

### `agent_session`

Purpose:

- long-lived workspace container
- owns messages, runs, artifacts, notes, and pinned context

Schema:

```json
{
  "session_id": "agsn_123",
  "title": "Macro follow-up on CPI shock",
  "status": "active",
  "client_type": "streamlit",
  "created_at": "2026-04-07T12:00:00Z",
  "updated_at": "2026-04-07T12:03:15Z",
  "last_message_at": "2026-04-07T12:03:15Z",
  "created_by": {
    "principal_type": "user",
    "user_id": "usr_123"
  },
  "context_items": [
    {
      "context_id": "ctx_1",
      "kind": "symbol",
      "ref": "AAPL",
      "label": "AAPL",
      "metadata": {
        "source": "homepage"
      }
    }
  ],
  "metadata": {
    "homepage_origin": "top_events",
    "homepage_bundle_id": "symbol::AAPL"
  }
}
```

Stable enums:

- `status`: `active`, `archived`
- `client_type`: `streamlit`, `iphone`, `api`, `unknown`

### `agent_message`

Purpose:

- durable conversation record
- must not rely on rendered transcript text alone

Schema:

```json
{
  "message_id": "agmsg_123",
  "session_id": "agsn_123",
  "role": "user",
  "created_at": "2026-04-07T12:03:15Z",
  "status": "completed",
  "content_blocks": [
    {
      "type": "text",
      "text": "Explain whether today's CPI surprise supports lower long-duration tech."
    }
  ],
  "run_id": "agrun_123",
  "metadata": {
    "client_request_id": "iphone-compose-001"
  }
}
```

Stable enums:

- `role`: `system`, `user`, `assistant`, `tool`
- `status`: `pending`, `completed`, `failed`

### `agent_run`

Purpose:

- execution record for one assistant turn
- owns tool calls and output artifacts

Schema:

```json
{
  "run_id": "agrun_123",
  "session_id": "agsn_123",
  "trigger_message_id": "agmsg_123",
  "assistant_message_id": "agmsg_124",
  "status": "running",
  "mode": "async",
  "model": "planner",
  "created_at": "2026-04-07T12:03:15Z",
  "started_at": "2026-04-07T12:03:16Z",
  "completed_at": null,
  "last_event_at": "2026-04-07T12:03:19Z",
  "budgets": {
    "max_tool_calls": 12,
    "max_search_queries": 6,
    "max_sandbox_runs": 0
  },
  "usage": {
    "tool_call_count": 2,
    "artifact_count": 1
  },
  "error": null
}
```

Stable enums:

- `status`: `queued`, `running`, `completed`, `failed`, `cancelled`, `timed_out`
- `mode`: `async`, `sync`

### `agent_tool_call`

Purpose:

- first-class log of tool activity
- should be renderable directly by homepage and iPhone

Schema:

```json
{
  "tool_call_id": "agtc_123",
  "run_id": "agrun_123",
  "tool_name": "dataset.attention_research_bundle",
  "status": "completed",
  "started_at": "2026-04-07T12:03:16Z",
  "completed_at": "2026-04-07T12:03:16Z",
  "arguments": {
    "bundle_id": "symbol::AAPL",
    "force_refresh": false
  },
  "result_summary": {
    "result_type": "dataset",
    "artifact_ids": ["agart_123"]
  },
  "error": null
}
```

Stable enums:

- `status`: `queued`, `running`, `completed`, `failed`, `cancelled`

### `agent_artifact`

Purpose:

- durable output block that clients can render outside the transcript

Schema:

```json
{
  "artifact_id": "agart_123",
  "session_id": "agsn_123",
  "run_id": "agrun_123",
  "message_id": "agmsg_124",
  "kind": "chart",
  "title": "AAPL pullback versus macro regime",
  "mime_type": "application/json",
  "created_at": "2026-04-07T12:03:16Z",
  "summary": "Chart model returned from shared query service.",
  "preview": {
    "chart_id": "technical_pullback",
    "title": "AAPL Pullback"
  },
  "source": {
    "tool_name": "chart.technical_pullback",
    "provenance": {
      "mode": "materialized",
      "datasets": ["technical_signal_history"],
      "details": {}
    }
  },
  "content_ref": {
    "storage_type": "inline_json",
    "inline_json": {
      "chart_id": "technical_pullback"
    }
  }
}
```

Stable enums:

- `kind`: `text`, `markdown`, `dataset`, `table`, `chart`, `code`, `json`, `image`, `file`, `note`

Artifact rules:

- small payloads may be inline
- larger payloads should use a separate fetch endpoint
- chart artifacts should preserve `ChartModel` shape when possible
- dataset artifacts should preserve `QueryResponse.payload` row shape when possible

### `agent_note`

Purpose:

- explicit user-visible scratchpad
- not hidden chain-of-thought

Schema:

```json
{
  "note_id": "agn_123",
  "session_id": "agsn_123",
  "kind": "observation",
  "title": "Initial view",
  "body_markdown": "CPI surprise looks disinflationary for yields but I want to verify USD behavior.",
  "created_at": "2026-04-07T12:02:00Z",
  "updated_at": "2026-04-07T12:04:00Z",
  "author_role": "user",
  "pinned": true,
  "metadata": {}
}
```

Stable enums:

- `kind`: `plan`, `observation`, `todo`, `assumption`, `result`
- `author_role`: `user`, `assistant`

### `agent_run_event`

Purpose:

- pollable event stream for long runs
- easier than full websocket/streaming for v1

Schema:

```json
{
  "sequence": 12,
  "run_id": "agrun_123",
  "event_type": "tool_completed",
  "created_at": "2026-04-07T12:03:16Z",
  "payload": {
    "tool_call_id": "agtc_123",
    "tool_name": "dataset.attention_research_bundle",
    "artifact_ids": ["agart_123"]
  }
}
```

Stable enums:

- `event_type`: `run_queued`, `run_started`, `message_created`, `tool_started`, `tool_completed`, `tool_failed`, `artifact_created`, `note_created`, `run_completed`, `run_failed`, `run_cancelled`

## Content Block Schema

`agent_message.content_blocks` should use typed blocks so both clients can render consistently.

Supported block types:

### `text`

```json
{
  "type": "text",
  "text": "CPI came in softer than expected."
}
```

### `markdown`

```json
{
  "type": "markdown",
  "markdown": "## Summary\nSoft CPI supported duration."
}
```

### `artifact_ref`

```json
{
  "type": "artifact_ref",
  "artifact_id": "agart_123",
  "label": "Pullback chart"
}
```

### `tool_call_ref`

```json
{
  "type": "tool_call_ref",
  "tool_call_id": "agtc_123",
  "label": "Loaded AAPL research bundle"
}
```

### `note_ref`

```json
{
  "type": "note_ref",
  "note_id": "agn_123",
  "label": "Initial view"
}
```

### `context_ref`

```json
{
  "type": "context_ref",
  "context_id": "ctx_1",
  "label": "Pinned symbol: AAPL"
}
```

## REST Endpoints

### `POST /v1/omnibar/resolve`

Resolve Spectral Nature 2 omnibar input into either quick-search behavior or agent behavior.

Response:

```json
{
  "intent": "ambiguous",
  "confidence": 0.54,
  "query_echo": "apple",
  "search_results": [
    {
      "result_id": "sr_1",
      "kind": "symbol",
      "ref": "AAPL",
      "label": "AAPL",
      "subtitle": "Apple Inc."
    }
  ],
  "agent_action": {
    "suggested_message_blocks": [
      {
        "type": "text",
        "text": "apple"
      }
    ]
  }
}
```

### `POST /v1/agent/sessions`

Create a new workspace session.

Request:

```json
{
  "title": "CPI follow-up",
  "client_type": "iphone",
  "context_items": [
    {
      "kind": "bundle_id",
      "ref": "symbol::AAPL",
      "label": "AAPL research bundle"
    }
  ],
  "metadata": {
    "entrypoint": "homepage_top_event"
  }
}
```

Response:

```json
{
  "ok": true,
  "session": {
    "session_id": "agsn_123",
    "title": "CPI follow-up",
    "status": "active",
    "client_type": "iphone"
  }
}
```

### `GET /v1/agent/sessions/{session_id}`

Return session metadata plus pinned context.

Response:

```json
{
  "session": {
    "session_id": "agsn_123",
    "title": "CPI follow-up",
    "status": "active",
    "client_type": "iphone",
    "context_items": []
  }
}
```

### `GET /v1/agent/sessions/{session_id}/messages`

List messages in reverse chronological or chronological order.

Query params:

- `limit`
- `cursor`
- `order` (`asc`, `desc`)

Response:

```json
{
  "messages": [],
  "next_cursor": null
}
```

### `POST /v1/agent/sessions/{session_id}/messages`

Persist a user message and start an assistant run.

Request:

```json
{
  "content_blocks": [
    {
      "type": "text",
      "text": "What is the macro case for semis after today's CPI?"
    }
  ],
  "context_items": [
    {
      "kind": "symbol",
      "ref": "NVDA",
      "label": "NVDA"
    }
  ],
  "run_options": {
    "mode": "async",
    "allow_tools": true,
    "allow_sandbox": false
  },
  "client_request_id": "iphone-compose-002"
}
```

Response:

```json
{
  "ok": true,
  "message": {
    "message_id": "agmsg_200",
    "role": "user",
    "status": "completed"
  },
  "run": {
    "run_id": "agrun_200",
    "status": "queued",
    "mode": "async"
  }
}
```

Behavior:

- always create the user message first
- create an async run by default
- create assistant message only when output is available

### `GET /v1/agent/runs/{run_id}`

Return latest run status and summary.

Response:

```json
{
  "run": {
    "run_id": "agrun_200",
    "status": "running",
    "usage": {
      "tool_call_count": 3,
      "artifact_count": 2
    }
  }
}
```

### `GET /v1/agent/runs/{run_id}/events`

Pollable event stream.

Query params:

- `after_sequence`
- `limit`

Response:

```json
{
  "events": [],
  "last_sequence": 0,
  "run_status": "running"
}
```

### `POST /v1/agent/runs/{run_id}/cancel`

Cancel a queued or running run.

Response:

```json
{
  "ok": true,
  "run": {
    "run_id": "agrun_200",
    "status": "cancelled"
  }
}
```

### `GET /v1/agent/sessions/{session_id}/artifacts`

List artifact metadata for a session.

Query params:

- `run_id`
- `message_id`
- `kind`
- `limit`
- `cursor`

Response:

```json
{
  "artifacts": [],
  "next_cursor": null
}
```

### `GET /v1/agent/artifacts/{artifact_id}`

Return artifact metadata and, if small enough, content inline.

Response:

```json
{
  "artifact": {
    "artifact_id": "agart_123",
    "kind": "chart",
    "preview": {},
    "content_ref": {
      "storage_type": "inline_json",
      "inline_json": {}
    }
  }
}
```

### `POST /v1/agent/sessions/{session_id}/notes`

Create a note.

Request:

```json
{
  "kind": "observation",
  "title": "USD check",
  "body_markdown": "Need to verify whether DXY confirmed the rates move.",
  "pinned": true
}
```

Response:

```json
{
  "ok": true,
  "note": {
    "note_id": "agn_200",
    "kind": "observation",
    "pinned": true
  }
}
```

### `GET /v1/agent/sessions/{session_id}/notes`

List notes for the session.

Response:

```json
{
  "notes": []
}
```

## Context Item Contract

Use a small typed context reference model so clients can pin homepage/iPhone selections without custom ad hoc payloads.

Schema:

```json
{
  "context_id": "ctx_1",
  "kind": "bundle_id",
  "ref": "symbol::AAPL",
  "label": "AAPL research bundle",
  "params": {},
  "metadata": {
    "source": "homepage"
  }
}
```

Stable enums for `kind`:

- `symbol`
- `bundle_id`
- `dataset_query`
- `chart_query`
- `attention_event`
- `run_trace`
- `document`
- `note`

## Tool Namespace Contract

Keep tool names explicit and stable.

Phase 1 reusable tool namespaces:

- `system.capabilities`
- `query.execute`
- `dataset.{name}`
- `chart.{name}`
- `workspace.search_news`
- `workspace.search_web`
- `workspace.rag_search`
- `workspace.read_notes`
- `workspace.write_note`

Phase 2:

- `sandbox.run_python`

Rules:

- do not invent client-specific tool names
- do not create one tool namespace for homepage and another for iPhone
- if a tool returns structured data or a chart, also persist an artifact for it

## Error Contract

Agent endpoints should use the existing HTTP error model:

- `400` invalid request
- `401` unauthenticated
- `403` missing scope or forbidden
- `404` missing resource
- `409` invalid state transition
- `500` unexpected server failure

For run failures, preserve both:

- HTTP success for the resource fetch, and
- in-resource error details on `agent_run.error`

Example:

```json
{
  "run": {
    "run_id": "agrun_500",
    "status": "failed",
    "error": {
      "code": "tool_failed",
      "message": "dataset.attention_research_bundle failed",
      "retryable": true
    }
  }
}
```

## Mobile-Friendly Constraints

- keep summary payloads compact
- return full artifact content only when explicitly requested or small enough
- keep cursor pagination on messages and artifacts
- keep enums stable and documented
- prefer typed blocks over long markdown blobs when the content is structured

This is important for iPhone because it avoids reparsing mixed chat text just to render a chart, note, or table.

## First Implementation Slice

Implement this first:

1. omnibar intent resolver endpoint (`POST /v1/omnibar/resolve`)
2. storage models for `agent_session`, `agent_message`, `agent_run`, `agent_artifact`, `agent_note`
3. REST endpoints for:
- create/get session
- create/list messages
- get run
- get run events
- list/get artifacts
- create/list notes
4. run flow:
- message create -> async run -> tool calls -> artifact creation -> assistant message
5. Phase 1 tools only:
- `dataset.*`
- `chart.*`
- `workspace.search_news`
- `workspace.search_web`

Do not start with sandbox execution.

## Cross-References

- `documents/plans/spectral_nature_2/HOMEPAGE_AGENT_WORKSPACE_PLAN_2026-04-06.md`
- `documents/plans/spectral_nature_2/HOMEPAGE_AGENT_WORKSPACE_PLAN_2026-04-06.md`
- `documents/plans/spectral_nature_2/IPHONE_APP_STRATEGY_2026-04-05.md`
- `documents/plans/spectral_nature_2/IPHONE_MVP_SCAFFOLD_2026-04-06.md`
- `documents/plans/spectral_nature_2/AGENTIC_API_AUTH_MCP_2026-04-07.md`
