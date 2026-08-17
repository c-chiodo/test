"""Inventory movements — receive, produce, move, load, ship, shrink, adjust.

Every operation the legacy Order Selection menu offered posts through one
function, :func:`post`, so the rules that matter (does this location hold
enough product? does it fit? is the order still open? does the user work at
this plant?) are applied once and cannot be bypassed by a screen that forgot
to call the check.

The ledger is append-only. A mistake is corrected with :func:`void`, which
writes a reversing entry and leaves the original visible — inventory history
that reconciles is the whole point of the system.
"""

from __future__ import annotations

from typing import Any

from .. import audit, db
from ..errors import BusinessRuleError, NotFound, ValidationError
from ..security import require_permission, require_plant
from ..util import round_lbs, to_float, today_iso, utc_now_iso
from . import numbering

#: Shape of each operation: which endpoints it uses and what it means.
OPERATIONS: dict[str, dict[str, Any]] = {
    "RECEIVE": {"needs_from": False, "needs_to": True, "label": "Receive"},
    "PRODUCE": {"needs_from": True, "needs_to": True, "label": "Produce"},
    "MOVE": {"needs_from": True, "needs_to": True, "label": "Move"},
    "LOAD": {"needs_from": True, "needs_to": True, "label": "Load trailer"},
    "SHIP": {"needs_from": True, "needs_to": False, "label": "Ship trailer"},
    "SHRINK": {"needs_from": True, "needs_to": False, "label": "Shrinkage"},
    "ADJUST": {"needs_from": False, "needs_to": True, "label": "Adjustment"},
}

TXN_SELECT = """
    SELECT t.*, tt.code AS transaction_type, tt.description AS transaction_description,
           p.code AS plant_code, u.username, u.full_name,
           fm.number AS from_material_number, fm.description AS from_material_description,
           tm.number AS to_material_number, tm.description AS to_material_description,
           fl.number AS from_location_number, tl.number AS to_location_number,
           d.code AS department_code
    FROM inventory_transaction t
    JOIN transaction_type tt ON tt.transaction_type_id = t.transaction_type_id
    JOIN plant p  ON p.plant_id = t.plant_id
    JOIN app_user u ON u.user_id = t.user_id
    LEFT JOIN material fm ON fm.material_id = t.from_material_id
    LEFT JOIN material tm ON tm.material_id = t.to_material_id
    LEFT JOIN location fl ON fl.location_id = t.from_location_id
    LEFT JOIN location tl ON tl.location_id = t.to_location_id
    LEFT JOIN department d ON d.department_id = t.department_id
"""


# ------------------------------------------------------------------ balances


def location_balance(
    *,
    plant_id: int | None = None,
    location_id: int | None = None,
    material_id: int | None = None,
    as_of: str | None = None,
    include_zero: bool = False,
    conn=None,
) -> list[dict]:
    """Balance per (location, material), optionally as of a point in time.

    "Set Point in time" on the legacy inquiry screen — kept, because
    reconstructing what a tank held when a load went out is the question
    support gets asked.
    """

    params: list[Any] = []
    time_clause = ""
    if as_of:
        time_clause = " AND t.transaction_date <= ?"

    # Every row counts, reversals included: a voided transaction and its
    # reversal cancel out, which is what keeps the ledger and the balance in
    # agreement without ever deleting history.
    sql = f"""
        WITH ledger AS (
            SELECT t.to_location_id AS location_id, t.to_material_id AS material_id,
                   t.to_qty AS qty
            FROM inventory_transaction t
            WHERE t.to_location_id IS NOT NULL AND t.to_qty <> 0
              {time_clause}
            UNION ALL
            SELECT t.from_location_id, t.from_material_id, -t.from_qty
            FROM inventory_transaction t
            WHERE t.from_location_id IS NOT NULL AND t.from_qty <> 0
              {time_clause}
        )
        SELECT l.location_id, l.number AS location_number, l.description AS location_description,
               lt.name AS location_type, l.max_capacity, p.plant_id, p.code AS plant_code,
               m.material_id, m.number AS material_number, m.description AS material_description,
               ROUND(SUM(ledger.qty), 2) AS balance
        FROM ledger
        JOIN location l ON l.location_id = ledger.location_id
        JOIN location_type lt ON lt.location_type_id = l.location_type_id
        JOIN plant p ON p.plant_id = l.plant_id
        JOIN material m ON m.material_id = ledger.material_id
        WHERE 1 = 1
    """
    if as_of:
        params.extend([as_of, as_of])
    if plant_id:
        sql += " AND l.plant_id = ?"
        params.append(plant_id)
    if location_id:
        sql += " AND l.location_id = ?"
        params.append(location_id)
    if material_id:
        sql += " AND m.material_id = ?"
        params.append(material_id)
    sql += " GROUP BY l.location_id, m.material_id"
    if not include_zero:
        sql += " HAVING ROUND(SUM(ledger.qty), 2) <> 0"
    sql += " ORDER BY p.code, l.number, m.number"
    rows = db.query(sql, params, conn)
    for row in rows:
        cap = row.get("max_capacity")
        row["percent_full"] = (
            round(row["balance"] / cap * 100.0, 1) if cap else None
        )
    return rows


