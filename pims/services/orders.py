"""Orders: the spine of the system.

Mirrors the legacy Order Edit / Order Selection menus — create, edit, filter,
close — plus the progress numbers those grids showed (Qty Ordered / Qty Loaded /
Qty Complete / Percent Completed), computed here from the transaction ledger
rather than stored on the order row where they could drift.
"""

from __future__ import annotations

from typing import Any

from .. import audit, db
from ..errors import BusinessRuleError, NotFound, ValidationError
from ..security import require_permission, require_plant
from ..util import parse_dt, today_iso, utc_now_iso

# Which transaction types count toward "fulfilled" for each order type.
PROGRESS_TYPES = {
    1: ("LOAD",),        # SO  — loaded onto trailers
    2: ("PRODUCE",),     # WO  — produced into a location
    3: ("RECEIVE",),     # PO  — received in
    4: ("MOVE",),        # TO  — moved between locations
}

ORDER_SELECT = """
    SELECT o.*, ot.code AS order_type, ot.description AS order_type_description,
           p.code AS plant_code, p.name AS plant_name,
           d.code AS department_code,
           s.name AS status, s.is_terminal,
           c.name AS customer_name, c.gp_custnmbr,
           v.name AS vendor_name, v.gp_vendorid,
           m1.number AS material_one_number, m1.description AS material_one_description,
           m2.number AS material_two_number, m3.number AS material_three_number,
           m4.number AS material_four_number,
           co.name AS company_name
    FROM "order" o
    JOIN order_type ot ON ot.order_type_id = o.order_type_id
    JOIN plant p       ON p.plant_id = o.plant_id
    JOIN status s      ON s.status_id = o.status_id
    JOIN company co    ON co.company_id = o.company_id
    LEFT JOIN department d ON d.department_id = o.department_id
    LEFT JOIN customer c   ON c.customer_id = o.customer_id
    LEFT JOIN vendor v     ON v.vendor_id = o.vendor_id
    LEFT JOIN material m1  ON m1.material_id = o.material_one_id
    LEFT JOIN material m2  ON m2.material_id = o.material_two_id
    LEFT JOIN material m3  ON m3.material_id = o.material_three_id
    LEFT JOIN material m4  ON m4.material_id = o.material_four_id
"""


def search(
    *,
    plant_id: int | None = None,
    order_type_id: int | None = None,
    department_id: int | None = None,
    status_id: int | None = None,
    material_id: int | None = None,
    customer_id: int | None = None,
    vendor_id: int | None = None,
    due_from: str | None = None,
    due_to: str | None = None,
    text: str | None = None,
    open_only: bool = False,
    limit: int = 200,
    offset: int = 0,
    conn=None,
) -> dict[str, Any]:
    """The Order Selection grid, with the same filters plus free text."""

    where: list[str] = ["o.active = 1"]
    params: list[Any] = []
    if plant_id:
        where.append("o.plant_id = ?")
        params.append(plant_id)
    if order_type_id:
        where.append("o.order_type_id = ?")
        params.append(order_type_id)
    if department_id:
        where.append("o.department_id = ?")
        params.append(department_id)
    if status_id:
        where.append("o.status_id = ?")
        params.append(status_id)
    if material_id:
        where.append(
            "(o.material_one_id = ? OR o.material_two_id = ? OR o.material_three_id = ?"
            " OR o.material_four_id = ?)"
        )
        params.extend([material_id] * 4)
    if customer_id:
        where.append("o.customer_id = ?")
        params.append(customer_id)
    if vendor_id:
        where.append("o.vendor_id = ?")
        params.append(vendor_id)
    if due_from:
        where.append("o.due_date >= ?")
        params.append(due_from)
    if due_to:
        where.append("o.due_date <= ?")
        params.append(due_to)
    if open_only:
        where.append("s.is_terminal = 0")
    if text:
        needle = f"%{text.strip()}%"
        where.append(
            "(CAST(o.order_id AS TEXT) LIKE ? OR o.order_reference LIKE ?"
            " OR o.blend_serial_number LIKE ? OR o.trailer_number LIKE ?"
            " OR c.name LIKE ? OR v.name LIKE ? OR m1.number LIKE ?"
            " OR m1.description LIKE ?)"
        )
        params.extend([needle] * 8)

    clause = " WHERE " + " AND ".join(where)
    total = db.scalar(
        "SELECT COUNT(*) FROM \"order\" o"
        " JOIN status s ON s.status_id = o.status_id"
        " LEFT JOIN customer c ON c.customer_id = o.customer_id"
        " LEFT JOIN vendor v ON v.vendor_id = o.vendor_id"
        " LEFT JOIN material m1 ON m1.material_id = o.material_one_id" + clause,
        params,
        conn,
    )
    rows = db.query(
        ORDER_SELECT + clause + " ORDER BY o.due_date DESC, o.order_id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
        conn,
    )
    for row in rows:
        row.update(progress(row["order_id"], row["order_type_id"], conn))
    return {"total": total, "rows": rows, "limit": limit, "offset": offset}


