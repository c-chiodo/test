"""Resolve a scanned or typed code to the thing it refers to.

A barcode scanner is a keyboard: it types the code and presses Enter. So one
box that works out what was scanned — an order, a sample, a BOL, a tank, a
product, a trailer — turns "find the order, open it, switch to the QC tab" into
one scan.

Ambiguity is real (a 5-digit material number and a 3-digit trailer both look
like numbers), so this returns every match it finds, best first, and lets the
operator pick when there is more than one.
"""

from __future__ import annotations

from typing import Any

from .. import db

MAX_HITS = 8


def resolve(code: str, plant_id: int | None = None, conn=None) -> dict[str, Any]:
    """Return ``{query, hits: [{type, label, sublabel, route, ...}]}``."""

    query = (code or "").strip()
    if not query:
        return {"query": query, "hits": []}

    hits: list[dict[str, Any]] = []
    hits.extend(_orders(query, conn))
    hits.extend(_samples(query, conn))
    hits.extend(_bols(query, conn))
    hits.extend(_locations(query, plant_id, conn))
    hits.extend(_materials(query, conn))
    hits.extend(_trailers(query, conn))

    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for hit in hits:
        key = (hit["type"], str(hit.get("id", hit["label"])))
        if key in seen:
            continue
        seen.add(key)
        unique.append(hit)
        if len(unique) >= MAX_HITS:
            break

    return {"query": query, "hits": unique}


def _orders(query: str, conn) -> list[dict]:
    if not query.isdigit():
        return []
    rows = db.query(
        """
        SELECT o.order_id, ot.code AS order_type, s.name AS status, p.code AS plant_code,
               m.number AS material_number, m.description AS material_description
        FROM "order" o
        JOIN order_type ot ON ot.order_type_id = o.order_type_id
        JOIN status s ON s.status_id = o.status_id
        JOIN plant p ON p.plant_id = o.plant_id
        LEFT JOIN material m ON m.material_id = o.material_one_id
        WHERE o.order_id = ? AND o.active = 1
        """,
        (int(query),),
        conn,
    )
    return [
        {
            "type": "order",
            "id": row["order_id"],
            "label": f"Order {row['order_id']}",
            "sublabel": (
                f"{row['order_type']} · {row['plant_code']} · {row['status']} · "
                f"{row['material_number'] or ''} {row['material_description'] or ''}"
            ).strip(),
            "route": f"orders/{row['order_id']}",
        }
        for row in rows
    ]


def _samples(query: str, conn) -> list[dict]:
    rows = db.query(
        """
        SELECT q.qc_id, q.order_id, q.sample_number, q.test_date, p.code AS plant_code
        FROM qc q
        JOIN "order" o ON o.order_id = q.order_id
        JOIN plant p ON p.plant_id = o.plant_id
        WHERE q.active = 1 AND UPPER(q.sample_number) = UPPER(?)
        ORDER BY q.qc_id DESC LIMIT 3
        """,
        (query,),
        conn,
    )
    hits = [
        {
            "type": "sample",
            "id": row["sample_number"],
            "label": f"Sample {row['sample_number']}",
            "sublabel": f"QC {row['qc_id']} on order {row['order_id']} · {row['plant_code']} · {row['test_date']}",
            "route": f"orders/{row['order_id']}",
            "order_id": row["order_id"],
        }
        for row in rows
    ]
    if hits:
        return hits
    # A sample logged in the lab but not yet recorded here is still a useful hit.
    known = db.query_one(
        "SELECT sample_code FROM lims_result WHERE UPPER(sample_code) = UPPER(?) LIMIT 1",
        (query,),
        conn,
    )
    if known:
        return [
            {
                "type": "sample",
                "id": known["sample_code"],
                "label": f"Sample {known['sample_code']}",
                "sublabel": "Has LIMS results but no QC record in PIMS",
                "route": "inquiry/qc",
            }
        ]
    return []


def _bols(query: str, conn) -> list[dict]:
    rows = db.query(
        """
        SELECT t.transaction_id, t.order_id, tt.code AS operation, p.code AS plant_code,
               COALESCE(NULLIF(t.to_bol, ''), t.from_bol) AS bol_number, t.trailer_number
        FROM inventory_transaction t
        JOIN transaction_type tt ON tt.transaction_type_id = t.transaction_type_id
        JOIN plant p ON p.plant_id = t.plant_id
        WHERE t.voided = 0
          AND (UPPER(t.to_bol) = UPPER(?) OR UPPER(t.from_bol) = UPPER(?))
        ORDER BY t.transaction_id DESC LIMIT 3
        """,
        (query, query),
        conn,
    )
    return [
        {
            "type": "bol",
            "id": row["transaction_id"],
            "label": f"BOL {row['bol_number']}",
            "sublabel": (
                f"{row['operation']} on order {row['order_id']} · {row['plant_code']}"
                + (f" · trailer {row['trailer_number']}" if row["trailer_number"] else "")
            ),
            "route": f"orders/{row['order_id']}",
            "order_id": row["order_id"],
        }
        for row in rows
    ]


def _locations(query: str, plant_id: int | None, conn) -> list[dict]:
    sql = """
        SELECT l.location_id, l.number, l.description, p.code AS plant_code
        FROM location l JOIN plant p ON p.plant_id = l.plant_id
        WHERE l.active = 1 AND UPPER(l.number) = UPPER(?)
    """
    params: list[Any] = [query]
    if plant_id:
        sql += " AND l.plant_id = ?"
        params.append(plant_id)
    return [
        {
            "type": "location",
            "id": row["location_id"],
            "label": row["number"],
            "sublabel": f"{row['description']} · {row['plant_code']}",
            "route": "inventory",
            "location_id": row["location_id"],
        }
        for row in db.query(sql + " LIMIT 3", params, conn)
    ]


def _materials(query: str, conn) -> list[dict]:
    return [
        {
            "type": "material",
            "id": row["material_id"],
            "label": f"{row['number']} {row['description']}",
            "sublabel": f"Product · {row['family'] or 'no family'}",
            "route": "specs",
            "material_id": row["material_id"],
        }
        for row in db.query(
            "SELECT material_id, number, description, family FROM material"
            " WHERE active = 1 AND UPPER(number) = UPPER(?) LIMIT 3",
            (query,),
            conn,
        )
    ]


def _trailers(query: str, conn) -> list[dict]:
    rows = db.query(
        """
        SELECT ps.stage_id, ps.order_id, ps.trailer_number, ps.quantity,
               c.name AS customer_name
        FROM pending_shipment ps
        JOIN "order" o ON o.order_id = ps.order_id
        LEFT JOIN customer c ON c.customer_id = o.customer_id
        WHERE ps.shipped = 0 AND TRIM(ps.trailer_number) = TRIM(?)
        ORDER BY ps.stage_id DESC LIMIT 3
        """,
        (query,),
        conn,
    )
    return [
        {
            "type": "trailer",
            "id": row["stage_id"],
            "label": f"Trailer {row['trailer_number']}",
            "sublabel": (
                f"Loaded and waiting to ship · order {row['order_id']}"
                + (f" · {row['customer_name']}" if row["customer_name"] else "")
            ),
            "route": "operations/ship",
            "stage_id": row["stage_id"],
            "order_id": row["order_id"],
        }
        for row in rows
    ]
