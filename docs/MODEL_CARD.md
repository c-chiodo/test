# OleoCast Model Card

**Version:** 0.1.0 · **Date:** 2026-08-12 · **Training data tag:** `synthetic-from-published-response-functions-v1`

## What these models do

Six LightGBM regressors predict, for a US soybean field-season, from
stage-window weather + soil + management features (37 features, see
`soyoil/features.py::FEATURE_NAMES`):

| Target | Unit | Intended use |
|---|---|---|
| `yield_bu_ac` | bu/ac | in-season yield forecast |
| `oil_pct` | % seed dry wt | seed oil concentration at maturity |
| `protein_pct` | % seed dry wt | seed protein (reported for crush math; see caveat 4) |
| `oleic_pct`, `linoleic_pct`, `linolenic_pct` | % of oil | fatty-acid profile (oil quality) |

Predictions ship with 80% and 90% conformal intervals whose width is
inflated by the fraction of the season not yet observed.

## Training data — read this first

The current models are trained on **1,056 synthetic field-seasons**
(8 Corn Belt regions × 2004–2025 × ~6 fields/region-year). Weather is drawn
from a climatology-anchored stochastic generator; targets are produced by
response functions encoding the published literature (see
`research/agronomy-oil-drivers.md` for the citation base), with realistic
residual noise.

**Implication:** validation metrics below measure how well the models
recover the *literature's* response structure from noisy data — they are
**not** real-world accuracy claims. The pipeline is designed to retrain
unchanged on USDA NASS county yields and US Soybean Quality Survey
composition data; production metrics will be re-published then and will be
lower. Published field benchmarks to expect: weather-only county yield
anomaly R² ≈ 0.25–0.55, seed oil R² ≈ 0.4–0.5 (~1.0 pt absolute error).
Do not quote the numbers below to customers as field accuracy.

## Validation protocol

- **Leave-one-year-out CV** across 2004–2025 (random k-fold leaks same-year
  weather across folds and is not used).
- **Baselines that must be beaten:** global mean and region-mean +
  linear year trend, both evaluated out-of-year. Skill = 1 − RMSE/RMSE_trend.
- **Conformal intervals** calibrated on out-of-fold (held-out-year)
  absolute residuals; coverage is checked empirically on the same
  out-of-fold predictions.
- No `year` feature enters the models (trees cannot extrapolate a trend);
  the synthetic genetic-gain trend is absorbed by residuals/baselines.

## Current metrics (LOYO, synthetic v1)

Regenerate with `python -m soyoil.train`; served live at `/api/model/meta`.

| Target | RMSE | R² | Baseline (trend) RMSE | Skill | 90% interval ± | Coverage |
|---|---|---|---|---|---|---|
| yield_bu_ac | 6.02 | 0.76 | 11.13 | 46% | 9.77 | 0.90 |
| oil_pct | 0.50 | 0.85 | 1.07 | 53% | 0.83 | 0.90 |
| protein_pct | 0.65 | 0.72 | 1.08 | 39% | 1.08 | 0.90 |
| oleic_pct | 1.37 | 0.89 | 3.37 | 59% | 2.18 | 0.90 |
| linoleic_pct | 1.34 | 0.78 | 2.44 | 45% | 2.22 | 0.90 |
| linolenic_pct | 0.55 | 0.84 | 1.15 | 52% | 0.93 | 0.90 |

(Exact numbers vary slightly per retrain; `/api/model/meta` is authoritative.)

## Known limitations

1. **Synthetic ground truth** (above) — the headline limitation.
2. **No genotype/variety information.** Published work shows genetics is a
   large share of composition variance; weather-only composition prediction
   has a hard ceiling around R² ≈ 0.5 in the field.
3. **Composition labels are coarse in the real world.** Public oil/protein
   data is state-level (US Soybean Quality Survey PDF reports), not county
   or field level. Production oil models will be hierarchical
   (state-year labels, field-level features) with wider intervals.
4. **Protein skill is weak in the published field literature** (R² ≈ 0.1
   weather-only). Treat protein output as a crush-math input, not a
   marketable prediction.
5. **Conformal coverage assumes year-exchangeability.** A regime change
   (new genetics, climate shift) degrades coverage; we report measured
   coverage on held-out years rather than claiming a guarantee.
6. **Phenology model is simplified** (GDD + photoperiod + frost
   termination, calibrated to extension ranges). SoySim/CROPGRO replacement
   is a straight swap behind `predict_stages()`.
7. **In-sandbox weather is simulated.** The live connectors (Open-Meteo,
   NASA POWER) are implemented and cached, but this build environment blocks
   those hosts, so demo forecasts run on the climatology simulator and are
   labeled `simulated-climatology` in every response.

## Ethical / commercial use notes

- Forecasts are decision support, not trading, insurance, or agronomic
  advice; interval bounds matter as much as point values.
- Weather data licensing: Open-Meteo free tier is **non-commercial**;
  production must use their commercial tier, self-hosted ERA5 (CC-BY-4.0
  data), or NASA POWER (public domain). PRISM prohibits commercial use.
- SHAP driver attributions explain the model, not causality in a given
  field.
