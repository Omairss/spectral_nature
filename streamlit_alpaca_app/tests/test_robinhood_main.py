from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import uuid

from cryptography.fernet import Fernet
import pandas as pd
import pytest

from services import brokerage_credentials, robinhood_main


USER_ID = "11111111-1111-1111-1111-111111111111"
OTHER_USER_ID = "22222222-2222-2222-2222-222222222222"
PORTFOLIO_ID = "33333333-3333-3333-3333-333333333333"
CONNECTION_ID = "44444444-4444-4444-4444-444444444444"
TICKET_ID = "55555555-5555-5555-5555-555555555555"
REF_ID = "66666666-6666-4666-8666-666666666666"
POSITION_KEY = "a" * 64


@pytest.fixture(autouse=True)
def _main_account_environment(monkeypatch):
    monkeypatch.setenv("APP_TRACK", "test")
    monkeypatch.setenv("ROBINHOOD_MAIN_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("ROBINHOOD_MCP_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    brokerage_credentials.connection_cipher.cache_clear()
    robinhood_main._ensure_store.cache_clear()
    yield
    brokerage_credentials.connection_cipher.cache_clear()
    cache_clear = getattr(robinhood_main._ensure_store, "cache_clear", None)
    if cache_clear is not None:
        cache_clear()


def test_direct_authentication_uses_fresh_device_token_and_returns_tokens(monkeypatch):
    captured: dict[str, object] = {}

    def _request(_self, method, path, **kwargs):
        captured.update({"method": method, "path": path, **kwargs})
        return {
            "access_token": "access-plain",
            "refresh_token": "refresh-plain",
            "token_type": "Bearer",
            "expires_in": 86400,
        }

    monkeypatch.setattr(robinhood_main.RobinhoodAppClient, "request", _request)

    result = robinhood_main.start_authentication(username="owner@example.com", password="password")

    assert result["status"] == "authenticated"
    assert result["access_token"] == "access-plain"
    assert captured["method"] == "POST"
    assert captured["path"] == robinhood_main.LOGIN_URL
    assert captured["data"]["username"] == "owner@example.com"
    assert captured["data"]["password"] == "password"
    assert captured["data"]["device_token"] == result["device_token"]
    assert "username" not in result
    assert "password" not in result


def test_transport_sets_content_type_from_each_payload():
    captured: list[dict[str, object]] = []

    class _Response:
        status_code = 200
        content = b"{}"

        @staticmethod
        def json():
            return {}

    client = robinhood_main.RobinhoodAppClient()

    def _request(method, url, **kwargs):
        captured.append({"method": method, "url": url, **kwargs})
        return _Response()

    client.session.request = _request
    client.request("POST", "/oauth2/token/", data={"grant_type": "password"})
    client.request("POST", "/pathfinder/user_machine/", json_payload={"flow": "suv"})

    assert captured[0]["headers"] == {
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"
    }
    assert captured[1]["headers"] == {"Content-Type": "application/json"}
    assert "Content-Type" not in client.session.headers


def test_transport_rejects_mixed_form_and_json_payloads():
    client = robinhood_main.RobinhoodAppClient()

    with pytest.raises(robinhood_main.RobinhoodMainRequestError, match="both form and JSON"):
        client.request("POST", "/pathfinder/user_machine/", data={"a": 1}, json_payload={"b": 2})


def test_current_stop_order_contract_is_json_version_7_and_closes(monkeypatch):
    captured: dict[str, object] = {}
    client = robinhood_main.RobinhoodAppClient(access_token="token")
    monkeypatch.setattr(client, "request", lambda *args, **kwargs: captured.update(kwargs) or {"id": "order-1"})

    client.place_stop_loss(
        account={"account_number": "123456789", "url": "https://api.robinhood.com/accounts/a/"},
        position={"instrument": "https://api.robinhood.com/instruments/a/"},
        quote={"bid_price": "149.90", "ask_price": "150.10"},
        order={"symbol": "AAPL", "quantity": "2", "stop_price": "135"},
        ref_id=REF_ID,
    )

    assert "data" not in captured
    payload = captured["json_payload"]
    assert payload["order_form_version"] == 7
    assert payload["position_effect"] == "close"
    assert payload["side"] == "sell"
    assert payload["trigger"] == "stop"


def test_json_rows_replaces_non_finite_values():
    rows = robinhood_main._json_rows(
        pd.DataFrame([{"symbol": "AAPL", "value": float("nan"), "other": float("inf")}])
    )

    assert rows == [{"symbol": "AAPL", "value": None, "other": None}]


def test_short_basket_requires_whole_shares():
    with pytest.raises(robinhood_main.RobinhoodMainPolicyError, match="whole number"):
        robinhood_main._canonical_equity_orders(
            [
                {
                    "account_key": POSITION_KEY,
                    "symbol": "AAPL",
                    "side": "sell_short",
                    "quantity": 1.5,
                }
            ]
        )


def test_provider_error_preserves_status_and_sanitized_drf_message():
    class _Response:
        status_code = 400
        content = b"present"

        @staticmethod
        def json():
            return {
                "non_field_errors": ["Device verification was rejected."],
                "username": "must-not-be-shown",
            }

    client = robinhood_main.RobinhoodAppClient()
    client.session.request = lambda *args, **kwargs: _Response()

    payload = client.request(
        "POST",
        robinhood_main.LOGIN_URL,
        data={"username": "owner@example.com", "password": "secret"},
        allow_error_payload=True,
    )
    error = robinhood_main._authentication_error("login", payload)

    assert payload["_http_status"] == 400
    assert str(error) == "Robinhood login failed (HTTP 400): Device verification was rejected."
    assert "owner@example.com" not in str(error)
    assert "must-not-be-shown" not in str(error)


def test_unknown_provider_error_exposes_only_safe_field_names():
    message = robinhood_main._safe_provider_message(
        {"username": "owner@example.com", "password": "secret", "challenge": {"state": "failed"}}
    )

    assert message == "Robinhood rejected fields: challenge."
    assert "owner@example.com" not in message


def test_challenge_state_accepts_nested_type_context(monkeypatch):
    client = robinhood_main.RobinhoodAppClient()
    monkeypatch.setattr(
        client,
        "request",
        lambda *args, **kwargs: {
            "type_context": {
                "context": {
                    "sheriff_challenge": {
                        "id": "challenge-1",
                        "type": "prompt",
                        "status": "issued",
                    }
                }
            }
        },
    )

    assert robinhood_main._challenge_state(client, "machine-1") == {
        "challenge_id": "challenge-1",
        "challenge_type": "prompt",
        "challenge_status": "issued",
    }


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"context": {"result": "workflow_status_approved"}}, "workflow_status_approved"),
        ({"type_context": {"result": "workflow_status_approved"}}, "workflow_status_approved"),
        (
            {"verification_workflow": {"workflow_status": "workflow_status_internal_pending"}},
            "workflow_status_internal_pending",
        ),
    ],
)
def test_workflow_result_accepts_current_provider_shapes(payload, expected):
    assert robinhood_main._workflow_result(payload) == expected


