"""One-way mirror: legacy ProductionData into the local store.

Every read goes through :class:`~pims.legacy.readonly.ReadOnlyConnection`;
every write goes to the *local* database and nowhere else. There is no code
path from here to an INSERT on the legacy side.

How it reads, so the load on production stays negligible:

* **Reference tables** (plants, materials, locations, types, customers…) are
  small and read whole on every pass.
* **Large tables** (orders, transactions, QC, staged shipments, checklists)
  are read as a primary-key range: every row whose id is at or above the
  highest id this mirror had seen N days ago. That catches new rows *and*
  edits to recent ones, and it is an index seek — the cheapest read SQL
  Server offers — instead of a scan on a date column that may not be indexed.
* **No ORDER BY, no UNION** on large tables. The transaction table and its
  archive are read separately; nothing is sorted or spooled in production's
  tempdb.
* A **full** pass (``--full``) re-reads everything and removes local rows
  that no longer exist in the legacy database. Run it off-hours, weekly.

How it writes: the local database's foreign keys are relaxed for the duration
so the mirror reflects the legacy data as it is — orphans included — and the
orphans are counted and reported rather than failing the sync. A mirror that
silently "fixes" what it copies is no longer a mirror.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal
from typing import Any, Callable

from .. import db
from ..errors import IntegrationError
from ..util import utc_now, utc_now_iso
from .probe import Resolution, TableResolution
from .readonly import ReadOnlyConnection

#: Read whole on every pass.
REFERENCE = {
    "plant", "company", "department", "order_type", "status", "material_type",
    "material", "location_type", "location", "customer", "vendor",
    "transaction_type", "user", "user_plant_access", "qa_question",
}

#: How far back the id-range re-read reaches, per table, in days.
WINDOWS = {"order": 180}
DEFAULT_WINDOW_DAYS = 14

#: Local accounts (people who sign in to the companion) take ids from here up,
#: so they can never collide with a mirrored legacy user id.
LOCAL_ACCOUNT_FLOOR = 1_000_000

#: The placeholder a transaction is attributed to when the legacy row has no
#: user, or the user table could not be read.
UNKNOWN_USER_ID = 0

TERMINAL_WORDS = ("closed", "cancel", "void", "deleted", "inactive")

#: What each legacy transaction type is, from its name. First match wins, so
#: the order matters: the real PIMS has MOVE-LOAD and PROD-LOAD (loads, not
#: moves or production), SHIPADJ (an adjustment, not a shipment), and a type
#: called REVERSAL that undoes its parent row.
TYPE_WORDS = [
    ("REVERSAL", ("revers",)),
    ("ADJUST", ("adj", "count", "correct")),
    ("LOAD", ("load",)),
    ("RECEIVE", ("receiv", "receipt")),
    ("PRODUCE", ("produc", "blend", "manufact")),
    ("SHIP", ("ship",)),
    ("MOVE", ("move", "transfer", "trf")),
    ("SHRINK", ("shrink", "loss")),
]

#: A reading typed into a remark: "M=2.44 S=0.1", "M-1.71 S-0.3", "M= 1.60 S= 0.1".
#: Only the labelled form is read; a bare "2.12/0.25" is ambiguous (which is
#: moisture?) and is left in the remark.
READING_PATTERNS = {
    "moisture": re.compile(r"(?<![A-Za-z])M\s*[=:-]\s*(\d+(?:\.\d+)?)", re.IGNORECASE),
    "spintest": re.compile(r"(?<![A-Za-z])S\s*[=:-]\s*(\d+(?:\.\d+)?)", re.IGNORECASE),
}


def readings_from_remark(remark: str) -> dict[str, float]:
    found: dict[str, float] = {}
    for analyte, pattern in READING_PATTERNS.items():
        match = pattern.search(remark or "")
        if match:
            try:
                found[analyte] = float(match.group(1))
            except ValueError:
                pass
    return found


# ----------------------------------------------------------------- values


def _value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dt.datetime):
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value.strip()
    return value


def _flag(value: Any, default: int = 1) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return 1 if value.strip().lower() in {"1", "y", "yes", "true", "t"} else 0
    return 1 if value else 0


def classify_type(name: str) -> str | None:
    lowered = (name or "").lower()
    for code, words in TYPE_WORDS:
        if any(word in lowered for word in words):
            return code
    return None


def _code_from(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^A-Z0-9]+", "_", (name or "TYPE").upper()).strip("_") or "TYPE"
    code, n = base, 2
    while code in taken:
        code, n = f"{base}_{n}", n + 1
    return code


def _unique(name: str, row_id: Any, taken: set[str]) -> str:
    name = (name or "").strip() or f"#{row_id}"
    if name in taken:
        name = f"{name} ({row_id})"
    return name


# ------------------------------------------------------------ transforms


class _Transforms:
    """Per-table shaping from legacy values to local rows. Stateful where the
    local schema demands uniqueness the legacy data does not promise."""

    def __init__(self) -> None:
        self.taken: dict[str, set[str]] = {}
        self.type_codes: set[str] = set()
        self.unclassified_types: list[dict[str, Any]] = []
        # -1 when the legacy database stores quantities leaving a location as
        # negative numbers (see _from_qty_sign); the local ledger stores them
        # positive and subtracts.
        self.from_sign = 1
        self.readings: list[tuple[int, str, float]] = []

    def _taken(self, key: str) -> set[str]:
        return self.taken.setdefault(key, set())

    def apply(self, key: str, row: dict[str, Any]) -> dict[str, Any] | None:
        fn: Callable | None = getattr(self, f"t_{key}", None)
        for flag in ("active", "enabled", "voided", "bol_required", "steam_on"):
            if flag in row:
                row[flag] = _flag(row[flag], 0 if flag in {"voided", "bol_required", "steam_on"} else 1)
        return fn(row) if fn else row

    def t_status(self, row):
        name = _unique(row["name"], row["status_id"], self._taken("status"))
        self._taken("status").add(name)
        row["name"] = name
        row["is_terminal"] = int(any(w in name.lower() for w in TERMINAL_WORDS))
        return row

    def t_material_type(self, row):
        row["name"] = _unique(row["name"], row["material_type_id"], self._taken("material_type"))
        self._taken("material_type").add(row["name"])
        return row

    def t_location_type(self, row):
        row["name"] = _unique(row["name"], row["location_type_id"], self._taken("location_type"))
        self._taken("location_type").add(row["name"])
        return row

    def t_transaction_type(self, row):
        name = row.pop("name", "") or ""
        kind = classify_type(name)
        code = kind
        if code is None or code in self.type_codes:
            if code is None:
                self.unclassified_types.append(
                    {"transaction_type_id": row["transaction_type_id"], "name": name}
                )
            code = _code_from(name, self.type_codes)
        self.type_codes.add(code)
        row["code"] = code
        row["description"] = name or code
        # Several legacy types share a kind (MOVE-LOAD and PROD-LOAD are both
        # loads); the code stays unique, the kind is what the rules read.
        row["kind"] = kind or code
        return row

    def t_material(self, row):
        row.setdefault("family", "")
        return row

    def t_user(self, row):
        legacy_id = row["user_id"]
        if legacy_id is None or int(legacy_id) >= LOCAL_ACCOUNT_FLOOR:
            return None
        name = row.get("username") or f"user-{legacy_id}"
        # Namespaced so a mirrored legacy user can never collide with, or be
        # mistaken for, a person who signs in to the companion.
        row["username"] = f"legacy:{name}"
        row["full_name"] = row.get("full_name") or name
        row["role"] = "operator"
        row["password_hash"] = ""        # cannot sign in, by design
        row.setdefault("email", "")
        row["date_added"] = utc_now_iso()
        return row

    def t_transaction(self, row):
        remarks = row.pop("remarks", None) or ""
        comments = row.pop("comments", None) or ""
        row["remarks"] = remarks or comments
        if row.get("user_id") in (None, ""):
            row["user_id"] = UNKNOWN_USER_ID
        row["user_date"] = (row.get("user_date") or row.get("transaction_date") or "")[:10]
        row["voided"] = 0
        row["is_reversal"] = 0
        if self.from_sign < 0 and row.get("from_qty") not in (None, ""):
            row["from_qty"] = -float(row["from_qty"])
        for analyte, value in readings_from_remark(row["remarks"]).items():
            self.readings.append((row["transaction_id"], analyte, value))
        return row

    def t_pending_shipment(self, row):
        row["shipped"] = _flag(row.get("shipped"), 0)
        row["cancelled"] = 0
        return row

    def t_qc(self, row):
        if row.get("flash_pf") not in (None, ""):
            row["flash_pf"] = str(row["flash_pf"]).strip().upper()[:1] or None
        return row


# -------------------------------------------------------------- local side


def _local_columns(table: str, conn) -> dict[str, dict[str, Any]]:
    return {r["name"]: r for r in db.query(f'PRAGMA table_info("{table}")', (), conn)}


def _fill_not_null(row: dict[str, Any], info: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Legacy NULLs where the local schema says NOT NULL become the local
    default, so a mirror never fails on a value the legacy app tolerated."""

    for name, col in info.items():
        if col["pk"] or not col["notnull"] or row.get(name) is not None:
            continue
        if name not in row and col["dflt_value"] is not None:
            continue                       # the INSERT will apply the default
        default = col["dflt_value"]
        if default is not None:
            row[name] = default.strip("'") if isinstance(default, str) else default
        elif name.startswith("date_"):
            row[name] = utc_now_iso()
        elif "INT" in (col["type"] or "").upper() or "REAL" in (col["type"] or "").upper():
            row[name] = 0
        else:
            row[name] = ""
    return row


