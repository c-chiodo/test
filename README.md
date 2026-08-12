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
tests/                29 tests incl. model credibility gates
research/             three deep research briefs (agronomy, data sources, ML design)
docs/MODEL_CARD.md    validation, limitations, and what we must not claim
```

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
