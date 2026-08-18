# AP Agent — emailed POs into Dynamics GP, no manual entry

The AP agent watches a mailbox for vendor purchase-order documents
(shipment confirmations, packing slips, invoices), extracts the order data
with Claude, reconciles it against the open PO in Microsoft Dynamics GP,
and produces the receivings transaction so the goods can be received in GP
without anyone re-keying the document.

```
vendor email ──▶ intake ──▶ extraction (Claude ⟵ vendor profile hints)
                                  │
                                  ▼
                     validation vs GP master data
                (vendor, open PO, items, qty, price)
                                  │
              ┌───────────────────┴───────────────────┐
              ▼                                       ▼
     clean + trusted vendor                  anything uncertain
              │                                       │
              ▼                                       ▼
   eConnect XML → outbox →              review queue → human approves
   on-prem bridge → GP                  (corrections teach the vendor
   (receipt posted)                      profile) → post
```

## The learning loop (why it gets smarter per vendor)

Every vendor formats documents differently. The agent keeps a **vendor
profile** (SQLite) per sender domain:

| What it learns | How it learns it | How it's used next time |
| --- | --- | --- |
| Item aliases (vendor SKU → GP item number) | The AP clerk fills in the GP item during review | Applied automatically; injected into the extraction prompt |
| Unit-of-measure conventions (`CTN` → `Case`) | Corrections during review | Mapped before validation |
| Layout notes ("PO number is in the subject line") | Claude records `layout_observations` on every document; human corrections add lessons | Replayed as prompt hints on the next document from that vendor |
| Trust score | Ratio of documents that needed no human edits | Gates auto-post |

**Auto-post is earned, never default.** A vendor must accumulate
`min_trusted_docs` (default 3) documents with a ≥90% clean track record
before the agent posts without review — and even then, any error-level
validation issue (unknown PO, over-receipt, >10% price variance) or a
low-confidence extraction still stops for a human.

## Modules

| File | Responsibility |
| --- | --- |
| `ap_agent/intake.py` | Email sources: `.eml` drop folder, IMAP (Gmail / M365 / Exchange) |
| `ap_agent/extraction.py` | Claude structured-output extraction with vendor hints; offline stub for tests |
| `ap_agent/store.py` | SQLite: vendor profiles + learning, review queue, dedup ledger, receipt numbers |
| `ap_agent/matching.py` | GP master data (CSV snapshot or read-only SQL) + validation rules |
| `ap_agent/gp.py` | eConnect `POPReceivingsType` XML + CSV fallback |
| `ap_agent/pipeline.py` | Orchestration, routing, approval/learning workflow |
| `ap_agent/cli.py` | `process`, `review`, `vendors` commands |

## Quick start (offline demo — no API key)

```bash
pip install -e .[ap]
python -m ap_agent process --eml-dir examples/ap_emails \
    --masterdata examples/gp_masterdata --stub
python -m ap_agent review list
python -m ap_agent review approve 1
python -m ap_agent vendors          # see what it learned
```

Live extraction: drop `--stub` and set `ANTHROPIC_API_KEY`. PDFs attached
to emails are passed to Claude natively (no OCR step needed).

## Validation rules

| Check | Severity |
| --- | --- |
| Sender/vendor not in GP | error |
| PO number missing / not an open PO / belongs to another vendor | error |
| Line item not on the PO (after alias resolution) | error |
| Quantity exceeds remaining-to-receive | error |
| Unit cost >10% off PO price | error |
| Unit cost 2–10% off PO price | warning |
| UofM differs from the PO | warning |

Errors block posting; the reviewer must correct them (which teaches the
profile) or reject the document.

## Getting the receipt into GP

The agent emits one eConnect XML file per receipt into an **outbox**
directory — a `POPReceivingsType` document with `taPopRcptHdrInsert`
(`POPTYPE=1`, Shipment) and one `taPopRcptLineInsert` per line, batch
`APAGENT`. Post them from a machine that can reach the GP SQL server with
a small bridge, e.g. a scheduled PowerShell script using the eConnect .NET
assembly:

```powershell
Add-Type -Path "C:\Program Files\Microsoft Dynamics\eConnect 18\Microsoft.Dynamics.GP.eConnect.dll"
$svc = New-Object Microsoft.Dynamics.GP.eConnect.eConnectMethods
$conn = "data source=GPSQL;initial catalog=YOURCO;integrated security=SSPI"
Get-ChildItem \\share\ap_outbox\*.xml | ForEach-Object {
    $svc.CreateEntity($conn, (Get-Content $_ -Raw))
    Move-Item $_ \\share\ap_outbox\archive\
}
```

Receipts land in a GP batch (`APAGENT`) for a final human glance in
*Transactions → Purchasing → Receivings Transaction Entry* before posting —
or post the batch automatically once you trust the flow. Alternatives:
`write_csv_fallback()` produces flat files for SmartConnect / Integration
Manager.

Master data can come from live read-only SQL (`SqlMasterData`, needs
`pyodbc` and a read-only login to the company DB: `POP10100/POP10110`,
`PM00200`) or from scheduled CSV snapshots (`CsvMasterData`) if the agent
runs off-network.

## Operational notes

- **Idempotent**: processed message-ids are ledgered; re-running a folder
  or replaying a mailbox never double-posts.
- **Receipt numbers** are sequenced `RCTAP000001…` from the agent's own
  DB so outbox files are unique and traceable back to the email.
- **Everything is inspectable**: the outbox XML is the exact document the
  bridge submits; the review queue stores the full extraction + reasons.
- **Not yet handled** (by design, park for phase 2): multi-PO documents in
  one email, landed cost/freight lines, non-PO invoices (PM invoice
  entry), lot/serial-tracked items (need `taPopRcptLotInsert`), and
  auto-matching the vendor invoice for 3-way match.
