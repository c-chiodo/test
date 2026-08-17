"""Truck-scale weights.

Weight is the number an operator transcribes most often and the one a typo
costs the most on, so it is the best candidate for machine capture. The plant
agent (``scripts/pims_scale_agent.py``) reads the indicator — most publish a
line of text over serial or TCP — and posts it here; the loadout screen then
offers the reading instead of an empty box.

A reading is a *suggestion* until an operator posts a transaction with it. It
is marked consumed at that point, so the same weigh-out cannot be used twice.
"""

from __future__ import annotations

import re
from typing import Any

from .. import audit, db
from ..errors import NotFound, ValidationError
from ..util import round_lbs, to_float, utc_now_iso

#: Indicators put the label on either side of the number — "GROSS 45320 LB" and
#: "45320 lb G" are both common — so both orders are matched, label first.
_LABEL_FIRST = re.compile(
    r"\b(?P<kind>GROSS|TARE|NET|G|T|N)\b\s*[:=]?\s*(?P<value>-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_LABEL_LAST = re.compile(
    r"(?P<value>-?\d+(?:\.\d+)?)\s*(?:lbs?|LB)?\s*\b(?P<kind>GROSS|TARE|NET|G|T|N)\b",
    re.IGNORECASE,
)
_BARE_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

_KIND_FIELD = {"G": "gross_lbs", "T": "tare_lbs", "N": "net_lbs"}


def parse_indicator_line(line: str) -> dict[str, float | None]:
    """Pull gross/tare/net out of a line of indicator text.

    A bare number is treated as gross, which is what single-value indicators
    send. Anything unrecognised comes back as all-None and is rejected by
    :func:`record` rather than stored as a zero.
    """

    found: dict[str, float | None] = {"gross_lbs": None, "tare_lbs": None, "net_lbs": None}
    # Indicators print thousands separators; "38,150" is one weight, not two.
    text = (line or "").replace(",", "")

    for pattern in (_LABEL_FIRST, _LABEL_LAST):
        for match in pattern.finditer(text):
            value = to_float(match.group("value"))
            field = _KIND_FIELD[match.group("kind")[0].upper()]
            if value is not None and found[field] is None:
                found[field] = value
        if any(value is not None for value in found.values()):
            break

    if all(value is None for value in found.values()):
        bare = _BARE_NUMBER.search(text)
        if bare:
            found["gross_lbs"] = to_float(bare.group())

    if found["net_lbs"] is None and found["gross_lbs"] is not None and found["tare_lbs"] is not None:
        found["net_lbs"] = found["gross_lbs"] - found["tare_lbs"]
    return found


def record(payload: dict[str, Any], user: dict | None = None, conn=None) -> dict:
    """Store a reading posted by the plant agent."""

    plant_id = payload.get("plant_id")
    if not plant_id:
        raise ValidationError("A reading needs a plant.", fields={"plant_id": "Required."})
    if not db.query_one("SELECT plant_id FROM plant WHERE plant_id = ?", (plant_id,), conn):
        raise NotFound(f"Plant {plant_id} does not exist.")

    if payload.get("line"):
        weights = parse_indicator_line(str(payload["line"]))
    else:
        weights = {
            "gross_lbs": to_float(payload.get("gross_lbs")),
            "tare_lbs": to_float(payload.get("tare_lbs")),
            "net_lbs": to_float(payload.get("net_lbs")),
        }
        if weights["net_lbs"] is None and weights["gross_lbs"] is not None and weights["tare_lbs"] is not None:
            weights["net_lbs"] = weights["gross_lbs"] - weights["tare_lbs"]

    if all(value is None for value in weights.values()):
        raise ValidationError(
            "No weight in that reading.",
            fields={"gross_lbs": "Send gross/tare/net, or a raw indicator line."},
        )
    for key, value in weights.items():
        if value is not None and value < 0:
            raise ValidationError(f"{key} cannot be negative.", fields={key: "Negative weight."})

    reading_id = db.insert(
        "scale_reading",
        {
            "plant_id": int(plant_id),
            "scale_id": payload.get("scale_id") or "",
            "trailer_number": str(payload.get("trailer_number") or "").strip(),
            "gross_lbs": round_lbs(weights["gross_lbs"]) if weights["gross_lbs"] is not None else None,
            "tare_lbs": round_lbs(weights["tare_lbs"]) if weights["tare_lbs"] is not None else None,
            "net_lbs": round_lbs(weights["net_lbs"]) if weights["net_lbs"] is not None else None,
            "captured_at": payload.get("captured_at") or utc_now_iso(),
            "received_at": utc_now_iso(),
            "source": payload.get("source") or "agent",
        },
        conn,
    )
    audit.record(
        username=(user or {}).get("username", "scale-agent"),
        action="scale.reading",
        entity="scale_reading",
        entity_id=reading_id,
        summary=(
            f"Scale reading {weights['net_lbs'] or weights['gross_lbs']:,.0f} lbs"
            f" at plant {plant_id}"
        ),
        detail={"trailer_number": payload.get("trailer_number"), **weights},
        conn=conn,
    )
    return get(reading_id, conn)


def get(reading_id: int, conn=None) -> dict:
    row = db.query_one(
        "SELECT * FROM scale_reading WHERE reading_id = ?", (reading_id,), conn
    )
    if row is None:
        raise NotFound(f"Scale reading {reading_id} was not found.")
    return row


def latest(
    plant_id: int,
    trailer_number: str | None = None,
    max_age_minutes: int = 120,
    conn=None,
) -> dict | None:
    """The most recent unused reading for this plant, and trailer if known."""

    sql = """
        SELECT * FROM scale_reading
        WHERE plant_id = ? AND consumed_by IS NULL
          AND captured_at >= datetime('now', ?)
    """
    params: list[Any] = [plant_id, f"-{int(max_age_minutes)} minutes"]
    if trailer_number:
        sql += " AND (TRIM(trailer_number) = TRIM(?) OR TRIM(trailer_number) = '')"
        params.append(trailer_number)
    sql += " ORDER BY captured_at DESC, reading_id DESC LIMIT 1"
    return db.query_one(sql, params, conn)


def consume(reading_id: int, transaction_id: int, conn=None) -> None:
    """Mark a reading as used by a posted transaction."""

    db.update(
        "scale_reading", {"reading_id": reading_id}, {"consumed_by": transaction_id}, conn
    )


def recent(plant_id: int | None = None, limit: int = 25, conn=None) -> list[dict]:
    sql = "SELECT * FROM scale_reading"
    params: list[Any] = []
    if plant_id:
        sql += " WHERE plant_id = ?"
        params.append(plant_id)
    sql += " ORDER BY reading_id DESC LIMIT ?"
    params.append(limit)
    return db.query(sql, params, conn)
