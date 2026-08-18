"""Blending: raw materials in, product out.

The legacy ``Order`` carried a ``Blend_recipe_id``; the recipes themselves
lived somewhere outside the material provided, so blending was the one plant
job the replacement could not do. This closes it, with the recipe as data a
supervisor can read and change instead of a formula compiled into a screen.

Three deliberate choices:

* A recipe belongs to the product it makes — one active recipe per product —
  so an operator never picks a recipe. They pick the work order; the product
  names the recipe; the recipe names the components.
* A batch is ordinary ledger rows. Each component becomes one PRODUCE
  transaction (component out of its tank, product into the blend tank), and
  the rows share a ``batch_id``. Blending invents no new movement type, so
  every existing rule — stock, capacity, plant access, voiding — applies
  unchanged.
* The plan is computed server-side and shown before anything posts: required
  pounds per component, the tank that will supply each, and whether it can.
  The execute step re-checks everything inside one transaction, so a plan
  that went stale while the operator read it fails whole, not half.
"""

from __future__ import annotations

from typing import Any

from .. import audit, db
from ..errors import BusinessRuleError, NotFound, ValidationError
from ..security import require_permission, require_plant
from ..util import round_lbs
from . import inventory, numbering

# ------------------------------------------------------------------ recipes


def recipes(conn=None, include_inactive: bool = False) -> list[dict]:
    sql = """
        SELECT r.recipe_id, r.material_id, r.name, r.notes, r.active,
               m.number AS material_number, m.description AS material_description
        FROM blend_recipe r
        JOIN material m ON m.material_id = r.material_id
    """
    if not include_inactive:
        sql += " WHERE r.active = 1"
    rows = db.query(sql + " ORDER BY m.number", (), conn)
    for row in rows:
        row["components"] = _components(row["recipe_id"], conn)
    return rows


def _components(recipe_id: int, conn) -> list[dict]:
    return db.query(
        """
        SELECT c.component_id, c.material_id, c.percentage, c.sort_order,
               m.number AS material_number, m.description AS material_description
        FROM blend_recipe_component c
        JOIN material m ON m.material_id = c.material_id
        WHERE c.recipe_id = ?
        ORDER BY c.sort_order, c.component_id
        """,
        (recipe_id,),
        conn,
    )


def recipe_for_material(material_id: int, conn=None) -> dict | None:
    row = db.query_one(
        "SELECT * FROM blend_recipe WHERE material_id = ? AND active = 1",
        (material_id,),
        conn,
    )
    if row:
        row["components"] = _components(row["recipe_id"], conn)
    return row


def set_recipe(
    material_id: int,
    name: str,
    components: list[dict],
    user: dict,
    notes: str = "",
    conn=None,
) -> dict:
    """Create or replace the recipe for a product.

    Replacing rather than editing in place: the old recipe row is deactivated
    and stays behind the batches that used it, so "what was the formula when
    this batch ran?" keeps an answer.
    """

    require_permission(user, "spec.write")
    if not components:
        raise ValidationError(
            "A recipe needs at least one component.",
            fields={"components": "Add the materials this product is made from."},
        )
    total = sum(float(c.get("percentage") or 0) for c in components)
    if abs(total - 100.0) > 0.01:
        raise ValidationError(
            f"Component percentages add up to {total:g}, not 100.",
            fields={"components": "Percentages by weight must sum to 100."},
        )
    seen: set[int] = set()
    for component in components:
        cid = int(component["material_id"])
        if cid == int(material_id):
            raise ValidationError(
                "A product cannot be a component of itself.",
                fields={"components": "Remove the product from its own recipe."},
            )
        if cid in seen:
            raise ValidationError(
                "A component appears twice.",
                fields={"components": "List each material once."},
            )
        if float(component.get("percentage") or 0) <= 0:
            raise ValidationError(
                "Every component needs a percentage above zero.",
                fields={"components": "Remove zero-percentage rows."},
            )
        seen.add(cid)

    with db.transaction(conn):
        db.execute(
            "UPDATE blend_recipe SET active = 0 WHERE material_id = ? AND active = 1",
            (material_id,),
            conn,
        )
        recipe_id = db.insert(
            "blend_recipe",
            {"material_id": material_id, "name": name.strip(), "notes": notes, "active": 1},
            conn,
        )
        for index, component in enumerate(components):
            db.insert(
                "blend_recipe_component",
                {
                    "recipe_id": recipe_id,
                    "material_id": int(component["material_id"]),
                    "percentage": float(component["percentage"]),
                    "sort_order": index * 10,
                },
                conn,
            )
        audit.record(
            username=user["username"],
            action="blend.recipe",
            entity="blend_recipe",
            entity_id=recipe_id,
            summary=f"Recipe set for material {material_id}",
            detail={"name": name, "components": components},
            conn=conn,
        )
    result = recipe_for_material(material_id, conn)
    assert result is not None
    return result


