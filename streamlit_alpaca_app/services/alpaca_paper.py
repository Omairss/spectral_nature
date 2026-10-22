"""Owned Alpaca paper account. Broker accounting, retained reads, explicit refresh.

Key Vault references live on an existing brokerage connection. This account is
independent of the main Portfolio provider and of historical research outcomes.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from datetime import timedelta
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
import math
import os
from typing import Any
import uuid

import pandas as pd

from services import auth_store, brokerage_mcp_store as store
from services.alpaca_api import AlpacaAPI
from services.auth_service import UserContext
from services.config import AppConfig
from services.secrets import get_secret_value_from_vault
from compute.paper_sizing import RiskPolicy, recommend_position, risk_after_quantity

PROVIDER = 'alpaca_paper'
RESOURCE_URL = 'https://paper-api.alpaca.markets'
EXECUTION_PRICE_TOLERANCE = .01
ACCOUNT_FIELDS = ('status', 'currency', 'equity', 'cash', 'buying_power', 'last_equity',
                  'long_market_value', 'short_market_value', 'initial_margin', 'maintenance_margin',
                  'trading_blocked', 'account_blocked', 'shorting_enabled', 'pattern_day_trader', 'daytrade_count')
POSITION_FIELDS = ('symbol', 'asset_class', 'qty', 'side', 'market_value', 'cost_basis',
                   'avg_entry_price', 'current_price', 'unrealized_pl', 'unrealized_plpc', 'change_today')
ORDER_FIELDS = ('id', 'client_order_id', 'symbol', 'side', 'type', 'time_in_force', 'qty', 'notional',
                'filled_qty', 'filled_avg_price', 'status', 'submitted_at', 'filled_at', 'canceled_at')
RISK_FIELDS = ('method', 'policy', 'symbol', 'stance', 'price', 'price_as_of', 'price_source',
               'price_dataset_version', 'equity', 'current_weight_pct', 'target_weight_pct',
               'suggested_notional', 'suggested_quantity', 'asset_volatility_pct',
               'portfolio_volatility_before_pct', 'portfolio_volatility_after_pct', 'binding_limit',
               'historical_daily_expected_shortfall_pct', 'observations', 'history_start', 'history_end',
               'covariance_shrinkage', 'execution_price_tolerance_pct')


class PaperAccountError(RuntimeError):
    pass


def environment_name() -> str:
    value = (os.getenv('APP_TRACK') or 'local').strip().lower()
    if not value or not all(c.isalnum() or c in '-_' for c in value):
        raise PaperAccountError('Invalid application environment.')
    return value


def _admin(context: UserContext) -> None:
    if not auth_store.user_has_active_portfolio_admin_membership(user_id=context.user_id, portfolio_id=context.portfolio_id):
        raise PermissionError('Administrator access is required for paper trading.')


def connection(context: UserContext) -> dict[str, Any] | None:
    _admin(context)
    return store.get_connection(user_id=context.user_id, provider=PROVIDER,
                                resource_url=RESOURCE_URL, environment=environment_name())


def api_for_connection(link: dict[str, Any]) -> AlpacaAPI:
    if link.get('provider') != PROVIDER or link.get('resource_url') != RESOURCE_URL or link.get('environment') != environment_name() or link.get('status') != 'connected':
        raise PaperAccountError('Paper account connection is unavailable.')
    refs = link.get('server_info') or {}
    key = get_secret_value_from_vault(refs.get('key_secret_name', ''), vault_name=refs.get('vault_name', ''))
    secret = get_secret_value_from_vault(refs.get('secret_secret_name', ''), vault_name=refs.get('vault_name', ''))
    if not key or not secret:
        raise PaperAccountError('Paper account credentials are unavailable.')
    return AlpacaAPI(AppConfig(key, secret, alpaca_trading_base_url=RESOURCE_URL))


def _account(api: AlpacaAPI, link: dict[str, Any]) -> dict:
    account = api.paper_request('GET', '/v2/account')
    if str(account.get('id')) != str(link['server_info']['account_id']):
        raise PaperAccountError('The paper credentials now refer to a different account. Reconnect it explicitly.')
    return account


def connect(context: UserContext, *, vault_name: str, key_secret_name: str, secret_secret_name: str) -> dict[str, Any]:
    """Operator setup; no public route accepts secret names or raw credentials."""
    _admin(context)
    refs = {'vault_name': vault_name, 'key_secret_name': key_secret_name, 'secret_secret_name': secret_secret_name}
    pending = {'provider': PROVIDER, 'resource_url': RESOURCE_URL, 'environment': environment_name(),
               'status': 'connected', 'server_info': refs}
    account = api_for_connection(pending).paper_request('GET', '/v2/account')
    if not account.get('id') or account.get('status') != 'ACTIVE':
        raise PaperAccountError('The Alpaca paper account is not active.')
    refs['account_id'] = account['id']
    store.save_direct_connection(user_id=context.user_id, provider=PROVIDER, resource_url=RESOURCE_URL,
        environment=environment_name(), access_token_ciphertext='', refresh_token_ciphertext='', device_token_ciphertext='',
        token_type='key_vault', scope='paper_trading', token_expires_at=None, server_info=refs)
    return refresh(context)


def _snapshot(context: UserContext, link: dict[str, Any]) -> dict[str, Any] | None:
    snapshot = store.get_portfolio_snapshot(portfolio_id=context.portfolio_id, connection_id=link['id'],
                                            user_id=context.user_id, environment=environment_name())
    if snapshot and (snapshot.get('details') or {}).get('account_id') == link['server_info']['account_id']:
        return snapshot
    return None


def _select(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {key: row[key] for key in fields if key in row}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def project(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Explicit public allowlist. Never return connection metadata or raw account identity."""
    details = snapshot.get('details') or {}
    summary = _select(snapshot.get('account_summary') or {}, ACCOUNT_FIELDS)
    equity, previous = _number(summary.get('equity')), _number(summary.get('last_equity'))
    summary['day_pl'] = equity - previous if equity is not None and previous is not None else None
    summary['day_return_pct'] = (equity / previous - 1) * 100 if equity is not None and previous and previous > 0 else None
    points = [_select(row, ('date', 'equity', 'profit_loss', 'return_pct')) for row in snapshot.get('timeseries') or []]
    first_funded = next((index for index, point in enumerate(points) if (_number(point.get('equity')) or 0) != 0), None)
    points = points[first_funded:] if first_funded is not None else []
    positions = [_select(row, POSITION_FIELDS) for row in snapshot.get('positions') or []]
    peak, drawdown = None, None
    for point in points:
        value = _number(point.get('equity'))
        if value is not None and value > 0:
            peak = max(peak or value, value)
        if value is not None and peak is not None:
            drawdown = min(drawdown or 0, (value / peak - 1) * 100)
    latest = next((p for p in reversed(points) if p.get('return_pct') is not None), {})
    return {'status': 'partial' if details.get('errors') else 'available', 'provider': PROVIDER,
            'risk_policy': asdict(RiskPolicy()),
            'as_of': str(snapshot.get('source_updated_at') or ''), 'period': '1Y', 'summary': summary,
            'holdings': positions, 'path': points, 'return_pct': latest.get('return_pct'),
            'max_drawdown_pct': drawdown, 'orders': [_select(row, ORDER_FIELDS) for row in details.get('orders') or []],
            'coverage': details.get('coverage') or {}, 'unavailable': sorted((details.get('errors') or {}).keys())}


