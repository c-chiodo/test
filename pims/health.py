"""Health checks, diagnostics and data-quality probes.

This is the "ability to support it" half of the replacement. Each check answers
a question that, in the legacy system, required a DBA with VIEW DEFINITION and
a day of ad-hoc SQL (the ``matrix_diag*.sql`` scripts that shipped in the PIMS
folder are exactly that work, done by hand, after the fact).

Every check returns ``{status, detail, ...}`` where status is one of
``ok`` / ``degraded`` / ``failed``, and the worst status wins overall — so a
single endpoint tells an on-call engineer whether the system is healthy.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import __version__, db, observability
from .config import get_settings
from .services import lims
from .util import today_iso, utc_now_iso

ORDER = {"ok": 0, "degraded": 1, "failed": 2}


def _worst(statuses: list[str]) -> str:
    return max(statuses or ["ok"], key=lambda s: ORDER.get(s, 0))


def liveness() -> dict[str, Any]:
    """Cheap check for a load balancer: is the process up?"""

    return {
        "status": "ok",
        "service": "pims",
        "version": __version__,
        "environment": get_settings().environment,
        "checked_at": utc_now_iso(),
    }


def check_database(conn=None) -> dict[str, Any]:
    settings = get_settings()
    try:
        conn = conn or db.get_connection()
        tables = db.table_names(conn)
        missing = sorted(
            {
                "order",
                "inventory_transaction",
                "qc",
                "material",
                "location",
                "app_user",
                "audit_log",
            }
            - set(tables)
        )
        counts = {
            name: db.scalar(f'SELECT COUNT(*) FROM "{name}"', (), conn)
            for name in ("order", "inventory_transaction", "qc", "material", "app_user")
        }
        path = settings.sqlite_path
        size_mb = round(path.stat().st_size / 1_048_576, 2) if path.exists() else 0.0
        integrity = db.scalar("PRAGMA quick_check", (), conn)
        status = "ok"
        detail = f"{counts['order']:,} orders, {counts['inventory_transaction']:,} transactions."
        if missing:
            status, detail = "failed", f"Missing tables: {', '.join(missing)}."
        elif integrity != "ok":
            status, detail = "failed", f"Integrity check returned {integrity!r}."
        return {
            "status": status,
            "detail": detail,
            "path": str(path),
            "size_mb": size_mb,
            "tables": len(tables),
            "counts": counts,
        }
    except sqlite3.Error as exc:
        return {"status": "failed", "detail": f"Database error: {exc}"}


def check_lims(conn=None) -> dict[str, Any]:
    return lims.freshness(conn)


def check_sessions(conn=None) -> dict[str, Any]:
    active = db.scalar(
        "SELECT COUNT(*) FROM user_session WHERE expires_at > ?", (utc_now_iso(),), conn
    )
    expired = db.scalar(
        "SELECT COUNT(*) FROM user_session WHERE expires_at <= ?", (utc_now_iso(),), conn
    )
    status = "degraded" if expired > 500 else "ok"
    return {
        "status": status,
        "detail": f"{active} active session(s), {expired} expired awaiting cleanup.",
        "active": active,
        "expired": expired,
    }


def check_errors() -> dict[str, Any]:
    counters = observability.counters()
    server_errors = counters.get("server_errors", 0)
    requests = counters.get("requests", 0)
    rate = (server_errors / requests * 100) if requests else 0.0
    if server_errors and rate > 5:
        status = "failed"
    elif server_errors:
        status = "degraded"
    else:
        status = "ok"
    return {
        "status": status,
        "detail": (
            f"{server_errors} server error(s) in {requests} request(s) "
            f"since start ({rate:.1f}%)."
        ),
        "counters": counters,
        "recent": observability.recent_errors(10),
        "slow_requests": observability.slow_requests(5),
    }


def check_specs(conn=None) -> dict[str, Any]:
    """Product setup gaps — the cause of "out-of-spec results aren't flagged"."""

    needs_review = db.scalar(
        "SELECT COUNT(*) FROM material_spec WHERE needs_review = 1 AND active = 1", (), conn
    )
    no_specs = db.query(
        """
        SELECT m.material_id, m.number, m.description
        FROM material m
        WHERE m.active = 1
          AND m.material_type_id = 1
          AND NOT EXISTS (SELECT 1 FROM material_spec s
                          WHERE s.material_id = m.material_id AND s.active = 1)
        ORDER BY m.number
        """,
        (),
        conn,
    )
    no_tests = db.query(
        """
        SELECT m.material_id, m.number, m.description
        FROM material m
        WHERE m.active = 1
          AND m.material_type_id = 1
          AND NOT EXISTS (SELECT 1 FROM material_test t WHERE t.material_id = m.material_id)
        ORDER BY m.number
        """,
        (),
        conn,
    )
    status = "ok"
    parts = []
    if no_specs or no_tests:
        status = "degraded"
    if needs_review:
        status = "degraded" if status == "ok" else status
        parts.append(f"{needs_review} limit(s) flagged for confirmation")
    if no_specs:
        parts.append(f"{len(no_specs)} finished product(s) with no limits")
    if no_tests:
        parts.append(f"{len(no_tests)} finished product(s) with no test list")
    return {
        "status": status,
        "detail": "; ".join(parts) or "Every finished product has limits and a test list.",
        "needs_review": needs_review,
        "materials_without_specs": no_specs[:25],
        "materials_without_tests": no_tests[:25],
    }


def data_quality(plant_id: int | None = None, conn=None) -> dict[str, Any]:
    """Findings a supervisor can act on today, each with the rows behind it."""

    plant_clause = " AND o.plant_id = ?" if plant_id else ""
    params: list[Any] = [plant_id] if plant_id else []

    negative = [
        row
        for row in _balances(conn)
        if row["balance"] < -0.01 and (not plant_id or row["plant_id"] == plant_id)
    ]
    over_capacity = [
        row
        for row in _balances(conn)
        if row["max_capacity"]
        and row["balance"] > row["max_capacity"] + 0.01
        and (not plant_id or row["plant_id"] == plant_id)
    ]
    stale_loads = db.query(
        f"""
        SELECT ps.stage_id, ps.order_id, ps.trailer_number, ps.quantity,
               t.transaction_date AS loaded_at, p.code AS plant_code
        FROM pending_shipment ps
        JOIN "order" o ON o.order_id = ps.order_id
        JOIN plant p ON p.plant_id = o.plant_id
        JOIN inventory_transaction t ON t.transaction_id = ps.transaction_id
        WHERE ps.shipped = 0 AND t.voided = 0
          AND t.transaction_date < datetime('now', '-2 days'){plant_clause}
        ORDER BY t.transaction_date
        LIMIT 100
        """,
        params,
        conn,
    )
    overdue = db.query(
        f"""
        SELECT o.order_id, o.due_date, p.code AS plant_code, s.name AS status,
               ot.code AS order_type
        FROM "order" o
        JOIN plant p ON p.plant_id = o.plant_id
        JOIN status s ON s.status_id = o.status_id
        JOIN order_type ot ON ot.order_type_id = o.order_type_id
        WHERE o.active = 1 AND s.is_terminal = 0 AND o.due_date < ?{plant_clause}
        ORDER BY o.due_date
        LIMIT 100
        """,
        [today_iso(), *params],
        conn,
    )
    qc_without_sample = db.query(
        f"""
        SELECT q.qc_id, q.order_id, q.test_date, p.code AS plant_code,
               m.number AS material_number
        FROM qc q
        JOIN "order" o ON o.order_id = q.order_id
        JOIN plant p ON p.plant_id = o.plant_id
        LEFT JOIN material m ON m.material_id = o.material_one_id
        WHERE q.active = 1 AND TRIM(COALESCE(q.sample_number, '')) = ''
          AND q.test_date >= date('now', '-30 days'){plant_clause}
        ORDER BY q.test_date DESC
        LIMIT 100
        """,
        params,
        conn,
    )
    unmatched_samples = db.query(
        f"""
        SELECT q.qc_id, q.order_id, q.sample_number, q.test_date, p.code AS plant_code
        FROM qc q
        JOIN "order" o ON o.order_id = q.order_id
        JOIN plant p ON p.plant_id = o.plant_id
        WHERE q.active = 1 AND TRIM(COALESCE(q.sample_number, '')) <> ''
          AND q.test_date >= date('now', '-30 days')
          AND NOT EXISTS (SELECT 1 FROM lims_result lr WHERE lr.sample_code = q.sample_number)
          {plant_clause}
        ORDER BY q.test_date DESC
        LIMIT 100
        """,
        params,
        conn,
    )

    findings = [
        {
            "key": "negative_balance",
            "label": "Locations with a negative balance",
            "severity": "high",
            "count": len(negative),
            "rows": negative[:25],
            "action": "Post an adjustment, or void the transaction that overdrew the location.",
        },
        {
            "key": "over_capacity",
            "label": "Locations holding more than their stated capacity",
            "severity": "medium",
            "count": len(over_capacity),
            "rows": over_capacity[:25],
            "action": "Confirm the tank capacity on the location record, or correct the balance.",
        },
        {
            "key": "stale_loads",
            "label": "Trailers loaded more than 2 days ago and never shipped",
            "severity": "medium",
            "count": len(stale_loads),
            "rows": stale_loads[:25],
            "action": "Ship the load in PIMS, or void the load transaction if it never left.",
        },
        {
            "key": "overdue_orders",
            "label": "Open orders past their due date",
            "severity": "low",
            "count": len(overdue),
            "rows": overdue[:25],
            "action": "Close, reschedule, or cancel.",
        },
        {
            "key": "qc_without_sample",
            "label": "QC records saved without a sample number",
            "severity": "medium",
            "count": len(qc_without_sample),
            "rows": qc_without_sample[:25],
            "action": "Add the sample number so LIMS results can be matched to the load.",
        },
        {
            "key": "unmatched_samples",
            "label": "QC sample numbers with no LIMS result",
            "severity": "medium",
            "count": len(unmatched_samples),
            "rows": unmatched_samples[:25],
            "action": (
                "Check the LIMS projection is refreshing and that the sample was "
                "logged in LabWare under this code."
            ),
        },
    ]
    total = sum(f["count"] for f in findings)
    return {
        "status": "ok" if total == 0 else "attention",
        "total_findings": total,
        "findings": findings,
        "plant_id": plant_id,
        "generated_at": utc_now_iso(),
    }


def _balances(conn=None) -> list[dict]:
    from .services import inventory

    return inventory.location_balance(include_zero=False, conn=conn)


def diagnostics(conn=None) -> dict[str, Any]:
    """Full support snapshot: every check, plus environment and versions."""

    settings = get_settings()
    checks = {
        "database": check_database(conn),
        "lims": check_lims(conn),
        "sessions": check_sessions(conn),
        "errors": check_errors(),
        "product_setup": check_specs(conn),
    }
    return {
        "status": _worst([c["status"] for c in checks.values()]),
        "service": "pims",
        "version": __version__,
        "environment": settings.environment,
        "checked_at": utc_now_iso(),
        "checks": checks,
        "runtime": observability.uptime(),
        "configuration": {
            "database_url": _redact(settings.database_url),
            "lims_mode": settings.lims_mode,
            "lims_source": settings.lims_source_label,
            "lims_warn_hours": settings.lims_warn_hours,
            "lims_fail_hours": settings.lims_fail_hours,
            "session_hours": settings.session_hours,
            "auto_seed": settings.auto_seed,
        },
    }


def _redact(url: str) -> str:
    """Never let a password reach a support screen or a pasted ticket."""

    if "://" not in url or "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"
