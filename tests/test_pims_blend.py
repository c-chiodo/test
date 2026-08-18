"""Blending: recipes scale, batches post whole or not at all, and the ledger
reconciles afterwards. The one plant job the replacement could not do."""

from __future__ import annotations

import uuid

import pytest

from pims import db, security
from pims.errors import BusinessRuleError, NotFound, PermissionError_, ValidationError
from pims.services import blend, inventory, orders


def _material_id(number: str, conn) -> int:
    return db.scalar("SELECT material_id FROM material WHERE number = ?", (number,), conn)


@pytest.fixture()
def operator(conn) -> dict:
    return security.get_user("toperator")


@pytest.fixture()
def stocked_components(conn, admin_user) -> dict[str, int]:
    """Fresh tanks holding the cattle-blend components, keyed by material number."""

    tanks: dict[str, int] = {}
    for number, qty in (("02001", 80_000), ("00010", 120_000), ("00001", 10_000)):
        code = f"DM-BT-{uuid.uuid4().hex[:6].upper()}"
        tank = db.insert(
            "location",
            {
                "number": code,
                "description": "Blend test tank",
                "location_type_id": db.scalar(
                    "SELECT location_type_id FROM location_type WHERE name = 'Tank'", (), conn
                ),
                "plant_id": 1,
                "max_capacity": 500_000,
                "active": 1,
            },
            conn,
        )
        inventory.post(
            "RECEIVE",
            {
                "plant_id": 1,
                "to_location_id": tank,
                "to_material_id": _material_id(number, conn),
                "to_qty": qty,
                "to_bol": f"001-{code}",
            },
            admin_user,
            conn,
        )
        tanks[number] = tank
    return tanks


@pytest.fixture()
def blend_order(conn, admin_user) -> int:
    created = orders.create(
        {
            "order_type_id": 2,
            "plant_id": 1,
            "company_id": 1,
            "department_id": 3,
            "order_date": "2026-08-17",
            "due_date": "2026-08-19",
            "material_one_id": _material_id("01020", conn),
            "material_one_quantity": 10_000,
        },
        admin_user,
        conn,
    )
    return created[0]["order_id"]


def _payload_from(plan: dict, tanks: dict[str, int] | None = None, **extra) -> dict:
    components = []
    for component in plan["components"]:
        from_location = component["from_location_id"]
        if tanks:
            from_location = tanks[component["material_number"]]
        components.append(
            {
                "material_id": component["material_id"],
                "from_location_id": from_location,
                "quantity": component["required"],
            }
        )
    payload = {
        "order_id": plan["order_id"],
        "material_id": plan["material_id"],
        "quantity": plan["quantity"],
        "to_location_id": plan["to_location_id"],
        "components": components,
    }
    payload.update(extra)
    return payload


# ------------------------------------------------------------------ recipes


def test_a_recipe_scales_to_the_orders_outstanding_quantity(conn, blend_order):
    plan = blend.plan(order_id=blend_order, conn=conn)
    assert plan["quantity"] == 10_000
    assert [c["material_number"] for c in plan["components"]] == ["02001", "00010", "00001"]
    assert plan["components"][0]["required"] == pytest.approx(4_000)   # 40%
    assert plan["components"][1]["required"] == pytest.approx(5_800)   # 58%
    assert plan["components"][2]["required"] == pytest.approx(200)     # 2%


def test_a_product_with_no_recipe_says_so(conn, admin_user):
    created = orders.create(
        {
            "order_type_id": 2,
            "plant_id": 1,
            "company_id": 1,
            "department_id": 3,
            "order_date": "2026-08-17",
            "due_date": "2026-08-19",
            "material_one_id": _material_id("05010", conn),
            "material_one_quantity": 5_000,
        },
        admin_user,
        conn,
    )
    with pytest.raises(BusinessRuleError, match="No recipe is set up"):
        blend.plan(order_id=created[0]["order_id"], conn=conn)


def test_recipe_percentages_must_sum_to_one_hundred(conn, admin_user):
    with pytest.raises(ValidationError, match="not 100"):
        blend.set_recipe(
            _material_id("01031", conn),
            "Bad recipe",
            [
                {"material_id": _material_id("02005", conn), "percentage": 60},
                {"material_id": _material_id("00010", conn), "percentage": 60},
            ],
            admin_user,
            conn=conn,
        )


def test_replacing_a_recipe_keeps_the_old_one_behind_its_batches(conn, admin_user):
    material = _material_id("01031", conn)
    before = blend.recipe_for_material(material, conn)
    blend.set_recipe(
        material,
        "MGR veg - 2.5 (revised)",
        [
            {"material_id": _material_id("02005", conn), "percentage": 40},
            {"material_id": _material_id("00010", conn), "percentage": 58},
            {"material_id": _material_id("00001", conn), "percentage": 2},
        ],
        admin_user,
        conn=conn,
    )
    after = blend.recipe_for_material(material, conn)
    assert after["recipe_id"] != before["recipe_id"]
    old = db.query_one(
        "SELECT active FROM blend_recipe WHERE recipe_id = ?", (before["recipe_id"],), conn
    )
    assert old["active"] == 0


