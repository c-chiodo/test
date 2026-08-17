"""Custom Query — the query builder, rebuilt safely.

The legacy Query Builder let staff assemble a report from a table family, a
table and a field, with where clauses, prompts and sorting. It was the
most-used tool in PIMS and worth keeping exactly as a concept.

What changes: queries run against a fixed set of curated views with a
whitelisted field list, and every value is bound as a parameter. A user cannot
reach a table they have no business reading, and cannot type SQL into a filter.
The generated statement is returned with the results, so what ran is never a
mystery to whoever gets the support call.
"""

from __future__ import annotations

import json
from typing import Any

from .. import audit, db
from ..errors import NotFound, ValidationError
from ..security import require_permission
from ..util import rows_to_csv, utc_now_iso

# ---------------------------------------------------------------- catalogue


def _field(name: str, expr: str, label: str, type_: str = "text") -> dict:
    return {"name": name, "expr": expr, "label": label, "type": type_}


SOURCES: dict[str, dict[str, Any]] = {
    "orders": {
        "label": "Orders",
        "description": "Order header with plant, material, customer and vendor",
        "from": """
            "order" o
            JOIN order_type ot ON ot.order_type_id = o.order_type_id
            JOIN plant p ON p.plant_id = o.plant_id
            JOIN status s ON s.status_id = o.status_id
            LEFT JOIN department d ON d.department_id = o.department_id
            LEFT JOIN customer c ON c.customer_id = o.customer_id
            LEFT JOIN vendor v ON v.vendor_id = o.vendor_id
            LEFT JOIN material m ON m.material_id = o.material_one_id
        """,
        "where": "o.active = 1",
        "fields": [
            _field("order_id", "o.order_id", "Order Id", "number"),
            _field("order_type", "ot.code", "Type"),
            _field("order_date", "o.order_date", "Order Date", "date"),
            _field("due_date", "o.due_date", "Due Date", "date"),
            _field("plant_code", "p.code", "Plant"),
            _field("department_code", "d.code", "Dept"),
            _field("status", "s.name", "Status"),
            _field("material_number", "m.number", "Mat 1"),
            _field("material_description", "m.description", "Mat 1 Description"),
            _field("quantity", "o.material_one_quantity", "Qty Ordered", "number"),
            _field("customer_name", "c.name", "Customer"),
            _field("vendor_name", "v.name", "Vendor"),
            _field("order_reference", "o.order_reference", "Reference"),
            _field("blend_serial_number", "o.blend_serial_number", "Blend SN"),
            _field("trailer_number", "o.trailer_number", "Trailer #"),
            _field("comments", "o.comments", "Comments"),
            _field("added_by", "o.added_by", "Added By"),
            _field("date_added", "o.date_added", "Date Added", "date"),
        ],
    },
    "transactions": {
        "label": "Inventory activity",
        "description": "Every posted movement, with from/to locations and quantities",
        "from": """
            inventory_transaction t
            JOIN transaction_type tt ON tt.transaction_type_id = t.transaction_type_id
            JOIN plant p ON p.plant_id = t.plant_id
            JOIN app_user u ON u.user_id = t.user_id
            LEFT JOIN material fm ON fm.material_id = t.from_material_id
            LEFT JOIN material tm ON tm.material_id = t.to_material_id
            LEFT JOIN location fl ON fl.location_id = t.from_location_id
            LEFT JOIN location tl ON tl.location_id = t.to_location_id
        """,
        "where": "t.voided = 0",
        "fields": [
            _field("transaction_id", "t.transaction_id", "Trans ID", "number"),
            _field("order_id", "t.order_id", "Order Id", "number"),
            _field("operation", "tt.code", "Type"),
            _field("plant_code", "p.code", "Plant"),
            _field("user_date", "t.user_date", "User Trans Date", "date"),
            _field("transaction_date", "t.transaction_date", "Actual Trans Date", "date"),
            _field("username", "u.username", "User"),
            _field("from_material", "fm.number", "From Mat"),
            _field("from_location", "fl.number", "From Loc"),
            _field("from_qty", "t.from_qty", "From Qty", "number"),
            _field("from_bol", "t.from_bol", "From BOL"),
            _field("to_material", "tm.number", "To Mat"),
            _field("to_location", "tl.number", "To Loc"),
            _field("to_qty", "t.to_qty", "To Qty", "number"),
            _field("to_bol", "t.to_bol", "To BOL"),
            _field("trailer_number", "t.trailer_number", "Trailer #"),
            _field("tank_hours", "t.tank_hours", "Tank Time", "number"),
            _field("employee_hours", "t.employee_hours", "Labor", "number"),
            _field("remarks", "t.remarks", "Remarks"),
        ],
    },
    "qc": {
        "label": "Quality control",
        "description": "QC records with the order and product they belong to",
        "from": """
            qc q
            JOIN "order" o ON o.order_id = q.order_id
            JOIN order_type ot ON ot.order_type_id = o.order_type_id
            JOIN plant p ON p.plant_id = o.plant_id
            LEFT JOIN material m ON m.material_id = o.material_one_id
            LEFT JOIN customer c ON c.customer_id = o.customer_id
        """,
        "where": "q.active = 1",
        "fields": [
            _field("qc_id", "q.qc_id", "QC Id", "number"),
            _field("order_id", "q.order_id", "Order Id", "number"),
            _field("order_type", "ot.code", "Type"),
            _field("plant_code", "p.code", "Plant"),
            _field("test_date", "q.test_date", "Test Date", "date"),
            _field("sample_number", "q.sample_number", "Sample #"),
            _field("bol_number", "q.bol_number", "BOL #"),
            _field("material_number", "m.number", "Material"),
            _field("material_description", "m.description", "Material Description"),
            _field("customer_name", "c.name", "Customer"),
            _field("moisture", "q.moisture", "Moisture", "number"),
            _field("temp", "q.temp", "Temp", "number"),
            _field("ph", "q.ph", "pH", "number"),
            _field("ffa", "q.ffa", "FFA", "number"),
            _field("tfa", "q.tfa", "TFA", "number"),
            _field("spintest_fallout", "q.spintest_fallout", "Spintest", "number"),
            _field("flash_pf", "q.flash_pf", "Flash"),
            _field("seal_number", "q.seal_number", "Seal #"),
            _field("last_material_hauled", "q.last_material_hauled", "Last Material Hauled"),
            _field("performed_by", "q.performed_by", "Performed By"),
            _field("comments", "q.comments", "Comments"),
        ],
    },
    "balances": {
        "label": "Location balances",
        "description": "Current quantity on hand by location and material",
        "from": """
            (SELECT l.location_id, l.plant_id, m.material_id,
                    ROUND(SUM(x.qty), 2) AS balance
             FROM (
                SELECT to_location_id AS location_id, to_material_id AS material_id, to_qty AS qty
                FROM inventory_transaction
                WHERE voided = 0 AND to_location_id IS NOT NULL
                UNION ALL
                SELECT from_location_id, from_material_id, -from_qty
                FROM inventory_transaction
                WHERE voided = 0 AND from_location_id IS NOT NULL
             ) x
             JOIN location l ON l.location_id = x.location_id
             JOIN material m ON m.material_id = x.material_id
             GROUP BY l.location_id, m.material_id
            ) b
            JOIN location l ON l.location_id = b.location_id
            JOIN location_type lt ON lt.location_type_id = l.location_type_id
            JOIN plant p ON p.plant_id = b.plant_id
            JOIN material m ON m.material_id = b.material_id
        """,
        "where": "b.balance <> 0",
        "fields": [
            _field("plant_code", "p.code", "Plant"),
            _field("location_number", "l.number", "Location"),
            _field("location_description", "l.description", "Location Description"),
            _field("location_type", "lt.name", "Location Type"),
            _field("material_number", "m.number", "Material"),
            _field("material_description", "m.description", "Material Description"),
            _field("family", "m.family", "Family"),
            _field("balance", "b.balance", "Balance (lbs)", "number"),
            _field("max_capacity", "l.max_capacity", "Capacity (lbs)", "number"),
        ],
    },
}

