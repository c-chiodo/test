"""Inventory posting rules, balances and reversals."""

from __future__ import annotations

import pytest

from pims import db
from pims.errors import BusinessRuleError, PermissionError_, ValidationError
from pims.services import inventory, orders


def _material_id(number: str, conn) -> int:
    return db.scalar("SELECT material_id FROM material WHERE number = ?", (number,), conn)


def _location_id(number: str, conn) -> int:
    return db.scalar("SELECT location_id FROM location WHERE number = ?", (number,), conn)


@pytest.fixture()
def work_order(conn, admin_user) -> int:
    created = orders.create(
        {
            "order_type_id": 2,
            "plant_id": 1,
            "company_id": 1,
            "department_id": 3,
            "order_date": "2026-08-17",
            "due_date": "2026-08-18",
            "material_one_id": _material_id("05001", conn),
            "material_one_quantity": 20_000,
        },
        admin_user,
        conn,
    )
    return created[0]["order_id"]


def test_receive_then_move_tracks_the_balance(conn, admin_user, work_order):
    tank = _location_id("DM-T101", conn)
    other = _location_id("DM-T102", conn)
    material = _material_id("05001", conn)

    before_tank = inventory.balance_of(tank, material, conn)
    before_other = inventory.balance_of(other, material, conn)

    inventory.post(
        "RECEIVE",
        {
            "order_id": work_order,
            "to_location_id": tank,
            "to_material_id": material,
            "to_qty": 10_000,
            "to_bol": "001-TEST-1",
        },
        admin_user,
        conn,
    )
    assert inventory.balance_of(tank, material, conn) == pytest.approx(before_tank + 10_000)

    inventory.post(
        "MOVE",
        {
            "plant_id": 1,
            "from_location_id": tank,
            "from_material_id": material,
            "from_qty": 4_000,
            "to_location_id": other,
            "to_material_id": material,
            "to_qty": 4_000,
        },
        admin_user,
        conn,
    )
    assert inventory.balance_of(tank, material, conn) == pytest.approx(before_tank + 6_000)
    assert inventory.balance_of(other, material, conn) == pytest.approx(before_other + 4_000)


def test_cannot_take_more_than_a_location_holds(conn, admin_user):
    tank = _location_id("DM-T103", conn)
    material = _material_id("05003", conn)
    available = inventory.balance_of(tank, material, conn)

    with pytest.raises(BusinessRuleError) as excinfo:
        inventory.post(
            "MOVE",
            {
                "plant_id": 1,
                "from_location_id": tank,
                "from_material_id": material,
                "from_qty": available + 5_000,
                "to_location_id": _location_id("DM-T104", conn),
                "to_material_id": material,
                "to_qty": available + 5_000,
            },
            admin_user,
            conn,
        )
    assert excinfo.value.detail["requested"] > excinfo.value.detail["available"]


def test_capacity_is_enforced_on_the_receiving_location(conn, admin_user):
    tank = _location_id("DM-T105", conn)
    material = _material_id("05001", conn)
    capacity = db.scalar(
        "SELECT max_capacity FROM location WHERE location_id = ?", (tank,), conn
    )
    current = inventory.location_total(tank, conn)

    with pytest.raises(BusinessRuleError) as excinfo:
        inventory.post(
            "RECEIVE",
            {
                "plant_id": 1,
                "to_location_id": tank,
                "to_material_id": material,
                "to_qty": capacity - current + 1_000,
                "to_bol": "001-CAP-1",
            },
            admin_user,
            conn,
        )
    assert excinfo.value.detail["capacity"] == pytest.approx(capacity)


def test_move_between_the_same_location_is_rejected(conn, admin_user):
    tank = _location_id("DM-T106", conn)
    material = _material_id("05001", conn)
    with pytest.raises(ValidationError):
        inventory.post(
            "MOVE",
            {
                "plant_id": 1,
                "from_location_id": tank,
                "from_material_id": material,
                "from_qty": 100,
                "to_location_id": tank,
                "to_material_id": material,
                "to_qty": 100,
            },
            admin_user,
            conn,
        )


def test_void_writes_a_reversal_and_keeps_the_original(conn, admin_user):
    tank = _location_id("DM-T107", conn)
    material = _material_id("05001", conn)
    opening = inventory.balance_of(tank, material, conn)

    txn = inventory.post(
        "RECEIVE",
        {
            "plant_id": 1,
            "to_location_id": tank,
            "to_material_id": material,
            "to_qty": 3_000,
            "to_bol": "001-VOID-1",
        },
        admin_user,
        conn,
    )
    assert inventory.balance_of(tank, material, conn) == pytest.approx(opening + 3_000)

    reversal = inventory.void(txn["transaction_id"], "keyed against the wrong tank", admin_user, conn)

    assert inventory.balance_of(tank, material, conn) == pytest.approx(opening)
    assert inventory.get(txn["transaction_id"], conn)["voided"] == 1
    assert reversal["parent_transaction_id"] == txn["transaction_id"]
    assert "keyed against the wrong tank" in reversal["remarks"]