def balance_of(location_id: int, material_id: int, conn=None) -> float:
    rows = location_balance(location_id=location_id, material_id=material_id, conn=conn)
    return rows[0]["balance"] if rows else 0.0


def location_total(location_id: int, conn=None) -> float:
    rows = location_balance(location_id=location_id, conn=conn)
    return round(sum(r["balance"] for r in rows), 2)


# -------------------------------------------------------------------- posting


def _lookup_type_id(code: str, conn) -> int:
    row = db.query_one(
        "SELECT transaction_type_id FROM transaction_type WHERE code = ?", (code,), conn
    )
    if row is None:
        raise ValidationError(f"Unknown operation {code!r}.", operation=code)
    return row["transaction_type_id"]


def _location(location_id: int, conn) -> dict:
    row = db.query_one(
        "SELECT * FROM location WHERE location_id = ? AND active = 1", (location_id,), conn
    )
    if row is None:
        raise NotFound(f"Location {location_id} does not exist.")
    return row


def post(
    operation: str,
    payload: dict[str, Any],
    user: dict,
    conn=None,
    allow_negative: bool = False,
) -> dict:
    """Validate and write one inventory transaction.

    ``payload`` keys: order_id, plant_id, department_id, user_date,
    from_location_id, from_material_id, from_qty, from_bol,
    to_location_id, to_material_id, to_qty, to_bol,
    trailer_number, tank_hours, employee_hours, remarks.
    """

    require_permission(user, "txn.post")
    operation = operation.upper()
    spec = OPERATIONS.get(operation)
    if spec is None:
        raise ValidationError(
            f"{operation} is not an inventory operation.",
            allowed=sorted(OPERATIONS),
        )

    errors: dict[str, str] = {}
    plant_id = payload.get("plant_id")
    order_id = payload.get("order_id")

    order = None
    if order_id:
        order = db.query_one(
            'SELECT o.*, s.is_terminal, s.name AS status FROM "order" o'
            " JOIN status s ON s.status_id = o.status_id WHERE o.order_id = ?",
            (order_id,),
            conn,
        )
        if order is None:
            raise NotFound(f"Order {order_id} was not found.")
        if order["is_terminal"]:
            raise BusinessRuleError(
                f"Order {order_id} is {order['status']}; reopen it before posting.",
                order_id=order_id,
            )
        plant_id = plant_id or order["plant_id"]

    if not plant_id:
        errors["plant_id"] = "Choose a plant."
    else:
        require_plant(user, int(plant_id), conn)

    from_qty = to_float(payload.get("from_qty")) or 0.0
    to_qty = to_float(payload.get("to_qty")) or 0.0
    from_location_id = payload.get("from_location_id")
    to_location_id = payload.get("to_location_id")
    from_material_id = payload.get("from_material_id")
    to_material_id = payload.get("to_material_id")

    if spec["needs_from"]:
        if not from_location_id:
            errors["from_location_id"] = "Choose a from location."
        if not from_material_id:
            errors["from_material_id"] = "Choose the material being taken."
        if from_qty <= 0:
            errors["from_qty"] = "Enter a quantity greater than zero."
    if spec["needs_to"]:
        if not to_location_id:
            errors["to_location_id"] = "Choose a to location."
        if not to_material_id:
            errors["to_material_id"] = "Choose the material being put away."
        if to_qty <= 0 and operation != "PRODUCE":
            # Produce may legitimately yield less than it consumed; the caller
            # supplies the produced quantity explicitly.
            to_qty = to_qty or from_qty
        if to_qty <= 0:
            errors["to_qty"] = "Enter a quantity greater than zero."

    if operation == "MOVE" and from_location_id and from_location_id == to_location_id:
        errors["to_location_id"] = "From and to locations must differ."

    if errors:
        raise ValidationError("This transaction cannot be posted.", fields=errors)

    # Locations must belong to the plant being posted against.
    for key, loc_id in (("from_location_id", from_location_id), ("to_location_id", to_location_id)):
        if loc_id:
            loc = _location(loc_id, conn)
            if loc["plant_id"] != plant_id:
                raise ValidationError(
                    f"{loc['number']} belongs to another plant.",
                    fields={key: "Location is not at this plant."},
                )
            if loc["bol_required"] and key == "to_location_id" and not payload.get("to_bol"):
                if operation in {"RECEIVE", "LOAD"}:
                    # The BOL series is ours, so mint one rather than making an
                    # operator type it. Turn off with bol.auto_generate=false
                    # and the old "enter the BOL number" rule comes back.
                    if numbering.setting("bol.auto_generate", conn) != "false":
                        payload = {
                            **payload,
                            "to_bol": numbering.next_bol(payload.get("trailer_number"), conn),
                        }
                    else:
                        raise ValidationError(
                            f"{loc['number']} requires a BOL number.",
                            fields={"to_bol": "Enter the BOL number."},
                        )

    # Stock check.
    if spec["needs_from"] and not allow_negative:
        available = balance_of(from_location_id, from_material_id, conn)
        if from_qty - available > 0.01:
            from_loc = _location(from_location_id, conn)
            material = db.query_one(
                "SELECT number, description FROM material WHERE material_id = ?",
                (from_material_id,),
                conn,
            )
            raise BusinessRuleError(
                f"{from_loc['number']} holds {round_lbs(available):,.0f} lbs of "
                f"{material['number']} — cannot take {round_lbs(from_qty):,.0f} lbs.",
                available=round_lbs(available),
                requested=round_lbs(from_qty),
                location=from_loc["number"],
                material=material["number"],
            )

    # Capacity check.
    if spec["needs_to"] and to_location_id:
        to_loc = _location(to_location_id, conn)
        cap = to_loc.get("max_capacity")
        if cap:
            current = location_total(to_location_id, conn)
            if current + to_qty - cap > 0.01:
                raise BusinessRuleError(
                    f"{to_loc['number']} holds {round_lbs(current):,.0f} lbs of "
                    f"{round_lbs(cap):,.0f} lbs capacity — {round_lbs(to_qty):,.0f} "
                    f"lbs will not fit.",
                    capacity=round_lbs(cap),
                    current=round_lbs(current),
                    requested=round_lbs(to_qty),
                    location=to_loc["number"],
                )

    row = {
        "transaction_type_id": _lookup_type_id(operation, conn),
        "parent_transaction_id": payload.get("parent_transaction_id"),
        "order_id": order_id,
        "plant_id": plant_id,
        "department_id": payload.get("department_id")
        or (order["department_id"] if order else None),
        "transaction_date": utc_now_iso(),
        "user_date": str(payload.get("user_date") or today_iso())[:10],
        "user_id": user["user_id"],
        "from_material_id": from_material_id if spec["needs_from"] else None,
        "from_location_id": from_location_id if spec["needs_from"] else None,
        "from_qty": round_lbs(from_qty) if spec["needs_from"] else 0,
        "from_bol": payload.get("from_bol") or "",
        "to_material_id": to_material_id if spec["needs_to"] else None,
        "to_location_id": to_location_id if spec["needs_to"] else None,
        "to_qty": round_lbs(to_qty) if spec["needs_to"] else 0,
        "to_bol": payload.get("to_bol") or "",
        "trailer_number": payload.get("trailer_number") or "",
        "tank_hours": to_float(payload.get("tank_hours")),
        "employee_hours": to_float(payload.get("employee_hours")),
        "remarks": payload.get("remarks") or "",
    }

    with db.transaction(conn):
        txn_id = db.insert("inventory_transaction", row, conn)
        if operation == "LOAD":
            db.insert(
                "pending_shipment",
                {
                    "order_id": order_id,
                    "transaction_id": txn_id,
                    "trailer_number": row["trailer_number"],
                    "quantity": row["from_qty"],
                    "shipped": 0,
                },
                conn,
            )
        if payload.get("scale_reading_id"):
            from ..integrations import scale as scale_integration

            scale_integration.consume(int(payload["scale_reading_id"]), txn_id, conn)
        if order_id and order and order["status_id"] == 1:
            db.update("order", {"order_id": order_id}, {"status_id": 2}, conn)
        audit.record(
            username=user["username"],
            action=f"post.{operation.lower()}",
            entity="transaction",
            entity_id=txn_id,
            summary=(
                f"{OPERATIONS[operation]['label']} "
                f"{round_lbs(from_qty or to_qty):,.0f} lbs"
                + (f" on order {order_id}" if order_id else "")
            ),
            detail={"values": row},
            conn=conn,
        )
    return get(txn_id, conn)