OPERATORS: dict[str, dict[str, Any]] = {
    "=": {"args": 1, "sql": "{expr} = ?"},
    "<>": {"args": 1, "sql": "{expr} <> ?"},
    ">": {"args": 1, "sql": "{expr} > ?"},
    ">=": {"args": 1, "sql": "{expr} >= ?"},
    "<": {"args": 1, "sql": "{expr} < ?"},
    "<=": {"args": 1, "sql": "{expr} <= ?"},
    "LIKE": {"args": 1, "sql": "{expr} LIKE ?"},
    "NOT LIKE": {"args": 1, "sql": "{expr} NOT LIKE ?"},
    "BETWEEN": {"args": 2, "sql": "{expr} BETWEEN ? AND ?"},
    "IN": {"args": "list", "sql": "{expr} IN ({marks})"},
    "IS NULL": {"args": 0, "sql": "{expr} IS NULL"},
    "IS NOT NULL": {"args": 0, "sql": "{expr} IS NOT NULL"},
}

MAX_ROWS = 5000


def catalogue() -> dict[str, Any]:
    """What the builder offers: sources, their fields, and the operators."""

    return {
        "sources": [
            {
                "key": key,
                "label": src["label"],
                "description": src["description"],
                "fields": [
                    {k: f[k] for k in ("name", "label", "type")} for f in src["fields"]
                ],
            }
            for key, src in SOURCES.items()
        ],
        "operators": [
            {"key": key, "args": spec["args"]} for key, spec in OPERATORS.items()
        ],
        "max_rows": MAX_ROWS,
    }


