"""Staged batches: acidulation, and anything else that settles.

Blending puts its ingredients together and is done. Acidulation is not: soap
comes in off trucks and railcars, goes into a reactor, acid goes in on top,
it is cooked and mixed, and then it is left to settle — for hours, often
across a shift change — before the acidulated soapstock is drawn off the top
and the acid water off the bottom. How much comes out is measured then, not
known in advance.

So a staged batch is a row with a stage and a clock, and the ledger rows
that move product carry its ``batch_id``:

    charging   soap goes into the vessel      MOVE from a tank, or RECEIVE
    acid       acid (and water) go in           straight off a truck/railcar
    mixing     cooked and mixed                 (time only)
    settling   left to separate                 (time only)
    drawn      drawn off and measured           PRODUCE: everything charged
                                                out of the vessel, the pounds
                                                drawn into a storage tank

The difference between what was charged and what was drawn off is the acid
water and process loss, and it is on the PRODUCE rows for anyone to read. The
recipe's yield is an *expectation*: it sizes the soap for the work order and
it is what the measured draw-off is compared against, with a confirmation
when they disagree by more than a few points — a warning, not a wall.

The batch lives on the server, not the screen, so the operator who charges
the reactor at 5 a.m. and the one who draws it off at 2 p.m. are looking at
the same batch.
"""

from __future__ import annotations

from typing import Any

from .. import audit, db
from ..errors import BusinessRuleError, NotFound, ValidationError
from ..security import require_permission, require_plant
from ..util import round_lbs, utc_now, utc_now_iso
from . import blend, inventory, numbering

STAGES = ("charging", "acid", "mixing", "settling", "drawn")
OPEN = ("charging", "acid", "mixing", "settling")
#: What each stage is called on the floor.
STAGE_LABEL = {
    "charging": "Charging soap",
    "acid": "Adding acid",
    "mixing": "Mixing",
    "settling": "Settling",
    "drawn": "Drawn off",
    "cancelled": "Cancelled",
}
#: How far the measured yield may stray from the recipe's before asking.
YIELD_TOLERANCE = 8.0

_STAGE_TIME = {"charging": "started_at", "acid": "acid_at", "mixing": "mixing_at", "settling": "settling_at", "drawn": "drawn_at"}


def _row(batch_id: str, conn) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM process_batch WHERE batch_id = ?", (batch_id,), conn)
    if row is None:
        raise NotFound(f"Batch {batch_id} was not found.")
    return row


def is_process_batch(batch_id: str, conn=None) -> bool:
    return bool(db.scalar("SELECT 1 FROM process_batch WHERE batch_id = ?", (batch_id,), conn))


def contents(batch_id: str, conn=None) -> dict[int, float]:
    """Pounds of each material charged into the vessel for this batch."""

    batch = _row(batch_id, conn)
    found: dict[int, float] = {}
    for row in db.query(
        """
        SELECT to_material_id AS material_id, SUM(to_qty) AS lbs
        FROM inventory_transaction
        WHERE batch_id = ? AND to_location_id = ? AND voided = 0 AND is_reversal = 0
        GROUP BY to_material_id
        """,
        (batch_id, batch["vessel_id"]),
        conn,
    ):
        if row["lbs"] and row["lbs"] > 0.005:
            found[row["material_id"]] = round_lbs(row["lbs"])
    return found


def _recipe(batch: dict, conn) -> dict | None:
    if not batch.get("recipe_id"):
        return None
    recipe = db.query_one("SELECT * FROM blend_recipe WHERE recipe_id = ?", (batch["recipe_id"],), conn)
    if recipe:
        recipe["components"] = blend._components(recipe["recipe_id"], conn)
    return recipe


def _minutes_since(iso: str | None) -> float | None:
    if not iso:
        return None
    from datetime import datetime, timezone

    then = datetime.fromisoformat(iso)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return round((utc_now() - then).total_seconds() / 60.0, 1)


