"""Numbering, prefill, scanning, alerts, scheduled jobs and integrations."""

from __future__ import annotations

import pytest

from pims import db, security
from pims.errors import AuthError, ValidationError
from pims.integrations import gp_sync, lims_ingest
from pims.integrations import scale as scale_integration
from pims.services import alerts, inventory, jobs, numbering, orders, prefill, qc, scan


def _material(number: str, conn) -> int:
    return db.scalar("SELECT material_id FROM material WHERE number = ?", (number,), conn)


def _location(number: str, conn) -> int:
    return db.scalar("SELECT location_id FROM location WHERE number = ?", (number,), conn)


@pytest.fixture()
def sales_order(conn, admin_user) -> int:
    created = orders.create(
        {
            "order_type_id": 1,
            "plant_id": 1,
            "company_id": 1,
            "department_id": 2,
            "order_date": "2026-08-17",
            "due_date": "2026-08-19",
            "customer_id": 3,
            "material_one_id": _material("05001", conn),
            "material_one_quantity": 6_000,
        },
        admin_user,
        conn,
    )
    return created[0]["order_id"]


# ------------------------------------------------------------- numbering


def test_sequences_never_hand_out_the_same_number_twice(conn):
    first = numbering.next_in_sequence("test-counter", conn)
    second = numbering.next_in_sequence("test-counter", conn)
    assert second == first + 1
    assert numbering.peek_sequence("test-counter", conn) == second + 1


def test_generated_bol_matches_the_legacy_shape(conn):
    number = numbering.next_bol("614", conn)
    assert number.startswith("001-")
    assert number.endswith("(614)")
    body = number[len("001-") : number.index("-1(")]
    assert body.isdigit() and len(body) == 6


def test_generated_sample_number_matches_the_legacy_shape(conn, sales_order):
    """DMC1359D251216Q0360397 — plant, party, date, sequence."""

    sample = numbering.next_sample_number(sales_order, when="2026-08-17T10:00:00+00:00", conn=conn)
    assert sample.startswith("DMC")            # Des Moines, customer
    assert "D260817Q" in sample
    assert sample.split("Q")[1].isdigit()


def test_purchase_order_samples_carry_the_vendor_marker(conn, admin_user):
    created = orders.create(
        {
            "order_type_id": 3,
            "plant_id": 1,
            "company_id": 1,
            "department_id": 1,
            "order_date": "2026-08-17",
            "due_date": "2026-08-18",
            "vendor_id": 1,
            "material_one_id": _material("02001", conn),
            "material_one_quantity": 5_000,
        },
        admin_user,
        conn,
    )
    sample = numbering.next_sample_number(created[0]["order_id"], conn=conn)
    assert sample.startswith("DMA")            # 'A' for a vendor, as in SCA1976…


def test_a_load_mints_its_own_bol(conn, admin_user, sales_order):
    """The loadout dock requires a BOL; the operator no longer types it."""

    material = _material("05001", conn)
    inventory.post(
        "RECEIVE",
        {
            "plant_id": 1,
            "to_location_id": _location("DM-T101", conn),
            "to_material_id": material,
            "to_qty": 6_000,
        },
        admin_user,
        conn,
    )
    txn = inventory.post(
        "LOAD",
        {
            "order_id": sales_order,
            "from_location_id": _location("DM-T101", conn),
            "from_material_id": material,
            "from_qty": 6_000,
            "to_location_id": _location("DM-TRAILER", conn),
            "to_material_id": material,
            "to_qty": 6_000,
            "trailer_number": "701",
        },
        admin_user,
        conn,
    )
    assert txn["to_bol"].startswith("001-")
    assert txn["to_bol"].endswith("(701)")


def test_sample_numbers_are_not_generated_unless_asked(conn, admin_user, sales_order):
    """Off by default: a code LabWare does not know is worse than a blank."""

    assert numbering.setting("sample.auto_generate", conn) == "false"
    record = qc.save(sales_order, {"moisture": 1.2}, admin_user, conn=conn)
    assert record["sample_number"] == ""

    explicit = qc.save(
        sales_order,
        {"moisture": 1.3, "generate_sample_number": True},
        admin_user,
        conn=conn,
    )
    assert explicit["sample_number"].startswith("DMC")


# --------------------------------------------------------------- prefill