def _source(key: str) -> dict:
    src = SOURCES.get(key)
    if src is None:
        raise ValidationError(
            f"Unknown data source {key!r}.", allowed=sorted(SOURCES)
        )
    return src


def build(definition: dict, prompts: dict[str, Any] | None = None) -> tuple[str, list]:
    """Turn a query definition into parameterised SQL. Never interpolates values."""

    prompts = prompts or {}
    src = _source(definition.get("source", ""))
    by_name = {f["name"]: f for f in src["fields"]}

    requested = definition.get("fields") or [f["name"] for f in src["fields"][:8]]
    unknown = [name for name in requested if name not in by_name]
    if unknown:
        raise ValidationError(
            f"Unknown field(s): {', '.join(unknown)}.", fields=sorted(by_name)
        )

    select = ", ".join(f'{by_name[n]["expr"]} AS "{n}"' for n in requested)
    where_sql = [src["where"]]
    params: list[Any] = []

    for index, filt in enumerate(definition.get("filters") or []):
        name = filt.get("field")
        if name not in by_name:
            raise ValidationError(f"Cannot filter on {name!r}.", field=name)
        operator = (filt.get("operator") or "=").upper()
        spec = OPERATORS.get(operator)
        if spec is None:
            raise ValidationError(
                f"Unsupported operator {operator!r}.", allowed=sorted(OPERATORS)
            )
        expr = by_name[name]["expr"]

        value = filt.get("value")
        if filt.get("prompt"):
            key = filt.get("prompt_key") or name
            if key not in prompts:
                raise ValidationError(
                    f"This query prompts for {key!r}.", prompt=key, index=index
                )
            value = prompts[key]

        logic = "AND" if index == 0 else (filt.get("logic") or "AND").upper()
        if logic not in {"AND", "OR"}:
            raise ValidationError("Logic must be AND or OR.", logic=logic)

        if spec["args"] == 0:
            clause = spec["sql"].format(expr=expr)
        elif spec["args"] == "list":
            items = value if isinstance(value, list) else str(value or "").split(",")
            items = [i.strip() for i in items if str(i).strip() != ""]
            if not items:
                raise ValidationError(f"IN needs at least one value for {name}.", field=name)
            clause = spec["sql"].format(expr=expr, marks=", ".join("?" for _ in items))
            params.extend(items)
        elif spec["args"] == 2:
            pair = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
            if len(pair) != 2:
                raise ValidationError(
                    f"BETWEEN needs two values for {name}.", field=name, value=value
                )
            clause = spec["sql"].format(expr=expr)
            params.extend([str(pair[0]).strip(), str(pair[1]).strip()])
        else:
            if value is None or value == "":
                raise ValidationError(f"Enter a value for {name}.", field=name)
            clause = spec["sql"].format(expr=expr)
            params.append(value)

        where_sql.append(f"{logic} ({clause})" if len(where_sql) > 1 else f"AND ({clause})")

    order_by = []
    for sort in definition.get("sort") or []:
        name = sort.get("field")
        if name not in by_name:
            raise ValidationError(f"Cannot sort on {name!r}.", field=name)
        direction = (sort.get("direction") or "ASC").upper()
        if direction not in {"ASC", "DESC"}:
            raise ValidationError("Sort direction must be ASC or DESC.", direction=direction)
        order_by.append(f'{by_name[name]["expr"]} {direction}')

    limit = int(definition.get("limit") or 1000)
    limit = max(1, min(limit, MAX_ROWS))

    sql = f"SELECT {select}\nFROM {src['from'].strip()}\nWHERE {' '.join(where_sql)}"
    if order_by:
        sql += "\nORDER BY " + ", ".join(order_by)
    sql += f"\nLIMIT {limit}"
    return sql, params