def get(batch_id: str, conn=None) -> dict[str, Any]:
    """The batch as the screen shows it: stage, clock, what is in, what to add."""

    batch = _row(batch_id, conn)
    recipe = _recipe(batch, conn)
    held = contents(batch_id, conn)
    names = {
        r["material_id"]: r
        for r in db.query("SELECT material_id, number, description FROM material", (), conn)
    }
    vessel = db.query_one("SELECT location_id, number, description, max_capacity FROM location WHERE location_id = ?",
                          (batch["vessel_id"],), conn)
    charges = db.query(
        inventory.TXN_SELECT + " WHERE t.batch_id = ? ORDER BY t.transaction_id",
        (batch_id,),
        conn,
    )
    total_in = round_lbs(sum(held.values()))

    # The guide: how much of each ingredient belongs with what is already
    # in, scaled from the first component (the soap) once it is charged, and
    # from the work order's target before that.
    guide: list[dict[str, Any]] = []
    expected_yield = float((recipe or {}).get("yield_pct") or 100.0)
    if recipe and recipe["components"]:
        lead = recipe["components"][0]
        lead_in = held.get(lead["material_id"], 0.0)
        if lead_in > 0:
            charge = lead_in * 100.0 / lead["percentage"]
            basis = f"for the {lead_in:,.0f} lbs of {lead['material_number']} charged"
        else:
            charge = blend.charge_for(batch["target_lbs"], expected_yield)
            basis = f"for {batch['target_lbs']:,.0f} lbs out at {expected_yield:g}% yield"
        for component in recipe["components"]:
            wanted = round_lbs(charge * component["percentage"] / 100.0)
            have = held.get(component["material_id"], 0.0)
            guide.append({
                "material_id": component["material_id"],
                "material_number": component["material_number"],
                "material_description": component["material_description"],
                "percentage": component["percentage"],
                "guide_lbs": wanted,
                "charged_lbs": round_lbs(have),
                "still_to_add": round_lbs(max(wanted - have, 0.0)),
                "basis": basis,
            })

    stage_since = batch.get(_STAGE_TIME.get(batch["status"], "started_at")) or batch["started_at"]
    return {
        **batch,
        "stage_label": STAGE_LABEL.get(batch["status"], batch["status"]),
        "stage_minutes": _minutes_since(stage_since) if batch["status"] in OPEN else None,
        "stage_since": stage_since,
        "vessel": vessel,
        "product": names.get(batch["material_id"]),
        "recipe": {k: recipe[k] for k in ("recipe_id", "name", "notes", "yield_pct", "vessel_type")} if recipe else None,
        "expected_yield": expected_yield,
        "contents": [
            {**{k: names[m][k] for k in ("number", "description")}, "material_id": m, "lbs": lbs}
            for m, lbs in held.items()
        ],
        "total_in": total_in,
        "expected_out": round_lbs(total_in * expected_yield / 100.0),
        "guide": guide,
        "transactions": charges,
        "timeline": [
            {"stage": s, "label": STAGE_LABEL[s], "at": batch.get(_STAGE_TIME[s])}
            for s in STAGES
        ],
    }


def open_batches(plant_id: int, department_id: int | None = None, conn=None) -> list[dict]:
    sql = "SELECT batch_id FROM process_batch WHERE plant_id = ? AND status IN ('charging','acid','mixing','settling')"
    params: list[Any] = [plant_id]
    if department_id:
        sql += " AND department_id = ?"
        params.append(department_id)
    return [get(r["batch_id"], conn) for r in db.query(sql + " ORDER BY started_at", params, conn)]


def vessels(plant_id: int, department_id: int | None = None, conn=None) -> list[dict]:
    """Each vessel staged recipes run in, and the batch in it, if any."""

    types = [r["vessel_type"] for r in db.query(
        "SELECT DISTINCT vessel_type FROM blend_recipe WHERE active = 1 AND method = 'staged'"
        + (" AND department_id = ?" if department_id else ""),
        [department_id] if department_id else [],
        conn,
    )]
    if not types:
        return []
    marks = ", ".join("?" for _ in types)
    rows = db.query(
        f"""
        SELECT l.location_id, l.number, l.description, l.max_capacity
        FROM location l JOIN location_type lt ON lt.location_type_id = l.location_type_id
        WHERE l.plant_id = ? AND l.active = 1 AND lt.name IN ({marks})
        ORDER BY l.number
        """,
        [plant_id, *types],
        conn,
    )
    busy = {b["vessel_id"]: b for b in open_batches(plant_id, conn=conn)}
    for row in rows:
        row["total"] = round_lbs(inventory.location_total(row["location_id"], conn))
        row["batch"] = busy.get(row["location_id"])
    return rows


# ------------------------------------------------------------------ writes


