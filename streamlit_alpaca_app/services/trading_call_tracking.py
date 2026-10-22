"""Immutable research approvals and daily paper-call performance.

Approval fixes the thesis and entry convention. Price ingestion owns valuation;
application reads never call a broker or start research.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid
from typing import Any

import pandas as pd
from psycopg.rows import dict_row

from .json_utils import to_jsonable
from .trading_agent import _TRADING_AGENT_HORIZON_DAYS as HORIZON_DAYS

DATASET = 'trading_agent_approved_outcomes'
PRICE_DATASET = 'trading_agent_call_prices'
ENTRY_POLICY = 'next_trading_day_close'
SUMMARY_COLUMNS = [
    'tracking_id', 'owner_id', 'candidate_id', 'ticker', 'stance', 'horizon_key', 'horizon_label',
    'approved_at_utc', 'scheduled_entry_date', 'scheduled_exit_date', 'entry_ts', 'entry_date', 'entry_close', 'target_date', 'last_price_ts', 'last_price_date', 'last_close',
    'return_pct', 'benchmark_return_pct', 'excess_return_pct', 'max_drawdown_pct',
    'status', 'price_dataset_version', 'as_of',
]
COLUMNS = [*SUMMARY_COLUMNS, 'path_json', 'forecast_json', 'hypothesis']


class TrackingUnavailable(RuntimeError):
    pass


def ensure_schema(connection: Any) -> None:
    """Called by mutation/migration, never by a page read."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('trading_agent_approved_calls')")
        if cursor.fetchone()[0] is not None:
            return
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext('trading_agent_approved_calls_schema_v1'))")
        cursor.execute('''CREATE TABLE IF NOT EXISTS trading_agent_approved_calls (
            tracking_id TEXT PRIMARY KEY, dataset_namespace TEXT NOT NULL,
            owner_id TEXT NOT NULL, candidate_id TEXT NOT NULL, action_id TEXT NOT NULL,
            ticker TEXT NOT NULL, stance TEXT NOT NULL CHECK (stance IN ('long','short')),
            horizon_key TEXT NOT NULL, horizon_label TEXT NOT NULL, horizon_days INTEGER NOT NULL CHECK (horizon_days > 0),
            approved_at_utc TIMESTAMPTZ NOT NULL, candidate_payload JSONB NOT NULL,
            entry_policy TEXT NOT NULL, approval_source TEXT NOT NULL,
            entry_payload JSONB, completed_at_utc TIMESTAMPTZ,
            UNIQUE(dataset_namespace, owner_id, candidate_id)
        )''')
        cursor.execute('''CREATE INDEX IF NOT EXISTS trading_agent_approved_calls_owner
            ON trading_agent_approved_calls(dataset_namespace,owner_id,approved_at_utc DESC)''')


def record_approval(cursor: Any, *, candidate: dict[str, Any], owner_id: str,
                    action_id: str, approved_at: Any, namespace: str,
                    source: str = 'paper_approval') -> str:
    stance = str(candidate.get('stance') or '').lower()
    horizon = str(candidate.get('horizon_key') or '')
    candidate_id = str(candidate.get('candidate_id') or '')
    ticker = str(candidate.get('ticker') or '').upper()
    if stance not in {'long', 'short'} or horizon not in HORIZON_DAYS or not candidate_id or not ticker or not owner_id:
        raise ValueError('A tracked call needs a directional thesis, horizon and signed-in owner.')
    identity = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([namespace, owner_id, candidate_id])))
    cursor.execute('''INSERT INTO trading_agent_approved_calls (
        tracking_id,dataset_namespace,owner_id,candidate_id,action_id,ticker,stance,horizon_key,horizon_label,horizon_days,
        approved_at_utc,candidate_payload,entry_policy,approval_source
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
        ON CONFLICT(dataset_namespace,owner_id,candidate_id) DO NOTHING''',
        (identity, namespace, owner_id, candidate_id, action_id, ticker, stance, horizon,
         candidate.get('horizon_label') or candidate.get('suggested_horizon') or horizon, HORIZON_DAYS[horizon],
         approved_at, json.dumps(to_jsonable(candidate), default=str), ENTRY_POLICY, source))
    return identity


