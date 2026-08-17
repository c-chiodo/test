"""Health checks, data-quality probes and the demo dataset's own consistency."""

from __future__ import annotations

from pims import db, health, observability
from pims.services import inventory, orders


def test_liveness_is_cheap_and_complete():
    body = health.liveness()
    assert body["status"] == "ok"
    assert body["service"] == "pims"
    assert body["version"]


def test_diagnostics_aggregates_to_the_worst_check(conn):
    report = health.diagnostics(conn)
    statuses = {check["status"] for check in report["checks"].values()}
    expected = "failed" if "failed" in statuses else "degraded" if "degraded" in statuses else "ok"
    assert report["status"] == expected


def test_error_check_reacts_to_recorded_failures():
    observability.reset()
    assert health.check_errors()["status"] == "ok"
    for _ in range(3):
        observability.record_request(
            method="GET", path="/api/orders", status=200, duration_ms=12, correlation_id="x"
        )
    observability.record_request(
        method="GET", path="/api/orders", status=500, duration_ms=9, correlation_id="y"
    )
    assert health.check_errors()["status"] == "failed"   # 25% of requests
    observability.reset()


def test_seeded_ledger_is_internally_consistent(conn):
    """The demo data must obey the rules the app enforces.

    A seed that overdraws tanks would make every data-quality finding noise,
    which is how a support screen stops being read.
    """

    balances = inventory.location_balance(include_zero=False, conn=conn)
    assert balances, "the demo dataset should have inventory"
    assert [b for b in balances if b["balance"] < -0.01] == []
    over = [
        b for b in balances
        if b["max_capacity"] and b["balance"] > b["max_capacity"] + 0.01
    ]
    assert over == []


def test_data_quality_findings_carry_their_rows_and_an_action(conn):
    report = health.data_quality(conn=conn)
    assert report["total_findings"] == sum(f["count"] for f in report["findings"])
    for finding in report["findings"]:
        assert finding["action"]
        assert finding["severity"] in {"high", "medium", "low"}
        assert len(finding["rows"]) <= min(finding["count"], 25)


def test_unmatched_sample_probe_finds_qc_without_a_lims_result(conn, admin_user):
    """The probe that answers 'was this sample ever logged in LabWare?'"""

    from pims.services import qc

    created = orders.create(
        {
            "order_type_id": 3,
            "plant_id": 1,
            "company_id": 1,
            "department_id": 1,
            "order_date": "2026-08-17",
            "due_date": "2026-08-18",
            "vendor_id": 1,
            "material_one_id": db.scalar(
                "SELECT material_id FROM material WHERE number = '05001'", (), conn
            ),
            "material_one_quantity": 1_000,
        },
        admin_user,
        conn,
    )
    order_id = created[0]["order_id"]
    qc.save(
        order_id,
        {
            "sample_number": "NEVER-LOGGED-IN-LABWARE",
            "moisture": 1.1, "temp": 120, "spintest_fallout": 0.3,
            "ffa": 40, "tfa": 95,
        },
        admin_user,
        acknowledge_warnings=True,
        conn=conn,
    )
    report = health.data_quality(plant_id=1, conn=conn)
    finding = next(f for f in report["findings"] if f["key"] == "unmatched_samples")
    assert any(row["sample_number"] == "NEVER-LOGGED-IN-LABWARE" for row in finding["rows"])


def test_product_setup_check_reports_the_flagged_limits(conn):
    check = health.check_specs(conn)
    assert check["needs_review"] >= 2          # HC3900 and Live Plus FFA
    assert check["status"] in {"ok", "degraded"}


def test_cli_diagnose_exits_zero_unless_a_check_failed(capsys):
    from pims.__main__ import main

    code = main(["diagnose"])
    captured = capsys.readouterr()
    assert '"service": "pims"' in captured.out
    assert code in (0, 1)
