from __future__ import annotations

from pathlib import Path

import pandas as pd


APP_ROOT = Path(__file__).resolve().parents[1]

SCAN_ROOTS = [
    APP_ROOT / "app.py",
    APP_ROOT / "services",
    APP_ROOT / "pipeline",
]

LOAD_CLIENT_ALLOWED = {
    "services/llm.py",
    "services/zopedia_runtime.py",
}

LOAD_ZOPEDIA_CLIENT_ALLOWED = {
    "services/aql_zopedia_engine.py",
    "services/zopedia_runtime.py",
}

RUN_OMNIBAR_AGENT_ALLOWED = {
    "services/aql_zopedia_engine.py",
    "services/omnibar_agent.py",
}

GENERATE_JSON_ALLOWED_EXACT = {
    "services/aql_zopedia_engine.py",
    "services/attention_context_llm.py",
    "services/attention_live_research.py",
    "services/common/hypothesis.py",
    "services/company.py",
    "services/entity_extraction.py",
    "services/entity_taxonomy.py",
    "services/knowledge_graph.py",
    "services/omnibar_agent.py",
    "services/omnibar_research.py",
    "services/page_agentic_summary.py",
    "services/saa/zopedia.py",
    "services/trading_agent.py",
    "services/zopedia_learning.py",
}

GENERATE_JSON_ALLOWED_PREFIXES = (
    "services/aql/",
)


def _source_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        if root.is_file():
            files.append(root)
            continue
        files.extend(path for path in root.rglob("*.py") if path.is_file())
    return sorted(files)


def _rel(path: Path) -> str:
    return path.relative_to(APP_ROOT).as_posix()


def test_llm_client_loading_only_happens_at_zopedia_runtime_boundary():
    offenders: list[str] = []
    for path in _source_files():
        rel = _rel(path)
        text = path.read_text(encoding="utf-8")
        if "load_llm_client" not in text:
            continue
        if rel not in LOAD_CLIENT_ALLOWED:
            offenders.append(rel)

    assert offenders == []


def test_product_llm_loading_enters_aql_zopedia_engine_boundary():
    offenders: list[str] = []
    for path in _source_files():
        rel = _rel(path)
        text = path.read_text(encoding="utf-8")
        if "load_zopedia_llm_client" not in text:
            continue
        if rel not in LOAD_ZOPEDIA_CLIENT_ALLOWED:
            offenders.append(rel)

    assert offenders == []


def test_omnibar_agent_loop_is_only_called_by_shared_engine():
    offenders: list[str] = []
    for path in _source_files():
        rel = _rel(path)
        text = path.read_text(encoding="utf-8")
        if "run_omnibar_agent(" not in text:
            continue
        if rel not in RUN_OMNIBAR_AGENT_ALLOWED:
            offenders.append(rel)

    assert offenders == []


def test_attention_home_job_uses_engine_summary_boundary():
    path = APP_ROOT / "pipeline/jobs/attention_home_build.py"
    text = path.read_text(encoding="utf-8")

    assert "build_aql_zopedia_attention_home_summary_with_trace" in text
    assert "from services.attention_home_summary import (\n" not in text


def test_attention_home_facade_uses_engine_summary_boundary():
    path = APP_ROOT / "services/attention_home_summary.py"
    text = path.read_text(encoding="utf-8")

    assert "build_aql_zopedia_attention_home_summary_with_trace" in text
    assert "from .aql import (\n    apply_display_limits," in text
    assert "build_attention_agentic_summary_with_trace," not in text


def test_aql_package_facade_uses_engine_summary_boundary():
    path = APP_ROOT / "services/aql/__init__.py"
    text = path.read_text(encoding="utf-8")

    assert "build_aql_zopedia_attention_home_summary_with_trace" in text
    assert "from .summarizer import (\n    apply_display_limits," in text
    assert "build_attention_agentic_summary_with_trace," not in text


def test_attention_home_facade_calls_engine_runtime(monkeypatch):
    from services import attention_home_summary

    calls: list[dict[str, object]] = []

    def _fake_engine(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return {"status": "ok", "source": "engine"}, {"aql_zopedia_engine_runs": pd.DataFrame()}

    monkeypatch.setattr(
        attention_home_summary,
        "build_aql_zopedia_attention_home_summary_with_trace",
        _fake_engine,
    )

    summary, trace = attention_home_summary.build_attention_agentic_summary_with_trace(
        {"top_events": []},
        llm_client=object(),
    )
    compact = attention_home_summary.build_attention_agentic_summary(
        {"top_events": []},
        llm_client=object(),
    )

    assert summary["source"] == "engine"
    assert compact["source"] == "engine"
    assert "aql_zopedia_engine_runs" in trace
    assert len(calls) == 2


def test_aql_package_facade_calls_engine_runtime(monkeypatch):
    import services.aql as aql

    calls: list[dict[str, object]] = []

    def _fake_engine(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return {"status": "ok", "source": "engine"}, {"aql_zopedia_engine_runs": pd.DataFrame()}

    monkeypatch.setattr(aql, "build_aql_zopedia_attention_home_summary_with_trace", _fake_engine)

    summary, trace = aql.build_attention_agentic_summary_with_trace({"top_events": []}, llm_client=object())
    compact = aql.build_attention_agentic_summary({"top_events": []}, llm_client=object())

    assert summary["source"] == "engine"
    assert compact["source"] == "engine"
    assert "aql_zopedia_engine_runs" in trace
    assert len(calls) == 2


def test_page_and_trading_default_runners_call_engine(monkeypatch):
    from services import aql_zopedia_engine, page_agentic_summary, trading_agent

    calls: list[dict[str, object]] = []

    def _fake_agent(**kwargs):
        calls.append(dict(kwargs))
        return {"status": "completed", "answer_markdown": "ok"}

    monkeypatch.setattr(aql_zopedia_engine, "run_aql_zopedia_agent", _fake_agent)

    page_agentic_summary._default_aql_agent_runner(query="page query")
    trading_agent._default_aql_agent_runner(query="trading query")

    assert calls[0]["task"] == "page_summary"
    assert calls[0]["surface"] == "page_agentic_summary"
    assert calls[1]["task"] == "trading_agent"
    assert calls[1]["surface"] == "trading_agent"


def test_generate_json_call_sites_are_reviewed_zopedia_native_surfaces():
    offenders: list[str] = []
    for path in _source_files():
        rel = _rel(path)
        text = path.read_text(encoding="utf-8")
        if ".generate_json(" not in text:
            continue
        allowed = rel in GENERATE_JSON_ALLOWED_EXACT or any(
            rel.startswith(prefix) for prefix in GENERATE_JSON_ALLOWED_PREFIXES
        )
        if not allowed:
            offenders.append(rel)

    assert offenders == []