def initialize_tracking() -> int:
    """Retain genuine historical log-only approvals, once, without inventing fills."""
    from . import pipeline_store as store
    connection = store.open_database_connection()
    if connection is None:
        raise TrackingUnavailable('Call tracking is unavailable.')
    try:
        ensure_schema(connection)
        retained = 0
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT to_regclass('trading_agent_actions') AS actions")
            if cursor.fetchone()['actions'] is None:
                connection.commit()
                return 0
            cursor.execute('''SELECT DISTINCT ON (requested_by,candidate_id)
                action_id,candidate_id,requested_by,created_at_utc,candidate_payload,horizon_key,ticker
                FROM trading_agent_actions a WHERE dataset_namespace=%s
                AND action IN ('place_requested','approved') AND execution_mode IN ('log_only','paper_tracking')
                AND coalesce(requested_by,'')<>''
                AND candidate_payload->>'stance' IN ('long','short')
                AND NOT EXISTS (SELECT 1 FROM trading_agent_approved_calls c
                    WHERE c.dataset_namespace=a.dataset_namespace AND c.owner_id=a.requested_by
                    AND c.candidate_id=a.candidate_id)
                ORDER BY requested_by,candidate_id,created_at_utc,action_id''', (store.dataset_namespace(),))
            rows = cursor.fetchall()
            for row in rows:
                candidate = row['candidate_payload']
                if isinstance(candidate, str):
                    candidate = json.loads(candidate)
                candidate = {**candidate, 'candidate_id': row['candidate_id']}
                candidate['horizon_key'] = candidate.get('horizon_key') or row['horizon_key']
                candidate['ticker'] = candidate.get('ticker') or row['ticker']
                if candidate.get('horizon_key') not in HORIZON_DAYS:
                    continue
                record_approval(cursor, candidate=candidate, owner_id=row['requested_by'], action_id=row['action_id'],
                                approved_at=row['created_at_utc'], namespace=store.dataset_namespace(), source='retained_log_approval')
                retained += max(cursor.rowcount, 0)
        connection.commit()
        return retained
    finally:
        connection.close()


def approved_calls(*, owner_id: str | None = None, tracking_id: str | None = None,
                   active_only: bool = False, include_snapshot: bool = True) -> list[dict[str, Any]]:
    from . import pipeline_store as store
    connection = store.open_database_connection()
    if connection is None:
        raise TrackingUnavailable('Call tracking is unavailable.')
    try:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT to_regclass('trading_agent_approved_calls') AS calls")
            if cursor.fetchone()['calls'] is None:
                return []
            clauses, params = ['dataset_namespace=%s'], [store.dataset_namespace()]
            if owner_id is not None:
                clauses.append('owner_id=%s'); params.append(owner_id)
            if tracking_id is not None:
                clauses.append('tracking_id=%s'); params.append(tracking_id)
            if active_only:
                clauses.append('completed_at_utc IS NULL')
            selected = '*' if include_snapshot else 'tracking_id,owner_id,candidate_id,ticker,stance,horizon_key,horizon_label,approved_at_utc,entry_policy,entry_payload,completed_at_utc'
            cursor.execute('SELECT '+selected+' FROM trading_agent_approved_calls WHERE '+ ' AND '.join(clauses)
                           + ' ORDER BY approved_at_utc DESC,tracking_id', params)
            return cursor.fetchall()
    except Exception as exc:
        raise TrackingUnavailable('Call tracking is unavailable.') from exc
    finally:
        connection.close()


