"""The read-only companion: it must never be able to change the legacy PIMS.

Most of these tests are about that one promise, from several directions: the
statement guard, the login preflight, the storage layer of the stand-in, and
a byte-for-byte comparison of the legacy database before and after a sync.
The rest check that what the mirror copies is faithful — the same balances,
the same history — because a read-only view that is wrong is its own risk.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
from pathlib import Path

import pytest

from pims import db
from pims.legacy import mirror, probe
from pims.legacy.readonly import (
    ReadOnlyConnection, WriteRefused, check_read_only, connect_sqlite, preflight,
)
from pims.legacy.schema import LEGACY_MAP

# ------------------------------------------------------------------ fixtures


def _load_builder():
    path = Path(__file__).resolve().parent.parent / "scripts" / "pims_make_legacy_standin.py"
    spec = importlib.util.spec_from_file_location("standin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def standin(tmp_path, conn) -> Path:
    """A ProductionData stand-in built from the seeded test database."""

    _load_builder().build(conn, tmp_path / "legacy")
    return tmp_path / "legacy"


@pytest.fixture()
def legacy(standin) -> ReadOnlyConnection:
    connection = connect_sqlite(str(standin / "ProductionData.db"), str(standin / "FECoreData.db"))
    yield connection
    connection.close()


@pytest.fixture()
def legacy_map():
    return LEGACY_MAP.with_physical(user="fecore.[User]")


@pytest.fixture()
def local(tmp_path) -> sqlite3.Connection:
    """The companion's own database — separate from the test session's."""

    connection = sqlite3.connect(tmp_path / "companion.db", isolation_level=None)
    connection.row_factory = db._row_to_dict
    connection.execute("PRAGMA foreign_keys = ON")
    db.create_schema(connection)
    db.apply_migrations(connection)
    yield connection
    connection.close()


def _digest(folder: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.glob("*.db"))}


def _sync(legacy, legacy_map, local, **kwargs):
    return mirror.run(legacy, probe.resolve(legacy, legacy_map), local, **kwargs)


# ------------------------------------------------------------ nothing writes


@pytest.mark.parametrize("sql", [
    "SELECT * FROM dbo.[Order]",
    "select [Delete_flag] from dbo.[PendingShipments] where Remarks = 'please update'",
    "WITH recent AS (SELECT 1 AS a) SELECT a FROM recent",
    "SELECT [Transaction_id] FROM dbo.[transaction] -- delete later\n",
    "SELECT * FROM t;",
])
def test_reads_pass_the_guard(sql):
    check_read_only(sql)


@pytest.mark.parametrize("sql", [
    "UPDATE dbo.[Order] SET Active = 0",
    "INSERT INTO dbo.[QC] (Qc_id) VALUES (1)",
    "DELETE FROM dbo.[transaction]",
    "SELECT 1; DELETE FROM dbo.[transaction]",
    "SELECT * INTO dbo.backup FROM dbo.[Order]",
    "EXEC dbo.Matrix_Sample_Data",
    "exec('select 1')",
    "MERGE dbo.[Order] AS t USING x ON 1 = 1 WHEN MATCHED THEN DELETE;",
    "SELECT 1 /* harmless */ ; DROP TABLE dbo.[Order]",
    "SELECT * FROM OPENQUERY(PHLIMSSQL, 'delete from x')",
    "TRUNCATE TABLE dbo.[PendingShipments]",
    "ALTER PROCEDURE dbo.Matrix_Sample_Data AS SELECT 1",
    "DECLARE @x int",
    "SELECT 1 WAITFOR DELAY '00:10:00'",
    "  -- a comment first\n  delete from t",
    "SELECT sp_configure",
])
def test_anything_that_could_write_is_refused(sql):
    with pytest.raises(WriteRefused):
        check_read_only(sql)


def test_the_guard_runs_before_the_driver_sees_the_statement(legacy):
    with pytest.raises(WriteRefused):
        list(legacy.rows("DELETE FROM dbo.[Order]"))


def test_the_stand_in_itself_refuses_writes_at_the_storage_layer(legacy):
    """Even going around the guard to the raw connection cannot write."""

    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        legacy.raw.execute("DELETE FROM dbo.[Order]")


def test_a_sync_leaves_the_legacy_database_byte_for_byte_unchanged(
    standin, legacy, legacy_map, local
):
    before = _digest(standin)
    _sync(legacy, legacy_map, local)
    _sync(legacy, legacy_map, local)
    _sync(legacy, legacy_map, local, full=True)
    assert _digest(standin) == before


# ------------------------------------------------------------- login check


class _FakeServer:
    """Answers the preflight's questions like SQL Server would for a login
    holding exactly ``granted``."""

    def __init__(self, granted: set[str]):
        self.granted = granted
        self.statements: list[str] = []

    def cursor(self):
        server = self

        class Cursor:
            description = [("value",)]

            def execute(self, sql, params=()):
                server.statements.append(sql)
                check_read_only(sql)
                key = (params[0] if params else sql).upper()
                if "SUSER_SNAME" in key:
                    self.value = "FE\\pims-companion"
                elif "DB_NAME()" in sql and not params:
                    self.value = "ProductionData"
                elif "SYSADMIN" in key:
                    self.value = int("sysadmin" in server.granted)
                elif "OBJECT" in sql:
                    table, perm = params
                    self.value = int(f"{perm} on {table}" in server.granted)
                else:
                    self.value = int(key.lower() in server.granted or key in server.granted)

            def fetchone(self):
                return (self.value,)

            def close(self):
                pass

        return Cursor()


def test_a_read_only_login_passes_preflight():
    server = _FakeServer({"SELECT"})
    report = preflight(ReadOnlyConnection(server, dialect="mssql"), ("dbo.[Order]",))
    assert report["ok"] is True
    assert report["login"] == "FE\\pims-companion"


@pytest.mark.parametrize("granted, reason", [
    ({"db_datawriter"}, "db_datawriter"),
    ({"db_owner"}, "db_owner"),
    ({"sysadmin"}, "sysadmin"),
    ({"UPDATE"}, "UPDATE"),
    ({"EXECUTE"}, "EXECUTE"),
    ({"UPDATE on dbo.Order"}, "UPDATE on dbo.Order"),
])
def test_a_login_that_could_write_is_refused_before_anything_is_read(granted, reason):
    server = _FakeServer(granted | {"SELECT"})
    with pytest.raises(WriteRefused, match=reason):
        preflight(ReadOnlyConnection(server, dialect="mssql"), ("dbo.[Order]",))
    assert all(s.lstrip().upper().startswith("SELECT") for s in server.statements)


# ------------------------------------------------------------- the schema


def test_the_map_resolves_against_a_database_shaped_like_production(legacy, legacy_map):
    resolution = probe.resolve(legacy, legacy_map)
    assert resolution.ok, probe.describe(resolution)
    transaction = resolution.get("transaction")
    assert transaction.readable == ["dbo.[transaction]", "dbo.[Transaction_Archive]"]
    assert transaction.columns["transaction_id"] == "Transaction_id"


def test_a_missing_required_column_is_reported_before_any_sync(standin, legacy_map):
    path = standin / "ProductionData.db"
    raw = sqlite3.connect(path)
    raw.execute("ALTER TABLE [Location] RENAME COLUMN [Number] TO [Loc_number]")
    raw.commit()
    raw.close()
    legacy = connect_sqlite(str(path), str(standin / "FECoreData.db"))
    resolution = probe.resolve(legacy, legacy_map)
    assert not resolution.ok
    assert "location" in resolution.as_dict()["blocking"]
    assert "missing required: Number" in probe.describe(resolution)
    with pytest.raises(Exception, match="does not match"):
        mirror.run(legacy, resolution, None)


def test_an_unreadable_user_database_does_not_stop_the_mirror(standin, legacy_map, local):
    legacy = connect_sqlite(str(standin / "ProductionData.db"))        # no FECoreData
    resolution = probe.resolve(legacy, legacy_map)
    assert resolution.ok
    assert resolution.get("user").usable is False
    report = mirror.run(legacy, resolution, local)
    assert report["placeholder_users"] > 0


# -------------------------------------------------------------- fidelity


BALANCES = """
    SELECT loc, mat, ROUND(SUM(q), 2) AS b FROM (
      SELECT to_location_id AS loc, to_material_id AS mat, to_qty AS q
      FROM inventory_transaction WHERE to_location_id IS NOT NULL
      UNION ALL
      SELECT from_location_id, from_material_id, -from_qty
      FROM inventory_transaction WHERE from_location_id IS NOT NULL
    ) GROUP BY loc, mat HAVING ROUND(SUM(q), 2) <> 0
