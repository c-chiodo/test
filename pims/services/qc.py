"""Quality control: regular QC records, in-process testing, QA checklists.

The validation here replaces the rule reported in FE-2026-002. The legacy
check was::

    if (Ordertype = SO AND Department = 2) OR Ordertype = PO:
        warn when moisture <= 0, temp <= 0, spintest <= 0

Order type is not what determines whether a sample runs a test — the product
is. A soaps, water or cattle purchase order tripped all three warnings on every
save, training staff either to type placeholder numbers or to click through
validation. Here the required analytes come from ``material_test`` for the
order's material, and each warning names the product it applies to.

Warnings do not block a save: QC staff legitimately record partial results. But
saving over an open warning is recorded — ``acknowledged_warnings`` lands in the
audit trail, so "we always click through that box" becomes visible instead of
invisible.
"""

from __future__ import annotations

from typing import Any

from .. import audit, db
from ..config import ANALYTE_LABELS
from ..errors import BusinessRuleError, NotFound, ValidationError
from ..security import require_permission
from ..util import to_float, utc_now_iso
from . import numbering
from . import specs as specs_service

QC_SELECT = """
    SELECT q.*, o.plant_id, o.order_type_id, o.material_one_id, o.customer_id, o.vendor_id,
           ot.code AS order_type, p.code AS plant_code,
           m.number AS material_number, m.description AS material_description, m.family
    FROM qc q
    JOIN "order" o ON o.order_id = q.order_id
    JOIN order_type ot ON ot.order_type_id = o.order_type_id
    JOIN plant p ON p.plant_id = o.plant_id
    LEFT JOIN material m ON m.material_id = o.material_one_id
"""

#: QC form field -> analyte key.
FIELD_ANALYTE = {
    "moisture": "moisture",
    "temp": "temp",
    "ph": "ph",
    "ffa": "ffa",
    "tfa": "tfa",
    "spintest_fallout": "spintest",
}
ANALYTE_FIELD = {v: k for k, v in FIELD_ANALYTE.items()}


def _order(order_id: int, conn) -> dict:
    row = db.query_one(
        'SELECT o.*, ot.code AS order_type, s.is_terminal, s.name AS status'
        ' FROM "order" o'
        " JOIN order_type ot ON ot.order_type_id = o.order_type_id"
        " JOIN status s ON s.status_id = o.status_id"
        " WHERE o.order_id = ?",
        (order_id,),
        conn,
    )
    if row is None:
        raise NotFound(f"Order {order_id} was not found.")
    return row


def _values_from(payload: dict) -> dict[str, Any]:
    return {
        analyte: to_float(payload.get(field))
        for field, analyte in FIELD_ANALYTE.items()
    }


def validate(order_id: int, payload: dict, conn=None) -> dict[str, Any]:
    """Check a QC entry against what the *product* is actually tested for.

    Returns the analyte evaluation, any warnings, and the spec verdict. Safe to
    call on every keystroke — it writes nothing.
    """

    order = _order(order_id, conn)
    material_id = order["material_one_id"]
    values = _values_from(payload)
    evaluations = specs_service.evaluate(material_id, values, conn)
    required = set(specs_service.required_tests(material_id, conn))

    material = db.query_one(
        "SELECT number, description, family FROM material WHERE material_id = ?",
        (material_id,),
        conn,
    ) or {"number": "?", "description": "unknown material", "family": ""}

    warnings: list[dict[str, Any]] = []
    for analyte in sorted(required):
        field = ANALYTE_FIELD.get(analyte)
        if field is None:
            continue                      # recorded in LIMS, not on this form
        value = values.get(analyte)
        label = ANALYTE_LABELS.get(analyte, analyte)
        if value is None:
            warnings.append(
                {
                    "field": field,
                    "analyte": analyte,
                    "severity": "warning",
                    "message": (
                        f"{label} is missing. {material['number']} "
                        f"{material['description']} is tested for {label.lower()}."
                    ),
                }
            )
        elif value <= 0 and analyte != "temp":
            warnings.append(
                {
                    "field": field,
                    "analyte": analyte,
                    "severity": "warning",
                    "message": f"{label} is {value}. Expected a value greater than zero.",
                }
            )

    for evaluation in evaluations:
        if evaluation["verdict"] == "out_of_spec":
            field = ANALYTE_FIELD.get(evaluation["analyte"])
            bounds = []
            if evaluation["min_value"] is not None:
                bounds.append(f"min {evaluation['min_value']}")
            if evaluation["max_value"] is not None:
                bounds.append(f"max {evaluation['max_value']}")
            warnings.append(
                {
                    "field": field,
                    "analyte": evaluation["analyte"],
                    "severity": "out_of_spec",
                    "message": (
                        f"{evaluation['label']} {evaluation['value']} is outside "
                        f"spec for {material['number']} ({', '.join(bounds)})."
                    ),
                }
            )

    settings_row = db.query_one(
        "SELECT value FROM system_setting WHERE key = 'qc.require_sample_number'", (), conn
    )
    if (
        settings_row
        and settings_row["value"] == "true"
        and required
        and not (payload.get("sample_number") or "").strip()
    ):
        warnings.append(
            {
                "field": "sample_number",
                "analyte": None,
                "severity": "warning",
                "message": "No sample number — the LIMS result cannot be matched back to this record.",
            }
        )

    summary = specs_service.summarize(evaluations)
    return {
        "order_id": order_id,
        "material": material,
        "required_tests": sorted(required),
        "not_tested": sorted(set(FIELD_ANALYTE.values()) - required),
        "evaluations": evaluations,
        "warnings": warnings,
        "summary": summary,
    }