def test_prefill_uses_the_plant_default_receiving_location(conn, admin_user):
    created = orders.create(
        {
            "order_type_id": 3,
            "plant_id": 1,
            "company_id": 1,
            "department_id": 1,
            "order_date": "2026-08-17",
            "due_date": "2026-08-18",
            "vendor_id": 2,
            "material_one_id": _material("00001", conn),
            "material_one_quantity": 12_000,
        },
        admin_user,
        conn,
    )
    suggestion = prefill.for_operation("receive", order_id=created[0]["order_id"], conn=conn)
    default = prefill.default_location(3, 1, conn)

    assert default is not None, "the seeded location_default should be readable"
    assert suggestion["values"]["to_location_id"] == default
    assert suggestion["values"]["to_material_id"] == _material("00001", conn)
    assert suggestion["values"]["to_qty"] == 12_000
    assert any("default" in note for note in suggestion["notes"])


def test_prefill_picks_the_tank_that_actually_holds_the_product(conn, admin_user, sales_order):
    material = _material("05001", conn)
    tank = _location("DM-T104", conn)
    inventory.post(
        "RECEIVE",
        {"plant_id": 1, "to_location_id": tank, "to_material_id": material, "to_qty": 90_000},
        admin_user,
        conn,
    )
    suggestion = prefill.for_operation("load", order_id=sales_order, conn=conn)
    assert suggestion["values"]["from_location_id"] == tank
    assert any("holds the most" in note for note in suggestion["notes"])


def test_prefill_remembers_what_a_trailer_last_hauled(conn, admin_user, sales_order):
    material = _material("05001", conn)
    inventory.post(
        "RECEIVE",
        {
            "plant_id": 1,
            "to_location_id": _location("DM-T105", conn),
            "to_material_id": material,
            "to_qty": 4_000,
        },
        admin_user,
        conn,
    )
    inventory.post(
        "LOAD",
        {
            "order_id": sales_order,
            "from_location_id": _location("DM-T105", conn),
            "from_material_id": material,
            "from_qty": 4_000,
            "to_location_id": _location("DM-TRAILER", conn),
            "to_material_id": material,
            "to_qty": 4_000,
            "trailer_number": "902",
        },
        admin_user,
        conn,
    )
    assert "05001" in prefill.last_material_hauled("902", conn)
    assert prefill.trailer_history("902", conn=conn)[0]["material_number"] == "05001"
    assert prefill.last_material_hauled("no-such-trailer", conn) == ""


def test_qc_prefill_carries_the_bol_from_the_load(conn, admin_user, sales_order):
    material = _material("05001", conn)
    inventory.post(
        "RECEIVE",
        {
            "plant_id": 1,
            "to_location_id": _location("DM-T106", conn),
            "to_material_id": material,
            "to_qty": 3_000,
        },
        admin_user,
        conn,
    )
    load = inventory.post(
        "LOAD",
        {
            "order_id": sales_order,
            "from_location_id": _location("DM-T106", conn),
            "from_material_id": material,
            "from_qty": 3_000,
            "to_location_id": _location("DM-TRAILER", conn),
            "to_material_id": material,
            "to_qty": 3_000,
            "trailer_number": "903",
        },
        admin_user,
        conn,
    )
    suggestion = prefill.for_qc(sales_order, conn)
    assert suggestion["values"]["bol_number"] == load["to_bol"]
    assert suggestion["sample_auto_generate"] is False


# ------------------------------------------------------------------ scan


def test_scan_resolves_an_order_a_material_and_a_location(conn, sales_order):
    order_hit = scan.resolve(str(sales_order), conn=conn)["hits"][0]
    assert order_hit["type"] == "order"
    assert order_hit["route"] == f"orders/{sales_order}"

    assert scan.resolve("05001", conn=conn)["hits"][0]["type"] == "material"
    assert scan.resolve("DM-T101", conn=conn)["hits"][0]["type"] == "location"
    assert scan.resolve("", conn=conn)["hits"] == []
    assert scan.resolve("not-a-code-at-all", conn=conn)["hits"] == []


def test_scan_finds_a_sample_and_routes_to_its_order(conn, admin_user, sales_order):
    saved = qc.save(
        sales_order,
        {"sample_number": "SCANME0001", "moisture": 1.1},
        admin_user,
        conn=conn,
    )
    hit = scan.resolve("scanme0001", conn=conn)["hits"][0]      # case-insensitive
    assert hit["type"] == "sample"
    assert hit["order_id"] == sales_order
    assert str(saved["qc_id"]) in hit["sublabel"]


# ---------------------------------------------------------------- alerts


def test_alert_rules_fire_on_conditions_that_exist(conn):
    found = alerts.evaluate(conn=conn)
    rules = {alert["rule"] for alert in found}
    assert rules, "the seeded data should trip at least one rule"
    assert rules <= {"lims_stale", "out_of_spec", "stale_load", "tank_full", "product_setup"}
    for alert in found:
        assert alert["severity"] in {"info", "warning", "critical"}
        assert alert["fingerprint"]


