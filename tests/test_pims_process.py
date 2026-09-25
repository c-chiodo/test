"""Staged batches: acidulation as Des Moines runs it.

Soap off trucks and railcars (or off the spur, received earlier) into a
settle tank as 1006 Soap in Process; acid and steam in on top; cook, mix,
settle; break into 20-series oil, MGR and process water, each measured with
its readings. The ledger rows are the ones the legacy PIMS wrote, so the
yields workbook's filters find them.
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


def _work(plant_id: int, reference: str, conn) -> dict:
    return db.query_one('SELECT * FROM "order" WHERE plant_id = ? AND order_reference = ?', (plant_id, reference), conn)


def test_the_demo_opens_with_a_settle_tank_settling(conn):
    batch = process.get("A-00001", conn)
    assert batch["status"] == "settling"
    assert batch["vessel"]["number"] == "DM-1"
    assert batch["process_material"]["number"] == "01006"
    assert batch["stage_minutes"] >= 170
    assert batch["total_in"] == pytest.approx(44_000 + 2_244 + 1_144)
    soap, acid, steam = batch["guide"]
    assert soap["label"] == "Soap" and soap["charged_lbs"] == pytest.approx(44_000)
    assert acid["guide_lbs"] == pytest.approx(2_244)          # 5.1 per 100 lbs soap
    assert steam["guide_lbs"] == pytest.approx(1_144)         # 2.6
    assert batch["metrics"]["theoretical_oil"] == pytest.approx(11_440)   # 26% TFA
    assert batch["metrics"]["expected_oil"] == pytest.approx(6_292)       # 55% first pass
    tile = next(t for t in display.tanks(1, conn)["tanks"] if t["number"] == "DM-1")
    assert tile["batch"]["label"] == "Settling" and tile["mixed"] is False


def test_charges_are_written_the_way_the_legacy_pims_wrote_them(conn):
    """PRODUCED <ingredient> -> 1006 Soap in Process in the settle tank: the
    shape the yields workbook's Processed Soap / Acid_Used / Steam_Used
    sheets filter on."""

    rows = db.query(
        """SELECT tt.code, fm.number AS from_number, tm.number AS to_number, t.from_qty, t.to_qty
           FROM inventory_transaction t JOIN transaction_type tt ON tt.transaction_type_id = t.transaction_type_id
           JOIN material fm ON fm.material_id = t.from_material_id JOIN material tm ON tm.material_id = t.to_material_id
           WHERE t.batch_id = 'A-00001' AND t.to_location_id = ? ORDER BY t.transaction_id""",
        (_location("DM-1", conn),), conn,
    )
    assert [(r["code"], r["from_number"], r["to_number"]) for r in rows] == [
        ("PRODUCE", "00007", "01006"), ("PRODUCE", "00001", "01006"), ("PRODUCE", "00004", "01006"),
    ]
    assert all(r["from_qty"] == r["to_qty"] for r in rows)


def test_a_second_settle_goes_into_the_next_free_tank(conn, admin_user):
    second = _work(1, "SETTLE-DM-2", conn)
    with pytest.raises(BusinessRuleError) as busy:
        process.start({"order_id": second["order_id"], "vessel_id": _location("DM-1", conn)}, admin_user, conn)
    assert busy.value.detail["rule"] == "vessel_busy"
    batch = process.start({"order_id": second["order_id"]}, admin_user, conn)
    try:
        assert batch["vessel"]["number"] == "DM-2"
    finally:
        process.cancel(batch["batch_id"], "test", admin_user, conn)


def test_a_blend_order_is_not_started_as_a_staged_batch(conn, admin_user):
    blend_order = db.query_one(
        """SELECT o.order_id FROM "order" o JOIN blend_recipe r ON r.material_id = o.material_one_id AND r.active = 1
           JOIN status s ON s.status_id = o.status_id
           WHERE r.method = 'blend' AND o.order_type_id = 2 AND s.is_terminal = 0
             AND NOT EXISTS (SELECT 1 FROM blend_recipe x WHERE x.material_id = o.material_one_id AND x.method = 'staged')
           LIMIT 1""",
        (), conn,
    )
    with pytest.raises(BusinessRuleError) as caught:
        process.start({"order_id": blend_order["order_id"]}, admin_user, conn)
    assert caught.value.detail["rule"] == "not_staged"


def test_a_whole_settle_from_the_spur_to_the_break(conn, admin_user):
    order = _work(2, "SETTLE-SC-1", conn)
    wetgums, degum = _material("00010", conn), _material("00006", conn)
    acid, steam = _material("00001", conn), _material("00004", conn)
    oil, mgr, water = _material("01018", conn), _material("01007", conn), _material("01008", conn)
    tank = _location("SC-1", conn)
    before = orders.progress(order["order_id"], 2, conn)["qty_fulfilled"]

    batch = process.start({"order_id": order["order_id"]}, admin_user, conn)
    assert batch["vessel"]["number"] == "SC-1"
    batch_id = batch["batch_id"]

    # Soap waiting on the spur (received for the invoice yesterday) ...
    waiting = process.spur(2, conn)
    car = next(w for w in waiting if w["material_id"] == wetgums)
    assert car["cars"][0]["trailer_number"] == "UTLX 667576"
    process.charge(batch_id, {"material_id": wetgums, "from_location_id": car["location_id"], "quantity": 40_000},
                   admin_user, conn)
    # ... and more straight off a railcar that has just arrived, against its PO.
    po = db.query_one(
        """SELECT order_id FROM "order" WHERE plant_id = 2 AND order_type_id = 3 AND material_one_id = ?
           AND ship_method LIKE 'RL-%'""", (degum,), conn,
    )
    got = process.charge(batch_id, {"material_id": degum, "order_id": po["order_id"], "quantity": 10_000,
                                    "conveyance": "Railcar", "vehicle": "GATX 21977"}, admin_user, conn)
    receipt = next(t for t in got["transactions"] if t["transaction_kind"] == "RECEIVE")
    assert receipt["trailer_number"] == "GATX 21977" and receipt["to_location_number"] == "SC-RECV-RAIL"
    assert got["guide"][0]["charged_lbs"] == pytest.approx(50_000)       # both soaps count as soap
    assert got["guide"][1]["guide_lbs"] == pytest.approx(2_550)          # acid, 5.1 per 100

    with pytest.raises(BusinessRuleError):
        process.advance(batch_id, "settling", admin_user, conn)
    process.advance(batch_id, "acid", admin_user, conn)
    process.charge(batch_id, {"material_id": acid, "from_location_id": _location("SC-103", conn), "quantity": 2_550},
                   admin_user, conn)
    # Steam has no tank to run dry.
    process.charge(batch_id, {"material_id": steam, "from_location_id": _location("SC-975", conn), "quantity": 1_300},
                   admin_user, conn)
    process.advance(batch_id, "mixing", admin_user, conn)
    process.advance(batch_id, "settling", admin_user, conn)
    assert inventory.location_total(tank, conn) == pytest.approx(53_850)

    layers = lambda o, m, w: [  # noqa: E731
        {"material_id": oil, "to_location_id": _location("SC-20", conn), "quantity": o,
         "readings": {"moisture": 1.2, "spintest": 0.1}},
        {"material_id": mgr, "to_location_id": _location("SC-42", conn), "quantity": m,
         "readings": {"moisture": 3.4, "spintest": 0.3}},
        {"material_id": water, "to_location_id": _location("SC-32", conn), "quantity": w},
    ]
    with pytest.raises(BusinessRuleError) as gap:
        process.draw_off(batch_id, {"outputs": layers(7_100, 12_000, 11_000)}, admin_user, conn)
    assert gap.value.detail["rule"] == "unbalanced_break"

    broken = process.draw_off(batch_id, {"outputs": layers(7_100, 12_000, 33_000)}, admin_user, conn)
    assert broken["status"] == "drawn"
    assert {o["role"]: o["lbs"] for o in broken["outputs"]} == {"oil": 7_100, "mgr": 12_000, "water": 33_000}
    assert broken["metrics"]["fpy"] == pytest.approx(54.6, abs=0.05)      # 7,100 / (50,000 x 26%)
    assert broken["metrics"]["split"]["water"] == pytest.approx(63.3, abs=0.1)
    assert broken["metrics"]["unaccounted"] == pytest.approx(53_850 - 52_100)
    # The tank is empty, every layer has its reading, the order has its oil.
    assert inventory.location_total(tank, conn) == pytest.approx(0, abs=0.01)
    oil_row = next(t for t in broken["transactions"] if t["to_material_id"] == oil)
    assert oil_row["readings"] == {"moisture": 1.2, "spintest": 0.1}
    assert oil_row["transaction_type"] == "PRODUCE" and oil_row["from_material_id"] == _material("01006", conn)
    after = orders.progress(order["order_id"], 2, conn)["qty_fulfilled"]
    assert after - before == pytest.approx(7_100)
    with pytest.raises(BusinessRuleError):
        process.draw_off(batch_id, {"outputs": layers(1, 1, 1)}, admin_user, conn)


def test_mgr_is_reprocessed_in_its_own_tanks(conn, admin_user):
    order = _work(1, "MGR-DM-1", conn)
    batch = process.start({"order_id": order["order_id"]}, admin_user, conn)
    try:
        assert batch["vessel"]["number"] == "DM-13"
        assert batch["process_material"]["number"] == "01007"
        got = process.charge(batch["batch_id"], {"material_id": _material("01007", conn),
                                                 "from_location_id": _location("DM-42", conn), "quantity": 10_000},
                             admin_user, conn)
        row = next(t for t in got["transactions"] if t["to_location_number"] == "DM-13")
        # PRODUCED 1007 42 -> 1007 13: the workbook's MGR_Processed sheet.
        assert row["transaction_type"] == "PRODUCE"
        assert (row["from_material_number"], row["to_material_number"]) == ("01007", "01007")
    finally:
        rows = db.query("SELECT transaction_id FROM inventory_transaction WHERE batch_id = ? AND is_reversal = 0",
                        (batch["batch_id"],), conn)
        for r in rows:
            inventory.void(r["transaction_id"], "test clean-up", admin_user, conn)
        process.cancel(batch["batch_id"], "test", admin_user, conn)


def test_only_recipe_ingredients_go_in(conn, admin_user):
    order = _work(1, "SETTLE-DM-2", conn)
    batch = process.start({"order_id": order["order_id"]}, admin_user, conn)
    try:
        with pytest.raises(ValidationError):
            process.charge(batch["batch_id"], {"material_id": _material("00003", conn),
                                               "from_location_id": _location("DM-T101", conn), "quantity": 100},
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
    by_number = {v["number"]: v for v in vessels.json()}
    assert {"DM-1", "DM-2", "DM-3", "DM-13", "DM-14"} <= set(by_number)
    assert by_number["DM-1"]["batch"]["batch_id"] == "A-00001"
    one = client.get("/api/process/A-00001", headers=headers)
    assert one.status_code == 200 and one.json()["stage_label"] == "Settling"
    spur = client.get("/api/process/spur?plant_id=2", headers=headers)
    assert spur.status_code == 200


def test_an_operator_cannot_see_another_plants_batch(client):
    token = client.post("/api/auth/login", json={"username": "toperator", "password": "pims-demo"}).json()["token"]
    refused = client.get("/api/process/vessels?plant_id=2", headers={"Authorization": f"Bearer {token}"})
    assert refused.status_code == 403
