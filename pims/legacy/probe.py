"""Compare the legacy map with the database it is pointed at.

``python -m pims legacy check`` runs this before anything else. It reads no
data rows: for each mapped table it asks for ``SELECT * … WHERE 1 = 0`` and
looks at the column names that come back. The result says, in plain words,
which tables and columns were found, which of several candidate spellings won,
what is missing, and whether the mirror can run.

The mirror only ever selects the columns this found, so an optional column
that does not exist at this site is skipped rather than becoming an error in
the middle of a sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .readonly import ReadOnlyConnection
from .schema import LEGACY_MAP, LegacyMap, Table


@dataclass
class TableResolution:
    table: Table
    readable: list[str] = field(default_factory=list)     # physical tables found
    unreadable: list[str] = field(default_factory=list)
    columns: dict[str, str] = field(default_factory=dict)  # local -> legacy
    missing_required: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)         # legacy columns not mapped
    rows: int | None = None

    @property
    def usable(self) -> bool:
        return bool(self.readable) and not self.missing_required

    @property
    def blocking(self) -> bool:
        return not self.usable and not self.table.optional

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.table.key,
            "physical": list(self.table.physical),
            "local": self.table.local,
            "readable": self.readable,
            "unreadable": self.unreadable,
            "usable": self.usable,
            "optional": self.table.optional,
            "blocking": self.blocking,
            "columns": self.columns,
            "missing_required": self.missing_required,
            "missing_optional": self.missing_optional,
            "unmapped_legacy_columns": self.extra,
            "rows": self.rows,
            "note": self.table.note,
        }


@dataclass
class Resolution:
    tables: dict[str, TableResolution]

    @property
    def ok(self) -> bool:
        return not any(t.blocking for t in self.tables.values())

    def get(self, key: str) -> TableResolution:
        return self.tables[key]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tables": [t.as_dict() for t in self.tables.values()],
            "blocking": [t.table.key for t in self.tables.values() if t.blocking],
        }


def resolve(
    connection: ReadOnlyConnection,
    legacy_map: LegacyMap = LEGACY_MAP,
    counts: bool = False,
) -> Resolution:
    """Work out which mapped tables and columns exist, without reading rows."""

    result: dict[str, TableResolution] = {}
    for table in legacy_map.tables:
        res = TableResolution(table)
        present: dict[str, str] = {}          # lower-case -> actual spelling
        common: set[str] | None = None
        for physical in table.physical:
            cols = connection.columns(physical)
            if cols is None:
                res.unreadable.append(physical)
                continue
            res.readable.append(physical)
            lowered = {c.lower(): c for c in cols}
            present.update(lowered)
            # With UNION-style tables, a column is only usable if every
            # readable table has it.
            common = set(lowered) if common is None else common & set(lowered)
        if common is None:
            common = set()

        for col in table.columns:
            hit = next((c for c in col.candidates if c.lower() in common), None)
            if hit:
                res.columns[col.local] = present[hit.lower()]
            elif col.required:
                res.missing_required.append(col.label)
            else:
                res.missing_optional.append(col.label)
        mapped = {v.lower() for v in res.columns.values()}
        res.extra = sorted(present[c] for c in common if c not in mapped)

        if counts and res.readable:
            res.rows = sum(
                int(connection.scalar(f"SELECT COUNT(*) FROM {physical}") or 0)
                for physical in res.readable
            )
        result[table.key] = res
    return Resolution(result)


def describe(resolution: Resolution) -> str:
    """A report a person can read, for the CLI."""

    lines: list[str] = []
    for res in resolution.tables.values():
        table = res.table
        if res.usable:
            mark = "OK  "
        elif table.optional:
            mark = "SKIP"
        else:
            mark = "FAIL"
        rows = f"  {res.rows:,} rows" if res.rows is not None else ""
        lines.append(f"[{mark}] {table.key:18} {table.label}{rows}")
        if res.unreadable:
            lines.append(f"         cannot read: {', '.join(res.unreadable)}")
        if res.missing_required:
            lines.append(f"         missing required: {', '.join(res.missing_required)}")
        if res.missing_optional:
            lines.append(f"         not present (skipped): {', '.join(res.missing_optional)}")
        if table.note:
            lines.append(f"         note: {table.note}")
    lines.append("")
    if resolution.ok:
        lines.append("The mirror can run. This check only reads: column names, and row counts with --counts.")
    else:
        blocking = [t.table.key for t in resolution.tables.values() if t.blocking]
        lines.append(
            "The mirror cannot run until these are resolved: " + ", ".join(blocking)
            + ". Send this report to whoever maintains the map (pims/legacy/schema.py)."
        )
    return "\n".join(lines)


def readiness_sql(legacy_map: LegacyMap = LEGACY_MAP) -> str:
    """A T-SQL script that answers, with SELECTs only, whether the companion can
    run against this server — for someone to run in SSMS or VS Code before
    anything is installed.

    Generated from the map so the two cannot drift apart. It reads catalog
    views and metadata only: no EXEC, no dynamic SQL, no temp tables, and row
    counts come from sys.partitions rather than scanning any table.
    """

    local_tables = sorted({
        p.split(".")[-1].strip("[]") for t in legacy_map.tables for p in t.physical
        if p.lower().startswith("dbo.")
    })
    in_list = ", ".join(f"'{name}'" for name in local_tables)
    expected = []
    for table in legacy_map.tables:
        for physical in table.physical:
            if not physical.lower().startswith("dbo."):
                continue
            name = physical.split(".")[-1].strip("[]")
            for col in table.columns:
                for candidate in col.candidates:
                    expected.append(
                        f"    ('{name}', '{candidate}', {1 if col.required else 0}, '{col.local}')"
                    )
    expected_rows = ",\n".join(expected)
    users_table = legacy_map.get("user").physical[0]
    id_rows = ",\n".join(
        f"    ('{p.split('.')[-1].strip('[]')}', '{t.id_column}')"
        for t in legacy_map.tables
        for p in t.physical
        if t.id_column and p.lower().startswith("dbo.")
    )
    return f"""-- =====================================================================