def get(order_id: int, conn=None) -> dict:
    row = db.query_one(ORDER_SELECT + " WHERE o.order_id = ?", (order_id,), conn)
    if row is None:
        raise NotFound(f"Order {order_id} was not found.")
    row.update(progress(order_id, row["order_type_id"], conn))
    return row


def progress(order_id: int, order_type_id: int, conn=None) -> dict[str, Any]:
    """Quantity fulfilled against the order, from the ledger."""

    codes = PROGRESS_TYPES.get(order_type_id, ())
    if not codes:
        return {"qty_fulfilled": 0.0, "percent_complete": 0.0, "qty_shipped": 0.0}
    marks = ", ".join("?" for _ in codes)
    fulfilled = db.scalar(
        f"""
        SELECT COALESCE(SUM(CASE WHEN tt.code = 'RECEIVE' OR tt.code = 'PRODUCE'
                                 THEN t.to_qty ELSE t.from_qty END), 0)
        FROM inventory_transaction t
        JOIN transaction_type tt ON tt.transaction_type_id = t.transaction_type_id
        WHERE t.order_id = ? AND t.voided = 0 AND t.is_reversal = 0
          AND tt.code IN ({marks})
        """,
        [order_id, *codes],
        conn,
    )
    shipped = db.scalar(
        """
        SELECT COALESCE(SUM(t.from_qty), 0)
        FROM inventory_transaction t
        JOIN transaction_type tt ON tt.transaction_type_id = t.transaction_type_id
        WHERE t.order_id = ? AND t.voided = 0 AND t.is_reversal = 0 AND tt.code = 'SHIP'
        """,
        (order_id,),
        conn,
    )
    ordered = db.scalar(
        'SELECT material_one_quantity FROM "order" WHERE order_id = ?', (order_id,), conn
    ) or 0
    pct = (fulfilled / ordered * 100.0) if ordered else 0.0
    return {
        "qty_fulfilled": round(float(fulfilled), 2),
        "qty_shipped": round(float(shipped), 2),
        "percent_complete": round(min(pct, 999.0), 2),
    }


def _validate(payload: dict, conn) -> dict:
    """Shared create/update validation. Raises with field-level detail."""

    errors: dict[str, str] = {}

    order_type_id = payload.get("order_type_id")
    if not order_type_id:
        errors["order_type_id"] = "Choose an order type."
    plant_id = payload.get("plant_id")
    if not plant_id:
        errors["plant_id"] = "Choose a plant."
    if not payload.get("company_id"):
        errors["company_id"] = "Choose a company."

    due = parse_dt(payload.get("due_date"))
    order_date = parse_dt(payload.get("order_date"))
    if due is None:
        errors["due_date"] = "Enter a due date."
    if order_date and due and due < order_date:
        errors["due_date"] = "Due date cannot be before the order date."

    material_one_id = payload.get("material_one_id")
    if not material_one_id:
        errors["material_one_id"] = "Choose the primary material."
    elif plant_id and not db.query_one(
        "SELECT 1 FROM material_plant WHERE material_id = ? AND plant_id = ?",
        (material_one_id, plant_id),
        conn,
    ):
        errors["material_one_id"] = "That material is not set up at this plant."

    qty = payload.get("material_one_quantity")
    if qty is None or float(qty) <= 0:
        errors["material_one_quantity"] = "Enter a quantity greater than zero."

    if order_type_id == 1 and not payload.get("customer_id"):
        errors["customer_id"] = "A sales order needs a customer."
    if order_type_id == 3 and not payload.get("vendor_id"):
        errors["vendor_id"] = "A purchase order needs a vendor."

    if errors:
        raise ValidationError("This order cannot be saved yet.", fields=errors)
    return payload


