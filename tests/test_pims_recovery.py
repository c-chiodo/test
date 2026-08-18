"""The mistakes an operator actually makes, and what the system does about them.

Every test here started as a defect found by running the app the way a plant
would: a truck shipped twice on a slow link, a load posted again after the
network dropped, a move that turned one product into another, a bill of lading
naming a product that was never on the trailer. They are regression tests, and
each one names the mistake it guards against.
"""

from __future__ import annotations

import uuid

import pytest

from pims import db, security
from pims.errors import AuthError, BusinessRuleError, PermissionError_, ValidationError
from pims.services import inventory, orders, qc


def _material_id(number: str, conn) -> int:
    return db.scalar("SELECT material_id FROM material WHERE number = ?", (number,), conn)


def _location_id(number: str, conn) -> int:
    return db.scalar("SELECT location_id FROM location WHERE number = ?", (number,), conn)


@pytest.fixture()
def operator(conn) -> dict:
    return security.get_user("toperator")


@pytest.fixture()
def stocked_tank(conn, admin_user) -> tuple[int, int]:
    """A fresh tank holding plenty of 05001, and the material id.

    A new location per test rather than a shared one: these tests deliberately
    push tanks to their limits, and one test's leftovers must not decide
    another's outcome.
    """

    number = f"DM-TEST-{uuid.uuid4().hex[:8].upper()}"
    tank = db.insert(
        "location",
        {
            "number": number,
            "description": "Recovery test tank",
            "location_type_id": db.scalar(
                "SELECT location_type_id FROM location_type LIMIT 1", (), conn
            ),
            "plant_id": 1,
            "max_capacity": 500_000,
            "active": 1,
        },
        conn,
    )
    material = _material_id("05001", conn)
    inventory.post(
        "RECEIVE",
        {
            "plant_id": 1,
            "to_location_id": tank,
            "to_material_id": material,
            "to_qty": 120_000,
            "to_bol": f"001-{number}",
        },
        admin_user,
        conn,
    )
    return tank, material


@pytest.fixture()
def sales_order(conn, admin_user, stocked_tank) -> int:
    _tank, material = stocked_tank
    created = orders.create(
        {
            "order_type_id": 1,
            "plant_id": 1,
            "company_id": 1,
            "department_id": 1,
            "order_date": "2026-08-17",
            "due_date": "2026-08-18",
            "customer_id": db.scalar("SELECT customer_id FROM customer LIMIT 1", (), conn),
            "material_one_id": material,
            "material_one_quantity": 40_000,
        },
        admin_user,
        conn,
    )
    return created[0]["order_id"]


def _load(conn, user, order_id, tank, material, qty=5_000, **extra) -> dict:
    payload = {
        "order_id": order_id,
        "plant_id": 1,
        "from_location_id": tank,
        "from_material_id": material,
        "from_qty": qty,
        "to_location_id": _location_id("DM-TRAILER", conn),
        "to_material_id": material,
        "to_qty": qty,
        "trailer_number": "486",
    }
    payload.update(extra)
    return inventory.post("LOAD", payload, user, conn)


# ------------------------------------------------- posting the same load twice


def test_a_repeated_post_with_the_same_key_returns_the_first_transaction(
    conn, admin_user, sales_order, stocked_tank
):
    """The network drops after the server commits; the operator presses again."""

    tank, material = stocked_tank
    key = uuid.uuid4().hex
    first = _load(conn, admin_user, sales_order, tank, material, idempotency_key=key)
    before = inventory.balance_of(tank, material, conn)

    second = _load(conn, admin_user, sales_order, tank, material, idempotency_key=key)

    assert second["transaction_id"] == first["transaction_id"]
    assert inventory.balance_of(tank, material, conn) == pytest.approx(before)
    staged = db.query(
        "SELECT * FROM pending_shipment WHERE transaction_id = ?",
        (first["transaction_id"],),
        conn,
    )
    assert len(staged) == 1


