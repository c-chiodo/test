"""Blending onto the trailer — the legacy PROD-LOAD — with caustic dosed
against the pH on the screen, so nothing is posted and reversed to get the
pH right."""

from __future__ import annotations

import pytest

from pims import db
from pims.errors import BusinessRuleError, ValidationError
from pims.services import inventory, loadblend, orders, prefill


def _material(number: str, conn) -> int:
    return db.scalar("SELECT material_id FROM material WHERE number = ?", (number,), conn)


@pytest.fixture()
def cattle_order(conn, admin_user) -> int:
    created = orders.create(
        {
            "order_type_id": 1, "plant_id": 1, "company_id": 1, "department_id": 2, "customer_id": 1,
            "order_date": "2026-09-25", "due_date": "2026-09-26",
            "material_one_id": _material("01021", conn), "material_one_quantity": 45_000,
            "trailer_number": "390",
        },
        admin_user, conn,
    )
    return created[0]["order_id"]


def _components(plan):
    return [{"material_id": c["material_id"], "from_location_id": c["from_location_id"], "quantity": c["required"]}
            for c in plan["components"]]


def test_a_cattle_blend_plans_as_its_recipe(conn, cattle_order):
    plan = loadblend.plan(cattle_order, conn=conn)
    assert [c["material_number"] for c in plan["components"]] == ["01007", "01008", "00003"]
    assert [c["dose"] for c in plan["components"]] == [None, None, "ph"]
    assert plan["components"][0]["required"] == pytest.approx(45_000 * 0.542)
    assert plan["target"] == {"analyte": "ph", "min": 2, "max": 4}      # the limit sheet's cattle blend pH
    assert all(c["from_location_id"] for c in plan["components"])


def test_a_straight_product_is_not_blended(conn, admin_user):
    straight = orders.create(
        {"order_type_id": 1, "plant_id": 1, "company_id": 1, "department_id": 2, "customer_id": 1,
         "order_date": "2026-09-25", "due_date": "2026-09-26",
         "material_one_id": _material("05004", conn), "material_one_quantity": 20_000},
        admin_user, conn,
    )[0]["order_id"]
    assert loadblend.plan(straight, conn=conn) is None


def test_the_whole_truck_posts_once_with_the_dosing_log(conn, admin_user, cattle_order):
    plan = loadblend.plan(cattle_order, conn=conn)
    # Caustic went in three times before the pH was right. None of that was posted.
    doses = [{"quantity": 1_500, "reading": 1.6}, {"quantity": 400, "reading": 1.9}, {"quantity": 209, "reading": 2.6}]
    got = loadblend.execute(cattle_order, {"trailer_number": "390", "components": _components(plan), "doses": doses},
                            admin_user, conn)
    assert got["blended"] is True
    assert [c["material_number"] for c in got["components"]] == ["01007", "01008", "00003"]
    caustic = got["components"][2]
    assert caustic["quantity"] == pytest.approx(2_109)                 # the doses, summed
    rows = db.query(
        """SELECT tt.code, tt.kind, t.to_bol, t.remarks, tm.number AS to_number FROM inventory_transaction t
           JOIN transaction_type tt ON tt.transaction_type_id = t.transaction_type_id
           JOIN material tm ON tm.material_id = t.to_material_id
           WHERE t.order_id = ? ORDER BY t.transaction_id""", (cattle_order,), conn,
    )
    assert {r["code"] for r in rows} == {"PROD_LOAD"} and {r["kind"] for r in rows} == {"LOAD"}
    assert len({r["to_bol"] for r in rows}) == 1                       # one BOL for the truck
    assert {r["to_number"] for r in rows} == {"01021"}                  # the blend is what is on the trailer
    assert "+1,500 lbs -> ph 1.6" in rows[2]["remarks"]
    # No reversals were needed to get there.
    assert db.scalar("SELECT COUNT(*) FROM inventory_transaction WHERE order_id = ? AND is_reversal = 1",
                     (cattle_order,), conn) == 0
    # The order counts the whole truck; one stage waits to ship with the blend on it.
    assert orders.progress(cattle_order, 1, conn)["qty_fulfilled"] == pytest.approx(got["from_qty"])
    stage = next(s for s in inventory.pending_shipments(order_id=cattle_order, conn=conn))
    assert stage["quantity"] == pytest.approx(got["from_qty"]) and stage["material_number"] == "01021"
    # The BOL is one line for the truck, and QC starts from the dosed pH.
    bol = inventory.bill_of_lading(cattle_order, conn, transaction_id=got["transaction_id"])
    assert len(bol["loads"]) == 1 and bol["total_quantity"] == pytest.approx(got["from_qty"])
    assert prefill.for_qc(cattle_order, conn)["values"]["ph"] == 2.6
    # And it ships as the blend.
    shipped = inventory.ship(stage["stage_id"], admin_user, conn)
    assert shipped["from_material_number"] == "01021"


def test_a_ph_outside_the_range_asks_first(conn, admin_user, cattle_order):
    plan = loadblend.plan(cattle_order, conn=conn)
    with pytest.raises(BusinessRuleError) as caught:
        loadblend.execute(cattle_order, {"trailer_number": "391", "components": _components(plan),
                                         "doses": [{"quantity": 900, "reading": 1.4}]}, admin_user, conn)
    assert caught.value.detail["rule"] == "reading_out_of_range"


def test_a_load_needs_a_trailer_and_its_tanks(conn, admin_user, cattle_order):
    plan = loadblend.plan(cattle_order, conn=conn)
    with pytest.raises(ValidationError):
        loadblend.execute(cattle_order, {"trailer_number": "", "components": _components(plan)}, admin_user, conn)
    missing = _components(plan)
    missing[0]["from_location_id"] = None
    with pytest.raises(ValidationError):
        loadblend.execute(cattle_order, {"trailer_number": "392", "components": missing}, admin_user, conn)


def test_a_retried_save_returns_the_same_truck(conn, admin_user, cattle_order):
    plan = loadblend.plan(cattle_order, conn=conn)
    payload = {"trailer_number": "393", "components": _components(plan), "idempotency_key": "truck-393",
               "doses": [{"quantity": 2_000, "reading": 2.8}]}
    first = loadblend.execute(cattle_order, dict(payload), admin_user, conn)
    again = loadblend.execute(cattle_order, dict(payload), admin_user, conn)
    assert again["transaction_id"] == first["transaction_id"]
    assert db.scalar("SELECT COUNT(*) FROM pending_shipment WHERE order_id = ?", (cattle_order,), conn) == 1