def list_for_order(order_id: int, conn=None) -> list[dict]:
    rows = db.query(
        QC_SELECT + " WHERE q.order_id = ? AND q.active = 1 ORDER BY q.qc_id DESC",
        (order_id,),
        conn,
    )
    for row in rows:
        row["evaluations"] = specs_service.evaluate(
            row["material_one_id"], _values_from(row), conn
        )
        row["spec_summary"] = specs_service.summarize(row["evaluations"])
    return rows


def get(qc_id: int, conn=None) -> dict:
    row = db.query_one(QC_SELECT + " WHERE q.qc_id = ?", (qc_id,), conn)
    if row is None:
        raise NotFound(f"QC record {qc_id} was not found.")
    row["evaluations"] = specs_service.evaluate(
        row["material_one_id"], _values_from(row), conn
    )
    row["spec_summary"] = specs_service.summarize(row["evaluations"])
    return row


def save(
    order_id: int,
    payload: dict,
    user: dict,
    qc_id: int | None = None,
    acknowledge_warnings: bool = False,
    conn=None,
) -> dict:
    """Create or update a QC record.

    Warnings are advisory; ``acknowledge_warnings`` records that the operator
    saw them and saved anyway. Values that fail a hard check (a negative
    moisture, a pH of 47) are rejected outright — the legacy screen accepted
    them.
    """

    require_permission(user, "qc.write")
    order = _order(order_id, conn)
    if order["is_terminal"] and qc_id is None:
        raise BusinessRuleError(
            f"Order {order_id} is {order['status']}; QC cannot be added.",
            order_id=order_id,
        )

    hard_errors: dict[str, str] = {}
    for field in ("moisture", "temp", "ph", "ffa", "tfa", "spintest_fallout"):
        value = to_float(payload.get(field))
        if value is None:
            continue
        if value < 0:
            hard_errors[field] = "Cannot be negative."
        elif field == "ph" and value > 14:
            hard_errors[field] = "pH must be between 0 and 14."
        elif field in {"moisture", "ffa", "tfa"} and value > 100:
            hard_errors[field] = "Percentages cannot exceed 100."
    flash = (payload.get("flash_pf") or "").strip().upper()
    if flash and flash not in {"P", "F", "PASS", "FAIL"}:
        hard_errors["flash_pf"] = "Enter P (pass) or F (fail)."
    if hard_errors:
        raise ValidationError("Check the highlighted values.", fields=hard_errors)

    check = validate(order_id, payload, conn)

    sample_number = (payload.get("sample_number") or "").strip()
    if not sample_number and not qc_id:
        auto = numbering.setting("sample.auto_generate", conn) == "true"
        if auto or payload.get("generate_sample_number"):
            sample_number = numbering.next_sample_number(order_id, conn=conn)

    values = {
        "bol_number": payload.get("bol_number") or "",
        "test_date": str(payload.get("test_date") or utc_now_iso())[:10],
        "performed_by": payload.get("performed_by") or user["username"],
        "moisture": to_float(payload.get("moisture")),
        "temp": to_float(payload.get("temp")),
        "ph": to_float(payload.get("ph")),
        "ffa": to_float(payload.get("ffa")),
        "tfa": to_float(payload.get("tfa")),
        "spintest_fallout": to_float(payload.get("spintest_fallout")),
        "flash_pf": flash[:1] if flash else None,
        "steam_on": 1 if payload.get("steam_on") else 0,
        "seal_number": payload.get("seal_number") or "",
        "last_material_hauled": payload.get("last_material_hauled") or "",
        "sample_number": sample_number,
        "blend_serial_number": payload.get("blend_serial_number")
        or order["blend_serial_number"],
        "comments": payload.get("comments") or "",
    }

    with db.transaction(conn):
        if qc_id:
            before = get(qc_id, conn)
            if before["order_id"] != order_id:
                raise ValidationError(
                    "That QC record belongs to a different order.", qc_id=qc_id
                )
            values["date_modified"] = utc_now_iso()
            values["modified_by"] = user["username"]
            db.update("qc", {"qc_id": qc_id}, values, conn)
            action, summary = "update", f"Updated QC {qc_id} on order {order_id}"
            detail = {
                "changes": audit.diff(
                    {k: before.get(k) for k in values}, values
                )
            }
        else:
            values["order_id"] = order_id
            values["date_added"] = utc_now_iso()
            values["added_by"] = user["username"]
            qc_id = db.insert("qc", values, conn)
            action, summary = "create", f"Recorded QC {qc_id} on order {order_id}"
            detail = {"values": values}

        detail["spec_summary"] = check["summary"]
        if check["warnings"]:
            detail["warnings"] = check["warnings"]
            detail["acknowledged_warnings"] = bool(acknowledge_warnings)
        audit.record(
            username=user["username"],
            action=f"qc.{action}",
            entity="qc",
            entity_id=qc_id,
            summary=summary,
            detail=detail,
            conn=conn,
        )

    saved = get(qc_id, conn)
    saved["warnings"] = check["warnings"]
    return saved


