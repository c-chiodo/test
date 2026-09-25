"""Blend onto the trailer: the legacy PROD-LOAD, without the reversals.

Most loads at the plants are not pumped from one tank. An FE Cattle Blend
3.5 is MGR veg, process water and caustic, each pumped from its own tank
straight onto the trailer as the ordered product; an HC3800 XL is HC3800
with a few pounds of AOX and Lipidol on the way in. The legacy PIMS records
these as PROD-LOAD rows — 3,434 of them in June to August 2026, more than
any other way of loading.

Caustic is the hard part. It goes in against the pH: some, test, a little
more, test again. In the legacy PIMS every step was a posting, so a load
that overshot was fixed by reversing and re-posting — 13.6% of PROD-LOAD
rows are reversals, and at Sioux City 31% of the caustic posted to FE
Cattle Blend 2.5 was later reversed.

Here the steps are on the screen, not in the ledger. The operator adds
caustic, types the pH, adds more if it needs it; nothing posts until the
load is right. Then the whole truck posts at once: one PROD_LOAD row per
component (component out of its tank, the blend into the trailer), one BOL,
one staged shipment, the dosing log in the caustic row's remarks and the
final pH as a reading on it — which the QC form then starts from.
"""

from __future__ import annotations

from typing import Any

from .. import audit, db
from ..errors import BusinessRuleError, NotFound, ValidationError
from ..security import require_permission, require_plant
from ..util import round_lbs
from . import blend, inventory, numbering


def _order(order_id: int, conn) -> dict[str, Any]:
    order = db.query_one('SELECT * FROM "order" WHERE order_id = ?', (order_id,), conn)
    if order is None:
        raise NotFound(f"Order {order_id} was not found.")
    return order


def _trailer_location(plant_id: int, conn) -> int:
    row = db.query_one(
        """
        SELECT l.location_id FROM location l JOIN location_type lt ON lt.location_type_id = l.location_type_id
        WHERE l.plant_id = ? AND l.active = 1 AND lt.name = 'Trailer' ORDER BY l.number LIMIT 1
        """,
        (plant_id,), conn,
    )
    if row is None:
        raise BusinessRuleError("This plant has no trailer location to load onto.", rule="no_trailer_location")
    return row["location_id"]


def _spec_range(material_id: int, analyte: str, conn) -> dict[str, Any] | None:
    row = db.query_one(
        "SELECT min_value, max_value FROM material_spec WHERE material_id = ? AND analyte = ?",
        (material_id, analyte), conn,
    )
    if not row or (row["min_value"] is None and row["max_value"] is None):
        return None
    return {"analyte": analyte, "min": row["min_value"], "max": row["max_value"]}


def plan(order_id: int, quantity: float | None = None, conn=None) -> dict[str, Any] | None:
    """The truck as the recipe would make it, or None when the product is
    not blended on the trailer (a straight load from one tank)."""

    order = _order(order_id, conn)
    recipe = blend.recipe_for_material(
        int(order["material_one_id"] or 0), conn, method="blend", plant_id=order["plant_id"],
    )
    if recipe is None or not recipe["components"]:
        return None
    if len(recipe["components"]) == 1 and recipe["components"][0]["material_id"] == order["material_one_id"]:
        return None

    from . import orders as orders_service

    done = orders_service.progress(order["order_id"], order["order_type_id"], conn)["qty_fulfilled"]
    remaining = round_lbs(max(float(order["material_one_quantity"] or 0) - done, 0.0))
    target = round_lbs(float(quantity)) if quantity else remaining
    balances = inventory.location_balance(plant_id=order["plant_id"], conn=conn)
    components = []
    for c in recipe["components"]:
        holding = sorted(
            (b for b in balances if b["material_id"] == c["material_id"] and b["balance"] > 0.5
             and b["location_type"] in ("Tank", "Blend")),
            key=lambda b: -b["balance"],
        )
        required = round_lbs(target * c["percentage"] / 100.0)
        tank = holding[0] if holding else None
        components.append({
            "material_id": c["material_id"],
            "material_number": c["material_number"],
            "material_description": c["material_description"],
            "percentage": c["percentage"],
            "required": required,
            "dose": c.get("dose"),
            "from_location_id": tank["location_id"] if tank else None,
            "from_location_number": tank["location_number"] if tank else None,
            "available": round_lbs(tank["balance"]) if tank else 0.0,
            "short": bool(tank is None or required - tank["balance"] > 0.01),
            "tanks": [{"location_id": b["location_id"], "number": b["location_number"], "lbs": round_lbs(b["balance"])}
                      for b in holding[:5]],
        })
    dose = next((c["dose"] for c in components if c["dose"]), None)
    return {
        "order_id": order["order_id"],
        "material_id": order["material_one_id"],
        "recipe": {k: recipe[k] for k in ("recipe_id", "name", "notes")},
        "remaining": remaining,
        "quantity": target,
        "components": components,
        "target": _spec_range(order["material_one_id"], dose, conn) if dose else None,
        "trailer_number": order.get("trailer_number") or "",
    }


