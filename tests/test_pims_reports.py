"""The spreadsheets' reports, computed from the ledger: DM Yields, caustic per
blended load, reversal rate, and the legacy export layouts the workbooks read.

Each test writes a small ledger into January 2020 — well clear of the seeded
demo history — inside a transaction that is rolled back afterwards, so the
expected numbers are exact and nothing leaks into the other tests.
"""

from __future__ import annotations

import pytest

from pims import db
from pims.services import reports

DAY = "2020-01-15"
PERIOD = {"plant_id": 1, "start": "2020-01-01", "end": "2020-01-31"}


def _m(number: str, conn) -> int:
    return db.scalar("SELECT material_id FROM material WHERE number = ?", (number,), conn)


def _l(number: str, conn) -> int:
    return db.scalar("SELECT location_id FROM location WHERE number = ?", (number,), conn)


@pytest.fixture()
def ledger(conn, admin_user):
    """One settle, one MGR reprocess, bottoms, an oil load, a cattle blend
    with a reversed caustic posting, and a water trailer — at Des Moines."""

    conn.execute("BEGIN")
    ids: dict[str, int] = {}

    def row(name: str, type_code: str, frm=None, to=None, qty: float = 0.0, **extra) -> int:
        values = {
            "transaction_type_id": db.scalar("SELECT transaction_type_id FROM transaction_type WHERE code = ?",
                                             (type_code,), conn),
            "plant_id": 1, "user_id": admin_user["user_id"],
            "transaction_date": f"{DAY}T10:00:00+00:00", "user_date": DAY, **extra,
        }
        if frm:
            values.update(from_material_id=_m(frm[0], conn), from_location_id=_l(frm[1], conn), from_qty=qty)
        if to:
            values.update(to_material_id=_m(to[0], conn), to_location_id=_l(to[1], conn), to_qty=qty)
        ids[name] = db.insert("inventory_transaction", values, conn)
        return ids[name]

    so = db.insert("order", {
        "order_id": 9_900_001, "order_type_id": 1, "order_date": DAY, "due_date": DAY, "company_id": 1,
        "plant_id": 1, "material_one_id": _m("01021", conn), "material_one_quantity": 45_000, "status_id": 4,
        "date_added": DAY, "added_by": "test", "order_reference": "O01-999999-1",
    }, conn)
    # The settle: 40,000 soap, acid, steam and some process water back in.
    row("receipt", "RECEIVE", to=("00007", "DM-RECV-TRUCK"), qty=40_000)
    row("soap", "PRODUCE", ("00007", "DM-RECV-TRUCK"), ("01006", "DM-2"), 40_000)
    row("acid", "PRODUCE", ("00001", "DM-103"), ("01006", "DM-2"), 2_000)
    row("steam", "PRODUCE", ("00004", "DM-975"), ("01006", "DM-2"), 1_000)
    row("water", "PRODUCE", ("01008", "DM-32"), ("01006", "DM-2"), 4_000)
    row("oil", "PRODUCE", ("01006", "DM-2"), ("01019", "DM-20"), 5_720)          # 55% of 40,000 × 26%
    row("mgr", "PRODUCE", ("01006", "DM-2"), ("01007", "DM-41"), 10_000)
    row("wet", "PRODUCE", ("01006", "DM-2"), ("01008", "DM-32"), 31_280)
    # MGR reprocessed in tank 13.
    row("mgr_in", "PRODUCE", ("01007", "DM-41"), ("01007", "DM-13"), 10_000)
    row("mgr_oil", "PRODUCE", ("01007", "DM-13"), ("01019", "DM-20"), 3_400)
    row("mgr_back", "PRODUCE", ("01007", "DM-13"), ("01007", "DM-42"), 3_000)
    row("mgr_wet", "PRODUCE", ("01007", "DM-13"), ("01008", "DM-32"), 3_600)
    row("bottoms", "PRODUCE", ("01019", "DM-20"), ("01007", "DM-41"), 1_000)
    # Oil out as AV4000, blended on the trailer.
    row("oil_load", "PROD_LOAD", ("01019", "DM-20"), ("05065", "DM-TRAILER"), 6_000)
    # A cattle blend: caustic posted, found wrong, reversed and posted again.
    load = {"order_id": so, "to_bol": "001-999999-1(390)", "trailer_number": "390"}
    row("c_mgr", "PROD_LOAD", ("01007", "DM-42"), None, 24_000, **load,
        to_material_id=_m("01021", conn), to_location_id=_l("DM-TRAILER", conn), to_qty=24_000)
    row("c_water", "PROD_LOAD", ("01008", "DM-32"), None, 18_000, **load,
        to_material_id=_m("01021", conn), to_location_id=_l("DM-TRAILER", conn), to_qty=18_000)
    bad = row("c_bad", "PROD_LOAD", ("00003", "DM-T102"), None, 900, **load,
              to_material_id=_m("01021", conn), to_location_id=_l("DM-TRAILER", conn), to_qty=900)
    row("c_reversal", "PROD_LOAD", ("01021", "DM-TRAILER"), ("00003", "DM-T102"), 900, **load,
        parent_transaction_id=bad, is_reversal=1)
    db.update("inventory_transaction", {"transaction_id": bad}, {"voided": 1}, conn)
    good = row("c_caustic", "PROD_LOAD", ("00003", "DM-T102"), None, 2_000, **load,
               to_material_id=_m("01021", conn), to_location_id=_l("DM-TRAILER", conn), to_qty=2_000)
    db.insert("txn_reading", {"transaction_id": good, "analyte": "ph", "value": 3.1}, conn)
    # A trailer of process water.
    row("w_load", "LOAD", ("01008", "DM-32"), ("01008", "DM-TRAILER"), 10_000)
    row("w_ship", "SHIP", ("01008", "DM-TRAILER"), None, 10_000, parent_transaction_id=ids["w_load"])
    try:
        yield ids
    finally:
        conn.execute("ROLLBACK")


