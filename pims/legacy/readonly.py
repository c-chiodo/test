"""A connection that can only read.

Wraps any DB-API connection (pyodbc against SQL Server in production, sqlite3
in the tests) and refuses, before the driver sees it, any statement that is not
a single ``SELECT`` or ``WITH``. The login should already be read-only; this is
the lock on the inside of the door, so a mistake in this codebase — a stray
``INTO``, a ``;`` followed by something else — fails loudly here rather than
becoming a question for the database permissions.

It is also built to be invisible to production:

* **No locks.** Reads run at READ UNCOMMITTED, so the companion takes no
  shared locks and can never make an operator's save in the PIMS desktop app
  wait. The cost is that a read can catch a row mid-edit; the mirror re-reads
  its recent window on every pass, so such a row is corrected within one sync.
* **Always yields.** ``DEADLOCK_PRIORITY LOW`` and a lock timeout mean that in
  any contention the companion is the party that gives up.
* **Gentle.** One connection, autocommit (no open transaction), small batches
  with a pause between them, a statement timeout, and no ORDER BY or UNION on
  large tables — nothing that sorts or spools in production's tempdb.
* **Refuses a writable login.** :func:`preflight` asks the server what this
  login may do; any write, DDL or EXECUTE permission stops the companion
  before it reads a single row. A misconfigured admin login fails safe.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from ..errors import IntegrationError

#: Words that have no business in a read. Checked as whole tokens outside
#: string literals, comments and [bracketed] identifiers — so a column named
#: [Delete_flag] or a remark containing 'update' does not trip it.
FORBIDDEN = frozenset(
    """
    INSERT UPDATE DELETE MERGE UPSERT REPLACE TRUNCATE
    CREATE ALTER DROP RENAME
    GRANT REVOKE DENY
    EXEC EXECUTE CALL
    INTO
    DECLARE SET USE GO
    BACKUP RESTORE DBCC KILL SHUTDOWN RECONFIGURE CHECKPOINT
    BULK OPENROWSET OPENQUERY OPENDATASOURCE OPENXML
    WRITETEXT UPDATETEXT READTEXT
    WAITFOR
    ATTACH DETACH PRAGMA VACUUM
    """.split()
)

_STRING = re.compile(r"'(?:[^']|'')*'")
_BRACKET = re.compile(r"\[[^\]]*\]")
_QUOTED = re.compile(r'"(?:[^"]|"")*"')
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_$#@]*")


class WriteRefused(IntegrationError):
    """A statement that could change the legacy database was stopped here."""

    code = "legacy_read_only"
    status = 500


def _strip(sql: str) -> str:
    """Remove everything that is data or commentary rather than SQL."""

    for pattern in (_BLOCK_COMMENT, _LINE_COMMENT, _STRING, _BRACKET, _QUOTED):
        sql = pattern.sub(" ", sql)
    return sql


def check_read_only(sql: str) -> None:
    """Raise :class:`WriteRefused` unless ``sql`` is a single read."""

    body = _strip(sql).strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    if ";" in body:
        raise WriteRefused(
            "Refused a batch of more than one statement against the legacy "
            "database. The companion only ever sends single SELECTs.",
            statement=sql[:200],
        )
    tokens = [t.upper() for t in _TOKEN.findall(body)]
    if not tokens or tokens[0] not in {"SELECT", "WITH"}:
        raise WriteRefused(
            "Refused a statement that is not a SELECT. The companion is "
            "read-only against the legacy database.",
            statement=sql[:200],
        )
    bad = sorted({t for t in tokens if t in FORBIDDEN or t.startswith(("SP_", "XP_"))})
    if bad:
        raise WriteRefused(
            f"Refused a statement containing {', '.join(bad)}. The companion is "
            "read-only against the legacy database.",
            statement=sql[:200],
            words=bad,
        )


@dataclass
class ReadOnlyConnection:
    """The only way this codebase talks to the legacy database."""

    raw: Any
    dialect: str = "mssql"          # mssql | sqlite (tests)
    batch_size: int = 2_000
    #: Pause between batches, so a large read is a trickle rather than a burst.
    pause_seconds: float = 0.0

    def _cursor(self, sql: str, params: Sequence[Any]):
        check_read_only(sql)
        cursor = self.raw.cursor()
        cursor.execute(sql, tuple(params))
        return cursor

    def rows(self, sql: str, params: Sequence[Any] = ()) -> Iterator[dict[str, Any]]:
        """Stream rows as dicts, a batch at a time — large tables are never
        pulled into memory whole, and the cursor is closed on exit."""

        cursor = self._cursor(sql, params)
        try:
            names = [d[0] for d in cursor.description]
            while True:
                batch = cursor.fetchmany(self.batch_size)
                if not batch:
                    return
                for row in batch:
                    yield dict(zip(names, row))
                if self.pause_seconds:
                    time.sleep(self.pause_seconds)
        finally:
            cursor.close()

    def all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return list(self.rows(sql, params))

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        cursor = self._cursor(sql, params)
        try:
            row = cursor.fetchone()
            return row[0] if row else None
        finally:
            cursor.close()

    def columns(self, table: str) -> list[str] | None:
        """The columns of ``table``, or None if it cannot be read.

        ``SELECT * … WHERE 1 = 0`` reads no rows and takes no meaningful locks,
        and works identically on SQL Server and SQLite — no catalog views, no
        permissions beyond SELECT on the table itself.
        """

        try:
            cursor = self._cursor(f"SELECT * FROM {table} WHERE 1 = 0", ())
        except WriteRefused:
            raise
        except Exception:                                   # noqa: BLE001
            return None
        try:
            return [d[0] for d in cursor.description]
        finally:
            cursor.close()

    def close(self) -> None:
        self.raw.close()


#: Permissions a companion login must NOT hold, checked with
#: HAS_PERMS_BY_NAME against the database. Any one of them means the login
#: could change production, and the companion will not run with it.
DANGEROUS_PERMISSIONS = (
    "INSERT", "UPDATE", "DELETE", "ALTER", "EXECUTE",
    "CREATE TABLE", "CREATE PROCEDURE", "CONTROL",
)

#: Roles that imply write or DDL rights.
DANGEROUS_ROLES = ("db_owner", "db_datawriter", "db_ddladmin", "db_securityadmin")


def preflight(
    connection: ReadOnlyConnection, tables: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Ask the server what this login can do; refuse anything beyond reading.

    Runs before every sync. The same questions ``read_procs.sql`` asks, as a
    gate rather than a report: the companion reads only if the answer is
    "select, and nothing else". Every statement here is a plain SELECT of
    built-in functions and passes the guard like any other read.
    """

    if connection.dialect != "mssql":
        return {"dialect": connection.dialect, "checked": False, "ok": True}

    login = connection.scalar("SELECT SUSER_SNAME()")
    database = connection.scalar("SELECT DB_NAME()")
    sysadmin = connection.scalar("SELECT IS_SRVROLEMEMBER('sysadmin')")
    roles = {
        role: connection.scalar("SELECT IS_MEMBER(?)", (role,)) for role in DANGEROUS_ROLES
    }
    perms = {
        perm: connection.scalar(
            "SELECT HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', ?)", (perm,)
        )
        for perm in DANGEROUS_PERMISSIONS
    }
    can_select = connection.scalar("SELECT HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'SELECT')")

    # A grant on one table does not show at database level, so ask about each
    # table the companion reads, too.
    object_grants = []
    for table in tables:
        name = table.replace("[", "").replace("]", "")
        for perm in ("INSERT", "UPDATE", "DELETE", "ALTER"):
            if connection.scalar("SELECT HAS_PERMS_BY_NAME(?, 'OBJECT', ?)", (name, perm)) == 1:
                object_grants.append(f"holds {perm} on {name}")

    problems = []
    if sysadmin == 1:
        problems.append("is a sysadmin")
    problems += [f"is a member of {role}" for role, member in roles.items() if member == 1]
    problems += [f"holds {perm}" for perm, held in perms.items() if held == 1]
    problems += object_grants

    report = {
        "dialect": "mssql",
        "checked": True,
        "login": login,
        "database": database,
        "can_select": can_select == 1,
        "problems": problems,
        "ok": not problems,
    }
    if problems:
        raise WriteRefused(
            f"The login {login} on {database} can change the database — it "
            + "; ".join(problems)
            + ". The companion only runs with a login that can read and nothing "
            "else. Ask the DBA for a login in db_datareader only (or point the "
            "companion at a restored copy or readable secondary).",
            **report,
        )
    return report