def test_a_genuine_second_load_carries_a_different_key_and_posts(
    conn, admin_user, sales_order, stocked_tank
):
    tank, material = stocked_tank
    first = _load(conn, admin_user, sales_order, tank, material, idempotency_key=uuid.uuid4().hex)
    second = _load(conn, admin_user, sales_order, tank, material, idempotency_key=uuid.uuid4().hex)
    assert first["transaction_id"] != second["transaction_id"]


# ------------------------------------------------------- shipping a trailer twice


def test_shipping_a_stage_twice_is_refused_and_leaves_one_ship(
    conn, admin_user, sales_order, stocked_tank
):
    tank, material = stocked_tank
    load = _load(conn, admin_user, sales_order, tank, material)
    stage = db.query_one(
        "SELECT stage_id FROM pending_shipment WHERE transaction_id = ?",
        (load["transaction_id"],),
        conn,
    )

    inventory.ship(stage["stage_id"], admin_user, conn)
    with pytest.raises(BusinessRuleError, match="already shipped"):
        inventory.ship(stage["stage_id"], admin_user, conn)

    ships = db.query(
        "SELECT t.transaction_id FROM inventory_transaction t"
        " JOIN transaction_type tt ON tt.transaction_type_id = t.transaction_type_id"
        " WHERE t.parent_transaction_id = ? AND tt.code = 'SHIP'",
        (load["transaction_id"],),
        conn,
    )
    assert len(ships) == 1


def test_the_stage_is_claimed_before_the_ship_is_written(
    conn, admin_user, sales_order, stocked_tank
):
    """The flag flip is the guard, so a second reader cannot pass it.

    Reading ``shipped`` and then acting on it let two terminals ship the same
    trailer. The claim happens inside the transaction now, so the stage is
    already marked by the time the SHIP row is written.
    """

    tank, material = stocked_tank
    load = _load(conn, admin_user, sales_order, tank, material)
    stage_id = db.scalar(
        "SELECT stage_id FROM pending_shipment WHERE transaction_id = ?",
        (load["transaction_id"],),
        conn,
    )
    inventory.ship(stage_id, admin_user, conn)
    assert db.scalar(
        "SELECT shipped FROM pending_shipment WHERE stage_id = ?", (stage_id,), conn
    ) == 1


# ------------------------------------------------------ moving and loading rules


def test_a_move_cannot_change_the_product(conn, admin_user, stocked_tank):
    tank, material = stocked_tank
    other_material = _material_id("05010", conn)
    with pytest.raises(ValidationError) as excinfo:
        inventory.post(
            "MOVE",
            {
                "plant_id": 1,
                "from_location_id": tank,
                "from_material_id": material,
                "from_qty": 1_000,
                "to_location_id": _location_id("DM-T102", conn),
                "to_material_id": other_material,
                "to_qty": 1_000,
            },
            admin_user,
            conn,
        )
    assert "cannot change it" in excinfo.value.detail["fields"]["to_material_id"]


def test_a_move_cannot_create_product_out_of_nothing(conn, admin_user, stocked_tank):
    tank, material = stocked_tank
    with pytest.raises(ValidationError) as excinfo:
        inventory.post(
            "MOVE",
            {
                "plant_id": 1,
                "from_location_id": tank,
                "from_material_id": material,
                "from_qty": 1_000,
                "to_location_id": _location_id("DM-T102", conn),
                "to_material_id": material,
                "to_qty": 180_000,
            },
            admin_user,
            conn,
        )
    assert "cannot create or lose" in excinfo.value.detail["fields"]["to_qty"]


def test_produce_may_still_change_product_and_yield(conn, admin_user, stocked_tank):
    """The one operation that is allowed to transmute, because that is its job."""

    tank, material = stocked_tank
    blend = _location_id("DM-BLEND-1", conn)
    produced = inventory.post(
        "PRODUCE",
        {
            "plant_id": 1,
            "from_location_id": tank,
            "from_material_id": material,
            "from_qty": 1_000,
            "to_location_id": blend,
            "to_material_id": _material_id("05010", conn),
            "to_qty": 950,
        },
        admin_user,
        conn,
    )
    assert produced["to_qty"] == 950


