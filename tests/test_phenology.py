from datetime import date

from soyoil.geography import REGIONS, daily_normal
from soyoil.phenology import (
    STAGES,
    critical_photoperiod,
    daylength_hours,
    gdd_day,
    gdd_targets,
    predict_stages,
)
from soyoil.weather import generate_weather


def _timeline(region_id: str, year: int, seed: int = 1):
    r = REGIONS[region_id]
    wx = generate_weather(r, year, seed=seed)

    def nf(d, r=r):
        tmax, tmin, _, _ = daily_normal(r, d)
        return tmax, tmin

    return predict_stages(
        r.typical_planting(year), r.maturity_group, r.lat,
        ((d, tx, tn) for d, tx, tn in zip(wx["date"], wx["tmax_c"], wx["tmin_c"])),
        extend_with_normals=nf,
    ), r


def test_gdd_day_basics():
    assert gdd_day(20.0, 10.0) == 5.0
    assert gdd_day(35.0, 25.0) == 17.5      # tmax capped at 30
    assert gdd_day(8.0, 2.0) == 0.0         # below base
    assert gdd_day(12.0, -5.0) == 1.0       # tmin floored at base


def test_gdd_targets_scale_with_maturity_group():
    early = gdd_targets(1.0)
    late = gdd_targets(4.0)
    assert early["R7"] < late["R7"]
    assert early["R1"] < late["R1"]


def test_daylength_reasonable():
    # Ames, IA midsummer civil daylength ~16h; midwinter ~10h
    assert 15.0 < daylength_hours(42.0, 172) < 17.5
    assert 9.0 < daylength_hours(42.0, 355) < 11.0
    assert critical_photoperiod(0.0) > critical_photoperiod(4.0)


def test_stage_order_and_within_season():
    for rid in REGIONS:
        tl, r = _timeline(rid, 2024)
        sd = tl.stage_dates
        # all core stages present
        for s in STAGES:
            assert s in sd, f"{rid}: missing {s}"
        # strictly ordered
        seq = [sd[s] for s in STAGES]
        assert seq == sorted(seq), f"{rid}: stages out of order"
        # season-bounded: maturity in the planting year
        assert sd["R7"].year == 2024, f"{rid}: R7 leaked into {sd['R7'].year}"
        assert sd["R1"].month in (6, 7), f"{rid}: R1 at {sd['R1']}"
        assert sd["R5"].month in (7, 8, 9), f"{rid}: R5 at {sd['R5']}"


def test_current_stage_progression():
    tl, _ = _timeline("story-ia", 2024)
    assert tl.current_stage(tl.planting) == "planted"
    assert tl.current_stage(date(2024, 12, 1)) == "R8"


def test_r5_timing_matches_extension_windows():
    """QA-audit calibration gate: R5 for MG 2.6 @ 42°N, ~May-10 planting must
    land 75-95 days after planting (extension/SoyStage range) in a normals-like
    year — the demo must not run weeks late."""
    from statistics import mean

    daps = []
    for seed in range(6):
        tl, r = _timeline("story-ia", 2024, seed=seed)
        daps.append((tl.stage_dates["R5"] - tl.planting).days)
    assert 72 <= mean(daps) <= 95, f"mean R5 DAP {mean(daps)} outside 72-95 (per-seed {daps})"


def test_frost_termination_bounds_cool_years():
    # Even a strongly cool year must not stall past the calendar year.
    for seed in range(5):
        tl, _ = _timeline("cass-nd", 2024, seed=seed)
        assert "R7" in tl.stage_dates
        assert tl.stage_dates["R7"].year == 2024