def view(context: UserContext) -> dict[str, Any]:
    link = connection(context)
    if not link or link.get('status') != 'connected':
        return {'status': 'unlinked', 'provider': PROVIDER}
    snapshot = _snapshot(context, link)
    return project(snapshot) if snapshot else {'status': 'pending', 'provider': PROVIDER}


def refresh(context: UserContext) -> dict[str, Any]:
    """Bounded broker reads on an authenticated explicit refresh, never on a GET."""
    link = connection(context)
    if not link:
        raise PaperAccountError('No Alpaca paper account is linked.')
    api = api_for_connection(link)
    old = _snapshot(context, link) or {}
    old_details = old.get('details') or {}
    account = _account(api, link)
    now = datetime.now(timezone.utc)
    coverage = dict(old_details.get('coverage') or {})
    coverage['account'] = now.isoformat()
    errors, fetched = {}, {}
    queries = {
        'positions': ('/v2/positions', {}),
        'orders': ('/v2/orders', {'status': 'all', 'limit': 100, 'direction': 'desc'}),
        'history': ('/v2/account/portfolio/history', {'period': '1A', 'timeframe': '1D', 'cashflow_types': 'ALL'}),
    }
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = {pool.submit(api.paper_request, 'GET', path, params=params): key for key, (path, params) in queries.items()}
        for future in as_completed(pending):
            key = pending[future]
            try:
                result = future.result()
                if not isinstance(result, dict if key == 'history' else list):
                    raise ValueError('Invalid broker response.')
                fetched[key] = result
                coverage[key] = now.isoformat()
            except Exception as exc:
                errors[key] = type(exc).__name__
    points = old.get('timeseries') or []
    if 'history' in fetched:
        history = fetched['history']
        timestamps = history.get('timestamp') or []
        fields = [history.get(key) or [] for key in ('equity', 'profit_loss', 'profit_loss_pct')]
        if any(len(values) != len(timestamps) for values in fields):
            errors['history'] = 'InvalidHistory'
            coverage['history'] = (old_details.get('coverage') or {}).get('history')
        else:
            points = [{'date': datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                       'equity': _number(equity), 'profit_loss': _number(pl),
                       'return_pct': _number(pct) * 100 if _number(pct) is not None else None}
                      for ts, equity, pl, pct in zip(timestamps, *fields)]
    saved = store.save_portfolio_snapshot(portfolio_id=context.portfolio_id, connection_id=link['id'],
        provider=PROVIDER, environment=environment_name(), account_summary=_select(account, ACCOUNT_FIELDS),
        positions=[_select(row, POSITION_FIELDS) for row in fetched.get('positions', old.get('positions') or [])],
        timeseries=points, source_updated_at=now,
        details={'account_id': link['server_info']['account_id'],
                 'orders': [_select(row, ORDER_FIELDS) for row in fetched.get('orders', old_details.get('orders') or [])],
                 'coverage': coverage, 'errors': errors})
    return project(saved)