"""


def test_mirrored_balances_match_the_source_exactly(conn, legacy, legacy_map, local):
    _sync(legacy, legacy_map, local)
    source = {(r["loc"], r["mat"]): r["b"] for r in db.query(BALANCES, (), conn)}
    mirrored = {(r["loc"], r["mat"]): r["b"] for r in db.query(BALANCES, (), local)}
    mirrored.pop((99999, 8), None)      # the orphan the stand-in adds on purpose
    assert mirrored == source


def test_archived_transactions_are_mirrored_with_the_live_ones(conn, legacy, legacy_map, local):
    _sync(legacy, legacy_map, local)
    source = db.scalar("SELECT COUNT(*) FROM inventory_transaction", (), conn)
    assert db.scalar("SELECT COUNT(*) FROM inventory_transaction", (), local) == source + 1


def test_legacy_quirks_are_reported_not_hidden(legacy, legacy_map, local):
    report = _sync(legacy, legacy_map, local)
    assert report["orphans"]["transactions_with_unknown_location"] == 1
    assert [t["name"] for t in report["unclassified_transaction_types"]] == ["Tote Fill"]
    codes = {r["code"] for r in db.query("SELECT code FROM transaction_type", (), local)}
    assert {"RECEIVE", "PRODUCE", "MOVE", "LOAD", "SHIP", "SHRINK", "ADJUST"} <= codes


def test_statuses_that_end_an_order_are_recognised_by_name(legacy, legacy_map, local):
    _sync(legacy, legacy_map, local)
    terminal = {r["name"]: r["is_terminal"] for r in db.query("SELECT * FROM status", (), local)}
    assert terminal["Closed"] == 1 and terminal["Cancelled"] == 1
    assert terminal["Open"] == 0 and terminal["In Process"] == 0


def test_the_real_product_limits_are_attached_to_mirrored_materials(legacy, legacy_map, local):
    report = _sync(legacy, legacy_map, local)
    assert report["specs_applied"] > 0
    assert db.scalar("SELECT COUNT(*) FROM material_spec", (), local) > 0


# ------------------------------------------------------------- incremental


def _writable(standin: Path) -> sqlite3.Connection:
    """The test's own handle on the stand-in, playing the legacy app."""

    return sqlite3.connect(standin / "ProductionData.db")


