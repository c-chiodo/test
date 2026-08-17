# Legacy defect register

Every documented issue found in the legacy PIMS material, what caused it, and
where it stands in the replacement. Each entry names a test that would fail if
the behaviour regressed.

---

## FE-2026-001 — "Add Matrix Results" shows a stale test list, duplicate columns and no results

**Reported** 2026-07-20 · **Source** `PIMS_Matrix_Diagnosis_and_Fix.docx`,
`PIMS_Matrix_Fix_Writeup.docx`

### What happened

PIMS read lab results from `MatrixFeedEnergy`, a copy of the retired LabWare
instance, while the lab had moved to `XLIMSFEEDGROUP` on the `PHLIMSSQL` linked
server in early 2026. Users saw:

- a test list of 98 stale codes instead of the 53 current ones, with renamed
  codes that no longer matched the lab (`PHOSPHORUS(CL)` → `PHOSPHORUS (CL)`);
- duplicate near-identical columns (`%FFA 1` beside `%FFA1`), because the procs
  pulled every historical test version — only 62 of 751 test rows were current;
- empty FFA columns, because samples logged after the cutover exist only in the
  new LIMS.

Three defects in the stored procedures, all in the new-LIMS branch that had
been left unfinished during migration:

1. `Matrix_Sample_TestCodes` decided "is this a new-LIMS sample?" with
   `SampleCode = @sample`. The selection dialog passes `'%'`, which never
   equals a real sample, so the list always fell back to the legacy database.
2. `AND SR.IncludeInReport = 1` was commented out in the new-LIMS path of
   `Matrix_Sample_ComponentNames` and `Matrix_Sample_Data`, so non-reportable
   components became empty and duplicate columns.
3. Debug `PRINT` statements left in `Matrix_Sample_ComponentNames`.

Compounding all of it: the per-cell write in `frmCustomQuery.btnAddTestResults_Click`
was wrapped in a try/catch that swallowed errors, so a column-name mismatch
produced a blank cell with no warning — indistinguishable from "not tested".

### Status: **fixed by design**

`pims/services/lims.py`:

- One `REPORTABLE_CLAUSE` applied to every read: `include_in_report = 1 AND
  current_version = 1 AND component NOT LIKE 'DATE-%'`. Superseded versions and
  comment components cannot reach a column, from any code path.
- Every projected row records the `source` it came from and when it was
  `retrieved_at`. `freshness()` turns that into an `ok`/`degraded`/`failed`
  status against configurable thresholds, shown on the dashboard and the
  support console. Results arriving from more than one source is itself a
  degraded status — the exact condition that went unnoticed for seven months.
- `matrix()` returns an explicit `misses` list. A blank cell always has a
  matching entry saying which sample and column had no result. Nothing is
  swallowed.

**Pinned by** `tests/test_pims_lims.py`:
`test_only_reportable_current_components_are_returned`,
`test_a_blank_matrix_cell_is_always_explained`,
`test_stale_projection_fails_the_check`,
`test_results_from_two_sources_are_reported_as_degraded`.

### Still needed operationally

The projection has to be fed. The ETL that reads current-version rows from the
live LIMS into `lims_result` is a deployment task — endpoint and payload are in
[PIMS_RUNBOOK.md](PIMS_RUNBOOK.md) §4, cadence in
[PIMS_MIGRATION.md](PIMS_MIGRATION.md).

---

## FE-2026-002 — QC validation demands moisture / temperature / spintest on purchase orders that don't run those tests

**Reported** 2026-07-21 · **Source** `PIMS_Bug_Report_QC_Validation.docx` ·
**Introduced** build 1.2.23.0 (compiled 2026-06-19); absent in 1.2.20.0

### What happened

`frmQualityControl.tab_RQC_Validate_PrintAndSave` decided which readings a QC
record must carry from the order type and department alone:

```
runChecks = (Order.Ordertype_id = 1 AND Order.Department_id = 2)   -- SO / loadout
         OR  Order.Ordertype_id = 3                                -- PO, any department
if runChecks:
    warn if Moisture <= 0  -> "Expected a value greater than 0 for the moisture"
    warn if Temp     <= 0
    warn if Spintest <= 0
```

