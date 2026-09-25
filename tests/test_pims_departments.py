"""Departments, and the Acid department in particular.

A department's work is the orders booked to it; its tanks are the ones
holding what it handles. Acid batches run on the blend engine with a yield
below 100%: more pounds go in than come out, and the order is credited with
exactly the pounds out.
"""

from __future__ import annotations

import pytest

from pims import db, security
from pims.errors import BusinessRuleError, ValidationError
from pims.services import blend, departments, display, orders


def _id(sql: str, params, conn):
    return db.scalar(sql, params, conn)


@pytest.fixture()
def acid(conn) -> int:
    return _id("SELECT department_id FROM department WHERE code = 'ACID'", (), conn)


@pytest.fixture()
def acid_order(conn, acid) -> dict:
    row = db.query_one(
        """SELECT o.* FROM "order" o JOIN status s ON s.status_id = o.status_id
           WHERE o.department_id = ? AND o.plant_id = 1 AND s.is_terminal = 0
           ORDER BY o.order_id LIMIT 1""",
        (acid,),
        conn,
    )
    assert row, "the seed gives DM open acid work"
    return row


def test_acid_is_a_department_only_where_the_soapstock_is(conn, acid):
    plants = {
        row["code"]
        for row in db.query(
            "SELECT p.code FROM plant p JOIN plant_department pd ON pd.plant_id = p.plant_id"
            " WHERE pd.department_id = ?",
            (acid,),
            conn,
        )
    }
    assert plants == {"DM", "SC", "PJ"}
    dm = {d["code"]: d for d in departments.for_plant(1, conn)}
    assert dm["ACID"]["runs_batches"] is True
    assert dm["ACID"]["open_orders"] >= 1
    assert "ACID" not in {d["code"] for d in departments.for_plant(4, conn)}


def test_each_plants_settle_carries_its_measured_ratios(conn):
    settles = {r["plant_code"]: r for r in blend.recipes(conn) if r["name"] == "Soap settle"}
    assert {code: r["material_number"] for code, r in settles.items()} == {"DM": "01019", "SC": "01018", "PJ": "01017"}
    ratios = {code: {c["material_number"]: c["percentage"] for c in r["components"] if not c["grp"]}
              for code, r in settles.items()}
    assert ratios == {"DM": {"00001": 5.2, "00004": 2.4}, "SC": {"00001": 4.8, "00004": 7.7},
                      "PJ": {"00001": 5.7, "00004": 0.6}}
    settle = settles["DM"]
    assert settle["vessel_type"] == "Acid" and settle["yield_pct"] == 68.0      # cooked in a reactor first
    assert settle["method"] == "staged" and settle["expected_tfa"] == 26.0
    soap = [c for c in settle["components"] if c["grp"] == "soap"]
    assert {c["material_number"] for c in soap} == {"00007", "00006", "00010", "00009"}
    assert [o["role"] for o in settle["outputs"]] == ["oil", "mgr", "water"]


def test_an_acid_order_is_not_blended_in_one_go(conn, acid_order, admin_user):
    plan = blend.plan(order_id=acid_order["order_id"], conn=conn)
    assert plan["recipe"]["method"] == "staged"
    with pytest.raises(BusinessRuleError) as caught:
        blend.execute(
            {
                "order_id": acid_order["order_id"], "plant_id": 1, "material_id": plan["material_id"],
                "quantity": plan["quantity"], "to_location_id": plan["to_location_id"],
                "components": [
                    {"material_id": c["material_id"], "from_location_id": c["from_location_id"], "quantity": c["required"]}
                    for c in plan["components"]
                ],
            },
            admin_user,
            conn,
        )
    assert caught.value.detail["rule"] == "staged_recipe"


def test_a_blend_with_a_yield_refuses_components_that_do_not_add_up(conn, admin_user):
    product = _id("SELECT material_id FROM material WHERE number = '05003'", (), conn)
    soap = _id("SELECT material_id FROM material WHERE number = '00007'", (), conn)
    blend.set_recipe(product, "Test yield", [{"material_id": soap, "percentage": 100}], admin_user,
                     conn=conn, yield_pct=90)
    try:
        with pytest.raises(ValidationError) as caught:
            blend.execute(
                {
                    "plant_id": 1, "material_id": product, "quantity": 9_000, "to_location_id": 1,
                    # Charged as if the yield were 100%: product out of nothing.
                    "components": [{"material_id": soap, "from_location_id": 4, "quantity": 9_000}],
                },
                admin_user,
                conn,
            )
        assert "90% yield" in str(caught.value)
    finally:
        db.execute("UPDATE blend_recipe SET active = 0 WHERE material_id = ?", (product,), conn)