def test_a_second_sync_reads_only_the_recent_range(standin, legacy, legacy_map, local):
    _sync(legacy, legacy_map, local)
    second = _sync(legacy, legacy_map, local)
    for key in ("order", "transaction", "qc"):
        assert second["tables"][key]["mode"] == "window"
        assert second["tables"][key]["read"] <= 1


def test_new_legacy_rows_arrive_on_the_next_sync(standin, legacy, legacy_map, local):
    _sync(legacy, legacy_map, local)
    app = _writable(standin)
    app.execute(
        "INSERT INTO [transaction] ([Transaction_id], [Transtype_id], [Plant_id], [Transaction_date],"
        " [User_id], [To_location_id], [To_material_id], [To_qty]) VALUES (9000002, 1, 1, '2026-09-25', 1, 5, 8, 750)"
    )
    app.commit()
    app.close()
    _sync(legacy, legacy_map, local)
    row = db.query_one("SELECT to_qty FROM inventory_transaction WHERE transaction_id = 9000002", (), local)
    assert row["to_qty"] == 750


def test_an_edit_to_a_recent_row_is_picked_up(standin, legacy, legacy_map, local):
    _sync(legacy, legacy_map, local)
    app = _writable(standin)
    app.execute("UPDATE [transaction] SET [Remarks] = 'corrected in PIMS' WHERE [Transaction_id] = 9000001")
    app.commit()
    app.close()
    _sync(legacy, legacy_map, local)
    remarks = db.scalar("SELECT remarks FROM inventory_transaction WHERE transaction_id = 9000001", (), local)
    assert remarks == "corrected in PIMS"


def test_a_full_sync_removes_rows_the_legacy_app_deleted(standin, legacy, legacy_map, local):
    _sync(legacy, legacy_map, local)
    app = _writable(standin)
    app.execute("DELETE FROM [transaction] WHERE [Transaction_id] = 9000001")
    app.commit()
    app.close()
    _sync(legacy, legacy_map, local)
    assert db.scalar("SELECT COUNT(*) FROM inventory_transaction WHERE transaction_id = 9000001", (), local) == 1
    report = _sync(legacy, legacy_map, local, full=True)
    assert report["tables"]["transaction"]["removed"] == 1
    assert db.scalar("SELECT COUNT(*) FROM inventory_transaction WHERE transaction_id = 9000001", (), local) == 0


# ---------------------------------------------------------------- accounts


def test_mirrored_legacy_users_cannot_sign_in(legacy, legacy_map, local):
    from pims import security

    _sync(legacy, legacy_map, local)
    legacy_users = db.query("SELECT * FROM app_user WHERE username LIKE 'legacy:%'", (), local)
    assert legacy_users
    assert all(u["password_hash"] == "" for u in legacy_users)
    assert not security.verify_password("pims-demo", legacy_users[0]["password_hash"])


