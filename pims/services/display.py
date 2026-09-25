"""The tank board: every tank at a plant, readable from across the room.

Two parts. :func:`tanks` turns the ledger into one tile per tank — what it
holds, how full, and whether that is abnormal. Display tokens let a board run
on a screen nobody is signed in to: a token can read one plant's tank levels
and nothing else, lasts 90 days, and can be revoked. It is stored hashed, so
the database never holds a usable token.

The tile's ``state`` follows the control-room convention (ISA-101): normal is
unremarkable, and only abnormal states — nearly full, over capacity, nearly
empty — carry a colour. A board where everything is coloured is a board
nobody reads.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from typing import Any

from .. import audit, db
from ..errors import AuthError, NotFound, ValidationError
from ..security import require_permission, require_plant
from ..util import utc_now, utc_now_iso
from . import departments as departments_service, inventory

TOKEN_DAYS = 90

#: Location types that are shown as tanks.
TANK_TYPES = ("Tank", "Blend", "Acid")

HIGH_PERCENT = 95.0
WARN_PERCENT = 85.0
LOW_PERCENT = 5.0


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _state(total: float, capacity: float | None) -> str:
    if total < -0.5:
        return "negative"
    if not capacity:
        return "normal" if total > 0.5 else "empty"
    percent = total / capacity * 100.0
    if percent > 100.0 + 0.01:
        return "over"
    if percent >= HIGH_PERCENT:
        return "high"
    if percent >= WARN_PERCENT:
        return "warn"
    if total <= 0.5:
        return "empty"
    if percent < LOW_PERCENT:
        return "low"
    return "normal"


def tanks(plant_id: int, conn=None, department_id: int | None = None) -> dict[str, Any]:
    """One tile per tank at the plant, in tank-number order.

    With ``department_id``, only that department's tanks: those holding a
    material it handles, and the vessels its batches run in.
    """

    plant = db.query_one("SELECT plant_id, code, name FROM plant WHERE plant_id = ?", (plant_id,), conn)
    if plant is None:
        raise NotFound(f"Plant {plant_id} was not found.")
    marks = ", ".join("?" for _ in TANK_TYPES)
    locations = db.query(
        f"""
        SELECT l.location_id, l.number, l.description, l.max_capacity, lt.name AS kind
        FROM location l
        JOIN location_type lt ON lt.location_type_id = l.location_type_id
        WHERE l.plant_id = ? AND l.active = 1 AND lt.name IN ({marks})
        ORDER BY l.number
        """,
        [plant_id, *TANK_TYPES],
        conn,
    )
    balances: dict[int, list[dict[str, Any]]] = {}
    for row in inventory.location_balance(plant_id=plant_id, conn=conn):
        balances.setdefault(row["location_id"], []).append(row)
    last_moved = {
        row["location_id"]: row["at"]
        for row in db.query(
            """
            SELECT location_id, MAX(at) AS at FROM (
                SELECT from_location_id AS location_id, transaction_date AS at
                FROM inventory_transaction WHERE plant_id = ? AND from_location_id IS NOT NULL
                UNION ALL
                SELECT to_location_id, transaction_date
                FROM inventory_transaction WHERE plant_id = ? AND to_location_id IS NOT NULL
            ) GROUP BY location_id
            """,
            (plant_id, plant_id),
            conn,
        )
    }

    tiles = []
    for loc in locations:
        rows = sorted(balances.get(loc["location_id"], []), key=lambda r: -r["balance"])
        total = round(sum(r["balance"] for r in rows), 2)
        capacity = loc["max_capacity"]
        tiles.append(
            {
                "location_id": loc["location_id"],
                "number": loc["number"],
                "description": loc["description"],
                "kind": loc["kind"],
                "capacity": capacity,
                "total": total,
                "percent_full": round(total / capacity * 100.0, 1) if capacity else None,
                "room": round(capacity - total, 2) if capacity else None,
                "state": _state(total, capacity),
                "products": [
                    {
                        "number": r["material_number"],
                        "description": r["material_description"],
                        "lbs": r["balance"],
                    }
                    for r in rows
                ],
                # More than one product in one tank is unusual enough to say.
                "mixed": len([r for r in rows if r["balance"] > 0.5]) > 1,
                "last_moved": last_moved.get(loc["location_id"]),
            }
        )
    # A reactor mid-batch says which stage it is at and for how long: a tank
    # at 40% that is settling is not a tank at 40% that is free.
    from . import process as process_service

    for batch in process_service.open_batches(plant_id, conn=conn):
        tile = next((t for t in tiles if t["location_id"] == batch["vessel_id"]), None)
        if tile is not None:
            tile["batch"] = {
                "batch_id": batch["batch_id"],
                "stage": batch["status"],
                "label": batch["stage_label"],
                "minutes": batch["stage_minutes"],
            }
            # Soap and acid together in a reactor is the point, not a mix-up.
            tile["mixed"] = False

    department = None
    if department_id:
        department = departments_service.get(int(department_id), conn)
        handled = departments_service.materials(int(department_id), plant_id, conn)
        vessels = departments_service.vessel_types(int(department_id), conn)
        keep = {
            loc["location_id"]
            for loc in locations
            if loc["kind"] in vessels
            or any(
                r["material_id"] in handled and r["balance"] > 0.5
                for r in balances.get(loc["location_id"], [])
            )
        }
        tiles = [tile for tile in tiles if tile["location_id"] in keep]
    return {
        "plant": plant,
        "department": department,
        "generated_at": utc_now_iso(),
        "tanks": tiles,
        "abnormal": sum(1 for t in tiles if t["state"] in {"over", "high", "negative"}),
    }


# ------------------------------------------------------------------ tokens


def mint(plant_id: int, user: dict, label: str = "", conn=None) -> dict[str, Any]:
    """A token a tank board can run on. Anyone who can see the plant may make one."""

    require_permission(user, "txn.read")
    require_plant(user, int(plant_id), conn)
    token = secrets.token_urlsafe(24)
    expires = (utc_now() + dt.timedelta(days=TOKEN_DAYS)).replace(microsecond=0)
    token_id = db.insert(
        "display_token",
        {
            "token_hash": _hash(token),
            "plant_id": int(plant_id),
            "label": (label or "Tank board").strip()[:80],
            "created_by": user["username"],
            "created_at": utc_now_iso(),
            "expires_at": expires.isoformat(),
        },
        conn,
    )
    audit.record(
        username=user["username"],
        action="display.mint",
        entity="display_token",
        entity_id=token_id,
        summary=f"Tank board opened for plant {plant_id}",
        detail={"label": label, "expires_at": expires.isoformat()},
        conn=conn,
    )
    return {"token": token, "token_id": token_id, "plant_id": int(plant_id), "expires_at": expires.isoformat()}


def plant_for_token(token: str, conn=None) -> int:
    """The one plant this token may see, or refuse."""

    if not token:
        raise AuthError("This board needs a display link. Open it again from PIMS.")
    row = db.query_one("SELECT * FROM display_token WHERE token_hash = ?", (_hash(token),), conn)
    if row is None or row["revoked"]:
        raise AuthError("This board's link has been switched off. Open it again from PIMS.")
    if row["expires_at"] < utc_now_iso():
        raise AuthError("This board's link has expired. Open it again from PIMS.")
    db.execute(
        "UPDATE display_token SET last_seen = ? WHERE token_id = ?",
        (utc_now_iso(), row["token_id"]),
        conn,
    )
    return int(row["plant_id"])


def list_tokens(plant_id: int | None = None, conn=None) -> list[dict[str, Any]]:
    sql = (
        "SELECT token_id, plant_id, label, created_by, created_at, expires_at, last_seen, revoked"
        " FROM display_token"
    )
    params: list[Any] = []
    if plant_id:
        sql += " WHERE plant_id = ?"
        params.append(plant_id)
    return db.query(sql + " ORDER BY token_id DESC", params, conn)


def revoke(token_id: int, user: dict, conn=None) -> dict[str, Any]:
    require_permission(user, "support.read")
    if not db.query_one("SELECT token_id FROM display_token WHERE token_id = ?", (token_id,), conn):
        raise ValidationError(f"Display link {token_id} does not exist.")
    db.update("display_token", {"token_id": token_id}, {"revoked": 1}, conn)
    audit.record(
        username=user["username"],
        action="display.revoke",
        entity="display_token",
        entity_id=token_id,
        summary=f"Tank board link {token_id} switched off",
        conn=conn,
    )
    return {"token_id": token_id, "revoked": True}