def _order_inputs(context: UserContext, link: dict[str, Any], candidate: dict, policy: RiskPolicy) -> tuple[dict, dict]:
    """Fresh broker facts plus the versioned completed-session research data."""
    api = api_for_connection(link)
    symbol = api._normalize_symbol(candidate.get('ticker') or '')
    account = _account(api, link)
    if account.get('status') != 'ACTIVE' or account.get('trading_blocked') or account.get('account_blocked'):
        raise PaperAccountError('Alpaca has blocked paper trading on this account.')
    clock = api.paper_request('GET', '/v2/clock')
    if not clock.get('is_open'):
        raise PaperAccountError('Paper market orders can be reviewed while the market is open.')
    positions = api.paper_request('GET', '/v2/positions')
    orders = api.paper_request('GET', '/v2/orders', params={'status': 'open', 'limit': 1})
    if orders:
        raise PaperAccountError('Wait for the open paper order to fill or cancel before sizing another trade.')
    if any(str(p.get('asset_class')) != 'us_equity' for p in positions):
        raise PaperAccountError('The sizing model needs a risk model for the non-equity holdings in this account.')
    asset = api.paper_request('GET', '/v2/assets/' + symbol)
    if not asset.get('tradable') or asset.get('class') != 'us_equity':
        raise PaperAccountError('This asset is not available for equity paper trading.')
    if candidate.get('stance') == 'short' and (not account.get('shorting_enabled') or not asset.get('shortable') or not asset.get('easy_to_borrow')):
        raise PaperAccountError('Alpaca does not currently offer this paper short.')
    quote = api._request('GET', api.config.alpaca_data_base_url, '/v2/stocks/snapshots',
                         params={'symbols': symbol, 'feed': 'iex'}, timeout=10, max_attempts=1).get(symbol) or {}
    latest = quote.get('latestTrade') or {}
    price = _number(latest.get('p'))
    now = pd.Timestamp.now(tz='UTC')
    observed = pd.to_datetime(latest.get('t'), utc=True, errors='coerce')
    if price is None or price <= 0 or pd.isna(observed) or not 0 <= (now - observed).total_seconds() <= 120:
        raise PaperAccountError('A recent trade price is unavailable for order sizing.')
    equity = float(account['equity'])
    weights = {str(p['symbol']): float(p['market_value']) / equity for p in positions}
    symbols = sorted({symbol, *weights})
    frames = api.get_stock_bars(symbols, start=(now - pd.Timedelta(days=550)).to_pydatetime(),
                               end=now.to_pydatetime(), timeout_seconds=30)
    frame = pd.concat([data.assign(symbol=key) for key, data in frames.items() if not data.empty], ignore_index=True) if any(not data.empty for data in frames.values()) else pd.DataFrame(columns=['symbol', 'timestamp', 'close'])
    from services.trading_call_tracking import completed_daily_prices
    prices = completed_daily_prices(frame, now)
    matrix = prices.pivot(index='session_date', columns='symbol', values='close').sort_index()
    today = now.tz_convert('America/New_York').date()
    calendar = api.paper_request('GET', '/v2/calendar', params={'start': (today - timedelta(days=550)).isoformat(), 'end': today.isoformat()})
    last_session = max(str(row['date']) for row in calendar if str(row['date']) < today.isoformat())
    if matrix.empty or matrix.index[-1] != last_session or any(key not in matrix or pd.isna(matrix.loc[last_session, key]) for key in symbols):
        raise PaperAccountError('The portfolio’s daily risk history is not current through the last closed session.')
    matrix = matrix.reindex(sorted(str(row['date']) for row in calendar if str(row['date']) < today.isoformat()))
    # Reindex to the observed common market sessions before differencing: absent
    # bars remain missing, never a zero or an unlabelled multi-day return.
    daily_returns = matrix.pct_change(fill_method=None)
    risk = recommend_position(returns=daily_returns, weights=weights, symbol=symbol,
        stance=candidate['stance'], equity=equity, buying_power=float(account['buying_power']),
        cash=float(account['cash']), price=price, policy=policy)
    quantum = Decimal('0.000001') if asset.get('fractionable') and candidate['stance'] == 'long' else Decimal('1')
    capacity = Decimal(str(risk['suggested_quantity']))
    # Reserve price movement before the human confirms. Revalidation compares
    # the exact reviewed shares with fresh full capacity, not another buffered
    # recommendation (which would reject every upward price tick).
    quantity = (capacity * Decimal(str(1 - EXECUTION_PRICE_TOLERANCE))).quantize(quantum, rounding=ROUND_DOWN)
    if quantity <= 0:
        raise PaperAccountError('The risk allocation is below Alpaca’s order quantity increment.')
    observations = daily_returns[risk['symbols']].dropna().tail(252)
    retained_returns = {'dates': observations.index.tolist(), 'symbols': observations.columns.tolist(), 'values': observations.to_numpy().tolist()}
    risk.update(capacity_quantity=str(capacity), execution_price_tolerance_pct=EXECUTION_PRICE_TOLERANCE * 100,
                risk_as_of=now.isoformat(), price_as_of=observed.isoformat(), price_source='Alpaca · IEX · all corporate-action adjustments',
                price_dataset_version=hashlib.sha256(json.dumps(retained_returns, sort_keys=True).encode()).hexdigest(),
                retained_returns=retained_returns,
                suggested_quantity=str(quantity), suggested_notional=float(quantity) * price)
    risk = risk_after_quantity(risk, str(quantity))
    client_id = 'sn-' + hashlib.sha256(json.dumps([context.user_id, account['id'], candidate['candidate_id']]).encode()).hexdigest()[:40]
    order = {'symbol': symbol, 'side': 'buy' if candidate['stance'] == 'long' else 'sell',
             'qty': str(quantity), 'type': 'market', 'time_in_force': 'day', 'client_order_id': client_id}
    return order, risk


