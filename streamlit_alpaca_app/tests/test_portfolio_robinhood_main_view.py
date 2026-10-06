from __future__ import annotations

import pandas as pd
from streamlit.testing.v1 import AppTest

from views import portfolio as view


def test_contributor_sankey_uses_best_and_worst_group_returns_not_flow_size():
    rows: list[dict[str, object]] = []
    for group_index in range(7):
        for member_index in range(10):
            contribution = (7 - group_index) * (member_index + 1) / 1000
            rows.append(
                {
                    "group_key": f"group-{group_index}",
                    "group": f"Group {group_index}",
                    "symbol": f"W{group_index}{member_index}",
                    "direction": "Winners",
                    "contribution": contribution,
                }
            )
            rows.append(
                {
                    "group_key": f"group-{group_index}",
                    "group": f"Group {group_index}",
                    "symbol": f"D{group_index}{member_index}",
                    "direction": "Detractors",
                    "contribution": -contribution,
                }
            )

    performance = pd.DataFrame(
        {
            "group_key": [f"group-{index}" for index in range(7)],
            "return": [-0.40, -0.20, -0.10, 0.0, 0.10, 0.20, 0.40],
        }
    )
    figure = view._contributor_sankey(
        pd.DataFrame(rows),
        performance,
        groups_per_tail=2,
    )

    assert figure is not None
    labels = list(figure.data[0].node.label)
    group_labels = [
        label
        for label in labels
        if label.startswith("Best · Group ") or label.startswith("Worst · Group ")
    ]
    assert len(group_labels) == 4
    assert len(labels) <= 4 * (1 + 2 + 6)
    assert {label.split(" · ")[1] for label in group_labels} == {
        "Group 0",
        "Group 1",
        "Group 5",
        "Group 6",
    }
    assert "Best · Group 6 · +40.0%" in labels
    assert "Worst · Group 0 · -40.0%" in labels
    assert not any(" · Group 2 · " in label for label in labels)


def test_initial_robinhood_refresh_renders_in_progress_instead_of_alpaca_fallback():
    app = AppTest.from_string(
        """
from types import SimpleNamespace
from views import portfolio as view

view._render_main_account_connection = lambda current_user: None
view.robinhood_main.portfolio_snapshot = lambda **kwargs: None
view.robinhood_main.portfolio_data_status = lambda **kwargs: {
    "state": "in_progress",
    "snapshot_ready": False,
    "refresh_status": "in_progress",
}
view.dashboard_loaders._load_account_cached = lambda *args, **kwargs: (_ for _ in ()).throw(
    AssertionError("Alpaca fallback must not run for a linked Robinhood portfolio")
)
user = SimpleNamespace(
    user_id="11111111-1111-1111-1111-111111111111",
    portfolio_id="33333333-3333-3333-3333-333333333333",
    is_admin=True,
    can_view_full_portfolio=True,
)
metric = SimpleNamespace(metric=lambda *args, **kwargs: None)
view._render_portfolio_section(
    None,
    None,
    force_data_refresh=False,
    current_user=user,
    sidebar_status=metric,
    sidebar_buying_power=metric,
)
"""
    )

    app.run()

    assert not app.exception
    assert [item.value for item in app.info] == ["Robinhood portfolio refresh · In progress"]


def test_batch_stop_loss_view_has_one_review_and_one_place_action():
    app = AppTest.from_string(
        """
from types import SimpleNamespace
from views import portfolio as view

view.robinhood_main.connection_status = lambda **kwargs: {
    "status": "connected",
    "linked": True,
    "execution_enabled": True,
}
view.robinhood_main.stop_loss_positions = lambda **kwargs: [
    {
        "position_key": "a" * 64,
        "account": "individual ••••6789",
        "symbol": "AAPL",
        "quantity": 2.0,
        "current_price": 150.0,
    },
]
view.robinhood_main.review_batch_stop_losses = lambda **kwargs: {
    "ticket_id": "55555555-5555-5555-5555-555555555555",
    "orders": [
        {
            "account": "individual ••••6789",
            "symbol": "AAPL",
            "quantity": "2",
            "stop_price": "135",
            "current_price": "150",
        }
    ],
    "canonical_orders": [
        {
            "position_key": "a" * 64,
            "symbol": "AAPL",
            "quantity": "2",
            "stop_price": "135",
        }
    ],
}

view._render_batch_stop_losses(
    SimpleNamespace(
        is_admin=True,
        user_id="11111111-1111-1111-1111-111111111111",
        portfolio_id="33333333-3333-3333-3333-333333333333",
    )
)
"""
    )

    app.run()
    assert not app.exception
    assert [button.label for button in app.button] == ["Load positions"]

    app.button[0].click().run()
    assert not app.exception
    assert [button.label for button in app.button] == ["Load positions", "Review 1 stop orders"]

    app.button[1].click().run()
    assert not app.exception
    labels = [button.label for button in app.button]
    assert labels.count("Place 1 stop orders") == 1
    assert labels.count("Cancel") == 1


