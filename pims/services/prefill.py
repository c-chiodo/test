"""What an operator screen should already know.

Every value here was typed by hand in the legacy client even though the system
had it: the tank a purchase order receives into (``location_default``, present
in the schema and never read), the product on the order, the trailer, the
quantity still outstanding, and what that trailer hauled last time.

Nothing here decides anything — it fills a form. The rules still run in
:mod:`pims.services.inventory` when the transaction is posted, so a stale
suggestion is rejected the same as a bad keystroke.
"""

from __future__ import annotations

from typing import Any

from .. import db
from ..errors import NotFound
from ..util import round_lbs, today_iso
from . import numbering

#: Which endpoint each operation fills, mirroring inventory.OPERATIONS.
_SHAPE = {
    "receive": {"from": False, "to": True},
    "produce": {"from": True, "to": True},
    "move": {"from": True, "to": True},
    "load": {"from": True, "to": True},
    "ship": {"from": True, "to": False},
    "shrink": {"from": True, "to": False},
}


def default_location(order_type_id: int, plant_id: int, conn=None) -> int | None:
    row = db.query_one(
        """
        SELECT ld.location_id
        FROM location_default ld
        JOIN location l ON l.location_id = ld.location_id AND l.active = 1
        WHERE ld.order_type_id = ? AND ld.plant_id = ?
        """,
        (order_type_id, plant_id),
        conn,
    )
    return row["location_id"] if row else None


def location_holding(material_id: int, plant_id: int, conn=None) -> dict | None:
    """The tank at this plant with the most of a product — the one to draw from."""

    from . import inventory

    rows = [
        row
        for row in inventory.location_balance(
            plant_id=plant_id, material_id=material_id, conn=conn
        )
        if row["balance"] > 0 and row["location_type"] in {"Tank", "Blend"}
    ]
    if not rows:
        return None
    return max(rows, key=lambda row: row["balance"])


def trailer_history(trailer_number: str, limit: int = 5, conn=None) -> list[dict]:
    """What this trailer carried before, newest first.

    Feeds "Last material hauled", which QC needs in order to judge whether the
    previous load is compatible with this one.
    """

    if not trailer_number.strip():
        return []
    return db.query(
        """
        SELECT t.transaction_id, t.order_id, t.user_date, t.transaction_date,
               m.number AS material_number, m.description AS material_description,
               c.name AS customer_name, tt.code AS operation
        FROM inventory_transaction t
        JOIN transaction_type tt ON tt.transaction_type_id = t.transaction_type_id
        LEFT JOIN material m ON m.material_id = COALESCE(t.from_material_id, t.to_material_id)
        LEFT JOIN "order" o ON o.order_id = t.order_id
        LEFT JOIN customer c ON c.customer_id = o.customer_id
        WHERE TRIM(t.trailer_number) = TRIM(?)
          AND t.voided = 0 AND t.is_reversal = 0
          AND tt.code IN ('LOAD', 'SHIP')
        ORDER BY t.transaction_id DESC
        LIMIT ?
        """,
        (trailer_number, limit),
        conn,
    )


def last_material_hauled(trailer_number: str, conn=None) -> str:
    history = trailer_history(trailer_number, limit=1, conn=conn)
    if not history:
        return ""
    row = history[0]
    return f"{row['material_number']} {row['material_description']}".strip()


