"""The reports the plants keep in spreadsheets, computed from the ledger.

Three workbooks were handed over as the reporting that matters:

* **DM Yields** — soap received and processed, acid and steam per pound of
  soap, how each settle and MGR break split, first-pass, second-pass and
  overall yield, and what went out the gate. It is built from a PIMS QUERY
  export cut into a dozen sheets by material and tank number, then summed
  with SUMIFS between two dates.
* **Caustic in MGR and pH** — caustic per blended load, gross, reversed and
  net, with the destination pH, by plant, product and month.
* **PIMS operations workbook** — the same caustic story plus the executive
  counts.

Each is a function here over the same rows, with the workbook's definitions
written down beside the numbers. The materials are named by their legacy
numbers — what the workbooks filter on, and what a mirrored legacy ledger
carries — so the reports read the same on native and mirrored data.

Two differences from the spreadsheets, both deliberate:

* Tank numbers do not decide what a row is. The workbook's "settle oil" is
  oil drawn from tanks 1–10 and "MGRV oil" is oil from tanks 13–17; here it
  is oil made from soap-in-process and oil made from MGR, which is the same
  thing at Des Moines and still true at a plant numbered differently.
* A reversed posting and its reversal are left out of the yields (the order
  screens do the same), and are shown — not netted silently — in the caustic
  and reversal reports, where the reversals are the point.

Days are the operator's business date (``user_date``), which is what the
workbooks' Transaction_Date column held in the Yields export.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from .. import db
from ..errors import ValidationError
from ..security import plants_for_user, require_plant
from ..util import rows_to_csv

# ------------------------------------------------------------------ materials

#: Legacy material numbers by the part they play. Overridable, per site, with
#: the ``reports.materials`` setting (a JSON object of the same shape).
MATERIALS: dict[str, list[int]] = {
    "soap": [6, 7, 10],            # Soap - Degum, Soap - Gum, Wetgums
    "acid": [1],
    "steam": [4],
    "water_in": [11, 1008],        # city water, process water (reprocessed)
    "process": [1006],             # Soap in Process-Veg
    "mgr": [1007],                 # MGR veg
    "mgr_animal": [1003],
    "oil": [1018, 1019],           # the 20-series oils off the settle
    "process_water": [1008],
    "caustic": [3],
    "out_mgrv": [1007, 3019],
    "out_mgra": [1003, 5003],
}

#: The workbook's trailer estimate for outbound water.
WATER_TRAILER_LBS = 46_000.0
DEFAULT_TFA = 26.0

#: Legacy transaction-type names, for the exports the workbooks read.
LEGACY_TYPE_NAMES = {
    "RECEIVE": "RECEIVED", "PRODUCE": "PRODUCED", "MOVE": "MOVEMENT", "LOAD": "MOVE-LOAD",
    "PROD_LOAD": "PROD-LOAD", "SHIP": "SHIP-LEAVE", "SHRINK": "SHRINKAGE", "ADJUST": "SHIPADJ",
}


def materials(conn=None) -> dict[str, set[int]]:
    raw = db.scalar("SELECT value FROM system_setting WHERE key = 'reports.materials'", (), conn)
    roles = dict(MATERIALS)
    if raw:
        try:
            override = json.loads(raw)
            roles.update({k: [int(n) for n in v] for k, v in override.items() if isinstance(v, list)})
        except (ValueError, TypeError):
            pass
    return {k: set(v) for k, v in roles.items()}


def _num(number: Any) -> int | None:
    """A material number as the legacy system held it: 00007 is 7."""

    try:
        return int(str(number).strip())
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------- period

def _period(filters: dict[str, Any]) -> tuple[str, str]:
    today = date.today()
    try:
        end = date.fromisoformat(str(filters.get("end") or today.isoformat())[:10])
        start = date.fromisoformat(str(filters.get("start") or (end - timedelta(days=27)).isoformat())[:10])
    except ValueError as exc:
        raise ValidationError("Dates must be YYYY-MM-DD.", fields={"start": str(exc)}) from exc
    if start > end:
        raise ValidationError("The start date is after the end date.", fields={"start": "Pick an earlier start."})
    if (end - start).days > 400:
        raise ValidationError("Report at most 400 days at a time.", fields={"start": "Pick a later start."})
    return start.isoformat(), end.isoformat()


def _plants(filters: dict[str, Any], user: dict, conn) -> list[dict]:
    if user.get("role") == "admin":
        allowed = db.query("SELECT plant_id, code, name FROM plant WHERE active = 1 ORDER BY code", (), conn)
    else:
        allowed = plants_for_user(user["user_id"], conn)
    plant_id = filters.get("plant_id")
    if plant_id in (None, "", "all", 0):
        return allowed
    require_plant(user, int(plant_id), conn)
    return [p for p in allowed if p["plant_id"] == int(plant_id)]


# --------------------------------------------------------------------- ledger

LEDGER_SELECT = """
SELECT t.transaction_id, t.parent_transaction_id, t.order_id, t.plant_id, p.code AS plant_code,
       t.transaction_date, t.user_date, tt.code AS type_code, tt.kind, tt.description AS type_name,
       t.from_material_id, fm.number AS from_number, fm.description AS from_description,
       t.from_location_id, fl.number AS from_location, flt.name AS from_location_type, t.from_qty, t.from_bol,
       t.to_material_id, tm.number AS to_number, tm.description AS to_description,
       t.to_location_id, tl.number AS to_location, tlt.name AS to_location_type, t.to_qty, t.to_bol,
       t.trailer_number, t.tank_hours, t.employee_hours, t.remarks, t.voided, t.is_reversal,
       d.code AS department_code, u.full_name AS user_name,
       o.order_reference, o.ship_method, v.gp_vendorid, v.name AS vendor_name
