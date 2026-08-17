"""Great Plains → PIMS master data and order headers.

GP is the master for customers, vendors and the SO/PO headers that today reach
PIMS through the ``GP_Job_Sync_*`` procedures. This is the same movement of
data, expressed as a job with an adapter, so it can be run and tested here and
repointed at GP without touching the rules.

GP stays the master: sync updates the GP-owned fields on an existing record and
never invents one in the other direction.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from .. import audit, db
from ..errors import IntegrationError
from ..services import jobs
from ..util import today_iso, utc_now_iso


class Source(Protocol):
    name: str

    def customers(self) -> list[dict[str, Any]]: ...
    def vendors(self) -> list[dict[str, Any]]: ...
    def orders(self) -> list[dict[str, Any]]: ...


class FileSource:
    """A JSON export — the shape an SSIS extract or a nightly BCP would drop.

    ``{"customers": [...], "vendors": [...], "orders": [...]}`` with GP keys:
    ``CUSTNMBR`` / ``VENDORID`` / ``SOPNUMBE``.
    """

    name = "file"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _load(self) -> dict[str, list[dict]]:
        if not self.path.exists():
            raise IntegrationError(f"GP export not found: {self.path}", path=str(self.path))
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise IntegrationError(f"GP export is not valid JSON: {exc}", path=str(self.path)) from exc

    def customers(self) -> list[dict]:
        return self._load().get("customers", [])

    def vendors(self) -> list[dict]:
        return self._load().get("vendors", [])

    def orders(self) -> list[dict]:
        return self._load().get("orders", [])


class StubSource:
    """Small in-memory export, so the job runs in tests and the sandbox."""

    name = "stub"

    def __init__(self, payload: dict[str, list[dict]] | None = None) -> None:
        self.payload = payload or {
            "customers": [
                {
                    "CUSTNMBR": "FEC-4410",
                    "CUSTNAME": "PRAIRIE FEED & GRAIN - ADEL",
                    "CITY": "Adel",
                    "STATE": "IA",
                }
            ],
            "vendors": [
                {
                    "VENDORID": "V-1490",
                    "VENDNAME": "SOUTH FORK RENDERING",
                    "CITY": "Ames",
                    "STATE": "IA",
                }
            ],
            "orders": [],
        }

    def customers(self) -> list[dict]:
        return self.payload.get("customers", [])

    def vendors(self) -> list[dict]:
        return self.payload.get("vendors", [])

    def orders(self) -> list[dict]:
        return self.payload.get("orders", [])


def _upsert_customer(row: dict, conn) -> str:
    key = str(row.get("CUSTNMBR") or "").strip()
    if not key:
        raise IntegrationError("Customer row has no CUSTNMBR.", row=row)
    values = {
        "name": row.get("CUSTNAME") or row.get("SHRTNAME") or key,
        "city": row.get("CITY") or "",
        "state": row.get("STATE") or "",
        "active": 0 if str(row.get("INACTIVE", "0")) in {"1", "True", "true"} else 1,
    }
    existing = db.query_one(
        "SELECT customer_id, name, city, state, active FROM customer WHERE gp_custnmbr = ?",
        (key,),
        conn,
    )
    if existing is None:
        next_id = (db.scalar("SELECT MAX(customer_id) FROM customer", (), conn) or 0) + 1
        db.insert("customer", {"customer_id": next_id, "gp_custnmbr": key, **values}, conn)
        return "created"
    changed = {k: v for k, v in values.items() if existing.get(k) != v}
    if not changed:
        return "unchanged"
    db.update("customer", {"customer_id": existing["customer_id"]}, changed, conn)
    return "updated"


def _upsert_vendor(row: dict, conn) -> str:
    key = str(row.get("VENDORID") or "").strip()
    if not key:
        raise IntegrationError("Vendor row has no VENDORID.", row=row)
    values = {
        "name": row.get("VENDNAME") or key,
        "city": row.get("CITY") or "",
        "state": row.get("STATE") or "",
        "active": 0 if str(row.get("INACTIVE", "0")) in {"1", "True", "true"} else 1,
    }
    existing = db.query_one(
        "SELECT vendor_id, name, city, state, active FROM vendor WHERE gp_vendorid = ?",
        (key,),
        conn,
    )
    if existing is None:
        next_id = (db.scalar("SELECT MAX(vendor_id) FROM vendor", (), conn) or 0) + 1
        db.insert("vendor", {"vendor_id": next_id, "gp_vendorid": key, **values}, conn)
        return "created"
    changed = {k: v for k, v in values.items() if existing.get(k) != v}
    if not changed:
        return "unchanged"
    db.update("vendor", {"vendor_id": existing["vendor_id"]}, changed, conn)
    return "updated"


def _import_order(row: dict, conn) -> str:
    """Create a PIMS order from a GP header, if it is not already here.

    Matched on ``order_reference`` = the GP document number, so re-running the
    job is a no-op rather than a duplicate order.
    """

    from ..services import jobs as jobs_service
    from ..services import orders as orders_service

    reference = str(row.get("SOPNUMBE") or row.get("PONUMBER") or "").strip()
    if not reference:
        raise IntegrationError("Order row has no document number.", row=row)
    if db.query_one(
        'SELECT order_id FROM "order" WHERE order_reference = ?', (reference,), conn
    ):
        return "unchanged"

    material = db.query_one(
        "SELECT material_id FROM material WHERE number = ?", (str(row.get("ITEMNMBR") or ""),), conn
    )
    plant = db.query_one("SELECT plant_id FROM plant WHERE code = ?", (str(row.get("LOCNCODE") or ""),), conn)
    if material is None or plant is None:
        return "skipped"

    payload: dict[str, Any] = {
        "order_type_id": 3 if row.get("PONUMBER") else 1,
        "plant_id": plant["plant_id"],
        "company_id": 1,
        "order_date": str(row.get("DOCDATE") or today_iso())[:10],
        "due_date": str(row.get("REQSHIPDATE") or row.get("DOCDATE") or today_iso())[:10],
        "order_reference": reference,
        "material_one_id": material["material_id"],
        "material_one_quantity": float(row.get("QUANTITY") or 0),
    }
    if row.get("CUSTNMBR"):
        customer = db.query_one(
            "SELECT customer_id FROM customer WHERE gp_custnmbr = ?",
            (str(row["CUSTNMBR"]).strip(),),
            conn,
        )
        if customer is None:
            return "skipped"
        payload["customer_id"] = customer["customer_id"]
    if row.get("VENDORID"):
        vendor = db.query_one(
            "SELECT vendor_id FROM vendor WHERE gp_vendorid = ?",
            (str(row["VENDORID"]).strip(),),
            conn,
        )
        if vendor is None:
            return "skipped"
        payload["vendor_id"] = vendor["vendor_id"]

    orders_service.create(payload, jobs_service.system_user(conn), conn)
    return "created"


def sync(source: Source | None = None, dry_run: bool = False, conn=None) -> dict[str, Any]:
    source = source or StubSource()
    counts = {
        "customers": {"created": 0, "updated": 0, "unchanged": 0},
        "vendors": {"created": 0, "updated": 0, "unchanged": 0},
        "orders": {"created": 0, "unchanged": 0, "skipped": 0},
    }
    with jobs.record_run("gp_sync", conn) as detail:
        customers = list(source.customers())
        vendors = list(source.vendors())
        order_rows = list(source.orders())
        if dry_run:
            return {
                "adapter": source.name,
                "dry_run": True,
                "would_read": {
                    "customers": len(customers),
                    "vendors": len(vendors),
                    "orders": len(order_rows),
                },
            }
        with db.transaction(conn):
            for row in customers:
                counts["customers"][_upsert_customer(row, conn)] += 1
            for row in vendors:
                counts["vendors"][_upsert_vendor(row, conn)] += 1
            for row in order_rows:
                counts["orders"][_import_order(row, conn)] += 1
        detail.update(counts)

    audit.record(
        username="system",
        action="gp.sync",
        entity="integration",
        entity_id="gp",
        summary=(
            f"GP sync: {counts['customers']['created']} new customer(s), "
            f"{counts['vendors']['created']} new vendor(s), "
            f"{counts['orders']['created']} new order(s)"
        ),
        detail=counts,
        conn=conn,
    )
    return {"adapter": source.name, "counts": counts, "ran_at": utc_now_iso()}
