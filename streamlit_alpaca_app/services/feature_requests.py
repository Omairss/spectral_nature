"""User-owned product requests and follow-up messages, separate from research."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from services import auth_service, pipeline_store


class FeatureRequestUnavailable(RuntimeError):
    pass


class FeatureRequestNotFound(LookupError):
    pass


class FeatureRequestConflict(ValueError):
    pass


class FeatureRequestRepository:
    def __init__(self, connection: Any, context: auth_service.UserContext) -> None:
        self.connection = connection
        self.context = context
        self.owner_id = str(UUID(context.user_id))
        self.environment = pipeline_store.dataset_namespace() or "production"
        self._bootstrap()

    @classmethod
    @contextmanager
    def open(cls, context: auth_service.UserContext) -> Iterator["FeatureRequestRepository"]:
        connection = pipeline_store.open_database_connection()
        if connection is None:
            raise FeatureRequestUnavailable("Feature requests are unavailable.")
        try:
            yield cls(connection, context)
        except psycopg.Error as exc:
            raise FeatureRequestUnavailable("Feature requests are unavailable.") from exc
        finally:
            connection.close()

    def _bootstrap(self) -> None:
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('app_feature_request_messages')")
            if cursor.fetchone()[0] is not None:
                return
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('app_feature_requests_schema_v1'))")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS app_feature_requests (
                    environment TEXT NOT NULL,
                    request_id UUID NOT NULL,
                    owner_id UUID NOT NULL,
                    owner_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    client TEXT NOT NULL,
                    app_version TEXT NOT NULL,
                    context TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (environment, request_id)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS app_feature_request_messages (
                    environment TEXT NOT NULL,
                    message_id UUID NOT NULL,
                    request_id UUID NOT NULL,
                    author_id UUID NOT NULL,
                    author_name TEXT NOT NULL,
                    author_role TEXT NOT NULL CHECK (author_role IN ('user', 'admin')),
                    body TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (environment, message_id),
                    FOREIGN KEY (environment, request_id)
                        REFERENCES app_feature_requests(environment, request_id) ON DELETE CASCADE
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS app_feature_requests_owner_updated ON app_feature_requests(environment, owner_id, updated_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS app_feature_request_messages_thread ON app_feature_request_messages(environment, request_id, created_at, message_id)")

    @staticmethod
    def _text(value: str, *, maximum: int, required: bool = False) -> str:
        value = str(value or "").strip()
        if required and not value:
            raise ValueError("Enter a title and message.")
        if len(value) > maximum:
            raise ValueError(f"Text must not exceed {maximum} characters.")
        return value

    def list_requests(self, *, all_requests: bool = False, limit: int = 100) -> dict[str, Any]:
        if all_requests and not self.context.is_admin:
            raise PermissionError("Admin role required.")
        with self.connection.transaction(), self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""
                SELECT r.request_id::text AS id, r.owner_id::text AS owner_id,
                       r.owner_name, r.title, r.client, r.app_version, r.context,
                       r.created_at, r.updated_at,
                       (SELECT COUNT(*) FROM app_feature_request_messages m
                        WHERE m.environment=r.environment AND m.request_id=r.request_id) AS message_count
                FROM app_feature_requests r
                WHERE r.environment=%s AND (r.owner_id=%s OR %s)
                ORDER BY r.updated_at DESC, r.request_id
                LIMIT %s
            """, (self.environment, self.owner_id, all_requests, max(1, min(limit, 100))))
            return {"can_review_all": self.context.is_admin, "requests": cursor.fetchall()}

    def _request(self, cursor: Any, request_id: str, *, lock: bool = False) -> dict[str, Any]:
        cursor.execute("""
            SELECT request_id::text AS id, owner_id::text AS owner_id, owner_name,
                   title, client, app_version, context, created_at, updated_at
            FROM app_feature_requests
            WHERE environment=%s AND request_id=%s AND (owner_id=%s OR %s)
        """ + (" FOR UPDATE" if lock else ""),
            (self.environment, str(UUID(request_id)), self.owner_id, self.context.is_admin))
        row = cursor.fetchone()
        if row is None:
            raise FeatureRequestNotFound("Feature request not found.")
        return row

    def get_request(self, request_id: str) -> dict[str, Any]:
        with self.connection.transaction(), self.connection.cursor(row_factory=dict_row) as cursor:
            request = self._request(cursor, request_id)
            cursor.execute("""
                SELECT message_id::text AS id, author_id::text AS author_id,
                       author_name, author_role, body, created_at
                FROM app_feature_request_messages
                WHERE environment=%s AND request_id=%s
                ORDER BY created_at, message_id
            """, (self.environment, request["id"]))
            request["messages"] = cursor.fetchall()
            request["message_count"] = len(request["messages"])
            return request

    def create_request(self, *, request_id: str, title: str, body: str,
                       client: str = "", app_version: str = "", context: str = "") -> dict[str, Any]:
        request_id = str(UUID(request_id))
        title = self._text(title, maximum=200, required=True)
        body = self._text(body, maximum=8000, required=True)
        client = self._text(client, maximum=40)
        app_version = self._text(app_version, maximum=80)
        context = self._text(context, maximum=400)
        with self.connection.transaction(), self.connection.cursor(row_factory=dict_row) as cursor:
            now = datetime.now(timezone.utc)
            cursor.execute("""
                INSERT INTO app_feature_requests
                    (environment, request_id, owner_id, owner_name, title, client,
                     app_version, context, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (environment, request_id) DO NOTHING RETURNING request_id
            """, (self.environment, request_id, self.owner_id, self.context.label, title,
                  client, app_version, context, now, now))
            if cursor.fetchone() is not None:
                if not self._insert_message(cursor, request_id=request_id, message_id=request_id, body=body, now=now):
                    raise FeatureRequestConflict("This request ID is already in use. Start a new request.")
            else:
                existing = self._request(cursor, request_id, lock=True)
                cursor.execute("SELECT body FROM app_feature_request_messages WHERE environment=%s AND message_id=%s AND request_id=%s", (self.environment, request_id, request_id))
                initial = cursor.fetchone()
                if existing["owner_id"] != self.owner_id or existing["title"] != title or initial is None or initial["body"] != body:
                    raise FeatureRequestConflict("This request was already submitted. Open it to add a follow-up.")
            return self.get_request(request_id)

    def _insert_message(self, cursor: Any, *, request_id: str, message_id: str, body: str, now: datetime) -> bool:
        cursor.execute("""
            INSERT INTO app_feature_request_messages
                (environment, message_id, request_id, author_id, author_name, author_role, body, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (environment, message_id) DO NOTHING RETURNING message_id
        """, (self.environment, message_id, request_id, self.owner_id,
              self.context.label, "admin" if self.context.is_admin else "user", body, now))
        return cursor.fetchone() is not None

    def add_message(self, request_id: str, *, message_id: str, body: str) -> dict[str, Any]:
        request_id = str(UUID(request_id))
        message_id = str(UUID(message_id))
        body = self._text(body, maximum=8000, required=True)
        with self.connection.transaction(), self.connection.cursor(row_factory=dict_row) as cursor:
            self._request(cursor, request_id, lock=True)
            now = datetime.now(timezone.utc)
            if self._insert_message(cursor, request_id=request_id, message_id=message_id, body=body, now=now):
                cursor.execute("UPDATE app_feature_requests SET updated_at=%s WHERE environment=%s AND request_id=%s", (now, self.environment, request_id))
            else:
                cursor.execute("SELECT request_id::text AS request_id, author_id::text AS author_id, body FROM app_feature_request_messages WHERE environment=%s AND message_id=%s", (self.environment, message_id))
                previous = cursor.fetchone()
                if previous is None or previous["request_id"] != request_id or previous["author_id"] != self.owner_id or previous["body"] != body:
                    raise FeatureRequestConflict("This message was already sent with different content. Refresh the request before sending again.")
            return self.get_request(request_id)