def test_the_same_condition_is_not_re_sent_inside_the_repeat_window(conn):
    first = alerts.run(send=True, conn=conn)
    assert first["new"], "first run should report the open conditions"
    second = alerts.run(send=True, conn=conn)
    assert second["new"] == []
    assert second["suppressed"] >= len(first["new"])


def test_dry_run_records_nothing(conn):
    before = len(alerts.recent(500, conn))
    result = alerts.run(send=False, conn=conn)
    assert result["dry_run"] is True
    assert len(alerts.recent(500, conn)) == before


def test_digest_summarises_open_findings(conn):
    summary = alerts.digest(conn=conn)
    assert "PIMS daily digest" in summary["subject"]
    assert isinstance(summary["lines"], list)


# ------------------------------------------------------------------ jobs


def test_auto_close_is_conservative(conn, admin_user, sales_order):
    """An order with a staged, unshipped trailer is not finished."""

    material = _material("05001", conn)
    inventory.post(
        "RECEIVE",
        {
            "plant_id": 1,
            "to_location_id": _location("DM-T107", conn),
            "to_material_id": material,
            "to_qty": 6_000,
        },
        admin_user,
        conn,
    )
    inventory.post(
        "LOAD",
        {
            "order_id": sales_order,
            "from_location_id": _location("DM-T107", conn),
            "from_material_id": material,
            "from_qty": 6_000,
            "to_location_id": _location("DM-TRAILER", conn),
            "to_material_id": material,
            "to_qty": 6_000,
            "trailer_number": "904",
        },
        admin_user,
        conn,
    )
    assert sales_order not in [o["order_id"] for o in jobs.closable(conn=conn)]

    stage = inventory.pending_shipments(order_id=sales_order, conn=conn)[0]
    inventory.ship(stage["stage_id"], admin_user, conn)
    qc.save(sales_order, {"moisture": 1.4, "sample_number": "CLOSE-1"}, admin_user, conn=conn)

    assert sales_order in [o["order_id"] for o in jobs.closable(conn=conn)]
    result = jobs.auto_close(dry_run=True, conn=conn)
    assert sales_order in result["would_close"]

    jobs.auto_close(conn=conn)
    assert orders.get(sales_order, conn)["status"] == "Closed"


def test_standing_orders_create_one_order_per_run(conn, admin_user):
    standing = jobs.create_recurring(
        {
            "name": "Weekly cattle blend — Storm Lake",
            "cadence": "weekly",
            "weekday": 0,
            "lead_days": 2,
            "next_run": "2026-08-17",
            "template": {
                "order_type_id": 1,
                "plant_id": 1,
                "company_id": 1,
                "department_id": 2,
                "customer_id": 2,
                "material_one_id": _material("01020", conn),
                "material_one_quantity": 24_000,
            },
        },
        admin_user,
        conn,
    )
    first = jobs.run_recurring(conn=conn)
    assert len(first["created"]) == 1

    # Running again the same day must not create a second order.
    assert jobs.run_recurring(conn=conn)["created"] == []

    refreshed = jobs.get_recurring(standing["recurring_id"], conn)
    assert refreshed["next_run"] > "2026-08-17"
    assert refreshed["last_order_id"] == first["created"][0]["order_id"]


def test_standing_order_rejects_an_incomplete_template(conn, admin_user):
    with pytest.raises(ValidationError):
        jobs.create_recurring(
            {"name": "broken", "cadence": "weekly", "template": {"plant_id": 1}},
            admin_user,
            conn,
        )


def test_every_job_run_is_recorded(conn):
    jobs.frequent(send=False, conn=conn)
    status = jobs.health_summary(conn)
    assert "frequent" in status
    assert status["frequent"]["last_status"] == "ok"


def test_jobs_run_as_a_system_account_that_cannot_sign_in(conn):
    system = jobs.system_user(conn)
    assert system["username"] == "system"
    assert system["password_hash"] == ""
    with pytest.raises(AuthError):
        security.login("system", "", conn)


# ---------------------------------------------------------- integrations


@pytest.mark.parametrize(
    "line,expected",
    [
        ("GROSS 45320 LB  TARE 15100 LB", {"gross_lbs": 45320.0, "tare_lbs": 15100.0, "net_lbs": 30220.0}),
        ("45320 lb G", {"gross_lbs": 45320.0, "tare_lbs": None, "net_lbs": None}),
        ("  38,150", {"gross_lbs": 38150.0, "tare_lbs": None, "net_lbs": None}),
        ("NET 30220 LB", {"gross_lbs": None, "tare_lbs": None, "net_lbs": 30220.0}),
        ("scale offline", {"gross_lbs": None, "tare_lbs": None, "net_lbs": None}),
    ],
)
def test_indicator_lines_are_parsed_both_ways_round(line, expected):
    assert scale_integration.parse_indicator_line(line) == expected


