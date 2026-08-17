"""LabWare LIMS integration — the "matrix" feature, rebuilt.

Three things went wrong in the legacy implementation, and each is addressed
here by design rather than by a stored-procedure patch (FE-2026-001):

1. **It read the wrong database.** The test list was built from a frozen copy
   of the retired LIMS, so it showed 98 stale test codes instead of the 53
   current ones, and samples logged after the cutover returned nothing. Here
   every projected row records the ``source`` it came from and when it was
   ``retrieved_at``; :func:`freshness` reports that, and the support console
   shows it. A silently stale feed is not possible to have for seven months
   without someone seeing it.

2. **It showed non-reportable and superseded components.** ``IncludeInReport``
   was filtered out of the new-LIMS code path, producing duplicate columns like
   "%FFA 1" beside "%FFA1". :data:`REPORTABLE_CLAUSE` applies the filter in one
   place, and ``DATE-`` components stay excluded.

3. **It hid its own failures.** Writing each result cell was wrapped in a
   try/except that swallowed errors, so a column-name mismatch produced a blank
   cell and no warning — indistinguishable from "this sample wasn't tested".
   :func:`matrix` returns an explicit ``misses`` list instead.
"""

from __future__ import annotations

from typing import Any

from .. import db
from ..config import get_settings
from ..errors import IntegrationError
from ..util import hours_since, utc_now_iso

#: Applied everywhere a result is read. Superseded versions and non-reportable
#: components never reach the UI.
REPORTABLE_CLAUSE = (
    " include_in_report = 1 AND current_version = 1 AND component NOT LIKE 'DATE-%' "
)


def test_codes(conn=None) -> list[dict]:
    """Distinct current test codes — the Test Selection list."""

    return db.query(
        f"""
        SELECT test_code, COUNT(DISTINCT component) AS components,
               COUNT(*) AS results, MAX(sampled_at) AS newest_sample
        FROM lims_result
        WHERE {REPORTABLE_CLAUSE}
        GROUP BY test_code
        ORDER BY test_code
        """,
        (),
        conn,
    )


def component_names(test_code: str | None = None, conn=None) -> list[dict]:
    sql = f"SELECT DISTINCT test_code, component FROM lims_result WHERE {REPORTABLE_CLAUSE}"
    params: list[Any] = []
    if test_code and test_code != "%":
        sql += " AND test_code = ?"
        params.append(test_code)
    return db.query(sql + " ORDER BY test_code, component", params, conn)


def results_for_sample(sample_code: str, conn=None) -> list[dict]:
    """Every current, reportable result for one sample."""

    return db.query(
        f"""
        SELECT sample_code, test_code, component, value_text, value_num,
               sampled_at, source, retrieved_at
        FROM lims_result
        WHERE sample_code = ? AND {REPORTABLE_CLAUSE}
        ORDER BY test_code, component
        """,
        (sample_code,),
        conn,
    )


def matrix(
    sample_codes: list[str],
    selections: list[dict] | None = None,
    conn=None,
) -> dict[str, Any]:
    """Append LIMS results to a set of samples as extra columns.

    ``selections`` is a list of ``{"test_code": ..., "component": ...}``;
    omit it to include every current test/component present for the samples.

    Returns ``columns``, one ``rows`` entry per requested sample, and ``misses``
    — the (sample, column) pairs with no result. A blank cell in the UI is
    always explained by an entry here, never by a swallowed exception.
    """

    sample_codes = [s for s in dict.fromkeys(sample_codes) if s]
    if not sample_codes:
        return {"columns": [], "rows": [], "misses": [], "source": _source_label(conn)}

    marks = ", ".join("?" for _ in sample_codes)
    rows = db.query(
        f"""
        SELECT sample_code, test_code, component, value_text, value_num, source
        FROM lims_result
        WHERE sample_code IN ({marks}) AND {REPORTABLE_CLAUSE}
        """,
        sample_codes,
        conn,
    )

    wanted: list[tuple[str, str]] | None = None
    if selections:
        wanted = [
            (s["test_code"], s["component"])
            for s in selections
            if s.get("test_code") and s.get("component")
        ]

    available = sorted({(r["test_code"], r["component"]) for r in rows})
    columns = wanted if wanted is not None else available
    column_keys = [f"{test} - {component}" for test, component in columns]

    indexed = {
        (r["sample_code"], r["test_code"], r["component"]): r for r in rows
    }
    out_rows: list[dict] = []
    misses: list[dict] = []
    for sample in sample_codes:
        row: dict[str, Any] = {"sample_code": sample}
        for (test, component), key in zip(columns, column_keys):
            hit = indexed.get((sample, test, component))
            if hit is None:
                row[key] = None
                misses.append(
                    {"sample_code": sample, "column": key, "reason": "no result recorded"}
                )
            else:
                row[key] = hit["value_num"] if hit["value_num"] is not None else hit["value_text"]
        out_rows.append(row)

    return {
        "columns": column_keys,
        "rows": out_rows,
        "misses": misses,
        "source": _source_label(conn),
        "requested_samples": len(sample_codes),
    }


