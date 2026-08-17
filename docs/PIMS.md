# PIMS — replacement system

PIMS (Production Inventory Management System) is the application Feed Energy's
QC, lab and operations staff use to manage orders, inventory movement, quality
control and reporting. The system in production today is a VB.NET WinForms
client (`PIMS.exe`, .NET Framework 4.5, last rebuilt 2026-04-09) talking to a
SQL Server database (`ProductionData` on `FESQLPROD01\PRODUCTION`), with almost
all business logic living in stored procedures and integrations reached through
linked servers to LabWare LIMS, Great Plains and Active Directory.

This directory documents the replacement: what it does, how it is supported,
what was wrong with the old one, and how to cut over.

| Document | What it covers |
|---|---|
| this file | architecture, screen-by-screen parity, what is and is not built |
| [PIMS_RUNBOOK.md](PIMS_RUNBOOK.md) | running and supporting it: deploy, health, common issues |
| [PIMS_DEFECTS.md](PIMS_DEFECTS.md) | the legacy defect register and how each is addressed |
| [PIMS_MIGRATION.md](PIMS_MIGRATION.md) | cutover from SQL Server / stored procedures |

## Why replace rather than patch

The two open defects on the legacy system are both symptoms of the same
architecture rather than isolated bugs.

*Nobody could see the rules.* The QC validation that started demanding a
moisture, temperature and spintest reading on every purchase order
(FE-2026-002) shipped in build 1.2.23.0. Establishing what it did required
decompiling the executable, because the rule was compiled into a WinForms event
handler and the source was not to hand. The matrix defect (FE-2026-001) needed
`sp_helptext` under an elevated login, which no ordinary user has.

*Nothing measured itself.* PIMS read a retired LIMS database for roughly seven
months. The evidence was there — the newest sample in the database it was
reading was from March, the maintenance jobs had stopped in February — but
nothing in the application looked, so the first signal was a user reporting
blank columns in July.

*Failures were swallowed.* The matrix feature wrapped each cell write in a
try/catch that discarded the error, so a column-name mismatch produced a blank
cell indistinguishable from "this sample was not tested".

Rebuilding addresses all three directly: rules in reviewable, tested Python;
a health check for every dependency; and errors that surface with a reference
number instead of disappearing.

## Architecture

```
pimsweb/            React + Vite browser client (the face lift)
  src/pages/        one file per screen
  src/components/   the shared component vocabulary
  src/lib/api.ts    one place that knows about tokens and error shapes

pims/               Python application
  app.py            FastAPI HTTP API — thin; parses, authenticates, delegates
  services/         the business rules, callable and testable without HTTP
    orders.py         create / edit / filter / close, fulfilment maths
    inventory.py      one posting function for all six movement types
    qc.py             QC records, validation, in-process, QA checklists
    specs.py          what a product is tested for and its limits
    lims.py           the matrix feature and LIMS projection
    inquiry.py        the four inquiry tabs
    query.py          the custom query builder, parameterised
    reference.py      dropdown data
    numbering.py      generated BOL and sample numbers
    prefill.py        what each operator screen should already know
    scan.py           resolve a scanned code to an order, sample, BOL, tank…
    alerts.py         alert rules, dedupe and webhook delivery
    jobs.py           auto-close, standing orders, the daily run
  integrations/     the systems PIMS does not own
    lims_ingest.py    LabWare results into the local projection
    gp_sync.py        Great Plains customers, vendors, order headers
    scale.py          truck-scale weights posted by a plant agent
  health.py         health checks, diagnostics, data-quality probes
  security.py       users, roles, plant access, sessions
  audit.py          the audit trail every write goes through
  observability.py  request timing, correlation ids, recent failures
  db.py, schema.sql the store
  seed.py           demo dataset (real limits, generated transactions)
```

The service layer never imports FastAPI, so every rule in the system can be
exercised from a test, a script or a scheduled job. `tests/test_pims_*.py`
does exactly that.

### Data model

Table names and columns deliberately track the legacy entities so migration is
mechanical — `Order`, `Order_QC`, `Transaction`, `Material`, `Location`,
`QAHeader` and friends are all recognisable. Three things are new:

- **`material_test`** — which analytes a material is actually tested for. This
  is the table the QC screen consults instead of guessing from order type.
- **`material_spec`** — min/max limits per material and analyte, transcribed
  from the PIMS/LIMS limit sheets, including a `needs_review` flag for the two
  products whose QC sheet and product label disagree.
- **`audit_log`** — who changed what, from what, to what.

The inventory ledger is append-only. A mistake is corrected by voiding, which
writes a reversing entry and marks the original; both stay visible and both
count toward the balance, so history reconciles instead of quietly changing.

## Screen parity with the legacy client