def create(payload: dict, user: dict, conn=None) -> list[dict]:
    """Create one order — or ``count`` identical ones, as the legacy dialog did."""

    require_permission(user, "order.write")
    payload = dict(payload)
    count = int(payload.pop("count", 1) or 1)
    if count < 1 or count > 50:
        raise ValidationError("Create between 1 and 50 orders at a time.", count=count)
    payload.setdefault("order_date", today_iso())
    _validate(payload, conn)
    require_plant(user, int(payload["plant_id"]), conn)

    created: list[dict] = []
    with db.transaction(conn):
        next_id = (db.scalar('SELECT MAX(order_id) FROM "order"', (), conn) or 328_000) + 1
        for i in range(count):
            order_id = next_id + i
            row = {
                "order_id": order_id,
                "order_type_id": payload["order_type_id"],
                "order_date": str(payload["order_date"])[:10],
                "due_date": str(payload["due_date"])[:10],
                "order_reference": payload.get("order_reference") or "",
                "company_id": payload["company_id"],
                "plant_id": payload["plant_id"],
                "department_id": payload.get("department_id"),
                "blend_serial_number": payload.get("blend_serial_number") or "",
                "vendor_id": payload.get("vendor_id"),
                "customer_id": payload.get("customer_id"),
                "material_one_id": payload["material_one_id"],
                "material_two_id": payload.get("material_two_id"),
                "material_three_id": payload.get("material_three_id"),
                "material_four_id": payload.get("material_four_id"),
                "material_one_quantity": float(payload["material_one_quantity"]),
                "ship_method": payload.get("ship_method") or "",
                "trailer_number": payload.get("trailer_number") or "",
                "load_by_eta": payload.get("load_by_eta"),
                "comments": payload.get("comments") or "",
                "status_id": payload.get("status_id") or 1,
                "date_added": utc_now_iso(),
                "added_by": user["username"],
            }
            db.insert("order", row, conn)
            audit.record(
                username=user["username"],
                action="create",
                entity="order",
                entity_id=order_id,
                summary=f"Created order {order_id}",
                detail={"values": row},
                conn=conn,
            )
            created.append(get(order_id, conn))
    return created


def update(order_id: int, payload: dict, user: dict, conn=None) -> dict:
    require_permission(user, "order.write")
    before = get(order_id, conn)
    if before["is_terminal"]:
        raise BusinessRuleError(
            f"Order {order_id} is {before['status']} and can no longer be edited.",
            order_id=order_id,
            status=before["status"],
        )
    require_plant(user, before["plant_id"], conn)

    merged = {**before, **{k: v for k, v in payload.items() if v is not None or k in payload}}
    _validate(merged, conn)
    require_plant(user, int(merged["plant_id"]), conn)

    editable = {
        "order_type_id",
        "order_date",
        "due_date",
        "order_reference",
        "company_id",
        "plant_id",
        "department_id",
        "blend_serial_number",
        "vendor_id",
        "customer_id",
        "material_one_id",
        "material_two_id",
        "material_three_id",
        "material_four_id",
        "material_one_quantity",
        "ship_method",
        "trailer_number",
        "load_by_eta",
        "comments",
        "status_id",
    }
    values = {k: merged[k] for k in editable if k in merged}
    values["date_modified"] = utc_now_iso()
    values["modified_by"] = user["username"]
    with db.transaction(conn):
        db.update("order", {"order_id": order_id}, values, conn)
        changes = audit.diff(
            {k: before.get(k) for k in editable}, {k: values.get(k) for k in editable}
        )
        audit.record(
            username=user["username"],
            action="update",
            entity="order",
            entity_id=order_id,
            summary=f"Updated order {order_id}",
            detail={"changes": changes},
            conn=conn,
        )
    return get(order_id, conn)