def _source_label(conn=None) -> str:
    row = db.query_one(
        "SELECT source FROM lims_result ORDER BY lims_result_id DESC LIMIT 1", (), conn
    )
    return row["source"] if row else get_settings().lims_source_label


def freshness(conn=None) -> dict[str, Any]:
    """How current the LIMS projection is — the check nobody had in 2026.

    Returns a status of ok / degraded / failed against the thresholds in
    settings, plus the numbers a support engineer needs to act.
    """

    settings = get_settings()
    row = db.query_one(
        """
        SELECT COUNT(*) AS results,
               COUNT(DISTINCT sample_code) AS samples,
               COUNT(DISTINCT test_code) AS test_codes,
               MAX(retrieved_at) AS last_retrieved,
               MAX(sampled_at) AS newest_sample
        FROM lims_result
        """,
        (),
        conn,
    ) or {}
    sources = [
        r["source"]
        for r in db.query("SELECT DISTINCT source FROM lims_result", (), conn)
    ]
    age = hours_since(row.get("last_retrieved"))
    if row.get("results", 0) == 0:
        status, detail = "failed", "No LIMS results have ever been projected."
    elif age is None:
        status, detail = "degraded", "Projection rows carry no retrieval timestamp."
    elif age > settings.lims_fail_hours:
        status = "failed"
        detail = (
            f"Last refresh was {age:.0f} h ago "
            f"(fails past {settings.lims_fail_hours} h) — PIMS is showing stale lab data."
        )
    elif age > settings.lims_warn_hours:
        status = "degraded"
        detail = f"Last refresh was {age:.0f} h ago (warns past {settings.lims_warn_hours} h)."
    else:
        status, detail = "ok", f"Last refresh {age:.1f} h ago."

    if len(sources) > 1:
        status = "degraded" if status == "ok" else status
        detail += f" Results present from {len(sources)} sources: {', '.join(sources)}."

    return {
        "status": status,
        "detail": detail,
        "mode": settings.lims_mode,
        "sources": sources,
        "expected_source": settings.lims_source_label,
        "results": row.get("results", 0),
        "samples": row.get("samples", 0),
        "test_codes": row.get("test_codes", 0),
        "last_retrieved": row.get("last_retrieved"),
        "newest_sample": row.get("newest_sample"),
        "age_hours": round(age, 2) if age is not None else None,
        "warn_after_hours": settings.lims_warn_hours,
        "fail_after_hours": settings.lims_fail_hours,
    }


def ingest(rows: list[dict], source: str | None = None, conn=None) -> dict[str, Any]:
    """Load results into the projection.

    This is the seam an ETL job (or a live linked-server reader) writes
    through. Each row needs sample_code, test_code, component and a value;
    include_in_report / current_version default to true so a feed that does not
    supply them still cannot resurrect superseded versions silently — the
    caller must set them false deliberately.
    """

    settings = get_settings()
    source = source or settings.lims_source_label
    retrieved = utc_now_iso()
    written = 0
    with db.transaction(conn):
        for row in rows:
            sample = (row.get("sample_code") or "").strip()
            test = (row.get("test_code") or "").strip()
            component = (row.get("component") or "").strip()
            if not (sample and test and component):
                raise IntegrationError(
                    "LIMS row is missing sample_code / test_code / component.",
                    row=row,
                )
            value = row.get("value")
            db.insert(
                "lims_result",
                {
                    "sample_code": sample,
                    "test_code": test,
                    "component": component,
                    "value_text": None if value is None else str(value),
                    "value_num": (
                        float(value)
                        if isinstance(value, (int, float))
                        else _maybe_float(value)
                    ),
                    "include_in_report": 1 if row.get("include_in_report", True) else 0,
                    "current_version": 1 if row.get("current_version", True) else 0,
                    "sampled_at": row.get("sampled_at"),
                    "source": row.get("source") or source,
                    "retrieved_at": retrieved,
                },
                conn,
            )
            written += 1
    return {"written": written, "source": source, "retrieved_at": retrieved}


def _maybe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