def test_an_adjustment_needs_a_reason(conn, admin_user, stocked_tank):
    tank, material = stocked_tank
    with pytest.raises(ValidationError) as excinfo:
        inventory.post(
            "ADJUST",
            {
                "plant_id": 1,
                "to_location_id": tank,
                "to_material_id": material,
                "to_qty": 500,
            },
            admin_user,
            conn,
        )
    assert "why the count is being changed" in excinfo.value.detail["fields"]["remarks"]


# ------------------------------------------------------ loading the wrong thing


def test_loading_a_product_the_order_is_not_for_is_refused(
    conn, admin_user, sales_order, stocked_tank
):
    tank, _material = stocked_tank
    wrong = _material_id("05010", conn)
    inventory.post(
        "RECEIVE",
        {
            "plant_id": 1,
            "to_location_id": tank,
            "to_material_id": wrong,
            "to_qty": 10_000,
            "to_bol": "001-RECOVERY-2",
        },
        admin_user,
        conn,
    )
    with pytest.raises(BusinessRuleError, match="but this load is 05010"):
        _load(conn, admin_user, sales_order, tank, wrong, qty=1_000)


def test_loading_past_the_ordered_quantity_asks_first_then_allows(
    conn, admin_user, sales_order, stocked_tank
):
    tank, material = stocked_tank
    _load(conn, admin_user, sales_order, tank, material, qty=40_000)

    with pytest.raises(BusinessRuleError, match="over"):
        _load(conn, admin_user, sales_order, tank, material, qty=5_000)

    confirmed = _load(
        conn, admin_user, sales_order, tank, material, qty=5_000, acknowledge_over_load=True
    )
    assert confirmed["from_qty"] == 5_000


def test_fulfilment_counts_only_the_ordered_product(
    conn, admin_user, sales_order, stocked_tank
):
    """A load of something else must not make the order look progressed."""

    tank, material = stocked_tank
    _load(conn, admin_user, sales_order, tank, material, qty=4_000)
    order = orders.get(sales_order, conn)
    assert order["qty_fulfilled"] == pytest.approx(4_000)

    # Post a load of a different product directly, bypassing the order check
    # the way a legacy import or a correction would.
    other = _material_id("05010", conn)
    inventory.post(
        "RECEIVE",
        {
            "plant_id": 1,
            "to_location_id": tank,
            "to_material_id": other,
            "to_qty": 9_000,
            "to_bol": "001-RECOVERY-3",
        },
        admin_user,
        conn,
    )
    db.execute(
        "UPDATE inventory_transaction SET order_id = ?"
        " WHERE transaction_id = (SELECT MAX(transaction_id) FROM inventory_transaction)",
        (sales_order,),
        conn,
    )
    assert orders.get(sales_order, conn)["qty_fulfilled"] == pytest.approx(4_000)


def test_remaining_quantity_is_signed_so_an_overload_is_visible(
    conn, admin_user, sales_order, stocked_tank
):
    tank, material = stocked_tank
    _load(conn, admin_user, sales_order, tank, material, qty=40_000)
    _load(
        conn, admin_user, sales_order, tank, material, qty=3_000, acknowledge_over_load=True
    )
    order = orders.get(sales_order, conn)
    assert order["qty_remaining"] == pytest.approx(-3_000)
    assert order["over_by"] == pytest.approx(3_000)


# --------------------------------------------------------------------- voiding


def test_voiding_a_load_cancels_the_stage_rather_than_shipping_it(
    conn, admin_user, sales_order, stocked_tank
):
    tank, material = stocked_tank
    load = _load(conn, admin_user, sales_order, tank, material)
    inventory.void(load["transaction_id"], "Loaded the wrong trailer", admin_user, conn)

    stage = db.query_one(
        "SELECT shipped, cancelled FROM pending_shipment WHERE transaction_id = ?",
        (load["transaction_id"],),
        conn,
    )
    assert stage["cancelled"] == 1
    assert stage["shipped"] == 0
    staged_ids = [
        s["transaction_id"] for s in inventory.pending_shipments(order_id=sales_order, conn=conn)
    ]
    assert load["transaction_id"] not in staged_ids


