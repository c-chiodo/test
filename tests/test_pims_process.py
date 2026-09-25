"""Staged batches: acidulation's charge, acid, mix, settle and draw-off.

Soap goes into the reactor from tanks or straight off a truck or railcar,
acid on top; it mixes, it settles, and what comes off is measured. The
ledger shows every pound: charges are moves (or receipts) into the reactor,
the draw-off consumes everything charged and produces what was measured, and
the difference is the acid water.
"""

from __future__ import annotations

import pytest

from pims import db
from pims.errors import BusinessRuleError, ValidationError
from pims.services import display, inventory, orders, process


def _id(sql: str, params, conn):
    return db.scalar(sql, params, conn)


def _material(number: str, conn) -> int:
    return _id("SELECT material_id FROM material WHERE number = ?", (number,), conn)


def _location(number: str, conn) -> int:
    return _id("SELECT location_id FROM location WHERE number = ?", (number,), conn)


def _acid_orders(plant_id: int, conn) -> list[dict]:
    return db.query(
        """SELECT o.* FROM "order" o JOIN status s ON s.status_id = o.status_id
           JOIN department d ON d.department_id = o.department_id
           WHERE d.code = 'ACID' AND o.plant_id = ? AND s.is_terminal = 0 AND o.order_type_id = 2
           ORDER BY o.order_id""",
        (plant_id,),
        conn,
    )


def test_the_demo_opens_with_a_reactor_settling(conn):
    batch = process.get("A-00001", conn)
    assert batch["status"] == "settling"
    assert batch["vessel"]["number"] == "DM-ACID-1"
    assert batch["stage_minutes"] >= 170
    assert batch["total_in"] == pytest.approx(20_000)
    assert batch["expected_out"] == pytest.approx(16_000)
    tile = next(t for t in display.tanks(1, conn)["tanks"] if t["number"] == "DM-ACID-1")
    assert tile["batch"]["label"] == "Settling"
    assert tile["mixed"] is False


def test_a_busy_reactor_is_not_offered_again(conn, admin_user):
    second = _acid_orders(1, conn)[1]
    with pytest.raises(BusinessRuleError) as caught:
        process.start({"order_id": second["order_id"]}, admin_user, conn)
    assert caught.value.detail["rule"] == "no_free_vessel"


def test_a_blend_order_is_not_started_as_a_staged_batch(conn, admin_user):
    blend_order = db.query_one(
        """SELECT o.order_id FROM "order" o JOIN blend_recipe r ON r.material_id = o.material_one_id AND r.active = 1
           JOIN status s ON s.status_id = o.status_id
           WHERE r.method = 'blend' AND o.order_type_id = 2 AND s.is_terminal = 0 LIMIT 1""",
        (), conn,
    )
    with pytest.raises(BusinessRuleError) as caught:
        process.start({"order_id": blend_order["order_id"]}, admin_user, conn)
    assert caught.value.detail["rule"] == "not_staged"