def test_yields_follow_the_dm_workbook(conn, admin_user, ledger):
    got = reports.acid_yields(PERIOD, admin_user, conn)
    assert got["inputs"] == {
        "soap_received": 40_000, "soap_processed": 40_000, "soap_processed_less_water": 36_000,
        "acid_used": 2_000, "acid_pct": 5.0, "steam": 1_000, "reprocessed_water": 4_000,
    }
    assert {p["part"]: p["lbs"] for p in got["settle_break"]} == {"oil": 5_720, "mgr": 10_000, "water": 31_280}
    assert {p["part"]: p["lbs"] for p in got["mgr_break"]} == {"oil": 3_400, "mgr": 3_000, "water": 3_600}
    # Into the MGR tank is processing, not break output.
    assert got["mgr_processed"] == 10_000
    assert got["oil"]["total_20s"] == 9_120 and got["oil"]["bottoms_20s"] == 1_000
    assert got["oil"]["oil_final"] == 6_000
    y = got["yields"]
    assert y["fpy"] == pytest.approx(55.0)                               # 5,720 ÷ (40,000 × 26%)
    assert y["spy"] == pytest.approx(100 - 1_000 / 9_120 * 100, abs=0.01)
    assert y["oy"] == pytest.approx(6_000 / 10_400 * 100, abs=0.01)
    assert y["oy_less_water"] == pytest.approx(6_000 / 9_360 * 100, abs=0.01)
    # The reversed caustic is not counted; what went out on the truck is.
    assert got["outbound"] == {"mgrv": 24_000, "mgra": 0, "water": 18_000, "caustic": 2_000}
    assert got["outbound_water"] == {"lbs": 10_000, "trailers_est": 0.2}
    day = next(d for d in got["daily"] if d["date"] == DAY)
    assert day["soap_processed"] == 40_000 and day["fpy_7d"] == pytest.approx(55.0)


