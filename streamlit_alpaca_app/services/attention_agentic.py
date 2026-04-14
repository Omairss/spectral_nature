# Thin shim — all code lives in services/aql/
from .runtime_policy import attention_graph_policy
from .aql import (
    AgenticAttentionArtifacts,
    build_bottom_up_attention_artifacts,
    build_bottom_up_attention_bundle,
    build_bottom_up_attention_home,
    recompute_attention_candidate_graph,
    search_symbol_news_payload,
)
# Private functions accessed directly by tests
from .aql._agentic import (
    _augment_candidate_frame,
    _candidate_context_documents,
    _chunk_source_documents,
    _documents_from_search_results,
    _fallback_claims_from_chunks,
    _fallback_event_writer,
    _graph_edges,
    _history_correlation_map,
    _load_search_clients,
    _search_query_results,
    _write_event_bundle,
)

__all__ = [
    "AgenticAttentionArtifacts",
    "build_bottom_up_attention_artifacts",
    "build_bottom_up_attention_bundle",
    "build_bottom_up_attention_home",
    "recompute_attention_candidate_graph",
    "search_symbol_news_payload",
]


# Patch-propagating module class (supports monkeypatching via tests)
import sys as _sys
import types as _types


class _PatchPropagatingModule(_types.ModuleType):
    """Module subclass that propagates attribute patches to sub-modules."""

    _PATCH_TARGETS: dict[str, str] = {
        "_load_search_clients": "services.aql.config",
    }

    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        target_module_name = self._PATCH_TARGETS.get(name)
        if target_module_name and target_module_name in _sys.modules:
            setattr(_sys.modules[target_module_name], name, value)


# Replace this module in sys.modules with the patch-propagating version
_current = _sys.modules[__name__]
_patched_module = _PatchPropagatingModule(__name__, __doc__)
_patched_module.__dict__.update({k: v for k, v in _current.__dict__.items() if k not in ("_patched_module",)})
_sys.modules[__name__] = _patched_module