def void(qc_id: int, reason: str, user: dict, conn=None) -> dict:
    require_permission(user, "qc.write")
    record = get(qc_id, conn)
    with db.transaction(conn):
        db.update(
            "qc",
            {"qc_id": qc_id},
            {
                "active": 0,
                "date_modified": utc_now_iso(),
                "modified_by": user["username"],
            },
            conn,
        )
        audit.record(
            username=user["username"],
            action="qc.void",
            entity="qc",
            entity_id=qc_id,
            summary=f"Voided QC {qc_id}",
            detail={"reason": reason, "order_id": record["order_id"]},
            conn=conn,
        )
    return get(qc_id, conn)


# ------------------------------------------------------------- in-process


def in_process(order_id: int, conn=None) -> list[dict]:
    return db.query(
        """
        SELECT r.*, tp.name AS test_point_name
        FROM qc_in_process r
        JOIN test_point tp ON tp.test_point_id = r.test_point_id
        WHERE r.order_id = ?
        ORDER BY r.reading_time DESC, r.reading_id DESC
        """,
        (order_id,),
        conn,
    )


def add_in_process(order_id: int, payload: dict, user: dict, conn=None) -> dict:
    require_permission(user, "qc.write")
    _order(order_id, conn)
    analyte = payload.get("analyte")
    if analyte not in ANALYTE_LABELS:
        raise ValidationError(
            "Choose what was measured.", fields={"analyte": "Unknown analyte."}
        )
    if not payload.get("test_point_id"):
        raise ValidationError(
            "Choose a test point.", fields={"test_point_id": "Required."}
        )
    reading_id = db.insert(
        "qc_in_process",
        {
            "order_id": order_id,
            "test_point_id": payload["test_point_id"],
            "reading_time": payload.get("reading_time") or utc_now_iso(),
            "analyte": analyte,
            "value": to_float(payload.get("value")),
            "comments": payload.get("comments") or "",
            "added_by": user["username"],
            "date_added": utc_now_iso(),
        },
        conn,
    )
    audit.record(
        username=user["username"],
        action="qc.in_process",
        entity="qc_in_process",
        entity_id=reading_id,
        summary=f"In-process {analyte} reading on order {order_id}",
        detail={"order_id": order_id},
        conn=conn,
    )
    return db.query_one(
        "SELECT * FROM qc_in_process WHERE reading_id = ?", (reading_id,), conn
    )