def completed_daily_prices(frame: pd.DataFrame, as_of: Any) -> pd.DataFrame:
    """Alpaca daily timestamps label the start of a New York calendar-day bucket."""
    columns = ['symbol','timestamp','close','session_date','available_at']
    if frame.empty:
        return pd.DataFrame(columns=columns)
    prices = frame[['symbol','timestamp','close']].copy()
    prices['symbol'] = prices['symbol'].astype(str).str.strip().str.upper()
    prices['timestamp'] = pd.to_datetime(prices['timestamp'],utc=True,errors='coerce')
    prices['close'] = pd.to_numeric(prices['close'],errors='coerce')
    prices = prices.dropna(subset=['timestamp','close'])
    prices = prices[(prices['close']>0) & (prices['close']<float('inf'))]
    local_days = prices['timestamp'].dt.tz_convert('America/New_York').dt.tz_localize(None).dt.normalize()
    prices['session_date'] = local_days.dt.strftime('%Y-%m-%d')
    prices['available_at'] = (local_days+pd.Timedelta(days=1)).dt.tz_localize('America/New_York').dt.tz_convert('UTC')
    cutoff = pd.to_datetime(as_of,utc=True)
    return prices[prices['available_at']<=cutoff].sort_values(['symbol','timestamp']).drop_duplicates(['symbol','session_date'],keep='last').reset_index(drop=True)


def build_approved_outcomes(*, calls: list[dict[str, Any]], prices: pd.DataFrame,
                            previous: pd.DataFrame, as_of: Any, price_version: str,
                            trading_days: list[str]) -> tuple[pd.DataFrame,dict[str,dict]]:
    """Pure valuation of approved snapshots, using one fresh adjusted-price basis."""
    prepared = completed_daily_prices(prices,as_of)
    groups = {symbol: group.reset_index(drop=True) for symbol,group in prepared.groupby('symbol',sort=False)}
    previous_by_id = {str(r['tracking_id']):r for r in previous.to_dict(orient='records')} if not previous.empty else {}
    benchmark = groups.get('SPY',pd.DataFrame(columns=prepared.columns))
    benchmark_by_day = benchmark.set_index('session_date')['close'].to_dict()
    calendar = sorted(set(pd.Timestamp(day).date().isoformat() for day in trading_days))
    if not calendar:
        raise ValueError('Approved-call valuation needs the market calendar.')
    evaluation_day = pd.Timestamp(as_of).tz_convert('America/New_York').strftime('%Y-%m-%d')
    rows, entries = [], {}
    for call in calls:
        identity = call['tracking_id']; old = previous_by_id.get(identity)
        if call.get('completed_at_utc') and old:
            rows.append(old); continue
        snapshot = call['candidate_payload']; snapshot = json.loads(snapshot) if isinstance(snapshot,str) else snapshot
        row = {key:call.get(key) for key in ('tracking_id','owner_id','candidate_id','ticker','stance','horizon_key','horizon_label')}
        row.update(approved_at_utc=pd.Timestamp(call['approved_at_utc']).isoformat(),status='pending_entry',path_json='[]',
                   forecast_json=json.dumps(snapshot.get('forecast') or {}),hypothesis=snapshot.get('hypothesis') or '',
                   price_dataset_version=price_version,as_of=pd.Timestamp(as_of).isoformat())
        series = groups.get(call['ticker'],pd.DataFrame(columns=prepared.columns))
        approval_day = pd.Timestamp(call['approved_at_utc']).tz_convert('America/New_York').strftime('%Y-%m-%d')
        next_session = next((day for day in calendar if day > approval_day), None)
        if next_session is None:
            raise ValueError('The market calendar does not cover the next approval session.')
        row['scheduled_entry_date'] = next_session
        entry = call.get('entry_payload')
        if isinstance(entry,str): entry=json.loads(entry)
        if not entry:
            eligible=series[series['session_date']==next_session]
            if eligible.empty:
                if next_session < evaluation_day:
                    row['status'] = 'price_unavailable'
                rows.append(row); continue
            first=eligible.iloc[0]
            entry={'timestamp':first['timestamp'].isoformat(),'session_date':first['session_date'],
                   'close':float(first['close']),'price_dataset_version':price_version}
            entries[identity]=entry
        # Re-read the anchor on this snapshot's adjusted basis. Never divide a new
        # split-adjusted mark by an old, differently adjusted entry price.
        anchor=series[series['timestamp']==pd.Timestamp(entry['timestamp'])]
        if anchor.empty:
            rows.append({**(old or row),'status':'price_unavailable'}); continue
        target=(pd.Timestamp(entry['session_date'])+pd.Timedelta(days=int(call['horizon_days']))).strftime('%Y-%m-%d')
        exit_session = next((day for day in calendar if day >= target), None)
        row['scheduled_exit_date'] = exit_session
        path=series[series['timestamp']>=pd.Timestamp(entry['timestamp'])].copy()
        if exit_session:
            path = path[path['session_date']<=exit_session]
        due=path[path['session_date']==exit_session] if exit_session else path.iloc[0:0]
        matured=not due.empty
        if matured: path=path[path['timestamp']<=due.iloc[0]['timestamp']]
        sign=1.0 if call['stance']=='long' else -1.0
        path['return_pct']=(path['close']/float(anchor.iloc[0]['close'])-1)*100*sign
        benchmark_entry=benchmark_by_day.get(entry['session_date'])
        path['benchmark_return_pct']=path['session_date'].map(benchmark_by_day)
        if benchmark_entry:
            path['benchmark_return_pct']=(path['benchmark_return_pct']/benchmark_entry-1)*100*sign
        else: path['benchmark_return_pct']=float('nan')
        points=[{'date':r['session_date'],'return_pct':float(r['return_pct']),
                 'benchmark_return_pct':None if pd.isna(r['benchmark_return_pct']) else float(r['benchmark_return_pct'])}
                for r in path.to_dict(orient='records')]
        last=path.iloc[-1]; market=points[-1]['benchmark_return_pct']
        wealth=1+path['return_pct']/100
        row.update(entry_ts=entry['timestamp'],entry_date=entry['session_date'],entry_close=entry['close'],target_date=target,
                   last_price_ts=last['timestamp'].isoformat(),last_price_date=last['session_date'],last_close=float(last['close']),
                   return_pct=float(last['return_pct']),benchmark_return_pct=market,
                   excess_return_pct=None if market is None else float(last['return_pct'])-market,
                   max_drawdown_pct=float(((wealth/wealth.cummax())-1).min()*100),
                   status='matured' if matured else ('price_unavailable' if exit_session and exit_session < evaluation_day else 'open'),path_json=json.dumps(points))
        rows.append(row)
    return pd.DataFrame(rows,columns=COLUMNS),entries