FROM inventory_transaction t
JOIN transaction_type tt ON tt.transaction_type_id = t.transaction_type_id
JOIN plant p ON p.plant_id = t.plant_id
LEFT JOIN material fm ON fm.material_id = t.from_material_id
LEFT JOIN material tm ON tm.material_id = t.to_material_id
LEFT JOIN location fl ON fl.location_id = t.from_location_id
LEFT JOIN location tl ON tl.location_id = t.to_location_id
LEFT JOIN location_type flt ON flt.location_type_id = fl.location_type_id
LEFT JOIN location_type tlt ON tlt.location_type_id = tl.location_type_id
LEFT JOIN department d ON d.department_id = t.department_id
LEFT JOIN app_user u ON u.user_id = t.user_id
LEFT JOIN "order" o ON o.order_id = t.order_id
LEFT JOIN vendor v ON v.vendor_id = o.vendor_id
"""


def _ledger(plant_ids: list[int], start: str, end: str, conn, *, include_reversed: bool = False) -> list[dict]:
    if not plant_ids:
        return []
    marks = ", ".join("?" for _ in plant_ids)
    sql = LEDGER_SELECT + f" WHERE t.plant_id IN ({marks}) AND substr(t.user_date, 1, 10) BETWEEN ? AND ?"
    if not include_reversed:
        sql += " AND t.voided = 0 AND t.is_reversal = 0"
    rows = db.query(sql + " ORDER BY t.transaction_id", (*plant_ids, start, end), conn)
    for r in rows:
        r["day"] = str(r["user_date"])[:10]
        r["f"] = _num(r["from_number"])
        r["t"] = _num(r["to_number"])
        r["from_qty"] = float(r["from_qty"] or 0)
        r["to_qty"] = float(r["to_qty"] or 0)
    return rows


def _pct(part: float, whole: float) -> float | None:
    return round(part / whole * 100.0, 2) if whole else None


def _r(x: float) -> float:
    return round(x, 1)


# ---------------------------------------------------------------- acid yields

YIELD_DEFINITIONS = [
    ("Soap received", "Soap (Soap - Gum, Soap - Degum, Wetgums) received, by the pound on the receipt."),
    ("Soap processed", "Soap charged into Soap in Process: the soap that went into a settle."),
    ("Acid used", "Acid charged into Soap in Process; % is per pound of soap processed."),
    ("Steam (est.)", "Steam charged into Soap in Process, as the operator estimated it."),
    ("Reprocessed water", "Process or city water charged back into Soap in Process."),
    ("Settle break", "What each settle drew off: 20's oil, MGR and water, and each as a share of the three."),
    ("MGR break", "What reprocessing MGR drew off: oil to the 20's, MGR kept back, and water. "
                  "MGR processed is what went into the MGR tanks to be reprocessed."),
    ("Total 20's oil", "All oil made into the 20-series tanks, from settles and MGR."),
    ("20's bottoms", "20's oil sent back to MGR."),
    ("Oil final", "20's oil made or loaded into a finished product."),
    ("FPY", "First-pass yield: settle oil ÷ (soap processed × TFA)."),
    ("SPY", "Second-pass yield: 1 − 20's bottoms ÷ total 20's oil."),
    ("OY", "Overall yield: oil final ÷ (soap processed × TFA). The second figure leaves reprocessed water out of the soap."),
    ("Outbound", "Pounds of MGRV, MGRA, process water and caustic blended onto trailers."),
    ("Outbound water", "Process water shipped; trailers estimated at 46,000 lbs each."),
]


def acid_yields(filters: dict[str, Any], user: dict, conn=None) -> dict[str, Any]:
    """DM Yields, for any plant and any dates."""

    start, end = _period(filters)
    plants = _plants(filters, user, conn)
    tfa = float(filters.get("tfa") or DEFAULT_TFA)
    m = materials(conn)
    rows = _ledger([p["plant_id"] for p in plants], start, end, conn)

    totals: dict[str, float] = defaultdict(float)
    daily: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    def add(key: str, qty: float, day: str) -> None:
        totals[key] += qty
        daily[day][key] += qty

    for r in rows:
        f, t, kind, day = r["f"], r["t"], r["kind"], r["day"]
        if kind == "RECEIVE" and t in m["soap"]:
            add("soap_received", r["to_qty"], day)
        if kind in ("PRODUCE", "MOVE") and t in m["process"]:
            if f in m["soap"]:
                add("soap_processed", r["from_qty"], day)
            elif f in m["acid"]:
                add("acid_used", r["from_qty"], day)
            elif f in m["steam"]:
                add("steam", r["from_qty"], day)
            elif f in m["water_in"]:
                add("reprocessed_water", r["from_qty"], day)
        if kind == "PRODUCE":
            if f in m["process"]:
                if t in m["oil"]:
                    add("settle_oil", r["to_qty"], day)
                elif t in m["mgr"]:
                    add("settle_mgr", r["to_qty"], day)
                elif t in m["process_water"]:
                    add("settle_water", r["to_qty"], day)
            elif f in m["mgr"]:
                if t in m["oil"]:
                    add("mgr_oil", r["to_qty"], day)
                elif t in m["mgr"] and r["to_location_type"] != "MGR" and r["from_location_id"] != r["to_location_id"]:
                    add("mgr_mgr", r["to_qty"], day)
                elif t in m["process_water"]:
                    add("mgr_water", r["to_qty"], day)
            elif f in m["mgr_animal"] and t in m["oil"]:
                add("mgra_oil", r["to_qty"], day)
            if t in m["mgr"] and r["to_location_type"] == "MGR" and r["from_location_type"] != "MGR":
                add("mgr_processed", r["to_qty"], day)
            if t in m["oil"]:
                add("total_oil", r["to_qty"], day)
            if f in m["oil"] and (t in m["mgr"] or t in m["mgr_animal"]):
                add("bottoms", r["to_qty"], day)
        if f in m["oil"] and kind in ("PRODUCE", "LOAD") and t is not None and t not in (
            m["oil"] | m["mgr"] | m["mgr_animal"] | m["process_water"]
        ):
            add("oil_final", r["to_qty"], day)
        # Blended onto a trailer: a component that is not the product.
        if kind == "LOAD" and f is not None and t is not None and f != t:
            if f in m["out_mgrv"]:
                add("out_mgrv", r["from_qty"], day)
            elif f in m["out_mgra"]:
                add("out_mgra", r["from_qty"], day)
            elif f in m["process_water"]:
                add("out_water", r["from_qty"], day)
            elif f in m["caustic"]:
                add("out_caustic", r["from_qty"], day)
        if kind == "SHIP" and f in m["process_water"]:
            add("water_shipped", r["from_qty"], day)

    theoretical = totals["soap_processed"] * tfa / 100.0
    net_soap = totals["soap_processed"] - totals["reprocessed_water"]
    settle = totals["settle_oil"] + totals["settle_mgr"] + totals["settle_water"]
    mgr_break = totals["mgr_oil"] + totals["mgr_mgr"] + totals["mgr_water"]

    def split(parts: dict[str, float]) -> list[dict]:
        whole = sum(parts.values())
        return [{"part": k, "lbs": _r(v), "pct": _pct(v, whole)} for k, v in parts.items()]

    series = []
    window: list[dict[str, float]] = []
    day = date.fromisoformat(start)
    while day.isoformat() <= end:
        d = daily.get(day.isoformat(), {})
        window = (window + [d])[-7:]
        soap7 = sum(w.get("soap_processed", 0.0) for w in window)
        series.append({
            "date": day.isoformat(),
            "soap_processed": _r(d.get("soap_processed", 0.0)),
            "acid_used": _r(d.get("acid_used", 0.0)),
            "oil_fp": _r(d.get("settle_oil", 0.0)),
            "oil_final": _r(d.get("oil_final", 0.0)),
            "fpy_7d": _pct(sum(w.get("settle_oil", 0.0) for w in window), soap7 * tfa / 100.0),
            "oy_7d": _pct(sum(w.get("oil_final", 0.0) for w in window), soap7 * tfa / 100.0),
        })
        day += timedelta(days=1)

    return {
        "report": "acid-yields",
        "start": start, "end": end, "tfa": tfa,
        "plants": [p["code"] for p in plants],
        "inputs": {
            "soap_received": _r(totals["soap_received"]),
            "soap_processed": _r(totals["soap_processed"]),
            "soap_processed_less_water": _r(net_soap),
            "acid_used": _r(totals["acid_used"]),
            "acid_pct": _pct(totals["acid_used"], totals["soap_processed"]),
            "steam": _r(totals["steam"]),
            "reprocessed_water": _r(totals["reprocessed_water"]),
        },
        "settle_break": split({"oil": totals["settle_oil"], "mgr": totals["settle_mgr"], "water": totals["settle_water"]}),
        "settle_total": _r(settle),
        "mgr_break": split({"oil": totals["mgr_oil"], "mgr": totals["mgr_mgr"], "water": totals["mgr_water"]}),
        "mgr_total": _r(mgr_break),
        "mgr_processed": _r(totals["mgr_processed"]),
        "oil": {
            "total_20s": _r(totals["total_oil"]),
            "settle_oil": _r(totals["settle_oil"]),
            "mgrv_oil": _r(totals["mgr_oil"]),
            "mgra_oil": _r(totals["mgra_oil"]),
            "bottoms_20s": _r(totals["bottoms"]),
            "bottoms_pct": _pct(totals["bottoms"], totals["total_oil"]),
            "oil_final": _r(totals["oil_final"]),
        },
        "yields": {
            "theoretical_oil": _r(theoretical),
            "fpy": _pct(totals["settle_oil"], theoretical),
            "spy": round(100.0 - totals["bottoms"] / totals["total_oil"] * 100.0, 2) if totals["total_oil"] else None,
            "oy": _pct(totals["oil_final"], theoretical),
            "oy_less_water": _pct(totals["oil_final"], net_soap * tfa / 100.0),
        },
        "outbound": {
            "mgrv": _r(totals["out_mgrv"]), "mgra": _r(totals["out_mgra"]),
            "water": _r(totals["out_water"]), "caustic": _r(totals["out_caustic"]),
        },
        "outbound_water": {
            "lbs": _r(totals["water_shipped"]),
            "trailers_est": round(totals["water_shipped"] / WATER_TRAILER_LBS, 1),
        },
        "daily": series,
        "definitions": [{"name": n, "definition": d} for n, d in YIELD_DEFINITIONS],
    }


# -------------------------------------------------------------------- caustic

def _order_ph(order_ids: Iterable[int], conn) -> dict[int, list[float]]:
    """The destination pH per order: dosed-load readings first, then QC. A
    zero is the legacy system's "not tested" and is not a reading."""

    ids = sorted({int(i) for i in order_ids if i})
    found: dict[int, list[float]] = defaultdict(list)
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        marks = ", ".join("?" for _ in part)
        for r in db.query(
            f"""
            SELECT t.order_id, r.value FROM txn_reading r
            JOIN inventory_transaction t ON t.transaction_id = r.transaction_id
            WHERE r.analyte = 'ph' AND t.order_id IN ({marks}) AND t.voided = 0 AND t.is_reversal = 0
            ORDER BY t.transaction_id
            """,
            part, conn,
        ):
            if r["value"] and r["value"] not in found[r["order_id"]]:
                found[r["order_id"]].append(round(float(r["value"]), 2))
        for r in db.query(
            f"SELECT order_id, ph FROM qc WHERE active = 1 AND order_id IN ({marks}) ORDER BY test_date",
            part, conn,
        ):
            if r["ph"] and round(float(r["ph"]), 2) not in found[r["order_id"]]:
                found[r["order_id"]].append(round(float(r["ph"]), 2))
    return found


