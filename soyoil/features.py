"""Stage-window feature engineering.

Turns daily weather + a phenology timeline + soil into the model feature
vector. Windows follow the agronomy brief: the oil-critical window is R5–R6
(seed fill); yield is set mostly R1–R6; early-season features capture stand
establishment. Aggregations are the accepted crop-ML set: means, sums,
stress-day counts, degree-day bins, a simple bucket water balance, and the
photothermal quotient.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from .geography import Region
from .phenology import StageTimeline

# The canonical feature order used by every model. Keep stable — models are
# trained against this exact list.
FEATURE_NAMES: list[str] = [
    # site
    "lat", "maturity_group", "soil_awc_mm", "som_pct", "planting_doy",
    # vegetative window (VE..R1)
    "veg_gdd", "veg_precip_mm", "veg_tmean_c", "veg_stress_water",
    # flowering / pod set (R1..R5)
    "flower_tmean_c", "flower_tmax_mean_c", "flower_precip_mm",
    "flower_srad_mj", "flower_vpd_kpa", "flower_days_gt30", "flower_days_gt35",
    "flower_stress_water", "flower_ptq",
    # seed fill (R5..R6) — the oil window
    "fill_tmean_c", "fill_tmin_mean_c", "fill_tmax_mean_c", "fill_night_warm",
    "fill_precip_mm", "fill_srad_mj", "fill_vpd_kpa",
    "fill_days_gt30", "fill_days_gt35", "fill_stress_water", "fill_ptq",
    "fill_gdd", "fill_length_days",
    # late (R6..R7)
    "late_tmean_c", "late_precip_mm", "late_stress_water",
    # whole season
    "season_precip_mm", "season_gdd", "aug_precip_mm",
]


@dataclass
class FeatureResult:
    features: dict[str, float]
    windows: dict[str, tuple[date, date]]
    coverage: float  # fraction of season windows covered by observed weather

    def vector(self) -> np.ndarray:
        return np.array([self.features[n] for n in FEATURE_NAMES], dtype=float)


def _win(df: pd.DataFrame, a: date, b: date) -> pd.DataFrame:
    m = (df["date"] >= a) & (df["date"] <= b)
    return df.loc[m]


def _water_stress_series(
    df: pd.DataFrame, awc_mm: float, start: date, start_frac: float = 0.85
) -> pd.Series:
    """Season-long bucket water balance starting at `start` (planting).

    Returns a daily stress series in [0,1] (0 = no stress). The bucket runs
    CONTINUOUSLY across the season — depletion carries over between stage
    windows, which is what makes late-season drought bite. Crop water use is
    approximated as Kc·ET0; ~35% of rainfall is lost to runoff/deep
    percolation before it reaches the root-zone bucket (heavy events don't
    all infiltrate). Stress ramps up once the bucket drops below 60% of AWC
    (onset of stomatal closure under rising demand).
    """
    season = df[df["date"] >= start]
    w = awc_mm * start_frac
    out: list[float] = []
    kc = 1.10  # season-average crop coefficient for soybean canopy
    infil = 0.65
    for pr, et0 in zip(season["precip_mm"], season["et0_mm"]):
        demand = kc * et0
        w = min(awc_mm, w + infil * pr) - min(demand, w)
        w = max(0.0, w)
        frac = w / awc_mm
        out.append(max(0.0, (0.60 - frac) / 0.60))
    return pd.Series(out, index=season["date"].to_numpy())


def _window_stress(stress: pd.Series, a: date, b: date) -> float:
    s = stress[(stress.index >= a) & (stress.index <= b)]
    return float(s.mean()) if len(s) else 0.0


def _gdd(df: pd.DataFrame) -> float:
    tmax = np.minimum(df["tmax_c"], 30.0)
    tmin = np.clip(df["tmin_c"], 10.0, None)
    tmin = np.minimum(tmin, tmax)
    return float(np.maximum((tmax + tmin) / 2.0 - 10.0, 0.0).sum())


def _ptq(df: pd.DataFrame) -> float:
    """Photothermal quotient: MJ m-2 d-1 per GDD-degree — radiation per unit
    development. Higher = more assimilate per stage of development."""
    if len(df) == 0:
        return 0.0
    gdd = _gdd(df) / len(df)
    return float(df["srad_mj_m2"].mean() / max(gdd, 0.5))


def compute_features(
    region: Region,
    timeline: StageTimeline,
    weather: pd.DataFrame,
    observed_through: date | None = None,
) -> FeatureResult:
    """Build the model feature vector. `observed_through` marks the boundary
    between observed weather and climatology-filled weather (for the coverage
    metric shown to users in-season)."""
    sd = timeline.stage_dates
    planting = timeline.planting
    # Window boundaries; fall back to sensible offsets if a stage never hit.
    r1 = sd.get("R1", planting + pd.Timedelta(days=55).to_pytimedelta())
    r5 = sd.get("R5", planting + pd.Timedelta(days=90).to_pytimedelta())
    r6 = sd.get("R6", planting + pd.Timedelta(days=118).to_pytimedelta())
    r7 = sd.get("R7", planting + pd.Timedelta(days=135).to_pytimedelta())
    ve = sd.get("VE", planting + pd.Timedelta(days=8).to_pytimedelta())

    windows = {
        "vegetative": (ve, r1), "flowering": (r1, r5),
        "seed_fill": (r5, r6), "late": (r6, r7),
        "season": (planting, r7),
    }

    veg = _win(weather, ve, r1)
    flo = _win(weather, r1, r5)
    fil = _win(weather, r5, r6)
    lat_w = _win(weather, r6, r7)
    season = _win(weather, planting, r7)
    year = planting.year
    aug = _win(weather, date(year, 8, 1), date(year, 8, 31))
    stress = _water_stress_series(weather, region.soil_awc_mm, planting)

    f: dict[str, float] = {
        "lat": region.lat,
        "maturity_group": timeline.maturity_group,
        "soil_awc_mm": region.soil_awc_mm,
        "som_pct": region.som_pct,
        "planting_doy": float(planting.timetuple().tm_yday),

        "veg_gdd": _gdd(veg),
        "veg_precip_mm": float(veg["precip_mm"].sum()),
        "veg_tmean_c": float(veg["tmean_c"].mean()) if len(veg) else 0.0,
        "veg_stress_water": _window_stress(stress, ve, r1),

        "flower_tmean_c": float(flo["tmean_c"].mean()) if len(flo) else 0.0,
        "flower_tmax_mean_c": float(flo["tmax_c"].mean()) if len(flo) else 0.0,
        "flower_precip_mm": float(flo["precip_mm"].sum()),
        "flower_srad_mj": float(flo["srad_mj_m2"].sum()),
        "flower_vpd_kpa": float(flo["vpd_kpa"].mean()) if len(flo) else 0.0,
        "flower_days_gt30": float((flo["tmax_c"] > 30.0).sum()),
        "flower_days_gt35": float((flo["tmax_c"] > 35.0).sum()),
        "flower_stress_water": _window_stress(stress, r1, r5),
        "flower_ptq": _ptq(flo),

        "fill_tmean_c": float(fil["tmean_c"].mean()) if len(fil) else 0.0,
        "fill_tmin_mean_c": float(fil["tmin_c"].mean()) if len(fil) else 0.0,
        "fill_tmax_mean_c": float(fil["tmax_c"].mean()) if len(fil) else 0.0,
        "fill_night_warm": float((fil["tmin_c"] > 20.0).sum()),
        "fill_precip_mm": float(fil["precip_mm"].sum()),
        "fill_srad_mj": float(fil["srad_mj_m2"].sum()),
        "fill_vpd_kpa": float(fil["vpd_kpa"].mean()) if len(fil) else 0.0,
        "fill_days_gt30": float((fil["tmax_c"] > 30.0).sum()),
        "fill_days_gt35": float((fil["tmax_c"] > 35.0).sum()),
        "fill_stress_water": _window_stress(stress, r5, r6),
        "fill_ptq": _ptq(fil),
        "fill_gdd": _gdd(fil),
        "fill_length_days": float(len(fil)),

        "late_tmean_c": float(lat_w["tmean_c"].mean()) if len(lat_w) else 0.0,
        "late_precip_mm": float(lat_w["precip_mm"].sum()),
        "late_stress_water": _window_stress(stress, r6, r7),

        "season_precip_mm": float(season["precip_mm"].sum()),
        "season_gdd": _gdd(season),
        "aug_precip_mm": float(aug["precip_mm"].sum()),
    }

    if observed_through is None:
        coverage = 1.0
    else:
        total = max((r7 - planting).days, 1)
        obs = min(max((observed_through - planting).days, 0), total)
        coverage = obs / total

    return FeatureResult(features=f, windows=windows, coverage=coverage)