def prompts_for(definition: dict) -> list[dict]:
    """Which values the query will ask for at run time."""

    src = _source(definition.get("source", ""))
    by_name = {f["name"]: f for f in src["fields"]}
    out = []
    for filt in definition.get("filters") or []:
        if filt.get("prompt"):
            name = filt.get("field")
            field = by_name.get(name, {})
            out.append(
                {
                    "key": filt.get("prompt_key") or name,
                    "field": name,
                    "label": field.get("label", name),
                    "type": field.get("type", "text"),
                    "operator": filt.get("operator", "="),
                }
            )
    return out


def run(
    definition: dict,
    user: dict,
    prompts: dict[str, Any] | None = None,
    conn=None,
) -> dict[str, Any]:
    require_permission(user, "query.run")
    sql, params = build(definition, prompts)
    rows = db.query(sql, params, conn)
    src = _source(definition["source"])
    by_name = {f["name"]: f for f in src["fields"]}
    columns = [
        {
            "name": name,
            "label": by_name[name]["label"],
            "type": by_name[name]["type"],
        }
        for name in (definition.get("fields") or list(rows[0].keys()) if rows else [])
        if name in by_name
    ]
    return {
        "columns": columns or [
            {"name": k, "label": k, "type": "text"} for k in (rows[0] if rows else {})
        ],
        "rows": rows,
        "row_count": len(rows),
        "truncated": len(rows) >= min(int(definition.get("limit") or 1000), MAX_ROWS),
        "sql": sql,
        "parameters": params,
    }


def to_csv(result: dict) -> str:
    return rows_to_csv(result["rows"], [c["name"] for c in result["columns"]])


# ------------------------------------------------------------ saved queries


def save(name: str, description: str, definition: dict, user: dict, query_id: int | None = None, conn=None) -> dict:
    require_permission(user, "query.run")
    if not name.strip():
        raise ValidationError("Give the query a name.", fields={"name": "Required."})
    build(definition, {p["key"]: "probe" for p in prompts_for(definition)})  # validate
    values = {
        "name": name.strip(),
        "description": description or "",
        "definition": json.dumps(definition),
    }
    with db.transaction(conn):
        if query_id:
            existing = db.query_one(
                "SELECT * FROM saved_query WHERE query_id = ?", (query_id,), conn
            )
            if existing is None:
                raise NotFound(f"Saved query {query_id} was not found.")
            db.update("saved_query", {"query_id": query_id}, values, conn)
        else:
            values.update({"added_by": user["username"], "date_added": utc_now_iso()})
            query_id = db.insert("saved_query", values, conn)
        audit.record(
            username=user["username"],
            action="query.save",
            entity="saved_query",
            entity_id=query_id,
            summary=f"Saved query '{name}'",
            conn=conn,
        )
    return get_saved(query_id, conn)


def get_saved(query_id: int, conn=None) -> dict:
    row = db.query_one(
        "SELECT * FROM saved_query WHERE query_id = ?", (query_id,), conn
    )
    if row is None:
        raise NotFound(f"Saved query {query_id} was not found.")
    row["definition"] = json.loads(row["definition"])
    row["prompts"] = prompts_for(row["definition"])
    return row


def list_saved(include_inactive: bool = False, conn=None) -> list[dict]:
    sql = "SELECT query_id, name, description, added_by, date_added, active FROM saved_query"
    if not include_inactive:
        sql += " WHERE active = 1"
    return db.query(sql + " ORDER BY name", (), conn)


def deactivate(query_id: int, user: dict, conn=None) -> None:
    with db.transaction(conn):
        db.update("saved_query", {"query_id": query_id}, {"active": 0}, conn)
        audit.record(
            username=user["username"],
            action="query.deactivate",
            entity="saved_query",
            entity_id=query_id,
            summary=f"Deactivated saved query {query_id}",
            conn=conn,
        )