def connect_mssql(
    dsn: str, timeout_seconds: int = 60, tables: tuple[str, ...] = ()
) -> ReadOnlyConnection:
    """Open the production connection. Requires pyodbc and a read-only login.

    ``dsn`` is an ODBC connection string, e.g.::

        Driver={ODBC Driver 18 for SQL Server};Server=FESQLPROD01\\PRODUCTION;
        Database=ProductionData;Trusted_Connection=yes;Encrypt=yes;
        TrustServerCertificate=yes

    ``ApplicationIntent=ReadOnly`` is appended if absent.
    """

    try:
        import pyodbc  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on the host
        raise IntegrationError(
            "pyodbc is not installed. `pip install pyodbc` and install "
            "'ODBC Driver 18 for SQL Server' to read the legacy database.",
            adapter="legacy",
        ) from exc

    if "applicationintent" not in dsn.lower():
        dsn = dsn.rstrip(";") + ";ApplicationIntent=ReadOnly"
    raw = pyodbc.connect(dsn, autocommit=True, readonly=True, timeout=30)  # pragma: no cover
    raw.timeout = timeout_seconds                                            # pragma: no cover
    # Session settings, sent directly rather than through the guard (which
    # refuses SET by design). None of them writes anything; each one makes the
    # companion yield to the production application instead of competing.
    cursor = raw.cursor()                                                    # pragma: no cover
    cursor.execute("SET LOCK_TIMEOUT 5000")                                  # pragma: no cover
    cursor.execute("SET DEADLOCK_PRIORITY LOW")                              # pragma: no cover
    cursor.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")       # pragma: no cover
    cursor.close()                                                           # pragma: no cover
    connection = ReadOnlyConnection(raw, dialect="mssql", pause_seconds=0.05)  # pragma: no cover
    try:                                                                     # pragma: no cover
        preflight(connection, tables)                                        # pragma: no cover
    except Exception:                                                        # pragma: no cover
        connection.close()                                                   # pragma: no cover
        raise                                                                # pragma: no cover
    return connection                                                        # pragma: no cover


