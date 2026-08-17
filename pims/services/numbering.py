"""Generated BOL and sample numbers.

Both were typed by hand in the legacy client, from a scheme staff carried in
their heads. That is where "QC records with no sample number" and "sample
numbers with no LIMS result" come from — a mistyped code cannot be matched back
to a load, and nothing notices.

The formats are reconstructed from the numbers in the legacy data:

    BOL     001-111585-1(GLT13)     prefix + sequence + leg, trailer in brackets
    sample  DMC1359D251216Q0360397  plant + party + date + sequence
            SCA1976D260721Q0384985  ('C' for a customer, 'A' for a vendor)

Both live in ``system_setting`` as templates, so a correction is a settings
change rather than a deploy — which matters, because the sample scheme has to
match what LabWare expects and that must be confirmed with the lab before this
is switched on in production (see ``docs/PIMS_RUNBOOK.md`` §12).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .. import db
from ..errors import NotFound
from ..util import parse_dt, utc_now

DEFAULTS: dict[str, str] = {
    "bol.prefix": "001-",
    "bol.format": "{prefix}{sequence:06d}-1",
    "bol.append_trailer": "true",
    "sample.format": "{plant}{party}D{date}Q{sequence:07d}",
    "sample.date_format": "%y%m%d",
}


def setting(key: str, conn=None) -> str:
    row = db.query_one("SELECT value FROM system_setting WHERE key = ?", (key,), conn)
    return row["value"] if row else DEFAULTS.get(key, "")


def next_in_sequence(key: str, conn=None) -> int:
    """Take the next value for a counter, atomically."""

    with db.transaction(conn):
        row = db.query_one(
            "SELECT next_value FROM number_sequence WHERE key = ?", (key,), conn
        )
        if row is None:
            value = 1
            db.insert("number_sequence", {"key": key, "next_value": 2}, conn)
        else:
            value = int(row["next_value"])
            db.update("number_sequence", {"key": key}, {"next_value": value + 1}, conn)
    return value


def peek_sequence(key: str, conn=None) -> int:
    row = db.query_one("SELECT next_value FROM number_sequence WHERE key = ?", (key,), conn)
    return int(row["next_value"]) if row else 1


def seed_sequence(key: str, value: int, conn=None) -> None:
    """Set a counter — used at cutover to continue the existing series."""

    with db.transaction(conn):
        if db.query_one("SELECT key FROM number_sequence WHERE key = ?", (key,), conn):
            db.update("number_sequence", {"key": key}, {"next_value": value}, conn)
        else:
            db.insert("number_sequence", {"key": key, "next_value": value}, conn)


def next_bol(trailer_number: str | None = None, conn=None) -> str:
    prefix = setting("bol.prefix", conn)
    template = setting("bol.format", conn) or DEFAULTS["bol.format"]
    sequence = next_in_sequence("bol", conn)
    number = template.format(prefix=prefix, sequence=sequence)
    if trailer_number and setting("bol.append_trailer", conn) == "true":
        number = f"{number}({trailer_number.strip()})"
    return number


def next_sample_number(order_id: int, when: Any = None, conn=None) -> str:
    """Sample code for an order, in the shape the lab reads today."""

    order = db.query_one(
        """
        SELECT o.order_id, o.customer_id, o.vendor_id, o.order_type_id,
               p.code AS plant_code, c.gp_custnmbr, v.gp_vendorid
        FROM "order" o
        JOIN plant p ON p.plant_id = o.plant_id
        LEFT JOIN customer c ON c.customer_id = o.customer_id
        LEFT JOIN vendor v   ON v.vendor_id = o.vendor_id
        WHERE o.order_id = ?
        """,
        (order_id,),
        conn,
    )
    if order is None:
        raise NotFound(f"Order {order_id} was not found.")

    if order["customer_id"]:
        party = f"C{_digits(order['gp_custnmbr'])}"
    elif order["vendor_id"]:
        party = f"A{_digits(order['gp_vendorid'])}"
    else:
        party = "W"

    moment = parse_dt(when) or utc_now()
    date_format = setting("sample.date_format", conn) or DEFAULTS["sample.date_format"]
    template = setting("sample.format", conn) or DEFAULTS["sample.format"]
    return template.format(
        plant=order["plant_code"],
        party=party,
        date=moment.astimezone(timezone.utc).strftime(date_format),
        sequence=next_in_sequence("sample", conn),
        order_id=order_id,
    )


def _digits(value: str | None) -> str:
    """'FEC-1359' -> '1359'; anything unrecognisable falls back to the raw text."""

    if not value:
        return ""
    tail = "".join(ch for ch in str(value) if ch.isdigit())
    return tail or str(value).strip().upper()


def preview(order_id: int, conn=None) -> dict[str, Any]:
    """What the next numbers would look like, without consuming them."""

    bol_sequence = peek_sequence("bol", conn)
    prefix = setting("bol.prefix", conn)
    template = setting("bol.format", conn) or DEFAULTS["bol.format"]
    return {
        "bol_number": template.format(prefix=prefix, sequence=bol_sequence),
        "bol_sequence": bol_sequence,
        "sample_sequence": peek_sequence("sample", conn),
        "sample_format": setting("sample.format", conn),
        "order_id": order_id,
    }


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%y%m%d")
