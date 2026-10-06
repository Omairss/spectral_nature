#!/usr/bin/env python3
"""Copy app_access into an isolated PostgreSQL control-plane database.

Connection strings and the optional application-role password are accepted only
through environment variables so they do not enter shell history or process
arguments.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

import psycopg
from psycopg import sql


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services import auth_store, brokerage_mcp_store


CONTROL_ROLE = "sn_control_app"


def _required_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _schema_name() -> str:
    value = (os.getenv("APP_ACCESS_SCHEMA") or "app_access").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise RuntimeError("APP_ACCESS_SCHEMA is invalid")
    return value


def _tables_and_dependencies(connection: Any, schema: str) -> tuple[list[str], dict[str, set[str]]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename",
            (schema,),
        )
        tables = [str(row[0]) for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT tc.table_name, ccu.table_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_schema=tc.constraint_schema
             AND ccu.constraint_name=tc.constraint_name
            WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema=%s
            """,
            (schema,),
        )
        dependencies = {table: set() for table in tables}
        for child, parent in cursor.fetchall():
            if child != parent and child in dependencies and parent in dependencies:
                dependencies[str(child)].add(str(parent))
    return tables, dependencies


def _dependency_order(tables: list[str], dependencies: dict[str, set[str]]) -> list[str]:
    ordered: list[str] = []
    remaining = set(tables)
    while remaining:
        ready = sorted(table for table in remaining if not (dependencies[table] & remaining))
        if not ready:
            raise RuntimeError(f"Cyclic cross-table dependencies: {sorted(remaining)}")
        ordered.extend(ready)
        remaining.difference_update(ready)
    return ordered


