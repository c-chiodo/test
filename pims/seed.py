"""Demo dataset.

The reference data here is real: the material numbers, product families and
QC limits are transcribed from the PIMS/LIMS limit sheets that shipped with the
legacy system (``PIMS_LIMS_Material_Limits.xlsx`` /
``PIMS_LIMS_Filters_by_Test.txt``), including the two rows the sheet itself
flags as unresolved. The *transactional* data — orders, movements, QC results,
users — is generated, deterministically, so the app has something to show.

Nothing in here runs against production. Seeding only happens when the target
database is empty.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import timedelta

from . import db
from .security import hash_password
from .util import utc_now, utc_now_iso

RNG_SEED = 20260817

# --------------------------------------------------------------- reference

COMPANIES = [(1, "Feed Energy Company"), (2, "Feed Energy Transport")]

# DM/SC/PJ appear in the legacy screens and limit sheets. LV appears only as a
# plant code in the limit sheet; its name here is a placeholder.
PLANTS = [
    (1, "DM", "DES MOINES"),
    (2, "SC", "SIOUX CITY"),
    (3, "PJ", "PLEASANT HILL"),
    (4, "LV", "LV PLANT"),
]

DEPARTMENTS = [
    (1, "RECV", "Receiving"),
    (2, "LOAD", "Loadout"),
    (3, "BLND", "Blending"),
    (4, "PROC", "Processing"),
    (5, "QC", "Quality Control"),
]

ORDER_TYPES = [
    (1, "SO", "Sales Order"),
    (2, "WO", "Work Order"),
    (3, "PO", "Purchase Order"),
    (4, "TO", "Transfer Order"),
]

STATUSES = [
    (1, "Open", "Created, not yet started", 0),
    (2, "In Process", "Production or loading under way", 0),
    (3, "Complete", "Quantity fulfilled, awaiting close", 0),
    (4, "Closed", "Closed — no further activity", 1),
    (5, "Cancelled", "Cancelled before fulfilment", 1),
]

MATERIAL_TYPES = [
    (1, "Finished Product"),
    (2, "Raw Material"),
    (3, "Blend Component"),
    (4, "By-product"),
]

LOCATION_TYPES = [
    (1, "Tank"),
    (2, "Receiving"),
    (3, "Loadout"),
    (4, "Blend"),
    (5, "Trailer"),
]

TRANSACTION_TYPES = [
    (1, "RECEIVE", "Receipt into a location"),
    (2, "PRODUCE", "Consume input(s), produce output"),
    (3, "MOVE", "Move inventory between locations"),
    (4, "LOAD", "Load a trailer from a location"),
    (5, "SHIP", "Ship a loaded trailer"),
    (6, "SHRINK", "Record shrinkage / loss"),
    (7, "ADJUST", "Inventory adjustment"),
]

# ------------------------------------------------------ specs (real data)
#
# analyte -> (min, max, needs_review, note)
FAMILY_SPECS: dict[str, dict[str, tuple[float | None, float | None, int, str]]] = {
    "AV4000": {
        "ffa": (None, 75, 0, ""),
        "tfa": (90, 100, 0, ""),
        "moisture": (0, 2, 0, ""),
        "impurities": (None, 1, 0, ""),
        "unsaponifiables": (None, 2, 0, ""),
    },
    "Pure Veg 4000": {
        "ffa": (None, 75, 0, ""),
        "tfa": (90, 100, 0, ""),
        "moisture": (0, 2, 0, ""),
        "impurities": (None, 1, 0, ""),
        "unsaponifiables": (None, 2, 0, ""),
    },
    "HC3800": {
        "ffa": (None, 55, 0, ""),
        "tfa": (90, 100, 0, ""),
        "moisture": (0, 2, 0, ""),
        "impurities": (None, 1, 0, ""),
        "unsaponifiables": (None, 2, 0, ""),
        "linoleic": (37, 55, 0, ""),
        "stearic": (0, 8, 0, ""),
    },
    "HC3900": {
        "ffa": (None, 65, 1, "QC sheet says 65, product label says 55 — confirm"),
        "tfa": (90, 100, 0, ""),
        "moisture": (0, 2, 0, ""),
        "impurities": (None, 1, 0, ""),
        "unsaponifiables": (None, 2, 0, ""),
    },
    "HCFC": {
        "ffa": (None, 65, 0, ""),
        "tfa": (90, 100, 0, ""),
        "moisture": (0, 2, 0, ""),
        "impurities": (None, 1, 0, ""),
        "unsaponifiables": (None, 2, 0, ""),
    },
    "Live Tallow": {
        "ffa": (None, 30, 0, ""),
        "tfa": (90, 100, 0, ""),
        "moisture": (0, 2, 0, ""),
        "impurities": (None, 1, 0, ""),
        "unsaponifiables": (None, 2, 0, ""),
    },
    "Live Plus": {
        "ffa": (None, 35, 1, "QC sheet says 35, product label says 30 — confirm"),
        "tfa": (90, 100, 0, ""),
        "moisture": (0, 2, 0, ""),
        "impurities": (None, 1, 0, ""),
        "unsaponifiables": (None, 2, 0, ""),
    },
    "Live70": {
        "ffa": (None, 55, 0, ""),
        "tfa": (90, 100, 0, ""),
        "moisture": (0, 2, 0, ""),
        "impurities": (None, 1, 0, ""),
        "unsaponifiables": (None, 2, 0, ""),
    },
    "ProduceR2/BuildR2": {
        "ffa": (None, 75, 0, ""),
        "tfa": (90, 100, 0, ""),
        "moisture": (0, 2, 0, ""),
        "impurities": (None, 1, 0, ""),
        "unsaponifiables": (None, 2, 0, ""),
    },
    "EZ Veg 3800": {
        "ffa": (None, 20, 0, ""),
        "moisture": (0, 1, 0, ""),
        "impurities": (None, 0.5, 0, ""),
        "unsaponifiables": (None, 2, 0, ""),
    },
    "MGR": {
        "tfa": (16, 18, 0, ""),
        "moisture": (30, 60, 0, ""),
        "ph": (2, 4, 0, ""),
    },
    "Cattle Blend": {
        "tfa": (12, 17, 0, ""),
        "moisture": (35, 65, 0, ""),
        "ph": (2, 4, 0, ""),
    },
    # Families with no published limit sheet rows.
    "Soaps": {"ffa": (None, 90, 0, "")},
    "Water": {"ph": (6, 9, 0, "")},
    "Raw Material": {},
}

# Which analytes each family is actually tested for. This table is the fix for
# FE-2026-002: QC validation asks the material what it runs, instead of
# inferring it from the order type.
FAMILY_TESTS: dict[str, list[str]] = {
    "AV4000": ["moisture", "temp", "spintest", "ffa", "tfa", "impurities", "unsaponifiables"],
    "Pure Veg 4000": ["moisture", "temp", "spintest", "ffa", "tfa", "impurities", "unsaponifiables"],
    "HC3800": ["moisture", "temp", "spintest", "ffa", "tfa", "impurities", "unsaponifiables", "linoleic", "stearic"],
    "HC3900": ["moisture", "temp", "spintest", "ffa", "tfa", "impurities", "unsaponifiables"],
    "HCFC": ["moisture", "temp", "spintest", "ffa", "tfa", "impurities", "unsaponifiables"],
    "Live Tallow": ["moisture", "temp", "spintest", "ffa", "tfa", "impurities", "unsaponifiables"],
    "Live Plus": ["moisture", "temp", "spintest", "ffa", "tfa", "impurities", "unsaponifiables"],
    "Live70": ["moisture", "temp", "spintest", "ffa", "tfa", "impurities", "unsaponifiables"],
    "ProduceR2/BuildR2": ["moisture", "temp", "spintest", "ffa", "tfa", "impurities", "unsaponifiables"],
    "EZ Veg 3800": ["moisture", "temp", "ffa", "impurities", "unsaponifiables"],
    # Cattle and MGR run moisture/pH/TFA — never temperature or spintest.
    "MGR": ["moisture", "ph", "tfa"],
    "Cattle Blend": ["moisture", "ph", "tfa"],
    # Soaps and water are the other products the legacy screen wrongly
    # demanded moisture/temperature/spintest for on every purchase order.
    "Soaps": ["ffa", "tfa"],
    "Water": ["ph"],
    "Raw Material": [],
}

# (number, description, family, material_type_id, plant codes)
MATERIALS = [
    ("00001", "Acid", "Raw Material", 2, "DM SC PJ LV"),
    ("00010", "Process Water", "Water", 2, "DM SC PJ LV"),
    ("02001", "Acidulated Soapstock", "Soaps", 2, "DM SC"),
    ("02005", "Soapstock - Veg", "Soaps", 2, "DM SC"),
    ("01020", "FE Cattle Blend - 2.5", "Cattle Blend", 1, "DM PJ SC"),
    ("01021", "FE Cattle Blend - 3.5", "Cattle Blend", 1, "DM PJ SC"),
    ("01031", "MGR veg - 2.5", "MGR", 1, "DM PJ SC"),
    ("05001", "AV4000", "AV4000", 1, "DM PJ SC"),
    ("05003", "HC3800", "HC3800", 1, "DM SC"),
    ("05004", "HC3900", "HC3900", 1, "DM SC"),
    ("05005", "Live Tallow", "Live Tallow", 1, "DM SC"),
    ("05006", "LIVE Plus", "Live Plus", 1, "DM SC"),
    ("05010", "All Veg HCFC", "HCFC", 1, "DM SC"),
    ("05012", "Hypercal FC", "HCFC", 1, "DM SC"),
    ("05017", "Winfield Acidulated Soybean Soapstock", "AV4000", 1, "DM SC"),
    ("05029", "AV4000 - AOX .025%", "AV4000", 1, "DM PJ SC"),
    ("05030", "HC3800 - AOX .025%", "HC3800", 1, "DM SC"),
    ("05031", "HYPERCAL FC - AOX .025%", "HCFC", 1, "DM SC"),
    ("05032", "LIVE Tallow AOX", "Live Tallow", 1, "DM SC"),
    ("05033", "Live Plus - AOX .025%", "Live Plus", 1, "DM SC"),
    ("05048", "LIVE Plus - HAG-0", "Live Plus", 1, "DM SC"),
    ("05049", "LIVE BLEND", "Live Plus", 1, "DM SC"),
    ("05054", "ProduceR2", "ProduceR2/BuildR2", 1, "DM SC"),
    ("05057", "BuildR2", "ProduceR2/BuildR2", 1, "DM SC"),
    ("05065", "AV4000 CO-0 LTV-0", "AV4000", 1, "DM PJ SC"),
    ("05068", "HC3900 CO-0", "HC3900", 1, "DM SC"),
    ("05074", "HCFC-Tallow 60", "HCFC", 1, "DM SC"),
    ("05075", "HCFC SPECIAL SWINE", "HCFC", 1, "DM SC"),
    ("05076", "AV4000 CO-0 LTV-0 - AOX", "AV4000", 1, "DM PJ SC"),
    ("05077", "HC3800 NT", "HC3800", 1, "DM SC"),
    ("05078", "Hypercal FC - Veg Tallow", "HCFC", 1, "DM SC"),
    ("05079", "AV4000 Plus", "AV4000", 1, "DM SC"),
    ("05080", "AV4000 CO-0 LTV-0 Plus", "AV4000", 1, "DM SC"),
    ("05081", "HC3800 XL - AOX .025%", "HC3800", 1, "DM SC"),
    ("05082", "PURE GOLD 4000", "Pure Veg 4000", 1, "PJ"),
    ("05083", "AV4000 Plus XL", "AV4000", 1, "DM SC"),
    ("05086", "Live70", "Live70", 1, "DM SC"),
    ("05691", "EZ Veg 3800", "EZ Veg 3800", 1, "LV"),
    ("05692", "EZ Veg 3800 XL - AOX", "EZ Veg 3800", 1, "LV"),
    ("05693", "Pure Veg 4000", "Pure Veg 4000", 1, "LV"),
    ("05694", "Pure Veg 4000 XL", "Pure Veg 4000", 1, "LV"),
    ("05695", "Pure Veg 4000 - AOX", "Pure Veg 4000", 1, "LV"),
]

CUSTOMERS = [
    ("FEC-0394", "AGSTATE - SHELDON (NORTH)", "Sheldon", "IA"),
    ("FEC-0678", "HILLSHIRE BRANDS - STORM LAKE", "Storm Lake", "IA"),
    ("FEC-1359", "IOWA STATE UNIVERSITY - FEEDMILL", "Ames", "IA"),
    ("FEC-3249", "KSX TRANSPORTATION, LLC", "Des Moines", "IA"),
    ("FEC-3747", "NEW COOP - JEFFERSON", "Jefferson", "IA"),
    ("FEC-3721", "NEW COOP - ROWAN FEED MILL", "Rowan", "IA"),
    ("FEC-0961", "QUALITY LIQUID FEEDS, INC. - DALHART", "Dalhart", "TX"),
    ("FEC-1180", "CENTRAL VALLEY AG - OAKLAND", "Oakland", "NE"),
    ("FEC-2260", "FARMERS COOP - MARCUS", "Marcus", "IA"),
]

VENDORS = [
    ("V-1002", "AG PROCESSING INC", "Sergeant Bluff", "IA"),
    ("V-1044", "WINFIELD SOLUTIONS", "Des Moines", "IA"),
    ("V-1180", "DARLING INGREDIENTS", "Sioux City", "IA"),
    ("V-1210", "MIDWEST RENDERING CO", "Council Bluffs", "IA"),
    ("V-1355", "GREAT RIVER SOY", "Muscatine", "IA"),
]

QA_QUESTIONS = [
    (1, "Trailer interior clean, dry and free of odor?", "yesno", 10),
    (2, "Previous load compatible with this product?", "yesno", 20),
    (3, "Seals applied and recorded on the BOL?", "yesno", 30),
    (4, "Hoses capped and wash ticket present?", "yesno", 40),
    (5, "Driver provided wash ticket number", "text", 50),
    (6, "Product temperature at load (°F)", "number", 60),
]

TEST_POINTS = [
    (1, 1, "Blend tank sample", "Sample drawn from the blend tank during mix"),
    (2, 1, "Loadout line", "In-line sample at the loadout arm"),
    (3, 2, "Blend tank sample", "Sample drawn from the blend tank during mix"),
    (4, 2, "Loadout line", "In-line sample at the loadout arm"),
    (5, 3, "Blend tank sample", "Sample drawn from the blend tank during mix"),
]

USERS = [
    ("cchiodo", "Chris Chiodo", "cchiodo@feedenergy.com", "admin", "DM SC PJ LV"),
    ("jmartin", "Jamie Martin", "jmartin@feedenergy.com", "supervisor", "DM SC"),
    ("rprice", "Robin Price", "rprice@feedenergy.com", "qc", "DM SC PJ"),
    ("toperator", "Terry Olson", "tolson@feedenergy.com", "operator", "DM"),
]

DEMO_PASSWORD = "pims-demo"

SYSTEM_SETTINGS = [
    ("bol.prefix", "001-", "Prefix applied to generated BOL numbers"),
    ("shrinkage.warn_pct", "0.5", "Warn when shrinkage exceeds this % of the run"),
    ("qc.require_sample_number", "true", "Require a sample number on finished-product QC"),
    ("lims.refresh_cron", "0 */2 * * *", "Expected LIMS projection refresh cadence"),
]


def seed_all(conn: sqlite3.Connection | None = None) -> None:
    """Populate an empty database. Idempotent by virtue of only running once."""

    conn = conn or db.get_connection()
    rng = random.Random(RNG_SEED)
    with db.transaction(conn):
        _seed_reference(conn)
        material_ids = _seed_materials(conn)
        location_ids = _seed_locations(conn)
        _seed_partners(conn)
        user_ids = _seed_users(conn)
        _seed_activity(conn, rng, material_ids, location_ids, user_ids)


def _seed_reference(conn) -> None:
    for cid, name in COMPANIES:
        db.insert("company", {"company_id": cid, "name": name}, conn)
    for pid, code, name in PLANTS:
        db.insert("plant", {"plant_id": pid, "code": code, "name": name}, conn)
    for did, code, desc in DEPARTMENTS:
        db.insert(
            "department", {"department_id": did, "code": code, "description": desc}, conn
        )
    for pid, _c, _n in PLANTS:
        for did, _c2, _d2 in DEPARTMENTS:
            db.insert("plant_department", {"plant_id": pid, "department_id": did}, conn)
    for oid, code, desc in ORDER_TYPES:
        db.insert(
            "order_type",
            {"order_type_id": oid, "code": code, "description": desc},
            conn,
        )
    for sid, name, desc, terminal in STATUSES:
        db.insert(
            "status",
            {
                "status_id": sid,
                "name": name,
                "description": desc,
                "is_terminal": terminal,
            },
            conn,
        )
    for mtid, name in MATERIAL_TYPES:
        db.insert("material_type", {"material_type_id": mtid, "name": name}, conn)
    for ltid, name in LOCATION_TYPES:
        db.insert("location_type", {"location_type_id": ltid, "name": name}, conn)
    for ttid, code, desc in TRANSACTION_TYPES:
        db.insert(
            "transaction_type",
            {"transaction_type_id": ttid, "code": code, "description": desc},
            conn,
        )
    for tpid, plant_id, name, desc in TEST_POINTS:
        db.insert(
            "test_point",
            {
                "test_point_id": tpid,
                "plant_id": plant_id,
                "name": name,
                "description": desc,
            },
            conn,
        )
    for qid, question, answer_type, order_ in QA_QUESTIONS:
        db.insert(
            "qa_question",
            {
                "question_id": qid,
                "question": question,
                "answer_type": answer_type,
                "sort_order": order_,
            },
            conn,
        )
    for key, value, desc in SYSTEM_SETTINGS:
        db.insert(
            "system_setting", {"key": key, "value": value, "description": desc}, conn
        )


def _seed_materials(conn) -> dict[str, int]:
    plant_by_code = {code: pid for pid, code, _ in PLANTS}
    ids: dict[str, int] = {}
    for idx, (number, desc, family, mtype, plants) in enumerate(MATERIALS, start=1):
        db.insert(
            "material",
            {
                "material_id": idx,
                "number": number,
                "description": desc,
                "material_type_id": mtype,
                "family": family,
                "density": 7.6 if number.startswith("05") else 8.34,
            },
            conn,
        )
        ids[number] = idx
        for code in plants.split():
            db.insert(
                "material_plant",
                {"material_id": idx, "plant_id": plant_by_code[code]},
                conn,
            )
        for analyte in FAMILY_TESTS.get(family, []):
            db.insert(
                "material_test",
                {"material_id": idx, "analyte": analyte, "required": 1},
                conn,
            )
        for analyte, (lo, hi, review, note) in FAMILY_SPECS.get(family, {}).items():
            db.insert(
                "material_spec",
                {
                    "material_id": idx,
                    "analyte": analyte,
                    "min_value": lo,
                    "max_value": hi,
                    "source": "PIMS/LIMS limit sheet",
                    "note": note,
                    "needs_review": review,
                },
                conn,
            )
    return ids


def _seed_locations(conn) -> dict[int, list[int]]:
    """Create tanks and dock locations per plant; return plant -> tank ids."""

    by_plant: dict[int, list[int]] = {}
    loc_id = 1
    for plant_id, code, _name in PLANTS:
        tanks: list[int] = []
        for n in range(1, 9):
            db.insert(
                "location",
                {
                    "location_id": loc_id,
                    "plant_id": plant_id,
                    "number": f"{code}-T{100 + n}",
                    "description": f"Storage tank {100 + n}",
                    "location_type_id": 1,
                    "company_id": 1,
                    "max_capacity": 250_000,
                    "bol_required": 0,
                },
                conn,
            )
            tanks.append(loc_id)
            loc_id += 1
        for number, desc, ltype, cap, bol in (
            ("RECV-RAIL", "Rail receiving", 2, None, 1),
            ("RECV-TRUCK", "Truck receiving", 2, None, 1),
            ("BLEND-1", "Blend tank 1", 4, 120_000, 0),
            ("LOADOUT", "Loadout rack", 3, None, 1),
            ("TRAILER", "Trailer staging", 5, None, 1),
        ):
            db.insert(
                "location",
                {
                    "location_id": loc_id,
                    "plant_id": plant_id,
                    "number": f"{code}-{number}",
                    "description": desc,
                    "location_type_id": ltype,
                    "company_id": 1,
                    "max_capacity": cap,
                    "bol_required": bol,
                },
                conn,
            )
            if number == "BLEND-1":
                by_plant.setdefault(plant_id, [])
            loc_id += 1
        by_plant[plant_id] = tanks
    # Default receiving/loadout locations per order type and plant.
    for plant_id, code, _ in PLANTS:
        recv = db.scalar(
            "SELECT location_id FROM location WHERE plant_id = ? AND number = ?",
            (plant_id, f"{code}-RECV-TRUCK"),
            conn,
        )
        loadout = db.scalar(
            "SELECT location_id FROM location WHERE plant_id = ? AND number = ?",
            (plant_id, f"{code}-LOADOUT"),
            conn,
        )
        db.insert(
            "location_default",
            {"order_type_id": 3, "plant_id": plant_id, "location_id": recv},
            conn,
        )
        db.insert(
            "location_default",
            {"order_type_id": 1, "plant_id": plant_id, "location_id": loadout},
            conn,
        )
    return by_plant


def _seed_partners(conn) -> None:
    for idx, (gp, name, city, state) in enumerate(CUSTOMERS, start=1):
        db.insert(
            "customer",
            {
                "customer_id": idx,
                "gp_custnmbr": gp,
                "name": name,
                "city": city,
                "state": state,
            },
            conn,
        )
    for idx, (gp, name, city, state) in enumerate(VENDORS, start=1):
        db.insert(
            "vendor",
            {
                "vendor_id": idx,
                "gp_vendorid": gp,
                "name": name,
                "city": city,
                "state": state,
            },
            conn,
        )
    for cid, requirement, production, carrier in (
        (2, "Kosher-compatible trailer wash required before loading", 1, 1),
        (2, "Certificate of analysis must accompany every load", 0, 0),
        (3, "Load between 06:00 and 14:00 only", 0, 1),
        (7, "Seal numbers must be photographed at the rack", 0, 1),
    ):
        db.insert(
            "partner_requirement",
            {
                "party_type": "customer",
                "party_id": cid,
                "requirement": requirement,
                "is_production": production,
                "is_carrier": carrier,
            },
            conn,
        )
    for vid, requirement in ((1, "Inbound rail cars require seal verification"),):
        db.insert(
            "partner_requirement",
            {"party_type": "vendor", "party_id": vid, "requirement": requirement},
            conn,
        )
    for cid, name in enumerate(
        ["KSX TRANSPORTATION", "HEARTLAND TANK LINES", "PRAIRIE CARRIERS"], start=1
    ):
        db.insert("carrier", {"carrier_id": cid, "name": name}, conn)


def _seed_users(conn) -> dict[str, int]:
    plant_by_code = {code: pid for pid, code, _ in PLANTS}
    ids: dict[str, int] = {}
    pw = hash_password(DEMO_PASSWORD)
    for username, full_name, email, role, plants in USERS:
        uid = db.insert(
            "app_user",
            {
                "username": username,
                "full_name": full_name,
                "email": email,
                "role": role,
                "password_hash": pw,
                "date_added": utc_now_iso(),
            },
            conn,
        )
        ids[username] = uid
        for code in plants.split():
            db.insert(
                "user_plant_access",
                {"user_id": uid, "plant_id": plant_by_code[code]},
                conn,
            )
    return ids


def _seed_activity(conn, rng, material_ids, tanks_by_plant, user_ids) -> None:
    """Generate ~90 days of orders, movements and QC results.

    Balances are tracked as the data is generated so the demo ledger is
    internally consistent: nothing is ever taken from a tank that does not hold
    it, and nothing is put into a tank that cannot fit it. A seeded dataset
    that violates its own rules would make every support screen cry wolf.
    """

    now = utc_now()
    finished = [
        n for n, d, f, t, p in MATERIALS if t == 1 and f not in {"Raw Material", "Water"}
    ]
    inbound = ["00001", "00010", "02001", "02005"]
    tank_capacity = 250_000
    order_id = 328_500

    # (location_id, material_id) -> lbs on hand, mirrored as rows are written.
    balances: dict[tuple[int, int], float] = {}

    def add(location_id: int, material_id: int, qty: float) -> None:
        balances[(location_id, material_id)] = balances.get((location_id, material_id), 0.0) + qty

    def total_at(location_id: int) -> float:
        return sum(q for (loc, _m), q in balances.items() if loc == location_id)

    def stock_at(location_id: int, material_id: int) -> float:
        return balances.get((location_id, material_id), 0.0)

    def holdings(tanks: list[int], minimum: float) -> list[tuple[int, int, float]]:
        return [
            (loc, mat, qty)
            for (loc, mat), qty in balances.items()
            if loc in tanks and qty >= minimum
        ]

    def loc(plant_id: int, suffix: str) -> int:
        code = next(c for pid, c, _ in PLANTS if pid == plant_id)
        return db.scalar(
            "SELECT location_id FROM location WHERE plant_id = ? AND number = ?",
            (plant_id, f"{code}-{suffix}"),
            conn,
        )

    def write(row: dict) -> int:
        txn_id = db.insert("inventory_transaction", row, conn)
        if row.get("from_location_id") and row.get("from_qty"):
            add(row["from_location_id"], row["from_material_id"], -row["from_qty"])
        if row.get("to_location_id") and row.get("to_qty"):
            add(row["to_location_id"], row["to_material_id"], row["to_qty"])
        return txn_id

    # Opening balances: each tank starts with one product.
    for plant_id, tanks in tanks_by_plant.items():
        for tank in tanks:
            number = rng.choice(finished)
            when = (now - timedelta(days=95)).replace(microsecond=0).isoformat()
            write(
                {
                    "transaction_type_id": 7,
                    "plant_id": plant_id,
                    "transaction_date": when,
                    "user_date": when[:10],
                    "user_id": user_ids["cchiodo"],
                    "to_material_id": material_ids[number],
                    "to_location_id": tank,
                    "to_qty": float(rng.randrange(60_000, 180_000, 500)),
                    "remarks": "Opening balance (demo seed)",
                }
            )

    for day in range(90, -1, -1):
        stamp = now - timedelta(days=day, hours=rng.randrange(6, 18))
        if stamp > now:
            continue
        iso = stamp.replace(microsecond=0).isoformat()
        for plant_id, tanks in tanks_by_plant.items():
            if plant_id == 4 and day % 3:          # LV runs lighter
                continue
            for _ in range(rng.choice([1, 1, 2, 2, 3])):
                kind = rng.choices([1, 2, 3], weights=[6, 3, 2])[0]
                username = rng.choice(list(user_ids))
                user_id = user_ids[username]
                department_id = {1: 2, 2: 3, 3: 1}[kind]

                if kind == 3:
                    # Purchase order: receive into a tank with room.
                    number = rng.choice(inbound)
                    material_id = material_ids[number]
                    qty = float(rng.randrange(20_000, 48_000, 500))
                    candidates = [t for t in tanks if total_at(t) + qty <= tank_capacity]
                    if not candidates:
                        continue
                    tank = rng.choice(candidates)
                elif kind == 2:
                    # Work order: consume from a tank that holds enough.
                    sources = holdings(tanks, 8_000)
                    if not sources:
                        continue
                    source, source_material, available = rng.choice(sources)
                    qty = float(rng.randrange(4_000, int(min(available, 40_000)), 500))
                    number = rng.choice(finished)
                    material_id = material_ids[number]
                    targets = [
                        t for t in tanks if t != source and total_at(t) + qty <= tank_capacity
                    ]
                    if not targets:
                        continue
                    tank = rng.choice(targets)
                else:
                    # Sales order: ship finished product a tank actually holds.
                    saleable = {material_ids[n] for n in finished}
                    sources = [
                        h for h in holdings(tanks, 4_000) if h[1] in saleable
                    ]
                    if not sources:
                        continue
                    tank, material_id, available = rng.choice(sources)
                    number = next(n for n, i in material_ids.items() if i == material_id)
                    ceiling = max(int(min(available, 48_000)), 4_500)
                    qty = float(rng.randrange(4_000, ceiling, 500))

                order_id += 1
                closed = day > 5 and rng.random() < 0.85
                db.insert(
                    "order",
                    {
                        "order_id": order_id,
                        "order_type_id": kind,
                        "order_date": iso[:10],
                        "due_date": (stamp + timedelta(days=rng.randrange(0, 6))).date().isoformat(),
                        "order_reference": f"REF{rng.randrange(10_000, 99_999)}",
                        "company_id": 1,
                        "plant_id": plant_id,
                        "department_id": department_id,
                        "blend_serial_number": (
                            f"{rng.randrange(100_000, 999_999)}" if kind == 2 else ""
                        ),
                        "vendor_id": rng.randrange(1, len(VENDORS) + 1) if kind == 3 else None,
                        "customer_id": (
                            rng.randrange(1, len(CUSTOMERS) + 1) if kind == 1 else None
                        ),
                        "material_one_id": material_id,
                        "material_one_quantity": qty,
                        "ship_method": "Bulk trailer" if kind == 1 else "",
                        "trailer_number": str(rng.randrange(100, 999)) if kind == 1 else "",
                        "comments": "",
                        "status_id": 4 if closed else rng.choice([1, 2, 2, 3]),
                        "date_added": iso,
                        "added_by": username,
                    },
                    conn,
                )

                base = {
                    "order_id": order_id,
                    "plant_id": plant_id,
                    "department_id": department_id,
                    "transaction_date": iso,
                    "user_date": iso[:10],
                    "user_id": user_id,
                    "remarks": "",
                }

                if kind == 3:
                    write(
                        {
                            **base,
                            "transaction_type_id": 1,
                            "to_material_id": material_id,
                            "to_location_id": tank,
                            "to_qty": qty,
                            "to_bol": f"001-{rng.randrange(111_000, 111_999)}-1",
                        }
                    )
                elif kind == 2:
                    write(
                        {
                            **base,
                            "transaction_type_id": 2,
                            "from_material_id": source_material,
                            "from_location_id": source,
                            "from_qty": qty,
                            "to_material_id": material_id,
                            "to_location_id": tank,
                            "to_qty": round(qty * rng.uniform(0.985, 0.999), 1),
                            "tank_hours": round(rng.uniform(1.0, 6.0), 1),
                            "employee_hours": round(rng.uniform(0.5, 3.0), 1),
                        }
                    )
                else:
                    trailer_loc = loc(plant_id, "TRAILER")
                    trailer = str(rng.randrange(100, 999))
                    txn = write(
                        {
                            **base,
                            "transaction_type_id": 4,
                            "from_material_id": material_id,
                            "from_location_id": tank,
                            "from_qty": qty,
                            "to_material_id": material_id,
                            "to_location_id": trailer_loc,
                            "to_qty": qty,
                            "trailer_number": trailer,
                            "to_bol": f"001-{rng.randrange(111_000, 111_999)}-1",
                        }
                    )
                    shipped = closed or rng.random() < 0.6
                    db.insert(
                        "pending_shipment",
                        {
                            "order_id": order_id,
                            "transaction_id": txn,
                            "trailer_number": trailer,
                            "quantity": qty,
                            "shipped": 1 if shipped else 0,
                        },
                        conn,
                    )
                    if shipped:
                        ship_time = (stamp + timedelta(hours=2)).replace(microsecond=0)
                        write(
                            {
                                **base,
                                "transaction_type_id": 5,
                                "parent_transaction_id": txn,
                                "transaction_date": ship_time.isoformat(),
                                "user_date": ship_time.date().isoformat(),
                                "from_material_id": material_id,
                                "from_location_id": trailer_loc,
                                "from_qty": qty,
                                "trailer_number": trailer,
                                "remarks": "Shipped",
                            }
                        )

                # Occasional shrinkage, only where there is product to lose.
                if rng.random() < 0.05:
                    loss = round(qty * rng.uniform(0.001, 0.006), 1)
                    if stock_at(tank, material_id) >= loss:
                        write(
                            {
                                **base,
                                "transaction_type_id": 6,
                                "from_material_id": material_id,
                                "from_location_id": tank,
                                "from_qty": loss,
                                "remarks": "Tank heel / line loss",
                            }
                        )

                if rng.random() < 0.75:
                    _seed_qc(
                        conn,
                        rng,
                        order_id=order_id,
                        plant_id=plant_id,
                        material_number=number,
                        material_id=material_id,
                        stamp=stamp,
                        username=username,
                    )


def _seed_qc(conn, rng, *, order_id, plant_id, material_number, material_id, stamp, username) -> None:
    family = next(f for n, d, f, t, p in MATERIALS if n == material_number)
    tests = set(FAMILY_TESTS.get(family, []))
    specs = FAMILY_SPECS.get(family, {})
    plant_code = next(c for pid, c, _ in PLANTS if pid == plant_id)
    sample = (
        f"{plant_code}C{rng.randrange(1000, 9999)}D"
        f"{stamp.strftime('%y%m%d')}Q{rng.randrange(300_000, 399_999)}"
    )

    def draw(analyte: str) -> float | None:
        if analyte not in tests:
            return None
        lo, hi = specs.get(analyte, (None, None, 0, ""))[:2]
        if lo is None and hi is None:
            return round(rng.uniform(1, 10), 2)
        lo_v = lo if lo is not None else max(0.0, (hi or 1) * 0.2)
        hi_v = hi if hi is not None else lo_v * 1.5
        span = hi_v - lo_v
        # ~8% of results land outside spec so the flagging is visible.
        if rng.random() < 0.08:
            value = hi_v + span * rng.uniform(0.02, 0.15) if rng.random() < 0.7 else lo_v - span * rng.uniform(0.02, 0.1)
        else:
            value = rng.uniform(lo_v + span * 0.15, hi_v - span * 0.1)
        return round(max(value, 0.0), 2)

    iso = stamp.replace(microsecond=0).isoformat()
    qc_id = db.insert(
        "qc",
        {
            "order_id": order_id,
            "bol_number": f"001-{rng.randrange(111_000, 111_999)}-1",
            "test_date": iso[:10],
            "performed_by": username,
            "moisture": draw("moisture"),
            "temp": round(rng.uniform(95, 140), 1) if "temp" in tests else None,
            "ph": draw("ph"),
            "ffa": draw("ffa"),
            "tfa": draw("tfa"),
            "spintest_fallout": (
                round(rng.uniform(0.1, 1.5), 2) if "spintest" in tests else None
            ),
            "flash_pf": "P" if "temp" in tests else None,
            "steam_on": 1 if rng.random() < 0.4 else 0,
            "seal_number": f"{rng.randrange(7000, 7999)}/{rng.randrange(7000, 7999)}",
            "last_material_hauled": rng.choice(
                ["Soybean oil", "Tallow", "Corn oil", "Empty", "Same product"]
            ),
            "sample_number": sample,
            "comments": "",
            "date_added": iso,
            "added_by": username,
        },
        conn,
    )

    # LIMS projection rows for this sample, as the matrix feature would show.
    retrieved = utc_now_iso()
    for test_code, component, value in (
        ("FFA (NIR)", "R-FFA", draw("ffa")),
        ("TFA (NIR)", "%TFA 1", draw("tfa")),
        ("MOISTURE", "%MOIST", draw("moisture")),
    ):
        if value is None:
            continue
        db.insert(
            "lims_result",
            {
                "sample_code": sample,
                "test_code": test_code,
                "component": component,
                "value_text": f"{value}",
                "value_num": value,
                "include_in_report": 1,
                "current_version": 1,
                "sampled_at": iso,
                "source": "XLIMSFEEDGROUP",
                "retrieved_at": retrieved,
            },
            conn,
        )

    if rng.random() < 0.35:
        header_id = db.insert(
            "qa_header",
            {
                "order_id": order_id,
                "plant_id": plant_id,
                "qc_id": qc_id,
                "trailer_number": str(rng.randrange(100, 999)),
                "trailer_load_time": iso,
                "date_added": iso,
                "added_by": username,
            },
            conn,
        )
        for qid, _q, answer_type, _o in QA_QUESTIONS:
            if answer_type == "yesno":
                response = "Yes" if rng.random() < 0.94 else "No"
            elif answer_type == "number":
                response = str(round(rng.uniform(100, 135), 1))
            else:
                response = f"W{rng.randrange(10_000, 99_999)}"
            db.insert(
                "qa_response",
                {"header_id": header_id, "question_id": qid, "response": response},
                conn,
            )
