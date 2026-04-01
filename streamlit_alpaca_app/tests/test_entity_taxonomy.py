from __future__ import annotations

import pandas as pd
import pytest

from services.attention_home_1d import build_attention_entity_master
from services.entity_taxonomy import (
    build_entity_taxonomy_snapshot,
    business_focus_label_from_taxonomy_row,
    classify_listings_with_llm,
)


def test_build_entity_taxonomy_snapshot_requires_dynamic_classification_for_unclassified_listings():
    listings = pd.DataFrame(
        [
            {"symbol": "AAL", "exchange": "NASDAQ", "security_name": "American Airlines Group Inc.", "is_etf": False, "source_file": "nasdaqlisted"},
            {"symbol": "ZZZZ", "exchange": "NYSE", "security_name": "Example Unknown Corp.", "is_etf": False, "source_file": "otherlisted"},
        ]
    )

    with pytest.raises(RuntimeError, match="requires dynamic classification"):
        build_entity_taxonomy_snapshot(listings, llm_client=None)


def test_build_entity_taxonomy_snapshot_covers_listings_with_llm_and_no_seed():
    class FakeLLM:
        class config:
            model = "gpt-5-mini"
            deployment = ""

        def __init__(self):
            self.calls = 0

        def generate_json(self, **kwargs):
            self.calls += 1
            payload = kwargs["user_prompt"]
            if '"symbol": "AAL"' in payload and '"symbol": "SHOP"' in payload and '"symbol": "ZZZZ"' in payload:
                return {
                    "classifications": [
                        {
                            "symbol": "AAL",
                            "sector": "Industrials",
                            "industry": "Airlines",
                            "commodity_role": "",
                            "rates_role": "",
                            "defensive_role": "",
                            "macro_role_tags": ["travel", "oil_beneficiary"],
                            "business_role_tags": ["travel_mobility"],
                            "confidence": "medium",
                            "notes": "Airline operator.",
                        },
                        {
                            "symbol": "SHOP",
                            "sector": "Information Technology",
                            "industry": "E-Commerce Software",
                            "commodity_role": "",
                            "rates_role": "",
                            "defensive_role": "",
                            "macro_role_tags": ["consumer_software"],
                            "business_role_tags": ["payments_and_commerce", "merchant_software"],
                            "confidence": "high",
                            "notes": "Merchant commerce platform.",
                        },
                        {
                            "symbol": "ZZZZ",
                            "sector": "Industrials",
                            "industry": "Diversified Industrials",
                            "commodity_role": "",
                            "rates_role": "",
                            "defensive_role": "",
                            "macro_role_tags": [],
                            "business_role_tags": ["diversified_industrials"],
                            "confidence": "low",
                            "notes": "Best-fit placeholder from limited listing metadata.",
                        },
                    ]
                }
            raise AssertionError(f"Unexpected prompt payload: {payload}")

    listings = pd.DataFrame(
        [
            {"symbol": "AAL", "exchange": "NASDAQ", "security_name": "American Airlines Group Inc.", "is_etf": False, "source_file": "nasdaqlisted"},
            {"symbol": "SHOP", "exchange": "NYSE", "security_name": "Shopify Inc.", "is_etf": False, "source_file": "otherlisted"},
            {"symbol": "ZZZZ", "exchange": "NYSE", "security_name": "Example Unknown Corp.", "is_etf": False, "source_file": "otherlisted"},
        ]
    )

    snapshot = build_entity_taxonomy_snapshot(listings, llm_client=FakeLLM())

    assert set(snapshot["symbol"]) == {"AAL", "SHOP", "ZZZZ"}

    aal = snapshot[snapshot["symbol"] == "AAL"].iloc[0]
    assert aal["source_of_truth"] == "llm_taxonomy"
    assert aal["sector"] == "Industrials"
    assert aal["industry"] == "Airlines"
    assert aal["business_role_tags"] == ["travel_mobility"]
    assert aal["macro_role_tags"] == ["travel", "oil_beneficiary"]

    shop = snapshot[snapshot["symbol"] == "SHOP"].iloc[0]
    assert shop["source_of_truth"] == "llm_taxonomy"
    assert shop["sector"] == "Information Technology"
    assert shop["industry"] == "E-Commerce Software"
    assert shop["business_role_tags"] == ["payments_and_commerce", "merchant_software"]

    unknown = snapshot[snapshot["symbol"] == "ZZZZ"].iloc[0]
    assert unknown["source_of_truth"] == "llm_taxonomy"
    assert unknown["sector"] == "Industrials"
    assert unknown["industry"] == "Diversified Industrials"
    assert unknown["business_role_tags"] == ["diversified_industrials"]


