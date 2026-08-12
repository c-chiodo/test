import json
import os
from datetime import date

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api.main import app
from soyoil.predict import forecast, model_meta
from soyoil.processing import crush_value
from soyoil.simulate import TARGETS
from soyoil.train import MODEL_DIR

client = TestClient(app)

pytestmark = pytest.mark.skipif(
    not os.path.exists(os.path.join(MODEL_DIR, "meta.json")),
    reason="models not trained (run python -m soyoil.train)",
)


# ---------- trained-model credibility gates ----------

def test_models_beat_baselines():
    meta = model_meta()
    for t, rep in meta["validation"].items():
        assert rep["loyo_rmse"] < rep["baseline_trend_rmse"], (
            f"{t}: does not beat region-mean+trend baseline"
        )
        assert rep["skill_vs_trend"] > 0


def test_conformal_coverage_close_to_nominal():
    meta = model_meta()
    for t, rep in meta["validation"].items():
        assert 0.85 <= rep["coverage_check"] <= 0.95, f"{t}: coverage off nominal"


def test_shap_directions_match_agronomy():
    """Trained model effect directions must agree with the published response
    functions for the strongest, unambiguous drivers."""
    import joblib
    import pandas as pd
    from soyoil.features import FEATURE_NAMES

    df = pd.read_parquet(os.path.join(MODEL_DIR, "training_table.parquet"))
    X = df[FEATURE_NAMES].to_numpy()
    m = joblib.load(os.path.join(MODEL_DIR, "yield_bu_ac.joblib"))

    # Perturb fill water stress upward: mean predicted yield must fall.
    i = FEATURE_NAMES.index("fill_stress_water")
    X2 = X.copy()
    X2[:, i] = np.clip(X2[:, i] + 0.3, 0, 1)
    assert m.predict(X2).mean() < m.predict(X).mean()


# ---------- prediction service ----------

def test_forecast_prediction_shapes_and_intervals():
    fc = forecast("story-ia", 2024, as_of=date(2024, 8, 1), prefer_live=False)
    assert set(fc.predictions) == set(TARGETS)
    for p in fc.predictions.values():
        assert p.lo90 <= p.lo80 <= p.value <= p.hi80 <= p.hi90
    assert 0 < fc.season_coverage < 1
    assert fc.crush["epv_usd_bu"] > 0


def test_intervals_narrow_as_season_progresses():
    early = forecast("story-ia", 2024, as_of=date(2024, 6, 15),
                     with_drivers=False, prefer_live=False)
    late = forecast("story-ia", 2024, as_of=date(2024, 10, 15),
                    with_drivers=False, prefer_live=False)
    w_early = early.predictions["yield_bu_ac"].hi90 - early.predictions["yield_bu_ac"].lo90
    w_late = late.predictions["yield_bu_ac"].hi90 - late.predictions["yield_bu_ac"].lo90
    assert w_late < w_early


def test_hot_dry_scenario_cuts_yield():
    base = forecast("story-ia", 2024, as_of=date(2024, 7, 15),
                    with_drivers=False, prefer_live=False)
    hot = forecast("story-ia", 2024, as_of=date(2024, 7, 15),
                   scenario_temp_c=5.0, scenario_precip_pct=-60.0,
                   with_drivers=False, prefer_live=False)
    assert hot.predictions["yield_bu_ac"].value < base.predictions["yield_bu_ac"].value


def test_forecast_is_deterministic():
    a = forecast("champaign-il", 2023, as_of=date(2023, 8, 1),
                 with_drivers=False, prefer_live=False)
    b = forecast("champaign-il", 2023, as_of=date(2023, 8, 1),
                 with_drivers=False, prefer_live=False)
    assert a.predictions["oil_pct"].value == b.predictions["oil_pct"].value
    assert a.stage_dates == b.stage_dates


# ---------- crush economics ----------

def test_crush_monotonic_in_oil():
    lo = crush_value(oil_pct=18.0, protein_pct=35.0, yield_bu_ac=55.0)
    hi = crush_value(oil_pct=22.0, protein_pct=35.0, yield_bu_ac=55.0)
    assert hi.oil_lb_per_bu > lo.oil_lb_per_bu
    assert hi.oil_value_usd_bu > lo.oil_value_usd_bu


def test_crush_quality_adjustments():
    hi_oleic = crush_value(oil_pct=20, protein_pct=35, yield_bu_ac=55, oleic_pct=75,
                           linolenic_pct=3)
    assert hi_oleic.oil_quality_adj_usd_lb > 0
    hi_lino = crush_value(oil_pct=20, protein_pct=35, yield_bu_ac=55, oleic_pct=22,
                          linolenic_pct=11)
    assert hi_lino.oil_quality_adj_usd_lb < 0


# ---------- API ----------

def test_api_health_and_regions():
    assert client.get("/api/health").json() == {"status": "ok"}
    regions = client.get("/api/regions").json()
    assert len(regions) >= 8
    assert {"region_id", "name", "lat", "lon"} <= set(regions[0])


def test_api_forecast_roundtrip():
    r = client.get("/api/forecast", params={
        "region_id": "story-ia", "year": 2024, "as_of": "2024-08-01"})
    assert r.status_code == 200
    d = r.json()
    assert d["current_stage"]
    assert "yield_bu_ac" in d["predictions"]
    assert d["predictions"]["yield_bu_ac"]["lo90"] < d["predictions"]["yield_bu_ac"]["hi90"]
    assert len(d["drivers"]["yield_bu_ac"]) > 0


def test_api_unknown_region_404():
    assert client.get("/api/forecast",
                      params={"region_id": "nowhere", "year": 2024}).status_code == 404


def test_api_crush_post():
    r = client.post("/api/crush", json={
        "oil_pct": 20.5, "protein_pct": 35.0, "yield_bu_ac": 60.0})
    assert r.status_code == 200
    assert r.json()["epv_usd_bu"] > 0


def test_api_history_comparison():
    r = client.get("/api/history", params={
        "region_id": "story-ia", "year": 2025, "n_years": 5})
    assert r.status_code == 200
    d = r.json()
    assert len(d["years"]) == 5
    assert [y["year"] for y in d["years"]] == list(range(2020, 2025))
    s = d["stats"]["yield_bu_ac"]
    assert s["min"] <= s["mean"] <= s["max"]
    # deterministic across calls (cached retrospectives)
    d2 = client.get("/api/history", params={
        "region_id": "story-ia", "year": 2025, "n_years": 5}).json()
    assert d == d2


def test_api_model_meta_exposes_validation():
    d = client.get("/api/model/meta").json()
    assert "validation" in d and "yield_bu_ac" in d["validation"]
    assert d["training_data"].startswith("synthetic")
