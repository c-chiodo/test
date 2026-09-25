"""Staged batches: acidulation, and anything else that settles and breaks.

Blending puts its ingredients together and is done. Acidulation is not. At
Des Moines, as the legacy ledger and the yields workbook record it:

    soap (Gum, Degum, Wetgums, VOP wet) arrives by truck or railcar — often
      received for the invoice while the car is still on the spur, and
      unloaded later;
    it is pumped into a settle tank, where it becomes "1006 Soap in
      Process-Veg"; acid (about 5.1 lbs per 100 lbs of soap) and steam
      (about 2.6) go in on top, PRODUCED into the same 1006;
    it cooks and mixes, then sits and settles — for hours, across shifts;
    then it *breaks*: oil off the top into the 20-series tanks (1019), MGR
      into the MGR tanks (1007), process water off the bottom (1008) — each
      measured, the oil and MGR with a moisture and an S reading;
    the MGR is reprocessed the same way in its own tanks, and breaks again.

So a staged batch is a row with a stage and a clock, and the ledger rows
carry its ``batch_id`` in the same shape the legacy PIMS wrote them, so the
yields workbook and every report read old and new data alike:

    charging   soap in       PRODUCE soap -> process material in the tank
                             (off a truck/railcar: RECEIVE onto the
                             receiving location, then PRODUCE from it)
    acid       acid, steam   PRODUCE acid / steam -> process material
    mixing     cook & mix    (time only)
    settling   settle        (time only)
    drawn      break         PRODUCE process material -> each output,
                             measured, readings attached

The recipe is a guide, not a gate: its first group (the soap) is 100, the
rest are pounds per 100 lbs of it, and its outputs name the layers the break
gives. First-pass yield is the oil against what the soap could give (its TFA,
26% on the yields sheet); a break whose measured layers do not account for
what went in asks for confirmation — a warning, not a wall.

The batch lives on the server, not the screen: the operator who charges the
tank at 5 a.m. and the one who breaks it after lunch open the same page.
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
    "charging": "Soap going in",
    "acid": "Acid going in",
    "mixing": "Cooking & mixing",
    "settling": "Settling",
    "drawn": "Broken",
    "cancelled": "Cancelled",
}
#: A single-output batch: how far the measured yield may stray from the
#: recipe's before asking.
YIELD_TOLERANCE = 8.0
#: A break: how much of what went in may be unaccounted for before asking.
BALANCE_TOLERANCE = 0.10

_STAGE_TIME = {"charging": "started_at", "acid": "acid_at", "mixing": "mixing_at", "settling": "settling_at", "drawn": "drawn_at"}


def _row(batch_id: str, conn) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM process_batch WHERE batch_id = ?", (batch_id,), conn)
    if row is None:
        raise NotFound(f"Batch {batch_id} was not found.")
    return row


def is_process_batch(batch_id: str, conn=None) -> bool:
    return bool(db.scalar("SELECT 1 FROM process_batch WHERE batch_id = ?", (batch_id,), conn))


def _recipe(batch: dict, conn) -> dict | None:
    if not batch.get("recipe_id"):
        return None
    return blend.recipe_for_material(batch["material_id"], conn, recipe_id=batch["recipe_id"])


#: Where a batch can be: the reactor it is cooked in, the tank it settles in,
#: the MGR tank it is reprocessed in. Every plant moves soap from one to
#: another before it breaks — Des Moines from 1–3 or 40 into 4–10, Sioux City
#: from 110/111 into 1–3 and then 4–10.
VESSEL_TYPES = ("Acid", "Settle", "MGR")


def _charges(batch: dict, conn) -> list[dict]:
    """The rows that put something into the batch.

    With a process material the batch may have moved tank to tank since; a
    charge is a row that *made* the process material, and a move — process
    material out of a tank the batch was in, into the next — is not one.
    """

    recipe = _recipe(batch, conn)
    process_material = (recipe or {}).get("process_material_id")
    if not process_material:
        return db.query(
            """
            SELECT * FROM inventory_transaction
            WHERE batch_id = ? AND to_location_id = ? AND voided = 0 AND is_reversal = 0
            ORDER BY transaction_id
            """,
            (batch["batch_id"], batch["vessel_id"]),
            conn,
        )
    rows = db.query(
        """
        SELECT * FROM inventory_transaction
        WHERE batch_id = ? AND to_material_id = ? AND voided = 0 AND is_reversal = 0
        ORDER BY transaction_id
        """,
        (batch["batch_id"], process_material),
        conn,
    )
    been_in = {r["to_location_id"] for r in rows}
    return [r for r in rows if not _is_move(r, process_material, been_in)]


def _is_move(row: dict, process_material: int, been_in: set) -> bool:
    return row["from_material_id"] == process_material and row["from_location_id"] in been_in


def moves(batch: dict, conn=None) -> list[dict]:
    """The batch's moves from tank to tank, oldest first."""

    recipe = _recipe(batch, conn)
    process_material = (recipe or {}).get("process_material_id")
    if not process_material:
        return []
    rows = db.query(
        inventory.TXN_SELECT + " WHERE t.batch_id = ? AND t.to_material_id = ? AND t.voided = 0"
        " AND t.is_reversal = 0 ORDER BY t.transaction_id",
        (batch["batch_id"], process_material), conn,
    )
    been_in = {r["to_location_id"] for r in rows}
    return [
        {"transaction_id": r["transaction_id"], "from": r["from_location_number"], "to": r["to_location_number"],
         "lbs": r["to_qty"], "at": r["transaction_date"]}
        for r in rows if _is_move(r, process_material, been_in)
    ]