def test_build_attention_entity_master_prefers_taxonomy_lookup(monkeypatch):
    monkeypatch.setattr(
        "services.entity_taxonomy.taxonomy_lookup_by_symbol",
        lambda symbols: {
            "PANW": {
                "symbol": "PANW",
                "asset_class": "equity",
                "security_type": "common_stock",
                "sector": "Information Technology",
                "industry": "Cybersecurity",
                "country": "US",
                "commodity_role": "",
                "rates_role": "",
                "defensive_role": "",
                "macro_role_tags": [],
                "business_role_tags": ["advertising"],
                "source_of_truth": "llm_taxonomy",
                "override_reason": "LLM taxonomy suggestion.",
            }
        },
    )

    entity_master = build_attention_entity_master(["PANW"])

    row = entity_master.iloc[0]
    assert row["sector"] == "Information Technology"
    assert row["industry"] == "Cybersecurity"
    assert row["source_of_truth"] == "llm_taxonomy"


def test_build_attention_entity_master_keeps_fallback_when_taxonomy_row_is_uninformative(monkeypatch):
    monkeypatch.setattr(
        "services.entity_taxonomy.taxonomy_lookup_by_symbol",
        lambda symbols: {
            "AAL": {
                "symbol": "AAL",
                "asset_class": "equity",
                "security_type": "common_stock",
                "sector": "Unknown",
                "industry": "Unknown",
                "country": "US",
                "commodity_role": "",
                "rates_role": "",
                "defensive_role": "",
                "macro_role_tags": [],
                "business_role_tags": [],
                "source_of_truth": "listing_metadata",
                "override_reason": "Default row only.",
            }
        },
    )

    entity_master = build_attention_entity_master(["AAL"])

    row = entity_master.iloc[0]
    assert row["sector"] == "Unknown"
    assert row["industry"] == "Unknown"
    assert row["source_of_truth"] == "listing_metadata"


def test_business_focus_label_from_taxonomy_row_maps_generated_tags():
    row = {
        "business_role_tags": ["payments_and_commerce"],
        "industry": "E-Commerce Software",
        "sector": "Information Technology",
        "commodity_role": "",
    }

    assert business_focus_label_from_taxonomy_row(row) == "Payments & Commerce"


def test_classify_listings_with_llm_maps_response_into_taxonomy_rows():
    class FakeLLM:
        class config:
            model = "gpt-5-mini"
            deployment = ""

        def generate_json(self, **kwargs):
            return {
                "classifications": [
                    {
                        "symbol": "PANW",
                        "sector": "Information Technology",
                        "industry": "Cybersecurity",
                        "commodity_role": "",
                        "rates_role": "",
                        "defensive_role": "",
                        "macro_role_tags": ["cybersecurity"],
                        "business_role_tags": ["enterprise_security_software"],
                        "confidence": "high",
                        "notes": "Enterprise security software company.",
                    }
                ]
            }

    listings = pd.DataFrame(
        [
            {"symbol": "PANW", "exchange": "NASDAQ", "security_name": "Palo Alto Networks, Inc.", "is_etf": False, "source_file": "nasdaqlisted"},
        ]
    )

    frame = classify_listings_with_llm(listings, FakeLLM(), batch_size=1)

    row = frame.iloc[0]
    assert row["symbol"] == "PANW"
    assert row["sector"] == "Information Technology"
    assert row["industry"] == "Cybersecurity"
    assert row["source_of_truth"] == "llm_taxonomy"
    assert row["label_confidence"] == "high"
    assert row["business_role_tags"] == ["enterprise_security_software"]
    assert row["macro_role_tags"] == ["cybersecurity"]