def test_the_tfa_is_a_parameter(conn, admin_user, ledger):
    got = reports.acid_yields({**PERIOD, "tfa": 22}, admin_user, conn)
    assert got["yields"]["fpy"] == pytest.approx(5_720 / (40_000 * 0.22) * 100, abs=0.01)


def test_caustic_per_load_shows_gross_reversed_and_net(conn, admin_user, ledger):
    got = reports.caustic(PERIOD, admin_user, conn)
    load = next(o for o in got["loads"] if o["order_id"] == 9_900_001)
    assert (load["gross"], load["reversed"], load["net"]) == (2_900, 900, 2_000)
    assert load["status"] == "Reversed and reposted"
    assert load["ph_readings"] == [3.1] and load["product"] == "FE Cattle Blend - 3.5"
    dm = next(p for p in got["by_plant"] if p["plant"] == "DM")
    assert dm["reversal_pct"] == pytest.approx(900 / 2_900 * 100, abs=0.01)
    assert dm["avg_ph"] == 3.1 and dm["not_tested"] == 0


def test_a_zero_ph_is_not_tested(conn, admin_user, ledger):
    db.execute("UPDATE txn_reading SET value = 0 WHERE transaction_id = ?", (ledger["c_caustic"],), conn)
    got = reports.caustic(PERIOD, admin_user, conn)
    dm = next(p for p in got["by_plant"] if p["plant"] == "DM")
    assert dm["avg_ph"] is None and dm["not_tested"] == 1


def test_reversal_rate_by_type(conn, admin_user, ledger):
    got = reports.reversals(PERIOD, admin_user, conn)
    prod_load = next(t for t in got["by_type"] if t["plant"] == "DM" and t["type"] == "PROD-LOAD")
    assert (prod_load["postings"], prod_load["reversed"]) == (5, 1)
    assert prod_load["rate_pct"] == 20.0 and prod_load["lbs_reversed"] == 900
    assert got["top_materials"][0]["material"] == "Caustic"


def test_export_in_the_report_layout(conn, admin_user, ledger):
    got = reports.export({**PERIOD, "layout": "report"}, admin_user, conn)
    assert got["columns"][:5] == ["Trans Id", "Parent", "Trans Date", "User Date", "Type"]
    by_id = {r["Trans Id"]: r for r in got["rows"]}
    original = by_id[ledger["c_bad"]]
    assert (original["Type"], original["From Mat"], original["From Qty"], original["To Qty"]) == ("PROD-LOAD", 3, -900, 900)
    # The reversal as the legacy system wrote it: the parent's row, signs turned round.
    reversal = by_id[ledger["c_reversal"]]
    assert reversal["Type"] == "REVERSAL" and reversal["Parent"] == ledger["c_bad"]
    assert (reversal["From Mat"], reversal["From Qty"], reversal["To Mat"], reversal["To Qty"]) == (3, 900, 1021, -900)
    assert reversal["From Loc"] == "T102" and original["Comments"] == "Trailer: 390"
    assert by_id[ledger["oil"]]["From Loc"] == 2 and by_id[ledger["oil"]]["To Loc"] == 20


def test_export_in_the_query_layouts(conn, admin_user, ledger):
    query = reports.export({**PERIOD, "layout": "query"}, admin_user, conn)
    assert len(query["columns"]) == 30
    rev = next(r for r in query["rows"] if r["Full_TransType_Name"] == "PROD-LOAD - REVERSAL")
    assert rev["From_Qty"] == 900 and rev["Order_Reference"] == "O01-999999-1"
    good = next(r for r in query["rows"] if r["From_Material_Number"] == 3 and r["From_Qty"] == -2_000)
    assert good["To_QC_Ph"] == 3.1
    yields = reports.export({**PERIOD, "layout": "yields"}, admin_user, conn)
    assert len(yields["columns"]) == 28
    assert yields["rows"][0]["Transaction_Date"] == DAY               # a date, as the Yields sheets compare it
    receipt = next(r for r in yields["rows"] if r["Full_TransType_Name"] == "RECEIVED")
    assert (receipt["To_Material_Number"], receipt["To_Qty"], receipt["From_Qty"]) == (7, 40_000, 0)
    csv = reports.to_csv(yields)
    assert csv.splitlines()[0].startswith("Transaction_Date,From_Location_Plant_Code")


