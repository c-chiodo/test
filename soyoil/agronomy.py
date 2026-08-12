"""Published weather → yield / oil / fatty-acid response functions.

These functions encode directions and magnitudes from the peer-reviewed
literature (see research/agronomy-oil-drivers.md for citations). They serve
two purposes:
  1. Generate agronomically-faithful synthetic training data while real
     ground-truth (NASS yields + USB composition surveys) is being ingested.
  2. Sanity-check trained models: SHAP directions must agree with these signs.

Key relationships encoded (all during R5–R6 seed fill unless noted):
  * Oil %: rises with temperature to an optimum near 28 °C mean, then falls
    steeply under heat (≈ -0.4 pt/°C near 32 °C, Dornbos & Mullen 1992);
    drought during fill lowers oil; warm nights (high Tmin / low DTR) depress
    oil (Zhang et al. 2016); inverse oil↔protein relationship (≈ -0.6 pt
    protein per +1 pt oil; we generate protein from oil).
  * Yield: hurt by heat (days >30/35 °C in flowering+fill), drought stress in
    the R1–R6 critical window, helped by August precipitation and radiation.
  * Fatty acids: higher fill temperature → oleic up, linoleic/linolenic down;
    drought during fill lowers oleic and raises stearic (Dornbos & Mullen)
    while also depressing linolenic.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class SeedOutcome:
    yield_bu_ac: float
    oil_pct: float           # % of seed dry weight (typical range 17–23)
    protein_pct: float       # % of seed dry weight (typical 33–37, 13% moisture basis ~34)
    oleic_pct: float         # % of oil
    linoleic_pct: float
    linolenic_pct: float
    palmitic_pct: float
    stearic_pct: float

    @property
    def oil_yield_lb_ac(self) -> float:
        # 60 lb/bu, oil fraction of seed weight
        return self.yield_bu_ac * 60.0 * self.oil_pct / 100.0


def _oil_temperature_response(fill_tmean_c: float) -> float:
    """Deviation in oil percentage points from a 21.0 base as an asymmetric
    quadratic in mean seed-fill temperature. Unimodal with optimum ~28 °C —
    reconciles Piper & Boote 1999 (oil rises with T in field data, which
    samples below the optimum) with Gibson & Mullen / Dornbos & Mullen (oil
    falls under controlled heat above the optimum). The hot limb is steeper
    than the cool limb: -0.055·(T-28)² gives ≈ -0.44 pt/°C at 32 °C,
    matching Dornbos & Mullen's ≈ -0.43 pt/°C."""
    t_opt = 28.0
    dev = fill_tmean_c - t_opt
    coef = 0.055 if dev > 0 else 0.014
    return -coef * dev**2 + 0.75


