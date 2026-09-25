# PIMS companion: read-only, alongside the legacy system

The legacy PIMS desktop application stays exactly as it is: it remains the
system of record, and operators keep entering loads, QC and orders in it. The
companion reads its database, mirrors it into a store of its own, and runs
every *read-side* part of the replacement on that mirror — dashboards, inquiry,
search, data quality, alerts, the LIMS matrix — without anyone needing, or
being able to use, write access to production.

Run it with `PIMS_MODE=companion`. Everything else in the repository is
unchanged; standalone mode is still the default.

---

## Can this affect production PIMS?

**It cannot write to it.** Four independent locks, any one of which is enough:

1. **The login.** The companion should be given a SQL login in
   `db_datareader` only. That is the guarantee that matters; the rest are
   defence in depth for the day someone gets it wrong.
2. **The companion refuses a login that could write.** Before every sync it
   asks the server what its login may do — `sysadmin`, `db_owner`,
   `db_datawriter`, `db_ddladmin`, database-level `INSERT`/`UPDATE`/`DELETE`/
   `EXECUTE`/`ALTER`, and per-table grants on every table it reads. Any "yes"
   and it stops before reading a single row, with a message saying why. A
   misconfigured admin login fails safe.
3. **The statement guard.** Every query passes through
   `pims/legacy/readonly.py`, which refuses anything that is not a single
   `SELECT` or `WITH` — `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `EXEC`,
   `SELECT … INTO`, `DROP`, `ALTER`, stacked statements, `OPENQUERY`, `WAITFOR`
   and the rest — before the database driver ever sees it.
4. **`ApplicationIntent=ReadOnly`** on the connection, which routes the
   companion to a readable secondary where the server has one.

**Nothing is installed on the server.** No triggers, no SQL Agent jobs, no
stored procedures, no linked servers, no schema changes, no service. `PIMS.exe`
is not touched. The companion is an ordinary client that runs SELECTs, on a
machine of your choosing.

The tests prove the no-write promise from each direction
(`tests/test_pims_legacy.py`): 16 kinds of write refused by the guard, six
kinds of over-privileged login refused by the preflight, the test stand-in for
`ProductionData` refusing writes at its own storage layer, and a byte-for-byte
hash of the legacy database before and after three syncs.

**It can add load, and this is the honest part.** Any read of the live
database uses some CPU and I/O. The companion is built to make that
negligible:

| | |
|---|---|
| **No locks** | Reads run at `READ UNCOMMITTED`, so the companion takes no shared locks and cannot make an operator's save in PIMS wait. |
| **Always yields** | `DEADLOCK_PRIORITY LOW` and a 5-second lock timeout: in any contention, the companion is the one that gives up. |
| **Index seeks, not scans** | After the first sync, every large table is read as a primary-key range (`WHERE Transaction_id >= ?`) — new rows plus recent edits — never by an unindexed date column. The readiness script (below) confirms each id column is indexed. |
| **Nothing sorted on the server** | No `ORDER BY` or `UNION` on large tables; the transaction table and its archive are read separately. Nothing spills to production's `tempdb`. |
| **Small and slow on purpose** | One connection, autocommit (no open transaction), 2,000-row batches with a pause between them, a 60-second statement timeout. |
| **Steady state** | A 15-minute sync reads the reference tables (a few hundred rows) and the recent id range of the rest. On the test stand-in, the second sync read **one row** per large table. |

Two things cost more, and both are scheduled: the **first** sync reads
everything once, and the **weekly `--full`** sync re-reads everything to pick
up deletions and edits to old rows. Run both off-hours.

**For literally zero effect on production, point the companion at a copy.**
A nightly backup restored to another server, or an Always On readable
secondary, carries the same data a day or seconds behind — and the production
server is then never touched at all. This is the recommended setup; the
readiness script tells you whether a readable secondary exists.

The trade-offs that come with this design, stated plainly:

- **A lock-free read can catch a row mid-edit.** If an operator's save is in
  flight at the instant the companion reads, the companion may see the
  half-saved row. The next sync re-reads it, so it is corrected within 15
  minutes. The companion never acts on what it reads, so a momentarily wrong
  row is a display glitch, not a consequence.
- **It is only as current as its last sync.** Every screen carries a banner —
  "Read-only mirror of PIMS · synced 4 min ago" — which turns amber past 45
  minutes, and the support console's health check degrades at 45 minutes and
  fails at 6 hours. A read-only view that is quietly out of date is how
  FE-2026-001 went unnoticed for seven months; this one says so.
- **Edits to rows older than the re-read window** (14 days; 180 for orders)
  and **deletions** appear at the weekly `--full` sync, not the next one.

---

## Setting it up

**1. Run the readiness check — nothing to install.**
`scripts/legacy/pims_companion_readiness.sql` in VS Code (MSSQL) or SSMS,
against `ProductionData` — or better, against the copy you intend to use. It
is SELECTs over catalog views and metadata only (no `EXEC`, no temp tables,
no table scans), in the same style as `read_procs.sql`, and answers:

1. Is this login read-only? (with a verdict column)
2. Are there table-level grants that would let it write anyway?
3. How big are the tables the companion reads? (from metadata, not by counting)
4. Does every column the map expects exist? (`found` / `MISSING`)
5. Is each id column indexed, so the reads are seeks?
6. Is there a readable secondary to point at instead of the primary?
7. Can this login read the user names in `FECoreData`?

Send the output back and any mismatch can be fixed in the map before anything
is installed.

**2. Ask the DBA for a login** in `db_datareader` on `ProductionData` — ideally
on the restored copy or readable secondary — plus read access to
`FECoreData.dbo.[User]` if names are wanted (optional; without it,
transactions are attributed to "Legacy user 123"). **A login that PIMS users
share will not do:** the legacy app runs on stored procedures, so its logins
normally hold `EXECUTE`, and the companion refuses any login with `EXECUTE`.

**3. Install on a machine that is not the SQL Server,** and configure:

```bash
pip install -r requirements.txt pyodbc          # plus "ODBC Driver 18 for SQL Server"
export PIMS_MODE=companion
export PIMS_DATABASE_URL=sqlite:////srv/pims/companion.db
export PIMS_LEGACY_DSN='Driver={ODBC Driver 18 for SQL Server};Server=<copy or secondary>;Database=ProductionData;Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate=yes'
```

**4. Check, then sync once, off-hours:**

```bash
python -m pims legacy check --counts     # the same questions as the script, plus the login gate
python -m pims legacy sync               # first sync reads everything once
python -m pims legacy add-user --username cchiodo --name "Chris Chiodo"
python -m pims serve
```

**5. Schedule it:**

```cron
*/15 * * * *  python -m pims legacy sync          # recent rows, index seeks
0 2 * * 0     python -m pims legacy sync --full   # everything, Sunday 02:00
```

Every run is recorded in the support console's job history.

**6. The LIMS matrix.** Point `PIMS_LIMS_DSN` at the LIMS server (`PHLIMSSQL`)
*directly*, with its own read-only login, rather than reading
`PHLIMSSQL.XLIMSFEEDGROUP` through the PIMS server — a linked-server query
makes the production PIMS server run it on your behalf. The LIMS reader goes
through the same guard and preflight as the mirror.

---

## What works, and what doesn't

| Works on the mirror | Refused — made in the PIMS desktop app instead |
|---|---|
| Dashboard, orders, order detail, activity | Creating, editing, closing orders |
| Inventory, point-in-time balances | Receive, produce, move, load, ship, shrinkage |
| Inquiry (all four tabs), custom query, CSV export | Voids |
| Scan / search box | QC entry, in-process readings, QA checklists |
| Data quality, alerts, daily digest | Load & ship, Blend (hidden) |
| The LIMS matrix with the FE-2026-001 filters | Standing orders, auto-close, GP sync (disabled) |
| Product limits (the companion's own data) | Kiosk mode (hidden) |
| Today — what is waiting, each job opening read-only | |
| Tank board and **Pop out tanks** on another screen | |
| Today's department picker — the legacy `Department`, `PlantDepartment` and `MaterialType.Department_Id` | Acid and other batch screens, staged batches (hidden) |

A refused action says where to make the change — "Make this change in the
PIMS desktop application (Order Selection Menu → Load Trailer); it will appear
here at the next sync." — rather than a bare error. The server refuses by
allowlist: a write endpoint added later is blocked in companion mode until
someone decides otherwise.

**FE-2026-001 without `ALTER` rights.** `PIMS_Matrix_Fix.sql` can never be
deployed by a read-only login — it is three `ALTER PROCEDURE`s. The companion
does not need it: it reads LabWare with the corrected filters itself, so the
matrix is right in the companion even while the legacy screen is still wrong.

---

## Where the map came from

`pims/legacy/schema.py` maps 21 legacy tables and 149 columns to the local
store. None of it is guessed:

- **Table names and join keys** come from the SQL compiled into `PIMS.exe` (the
  Custom Query builder's `FROM`/`JOIN` clauses): `dbo.[Order]`,
  `dbo.[transaction]` *and* `dbo.[Transaction_Archive]` (the app reads both
  with `UNION ALL`, and so does the mirror), `dbo.[PendingShipments]`,
  `dbo.[QC]`, `dbo.[TransType]`, `FECoreData.dbo.[User]`, and the rest.
- **Columns** come from the entity classes in `PIMS.xml`.
- **LabWare** names come from the legacy matrix procedures themselves
  (`PIMS_Matrix_Fix.sql`).

The two sources disagree: `PIMS.xml` dates from 2018, `PIMS.exe` was rebuilt in
2026, and the newer build references columns the old entity model does not
have (`from_qc_id`, FFA, TFA, BOL numbers on transactions). That is why a
column can be *required*, *optional*, or have several *candidate* spellings, and
why nothing runs until `legacy check` — or the readiness script — has compared
the map with the live database.

What the mirror does with legacy data the local store is stricter about:

- **Transaction types** are classified into RECEIVE / PRODUCE / MOVE / LOAD /
  SHIP / SHRINK / ADJUST by name. Anything unrecognised is reported and still
  counts toward balances.
- **Order statuses** carry no "terminal" flag in the legacy database; Closed /
  Cancelled / Void are recognised by name.
- **Orphans** — a transaction pointing at a location that no longer exists —
  are mirrored as they are and counted in every sync report, never "fixed".
- **Legacy users** are mirrored as `legacy:<name>` with no password; they can
  never sign in to the companion. Companion sign-ins are separate accounts,
  created with `legacy add-user`, and no sync ever touches them.
- **Product limits** are not in the PIMS database (they came from the LIMS
  limit sheets), so they are attached to mirrored materials by material number
  and kept only in the companion.

Rehearse the whole thing without a server:
`python scripts/pims_make_legacy_standin.py OUT_DIR` builds a SQLite stand-in
with the legacy table names and quirks, opened read-only at the storage layer.

---

## Beyond read-only

The companion is also a cutover rehearsal. Every sync is a migration dry run
of the mapping in [PIMS_MIGRATION.md](PIMS_MIGRATION.md), and every report
lists what did not map cleanly — so by the time anyone decides to move the
writing screens (Load & ship, Blend, QC entry) over, the data path has been
proven against production for months. That decision, and the write access it
needs, is a separate conversation; nothing in companion mode depends on it.
