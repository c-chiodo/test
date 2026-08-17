"""Database access.

A thin layer over sqlite3: connection management, schema creation, and helpers
that return plain dicts. Deliberately not an ORM — the queries in the service
layer are the readable, reviewable statement of what the system does, which is
exactly what the legacy stored procedures were not (business logic there was
invisible to anyone without VIEW DEFINITION on the production server).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import PACKAGE_DIR, Settings, get_settings

_local = threading.local()


def _row_to_dict(cursor: sqlite3.Cursor, row: tuple) -> dict[str, Any]:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def connect(settings: Settings | None = None) -> sqlite3.Connection:
    """Open a new connection with the conventions PIMS relies on."""

    settings = settings or get_settings()
    path = settings.sqlite_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = _row_to_dict
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def get_connection() -> sqlite3.Connection:
    """Return this thread's connection, creating it on first use."""

    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = connect()
        _local.conn = conn
    return conn


def close_connection() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


@contextmanager
def transaction(conn: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
    """Run a unit of work atomically.

    Nested use is safe: an inner block joins the outer transaction rather than
    committing early, so a service method can call another without splitting
    the write into two half-applied pieces.
    """

    conn = conn or get_connection()
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def query(sql: str, params: Sequence[Any] | dict[str, Any] = (), conn=None) -> list[dict]:
    conn = conn or get_connection()
    return list(conn.execute(sql, params))


def query_one(
    sql: str, params: Sequence[Any] | dict[str, Any] = (), conn=None
) -> dict | None:
    rows = query(sql, params, conn)
    return rows[0] if rows else None


def scalar(sql: str, params: Sequence[Any] | dict[str, Any] = (), conn=None) -> Any:
    row = query_one(sql, params, conn)
    if row is None:
        return None
    return next(iter(row.values()))


def execute(sql: str, params: Sequence[Any] | dict[str, Any] = (), conn=None) -> int:
    """Run a statement; return lastrowid (or rowcount for UPDATE/DELETE)."""

    conn = conn or get_connection()
    cur = conn.execute(sql, params)
    return cur.lastrowid if cur.lastrowid else cur.rowcount


def executemany(sql: str, rows: Iterable[Sequence[Any]], conn=None) -> None:
    conn = conn or get_connection()
    conn.executemany(sql, rows)


def insert(table: str, values: dict[str, Any], conn=None) -> int:
    cols = ", ".join(f'"{c}"' for c in values)
    marks = ", ".join("?" for _ in values)
    return execute(
        f'INSERT INTO "{table}" ({cols}) VALUES ({marks})', list(values.values()), conn
    )


def update(table: str, key: dict[str, Any], values: dict[str, Any], conn=None) -> int:
    sets = ", ".join(f'"{c}" = ?' for c in values)
    where = " AND ".join(f'"{c}" = ?' for c in key)
    return execute(
        f'UPDATE "{table}" SET {sets} WHERE {where}',
        [*values.values(), *key.values()],
        conn,
    )


def schema_sql() -> str:
    return (PACKAGE_DIR / "schema.sql").read_text(encoding="utf-8")


def create_schema(conn: sqlite3.Connection | None = None) -> None:
    conn = conn or get_connection()
    conn.executescript(schema_sql())


def table_names(conn: sqlite3.Connection | None = None) -> list[str]:
    rows = query(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name", (), conn
    )
    return [r["name"] for r in rows]


def is_empty(conn: sqlite3.Connection | None = None) -> bool:
    conn = conn or get_connection()
    if "plant" not in table_names(conn):
        return True
    return scalar("SELECT COUNT(*) FROM plant", (), conn) == 0


def init_db(settings: Settings | None = None, seed: bool | None = None) -> None:
    """Create the schema and, for an empty database, load the demo dataset."""

    settings = settings or get_settings()
    conn = get_connection()
    create_schema(conn)
    should_seed = settings.auto_seed if seed is None else seed
    if should_seed and is_empty(conn):
        from . import seed as seed_module

        seed_module.seed_all(conn)


def database_file(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).sqlite_path
