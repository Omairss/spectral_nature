from __future__ import annotations

from datetime import datetime, timezone
import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services import auth_store


class _FakeCursor:
    def __init__(self) -> None:
        self.description = []
        self.executed: list[tuple[str, tuple[object, ...] | None]] = []
        self._rows: list[tuple[object, ...]] = []

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.executed.append((sql, params))
        self.description = [
            ("id",),
            ("email",),
            ("first_name",),
            ("last_name",),
            ("display_name",),
            ("status",),
            ("role",),
            ("last_login_at",),
            ("failed_login_count",),
            ("locked_until",),
            ("open_session_count",),
            ("active_session_count",),
            ("last_seen_at",),
        ]
        self._rows = [
            (
                "user-1",
                "investor@example.com",
                "Ivy",
                "Investor",
                "Ivy Investor",
                "active",
                "investor",
                datetime(2026, 4, 11, 18, 0, tzinfo=timezone.utc),
                2,
                None,
                3,
                1,
                datetime(2026, 4, 11, 18, 20, tzinfo=timezone.utc),
            )
        ]

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class _SequencedCursor:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses = list(responses)
        self._response_index = 0
        self.description = []
        self.executed: list[tuple[str, tuple[object, ...] | None]] = []
        self._rows: list[tuple[object, ...]] = []

    def __enter__(self) -> _SequencedCursor:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.executed.append((sql, params))
        response = self._responses[self._response_index]
        self._response_index += 1
        self.description = [(column,) for column in list(response.get("columns") or [])]
        self._rows = list(response.get("rows") or [])

    def fetchone(self) -> tuple[object, ...] | None:
        if not self._rows:
            return None
        return self._rows[0]

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.closed = False

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def test_list_users_aliases_users_table_and_merges_session_stats(monkeypatch):
    fake_cursor = _FakeCursor()
    fake_conn = _FakeConnection(fake_cursor)

    monkeypatch.setattr(auth_store, "_db_connect", lambda: fake_conn)
    monkeypatch.setattr(auth_store, "_schema_name", lambda: "app_access")
    monkeypatch.setattr(auth_store, "_now_utc", lambda: datetime(2026, 4, 11, 18, 30, tzinfo=timezone.utc))
    monkeypatch.setattr(auth_store, "_active_membership_for_user", lambda cursor, user_id: None)

    users = auth_store.list_users()

    assert fake_conn.closed is True
    assert len(fake_cursor.executed) == 1
    sql, params = fake_cursor.executed[0]
    assert "FROM app_access.users u" in sql
    assert "ON c.user_id = u.id" in sql
    assert "ON sess.user_id = u.id" in sql
    assert params is not None
    assert len(params) == 4
    assert len(users) == 1
    assert users[0]["user_id"] == "user-1"
    assert users[0]["email"] == "investor@example.com"
    assert users[0]["display_name"] == "Ivy Investor"
    assert users[0]["role"] == "investor"
    assert users[0]["status"] == "active"
    assert users[0]["portfolio_slug"] == ""
    assert users[0]["share_fraction"] == 0.0
    assert users[0]["can_view_full_portfolio"] is False
    assert users[0]["last_login_at"] == datetime(2026, 4, 11, 18, 0, tzinfo=timezone.utc)
    assert users[0]["failed_login_count"] == 2
    assert users[0]["locked_until"] is None
    assert users[0]["open_session_count"] == 3
    assert users[0]["active_session_count"] == 1
    assert users[0]["last_seen_at"] == datetime(2026, 4, 11, 18, 20, tzinfo=timezone.utc)