def test_material_roles_can_be_overridden(conn, admin_user, ledger):
    db.execute(
        "INSERT OR REPLACE INTO system_setting (key, value) VALUES ('reports.materials', ?)",
        ('{"soap": [7, 10, 6, 1234]}',), conn,
    )
    assert 1234 in reports.materials(conn)["soap"]
    assert reports.materials(conn)["acid"] == {1}


def test_bad_periods_are_refused(conn, admin_user):
    from pims.errors import ValidationError

    with pytest.raises(ValidationError):
        reports.acid_yields({"start": "2020-02-01", "end": "2020-01-01"}, admin_user, conn)
    with pytest.raises(ValidationError):
        reports.export({"layout": "nope"}, admin_user, conn)


def test_reports_over_the_api(client, auth):
    got = client.post("/api/reports/acid-yields", json={"plant_id": 1}, headers=auth)
    assert got.status_code == 200, got.text
    assert got.json()["yields"]["fpy"] is not None                     # the seeded six weeks
    csv = client.post("/api/reports/caustic/csv", json={}, headers=auth)
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert "caustic_" in csv.headers["content-disposition"]
    assert client.post("/api/reports/nope", json={}, headers=auth).status_code == 422


def test_the_companion_runs_reports(pims_app):
    from pims import app as pims_app
    assert any(p.match("/api/reports/acid-yields") for p in pims_app.COMPANION_ALLOWED_WRITES)
    assert any(p.match("/api/reports/export/csv") for p in pims_app.COMPANION_ALLOWED_WRITES)


def _type_id(code: str, conn) -> int:
    return db.scalar("SELECT transaction_type_id FROM transaction_type WHERE code = ?", (code,), conn)


def test_a_legacy_ship_correction_is_not_more_shipped(conn, admin_user, ledger):
    """The legacy PIMS corrects a SHIP-LEAVE with a SHIPADJ and a second
    SHIP-LEAVE under it, equal and opposite. Neither is more product out."""

    from pims.services import orders

    for name in ("w_load", "w_ship"):
        db.update("inventory_transaction", {"transaction_id": ledger[name]}, {"order_id": 9_900_001}, conn)
    base = {"plant_id": 1, "user_id": admin_user["user_id"], "order_id": 9_900_001,
            "transaction_date": f"{DAY}T12:00:00+00:00", "user_date": DAY,
            "parent_transaction_id": ledger["w_ship"], "from_material_id": _m("01008", conn),
            "from_location_id": _l("DM-TRAILER", conn)}
    db.insert("inventory_transaction", {**base, "transaction_type_id": _type_id("SHIP", conn), "from_qty": 2_998}, conn)
    db.insert("inventory_transaction", {**base, "transaction_type_id": _type_id("ADJUST", conn), "from_qty": -2_998}, conn)
    assert orders.progress(9_900_001, 1, conn)["qty_shipped"] == 10_000
    assert reports.acid_yields(PERIOD, admin_user, conn)["outbound_water"]["lbs"] == 10_000


def test_mgr_tanks_are_found_by_what_they_do(conn, admin_user, ledger):
    """Plants name their MGR tanks differently; one not typed MGR is still
    found as the tank MGR is broken into oil from."""

    db.execute("UPDATE location SET location_type_id = 1 WHERE number = 'DM-13'", (), conn)
    got = reports.acid_yields(PERIOD, admin_user, conn)
    assert got["mgr_processed"] == 10_000
    assert {p["part"]: p["lbs"] for p in got["mgr_break"]} == {"oil": 3_400, "mgr": 3_000, "water": 3_600}


def test_pleasant_hill_oil_and_rain_water_count(conn, admin_user):
    roles = reports.materials(conn)
    assert 1017 in roles["oil"] and 15 in roles["water_in"]
