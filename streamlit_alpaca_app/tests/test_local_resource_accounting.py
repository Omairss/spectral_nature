"""Billing/admission contracts, not financial-answer quality tests."""
import json
from types import SimpleNamespace
import pytest
from services.llm_execution import LocalResearchExecution, ResearchSpendExceeded


def local(root, limit=4):
    return LocalResearchExecution(root, overrides={}, budget_usd=0,
        input_usd_per_million=0, output_usd_per_million=0,
        cost_basis="local_resources_unpriced", max_provider_attempts=limit)


def test_local_missing_usage_survives_reload_and_keeps_attempt_limit(tmp_path):
    run = local(tmp_path, 2)
    run.validate_transport(SimpleNamespace(provider="local", base_url="http://127.0.0.1:8765/v1"))
    run.reserve("cancelled", {})
    run.record("provider_attempts", {"provider":"local", "attempt_id":"cancelled", "status":"started"})
    run.record("provider_attempts", {"provider":"local", "attempt_id":"cancelled", "status":"failure", "usage":None})
    run = local(tmp_path, 2)
    assert run.summary()["unknown_usage_attempts"] == ["cancelled"]
    assert run.summary()["external_provider_charge_usd"] == 0
    assert run.summary()["local_resource_cost_usd"] is None
    run.reserve("orphan", {})
    run.record("provider_attempts", {"provider":"local", "attempt_id":"orphan", "status":"started"})
    run = local(tmp_path, 2)
    with pytest.raises(ResearchSpendExceeded, match="attempt limit"):
        run.reserve("third", {})
    assert set(run.summary()["unknown_usage_attempts"]) == {"cancelled", "orphan"}


def test_paid_missing_usage_still_blocks_and_cannot_be_relabelled(tmp_path):
    run = LocalResearchExecution(tmp_path, overrides={}, budget_usd=5,
        input_usd_per_million=0.3, output_usd_per_million=1.2)
    run.record("provider_attempts", {"provider":"deepseek", "attempt_id":"paid", "status":"failure", "usage":None})
    with pytest.raises(ResearchSpendExceeded, match="charge is unknown"):
        run.reserve("next", {})
    with pytest.raises(ValueError, match="reinterpret"):
        local(tmp_path)


def test_local_binding_rejects_external_transport_and_accounting_change(tmp_path):
    run = local(tmp_path)
    with pytest.raises(ValueError, match="loopback"):
        run.validate_transport(SimpleNamespace(provider="local", base_url="https://example.com/v1"))
    with pytest.raises(ValueError, match="loopback"):
        run.validate_transport(SimpleNamespace(provider="deepseek", base_url="http://127.0.0.1/v1"))
    with pytest.raises(ValueError, match="basis cannot change"):
        LocalResearchExecution(tmp_path, overrides={}, budget_usd=5,
            input_usd_per_million=0.3, output_usd_per_million=1.2)


def test_legacy_hosted_or_unidentified_receipts_cannot_become_local(tmp_path):
    (tmp_path/"provider_attempts.jsonl").write_text(json.dumps(
        {"attempt_id":"legacy", "status":"started"})+"\n")
    with pytest.raises(ValueError, match="reinterpret"):
        local(tmp_path)