def test_save_authentication_encrypts_tokens_and_persists_only_masked_accounts(monkeypatch):
    captured: dict[str, object] = {}

    class _Client:
        def __init__(self, **kwargs):
            assert kwargs["access_token"] == "access-plain"

        def accounts(self):
            return [
                {"account_number": "123456789", "type": "individual"},
                {"account_number": "987654321", "type": "roth_ira"},
            ]

    monkeypatch.setattr(robinhood_main, "RobinhoodAppClient", _Client)
    monkeypatch.setattr(
        robinhood_main.brokerage_mcp_store,
        "save_direct_connection",
        lambda **kwargs: captured.update(kwargs) or {"id": CONNECTION_ID},
    )

    robinhood_main.save_authentication(
        user_id=USER_ID,
        authenticated={
            "status": "authenticated",
            "access_token": "access-plain",
            "refresh_token": "refresh-plain",
            "device_token": "device-plain",
            "token_type": "Bearer",
            "scope": "internal",
            "expires_in": 172800,
        },
    )

    assert brokerage_credentials.decrypt_secret(captured["access_token_ciphertext"]) == "access-plain"
    assert brokerage_credentials.decrypt_secret(captured["refresh_token_ciphertext"]) == "refresh-plain"
    assert brokerage_credentials.decrypt_secret(captured["device_token_ciphertext"]) == "device-plain"
    persisted_info = json.dumps(captured["server_info"], sort_keys=True, ensure_ascii=False)
    assert "123456789" not in persisted_info
    assert "987654321" not in persisted_info
    assert "••••6789" in persisted_info
    assert "••••4321" in persisted_info
    assert captured["token_expires_at"] <= datetime.now(timezone.utc) + timedelta(days=1, seconds=2)