Because order type 3 matched unconditionally, every purchase-order sample was
required to have all three — including soaps, water and cattle products that
never run them. Reported impact: staff prompted on every save, with the
attendant risk of placeholder values being entered to clear the prompt, or of
routinely clicking through validation warnings.

### Status: **fixed**

The question "does this need a moisture value?" is answered by the product, not
the order type. `material_test` holds the analytes each material is tested for;
`pims/services/specs.py::required_tests` reads it; `qc.validate` warns only for
those, and names the product in the message:

> Moisture is missing. 05001 AV4000 is tested for moisture.

The QC screen greys the fields the product does not run and states plainly
which tests apply. Warnings remain advisory — QC staff legitimately record
partial results — but saving over an open warning sets `acknowledged_warnings`
in the audit trail, so a habit of clicking through is visible.

Separately, values that are impossible rather than merely missing (a negative
moisture, a pH of 47) are now rejected outright; the legacy screen accepted
them.

**Pinned by** `tests/test_pims_qc.py`:
`test_purchase_order_does_not_demand_tests_the_product_never_runs`
(parameterised over soaps, water and cattle blend),
`test_oil_product_still_warns_when_a_required_test_is_missing`,
`test_required_tests_come_from_the_material_not_the_order_type`,
`test_saving_qc_records_acknowledged_warnings`.

---

## FE-2026-003 — Contradictory product limits

**Source** `PIMS_LIMS_Material_Limits.xlsx`, `PIMS_LIMS_Filters_by_Test.txt`
(rows marked ⚠)

Two products carry an FFA limit where the QC sheet and the product label
disagree:

| Material | Product | QC sheet | Label |
|---|---|---|---|
| 05004, 05068 | HC3900 | ≤ 65 | ≤ 55 |
| 05006, 05033, 05048, 05049 | Live Plus | ≤ 35 | ≤ 30 |

### Status: **surfaced, awaiting a business decision**

PIMS loads the QC-sheet value and flags the row (`material_spec.needs_review`).
The flag appears in **Products & limits → Needs confirmation**, in the QC entry
hint, and as a `degraded` product-setup check. Clearing it is a deliberate act
in the edit dialog, recorded in the audit trail.

This is not a software fix — QC has to decide which value is right. The point
is that the disagreement is in the system rather than in a spreadsheet comment.

**Pinned by** `tests/test_pims_qc.py::test_contradictory_limits_are_surfaced_not_silently_chosen`.

---

## FE-2026-004 — Business logic invisible to the people supporting it

**Source** the investigation trail itself: `matrix_diag*.sql`, `read_procs.sql`,
`dump_procs.ps1`, and the note in the bug report that the validation rule was
established "by decompiling 1.2.23.0".

A standard account (`DESMOINES\cchiodo`) could execute the stored procedures but
had no `VIEW DEFINITION`, so reading the rule required an elevated login;
application-side rules required a decompiler. Diagnosing a two-line defect took
a day of ad-hoc SQL and a binary string comparison between two builds.

### Status: **addressed structurally**

Business rules live in `pims/services/*.py` — reviewable in a pull request,
covered by tests, and attributable through `git log`. The custom query builder
returns the SQL it ran alongside the results. Support screens read through the
same API as everything else, gated on a role rather than a database grant.

---

## Usability issues carried over from the user guide

From the troubleshooting section of `PIMS_User_Guide.docx` — documented as
"things users ask about", which is a reasonable definition of a defect:

| Guide entry | Now |
|---|---|
| "A matrix column is blank for some rows … if a sample you KNOW was tested is blank, report it to IT" | The `misses` list distinguishes "no result recorded" from a failure, and the LIMS freshness check tells you which it is |
| "Duplicate-looking test columns … pick the one carrying the value" | Cannot occur: non-reportable and superseded components are filtered on every read |
| "The test list doesn't match what's in LIMS — tell IT" | Freshness check reports source, age and test-code count without a ticket |
| "Out-of-spec results aren't flagged — product limits may not be set" | The product-setup check counts products with no limits or no test list; Products & limits shows and edits them |
| "Text filter returns nothing — remember % is the wildcard" | Still true (LIKE is LIKE), but the placeholder in the filter row says so |