def test_void_requires_a_reason(conn, admin_user):
    tank = _location_id("DM-T108", conn)
    material = _material_id("05001", conn)
    txn = inventory.post(
        "RECEIVE",
        {
            "plant_id": 1,
            "to_location_id": tank,
            "to_material_id": material,
            "to_qty": 500,
            "to_bol": "001-VOID-2",
        },
        admin_user,
        conn,
    )
    with pytest.raises(ValidationError):
        inventory.void(txn["transaction_id"], "   ", admin_user, conn)


def test_load_then_ship_clears_the_staged_trailer(conn, admin_user):
    material = _material_id("05001", conn)
    tank = _location_id("DM-T101", conn)
    trailer = _location_id("DM-TRAILER", conn)
    created = orders.create(
        {
            "order_type_id": 1,
            "plant_id": 1,
            "company_id": 1,
            "department_id": 2,
            "order_date": "2026-08-17",
            "due_date": "2026-08-18",
            "customer_id": 2,
            "material_one_id": material,
            "material_one_quantity": 5_000,
        },
        admin_user,
        conn,
    )
    order_id = created[0]["order_id"]

    # Relative to whatever is already staged: other tests load this dock too.
    trailer_before = inventory.balance_of(trailer, material, conn)

    # Make sure the tank holds this product — the seeded contents vary.
    inventory.post(
        "RECEIVE",
        {
            "plant_id": 1,
            "to_location_id": tank,
            "to_material_id": material,
            "to_qty": 5_000,
            "to_bol": "001-STOCK-1",
        },
        admin_user,
        conn,
    )
    inventory.post(
        "LOAD",
        {
            "order_id": order_id,
            "from_location_id": tank,
            "from_material_id": material,
            "from_qty": 5_000,
            "to_location_id": trailer,
            "to_material_id": material,
            "to_qty": 5_000,
            "to_bol": "001-SHIP-1",
            "trailer_number": "614",
        },
        admin_user,
        conn,
    )
    staged = inventory.pending_shipments(order_id=order_id, conn=conn)
    assert len(staged) == 1

    inventory.ship(staged[0]["stage_id"], admin_user, conn)
    assert inventory.pending_shipments(order_id=order_id, conn=conn) == []
    assert inventory.balance_of(trailer, material, conn) == pytest.approx(trailer_before)

    progress = orders.get(order_id, conn)
    assert progress["percent_complete"] == pytest.approx(100.0)
    assert progress["qty_shipped"] == pytest.approx(5_000)


def test_orders_with_unshipped_loads_do_not_close_silently(conn, admin_user):
    material = _material_id("05001", conn)
    created = orders.create(
        {
            "order_type_id": 1,
            "plant_id": 1,
            "company_id": 1,
            "department_id": 2,
            "order_date": "2026-08-17",
            "due_date": "2026-08-18",
            "customer_id": 3,
            "material_one_id": material,
            "material_one_quantity": 2_000,
        },
        admin_user,
        conn,
    )
    order_id = created[0]["order_id"]
    inventory.post(
        "RECEIVE",
        {
            "plant_id": 1,
            "to_location_id": _location_id("DM-T101", conn),
            "to_material_id": material,
            "to_qty": 2_000,
            "to_bol": "001-STOCK-2",
        },
        admin_user,
        conn,
    )
    inventory.post(
        "LOAD",
        {
            "order_id": order_id,
            "from_location_id": _location_id("DM-T101", conn),
            "from_material_id": material,
            "from_qty": 2_000,
            "to_location_id": _location_id("DM-TRAILER", conn),
            "to_material_id": material,
            "to_qty": 2_000,
            "to_bol": "001-OPEN-1",
            "trailer_number": "615",
        },
        admin_user,
        conn,
    )

    result = orders.close([order_id], admin_user, conn)
    assert result["closed"] == []
    assert "not shipped" in result["skipped"][0]["reason"]

    forced = orders.close([order_id], admin_user, conn, force=True)
    assert forced["closed"] == [order_id]


def test_plant_access_is_enforced_on_writes(conn):
    from pims import security

    operator = security.get_user("toperator")     # granted DM only
    material = _material_id("05001", conn)
    with pytest.raises(PermissionError_):
        inventory.post(
            "RECEIVE",
            {
                "plant_id": 2,                      # Sioux City
                "to_location_id": _location_id("SC-T101", conn),
                "to_material_id": material,
                "to_qty": 100,
                "to_bol": "001-ACL-1",
            },
            operator,
            conn,
        )


def test_balance_can_be_reconstructed_at_a_point_in_time(conn, admin_user):
    tank = _location_id("DM-T102", conn)
    material = _material_id("05001", conn)
    before = inventory.balance_of(tank, material, conn)

    inventory.post(
        "RECEIVE",
        {
            "plant_id": 1,
            "to_location_id": tank,
            "to_material_id": material,
            "to_qty": 1_500,
            "to_bol": "001-ASOF-1",
        },
        admin_user,
        conn,
    )
    historic = inventory.location_balance(
        location_id=tank,
        material_id=material,
        as_of="2026-08-01T00:00:00+00:00",
        conn=conn,
    )
    now = inventory.balance_of(tank, material, conn)
    assert now == pytest.approx(before + 1_500)
    assert historic[0]["balance"] != now
