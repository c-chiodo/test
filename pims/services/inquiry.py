"""The Inquiry screen: Location Balance, Activity, Order, QC.

Four read-only views the legacy client offered in one tabbed window, kept
together here because that is how staff think about them, and because each one
needs to export to CSV the same way.
"""

from __future__ import annotations

from typing import Any

from .. import db
from ..errors import ValidationError
from ..util import rows_to_csv
from . import inventory, orders, specs as specs_service
from .qc import QC_SELECT, _values_from

TABS = ("balance", "activity", "order", "qc")


def run(tab: str, filters: dict[str, Any], conn=None) -> dict[str, Any]:
    tab = (tab or "balance").lower()
    if tab not in TABS:
        raise ValidationError(f"Unknown inquiry {tab!r}.", allowed=list(TABS))
    handler = {
        "balance": _balance,
        "activity": _activity,
        "order": _orders,
        "qc": _qc,
    }[tab]
    rows, columns = handler(filters, conn)
    return {"tab": tab, "columns": columns, "rows": rows, "row_count": len(rows)}


def to_csv(result: dict) -> str:
    return rows_to_csv(result["rows"], [c["name"] for c in result["columns"]])


def _cols(*pairs: tuple[str, str]) -> list[dict]:
    return [{"name": name, "label": label} for name, label in pairs]


def _balance(f: dict, conn):
    rows = inventory.location_balance(
        plant_id=f.get("plant_id"),
        location_id=f.get("location_id"),
        material_id=f.get("material_id"),
        as_of=f.get("as_of"),
        include_zero=bool(f.get("include_zero")),
        conn=conn,
    )
    return rows, _cols(
        ("plant_code", "Plant"),
        ("location_number", "Location"),
        ("location_description", "Description"),
        ("location_type", "Type"),
        ("material_number", "Material"),
        ("material_description", "Material Description"),
        ("balance", "Balance (lbs)"),
        ("max_capacity", "Capacity (lbs)"),
        ("percent_full", "% Full"),
    )


def _activity(f: dict, conn):
    rows = inventory.activity(
        plant_id=f.get("plant_id"),
        order_id=f.get("order_id"),
        location_id=f.get("location_id"),
        material_id=f.get("material_id"),
        operation=f.get("operation"),
        date_from=f.get("date_from"),
        date_to=f.get("date_to"),
        include_voided=bool(f.get("include_voided")),
        limit=int(f.get("limit") or 500),
        conn=conn,
    )
    return rows, _cols(
        ("transaction_id", "Trans ID"),
        ("order_id", "Order Id"),
        ("transaction_type", "Type"),
        ("plant_code", "Plant"),
        ("user_date", "User Trans Date"),
        ("username", "User"),
        ("from_material_number", "From Mat"),
        ("from_location_number", "From Loc"),
        ("from_qty", "From Qty"),
        ("to_material_number", "To Mat"),
        ("to_location_number", "To Loc"),
        ("to_qty", "To Qty"),
        ("trailer_number", "Trailer #"),
        ("remarks", "Remarks"),
    )


def _orders(f: dict, conn):
    result = orders.search(
        plant_id=f.get("plant_id"),
        order_type_id=f.get("order_type_id"),
        department_id=f.get("department_id"),
        status_id=f.get("status_id"),
        material_id=f.get("material_id"),
        customer_id=f.get("customer_id"),
        vendor_id=f.get("vendor_id"),
        due_from=f.get("date_from"),
        due_to=f.get("date_to"),
        text=f.get("text"),
        limit=int(f.get("limit") or 500),
        conn=conn,
    )
    return result["rows"], _cols(
        ("order_id", "Order Id"),
        ("order_type", "Type"),
        ("order_date", "Order Date"),
        ("due_date", "Due Date"),
        ("plant_code", "Plant"),
        ("status", "Status"),
        ("material_one_number", "Mat 1"),
        ("material_one_description", "Mat 1 Description"),
        ("material_one_quantity", "Qty Ordered"),
        ("qty_fulfilled", "Qty Complete"),
        ("percent_complete", "% Complete"),
        ("customer_name", "Customer"),
        ("vendor_name", "Vendor"),
    )


def _qc(f: dict, conn):
    sql = QC_SELECT + " WHERE q.active = 1"
    params: list[Any] = []
    if f.get("plant_id"):
        sql += " AND o.plant_id = ?"
        params.append(f["plant_id"])
    if f.get("order_id"):
        sql += " AND q.order_id = ?"
        params.append(f["order_id"])
    if f.get("material_id"):
        sql += " AND o.material_one_id = ?"
        params.append(f["material_id"])
    if f.get("sample_number"):
        sql += " AND q.sample_number LIKE ?"
        params.append(f"%{f['sample_number']}%")
    if f.get("date_from"):
        sql += " AND q.test_date >= ?"
        params.append(f["date_from"])
    if f.get("date_to"):
        sql += " AND q.test_date <= ?"
        params.append(f["date_to"])
    sql += " ORDER BY q.test_date DESC, q.qc_id DESC LIMIT ?"
    params.append(int(f.get("limit") or 500))

    rows = db.query(sql, params, conn)
    for row in rows:
        evaluations = specs_service.evaluate(
            row["material_one_id"], _values_from(row), conn
        )
        summary = specs_service.summarize(evaluations)
        row["spec_status"] = summary["status"]
        row["out_of_spec"] = ", ".join(summary["out_of_spec"])
    if f.get("out_of_spec_only"):
        rows = [r for r in rows if r["spec_status"] == "out_of_spec"]
    return rows, _cols(
        ("qc_id", "QC Id"),
        ("order_id", "Order Id"),
        ("plant_code", "Plant"),
        ("test_date", "Test Date"),
        ("material_number", "Material"),
        ("sample_number", "Sample #"),
        ("bol_number", "BOL #"),
        ("moisture", "Moisture"),
        ("temp", "Temp"),
        ("ph", "pH"),
        ("ffa", "FFA"),
        ("tfa", "TFA"),
        ("spintest_fallout", "Spintest"),
        ("spec_status", "Spec"),
        ("out_of_spec", "Out of spec"),
        ("performed_by", "Performed By"),
    )