def test_get_access_admin_dashboard_filters_selected_user_and_hydrates_activity(monkeypatch):
    user_id = "11111111-1111-1111-1111-111111111111"
    now = datetime(2026, 4, 11, 19, 0, tzinfo=timezone.utc)
    cursor = _SequencedCursor(
        [
            {"columns": ["total_users"], "rows": [(3,)]},
            {"columns": ["open_session_count", "active_session_count", "active_user_count"], "rows": [(1, 1, 1)]},
            {"columns": ["locked_user_count"], "rows": [(0,)]},
            {"columns": ["pending_invite_count"], "rows": [(2,)]},
            {"columns": ["section_view_count", "login_success_count", "active_user_count"], "rows": [(4, 1, 1)]},
            {
                "columns": [
                    "failed_login_count",
                    "login_lock_count",
                    "password_reset_request_count",
                    "admin_password_reset_count",
                    "password_reset_complete_count",
                    "unique_ip_count",
                ],
                "rows": [(2, 1, 1, 0, 0, 1)],
            },
            {
                "columns": [
                    "user_id",
                    "email",
                    "display_name",
                    "role",
                    "status",
                    "last_login_at",
                    "open_session_count",
                    "active_session_count",
                    "last_seen_at",
                    "section_view_count",
                    "distinct_section_count",
                    "last_activity_at",
                    "top_section",
                    "top_section_view_count",
                ],
                "rows": [
                    (
                        user_id,
                        "investor@example.com",
                        "Ivy Investor",
                        "investor",
                        "active",
                        now,
                        1,
                        1,
                        now,
                        4,
                        2,
                        now,
                        "Home",
                        2,
                    )
                ],
            },
            {"columns": ["section_name", "view_count", "unique_user_count", "last_view_at"], "rows": [("Home", 4, 1, now)]},
            {
                "columns": [
                    "user_id",
                    "user_label",
                    "section_label",
                    "target_label",
                    "target_type",
                    "event_count",
                    "last_event_at",
                ],
                "rows": [
                    (
                        user_id,
                        "Ivy Investor",
                        "Home",
                        "AAPL",
                        "ticker",
                        3,
                        now,
                    ),
                    (
                        user_id,
                        "Ivy Investor",
                        "Home",
                        "",
                        "",
                        4,
                        now,
                    ),
                ],
            },
            {
                "columns": [
                    "id",
                    "user_id",
                    "email",
                    "display_name",
                    "created_at",
                    "last_seen_at",
                    "expires_at",
                    "ip_address",
                    "user_agent",
                    "is_active_now",
                ],
                "rows": [("session-1", user_id, "investor@example.com", "Ivy Investor", now, now, now, "1.2.3.4", "Agent", True)],
            },
            {
                "columns": [
                    "id",
                    "created_at",
                    "event_type",
                    "email",
                    "user_email",
                    "display_name",
                    "section_name",
                    "ip_address",
                    "user_agent",
                    "detail_json",
                ],
                "rows": [("evt-1", now, "login_failed", "investor@example.com", "investor@example.com", "Ivy Investor", "Home", "1.2.3.4", "Agent", '{"reason":"invalid_password"}')],
            },
            {"columns": ["target_label", "target_type", "event_count", "last_event_at"], "rows": [("AAPL", "ticker", 3, now)]},
            {
                "columns": [
                    "id",
                    "created_at",
                    "event_category",
                    "event_type",
                    "section_name",
                    "email",
                    "user_email",
                    "display_name",
                    "ip_address",
                    "user_agent",
                    "detail_json",
                    "target_label",
                    "target_type",
                ],
                "rows": [
                    (
                        "evt-2",
                        now,
                        "usage",
                        "ticker_open",
                        "Home",
                        "investor@example.com",
                        "investor@example.com",
                        "Ivy Investor",
                        "1.2.3.4",
                        "Agent",
                        '{"surface":"home_v2","target_label":"AAPL","target_type":"ticker"}',
                        "AAPL",
                        "ticker",
                    )
                ],
            },
        ]
    )
    fake_conn = _FakeConnection(cursor)

    monkeypatch.setattr(auth_store, "_db_connect", lambda: fake_conn)
    monkeypatch.setattr(auth_store, "_schema_name", lambda: "app_access")
    monkeypatch.setattr(auth_store, "_now_utc", lambda: now)
    monkeypatch.setattr(auth_store, "_ensure_schema", lambda conn: None)

    dashboard = auth_store.get_access_admin_dashboard(
        usage_window_days=14,
        security_window_days=7,
        active_window_minutes=30,
        recent_event_limit=20,
        user_id=user_id,
        user_email="investor@example.com",
    )

    assert fake_conn.closed is True
    assert dashboard["filtered_user_id"] == user_id
    assert dashboard["filtered_user_email"] == "investor@example.com"
    assert dashboard["summary"]["section_views_window"] == 4
    assert dashboard["summary"]["failed_logins_window"] == 2
    assert dashboard["user_usage"][0]["user_id"] == user_id
    assert dashboard["usage_sankey"][0]["user_label"] == "Ivy Investor"
    assert dashboard["usage_sankey"][0]["target_label"] == "AAPL"
    assert dashboard["selected_user_targets"][0]["target_label"] == "AAPL"
    assert dashboard["selected_user_targets"][0]["event_count"] == 3
    assert dashboard["selected_user_activity"][0]["target_label"] == "AAPL"
    assert dashboard["selected_user_activity"][0]["detail"]["surface"] == "home_v2"
    assert len(cursor.executed) == 13
    assert "WHERE user_id = %s::uuid" in cursor.executed[1][0]
    assert user_id in tuple(cursor.executed[5][1] or ())
    assert "investor@example.com" in tuple(cursor.executed[5][1] or ())
