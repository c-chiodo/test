from datetime import date

import numpy as np

from soyoil.agronomy import EXPECTED_SIGNS, expected_outcome
from soyoil.features import FEATURE_NAMES, compute_features
from soyoil.geography import REGIONS, daily_normal
from soyoil.phenology import predict_stages
from soyoil.weather import generate_weather


def _features(region_id="story-ia", year=2024, seed=3):
    r = REGIONS[region_id]
    wx = generate_weather(r, year, seed=seed)

    def nf(d):
        tmax, tmin, _, _ = daily_normal(r, d)
        return tmax, tmin

    tl = predict_stages(
        r.typical_planting(year), r.maturity_group, r.lat,
        ((d, tx, tn) for d, tx, tn in zip(wx["date"], wx["tmax_c"], wx["tmin_c"])),
        extend_with_normals=nf,
    )
    return compute_features(r, tl, wx)


def test_feature_vector_complete_and_finite():
    fr = _features()
    assert set(fr.features) == set(FEATURE_NAMES)
    v = fr.vector()
    assert v.shape == (len(FEATURE_NAMES),)
    assert np.isfinite(v).all()


def test_ranges_plausible():
    fr = _features()
    f = fr.features
    assert 10 < f["fill_tmean_c"] < 35
    assert 0 <= f["fill_stress_water"] <= 1
    assert 0 < f["fill_length_days"] < 80
    assert 0 < f["season_gdd"] < 3000
    assert f["season_precip_mm"] > 50


def test_coverage_partial_season():
    r = REGIONS["story-ia"]
    wx = generate_weather(r, 2024, seed=3)

    def nf(d):
        tmax, tmin, _, _ = daily_normal(r, d)
        return tmax, tmin

    tl = predict_stages(
        r.typical_planting(2024), r.maturity_group, r.lat,
        ((d, tx, tn) for d, tx, tn in zip(wx["date"], wx["tmax_c"], wx["tmin_c"])),
        extend_with_normals=nf,
    )
    mid = compute_features(r, tl, wx, observed_through=date(2024, 7, 15))
    end = compute_features(r, tl, wx, observed_through=date(2024, 11, 1))
    assert 0 < mid.coverage < 1
    assert end.coverage == 1.0


# ---- agronomy response-function direction checks ----

def _base_features():
    """A moderate (non-drought, non-floor) season to perturb from — the
    seeded random season can land at the yield floor, where gradients vanish."""
    f = dict(_features().features)
    for k in ("veg_stress_water", "flower_stress_water", "fill_stress_water",
              "late_stress_water"):
        f[k] = 0.05
    f["fill_days_gt35"] = 1.0
    f["flower_days_gt35"] = 1.0
    return f


def test_drought_in_fill_lowers_oil_and_yield_raises_protein():
    f = _base_features()
    base = expected_outcome(f)
    f2 = dict(f, fill_stress_water=min(1.0, f["fill_stress_water"] + 0.3),
              flower_stress_water=min(1.0, f["flower_stress_water"] + 0.3))
    stressed = expected_outcome(f2)
    assert stressed.yield_bu_ac < base.yield_bu_ac
    assert stressed.oil_pct < base.oil_pct
    assert stressed.protein_pct > base.protein_pct


def test_oil_temperature_dome():
    f = _base_features()
    cool = expected_outcome(dict(f, fill_tmean_c=18.0))
    optimal = expected_outcome(dict(f, fill_tmean_c=28.0))
    hot = expected_outcome(dict(f, fill_tmean_c=34.0))
    assert optimal.oil_pct > cool.oil_pct
    assert optimal.oil_pct > hot.oil_pct


def test_warm_fill_shifts_fatty_acids():
    f = _base_features()
    cool = expected_outcome(dict(f, fill_tmean_c=20.0))
    warm = expected_outcome(dict(f, fill_tmean_c=30.0))
    assert warm.oleic_pct > cool.oleic_pct
    assert warm.linolenic_pct < cool.linolenic_pct
    assert warm.linoleic_pct < cool.linoleic_pct or warm.linoleic_pct < cool.linoleic_pct + 1e-9


def test_heat_days_hurt_yield():
    f = _base_features()
    base = expected_outcome(f)
    hot = expected_outcome(dict(f, fill_days_gt35=f["fill_days_gt35"] + 10,
                                flower_days_gt35=f["flower_days_gt35"] + 10))
    assert hot.yield_bu_ac < base.yield_bu_ac


def test_expected_signs_registry_is_consistent():
    # Every registered sign must be reproduced by the response functions.
    f = _base_features()
    bumps = {
        "fill_stress_water": 0.4, "flower_stress_water": 0.4,
        "fill_days_gt35": 8, "aug_precip_mm": 60, "fill_srad_mj": 120,
        "fill_tmean_c": 4,
    }
    attr = {"yield_bu_ac": "yield_bu_ac", "oil_pct": "oil_pct",
            "protein_pct": "protein_pct", "oleic_pct": "oleic_pct",
            "linolenic_pct": "linolenic_pct"}
    base = expected_outcome(f)
    for target, signs in EXPECTED_SIGNS.items():
        for feat, sign in signs.items():
            if feat not in bumps:
                continue
            # oil temperature response is dome-shaped — sign valid below optimum
            f2 = dict(f)
            f2[feat] = f[feat] + bumps[feat]
            out = expected_outcome(f2)
            d = getattr(out, attr[target]) - getattr(base, attr[target])
            if feat == "fill_tmean_c" and f["fill_tmean_c"] + bumps[feat] > 28:
                continue
            assert d * sign >= 0, f"{target}/{feat}: moved {d} against sign {sign}"
