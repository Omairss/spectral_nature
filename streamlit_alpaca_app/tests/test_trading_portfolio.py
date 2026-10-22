"""Accounting/ownership checks, independent of research or model quality."""
from types import SimpleNamespace

import pandas as pd
import pytest

from services import pipeline_store
from services import trading_call_tracking as tracking


def call(identity, returns, *, ticker=None, stance='long', status='open', benchmark=None):
    days = [f'2026-10-{day:02}' for day in returns]
    return {'tracking_id': identity, 'candidate_id': identity, 'ticker': ticker or identity,
            'stance': stance, 'horizon_key': '1w', 'horizon_label': '1 Week',
            'approved_at_utc': '2026-10-01T12:00:00Z', 'entry_policy': tracking.ENTRY_POLICY,
            'entry_date': days[0] if days else None, 'status': status,
            'path': [{'date': f'2026-10-{day:02}', 'return_pct': value,
                      'benchmark_return_pct': (benchmark or {}).get(day, 0)} for day, value in returns.items()]}


def test_staggered_funding_is_return_neutral_and_contributions_reconcile():
    a = call('A', {1: 0, 2: 10, 3: 21}, status='matured')
    b = call('B', {2: 0, 3: -10}, stance='short')
    result = tracking.build_paper_portfolio([a, b])
    # Day two: 10% on one unit, then one unit of new funding.
    # Day three: +0.11 - 0.10 profit on 2.10 opening capital.
    expected = (1.10 * (1 + .01 / 2.10) - 1) * 100
    assert result['return_pct'] == pytest.approx(expected)
    assert result['path'][1]['return_pct'] == pytest.approx(10)
    assert result['path'][-1]['value_units'] == pytest.approx(2.11)
    assert sum(p['contribution_pct'] for p in result['attribution']) == pytest.approx(expected)
    assert result['allocation']['cash_pct'] == pytest.approx(1.21 / 2.11 * 100)
    assert result['allocation']['short_pct'] == pytest.approx(1.10 / 2.11 * 100)
    assert [h['ticker'] for h in result['holdings']] == ['B']


def test_matured_calls_remain_cash_and_later_funding_does_not_dilute_history():
    result = tracking.build_paper_portfolio([
        call('A', {1: 0, 2: 20}, status='matured'), call('B', {5: 0, 6: 0})])
    assert result['return_pct'] == pytest.approx(20)
    assert result['allocation']['cash_pct'] == pytest.approx(1.2 / 2.2 * 100)
    assert result['allocation']['long_pct'] == pytest.approx(1 / 2.2 * 100)
    assert result['completed_calls'] == 1
    assert result['open_calls'] == 1


def test_overlapping_symbol_keeps_both_directions_and_exposes_gross_risk():
    result = tracking.build_paper_portfolio([
        call('long', {1: 0, 2: 10}, ticker='ABC'),
        call('short', {1: 0, 2: -10}, ticker='ABC', stance='short')])
    assert result['return_pct'] == pytest.approx(0)
    assert len(result['holdings']) == 1
    holding = result['holdings'][0]
    assert holding['stance'] == 'mixed'
    assert holding['net_exposure_pct'] == pytest.approx(0)
    assert holding['gross_exposure_pct'] == pytest.approx(110)
    assert set(holding['tracking_ids']) == {'long', 'short'}


def test_matched_benchmark_uses_the_same_staggered_funding_and_short_sign():
    result = tracking.build_paper_portfolio([
        call('A', {1: 0, 2: 10, 3: 20}, benchmark={1: 0, 2: 5, 3: 10}),
        call('B', {2: 0, 3: -10}, stance='short', benchmark={2: 0, 3: -5})])
    assert result['benchmark_return_pct'] == pytest.approx(5)
    assert result['excess_return_pct'] == pytest.approx(result['return_pct'] - 5)


def test_missing_benchmark_is_unknown_and_missing_marks_keep_the_position_visible():
    stale = call('A', {1: 0, 2: 10}, status='price_unavailable', benchmark={1: None, 2: None})
    result = tracking.build_paper_portfolio([stale, call('B', {}, status='price_unavailable')])
    assert result['status'] == 'partial'
    assert result['benchmark_status'] == 'unavailable'
    assert all(p['benchmark_return_pct'] is None for p in result['path'])
    assert result['benchmark_return_pct'] is None
    assert result['return_pct'] == pytest.approx(10)
    assert result['current_marks'] == 0
    assert result['open_calls'] == 2
    missing = next(h for h in result['holdings'] if h['ticker'] == 'B')
    assert missing['net_exposure_pct'] is None
    assert missing['contribution_pct'] is None