def contents(batch_id: str, conn=None) -> dict[int, float]:
    """Pounds of each ingredient charged into the vessel for this batch.

    With a process material the vessel holds one thing (1006), so what went
    in is read from the ingredient side of each charge.
    """

    batch = _row(batch_id, conn)
    recipe = _recipe(batch, conn)
    by_ingredient = bool(recipe and recipe.get("process_material_id"))
    found: dict[int, float] = {}
    for row in _charges(batch, conn):
        material = row["from_material_id"] if by_ingredient and row["from_material_id"] else row["to_material_id"]
        found[material] = round_lbs(found.get(material, 0.0) + float(row["to_qty"] or 0))
    return {m: lbs for m, lbs in found.items() if lbs > 0.005}


def _in_vessel(batch: dict, recipe: dict | None, conn) -> dict[int, float]:
    """What the vessel holds for this batch, by the material it holds it as."""

    held: dict[int, float] = {}
    for row in _charges(batch, conn):
        held[row["to_material_id"]] = held.get(row["to_material_id"], 0.0) + float(row["to_qty"] or 0)
    return {m: round_lbs(v) for m, v in held.items() if v > 0.005}


def _groups(recipe: dict | None) -> list[dict[str, Any]]:
    """The recipe's ingredients, alternatives folded into one line each."""

    groups: list[dict[str, Any]] = []
    for component in (recipe or {}).get("components", []):
        key = component.get("grp") or f"m{component['material_id']}"
        group = next((g for g in groups if g["key"] == key), None)
        if group is None:
            group = {
                "key": key,
                "label": (component.get("grp") or component["material_description"]).title()
                if component.get("grp") else component["material_description"],
                "percentage": component["percentage"],
                "materials": [],
            }
            groups.append(group)
        group["materials"].append({
            "material_id": component["material_id"],
            "number": component["material_number"],
            "description": component["material_description"],
        })
    return groups


def _minutes_since(iso: str | None) -> float | None:
    if not iso:
        return None
    from datetime import datetime, timezone

    then = datetime.fromisoformat(iso)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return round((utc_now() - then).total_seconds() / 60.0, 1)