def caustic(filters: dict[str, Any], user: dict, conn=None) -> dict[str, Any]:
    """Caustic blended onto trailers, one row per order: gross, reversed, net
    and the pH the load went out at."""

    start, end = _period(filters)
    plants = _plants(filters, user, conn)
    m = materials(conn)
    rows = [
        r for r in _ledger([p["plant_id"] for p in plants], start, end, conn, include_reversed=True)
        if r["kind"] == "LOAD" and not r["is_reversal"] and r["f"] in m["caustic"] and r["t"] not in m["caustic"]
    ]
    per_order: dict[Any, dict[str, Any]] = {}
    for r in rows:
        key = r["order_id"] or f"txn-{r['transaction_id']}"
        o = per_order.setdefault(key, {
            "transaction_date": r["transaction_date"], "day": r["day"], "plant": r["plant_code"],
            "product_number": r["to_number"], "product": r["to_description"], "order_id": r["order_id"],
            "order_reference": r["order_reference"] or "", "gross": 0.0, "reversed": 0.0, "postings": 0,
        })
        o["gross"] += r["from_qty"]
        o["postings"] += 1
        if r["voided"]:
            o["reversed"] += r["from_qty"]
    ph = _order_ph([o["order_id"] for o in per_order.values()], conn)
    loads = []
    for o in per_order.values():
        readings = ph.get(o["order_id"], []) if o["order_id"] else []
        net = o["gross"] - o["reversed"]
        status = ("No reversal" if not o["reversed"] else "Fully reversed" if net <= 0.5 else "Reversed and reposted")
        loads.append({
            **o, "gross": _r(o["gross"]), "reversed": _r(o["reversed"]), "net": _r(net),
            "ph_readings": readings, "ph": readings[-1] if readings else None, "status": status,
        })
    loads.sort(key=lambda o: (o["plant"], o["product"] or "", o["transaction_date"]))

    def summarise(group: list[dict]) -> dict[str, Any]:
        gross = sum(o["gross"] for o in group)
        reversed_ = sum(o["reversed"] for o in group)
        tested = [o["ph"] for o in group if o["ph"] is not None]
        counted = [o for o in group if o["net"] > 0.5]
        return {
            "loads": len(counted), "gross": _r(gross), "reversed": _r(reversed_), "net": _r(gross - reversed_),
            "reversal_pct": _pct(reversed_, gross),
            "avg_per_load": _r((gross - reversed_) / len(counted)) if counted else None,
            "avg_ph": round(sum(tested) / len(tested), 2) if tested else None,
            "not_tested": sum(1 for o in counted if o["ph"] is None),
        }

    by_plant = defaultdict(list)
    by_plant_product = defaultdict(list)
    by_month = defaultdict(list)
    for o in loads:
        by_plant[o["plant"]].append(o)
        by_plant_product[(o["plant"], o["product"])].append(o)
        by_month[(o["day"][:7], o["plant"])].append(o)

    return {
        "report": "caustic",
        "start": start, "end": end, "plants": [p["code"] for p in plants],
        "total": summarise(loads),
        "by_plant": [{"plant": k, **summarise(v)} for k, v in sorted(by_plant.items())],
        "by_product": [{"plant": k[0], "product": k[1], **summarise(v)} for k, v in sorted(by_plant_product.items())],
        "by_month": [{"month": k[0], "plant": k[1], **summarise(v)} for k, v in sorted(by_month.items())],
        "loads": loads,
        "definitions": [
            {"name": "Gross", "definition": "Every caustic posting blended onto a trailer, including those later reversed."},
            {"name": "Reversed", "definition": "Caustic postings that were reversed (the legacy PROD-LOAD - REVERSAL rows)."},
            {"name": "Net", "definition": "Gross less reversed: the caustic that went out on the truck."},
            {"name": "pH", "definition": "The last pH on the load — the dosed reading if the load was blended here, "
                                         "otherwise the QC result. A legacy 0 means not tested and is not averaged."},
        ],
    }