def test_classify_listings_with_llm_emits_progress_events():
    class FakeLLM:
        class config:
            model = "gpt-5-mini"
            deployment = ""

        def generate_json(self, **kwargs):
            payload = kwargs["user_prompt"]
            if '"symbol": "AAL"' in payload:
                return {
                    "classifications": [
                        {
                            "symbol": "AAL",
                            "sector": "Industrials",
                            "industry": "Airlines",
                            "commodity_role": "",
                            "rates_role": "",
                            "defensive_role": "",
                            "macro_role_tags": ["travel", "oil_beneficiary"],
                            "business_role_tags": ["travel_mobility"],
                            "confidence": "medium",
                            "notes": "Airline operator.",
                        }
                    ]
                }
            if '"symbol": "SHOP"' in payload:
                return {
                    "classifications": [
                        {
                            "symbol": "SHOP",
                            "sector": "Information Technology",
                            "industry": "E-Commerce Software",
                            "commodity_role": "",
                            "rates_role": "",
                            "defensive_role": "",
                            "macro_role_tags": ["consumer_software"],
                            "business_role_tags": ["payments_and_commerce", "merchant_software"],
                            "confidence": "high",
                            "notes": "Commerce software platform.",
                        }
                    ]
                }
            raise AssertionError(f"Unexpected prompt payload: {payload}")

    listings = pd.DataFrame(
        [
            {"symbol": "AAL", "exchange": "NASDAQ", "security_name": "American Airlines Group Inc.", "is_etf": False, "source_file": "nasdaqlisted"},
            {"symbol": "SHOP", "exchange": "NYSE", "security_name": "Shopify Inc.", "is_etf": False, "source_file": "otherlisted"},
        ]
    )
    events: list[dict[str, object]] = []

    frame = classify_listings_with_llm(
        listings,
        FakeLLM(),
        batch_size=1,
        progress_callback=lambda payload: events.append(dict(payload)),
    )

    assert len(frame) == 2
    assert events[0]["event"] == "classify_start"
    assert any(event["event"] == "batch_start" for event in events)
    assert any(event["event"] == "batch_complete" for event in events)
    assert events[-1]["event"] == "classify_complete"


def test_build_entity_taxonomy_snapshot_prefers_informative_rows_over_unknown_existing():
    class FakeLLM:
        class config:
            model = "gpt-5-mini"
            deployment = ""

        def generate_json(self, **kwargs):
            return {
                "classifications": [
                    {
                        "symbol": "AAME",
                        "sector": "Financials",
                        "industry": "Asset Management",
                        "commodity_role": "",
                        "rates_role": "",
                        "defensive_role": "",
                        "macro_role_tags": ["financial_conditions"],
                        "business_role_tags": ["asset_manager"],
                        "confidence": "low",
                        "notes": "Best-fit from listing metadata.",
                    }
                ]
            }

    listings = pd.DataFrame(
        [
            {"symbol": "AAME", "exchange": "NYSE", "security_name": "Atlantic American Corp.", "is_etf": False, "source_file": "otherlisted"},
        ]
    )
    existing = pd.DataFrame(
        [
            {
                "symbol": "AAME",
                "sector": "Unknown",
                "industry": "Unknown",
                "business_role_tags": [],
                "macro_role_tags": [],
                "source_of_truth": "llm_taxonomy",
                "label_provider": "llm",
                "label_confidence": "high",
            }
        ]
    )

    snapshot = build_entity_taxonomy_snapshot(
        listings,
        existing_frame=existing,
        llm_client=FakeLLM(),
        llm_batch_size=1,
    )

    row = snapshot[snapshot["symbol"] == "AAME"].iloc[0]
    assert row["sector"] == "Financials"
    assert row["industry"] == "Asset Management"
    assert row["business_role_tags"] == ["asset_manager"]