def get(transaction_id: int, conn=None) -> dict:
    row = db.query_one(TXN_SELECT + " WHERE t.transaction_id = ?", (transaction_id,), conn)
    if row is None:
        raise NotFound(f"Transaction {transaction_id} was not found.")
    return row


def void(transaction_id: int, reason: str, user: dict, conn=None) -> dict:
    """Reverse a transaction with an offsetting entry. Never deletes."""

    require_permission(user, "txn.void")
    original = get(transaction_id, conn)
    if original["voided"]:
        raise BusinessRuleError(
            f"Transaction {transaction_id} is already voided.", transaction_id=transaction_id
        )
    if not reason.strip():
        raise ValidationError(
            "A reason is required to void a transaction.",
            fields={"reason": "Explain why this is being reversed."},
        )
    require_plant(user, original["plant_id"], conn)

    with db.transaction(conn):
        reversal = {
            "transaction_type_id": original["transaction_type_id"],
            "parent_transaction_id": transaction_id,
            "order_id": original["order_id"],
            "plant_id": original["plant_id"],
            "department_id": original["department_id"],
            "transaction_date": utc_now_iso(),
            "user_date": today_iso(),
            "user_id": user["user_id"],
            # Swap the endpoints: what went out comes back, what came in goes out.
            "from_material_id": original["to_material_id"],
            "from_location_id": original["to_location_id"],
            "from_qty": original["to_qty"],
            "to_material_id": original["from_material_id"],
            "to_location_id": original["from_location_id"],
            "to_qty": original["from_qty"],
            "trailer_number": original["trailer_number"],
            "remarks": f"Reversal of transaction {transaction_id}: {reason.strip()}",
            "is_reversal": 1,
        }
        reversal_id = db.insert("inventory_transaction", reversal, conn)
        db.update(
            "inventory_transaction", {"transaction_id": transaction_id}, {"voided": 1}, conn
        )
        db.execute(
            "UPDATE pending_shipment SET shipped = 1 WHERE transaction_id = ? AND shipped = 0",
            (transaction_id,),
            conn,
        )
        audit.record(
            username=user["username"],
            action="void",
            entity="transaction",
            entity_id=transaction_id,
            summary=f"Voided transaction {transaction_id}",
            detail={"reason": reason, "reversal_id": reversal_id},
            conn=conn,
        )
    return get(reversal_id, conn)