# ------------------------------------------------------------------ reversals

def reversals(filters: dict[str, Any], user: dict, conn=None) -> dict[str, Any]:
    """How often a posting is undone, by plant and kind of transaction — the
    cost of a screen that makes people post before they are sure."""

    start, end = _period(filters)
    plants = _plants(filters, user, conn)
    rows = [r for r in _ledger([p["plant_id"] for p in plants], start, end, conn, include_reversed=True)
            if not r["is_reversal"]]

    def label(r: dict) -> str:
        return LEGACY_TYPE_NAMES.get(r["type_code"]) or r["type_name"] or r["type_code"]

    def summarise(group: list[dict]) -> dict[str, Any]:
        undone = [r for r in group if r["voided"]]
        return {
            "postings": len(group), "reversed": len(undone), "rate_pct": _pct(len(undone), len(group)),
            "lbs_reversed": _r(sum(max(r["from_qty"], r["to_qty"]) for r in undone)),
        }

    by_type = defaultdict(list)
    by_week = defaultdict(list)
    by_material = defaultdict(list)
    for r in rows:
        by_type[(r["plant_code"], label(r))].append(r)
        monday = date.fromisoformat(r["day"]) - timedelta(days=date.fromisoformat(r["day"]).weekday())
        by_week[monday.isoformat()].append(r)
        if r["voided"]:
            by_material[(r["plant_code"], label(r), r["from_description"] or r["to_description"] or "")].append(r)

    top = sorted(
        ({"plant": k[0], "type": k[1], "material": k[2], "reversed": len(v),
          "lbs_reversed": _r(sum(max(r["from_qty"], r["to_qty"]) for r in v))} for k, v in by_material.items()),
        key=lambda x: -x["lbs_reversed"],
    )[:15]
    return {
        "report": "reversals",
        "start": start, "end": end, "plants": [p["code"] for p in plants],
        "total": summarise(rows),
        "by_type": [{"plant": k[0], "type": k[1], **summarise(v)} for k, v in sorted(by_type.items())],
        "by_week": [{"week": k, **summarise(v)} for k, v in sorted(by_week.items())],
        "top_materials": top,
    }


