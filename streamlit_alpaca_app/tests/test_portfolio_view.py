from types import SimpleNamespace

import pandas as pd
import pytest

from services import portfolio_view, robinhood_main


@pytest.fixture
def context():
    return SimpleNamespace(
        user_id="user", portfolio_id="portfolio", portfolio_name="Portfolio",
        share_fraction=1.0, can_view_full_portfolio=True, is_admin=True,
    )


@pytest.mark.parametrize("state", ["waiting", "in_progress", "failed", "unavailable", "ready"])
def test_linked_portfolio_without_snapshot_never_reads_another_broker(monkeypatch, context, state):
    monkeypatch.setattr(robinhood_main, "portfolio_data_status", lambda **_: {"state": state})
    monkeypatch.setattr(robinhood_main, "portfolio_snapshot", lambda **_: None)
    with pytest.raises(robinhood_main.RobinhoodMainError):
        portfolio_view.build_portfolio_view(context, service=object())


def test_missing_environment_connection_does_not_fall_back(monkeypatch, context):
    def unavailable(**_):
        raise robinhood_main.RobinhoodMainAuthorizationError("Connection unavailable")
    monkeypatch.setattr(robinhood_main, "portfolio_data_status", unavailable)
    with pytest.raises(robinhood_main.RobinhoodMainAuthorizationError):
        portfolio_view.build_portfolio_view(context, service=object())


def test_unlinked_portfolio_can_use_legacy_broker(monkeypatch, context):
    monkeypatch.setattr(robinhood_main, "portfolio_data_status", lambda **_: {"state": "unlinked"})
    access = SimpleNamespace(
        resolve_account=lambda **_: SimpleNamespace(payload={"equity": 42}),
        resolve_positions=lambda **_: SimpleNamespace(payload=pd.DataFrame()),
        resolve_portfolio_timeseries=lambda *_, **__: SimpleNamespace(payload=pd.DataFrame()),
    )
    result = portfolio_view.build_portfolio_view(context, service=SimpleNamespace(data_access=access))
    assert result["provider"] == "alpaca"
    assert result["summary"] == {"equity": 42}
    assert result["period"] == "5Y"


def test_linked_snapshot_keeps_its_provider(monkeypatch, context):
    monkeypatch.setattr(robinhood_main, "portfolio_data_status", lambda **_: {"state": "ready"})
    monkeypatch.setattr(robinhood_main, "portfolio_snapshot", lambda **_: {"account_summary": {"equity": 84}})
    result = portfolio_view.build_portfolio_view(context, service=object())
    assert result["provider"] == "robinhood_main"
    assert result["summary"] == {"equity": 84}
