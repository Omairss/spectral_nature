"""Real PostgreSQL ownership/freshness checks in a disposable private schema."""
from datetime import datetime, timedelta, timezone
import os
import uuid

import psycopg
import pytest

from services import auth_store, brokerage_mcp_store as store


@pytest.mark.skipif(not os.getenv('PAPER_INTEGRATION_DATABASE_URL'), reason='An explicitly configured integration database is required.')
def test_broker_snapshots_keep_identity_fresh_components_and_one_use_confirmation(monkeypatch):
    dsn = os.environ['PAPER_INTEGRATION_DATABASE_URL']
    schema = 'paper_qa_' + uuid.uuid4().hex[:10]
    monkeypatch.setenv('APP_ACCESS_SCHEMA', schema)
    monkeypatch.setattr(auth_store, 'open_auth_store_connection', lambda: psycopg.connect(dsn))
    now = datetime.now(timezone.utc)
    owner, foreign, portfolio = (str(uuid.uuid4()) for _ in range(3))
    try:
        assert store.ensure_brokerage_mcp_schema()
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                for identity, email in [(owner, 'owner@example.invalid'), (foreign, 'foreign@example.invalid')]:
                    cur.execute(f'''INSERT INTO {schema}.users
                        (id,email,first_name,last_name,status,role,created_at,updated_at)
                        VALUES(%s,%s,'QA','Owner','active','admin',%s,%s)''', (identity, email, now, now))
                cur.execute(f'''INSERT INTO {schema}.portfolios
                    (id,slug,name,brokerage_source,status,created_at,updated_at)
                    VALUES(%s,'qa','QA','unlinked','active',%s,%s)''', (portfolio, now, now))
                cur.execute(f'''INSERT INTO {schema}.portfolio_memberships
                    (id,user_id,portfolio_id,role,share_fraction,effective_from,created_at,updated_at)
                    VALUES(%s,%s,%s,'admin',1,%s,%s,%s)''', (str(uuid.uuid4()), owner, portfolio, now-timedelta(days=1), now, now))
                cur.execute(f'''ALTER TABLE {schema}.brokerage_portfolio_snapshots
                    ADD CONSTRAINT brokerage_portfolio_snapshots_portfolio_id_environment_key UNIQUE(portfolio_id,environment)''')
        assert auth_store.user_has_active_portfolio_admin_membership(user_id=owner, portfolio_id=portfolio)
        with psycopg.connect(dsn) as conn:
            conn.execute(f"UPDATE {schema}.users SET status='disabled' WHERE id=%s", (owner,))
        assert not auth_store.user_has_active_portfolio_admin_membership(user_id=owner, portfolio_id=portfolio)
        with psycopg.connect(dsn) as conn:
            conn.execute(f"UPDATE {schema}.users SET status='active' WHERE id=%s", (owner,))
        links = []
        for provider in ['alpaca_paper', 'other_broker']:
            links.append(store.save_direct_connection(user_id=owner, provider=provider, resource_url='https://example.invalid',
                environment='development', access_token_ciphertext='', refresh_token_ciphertext='', device_token_ciphertext='',
                token_type='key_vault', scope='', token_expires_at=None))
        def save(link, minute, equity, positions, position_minute, errors=None):
            asof = now + timedelta(minutes=minute)
            return store.save_portfolio_snapshot(portfolio_id=portfolio, connection_id=link['id'], provider=link['provider'],
                environment='development', account_summary={'equity': equity}, positions=positions, timeseries=[],
                source_updated_at=asof, details={'coverage': {'account': asof.isoformat(),
                    'positions': (now + timedelta(minutes=position_minute)).isoformat()}, 'errors': errors or {}})
        save(links[0], 3, 103000, [], 0, {'positions': 'TimeoutError'})
        save(links[1], 1, 123, [{'symbol': 'OTHER'}], 1)
        late = save(links[0], 2, 102000, [{'symbol': 'A', 'qty': '2'}], 2)
        assert late['account_summary']['equity'] == 103000
        assert late['positions'] == [{'symbol': 'A', 'qty': '2'}]
        assert late['details']['errors'] == {'positions': 'TimeoutError'}
        save(links[0], 4, 104000, [], 4)
        stale = save(links[0], 1, 101000, [{'symbol': 'STALE'}], 1)
        assert stale['account_summary']['equity'] == 104000
        assert stale['positions'] == []  # A new successful empty book is real.
        assert stale['details']['errors'] == {}
        for link in links:
            args = dict(portfolio_id=portfolio, connection_id=link['id'], user_id=owner, environment='development')
            assert store.get_portfolio_snapshot(**args)['provider'] == link['provider']
            assert store.get_portfolio_snapshot(**{**args, 'user_id': foreign}) is None
            assert store.get_portfolio_snapshot(**{**args, 'environment': 'production'}) is None
        ticket = store.create_order_ticket(connection_id=links[0]['id'], user_id=owner, provider='alpaca_paper',
            environment='development', review_tool_name='review', execution_tool_name='submit', arguments_hash='a'*64,
            ref_id=str(uuid.uuid4()), request_summary={'order': {'qty': '1'}}, expires_at=datetime.now(timezone.utc)+timedelta(minutes=2))
        args = dict(ticket_id=ticket['id'], user_id=owner, provider='alpaca_paper', environment='development', arguments_hash='a'*64)
        assert store.consume_order_ticket(**{**args, 'user_id': foreign}) is None
        assert store.consume_order_ticket(**args)
        assert store.consume_order_ticket(**args) is None
        store.finish_order_ticket(ticket_id=ticket['id'], status='unknown', result_summary={'client_order_id': 'retained'})
        read = {key: value for key, value in args.items() if key != 'arguments_hash'}
        assert store.get_order_ticket(**read)['status'] == 'unknown'
        store.finish_order_ticket(ticket_id=ticket['id'], status='failed')
        assert store.get_order_ticket(**read)['status'] == 'unknown'
        store.finish_order_ticket(ticket_id=ticket['id'], status='executed', result_summary={'order': {'id': 'provider-proof'}})
        assert store.get_order_ticket(**read)['result_summary']['order']['id'] == 'provider-proof'
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
