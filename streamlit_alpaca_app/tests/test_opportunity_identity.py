from services import robinhood_main_analytics as analytics


def test_empty_selection_never_expands_to_retired_resources(monkeypatch):
    monkeypatch.setattr(analytics.robinhood_main_store, "get_resource", lambda **_: {"payload": {"sets": [{"set_key": "current"}]}})
    def unexpected(**_):
        raise AssertionError("An empty selection must not read the unfiltered resource store")
    monkeypatch.setattr(analytics.robinhood_main_store, "list_resources", unexpected)
    result = analytics.opportunity_analytics(portfolio_id="p", environment="development", lookback="1Y", group_keys=[])
    assert result["series"].empty
    assert result["groups"] == []


def test_retired_keys_are_not_part_of_current_analytics(monkeypatch):
    monkeypatch.setattr(analytics.robinhood_main_store, "get_resource", lambda **_: {"payload": {"sets": [{"set_key": "current"}]}})
    requested = []
    def resources(**kwargs):
        requested.extend(kwargs["resource_keys"])
        return []
    monkeypatch.setattr(analytics.robinhood_main_store, "list_resources", resources)
    monkeypatch.setattr(analytics.robinhood_main_store, "get_series_points", lambda **_: [])
    result = analytics.opportunity_analytics(portfolio_id="p", environment="development", lookback="1Y", group_keys=["current", "retired"])
    assert requested == ["current"]
    assert result["rankings"].empty