# --------------------------------------------------------------------- plan


def plan(
    *,
    order_id: int | None = None,
    material_id: int | None = None,
    quantity: float | None = None,
    plant_id: int | None = None,
    conn=None,
) -> dict:
    """Scale the recipe to a target quantity and say where it will come from.

    Safe to call on every keystroke — it writes nothing. Each component gets
    the tank holding the most of it at the plant, its requirement in pounds,
    and an honest ``short`` flag; the output gets the plant's blend tank and a
    capacity check. The screen renders this verbatim, which is the point: the
    operator sees exactly what execute will do.
    """

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
        material_id = material_id or order["material_one_id"]
        plant_id = plant_id or order["plant_id"]
    if not material_id:
        raise ValidationError(
            "Say what product to blend.", fields={"material_id": "Choose a product."}
        )

    recipe = recipe_for_material(int(material_id), conn)
    material = db.query_one(
        "SELECT number, description FROM material WHERE material_id = ?",
        (material_id,),
        conn,
    )
    if recipe is None:
        raise BusinessRuleError(
            f"No recipe is set up for {material['number']} {material['description']}. "
            "An administrator adds one under Products & limits.",
            rule="no_recipe",
            material_id=material_id,
        )

    target = float(quantity or 0)
    if target <= 0 and order is not None:
        from . import orders as orders_service

        progress = orders_service.progress(order["order_id"], order["order_type_id"], conn)
        target = max(float(order["material_one_quantity"]) - progress["qty_fulfilled"], 0)
    target = round_lbs(target)

    notes: list[str] = []
    if order is not None and target:
        notes.append(f"{target:,.0f} lbs outstanding on order {order['order_id']}")

    balances = inventory.location_balance(plant_id=plant_id, conn=conn)
    components: list[dict[str, Any]] = []
    for component in recipe["components"]:
        required = round_lbs(target * component["percentage"] / 100.0)
        holdings = sorted(
            (b for b in balances if b["material_id"] == component["material_id"]),
            key=lambda b: -b["balance"],
        )
        tank = holdings[0] if holdings and holdings[0]["balance"] > 0 else None
        available = tank["balance"] if tank else 0.0
        components.append(
            {
                **{k: component[k] for k in (
                    "material_id", "material_number", "material_description", "percentage",
                )},
                "required": required,
                "from_location_id": tank["location_id"] if tank else None,
                "from_location_number": tank["location_number"] if tank else None,
                "available": round_lbs(available),
                "short": required - available > 0.01,
            }
        )
        if tank:
            notes.append(
                f"{tank['location_number']} holds the most "
                f"{component['material_number']} ({available:,.0f} lbs)"
            )

    destination = db.query_one(
        """
        SELECT l.location_id, l.number, l.max_capacity
        FROM location l
        JOIN location_type lt ON lt.location_type_id = l.location_type_id
        WHERE l.plant_id = ? AND l.active = 1 AND lt.name = 'Blend'
        LIMIT 1
        """,
        (plant_id,),
        conn,
    )
    headroom = None
    if destination and destination["max_capacity"]:
        current = inventory.location_total(destination["location_id"], conn)
        headroom = round_lbs(destination["max_capacity"] - current)
        notes.append(
            f"{destination['number']} has room for {max(headroom, 0):,.0f} lbs"
        )

    return {
        "order_id": order["order_id"] if order else None,
        "material_id": material_id,
        "material_number": material["number"],
        "material_description": material["description"],
        "recipe": {k: recipe[k] for k in ("recipe_id", "name", "notes")},
        "quantity": target,
        "components": components,
        "to_location_id": destination["location_id"] if destination else None,
        "to_location_number": destination["number"] if destination else None,
        "destination_headroom": headroom,
        "short": any(c["short"] for c in components),
        "does_not_fit": headroom is not None and target - headroom > 0.01,
        "notes": notes,
    }


# ------------------------------------------------------------------ execute