def _upsert(table: str, rows: list[dict[str, Any]], info, conn) -> None:
    if not rows:
        return
    columns = sorted({c for row in rows for c in row if c in info})
    marks = ", ".join("?" for _ in columns)
    names = ", ".join(f'"{c}"' for c in columns)
    db.executemany(
        f'INSERT OR REPLACE INTO "{table}" ({names}) VALUES ({marks})',
        [[row.get(c) for c in columns] for row in rows],
        conn,
    )


# ------------------------------------------------------------- watermarks


def _window_low(key: str, days: int, conn) -> int | None:
    cutoff = (utc_now() - dt.timedelta(days=days)).replace(microsecond=0).isoformat()
    row = db.query_one(
        "SELECT max_id FROM legacy_watermark WHERE table_key = ? AND recorded_at <= ?"
        " ORDER BY recorded_at DESC LIMIT 1",
        (key, cutoff),
        conn,
    ) or db.query_one(
        # Before the window has filled — the first N days after setup — reach
        # back to the first sync rather than re-reading the whole table.
        "SELECT max_id FROM legacy_watermark WHERE table_key = ? ORDER BY recorded_at LIMIT 1",
        (key,),
        conn,
    )
    return None if row is None else int(row["max_id"])


def _record_watermark(key: str, max_id: int, conn) -> None:
    db.execute(
        "INSERT OR REPLACE INTO legacy_watermark (table_key, recorded_at, max_id) VALUES (?, ?, ?)",
        (key, utc_now().replace(microsecond=0).isoformat(), max_id),
        conn,
    )
    db.execute(
        "DELETE FROM legacy_watermark WHERE table_key = ? AND recorded_at < ?",
        (key, (utc_now() - dt.timedelta(days=400)).isoformat()),
        conn,
    )