def test_voiding_a_ship_puts_the_trailer_back_on_the_dock(
    conn, admin_user, sales_order, stocked_tank
):
    """Reversing a shipment used to leave the stage un-shippable forever."""

    tank, material = stocked_tank
    load = _load(conn, admin_user, sales_order, tank, material)
    stage_id = db.scalar(
        "SELECT stage_id FROM pending_shipment WHERE transaction_id = ?",
        (load["transaction_id"],),
        conn,
    )
    ship = inventory.ship(stage_id, admin_user, conn)

    inventory.void(ship["transaction_id"], "Shipped the wrong trailer", admin_user, conn)

    assert db.scalar(
        "SELECT shipped FROM pending_shipment WHERE stage_id = ?", (stage_id,), conn
    ) == 0
    again = inventory.ship(stage_id, admin_user, conn)
    assert again["transaction_id"] != ship["transaction_id"]


def test_an_operator_can_reverse_their_own_recent_unshipped_load(
    conn, operator, sales_order, stocked_tank
):
    tank, material = stocked_tank
    load = _load(conn, operator, sales_order, tank, material, qty=1_200)
    reversal = inventory.void(load["transaction_id"], "Wrong trailer", operator, conn)
    assert reversal["is_reversal"] == 1


def test_an_operator_cannot_reverse_someone_elses_transaction(
    conn, operator, admin_user, sales_order, stocked_tank
):
    tank, material = stocked_tank
    load = _load(conn, admin_user, sales_order, tank, material, qty=1_100)
    with pytest.raises(PermissionError_, match="Ask a supervisor"):
        inventory.void(load["transaction_id"], "Not mine", operator, conn)


def test_an_operator_cannot_reverse_a_load_that_has_shipped(
    conn, operator, admin_user, sales_order, stocked_tank
):
    tank, material = stocked_tank
    load = _load(conn, operator, sales_order, tank, material, qty=1_300)
    stage_id = db.scalar(
        "SELECT stage_id FROM pending_shipment WHERE transaction_id = ?",
        (load["transaction_id"],),
        conn,
    )
    inventory.ship(stage_id, admin_user, conn)
    with pytest.raises(PermissionError_, match="already shipped"):
        inventory.void(load["transaction_id"], "Too late", operator, conn)


def test_activity_says_why_a_row_cannot_be_reversed(
    conn, operator, admin_user, sales_order, stocked_tank
):
    """A blank space where the button should be told the operator nothing."""

    tank, material = stocked_tank
    load = _load(conn, admin_user, sales_order, tank, material, qty=1_400)
    rows = inventory.annotate_void_rights(
        inventory.activity(order_id=sales_order, conn=conn), operator
    )
    row = next(r for r in rows if r["transaction_id"] == load["transaction_id"])
    assert row["can_void"] is False
    assert "posted by someone else" in row["void_blocked"]
    assert "Ask a supervisor" in row["void_blocked"]


# ---------------------------------------------------------------- the document


def test_the_ship_list_names_the_product_on_the_trailer(
    conn, admin_user, sales_order, stocked_tank
):
    """Not the one on the order header, which is what it used to show."""

    tank, material = stocked_tank
    load = _load(conn, admin_user, sales_order, tank, material, qty=2_500)
    stage = next(
        s
        for s in inventory.pending_shipments(order_id=sales_order, conn=conn)
        if s["transaction_id"] == load["transaction_id"]
    )
    assert stage["material_number"] == "05001"
    assert stage["loaded_by"]


