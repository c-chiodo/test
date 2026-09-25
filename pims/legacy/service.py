"""The companion's operations: check, sync, status, local accounts.

Thin glue between the CLI / API / scheduled jobs and the read-only pieces.
Every connection it opens to the legacy database is opened through
:func:`~pims.legacy.readonly.connect`, preflighted, used, and closed.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator

from .. import db, security
from ..config import Settings, get_settings
from ..errors import IntegrationError, ValidationError
from ..util import hours_since, utc_now_iso
from . import mirror, probe
from .readonly import ReadOnlyConnection, connect, preflight
from .schema import LEGACY_MAP, LegacyMap


def legacy_map(settings: Settings | None = None) -> LegacyMap:
    settings = settings or get_settings()
    return LEGACY_MAP.with_physical(user=settings.legacy_users_table)


@contextmanager
def open_legacy(settings: Settings | None = None) -> Iterator[ReadOnlyConnection]:
    settings = settings or get_settings()
    if not settings.legacy_dsn:
        raise IntegrationError(
            "No legacy database is configured. Set PIMS_LEGACY_DSN — ideally to a "
            "restored copy or a readable secondary, so production is never touched.",
            adapter="legacy",
        )
    tables = tuple(p for t in legacy_map(settings).tables for p in t.physical)
    connection = connect(settings.legacy_dsn, tables=tables)
    try:
        yield connection
    finally:
        connection.close()


def check(counts: bool = False, settings: Settings | None = None) -> dict[str, Any]:
    with open_legacy(settings) as legacy:
        login = preflight(legacy, tuple(p for t in legacy_map(settings).tables for p in t.physical))
        resolution = probe.resolve(legacy, legacy_map(settings), counts=counts)
    return {"login": login, **resolution.as_dict(), "report": probe.describe(resolution)}


def sync(full: bool = False, settings: Settings | None = None, conn=None) -> dict[str, Any]:
    from ..services import jobs

    settings = settings or get_settings()
    conn = conn or db.get_connection()
    with jobs.record_run("legacy.full" if full else "legacy.sync", conn) as detail:
        with open_legacy(settings) as legacy:
            resolution = probe.resolve(legacy, legacy_map(settings))
            report = mirror.run(
                legacy, resolution, conn, full=full, window_days=settings.legacy_window_days
            )
        detail.update({
            key: value.get("read") for key, value in report["tables"].items() if isinstance(value, dict)
        })
    return report


def status(settings: Settings | None = None) -> dict[str, Any]:
    """What the UI banner and health check need: is this a mirror, and how old."""

    settings = settings or get_settings()
    if not settings.companion:
        return {"mode": "standalone", "companion": False}
    row = db.query_one("SELECT value FROM system_setting WHERE key = 'legacy.last_sync'")
    last = row["value"] if row else None
    age = hours_since(last) if last else None
    report_row = db.query_one("SELECT value FROM system_setting WHERE key = 'legacy.last_report'")
    try:
        report = json.loads(report_row["value"]) if report_row else {}
    except json.JSONDecodeError:
        report = {}
    return {
        "mode": "companion",
        "companion": True,
        "configured": bool(settings.legacy_dsn),
        "last_sync": last,
        "age_minutes": None if age is None else round(age * 60, 1),
        "orphans": report.get("orphans", {}),
        "unclassified_transaction_types": report.get("unclassified_transaction_types", []),
    }


def create_local_account(
    username: str,
    full_name: str,
    password: str,
    role: str = "admin",
    conn=None,
) -> dict[str, Any]:
    """A person who signs in to the companion.

    Ids start at ``LOCAL_ACCOUNT_FLOOR`` so they can never collide with, or be
    overwritten by, a mirrored legacy user; the mirror never touches ids
    above it.
    """

    if role not in security.ROLES:
        raise ValidationError(f"Unknown role {role!r}.", allowed=list(security.ROLES))
    if len(password) < 10:
        raise ValidationError("Use a password of at least 10 characters.")
    conn = conn or db.get_connection()
    top = db.scalar("SELECT MAX(user_id) FROM app_user", (), conn) or 0
    user_id = max(int(top) + 1, mirror.LOCAL_ACCOUNT_FLOOR)
    with db.transaction(conn):
        db.insert(
            "app_user",
            {
                "user_id": user_id,
                "username": username.strip(),
                "full_name": full_name.strip() or username.strip(),
                "role": role,
                "password_hash": security.hash_password(password),
                "active": 1,
                "date_added": utc_now_iso(),
            },
            conn,
        )
        for plant in db.query("SELECT plant_id FROM plant", (), conn):
            db.execute(
                "INSERT OR IGNORE INTO user_plant_access (user_id, plant_id) VALUES (?, ?)",
                (user_id, plant["plant_id"]),
                conn,
            )
    return {"user_id": user_id, "username": username, "role": role}