| Legacy screen | Replacement | Notes |
|---|---|---|
| Order Selection Menu | **Orders** | One grid with selection instead of two near-identical menus |
| Order Edit Menu | **Orders** | Same filters (plant, type, dept, due dates, material, customer, vendor) plus free-text search |
| Create Order dialog | **Orders → Create order** | Including "# of orders to create" |
| Edit Selected Order | Order detail → Edit | Blocked on closed orders, with a reason |
| Close Selected Orders | Orders → Close selected | Reports per-order outcomes; refuses orders with unshipped loads unless forced |
| Receive | **Plant floor → Receive** | |
| Produce | **Plant floor → Produce** | Input/output, tank time, labour, both balance panels |
| Move | **Plant floor → Move** | |
| Load Trailer | **Plant floor → Load trailer** | Stages a pending shipment |
| Ship Trailer | **Plant floor → Ship trailer** | Staged list, BOL preview, print |
| Shrinkage | **Plant floor → Shrinkage** | |
| Quality Control → Regular QC | Order detail → **Quality control** | Product-driven validation; spec flagging |
| Quality Control → In Process Testing | Order detail → Quality control | Readings against test points |
| QA Checklist | Order detail → **QA checklist** | Exceptions highlighted |
| PIMS Data Inquiry (4 tabs) | **Inquiry** | Location balance (with point-in-time), Activity, Order, QC; CSV export |
| Custom Query / Query Builder | **Custom query** | Curated sources, prompts, save/load, CSV, shows the SQL it ran |
| Add Matrix Results | Custom query → Add matrix results | Reportable current components only; misses reported explicitly |
| Matrix viewer (single sample) | `GET /api/lims/sample/{code}` | API only for now — see "Not built" |
| Reports (ReportViewer / RDLC) | BOL view + CSV export | See "Not built" |
| Change Plant | Plant switcher in the top bar | |
| "You are in the TEST environment" | Environment badge in the top bar | |
| Customers / Materials / Requirements | **Products & limits**, requirements shown on the order | Master data still originates in Great Plains |
| — | **Load & ship** | New: load → QC → checklist → BOL in one flow, pre-filled |
| — | Kiosk mode | New: PIN sign-in, touch layout and idle sign-out for a shared plant terminal |
| — | Scan box | New: one box resolves an order, sample, BOL, tank, product or trailer |
| — | **Support console** | New: health, data quality, alerts, scheduled jobs, audit trail, failures |

## What the system does on its own

Automation is deliberately the boring kind: it fills in what is already known,
watches what nobody was watching, and never decides something a person should.

- **Numbers are minted, not typed.** BOL numbers on every load and receipt;
  sample numbers on request (see the caution in the runbook).
- **Screens arrive pre-filled** — the tank holding the most of the product, the
  plant's default receiving location, the outstanding quantity, the trailer and
  what it last hauled — each with the reason shown beside it.
- **Weights come from the scale** when an agent is running at the plant.
- **Scanning replaces navigation**: one box, any barcode on the floor.
- **Alerts** for a stale LIMS feed, out-of-spec results, trailers loaded and
  not shipped, tanks over 95%, and product-setup gaps — deduplicated, delivered
  to a webhook, and recorded either way.
- **Scheduled jobs** close finished orders, raise standing orders, pull LIMS
  results and GP master data, and mail a daily digest.

Every one of these is a setting away from being turned off, and every job runs
with `--dry-run` first. See [PIMS_RUNBOOK.md](PIMS_RUNBOOK.md) §12–16.

## What is not built

Stated plainly, because a replacement that quietly drops features is worse than
one with a known gap list:

- **RDLC report rendering.** The BOL is reproduced as a printable view and
  every grid exports to CSV, but the other ReportViewer reports have not been
  ported. Porting them needs the `.rdlc` files, which were not in the material
  provided.
- **Great Plains synchronisation.** Customers, vendors and orders sync from GP
  through `GP_Job_Sync_*` procedures today. The replacement models the data and
  reads it, but the sync job itself is not implemented — see
  [PIMS_MIGRATION.md](PIMS_MIGRATION.md).
- **Active Directory sign-in.** Authentication is username/password with
  PBKDF2 hashing and server-side sessions. `security.user_from_token` is the
  single seam where SSO replaces it.
- **Live SQL Server backing store.** The application ships with SQLite so it
  runs anywhere, including this demo. The repository layer is deliberately
  narrow (`pims/db.py`) and the migration document sets out the SQL Server path.
- **Label printing** ("Save and Print Sample Labels") and the maintenance
  request / customer issue menu items.
- **Blend recipes.** The legacy `Order` carries a `Blend_recipe_id`; recipes
  themselves were not part of the material provided.

## Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pims init-db          # creates and seeds a demo database
.venv/bin/python -m pims serve            # http://127.0.0.1:8080

cd pimsweb && npm install && npm run dev   # front-end dev server on :5174
```

The built front end is committed, so `python -m pims serve` alone gives a
working system at <http://127.0.0.1:8080> with API docs at `/docs`.

Demo sign-ins (seeded, demo build only): `cchiodo` (admin), `jmartin`
(supervisor), `rprice` (QC), `toperator` (operator) — password `pims-demo`.

### The browser sandbox

For looking around without running anything, `npm run build:demo` produces a
single self-contained HTML file (`pimsweb/dist-demo/pims-sandbox.html`) that
opens in any browser with no server. The seeded dataset is baked in and the
rules run in the page (`pimsweb/src/demo/`): movements are refused when a tank
cannot cover them, QC validation still asks the material what it is tested for,
voids still write reversing entries, and the support console reports on the
sandbox's own state. Edits live in the tab and a reload resets it.

Two caveats: it is a re-implementation for exploration, so the server in `pims/`
is the one that is tested and the one that would run in a plant; and file
downloads are blocked inside the sandbox, so CSV exports copy to the clipboard
instead. Regenerate the dataset after changing the seed with
`python scripts/pims_export_demo.py`.

## Roles

| Role | Can |
|---|---|
| operator | read orders, post movements, run queries |
| qc | read orders, record QC, edit nothing else |
| supervisor | the above plus edit and close orders, void transactions, support console |
| admin | everything, including product limits and test lists |

Plant access is per user and enforced on every write, not just hidden in the
picker.
