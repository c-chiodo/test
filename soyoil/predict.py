"""In-season prediction service: the engine behind the API.

For a (region, year, planting date, maturity group, as-of date):
  1. Assemble season weather: observed through as-of date (live connectors
     when reachable, deterministic simulation otherwise), remainder filled
     with climate normals ("climatology fill" — the standard in-season method).
  2. Run phenology to date stages, projecting future stages with normals.
  3. Compute stage-window features and predict all targets with conformal
     intervals. Interval width is inflated by remaining-season uncertainty
     (shrinks as the season progresses — matches published skill-vs-date curves).
  4. Explain drivers with SHAP on the LightGBM models.
  5. Scenario mode: user-supplied deltas (°C, % precip) applied to the
     remaining season for what-if analysis.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache

import joblib
import numpy as np
import pandas as pd

from .features import FEATURE_NAMES, FeatureResult, compute_features
from .geography import REGIONS, Region, daily_normal
from .phenology import STAGES, StageTimeline, predict_stages
from .processing import CrushAssumptions, crush_value
from .simulate import TARGETS
from .train import MODEL_DIR
from .weather import add_derived, generate_weather, get_season_weather


@dataclass
class Prediction:
    target: str
    value: float
    lo80: float
    hi80: float
    lo90: float
    hi90: float


@dataclass
class DriverContribution:
    feature: str
    value: float
    shap: float


@dataclass
class SeasonForecast:
    region_id: str
    year: int
    as_of: str
    planting_date: str
    maturity_group: float
    current_stage: str
    season_coverage: float
    weather_source: str
    stage_dates: dict[str, str]
    predictions: dict[str, Prediction]
    oil_yield_lb_ac: float
    drivers: dict[str, list[DriverContribution]] = field(default_factory=dict)
    crush: dict | None = None
    weather_daily: list[dict] = field(default_factory=list)


@lru_cache(maxsize=1)
def _load_models() -> tuple[dict, dict]:
    meta_path = os.path.join(MODEL_DIR, "meta.json")
    if not os.path.exists(meta_path):
        raise RuntimeError(
            "Models not trained. Run `python -m soyoil.train` first.")
    with open(meta_path) as fh:
        meta = json.load(fh)
    models = {t: joblib.load(os.path.join(MODEL_DIR, f"{t}.joblib"))
              for t in meta["targets"]}
    return models, meta


def model_meta() -> dict:
    _, meta = _load_models()
    return meta


def _season_uncertainty_factor(coverage: float) -> float:
    """Interval inflation for unobserved remaining season: 1.0 at harvest,
    ~1.9 at planting. Linear in remaining-season fraction (conservative)."""
    return 1.0 + 0.9 * (1.0 - coverage)


def _assemble_weather(
    region: Region, year: int, as_of: date,
    scenario_temp_c: float = 0.0, scenario_precip_pct: float = 0.0,
    prefer_live: bool = True,
) -> tuple[pd.DataFrame, str]:
    """Observed weather to as_of + normals fill (with scenario deltas) to season end."""
    obs = get_season_weather(region, year, end=min(as_of, date(year, 12, 1)),
                             prefer_live=prefer_live)
    source = obs.attrs.get("source", "unknown")

    rows = []
    d = as_of + timedelta(days=1)
    season_end = date(year, 11, 15)
    while d <= season_end:
        tmax, tmin, pr, sr = daily_normal(region, d)
        tmax += scenario_temp_c
        tmin += scenario_temp_c
        pr *= 1.0 + scenario_precip_pct / 100.0
        rh = 68.0
        et0 = max(0.2, 0.0023 * max(tmax - tmin, 1) ** 0.5
                  * ((tmax + tmin) / 2 + 17.8) * sr / 2.45)
        rows.append((d, tmax, tmin, pr, sr, rh, et0))
        d += timedelta(days=1)
    if rows:
        fut = pd.DataFrame(rows, columns=["date", "tmax_c", "tmin_c", "precip_mm",
                                          "srad_mj_m2", "rh_pct", "et0_mm"])
        fut = add_derived(fut)
        wx = pd.concat([obs, fut], ignore_index=True)
    else:
        wx = obs
    return wx, source


def forecast(
    region_id: str,
    year: int,
    as_of: date | None = None,
    planting: date | None = None,
    maturity_group: float | None = None,
    scenario_temp_c: float = 0.0,
    scenario_precip_pct: float = 0.0,
    with_drivers: bool = True,
    with_weather: bool = False,
    crush_assumptions: CrushAssumptions | None = None,
    prefer_live: bool = True,
) -> SeasonForecast:
    region = REGIONS[region_id]
    models, meta = _load_models()
    as_of = as_of or date(year, 8, 12)
    planting = planting or region.typical_planting(year)
    mg = maturity_group if maturity_group is not None else region.maturity_group

    wx, source = _assemble_weather(region, year, as_of,
                                   scenario_temp_c, scenario_precip_pct,
                                   prefer_live=prefer_live)

    def normals_fn(d: date) -> tuple[float, float]:
        tmax, tmin, _, _ = daily_normal(region, d)
        return (tmax + scenario_temp_c, tmin + scenario_temp_c)

    tl = predict_stages(
        planting, mg, region.lat,
        ((d, tx, tn) for d, tx, tn in zip(wx["date"], wx["tmax_c"], wx["tmin_c"])),
        extend_with_normals=normals_fn,
    )
    fr = compute_features(region, tl, wx, observed_through=as_of)
    x = fr.vector().reshape(1, -1)

    infl = _season_uncertainty_factor(fr.coverage)
    preds: dict[str, Prediction] = {}
    for t in meta["targets"]:
        v = float(models[t].predict(x)[0])
        q80 = meta["conformal"][t]["q80"] * infl
        q90 = meta["conformal"][t]["q90"] * infl
        preds[t] = Prediction(target=t, value=round(v, 2),
                              lo80=round(v - q80, 2), hi80=round(v + q80, 2),
                              lo90=round(v - q90, 2), hi90=round(v + q90, 2))

    drivers: dict[str, list[DriverContribution]] = {}
    if with_drivers:
        for t in ("yield_bu_ac", "oil_pct"):
            booster = models[t]
            contrib = booster.predict(x, pred_contrib=True)[0]  # last item = bias
            pairs = sorted(
                zip(FEATURE_NAMES, contrib[:-1]), key=lambda p: -abs(p[1])
            )[:8]
            drivers[t] = [
                DriverContribution(feature=n, value=round(fr.features[n], 2),
                                   shap=round(float(s), 3))
                for n, s in pairs
            ]

    oil_yield = preds["yield_bu_ac"].value * 60.0 * preds["oil_pct"].value / 100.0
    crush = crush_value(
        oil_pct=preds["oil_pct"].value, protein_pct=preds["protein_pct"].value,
        yield_bu_ac=preds["yield_bu_ac"].value,
        oleic_pct=preds["oleic_pct"].value,
        linolenic_pct=preds["linolenic_pct"].value,
        a=crush_assumptions,
    )

    weather_daily = []
    if with_weather:
        wser = wx[wx["date"] >= planting]
        weather_daily = [
            {"date": r.date.isoformat(), "tmax_c": round(r.tmax_c, 1),
             "tmin_c": round(r.tmin_c, 1), "precip_mm": round(r.precip_mm, 1),
             "observed": r.date <= as_of}
            for r in wser.itertuples()
        ]

    return SeasonForecast(
        region_id=region_id, year=year, as_of=as_of.isoformat(),
        planting_date=planting.isoformat(), maturity_group=round(mg, 1),
        current_stage=tl.current_stage(as_of),
        season_coverage=round(fr.coverage, 3),
        weather_source=source,
        stage_dates={s: d.isoformat() for s, d in tl.stage_dates.items()},
        predictions=preds,
        oil_yield_lb_ac=round(oil_yield, 1),
        drivers=drivers,
        crush=crush.__dict__,
        weather_daily=weather_daily,
    )


@lru_cache(maxsize=256)
def _season_outcome(region_id: str, year: int) -> dict:
    """Retrospective end-of-season outcome for one historical year (full
    observed weather, no climatology fill uncertainty)."""
    fc = forecast(region_id, year, as_of=date(year, 11, 15),
                  with_drivers=False, with_weather=False)
    return {
        "provenance": f"retrospective-model-estimate ({fc.weather_source})",
        "year": year,
        "yield_bu_ac": fc.predictions["yield_bu_ac"].value,
        "oil_pct": fc.predictions["oil_pct"].value,
        "protein_pct": fc.predictions["protein_pct"].value,
        "oil_yield_lb_ac": fc.oil_yield_lb_ac,
        "epv_usd_ac": fc.crush["epv_usd_ac"],
        "fill_start": fc.stage_dates.get("R5"),
        "planting": fc.planting_date,
    }


def history_comparison(
    region_id: str, current_year: int, n_years: int = 10,
) -> dict:
    """Current-season forecast vs. the last `n_years` completed seasons.

    Returns per-year retrospective outcomes, the historical mean/min/max for
    each headline metric, and the current forecast's deviation from the
    historical average — the 'is this year better or worse than normal'
    answer partners ask for first.
    """
    years = list(range(current_year - n_years, current_year))
    rows = [_season_outcome(region_id, y) for y in years]

    metrics = ["yield_bu_ac", "oil_pct", "protein_pct", "oil_yield_lb_ac", "epv_usd_ac"]
    stats = {}
    for m in metrics:
        vals = np.array([r[m] for r in rows], dtype=float)
        stats[m] = {
            "mean": round(float(vals.mean()), 2),
            "min": round(float(vals.min()), 2),
            "max": round(float(vals.max()), 2),
            "std": round(float(vals.std(ddof=1)), 2),
        }
    return {
        "region_id": region_id,
        "provenance": (
            "Historical values are retrospective MODEL estimates driven by the "
            "weather source named per year — they are not USDA/NASS records."
        ),
        "years": rows,
        "stats": stats,
    }


def season_progression(
    region_id: str, year: int, planting: date | None = None,
    maturity_group: float | None = None, step_days: int = 14,
) -> list[dict]:
    """Forecast trajectory across the season — shows skill/interval narrowing
    as observation coverage grows. Used for the dashboard's timeline chart."""
    region = REGIONS[region_id]
    planting = planting or region.typical_planting(year)
    out = []
    d = planting + timedelta(days=21)
    end = date(year, 10, 20)
    while d <= end:
        fc = forecast(region_id, year, as_of=d, planting=planting,
                      maturity_group=maturity_group, with_drivers=False)
        out.append({
            "as_of": d.isoformat(),
            "stage": fc.current_stage,
            "weather_source": fc.weather_source,
            "coverage": fc.season_coverage,
            "yield": fc.predictions["yield_bu_ac"].__dict__,
            "oil": fc.predictions["oil_pct"].__dict__,
            "oil_yield_lb_ac": fc.oil_yield_lb_ac,
        })
        d += timedelta(days=step_days)
    return out