def _candidate(context: UserContext, candidate_id: str) -> dict:
    from services.trading_agent_view import build_trading_agent_view
    current = build_trading_agent_view(context)
    candidate = next((row for horizon in current.get('horizons', []) for row in horizon.get('candidates', [])
                      if row.get('candidate_id') == candidate_id), None)
    if not candidate or candidate.get('stance') not in {'long', 'short'}:
        raise ValueError('The directional call is no longer current.')
    return candidate


def review_order(context: UserContext, *, candidate_id: str, policy: RiskPolicy) -> dict:
    link = connection(context)
    if not link:
        raise PaperAccountError('No Alpaca paper account is linked.')
    candidate = _candidate(context, candidate_id)
    order, risk = _order_inputs(context, link, candidate, policy)
    payload = {'order': order, 'candidate': candidate, 'risk': risk, 'portfolio_id': context.portfolio_id,
               'account_id': link['server_info']['account_id']}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    expires = datetime.now(timezone.utc) + timedelta(seconds=120)
    ticket = store.create_order_ticket(connection_id=link['id'], user_id=context.user_id, provider=PROVIDER,
        environment=environment_name(), review_tool_name='review_paper_order', execution_tool_name='submit_paper_order',
        arguments_hash=digest, ref_id=str(uuid.uuid4()), request_summary=payload, expires_at=expires)
    return {'ticket_id': ticket['id'], 'arguments_hash': digest, 'expires_at': expires.isoformat(),
            'order': order, 'risk': _select(risk, RISK_FIELDS)}