def test_editing_a_recipe_is_not_an_operator_action(conn, operator):
    with pytest.raises(PermissionError_):
        blend.set_recipe(
            _material_id("01020", conn),
            "Nope",
            [{"material_id": _material_id("00010", conn), "percentage": 100}],
            operator,
            conn=conn,
        )


# ------------------------------------------------------------------- batches


def test_a_batch_consumes_every_component_and_produces_the_product(
    conn, operator, blend_order, stocked_components
):
    plan = blend.plan(order_id=blend_order, conn=conn)
    product = plan["material_id"]
    before = inventory.balance_of(plan["to_location_id"], product, conn)

    result = blend.execute(_payload_from(plan, stocked_components), operator, conn)

    assert result["batch_id"].startswith("B-")
    assert len(result["transactions"]) == 3
    assert result["quantity"] == pytest.approx(10_000)
    assert inventory.balance_of(plan["to_location_id"], product, conn) == pytest.approx(
        before + 10_000
    )
    for component in plan["components"]:
        tank = stocked_components[component["material_number"]]
        remaining = inventory.balance_of(tank, component["material_id"], conn)
        assert remaining == pytest.approx(
            {"02001": 80_000, "00010": 120_000, "00001": 10_000}[component["material_number"]]
            - component["required"]
        )
    assert orders.get(blend_order, conn)["qty_fulfilled"] == pytest.approx(10_000)


def test_a_short_component_refuses_the_whole_batch(
    conn, operator, blend_order, stocked_components
):
    """Nothing posts: a half-blended ledger is worse than a refused batch."""

    plan = blend.plan(order_id=blend_order, conn=conn)
    payload = _payload_from(plan, stocked_components)
    payload["components"][2]["quantity"] = 60_000   # far beyond the acid tank
    payload["quantity"] = round(sum(c["quantity"] for c in payload["components"]), 2)

    txns_before = db.scalar("SELECT COUNT(*) FROM inventory_transaction", (), conn)
    with pytest.raises(BusinessRuleError, match="cannot take"):
        blend.execute(payload, operator, conn)
    assert db.scalar("SELECT COUNT(*) FROM inventory_transaction", (), conn) == txns_before


def test_component_quantities_must_sum_to_the_batch(conn, operator, blend_order, stocked_components):
    plan = blend.plan(order_id=blend_order, conn=conn)
    payload = _payload_from(plan, stocked_components)
    payload["components"][0]["quantity"] += 500
    with pytest.raises(ValidationError, match="add up to"):
        blend.execute(payload, operator, conn)


def test_a_retried_batch_returns_the_batch_that_already_ran(
    conn, operator, blend_order, stocked_components
):
    plan = blend.plan(order_id=blend_order, conn=conn)
    key = uuid.uuid4().hex
    first = blend.execute(_payload_from(plan, stocked_components, idempotency_key=key), operator, conn)
    again = blend.execute(_payload_from(plan, stocked_components, idempotency_key=key), operator, conn)
    assert again["batch_id"] == first["batch_id"]
    assert len(again["transactions"]) == 3


def test_a_batch_against_the_wrong_order_is_refused(conn, operator, admin_user, stocked_components):
    created = orders.create(
        {
            "order_type_id": 2,
            "plant_id": 1,
            "company_id": 1,
            "department_id": 3,
            "order_date": "2026-08-17",
            "due_date": "2026-08-19",
            "material_one_id": _material_id("01021", conn),
            "material_one_quantity": 5_000,
        },
        admin_user,
        conn,
    )
    plan = blend.plan(material_id=_material_id("01020", conn), quantity=1_000, plant_id=1, conn=conn)
    payload = _payload_from(plan, stocked_components)
    payload["order_id"] = created[0]["order_id"]
    with pytest.raises(BusinessRuleError, match="not this product"):
        blend.execute(payload, operator, conn)


def test_voiding_a_batch_reverses_every_row_together(
    conn, admin_user, blend_order, stocked_components
):
    plan = blend.plan(order_id=blend_order, conn=conn)
    product = plan["material_id"]
    before = inventory.balance_of(plan["to_location_id"], product, conn)
    result = blend.execute(_payload_from(plan, stocked_components), admin_user, conn)

    voided = blend.void_batch(result["batch_id"], "Wrong ratio", admin_user, conn)

    assert voided["voided"] is True
    assert inventory.balance_of(plan["to_location_id"], product, conn) == pytest.approx(before)
    for component in plan["components"]:
        tank = stocked_components[component["material_number"]]
        assert inventory.balance_of(tank, component["material_id"], conn) == pytest.approx(
            {"02001": 80_000, "00010": 120_000, "00001": 10_000}[component["material_number"]]
        )


def test_a_missing_batch_is_a_clean_not_found(conn):
    with pytest.raises(NotFound):
        blend.batch("B-99999", conn)
