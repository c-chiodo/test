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

---

## Defects found in the replacement, 2026-08-17

The register above covers the legacy system. This section covers the
replacement, and exists for the same reason: an undocumented defect is one that
gets rediscovered by an operator at four in the morning.

These were found by driving the running application the way a plant would —
loading trucks, dropping the network mid-post, shipping the same trailer from
two terminals, letting the kiosk time out mid-flow, and measuring the screens
against WCAG 2.2 and the ISA-101 guidance for control-room displays. Every one
is fixed, and every one has a test in `tests/test_pims_recovery.py` named after
the mistake it guards against.

### Wrong data

| # | What went wrong | Cause | Fix |
|---|---|---|---|
| R-01 | A dropped connection after the server committed let the operator post the same load again. One truck, four loads, four BOL numbers, 4,936 lbs staged against 1,234 lbs of product. | `fetch` transport failures surfaced as the bare string "Failed to fetch" — no statement of whether the write landed — and `post()` had no way to recognise a retry. | `inventory_transaction.idempotency_key` with a unique index; the form mints one key and holds it until the post succeeds. A retry returns the transaction that already exists. The client now says the network dropped, that the write may have landed, and that pressing the button again is safe. |
| R-02 | Five concurrent ships of one staged trailer: three succeeded. 15,000 lbs shipped against a 5,000 lb load, same BOL on all three. | `ship()` read `pending_shipment.shipped` and checked it *outside* the transaction it then opened. Each uvicorn worker thread has its own connection, so several read `shipped = 0`. | The claim is the guard: a conditional `UPDATE … WHERE shipped = 0` inside the transaction, which `BEGIN IMMEDIATE` serialises. Verified: 5 concurrent requests → 1 accepted, 4 refused, 1 SHIP row. |
| R-03 | One MOVE turned 1,000 lbs of Acidulated Soapstock into 1,000 lbs of Cattle Blend. Another turned 1,000 lbs into 180,000 lbs. Both posted by an operator, both HTTP 201. | `post()` checked that materials and quantities were *present* and greater than zero, and never compared the two sides. | MOVE and LOAD must take out and put in the same product and the same weight. PRODUCE remains the one operation allowed to transmute or change yield, which is what producing is. The Plant floor form locks the To fields to the From values for those operations. |
| R-04 | The staged-trailer list and the printed bill of lading named the product on the **order header**, not the product on the trailer. A load of Soapstock - Veg printed as All Veg HCFC. | `pending_shipments()` joined `material` through `o.material_one_id`. | The join goes through the transaction. The BOL prints the products actually loaded, and says so loudly when they disagree with the order. |
| R-05 | Voiding a SHIP left the stage marked shipped, so the trailer standing at the dock could never be shipped again without a database edit. Voiding a LOAD marked the cancelled load as *shipped*. | One statement for every void: `UPDATE pending_shipment SET shipped = 1 WHERE transaction_id = ?`. | A `cancelled` column, so the two states stay apart. Voiding a LOAD cancels the stage; voiding a SHIP puts the trailer back on the list. |
| R-06 | Fulfilment counted any material, so a load from the wrong tank showed the order progressing — one order reached 223% complete, 4,936 lbs of it the wrong product. Orders already fully loaded sat in "ready to load" showing "Left to load: 0". | `orders.progress` had no material filter; the picker clamped the remainder at zero. | Fulfilment counts only the ordered product. A load of anything else against a sales order is refused, naming both products. The remainder is signed, so "3,500 over" is visible. Loading past the ordered quantity asks for confirmation rather than proceeding in silence. |
| R-07 | `POST /api/orders/{id}/qc` with `{"moisture": "abc", "ph": "7.2.1"}` returned 201 and an empty record, with a green "QC recorded". | `to_float` returns `None` for anything unparseable and `qc.save` wrote it through. | A non-empty value that will not parse is rejected by name. Blank is still blank — partial records are normal QC work. |
| R-08 | An out-of-spec result could be saved without acknowledgement by anything other than the one screen that asked, and the record kept no trace of the sign-off. | The gate was client-side only. | The server refuses, and `qc.acknowledged_warnings` plus `warning_snapshot` put the sign-off and what it was signed against on the record. A *missing* reading stays advisory. |
| R-09 | `PUT /api/specs` and `PUT /api/materials/{id}/tests` were gated on `spec.read`. A QC user widened an FFA limit from 65 to 999 and the failing result passed. | There was no `spec.write` permission. | There is now, and supervisors and admins hold it. |

### Recovery