class _SnapshotClient:
    def accounts(self):
        return [
            {
                "account_number": "100000001",
                "type": "individual",
                "portfolio_cash": "100",
                "buying_power": "200",
                "day_trade_count": 1,
            },
            {
                "account_number": "200000002",
                "type": "roth_ira",
                "portfolio_cash": "50",
                "buying_power": "75",
                "day_trade_count": 0,
            },
        ]

    def portfolio(self, account_number):
        return {"equity": "1000" if account_number == "100000001" else "2000"}

    def portfolio_performance(self, account_number, span="all"):
        multiplier = 1 if account_number == "100000001" else 2
        return {
            "performance_baseline": {"amount": f"${1000 * multiplier:,.2f}"},
            "lines": [
                {
                    "identifier": "returns",
                    "segments": [
                        {
                            "points": [
                                {
                                    "y": 0.0,
                                    "cursor_data": {
                                        "label": {"value": "2026-09-28T00:00:00Z"},
                                        "primary_value": {"value": f"${1000 * multiplier:,.2f}"},
                                    },
                                },
                                {
                                    "y": 0.01,
                                    "cursor_data": {
                                        "label": {"value": "2026-09-29T00:00:00Z"},
                                        "primary_value": {"value": f"${1010 * multiplier:,.2f}"},
                                    },
                                },
                                {
                                    "y": 0.02,
                                    "cursor_data": {
                                        "label": {"value": "2026-09-30T00:00:00Z"},
                                        "primary_value": {"value": f"${1020 * multiplier:,.2f}"},
                                    },
                                },
                            ]
                        }
                    ],
                }
            ],
        }

    def positions(self, account_number):
        return [
            {
                "instrument": f"https://api.robinhood.com/instruments/{account_number}/",
                "quantity": "2" if account_number == "100000001" else "1",
                "shares_held_for_sells": "0",
                "average_buy_price": "100" if account_number == "100000001" else "120",
            }
        ]

    def instrument(self, _url):
        return {"symbol": "AAPL"}

    def quotes(self, _symbols):
        return {"AAPL": {"last_trade_price": "150", "previous_close": "145"}}

    def benchmark_history(self):
        return [
            {"symbol": "SPY", "begins_at": "2026-09-28T00:00:00Z", "close_price": "600"},
            {"symbol": "SPY", "begins_at": "2026-09-29T00:00:00Z", "close_price": "606"},
            {"symbol": "SPY", "begins_at": "2026-09-30T00:00:00Z", "close_price": "612"},
        ]

    def option_positions(self, _account_number):
        return []

    def option_market_data(self, _option_ids):
        return {}

    def futures_accounts(self):
        return []

    def watchlists(self):
        return []

    def popular_watchlists(self):
        return []

    def saved_screeners(self):
        return []


def test_snapshot_aggregates_all_main_accounts_without_persisting_account_numbers():
    summary, positions, timeseries, details = robinhood_main._normalized_snapshot(
        _SnapshotClient(),
        {"id": CONNECTION_ID},
    )

    assert summary["status"] == "active"
    assert summary["equity"] == 3000.0
    assert summary["cash"] == 150.0
    assert summary["portfolio_value"] == 3000.0
    assert summary["buying_power"] == 275.0
    assert summary["daytrade_count"] == 1
    assert summary["account_count"] == 2
    assert summary["equity_position_count"] == 2
    assert positions.loc[0, "symbol"] == "AAPL"
    assert positions.loc[0, "qty"] == 3.0
    assert positions.loc[0, "market_value"] == 450.0
    assert list(timeseries["portfolio"]) == [3000.0, 3030.0, 3060.0]
    assert list(timeseries["SPY"]) == [600.0, 606.0, 612.0]
    persisted = json.dumps(
        {
            "summary": summary,
            "positions": robinhood_main._json_rows(positions),
            "timeseries": robinhood_main._json_rows(timeseries),
            "details": details,
        },
        default=str,
    )
    assert "100000001" not in persisted
    assert "200000002" not in persisted