def pin_entries(entries: dict[str,dict[str,Any]]) -> dict[str,dict[str,Any]]:
    from . import pipeline_store as store
    if not entries: return {}
    connection=store.open_database_connection()
    if connection is None: raise TrackingUnavailable('Call tracking is unavailable.')
    try:
        result={}
        with connection.cursor() as cursor:
            for identity,entry in entries.items():
                cursor.execute('''UPDATE trading_agent_approved_calls SET entry_payload=%s::jsonb
                    WHERE dataset_namespace=%s AND tracking_id=%s AND entry_payload IS NULL''',
                    (json.dumps(entry),store.dataset_namespace(),identity))
                cursor.execute('''SELECT entry_payload FROM trading_agent_approved_calls
                    WHERE dataset_namespace=%s AND tracking_id=%s''',(store.dataset_namespace(),identity))
                result[identity]=cursor.fetchone()[0]
        connection.commit(); return result
    finally: connection.close()


def complete_calls(frame: pd.DataFrame) -> None:
    """Advance completion only after the immutable outcome artifact is published."""
    from . import pipeline_store as store
    matured=frame[frame['status'].eq('matured')]
    if matured.empty:return
    connection=store.open_database_connection()
    if connection is None:raise TrackingUnavailable('Call tracking is unavailable.')
    try:
        with connection.cursor() as cursor:
            for row in matured.to_dict(orient='records'):
                cursor.execute('''UPDATE trading_agent_approved_calls SET completed_at_utc=%s
                    WHERE dataset_namespace=%s AND tracking_id=%s AND completed_at_utc IS NULL''',
                    (row['as_of'],store.dataset_namespace(),row['tracking_id']))
        connection.commit()
    finally:connection.close()


