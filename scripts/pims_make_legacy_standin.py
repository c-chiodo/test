"""Build a stand-in for the legacy ProductionData database, as SQLite.

The companion's tests and rehearsals run against this instead of the real
server. It has the legacy table and column names (from pims/legacy/schema.py,
first spelling of each), is filled from the demo dataset, and carries the
quirks the real one does: transactions split across [transaction] and
[Transaction_Archive], a transaction type nobody classified, an orphaned
location reference, a transaction with no user, users in a separate database.

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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pims.legacy.schema import LEGACY_MAP  # noqa: E402

LEGACY_TYPE_NAMES = {
    "RECEIVE": "Receive", "PRODUCE": "Produce", "MOVE": "Move",
    "LOAD": "Load Trailer", "SHIP": "Ship Trailer", "SHRINK": "Shrinkage",
    "ADJUST": "Inventory Adjustment",
}


def build(source: sqlite3.Connection, out_dir: Path) -> dict[str, int]:
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
    main.execute("INSERT INTO [TransType] VALUES (99, 'Tote Fill')")
    main.execute(
        "INSERT INTO [transaction] ([Transaction_id], [Transtype_id], [Plant_id], "
        "[Transaction_date], [User_id], [To_location_id], [To_material_id], [To_qty], [Remarks])"
        " VALUES (9000001, 1, 1, '2026-01-05T08:00:00', NULL, 99999, 8, 100, 'orphaned location')"
    )
    main.commit()
    users.commit()
    main.close()
    users.close()
    return counts


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
    print(f"stand-in written to {out_dir}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