def test_performance_summary_falls_back_to_curve_start_when_baseline_is_missing():
    class _Client:
        def portfolio_performance(self, _account_number, span="all"):
            return {
                "lines": [
                    {
                        "identifier": "returns",
                        "segments": [
                            {
                                "points": [
                                    {
                                        "y": 0.0,
                                        "cursor_data": {
                                            "label": {"value": "2026-09-28T00:00:00Z"},
                                            "primary_value": {"value": "$500.00"},
                                        },
                                    },
                                    {
                                        "y": 0.1,
                                        "cursor_data": {
                                            "label": {"value": "2026-09-29T00:00:00Z"},
                                            "primary_value": {"value": "$1,100.00"},
                                        },
                                    },
                                ]
                            }
                        ],
                    }
                ]
            }

    history, summaries = robinhood_main._collect_performance(
        _Client(),
        [{"account_number": "123456789", "type": "margin"}],
        {},
    )

    assert list(history["portfolio"]) == [500.0, 1100.0]
    assert summaries[0]["baseline"] == pytest.approx(1000.0)
    assert summaries[0]["return_amount"] == pytest.approx(100.0)
    assert summaries[0]["return_pct"] == pytest.approx(10.0)


def test_option_collector_signs_value_pnl_and_greeks_by_leg_direction():
    class _Client:
        def option_positions(self, _account_number):
            return [
                {
                    "symbol": "AAPL",
                    "strategy": "short_call",
                    "quantity": "2",
                    "average_open_price": "500",
                    "legs": [
                        {
                            "option_id": "option-1",
                            "position_type": "short",
                            "option_type": "call",
                            "strike_price": "150",
                            "expiration_date": "2026-12-18",
                            "ratio_quantity": "1",
                        }
                    ],
                }
            ]

        def option_market_data(self, _option_ids):
            return {
                "option-1": {
                    "mark_price": "2",
                    "previous_close_price": "2.5",
                    "delta": "0.4",
                    "gamma": "0.02",
                    "theta": "-0.03",
                    "vega": "0.08",
                    "rho": "0.01",
                    "implied_volatility": "0.35",
                    "open_interest": "100",
                    "volume": "10",
                }
            }

    rows = robinhood_main._collect_options(
        _Client(),
        CONNECTION_ID,
        [{"account_number": "123456789", "type": "margin"}],
        {},
    )

    assert rows[0]["market_value"] == -400.0
    assert rows[0]["cost_basis"] == -1000.0
    assert rows[0]["unrealized_pl"] == 600.0
    assert rows[0]["daily_pl"] == 100.0
    assert rows[0]["delta"] == -80.0


def test_futures_collector_joins_cost_basis_quotes_and_multiplier():
    class _Client:
        def futures_accounts(self):
            return [{"id": "futures-account", "rhsAccountNumber": "123456789"}]

        def futures_positions(self, _account_id):
            return [
                {
                    "contractId": "contract-1",
                    "quantity": "1",
                    "side": "long",
                    "contract": {
                        "id": "contract-1",
                        "symbol": "MESZ6",
                        "description": "Micro E-mini S&P",
                        "multiplier": "5",
                    },
                }
            ]

        def futures_cost_basis(self, _account_id):
            return {
                "contract-1": {
                    "averagePrice": {"amount": "6000"},
                    "unrealizedPnl": {"amount": "500"},
                    "realizedPnl": {"amount": "50"},
                }
            }

        def futures_market_data(self, _contract_ids):
            return {
                "contract-1": {
                    "last_trade_price": "6100",
                    "previous_close_price": "6050",
                    "symbol": "/MESZ6:XCME",
                }
            }

        def futures_contract(self, _symbol):
            return {
                "displaySymbol": "/MESZ6:XCME",
                "description": "Micro E-mini S&P",
                "expiration": "2026-12-18",
                "multiplier": "5",
            }

    rows = robinhood_main._collect_futures(
        _Client(),
        CONNECTION_ID,
        [{"account_number": "123456789", "type": "margin"}],
        {},
    )

    assert rows[0]["symbol"] == "MESZ6"
    assert rows[0]["multiplier"] == 5.0
    assert rows[0]["market_value"] == 30500.0
    assert rows[0]["daily_pl"] == 250.0
    assert rows[0]["unrealized_pl"] == 500.0
    assert rows[0]["realized_pl"] == 50.0