# ----------------------------------------------------------------- shipping


def pending_shipments(
    plant_id: int | None = None, order_id: int | None = None, conn=None
) -> list[dict]:
    sql = """
        SELECT ps.stage_id, ps.order_id, ps.transaction_id, ps.trailer_number,
               ps.quantity, o.plant_id, p.code AS plant_code,
               c.name AS customer_name, t.to_bol AS bol_number,
               m.number AS material_number, m.description AS material_description,
               t.transaction_date AS loaded_at
        FROM pending_shipment ps
        JOIN "order" o ON o.order_id = ps.order_id
        JOIN plant p ON p.plant_id = o.plant_id
        JOIN inventory_transaction t ON t.transaction_id = ps.transaction_id
        LEFT JOIN customer c ON c.customer_id = o.customer_id
        LEFT JOIN material m ON m.material_id = o.material_one_id
        WHERE ps.shipped = 0 AND t.voided = 0
    """
    params: list[Any] = []
    if plant_id:
        sql += " AND o.plant_id = ?"
        params.append(plant_id)
    if order_id:
        sql += " AND ps.order_id = ?"
        params.append(order_id)
    return db.query(sql + " ORDER BY c.name, ps.order_id", params, conn)


def ship(stage_id: int, user: dict, conn=None, user_date: str | None = None) -> dict:
    """Ship a loaded trailer: consume the staged quantity and close the stage."""

    require_permission(user, "txn.post")
    stage = db.query_one(
        """
        SELECT ps.*, t.to_location_id, t.to_material_id, t.plant_id, t.to_bol
        FROM pending_shipment ps
        JOIN inventory_transaction t ON t.transaction_id = ps.transaction_id
        WHERE ps.stage_id = ?
        """,
        (stage_id,),
        conn,
    )
    if stage is None:
        raise NotFound(f"Staged load {stage_id} was not found.")
    if stage["shipped"]:
        raise BusinessRuleError("That trailer has already shipped.", stage_id=stage_id)

    with db.transaction(conn):
        txn = post(
            "SHIP",
            {
                "order_id": stage["order_id"],
                "plant_id": stage["plant_id"],
                "from_location_id": stage["to_location_id"],
                "from_material_id": stage["to_material_id"],
                "from_qty": stage["quantity"],
                "from_bol": stage["to_bol"],
                "trailer_number": stage["trailer_number"],
                "parent_transaction_id": stage["transaction_id"],
                "user_date": user_date,
                "remarks": "Shipped",
            },
            user,
            conn,
        )
        db.update("pending_shipment", {"stage_id": stage_id}, {"shipped": 1}, conn)
    return txn