def for_operation(
    operation: str,
    *,
    order_id: int | None = None,
    plant_id: int | None = None,
    trailer_number: str | None = None,
    conn=None,
) -> dict[str, Any]:
    """Suggested values for one of the plant-floor screens."""

    operation = operation.lower()
    shape = _SHAPE.get(operation)
    if shape is None:
        raise NotFound(f"{operation} is not an operation with a prefill.")

    from . import orders as orders_service

    suggestion: dict[str, Any] = {"user_date": today_iso()}
    notes: list[str] = []
    order = None

    if order_id:
        order = orders_service.get(order_id, conn)
        plant_id = plant_id or order["plant_id"]
        material_id = order["material_one_id"]
        remaining = round_lbs(
            max((order["material_one_quantity"] or 0) - order["qty_fulfilled"], 0)
        )
        if shape["from"]:
            suggestion["from_material_id"] = material_id
        if shape["to"]:
            suggestion["to_material_id"] = material_id
        if remaining > 0:
            suggestion["from_qty" if shape["from"] else "to_qty"] = remaining
            notes.append(f"{remaining:,.0f} lbs outstanding on order {order_id}")
        if order.get("trailer_number"):
            suggestion["trailer_number"] = order["trailer_number"]
        if order.get("department_id"):
            suggestion["department_id"] = order["department_id"]

        # Where the product should come from or go to.
        if operation == "receive":
            default = default_location(order["order_type_id"], plant_id, conn)
            if default:
                suggestion["to_location_id"] = default
                notes.append("Receiving location from the plant's default")
        elif operation in {"load", "ship", "shrink"} and material_id:
            holding = location_holding(material_id, plant_id, conn)
            if holding:
                suggestion["from_location_id"] = holding["location_id"]
                notes.append(
                    f"{holding['location_number']} holds the most "
                    f"{holding['material_number']} ({holding['balance']:,.0f} lbs)"
                )
            if operation == "load":
                trailer_stage = db.query_one(
                    """
                    SELECT location_id FROM location
                    WHERE plant_id = ? AND location_type_id =
                          (SELECT location_type_id FROM location_type WHERE name = 'Trailer')
                      AND active = 1
                    LIMIT 1
                    """,
                    (plant_id,),
                    conn,
                )
                if trailer_stage:
                    suggestion["to_location_id"] = trailer_stage["location_id"]
        elif operation == "produce" and material_id:
            default = default_location(order["order_type_id"], plant_id, conn)
            blend = db.query_one(
                """
                SELECT location_id FROM location
                WHERE plant_id = ? AND active = 1 AND location_type_id =
                      (SELECT location_type_id FROM location_type WHERE name = 'Blend')
                LIMIT 1
                """,
                (plant_id,),
                conn,
            )
            if blend:
                suggestion["from_location_id"] = blend["location_id"]
            if default:
                suggestion["to_location_id"] = default

    trailer = trailer_number or suggestion.get("trailer_number") or ""
    if trailer:
        previous = last_material_hauled(trailer, conn)
        if previous:
            suggestion["last_material_hauled"] = previous
            notes.append(f"Trailer {trailer} last hauled {previous}")

    # A BOL is minted when the transaction posts; show what it will be.
    if operation in {"receive", "load"}:
        suggestion["bol_preview"] = numbering.preview(order_id or 0, conn)["bol_number"]
        notes.append("BOL number is generated when you post")

    return {
        "operation": operation,
        "order_id": order_id,
        "plant_id": plant_id,
        "values": suggestion,
        "notes": notes,
        "trailer_history": trailer_history(trailer, conn=conn) if trailer else [],
    }


def for_qc(order_id: int, conn=None) -> dict[str, Any]:
    """Suggested values for the QC form, including the seals and BOL already
    recorded against this order's loads."""

    from . import qc as qc_service

    order = db.query_one(
        'SELECT o.*, p.code AS plant_code FROM "order" o'
        " JOIN plant p ON p.plant_id = o.plant_id WHERE o.order_id = ?",
        (order_id,),
        conn,
    )
    if order is None:
        raise NotFound(f"Order {order_id} was not found.")

    load = db.query_one(
        """
        SELECT t.to_bol, t.trailer_number
        FROM inventory_transaction t
        JOIN transaction_type tt ON tt.transaction_type_id = t.transaction_type_id
        WHERE t.order_id = ? AND tt.code = 'LOAD' AND t.voided = 0 AND t.is_reversal = 0
        ORDER BY t.transaction_id DESC LIMIT 1
        """,
        (order_id,),
        conn,
    )
    previous = db.query_one(
        "SELECT last_material_hauled, seal_number FROM qc"
        " WHERE order_id = ? AND active = 1 ORDER BY qc_id DESC LIMIT 1",
        (order_id,),
        conn,
    )

    values: dict[str, Any] = {"test_date": today_iso()}
    if load:
        values["bol_number"] = load["to_bol"]
        if load["trailer_number"]:
            values["last_material_hauled"] = last_material_hauled(
                load["trailer_number"], conn
            )
    if previous and not values.get("last_material_hauled"):
        values["last_material_hauled"] = previous["last_material_hauled"]

    auto = numbering.setting("sample.auto_generate", conn) == "true"
    return {
        "order_id": order_id,
        "values": values,
        "sample_auto_generate": auto,
        "sample_preview": numbering.preview(order_id, conn),
        "validation": qc_service.validate(order_id, values, conn),
    }