# -------------------------------------------------------- legacy export layouts

#: The column layouts the existing workbooks read, so they keep working with
#: an export from here pasted over the old one.
LAYOUTS: dict[str, dict[str, Any]] = {
    "query": {
        "label": "PIMS QUERY Export (30 columns — the operations workbook)",
        "columns": [
            "Transaction_Date", "User_Date", "Full_TransType_Name", "Order_Id", "From_Material_Number",
            "From_Material_Description", "From_Location_Plant_Code", "From_Location_Number", "From_BOL_Number",
            "From_QC_Moisture", "From_QC_Temp", "From_QC_Ph", "From_QC_Spintest_Fallout", "From_QC_Sample_Number",
            "From_Qty", "To_Material_Number", "To_Material_Description", "To_Location_Plant_Code",
            "To_Location_Number", "To_BOL_Number", "To_QC_Moisture", "To_QC_Temp", "To_QC_Ph",
            "To_QC_Spintest_Fallout", "To_QC_Sample_Number", "To_Qty", "User_Name", "Transaction_Remarks",
            "Order_Reference", "Ship_Method",
        ],
    },
    "yields": {
        "label": "PIMS QUERY Export (28 columns — DM Yields)",
        "columns": [
            "Transaction_Date", "From_Location_Plant_Code", "To_Location_Plant_Code", "Order_Id", "Order_Reference",
            "Full_TransType_Name", "GP_Vendor_ID", "GP_Vendor_Name", "From_Material_Number",
            "From_Material_Description", "From_Location_Number", "From_BOL_Number", "From_QC_Moisture",
            "From_QC_Temp", "From_QC_Ph", "From_Qty", "To_Material_Number", "To_Material_Description",
            "To_Location_Number", "To_Qty", "To_BOL_Number", "To_QC_Test_Date", "To_QC_Moisture", "To_QC_Temp",
            "To_QC_Ph", "To_QC_Spintest_Fallout", "User_Name", "Transaction_Remarks",
        ],
    },
    "report": {
        "label": "PIMS Report Export (24 columns — caustic, MGR and pH)",
        "columns": [
            "Trans Id", "Parent", "Trans Date", "User Date", "Type", "Ord", "Plant", "From Mat", "From Mat Desc",
            "From Loc", "From BOL", "From Qty", "To Mat", "To Mat Desc", "To Loc", "To BOL", "To Qty", "User",
            "Dept", "From Loc Hr", "Emp Hr", "Seal", "Remarks", "Comments",
        ],
    },
}