def get(batch_id: str, conn=None) -> dict[str, Any]:
    """The batch as the screen shows it: stage, clock, what is in, what to add,
    what it broke into and how it yielded."""

    batch = _row(batch_id, conn)
    recipe = _recipe(batch, conn)
    held = contents(batch_id, conn)
    names = {
        r["material_id"]: r
        for r in db.query("SELECT material_id, number, description FROM material", (), conn)
    }
    vessel = db.query_one(
        "SELECT location_id, number, description, max_capacity FROM location WHERE location_id = ?",
        (batch["vessel_id"],), conn,
    )
    rows = db.query(
        inventory.TXN_SELECT + " WHERE t.batch_id = ? ORDER BY t.transaction_id",
        (batch_id,),
        conn,
    )
    readings: dict[int, dict[str, float]] = {}
    for r in db.query(
        "SELECT r.transaction_id, r.analyte, r.value FROM txn_reading r"
        " JOIN inventory_transaction t ON t.transaction_id = r.transaction_id WHERE t.batch_id = ?",
        (batch_id,), conn,
    ):
        readings.setdefault(r["transaction_id"], {})[r["analyte"]] = r["value"]
    for row in rows:
        row["readings"] = readings.get(row["transaction_id"], {})

    total_in = round_lbs(sum(held.values()))
    tfa = float((recipe or {}).get("expected_tfa") or 0) or None
    expected_yield = float((recipe or {}).get("yield_pct") or 100.0)
    groups = _groups(recipe)

    # The guide: each ingredient against the soap actually charged, or,
    # before any is, against what the work order wants out.
    guide: list[dict[str, Any]] = []
    if groups:
        lead = groups[0]
        lead_in = sum(held.get(m["material_id"], 0.0) for m in lead["materials"])
        if lead_in > 0:
            basis_lbs = lead_in
            basis = f"for the {lead_in:,.0f} lbs of {lead['label'].lower()} in"
        else:
            basis_lbs = _lead_for_target(batch["target_lbs"], recipe, lead)
            basis = f"for {batch['target_lbs']:,.0f} lbs out"
        for group in groups:
            wanted = round_lbs(basis_lbs * group["percentage"] / 100.0)
            have = round_lbs(sum(held.get(m["material_id"], 0.0) for m in group["materials"]))
            guide.append({
                **group,
                # Kept for screens that read one material per line.
                "material_id": group["materials"][0]["material_id"],
                "material_number": group["materials"][0]["number"],
                "material_description": group["label"],
                "guide_lbs": wanted,
                "charged_lbs": have,
                "still_to_add": round_lbs(max(wanted - have, 0.0)),
                "basis": basis,
            })

    # What it broke into, by the recipe's outputs.
    outputs = []
    process_material = (recipe or {}).get("process_material_id")
    out_rows = [r for r in rows if r["from_location_id"] == batch["vessel_id"] and not r["voided"]
                and not r["is_reversal"] and not (process_material and r["to_material_id"] == process_material)]
    for output in (recipe or {}).get("outputs", []):
        mine = [r for r in out_rows if r["to_material_id"] == output["material_id"]]
        outputs.append({
            **output,
            "lbs": round_lbs(sum(float(r["to_qty"] or 0) for r in mine)),
            "into": sorted({r["to_location_number"] for r in mine if r["to_location_number"]}),
            # What was measured; "readings" stays the recipe's list of what to take.
            "measured": mine[0]["readings"] if mine else {},
            "suggested_tanks": _suggested_tanks(batch["plant_id"], output["material_id"], batch["vessel_id"], conn),
        })

    metrics = _metrics(guide, outputs, total_in, tfa, out_rows, batch, expected_yield)
    stage_since = batch.get(_STAGE_TIME.get(batch["status"], "started_at")) or batch["started_at"]
    return {
        **batch,
        "stage_label": STAGE_LABEL.get(batch["status"], batch["status"]),
        "stage_minutes": _minutes_since(stage_since) if batch["status"] in OPEN else None,
        "stage_since": stage_since,
        "vessel": vessel,
        "moves": moves(batch, conn),
        "can_move": bool(process_material) and batch["status"] in OPEN and total_in > 0,
        "product": names.get(batch["material_id"]),
        "process_material": names.get((recipe or {}).get("process_material_id")),
        "recipe": {
            k: recipe.get(k) for k in ("recipe_id", "name", "notes", "yield_pct", "vessel_type", "expected_tfa")
        } if recipe else None,
        "expected_yield": expected_yield,
        "contents": [
            {**{k: names[m][k] for k in ("number", "description")}, "material_id": m, "lbs": lbs}
            for m, lbs in held.items()
        ],
        "total_in": total_in,
        "expected_out": round_lbs(total_in * expected_yield / 100.0) if not outputs else metrics.get("expected_oil"),
        "guide": guide,
        "outputs": outputs,
        "metrics": metrics,
        "transactions": rows,
        "timeline": [
            {"stage": s, "label": STAGE_LABEL[s], "at": batch.get(_STAGE_TIME[s])}
            for s in STAGES
        ],
    }