def _table_columns(connection: Any, schema: str, table: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema=%s AND table_name=%s
            ORDER BY ordinal_position
            """,
            (schema, table),
        )
        return [str(row[0]) for row in cursor.fetchall()]


def _copy_table(source: Any, target: Any, schema: str, table: str) -> int:
    columns = _table_columns(source, schema, table)
    target_columns = set(_table_columns(target, schema, table))
    missing = [column for column in columns if column not in target_columns]
    if missing:
        raise RuntimeError(f"Target table {table} is missing columns: {missing}")

    column_sql = sql.SQL(", ").join(map(sql.Identifier, columns))
    order_sql = sql.SQL(" ORDER BY created_at NULLS FIRST") if "created_at" in columns else sql.SQL("")
    copy_out_sql = sql.SQL("COPY (SELECT {} FROM {}.{}{}) TO STDOUT").format(
        column_sql,
        sql.Identifier(schema),
        sql.Identifier(table),
        order_sql,
    )
    copy_in_sql = sql.SQL("COPY {}.{} ({}) FROM STDIN").format(
        sql.Identifier(schema),
        sql.Identifier(table),
        column_sql,
    )
    with source.cursor() as source_cursor, target.cursor() as target_cursor:
        with source_cursor.copy(copy_out_sql) as copy_out, target_cursor.copy(copy_in_sql) as copy_in:
            for chunk in copy_out:
                copy_in.write(chunk)
        source_cursor.execute(
            sql.SQL("SELECT count(*) FROM {}.{}").format(
                sql.Identifier(schema),
                sql.Identifier(table),
            )
        )
        return int(source_cursor.fetchone()[0])


def _provision_role(connection: Any, schema: str) -> None:
    password = _required_env("CONTROL_APP_PASSWORD")
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (CONTROL_ROLE,))
        role_identifier = sql.Identifier(CONTROL_ROLE)
        if cursor.fetchone():
            cursor.execute(
                sql.SQL(
                    "ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOREPLICATION CONNECTION LIMIT 20 PASSWORD {}"
                ).format(role_identifier, sql.Literal(password))
            )
        else:
            cursor.execute(
                sql.SQL(
                    "CREATE ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOREPLICATION CONNECTION LIMIT 20 PASSWORD {}"
                ).format(role_identifier, sql.Literal(password))
            )

        cursor.execute("REVOKE CONNECT ON DATABASE pipeline FROM PUBLIC")
        cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE pipeline TO {}").format(role_identifier))
        cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        cursor.execute(
            sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(
                sql.Identifier(schema),
                role_identifier,
            )
        )
        cursor.execute(
            """
            SELECT c.relname, c.relkind
            FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relkind IN ('r','p','S','v','m','f')
            ORDER BY c.relkind,c.relname
            """,
            (schema,),
        )
        relation_commands = {
            "r": "TABLE",
            "p": "TABLE",
            "S": "SEQUENCE",
            "v": "VIEW",
            "m": "MATERIALIZED VIEW",
            "f": "FOREIGN TABLE",
        }
        for name, kind in cursor.fetchall():
            cursor.execute(
                sql.SQL("ALTER {} {}.{} OWNER TO {}").format(
                    sql.SQL(relation_commands[str(kind)]),
                    sql.Identifier(schema),
                    sql.Identifier(str(name)),
                    role_identifier,
                )
            )
        cursor.execute(
            sql.SQL("GRANT USAGE, CREATE ON SCHEMA {} TO {}").format(
                sql.Identifier(schema), role_identifier
            )
        )
        cursor.execute(
            sql.SQL("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA {} TO {}").format(
                sql.Identifier(schema), role_identifier
            )
        )
        cursor.execute(
            sql.SQL("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {} TO {}").format(
                sql.Identifier(schema), role_identifier
            )
        )
        cursor.execute(
            sql.SQL("ALTER ROLE {} SET search_path TO {}, pg_catalog").format(
                role_identifier, sql.Identifier(schema)
            )
        )
        cursor.execute(sql.SQL("ALTER ROLE {} SET statement_timeout TO '15s'").format(role_identifier))
        cursor.execute(sql.SQL("ALTER ROLE {} SET lock_timeout TO '3s'").format(role_identifier))
        cursor.execute(
            sql.SQL("ALTER ROLE {} SET idle_in_transaction_session_timeout TO '10s'").format(
                role_identifier
            )
        )


def migrate(*, provision_role: bool) -> dict[str, Any]:
    source = psycopg.connect(_required_env("SOURCE_DSN"), connect_timeout=10)
    target = psycopg.connect(_required_env("TARGET_DSN"), connect_timeout=10)
    schema = _schema_name()
    try:
        source.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with target.transaction():
            auth_store.ensure_auth_schema_on_connection(target)
            brokerage_mcp_store._ensure_schema(target)

        tables, dependencies = _tables_and_dependencies(source, schema)
        if not tables:
            raise RuntimeError(f"Source schema {schema} has no tables")
        ordered = _dependency_order(tables, dependencies)

        with target.transaction():
            with target.cursor() as cursor:
                relations = sql.SQL(", ").join(
                    sql.Identifier(schema, table) for table in tables
                )
                cursor.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(relations))
            source_counts = {
                table: _copy_table(source, target, schema, table) for table in ordered
            }
            if provision_role:
                _provision_role(target, schema)

            target_counts: dict[str, int] = {}
            with target.cursor() as cursor:
                for table in tables:
                    cursor.execute(
                        sql.SQL("SELECT count(*) FROM {}.{}").format(
                            sql.Identifier(schema),
                            sql.Identifier(table),
                        )
                    )
                    target_counts[table] = int(cursor.fetchone()[0])
            mismatches = {
                table: [source_counts[table], target_counts[table]]
                for table in tables
                if source_counts[table] != target_counts[table]
            }
            if mismatches:
                raise RuntimeError(f"Control copy count mismatch: {mismatches}")
        source.commit()
        return {
            "tables_copied": len(tables),
            "rows_copied": sum(source_counts.values()),
            "counts_match": True,
            "users": source_counts.get("users", 0),
        }
    finally:
        source.close()
        target.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provision-role", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(provision_role=bool(args.provision_role)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
