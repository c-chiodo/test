"""The tank board: right numbers, abnormal states flagged, and a display link
that can read one plant's tanks and nothing else."""

from __future__ import annotations

import uuid

import pytest

from pims import db, security
from pims.errors import AuthError, PermissionError_
from pims.services import display, inventory


def _tank(conn, admin_user, capacity: float, fill: float) -> int:
    number = f"DM-TB-{uuid.uuid4().hex[:6].upper()}"
    location = db.insert(
        "location",
        {
            "number": number,
            "description": "Board test tank",
            "location_type_id": db.scalar(
                "SELECT location_type_id FROM location_type WHERE name = 'Tank'", (), conn
            ),
            "plant_id": 1,
            "max_capacity": capacity,
            "active": 1,
        },
        conn,
    )
    if fill:
        inventory.post(
            "RECEIVE",
            {
                "plant_id": 1,
                "to_location_id": location,
                "to_material_id": db.scalar("SELECT material_id FROM material WHERE number = '05001'", (), conn),
                "to_qty": fill,
                "to_bol": f"001-{number}",
            },
            admin_user,
            conn,
        )
    return location


def _tile(board: dict, location_id: int) -> dict:
    return next(t for t in board["tanks"] if t["location_id"] == location_id)


def test_every_tank_at_the_plant_gets_a_tile_with_the_ledger_total(conn):
    board = display.tanks(1, conn)
    tanks = db.query(
        "SELECT l.location_id FROM location l JOIN location_type lt ON lt.location_type_id = l.location_type_id"
        " WHERE l.plant_id = 1 AND l.active = 1 AND lt.name IN ('Tank', 'Blend')",
        (), conn,
    )
    assert {t["location_id"] for t in board["tanks"]} == {t["location_id"] for t in tanks}
    for tile in board["tanks"]:
        assert tile["total"] == pytest.approx(inventory.location_total(tile["location_id"], conn))


@pytest.mark.parametrize("fill, state", [
    (0, "empty"),
    (3_000, "low"),
    (50_000, "normal"),
    (88_000, "warn"),
    (96_000, "high"),
])
def test_only_abnormal_levels_are_flagged(conn, admin_user, fill, state):
    location = _tank(conn, admin_user, 100_000, fill)
    assert _tile(display.tanks(1, conn), location)["state"] == state


def test_a_tank_over_capacity_is_its_own_state(conn, admin_user):
    location = _tank(conn, admin_user, 100_000, 90_000)
    db.execute("UPDATE location SET max_capacity = 80000 WHERE location_id = ?", (location,), conn)
    try:
        assert _tile(display.tanks(1, conn), location)["state"] == "over"
    finally:
        # Leave the shared test database as consistent as it was found.
        db.execute("UPDATE location SET max_capacity = 100000 WHERE location_id = ?", (location,), conn)


def test_a_display_link_reads_its_plant_without_anyone_signed_in(client, auth):
    minted = client.post("/api/display/token", json={"plant_id": 1, "label": "Loadout wall"}, headers=auth)
    assert minted.status_code == 201
    token = minted.json()["token"]
    board = client.get(f"/api/display/tanks?token={token}")        # no Authorization header
    assert board.status_code == 200
    assert board.json()["plant"]["plant_id"] == 1


def test_a_display_link_cannot_be_pointed_at_another_plant(client, auth):
    token = client.post("/api/display/token", json={"plant_id": 1}, headers=auth).json()["token"]
    board = client.get(f"/api/display/tanks?token={token}&plant_id=2")
    assert board.json()["plant"]["plant_id"] == 1


def test_a_display_link_reads_tank_levels_and_nothing_else(client, auth):
    token = client.post("/api/display/token", json={"plant_id": 1}, headers=auth).json()["token"]
    for path in ("/api/orders", "/api/balances?plant_id=1", "/api/auth/me"):
        assert client.get(path, headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_a_revoked_link_stops_working(client, auth):
    minted = client.post("/api/display/token", json={"plant_id": 1}, headers=auth).json()
    assert client.post(f"/api/display/tokens/{minted['token_id']}/revoke", headers=auth).status_code == 200
    assert client.get(f"/api/display/tanks?token={minted['token']}").status_code == 401


def test_an_expired_link_stops_working(conn, admin_user):
    minted = display.mint(1, admin_user, conn=conn)
    db.execute(
        "UPDATE display_token SET expires_at = '2000-01-01T00:00:00+00:00' WHERE token_id = ?",
        (minted["token_id"],), conn,
    )
    with pytest.raises(AuthError, match="expired"):
        display.plant_for_token(minted["token"], conn)


def test_the_database_never_holds_a_usable_token(conn, admin_user):
    minted = display.mint(1, admin_user, conn=conn)
    stored = db.query_one("SELECT * FROM display_token WHERE token_id = ?", (minted["token_id"],), conn)
    assert minted["token"] not in str(stored)


def test_nobody_can_open_a_board_for_a_plant_they_cannot_see(conn):
    operator = security.get_user("toperator")
    allowed = {p["plant_id"] for p in security.plants_for_user(operator["user_id"], conn)}
    other = next(p["plant_id"] for p in db.query("SELECT plant_id FROM plant", (), conn) if p["plant_id"] not in allowed)
    with pytest.raises(PermissionError_):
        display.mint(other, operator, conn=conn)


def test_a_read_only_companion_can_still_open_a_tank_board(client, auth, monkeypatch):
    """The board only reads, so it is available beside the legacy PIMS today."""

    from pims.config import get_settings

    monkeypatch.setattr(get_settings(), "mode", "companion")
    assert client.post("/api/display/token", json={"plant_id": 1}, headers=auth).status_code == 201