def confirm_order(context: UserContext, *, ticket_id: str, arguments_hash: str) -> dict:
    """One-use, owner-bound confirmation. Re-read/re-size before a paper-only POST."""
    from services.alpaca_api import AlpacaAPIError
    link = connection(context)
    if not link:
        raise PaperAccountError('No Alpaca paper account is linked.')
    api = api_for_connection(link)
    ticket = store.get_order_ticket(ticket_id=ticket_id, user_id=context.user_id, provider=PROVIDER, environment=environment_name())
    if not ticket or ticket['connection_id'] != link['id'] or ticket.get('arguments_hash') != arguments_hash:
        raise ValueError('The paper order review is unavailable.')
    payload = ticket['request_summary']
    if payload.get('portfolio_id') != context.portfolio_id:
        raise PermissionError('This order belongs to another portfolio.')
    if payload.get('account_id') != link['server_info']['account_id']:
        raise ValueError('The paper account changed. Review the order for the current account.')
    order = payload['order']
    # Use one account lock across replicas and environments, including tickets
    # for different calls. Never validate two orders against the same free cash.
    lock = auth_store.open_auth_store_connection()
    if lock is None:
        raise PaperAccountError('The paper order service is unavailable.')
    with lock:
        with lock.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_xact_lock(hashtext(%s))', ('alpaca_paper:' + link['server_info']['account_id'],))
            if not cursor.fetchone()[0]:
                raise PaperAccountError('Another paper order is being processed. Refresh the account shortly.')
        _admin(context)
        if ticket['status'] in {'executed', 'executing', 'unknown'}:
            try:
                _account(api, link)
                existing = api.paper_request('GET', '/v2/orders:by_client_order_id', params={'client_order_id': order['client_order_id']})
                api.validate_order_receipt(existing, order)
                receipt = _select(existing, ORDER_FIELDS)
                store.finish_order_ticket(ticket_id=ticket_id, status='executed',
                    result_summary={**(ticket.get('result_summary') or {}), 'order': receipt, 'reconciled': True})
                from services.pipeline_store import record_trading_agent_action
                ok, _ = record_trading_agent_action(candidate=payload['candidate'], action='approve',
                                                  requested_by=context.user_id, requested_email=context.email)
                return {'status': 'submitted', 'order': receipt, 'approval_recorded': ok}
            except AlpacaAPIError as exc:
                if exc.status_code != 404:
                    raise PaperAccountError('The paper order status is temporarily unavailable.') from exc
                raise PaperAccountError('The paper order is still being reconciled. Do not submit a replacement.') from exc
        claimed = store.consume_order_ticket(ticket_id=ticket_id, user_id=context.user_id, provider=PROVIDER,
            environment=environment_name(), arguments_hash=arguments_hash)
        if not claimed:
            raise ValueError('This review expired or was already used. Review the order again.')
        submission_attempted = False
        risk = None
        try:
            # The reviewed thesis remains pinned, even when new research publishes.
            _, risk = _order_inputs(context, link, payload['candidate'], RiskPolicy(**payload['risk']['policy']))
            if Decimal(order['qty']) > Decimal(risk['capacity_quantity']):
                raise ValueError('The reviewed quantity exceeds the current risk allocation. Review the updated amount.')
            if abs(float(risk['price']) / float(payload['risk']['price']) - 1) > EXECUTION_PRICE_TOLERANCE:
                raise ValueError('The price changed by more than 1%. Review the updated order.')
            risk = risk_after_quantity(risk, order['qty'])
            _admin(context)
            submission_attempted = True
            result = api.paper_request('POST', '/v2/orders', body=order)
            api.validate_order_receipt(result, order)
        except Exception as exc:
            # A provider rejection is definite. A timeout, transport error or
            # server error is ambiguous; reconcile its deterministic identity.
            definite = not submission_attempted or isinstance(exc, AlpacaAPIError) and exc.status_code is not None and 400 <= exc.status_code < 500
            store.finish_order_ticket(ticket_id=ticket_id, status='failed' if definite else 'unknown',
                result_summary={'client_order_id': order['client_order_id'], 'confirmation_sizing': risk}, error=type(exc).__name__)
            if definite:
                raise PaperAccountError(str(exc) if isinstance(exc, (ValueError, PaperAccountError)) else 'Alpaca rejected the paper order.') from exc
            raise PaperAccountError('The submission status is unknown. Refresh to reconcile the paper order.') from exc
        receipt = _select(result, ORDER_FIELDS)
        store.finish_order_ticket(ticket_id=ticket_id, status='executed',
                                  result_summary={'order': receipt, 'confirmed_risk': risk})
    # Approval remains research history, with its own observation convention;
    # broker performance comes only from broker fills and account history.
    from services.pipeline_store import record_trading_agent_action
    ok, _ = record_trading_agent_action(candidate=payload['candidate'], action='approve',
                                      requested_by=context.user_id, requested_email=context.email)
    return {'status': 'submitted', 'order': receipt, 'approval_recorded': ok}
