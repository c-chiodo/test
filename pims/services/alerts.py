"""Alerts: the system tells somebody, instead of waiting to be asked.

Every rule here reads a check that already exists — the data-quality probes and
the LIMS freshness check — and turns it into a message. Nothing new is computed;
what is new is that a stale LIMS feed or a trailer sitting unshipped since
Tuesday arrives in a channel a person reads, which is precisely what did not
happen for seven months in 2026.

Two design rules keep alerting from becoming noise people filter to a folder:

* **Fingerprints.** A condition is identified by what it is about (this stage,
  this QC record), not by when it fired. The same condition inside
  ``alerts.repeat_hours`` is not sent again.
* **Severity floor.** ``alerts.min_severity`` decides what reaches the webhook.
  Everything is recorded either way, so the support console shows what was
  suppressed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .. import db, health
from ..util import hours_since, utc_now_iso
from . import lims, numbering, qc as qc_service

SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


def _setting(key: str, default: str = "", conn=None) -> str:
    row = db.query_one("SELECT value FROM system_setting WHERE key = ?", (key,), conn)
    return row["value"] if row and row["value"] != "" else default


# ------------------------------------------------------------------- rules


def evaluate(plant_id: int | None = None, conn=None) -> list[dict[str, Any]]:
    """Run every rule and return the conditions that are currently true."""

    found: list[dict[str, Any]] = []
    found.extend(_lims_stale(conn))
    found.extend(_out_of_spec(plant_id, conn))
    found.extend(_stale_loads(plant_id, conn))
    found.extend(_tank_pressure(plant_id, conn))
    found.extend(_setup_gaps(conn))
    return found


def _lims_stale(conn) -> list[dict]:
    freshness = lims.freshness(conn)
    if freshness["status"] == "ok":
        return []
    age = freshness.get("age_hours")
    return [
        {
            "rule": "lims_stale",
            "severity": "critical" if freshness["status"] == "failed" else "warning",
            "subject": f"LIMS feed {freshness['status']}"
            + (f" — last refresh {age:.0f} h ago" if age is not None else ""),
            "body": freshness["detail"] + " Lab results shown in PIMS may be out of date.",
            "fingerprint": f"lims_stale:{freshness['status']}",
            "entity": "lims",
            "entity_id": freshness.get("expected_source", ""),
            "plant_id": None,
        }
    ]


def _out_of_spec(plant_id: int | None, conn) -> list[dict]:
    alerts = []
    for record in qc_service.out_of_spec_report(plant_id, days=2, limit=50, conn=conn):
        analytes = ", ".join(
            f"{e['label']} {e['value']}" for e in record["evaluations"]
        )
        alerts.append(
            {
                "rule": "out_of_spec",
                "severity": "warning",
                "subject": (
                    f"Out of spec: {record['material_number']} on order "
                    f"{record['order_id']} ({record['plant_code']})"
                ),
                "body": (
                    f"{analytes}. Sample {record['sample_number'] or 'not recorded'}, "
                    f"tested {record['test_date']} by {record['performed_by']}."
                ),
                "fingerprint": f"out_of_spec:{record['qc_id']}",
                "entity": "qc",
                "entity_id": str(record["qc_id"]),
                "plant_id": record["plant_id"],
            }
        )
    return alerts


def _stale_loads(plant_id: int | None, conn) -> list[dict]:
    quality = health.data_quality(plant_id, conn)
    finding = next(f for f in quality["findings"] if f["key"] == "stale_loads")
    alerts = []
    for row in finding["rows"]:
        age = hours_since(row["loaded_at"])
        alerts.append(
            {
                "rule": "stale_load",
                "severity": "warning",
                "subject": (
                    f"Trailer {row['trailer_number']} loaded "
                    f"{age / 24:.0f} days ago and not shipped ({row['plant_code']})"
                ),
                "body": (
                    f"Order {row['order_id']}, {row['quantity']:,.0f} lbs staged since "
                    f"{row['loaded_at']}. Ship it in PIMS, or void the load if it never left."
                ),
                "fingerprint": f"stale_load:{row['stage_id']}",
                "entity": "pending_shipment",
                "entity_id": str(row["stage_id"]),
                "plant_id": plant_id,
            }
        )
    return alerts


def _tank_pressure(plant_id: int | None, conn) -> list[dict]:
    """Tanks close to full or nearly empty — the thing operators find out about
    when a truck is already on the scale."""

    from . import inventory

    alerts = []
    for row in inventory.location_balance(plant_id=plant_id, conn=conn):
        if row["location_type"] != "Tank" or not row["max_capacity"]:
            continue
        pct = row["percent_full"] or 0
        if pct >= 95:
            alerts.append(
                {
                    "rule": "tank_full",
                    "severity": "warning",
                    "subject": f"{row['location_number']} is {pct:.0f}% full ({row['plant_code']})",
                    "body": (
                        f"{row['balance']:,.0f} lbs of {row['material_number']} "
                        f"{row['material_description']} against a "
                        f"{row['max_capacity']:,.0f} lb capacity."
                    ),
                    "fingerprint": f"tank_full:{row['location_id']}:{row['material_id']}",
                    "entity": "location",
                    "entity_id": str(row["location_id"]),
                    "plant_id": row["plant_id"],
                }
            )
    return alerts


def _setup_gaps(conn) -> list[dict]:
    check = health.check_specs(conn)
    if check["status"] == "ok":
        return []
    return [
        {
            "rule": "product_setup",
            "severity": "info",
            "subject": "Product setup needs attention",
            "body": check["detail"] + " See Products & limits.",
            "fingerprint": f"product_setup:{check['detail']}",
            "entity": "material_spec",
            "entity_id": "",
            "plant_id": None,
        }
    ]


# --------------------------------------------------------------- delivery


def _recently_sent(fingerprint: str, window_hours: float, conn) -> bool:
    row = db.query_one(
        "SELECT created_at FROM alert_log WHERE fingerprint = ?"
        " ORDER BY alert_id DESC LIMIT 1",
        (fingerprint,),
        conn,
    )
    if row is None:
        return False
    age = hours_since(row["created_at"])
    return age is not None and age < window_hours


def deliver(alert: dict[str, Any], webhook_url: str, conn=None) -> dict[str, Any]:
    """Post one alert to the configured webhook. Teams and Slack both accept
    a JSON body with a ``text`` field, which is why that shape is used."""

    payload = json.dumps(
        {
            "text": f"*{alert['subject']}*\n{alert['body']}",
            "pims": {
                "rule": alert["rule"],
                "severity": alert["severity"],
                "entity": alert["entity"],
                "entity_id": alert["entity_id"],
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        webhook_url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return {"delivered": 1, "error": "", "status": response.status}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"delivered": 0, "error": str(exc)[:300], "status": None}


def run(
    plant_id: int | None = None,
    send: bool = True,
    conn=None,
) -> dict[str, Any]:
    """Evaluate every rule, record what is new, deliver what qualifies.

    ``send=False`` is a dry run: conditions are reported but nothing is written
    or posted, which is what ``python -m pims alerts`` does by default.
    """

    webhook = _setting("alerts.webhook_url", "", conn)
    floor = SEVERITY_ORDER.get(_setting("alerts.min_severity", "warning", conn), 1)
    repeat_hours = float(_setting("alerts.repeat_hours", "24", conn) or 24)

    found = evaluate(plant_id, conn)
    new: list[dict] = []
    suppressed: list[dict] = []
    delivered = 0

    for alert in found:
        if _recently_sent(alert["fingerprint"], repeat_hours, conn):
            suppressed.append(alert)
            continue
        if not send:
            new.append(alert)
            continue

        outcome = {"delivered": 0, "error": ""}
        channel = ""
        if webhook and SEVERITY_ORDER.get(alert["severity"], 0) >= floor:
            channel = "webhook"
            outcome = deliver(alert, webhook, conn)

        db.insert(
            "alert_log",
            {
                "created_at": utc_now_iso(),
                "rule": alert["rule"],
                "severity": alert["severity"],
                "subject": alert["subject"],
                "body": alert["body"],
                "fingerprint": alert["fingerprint"],
                "plant_id": alert.get("plant_id"),
                "entity": alert["entity"],
                "entity_id": alert["entity_id"],
                "channel": channel,
                "delivered": outcome["delivered"],
                "error": outcome["error"],
            },
            conn,
        )
        delivered += outcome["delivered"]
        new.append(alert)

    return {
        "evaluated": len(found),
        "new": new,
        "suppressed": len(suppressed),
        "delivered": delivered,
        "webhook_configured": bool(webhook),
        "min_severity": _setting("alerts.min_severity", "warning", conn),
        "dry_run": not send,
    }


def digest(plant_id: int | None = None, conn=None) -> dict[str, Any]:
    """The once-a-day summary: what is open, not what changed."""

    quality = health.data_quality(plant_id, conn)
    diagnostics = health.diagnostics(conn)
    lines = [
        f"{finding['count']} × {finding['label']}"
        for finding in quality["findings"]
        if finding["count"]
    ]
    return {
        "generated_at": utc_now_iso(),
        "plant_id": plant_id,
        "system_status": diagnostics["status"],
        "total_findings": quality["total_findings"],
        "lines": lines,
        "lims": diagnostics["checks"]["lims"]["detail"],
        "subject": (
            f"PIMS daily digest — {quality['total_findings']} finding(s), "
            f"system {diagnostics['status']}"
        ),
        "body": "\n".join(lines) or "Nothing outstanding.",
    }


def send_digest(plant_id: int | None = None, conn=None) -> dict[str, Any]:
    summary = digest(plant_id, conn)
    webhook = _setting("alerts.webhook_url", "", conn)
    outcome = {"delivered": 0, "error": ""}
    if webhook:
        outcome = deliver(
            {
                "subject": summary["subject"],
                "body": summary["body"],
                "rule": "digest",
                "severity": "info",
                "entity": "digest",
                "entity_id": "",
            },
            webhook,
            conn,
        )
    db.insert(
        "alert_log",
        {
            "created_at": utc_now_iso(),
            "rule": "digest",
            "severity": "info",
            "subject": summary["subject"],
            "body": summary["body"],
            # Fingerprinted per day so a second run does not double-send.
            "fingerprint": f"digest:{utc_now_iso()[:10]}:{plant_id or 'all'}",
            "plant_id": plant_id,
            "entity": "digest",
            "entity_id": utc_now_iso()[:10],
            "channel": "webhook" if webhook else "",
            "delivered": outcome["delivered"],
            "error": outcome["error"],
        },
        conn,
    )
    return {**summary, **outcome, "webhook_configured": bool(webhook)}


def recent(limit: int = 50, conn=None) -> list[dict]:
    return db.query(
        "SELECT * FROM alert_log ORDER BY alert_id DESC LIMIT ?", (limit,), conn
    )


def acknowledge(alert_id: int, username: str, conn=None) -> dict:
    db.update(
        "alert_log",
        {"alert_id": alert_id},
        {"acknowledged_at": utc_now_iso(), "acknowledged_by": username},
        conn,
    )
    return db.query_one("SELECT * FROM alert_log WHERE alert_id = ?", (alert_id,), conn)


def settings_summary(conn=None) -> dict[str, Any]:
    """What alerting is configured to do — for the support console."""

    return {
        "webhook_configured": bool(_setting("alerts.webhook_url", "", conn)),
        "min_severity": _setting("alerts.min_severity", "warning", conn),
        "repeat_hours": _setting("alerts.repeat_hours", "24", conn),
        "auto_close": _setting("autoclose.enabled", "true", conn) == "true",
        "sample_auto_generate": numbering.setting("sample.auto_generate", conn) == "true",
    }