def _lead_for_target(target: float, recipe: dict | None, lead: dict) -> float:
    """Soap to charge for ``target`` lbs of the product out."""

    if not target:
        return 0.0
    tfa = float((recipe or {}).get("expected_tfa") or 0)
    yield_pct = float((recipe or {}).get("yield_pct") or 100.0)
    if tfa and (recipe or {}).get("outputs"):
        # Oil out = soap x TFA x first-pass yield.
        return round_lbs(target / (tfa / 100.0) / (yield_pct / 100.0))
    return round_lbs(blend.charge_for(target, yield_pct) * lead["percentage"] / 100.0)


def _metrics(guide, outputs, total_in, tfa, out_rows, batch, expected_yield) -> dict[str, Any]:
    """The yields sheet's numbers, for one batch."""

    lead_in = guide[0]["charged_lbs"] if guide else 0.0
    oil = next((o["lbs"] for o in outputs if o["role"] == "oil"), None)
    measured = round_lbs(sum(o["lbs"] for o in outputs)) if outputs else round_lbs(
        sum(float(r["to_qty"] or 0) for r in out_rows))
    metrics: dict[str, Any] = {"lead_in": lead_in, "total_in": total_in, "measured_out": measured}
    for g in guide[1:]:
        if lead_in:
            metrics[f"{g['key']}_per_100"] = round(g["charged_lbs"] / lead_in * 100.0, 2)
    if tfa and lead_in:
        metrics["tfa"] = tfa
        # First-pass yield is oil against what the soap could give.
        metrics["expected_oil"] = round_lbs(lead_in * tfa / 100.0 * expected_yield / 100.0)
        metrics["theoretical_oil"] = round_lbs(lead_in * tfa / 100.0)
        if oil is not None and batch["status"] == "drawn":
            metrics["fpy"] = round(oil / (lead_in * tfa / 100.0) * 100.0, 1)
    if measured and outputs and batch["status"] == "drawn":
        metrics["split"] = {o["role"]: round(o["lbs"] / measured * 100.0, 1) for o in outputs}
        metrics["unaccounted"] = round_lbs(total_in - measured)
    return metrics


def _suggested_tanks(plant_id: int, material_id: int, exclude: int, conn) -> list[int]:
    """Storage tanks already holding this output, most room first."""

    rows = [
        r for r in inventory.location_balance(plant_id=plant_id, material_id=material_id, conn=conn)
        if r["location_id"] != exclude and r["location_type"] == "Tank" and r["balance"] > 0.5
    ]
    rows.sort(key=lambda r: -((r["max_capacity"] or 0) - inventory.location_total(r["location_id"], conn)))
    return [r["location_id"] for r in rows[:3]]


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
    # A batch starts where its recipe says and can be moved on to any of them.
    types = sorted(set(types) | set(VESSEL_TYPES))
    marks = ", ".join("?" for _ in types)
    rows = db.query(
        f"""
        SELECT l.location_id, l.number, l.description, l.max_capacity, lt.name AS vessel_type
        FROM location l JOIN location_type lt ON lt.location_type_id = l.location_type_id
        WHERE l.plant_id = ? AND l.active = 1 AND lt.name IN ({marks})
        ORDER BY lt.name DESC, l.number
        """,
        [plant_id, *types],
        conn,
    )
    busy = {b["vessel_id"]: b for b in open_batches(plant_id, conn=conn)}
    for row in rows:
        row["total"] = round_lbs(inventory.location_total(row["location_id"], conn))
        row["batch"] = busy.get(row["location_id"])
    return rows


