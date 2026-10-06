from __future__ import annotations

import pandas as pd

from compute.fred import FRED_SERIES_SPECS
from services.pipeline_store import SOURCE_DATASETS
from services.signal_catalog import CATALOG_COLUMNS, build_signal_catalog, signal_specs


def test_signal_catalog_has_unique_stable_ids_and_required_contract_fields():
    catalog = build_signal_catalog()

    assert list(catalog.columns) == CATALOG_COLUMNS
    assert catalog["signal_id"].is_unique
    assert catalog["signal_id"].notna().all()
    assert set(catalog["ranking_role"]) >= {
        "direct",
        "conditioning",
        "quality_gate",
        "existing_composite",
        "research_only",
        "source_input",
        "outcome_label",
    }
    known_datasets = {dataset for datasets in SOURCE_DATASETS.values() for dataset in datasets}
    assert set(catalog["source_dataset"]).issubset(known_datasets)
    assert catalog["description"].astype(str).str.strip().ne("").all()


def test_signal_catalog_expands_every_fred_series_across_summary_and_dense_transforms():
    specs = signal_specs()
    fred = [spec for spec in specs if spec.signal_id.startswith("fred.")]

    assert len(fred) == len(FRED_SERIES_SPECS) * 17
    assert {spec.source_entity for spec in fred} == {spec.series_id for spec in FRED_SERIES_SPECS}
    assert any(spec.signal_id == "fred.CPIAUCSL.yoy_pct" for spec in fred)
    assert any(spec.signal_id == "fred.T10Y2Y.regime" for spec in fred)


def test_signal_catalog_marks_outcomes_as_post_decision_and_options_as_not_broad_rank_inputs():
    catalog = build_signal_catalog().set_index("signal_id")

    outcome = catalog.loc["trading_agent_outcomes.is_win"]
    assert outcome["ranking_role"] == "outcome_label"
    assert outcome["time_safety"] == "post_decision_prohibited"

    option = catalog.loc["option_contract_snapshots.impliedVolatility"]
    assert option["ranking_role"] == "source_input"
    assert option["coverage_scope"] == "top_liquid_symbols_only"


def test_signal_catalog_computes_field_and_dynamic_entity_coverage_from_frames():
    frames = {
        "momentum_profiles": pd.DataFrame(
            [
                {"symbol": "A", "return_1m_pct": 4.0},
                {"symbol": "B", "return_1m_pct": pd.NA},
            ]
        ),
        "fred_summary": pd.DataFrame(
            [
                {"series_id": "CPIAUCSL", "latest_value": 100.0, "yoy_pct": 2.5},
                {"series_id": "UNRATE", "latest_value": 4.2, "yoy_pct": pd.NA},
            ]
        ),
    }
    catalog = build_signal_catalog(frames=frames).set_index("signal_id")

    momentum = catalog.loc["momentum_profiles.return_1m_pct"]
    assert momentum["availability"] == "available"
    assert momentum["non_null_count"] == 1
    assert momentum["coverage_ratio"] == 0.5

    cpi = catalog.loc["fred.CPIAUCSL.yoy_pct"]
    assert cpi["availability"] == "available"
    assert cpi["non_null_count"] == 1
    assert cpi["coverage_ratio"] == 1.0

    missing = catalog.loc["technical_signals_latest.rsi_14"]
    assert missing["availability"] == "defined_not_observed"
