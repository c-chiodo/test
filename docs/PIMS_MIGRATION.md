# Migration and cutover

How to get from the WinForms client on `ProductionData` to this application,
without a big-bang weekend.

## The shape of the problem

| | Legacy | Replacement |
|---|---|---|
| Client | `PIMS.exe`, .NET 4.5 WinForms, installed per machine | Browser |
| Business logic | Stored procedures in `ProductionData` + compiled event handlers | `pims/services/*.py` |
| Store | SQL Server `FESQLPROD01\PRODUCTION` | SQLite today; SQL Server supported (below) |
| LIMS | Linked server `PHLIMSSQL` read live, per result row | Projection table refreshed by a job |
| ERP | Linked server `GP`, `GP_Job_Sync_*` procedures | Not implemented — see step 4 |
| Directory | Linked server `ADSI` | Username/password; SSO seam documented |
| Reports | ReportViewer / RDLC | BOL view + CSV; RDLC not ported |

## Backing store: SQLite to SQL Server

The application talks to the database through `pims/db.py` — about 120 lines of
connect, query, insert, update, transaction. There is no ORM and no schema
introspection, so the port is a rewrite of that one module plus a dialect pass
over the SQL in `pims/services/`.

What needs attention when you do it:

1. **Placeholders.** `?` → `?` with pyodbc, so most statements are unchanged.
2. **`"order"` quoting.** SQLite double quotes → SQL Server brackets, or rename
   the table to `orders` in `schema.sql` and update the four services that name
   it.
3. **Date functions.** `date('now', '-30 days')` and `datetime('now', '-1 day')`
   appear in `health.py`, `qc.py` and `orders.py` → `DATEADD`. There are eight
   of them; `grep -n "'now'" pims/` finds them all.
4. **`AUTOINCREMENT`** → `IDENTITY`, and `db.insert` returns
   `SCOPE_IDENTITY()` instead of `lastrowid`.
5. **Balance CTE.** `inventory.location_balance` is standard SQL and ports
   as-is.
6. **Concurrency.** SQLite serialises writers; SQL Server does not. The posting
   path already wraps its read-then-write in one transaction
   (`db.transaction`), so it needs `UPDLOCK`/`HOLDLOCK` on the balance read, or
   `SERIALIZABLE`, to keep the stock check honest under concurrent posting.

Nothing else in the codebase knows what the database is.

## Data migration

Column names track the legacy entities, so the bulk of it is
`INSERT ... SELECT`. The mapping, legacy → new:

| Legacy (`ProductionData`) | New | Notes |
|---|---|---|
| `Order.Order_id`, `Ordertype_id`, `Order_date`, `Due_date`, `Order_reference`, `Company_id`, `Plant_id`, `Department_id`, `Blend_serial_number`, `Vendor_id`, `Customer_id`, `Material_one_id`…`Material_four_id`, `Material_one_quantity`, `Status_id`, `Active`, `Date_added`, `Added_by`, `Date_modified`, `Modified_by` | `order.*`, same names lower-cased | `Blend_recipe_id` has no home yet (recipes not modelled) |
| `Transaction.Transaction_id`, `Parent_transaction_id`, `Transtype_id`, `User_id`, `Plant_id`, `Department_id`, `Transaction_date`, `User_date`, `Order_id`, `From_material_id`, `From_location_id`, `From_qty`, `To_material_id`, `To_location_id`, `To_qty`, `Employee_hours`, `From_location_hours`, `Comments`, `Remarks` | `inventory_transaction.*` | `From_location_hours` → `tank_hours`; `Archive`/`Purge` are dropped — the ledger is append-only. Set `voided = 0, is_reversal = 0` for all history |
| `QC.*` (`Qc_id`, `Order_id`, `Bol_number`, `Test_date`, `Performed_by`, `Moisture`, `Temp`, `Ph`, `Spintest_fallout`, `Seal_number`, `Last_material_hauled`, `Sample_number`, `Blend_serial_number`, `Comments`) | `qc.*` | `ffa`, `tfa`, `flash_pf`, `steam_on` exist on the screen but not the documented entity — map from whatever columns hold them |
| `Material`, `MaterialType`, `MaterialPlant` | `material`, `material_type`, `material_plant` | `family` is new; populate from the limit sheet grouping |
| `Location` (incl. `Max_capacity`, `Bol_Required`, tank geometry) | `location` | Tank geometry columns (`Max_Feet`, `Tank_Diameter`, `Cone_Height`…) are not modelled; add them if strapping tables are needed |
| `Customer` (`Gp_*`), `Vendor` (`Gp_*`) | `customer`, `vendor` | Keep the GP key in `gp_custnmbr` / `gp_vendorid` |
| `Customer_Requirements`, `Vendor_Requirements` | `partner_requirement` with `party_type` | |
| `QAHeader`, `QAQuestion`, `QAResponse`, `QAConfiguration` | `qa_header`, `qa_question`, `qa_response` | `QAConfiguration` folds into question ordering |
| `TestPoint` | `test_point` | |
| `PendingShipments` (`Stage_id`, `Delete_flag`) | `pending_shipment` (`shipped`) | |
| `SavedCustomQuery.Text` | `saved_query.definition` | **Not mechanical.** Legacy saved queries store generated SQL; the new builder stores a JSON definition. Either re-create the saved queries by hand (there will not be many that matter) or write a one-off parser |
| `UserPlantAccess`, `UserCompanyAccess` | `user_plant_access` | Company access is not modelled |
| `MachineSetting`, `System_Settings` | `system_setting` | |

