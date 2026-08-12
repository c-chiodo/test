"""Daily weather: schema, live connectors, and an offline stochastic generator.

Live connectors target the public APIs we recommend in research/data-sources.md
(Open-Meteo ERA5 archive + forecast, NASA POWER). They degrade gracefully:
when the network/egress policy blocks a host, callers fall back to the
climatology-driven stochastic generator so the whole product keeps working
offline. Every DataFrame carries a `source` attribute so the UI can show
data provenance honestly.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .geography import Region, daily_normal

log = logging.getLogger(__name__)

CACHE_DIR = os.environ.get(
    "SOYOIL_CACHE", os.path.join(os.path.dirname(__file__), "data", "cache")
)

DAILY_COLUMNS = ["date", "tmax_c", "tmin_c", "precip_mm", "srad_mj_m2", "rh_pct", "et0_mm"]


def _vpd_kpa(tmax_c: float, tmin_c: float, rh_pct: float) -> float:
    """Daily mean vapor-pressure deficit (kPa), FAO-56 style approximation."""
    def es(t: float) -> float:
        return 0.6108 * math.exp(17.27 * t / (t + 237.3))
    es_mean = (es(tmax_c) + es(tmin_c)) / 2.0
    ea = es_mean * rh_pct / 100.0
    return max(0.0, es_mean - ea)


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["tmean_c"] = (df["tmax_c"] + df["tmin_c"]) / 2.0
    df["vpd_kpa"] = [
        _vpd_kpa(a, b, c) for a, b, c in zip(df["tmax_c"], df["tmin_c"], df["rh_pct"])
    ]
    return df


# ---------------------------------------------------------------------------
# Live connectors (used when egress allows; cached to disk)
# ---------------------------------------------------------------------------

OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
NASA_POWER = "https://power.larc.nasa.gov/api/temporal/daily/point"

_OM_DAILY = ",".join([
    "temperature_2m_max", "temperature_2m_min", "precipitation_sum",
    "shortwave_radiation_sum", "relative_humidity_2m_mean", "et0_fao_evapotranspiration",
])


# Circuit breaker: once a live source fails (e.g. egress policy denial), stop
# hitting it for the rest of the process — fall straight back to simulation.
_source_down: set[str] = set()


def _cache_path(key: dict) -> str:
    h = hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:24]
    return os.path.join(CACHE_DIR, f"wx_{h}.parquet")


def fetch_open_meteo(
    lat: float, lon: float, start: date, end: date, timeout: float = 20.0
) -> pd.DataFrame | None:
    """Fetch daily historical weather from the Open-Meteo ERA5 archive.
    Returns None on any network/policy failure (caller falls back)."""
    key = {"src": "open-meteo", "lat": round(lat, 3), "lon": round(lon, 3),
           "start": start, "end": end}
    path = _cache_path(key)
    if os.path.exists(path):
        return pd.read_parquet(path)
    if "open-meteo" in _source_down:
        return None
    try:
        import httpx

        r = httpx.get(
            OPEN_METEO_ARCHIVE,
            params={
                "latitude": lat, "longitude": lon,
                "start_date": start.isoformat(), "end_date": end.isoformat(),
                "daily": _OM_DAILY, "timezone": "auto",
            },
            timeout=timeout,
        )
        r.raise_for_status()
        d = r.json()["daily"]
        df = pd.DataFrame({
            "date": pd.to_datetime(d["time"]).date,
            "tmax_c": d["temperature_2m_max"],
            "tmin_c": d["temperature_2m_min"],
            "precip_mm": d["precipitation_sum"],
            "srad_mj_m2": [x / 1000.0 * 3.6 if x is not None else None
                           for x in d["shortwave_radiation_sum"]],  # Wh->MJ if needed
            "rh_pct": d["relative_humidity_2m_mean"],
            "et0_mm": d["et0_fao_evapotranspiration"],
        }).dropna()
        df = add_derived(df)
        os.makedirs(CACHE_DIR, exist_ok=True)
        df.to_parquet(path)
        return df
    except Exception as e:  # noqa: BLE001 — any failure means "use fallback"
        log.warning("open-meteo fetch failed (%s); falling back", e)
        _source_down.add("open-meteo")
        return None


def fetch_nasa_power(
    lat: float, lon: float, start: date, end: date, timeout: float = 30.0
) -> pd.DataFrame | None:
    """Fetch daily agroclimatology from NASA POWER. Returns None on failure."""
    key = {"src": "nasa-power", "lat": round(lat, 3), "lon": round(lon, 3),
           "start": start, "end": end}
    path = _cache_path(key)
    if os.path.exists(path):
        return pd.read_parquet(path)
    if "nasa-power" in _source_down:
        return None
    try:
        import httpx

        r = httpx.get(
            NASA_POWER,
            params={
                "parameters": "T2M_MAX,T2M_MIN,PRECTOTCORR,ALLSKY_SFC_SW_DWN,RH2M",
                "community": "AG", "latitude": lat, "longitude": lon,
                "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
                "format": "JSON",
            },
            timeout=timeout,
        )
        r.raise_for_status()
        p = r.json()["properties"]["parameter"]
        dates = sorted(p["T2M_MAX"].keys())
        df = pd.DataFrame({
            "date": [date(int(k[:4]), int(k[4:6]), int(k[6:8])) for k in dates],
            "tmax_c": [p["T2M_MAX"][k] for k in dates],
            "tmin_c": [p["T2M_MIN"][k] for k in dates],
            "precip_mm": [p["PRECTOTCORR"][k] for k in dates],
            "srad_mj_m2": [p["ALLSKY_SFC_SW_DWN"][k] for k in dates],
            "rh_pct": [p["RH2M"][k] for k in dates],
        })
        df = df[(df[["tmax_c", "tmin_c"]] > -900).all(axis=1)]  # POWER fill value -999
        df["et0_mm"] = np.clip(0.0023 * (df.tmax_c - df.tmin_c) ** 0.5
                               * ((df.tmax_c + df.tmin_c) / 2 + 17.8)
                               * df.srad_mj_m2 / 2.45, 0, None)  # Hargreaves
        df = add_derived(df)
        os.makedirs(CACHE_DIR, exist_ok=True)
        df.to_parquet(path)
        return df
    except Exception as e:  # noqa: BLE001
        log.warning("nasa-power fetch failed (%s); falling back", e)
        _source_down.add("nasa-power")
        return None


# ---------------------------------------------------------------------------
# Offline stochastic weather generator (WGEN-style, climatology-anchored)
# ---------------------------------------------------------------------------

@dataclass
class SeasonAnomaly:
    """Season-level anomalies that give each simulated year a coherent
    'personality' (hot/dry year, cool/wet year, ...)."""
    temp_c: float          # season mean temperature shift
    precip_factor: float   # multiplicative precip anomaly
    srad_factor: float
    late_heat_c: float     # extra warming applied Jul-Aug (heat-dome years)
    aug_dry_factor: float  # extra Aug-Sep precip anomaly (seed-fill drought)


def sample_season_anomaly(rng: np.random.Generator) -> SeasonAnomaly:
    # Anomalies are clipped to ~2σ so simulated seasons span dry/hot/cool/wet
    # extremes without generating physically implausible outliers.
    return SeasonAnomaly(
        temp_c=float(np.clip(rng.normal(0.0, 0.9), -1.8, 1.8)),
        precip_factor=float(np.clip(np.exp(rng.normal(0.0, 0.28)), 0.5, 1.9)),
        srad_factor=float(np.clip(rng.normal(1.0, 0.05), 0.85, 1.15)),
        late_heat_c=float(min(2.5, max(0.0, rng.normal(0.0, 1.2)))),
        aug_dry_factor=float(np.clip(np.exp(rng.normal(0.0, 0.35)), 0.45, 2.1)),
    )


def generate_weather(
    region: Region,
    year: int,
    seed: int | None = None,
    anomaly: SeasonAnomaly | None = None,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """Generate one year of daily weather anchored to the region's normals.

    First-order autocorrelated temperature residuals (r=0.65), Markov-chain
    precipitation occurrence with gamma-distributed amounts, radiation coupled
    to wet/dry state. Deterministic under (region, year, seed).
    """
    if seed is None:
        # Stable across processes (built-in hash() is salted per process).
        key = f"{region.region_id}:{year}".encode()
        seed = int.from_bytes(hashlib.sha256(key).digest()[:4], "big")
    rng = np.random.default_rng(seed)
    if anomaly is None:
        anomaly = sample_season_anomaly(rng)
    start = start or date(year, 1, 1)
    end = end or date(year, 12, 31)

    rows = []
    t_resid = 0.0
    wet_yesterday = False
    d = start
    while d <= end:
        n_tmax, n_tmin, n_pr_day, n_srad = daily_normal(region, d)
        doy = d.timetuple().tm_yday

        # Temperature: AR(1) residual, sd ~3.2 °C
        t_resid = 0.65 * t_resid + rng.normal(0.0, 3.2 * math.sqrt(1 - 0.65**2))
        heat_extra = anomaly.late_heat_c if 182 <= doy <= 243 else 0.0
        tmax = n_tmax + anomaly.temp_c + heat_extra + t_resid + rng.normal(0, 1.1)
        tmin = n_tmin + anomaly.temp_c + 0.5 * heat_extra + 0.8 * t_resid + rng.normal(0, 1.0)
        tmin = min(tmin, tmax - 1.5)

        # Precipitation: Markov occurrence (p_wet|dry=.28, p_wet|wet=.48 scaled
        # by month wetness), gamma amounts.
        p_base = min(0.75, n_pr_day / 3.6)
        p_wet = p_base * (1.7 if wet_yesterday else 0.85)
        pr_factor = anomaly.precip_factor * (
            anomaly.aug_dry_factor if 213 <= doy <= 273 else 1.0
        )
        wet = rng.random() < min(0.85, p_wet * pr_factor**0.5)
        if wet:
            mean_amt = max(1.0, n_pr_day / max(p_base, 0.05)) * pr_factor
            precip = float(rng.gamma(0.75, mean_amt / 0.75))
        else:
            precip = 0.0
        wet_yesterday = wet

        # Radiation: reduced on wet days
        srad = n_srad * anomaly.srad_factor * (0.62 if wet else 1.04)
        srad = float(max(1.0, srad + rng.normal(0, 1.2)))

        rh = float(np.clip(68 + (14 if wet else -4) + rng.normal(0, 6)
                           - 2.2 * anomaly.late_heat_c * (182 <= doy <= 243), 25, 100))
        et0 = float(max(0.2, 0.0023 * max(tmax - tmin, 1) ** 0.5
                        * ((tmax + tmin) / 2 + 17.8) * srad / 2.45))
        rows.append((d, tmax, tmin, precip, srad, rh, et0))
        d += timedelta(days=1)

    df = pd.DataFrame(rows, columns=DAILY_COLUMNS)
    df = add_derived(df)
    df.attrs["source"] = "simulated-climatology"
    df.attrs["anomaly"] = anomaly.__dict__
    return df


def get_season_weather(
    region: Region, year: int, end: date | None = None, prefer_live: bool = True
) -> pd.DataFrame:
    """Season weather with provenance: live APIs first, generator fallback."""
    start = date(year, 3, 1)
    stop = end or date(year, 12, 1)
    if prefer_live:
        for fetch, name in ((fetch_open_meteo, "open-meteo-era5"),
                            (fetch_nasa_power, "nasa-power")):
            df = fetch(region.lat, region.lon, start, stop)
            if df is not None and len(df) > 30:
                df.attrs["source"] = name
                return df
    df = generate_weather(region, year, start=start, end=stop)
    return df
