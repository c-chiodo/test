"""LabWare LIMS → the local result projection.

The legacy matrix feature read LIMS live, once per result row, across a linked
server — the fix write-up notes a 509-row screen meant 509 round trips. This
does the opposite: a scheduled job pulls current, reportable rows in one query
and writes them locally, and the freshness check tells you when it stops.

Adapters
--------
``StubSource``       generates results for QC samples that have none, so the
                     pipeline can be run and tested without LabWare.
``SqlServerSource``  the real one: reads ``[PHLIMSSQL].[XLIMSFEEDGROUP]``
                     through pyodbc with the filters that FE-2026-001 was
                     caused by omitting.
"""

from __future__ import annotations

from datetime import date, timedelta

import random
from typing import Any, Iterable, Protocol

from .. import db
from ..config import get_settings
from ..errors import IntegrationError
from ..services import jobs, lims
from ..util import utc_now_iso

#: The query the real adapter runs. Kept here as the single statement of what
#: "a result worth showing" means, and mirrored in docs/PIMS_MIGRATION.md.
#: The live LabWare query. Table and column names, joins and filters are the
#: ones the legacy matrix procedures themselves use (PIMS_Matrix_Fix.sql):
#: samples, tests and results joined on SampleCode + AuditFlag (+ TestPosition
#: for results), current versions only (AuditFlag = 0), reportable components
#: only (IncludeInReport = 1 — the filter whose absence caused FE-2026-001),
#: tests at a reportable status (TestStatus <= 40), no DATE- components.
#: LabWare stores dates as integers (YYYYMMDD), so the cutoff is passed as one.
SOURCE_QUERY = """
SELECT s.SampleCode, st.TestCode, sr.ComponentName, sr.ComponentValue, s.RegisterDate
FROM   {db}.dbo.Samples       s
JOIN   {db}.dbo.SampleTests   st ON st.SampleCode = s.SampleCode
                                AND st.AuditFlag  = s.AuditFlag
JOIN   {db}.dbo.SampleResults sr ON sr.SampleCode   = st.SampleCode
                                AND sr.AuditFlag    = st.AuditFlag
                                AND sr.TestPosition = st.TestPosition
WHERE  s.AuditFlag = 0
  AND  sr.IncludeInReport = 1
  AND  st.TestStatus <= 40
  AND  sr.ComponentName NOT LIKE '%DATE-%'
  AND  s.RegisterDate >= ?
"""


class Source(Protocol):
    """Anything that can hand over LIMS rows."""

    name: str

    def fetch(self, since_days: int) -> Iterable[dict[str, Any]]:
        ...


class StubSource:
    """Results for samples PIMS has recorded but LIMS has not answered for.

    Deliberately only fills gaps: it never invents a sample the lab has not
    seen, so running it makes the "QC sample numbers with no LIMS result"
    finding resolvable rather than papering over it.
    """

    name = "stub"

    def __init__(self, seed: int = 20260817, limit: int = 50) -> None:
        self.rng = random.Random(seed)
        self.limit = limit

    #: QC field -> the LIMS test and component it comes back as.
    COMPONENTS = {
        "moisture": ("MOISTURE", "%MOIST"),
        "ffa": ("FFA (NIR)", "R-FFA"),
        "tfa": ("TFA (NIR)", "%TFA 1"),
        "ph": ("PH", "PH"),
    }

    def fetch(self, since_days: int) -> list[dict[str, Any]]:
        gaps = db.query(
            """
            SELECT q.sample_number, q.test_date, q.moisture, q.ffa, q.tfa, q.ph
            FROM qc q
            WHERE q.active = 1
              AND TRIM(COALESCE(q.sample_number, '')) <> ''
              AND q.test_date >= date('now', ?)
              AND NOT EXISTS (
                    SELECT 1 FROM lims_result lr WHERE lr.sample_code = q.sample_number)
            ORDER BY q.test_date DESC
            LIMIT ?
            """,
            (f"-{int(since_days)} days", self.limit),
        )
        rows: list[dict[str, Any]] = []
        for gap in gaps:
            # Only the analytes this sample actually carries a result for — the
            # lab has nothing to report for a test that was never run.
            for field, (test_code, component) in self.COMPONENTS.items():
                recorded = gap[field]
                if recorded is None:
                    continue
                # The lab's number, not the operator's: close but not identical.
                value = round(float(recorded) * self.rng.uniform(0.985, 1.015), 2)
                rows.append(
                    {
                        "sample_code": gap["sample_number"],
                        "test_code": test_code,
                        "component": component,
                        "value": value,
                        "sampled_at": gap["test_date"],
                    }
                )
        return rows