Two tables have no legacy source and must be populated before go-live:

- **`material_test`** — which analytes each product runs. Derive from the LIMS
  limit sheets, then have QC confirm. This drives QC validation; getting it
  wrong reproduces FE-2026-002 in the other direction (not asking for a
  reading that should be taken).
- **`material_spec`** — min/max limits. Transcribed from
  `PIMS_LIMS_Material_Limits.xlsx` in `pims/seed.py`; verify against LIMS
  before trusting them in production.

Migration order (foreign keys): companies, plants, departments → material
types, materials, material_plant → location types, locations → customers,
vendors, requirements → users, plant access → orders → transactions → QC → QA →
saved queries.

## The LIMS feed

Do **not** repeat the linked-server-per-result-row design: the fix write-up
notes that `Matrix_Sample_Data` ran once per row and a 509-row screen meant 509
round trips.

Instead, run a scheduled job that reads current-version, reportable rows from
`[PHLIMSSQL].[XLIMSFEEDGROUP]` and posts them to `POST /api/lims/ingest`:

```sql
SELECT s.SampleCode, st.TestCode, sr.ComponentName, sr.ResultValue, s.SampledDate
FROM   XLIMSFEEDGROUP.dbo.Samples       s
JOIN   XLIMSFEEDGROUP.dbo.SampleTests   st ON st.SampleCode = s.SampleCode
JOIN   XLIMSFEEDGROUP.dbo.SampleResults sr ON sr.SampleCode = s.SampleCode
                                          AND sr.TestCode   = st.TestCode
WHERE  st.AuditFlag = 0                 -- current version only
  AND  sr.IncludeInReport = 1           -- the filter whose absence caused FE-2026-001
  AND  sr.ComponentName NOT LIKE 'DATE-%'
  AND  s.SampledDate >= DATEADD(day, -2, GETDATE());
```

Every two hours keeps the freshness check comfortably inside its 24-hour warn
threshold. If the job stops, the check goes `degraded` at 24 h and `failed` at
72 h, on the dashboard, where somebody sees it.

## Great Plains

`GP_Job_Sync_*` currently moves SO/PO headers and customer/vendor master into
PIMS. Two options:

- **Keep the existing jobs**, repointed at the new tables. Least work; keeps
  the sync in SQL where the team already maintains it.
- **Write a connector** that reads GP and posts through the API, so the same
  validation applies to synced orders as to typed ones. Better long-term, more
  work.

Either way, GP remains the master for customers, vendors and order headers.
PIMS should not let a user edit a GP-sourced customer.

## Cutover plan

1. **Parallel run, read-only.** Deploy against a restored copy of
   `ProductionData`. Nobody types into it; QC and supervisors use it to look
   things up. Compare balances and order progress against the legacy screens
   and reconcile the differences — expect some, because the new ledger counts
   voided/reversal pairs explicitly.
2. **One plant, one department.** Move Des Moines loadout (or whichever has the
   most patient supervisor) to posting in the new system for real, with the old
   client still installed. Two weeks.
3. **Watch the support console daily** during that period. Data-quality
   findings early on are usually process differences, not bugs — a trailer
   loaded but never shipped in PIMS shows up immediately here and was invisible
   before.
4. **Remaining departments, then plants.**
5. **Decommission** the client only after a full month-end has closed cleanly
   in the new system.

## Things to decide before step 2

- Which FFA limit is right for HC3900 and Live Plus (see
  [PIMS_DEFECTS.md](PIMS_DEFECTS.md) FE-2026-003).
- Whether QC warnings should ever be blocking, and for whom.
- Whether SSO is required at go-live or can follow.
- Which RDLC reports are actually still used — the answer is usually "two of
  them", and it is worth finding out before porting fourteen.