def test_main_account_holdings_and_performance_render_all_asset_classes():
    app = AppTest.from_string(
        """
from types import SimpleNamespace
import pandas as pd
from views import portfolio as view

view.robinhood_main.option_leg_history = lambda **kwargs: []

snapshot = {
    "details": {
        "holdings": [
            {"asset_class": "equity", "account": "margin ••••7145", "symbol": "AAPL", "direction": "long", "qty": 2.0, "available_quantity": 2.0, "avg_entry_price": 100.0, "current_price": 150.0, "market_value": 300.0, "daily_pl": 4.0, "unrealized_pl": 100.0, "unrealized_plpc": 0.5, "change_today": 0.01},
            {"asset_class": "option", "account": "margin ••••7145", "symbol": "AAPL", "description": "150 Call", "strategy": "long call", "direction": "long", "quantity": 1.0, "expirations": ["2026-12-18"], "average_open_price": 500.0, "market_value": 650.0, "daily_pl": 25.0, "unrealized_pl": 150.0, "unrealized_plpc": 0.3, "implied_volatility": 0.35, "open_interest": 1200, "volume": 200, "delta": 55.0, "gamma": 3.0, "theta": -4.0, "vega": 9.0, "rho": 1.0, "legs": [{"leg_key": "b" * 64, "expiration": "2026-12-18", "strike": 150.0, "option_type": "call", "side": "long"}]},
            {"asset_class": "future", "account": "margin ••••9387", "symbol": "MESZ6", "description": "Micro E-mini S&P", "direction": "long", "quantity": 1.0, "expiration": "2026-12-18", "average_open_price": 6000.0, "current_price": 6100.0, "market_value": 6100.0, "unrealized_pl": 100.0, "unrealized_plpc": 0.0167},
        ]
    },
    "timeseries": pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-09-28", "2026-09-29", "2026-09-30"], utc=True),
        "portfolio": [6000.0, 6200.0, 6300.0],
        "SPY": [600.0, 603.0, 606.0],
    }),
}
user = SimpleNamespace(user_id="11111111-1111-1111-1111-111111111111", portfolio_id="33333333-3333-3333-3333-333333333333")
view.robinhood_main_analytics.equity_position_analytics = lambda **kwargs: {
    "positions": pd.DataFrame(), "correlation": pd.DataFrame(),
    "components": pd.DataFrame(), "stats": {},
}
view._render_main_holdings(user, snapshot, "1Y")
view._render_main_performance(snapshot, "1Y")
"""
    )

    app.run()

    assert not app.exception
    assert [tab.label for tab in app.tabs[:3]] == ["Equities (1)", "Options (1)", "Futures (1)"]
    assert len(app.get("plotly_chart")) >= 5


def test_member_group_view_is_strictly_read_only():
    app = AppTest.from_string(
        """
from types import SimpleNamespace
import pandas as pd
from views import portfolio as view

view.robinhood_main_analytics.opportunity_analytics = lambda **kwargs: {
    "series": pd.DataFrame(), "rankings": pd.DataFrame(),
    "contributions": pd.DataFrame(), "correlation": pd.DataFrame(),
}
view.robinhood_main.opportunity_set_snapshot = lambda **kwargs: {
    "kind": "watchlist",
    "members": [
        {"symbol": "AAPL", "current_price": 200.0, "change_today": 0.01, "period_return": 0.12}
    ],
}
snapshot = {
    "details": {
        "opportunity_sets": [
            {"set_key": "group-a", "name": "Growth", "kind": "watchlist", "analytics_status": "ready"}
        ]
    }
}
user = SimpleNamespace(
    user_id="11111111-1111-1111-1111-111111111111",
    portfolio_id="33333333-3333-3333-3333-333333333333",
    is_admin=False,
)
view._render_opportunity_sets(user, snapshot, "1Y")
"""
    )

    app.run()

    assert not app.exception
    assert len(app.get("data_editor")) == 0
    assert len(app.button) == 0
    assert len(app.dataframe) == 1