def bill_of_lading(order_id: int, conn=None) -> dict:
    """Data for the BOL document the legacy ReportViewer printed."""

    from . import orders as orders_service

    order = orders_service.get(order_id, conn)
    loads = db.query(
        """
        SELECT t.transaction_id, t.to_bol AS bol_number, t.trailer_number, t.from_qty AS quantity,
               t.transaction_date, m.number AS material_number, m.description AS material_description,
               u.full_name AS loaded_by
        FROM inventory_transaction t
        JOIN transaction_type tt ON tt.transaction_type_id = t.transaction_type_id
        JOIN app_user u ON u.user_id = t.user_id
        LEFT JOIN material m ON m.material_id = t.from_material_id
        WHERE t.order_id = ? AND tt.code = 'LOAD' AND t.voided = 0
        ORDER BY t.transaction_id
        """,
        (order_id,),
        conn,
    )
    qc_rows = db.query(
        "SELECT qc_id, sample_number, seal_number, bol_number, test_date"
        " FROM qc WHERE order_id = ? AND active = 1 ORDER BY qc_id DESC",
        (order_id,),
        conn,
    )
    return {
        "shipper": {
            "name": "FEED ENERGY COMPANY",
            "address": "3121 Dean Avenue, Des Moines, IA 50317",
        },
        "order": order,
        "loads": loads,
        "qc": qc_rows,
        "total_quantity": round(sum(load["quantity"] for load in loads), 2),
    }


def activity(
    *,
    plant_id: int | None = None,
    order_id: int | None = None,
    location_id: int | None = None,
    material_id: int | None = None,
    operation: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    include_voided: bool = False,
    limit: int = 500,
    conn=None,
) -> list[dict]:
    """The Activity inquiry: the ledger, filtered."""

    sql = TXN_SELECT + " WHERE 1 = 1"
    params: list[Any] = []
    if not include_voided:
        # Hide reversed pairs from the working view; the support console and
        # the "show voided" toggle bring them back.
        sql += " AND t.voided = 0 AND t.is_reversal = 0"
    if plant_id:
        sql += " AND t.plant_id = ?"
        params.append(plant_id)
    if order_id:
        sql += " AND t.order_id = ?"
        params.append(order_id)
    if location_id:
        sql += " AND (t.from_location_id = ? OR t.to_location_id = ?)"
        params.extend([location_id, location_id])
    if material_id:
        sql += " AND (t.from_material_id = ? OR t.to_material_id = ?)"
        params.extend([material_id, material_id])
    if operation:
        sql += " AND tt.code = ?"
        params.append(operation.upper())
    if date_from:
        sql += " AND t.user_date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND t.user_date <= ?"
        params.append(date_to)
    sql += " ORDER BY t.transaction_id DESC LIMIT ?"
    params.append(limit)
    return db.query(sql, params, conn)
