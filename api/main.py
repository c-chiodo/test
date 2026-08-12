"""OleoCast API — FastAPI backend for the soybean yield & oil platform.

Run:  uvicorn api.main:app --reload
Docs: /docs (OpenAPI)
"""

from __future__ import annotations

import json
import os
from datetime import date

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from soyoil.geography import REGIONS
from soyoil.predict import (
    forecast,
    history_comparison,
    model_meta,
    season_progression,
)
from soyoil.processing import CrushAssumptions, crush_value

app = FastAPI(
    title="OleoCast API",
    version="0.1.0",
    description=(
        "Weather-driven prediction of soybean phenology, yield, seed oil "
        "concentration, fatty-acid profile, and crush value. "
        "See /model/meta for validation metrics and training-data provenance."
    ),
)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/regions")
def regions() -> list[dict]:
    return [
        {
            "region_id": r.region_id, "name": r.name, "state": r.state,
            "fips": r.fips, "lat": r.lat, "lon": r.lon,
            "maturity_group": r.maturity_group,
            "typical_planting_doy": r.typical_planting_doy,
            "soil_awc_mm": r.soil_awc_mm,
        }
        for r in REGIONS.values()
    ]


@app.get("/api/model/meta")
def get_model_meta() -> dict:
    """Model metadata: features, LOYO validation metrics per target,
    conformal interval widths, and training-data provenance."""
    return model_meta()


@app.get("/api/forecast")
def get_forecast(
    region_id: str,
    year: int = Query(ge=1990, le=2035),
    as_of: date | None = None,
    planting: date | None = None,
    maturity_group: float | None = Query(default=None, ge=0.0, le=6.0),
    scenario_temp_c: float = Query(default=0.0, ge=-5.0, le=6.0),
    scenario_precip_pct: float = Query(default=0.0, ge=-70.0, le=70.0),
    include_weather: bool = False,
) -> dict:
    """In-season (or retrospective) forecast for one region-season, with
    80/90% conformal intervals, SHAP driver attributions, projected stage
    dates, and crush value."""
    if region_id not in REGIONS:
        raise HTTPException(404, f"unknown region_id {region_id!r}")
    fc = forecast(
        region_id, year, as_of=as_of, planting=planting,
        maturity_group=maturity_group,
        scenario_temp_c=scenario_temp_c, scenario_precip_pct=scenario_precip_pct,
        with_weather=include_weather,
    )
    out = fc.__dict__.copy()
    out["predictions"] = {k: v.__dict__ for k, v in fc.predictions.items()}
    out["drivers"] = {k: [c.__dict__ for c in v] for k, v in fc.drivers.items()}
    return out


@app.get("/api/progression")
def get_progression(
    region_id: str,
    year: int = Query(ge=1990, le=2035),
    step_days: int = Query(default=14, ge=5, le=30),
) -> list[dict]:
    """Forecast trajectory across a season: how the prediction and its
    intervals evolve as observed weather replaces climatology."""
    if region_id not in REGIONS:
        raise HTTPException(404, f"unknown region_id {region_id!r}")
    return season_progression(region_id, year, step_days=step_days)


@app.get("/api/history")
def get_history(
    region_id: str,
    year: int = Query(ge=1990, le=2035),
    n_years: int = Query(default=10, ge=3, le=20),
) -> dict:
    """Historical context: retrospective outcomes for the previous seasons
    plus mean/min/max/std per metric, for comparing the current forecast
    against 'a normal year' in this region."""
    if region_id not in REGIONS:
        raise HTTPException(404, f"unknown region_id {region_id!r}")
    return history_comparison(region_id, year, n_years=n_years)


class CrushRequest(BaseModel):
    oil_pct: float = Field(ge=14, le=28)
    protein_pct: float = Field(ge=28, le=42)
    yield_bu_ac: float = Field(ge=5, le=120)
    oleic_pct: float = 23.0
    linolenic_pct: float = 8.0
    oil_price_usd_lb: float = 0.47
    meal_price_usd_ton: float = 340.0
    bean_price_usd_bu: float = 10.60


@app.post("/api/crush")
def post_crush(req: CrushRequest) -> dict:
    """Crush economics for a given composition and price deck."""
    res = crush_value(
        oil_pct=req.oil_pct, protein_pct=req.protein_pct,
        yield_bu_ac=req.yield_bu_ac, oleic_pct=req.oleic_pct,
        linolenic_pct=req.linolenic_pct,
        a=CrushAssumptions(
            oil_price_usd_lb=req.oil_price_usd_lb,
            meal_price_usd_ton=req.meal_price_usd_ton,
            bean_price_usd_bu=req.bean_price_usd_bu,
        ),
    )
    return res.__dict__


@app.get("/api/datasources")
def datasources() -> list[dict]:
    """Provenance registry: the public sources this product is built to
    ingest, and each one's licensing posture. Mirrors research/data-sources.md."""
    return [
        {"name": "Open-Meteo ERA5 archive", "kind": "weather-historical",
         "auth": "none", "license": "free tier is NON-COMMERCIAL; commercial tier or self-hosting required in production",
         "status": "connector-ready"},
        {"name": "NASA POWER", "kind": "weather-historical", "auth": "none",
         "license": "public domain", "status": "connector-ready"},
        {"name": "USDA NASS Quick Stats", "kind": "yield-ground-truth",
         "auth": "free API key", "license": "public domain",
         "status": "connector-planned"},
        {"name": "US Soybean Quality Annual Report (USB)", "kind": "composition-ground-truth",
         "auth": "none (PDF ETL)", "license": "public report; state/region level",
         "status": "etl-planned"},
        {"name": "USDA SSURGO / Soil Data Access", "kind": "soil", "auth": "none",
         "license": "public domain", "status": "connector-planned"},
        {"name": "ORNL MODIS/VIIRS subsets", "kind": "remote-sensing", "auth": "none",
         "license": "public", "status": "connector-planned"},
        {"name": "Simulated climatology generator", "kind": "fallback",
         "auth": "n/a", "license": "internal",
         "status": "active (used when live sources unreachable)"},
    ]


# Serve the built web app when present (production layout).
_static = os.path.join(os.path.dirname(__file__), "..", "web", "dist")
if os.path.isdir(_static):
    app.mount("/", StaticFiles(directory=_static, html=True), name="web")