def start(payload: dict[str, Any], user: dict, conn=None) -> dict:
    """Open a staged batch for a work order in a free vessel."""

    require_permission(user, "txn.post")
    order_id = payload.get("order_id")
    if not order_id:
        raise ValidationError("Say which work order.", fields={"order_id": "Choose a work order."})
    order = db.query_one('SELECT * FROM "order" WHERE order_id = ?', (order_id,), conn)
    if order is None:
        raise NotFound(f"Order {order_id} was not found.")
    require_plant(user, int(order["plant_id"]), conn)
    recipe = blend.recipe_for_material(int(order["material_one_id"] or 0), conn)
    if recipe is None or recipe.get("method") != "staged":
        raise BusinessRuleError(
            f"Order {order_id} is not made in stages — run it on the Blend screen.",
            rule="not_staged", order_id=order_id,
        )
    existing = db.query_one(
        "SELECT batch_id FROM process_batch WHERE order_id = ? AND status IN ('charging','acid','mixing','settling')",
        (order_id,), conn,
    )
    if existing:
        return get(existing["batch_id"], conn)

    free = [v for v in vessels(order["plant_id"], recipe.get("department_id"), conn) if not v["batch"]]
    vessel_id = payload.get("vessel_id")
    if vessel_id:
        vessel_id = int(vessel_id)
        if vessel_id not in {v["location_id"] for v in vessels(order["plant_id"], recipe.get("department_id"), conn)}:
            raise ValidationError("That is not one of this department's vessels.", fields={"vessel_id": "Choose a reactor."})
        if vessel_id not in {v["location_id"] for v in free}:
            busy = next(b for b in open_batches(order["plant_id"], conn=conn) if b["vessel_id"] == vessel_id)
            raise BusinessRuleError(
                f"{busy['vessel']['number']} already has batch {busy['batch_id']} in it "
                f"({busy['stage_label'].lower()}). Draw that off first, or use another reactor.",
                rule="vessel_busy", batch_id=busy["batch_id"],
            )
    elif free:
        vessel_id = free[0]["location_id"]
    else:
        raise BusinessRuleError(
            "Every reactor at this plant has a batch in it. Draw one off first.",
            rule="no_free_vessel",
        )

    from . import orders as orders_service

    done = orders_service.progress(order["order_id"], order["order_type_id"], conn)["qty_fulfilled"]
    target = round_lbs(max(float(order["material_one_quantity"] or 0) - done, 0.0))
    if payload.get("target_lbs"):
        target = round_lbs(float(payload["target_lbs"]))
    prefix = blend.batch_prefix(recipe.get("department_id"), conn)
    with db.transaction(conn):
        sequence = "blend" if prefix == "B" else f"batch-{prefix}"
        batch_id = f"{prefix}-{numbering.next_in_sequence(sequence, conn):05d}"
        db.insert(
            "process_batch",
            {
                "batch_id": batch_id,
                "order_id": order["order_id"],
                "plant_id": order["plant_id"],
                "department_id": recipe.get("department_id") or order.get("department_id"),
                "recipe_id": recipe["recipe_id"],
                "vessel_id": vessel_id,
                "material_id": order["material_one_id"],
                "target_lbs": target,
                "status": "charging",
                "started_at": utc_now_iso(),
                "started_by": user["username"],
            },
            conn,
        )
        audit.record(
            username=user["username"], action="process.start", entity="process_batch", entity_id=batch_id,
            order_id=order["order_id"], summary=f"Started batch {batch_id} for {target:,.0f} lbs",
            detail={"vessel_id": vessel_id}, conn=conn,
        )
    return get(batch_id, conn)