def connect_sqlite(path: str, users_path: str | None = None) -> ReadOnlyConnection:
    """A stand-in for ProductionData, for tests and rehearsals.

    The file is attached as schema ``dbo`` so the legacy names (``dbo.[Order]``)
    resolve unchanged, and it is opened with SQLite's own ``mode=ro`` — so
    the stand-in refuses writes at the storage layer too, independently of
    the guard above.
    """

    import sqlite3
    from pathlib import Path

    raw = sqlite3.connect(":memory:", uri=True)
    raw.execute(f"ATTACH DATABASE '{Path(path).resolve().as_uri()}?mode=ro' AS dbo")
    if users_path:
        raw.execute(f"ATTACH DATABASE '{Path(users_path).resolve().as_uri()}?mode=ro' AS fecore")
    return ReadOnlyConnection(raw, dialect="sqlite")


def connect(dsn: str, tables: tuple[str, ...] = ()) -> ReadOnlyConnection:
    """Open whatever ``PIMS_LEGACY_DSN`` points at, read-only.

    ``sqlite:///path/to/copy.db`` for a local stand-in; anything else is an
    ODBC connection string for SQL Server.
    """

    if dsn.startswith("sqlite:///"):
        main, _, users = dsn[len("sqlite:///"):].partition("|")
        return connect_sqlite(main, users or None)
    return connect_mssql(dsn, tables=tables)