def enqueue_outcome_reviews(frame: pd.DataFrame, calls: list[dict[str, Any]]) -> int:
    """Offer completed, published calls to the shared reviewer, without inference.

    Reoffering retained outcomes recovers a failed enqueue even after all calls
    mature. The immutable ledger, not artifact metadata, establishes ownership.
    """
    from . import pipeline_store as store
    from .zopedia_learning import bootstrap_zopedia_learning_storage, enqueue_learning_episode
    if frame.empty:
        return 0
    matured = frame[frame['status'].eq('matured')]
    if matured.empty:
        return 0
    by_id = {call['tracking_id']: call for call in calls}
    connection = store.open_database_connection()
    if connection is None:
        raise TrackingUnavailable('Outcome review queue is unavailable.')
    try:
        bootstrap_zopedia_learning_storage(connection)
        for outcome in matured.to_dict(orient='records'):
            call = by_id[outcome['tracking_id']]
            if outcome['owner_id'] != call['owner_id']:
                raise TrackingUnavailable('Outcome ownership differs from its approval.')
            decision = {key: call.get(key) for key in (
                'tracking_id', 'candidate_id', 'ticker', 'stance', 'horizon_key', 'horizon_days',
                'approved_at_utc', 'candidate_payload', 'entry_policy', 'entry_payload')}
            enqueue_learning_episode(connection, user_key=call['owner_id'], namespace=store.dataset_namespace(),
                source_id='approved_call:' + call['tracking_id'],
                question=f"Review the original {call['ticker']} {call['stance']} thesis over its {call['horizon_label']} horizon, its assumptions and subsequent paper outcome.",
                evidence_records={
                    'decision': {'title': 'Original approved call', 'metadata': {'phase': 'decision'}, 'content': to_jsonable(decision)},
                    'outcome': {'title': 'Published paper outcome', 'metadata': {'phase': 'outcome', 'retrospective': True},
                                'content': to_jsonable(outcome)},
                })
        connection.commit()
        return len(matured)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def tracking_view(*, owner_id: str, tracking_id: str | None = None) -> dict[str,Any]:
    from . import pipeline_store as store
    calls=approved_calls(owner_id=owner_id,tracking_id=tracking_id,include_snapshot=bool(tracking_id))
    if tracking_id and not calls:raise LookupError('Tracked call not found.')
    metadata=store.latest_dataset_metadata(DATASET) if calls else None
    columns=COLUMNS if tracking_id else SUMMARY_COLUMNS
    values=store.load_dataset_frame(metadata,columns=columns,symbols=[calls[0]['ticker']] if tracking_id else None) if metadata else pd.DataFrame()
    by_id={str(row['tracking_id']):row for row in values.to_dict(orient='records')} if not values.empty else {}
    results=[]
    for call in calls:
        row={key:call.get(key) for key in ('tracking_id','candidate_id','ticker','stance','horizon_key','horizon_label','approved_at_utc')}
        row.update(status='pending_entry' if not call.get('entry_payload') else 'updating',entry_policy=call['entry_policy'])
        performance = by_id.get(call['tracking_id'],{})
        if performance and performance.get('owner_id') != owner_id:
            raise TrackingUnavailable('Tracked call ownership does not match its publication.')
        row.update(performance)
        row.pop('owner_id',None)
        if tracking_id:
            try:
                row['path'] = json.loads(row.pop('path_json', '[]'))
            except (TypeError, ValueError):
                row['path'] = []
                row['path_status'] = 'unavailable'
            snapshot=call['candidate_payload'];snapshot=json.loads(snapshot) if isinstance(snapshot,str) else snapshot
            row['candidate']=snapshot
        results.append(to_jsonable(row))
    if not tracking_id:
        for row in results:
            row.pop('path_json', None)
    return {'status':'available','as_of':getattr(metadata,'asof_time_utc',''),'calls':results}