class SqlServerSource:
    """The live LIMS, read through the same read-only door as the legacy mirror.

    Connect straight to the LIMS server (``PHLIMSSQL``) where you can: reading
    ``PHLIMSSQL.XLIMSFEEDGROUP`` *through* the PIMS server makes that server
    run the distributed query on your behalf, which is load on production
    PIMS that a direct connection avoids entirely.
    """

    name = "sqlserver"

    def __init__(self, dsn: str, database: str = "XLIMSFEEDGROUP") -> None:
        self.dsn = dsn
        self.database = database

    def fetch(self, since_days: int) -> list[dict[str, Any]]:
        from ..legacy.readonly import connect_mssql

        cutoff = int((date.today() - timedelta(days=since_days)).strftime("%Y%m%d"))
        tables = tuple(
            f"{self.database}.dbo.{name}" for name in ("Samples", "SampleTests", "SampleResults")
        )
        connection = connect_mssql(self.dsn, tables=tables)     # pragma: no cover
        try:                                                    # pragma: no cover
            return [
                {
                    "sample_code": row["SampleCode"],
                    "test_code": row["TestCode"],
                    "component": row["ComponentName"],
                    "value": _number(row["ComponentValue"]),
                    "sampled_at": _labware_date(row["RegisterDate"]),
                }
                for row in connection.rows(SOURCE_QUERY.format(db=self.database), (cutoff,))
            ]
        finally:                                                # pragma: no cover
            connection.close()


def _number(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _labware_date(value: Any) -> str:
    """LabWare's integer YYYYMMDD as an ISO date."""

    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def build_source(mode: str | None = None, dsn: str | None = None) -> Source:
    """Pick an adapter from configuration. ``local``/``stub`` need nothing."""

    mode = (mode or get_settings().lims_mode).lower()
    if mode in {"local", "stub"}:
        return StubSource()
    if mode == "sqlserver":
        if not dsn:
            raise IntegrationError(
                "Set PIMS_LIMS_DSN to read the live LIMS.", adapter="sqlserver"
            )
        return SqlServerSource(dsn)
    raise IntegrationError(f"Unknown LIMS mode {mode!r}.", adapter=mode)


def sync(
    source: Source | None = None,
    since_days: int = 2,
    dry_run: bool = False,
    conn=None,
) -> dict[str, Any]:
    """Pull results and write them into the projection."""

    settings = get_settings()
    source = source or build_source()
    with jobs.record_run("lims_sync", conn) as detail:
        rows = list(source.fetch(since_days))
        detail.update({"adapter": source.name, "fetched": len(rows), "since_days": since_days})
        if dry_run:
            return {
                "adapter": source.name,
                "fetched": len(rows),
                "written": 0,
                "dry_run": True,
                "sample": rows[:3],
            }
        written = 0
        if rows:
            written = lims.ingest(rows, settings.lims_source_label, conn)["written"]
        detail["written"] = written
        return {
            "adapter": source.name,
            "fetched": len(rows),
            "written": written,
            "source_label": settings.lims_source_label,
            "retrieved_at": utc_now_iso(),
            "freshness": lims.freshness(conn),
        }
