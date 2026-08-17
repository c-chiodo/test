"""LIMS projection and the matrix feature.

These are the regression gates for FE-2026-001 — the "Add Matrix Results"
failure caused by reading a retired LIMS database and dropping the
IncludeInReport filter on the new-LIMS code path.
"""

from __future__ import annotations

import pytest

from pims import db
from pims.config import Settings, get_settings, reset_settings
from pims.services import lims


@pytest.fixture()
def sample(conn):
    """One sample carrying exactly the component mix from the fix write-up."""

    code = "SCTEST0001D260817Q0000001"
    db.execute("DELETE FROM lims_result WHERE sample_code = ?", (code,), conn)
    rows = [
        # (test, component, value, include_in_report, current_version)
        ("FFA (NIR)", "R-FFA", 56.19, 1, 1),
        ("FFA (NIR)", "%FFA / Comments", None, 0, 1),      # non-reportable
        ("FFA (NIR)", "DATE-FFA", 21, 1, 1),               # date field
        ("TFA (NIR)", "%TFA 1", 21.5, 1, 1),
        ("TFA (NIR)", "%TFA / 1 / Comments", None, 0, 1),  # non-reportable
        ("FFA (NIR)", "%FFA1", 55.0, 1, 0),                # superseded version
    ]
    for test, component, value, include, current in rows:
        db.insert(
            "lims_result",
            {
                "sample_code": code,
                "test_code": test,
                "component": component,
                "value_text": None if value is None else str(value),
                "value_num": value,
                "include_in_report": include,
                "current_version": current,
                "sampled_at": "2026-08-17T10:00:00+00:00",
                "source": "XLIMSFEEDGROUP",
                "retrieved_at": "2026-08-17T11:00:00+00:00",
            },
            conn,
        )
    return code


def test_only_reportable_current_components_are_returned(conn, sample):
    """FE-2026-001 defects 1 and 2: no duplicates, no comment columns."""

    components = {r["component"] for r in lims.results_for_sample(sample, conn)}
    assert components == {"R-FFA", "%TFA 1"}
    assert "%FFA1" not in components            # superseded version
    assert "DATE-FFA" not in components         # date field
    assert not any("Comments" in c for c in components)


def test_matrix_returns_one_clean_column_per_test(conn, sample):
    result = lims.matrix([sample], conn=conn)
    assert result["columns"] == ["FFA (NIR) - R-FFA", "TFA (NIR) - %TFA 1"]
    assert result["rows"][0]["FFA (NIR) - R-FFA"] == 56.19
    assert result["misses"] == []


def test_a_blank_matrix_cell_is_always_explained(conn, sample):
    """The legacy code swallowed cell errors; a blank was indistinguishable
    from 'not tested'. Every empty cell now has a matching miss."""

    result = lims.matrix(
        [sample, "SAMPLE-THAT-WAS-NEVER-LOGGED"],
        selections=[{"test_code": "FFA (NIR)", "component": "R-FFA"}],
        conn=conn,
    )
    missing_row = next(r for r in result["rows"] if r["sample_code"] != sample)
    assert missing_row["FFA (NIR) - R-FFA"] is None
    assert result["misses"] == [
        {
            "sample_code": "SAMPLE-THAT-WAS-NEVER-LOGGED",
            "column": "FFA (NIR) - R-FFA",
            "reason": "no result recorded",
        }
    ]


def test_test_code_list_excludes_stale_and_non_reportable_entries(conn, sample):
    codes = {row["test_code"] for row in lims.test_codes(conn)}
    assert "FFA (NIR)" in codes
    components = lims.component_names("FFA (NIR)", conn)
    assert [c["component"] for c in components] == ["R-FFA"]


def test_freshness_reports_ok_when_the_projection_is_current(conn):
    report = lims.freshness(conn)
    assert report["status"] in {"ok", "degraded"}
    assert report["results"] > 0
    assert report["expected_source"] == "XLIMSFEEDGROUP"


def test_stale_projection_fails_the_check(conn):
    """A seven-month-old feed must not read as healthy.

    This is the check whose absence let PIMS serve a retired LIMS from
    March 2026 until it was noticed in July.
    """

    db.execute(
        "UPDATE lims_result SET retrieved_at = '2026-01-05T00:00:00+00:00'", (), conn
    )
    try:
        report = lims.freshness(conn)
        assert report["status"] == "failed"
        assert report["age_hours"] > 72
        assert "stale" in report["detail"].lower()
    finally:
        db.execute(
            "UPDATE lims_result SET retrieved_at = datetime('now')", (), conn
        )


def test_results_from_two_sources_are_reported_as_degraded(conn):
    db.insert(
        "lims_result",
        {
            "sample_code": "LEGACY-1",
            "test_code": "FFA",
            "component": "%FFA 1",
            "value_num": 12.0,
            "include_in_report": 1,
            "current_version": 1,
            "source": "MatrixFeedEnergy",
            "retrieved_at": "2026-08-17T11:00:00+00:00",
        },
        conn,
    )
    try:
        report = lims.freshness(conn)
        assert set(report["sources"]) == {"XLIMSFEEDGROUP", "MatrixFeedEnergy"}
        assert report["status"] == "degraded"
        assert "2 sources" in report["detail"]
    finally:
        db.execute("DELETE FROM lims_result WHERE sample_code = 'LEGACY-1'", (), conn)


def test_ingest_stamps_source_and_retrieval_time(conn):
    result = lims.ingest(
        [
            {
                "sample_code": "INGEST-1",
                "test_code": "MOISTURE",
                "component": "%MOIST",
                "value": 1.4,
                "sampled_at": "2026-08-17T09:00:00+00:00",
            }
        ],
        source="XLIMSFEEDGROUP",
        conn=conn,
    )
    assert result["written"] == 1
    row = db.query_one(
        "SELECT * FROM lims_result WHERE sample_code = 'INGEST-1'", (), conn
    )
    assert row["source"] == "XLIMSFEEDGROUP"
    assert row["retrieved_at"]
    assert row["value_num"] == 1.4


def test_ingest_rejects_incomplete_rows(conn):
    from pims.errors import IntegrationError

    with pytest.raises(IntegrationError):
        lims.ingest([{"sample_code": "X", "test_code": "", "component": "c"}], conn=conn)