def test_a_scale_reading_is_offered_once_then_consumed(conn, admin_user, sales_order):
    reading = scale_integration.record(
        {"plant_id": 1, "trailer_number": "905", "line": "GROSS 40000 LB TARE 15000 LB"},
        admin_user,
        conn,
    )
    assert reading["net_lbs"] == 25_000
    assert scale_integration.latest(1, "905", conn=conn)["reading_id"] == reading["reading_id"]

    material = _material("05001", conn)
    inventory.post(
        "RECEIVE",
        {
            "plant_id": 1,
            # The receiving bay rather than a tank: this test is about the
            # weight being consumed, and it should not fail on the day the
            # seeded tank happens to be nearly full.
            "to_location_id": _location("DM-RECV-TRUCK", conn),
            "to_material_id": material,
            "to_qty": 25_000,
            "scale_reading_id": reading["reading_id"],
        },
        admin_user,
        conn,
    )
    assert scale_integration.latest(1, "905", conn=conn) is None


def test_a_reading_with_no_weight_is_rejected(conn, admin_user):
    with pytest.raises(ValidationError):
        scale_integration.record({"plant_id": 1, "line": "scale offline"}, admin_user, conn)


def test_lims_sync_only_fills_gaps(conn, admin_user, sales_order):
    from pims.services import lims

    qc.save(
        sales_order,
        {"sample_number": "GAP-SAMPLE-1", "moisture": 1.5, "ffa": 40},
        admin_user,
        conn=conn,
    )
    assert lims.results_for_sample("GAP-SAMPLE-1", conn) == []

    result = lims_ingest.sync(lims_ingest.StubSource(limit=200), since_days=30, conn=conn)
    assert result["written"] > 0
    assert lims.results_for_sample("GAP-SAMPLE-1", conn)

    # A second run has nothing left to do for that sample.
    again = lims_ingest.sync(lims_ingest.StubSource(limit=200), since_days=30, conn=conn)
    assert all(row["sample_code"] != "GAP-SAMPLE-1" for row in again.get("sample", []))


def test_gp_sync_creates_then_leaves_alone(conn):
    payload = {
        "customers": [{"CUSTNMBR": "FEC-9001", "CUSTNAME": "TEST CO-OP", "CITY": "Ames", "STATE": "IA"}],
        "vendors": [{"VENDORID": "V-9001", "VENDNAME": "TEST RENDERING"}],
        "orders": [],
    }
    first = gp_sync.sync(gp_sync.StubSource(payload), conn=conn)
    assert first["counts"]["customers"]["created"] == 1

    second = gp_sync.sync(gp_sync.StubSource(payload), conn=conn)
    assert second["counts"]["customers"]["unchanged"] == 1

    payload["customers"][0]["CITY"] = "Nevada"
    third = gp_sync.sync(gp_sync.StubSource(payload), conn=conn)
    assert third["counts"]["customers"]["updated"] == 1
    assert db.query_one(
        "SELECT city FROM customer WHERE gp_custnmbr = 'FEC-9001'", (), conn
    )["city"] == "Nevada"


def test_gp_sync_dry_run_changes_nothing(conn):
    before = db.scalar("SELECT COUNT(*) FROM customer", (), conn)
    result = gp_sync.sync(gp_sync.StubSource(), dry_run=True, conn=conn)
    assert result["dry_run"] is True
    assert db.scalar("SELECT COUNT(*) FROM customer", (), conn) == before


# ----------------------------------------------------------------- kiosk


def test_pin_sign_in_gives_a_short_session(conn):
    session = security.login_with_pin("toperator", "5588", plant_id=1, conn=conn)
    assert session["user"]["username"] == "toperator"
    assert session["session_minutes"] <= 60
    assert security.user_from_token(session["token"], conn)["username"] == "toperator"


def test_a_wrong_pin_is_refused(conn):
    with pytest.raises(AuthError):
        security.login_with_pin("toperator", "0000", conn=conn)


def test_pin_sign_in_respects_plant_access(conn):
    from pims.errors import PermissionError_

    with pytest.raises(PermissionError_):
        security.login_with_pin("toperator", "5588", plant_id=2, conn=conn)  # DM only


def test_kiosk_list_only_shows_operators_for_that_plant(conn):
    names = {user["username"] for user in security.kiosk_users(4, conn)}   # LV
    assert names == {"cchiodo"}


def test_pins_must_be_digits(conn):
    with pytest.raises(ValidationError):
        security.set_pin("toperator", "abcd", conn)