def execute(payload: dict[str, Any], user: dict, conn=None) -> dict:
    """Run one batch: consume every component, produce the product. Atomic.

    ``payload``: order_id?, material_id, quantity, to_location_id,
    components: [{material_id, from_location_id, quantity}], user_date?,
    idempotency_key?, remarks?.

    The client sends the tanks it showed the operator, not just "do the plan"
    — so what was on the screen is what happens, even if stock moved while
    they read it. If it no longer can happen, the whole batch is refused and
    nothing posts.
    """

    require_permission(user, "txn.post")

    # A retried batch returns the batch that already ran, exactly like a
    # retried load. The key marks the first row; finding it means every row
    # committed, because they only ever commit together.
    idempotency_key = (payload.get("idempotency_key") or "").strip() or None
    if idempotency_key:
        existing = db.query_one(
            "SELECT batch_id FROM inventory_transaction WHERE idempotency_key = ?",
            (idempotency_key,),
            conn,
        )
        if existing and existing["batch_id"]:
            return batch(existing["batch_id"], conn)

    material_id = int(payload.get("material_id") or 0)
    quantity = round_lbs(float(payload.get("quantity") or 0))
    to_location_id = payload.get("to_location_id")
    components = payload.get("components") or []

    errors: dict[str, str] = {}
    if not material_id:
        errors["material_id"] = "Choose the product being blended."
    if quantity <= 0:
        errors["quantity"] = "Enter a quantity greater than zero."
    if not to_location_id:
        errors["to_location_id"] = "Choose the tank the blend goes into."
    if not components:
        errors["components"] = "The batch has no components."
    if errors:
        raise ValidationError("This batch cannot be blended.", fields=errors)

    total = round_lbs(sum(float(c.get("quantity") or 0) for c in components))
    if abs(total - quantity) > 0.5:
        raise ValidationError(
            f"The components add up to {total:,.0f} lbs but the batch is "
            f"{quantity:,.0f} lbs.",
            fields={"components": "Component quantities must sum to the batch quantity."},
        )

    order = None
    order_id = payload.get("order_id")
    plant_id = payload.get("plant_id")
    if order_id:
        order = db.query_one('SELECT * FROM "order" WHERE order_id = ?', (order_id,), conn)
        if order is None:
            raise NotFound(f"Order {order_id} was not found.")
        plant_id = plant_id or order["plant_id"]
        if order["material_one_id"] and int(order["material_one_id"]) != material_id:
            wanted = db.query_one(
                "SELECT number FROM material WHERE material_id = ?",
                (order["material_one_id"],),
                conn,
            )
            raise BusinessRuleError(
                f"Order {order_id} is for {wanted['number']}, not this product.",
                order_id=order_id,
            )
    if not plant_id:
        raise ValidationError("Choose a plant.", fields={"plant_id": "Choose a plant."})
    require_plant(user, int(plant_id), conn)

    with db.transaction(conn):
        batch_id = f"B-{numbering.next_in_sequence('blend', conn):05d}"
        serial = order["blend_serial_number"] if order else ""
        for index, component in enumerate(components):
            txn = inventory.post(
                "PRODUCE",
                {
                    "order_id": order_id,
                    "plant_id": plant_id,
                    "from_location_id": component["from_location_id"],
                    "from_material_id": component["material_id"],
                    "from_qty": float(component["quantity"]),
                    "to_location_id": to_location_id,
                    "to_material_id": material_id,
                    "to_qty": float(component["quantity"]),
                    "user_date": payload.get("user_date"),
                    "remarks": payload.get("remarks")
                    or f"Blend batch {batch_id}" + (f" (serial {serial})" if serial else ""),
                    "idempotency_key": f"{idempotency_key}:{index}" if idempotency_key else None,
                },
                user,
                conn,
            )
            db.update(
                "inventory_transaction",
                {"transaction_id": txn["transaction_id"]},
                # The key on the first row is the whole batch's receipt.
                {"batch_id": batch_id, "idempotency_key": idempotency_key if index == 0 else None},
                conn,
            )
        audit.record(
            username=user["username"],
            action="blend.batch",
            entity="blend_batch",
            entity_id=batch_id,
            order_id=order_id,
            summary=f"Blended {quantity:,.0f} lbs in batch {batch_id}"
            + (f" on order {order_id}" if order_id else ""),
            detail={"components": components, "to_location_id": to_location_id},
            conn=conn,
        )
    return batch(batch_id, conn)


def batch(batch_id: str, conn=None) -> dict:
    rows = db.query(
        inventory.TXN_SELECT + " WHERE t.batch_id = ? ORDER BY t.transaction_id",
        (batch_id,),
        conn,
    )
    if not rows:
        raise NotFound(f"Batch {batch_id} was not found.")
    return {
        "batch_id": batch_id,
        "order_id": rows[0]["order_id"],
        "product_number": rows[0]["to_material_number"],
        "product_description": rows[0]["to_material_description"],
        "to_location_number": rows[0]["to_location_number"],
        "quantity": round_lbs(sum(r["to_qty"] for r in rows if not r["voided"])),
        "voided": all(r["voided"] for r in rows),
        "transactions": rows,
    }


def void_batch(batch_id: str, reason: str, user: dict, conn=None) -> dict:
    """Reverse a whole batch. Voiding one component of a blend is not a thing
    that can happen to a tank, so it is not a thing the system offers."""

    rows = db.query(
        "SELECT transaction_id FROM inventory_transaction"
        " WHERE batch_id = ? AND voided = 0 AND is_reversal = 0",
        (batch_id,),
        conn,
    )
    if not rows:
        raise NotFound(f"Batch {batch_id} has nothing left to void.")
    with db.transaction(conn):
        for row in rows:
            inventory.void(row["transaction_id"], reason, user, conn)
    return batch(batch_id, conn)