def expected_outcome(f: dict[str, float], rng: np.random.Generator | None = None,
                     tech_year: int | None = None) -> SeedOutcome:
    """Deterministic expected outcome from a feature dict (FEATURE_NAMES keys).
    Pass `rng` to add residual noise (for synthetic training data); pass
    `tech_year` to add the genetic-gain yield trend (~0.45 bu/ac/yr)."""

    # ---------------- yield (bu/ac) ----------------
    y = 58.0
    # Water: stress indices are mean daily deficit fractions in [0,1]
    y -= 34.0 * f["flower_stress_water"]
    y -= 40.0 * f["fill_stress_water"]
    y -= 9.0 * f["veg_stress_water"]
    # Heat: each day >30 °C in flowering/fill costs; >35 °C costs much more
    y -= 0.22 * f["flower_days_gt30"] + 0.55 * f["flower_days_gt35"]
    y -= 0.28 * f["fill_days_gt30"] + 0.70 * f["fill_days_gt35"]
    # August rain is the classic soybean yield-maker (~+1.1 bu/ac per 25 mm
    # around a 90 mm norm, saturating)
    y += 4.5 * np.tanh((f["aug_precip_mm"] - 90.0) / 80.0)
    # Radiation during fill (per 100 MJ around a 600 MJ norm)
    y += 1.6 * (f["fill_srad_mj"] - 600.0) / 100.0
    # VPD penalty during flowering (atmospheric drought), per kPa over 1.2
    y -= 6.0 * max(0.0, f["flower_vpd_kpa"] - 1.2)
    # Season length / GDD adequacy: penalize short thermal seasons
    y += 3.0 * np.tanh((f["season_gdd"] - 1500.0) / 300.0)
    # Late planting penalty (~0.25 bu/ac/day past DOY 135)
    y -= 0.25 * max(0.0, f["planting_doy"] - 135.0)
    # Cool-climate high-latitude penalty beyond MG adaptation
    y -= 0.8 * max(0.0, f["lat"] - 44.0)
    if tech_year is not None:
        y += 0.45 * (tech_year - 2010)
    y = max(8.0, y)

    # ---------------- oil % ----------------
    oil = 21.0
    oil += _oil_temperature_response(f["fill_tmean_c"])
    # Heat stress days during fill beyond the dome (extreme days hurt oil)
    oil -= 0.030 * f["fill_days_gt35"]
    # Warm nights (high Tmin / low DTR) during fill depress oil
    # (Zhang et al. 2016: Tmin negative, DTR positive for oil)
    oil -= 0.012 * min(f["fill_night_warm"], 25.0)
    # Drought during seed fill reduces oil
    oil -= 2.2 * f["fill_stress_water"]
    # Radiation during fill raises oil (assimilate supply), per 100 MJ
    oil += 0.15 * (f["fill_srad_mj"] - 600.0) / 100.0
    # Latitude gradient: northern seed runs lower oil / higher protein? In US
    # surveys northern states run LOWER protein and slightly HIGHER oil is
    # not consistent; the robust signal is temperature, already captured.
    # Small residual latitude term for survey realism:
    oil -= 0.06 * max(0.0, f["lat"] - 42.0)
    oil = float(np.clip(oil, 16.5, 24.5))

    # ---------------- protein % (inverse to oil) ----------------
    protein = 34.5 - 0.60 * (oil - 21.0)
    protein += 1.2 * f["fill_stress_water"]  # drought raises protein
    protein = float(np.clip(protein, 30.0, 39.5))

    # ---------------- fatty acid profile (% of oil) ----------------
    t_dev = f["fill_tmean_c"] - 23.0
    drought = f["fill_stress_water"]
    # Drought lowers oleic and raises stearic (Dornbos & Mullen 1992);
    # temperature raises oleic and lowers the polyunsaturates.
    oleic = 23.0 + 1.1 * t_dev - 3.0 * drought
    linolenic = 8.0 - 0.35 * t_dev - 1.0 * drought
    linoleic = 54.0 - 0.75 * t_dev + 1.5 * drought
    palmitic = 11.0 - 0.05 * t_dev
    stearic = 4.0 + 0.05 * t_dev + 1.0 * drought

    if rng is not None:
        y = max(5.0, y + rng.normal(0, 4.5))          # residual yield noise
        oil = float(np.clip(oil + rng.normal(0, 0.45), 16.0, 25.0))
        protein = float(np.clip(protein + rng.normal(0, 0.6), 29.0, 40.0))
        oleic = max(12.0, oleic + rng.normal(0, 1.2))
        linoleic = max(40.0, linoleic + rng.normal(0, 1.2))
        linolenic = max(2.0, linolenic + rng.normal(0, 0.5))

    # Normalize the profile to 100% AFTER noise so no component can drift
    # to a physically impossible share.
    total = oleic + linoleic + linolenic + palmitic + stearic
    oleic, linoleic, linolenic, palmitic, stearic = (
        100.0 * x / total for x in (oleic, linoleic, linolenic, palmitic, stearic)
    )

    return SeedOutcome(
        yield_bu_ac=float(y), oil_pct=float(oil), protein_pct=float(protein),
        oleic_pct=float(oleic), linoleic_pct=float(linoleic),
        linolenic_pct=float(linolenic), palmitic_pct=float(palmitic),
        stearic_pct=float(stearic),
    )


# Expected SHAP/effect directions used by tests to sanity-check trained models.
EXPECTED_SIGNS = {
    "yield_bu_ac": {"fill_stress_water": -1, "flower_stress_water": -1,
                    "fill_days_gt35": -1, "aug_precip_mm": +1, "fill_srad_mj": +1},
    "oil_pct": {"fill_stress_water": -1, "fill_srad_mj": +1},
    "protein_pct": {"fill_stress_water": +1},
    "oleic_pct": {"fill_tmean_c": +1, "fill_stress_water": -1},
    "linolenic_pct": {"fill_tmean_c": -1},
}
