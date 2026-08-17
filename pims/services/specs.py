"""Product specifications: which tests a material runs, and its limits.

This module is the corrected version of the rule the legacy client got wrong.
Build 1.2.23.0 decided which analytes a QC record must carry from the *order
type* alone::

    runChecks = (Ordertype = SO AND Department = 2) OR Ordertype = PO
    if runChecks: warn when moisture / temp / spintest <= 0

which demanded a moisture, temperature and spintest reading on every purchase
order — including soaps, water and cattle products that never run those tests
(FE-2026-002). Here the question "does this need a moisture value?" is answered
by the material, from ``material_test``, and the limits come from
``material_spec``.
"""

from __future__ import annotations

from typing import Any

from .. import db
from ..config import ANALYTE_LABELS, ANALYTE_UNITS
from ..errors import NotFound, ValidationError
from ..util import to_float


def required_tests(material_id: int | None, conn=None) -> list[str]:
    """Analytes this material is tested for. Unknown material -> no tests."""

    if not material_id:
        return []
    return [
        r["analyte"]
        for r in db.query(
            "SELECT analyte FROM material_test"
            " WHERE material_id = ? AND required = 1 ORDER BY analyte",
            (material_id,),
            conn,
        )
    ]


def specs_for(material_id: int | None, conn=None) -> dict[str, dict[str, Any]]:
    if not material_id:
        return {}
    rows = db.query(
        """
        SELECT analyte, min_value, max_value, source, note, needs_review
        FROM material_spec
        WHERE material_id = ? AND active = 1
        """,
        (material_id,),
        conn,
    )
    return {r["analyte"]: r for r in rows}


def evaluate(material_id: int | None, values: dict[str, Any], conn=None) -> list[dict]:
    """Compare measured values against the material's limits.

    Returns one row per analyte that has either a value or a spec, with a
    verdict of ``in_spec`` / ``out_of_spec`` / ``missing`` / ``no_spec``. The
    UI colours results from this; nothing else re-derives the comparison.
    """

    specs = specs_for(material_id, conn)
    tests = set(required_tests(material_id, conn))
    results: list[dict] = []
    for analyte in sorted(set(specs) | tests | {k for k, v in values.items() if v is not None}):
        value = to_float(values.get(analyte))
        spec = specs.get(analyte)
        lo = spec["min_value"] if spec else None
        hi = spec["max_value"] if spec else None
        if value is None:
            verdict = "missing" if analyte in tests else "not_run"
        elif spec is None:
            verdict = "no_spec"
        elif (lo is not None and value < lo) or (hi is not None and value > hi):
            verdict = "out_of_spec"
        else:
            verdict = "in_spec"
        results.append(
            {
                "analyte": analyte,
                "label": ANALYTE_LABELS.get(analyte, analyte.title()),
                "unit": ANALYTE_UNITS.get(analyte, ""),
                "value": value,
                "min_value": lo,
                "max_value": hi,
                "required": analyte in tests,
                "verdict": verdict,
                "needs_review": bool(spec["needs_review"]) if spec else False,
                "note": spec["note"] if spec else "",
            }
        )
    return results


def summarize(evaluations: list[dict]) -> dict[str, Any]:
    out = [e for e in evaluations if e["verdict"] == "out_of_spec"]
    missing = [e for e in evaluations if e["verdict"] == "missing"]
    return {
        "out_of_spec": [e["analyte"] for e in out],
        "missing_required": [e["analyte"] for e in missing],
        "status": "out_of_spec" if out else ("incomplete" if missing else "in_spec"),
    }


def list_specs(
    family: str | None = None, needs_review: bool | None = None, conn=None
) -> list[dict]:
    sql = """
        SELECT s.spec_id, s.material_id, m.number, m.description, m.family,
               s.analyte, s.min_value, s.max_value, s.source, s.note, s.needs_review
        FROM material_spec s
        JOIN material m ON m.material_id = s.material_id
        WHERE s.active = 1
    """
    params: list[Any] = []
    if family:
        sql += " AND m.family = ?"
        params.append(family)
    if needs_review:
        sql += " AND s.needs_review = 1"
    sql += " ORDER BY m.number, s.analyte"
    return db.query(sql, params, conn)


def upsert_spec(
    *,
    material_id: int,
    analyte: str,
    min_value: float | None,
    max_value: float | None,
    note: str = "",
    source: str = "PIMS",
    needs_review: bool = False,
    conn=None,
) -> dict:
    if analyte not in ANALYTE_LABELS:
        raise ValidationError(f"Unknown analyte {analyte!r}.", analyte=analyte)
    if min_value is not None and max_value is not None and min_value > max_value:
        raise ValidationError(
            "Minimum cannot be greater than maximum.",
            analyte=analyte,
            min_value=min_value,
            max_value=max_value,
        )
    if not db.query_one(
        "SELECT material_id FROM material WHERE material_id = ?", (material_id,), conn
    ):
        raise NotFound(f"Material {material_id} does not exist.")
    existing = db.query_one(
        "SELECT spec_id FROM material_spec WHERE material_id = ? AND analyte = ?",
        (material_id, analyte),
        conn,
    )
    values = {
        "min_value": min_value,
        "max_value": max_value,
        "note": note,
        "source": source,
        "needs_review": 1 if needs_review else 0,
        "active": 1,
    }
    if existing:
        db.update("material_spec", {"spec_id": existing["spec_id"]}, values, conn)
        spec_id = existing["spec_id"]
    else:
        spec_id = db.insert(
            "material_spec",
            {"material_id": material_id, "analyte": analyte, **values},
            conn,
        )
    return db.query_one(
        "SELECT * FROM material_spec WHERE spec_id = ?", (spec_id,), conn
    )


def set_required_tests(material_id: int, analytes: list[str], conn=None) -> list[str]:
    unknown = [a for a in analytes if a not in ANALYTE_LABELS]
    if unknown:
        raise ValidationError(f"Unknown analytes: {', '.join(unknown)}.", analytes=unknown)
    with db.transaction(conn):
        db.execute("DELETE FROM material_test WHERE material_id = ?", (material_id,), conn)
        for analyte in analytes:
            db.insert(
                "material_test",
                {"material_id": material_id, "analyte": analyte, "required": 1},
                conn,
            )
    return required_tests(material_id, conn)


def review_queue(conn=None) -> list[dict]:
    """Limits the source sheet itself flagged as contradictory.

    Two products ship with a QC-sheet limit that disagrees with the product
    label. Surfacing them is the point: in the legacy system this lived in a
    spreadsheet comment.
    """

    return list_specs(needs_review=True, conn=conn)
