"""Synthetic training-data generation.

Builds a multi-region, multi-year training table by (1) sampling coherent
season weather anomalies, (2) generating daily weather, (3) running the
phenology model, (4) computing stage-window features, and (5) producing
targets from the published response functions in soyoil.agronomy with
realistic residual noise.

IMPORTANT HONESTY NOTE: models trained on this table learn the *published
literature's* response structure, not local ground truth. The pipeline is
designed so the same code retrains on NASS county yields + USB composition
surveys the moment those connectors run with network access. See
docs/MODEL_CARD.md for the customer-facing framing.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from .agronomy import expected_outcome
from .features import FEATURE_NAMES, compute_features
from .geography import REGIONS, Region, daily_normal
from .phenology import predict_stages
from .weather import generate_weather, sample_season_anomaly

TARGETS = ["yield_bu_ac", "oil_pct", "protein_pct",
           "oleic_pct", "linoleic_pct", "linolenic_pct"]


def simulate_field_season(
    region: Region, year: int, rng: np.random.Generator
) -> dict[str, float] | None:
    """One synthetic field-season: returns feature dict + targets + metadata."""
    anomaly = sample_season_anomaly(rng)
    wx = generate_weather(region, year, seed=int(rng.integers(0, 2**31)),
                          anomaly=anomaly)

    # Planting date: regional norm +/- logistics noise, later in wet springs
    plant_shift = rng.normal(0, 6) + 4.0 * max(0.0, anomaly.precip_factor - 1.15)
    planting = date(year, 1, 1) + timedelta(
        days=region.typical_planting_doy - 1 + int(round(plant_shift)))
    mg = region.maturity_group + float(rng.normal(0, 0.25))

    def normals_fn(d: date) -> tuple[float, float]:
        tmax, tmin, _, _ = daily_normal(region, d)
        return (tmax, tmin)

    tl = predict_stages(
        planting, mg, region.lat,
        ((d, tx, tn) for d, tx, tn in zip(wx["date"], wx["tmax_c"], wx["tmin_c"])),
        extend_with_normals=normals_fn,
    )
    if "R7" not in tl.stage_dates:
        return None  # season didn't finish (extreme cold sim) — drop

    fr = compute_features(region, tl, wx)
    out = expected_outcome(fr.features, rng=rng, tech_year=year)

    row: dict[str, float] = dict(fr.features)
    row.update({
        "region_id": region.region_id, "year": year,
        "planting_date": planting.isoformat(),
        "yield_bu_ac": out.yield_bu_ac, "oil_pct": out.oil_pct,
        "protein_pct": out.protein_pct, "oleic_pct": out.oleic_pct,
        "linoleic_pct": out.linoleic_pct, "linolenic_pct": out.linolenic_pct,
        "oil_yield_lb_ac": out.oil_yield_lb_ac,
    })
    return row


def build_training_table(
    years: range = range(2004, 2026),
    fields_per_region_year: int = 6,
    seed: int = 7,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for region in REGIONS.values():
        for year in years:
            for _ in range(fields_per_region_year):
                row = simulate_field_season(region, year, rng)
                if row is not None:
                    rows.append(row)
    df = pd.DataFrame(rows)
    # Ensure column order: features, then targets, then meta
    cols = FEATURE_NAMES + TARGETS + ["oil_yield_lb_ac", "region_id", "year", "planting_date"]
    return df[cols]