def test_futures_accounts_excludes_non_futures_ceres_accounts(monkeypatch):
    client = robinhood_main.RobinhoodAppClient(access_token="token")
    monkeypatch.setattr(
        client,
        "request",
        lambda *args, **kwargs: {
            "results": [
                {"id": "event-account", "accountType": "PREDICTION_MARKET"},
                {"id": "futures-account", "accountType": "FUTURES"},
            ]
        },
    )

    assert client.futures_accounts() == [
        {"id": "futures-account", "accountType": "FUTURES"}
    ]


def test_futures_collector_maps_live_ceres_cost_basis_shape():
    class _Client:
        def futures_accounts(self):
            return [{"id": "futures-account", "rhsAccountNumber": "123456789"}]

        def futures_positions(self, _account_id):
            return [
                {
                    "contractId": "contract-1",
                    "quantity": "2",
                    "avgTradePrice": "91.61",
                }
            ]

        def futures_cost_basis(self, _account_id):
            return {
                "contract-1": {
                    "avgTradePrice": "91.61",
                    "dayOpenPnlCostBasis": {"amount": "18574", "currency": "USD"},
                    "dayPnlCostBasis": {"amount": "18574", "currency": "USD"},
                    "openPnlCostBasis": {"amount": "18322", "currency": "USD"},
                    "signedQuantity": "2",
                }
            }

        def futures_market_data(self, _contract_ids):
            return {
                "contract-1": {
                    "instrument_id": "contract-1",
                    "symbol": "/MCLX26:XNYM",
                    "bid_price": "91.21",
                    "ask_price": "91.40",
                    "last_trade_price": "91.27",
                }
            }

        def futures_contract(self, _symbol):
            return {
                "displaySymbol": "/MCLX26:XNYM",
                "description": "Micro WTI Crude Oil",
                "expiration": "2026-10-20",
                "multiplier": "100",
            }

    errors: dict[str, str] = {}
    rows = robinhood_main._collect_futures(
        _Client(),
        CONNECTION_ID,
        [{"account_number": "123456789", "type": "margin"}],
        errors,
    )

    assert errors == {}
    assert rows[0]["symbol"] == "MCLX26"
    assert rows[0]["quantity"] == 2.0
    assert rows[0]["average_open_price"] == 91.61
    assert rows[0]["current_price"] == pytest.approx(91.305)
    assert rows[0]["multiplier"] == 100.0
    assert rows[0]["cost_basis"] == 18322.0
    assert rows[0]["market_value"] == pytest.approx(18261.0)
    assert rows[0]["unrealized_pl"] == pytest.approx(-61.0)
    assert rows[0]["daily_pl"] == pytest.approx(-313.0)
    assert rows[0]["realized_pl"] is None


def test_stop_loss_service_rejects_a_connection_owned_by_another_admin(monkeypatch):
    monkeypatch.setattr(robinhood_main, "_ensure_store", lambda: True)
    monkeypatch.setattr(
        robinhood_main.brokerage_mcp_store,
        "get_portfolio_brokerage",
        lambda **kwargs: {"brokerage_source": robinhood_main.PROVIDER, "brokerage_account_ref": CONNECTION_ID},
    )
    monkeypatch.setattr(
        robinhood_main.brokerage_mcp_store,
        "get_connection_by_id",
        lambda **kwargs: {"id": CONNECTION_ID, "user_id": OTHER_USER_ID},
    )

    with pytest.raises(robinhood_main.RobinhoodMainAuthorizationError, match="another administrator"):
        robinhood_main.stop_loss_positions(user_id=USER_ID, portfolio_id=PORTFOLIO_ID)