def test_a_bill_of_lading_for_one_load_carries_that_load_alone(
    conn, admin_user, sales_order, stocked_tank
):
    """The driver's paperwork is for the truck in front of them."""

    tank, material = stocked_tank
    first = _load(conn, admin_user, sales_order, tank, material, qty=3_000)
    _load(conn, admin_user, sales_order, tank, material, qty=7_000)

    whole_order = inventory.bill_of_lading(sales_order, conn)
    assert whole_order["total_quantity"] == pytest.approx(10_000)

    one_truck = inventory.bill_of_lading(
        sales_order, conn, transaction_id=first["transaction_id"]
    )
    assert one_truck["total_quantity"] == pytest.approx(3_000)
    assert len(one_truck["loads"]) == 1
    assert one_truck["single_load"] is True
    assert one_truck["material_mismatch"] is False
    assert [p["number"] for p in one_truck["products"]] == ["05001"]


# --------------------------------------------------------------------- the QC


def test_a_qc_number_that_will_not_parse_is_rejected(conn, admin_user, sales_order):
    with pytest.raises(ValidationError) as excinfo:
        qc.save(sales_order, {"moisture": "abc", "ph": "7.2.1"}, admin_user, conn=conn)
    fields = excinfo.value.detail["fields"]
    assert "is not a number" in fields["moisture"]
    assert "is not a number" in fields["ph"]


def test_a_blank_qc_field_is_still_allowed(conn, admin_user, sales_order):
    record = qc.save(sales_order, {"moisture": "", "ph": None}, admin_user, conn=conn)
    assert record["moisture"] is None


def test_an_out_of_spec_result_cannot_be_saved_without_an_acknowledgement(
    conn, admin_user, sales_order
):
    with pytest.raises(BusinessRuleError, match="outside spec"):
        qc.save(sales_order, {"ffa": 99, "moisture": 1.0}, admin_user, conn=conn)

    saved = qc.save(
        sales_order,
        {"ffa": 99, "moisture": 1.0},
        admin_user,
        acknowledge_warnings=True,
        conn=conn,
    )
    assert saved["acknowledged_warnings"] == 1
    assert "ffa" in saved["warning_snapshot"]


def test_a_missing_reading_stays_advisory(conn, admin_user, sales_order):
    """Partial results are a normal part of the job and must still save."""

    saved = qc.save(sales_order, {"moisture": 1.0}, admin_user, conn=conn)
    assert saved["qc_id"]


# -------------------------------------------------------------- the checklist


def test_not_applicable_is_recorded_rather_than_left_blank(conn, admin_user, sales_order):
    questions = qc.checklist_questions(conn=conn)
    responses = {str(q["question_id"]): "Yes" for q in questions}
    first = questions[0]["question_id"]
    responses[str(first)] = "N/A"

    header = qc.save_qa_checklist(sales_order, {"responses": responses}, admin_user, conn)
    assert questions[0]["question"] in header["not_applicable"]
    assert questions[0]["question"] not in header["exceptions"]


def test_a_blank_answer_is_still_refused(conn, admin_user, sales_order):
    questions = qc.checklist_questions(conn=conn)
    responses = {str(q["question_id"]): "Yes" for q in questions}
    responses[str(questions[0]["question_id"])] = "  "
    with pytest.raises(ValidationError, match="N/A"):
        qc.save_qa_checklist(sales_order, {"responses": responses}, admin_user, conn)


def test_the_trailer_inspection_questions_come_before_the_load(conn):
    """Four of them inspect an empty trailer; asking after the load is theatre."""

    pre = qc.checklist_questions("pre_load", conn=conn)
    assert [q["question"] for q in pre]
    assert all(q["stage"] == "pre_load" for q in pre)
    assert "clean" in pre[0]["question"].lower()


def test_the_pre_load_checklist_can_be_saved_on_its_own(conn, admin_user, sales_order):
    pre = qc.checklist_questions("pre_load", conn=conn)
    header = qc.save_qa_checklist(
        sales_order,
        {"responses": {str(q["question_id"]): "Yes" for q in pre}},
        admin_user,
        conn,
        stage="pre_load",
    )
    assert header["stage"] == "pre_load"


# ------------------------------------------------------------------ the gates