def execute(order_id: int, payload: dict[str, Any], user: dict, conn=None) -> dict[str, Any]:
    """Post the whole truck. ``payload``: trailer_number, components:
    [{material_id, from_location_id, quantity}], doses: [{quantity,
    reading}] for the dosed component (its quantity is their sum), user_date?,
    acknowledge_over_load?, acknowledge_reading?, idempotency_key?."""

    require_permission(user, "txn.post")
    order = _order(order_id, conn)
    require_plant(user, int(order["plant_id"]), conn)
    if int(order["order_type_id"]) != 1:
        raise ValidationError("Only a sales order is loaded onto a trailer.", fields={"order_id": "Choose a sales order."})

    idempotency_key = (payload.get("idempotency_key") or "").strip() or None
    if idempotency_key:
        first = db.query_one(
            "SELECT transaction_id FROM inventory_transaction WHERE idempotency_key = ?", (f"{idempotency_key}:0",), conn,
        )
        if first:
            return load(first["transaction_id"], conn)

    planned = plan(order_id, conn=conn)
    if planned is None:
        raise BusinessRuleError(
            "This product is not blended on the trailer — load it from its tank.", rule="not_blended",
        )
    recipe_ids = {c["material_id"]: c for c in planned["components"]}
    trailer = (payload.get("trailer_number") or "").strip()
    errors: dict[str, str] = {}
    if not trailer:
        errors["trailer_number"] = "Which trailer?"
    rows: list[dict[str, Any]] = []
    doses = [d for d in (payload.get("doses") or []) if float(d.get("quantity") or 0) > 0]
    for component in payload.get("components") or []:
        material_id = int(component.get("material_id") or 0)
        spec = recipe_ids.get(material_id)
        if spec is None:
            errors["components"] = "A component that is not in this blend's recipe."
            continue
        qty = float(component.get("quantity") or 0)
        if spec["dose"] and doses:
            qty = sum(float(d["quantity"]) for d in doses)
        if qty <= 0:
            continue
        if not component.get("from_location_id"):
            errors["components"] = f"Say which tank the {spec['material_number']} comes from."
            continue
        rows.append({"material_id": material_id, "from_location_id": int(component["from_location_id"]),
                     "quantity": round_lbs(qty), "spec": spec})
    if not rows:
        errors["components"] = "Nothing to load."
    if errors:
        raise ValidationError("This load cannot be saved yet.", fields=errors)

    total = round_lbs(sum(r["quantity"] for r in rows))
    # The last pH against the product's range: a warning, not a wall — the
    # truck can go, and the record says what the reading was. Asked before
    # the over-the-order question: the pH is the one the operator can still fix.
    target = planned["target"]
    final = next((float(d["reading"]) for d in reversed(doses) if d.get("reading") not in (None, "")), None)
    if target and final is not None and not payload.get("acknowledge_reading"):
        low, high = target["min"], target["max"]
        if (low is not None and final < low) or (high is not None and final > high):
            raise BusinessRuleError(
                f"The last {target['analyte']} reading is {final:g}; this product should be "
                f"{low if low is not None else '—'} to {high if high is not None else '—'}. "
                "Add more, or confirm to load it as it is.",
                rule="reading_out_of_range", reading=final, acknowledge_field="acknowledge_reading",
            )

    # Over the order: a confirmation, as for any load.
    inventory._check_over_fulfilment(order, total, bool(payload.get("acknowledge_over_load")), conn)

    to_location = _trailer_location(order["plant_id"], conn)
    with db.transaction(conn):
        bol = payload.get("to_bol") or (
            numbering.next_bol(trailer, conn) if numbering.setting("bol.auto_generate", conn) != "false" else ""
        )
        posted = []
        for index, row in enumerate(rows):
            remarks = ""
            if row["spec"]["dose"] and doses:
                remarks = "Dosed to " + row["spec"]["dose"] + ": " + "; ".join(
                    f"+{float(d['quantity']):,.0f} lbs -> {row['spec']['dose']} {d.get('reading', '—')}" for d in doses
                )
            txn = inventory.post(
                "PROD_LOAD",
                {
                    "order_id": order_id, "plant_id": order["plant_id"], "department_id": order.get("department_id"),
                    "from_location_id": row["from_location_id"], "from_material_id": row["material_id"],
                    "from_qty": row["quantity"], "to_location_id": to_location,
                    "to_material_id": order["material_one_id"], "to_qty": row["quantity"],
                    "to_bol": bol, "trailer_number": trailer, "user_date": payload.get("user_date"),
                    "remarks": remarks,
                    "idempotency_key": f"{idempotency_key}:{index}" if idempotency_key else None,
                },
                user,
                conn,
            )
            posted.append(txn)
            if row["spec"]["dose"] and final is not None:
                db.execute(
                    "INSERT OR REPLACE INTO txn_reading (transaction_id, analyte, value, source) VALUES (?, ?, ?, 'entered')",
                    (txn["transaction_id"], row["spec"]["dose"], final), conn,
                )
        stage_id = db.insert("pending_shipment", {
            "order_id": order_id, "transaction_id": posted[0]["transaction_id"],
            "trailer_number": trailer, "quantity": total, "shipped": 0,
        }, conn)
        audit.record(
            username=user["username"], action="load.blend", entity="transaction",
            entity_id=posted[0]["transaction_id"], order_id=order_id,
            summary=f"Blended {total:,.0f} lbs onto trailer {trailer} (BOL {bol})",
            detail={"components": [{k: r[k] for k in ("material_id", "from_location_id", "quantity")} for r in rows],
                    "doses": doses},
            conn=conn,
        )
    result = load(posted[0]["transaction_id"], conn)
    result["stage_id"] = stage_id
    return result