def test_a_whole_acid_batch_from_soap_to_draw_off(conn, admin_user):
    order = _acid_orders(2, conn)[0]
    reactor = _location("SC-ACID-1", conn)
    soap, acid, water = _material("02005", conn), _material("00001", conn), _material("00010", conn)
    before = orders.progress(order["order_id"], 2, conn)["qty_fulfilled"]

    batch = process.start({"order_id": order["order_id"]}, admin_user, conn)
    assert batch["batch_id"].startswith("A-")
    assert batch["vessel_id"] == reactor
    assert batch["status"] == "charging"
    # Starting again for the same order returns the batch already running.
    assert process.start({"order_id": order["order_id"]}, admin_user, conn)["batch_id"] == batch["batch_id"]
    batch_id = batch["batch_id"]

    # Soap from a tank, and more straight off a railcar against its PO.
    process.charge(batch_id, {"material_id": soap, "from_location_id": _location("SC-T104", conn), "quantity": 10_000}, admin_user, conn)
    po = db.query_one(
        """SELECT o.order_id FROM "order" o WHERE o.plant_id = 2 AND o.order_type_id = 3
           AND o.material_one_id = ? AND o.ship_method = 'Rail'""",
        (soap,), conn,
    )
    got = process.charge(
        batch_id,
        {"material_id": soap, "order_id": po["order_id"], "quantity": 8_000, "conveyance": "Railcar", "vehicle": "UTLX 204417"},
        admin_user, conn,
    )
    assert got["total_in"] == pytest.approx(18_000)
    receipt = next(t for t in got["transactions"] if t["transaction_type"] == "RECEIVE")
    assert receipt["trailer_number"] == "UTLX 204417"
    assert "railcar" in receipt["remarks"].lower()
    # The guide rescales acid to the soap actually charged: 90 : 6.
    acid_guide = next(g for g in got["guide"] if g["material_id"] == acid)
    assert acid_guide["guide_lbs"] == pytest.approx(1_200)

    # Stages go in order.
    with pytest.raises(BusinessRuleError):
        process.advance(batch_id, "settling", admin_user, conn)
    process.advance(batch_id, "acid", admin_user, conn)
    process.charge(batch_id, {"material_id": acid, "from_location_id": _location("SC-T101", conn), "quantity": 1_200}, admin_user, conn)
    process.charge(batch_id, {"material_id": water, "from_location_id": _location("SC-T102", conn), "quantity": 800}, admin_user, conn)
    process.advance(batch_id, "mixing", admin_user, conn)
    with pytest.raises(BusinessRuleError):
        process.draw_off(batch_id, {"to_location_id": _location("SC-T103", conn), "quantity": 16_000}, admin_user, conn)
    settled = process.advance(batch_id, "settling", admin_user, conn)
    assert settled["total_in"] == pytest.approx(20_000)
    assert inventory.location_total(reactor, conn) == pytest.approx(20_000)

    # Nothing more goes in once it is settling.
    with pytest.raises(BusinessRuleError):
        process.charge(batch_id, {"material_id": acid, "from_location_id": _location("SC-T101", conn), "quantity": 10}, admin_user, conn)

    # A reading far from the usual yield asks first.
    with pytest.raises(BusinessRuleError) as odd:
        process.draw_off(batch_id, {"to_location_id": _location("SC-T103", conn), "quantity": 11_000}, admin_user, conn)
    assert odd.value.detail["rule"] == "unexpected_yield"
    with pytest.raises(ValidationError):
        process.draw_off(batch_id, {"to_location_id": _location("SC-T103", conn), "quantity": 25_000}, admin_user, conn)

    drawn = process.draw_off(batch_id, {"to_location_id": _location("SC-T103", conn), "quantity": 15_800}, admin_user, conn)
    assert drawn["status"] == "drawn"
    assert drawn["drawn_lbs"] == pytest.approx(15_800)
    produce = [t for t in drawn["transactions"] if t["transaction_type"] == "PRODUCE"]
    assert sum(t["from_qty"] for t in produce) == pytest.approx(20_000)
    assert sum(t["to_qty"] for t in produce) == pytest.approx(15_800)
    assert "acid water" in produce[0]["remarks"]
    # The reactor is empty again and the order is credited with what came off.
    assert inventory.location_total(reactor, conn) == pytest.approx(0, abs=0.01)
    after = orders.progress(order["order_id"], 2, conn)["qty_fulfilled"]
    assert after - before == pytest.approx(15_800)
    with pytest.raises(BusinessRuleError):
        process.draw_off(batch_id, {"to_location_id": _location("SC-T103", conn), "quantity": 1}, admin_user, conn)


def test_only_recipe_ingredients_go_in(conn, admin_user):
    order = _acid_orders(2, conn)[-1]
    batch = process.start({"order_id": order["order_id"]}, admin_user, conn)
    try:
        with pytest.raises(ValidationError):
            process.charge(batch["batch_id"], {"material_id": _material("05004", conn),
                                               "from_location_id": _location("SC-T101", conn), "quantity": 100},
                           admin_user, conn)
        with pytest.raises(BusinessRuleError):
            process.advance(batch["batch_id"], "acid", admin_user, conn)   # nothing charged yet
    finally:
        process.cancel(batch["batch_id"], "test", admin_user, conn)
    assert process.get(batch["batch_id"], conn)["status"] == "cancelled"


def test_a_batch_with_product_in_cannot_be_cancelled(conn, admin_user):
    with pytest.raises(BusinessRuleError) as caught:
        process.cancel("A-00001", "", admin_user, conn)
    assert caught.value.detail["rule"] == "batch_not_empty"


def test_the_staged_batch_is_not_voided_like_a_blend(conn, admin_user):
    from pims.services import blend

    with pytest.raises(BusinessRuleError) as caught:
        blend.void_batch("A-00001", "no", admin_user, conn)
    assert caught.value.detail["rule"] == "staged_batch"


def test_the_acid_screen_api(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    acid = next(d for d in client.get("/api/departments?plant_id=1", headers=headers).json() if d["code"] == "ACID")
    assert acid["methods"] == ["staged"]
    vessels = client.get(f"/api/process/vessels?plant_id=1&department_id={acid['department_id']}", headers=headers)
    assert vessels.status_code == 200
    reactor = vessels.json()[0]
    assert reactor["number"] == "DM-ACID-1"
    assert reactor["batch"]["batch_id"] == "A-00001"
    one = client.get("/api/process/A-00001", headers=headers)
    assert one.status_code == 200 and one.json()["stage_label"] == "Settling"


def test_an_operator_cannot_see_another_plants_batch(client):
    token = client.post("/api/auth/login", json={"username": "toperator", "password": "pims-demo"}).json()["token"]
    refused = client.get("/api/process/vessels?plant_id=2", headers={"Authorization": f"Bearer {token}"})
    assert refused.status_code == 403