def test_trade_authority_requires_an_active_admin_membership(monkeypatch):
    monkeypatch.setattr(robinhood_main, "_ensure_store", lambda: True)
    monkeypatch.setattr(
        robinhood_main.brokerage_mcp_store,
        "get_portfolio_brokerage",
        lambda **kwargs: {
            "brokerage_source": robinhood_main.PROVIDER,
            "brokerage_account_ref": CONNECTION_ID,
        },
    )
    monkeypatch.setattr(
        robinhood_main.brokerage_mcp_store,
        "get_connection_by_id",
        lambda **kwargs: {"id": CONNECTION_ID, "user_id": USER_ID},
    )
    monkeypatch.setattr(
        robinhood_main.auth_store,
        "user_has_active_portfolio_admin_membership",
        lambda **kwargs: False,
    )

    with pytest.raises(robinhood_main.RobinhoodMainAuthorizationError, match="Administrator access"):
        robinhood_main._owned_portfolio_connection(
            user_id=USER_ID,
            portfolio_id=PORTFOLIO_ID,
        )


def test_cached_portfolio_read_allows_an_active_member_without_trade_authority(monkeypatch):
    monkeypatch.setattr(robinhood_main, "_ensure_store", lambda: True)
    monkeypatch.setattr(
        robinhood_main.auth_store,
        "user_has_active_portfolio_membership",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        robinhood_main.brokerage_mcp_store,
        "get_portfolio_brokerage",
        lambda **kwargs: {
            "brokerage_source": robinhood_main.PROVIDER,
            "brokerage_account_ref": CONNECTION_ID,
        },
    )
    monkeypatch.setattr(
        robinhood_main.brokerage_mcp_store,
        "get_connection_by_id",
        lambda **kwargs: {"id": CONNECTION_ID, "user_id": OTHER_USER_ID},
    )

    connection = robinhood_main._portfolio_read_connection(
        user_id=USER_ID,
        portfolio_id=PORTFOLIO_ID,
    )

    assert connection["user_id"] == OTHER_USER_ID


def test_cached_portfolio_read_rejects_a_nonmember(monkeypatch):
    monkeypatch.setattr(robinhood_main, "_ensure_store", lambda: True)
    monkeypatch.setattr(
        robinhood_main.auth_store,
        "user_has_active_portfolio_membership",
        lambda **kwargs: False,
    )

    with pytest.raises(robinhood_main.RobinhoodMainAuthorizationError, match="not available"):
        robinhood_main._portfolio_read_connection(
            user_id=USER_ID,
            portfolio_id=PORTFOLIO_ID,
        )


def _resolved_order() -> dict[str, object]:
    return {
        "order": {
            "position_key": POSITION_KEY,
            "symbol": "AAPL",
            "quantity": "2",
            "stop_price": "135",
        },
        "position": {
            "account": {"account_number": "123456789", "url": "https://api.robinhood.com/accounts/a/"},
            "account_label": "individual ••••6789",
            "instrument": "https://api.robinhood.com/instruments/a/",
            "quote": {"last_trade_price": "150", "ask_price": "150.10", "bid_price": "149.90"},
            "current_price": robinhood_main.Decimal("150"),
        },
    }


def test_review_creates_one_exact_payload_bound_batch_ticket(monkeypatch):
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        robinhood_main,
        "_owned_portfolio_connection",
        lambda **kwargs: {"id": CONNECTION_ID, "user_id": USER_ID},
    )
    monkeypatch.setattr(
        robinhood_main,
        "_resolve_live_batch",
        lambda connection, orders: (object(), [_resolved_order()]),
    )
    monkeypatch.setattr(
        robinhood_main.brokerage_mcp_store,
        "create_order_ticket",
        lambda **kwargs: captured.update(kwargs) or {"id": TICKET_ID, "expires_at": kwargs["expires_at"]},
    )
    orders = [{"position_key": POSITION_KEY, "symbol": "aapl", "quantity": 2, "stop_price": 135}]

    review = robinhood_main.review_batch_stop_losses(
        user_id=USER_ID,
        portfolio_id=PORTFOLIO_ID,
        orders=orders,
    )

    assert review["ticket_id"] == TICKET_ID
    assert review["orders"] == [
        {
            "account": "individual ••••6789",
            "symbol": "AAPL",
            "quantity": "2",
            "stop_price": "135",
            "current_price": "150",
        }
    ]
    assert captured["arguments_hash"] == robinhood_main._arguments_hash(review["canonical_orders"])
    assert captured["request_summary"]["order_count"] == 1
    assert "123456789" not in json.dumps(captured["request_summary"])


