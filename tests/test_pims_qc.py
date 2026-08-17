"""QC validation and the product-spec engine.

The first two tests are the regression gate for FE-2026-002 — the reported
defect where every Purchase Order sample was required to have a moisture,
temperature and spintest value, including products that never run those tests.
"""

from __future__ import annotations

import pytest

from pims import db
from pims.services import orders, qc, specs


def _material_id(number: str, conn) -> int:
    return db.scalar("SELECT material_id FROM material WHERE number = ?", (number,), conn)


def _make_order(conn, admin_user, *, material_number: str, order_type_id: int) -> int:
    """A fresh order at Des Moines for the given product."""

    payload = {
        "order_type_id": order_type_id,
        "plant_id": 1,
        "company_id": 1,
        "department_id": 1,
        "due_date": "2026-08-20",
        "order_date": "2026-08-17",
        "material_one_id": _material_id(material_number, conn),
        "material_one_quantity": 40_000,
    }
    if order_type_id == 1:
        payload["customer_id"] = 1
    if order_type_id == 3:
        payload["vendor_id"] = 1
    created = orders.create(payload, admin_user, conn)
    return created[0]["order_id"]


@pytest.mark.parametrize("material_number", ["02001", "00010", "01020"])
def test_purchase_order_does_not_demand_tests_the_product_never_runs(
    conn, admin_user, material_number
):
    """FE-2026-002: soaps, water and cattle POs must not warn on moisture/temp/spintest.

    The legacy rule warned for all three on every PO regardless of product.
    """

    order_id = _make_order(conn, admin_user, material_number=material_number, order_type_id=3)
    result = qc.validate(order_id, {"sample_number": "SC1234D260817Q0001", "ffa": 5.0}, conn)

    flagged = {w["analyte"] for w in result["warnings"]}
    assert "temp" not in flagged
    assert "spintest" not in flagged
    if material_number in {"02001", "00010"}:
        assert "moisture" not in flagged


def test_oil_product_still_warns_when_a_required_test_is_missing(conn, admin_user):
    """The check that mattered is kept: an oil product does run all three."""

    order_id = _make_order(conn, admin_user, material_number="05001", order_type_id=3)
    result = qc.validate(order_id, {"sample_number": "DM1D260817Q1"}, conn)

    flagged = {w["analyte"] for w in result["warnings"]}
    assert {"moisture", "temp", "spintest"} <= flagged
    # The message names the product, so the operator can tell whether it applies.
    moisture_warning = next(w for w in result["warnings"] if w["analyte"] == "moisture")
    assert "05001" in moisture_warning["message"]


def test_required_tests_come_from_the_material_not_the_order_type(conn):
    cattle = _material_id("01020", conn)
    oil = _material_id("05001", conn)

    assert set(specs.required_tests(cattle, conn)) == {"moisture", "ph", "tfa"}
    assert "spintest" in specs.required_tests(oil, conn)
    assert "spintest" not in specs.required_tests(cattle, conn)


def test_spec_evaluation_flags_out_of_spec_values(conn):
    """AV4000 allows FFA up to 75; 80 is out, 60 is in."""

    material_id = _material_id("05001", conn)

    high = specs.evaluate(material_id, {"ffa": 80.0}, conn)
    assert next(e for e in high if e["analyte"] == "ffa")["verdict"] == "out_of_spec"

    ok = specs.evaluate(material_id, {"ffa": 60.0}, conn)
    assert next(e for e in ok if e["analyte"] == "ffa")["verdict"] == "in_spec"


def test_spec_limits_match_the_published_limit_sheet(conn):
    """Spot-check the transcription against PIMS_LIMS_Material_Limits.xlsx."""

    expectations = {
        "05003": ("ffa", None, 55.0),      # HC3800
        "05005": ("ffa", None, 30.0),      # Live Tallow
        "05691": ("moisture", 0.0, 1.0),   # EZ Veg 3800
        "01031": ("tfa", 16.0, 18.0),      # MGR
        "01020": ("ph", 2.0, 4.0),         # Cattle Blend
    }
    for number, (analyte, low, high) in expectations.items():
        material_id = _material_id(number, conn)
        spec = specs.specs_for(material_id, conn)[analyte]
        assert spec["min_value"] == low, number
        assert spec["max_value"] == high, number


def test_contradictory_limits_are_surfaced_not_silently_chosen(conn):
    """HC3900 and Live Plus FFA disagree between QC sheet and label."""

    review = specs.review_queue(conn)
    numbers = {row["number"] for row in review}
    assert {"05004", "05006"} <= numbers
    assert all("confirm" in row["note"].lower() for row in review)


def test_saving_qc_records_acknowledged_warnings(conn, admin_user):
    order_id = _make_order(conn, admin_user, material_number="05001", order_type_id=3)
    saved = qc.save(
        order_id,
        {"sample_number": "DM2D260817Q2", "ffa": 12.0},
        admin_user,
        acknowledge_warnings=True,
        conn=conn,
    )
    assert saved["warnings"], "an oil product with no moisture should warn"

    from pims import audit

    entries = audit.for_entity("qc", saved["qc_id"], conn=conn)
    assert entries[0]["detail"]["acknowledged_warnings"] is True


def test_impossible_values_are_rejected_outright(conn, admin_user):
    from pims.errors import ValidationError

    order_id = _make_order(conn, admin_user, material_number="05001", order_type_id=3)
    with pytest.raises(ValidationError) as excinfo:
        qc.save(order_id, {"ph": 47, "moisture": -2}, admin_user, conn=conn)
    fields = excinfo.value.detail["fields"]
    assert "ph" in fields and "moisture" in fields