def charge(batch_id: str, payload: dict[str, Any], user: dict, conn=None) -> dict:
    """Put an ingredient into the vessel: from a tank, or straight off a delivery.

    ``payload``: material_id, quantity, and either from_location_id (a MOVE
    from a tank) or order_id (a RECEIVE against that purchase order, off the
    truck or railcar), plus vehicle (trailer or car number) and conveyance
    ('Truck' / 'Railcar').
    """

    require_permission(user, "txn.post")
    batch = _row(batch_id, conn)
    require_plant(user, int(batch["plant_id"]), conn)
    if batch["status"] not in ("charging", "acid", "mixing"):
        raise BusinessRuleError(
            f"Batch {batch_id} is {STAGE_LABEL[batch['status']].lower()} — nothing more goes in now.",
            rule="batch_closed_to_charges",
        )
    recipe = _recipe(batch, conn)
    material_id = int(payload.get("material_id") or 0)
    quantity = round_lbs(float(payload.get("quantity") or 0))
    allowed = {c["material_id"] for c in (recipe or {}).get("components", [])}
    if allowed and material_id not in allowed:
        raise ValidationError(
            "That material is not in this batch's recipe.",
            fields={"material_id": "Charge one of the recipe's ingredients."},
        )
    if quantity <= 0:
        raise ValidationError("Enter the pounds that went in.", fields={"quantity": "Greater than zero."})

    vehicle = (payload.get("vehicle") or "").strip()
    conveyance = (payload.get("conveyance") or "").strip()
    remarks = f"Charged to {batch_id}" + (f" off {conveyance.lower()} {vehicle}".rstrip() if conveyance else "")
    base = {
        "plant_id": batch["plant_id"],
        "department_id": batch["department_id"],
        "to_location_id": batch["vessel_id"],
        "to_material_id": material_id,
        "to_qty": quantity,
        "user_date": payload.get("user_date"),
        "remarks": payload.get("remarks") or remarks,
        "trailer_number": vehicle,
        "idempotency_key": payload.get("idempotency_key"),
    }
    with db.transaction(conn):
        if payload.get("order_id"):
            txn = inventory.post("RECEIVE", {**base, "order_id": payload["order_id"]}, user, conn)
        else:
            if not payload.get("from_location_id"):
                raise ValidationError(
                    "Say where it came from — a tank, or a delivery.",
                    fields={"from_location_id": "Choose the tank, or the truck or railcar."},
                )
            txn = inventory.post(
                "MOVE",
                {**base, "from_location_id": payload["from_location_id"], "from_material_id": material_id,
                 "from_qty": quantity},
                user,
                conn,
            )
        db.update("inventory_transaction", {"transaction_id": txn["transaction_id"]}, {"batch_id": batch_id}, conn)
    return get(batch_id, conn)


def advance(batch_id: str, to: str, user: dict, conn=None) -> dict:
    """Move the batch to its next stage and start that stage's clock."""

    require_permission(user, "txn.post")
    batch = _row(batch_id, conn)
    require_plant(user, int(batch["plant_id"]), conn)
    if to not in ("acid", "mixing", "settling"):
        raise ValidationError("Draw-off is its own step; the stages here are acid, mixing and settling.")
    current = STAGES.index(batch["status"]) if batch["status"] in STAGES else -1
    if STAGES.index(to) != current + 1:
        raise BusinessRuleError(
            f"Batch {batch_id} is {STAGE_LABEL.get(batch['status'], batch['status']).lower()}; "
            f"it goes to {STAGE_LABEL[STAGES[current + 1]].lower() if 0 <= current < 4 else 'nothing'} next.",
            rule="stage_order",
        )
    if to == "acid" and not contents(batch_id, conn):
        raise BusinessRuleError("Nothing has been charged yet — put the soap in first.", rule="empty_batch")
    with db.transaction(conn):
        db.update("process_batch", {"batch_id": batch_id}, {"status": to, _STAGE_TIME[to]: utc_now_iso()}, conn)
        audit.record(
            username=user["username"], action=f"process.{to}", entity="process_batch", entity_id=batch_id,
            order_id=batch["order_id"], summary=f"Batch {batch_id}: {STAGE_LABEL[to].lower()}", conn=conn,
        )
    return get(batch_id, conn)