def test_confirmation_consumes_ticket_and_places_each_order_with_deterministic_ref(monkeypatch):
    placed: list[dict[str, object]] = []
    finished_tickets: list[dict[str, object]] = []

    class _Client:
        def place_stop_loss(self, **kwargs):
            placed.append(kwargs)
            return {"id": "order-1", "state": "queued"}

    canonical = [
        {
            "position_key": POSITION_KEY,
            "symbol": "AAPL",
            "quantity": "2",
            "stop_price": "135",
        }
    ]
    monkeypatch.setattr(
        robinhood_main.brokerage_mcp_store,
        "consume_order_ticket",
        lambda **kwargs: {
            "id": TICKET_ID,
            "connection_id": CONNECTION_ID,
            "ref_id": REF_ID,
            "request_summary": {"portfolio_id": PORTFOLIO_ID},
        },
    )
    monkeypatch.setattr(robinhood_main, "_require_portfolio_admin", lambda **kwargs: None)
    monkeypatch.setattr(
        robinhood_main.brokerage_mcp_store,
        "get_connection_by_id",
        lambda **kwargs: {"id": CONNECTION_ID, "user_id": USER_ID},
    )
    monkeypatch.setattr(
        robinhood_main,
        "_resolve_live_batch",
        lambda connection, orders: (_Client(), [_resolved_order()]),
    )
    monkeypatch.setattr(robinhood_main.brokerage_mcp_store, "start_audit_event", lambda **kwargs: "audit-1")
    monkeypatch.setattr(robinhood_main.brokerage_mcp_store, "finish_audit_event", lambda **kwargs: None)
    monkeypatch.setattr(
        robinhood_main.brokerage_mcp_store,
        "finish_order_ticket",
        lambda **kwargs: finished_tickets.append(kwargs),
    )

    result = robinhood_main.confirm_batch_stop_losses(
        user_id=USER_ID,
        ticket_id=TICKET_ID,
        orders=canonical,
    )

    assert result["status"] == "executed"
    assert result["submitted"] == 1
    assert placed[0]["ref_id"] == str(uuid.uuid5(uuid.UUID(REF_ID), POSITION_KEY))
    assert finished_tickets[-1]["status"] == "executed"


def test_confirmation_fails_closed_when_review_changed_expired_or_used(monkeypatch):
    monkeypatch.setattr(
        robinhood_main.brokerage_mcp_store,
        "consume_order_ticket",
        lambda **kwargs: None,
    )

    with pytest.raises(robinhood_main.RobinhoodMainPolicyError, match="expired, changed, or was already used"):
        robinhood_main.confirm_batch_stop_losses(
            user_id=USER_ID,
            ticket_id=TICKET_ID,
            orders=[
                {
                    "position_key": POSITION_KEY,
                    "symbol": "AAPL",
                    "quantity": "3",
                    "stop_price": "135",
                }
            ],
        )


def test_disconnect_unlinks_without_reassigning_to_another_broker(monkeypatch):
    captured: dict[str, object] = {}
    monkeypatch.setattr(robinhood_main, "_require_portfolio_admin", lambda **kwargs: None)
    monkeypatch.setattr(robinhood_main, "_connection_for_user", lambda _user_id: {"id": CONNECTION_ID})
    monkeypatch.setattr(
        robinhood_main.brokerage_mcp_store,
        "disconnect_connection",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        robinhood_main.brokerage_mcp_store,
        "get_portfolio_brokerage",
        lambda **kwargs: {
            "brokerage_source": robinhood_main.PROVIDER,
            "brokerage_account_ref": CONNECTION_ID,
        },
    )
    monkeypatch.setattr(
        robinhood_main.brokerage_mcp_store,
        "set_portfolio_brokerage",
        lambda **kwargs: captured.update(kwargs) or kwargs,
    )

    assert robinhood_main.disconnect(user_id=USER_ID, portfolio_id=PORTFOLIO_ID)
    assert captured == {
        "portfolio_id": PORTFOLIO_ID,
        "brokerage_source": "unlinked",
        "brokerage_account_ref": None,
    }
