"""Scheduled work: auto-close, standing orders, and the daily run.

Everything here is designed to be run from cron (or Task Scheduler) and to be
safe to run twice — the second run finds nothing to do rather than doing it
again. Each run writes a ``job_run`` row, so "did last night's job run?" is a
query instead of a hunt through logs.

The jobs act as a ``system`` user: a real row in ``app_user`` with no usable
password, so its actions are attributable in the audit trail and nobody can
sign in as it.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any, Iterator

from .. import audit, db, security
from ..errors import NotFound, ValidationError
from ..util import parse_dt, today_iso, utc_now, utc_now_iso
from . import alerts, orders as orders_service


def system_user(conn=None) -> dict:
    """The account scheduled work runs as. Created on first use."""

    row = db.query_one("SELECT * FROM app_user WHERE username = 'system'", (), conn)
    if row is None:
        user_id = db.insert(
            "app_user",
            {
                "username": "system",
                "full_name": "PIMS scheduled jobs",
                "email": "",
                "role": "admin",
                "password_hash": "",     # unusable: verify_password always fails
                "pin_hash": "",
                "date_added": utc_now_iso(),
            },
            conn,
        )
        row = db.query_one("SELECT * FROM app_user WHERE user_id = ?", (user_id,), conn)
    return row


@contextmanager
def record_run(job: str, conn=None) -> Iterator[dict]:
    """Wrap a job so its outcome is recorded whether it succeeds or not."""

    run_id = db.insert(
        "job_run", {"job": job, "started_at": utc_now_iso(), "status": "running"}, conn
    )
    detail: dict[str, Any] = {}
    try:
        yield detail
    except Exception as exc:
        db.update(
            "job_run",
            {"run_id": run_id},
            {
                "finished_at": utc_now_iso(),
                "status": "failed",
                "error": str(exc)[:500],
                "detail": json.dumps(detail, default=str),
            },
            conn,
        )
        raise
    db.update(
        "job_run",
        {"run_id": run_id},
        {
            "finished_at": utc_now_iso(),
            "status": "ok",
            "detail": json.dumps(detail, default=str),
        },
        conn,
    )


def last_runs(limit: int = 20, conn=None) -> list[dict]:
    return db.query(
        "SELECT * FROM job_run ORDER BY run_id DESC LIMIT ?", (limit,), conn
    )


def _setting(key: str, default: str, conn=None) -> str:
    row = db.query_one("SELECT value FROM system_setting WHERE key = ?", (key,), conn)
    return row["value"] if row else default


# ------------------------------------------------------------- auto-close


def closable(plant_id: int | None = None, conn=None) -> list[dict]:
    """Orders that have finished but are still sitting open.

    Deliberately conservative: fulfilled to the configured percentage, nothing
    staged and unshipped, and — when ``autoclose.require_qc`` is on — carrying a
    QC record. An order closed early is worse than one closed late.
    """

    min_percent = float(_setting("autoclose.min_percent", "99", conn))
    require_qc = _setting("autoclose.require_qc", "true", conn) == "true"

    candidates = orders_service.search(
        plant_id=plant_id, open_only=True, limit=1000, conn=conn
    )["rows"]
    ready = []
    for order in candidates:
        if order["percent_complete"] < min_percent:
            continue
        pending = db.scalar(
            "SELECT COUNT(*) FROM pending_shipment WHERE order_id = ? AND shipped = 0",
            (order["order_id"],),
            conn,
        )
        if pending:
            continue
        if order["order_type"] == "SO" and order["qty_shipped"] <= 0:
            continue          # loaded but never shipped is not complete
        if require_qc:
            has_qc = db.scalar(
                "SELECT COUNT(*) FROM qc WHERE order_id = ? AND active = 1",
                (order["order_id"],),
                conn,
            )
            if not has_qc:
                continue
        ready.append(order)
    return ready


def auto_close(plant_id: int | None = None, dry_run: bool = False, conn=None) -> dict:
    if _setting("autoclose.enabled", "true", conn) != "true":
        return {"enabled": False, "closed": [], "candidates": 0}

    ready = closable(plant_id, conn)
    if dry_run:
        return {
            "enabled": True,
            "dry_run": True,
            "candidates": len(ready),
            "would_close": [order["order_id"] for order in ready],
        }
    user = system_user(conn)
    result = orders_service.close([o["order_id"] for o in ready], user, conn)
    return {"enabled": True, "candidates": len(ready), **result}


# ---------------------------------------------------------- standing orders


CADENCES = ("daily", "weekly", "monthly")


def create_recurring(payload: dict, user: dict, conn=None) -> dict:
    """Register a standing order — the thing "# of orders to create" stood in for."""

    security.require_permission(user, "order.write")
    cadence = (payload.get("cadence") or "").lower()
    if cadence not in CADENCES:
        raise ValidationError(
            f"Cadence must be one of {', '.join(CADENCES)}.",
            fields={"cadence": "daily, weekly or monthly."},
        )
    template = payload.get("template") or {}
    for field in ("order_type_id", "plant_id", "company_id", "material_one_id", "material_one_quantity"):
        if not template.get(field):
            raise ValidationError(
                "The order template is incomplete.", fields={f"template.{field}": "Required."}
            )
    if not (payload.get("name") or "").strip():
        raise ValidationError("Give the standing order a name.", fields={"name": "Required."})

    row = {
        "name": payload["name"].strip(),
        "template": json.dumps(template),
        "cadence": cadence,
        "weekday": payload.get("weekday"),
        "day_of_month": payload.get("day_of_month"),
        "lead_days": int(payload.get("lead_days") or 0),
        "next_run": str(payload.get("next_run") or today_iso())[:10],
        "active": 1,
        "added_by": user["username"],
        "date_added": utc_now_iso(),
    }
    recurring_id = db.insert("recurring_order", row, conn)
    audit.record(
        username=user["username"],
        action="recurring.create",
        entity="recurring_order",
        entity_id=recurring_id,
        summary=f"Standing order '{row['name']}' ({cadence})",
        detail=row,
        conn=conn,
    )
    return get_recurring(recurring_id, conn)


def get_recurring(recurring_id: int, conn=None) -> dict:
    row = db.query_one(
        "SELECT * FROM recurring_order WHERE recurring_id = ?", (recurring_id,), conn
    )
    if row is None:
        raise NotFound(f"Standing order {recurring_id} was not found.")
    row["template"] = json.loads(row["template"])
    return row


def list_recurring(include_inactive: bool = False, conn=None) -> list[dict]:
    sql = "SELECT * FROM recurring_order"
    if not include_inactive:
        sql += " WHERE active = 1"
    rows = db.query(sql + " ORDER BY next_run, recurring_id", (), conn)
    for row in rows:
        row["template"] = json.loads(row["template"])
    return rows


def deactivate_recurring(recurring_id: int, user: dict, conn=None) -> None:
    db.update("recurring_order", {"recurring_id": recurring_id}, {"active": 0}, conn)
    audit.record(
        username=user["username"],
        action="recurring.deactivate",
        entity="recurring_order",
        entity_id=recurring_id,
        summary=f"Stopped standing order {recurring_id}",
        conn=conn,
    )


def _advance(current: date, cadence: str, weekday: int | None, day_of_month: int | None) -> date:
    if cadence == "daily":
        return current + timedelta(days=1)
    if cadence == "weekly":
        step = current + timedelta(days=7)
        if weekday is not None:
            while step.weekday() != weekday:
                step += timedelta(days=1)
        return step
    # monthly
    year, month = (current.year + 1, 1) if current.month == 12 else (current.year, current.month + 1)
    day = min(day_of_month or current.day, 28)
    return date(year, month, day)


def run_recurring(dry_run: bool = False, conn=None) -> dict:
    """Create the orders that are due today. Safe to run repeatedly."""

    today = date.fromisoformat(today_iso())
    due = [row for row in list_recurring(conn=conn) if row["next_run"] <= today.isoformat()]
    if dry_run:
        return {"due": [row["recurring_id"] for row in due], "created": [], "dry_run": True}

    user = system_user(conn)
    created: list[dict] = []
    for row in due:
        template = dict(row["template"])
        template["order_date"] = today.isoformat()
        template["due_date"] = (today + timedelta(days=row["lead_days"])).isoformat()
        template["order_reference"] = template.get("order_reference") or row["name"]
        template.pop("count", None)
        orders = orders_service.create(template, user, conn)
        order_id = orders[0]["order_id"]
        next_run = _advance(
            date.fromisoformat(row["next_run"]),
            row["cadence"],
            row["weekday"],
            row["day_of_month"],
        )
        db.update(
            "recurring_order",
            {"recurring_id": row["recurring_id"]},
            {
                "last_run": today.isoformat(),
                "last_order_id": order_id,
                "next_run": next_run.isoformat(),
            },
            conn,
        )
        audit.record(
            username="system",
            action="recurring.fire",
            entity="order",
            entity_id=order_id,
            summary=f"Created order {order_id} from standing order '{row['name']}'",
            detail={"recurring_id": row["recurring_id"], "next_run": next_run.isoformat()},
            conn=conn,
        )
        created.append({"recurring_id": row["recurring_id"], "order_id": order_id})
    return {"due": [row["recurring_id"] for row in due], "created": created}


# ------------------------------------------------------------- the daily run


def daily(plant_id: int | None = None, send: bool = True, conn=None) -> dict[str, Any]:
    """Everything that should happen once a day, in order."""

    summary: dict[str, Any] = {"started_at": utc_now_iso(), "plant_id": plant_id}
    with record_run("daily", conn) as detail:
        summary["recurring"] = run_recurring(conn=conn)
        summary["auto_close"] = auto_close(plant_id, conn=conn)
        summary["alerts"] = alerts.run(plant_id, send=send, conn=conn)
        summary["digest"] = alerts.send_digest(plant_id, conn) if send else alerts.digest(plant_id, conn)
        summary["sessions_purged"] = security.purge_expired_sessions(conn)
        detail.update(
            {
                "orders_created": len(summary["recurring"]["created"]),
                "orders_closed": len(summary["auto_close"].get("closed", [])),
                "alerts_new": len(summary["alerts"]["new"]),
                "findings": summary["digest"].get("total_findings"),
            }
        )
    summary["finished_at"] = utc_now_iso()
    return summary


def frequent(plant_id: int | None = None, send: bool = True, conn=None) -> dict[str, Any]:
    """The every-few-minutes run: alerts only, no order creation."""

    with record_run("frequent", conn) as detail:
        result = alerts.run(plant_id, send=send, conn=conn)
        detail.update({"alerts_new": len(result["new"]), "delivered": result["delivered"]})
    return result


def health_summary(conn=None) -> dict[str, Any]:
    """Are the scheduled jobs actually running? Surfaced in diagnostics."""

    rows = db.query(
        """
        SELECT job, MAX(started_at) AS last_started,
               (SELECT status FROM job_run j2 WHERE j2.job = j1.job
                ORDER BY run_id DESC LIMIT 1) AS last_status
        FROM job_run j1 GROUP BY job
        """,
        (),
        conn,
    )
    jobs = {}
    for row in rows:
        started = parse_dt(row["last_started"])
        jobs[row["job"]] = {
            "last_started": row["last_started"],
            "last_status": row["last_status"],
            "hours_ago": round((utc_now() - started).total_seconds() / 3600, 1) if started else None,
        }
    return jobs
