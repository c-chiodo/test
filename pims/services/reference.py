"""Reference data: plants, materials, locations, partners, statuses.

These are the dropdowns. The legacy client filled them with one stored
procedure call per combo box, per form open; here they are one payload the UI
fetches once.
"""

from __future__ import annotations

from .. import db
from ..config import ANALYTE_LABELS, ANALYTE_UNITS, ANALYTES
from ..errors import NotFound


def plants(conn=None) -> list[dict]:
    return db.query(
        "SELECT plant_id, code, name FROM plant WHERE active = 1 ORDER BY code", (), conn
    )


def companies(conn=None) -> list[dict]:
    return db.query(
        "SELECT company_id, name FROM company WHERE active = 1 ORDER BY name", (), conn
    )


def departments(conn=None) -> list[dict]:
    return db.query(
        "SELECT department_id, code, description FROM department WHERE active = 1"
        " ORDER BY code",
        (),
        conn,
    )


def order_types(conn=None) -> list[dict]:
    return db.query(
        "SELECT order_type_id, code, description FROM order_type ORDER BY order_type_id",
        (),
        conn,
    )


def statuses(conn=None) -> list[dict]:
    return db.query(
        "SELECT status_id, name, description, is_terminal FROM status ORDER BY status_id",
        (),
        conn,
    )


def transaction_types(conn=None) -> list[dict]:
    return db.query(
        "SELECT transaction_type_id, code, description FROM transaction_type"
        " ORDER BY transaction_type_id",
        (),
        conn,
    )


def materials(plant_id: int | None = None, conn=None) -> list[dict]:
    sql = """
        SELECT m.material_id, m.number, m.description, m.family, m.density,
               mt.name AS material_type
        FROM material m
        JOIN material_type mt ON mt.material_type_id = m.material_type_id
        WHERE m.active = 1
    """
    params: list = []
    if plant_id:
        sql += """
          AND EXISTS (SELECT 1 FROM material_plant mp
                      WHERE mp.material_id = m.material_id AND mp.plant_id = ?)
        """
        params.append(plant_id)
    sql += " ORDER BY m.number"
    return db.query(sql, params, conn)


def material(material_id: int, conn=None) -> dict:
    row = db.query_one(
        """
        SELECT m.*, mt.name AS material_type
        FROM material m
        JOIN material_type mt ON mt.material_type_id = m.material_type_id
        WHERE m.material_id = ?
        """,
        (material_id,),
        conn,
    )
    if row is None:
        raise NotFound(f"Material {material_id} does not exist.")
    row["tests"] = [
        r["analyte"]
        for r in db.query(
            "SELECT analyte FROM material_test WHERE material_id = ? AND required = 1",
            (material_id,),
            conn,
        )
    ]
    row["specs"] = db.query(
        "SELECT analyte, min_value, max_value, source, note, needs_review"
        " FROM material_spec WHERE material_id = ? AND active = 1 ORDER BY analyte",
        (material_id,),
        conn,
    )
    row["plants"] = db.query(
        """
        SELECT p.plant_id, p.code, p.name
        FROM material_plant mp JOIN plant p ON p.plant_id = mp.plant_id
        WHERE mp.material_id = ? ORDER BY p.code
        """,
        (material_id,),
        conn,
    )
    return row


def locations(plant_id: int | None = None, conn=None) -> list[dict]:
    sql = """
        SELECT l.location_id, l.plant_id, l.number, l.description, l.max_capacity,
               l.bol_required, lt.name AS location_type, p.code AS plant_code
        FROM location l
        JOIN location_type lt ON lt.location_type_id = l.location_type_id
        JOIN plant p ON p.plant_id = l.plant_id
        WHERE l.active = 1
    """
    params: list = []
    if plant_id:
        sql += " AND l.plant_id = ?"
        params.append(plant_id)
    sql += " ORDER BY p.code, l.number"
    return db.query(sql, params, conn)


def customers(conn=None) -> list[dict]:
    return db.query(
        "SELECT customer_id, gp_custnmbr, name, city, state FROM customer"
        " WHERE active = 1 ORDER BY name",
        (),
        conn,
    )


def vendors(conn=None) -> list[dict]:
    return db.query(
        "SELECT vendor_id, gp_vendorid, name, city, state FROM vendor"
        " WHERE active = 1 ORDER BY name",
        (),
        conn,
    )


def requirements(party_type: str, party_id: int, conn=None) -> list[dict]:
    return db.query(
        """
        SELECT requirement_id, requirement, sort_order, is_production, is_carrier
        FROM partner_requirement
        WHERE party_type = ? AND party_id = ? AND active = 1
        ORDER BY sort_order, requirement_id
        """,
        (party_type, party_id),
        conn,
    )


def test_points(plant_id: int | None = None, conn=None) -> list[dict]:
    sql = "SELECT test_point_id, plant_id, name, description FROM test_point WHERE active = 1"
    params: list = []
    if plant_id:
        sql += " AND plant_id = ?"
        params.append(plant_id)
    return db.query(sql + " ORDER BY name", params, conn)


def qa_questions(conn=None) -> list[dict]:
    return db.query(
        "SELECT question_id, question, answer_type FROM qa_question"
        " WHERE enabled = 1 ORDER BY sort_order, question_id",
        (),
        conn,
    )


def analytes() -> list[dict]:
    return [
        {"key": a, "label": ANALYTE_LABELS[a], "unit": ANALYTE_UNITS.get(a, "")}
        for a in ANALYTES
    ]


def bundle(plant_id: int | None = None, conn=None) -> dict:
    """Everything the UI needs to render its pickers, in one round trip."""

    return {
        "plants": plants(conn),
        "companies": companies(conn),
        "departments": departments(conn),
        "order_types": order_types(conn),
        "statuses": statuses(conn),
        "transaction_types": transaction_types(conn),
        "materials": materials(plant_id, conn),
        "locations": locations(plant_id, conn),
        "customers": customers(conn),
        "vendors": vendors(conn),
        "test_points": test_points(plant_id, conn),
        "qa_questions": qa_questions(conn),
        "analytes": analytes(),
    }