# ------------------------------------------------------------ QA checklist


def qa_checklists(order_id: int, conn=None) -> list[dict]:
    headers = db.query(
        "SELECT * FROM qa_header WHERE order_id = ? ORDER BY header_id DESC",
        (order_id,),
        conn,
    )
    for header in headers:
        header["responses"] = db.query(
            """
            SELECT r.response_id, r.question_id, r.response, q.question, q.answer_type
            FROM qa_response r
            JOIN qa_question q ON q.question_id = r.question_id
            WHERE r.header_id = ?
            ORDER BY q.sort_order
            """,
            (header["header_id"],),
            conn,
        )
        header["exceptions"] = [
            r["question"] for r in header["responses"] if r["response"] == "No"
        ]
    return headers


def save_qa_checklist(order_id: int, payload: dict, user: dict, conn=None) -> dict:
    require_permission(user, "qc.write")
    order = _order(order_id, conn)
    responses = payload.get("responses") or {}
    questions = {
        q["question_id"]: q
        for q in db.query(
            "SELECT question_id, question, answer_type FROM qa_question WHERE enabled = 1",
            (),
            conn,
        )
    }
    unanswered = [
        q["question"]
        for qid, q in questions.items()
        if not str(responses.get(str(qid), responses.get(qid, ""))).strip()
    ]
    if unanswered:
        raise ValidationError(
            "Answer every checklist question before saving.",
            fields={"responses": "; ".join(unanswered)},
        )

    with db.transaction(conn):
        header_id = db.insert(
            "qa_header",
            {
                "order_id": order_id,
                "plant_id": order["plant_id"],
                "qc_id": payload.get("qc_id"),
                "trailer_number": payload.get("trailer_number") or "",
                "trailer_load_time": payload.get("trailer_load_time"),
                "comments": payload.get("comments") or "",
                "date_added": utc_now_iso(),
                "added_by": user["username"],
            },
            conn,
        )
        for qid in questions:
            answer = responses.get(str(qid), responses.get(qid, ""))
            db.insert(
                "qa_response",
                {"header_id": header_id, "question_id": qid, "response": str(answer)},
                conn,
            )
        failures = [
            questions[qid]["question"]
            for qid in questions
            if str(responses.get(str(qid), responses.get(qid, ""))) == "No"
        ]
        audit.record(
            username=user["username"],
            action="qa.checklist",
            entity="qa_header",
            entity_id=header_id,
            summary=f"QA checklist completed on order {order_id}",
            detail={"failures": failures, "order_id": order_id},
            conn=conn,
        )
    return qa_checklists(order_id, conn)[0]


def out_of_spec_report(
    plant_id: int | None = None, days: int = 30, limit: int = 200, conn=None
) -> list[dict]:
    """Recent QC records whose results fall outside the product's limits."""

    sql = QC_SELECT + " WHERE q.active = 1 AND q.test_date >= date('now', ?)"
    params: list[Any] = [f"-{int(days)} days"]
    if plant_id:
        sql += " AND o.plant_id = ?"
        params.append(plant_id)
    sql += " ORDER BY q.test_date DESC, q.qc_id DESC LIMIT ?"
    params.append(limit * 3)

    flagged: list[dict] = []
    for row in db.query(sql, params, conn):
        evaluations = specs_service.evaluate(
            row["material_one_id"], _values_from(row), conn
        )
        summary = specs_service.summarize(evaluations)
        if summary["out_of_spec"]:
            row["evaluations"] = [e for e in evaluations if e["verdict"] == "out_of_spec"]
            row["spec_summary"] = summary
            flagged.append(row)
        if len(flagged) >= limit:
            break
    return flagged
