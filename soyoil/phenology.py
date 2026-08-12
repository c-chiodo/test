"""Soybean phenology: growth-stage prediction from daily weather.

Model: thermal-time (growing degree days, base 10 °C — the standard soybean
base temperature) modified by photoperiod after emergence, following the
approach used in SoySim / Setiyono et al. (2007) in simplified form.

Stages follow the Fehr & Caviness (1977) scale:
    VE  emergence
    V1..Vn vegetative nodes
    R1  beginning bloom          R2  full bloom
    R3  beginning pod            R4  full pod
    R5  beginning seed           R6  full seed
    R7  beginning maturity       R8  full maturity

Cumulative GDD (°C·d, base 10, capped at 30) targets per stage are
parameterized by maturity group (MG); values are calibrated to the ranges
published by Iowa State / Purdue extension and Setiyono et al. (2007).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable

# Base / ceiling temperatures for soybean thermal time (°C).
T_BASE = 10.0
T_CEIL = 30.0

# Ordered reproductive + key vegetative stages we track.
STAGES = ["VE", "V2", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"]

# Cumulative GDD targets (base 10 °C) from planting for a reference MG 3.0
# cultivar, midpoints of published extension ranges.
_GDD_TARGETS_MG3 = {
    "VE": 90.0,     # emergence ~ 90-130 GDD10 after planting
    "V2": 190.0,
    "R1": 500.0,    # beginning bloom (~late June for early-May planting)
    "R2": 580.0,
    "R3": 680.0,    # beginning pod
    "R4": 790.0,
    "R5": 900.0,    # beginning seed — start of the oil-critical window
                    # (DAP ~75-90 for MG 2.6 @ 42°N, May-10 planting)
    "R6": 1150.0,   # full seed — end of most oil deposition
    "R7": 1310.0,   # physiological maturity (~mid-late September)
}

# R7 -> R8 is drydown, driven by seed moisture loss rather than thermal time;
# stamp it a fixed interval after R7.
DRYDOWN_DAYS = 12

# Later maturity groups need more thermal time, scaled proportionally across
# stages. 10%/MG reproduces the observed maturity spread between MG 0
# (Red River Valley) and MG 3-4 (central Corn Belt) cultivars.
_MG_SCALE_PER_GROUP = 0.10

# Photoperiod sensitivity: soybean is a short-day plant. Days longer than the
# critical photoperiod slow reproductive development. P_crit decreases with MG.


def critical_photoperiod(maturity_group: float) -> float:
    """Critical photoperiod (h) above which development slows (Setiyono 2007 form)."""
    return 14.35 - 0.32 * (maturity_group - 3.0)


def daylength_hours(lat_deg: float, doy: int) -> float:
    """Civil daylength (h) including civil twilight (sun 6° below horizon),
    which is the convention for soybean photoperiod response."""
    phi = math.radians(lat_deg)
    decl = math.radians(23.45) * math.sin(2.0 * math.pi * (284 + doy) / 365.0)
    # -6 degrees for civil twilight
    cos_h = (math.sin(math.radians(-6.0)) - math.sin(phi) * math.sin(decl)) / (
        math.cos(phi) * math.cos(decl)
    )
    cos_h = min(1.0, max(-1.0, cos_h))
    return 2.0 * math.degrees(math.acos(cos_h)) / 15.0


def gdd_day(tmax_c: float, tmin_c: float) -> float:
    """Single-day GDD, base 10 °C with a 30 °C cap on Tmax (standard method)."""
    tmax = min(tmax_c, T_CEIL)
    tmin = max(tmin_c, T_BASE)
    if tmax < T_BASE:
        return 0.0
    tmin = min(tmin, tmax)
    return max(0.0, (tmax + tmin) / 2.0 - T_BASE)


def gdd_targets(maturity_group: float) -> dict[str, float]:
    scale = 1.0 + _MG_SCALE_PER_GROUP * (maturity_group - 3.0)
    return {s: g * scale for s, g in _GDD_TARGETS_MG3.items()}


_GDD_STAGES = [s for s in STAGES if s != "R8"]


@dataclass
class StageTimeline:
    """Predicted (or partially observed) stage dates for one field-season."""

    planting: date
    maturity_group: float
    stage_dates: dict[str, date] = field(default_factory=dict)
    gdd_cum: float = 0.0
    complete: bool = False  # True once R8 reached

    def window(self, start_stage: str, end_stage: str) -> tuple[date, date] | None:
        a = self.stage_dates.get(start_stage)
        b = self.stage_dates.get(end_stage)
        if a is None or b is None:
            return None
        return (a, b)

    def current_stage(self, on: date) -> str:
        cur = "planted"
        for s in STAGES:
            d = self.stage_dates.get(s)
            if d is not None and d <= on:
                cur = s
        return cur


def predict_stages(
    planting: date,
    maturity_group: float,
    lat_deg: float,
    daily: Iterable[tuple[date, float, float]],
    extend_with_normals: "callable | None" = None,
) -> StageTimeline:
    """Walk daily weather forward from planting, accumulating photoperiod-
    adjusted GDD and stamping stage dates as targets are crossed.

    daily: iterable of (date, tmax_c, tmin_c), sorted, starting at/before planting.
    extend_with_normals: optional fn(date) -> (tmax_c, tmin_c) used to keep
        stepping past the end of observed weather (e.g. with climate normals)
        so the remaining-season stages still get projected dates.
    """
    targets = gdd_targets(maturity_group)
    p_crit = critical_photoperiod(maturity_group)
    tl = StageTimeline(planting=planting, maturity_group=maturity_group)
    remaining = [s for s in _GDD_STAGES]

    def step(d: date, tmax: float, tmin: float) -> None:
        g = gdd_day(tmax, tmin)
        dl = daylength_hours(lat_deg, d.timetuple().tm_yday)
        if "VE" in tl.stage_dates:
            if "R5" not in tl.stage_dates and dl > p_crit:
                # Long days slow progression toward flowering (soybean is a
                # short-day plant): linear penalty above P_crit with a floor.
                g *= max(0.75, 1.0 - 0.05 * (dl - p_crit))
            elif "R5" in tl.stage_dates and dl < p_crit:
                # Shortening fall days accelerate late reproductive
                # development (SoySim models the same effect).
                g *= min(1.4, 1.0 + 0.10 * (p_crit - dl))
        tl.gdd_cum += g
        while remaining and tl.gdd_cum >= targets[remaining[0]]:
            tl.stage_dates[remaining[0]] = d
            remaining.pop(0)
        # Killing frost after flowering terminates the season: everything
        # still pending collapses to maturity at the frost date.
        if tmin <= -2.0 and "R1" in tl.stage_dates and "R7" not in tl.stage_dates:
            for s in list(remaining):
                tl.stage_dates[s] = d
            remaining.clear()
        if "R7" in tl.stage_dates and "R8" not in tl.stage_dates:
            tl.stage_dates["R8"] = tl.stage_dates["R7"] + timedelta(days=DRYDOWN_DAYS)

    last_day = planting - timedelta(days=1)
    for d, tmax, tmin in daily:
        if d < planting:
            continue
        step(d, tmax, tmin)
        last_day = d
        if not remaining:
            break

    # Project the rest of the season with normals if provided.
    if remaining and extend_with_normals is not None:
        d = last_day
        for _ in range(320):  # hard stop: no season runs a year
            d = d + timedelta(days=1)
            tmax, tmin = extend_with_normals(d)
            step(d, tmax, tmin)
            if not remaining:
                break

    tl.complete = "R8" in tl.stage_dates
    return tl
