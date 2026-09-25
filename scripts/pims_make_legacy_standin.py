"""Build a stand-in for the legacy ProductionData database, as SQLite.

The companion's tests and rehearsals run against this instead of the real
server. It has the legacy table and column names (from pims/legacy/schema.py,
first spelling of each), is filled from the demo dataset, and carries the
quirks the real one does — the ones the August 2026 exports showed:

  - transaction type names as the legacy PIMS spells them (PRODUCED,
    MOVE-LOAD, PROD-LOAD, SHIP-LEAVE, SHIPADJ, MOVEMENT, RECEIVED, SHRINKAGE)
    and a type called REVERSAL whose rows undo their parent row;
  - quantities out of a location stored negative (From_qty -4689, To_qty
    4689), and a reversal's the other way round;
  - a sales order loaded PROD-LOAD style — components straight from tanks
    onto the trailer as the ordered product — with one component reversed;
  - transactions split across [transaction] and [Transaction_Archive], a
    type nobody classified, an orphaned location reference, a transaction
    with no user, users in a separate database.

    python scripts/pims_make_legacy_standin.py OUT_DIR
        -> OUT_DIR/ProductionData.db, OUT_DIR/FECoreData.db

Point the companion at it with
    PIMS_LEGACY_DSN="sqlite:///OUT_DIR/ProductionData.db|OUT_DIR/FECoreData.db"
    PIMS_LEGACY_USERS_TABLE="fecore.[User]"
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pims.legacy.schema import LEGACY_MAP  # noqa: E402

LEGACY_TYPE_NAMES = {
    "RECEIVE": "RECEIVED", "PRODUCE": "PRODUCED", "MOVE": "MOVEMENT",
    "LOAD": "MOVE-LOAD", "SHIP": "SHIP-LEAVE", "SHRINK": "SHRINKAGE",
    "ADJUST": "SHIPADJ",
}
#: Types the local seed does not have but the legacy database does.
EXTRA_TYPES = {97: "PROD-LOAD", 98: "REVERSAL", 99: "Tote Fill"}
PROD_LOAD, REVERSAL = 97, 98
#: The synthetic PROD-LOAD sales order, and its rows.
PROD_LOAD_ORDER = 9_100_001


def build(source: sqlite3.Connection, out_dir: Path, *, negative_from: bool = True) -> dict[str, Any]:
    """Write the stand-in. Returns row counts, plus ``synthetic``: the
    balance changes and order totals of the rows added on top of the source,
    so a test can say what the mirror should end up with."""

    out_dir.mkdir(parents=True, exist_ok=True)
    main_path, users_path = out_dir / "ProductionData.db", out_dir / "FECoreData.db"
    for path in (main_path, users_path):
        if path.exists():
            path.unlink()
    main = sqlite3.connect(main_path)
    users = sqlite3.connect(users_path)
    counts: dict[str, int] = {}

    for table in LEGACY_MAP.tables:
        legacy_cols = [c.candidates[0] for c in table.columns]
        local_cols = [c.local for c in table.columns]
        target = users if table.key == "user" else main
        for physical in table.physical:
            name = physical.split(".")[-1].strip("[]")
            ddl = ", ".join(f"[{c}]" for c in legacy_cols)
            target.execute(f"CREATE TABLE [{name}] ({ddl})")

        rows = _source_rows(source, table.key, local_cols)
        if table.key == "transaction":
            rows = _legacy_transactions(rows, negative_from)
            ids = sorted(r["transaction_id"] for r in rows)
            cut = ids[len(ids) // 2] if ids else 0
            live = [r for r in rows if r["transaction_id"] >= cut]
            archived = [r for r in rows if r["transaction_id"] < cut]
            _insert(target, "transaction", legacy_cols, local_cols, live)
            _insert(target, "Transaction_Archive", legacy_cols, local_cols, archived)
        else:
            name = table.physical[0].split(".")[-1].strip("[]")
            _insert(target, name, legacy_cols, local_cols, rows)
        counts[table.key] = len(rows)

    # The quirks.
    for type_id, name in EXTRA_TYPES.items():
        main.execute("INSERT INTO [TransType] VALUES (?, ?)", (type_id, name))
    main.execute(
        "INSERT INTO [transaction] ([Transaction_id], [Transtype_id], [Plant_id], "
        "[Transaction_date], [User_id], [To_location_id], [To_material_id], [To_qty], [Remarks])"
        " VALUES (9000001, 1, 1, '2026-01-05T08:00:00', NULL, 99999, 8, 100, 'orphaned location')"
    )
    counts["synthetic"] = _prod_load_order(source, main, negative_from)
    main.commit()
    users.commit()
    main.close()
    users.close()
    return counts


def _legacy_transactions(rows: list[dict], negative_from: bool) -> list[dict]:
    """The local ledger as the legacy PIMS would have written it.

    A local void writes a reversal with the endpoints swapped and positive
    quantities. The legacy PIMS writes a REVERSAL row with the parent's
    endpoints and the quantities negated. Both sum to the same balances.
    """

    by_id = {r["transaction_id"]: r for r in rows}
    out = []
    for row in rows:
        row = dict(row)
        parent = by_id.get(row.get("parent_transaction_id"))
        if row.pop("is_reversal", 0) and parent is not None:
            row.update(
                transaction_type_id=REVERSAL,
                from_location_id=parent["from_location_id"], from_material_id=parent["from_material_id"],
                from_qty=-(parent["from_qty"] or 0) if parent["from_qty"] is not None else None,
                to_location_id=parent["to_location_id"], to_material_id=parent["to_material_id"],
                to_qty=-(parent["to_qty"] or 0) if parent["to_qty"] is not None else None,
            )
        row.pop("voided", None)
        if negative_from and row.get("from_qty") is not None:
            row["from_qty"] = -row["from_qty"]
        out.append(row)
    return out


def _prod_load_order(source, main, negative_from: bool) -> dict[str, Any]:
    """A sales order loaded the PROD-LOAD way: two components pumped from
    their tanks straight onto the trailer as the ordered product, and a third
    (caustic, say) posted and then reversed."""

    stock = _rows(source, """
        WITH ledger AS (
            SELECT to_location_id AS loc, to_material_id AS mat, to_qty AS q FROM inventory_transaction
            WHERE to_location_id IS NOT NULL
            UNION ALL
            SELECT from_location_id, from_material_id, -from_qty FROM inventory_transaction
            WHERE from_location_id IS NOT NULL
        )
        SELECT ledger.loc, ledger.mat, SUM(ledger.q) AS lbs
        FROM ledger JOIN location l ON l.location_id = ledger.loc
        JOIN location_type lt ON lt.location_type_id = l.location_type_id
        WHERE l.plant_id = 1 AND lt.name = 'Tank'
        GROUP BY ledger.loc, ledger.mat HAVING SUM(ledger.q) > 30000
        ORDER BY ledger.loc, ledger.mat LIMIT 2
    """)
    trailer = _rows(source, """
        SELECT l.location_id FROM location l JOIN location_type lt ON lt.location_type_id = l.location_type_id
        WHERE l.plant_id = 1 AND lt.name = 'Trailer' LIMIT 1
    """)[0]["location_id"]
    product = _rows(source, "SELECT material_id FROM material WHERE number = '01021'")
    product_id = product[0]["material_id"] if product else 7
    (a_loc, a_mat, _), (b_loc, b_mat, _) = [(r["loc"], r["mat"], r["lbs"]) for r in stock]

    main.execute(
        'INSERT INTO [Order] ([Order_id], [Ordertype_id], [Plant_id], [Status_id], [Material_one_id],'
        " [Material_one_quantity], [Order_date], [Due_date], [Company_id]) VALUES (?, 1, 1, 1, ?, 45000,"
        " '2026-08-30', '2026-08-31', 1)",
        (PROD_LOAD_ORDER, product_id),
    )
    sign = -1 if negative_from else 1
    rows = [
        # id, type, from loc, from mat, qty, parent
        (9_000_002, PROD_LOAD, a_loc, a_mat, 20_000.0, None),
        (9_000_003, PROD_LOAD, b_loc, b_mat, 24_000.0, None),
        (9_000_004, PROD_LOAD, a_loc, a_mat, 1_000.0, None),
        (9_000_005, REVERSAL, a_loc, a_mat, -1_000.0, 9_000_004),
    ]
    for txn_id, type_id, loc, mat, qty, parent in rows:
        main.execute(
            "INSERT INTO [transaction] ([Transaction_id], [Transtype_id], [Plant_id], [Transaction_date],"
            " [User_id], [Order_id], [From_location_id], [From_material_id], [From_qty],"
            " [To_location_id], [To_material_id], [To_qty], [Parent_transaction_id], [Remarks])"
            " VALUES (?, ?, 1, '2026-08-30T10:00:00', 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (txn_id, type_id, PROD_LOAD_ORDER, loc, mat, sign * qty, trailer, product_id, qty, parent,
             "M=0.84 S=0.1" if txn_id == 9_000_002 else ""),
        )
    return {
        "order_id": PROD_LOAD_ORDER,
        "loaded": 44_000.0,
        "balances": {(a_loc, a_mat): -20_000.0, (b_loc, b_mat): -24_000.0, (trailer, product_id): 44_000.0},
        "reading_transaction": 9_000_002,
    }


def _rows(source, sql: str):
    # A cursor of our own, so the caller's connection is left exactly as it was.
    cursor = source.cursor()
    cursor.row_factory = sqlite3.Row
    return cursor.execute(sql).fetchall()


def _source_rows(source, key: str, local_cols: list[str]) -> list[dict]:
    if key == "transaction_type":
        return [
            {"transaction_type_id": r["transaction_type_id"],
             "name": LEGACY_TYPE_NAMES.get(r["code"], r["code"].title())}
            for r in _rows(source, "SELECT * FROM transaction_type")
        ]
    if key == "user":
        return [
            {"user_id": r["user_id"], "username": r["username"], "full_name": r["full_name"],
             "email": r["email"], "active": r["active"]}
            for r in _rows(source, "SELECT * FROM app_user")
        ]
    if key == "pending_shipment":
        return [
            {"stage_id": r["stage_id"], "order_id": r["order_id"],
             "transaction_id": r["transaction_id"], "trailer_number": r["trailer_number"],
             "quantity": r["quantity"], "shipped": r["shipped"] or r["cancelled"]}
            for r in _rows(source, "SELECT * FROM pending_shipment")
        ]
    table = {
        "order": '"order"', "transaction": "inventory_transaction",
    }.get(key, LEGACY_MAP.get(key).local)
    available = {r[1] for r in _rows(source, f"PRAGMA table_info({table})")}
    out = []
    for r in _rows(source, f"SELECT * FROM {table}"):
        row = {c: (r[c] if c in available else None) for c in local_cols}
        if key == "transaction":
            # Not a legacy column; read here so the reversal can be rewritten
            # the legacy way (see _legacy_transactions).
            row["is_reversal"] = r["is_reversal"]
        out.append(row)
    return out


def _insert(conn, name, legacy_cols, local_cols, rows) -> None:
    if not rows:
        return
    marks = ", ".join("?" for _ in legacy_cols)
    cols = ", ".join(f"[{c}]" for c in legacy_cols)
    conn.executemany(
        f"INSERT INTO [{name}] ({cols}) VALUES ({marks})",
        [[row.get(c) for c in local_cols] for row in rows],
    )


def main() -> int:
    from pims import db
    from pims.config import get_settings

    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "data/legacy-standin")
    settings = get_settings()
    db.init_db(settings)
    counts = build(db.get_connection(), out_dir)
    counts.pop("synthetic", None)
    print(f"stand-in written to {out_dir}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