def test_a_companion_account_survives_every_sync(legacy, legacy_map, local):
    from pims.legacy.service import create_local_account

    _sync(legacy, legacy_map, local)
    account = create_local_account("cchiodo", "Chris Chiodo", "a-long-password", conn=local)
    assert account["user_id"] >= mirror.LOCAL_ACCOUNT_FLOOR
    _sync(legacy, legacy_map, local, full=True)
    kept = db.query_one("SELECT * FROM app_user WHERE user_id = ?", (account["user_id"],), local)
    assert kept["username"] == "cchiodo"


# ------------------------------------------------------------ companion mode


@pytest.fixture()
def companion(monkeypatch):
    from pims.config import get_settings

    monkeypatch.setattr(get_settings(), "mode", "companion")


@pytest.mark.parametrize("path, body, screen", [
    ("/api/transactions/load", {"order_id": 1}, "Order Selection Menu → Load Trailer"),
    ("/api/transactions/receive", {}, "Order Selection Menu → Receive"),
    ("/api/orders", {}, "Order Edit Menu → Create Order"),
    ("/api/orders/close", {"order_ids": [1]}, "Order Edit Menu → Close Selected Orders"),
    ("/api/shipments/1/ship", {}, "Order Selection Menu → Ship Trailer"),
    ("/api/orders/1/qc", {}, "Quality Control → Regular QC"),
    ("/api/blend/execute", {}, "Order Selection Menu → Produce"),
])
def test_a_companion_refuses_writes_and_says_where_to_make_them(
    client, auth, companion, path, body, screen
):
    response = client.post(path, json=body, headers=auth)
    assert response.status_code == 409
    payload = response.json()
    assert payload["code"] == "companion_read_only"
    assert payload["detail"]["legacy_screen"] == screen
    assert "PIMS desktop application" in payload["message"]


def test_a_write_endpoint_nobody_has_classified_is_refused_by_default(client, auth, companion):
    """An allowlist, not a blocklist: new write routes start blocked."""

    response = client.post("/api/some/future/write", json={}, headers=auth)
    assert response.status_code == 409


def test_a_companion_still_answers_reads_and_read_shaped_posts(client, auth, companion):
    assert client.get("/api/orders?limit=5", headers=auth).status_code == 200
    ran = client.post(
        "/api/query/run", json={"source": "orders", "columns": ["order_id"], "limit": 5}, headers=auth
    )
    assert ran.status_code != 409


def test_nobody_holds_write_permissions_in_a_companion_not_even_an_admin(client, auth, companion):
    me = client.get("/api/auth/me", headers=auth).json()
    assert me["role"] == "admin"
    assert "txn.post" not in me["permissions"] and "*" not in me["permissions"]
    assert "spec.write" in me["permissions"]        # limits are the companion's own data


def test_jobs_that_create_or_close_orders_do_not_run_in_a_companion(client, auth, companion):
    for job in ("recurring", "auto-close", "gp-sync"):
        response = client.post(f"/api/jobs/{job}/run", json={}, headers=auth)
        assert response.status_code == 400, job


def test_the_health_probe_says_which_mode_this_is(client, companion):
    assert client.get("/api/health").json()["mode"] == "companion"


def test_a_standalone_install_is_unaffected(client, auth):
    assert client.get("/api/health").json()["mode"] == "standalone"
    me = client.get("/api/auth/me", headers=auth).json()
    assert "*" in me["permissions"]


# ---------------------------------------------------------- readiness script


def _statements(script: str) -> list[str]:
    """Split T-SQL on semicolons that are not inside strings or comments."""

    import re

    body = re.sub(r"--[^\n]*", "", script)
    parts, current, in_string = [], [], False
    for char in body:
        if char == "'":
            in_string = not in_string
        if char == ";" and not in_string:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if "".join(current).strip():
        parts.append("".join(current).strip())
    return [p for p in parts if p]


def test_every_statement_in_the_readiness_script_is_a_read():
    """The script someone runs by hand against production passes the same
    guard as the companion's own queries. PRINT and SET NOCOUNT ON are the
    only other statements, and neither can change anything."""

    script = probe.readiness_sql(LEGACY_MAP)
    statements = _statements(script)
    assert len(statements) > 5
    for statement in statements:
        lines = [line for line in statement.splitlines() if not line.strip().upper().startswith("PRINT ")]
        remainder = "\n".join(lines).strip()
        if not remainder or remainder.upper() == "SET NOCOUNT ON":
            continue
        check_read_only(remainder)


def test_the_committed_readiness_script_matches_the_map():
    path = Path(__file__).resolve().parent.parent / "scripts" / "legacy" / "pims_companion_readiness.sql"
    assert path.read_text() == probe.readiness_sql(LEGACY_MAP) + "\n"