def close(order_ids: list[int], user: dict, conn=None, force: bool = False) -> dict:
    """Close orders, reporting per-order outcomes instead of failing the batch."""

    require_permission(user, "order.close")
    closed: list[int] = []
    skipped: list[dict] = []
    with db.transaction(conn):
        for order_id in order_ids:
            try:
                order = get(order_id, conn)
            except NotFound:
                skipped.append({"order_id": order_id, "reason": "not found"})
                continue
            if order["is_terminal"]:
                skipped.append(
                    {"order_id": order_id, "reason": f"already {order['status']}"}
                )
                continue
            pending = db.scalar(
                "SELECT COUNT(*) FROM pending_shipment WHERE order_id = ? AND shipped = 0",
                (order_id,),
                conn,
            )
            if pending and not force:
                skipped.append(
                    {
                        "order_id": order_id,
                        "reason": f"{pending} trailer(s) loaded but not shipped",
                    }
                )
                continue
            db.update(
                "order",
                {"order_id": order_id},
                {
                    "status_id": 4,
                    "date_modified": utc_now_iso(),
                    "modified_by": user["username"],
                },
                conn,
            )
            audit.record(
                username=user["username"],
                action="close",
                entity="order",
                entity_id=order_id,
                summary=f"Closed order {order_id}",
                detail={"forced": force},
                conn=conn,
            )
            closed.append(order_id)
    return {"closed": closed, "skipped": skipped}


def dashboard(plant_id: int | None = None, conn=None) -> dict[str, Any]:
    """Headline numbers for the landing page."""

    params: list[Any] = []
    plant_clause = ""
    if plant_id:
        plant_clause = " AND o.plant_id = ?"
        params.append(plant_id)

    open_orders = db.scalar(
        'SELECT COUNT(*) FROM "order" o JOIN status s ON s.status_id = o.status_id'
        " WHERE o.active = 1 AND s.is_terminal = 0" + plant_clause,
        params,
        conn,
    )
    overdue = db.scalar(
        'SELECT COUNT(*) FROM "order" o JOIN status s ON s.status_id = o.status_id'
        " WHERE o.active = 1 AND s.is_terminal = 0 AND o.due_date < ?" + plant_clause,
        [today_iso(), *params],
        conn,
    )
    awaiting_ship = db.scalar(
        'SELECT COUNT(*) FROM pending_shipment ps JOIN "order" o ON o.order_id = ps.order_id'
        " WHERE ps.shipped = 0" + plant_clause,
        params,
        conn,
    )
    by_type = db.query(
        'SELECT ot.code, COUNT(*) AS count FROM "order" o'
        " JOIN order_type ot ON ot.order_type_id = o.order_type_id"
        " JOIN status s ON s.status_id = o.status_id"
        " WHERE o.active = 1 AND s.is_terminal = 0" + plant_clause + " GROUP BY ot.code",
        params,
        conn,
    )
    recent_txn = db.scalar(
        "SELECT COUNT(*) FROM inventory_transaction t"
        " WHERE t.voided = 0 AND t.transaction_date >= datetime('now', '-1 day')"
        + (" AND t.plant_id = ?" if plant_id else ""),
        params,
        conn,
    )
    return {
        "open_orders": open_orders,
        "overdue_orders": overdue,
        "awaiting_shipment": awaiting_ship,
        "open_by_type": {r["code"]: r["count"] for r in by_type},
        "transactions_24h": recent_txn,
    }