def test_pending_entry_and_empty_portfolio_do_not_invent_zero_returns():
    result = tracking.build_paper_portfolio([call('A', {}, status='pending_entry')])
    assert result['status'] == 'pending'
    assert result['return_pct'] is None
    assert result['pending_calls'] == 1
    assert result['path'] == []
    assert tracking.build_paper_portfolio([])['status'] == 'empty'


def test_drawdown_uses_combined_wealth_and_nonpositive_capital_never_restarts():
    result = tracking.build_paper_portfolio([call('A', {1: 0, 2: 20, 3: 8})])
    assert result['max_drawdown_pct'] == pytest.approx(-10)
    exhausted = tracking.build_paper_portfolio([
        call('A', {1: 0, 2: -150}, stance='short'), call('B', {3: 0, 4: 20})])
    assert exhausted['status'] == 'capital_exhausted'
    assert exhausted['return_pct'] is None
    assert exhausted['path'][-1]['return_pct'] is None


def test_summary_joins_only_owned_approvals_and_keeps_individual_paths_lazy(monkeypatch):
    owned = call('owned', {1: 0, 2: 10})
    foreign = call('foreign', {1: 0, 2: 900})
    frames = []
    for c, owner in [(owned, 'me'), (foreign, 'other')]:
        import json
        frames.append({**c, 'owner_id': owner, 'path_json': json.dumps(c['path'])})
    monkeypatch.setattr(tracking, 'approved_calls', lambda **kw: [owned] if kw['owner_id'] == 'me' else [])
    monkeypatch.setattr(pipeline_store, 'latest_dataset_metadata', lambda *a: SimpleNamespace(asof_time_utc='2026-10-03'))
    monkeypatch.setattr(pipeline_store, 'load_dataset_frame', lambda *a, **kw: pd.DataFrame(frames))
    result = tracking.tracking_view(owner_id='me')
    assert result['portfolio']['return_pct'] == pytest.approx(10)
    assert result['portfolio']['approved_calls'] == 1
    assert len(result['calls']) == 1
    assert 'path' not in result['calls'][0]
    assert 'owner_id' not in result['calls'][0]
    frames[0]['path_json'] = '{'
    unreadable = tracking.tracking_view(owner_id='me')
    assert len(unreadable['calls']) == 1
    assert unreadable['calls'][0]['path_status'] == 'unavailable'
    assert unreadable['portfolio']['status'] == 'unavailable'
    frames[0]['owner_id'] = 'other'
    with pytest.raises(tracking.TrackingUnavailable):
        tracking.tracking_view(owner_id='me')


def test_historical_missing_marks_remain_visible_after_current_marks_recover():
    result = tracking.build_paper_portfolio([
        call('A', {1: 0, 3: 10}), call('B', {1: 0, 2: 5, 3: 10})])
    assert result['status'] == 'partial'
    assert result['stale_sessions'] == 1
    assert result['current_marks'] == 2
    assert result['path'][1]['uses_older_marks'] is True


def test_invalid_path_does_not_erase_other_owned_positions():
    broken = call('broken', {1: 4, 2: 10})  # Missing the zero-return entry anchor.
    result = tracking.build_paper_portfolio([call('good', {1: 0, 2: 10}), broken])
    assert result['status'] == 'partial'
    assert result['unpriced_calls'] == 1
    assert result['return_pct'] == pytest.approx(10)
    assert len(result['holdings']) == 2


def test_research_unavailability_does_not_hide_the_owned_portfolio(monkeypatch):
    from services import trading_agent_view as view
    portfolio = tracking.build_paper_portfolio([call('A', {1: 0, 2: 10})])
    monkeypatch.setattr(view, 'tracking_view', lambda **kw: {'status': 'available', 'calls': [], 'as_of': '2026-10-03', 'portfolio': portfolio})
    monkeypatch.setattr(view, '_latest_output', lambda: (pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), None))
    result = view.build_trading_agent_view(SimpleNamespace(user_id='me'))
    assert result['status'] == 'unavailable'
    assert result['paper_portfolio']['return_pct'] == pytest.approx(10)