-- PIMS companion — READINESS CHECK  (READ-ONLY)
-- Run in VS Code (MSSQL) or SSMS against FESQLPROD01\\PRODUCTION, database
-- ProductionData — or, better, against the restored copy / readable
-- secondary the companion will actually read.
--
-- Every statement below is a SELECT over catalog views and metadata.
-- No EXEC, no dynamic SQL, no temp tables, no table scans: row counts come
-- from sys.partitions. Nothing here writes to anything.
--
-- Generated from pims/legacy/schema.py — regenerate with
--     python -m pims legacy readiness-sql
-- =====================================================================
SET NOCOUNT ON;

PRINT '========== 1. Is this login read-only? (the companion refuses to run otherwise) ==========';
SELECT
    login_name        = SUSER_SNAME(),
    database_name     = DB_NAME(),
    is_sysadmin       = IS_SRVROLEMEMBER('sysadmin'),
    is_db_owner       = IS_MEMBER('db_owner'),
    is_datawriter     = IS_MEMBER('db_datawriter'),
    is_ddladmin       = IS_MEMBER('db_ddladmin'),
    can_select        = HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'SELECT'),
    can_insert        = HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'INSERT'),
    can_update        = HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'UPDATE'),
    can_delete        = HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'DELETE'),
    can_execute       = HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'EXECUTE'),
    can_alter         = HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'ALTER'),
    verdict = CASE
        WHEN IS_SRVROLEMEMBER('sysadmin') = 1 OR IS_MEMBER('db_owner') = 1
          OR IS_MEMBER('db_datawriter') = 1 OR IS_MEMBER('db_ddladmin') = 1
          OR HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'INSERT') = 1
          OR HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'UPDATE') = 1
          OR HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'DELETE') = 1
          OR HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'EXECUTE') = 1
          OR HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'ALTER') = 1
        THEN 'NOT READ-ONLY - the companion will refuse this login; ask for db_datareader only'
        ELSE 'read-only - OK'
    END;

PRINT '========== 2. Table-level grants that bypass the database-level answer ==========';
SELECT t.name AS table_name, p.permission_name, p.state_desc, pr.name AS grantee
FROM sys.database_permissions p
JOIN sys.tables t              ON t.object_id = p.major_id
JOIN sys.database_principals pr ON pr.principal_id = p.grantee_principal_id
WHERE p.class = 1
  AND p.permission_name IN ('INSERT', 'UPDATE', 'DELETE', 'ALTER', 'CONTROL')
  AND p.state IN ('G', 'W')
  AND t.name IN ({in_list})
  AND (pr.name = USER_NAME() OR IS_MEMBER(pr.name) = 1)
ORDER BY t.name, p.permission_name;
-- Expect no rows.

