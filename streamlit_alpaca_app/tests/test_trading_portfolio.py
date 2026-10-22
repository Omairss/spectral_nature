"""Numerical/account boundaries; these checks do not evaluate research quality."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from compute.paper_sizing import RiskPolicy, recommend_position, risk_after_quantity
from services import alpaca_paper, pipeline_store, trading_call_tracking as tracking


def history(scale=.02):
    rng = np.random.default_rng(423)
    x = rng.normal(0, scale, 300)
    return pd.DataFrame({"A": x, "B": x * .99 + rng.normal(0, .0001, 300)},
                        index=pd.bdate_range("2025-01-01", periods=300).strftime("%Y-%m-%d"))


def size(returns=None, weights=None, **kw):
    args = dict(returns=history() if returns is None else returns,
                weights=weights or {}, symbol="A", stance="long",
                equity=100000, buying_power=400000, cash=100000, price=100,
                policy=RiskPolicy())
    args.update(kw)
    return recommend_position(**args)


def test_volatility_budget_has_correct_dollar_units_and_no_alpha_assumptions():
    result = size(returns=history()[["A"]])
    sigma = np.std(history().A.tail(252), ddof=0) * np.sqrt(252)
    assert result["asset_volatility_pct"] == pytest.approx(sigma * 100)
    assert result["suggested_notional"] == pytest.approx(100000 * .02 / sigma)
    assert result["portfolio_volatility_after_pct"] == pytest.approx(2)
    assert "expected_return" not in result and "win_probability" not in result


def test_higher_volatility_reduces_capital_for_the_same_risk_budget():
    low = size(returns=history(.02)[["A"]])
    high = size(returns=history(.04)[["A"]])
    assert high["suggested_notional"] == pytest.approx(low["suggested_notional"] / 2)


def test_review_risk_matches_exact_shares_after_rounding_or_price_reserve():
    result = size()
    half = risk_after_quantity(result, str(result['suggested_quantity'] / 2))
    assert half['portfolio_volatility_after_pct'] == pytest.approx(result['portfolio_volatility_after_pct'] / 2)
    assert half['suggested_notional'] == pytest.approx(result['suggested_notional'] / 2)


def test_correlated_existing_holdings_use_up_portfolio_risk_capacity():
    policy = replace(RiskPolicy(), position_volatility_pct=10, portfolio_volatility_pct=10, max_position_pct=100)
    empty = size(policy=policy)
    correlated = size(weights={"B": .2}, policy=policy)
    assert correlated["suggested_notional"] < empty["suggested_notional"]
    assert correlated["portfolio_volatility_after_pct"] <= 10 + 1e-8


def test_same_risk_budget_caps_single_name_and_preserves_cash():
    small = size(cash=500)
    assert small["suggested_notional"] == pytest.approx(500)
    assert small["binding_limit"] == "funding"
    capped = size(policy=replace(RiskPolicy(), max_position_pct=1))
    assert capped["suggested_notional"] == pytest.approx(1000)


def test_existing_target_is_not_repeated_for_every_new_call():
    first = size()
    w = first["target_weight_pct"] / 100
    with pytest.raises(ValueError, match="already meets"):
        size(weights={"A": w})
    incremental = size(weights={"A": w / 2})
    assert incremental["suggested_notional"] == pytest.approx(first["suggested_notional"] / 2)


def test_opposite_thesis_cannot_silently_close_broker_holding():
    with pytest.raises(ValueError, match="opposite position"):
        size(weights={"A": -.01})


def test_short_uses_signed_covariance_and_exposure():
    result = size(stance="short", weights={"B": .05})
    assert result["target_weight_pct"] < 0
    assert result["portfolio_volatility_after_pct"] < result["asset_volatility_pct"]


def test_missing_or_insufficient_history_never_becomes_zero_risk():
    with pytest.raises(ValueError, match="missing"):
        size(returns=history()[["B"]])
    with pytest.raises(ValueError, match="126"):
        size(returns=history().tail(100))
    with pytest.raises(ValueError, match="126"):
        size(returns=history().assign(A=np.nan))
    with pytest.raises(ValueError):
        size(policy=RiskPolicy(position_volatility_pct=float("nan")))


def test_broker_projection_does_not_return_credentials_or_invent_holdings():
    result = alpaca_paper.project({"account_summary": {"equity": "100000", "cash": "100000",
        "last_equity": "100000", "id": "private", "api_key": "private"},
        "positions": [], "timeseries": [{"date": "2026-10-21", "equity": 100000, "return_pct": 0}],
        "server_info": {"secret_secret_name": "private"}, "details": {"coverage": {"account": "2026-10-22"}}})
    assert result["summary"]["cash"] == 100000
    assert result["summary"]["equity"] == 100000
    assert result["holdings"] == []
    assert result["return_pct"] == 0
    assert "private" not in str(result)


def test_prefunding_history_is_trimmed_but_later_loss_to_zero_is_retained():
    result = alpaca_paper.project({'timeseries': [
        {'date': 'before', 'equity': 0}, {'date': 'funding', 'equity': 100000},
        {'date': 'loss', 'equity': 0, 'return_pct': -100}]})
    assert [point['date'] for point in result['path']] == ['funding', 'loss']
    assert result['max_drawdown_pct'] == -100
    negative = alpaca_paper.project({'timeseries': [{'date': 'negative', 'equity': -100}]})
    assert negative['path'][0]['equity'] == -100
    assert negative['max_drawdown_pct'] is None


def test_refresh_failure_keeps_last_good_component_and_timestamp(monkeypatch):
    context = SimpleNamespace(user_id="owner", portfolio_id="portfolio")
    link = {"id": "link", "server_info": {"account_id": "account"}}
    old = {"account_summary": {}, "positions": [{"symbol": "OLD", "qty": "2"}],
           "timeseries": [], "details": {"coverage": {"positions": "old-observation"}, "orders": []}}
    monkeypatch.setattr(alpaca_paper, "connection", lambda _: link)
    monkeypatch.setattr(alpaca_paper, "_snapshot", lambda *_: old)
    class API:
        def paper_request(self, method, path, **kw):
            if path == "/v2/account": return {"id": "account", "equity": "100000", "cash": "100000"}
            if path == "/v2/positions": raise TimeoutError()
            if path == "/v2/orders": return []
            return {"timestamp": [], "equity": [], "profit_loss": [], "profit_loss_pct": []}
    monkeypatch.setattr(alpaca_paper, "api_for_connection", lambda _: API())
    monkeypatch.setattr(alpaca_paper.store, "save_portfolio_snapshot", lambda **kw: kw)
    result = alpaca_paper.refresh(context)
    assert result["status"] == "partial"
    assert result["holdings"][0]["symbol"] == "OLD"
    assert result["coverage"]["positions"] == "old-observation"
    assert result["unavailable"] == ["positions"]


def test_research_failure_does_not_hide_broker_account(monkeypatch):
    from services import trading_agent_view as view
    monkeypatch.setattr(view, "tracking_view", lambda **kw: {"status": "unavailable", "calls": [], "as_of": ""})
    monkeypatch.setattr(view.alpaca_paper, "view", lambda _: {"status": "available", "summary": {"cash": "100000"}})
    monkeypatch.setattr(view, "_latest_output", lambda: (pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), None))
    result = view.build_trading_agent_view(SimpleNamespace(user_id="owner"))
    assert result["status"] == "unavailable"
    assert result["paper_portfolio"]["summary"]["cash"] == "100000"


def test_per_call_history_remains_owned_and_lazy(monkeypatch):
    owned = {"tracking_id": "owned", "candidate_id": "c", "ticker": "A",
             "stance": "long", "entry_policy": tracking.ENTRY_POLICY, "candidate_payload": {}}
    rows = [{"tracking_id": "owned", "owner_id": "me", "path_json": "{"}]
    monkeypatch.setattr(tracking, "approved_calls", lambda **kw: [owned])
    monkeypatch.setattr(pipeline_store, "latest_dataset_metadata", lambda *_: SimpleNamespace(asof_time_utc="2026-10-22"))
    monkeypatch.setattr(pipeline_store, "load_dataset_frame", lambda *a, **kw: pd.DataFrame(rows))
    result = tracking.tracking_view(owner_id="me")
    assert "path" not in result["calls"][0]
    assert tracking.tracking_view(owner_id="me", tracking_id="owned")["calls"][0]["path_status"] == "unavailable"
    rows[0]["owner_id"] = "other"
    with pytest.raises(tracking.TrackingUnavailable):
        tracking.tracking_view(owner_id="me")


def confirmation_setup(monkeypatch):
    from contextlib import nullcontext
    context = SimpleNamespace(user_id='owner', portfolio_id='portfolio', email='qa@example.invalid')
    link = {'id': 'link', 'server_info': {'account_id': 'account'}}
    ticket = {'connection_id': 'link', 'arguments_hash': 'a' * 64, 'status': 'reviewed',
        'request_summary': {'portfolio_id': 'portfolio', 'account_id': 'account', 'candidate': {'candidate_id': 'call'},
            'order': {'qty': '10', 'client_order_id': 'stable-client-id'},
            'risk': {'price': 100, 'policy': {}}}}
    calls, terminals = [], []
    class Cursor:
        def execute(self, *a): pass
        def fetchone(self): return (True,)
        def __enter__(self): return self
        def __exit__(self, *a): pass
    class Lock:
        def cursor(self): return Cursor()
        def __enter__(self): return self
        def __exit__(self, *a): pass
    class API:
        from services.alpaca_api import AlpacaAPI as _Adapter
        validate_order_receipt = staticmethod(_Adapter.validate_order_receipt)
        def paper_request(self, method, path, **kw):
            calls.append((method, path))
            if method == 'POST': raise TimeoutError()
            if path == '/v2/account': return {'id': 'account'}
            return {'id': 'broker-order', 'client_order_id': 'stable-client-id', 'qty': '10', 'status': 'accepted'}
    monkeypatch.setattr(alpaca_paper, 'connection', lambda _: link)
    monkeypatch.setattr(alpaca_paper, '_admin', lambda _: None)
    monkeypatch.setattr(alpaca_paper, 'api_for_connection', lambda _: API())
    monkeypatch.setattr(alpaca_paper.auth_store, 'open_auth_store_connection', lambda: Lock())
    monkeypatch.setattr(alpaca_paper.store, 'get_order_ticket', lambda **kw: ticket)
    monkeypatch.setattr(alpaca_paper.store, 'consume_order_ticket', lambda **kw: ticket if ticket['status'] == 'reviewed' else None)
    def finish(**kw):
        terminals.append(kw)
        ticket['status'] = kw['status']
    monkeypatch.setattr(alpaca_paper.store, 'finish_order_ticket', finish)
    monkeypatch.setattr(alpaca_paper, '_order_inputs', lambda *a: ({'qty': '9.9'}, {**size(), 'price': 100, 'capacity_quantity': '10'}))
    monkeypatch.setattr(pipeline_store, 'record_trading_agent_action', lambda **kw: (True, 'recorded'))
    return context, ticket, calls, terminals


def test_ambiguous_submission_reconciles_same_identity_without_second_post(monkeypatch):
    context, ticket, calls, terminals = confirmation_setup(monkeypatch)
    with pytest.raises(alpaca_paper.PaperAccountError, match='unknown'):
        alpaca_paper.confirm_order(context, ticket_id='ticket', arguments_hash='a' * 64)
    assert ticket['status'] == 'unknown'
    result = alpaca_paper.confirm_order(context, ticket_id='ticket', arguments_hash='a' * 64)
    assert result['order']['id'] == 'broker-order'
    assert ticket['status'] == 'executed'
    assert [method for method, _ in calls].count('POST') == 1


def test_failed_revalidation_never_submits_or_claims_ambiguous_execution(monkeypatch):
    context, ticket, calls, terminals = confirmation_setup(monkeypatch)
    monkeypatch.setattr(alpaca_paper, '_order_inputs', lambda *a: (_ for _ in ()).throw(alpaca_paper.PaperAccountError('Market is closed.')))
    with pytest.raises(alpaca_paper.PaperAccountError, match='closed'):
        alpaca_paper.confirm_order(context, ticket_id='ticket', arguments_hash='a' * 64)
    assert calls == []
    assert ticket['status'] == 'failed'


def test_smaller_current_risk_capacity_invalidates_reviewed_quantity(monkeypatch):
    context, ticket, calls, terminals = confirmation_setup(monkeypatch)
    monkeypatch.setattr(alpaca_paper, '_order_inputs', lambda *a: ({'qty': '8.9'}, {'price': 100, 'capacity_quantity': '9'}))
    with pytest.raises(alpaca_paper.PaperAccountError, match='exceeds'):
        alpaca_paper.confirm_order(context, ticket_id='ticket', arguments_hash='a' * 64)
    assert calls == []


def test_expired_and_foreign_review_cannot_submit(monkeypatch):
    context, ticket, calls, terminals = confirmation_setup(monkeypatch)
    monkeypatch.setattr(alpaca_paper.store, 'consume_order_ticket', lambda **kw: None)
    with pytest.raises(ValueError, match='expired'):
        alpaca_paper.confirm_order(context, ticket_id='ticket', arguments_hash='a' * 64)
    ticket['request_summary']['portfolio_id'] = 'other'
    with pytest.raises(PermissionError):
        alpaca_paper.confirm_order(context, ticket_id='ticket', arguments_hash='a' * 64)
    assert calls == []


def test_reconnection_invalidates_old_tickets_and_cached_account_data(monkeypatch):
    context, ticket, calls, terminals = confirmation_setup(monkeypatch)
    ticket['request_summary']['account_id'] = 'previous-account'
    with pytest.raises(ValueError, match='account changed'):
        alpaca_paper.confirm_order(context, ticket_id='ticket', arguments_hash='a' * 64)
    assert calls == []
    monkeypatch.setattr(alpaca_paper.store, 'get_portfolio_snapshot', lambda **kw: {'details': {'account_id': 'previous-account'}})
    assert alpaca_paper._snapshot(context, {'id': 'link', 'server_info': {'account_id': 'account'}}) is None


def test_paper_transport_rejects_live_endpoint_and_never_retries_submission(monkeypatch):
    from services.alpaca_api import AlpacaAPI, AlpacaAPIError
    from services.config import AppConfig
    import requests
    invocations = []
    class Response:
        status_code, text, headers = 500, 'unavailable', {}
        def json(self): return {}
    monkeypatch.setattr(requests, 'request', lambda **kw: (invocations.append(kw) or Response()))
    with pytest.raises(AlpacaAPIError, match='paper account'):
        AlpacaAPI(AppConfig('test', 'test', 'https://api.alpaca.markets')).paper_request('POST', '/v2/orders', body={})
    assert invocations == []
    with pytest.raises(AlpacaAPIError):
        AlpacaAPI(AppConfig('test', 'test')).paper_request('POST', '/v2/orders', body={})
    assert len(invocations) == 1


def test_receipt_must_match_reviewed_identity_and_exact_quantity():
    from services.alpaca_api import AlpacaAPI, AlpacaAPIError
    expected = {'symbol': 'A', 'qty': '10', 'side': 'buy', 'client_order_id': 'review-id'}
    receipt = {**expected, 'qty': '10.000000', 'id': 'broker-id', 'status': 'accepted'}
    assert AlpacaAPI.validate_order_receipt(receipt, expected)['id'] == 'broker-id'
    for changes in [{'qty': '11'}, {'symbol': 'B'}, {'client_order_id': 'other'}, {'id': None}]:
        with pytest.raises(AlpacaAPIError):
            AlpacaAPI.validate_order_receipt({**receipt, **changes}, expected)