def _legacy_location(number: Any, plant_code: str) -> Any:
    if not number:
        return None
    text = str(number)
    if text.upper().startswith(plant_code.upper() + "-"):
        text = text[len(plant_code) + 1:]
    return int(text) if text.isdigit() else text


def _stamp(value: Any, *, date_only: bool = False) -> str:
    text = str(value or "")
    if not text:
        return ""
    if date_only:
        return text[:10]
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text


def export(filters: dict[str, Any], user: dict, conn=None) -> dict[str, Any]:
    """The ledger in one of the legacy export layouts: From_Qty negative, To_Qty
    positive, a reversal as its parent's row with the signs turned round."""

    layout = str(filters.get("layout") or "query")
    if layout not in LAYOUTS:
        raise ValidationError("Unknown export layout.", fields={"layout": f"One of {', '.join(LAYOUTS)}."})
    start, end = _period(filters)
    plants = _plants(filters, user, conn)
    rows = _ledger([p["plant_id"] for p in plants], start, end, conn, include_reversed=True)
    parents = {r["transaction_id"]: r for r in rows}
    missing = [r["parent_transaction_id"] for r in rows if r["is_reversal"] and r["parent_transaction_id"]
               and r["parent_transaction_id"] not in parents]
    if missing:
        marks = ", ".join("?" for _ in missing)
        for p in db.query(LEDGER_SELECT + f" WHERE t.transaction_id IN ({marks})", missing, conn):
            parents[p["transaction_id"]] = {**p, "from_qty": float(p["from_qty"] or 0), "to_qty": float(p["to_qty"] or 0)}

    order_ids = sorted({r["order_id"] for r in rows if r["order_id"]})
    qc_by_bol: dict[tuple[int, str], dict] = {}
    qc_latest: dict[int, dict] = {}
    for chunk in range(0, len(order_ids), 500):
        part = order_ids[chunk:chunk + 500]
        marks = ", ".join("?" for _ in part)
        for q in db.query(f"SELECT * FROM qc WHERE active = 1 AND order_id IN ({marks}) ORDER BY test_date", part, conn):
            qc_by_bol[(q["order_id"], q["bol_number"] or "")] = q
            qc_latest[q["order_id"]] = q
    readings: dict[int, dict[str, float]] = defaultdict(dict)
    ids = [r["transaction_id"] for r in rows]
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        marks = ", ".join("?" for _ in part)
        for x in db.query(f"SELECT * FROM txn_reading WHERE transaction_id IN ({marks})", part, conn):
            readings[x["transaction_id"]][x["analyte"]] = x["value"]

    out = []
    for r in rows:
        name = LEGACY_TYPE_NAMES.get(r["type_code"]) or r["type_name"] or r["type_code"]
        side = r
        sign = 1.0
        if r["is_reversal"]:
            parent = parents.get(r["parent_transaction_id"])
            # A reversal written here swaps the endpoints; the legacy row
            # keeps its parent's and turns the signs round instead.
            if parent and r["from_material_id"] == parent["to_material_id"] and r["from_location_id"] == parent["to_location_id"] \
                    and r["from_qty"] >= 0:
                side = {**parent, "from_qty": r["to_qty"], "to_qty": r["from_qty"]}
                sign = -1.0
            elif parent:
                side = {**r, "from_qty": abs(r["from_qty"]), "to_qty": abs(r["to_qty"])}
                sign = -1.0
        from_qty = -side["from_qty"] * sign if side["from_material_id"] else 0.0
        to_qty = side["to_qty"] * sign if side["to_material_id"] else 0.0
        load_side = r["kind"] in ("LOAD", "SHIP")
        qc = (qc_by_bol.get((r["order_id"], side["to_bol"] or "")) or (qc_latest.get(r["order_id"]) if load_side else None)) or {}
        seen = readings.get(r["transaction_id"], {})
        to_ph = qc.get("ph") if qc.get("ph") is not None else seen.get("ph")
        to_moisture = qc.get("moisture") if qc.get("moisture") is not None else seen.get("moisture")
        to_spin = qc.get("spintest_fallout") if qc.get("spintest_fallout") is not None else seen.get("spintest")
        plant = r["plant_code"]
        record = {
            # query layout
            "Transaction_Date": _stamp(r["transaction_date"], date_only=layout == "yields"),
            "User_Date": _stamp(r["user_date"], date_only=True),
            "Full_TransType_Name": f"{name} - REVERSAL" if r["is_reversal"] else name,
            "Order_Id": r["order_id"],
            "From_Material_Number": _num(side["from_number"]),
            "From_Material_Description": side["from_description"],
            "From_Location_Plant_Code": plant if side["from_location_id"] else None,
            "From_Location_Number": _legacy_location(side["from_location"], plant),
            "From_BOL_Number": side["from_bol"] or None,
            "From_Qty": round(from_qty, 2),
            "To_Material_Number": _num(side["to_number"]),
            "To_Material_Description": side["to_description"],
            "To_Location_Plant_Code": plant if side["to_location_id"] else None,
            "To_Location_Number": _legacy_location(side["to_location"], plant),
            "To_BOL_Number": side["to_bol"] or None,
            "To_QC_Test_Date": _stamp(qc.get("test_date"), date_only=True) or None,
            "To_QC_Moisture": to_moisture, "To_QC_Temp": qc.get("temp"), "To_QC_Ph": to_ph,
            "To_QC_Spintest_Fallout": to_spin, "To_QC_Sample_Number": qc.get("sample_number") or None,
            "To_Qty": round(to_qty, 2),
            "User_Name": r["user_name"], "Transaction_Remarks": r["remarks"] or None,
            "Order_Reference": r["order_reference"] or None, "Ship_Method": r["ship_method"] or None,
            "GP_Vendor_ID": r["gp_vendorid"], "GP_Vendor_Name": r["vendor_name"],
            # report layout
            "Trans Id": r["transaction_id"], "Parent": r["parent_transaction_id"],
            "Trans Date": _stamp(r["transaction_date"]), "User Date": _stamp(r["user_date"], date_only=True),
            "Type": "REVERSAL" if r["is_reversal"] else name, "Ord": r["order_id"], "Plant": plant,
            "From Mat": _num(side["from_number"]), "From Mat Desc": side["from_description"],
            "From Loc": _legacy_location(side["from_location"], plant) or 0, "From BOL": side["from_bol"] or None,
            "From Qty": round(from_qty, 2), "To Mat": _num(side["to_number"]), "To Mat Desc": side["to_description"],
            "To Loc": _legacy_location(side["to_location"], plant), "To BOL": side["to_bol"] or None,
            "To Qty": round(to_qty, 2), "User": r["user_name"], "Dept": r["department_code"],
            "From Loc Hr": r["tank_hours"] or 0, "Emp Hr": r["employee_hours"] or 0,
            "Seal": qc.get("seal_number") or None, "Remarks": r["remarks"] or None,
            "Comments": f"Trailer: {r['trailer_number']}" if r["trailer_number"] else None,
        }
        out.append(record)
    columns = LAYOUTS[layout]["columns"]
    return {
        "report": "export", "layout": layout, "label": LAYOUTS[layout]["label"],
        "start": start, "end": end, "plants": [p["code"] for p in plants],
        "columns": columns, "rows": [{c: rec.get(c) for c in columns} for rec in out],
    }


