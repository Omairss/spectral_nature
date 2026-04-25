# services/attention/ — attention layer services package.
#
# This package provides a clean import namespace for the attention_*
# modules that live under services/. New code should import from
# services.attention instead of services.attention_*.
#
# The underlying modules remain at their original paths for now.
# This package re-exports their public APIs so callers can use either:
#   from services.attention import build_attention_market_events
#   from services.attention_market_events import build_attention_market_events
#
# Submodule mapping:
#   services.attention.agentic       -> services.attention_agentic (shim -> services.aql)
#   services.attention.context_llm   -> services.attention_context_llm
#   services.attention.feed_brief    -> services.attention_feed_brief
#   services.attention.graph_network -> services.attention_graph_network
#   services.attention.graph_topology -> services.attention_graph_topology
#   services.attention.home_1d       -> services.attention_home_1d
#   services.attention.home_summary  -> services.attention_home_summary (shim -> services.aql)
#   services.attention.live_research -> services.attention_live_research
#   services.attention.market_events -> services.attention_market_events
#   services.attention.materialized  -> services.attention_materialized
#   services.attention.surface       -> services.attention_surface
#   services.attention.ticker_snapshots -> services.attention_ticker_snapshots

# Re-export submodules so `from services.attention import agentic` works
from services import attention_agentic as agentic  # noqa: F401
from services import attention_context_llm as context_llm  # noqa: F401
from services import attention_feed_brief as feed_brief  # noqa: F401
from services import attention_graph_network as graph_network  # noqa: F401
from services import attention_graph_topology as graph_topology  # noqa: F401
from services import attention_home_1d as home_1d  # noqa: F401
from services import attention_home_summary as home_summary  # noqa: F401
from services import attention_live_research as live_research  # noqa: F401
from services import attention_market_events as market_events  # noqa: F401
from services import attention_materialized as materialized  # noqa: F401
from services import attention_surface as surface  # noqa: F401
from services import attention_ticker_snapshots as ticker_snapshots  # noqa: F401
