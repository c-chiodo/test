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
    (6, "ACID", "Acid"),
]

#: Departments not every plant has. Acidulation runs where the soapstock is.
PLANT_DEPARTMENTS = {"ACID": ("DM", "SC")}

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
    (6, "Acid"),
    (7, "Settle"),      # a soap settle tank (DM 1-10)
    (8, "MGR"),         # an MGR reprocessing tank (DM 13-17)
    (9, "Utility"),     # steam, city water: no tank to run dry
]

TRANSACTION_TYPES = [
    (1, "RECEIVE", "Receipt into a location"),
    (2, "PRODUCE", "Consume input(s), produce output"),
    (3, "MOVE", "Move inventory between locations"),
    (4, "LOAD", "Load a trailer from a location"),
    (5, "SHIP", "Ship a loaded trailer"),
    (6, "SHRINK", "Record shrinkage / loss"),
    (7, "ADJUST", "Inventory adjustment"),
    # The legacy PROD-LOAD: components pumped from their tanks straight onto
    # the trailer as the ordered blend. A load, by kind.
    (8, "PROD_LOAD", "PROD-LOAD: blend onto a trailer"),
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
    # Kept in place so every other material keeps its id; these three are
    # now the real legacy materials with those positions' roles.
    ("00010", "Wetgums", "Soaps", 2, "DM SC PJ"),
    ("01019", "Veg DM (20 series)", "Veg oil", 2, "DM"),
    ("00007", "Soap - Gum", "Soaps", 2, "DM SC PJ"),
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
    # The acidulation materials, numbered as the legacy PIMS numbers them.
    ("00003", "Caustic", "Raw Material", 2, "DM SC PJ LV"),
    ("00004", "Steam", "Utility", 2, "DM SC PJ"),
    ("00006", "Soap - Degum", "Soaps", 2, "DM SC PJ"),
    ("00009", "VOP wet", "Soaps", 2, "DM SC"),
    ("00011", "Water - city water in", "", 2, "DM SC PJ LV"),
    ("01003", "MGR animal", "MGR", 2, "DM SC"),
    ("01006", "Soap in Process-Veg", "", 2, "DM SC PJ"),
    ("01007", "MGR veg", "MGR", 2, "DM SC PJ"),
    ("01008", "PROCESS WATER", "Water", 2, "DM SC PJ LV"),
    ("01018", "Veg SC (20 series)", "Veg oil", 2, "SC"),
    ("02021", "AOX Santoquin", "Raw Material", 2, "DM SC"),
    ("02048", "LIPIDOL GOLD", "Raw Material", 2, "DM SC"),
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

# The stage matters: four of these inspect an *empty* trailer, and asking them
# after the product is aboard makes them a formality. `pre_load` questions are
# asked before the load posts and block it; the rest are asked after.
QA_QUESTIONS = [
    (1, "Trailer interior clean, dry and free of odor?", "yesno", "pre_load", 10),
    (2, "Previous load compatible with this product?", "yesno", "pre_load", 20),
    (3, "Hoses capped and wash ticket present?", "yesno", "pre_load", 30),
    (4, "Driver provided wash ticket number", "text", "pre_load", 40),
    (5, "Seals applied and recorded on the BOL?", "yesno", "post_load", 50),
    (6, "Product temperature at load (°F)", "number", "post_load", 60),
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
    ("bol.format", "{prefix}{sequence:06d}-1", "Template for generated BOL numbers"),
    ("bol.append_trailer", "true", "Append (trailer) to the generated BOL, as the legacy numbers do"),
    ("bol.auto_generate", "true", "Mint a BOL when a load or receipt needs one and none was entered"),
    ("sample.format", "{plant}{party}D{date}Q{sequence:07d}", "Template for generated sample numbers"),
    ("sample.date_format", "%y%m%d", "Date portion of a generated sample number"),
    # Off by default: a code LabWare does not recognise trades a typo problem
    # for a matching problem. Confirm the scheme with the lab, then enable.
    ("sample.auto_generate", "false", "Fill the sample number automatically on new QC records"),
    ("shrinkage.warn_pct", "0.5", "Warn when shrinkage exceeds this % of the run"),
    ("qc.require_sample_number", "true", "Require a sample number on finished-product QC"),
    ("lims.refresh_cron", "0 */2 * * *", "Expected LIMS projection refresh cadence"),
    ("alerts.webhook_url", "", "Incoming webhook (Teams/Slack) for alerts; blank = record only"),
    ("alerts.min_severity", "warning", "Lowest severity delivered to the webhook"),
    ("alerts.repeat_hours", "24", "Do not re-send the same finding inside this window"),
    ("autoclose.enabled", "true", "Close orders once fulfilled, shipped and QC'd"),
    ("autoclose.require_qc", "true", "Only auto-close when the order has a QC record"),
    ("autoclose.min_percent", "99", "Percent complete an order must reach to auto-close"),
]

#: Counters start above the numbers already in the legacy series so a generated
#: number can never collide with a historical one.
NUMBER_SEQUENCES = [("bol", 112_000), ("sample", 400_000), ("blend", 5_000)]

#: (product number, recipe name, [(component number, percentage by weight)]).
#:
#: The *structure* is real — these are the products the plant blends, from the
#: raw materials it receives. The *numbers* are invented: the legacy order
#: table referenced a Blend_recipe_id, but the recipes themselves were not in
#: the material provided. Each seeded recipe says so in its notes; replace
#: them with the plant's real formulations before anyone blends to them.
BLEND_RECIPES = [
    # From a real FE Cattle Blend 3.5 load (MGR veg 24,411 + process water
    # 18,551 + caustic 2,109 onto one trailer); 2.5 and MGR veg 2.5 are
    # proportioned by analogy and are placeholders.
    ("01020", "FE Cattle Blend - 2.5", [("01007", 57.0), ("01008", 40.5), ("00003", 2.5)]),
    ("01021", "FE Cattle Blend - 3.5", [("01007", 54.2), ("01008", 41.1), ("00003", 4.7)]),
    ("01031", "MGR veg - 2.5", [("01007", 97.5), ("00003", 2.5)]),
    # 20-series oil made ready as AV4000, as the yields sheet's "Oil Ready".
    ("05001", "AV4000", [("01019", 100.0)]),
    # A real HC3800 XL load: HC3800 45,522 + AOX 6 + Lipidol Gold 155.
    ("05081", "HC3800 XL - AOX .025%", [("05003", 99.635), ("02021", 0.025), ("02048", 0.34)]),
]

#: Acidulation as the Des Moines yields workbook and the legacy ledger show it:
#: soap charged into a settle tank as 1006 Soap in Process, acid (5.1 lbs per
#: 100 lbs of soap over the sampled week) and steam (2.6) PRODUCED in on top;
#: settled; broken into 20-series oil, MGR and process water. The MGR is
#: reprocessed in its own tanks and broken again. The ratios are the
#: workbook's measured averages; the expected first-pass yield (55% of what
#: the soap's TFA could give) and MGR oil fraction (34%) are its figures too.
SOAPS = ["00007", "00006", "00010", "00009"]
STAGED_RECIPES = [
    # product, name, vessel, process material, components, outputs, TFA, yield
    ("01019", "Soap settle", "Settle", "01006",
     [*[(n, 100.0, "soap") for n in SOAPS], ("00001", 5.1, None), ("00004", 2.6, None)],
     [("01019", "oil", "Oil (20's)", "moisture,spintest"), ("01007", "mgr", "MGR", "moisture,spintest"),
      ("01008", "water", "Process water", "")],
     26.0, 55.0),
    ("01018", "Soap settle", "Settle", "01006",
     [*[(n, 100.0, "soap") for n in SOAPS], ("00001", 5.1, None), ("00004", 2.6, None)],
     [("01018", "oil", "Oil (20's)", "moisture,spintest"), ("01007", "mgr", "MGR", "moisture,spintest"),
      ("01008", "water", "Process water", "")],
     26.0, 55.0),
    ("01019", "MGR reprocess", "MGR", "01007",
     [("01007", 100.0, None)],
     [("01019", "oil", "Oil (20's)", "moisture,spintest"), ("01007", "mgr", "MGR (to 41)", "moisture,spintest"),
      ("01008", "water", "Process water", "")],
     None, 34.0),
]
STAGED_NOTE = (
    "Ratios from the Des Moines yields workbook (acid 5.1 and steam 2.6 lbs per "
    "100 lbs of soap; TFA 26% assumed). Adjust to the plant's practice."
)

#: Kiosk PINs for the shared plant terminal. Demo values.
USER_PINS = {"cchiodo": "4021", "jmartin": "2210", "rprice": "3317", "toperator": "5588"}


def seed_all(conn: sqlite3.Connection | None = None) -> None:
    """Populate an empty database. Idempotent by virtue of only running once."""

    conn = conn or db.get_connection()
    rng = random.Random(RNG_SEED)
    with db.transaction(conn):
        _seed_reference(conn)
        material_ids = _seed_materials(conn)
        _seed_recipes(conn, material_ids)
        location_ids = _seed_locations(conn)
        _seed_partners(conn)
        user_ids = _seed_users(conn)
        _seed_activity(conn, rng, material_ids, location_ids, user_ids)
        _seed_acid(conn, material_ids, user_ids)
        _seed_deliveries(conn, material_ids)
        _seed_history(conn, material_ids, user_ids)


def _seed_recipes(conn, material_ids: dict[str, int]) -> None:
    departments = {code: did for did, code, _d in DEPARTMENTS}
    recipes = [(p, n, parts, "BLND", 100.0, "Blend", "Demo formulation — replace with the plant's real recipe.", "blend")
               for p, n, parts in BLEND_RECIPES]
    for product, name, parts, department, yield_pct, vessel, note, method in recipes:
        recipe_id = db.insert(
            "blend_recipe",
            {
                "material_id": material_ids[product],
                "name": name,
                "notes": note,
                "active": 1,
                "department_id": departments[department],
                "yield_pct": yield_pct,
                "vessel_type": vessel,
                "method": method,
            },
            conn,
        )
        for index, (component, pct) in enumerate(parts):
            db.insert(
                "blend_recipe_component",
                {
                    "recipe_id": recipe_id,
                    "material_id": material_ids[component],
                    "percentage": pct,
                    "sort_order": index * 10,
                    # Caustic goes in against the pH, a little at a time.
                    "dose": "ph" if component == "00003" else None,
                },
                conn,
            )
    acid = next(did for did, code, _d in DEPARTMENTS if code == "ACID")
    for product, name, vessel, process_material, parts, outputs, tfa, yield_pct in STAGED_RECIPES:
        recipe_id = db.insert(
            "blend_recipe",
            {
                "material_id": material_ids[product], "name": name, "notes": STAGED_NOTE, "active": 1,
                "department_id": acid, "yield_pct": yield_pct, "vessel_type": vessel, "method": "staged",
                "process_material_id": material_ids[process_material], "expected_tfa": tfa,
            },
            conn,
        )
        for index, (component, pct, grp) in enumerate(parts):
            db.insert(
                "blend_recipe_component",
                {"recipe_id": recipe_id, "material_id": material_ids[component], "percentage": pct,
                 "sort_order": index * 10, "grp": grp},
                conn,
            )
        for index, (material, role, label, readings) in enumerate(outputs):
            db.insert(
                "blend_recipe_output",
                {"recipe_id": recipe_id, "material_id": material_ids[material], "role": role,
                 "label": label, "readings": readings, "sort_order": index * 10},
                conn,
            )


def _seed_reference(conn) -> None:
    for cid, name in COMPANIES:
        db.insert("company", {"company_id": cid, "name": name}, conn)
    for pid, code, name in PLANTS:
        db.insert("plant", {"plant_id": pid, "code": code, "name": name}, conn)
    for did, code, desc in DEPARTMENTS:
        db.insert(
            "department", {"department_id": did, "code": code, "description": desc}, conn
        )
    for pid, plant_code, _n in PLANTS:
        for did, dept_code, _d2 in DEPARTMENTS:
            if plant_code not in PLANT_DEPARTMENTS.get(dept_code, (plant_code,)):
                continue
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
            {"transaction_type_id": ttid, "code": code, "description": desc,
             "kind": "LOAD" if code == "PROD_LOAD" else code},
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
    for qid, question, answer_type, stage, order_ in QA_QUESTIONS:
        db.insert(
            "qa_question",
            {
                "question_id": qid,
                "question": question,
                "answer_type": answer_type,
                "stage": stage,
                "sort_order": order_,
            },
            conn,
        )
    for key, value, desc in SYSTEM_SETTINGS:
        db.insert(
            "system_setting", {"key": key, "value": value, "description": desc}, conn
        )
    for key, start in NUMBER_SEQUENCES:
        db.insert("number_sequence", {"key": key, "next_value": start}, conn)


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
                "pin_hash": hash_password(USER_PINS.get(username, "0000")),
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
    # The raw materials the blend and loadout side keeps in storage tanks:
    # acid, caustic, MGR veg and process water (what a cattle blend is).
    inbound = ["00001", "00003", "01007", "01008"]
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

    def compatible(tank: int, material_id: int) -> bool:
        """A tank takes more of what it already holds, or anything when empty.
        Real tanks hold one product at a time; a seed that ignored that put
        acid, water and soapstock in the same tank and made every tile on the
        tank board read "mixed"."""

        return all(
            qty <= HEEL_LBS for (loc, mat), qty in balances.items()
            if loc == tank and mat != material_id
        )

    def clear_heel(tank: int, material_id: int, base: dict) -> None:
        """Before a tank takes a different product, the last of the old one is
        written off — the heel. Plants do this at a product change; the seed
        doing it too is what lets a tank be reused."""

        for (loc, mat), qty in list(balances.items()):
            if loc == tank and mat != material_id and qty > 0.01:
                write(
                    {
                        **base,
                        "transaction_type_id": 6,
                        "from_material_id": mat,
                        "from_location_id": tank,
                        "from_qty": round(qty, 2),
                        "remarks": "Heel written off before product change",
                    }
                )

    # A tank this empty is ready for a new product once the heel is written off.
    HEEL_LBS = 2_000.0
    # Receipts stop here, the way an operator would.
    FILL_LIMIT = 0.90

    # Opening balances: one product per tank. The raw materials blending
    # consumes get tanks of their own, one tank starts empty, and the rest
    # hold finished product.
    for plant_id, tanks in tanks_by_plant.items():
        for index, tank in enumerate(tanks):
            if index < len(inbound):
                number = inbound[index]
            elif index == len(inbound):
                continue                       # an empty tank to receive into
            else:
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
                    candidates = [
                        t for t in tanks
                        if total_at(t) + qty <= tank_capacity * FILL_LIMIT
                        and compatible(t, material_id)
                    ]
                    if not candidates:
                        continue
                    # Prefer the tank already holding this product.
                    holding = [t for t in candidates if stock_at(t, material_id) > 1.0]
                    tank = rng.choice(holding or candidates)
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
                        t for t in tanks
                        if t != source and total_at(t) + qty <= tank_capacity * FILL_LIMIT
                        and compatible(t, material_id)
                    ]
                    if not targets:
                        continue
                    holding = [t for t in targets if stock_at(t, material_id) > 1.0]
                    tank = rng.choice(holding or targets)
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

                if kind in (2, 3):
                    clear_heel(tank, material_id, base)
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
                    # Open work orders keep work: roughly half are produced
                    # part-way, so the Blend screen has batches left to run.
                    produced = qty
                    if not closed and rng.random() < 0.5:
                        produced = round(qty * rng.uniform(0.2, 0.6), 1)
                    write(
                        {
                            **base,
                            "transaction_type_id": 2,
                            "from_material_id": source_material,
                            "from_location_id": source,
                            "from_qty": produced,
                            "to_material_id": material_id,
                            "to_location_id": tank,
                            "to_qty": round(produced * rng.uniform(0.985, 0.999), 1),
                            "tank_hours": round(rng.uniform(1.0, 6.0), 1),
                            "employee_hours": round(rng.uniform(0.5, 3.0), 1),
                        }
                    )
                else:
                    trailer_loc = loc(plant_id, "TRAILER")
                    trailer = str(rng.randrange(100, 999))
                    # Open orders are not all loaded to the last pound. Roughly
                    # half of them have a truck still to go, which is what a
                    # loadout screen is looking at for most of a shift — and
                    # what the demo used to have none of.
                    load_qty = qty
                    if not closed and rng.random() < 0.55:
                        load_qty = round(qty * rng.uniform(0.3, 0.7), 1)
                    txn = write(
                        {
                            **base,
                            "transaction_type_id": 4,
                            "from_material_id": material_id,
                            "from_location_id": tank,
                            "from_qty": load_qty,
                            "to_material_id": material_id,
                            "to_location_id": trailer_loc,
                            "to_qty": load_qty,
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
                            "quantity": load_qty,
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
                                "from_qty": load_qty,
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
        # Two passes, the way the flow records them: the trailer inspection
        # before the product goes in, the seals and load temperature after. A
        # minority of loads skip the first one — which is what the "loaded
        # before the trailer check" probe is there to surface.
        trailer = str(rng.randrange(100, 999))
        stages = ["pre_load", "post_load"]
        if rng.random() < 0.15:
            stages.remove("pre_load")
        for stage in stages:
            questions = [q for q in QA_QUESTIONS if q[3] == stage]
            if not questions:
                continue
            header_id = db.insert(
                "qa_header",
                {
                    "order_id": order_id,
                    "plant_id": plant_id,
                    "qc_id": qc_id if stage == "post_load" else None,
                    "trailer_number": trailer,
                    "trailer_load_time": iso,
                    "stage": stage,
                    "date_added": iso,
                    "added_by": username,
                },
                conn,
            )
            for qid, _q, answer_type, _stage, _o in questions:
                if rng.random() < 0.05:
                    # Some questions genuinely do not apply — a tank wagon with
                    # no hoses to cap. Seeded so the N/A path has data behind it.
                    response = "N/A"
                elif answer_type == "yesno":
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


def _seed_acid(conn, material_ids: dict[str, int], user_ids: dict[str, int]) -> None:
    """The acid department's tanks, numbered as Des Moines numbers them, with
    opening stock, open work, a settle tank mid-batch and a railcar on the spur.

    Written after everything else, with fixed values rather than the shared
    random stream, so adding it did not reshuffle the rest of the demo ledger.
    """

    acid_id = next(did for did, code, _d in DEPARTMENTS if code == "ACID")
    types = {name: tid for tid, name in LOCATION_TYPES}
    now = utc_now().replace(microsecond=0)
    opened = (now - timedelta(days=95)).isoformat()
    order_id = int(db.scalar('SELECT MAX(order_id) FROM "order"', (), conn) or 0)
    today = now.date()

    layout = {
        "DM": [
            ("1", "Settle tank 1", "Settle", 150_000, None), ("2", "Settle tank 2", "Settle", 150_000, None),
            ("3", "Settle tank 3", "Settle", 150_000, None),
            ("13", "MGR tank 13", "MGR", 60_000, None), ("14", "MGR tank 14", "MGR", 60_000, None),
            ("20", "20's oil tank 20", "Tank", 200_000, ("01019", 60_000)),
            ("21", "20's oil tank 21", "Tank", 200_000, ("01019", 30_000)),
            ("32", "Process water 32", "Tank", 150_000, ("01008", 40_000)),
            ("33", "Process water 33", "Tank", 150_000, ("01008", 20_000)),
            ("40", "Soap in process 40", "Tank", 150_000, ("01006", 50_000)),
            ("41", "MGR veg out 41", "Tank", 100_000, ("01007", 60_000)),
            ("42", "MGR veg 42", "Tank", 100_000, ("01007", 25_000)),
            ("103", "Acid tank 103", "Tank", 80_000, ("00001", 30_000)),
            ("975", "Steam", "Utility", None, None),
        ],
        "SC": [
            ("1", "Settle tank 1", "Settle", 150_000, None), ("2", "Settle tank 2", "Settle", 150_000, None),
            ("13", "MGR tank 13", "MGR", 60_000, None),
            ("20", "20's oil tank 20", "Tank", 200_000, ("01018", 40_000)),
            ("32", "Process water 32", "Tank", 150_000, ("01008", 30_000)),
            ("41", "MGR veg out 41", "Tank", 100_000, ("01007", 40_000)),
            ("42", "MGR veg 42", "Tank", 100_000, ("01007", 20_000)),
            ("103", "Acid tank 103", "Tank", 80_000, ("00001", 25_000)),
            ("975", "Steam", "Utility", None, None),
        ],
    }

    def loc(plant_id: int, number: str) -> int:
        return db.scalar("SELECT location_id FROM location WHERE plant_id = ? AND number = ?", (plant_id, number), conn)

    def txn(row: dict) -> int:
        return db.insert("inventory_transaction", {
            "plant_id": row.pop("plant_id"), "department_id": acid_id, "user_id": user_ids["toperator"],
            "transaction_date": row.get("transaction_date", opened), "user_date": row.pop("user_date", opened[:10]),
            "remarks": row.pop("remarks", ""), **row,
        }, conn)

    def work_order(plant_id: int, product: str, qty: float, days: int, reference: str, recipe: str | None = None) -> int:
        nonlocal order_id
        order_id += 1
        recipe_id = None
        if recipe:
            recipe_id = db.scalar(
                "SELECT recipe_id FROM blend_recipe WHERE material_id = ? AND vessel_type = ? AND active = 1",
                (material_ids[product], recipe), conn,
            )
        db.insert("order", {
            "order_id": order_id, "order_type_id": 2, "order_date": today.isoformat(),
            "due_date": (today + timedelta(days=days)).isoformat(), "order_reference": reference,
            "company_id": 1, "plant_id": plant_id, "department_id": acid_id, "blend_serial_number": "",
            "material_one_id": material_ids[product], "material_one_quantity": qty, "ship_method": "",
            "trailer_number": "", "comments": "", "status_id": 1,
            "date_added": now.isoformat(), "added_by": "jmartin", "recipe_id": recipe_id,
        }, conn)
        return order_id

    for plant_id, code, _name in PLANTS:
        if code not in PLANT_DEPARTMENTS["ACID"]:
            continue
        for number, description, kind, capacity, stock in layout[code]:
            location_id = db.insert("location", {
                "plant_id": plant_id, "number": f"{code}-{number}", "description": description,
                "location_type_id": types[kind], "company_id": 1, "max_capacity": capacity, "bol_required": 0,
            }, conn)
            if stock:
                txn({"plant_id": plant_id, "transaction_type_id": 1, "to_location_id": location_id,
                     "to_material_id": material_ids[stock[0]], "to_qty": stock[1], "remarks": "Opening balance"})

        oil = "01019" if code == "DM" else "01018"
        settle_wo = work_order(plant_id, oil, 6_000.0, 1, f"SETTLE-{code}-1", "Settle")
        work_order(plant_id, oil, 7_500.0, 3, f"SETTLE-{code}-2", "Settle")
        if code == "DM":
            work_order(plant_id, "01019", 5_000.0, 2, "MGR-DM-1", "MGR")
            _seed_settling_batch(conn, settle_wo, plant_id, acid_id, material_ids, user_ids, loc, txn)
        else:
            # A railcar of wetgums received for the invoice, still on the spur.
            spur = loc(plant_id, f"{code}-RECV-RAIL")
            at = (now - timedelta(hours=30)).isoformat()
            txn({"plant_id": plant_id, "transaction_type_id": 1, "to_location_id": spur,
                 "to_material_id": material_ids["00010"], "to_qty": 58_400.0, "trailer_number": "UTLX 667576",
                 "transaction_date": at, "user_date": at[:10],
                 "remarks": "UTLX 667576 received to pay invoice; car still on the spur"})


def _seed_settling_batch(conn, order_id, plant_id, department_id, material_ids, user_ids, loc, txn) -> None:
    """DM settle tank 1 mid-batch: a truck of Soap - Gum unloaded into it,
    acid and steam on top, three hours into settling — the state a day-shift
    operator walks in to."""

    now = utc_now().replace(microsecond=0)
    started = now - timedelta(hours=7)
    tank = loc(plant_id, "DM-1")
    truck = loc(plant_id, "DM-RECV-TRUCK")
    recipe_id = db.scalar(
        "SELECT recipe_id FROM blend_recipe WHERE material_id = ? AND vessel_type = 'Settle' AND active = 1",
        (material_ids["01019"],), conn,
    )
    db.insert("process_batch", {
        "batch_id": "A-00001", "order_id": order_id, "plant_id": plant_id, "department_id": department_id,
        "recipe_id": recipe_id, "vessel_id": tank, "material_id": material_ids["01019"], "target_lbs": 6_000.0,
        "status": "settling", "started_at": started.isoformat(), "started_by": "toperator",
        "acid_at": (started + timedelta(minutes=45)).isoformat(),
        "mixing_at": (started + timedelta(minutes=70)).isoformat(),
        "settling_at": (now - timedelta(hours=3)).isoformat(),
    }, conn)
    at = lambda minutes: (started + timedelta(minutes=minutes)).isoformat()  # noqa: E731
    soap, into = material_ids["00007"], material_ids["01006"]
    # Received off truck 5521 onto the truck bay, then pumped into the tank.
    txn({"plant_id": plant_id, "transaction_type_id": 1, "to_location_id": truck, "to_material_id": soap,
         "to_qty": 44_000.0, "trailer_number": "5521", "transaction_date": at(0), "user_date": at(0)[:10],
         "remarks": "Charged to A-00001 off truck 5521", "batch_id": "A-00001"})
    for material, source, lbs, minutes in (("00007", truck, 44_000.0, 5),
                                           ("00001", loc(plant_id, "DM-103"), 2_244.0, 50),
                                           ("00004", loc(plant_id, "DM-975"), 1_144.0, 55)):
        txn({"plant_id": plant_id, "transaction_type_id": 2, "order_id": order_id,
             "from_location_id": source, "from_material_id": material_ids[material], "from_qty": lbs,
             "to_location_id": tank, "to_material_id": into, "to_qty": lbs,
             "transaction_date": at(minutes), "user_date": at(minutes)[:10],
             "remarks": "Charged to A-00001", "batch_id": "A-00001"})
    db.execute(
        "INSERT INTO number_sequence (key, next_value) VALUES ('batch-A', 2)"
        " ON CONFLICT(key) DO UPDATE SET next_value = MAX(next_value, 2)",
        (), conn,
    )


def _seed_deliveries(conn, material_ids: dict[str, int]) -> None:
    """Purchase orders with the truck still to arrive, so Receiving has work.

    Every seeded purchase order before this was received the moment it was
    raised, which left the receiving lane — and the receive screen's list of
    deliveries — permanently empty in the demo.
    """

    receiving = next(did for did, code, _d in DEPARTMENTS if code == "RECV")
    acid = next(did for did, code, _d in DEPARTMENTS if code == "ACID")
    order_id = int(db.scalar('SELECT MAX(order_id) FROM "order"', (), conn) or 0)
    today = utc_now().date()
    # Soap and acid for acidulation are the acid department's deliveries; a
    # railcar of soap holds three trucks' worth.
    expected = {
        "DM": (("00007", 44_000.0, 2, "ACID", "TL-GREENE LINES"), ("00001", 18_000.0, 5, "ACID", "TL-GREENE LINES")),
        "SC": (("00006", 60_000.0, 1, "ACID", "RL-BNSF"),),
        "PJ": (("00001", 18_000.0, 3, "RECV", "TL-GREENE LINES"),),
        "LV": (("00001", 12_000.0, 4, "RECV", "TL-GREENE LINES"),),
    }
    for plant_id, code, _name in PLANTS:
        for index, (number, qty, vendor, dept, method) in enumerate(expected.get(code, ())):
            order_id += 1
            db.insert(
                "order",
                {
                    "order_id": order_id,
                    "order_type_id": 3,
                    "order_date": today.isoformat(),
                    "due_date": (today + timedelta(days=index)).isoformat(),
                    "order_reference": f"PO-{code}-{index + 1}",
                    "company_id": 1,
                    "plant_id": plant_id,
                    "department_id": acid if dept == "ACID" else receiving,
                    "blend_serial_number": "",
                    "vendor_id": vendor,
                    "material_one_id": material_ids[number],
                    "material_one_quantity": qty,
                    "ship_method": method,
                    "trailer_number": "",
                    "comments": "",
                    "status_id": 1,
                    "date_added": utc_now().replace(microsecond=0).isoformat(),
                    "added_by": "jmartin",
                },
                conn,
            )


def _seed_history(conn, material_ids: dict[str, int], user_ids: dict[str, int]) -> None:
    """Six weeks of the acid department and its blended loads, for the reports.

    Des Moines settles most days and reprocesses MGR weekly; Sioux City
    settles every other day. The oil goes out as AV4000, the MGR and water as
    FE Cattle Blend with caustic dosed to the pH, the rest of the water by the
    trailer. Early loads have the legacy habit of posting the caustic, finding
    the pH wrong and reversing it; the last ten days were dosed on the screen.

    Every tank is tracked as rows are written, so nothing leaves a tank that
    does not hold it and the balances the demo opened with are what it keeps.
    Its own random stream, so the rest of the demo ledger is unchanged.
    """

    rng = random.Random(8801)
    acid_id = next(did for did, code, _d in DEPARTMENTS if code == "ACID")
    now = utc_now().replace(minute=0, second=0, microsecond=0)
    order_id = int(db.scalar('SELECT MAX(order_id) FROM "order"', (), conn) or 0)
    users = [user_ids["toperator"], user_ids["toperator"], user_ids["rprice"]]
    level: dict[tuple[int, int], float] = {}

    def loc(plant_id: int, number: str) -> int | None:
        return db.scalar("SELECT location_id FROM location WHERE plant_id = ? AND number = ?", (plant_id, number), conn)

    def on_hand(location_id: int, material: str) -> float:
        key = (location_id, material_ids[material])
        if key not in level:
            level[key] = float(db.scalar(
                """SELECT COALESCE(SUM(CASE WHEN to_location_id = :l AND to_material_id = :m THEN to_qty ELSE 0 END), 0)
                        - COALESCE(SUM(CASE WHEN from_location_id = :l AND from_material_id = :m THEN from_qty ELSE 0 END), 0)
                   FROM inventory_transaction""",
                {"l": location_id, "m": material_ids[material]}, conn,
            ) or 0.0)
        return level[key]

    def txn(row: dict, at) -> int:
        stamp = at.isoformat()
        row = {"department_id": acid_id, "user_id": rng.choice(users), "transaction_date": stamp,
               "user_date": stamp[:10], "remarks": "", **row}
        if row.get("from_location_id") and row.get("from_material_id"):
            key = (row["from_location_id"], row["from_material_id"])
            level[key] = level.get(key, 0.0) - row["from_qty"]
        if row.get("to_location_id") and row.get("to_material_id"):
            key = (row["to_location_id"], row["to_material_id"])
            level[key] = level.get(key, 0.0) + row["to_qty"]
        return db.insert("inventory_transaction", row, conn)

    def reverse(txn_id: int, at, reason: str) -> None:
        o = db.query_one("SELECT * FROM inventory_transaction WHERE transaction_id = ?", (txn_id,), conn)
        txn({"transaction_type_id": o["transaction_type_id"], "parent_transaction_id": txn_id, "order_id": o["order_id"],
             "plant_id": o["plant_id"], "department_id": o["department_id"], "user_id": o["user_id"],
             "from_material_id": o["to_material_id"], "from_location_id": o["to_location_id"], "from_qty": o["to_qty"],
             "to_material_id": o["from_material_id"], "to_location_id": o["from_location_id"], "to_qty": o["from_qty"],
             "trailer_number": o["trailer_number"], "is_reversal": 1,
             "remarks": f"Reversal of transaction {txn_id}: {reason}"}, at)
        db.update("inventory_transaction", {"transaction_id": txn_id}, {"voided": 1}, conn)

    def order(plant_id: int, kind: int, product: str, qty: float, at, reference: str, department_id=acid_id) -> int:
        nonlocal order_id
        order_id += 1
        db.insert("order", {
            "order_id": order_id, "order_type_id": kind, "order_date": at.date().isoformat(),
            "due_date": at.date().isoformat(), "order_reference": reference, "company_id": 1,
            "plant_id": plant_id, "department_id": department_id, "blend_serial_number": "",
            "vendor_id": rng.randrange(1, len(VENDORS) + 1) if kind == 3 else None,
            "customer_id": rng.randrange(1, len(CUSTOMERS) + 1) if kind == 1 else None,
            "material_one_id": material_ids[product], "material_one_quantity": qty,
            "ship_method": "Bulk trailer" if kind == 1 else "", "trailer_number": "", "comments": "",
            "status_id": 4, "date_added": at.isoformat(), "added_by": "jmartin",
        }, conn)
        return order_id

    def ship(plant_id: int, so: int, first_txn: int, product: str, lbs: float, trailer: str, at) -> None:
        db.insert("pending_shipment", {"order_id": so, "transaction_id": first_txn, "trailer_number": trailer,
                                       "quantity": lbs, "shipped": 1}, conn)
        txn({"transaction_type_id": 5, "parent_transaction_id": first_txn, "order_id": so, "plant_id": plant_id,
             "from_material_id": material_ids[product], "from_location_id": loc(plant_id, f"{code}-TRAILER"),
             "from_qty": lbs, "trailer_number": trailer, "remarks": "Shipped"}, at + timedelta(hours=1))

    def qc(so: int, bol: str, at, ph: float | None, moisture: float | None = None) -> None:
        db.insert("qc", {"order_id": so, "bol_number": bol, "test_date": at.isoformat(), "performed_by": "rprice",
                         "moisture": moisture, "ph": ph, "sample_number": f"{code}C{rng.randrange(1000, 9999)}D{at:%y%m%d}",
                         "seal_number": str(rng.randrange(100_000, 999_999)),
                         "date_added": at.isoformat(), "added_by": "rprice"}, conn)

    cattle_dept = db.scalar(
        "SELECT mt.department_id FROM material m JOIN material_type mt ON mt.material_type_id = m.material_type_id"
        " WHERE m.material_id = ?", (material_ids["01021"],), conn,
    )

    for plant_id, code, _name in PLANTS:
        if code not in PLANT_DEPARTMENTS["ACID"]:
            continue
        dm = code == "DM"
        oil = "01019" if dm else "01018"
        settles = [loc(plant_id, f"{code}-{n}") for n in (("2", "3") if dm else ("1", "2"))]
        oil_tanks = [loc(plant_id, f"{code}-{n}") for n in (("20", "21") if dm else ("20",))]
        water_tanks = [loc(plant_id, f"{code}-{n}") for n in (("32", "33") if dm else ("32",))]
        mgr_out, mgr_back = loc(plant_id, f"{code}-41"), loc(plant_id, f"{code}-42")
        reactor = loc(plant_id, f"{code}-13")
        acid_tank, steam = loc(plant_id, f"{code}-103"), loc(plant_id, f"{code}-975")
        truck, trailer_loc = loc(plant_id, f"{code}-RECV-TRUCK"), loc(plant_id, f"{code}-TRAILER")
        caustic_tank = db.scalar(
            """SELECT l.location_id FROM location l JOIN inventory_transaction t ON t.to_location_id = l.location_id
               WHERE l.plant_id = ? AND t.to_material_id = ? AND l.location_type_id = 1
               GROUP BY l.location_id ORDER BY SUM(t.to_qty) DESC LIMIT 1""",
            (plant_id, material_ids["00003"]), conn,
        )
        mgr_store = db.scalar(
            """SELECT l.location_id FROM location l JOIN inventory_transaction t ON t.to_location_id = l.location_id
               WHERE l.plant_id = ? AND t.to_material_id = ? AND l.location_type_id = 1 AND l.number LIKE ?
               GROUP BY l.location_id ORDER BY SUM(t.to_qty) DESC LIMIT 1""",
            (plant_id, material_ids["01007"], f"{code}-T%"), conn,
        ) or mgr_back
        floor = {key: on_hand(*key) for key in
                 [(t, oil) for t in oil_tanks] + [(t, "01008") for t in water_tanks] + [(mgr_out, "01007")]}
        week_orders: dict[int, tuple[int, int]] = {}
        acid_this_week = 0.0

        def over(tank: int, material: str) -> float:
            return on_hand(tank, material) - floor[(tank, material)]

        for days_ago in range(42, 1, -1):
            day = now - timedelta(days=days_ago)
            week = days_ago // 7
            if week not in week_orders:
                start = day.replace(hour=6)
                week_orders[week] = (
                    order(plant_id, 2, oil, 0.0, start, f"SETTLE-{code}-W{day:%V}"),
                    order(plant_id, 3, "00007", 0.0, start, f"P01-0{rng.randrange(21_000, 21_999)}-{rng.randrange(1, 90)}"),
                )
                if acid_this_week:
                    ro = order(plant_id, 3, "00001", acid_this_week, start, f"P01-0{rng.randrange(21_000, 21_999)}-1")
                    txn({"transaction_type_id": 1, "order_id": ro, "plant_id": plant_id, "to_location_id": acid_tank,
                         "to_material_id": material_ids["00001"], "to_qty": round(acid_this_week), "remarks": "Acid delivery"},
                        start)
                    acid_this_week = 0.0
            wo, po = week_orders[week]
            settle_today = (rng.random() < 0.86) if dm else (days_ago % 2 == 0)
            if settle_today:
                tank = settles[days_ago % len(settles)]
                t0 = day.replace(hour=rng.choice((5, 6, 7, 8)))
                soap_mat = rng.choice(("00007", "00007", "00010", "00006"))
                soap = round(rng.uniform(38_000, 48_000), -1)
                truck_no = str(rng.randrange(100, 999))
                txn({"transaction_type_id": 1, "order_id": po, "plant_id": plant_id, "to_location_id": truck,
                     "to_material_id": material_ids[soap_mat], "to_qty": soap, "trailer_number": truck_no,
                     "to_bol": f"P01-0{rng.randrange(21_000, 21_999)}({truck_no})"}, t0)
                inputs = [(soap_mat, truck, soap, 5)]
                acid = round(soap * rng.uniform(0.045, 0.058))
                acid_this_week += acid
                inputs.append(("00001", acid_tank, acid, 50))
                inputs.append(("00004", steam, round(soap * rng.uniform(0.02, 0.034)), 55))
                if rng.random() < 0.5 and on_hand(water_tanks[0], "01008") > 12_000:
                    inputs.append(("01008", water_tanks[0], round(rng.uniform(3_000, 7_000)), 20))
                for material, source, lbs, minutes in inputs:
                    txn({"transaction_type_id": 2, "order_id": wo, "plant_id": plant_id, "from_location_id": source,
                         "from_material_id": material_ids[material], "from_qty": lbs, "to_location_id": tank,
                         "to_material_id": material_ids["01006"], "to_qty": lbs, "remarks": "Charged to settle"},
                        t0 + timedelta(minutes=minutes))
                total_in = sum(i[2] for i in inputs)
                fpy = min(max(rng.gauss(0.55, 0.05), 0.42), 0.66)
                oil_lbs = round(soap * 0.26 * fpy)
                mgr_lbs = round(total_in * rng.uniform(0.21, 0.28))
                t1 = t0 + timedelta(hours=rng.choice((9, 10, 11)))
                oil_tank = min(oil_tanks, key=lambda t: on_hand(t, oil))
                water_tank = min(water_tanks, key=lambda t: on_hand(t, "01008"))
                moisture, spin = round(rng.uniform(1.4, 3.4), 2), rng.choice((0.1, 0.2, 0.2, 0.3))
                for material, dest, lbs, remarks in ((oil, oil_tank, oil_lbs, f"M={moisture} S={spin}"),
                                                     ("01007", mgr_out, mgr_lbs, f"M={round(rng.uniform(3, 8), 2)} S=0.3"),
                                                     ("01008", water_tank, total_in - oil_lbs - mgr_lbs, "")):
                    tid = txn({"transaction_type_id": 2, "order_id": wo, "plant_id": plant_id, "from_location_id": tank,
                               "from_material_id": material_ids["01006"], "from_qty": lbs, "to_location_id": dest,
                               "to_material_id": material_ids[material], "to_qty": lbs, "remarks": remarks}, t1)
                    if material == oil:
                        db.insert("txn_reading", {"transaction_id": tid, "analyte": "moisture", "value": moisture,
                                                  "source": "remark"}, conn)
                        db.insert("txn_reading", {"transaction_id": tid, "analyte": "spintest", "value": spin,
                                                  "source": "remark"}, conn)

            # Every few days the MGR is reprocessed; weekly the 20's bottoms go back to it.
            t0 = day.replace(hour=13)
            if days_ago % (3 if dm else 6) == 0 and reactor:
                # What the settles and bottoms added since, not the tank's opening stock.
                batch = round(min(over(mgr_out, "01007"), rng.uniform(32_000, 40_000)), -1)
                if batch > 10_000:
                    txn({"transaction_type_id": 2, "order_id": wo, "plant_id": plant_id, "from_location_id": mgr_out,
                         "from_material_id": material_ids["01007"], "from_qty": batch, "to_location_id": reactor,
                         "to_material_id": material_ids["01007"], "to_qty": batch, "remarks": "MGR to reprocess"}, t0)
                    oil_lbs = round(batch * rng.uniform(0.30, 0.38))
                    back = round(batch * rng.uniform(0.25, 0.35))
                    for material, dest, lbs in ((oil, oil_tanks[-1], oil_lbs), ("01007", mgr_back, back),
                                                ("01008", water_tanks[-1], batch - oil_lbs - back)):
                        txn({"transaction_type_id": 2, "order_id": wo, "plant_id": plant_id, "from_location_id": reactor,
                             "from_material_id": material_ids["01007"], "from_qty": lbs, "to_location_id": dest,
                             "to_material_id": material_ids[material], "to_qty": lbs, "remarks": "MGR break"},
                            t0 + timedelta(hours=8))
            if days_ago % 7 == 3:
                bottoms = round(rng.uniform(7_000, 12_000) * (1 if dm else 0.5))
                if on_hand(oil_tanks[0], oil) > bottoms + 10_000:
                    txn({"transaction_type_id": 2, "order_id": wo, "plant_id": plant_id, "from_location_id": oil_tanks[0],
                         "from_material_id": material_ids[oil], "from_qty": bottoms, "to_location_id": mgr_out,
                         "to_material_id": material_ids["01007"], "to_qty": bottoms, "remarks": "20's bottoms"},
                        t0 + timedelta(hours=9))

            # What the day made goes out the gate once there is a truckload.
            t_out = day.replace(hour=20)
            for tank in oil_tanks:
                while over(tank, oil) >= 45_000:
                    lbs = round(rng.uniform(44_000, 45_500))
                    trailer = str(rng.randrange(100, 999))
                    so = order(plant_id, 1, "05065", lbs, t_out, f"O01-1{rng.randrange(10_000, 19_999)}-1", None)
                    bol = f"001-{rng.randrange(111_000, 118_999)}-1({trailer})"
                    first = txn({"transaction_type_id": 8, "order_id": so, "plant_id": plant_id, "from_location_id": tank,
                                 "from_material_id": material_ids[oil], "from_qty": lbs, "to_location_id": trailer_loc,
                                 "to_material_id": material_ids["05065"], "to_qty": lbs, "to_bol": bol,
                                 "trailer_number": trailer, "department_id": None}, t_out)
                    qc(so, bol, t_out, None, round(rng.uniform(1.1, 1.6), 2))
                    ship(plant_id, so, first, "05065", lbs, trailer, t_out)
            # A cattle blend most days: MGR off the break if there is a load of
            # it, otherwise from storage (topped up by the inbound MGR trucks).
            for _load in range(1 if rng.random() < (0.85 if dm else 0.5) else 0):
                if not caustic_tank:
                    break
                product = rng.choice(("01021", "01021", "01020"))
                total = round(rng.uniform(44_000, 46_500))
                mgr_share, caustic_share = (0.542, 0.047) if product == "01021" else (0.57, 0.025)
                mgr_lbs, caustic_lbs = round(total * mgr_share), round(total * caustic_share * rng.uniform(0.85, 1.15))
                water_tank = max(water_tanks, key=lambda t: on_hand(t, "01008"))
                water_lbs = total - mgr_lbs - caustic_lbs
                if on_hand(water_tank, "01008") < water_lbs + 2_000:
                    break
                mgr_from = mgr_back if on_hand(mgr_back, "01007") >= mgr_lbs + 2_000 else mgr_store
                if on_hand(mgr_from, "01007") < mgr_lbs + 20_000:
                    lbs = round(rng.uniform(44_000, 46_000))
                    po_mgr = order(plant_id, 3, "01007", lbs, t_out, f"P01-0{rng.randrange(21_000, 21_999)}-{rng.randrange(1, 90)}", None)
                    txn({"transaction_type_id": 1, "order_id": po_mgr, "plant_id": plant_id, "to_location_id": mgr_from,
                         "to_material_id": material_ids["01007"], "to_qty": lbs, "department_id": None,
                         "trailer_number": str(rng.randrange(100, 999))}, t_out - timedelta(hours=4))
                trailer = str(rng.randrange(100, 999))
                so = order(plant_id, 1, product, total, t_out, f"O01-1{rng.randrange(10_000, 19_999)}-1", cattle_dept)
                bol = f"001-{rng.randrange(111_000, 118_999)}-1({trailer})"
                base = {"transaction_type_id": 8, "order_id": so, "plant_id": plant_id, "to_location_id": trailer_loc,
                        "to_material_id": material_ids[product], "to_bol": bol, "trailer_number": trailer,
                        "department_id": cattle_dept}
                first = txn({**base, "from_location_id": mgr_from, "from_material_id": material_ids["01007"],
                             "from_qty": mgr_lbs, "to_qty": mgr_lbs}, t_out)
                txn({**base, "from_location_id": water_tank, "from_material_id": material_ids["01008"],
                     "from_qty": water_lbs, "to_qty": water_lbs}, t_out + timedelta(minutes=10))
                dosed_here = days_ago <= 10
                ph = round(min(max(rng.gauss(3.2, 0.5 if dosed_here else 0.8), 1.6), 6.2), 2)
                if not dosed_here and rng.random() < (0.35 if not dm else 0.18):
                    # Posted, tested, wrong: reversed and posted again.
                    wrong = round(caustic_lbs * rng.uniform(0.5, 0.9))
                    bad = txn({**base, "from_location_id": caustic_tank, "from_material_id": material_ids["00003"],
                               "from_qty": wrong, "to_qty": wrong}, t_out + timedelta(minutes=20))
                    reverse(bad, t_out + timedelta(minutes=40), "pH high, re-dosing")
                cid = txn({**base, "from_location_id": caustic_tank, "from_material_id": material_ids["00003"],
                           "from_qty": caustic_lbs, "to_qty": caustic_lbs,
                           "remarks": f"Dosed to ph: +{caustic_lbs:,} lbs -> ph {ph}" if dosed_here else ""},
                          t_out + timedelta(minutes=50))
                if dosed_here:
                    db.insert("txn_reading", {"transaction_id": cid, "analyte": "ph", "value": ph, "source": "entered"}, conn)
                # About one load in six went out untested.
                qc(so, bol, t_out + timedelta(minutes=55), ph if dosed_here or rng.random() > 0.17 else None)
                ship(plant_id, so, first, product, total, trailer, t_out)
            for tank in water_tanks:
                while over(tank, "01008") >= 46_000:
                    lbs = round(rng.uniform(45_500, 46_700))
                    trailer = str(rng.randrange(100, 999))
                    so = order(plant_id, 1, "01008", lbs, t_out, f"O01-1{rng.randrange(10_000, 19_999)}-1")
                    bol = f"001-{rng.randrange(111_000, 118_999)}-1({trailer})"
                    first = txn({"transaction_type_id": 4, "order_id": so, "plant_id": plant_id, "from_location_id": tank,
                                 "from_material_id": material_ids["01008"], "from_qty": lbs, "to_location_id": trailer_loc,
                                 "to_material_id": material_ids["01008"], "to_qty": lbs, "to_bol": bol,
                                 "trailer_number": trailer}, t_out + timedelta(hours=1))
                    ship(plant_id, so, first, "01008", lbs, trailer, t_out + timedelta(hours=1))

        # The week's work orders and receipts carry what they did.
        for wo, po in week_orders.values():
            db.execute(
                'UPDATE "order" SET material_one_quantity = COALESCE((SELECT SUM(to_qty) FROM inventory_transaction'
                " WHERE order_id = ? AND to_material_id = ? AND voided = 0 AND is_reversal = 0), 0) WHERE order_id = ?",
                (wo, material_ids[oil], wo), conn,
            )
            db.execute(
                'UPDATE "order" SET material_one_quantity = COALESCE((SELECT SUM(to_qty) FROM inventory_transaction'
                " WHERE order_id = ?), 0) WHERE order_id = ?",
                (po, po), conn,
            )