@pytest.mark.parametrize("bad", [0, -5, 101])
def test_an_impossible_yield_is_refused(conn, admin_user, bad):
    material = _id("SELECT material_id FROM material WHERE number = '01019'", (), conn)
    parts = [
        {"material_id": _id("SELECT material_id FROM material WHERE number = '00007'", (), conn),
         "percentage": 100},
    ]
    with pytest.raises(ValidationError):
        blend.set_recipe(material, "Bad", parts, admin_user, conn=conn, yield_pct=bad)


def test_a_blend_is_unchanged_by_all_this(conn):
    blending = _id("SELECT department_id FROM department WHERE code = 'BLND'", (), conn)
    recipe = next(r for r in blend.recipes(conn) if r["material_number"] == "01020")
    assert recipe["yield_pct"] == 100.0
    assert recipe["department_id"] == blending
    assert blend.batch_prefix(blending, conn) == "B"
    assert blend.charge_for(1000, 100) == 1000


def test_the_acid_tanks_are_the_ones_holding_what_acid_handles(conn, acid):
    board = display.tanks(1, conn, department_id=acid)
    numbers = {t["number"] for t in board["tanks"]}
    assert {"DM-1", "DM-4", "DM-13", "DM-103"} <= numbers        # reactor, settle, MGR, acid tanks
    assert board["department"]["code"] == "ACID"
    handled = departments.materials(acid, 1, conn)
    everything = display.tanks(1, conn)
    for tile in everything["tanks"]:
        holds_acid_material = any(
            p["lbs"] > 0.5 and _id("SELECT material_id FROM material WHERE number = ?", (p["number"],), conn) in handled
            for p in tile["products"]
        )
        if tile["kind"] in {"Acid", "Settle", "MGR"} or holds_acid_material:
            assert tile["number"] in numbers
        else:
            assert tile["number"] not in numbers


def test_the_department_list_comes_through_the_api(client, admin_token):
    response = client.get("/api/departments?plant_id=1", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    assert "ACID" in {d["code"] for d in response.json()}
    acid = next(d for d in response.json() if d["code"] == "ACID")
    board = client.get(
        f"/api/display/tanks?plant_id=1&department_id={acid['department_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert board.status_code == 200
    assert any(t["kind"] == "Settle" for t in board.json()["tanks"])


def test_a_display_token_can_narrow_to_a_department_but_not_widen(conn, acid, client, admin_user):
    minted = display.mint(1, admin_user, "acid room", conn)
    token = minted["token"]
    narrowed = client.get(f"/api/display/tanks?token={token}&department_id={acid}")
    assert narrowed.status_code == 200
    assert narrowed.json()["plant"]["code"] == "DM"
    # Asking for another plant with the same token still shows the token's plant.
    other = client.get(f"/api/display/tanks?token={token}&plant_id=2")
    assert other.json()["plant"]["code"] == "DM"


def test_an_operator_sees_departments_for_plants_they_can_reach(client):
    login = client.post("/api/auth/login", json={"username": "toperator", "password": "pims-demo"})
    token = login.json()["token"]
    ok = client.get("/api/departments?plant_id=1", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    # Terry works at DM only; Sioux City's departments are not theirs to list.
    assert login.json()["user"]["plants"] and {p["code"] for p in login.json()["user"]["plants"]} == {"DM"}
    refused = client.get("/api/departments?plant_id=2", headers={"Authorization": f"Bearer {token}"})
    assert refused.status_code == 403


def test_a_department_can_be_added_to_a_plant(conn):
    added = departments.add("PKG", "Packaging", ["PJ"], conn=conn)
    assert added["plants"] == ["PJ"]
    assert "PKG" in {d["code"] for d in departments.for_plant(3, conn)}
    assert "PKG" not in {d["code"] for d in departments.for_plant(1, conn)}
    # Adding it again to another plant reuses the department.
    again = departments.add("pkg", "Packaging", ["LV"], conn=conn)
    assert again["department_id"] == added["department_id"]


def test_a_companion_never_adds_departments(conn, monkeypatch):
    from pims import config

    monkeypatch.setattr(config.get_settings(), "mode", "companion")
    with pytest.raises(ValidationError) as caught:
        departments.add("PKG2", "Packaging", ["PJ"], conn=conn)
    assert "legacy PIMS" in str(caught.value)