def spur(plant_id: int, conn=None) -> list[dict]:
    """Deliveries received but not unloaded: what sits on the receiving
    locations — the railcar on the spur, the truck in the yard — with the
    cars that brought it and how long ago."""

    rows = [
        r for r in inventory.location_balance(plant_id=plant_id, conn=conn)
        if r["location_type"] == "Receiving" and r["balance"] > 0.5
    ]
    for row in rows:
        cars = db.query(
            """
            SELECT t.trailer_number, t.transaction_date, t.to_qty, t.order_id
            FROM inventory_transaction t
            JOIN transaction_type tt ON tt.transaction_type_id = t.transaction_type_id
            WHERE t.to_location_id = ? AND t.to_material_id = ? AND tt.kind = 'RECEIVE'
              AND t.voided = 0 AND t.is_reversal = 0
            ORDER BY t.transaction_id DESC LIMIT 5
            """,
            (row["location_id"], row["material_id"]),
            conn,
        )
        row["cars"] = cars
        row["oldest_minutes"] = _minutes_since(cars[-1]["transaction_date"]) if cars else None
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
    recipe = blend.recipe_for_material(
        int(order["material_one_id"] or 0), conn, method="staged", recipe_id=order.get("recipe_id"),
        plant_id=order["plant_id"],
    )
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

    candidates = [v for v in vessels(order["plant_id"], recipe.get("department_id"), conn)
                  if v["vessel_type"] == recipe["vessel_type"]]
    free = [v for v in candidates if not v["batch"]]
    vessel_id = payload.get("vessel_id")
    if vessel_id:
        vessel_id = int(vessel_id)
        if vessel_id not in {v["location_id"] for v in candidates}:
            raise ValidationError("That is not one of this recipe's tanks.", fields={"vessel_id": "Choose a tank."})
        if vessel_id not in {v["location_id"] for v in free}:
            busy = next(b for b in open_batches(order["plant_id"], conn=conn) if b["vessel_id"] == vessel_id)
            raise BusinessRuleError(
                f"{busy['vessel']['number']} already has batch {busy['batch_id']} in it "
                f"({busy['stage_label'].lower()}). Break that first, or use another tank.",
                rule="vessel_busy", batch_id=busy["batch_id"],
            )
    elif free:
        vessel_id = free[0]["location_id"]
    else:
        raise BusinessRuleError(
            f"Every {recipe['vessel_type'].lower()} tank at this plant has a batch in it. Break one first.",
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


def _receiving_location(plant_id: int, conveyance: str, conn) -> int:
    rows = db.query(
        """
        SELECT l.location_id, l.number FROM location l
        JOIN location_type lt ON lt.location_type_id = l.location_type_id
        WHERE l.plant_id = ? AND l.active = 1 AND lt.name = 'Receiving' ORDER BY l.number
        """,
        (plant_id,), conn,
    )
    if not rows:
        raise BusinessRuleError(
            "This plant has no receiving location to take a delivery onto.", rule="no_receiving_location",
        )
    word = "RAIL" if conveyance.lower().startswith("rail") else "TRUCK"
    return next((r["location_id"] for r in rows if word in r["number"].upper()), rows[0]["location_id"])


def charge(batch_id: str, payload: dict[str, Any], user: dict, conn=None) -> dict:
    """Put an ingredient in: from a tank, from what is waiting on the spur,
    from a utility (steam), or straight off a delivery.

    ``payload``: material_id, quantity, and either from_location_id or
    order_id (a purchase order, straight off its truck/railcar), plus
    conveyance ('Truck' / 'Railcar') and vehicle (trailer or car number).
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

    # In the tank it becomes the process material (1006), exactly as the
    # legacy PIMS recorded it; without one it stays what it was.
    into = (recipe or {}).get("process_material_id") or material_id
    # Always PRODUCED into the tank, as the legacy wrote it (MGR 1007 into
    # an MGR tank is PRODUCED 1007 -> 1007 there too).
    kind = "PRODUCE" if (recipe or {}).get("process_material_id") else "MOVE"
    vehicle = (payload.get("vehicle") or "").strip()
    conveyance = (payload.get("conveyance") or "").strip()
    remarks = payload.get("remarks") or (
        f"Charged to {batch_id}" + (f" off {conveyance.lower()} {vehicle}".rstrip() if conveyance else "")
    )
    base = {
        "plant_id": batch["plant_id"],
        "department_id": batch["department_id"],
        # The work order rides on the charge, as it did in the legacy rows.
        "order_id": batch["order_id"],
        "user_date": payload.get("user_date"),
        "remarks": remarks,
        "trailer_number": vehicle,
    }
    with db.transaction(conn):
        from_location = payload.get("from_location_id")
        if payload.get("order_id"):
            # Received onto the truck/rail location against the PO, then
            # unloaded from it into the tank: two rows, as the legacy did.
            from_location = _receiving_location(batch["plant_id"], conveyance or "Truck", conn)
            receipt = inventory.post(
                "RECEIVE",
                {**base, "order_id": payload["order_id"], "to_location_id": from_location,
                 "to_material_id": material_id, "to_qty": quantity,
                 "idempotency_key": (payload.get("idempotency_key") or "") + ":receipt" if payload.get("idempotency_key") else None},
                user, conn,
            )
            db.update("inventory_transaction", {"transaction_id": receipt["transaction_id"]}, {"batch_id": batch_id}, conn)
        if not from_location:
            raise ValidationError(
                "Say where it came from — a tank, the spur, or a delivery.",
                fields={"from_location_id": "Choose the tank, or the truck or railcar."},
            )
        utility = db.scalar(
            "SELECT lt.name = 'Utility' FROM location l JOIN location_type lt"
            " ON lt.location_type_id = l.location_type_id WHERE l.location_id = ?",
            (from_location,), conn,
        )
        txn = inventory.post(
            kind,
            {**base, "from_location_id": from_location, "from_material_id": material_id, "from_qty": quantity,
             "to_location_id": batch["vessel_id"], "to_material_id": into, "to_qty": quantity,
             "idempotency_key": payload.get("idempotency_key")},
            user,
            conn,
            # Steam has no tank to run dry: its utility location may go below zero.
            allow_negative=bool(utility),
        )
        db.update("inventory_transaction", {"transaction_id": txn["transaction_id"]}, {"batch_id": batch_id}, conn)
    return get(batch_id, conn)


def advance(batch_id: str, to: str, user: dict, conn=None) -> dict:
    """Move the batch to its next stage and start that stage's clock."""

    require_permission(user, "txn.post")
    batch = _row(batch_id, conn)
    require_plant(user, int(batch["plant_id"]), conn)
    if to not in ("acid", "mixing", "settling"):
        raise ValidationError("The break is its own step; the stages here are acid, mixing and settling.")
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


def move(batch_id: str, payload: dict[str, Any], user: dict, conn=None) -> dict:
    """Move the batch, all of it, to another tank: out of the reactor into a
    settle tank, say. ``payload``: to_location_id, user_date?, remarks?.

    Written as the legacy PIMS writes it — Soap in Process produced out of
    one tank into the next — and tagged with the batch, so the batch keeps
    what was charged into it and breaks from wherever it is now.
    """

    require_permission(user, "txn.post")
    batch = _row(batch_id, conn)
    require_plant(user, int(batch["plant_id"]), conn)
    recipe = _recipe(batch, conn)
    process_material = (recipe or {}).get("process_material_id")
    if not process_material:
        raise BusinessRuleError(f"Batch {batch_id} stays in its tank until it is drawn off.", rule="not_movable")
    if batch["status"] not in OPEN:
        raise BusinessRuleError(f"Batch {batch_id} is {batch['status']}.", rule="batch_closed")
    to_location_id = int(payload.get("to_location_id") or 0)
    target = db.query_one(
        "SELECT l.*, lt.name AS vessel_type FROM location l JOIN location_type lt ON lt.location_type_id = l.location_type_id"
        " WHERE l.location_id = ?",
        (to_location_id,), conn,
    )
    if target is None or target["plant_id"] != batch["plant_id"] or not target["active"]:
        raise ValidationError("Choose the tank it is going into.", fields={"to_location_id": "Choose a tank at this plant."})
    if to_location_id == batch["vessel_id"]:
        raise ValidationError("It is already in that tank.", fields={"to_location_id": "Choose a different tank."})
    if target["vessel_type"] not in VESSEL_TYPES:
        raise ValidationError(
            f"{target['number']} is not a reactor, settle or MGR tank.",
            fields={"to_location_id": "Choose a settle, reactor or MGR tank."},
        )
    other = db.query_one(
        f"SELECT batch_id FROM process_batch WHERE vessel_id = ? AND status IN ({', '.join('?' for _ in OPEN)})"
        " AND batch_id <> ?",
        (to_location_id, *OPEN, batch_id), conn,
    )
    if other:
        raise BusinessRuleError(f"{target['number']} has batch {other['batch_id']} in it.", rule="vessel_busy")
    lbs = round_lbs(sum(_in_vessel(batch, recipe, conn).values()))
    if lbs <= 0:
        raise BusinessRuleError("Nothing has been charged yet — there is nothing to move.", rule="empty_batch")
    source = db.scalar("SELECT number FROM location WHERE location_id = ?", (batch["vessel_id"],), conn)
    with db.transaction(conn):
        txn = inventory.post(
            "PRODUCE",
            {
                "order_id": batch["order_id"], "plant_id": batch["plant_id"], "department_id": batch["department_id"],
                "from_location_id": batch["vessel_id"], "from_material_id": process_material, "from_qty": lbs,
                "to_location_id": to_location_id, "to_material_id": process_material, "to_qty": lbs,
                "user_date": payload.get("user_date"),
                "remarks": (payload.get("remarks") or f"{batch_id} moved from {source} to {target['number']}"),
            },
            user,
            conn,
        )
        db.update("inventory_transaction", {"transaction_id": txn["transaction_id"]}, {"batch_id": batch_id}, conn)
        db.update("process_batch", {"batch_id": batch_id}, {"vessel_id": to_location_id}, conn)
        audit.record(
            username=user["username"], action="process.move", entity="process_batch", entity_id=batch_id,
            order_id=batch["order_id"], summary=f"Batch {batch_id} moved from {source} to {target['number']}",
            detail={"lbs": lbs, "from": source, "to": target["number"]}, conn=conn,
        )
    return get(batch_id, conn)


def draw_off(batch_id: str, payload: dict[str, Any], user: dict, conn=None) -> dict:
    """Break the settled batch: each layer measured into its own tank.

    ``payload``: outputs: [{material_id, to_location_id, quantity,
    readings: {moisture, spintest}}], acknowledge_balance?, remarks?,
    user_date?. A recipe with no outputs takes the older single draw-off,
    ``to_location_id`` and ``quantity``, with ``acknowledge_yield``.
    """

    require_permission(user, "txn.post")
    batch = _row(batch_id, conn)
    require_plant(user, int(batch["plant_id"]), conn)
    if batch["status"] != "settling":
        raise BusinessRuleError(
            f"Batch {batch_id} is {STAGE_LABEL.get(batch['status'], batch['status']).lower()}. "
            "It is broken once it has settled.",
            rule="not_settled",
        )
    recipe = _recipe(batch, conn)
    in_vessel = _in_vessel(batch, recipe, conn)
    total_in = round_lbs(sum(in_vessel.values()))
    single = not (recipe or {}).get("outputs")

    if single:
        outputs = [{
            "material_id": batch["material_id"],
            "to_location_id": payload.get("to_location_id"),
            "quantity": payload.get("quantity"),
            "readings": payload.get("readings") or {},
        }]
    else:
        wanted = {o["material_id"] for o in recipe["outputs"]}
        outputs = [o for o in (payload.get("outputs") or []) if float(o.get("quantity") or 0) > 0]
        stray = [o for o in outputs if int(o.get("material_id") or 0) not in wanted]
        if stray:
            raise ValidationError("A layer that is not one of this recipe's outputs.",
                                  fields={"outputs": "Oil, MGR or water — the recipe's outputs."})
    errors: dict[str, str] = {}
    if not outputs or not all(float(o.get("quantity") or 0) > 0 for o in outputs):
        errors["quantity"] = "Enter the pounds that came off."
    if any(not o.get("to_location_id") for o in outputs):
        errors["to_location_id"] = "Choose the tank each layer went into."
    if errors:
        raise ValidationError("This break cannot be recorded.", fields=errors)
    for o in outputs:
        o["quantity"] = round_lbs(float(o["quantity"]))
    measured = round_lbs(sum(o["quantity"] for o in outputs))

    if single:
        if measured - total_in > 0.5:
            raise ValidationError(
                f"{measured:,.0f} lbs drawn off is more than the {total_in:,.0f} lbs that went in.",
                fields={"quantity": "Cannot be more than was charged."},
            )
        expected = float((recipe or {}).get("yield_pct") or 100.0)
        actual = round(measured / total_in * 100.0, 1) if total_in else 0.0
        if abs(actual - expected) > YIELD_TOLERANCE and not payload.get("acknowledge_yield"):
            raise BusinessRuleError(
                f"That is a {actual:g}% yield; this recipe usually gives about {expected:g}%. "
                "Check the gauge — confirm if the number is right.",
                rule="unexpected_yield", actual=actual, expected=expected,
                acknowledge_field="acknowledge_yield",
            )
    elif total_in and abs(measured - total_in) / total_in > BALANCE_TOLERANCE and not payload.get("acknowledge_balance"):
        gap = total_in - measured
        raise BusinessRuleError(
            f"{total_in:,.0f} lbs went in and {measured:,.0f} lbs are measured coming off — "
            f"{abs(gap):,.0f} lbs {'unaccounted for' if gap > 0 else 'more than went in'}. "
            "Check the gauges; confirm if the numbers are right.",
            rule="unbalanced_break", total_in=total_in, measured=measured,
            acknowledge_field="acknowledge_balance",
        )

    # The tank is emptied of the batch whatever the gauges say: each layer
    # takes its share of what went in, and gets its measured pounds out.
    # A gap (evaporation, a heel) is then on the rows for anyone to see.
    source_material, source_lbs = next(iter(in_vessel.items())) if len(in_vessel) == 1 else (None, total_in)
    shares = [round_lbs(total_in * o["quantity"] / measured) for o in outputs[:-1]]
    shares.append(round_lbs(total_in - sum(shares)))
    remarks = payload.get("remarks") or (
        f"Broke batch {batch_id}: {measured:,.0f} lbs measured off {total_in:,.0f} lbs in"
    )
    with db.transaction(conn):
        rows_out = []
        if source_material is not None:
            for output, share in zip(outputs, shares):
                rows_out.append(_produce_out(batch, source_material, share, output, remarks, user, conn))
        else:
            # Ingredients kept apart in the vessel (no process material):
            # each gives up its share to each layer.
            for output, share in zip(outputs, shares):
                for material_id, lbs in in_vessel.items():
                    part = round_lbs(share * lbs / total_in)
                    if part <= 0:
                        continue
                    rows_out.append(_produce_out(
                        batch, material_id, part,
                        {**output, "quantity": round_lbs(output["quantity"] * lbs / total_in)},
                        remarks, user, conn,
                    ))
        db.update(
            "process_batch",
            {"batch_id": batch_id},
            {"status": "drawn", "drawn_at": utc_now_iso(), "drawn_by": user["username"], "drawn_lbs": measured},
            conn,
        )
        audit.record(
            username=user["username"], action="process.draw", entity="process_batch", entity_id=batch_id,
            order_id=batch["order_id"],
            summary=f"Broke batch {batch_id}: {measured:,.0f} lbs measured off {total_in:,.0f} lbs in",
            detail={"outputs": outputs, "total_in": total_in}, conn=conn,
        )
    return get(batch_id, conn)


def _produce_out(batch, from_material, from_qty, output, remarks, user, conn) -> dict:
    txn = inventory.post(
        "PRODUCE",
        {
            "order_id": batch["order_id"],
            "plant_id": batch["plant_id"],
            "department_id": batch["department_id"],
            "from_location_id": batch["vessel_id"],
            "from_material_id": from_material,
            "from_qty": from_qty,
            "to_location_id": output["to_location_id"],
            "to_material_id": int(output["material_id"]),
            "to_qty": output["quantity"],
            "remarks": remarks,
        },
        user,
        conn,
    )
    db.update("inventory_transaction", {"transaction_id": txn["transaction_id"]}, {"batch_id": batch["batch_id"]}, conn)
    for analyte, value in (output.get("readings") or {}).items():
        if value in (None, ""):
            continue
        db.execute(
            "INSERT OR REPLACE INTO txn_reading (transaction_id, analyte, value, source) VALUES (?, ?, ?, 'entered')",
            (txn["transaction_id"], analyte, float(value)),
            conn,
        )
    return txn


def cancel(batch_id: str, reason: str, user: dict, conn=None) -> dict:
    """Abandon a batch nothing has gone into. One with product in is broken
    or emptied by a move — the tank's contents are real either way."""

    require_permission(user, "txn.post")
    batch = _row(batch_id, conn)
    require_plant(user, int(batch["plant_id"]), conn)
    if batch["status"] not in OPEN:
        raise BusinessRuleError(f"Batch {batch_id} is already {batch['status']}.", rule="batch_closed")
    if contents(batch_id, conn):
        raise BusinessRuleError(
            f"Batch {batch_id} has product in {db.scalar('SELECT number FROM location WHERE location_id = ?', (batch['vessel_id'],), conn)}. "
            "Undo the charges, or break it, before cancelling.",
            rule="batch_not_empty",
        )
    with db.transaction(conn):
        db.update("process_batch", {"batch_id": batch_id}, {"status": "cancelled", "notes": reason or ""}, conn)
        audit.record(
            username=user["username"], action="process.cancel", entity="process_batch", entity_id=batch_id,
            order_id=batch["order_id"], summary=f"Cancelled batch {batch_id}", detail={"reason": reason}, conn=conn,
        )
    return get(batch_id, conn)