| # | What went wrong | Fix |
|---|---|---|
| R-10 | Two identical **Ship** buttons on the Load & ship screen, and `ShipStep` was never told which trailer the flow had just loaded. Clicking the first one shipped somebody else's truck. | The step knows its own load. That row sorts first, is marked **this truck**, and is the only one with a primary button; shipping any other trailer names it and asks. The Plant floor ship list asks too. |
| R-11 | The kiosk signs out after 180 idle seconds and every value in the flow lived in React state. The load was already committed — product gone, BOL minted, stage waiting — and the operator came back to an empty screen and loaded the truck again. | The flow is kept in `sessionStorage` and rehydrated on mount, with a banner naming the trailer, the weight and the BOL, and whether it shipped. The sign-out itself now says what happened and that nothing was lost. |
| R-12 | An operator could not reverse their own mistake and was told only `Your role (operator) cannot txn void.` The Activity screen rendered nothing at all where the button would be. | An operator may reverse their own posting for 12 hours, provided it has not shipped. Where they may not, the button is present and disabled with the reason beside it. Refusals name the action in plain words and who to ask. |
| R-13 | The bill of lading printed every load on the order and totalled them; printing produced the whole application — sidebar, nav, toasts and all. | The BOL takes a transaction and covers that truck. A print stylesheet reduces the page to the document. |
| R-14 | Four of the six checklist questions inspect an *empty* trailer, and all six were asked after the load. | Questions carry a stage. The trailer check is step 2, before the load; seals and load temperature are asked after. It warns rather than blocks — an operator with a driver waiting can post the load and come back, and the ledger row records that it was posted before the trailer check. |
| R-15 | "N/A" on a checklist left no trace, and text and numeric questions had no way to answer N/A at all — the only way past a question that did not apply was to invent a value. | N/A is available on every question type and is reported separately from exceptions, so "checked, does not apply" is distinguishable from "never asked". |
| R-16 | The QC banner promised seven tests on a form with six fields. | The banner separates what is typed here from what comes back from the lab. |
| R-17 | The order History tab was empty for every order, including one carrying four duplicate loads. | `audit_log.order_id`, and the history returns the order's loads, ships, voids and QC together. |
| R-18 | Field-level errors printed database column names: `from_material_id: Choose the material being taken.` | They print the label the form uses. |
| R-19 | `ADJUST` and `SHRINK` — the two operations that change the books with nothing physically moving — needed no reason. | Both do. |

### Plant floor and accessibility

| # | What went wrong | Fix |
|---|---|---|
| R-20 | `body.kiosk` was applied inside `Shell`, which only mounts after sign-in — so the PIN screen, the one screen every operator touches at shift change with gloves on, rendered at desktop density. | Applied in `Session`, before the sign-in screen renders. |
| R-21 | `Kiosk.tsx` contained no `<input>` at all, despite a comment claiming a scanner or keypad could type the PIN. Anyone without a working touchscreen, or using assistive technology, could not sign in. | A real labelled field, focused on arrival, that a keyboard, a badge scanner and a screen reader can all use. The pad writes into it. |
| R-22 | No throttling on PIN attempts. A four-digit PIN is 10,000 guesses and a kiosk sits in a yard. | Five failures pause the account for five minutes, on PINs and passwords alike. |
| R-23 | Auto-submit at four digits would have burned an attempt on a half-typed longer PIN once throttling existed. | Explicit Sign in. |
| R-24 | `.scan input:focus { outline: none }` removed the focus ring from the barcode target. | Restored, on the wrapper and the field. |
| R-25 | Checkboxes rendered at the browser default of 13 px — including "I have reviewed this out-of-spec result" — and the checklist Yes/No/N/A buttons sat 6 px apart. | 22 px controls in 44 px rows; answers 14 px apart with a 76 px minimum width, and larger again in kiosk mode. |
| R-26 | `--text-3` carried field hints at 3.05:1, including the "On hand" line that stops a tank being over-drawn. `--warn` and `--ok` failed against their own soft backgrounds. White on the dark theme's light brand and accent colours measured 2.2–2.7:1. | Every pair measured and corrected: hints 4.6:1, accents 5.5–6.6:1 on their own backgrounds, and an `--on-accent` token that flips with the theme (7.1:1 and 8.5:1 in dark). |
| R-27 | Kiosk scaling stopped at buttons, inputs, labels, cells and steps — hints, badges, chips, alerts and toasts, which is where the explanations live, stayed at desktop size. | All of them scale. |

### Not a code defect: the demo dataset

The seed loaded every sales order to its full quantity, so "Sales orders ready
to load" was a screen of orders with nothing left to load. Roughly half of the
open sales orders now carry a partial load, which is what a loadout screen
looks at for most of a shift.