# ------------------------------------------------------------------- run


def _select(res: TableResolution, physical: str, low: int | None) -> tuple[str, tuple]:
    cols = ", ".join(f"[{legacy}] AS [{local}]" for local, legacy in res.columns.items())
    sql = f"SELECT {cols} FROM {physical}"
    if low is not None and res.table.id_column:
        id_col = next(
            legacy for legacy in res.columns.values()
            if legacy.lower() == res.table.id_column.lower()
        )
        return f"{sql} WHERE [{id_col}] >= ?", (low,)
    return sql, ()


def _local_id(res: TableResolution) -> str | None:
    if not res.table.id_column:
        return None
    return next(
        (local for local, legacy in res.columns.items()
         if legacy.lower() == res.table.id_column.lower()),
        None,
    )


def run(
    legacy: ReadOnlyConnection,
    resolution: Resolution,
    conn=None,
    *,
    full: bool = False,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict[str, Any]:
    """Mirror the legacy tables into the local store. Returns a report."""

    if not resolution.ok:
        blocking = [t.table.key for t in resolution.tables.values() if t.blocking]
        raise IntegrationError(
            "The legacy map does not match the database: "
            + ", ".join(blocking)
            + ". Run `python -m pims legacy check` for the detail.",
            adapter="legacy",
            blocking=blocking,
        )

    conn = conn or db.get_connection()
    transforms = _Transforms()
    report: dict[str, Any] = {"full": full, "started_at": utc_now_iso(), "tables": {}}

    txn = resolution.tables.get("transaction")
    if txn is not None and txn.usable:
        sign = _from_qty_sign(legacy, txn)
        transforms.from_sign = -1 if sign["negative"] else 1
        report["from_qty_sign"] = sign

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        with db.transaction(conn):
            for key, res in resolution.tables.items():
                if not res.usable:
                    report["tables"][key] = {"skipped": True}
                    continue
                report["tables"][key] = _mirror_table(
                    key, res, legacy, transforms, conn, full=full, window_days=window_days
                )
            report["reversals"] = _link_reversals(conn)
            report["readings_from_remarks"] = _store_readings(transforms.readings, conn)
            report["placeholder_users"] = _placeholder_users(conn)
            report["specs_applied"] = _apply_known_specs(conn)
            report["unclassified_transaction_types"] = transforms.unclassified_types
            report["orphans"] = _orphans(conn)
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    report["finished_at"] = utc_now_iso()
    db.execute(
        "INSERT OR REPLACE INTO system_setting (key, value, description) VALUES (?, ?, ?)",
        ("legacy.last_sync", report["finished_at"],
         "When the read-only mirror of ProductionData last completed"),
        conn,
    )
    db.execute(
        "INSERT OR REPLACE INTO system_setting (key, value, description) VALUES (?, ?, ?)",
        ("legacy.last_report", json.dumps(report, default=str)[:20_000],
         "Summary of the last mirror run"),
        conn,
    )
    return report


def _mirror_table(key, res, legacy, transforms, conn, *, full, window_days) -> dict[str, Any]:
    table = res.table
    info = _local_columns(table.local, conn)
    local_id = _local_id(res)
    reference = key in REFERENCE
    low = None
    if not full and not reference and local_id:
        low = _window_low(key, WINDOWS.get(key, window_days), conn)

    read = written = 0
    seen: set[Any] = set()
    max_id = 0
    batch: list[dict[str, Any]] = []
    for physical in res.readable:
        sql, params = _select(res, physical, low)
        for raw_row in legacy.rows(sql, params):
            read += 1
            row = {k: _value(v) for k, v in raw_row.items()}
            shaped = transforms.apply(key, row)
            if shaped is None:
                continue
            if local_id and shaped.get(local_id) is not None:
                seen.add(shaped[local_id])
                try:
                    max_id = max(max_id, int(shaped[local_id]))
                except (TypeError, ValueError):
                    pass
            batch.append(_fill_not_null(shaped, info))
            if len(batch) >= 1_000:
                _upsert(table.local, batch, info, conn)
                written += len(batch)
                batch = []
    _upsert(table.local, batch, info, conn)
    written += len(batch)

    removed = 0
    if key == "user_plant_access":
        pass          # handled by the delete-then-insert below
    elif (full or reference) and local_id:
        # Rows the legacy database no longer has. Mirrored users only ever
        # below the local-account floor, so a person's companion login is
        # never removed by a sync.
        existing = [
            r[local_id] for r in db.query(
                f'SELECT "{local_id}" FROM "{table.local}"'
                + (f' WHERE "{local_id}" < {LOCAL_ACCOUNT_FLOOR} AND "{local_id}" <> {UNKNOWN_USER_ID}'
                   if key == "user" else ""),
                (), conn,
            )
        ]
        gone = [value for value in existing if value not in seen]
        for chunk in range(0, len(gone), 500):
            part = gone[chunk:chunk + 500]
            db.execute(
                f'DELETE FROM "{table.local}" WHERE "{local_id}" IN ({",".join("?" for _ in part)})',
                part, conn,
            )
        removed = len(gone)

    if local_id and not reference:
        top = db.scalar(f'SELECT MAX("{local_id}") FROM "{table.local}"', (), conn) or 0
        _record_watermark(key, max(int(top), max_id), conn)

    return {
        "read": read,
        "written": written,
        "removed": removed,
        "mode": "full" if (full or reference) else ("window" if low is not None else "initial"),
        "from_id": low,
        "local_rows": db.scalar(f'SELECT COUNT(*) FROM "{table.local}"', (), conn),
    }


def _from_qty_sign(legacy: ReadOnlyConnection, res: TableResolution) -> dict[str, Any]:
    """Does the legacy database store quantities leaving a location as
    negative numbers?

    Both of the legacy exports show them negative (a PRODUCED row reads
    From_Qty -4689, To_Qty 4689, and its reversal +4689). Whether that is how
    the rows are stored, or only how the export shows them, is not visible
    from outside — so it is measured: the most recent rows with a from
    quantity, read by primary key (cheap on the clustered index). Nine in ten
    negative and the mirror flips them; the local ledger keeps them positive.
    """

    column = res.columns.get("from_qty")
    id_column = next(
        (legacy_col for local, legacy_col in res.columns.items() if local == "transaction_id"), None,
    )
    if not column or not id_column or not res.readable:
        return {"sampled": 0, "negative": False}
    physical = res.readable[0]
    if legacy.dialect == "mssql":
        sql = (f"SELECT TOP 2000 [{column}] AS q FROM {physical} "
               f"WHERE [{column}] <> 0 ORDER BY [{id_column}] DESC")
    else:
        sql = (f"SELECT [{column}] AS q FROM {physical} "
               f"WHERE [{column}] <> 0 ORDER BY [{id_column}] DESC LIMIT 2000")
    values = [row["q"] for row in legacy.rows(sql)]
    below = sum(1 for v in values if v is not None and float(v) < 0)
    return {
        "sampled": len(values),
        "below_zero": below,
        "negative": bool(values) and below >= 0.9 * len(values),
    }


def _link_reversals(conn) -> dict[str, int]:
    """Turn legacy REVERSAL rows into the local shape.

    The legacy PIMS undoes a posting with a new row of type REVERSAL whose
    parent is the row it undoes — which is where the export's "PRODUCED -
    REVERSAL" and "PROD-LOAD - REVERSAL" names come from. Locally a reversal
    carries its parent's type and is_reversal = 1, and the parent is marked
    voided, so an order's progress stops counting a posting that was undone.
    Balances were right either way: both rows are summed.
    """

    reversal_types = [
        r["transaction_type_id"]
        for r in db.query("SELECT transaction_type_id FROM transaction_type WHERE kind = 'REVERSAL'", (), conn)
    ]
    if not reversal_types:
        return {"reversals": 0, "voided": 0}
    marks = ", ".join("?" for _ in reversal_types)
    db.execute(
        f"""
        UPDATE inventory_transaction
        SET is_reversal = 1,
            transaction_type_id = COALESCE(
                (SELECT p.transaction_type_id FROM inventory_transaction p
                 WHERE p.transaction_id = inventory_transaction.parent_transaction_id),
                transaction_type_id)
        WHERE transaction_type_id IN ({marks}) AND parent_transaction_id IS NOT NULL
        """,
        reversal_types,
        conn,
    )
    voided = db.affected(
        """
        UPDATE inventory_transaction SET voided = 1
        WHERE voided = 0 AND transaction_id IN (
            SELECT parent_transaction_id FROM inventory_transaction
            WHERE is_reversal = 1 AND parent_transaction_id IS NOT NULL)
        """,
        (),
        conn,
    )
    return {
        "reversals": db.scalar("SELECT COUNT(*) FROM inventory_transaction WHERE is_reversal = 1", (), conn),
        "newly_voided": voided,
    }


def _store_readings(readings: list[tuple[int, str, float]], conn) -> int:
    if not readings:
        return 0
    db.executemany(
        "INSERT OR REPLACE INTO txn_reading (transaction_id, analyte, value, source)"
        " VALUES (?, ?, ?, 'remarks')",
        readings,
        conn,
    )
    return len(readings)


def _placeholder_users(conn) -> int:
    """A local user row for every legacy user id the ledger mentions but the
    user table did not supply, so attribution survives an unreadable
    FECoreData."""

    missing = db.query(
        """
        SELECT DISTINCT t.user_id FROM inventory_transaction t
        LEFT JOIN app_user u ON u.user_id = t.user_id
        WHERE u.user_id IS NULL
        """,
        (), conn,
    )
    for row in missing:
        uid = row["user_id"]
        label = "unknown" if uid == UNKNOWN_USER_ID else f"user-{uid}"
        db.execute(
            "INSERT OR IGNORE INTO app_user (user_id, username, full_name, role, password_hash, date_added)"
            " VALUES (?, ?, ?, 'operator', '', ?)",
            (uid, f"legacy:{label}", f"Legacy user {uid}" if uid else "Unknown (legacy)", utc_now_iso()),
            conn,
        )
    return len(missing)


def _apply_known_specs(conn) -> int:
    """Attach the transcribed product limits to mirrored materials by number.

    Limits are not in the legacy PIMS database — they came from the LIMS limit
    sheets — so they live only in the companion and are never overwritten by a
    sync. A material the sheets do not cover shows up in the product-setup
    data-quality check, which is where it belongs.
    """

    from .. import seed

    by_number = {number: family for number, _d, family, _t, _p in seed.MATERIALS}
    applied = 0
    for row in db.query("SELECT material_id, number, family FROM material", (), conn):
        family = by_number.get(row["number"])
        if not family:
            continue
        if row["family"] != family:
            db.update("material", {"material_id": row["material_id"]}, {"family": family}, conn)
        for analyte in seed.FAMILY_TESTS.get(family, []):
            db.execute(
                "INSERT OR IGNORE INTO material_test (material_id, analyte, required) VALUES (?, ?, 1)",
                (row["material_id"], analyte), conn,
            )
        for analyte, (lo, hi, review, note) in seed.FAMILY_SPECS.get(family, {}).items():
            db.execute(
                "INSERT OR IGNORE INTO material_spec"
                " (material_id, analyte, min_value, max_value, source, note, needs_review)"
                " VALUES (?, ?, ?, ?, 'QC sheet', ?, ?)",
                (row["material_id"], analyte, lo, hi, note, review), conn,
            )
        applied += 1
    return applied


def _orphans(conn) -> dict[str, int]:
    """References the legacy data makes to rows it does not have."""

    checks = {
        "transactions_with_unknown_location": """
            SELECT COUNT(*) FROM inventory_transaction t
            WHERE (t.from_location_id IS NOT NULL AND t.from_location_id NOT IN (SELECT location_id FROM location))
               OR (t.to_location_id IS NOT NULL AND t.to_location_id NOT IN (SELECT location_id FROM location))""",
        "transactions_with_unknown_material": """
            SELECT COUNT(*) FROM inventory_transaction t
            WHERE (t.from_material_id IS NOT NULL AND t.from_material_id NOT IN (SELECT material_id FROM material))
               OR (t.to_material_id IS NOT NULL AND t.to_material_id NOT IN (SELECT material_id FROM material))""",
        "transactions_with_unknown_order": """
            SELECT COUNT(*) FROM inventory_transaction t
            WHERE t.order_id IS NOT NULL AND t.order_id NOT IN (SELECT order_id FROM "order")""",
        "qc_with_unknown_order": """
            SELECT COUNT(*) FROM qc WHERE order_id NOT IN (SELECT order_id FROM "order")""",
        "staged_loads_with_unknown_transaction": """
            SELECT COUNT(*) FROM pending_shipment
            WHERE transaction_id NOT IN (SELECT transaction_id FROM inventory_transaction)""",
    }
    return {name: int(db.scalar(sql, (), conn) or 0) for name, sql in checks.items()}