# ------------------------------------------------------------------ dispatch

REPORTS = {"acid-yields": acid_yields, "caustic": caustic, "reversals": reversals, "export": export}


def run(name: str, filters: dict[str, Any], user: dict, conn=None) -> dict[str, Any]:
    if name not in REPORTS:
        raise ValidationError("Unknown report.", fields={"report": f"One of {', '.join(REPORTS)}."})
    return REPORTS[name](filters or {}, user, conn)


def to_csv(result: dict[str, Any]) -> str:
    """The table a report is mostly about, as the spreadsheet would hold it."""

    name = result["report"]
    if name == "export":
        return rows_to_csv(result["rows"], result["columns"])
    if name == "acid-yields":
        return rows_to_csv(result["daily"], ["date", "soap_processed", "acid_used", "oil_fp", "oil_final", "fpy_7d", "oy_7d"])
    if name == "caustic":
        rows = [{**o, "ph_readings": "; ".join(f"{v:g}" for v in o["ph_readings"])} for o in result["loads"]]
        return rows_to_csv(rows, ["transaction_date", "plant", "product", "order_id", "order_reference", "ph_readings",
                                  "gross", "reversed", "net", "status"])
    return rows_to_csv(result["by_type"], ["plant", "type", "postings", "reversed", "rate_pct", "lbs_reversed"])


def filename(result: dict[str, Any]) -> str:
    base = result["report"] if result["report"] != "export" else f"PIMS_{result['layout']}_export"
    return f"{base}_{result['start']}_{result['end']}.csv"
