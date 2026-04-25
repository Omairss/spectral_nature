# Agent Architecture

This folder holds durable architecture for agentic research, graph experiments, and export surfaces used by agents and external clients.

Boundary rule: agents own planning, tool routing, conversation state, and chat/search workflows. Business logic belongs behind tools or public module APIs, not inside the agent loop.

## Docs

- `AGENTIC_SUMMARY_2026-04-13.md`: market-wide homepage summary, search, and hypothesis synthesis
- `AGENTIC_TICKER_BACKGROUND_TAVILY_2026-04-03.md`: ticker background enrichment and Tavily integration notes
- `DEPENDENCY_GRAPH_INTEGRATION_2026-04-10.md`: JSON-backed dependency graph feature plan
- `OPEN_KNOWLEDGE_GRAPH_EXPERIMENT_2026-04-12.md`: admin-only open-ended graph generation experiment
- `RESEARCH_EXPORT_API_2026-04-18.md`: unified research export endpoint and folder structure
