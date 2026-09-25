"""Departments: who does which work, and which tanks are theirs.

The legacy schema already had the pieces — ``Department`` and
``PlantDepartment`` for which departments a plant has, ``Department_id`` on
every order and transaction, and ``MaterialType.Department_Id`` tying kinds
of material to a department — but the screens never used them to narrow
anything down. An operator in the acid building saw the loadout's trucks and
the blend room's batches along with their own.

Nothing here knows a department by name. A department's work is the orders
booked to it; its materials are the ones its material types, recipes and open
orders name; its tanks are the ones holding those materials, plus the vessels
its recipes run in. So a department added to the legacy database — Acid,
or anything else — gets its own view without a code change.
"""

from __future__ import annotations

from typing import Any

from .. import audit, db
from ..errors import NotFound, ValidationError


def for_plant(plant_id: int, conn=None) -> list[dict[str, Any]]:
    """The plant's active departments, with how much open work each has."""

    rows = db.query(
        """
        SELECT d.department_id, d.code, d.description
        FROM department d
        JOIN plant_department pd ON pd.department_id = d.department_id
        WHERE pd.plant_id = ? AND d.active = 1
        ORDER BY d.description
        """,
        (plant_id,),
        conn,
    )
    open_work = {
        row["department_id"]: row["n"]
        for row in db.query(
            """
            SELECT o.department_id, COUNT(*) AS n
            FROM "order" o JOIN status s ON s.status_id = o.status_id
            WHERE o.plant_id = ? AND o.active = 1 AND s.is_terminal = 0
              AND o.department_id IS NOT NULL
            GROUP BY o.department_id
            """,
            (plant_id,),
            conn,
        )
    }
    vessels: dict[int, set[str]] = {}
    methods: dict[int, set[str]] = {}
    for row in db.query(
        "SELECT DISTINCT department_id, vessel_type, method FROM blend_recipe"
        " WHERE active = 1 AND department_id IS NOT NULL",
        (),
        conn,
    ):
        vessels.setdefault(row["department_id"], set()).add(row["vessel_type"])
        methods.setdefault(row["department_id"], set()).add(row["method"])
    for row in rows:
        row["open_orders"] = open_work.get(row["department_id"], 0)
        row["runs_batches"] = row["department_id"] in vessels
        # 'Blend' vessels are the Blend screen's; anything else gets a screen
        # of its own, named for the department.
        row["vessel_types"] = sorted(vessels.get(row["department_id"], ()))
        # 'staged' batches are charged, settled and drawn off over hours.
        row["methods"] = sorted(methods.get(row["department_id"], ()))
    return rows


def get(department_id: int, conn=None) -> dict[str, Any]:
    row = db.query_one(
        "SELECT department_id, code, description FROM department WHERE department_id = ?",
        (department_id,),
        conn,
    )
    if row is None:
        raise NotFound(f"Department {department_id} was not found.")
    return row


def materials(department_id: int, plant_id: int | None = None, conn=None) -> set[int]:
    """Every material this department handles.

    The union of: material types assigned to it (the legacy link), the
    products and components of recipes it runs, and the product on each of
    its open orders.
    """

    found: set[int] = set()
    for row in db.query(
        "SELECT m.material_id FROM material m"
        " JOIN material_type mt ON mt.material_type_id = m.material_type_id"
        " WHERE mt.department_id = ?",
        (department_id,),
        conn,
    ):
        found.add(row["material_id"])
    for row in db.query(
        """
        SELECT r.material_id AS product, c.material_id AS component
        FROM blend_recipe r
        LEFT JOIN blend_recipe_component c ON c.recipe_id = r.recipe_id
        WHERE r.active = 1 AND r.department_id = ?
        """,
        (department_id,),
        conn,
    ):
        found.add(row["product"])
        if row["component"]:
            found.add(row["component"])
    sql = """
        SELECT DISTINCT o.material_one_id FROM "order" o
        JOIN status s ON s.status_id = o.status_id
        WHERE o.department_id = ? AND o.active = 1 AND s.is_terminal = 0
          AND o.material_one_id IS NOT NULL
    """
    params: list[Any] = [department_id]
    if plant_id:
        sql += " AND o.plant_id = ?"
        params.append(plant_id)
    for row in db.query(sql, params, conn):
        found.add(row["material_one_id"])
    return found


def vessel_types(department_id: int, conn=None) -> set[str]:
    """The kinds of location this department's batches run in."""

    rows = db.query(
        "SELECT DISTINCT vessel_type, method FROM blend_recipe WHERE active = 1 AND department_id = ?",
        (department_id,),
        conn,
    )
    types = {row["vessel_type"] for row in rows}
    if any(row["method"] == "staged" for row in rows):
        # A staged batch starts in one kind of tank and moves on through the
        # others (reactor, settle tank, MGR tank).
        from .process import VESSEL_TYPES

        types |= set(VESSEL_TYPES)
    return types


def add(code: str, description: str, plant_codes: list[str], username: str = "cli", conn=None) -> dict:
    """Create a department (or reuse one with that code) and give it to plants.

    For a standalone PIMS. A companion's departments are the legacy
    database's, and the companion never writes there — add the department in
    the PIMS desktop application and it arrives at the next sync.
    """

    from ..config import get_settings

    if get_settings().companion:
        raise ValidationError(
            "Departments come from the legacy PIMS in companion mode. Add it there; "
            "it appears here at the next sync."
        )
    code = code.strip().upper()
    description = description.strip()
    if not code or not description:
        raise ValidationError("A department needs a code and a name.")
    with db.transaction(conn):
        existing = db.query_one("SELECT department_id FROM department WHERE code = ?", (code,), conn)
        department_id = existing["department_id"] if existing else db.insert(
            "department", {"code": code, "description": description, "active": 1}, conn
        )
        added = []
        for plant_code in plant_codes:
            plant = db.query_one("SELECT plant_id FROM plant WHERE code = ?", (plant_code.strip().upper(),), conn)
            if plant is None:
                raise NotFound(f"Plant {plant_code} was not found.")
            db.execute(
                "INSERT OR IGNORE INTO plant_department (plant_id, department_id) VALUES (?, ?)",
                (plant["plant_id"], department_id),
                conn,
            )
            added.append(plant_code.strip().upper())
        audit.record(
            username=username,
            action="department.add",
            entity="department",
            entity_id=department_id,
            summary=f"Department {code} ({description}) at {', '.join(added) or 'no plants'}",
            detail={"code": code, "description": description, "plants": added},
            conn=conn,
        )
    return {"department_id": department_id, "code": code, "description": description, "plants": added}