def load(first_transaction_id: int, conn=None) -> dict[str, Any]:
    """One blended truck, as the screens show it."""

    first = inventory.get(first_transaction_id, conn)
    rows = db.query(
        inventory.TXN_SELECT + " WHERE t.to_bol = ? AND COALESCE(t.trailer_number, '') = ?"
        " AND t.order_id = ? AND t.voided = 0 AND t.is_reversal = 0 ORDER BY t.transaction_id",
        (first["to_bol"], first["trailer_number"] or "", first["order_id"]),
        conn,
    )
    reading = db.query_one(
        "SELECT r.analyte, r.value FROM txn_reading r JOIN inventory_transaction t ON t.transaction_id = r.transaction_id"
        " WHERE t.to_bol = ? AND t.order_id = ? ORDER BY r.transaction_id DESC LIMIT 1",
        (first["to_bol"], first["order_id"]), conn,
    )
    total = round_lbs(sum(float(r["to_qty"] or 0) for r in rows))
    return {
        # The shape the load-and-ship flow keeps for any load.
        **first,
        "from_qty": total,
        "to_qty": total,
        "from_material_number": first["to_material_number"],
        "from_location_number": "blended from " + ", ".join(sorted({r["from_location_number"] for r in rows})),
        "components": [
            {"material_number": r["from_material_number"], "material_description": r["from_material_description"],
             "from_location_number": r["from_location_number"], "quantity": r["from_qty"]}
            for r in rows
        ],
        "reading": reading,
        "blended": True,
    }