def test_build_entity_taxonomy_snapshot_retries_unresolved_symbols_with_smaller_batches():
    class FakeLLM:
        class config:
            model = "gpt-5-mini"
            deployment = ""

        def __init__(self):
            self.repair_prompts: list[str] = []

        def generate_json(self, **kwargs):
            prompt = kwargs["user_prompt"]
            is_repair_pass = "Do not use 'Unknown' for sector or industry in this pass." in kwargs["system_prompt"]
            if not is_repair_pass:
                return {
                    "classifications": [
                        {
                            "symbol": "AAL",
                            "sector": "Unknown",
                            "industry": "Unknown",
                            "commodity_role": "",
                            "rates_role": "",
                            "defensive_role": "",
                            "macro_role_tags": [],
                            "business_role_tags": [],
                            "confidence": "low",
                            "notes": "Insufficient context.",
                        },
                        {
                            "symbol": "SHOP",
                            "sector": "Unknown",
                            "industry": "Unknown",
                            "commodity_role": "",
                            "rates_role": "",
                            "defensive_role": "",
                            "macro_role_tags": [],
                            "business_role_tags": [],
                            "confidence": "low",
                            "notes": "Insufficient context.",
                        },
                    ]
                }

            self.repair_prompts.append(prompt)
            if '"symbol": "AAL"' in prompt and '"symbol": "SHOP"' in prompt:
                return {
                    "classifications": [
                        {
                            "symbol": "AAL",
                            "sector": "Industrials",
                            "industry": "Airlines",
                            "commodity_role": "",
                            "rates_role": "",
                            "defensive_role": "",
                            "macro_role_tags": ["travel"],
                            "business_role_tags": ["travel_mobility"],
                            "confidence": "medium",
                            "notes": "Airline operator.",
                        }
                    ]
                }
            if '"symbol": "SHOP"' in prompt and '"symbol": "AAL"' not in prompt:
                return {
                    "classifications": [
                        {
                            "symbol": "SHOP",
                            "sector": "Information Technology",
                            "industry": "E-Commerce Software",
                            "commodity_role": "",
                            "rates_role": "",
                            "defensive_role": "",
                            "macro_role_tags": ["consumer_software"],
                            "business_role_tags": ["payments_and_commerce"],
                            "confidence": "medium",
                            "notes": "Commerce platform.",
                        }
                    ]
                }
            raise AssertionError(f"Unexpected repair payload: {prompt}")

    listings = pd.DataFrame(
        [
            {"symbol": "AAL", "exchange": "NASDAQ", "security_name": "American Airlines Group Inc.", "is_etf": False, "source_file": "nasdaqlisted"},
            {"symbol": "SHOP", "exchange": "NYSE", "security_name": "Shopify Inc.", "is_etf": False, "source_file": "otherlisted"},
        ]
    )

    fake_llm = FakeLLM()
    snapshot = build_entity_taxonomy_snapshot(
        listings,
        llm_client=fake_llm,
        llm_batch_size=2,
    )

    assert set(snapshot["symbol"]) == {"AAL", "SHOP"}
    assert set(snapshot["sector"]) == {"Industrials", "Information Technology"}
    assert any('"symbol": "AAL"' in prompt and '"symbol": "SHOP"' in prompt for prompt in fake_llm.repair_prompts)
    assert any('"symbol": "SHOP"' in prompt and '"symbol": "AAL"' not in prompt for prompt in fake_llm.repair_prompts)
