# OleoCast — weather-driven soybean oil intelligence

Predict soybean **yield**, **seed oil concentration**, **fatty-acid
profile**, and **crush value** from public weather data — in season, with
honest, validated uncertainty. Built as a partner-facing platform:
agtech-SaaS marketing site + working analytics product + documented API.

![stack](https://img.shields.io/badge/python-3.11-blue) ![stack](https://img.shields.io/badge/react-18-blue) ![stack](https://img.shields.io/badge/lightgbm-4.x-green)

## Why

A bushel is not a bushel: two fields with identical yield can differ by a
point of oil, which is real money at the crush plant. Oil is made during
seed fill (R5–R6), and seed-fill weather is public data — so the oil a
region will deliver is knowable months before harvest. OleoCast turns that
into forecasts a processor, originator, or grower can act on.

## Quick start

```bash
# backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m soyoil.train          # ~2 min: builds data, validates (LOYO), fits, saves
.venv/bin/uvicorn api.main:app --port 8000

# frontend (dev)
cd web && npm install && npm run dev       # http://localhost:5173 (proxies /api)

# or production layout: build once, FastAPI serves it
cd web && npm run build                    # then open http://localhost:8000

# tests
.venv/bin/python -m pytest tests/ -q
```

## What's in the box

```
soyoil/               core science + ML package
  phenology.py        GDD + photoperiod + frost-termination stage model (VE..R8)
  geography.py        demo regions (8 Corn Belt counties) w/ soils + climate normals
  weather.py          Open-Meteo + NASA POWER connectors (cached, circuit-breaker)
                      + climatology-anchored stochastic generator (offline fallback)
  features.py         stage-window feature engineering (37 features):
                      season-long bucket water balance, VPD, heat-stress days,
                      degree days, photothermal quotient — aligned to R-stages
  agronomy.py         published weather→yield/oil/fatty-acid response functions
  simulate.py         synthetic training-table generator (bootstrap until real labels)
  train.py            LightGBM per target · leave-one-year-out CV · baselines ·
                      conformal intervals · persisted validation report
  predict.py          in-season forecasts, scenario engine, SHAP drivers,
                      season progression, historical comparison
  processing.py       crush economics (oil/meal per bu, EPV, quality premiums)
api/main.py           FastAPI app (OpenAPI docs at /docs)
web/                  React + Vite + Recharts app (marketing + dashboard)
pid/                  agentic P&ID builder (separate tool — see docs/PID_BUILDER.md)
pims/                 PIMS replacement — API + business rules (see docs/PIMS.md)
pimsweb/              PIMS browser client (React + Vite)
tests/                model credibility gates + P&ID builder suite
research/             three deep research briefs (agronomy, data sources, ML design)
docs/MODEL_CARD.md    validation, limitations, and what we must not claim
docs/PID_BUILDER.md   the P&ID builder: agents, rules, and what it does not check
```

## Also in this repo: the P&ID builder

`pid/` is a separate tool that shares the plant-engineering side of the same
domain. Describe a process in prose and it produces a piping and instrumentation
diagram, the schedules that go with it (line list, instrument index, loop and
relief schedules), and a list of everything the drawing still gets wrong.

```bash
pip install -e ".[pid]"
pid example --out /tmp/demo                        # worked drawing, no API call
pid build "Degummed oil is pumped from a day tank, heated against LP steam,
contacted with bleaching earth under vacuum, then filtered." --area 1200
```

Four agents build the drawing — layout, instrumentation, safeguarding, then a
review pass — and a deterministic rule engine checks the result against ISA-5.1
tagging and process-safety conventions. Everything after the last API call is
ordinary code, so `pid validate`, `pid render` and `pid export` are free and
reproducible. It produces a drawing for engineers to review; it is not a
substitute for a HAZOP or a P.E. stamp. Details in
[docs/PID_BUILDER.md](docs/PID_BUILDER.md).

## Also in this repo: PIMS

`pims/` and `pimsweb/` are a replacement for Feed Energy's PIMS — the VB.NET
WinForms Production Inventory Management System that plant, lab and QC staff
use for orders, receiving, production, movement, loading, shipping, shrinkage
and quality control. Same workflows, browser-based, with the business rules in
tested Python instead of stored procedures and a compiled event handler.

```bash
python -m pims init-db       # create and seed a demo database
python -m pims serve         # http://127.0.0.1:8080 (API docs at /docs)
python -m pims diagnose      # every health check; exit 1 if any failed
```

To look around without running anything, `cd pimsweb && npm run build:demo`
builds a single self-contained HTML file that opens in a browser with the
seeded data and the rules running in the page.

It automates the parts operators used to type: BOL and sample numbers are
generated, plant-floor screens arrive pre-filled with the reason shown, a truck
scale can post weights straight into a load, one scan box resolves any barcode
on the floor, and a shared terminal signs in with a PIN and signs itself out.
Scheduled jobs close finished orders, raise standing orders, pull LIMS and GP
data, and raise alerts for a stale lab feed or a trailer that never shipped.

The second half of the job is being able to support it: a health endpoint per
dependency, a data-quality probe that lists the rows behind each finding, an
audit trail with before/after values, and correlation ids that tie a user's
error message to a log line. Both open defects in the legacy system —
a matrix feature reading a LIMS database that was retired seven months earlier,
and a QC validation that demanded moisture, temperature and spintest readings
on products that never run them — are fixed, with tests pinning them, and the
health checks that would have caught each one are in place.

Documentation: [docs/PIMS.md](docs/PIMS.md) (architecture and screen parity),
[docs/PIMS_RUNBOOK.md](docs/PIMS_RUNBOOK.md) (support),
[docs/PIMS_DEFECTS.md](docs/PIMS_DEFECTS.md) (legacy defect register),
[docs/PIMS_MIGRATION.md](docs/PIMS_MIGRATION.md) (cutover).

## API surface

| Endpoint | What it returns |
|---|---|
| `GET /api/forecast?region_id&year&as_of&scenario_temp_c&scenario_precip_pct` | predictions + 80/90% intervals, stage dates, SHAP drivers, crush value, provenance |
| `GET /api/progression?region_id&year` | forecast + interval trajectory across the season |
| `GET /api/history?region_id&year&n_years` | last-N-season retrospectives + mean/min/max/std vs. the current forecast |
| `POST /api/crush` | crush economics for any composition + price deck |
| `GET /api/model/meta` | features, LOYO metrics, conformal widths, training provenance |
| `GET /api/regions`, `GET /api/datasources`, `GET /api/health` | reference data |

## Design decisions that matter

- **Phenology-aligned features, not calendar months.** Oil responds to
  R5–R6 weather; "August rain" is a proxy. We date stages per field-season
  and aggregate weather inside the biological windows.
- **Validation you can audit.** Leave-one-year-out only; skill reported
  against a region-mean+trend baseline; conformal intervals calibrated on
  held-out years, with measured coverage published. Tests fail if a model
  stops beating its baseline or its effect directions contradict the
  agronomy (`tests/test_models_api.py`).
- **Uncertainty scales with ignorance.** Interval width inflates with the
  fraction of the season not yet observed and shrinks toward harvest.
- **Provenance everywhere.** Every forecast is stamped with its weather
  source; when live APIs are unreachable the climatology simulator is used
  *and labeled*, never silently.
- **Honest bootstrap.** Real composition ground truth (USDA NASS yields,
  US Soybean Quality Survey) isn't wired in yet, so models train on
  synthetic seasons generated from published response functions. The model
  card and the product UI both say so prominently. The training pipeline
  retrains unchanged when real labels land.

## Research foundation

Three agent-produced briefs (each with confidence-tagged claims and full
reference lists) live in `research/`:

- `agronomy-oil-drivers.md` — 129 refs on weather → oil/yield/fatty acids;
  resolves the oil–temperature sign contradiction (unimodal, optimum ≈28 °C)
- `data-sources.md` — ~25 public sources with endpoints, licensing
  (incl. the Open-Meteo non-commercial and PRISM restrictions), and the
  finding that public composition data is state-level only
- `ml-modeling-design.md` — published accuracy benchmarks, feature
  engineering practice, validation protocol, conformal uncertainty design,
  and a "claims we must not make" list

## Hosting

The repo is deploy-ready: the built frontend (`web/dist/`) and trained model
bundle (`soyoil/models/`) are committed, so a host only needs Python.

**Render (recommended, free tier):** connect your GitHub account at
render.com → *New + → Blueprint* → pick this repo and branch → Apply. The
included `render.yaml` does the rest; the service comes up at
`https://<name>.onrender.com` with `/api/health` as the health check.
Free-tier services sleep when idle — the first request after a quiet spell
takes ~a minute.

**Any other host** (Railway, Fly.io, a VM): `pip install -r requirements.txt`
then `uvicorn api.main:app --host 0.0.0.0 --port $PORT` (see `Procfile`).

**Office LAN only:** `uvicorn api.main:app --host 0.0.0.0 --port 8000` on
your machine, then colleagues browse to `http://<your-ip>:8000`.

Note: a hosted demo is public to anyone with the URL — it ships no
authentication. Keep internal commercial data out of it (the app serves only
the demo counties and model outputs).

## Roadmap to production

1. NASS Quick Stats connector → real county yield labels (free API key)
2. US Soybean Quality Survey PDF ETL → state-level composition labels;
   hierarchical oil model (state labels, field features)
3. SSURGO soils + CDL soybean masks per field boundary
4. MODIS/VIIRS NDVI in-season features (no-auth ORNL API)
5. Variety/maturity-group metadata from partners (biggest composition lever)
6. Commercial weather licensing (Open-Meteo commercial tier or self-hosted ERA5)

---
*Demo build. Forecasts are decision support, not trading or agronomic advice.*