def test_changing_a_product_limit_needs_more_than_permission_to_read_it(conn):
    qc_user = security.get_user("rprice")
    assert security.has_permission(qc_user, "spec.read")
    assert not security.has_permission(qc_user, "spec.write")


def test_a_refusal_names_the_action_and_who_can_do_it(conn):
    operator_user = security.get_user("toperator")
    with pytest.raises(PermissionError_) as excinfo:
        security.require_permission(operator_user, "txn.void")
    assert "void a transaction" in str(excinfo.value)
    assert "Ask a supervisor" in str(excinfo.value)


def test_repeated_bad_pins_pause_the_account(conn):
    from pims.seed import USER_PINS

    security._attempts.clear()
    try:
        for _ in range(5):
            with pytest.raises(AuthError, match="not recognised"):
                security.login_with_pin("toperator", "0000", conn=conn)
        with pytest.raises(AuthError, match="Too many failed attempts"):
            security.login_with_pin("toperator", USER_PINS["toperator"], conn=conn)
    finally:
        security._attempts.clear()


def test_a_good_pin_clears_the_count(conn):
    from pims.seed import USER_PINS

    security._attempts.clear()
    with pytest.raises(AuthError):
        security.login_with_pin("toperator", "0000", conn=conn)
    security.login_with_pin("toperator", USER_PINS["toperator"], conn=conn)
    assert "pin:toperator" not in security._attempts


# ---------------------------------------------------------------- the history


def test_an_orders_history_includes_its_loads_and_its_qc(
    conn, admin_user, sales_order, stocked_tank
):
    """The screen a supervisor opens to see what happened was empty."""

    from pims import audit

    tank, material = stocked_tank
    _load(conn, admin_user, sales_order, tank, material, qty=1_500)
    qc.save(sales_order, {"moisture": 1.2}, admin_user, conn=conn)

    history = audit.for_order(sales_order, conn=conn)
    actions = {row["action"] for row in history}
    assert "post.load" in actions
    assert "qc.create" in actions


def test_a_load_posts_without_the_trailer_check(conn, admin_user, sales_order, stocked_tank):
    """The trailer check advises; it does not stand between a truck and the dock."""

    tank, material = stocked_tank
    load = _load(conn, admin_user, sales_order, tank, material, qty=900)
    assert load["transaction_id"]


def test_a_load_before_the_trailer_check_is_reported(
    conn, admin_user, sales_order, stocked_tank
):
    """Skipping it is allowed. It is not invisible."""

    from pims import health

    tank, material = stocked_tank
    load = _load(conn, admin_user, sales_order, tank, material, qty=800)

    finding = next(
        f for f in health.data_quality(plant_id=1, conn=conn)["findings"]
        if f["key"] == "unchecked_loads"
    )
    assert load["transaction_id"] in [row["transaction_id"] for row in finding["rows"]]


def test_answering_the_trailer_check_first_clears_the_finding(
    conn, admin_user, stocked_tank
):
    from pims import health

    tank, material = stocked_tank
    order_id = orders.create(
        {
            "order_type_id": 1,
            "plant_id": 1,
            "company_id": 1,
            "department_id": 1,
            "order_date": "2026-08-17",
            "due_date": "2026-08-18",
            "customer_id": db.scalar("SELECT customer_id FROM customer LIMIT 1", (), conn),
            "material_one_id": material,
            "material_one_quantity": 40_000,
        },
        admin_user,
        conn,
    )[0]["order_id"]

    pre = qc.checklist_questions("pre_load", conn=conn)
    qc.save_qa_checklist(
        order_id,
        {"responses": {str(q["question_id"]): "Yes" for q in pre}},
        admin_user,
        conn,
        stage="pre_load",
    )
    load = _load(conn, admin_user, order_id, tank, material, qty=700)

    finding = next(
        f for f in health.data_quality(plant_id=1, conn=conn)["findings"]
        if f["key"] == "unchecked_loads"
    )
    assert load["transaction_id"] not in [row["transaction_id"] for row in finding["rows"]]
