# PIMS runbook

For whoever is holding the pager. Everything here works from a browser or a
shell — none of it needs `db_owner`, a decompiler, or a DBA on a call.

## 1. Is it up?

```bash
curl -s https://<host>/api/health
# {"status":"ok","service":"pims","version":"1.0.0","environment":"PRODUCTION",...}
```

`/api/health` is unauthenticated and cheap — point the load balancer at it.

## 2. Is it healthy?

**Support console → Health** in the browser (supervisor or admin), or:

```bash
python -m pims diagnose        # exits 1 when any check has failed
```

Five checks, each returning `ok` / `degraded` / `failed`; the worst one wins.

| Check | Fails when | First action |
|---|---|---|
| `database` | a core table is missing, or `PRAGMA quick_check` is unhappy | restore from backup; do not accept writes until it reads `ok` |
| `lims` | the LIMS projection has not refreshed inside the fail window (default 72 h) | §4 |
| `sessions` | over 500 expired sessions are queued | harmless; `security.purge_expired_sessions()` runs at startup |
| `errors` | over 5% of requests since start returned 5xx | §6 |
| `product_setup` | a finished product has no limits or no test list, or a limit is flagged for confirmation | §5 |

`degraded` means "someone should look today". `failed` means "PIMS is showing
people something wrong right now".

## 3. A user reports a problem

Ask for the **correlation id**. Every error the UI shows carries one
("Reference for support: `a1b2c3d4e5f6`"), it comes back in the
`X-Correlation-Id` response header, and it is in the log line for that request.

```
Support console → Errors        # recent failures, newest first, with the id
GET /api/support/errors?limit=100
```

If the id is not in the list, the request never reached the application —
look at the proxy or the network.

## 4. LIMS results are missing or look wrong

This is the failure mode that cost seven months in 2026 (FE-2026-001), so it
has its own instrumentation.

```
Support console → Health → lims
GET /api/lims/freshness
```

The check reports the **source** the projected rows came from, the **expected**
source from configuration, the age of the last refresh, and how many samples
and test codes are present.

| Symptom | Meaning | Action |
|---|---|---|
| `status: failed`, large `age_hours` | the refresh job has stopped | restart the ETL job; PIMS is serving stale lab data until it runs |
| `sources` contains something other than `expected_source` | rows arrived from a database PIMS is not supposed to be reading | stop the job writing them, delete those rows, re-ingest from the live LIMS |
| `test_codes` far below what the lab sees in LabWare | the feed is filtered wrongly, or is pointed at a retired instance | compare against `SELECT DISTINCT TestCode FROM <lims>.dbo.Tests WHERE RecordStatus = 1` |
| One sample has no results | expected when the test was not run | confirm in **Custom query → Add matrix results**: the `misses` note distinguishes "no result recorded" from a lookup failure |

Reload the projection:

```bash
curl -X POST https://<host>/api/lims/ingest \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"source":"XLIMSFEEDGROUP","rows":[{"sample_code":"...","test_code":"FFA (NIR)","component":"R-FFA","value":56.19,"sampled_at":"2026-08-17T10:00:00Z"}]}'
```

Rows default to reportable and current-version. Superseded versions and
non-reportable components must be marked as such by the feed; PIMS filters them
out of every read, which is what stops the duplicate `%FFA 1` / `%FFA1` columns
from coming back.

Thresholds are configuration, not code: `PIMS_LIMS_WARN_HOURS` (default 24),
`PIMS_LIMS_FAIL_HOURS` (72), `PIMS_LIMS_SOURCE`.

## 5. "Out-of-spec results aren't being flagged"

A product only gets flagged if it has limits, and only gets asked for a reading
if it has a test list.

```
Support console → Health → product setup     # counts the gaps
Products & limits → Test lists                # per product
GET /api/specs?needs_review=true              # limits flagged as contradictory
```

Fix it in **Products & limits**: click a limit to edit min/max. Changes are
audited with the username. Two limits ship flagged for confirmation because the
source sheet disagreed with the product label (HC3900 FFA 65 vs 55, Live Plus
FFA 35 vs 30); PIMS uses the QC-sheet value and shows the flag rather than
choosing silently. Clearing the flag is a deliberate act in the edit dialog.

## 6. Error rate is up

```
Support console → Errors      # counters, recent failures, slow requests (>750 ms)
```

Codes are stable and mean what they say: `validation_error` (the request cannot
be applied as written, with field-level detail), `business_rule` (well-formed
but not allowed now — insufficient stock, closed order), `forbidden` (role or
plant access), `integration_error` (an upstream system).

A spike in `business_rule` is usually a process problem, not a software one:
look at which rule.

## 7. The numbers look wrong

**Support console → Data quality** runs six probes and shows the rows behind
each, with what to do:

| Finding | Usual cause |
|---|---|
| Locations with a negative balance | a movement was posted against the wrong tank; void it |
| Locations over stated capacity | the capacity on the location record is wrong, or a receipt was overstated |
| Trailers loaded over 2 days ago and never shipped | the ship step was skipped in PIMS after the truck left |
| Open orders past their due date | housekeeping |
| QC records with no sample number | the LIMS result cannot be matched back to the load |
| QC sample numbers with no LIMS result | either the sample was never logged in LabWare, or §4 |

Also available as `python -m pims check`.

## 8. Who changed this?

```
Support console → Audit trail            # everything, filterable by user
Order detail → History                   # one order, with before/after values
python -m pims audit --entity order --entity-id 329421
```

Saving a QC record over an open warning is recorded, with the warnings that
were showing. If people are routinely clicking through a validation prompt,
that is visible instead of invisible — the failure mode the 2026 bug report
warned about.

## 9. Deploying

```bash
pip install -r requirements.txt
cd pimsweb && npm install && npm run build     # only if the UI changed
uvicorn pims.app:app --host 0.0.0.0 --port $PORT
```

Environment:

| Variable | Default | Notes |
|---|---|---|
| `PIMS_ENV` | `DEMO` | Anything other than `PRODUCTION` paints the badge in the UI |
| `PIMS_DATABASE_URL` | `sqlite:///data/pims.db` | |
| `PIMS_AUTO_SEED` | `true` | **Set to `false` in production.** Seeding only happens on an empty database, but be explicit |
| `PIMS_LIMS_SOURCE` | `XLIMSFEEDGROUP` | The LIMS database PIMS expects results from |
| `PIMS_LIMS_WARN_HOURS` / `PIMS_LIMS_FAIL_HOURS` | 24 / 72 | Freshness thresholds |
| `PIMS_SESSION_HOURS` | 12 | Session lifetime |

Before going live: set `PIMS_ENV=PRODUCTION`, set `PIMS_AUTO_SEED=false`,
replace the seeded demo users, and put TLS in front of it.

## 10. Backup and restore

The SQLite deployment is a single file plus its WAL:

```bash
sqlite3 data/pims.db ".backup '/backups/pims-$(date +%F).db'"   # safe while running
```

Restore is a file copy with the service stopped. On SQL Server, the existing
database backup policy covers it — see [PIMS_MIGRATION.md](PIMS_MIGRATION.md).

Nothing in PIMS deletes an inventory transaction or a QC record: voids are
reversing entries and QC voids set `active = 0`. Recovery from a bad entry is a
void, not a restore.

## 11. Rolling back a release

The application is stateless apart from the database. Redeploy the previous
revision; no schema migration ships without a documented reverse. If a release
introduced a rule change (as build 1.2.23.0 did), the rule is in
`pims/services/`, the test that pins it is in `tests/`, and `git log` on that
file answers "when did this change and why" without a decompiler.