def draw_off(batch_id: str, payload: dict[str, Any], user: dict, conn=None) -> dict:
    """Draw the settled product off into a tank, measured, and close the batch.

    ``payload``: to_location_id, quantity (pounds of product drawn off),
    acknowledge_yield?, remarks?, user_date?.
    """

    require_permission(user, "txn.post")
    batch = _row(batch_id, conn)
    require_plant(user, int(batch["plant_id"]), conn)
    if batch["status"] != "settling":
        raise BusinessRuleError(
            f"Batch {batch_id} is {STAGE_LABEL.get(batch['status'], batch['status']).lower()}. "
            "It is drawn off once it has settled.",
            rule="not_settled",
        )
    held = contents(batch_id, conn)
    total_in = round_lbs(sum(held.values()))
    quantity = round_lbs(float(payload.get("quantity") or 0))
    to_location_id = payload.get("to_location_id")
    errors = {}
    if quantity <= 0:
        errors["quantity"] = "Enter the pounds drawn off."
    if not to_location_id:
        errors["to_location_id"] = "Choose the tank it went into."
    if errors:
        raise ValidationError("This draw-off cannot be recorded.", fields=errors)
    if quantity - total_in > 0.5:
        raise ValidationError(
            f"{quantity:,.0f} lbs drawn off is more than the {total_in:,.0f} lbs that went in.",
            fields={"quantity": "Cannot be more than was charged."},
        )
    recipe = _recipe(batch, conn)
    expected = float((recipe or {}).get("yield_pct") or 100.0)
    actual = round(quantity / total_in * 100.0, 1) if total_in else 0.0
    if abs(actual - expected) > YIELD_TOLERANCE and not payload.get("acknowledge_yield"):
        raise BusinessRuleError(
            f"That is a {actual:g}% yield; this recipe usually gives about {expected:g}%. "
            "Check the gauge — confirm if the number is right.",
            rule="unexpected_yield", actual=actual, expected=expected,
            acknowledge_field="acknowledge_yield",
        )

    loss = round_lbs(total_in - quantity)
    items = list(held.items())
    outputs = [round_lbs(lbs * quantity / total_in) for _, lbs in items[:-1]]
    outputs.append(round_lbs(quantity - sum(outputs)))
    remarks = payload.get("remarks") or (
        f"Drew off batch {batch_id}: {quantity:,.0f} of {total_in:,.0f} lbs ({actual:g}%); "
        f"{loss:,.0f} lbs acid water and loss"
    )
    with db.transaction(conn):
        for (material_id, lbs), out in zip(items, outputs):
            txn = inventory.post(
                "PRODUCE",
                {
                    "order_id": batch["order_id"],
                    "plant_id": batch["plant_id"],
                    "department_id": batch["department_id"],
                    "from_location_id": batch["vessel_id"],
                    "from_material_id": material_id,
                    "from_qty": lbs,
                    "to_location_id": to_location_id,
                    "to_material_id": batch["material_id"],
                    "to_qty": out,
                    "user_date": payload.get("user_date"),
                    "remarks": remarks,
                },
                user,
                conn,
            )
            db.update("inventory_transaction", {"transaction_id": txn["transaction_id"]}, {"batch_id": batch_id}, conn)
        db.update(
            "process_batch",
            {"batch_id": batch_id},
            {"status": "drawn", "drawn_at": utc_now_iso(), "drawn_by": user["username"], "drawn_lbs": quantity},
            conn,
        )
        audit.record(
            username=user["username"], action="process.draw", entity="process_batch", entity_id=batch_id,
            order_id=batch["order_id"],
            summary=f"Drew off {quantity:,.0f} lbs from batch {batch_id} ({actual:g}% yield)",
            detail={"to_location_id": to_location_id, "charged": total_in, "loss": loss}, conn=conn,
        )
    return get(batch_id, conn)


def cancel(batch_id: str, reason: str, user: dict, conn=None) -> dict:
    """Abandon a batch nothing has gone into. One with product in is drawn
    off or emptied by a move — the reactor's contents are real either way."""

    require_permission(user, "txn.post")
    batch = _row(batch_id, conn)
    require_plant(user, int(batch["plant_id"]), conn)
    if batch["status"] not in OPEN:
        raise BusinessRuleError(f"Batch {batch_id} is already {batch['status']}.", rule="batch_closed")
    if contents(batch_id, conn):
        raise BusinessRuleError(
            f"Batch {batch_id} has product in {db.scalar('SELECT number FROM location WHERE location_id = ?', (batch['vessel_id'],), conn)}. "
            "Undo the charges, or draw it off, before cancelling.",
            rule="batch_not_empty",
        )
    with db.transaction(conn):
        db.update("process_batch", {"batch_id": batch_id}, {"status": "cancelled", "notes": reason or ""}, conn)
        audit.record(
            username=user["username"], action="process.cancel", entity="process_batch", entity_id=batch_id,
            order_id=batch["order_id"], summary=f"Cancelled batch {batch_id}", detail={"reason": reason}, conn=conn,
        )
    return get(batch_id, conn)