PRINT '========== 3. The tables the companion reads, and their size (metadata only) ==========';
SELECT t.name AS table_name, SUM(ps.row_count) AS approx_rows,
       CAST(SUM(ps.used_page_count) * 8 / 1024.0 AS DECIMAL(12, 1)) AS used_mb
FROM sys.tables t
JOIN sys.dm_db_partition_stats ps ON ps.object_id = t.object_id AND ps.index_id IN (0, 1)
WHERE t.name IN ({in_list})
GROUP BY t.name
ORDER BY t.name;
-- If dm_db_partition_stats is refused (it needs VIEW DATABASE STATE), this
-- fallback reads the same numbers from sys.partitions:
SELECT t.name AS table_name, SUM(p.rows) AS approx_rows
FROM sys.tables t
JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1)
WHERE t.name IN ({in_list})
GROUP BY t.name
ORDER BY t.name;

PRINT '========== 4. Columns the map expects: FOUND / MISSING ==========';
WITH expected (table_name, column_name, is_required, maps_to) AS (
    SELECT * FROM (VALUES
{expected_rows}
    ) AS v (table_name, column_name, is_required, maps_to)
)
SELECT e.table_name, e.maps_to, e.column_name,
       CASE WHEN e.is_required = 1 THEN 'required' ELSE 'optional' END AS kind,
       CASE WHEN c.COLUMN_NAME IS NULL THEN 'MISSING' ELSE 'found' END AS status,
       c.DATA_TYPE
FROM expected e
LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
       ON c.TABLE_SCHEMA = 'dbo' AND c.TABLE_NAME = e.table_name AND c.COLUMN_NAME = e.column_name
ORDER BY e.table_name, e.maps_to, status DESC;
-- A 'MISSING' optional column is fine when another spelling for the same
-- maps_to is 'found'. A concept with every spelling MISSING and kind
-- 'required' blocks the mirror: send this output back.

PRINT '========== 5. Are the id-range reads index seeks? (they should be) ==========';
WITH ids (table_name, column_name) AS (
    SELECT * FROM (VALUES
{id_rows}
    ) AS v (table_name, column_name)
)
SELECT ids.table_name, ids.column_name,
       i.name AS index_name, i.type_desc,
       CASE WHEN i.name IS NULL THEN 'NO INDEX LEADS WITH THIS COLUMN - reads would scan; raise with the DBA'
            ELSE 'seek - OK' END AS verdict
FROM ids
LEFT JOIN sys.tables t ON t.name = ids.table_name
LEFT JOIN sys.columns c ON c.object_id = t.object_id AND c.name = ids.column_name
LEFT JOIN sys.index_columns ic ON ic.object_id = t.object_id AND ic.column_id = c.column_id AND ic.key_ordinal = 1
LEFT JOIN sys.indexes i ON i.object_id = ic.object_id AND i.index_id = ic.index_id
ORDER BY ids.table_name;

PRINT '========== 6. Where should the companion read from? ==========';
SELECT name, is_read_committed_snapshot_on, snapshot_isolation_state_desc, recovery_model_desc
FROM sys.databases WHERE name = DB_NAME();
-- A readable secondary means the companion can read with zero load on the
-- primary. This needs VIEW SERVER STATE; "permission denied" just means ask the DBA.
SELECT ar.replica_server_name, ar.secondary_role_allow_connections_desc
FROM sys.availability_replicas ar;

PRINT '========== 7. Can this login see the user names? ==========';
SELECT COUNT(*) AS readable_user_rows FROM {users_table};
-- "Invalid object" or "permission denied" is fine: transactions are then
-- attributed to "Legacy user <id>" instead of a name.

PRINT '========== 8. What the transaction types are called ==========';
SELECT * FROM dbo.[TransType];
-- The mirror reads what each type is from its name (PROD-LOAD and MOVE-LOAD
-- are loads, SHIP-LEAVE a ship, REVERSAL undoes its parent). Send this back
-- so any name it does not recognise can be added.

PRINT '========== 9. Are quantities out of a location stored negative? ==========';
SELECT TOP 20 Transaction_id, Transtype_id, From_qty, To_qty, Parent_transaction_id
FROM dbo.[transaction]
ORDER BY Transaction_id DESC;
-- The exports show From_Qty negative (-4689 out, +4689 in). The mirror
-- measures this on every sync, but these 20 rows show it plainly.
"""
