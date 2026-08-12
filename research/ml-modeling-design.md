# ML Modeling Design Brief: Soybean Yield, Seed Oil, and Fatty-Acid Profile

**Scope:** in-season prediction of (a) soybean yield and (b) soybean seed oil concentration + fatty-acid
profile, at field and county scale, from weather + soil + phenology features.

**Audience:** our modeling team, and (indirectly) whoever writes customer-facing accuracy language.

**Date:** 2026-08-12

---

## 0. READ THIS FIRST — provenance and verification status

This brief is built from the published literature, but the research environment blocked direct HTTPS
access to most publisher domains (ScienceDirect, Nature, PNAS, Springer, Wiley, Frontiers, MDPI,
Taylor & Francis, arXiv, PMC, AMS Journals, Copernicus). Numbers below were therefore obtained mostly
from search-engine extraction of those pages plus abstracts, not from reading the full PDFs.

Each quantitative claim carries a marker:

| Marker | Meaning |
|---|---|
| `[P]` | I read the primary artifact directly and quote it |
| `[S]` | Secondary: extracted from the source's abstract / indexed page text via search, not the full PDF |
| `[X]` | Could not verify; treat as a lead only |

**Hard rule for the commercial team: no `[S]` or `[X]` number may appear in a customer-facing document
until someone has opened the paper and confirmed it.** Several of these are the kind of number that
gets garbled in secondary reporting (e.g. whether an R² is on raw yields or detrended anomalies changes
its meaning completely). A verification checklist is in §9.4.

Only one item here is `[P]`: the feature structure of the Khaki/Wang CNN-RNN dataset, read from the
authors' own repository.

---

## 1. State of the art: what has actually been achieved

### 1.1 Unit conversions (use these; the literature mixes units constantly)

- 1 bu/ac soybean = 67.25 kg/ha = 0.06725 t/ha
- 1 t/ha = 14.87 bu/ac
- US average soybean yield 2024 = **50.7 bu/ac** `[S]` (USDA NASS, via farmdoc); 2025 Aug-1 forecast
  53.6 bu/ac `[S]`. Use ~50 bu/ac (3.4 t/ha) when converting a paper's "% of average yield" into bu/ac.

### 1.2 Econometric / regularized-linear weather-yield models

**Schlenker & Roberts 2009, PNAS 106:15594** — the reference point for weather-yield functional form.
County-level US corn, soybean, cotton, 1950–2005, paired with a fine-scale daily weather dataset that
resolves the *within-day distribution* of temperature and computes time spent in each 1 °C interval,
restricted to the locations within each county where crops are actually grown. `[S]`

- Optimum temperature: **corn 29 °C, soybean 30 °C, cotton 32 °C**; above threshold, damage is steep,
  and the slope of decline above the optimum is much steeper than the incline below it. `[S]`
- Functional forms compared: 1 °C dummy/step bins, Chebyshev polynomials, piecewise-linear splines;
  quadratic in season-total precipitation. County fixed effects + state-specific (quadratic) time trends. `[S]`
- Projected −30 % corn and −46 % soybean by 2100 under their climate scenario. `[S]`

**Critical benchmarking caveat.** These panel models report very high R² *because county fixed effects
and state time trends absorb most of the variance*. That R² is not a forecast skill number and must
never be quoted as one. The relevant question — how much *interannual* yield variance weather explains —
has a much more sobering answer (§1.7).

**Roberts, Braun, Sinclair, Lobell & Schlenker 2017, Environ. Res. Lett. 12:095010** — compares a
simple process model (SSM), the Schlenker-Roberts statistical model, and their combination against
actual maize yields on a large representative sample of *farmer-managed fields* in the Corn Belt.
After statistical post-model calibration, the process model predicted actual outcomes slightly better
than the statistical model, and **the combined model performed significantly better than either**. `[S]`
Exact out-of-sample R² not recoverable in this environment `[X]`. This is the strongest published
argument for our hybrid roadmap (§9.6).

### 1.3 Gradient boosting / random forest, county scale

**County-level soybean XGBoost framework (Int. J. Appl. Earth Obs. Geoinf., 2023)** — 959 counties,
12 Midwestern states. `[S]`

- Test **R² 0.82, RMSE 0.246 t/ha (= 3.66 bu/ac)**. `[S]`
- XGBoost beat linear regression, random forest, KNN, ANN, SVR, LSTM and DNN **on identical inputs**. `[S]`
- Detrending effect: **R² rose 0.58 → 0.82 and RMSE fell 0.374 → 0.246 t/ha (5.56 → 3.66 bu/ac)** when
  yields were detrended using long-term historical yield data. `[S]`
  *Ambiguity to resolve on verification: whether the improved R² is computed on the detrended target or
  back-transformed to raw yields. This single ambiguity is the difference between "we explain 82 % of
  yield" and "we explain 82 % of a target from which the easy part has been removed."*

**Global de-trending study (GIScience & Remote Sensing, 2024, 15481603.2024.2349341)** — systematically
compares no trend processing / year-as-feature / average-yield-as-feature / linear-yield-as-feature /
global detrending for Midwest maize and soybean XGBoost models. `[S]`

- **Incorporating the yield trend as a predictor significantly improved accuracy and reduced
  uncertainty** versus no trend processing; global detrending reduced average error substantially. `[S]`
- Explicit leakage warning, which we must design around: *"the yield trend for later years includes
  information from earlier years, and evaluating models by including earlier years in the test set and
  later years in the training set would cause information leakage"*; and *"when using yield trends,
  cross-validation cannot be run because the test fold could end up in a bin earlier than the training
  folds."* `[S]` Best practice they recommend: **put the last few years of each region in the test set.** `[S]`

**Random forest, field/plot scale** — RF beat SVR and MLR at **RMSE 414 kg/ha (6.16 bu/ac), R² 0.748**. `[S]`

**Detrending methods review** (Field Crops Res. / Agric. For. Meteorol. 2026, S016819232600002X) —
notes that the common practice of detrend-then-analyze *"has an incorrigible bias due to circular
dependency, and errors in the detrending step inevitably leak into subsequent steps"*; spatially aware
detrending reduced artificial cross-boundary discontinuities by up to 45 % versus county-by-county
detrending. `[S]` This drives our recommendation to fit trend and weather response **jointly** or to at
least fit trend on training years only (§2.6).

### 1.4 Deep learning on remote sensing / weather sequences

**You, Li, Low, Lobell & Ermon 2017, AAAI-17 ("Deep Gaussian Process for Crop Yield Prediction")** —
county-level US soybean from MODIS surface reflectance + temperature band histograms; the GP layer
explicitly models spatiotemporal structure. Best Student Paper, Computational Sustainability track. `[S]`

- **28 % reduction in RMSE** on county-level soybean versus traditional (competing) methods. `[S]`
- **LSTM average RMSE 5.83 bu/ac → 5.55 bu/ac with the GP layer**, averaged over test years 2011–2015. `[S]`
  At ~44 bu/ac average soybean yield in that era this is ~12.6 % rRMSE.
- Full per-method table (ridge, decision tree, DNN, CNN, CNN+GP, LSTM+GP) not recoverable here `[X]`.

**Khaki & Wang 2019, Front. Plant Sci. 10:621 ("Crop Yield Prediction Using Deep Neural Networks")** —
2018 Syngenta Crop Challenge: **2,267 maize hybrids × 2,247 locations, 2008–2016.** `[S]`

- DNN validation **RMSE = 12 % of average yield and 50 % of the standard deviation** using *predicted*
  weather; **11 % / 46 %** with perfect weather. `[S]`
- Significantly outperformed Lasso, shallow NN, and regression tree. `[S]`
- **The "% of standard deviation" framing is the honest one and we should adopt it.** RMSE at 46–50 % of
  σ means the model explains roughly 1 − 0.46² ≈ 75 % to 1 − 0.50² = 75 % of variance *in that trial
  network*, where genotype and location contrasts are large and deliberately spread.

**Khaki, Wang & Archontoulis 2020, Front. Plant Sci. 10:1750 (CNN-RNN; arXiv:1911.09045)** — 13 Corn
Belt states, test years **2016, 2017, 2018**. `[S]`

| Model | Corn RMSE (% of avg yield) | Soybean RMSE (% of avg yield) |
|---|---|---|
| CNN-RNN | **9 %** | **8 %** |
| Deep FCN | 10 % | — |
| Random forest | 11 % | — |
| LASSO | 12 % | — |

`[S]` for the table. At ~50 bu/ac, soybean 8 % ≈ **4 bu/ac RMSE**; corn 9 % at ~175 bu/ac ≈ 15.8 bu/ac.

Feature structure, read from the authors' own repository `[P]`:

- **Weather: 6 variables × 52 weekly time steps = 312 features**
- **Soil: 11 properties × 6 depths (0–5, 5–15, 15–30, 30–60, 60–100, 100–200 cm) = 66 features**
  (SoilGrids250m)
- **Management: 14 binary planting-date-week indicators**
- **Total 392 features per observation**, plus `location_id`, `year`, and the yield response.

This is a well-validated, cheap-to-replicate feature template and I recommend we start from it (§9.2).

**3D-ResNet-BiLSTM (Remote Sensing 15:5551, 2023)** — county soybean from Sentinel-1 + Sentinel-2
time series + Daymet: **R² 0.79, RMSE 5.56 bu/ac (0.374 t/ha)**. `[S]`

**County-level CNN-LSTM (Sensors 19:4363, 2019)** — end-of-season and in-season county soybean;
in-season behavior in §5. Test RMSE **0.263 t/ha (3.91 bu/ac)** around 30 August. `[S]`

**Multi-source RS + DL, county soybean (Agriculture 15:1337, 2025)** — ACGM model **R² 0.74,
RMSE 123.94 kg/ha (1.84 bu/ac)**. `[S]` *This RMSE is implausibly low for county soybean yield and is
almost certainly on a transformed/detrended target or a narrow subset. Flag as suspicious; do not use
for benchmarking.*

### 1.5 Transformer approaches

**Transformer on 30 years of Northern + Southern Uniform Soybean Tests** (weather time series +
genotype + maturity group + geographic location, predicting variety performance across environments):
**R² 77.6 ± 0.2 % (yield), 63.9 ± 4.7 % (oil), 79.3 ± 2.3 % (protein).** `[S]`

**Do not benchmark against this.** It (a) includes genotype/pedigree information we will not have,
(b) predicts trial-network variety performance where genotype main effects and MG × latitude structure
carry much of the signal, and (c) is not an in-season forecast. The 79 % protein R² in particular is a
genotype result — weather-only protein prediction lands around R² 0.09 (§1.6). Quoting the 79 % as our
capability would be a material misrepresentation.

### 1.6 Seed composition: the numbers that actually govern our claims

This is the part of the literature that matters most for us, because our target set includes oil and
fatty acids and because the published skill is *much lower* than the yield literature.

**Weather + soil + season only, US on-farm, 235 fields, 13 states, 2022–2023:**
**yield R² 0.56, seed oil R² 0.39, seed protein R² 0.09.** `[S]`
→ **This is the single best analogue for our exact problem and should anchor our customer claims.**

**Piper & Boote 1999, JAOCS** — US Uniform Soybean Tests, 20 cultivars, 10 maturity groups, 60
locations, **1,863 cultivar × location × year observations**. Predictor: mean daily temperature from
predicted first pod to observed maturity, range **14.6–28.7 °C**. Linear, quadratic and linear-plateau
forms compared; quadratic best. `[S]`

- **Adjusted R² = 0.239 for oil, 0.003 for protein.** `[S]`
- Oil concentration increases with temperature, approaching a maximum at **~28 °C**. `[S]`

**Temperature alone explains ~24 % of oil variance and essentially none of protein variance.** Any
proposal that promises accurate weather-driven protein prediction is contradicted by the best available
large-sample evidence.

**Assefa et al. 2019, Front. Plant Sci. 10:298** — 13,574 observations from 21 US studies, 2002–2017. `[S]`

- **Environment (site-year) accounts for >70 % of the variation** in both seed composition and yield. `[S]`
- Delayed planting reduced oil by **0.007–0.06 % per week** and yield by **0.01–0.04 Mg/ha per week**,
  mainly at northern latitudes (40–45 °N). `[S]`
- At southern latitudes (30–35 °N), later maturity groups → lower oil, higher protein. `[S]`

The >70 % environment share is encouraging for a weather/soil-driven model *in principle* — but note
"environment" bundles weather, soil, latitude, photoperiod, and unmeasured site management, and the
realized predictive R² from measurable weather/soil is 0.39 (oil) / 0.09 (protein).

**Rotundo & Westgate 2009, Field Crops Res. (meta-analysis of environmental effects on soybean seed
composition)** — the mechanistic backbone for our synthetic generator: `[S]`

- Water stress reduces the **content** (mg/seed) of protein, oil and residual fractions; **protein
  accumulation is less affected than oil and residual**, so final protein **concentration rises**. `[S]`
- Increasing N supply raises both protein concentration and content. `[S]`
- **The magnitude of response under controlled field manipulation was far smaller than the spread
  observed in the Uniform Soybean Regional Field Tests.** `[S]`
  → i.e. a large fraction of real-world composition variance is *not* attributable to the
  manipulable/measurable drivers. Irreducible-noise floor warning.

**Chiozza et al. 2025, Crop Science (doi 10.1002/csc2.70142)** — field trials spanning **22 US states,
24 years**; eight random-forest models with varying predictor counts, evaluated by **leave-one-year-out
(LOYO)** CV, plus GAMs. `[S]`

- **Models using only latitude, longitude, planting date, and mean temperature from planting to
  flowering performed similarly to much more informed models.** `[S]`
  → Strong, actionable finding: **feature parsimony**. Adding weather complexity to composition models
  buys little. Our composition model should start tiny and only grow if LOYO CV says otherwise.
- Inverse protein–oil relationship confirmed; over 24 years protein declined in MG 0–5 and rose in
  MG 6–8. `[S]`
- Their exact RMSE values were not recoverable here `[X]` — **verify before quoting** (§9.4).

**Satellite-based composition (for context on the achievable ceiling with imagery):**

- Habibi et al. 2023, Comput. Electron. Agric. 212:108096 — 47 fields, Kansas + Iowa, 2019–2021; six
  learners (ElasticNet, RF, XGBoost, LightGBM, CatBoost, ensemble). Best **RMSE 1.80 percentage points
  protein, 1.04 pp oil.** `[S]`
- PlanetScope + GRU (ISPRS J. Photogramm. 2023) — **R² 0.36 protein, 0.53 oil**, absolute error 1.80 %
  protein / 1.04 % oil. `[S]`

So even *with* in-season imagery, oil R² ≈ 0.5 and protein R² ≈ 0.36, with ~1 pp oil error. Our
weather+soil-only model should be expected to be somewhat worse than that.

**Fatty acids.** No published field-scale, weather-only fatty-acid prediction benchmark was found. The
mechanistic evidence is consistent and strong, but it is largely controlled-environment:

- Fatty acid composition is strongly temperature-affected: as temperature rises, **linolenic and
  linoleic decrease markedly, oleic increases; palmitic and stearic remain essentially unchanged**
  (Wolf et al. 1982, JAOCS; Gibson & Mullen 1996, JAOCS). `[S]`
- High temperature during seed fill (R5–R8) increases oleic, decreases linoleic and linolenic. `[S]`
- **Day × night interaction:** when night temperature was increased at 30 °C day temperature during
  R5–R8 and R1–R8, **oleic decreased and linoleic increased** — i.e. the response is not a simple
  function of mean temperature. `[S]`
- Mechanism: high daytime temperatures reduce **desaturase activity**. `[S]`
- A published model of climate-driven variability in essential fatty acids used **233 data points across
  daytime highs 15–40 °C** (Am. J. Clin. Nutr., S0002-9165(23)66122-2). `[S]`
- Cultivar × temperature interaction is significant for protein, oil, palmitic, oleic, linolenic,
  raffinose and stachyose across five day/night regimes (21/13, 25/17, 29/21, 33/25, 37/29 °C);
  optimum temperature for yield was **26 °C** (indeterminate AG5332) and **23 °C** (determinate
  P5333RY) (Agron. J. 112:194–204, 2020). `[S]`

**Implication:** oleic, linoleic and linolenic are legitimately weather-responsive and worth modeling.
Palmitic and stearic are not, from weather. And the significant cultivar × temperature interaction means
**a weather-only fatty-acid model has a hard ceiling set by unobserved genotype**.

### 1.7 The honest reality check on weather-driven skill

Two results should govern every accuracy claim we make:

- **Nationally averaged US soybean yield has ~22 % interannual variability, of which ~32 % is
  significantly explained by temperature and precipitation; adding radiation explains a further ~5 %.**
  (Sci. Rep. 6:33160, "The Role of Climate Covariability on Crop Yields in the Conterminous US") `[S]`
- **In the major growing regions (Missouri, Iowa, Illinois), up to ~30 % of total year-to-year soybean
  yield variability is explained by temperature plus precipitation**; climate variability has larger
  explanatory power in the Southeastern US (>60 %). `[S]`

And on which weather matters:

- **August/September root-zone soil moisture and August maximum temperature are the dominant drivers,
  each selected in >25 % of counties** (Earth Syst. Dynam. 12:1371, compound hot–dry extremes on US
  soybean yields). `[S]`
- **VPD 60–90 days after sowing was the most important environmental driver of Midwest maize yields
  1995–2012**, above temperature and precipitation; rising VPD was associated with slowing genetic gain
  (Lobell et al. 2014); a corn-belt meta-analysis found a **dominant role of VPD over soil moisture** in
  regulating crop productivity. `[S]`

Reconcile this with §1.3–1.4: an R² of 0.82 on raw county yields is real but is mostly **spatial**
(county-to-county soil/latitude differences) and **trend** (technology). The *weather* contribution to
*interannual anomaly* is on the order of R² 0.25–0.40 in the core Corn Belt. Our validation protocol
must report both numbers so we never accidentally sell the easy one.

### 1.8 Hybrid crop-model + ML

**Shahhosseini, Hu, Huber & Archontoulis 2021, Sci. Rep. 11:1606** — US Corn Belt maize; adding
APSIM-simulated variables as ML input features **decreased RMSE by 7–20 %**. `[S]`

- Most important APSIM features: **average drought stress and average water table depth during the
  growing season**; soil-moisture-related APSIM variables were most influential, followed by
  crop-related and phenology-related variables. `[S]`
- Togliatti et al.: including a 7–14 day weather forecast did **not** improve end-of-season yield
  prediction accuracy for APSIM maize/soybean. `[S]` (Important for §5 — short-range NWP is not the
  lever; the lever is how you fill the *rest* of the season.)

**Shahhosseini et al. 2020, Front. Plant Sci. (arXiv:2001.09055) "Forecasting Corn Yield with Machine
Learning Ensembles"** — IL/IN/IA county scale, complete and partial in-season weather, ensembles built
with a **blocked sequential procedure to generate out-of-bag predictions**, forecasts aggregated to
district and state level. Optimized weighted ensemble and simple average ensemble were most precise at
**RRMSE 9.5 %**. `[S]`

**CROPGRO-Soybean (DSSAT)** — relevant because it is the only widely available mechanistic model that
simulates our composition targets: it **simulates seed oil and protein concentration from a carbon and
nitrogen balance with cultivar-specific target protein/oil concentrations and a temperature effect**;
development is driven by temperature and photoperiod at hourly time step. `[S]` Reported validation:
biomass R² 0.98, 0–80 cm soil water R² 0.64, **yield R² 0.81, nRMSE 10.83 %**. `[S]`

**Paudel et al. 2021, Agricultural Systems 187:103016** — a reusable ML workflow on MARS Crop Yield
Forecasting System data, 5 crops × 3 countries at NUTS2/NUTS3; forecasts at early season (30 days after
planting) and end of season, with and without previous-year predicted yield trend. ML beat the MCYFS
operational baseline; the optimized configuration had significantly better normalized RMSE at 60 days
before harvest (Wilcoxon signed-rank). `[S]` **Their design — always compare to the incumbent
operational forecast, and always report with/without trend — is the design we should copy.**

### 1.9 Field scale: read the fine print

**"Harvesting insights" (Sci. Rep. 16:8994, 2026)** — US commercial maize and soybean yield-monitor
data, **134 unique crop-site-years**; ML predicted yield at **R² > 0.87, RMSE < 1.13 Mg/ha
(16.8 bu/ac)** using weather, soil and terrain, interpreted with SHAP. `[S]`

Note the internal tension: R² > 0.87 with RMSE up to 16.8 bu/ac is only coherent if the target variance
is enormous — which it is, because this is **within-field, sub-field pixel-level** variance across
sites and years. **Within-field spatial R² is not year-ahead forecast skill.** If a competitor quotes
"R² 0.87 at field scale," this is likely the sleight of hand. We must not do the same.

**Cross-validation strategy at field scale** (S2666154324001339, soybean UAV): random CV showed poor
error tracking when predicting beyond the model's spatial domain, while **spatial CV and
leave-one-field-out CV gave better expectations for out-of-domain yield prediction**. `[S]`

---

## 2. Feature engineering: accepted best practice

### 2.1 The three competing representations of daily weather

| Approach | What it is | Pros | Cons | Verdict |
|---|---|---|---|---|
| **Fixed calendar windows** | Aggregate to weeks/months/thirds-of-season | Trivially reproducible; no phenology model needed; what Khaki's 6 × 52 template does `[P]` | Misaligns growth stages across latitudes, planting dates and maturity groups | **Keep as the robust baseline** |
| **Growth-stage windows** | Aggregate within VE–V n, R1–R3, R3–R5, R5–R7, R7–R8 | Agronomically meaningful; matches the physiology of the stress response; supports the composition targets directly | Requires a phenology model whose own error is 6–7 days (§2.4); errors propagate | **Primary representation** |
| **Functional / distributed-lag / spline** | Represent the whole daily series with a basis (B-spline, Fourier, penalized distributed lag) and let the model learn the weight function | No arbitrary window boundaries; recovers the shape of the critical period; Schlenker & Roberts used piecewise-linear splines in *hourly temperature* `[S]` | Needs more data; harder to explain to agronomists | **Secondary / research track** |

Published comparisons favor phenology alignment: *"integrating a crop growth window derived from crop
phenology data yielded more accurate predictions than the conventional method, which utilized a fixed
growing season definition devoid of spatial-temporal variations"*; and *"segmenting time windows by
phenological stages allows for more precise extraction of environmental variables"*. `[S]`

**Recommendation: compute both, in the same feature table, and let LOYO CV choose.** They are cheap.
The calendar block is your fallback when the phenology model is out of range (unusual MG, replant,
double-crop).

### 2.2 Temperature: degree-day binning (the Schlenker-Roberts approach)

The single most transferable idea in this literature. Implementation:

1. For each day, reconstruct the **within-day temperature distribution** from Tmin/Tmax. Schlenker &
   Roberts fit a sinusoidal interpolation between daily min and max and integrate the time spent in each
   interval. `[S]` A sine curve between Tmin and Tmax is standard and adequate.
2. Accumulate **time (in degree-days or hours) in each 1 °C or 3 °C bin** over the window. Use 3 °C bins
   at our data volume; 1 °C bins produce ~40 sparse, collinear columns.
3. Bin range: **0–45 °C**, with open-ended bins below 5 °C and above 40 °C.
4. Add the classical summary pair as well:
   - `GDD_base10_cap30` — soybean growing degree days, base 10 °C, upper cap 30 °C
   - `KDD_30` — killing degree days = Σ max(0, Tmax − 30), using **30 °C for soybean** per Schlenker &
     Roberts' soybean threshold `[S]`
5. Compute bins **per growth-stage window**, not just season-total. The R3–R5 (pod set to seed fill)
   window is where the yield signal concentrates; R5–R8 is where the composition signal concentrates. `[S]`

**Why bins beat a quadratic:** the response is sharply asymmetric — the decline above optimum is much
steeper than the incline below `[S]` — and a quadratic forces symmetry. Bins (or a piecewise-linear
spline with a knot at 30 °C) let the data speak. For a tree model this matters less (trees find
thresholds), which is exactly why the **binned representation is most valuable for our regularized
linear benchmark** and for interpretability.

### 2.3 Water, VPD and stress-day counts

Feature families, in rough order of expected value:

1. **VPD.** `VPD = es(Tmax,Tmin) − ea`, using a saturation-vapor-pressure formulation (Tetens) and
   dewpoint or specific humidity for `ea`. Compute daily mean and daily max VPD; aggregate as mean,
   90th percentile, and `Σ max(0, VPD − 2.0 kPa)` per stage window. Justified by VPD being the most
   important driver of Midwest maize yield at 60–90 days after sowing, and by VPD dominating soil
   moisture in a corn-belt meta-analysis `[S]`. Gridded VPD products built on Daymet exist (Sci. Data
   2025) `[S]`.
2. **Water-balance accumulators.** A simple daily bucket is enough and vastly better than raw
   precipitation:
   - Reference ET (Hargreaves if radiation is scarce, Penman-Monteith / ASCE if available)
   - Crop coefficient Kc by stage → ETc
   - Bucket depth = **soil AWC × effective rooting depth** (rooting depth ramping with thermal time)
   - Outputs per stage window: `water_deficit = Σ(ETc − ETa)`, `relative_ET = ΣETa/ΣETc`,
     `days_below_50pct_ASW`, `min_relative_soil_water`, `n_days_water_stress`
   - Cumulative precipitation and precipitation deficit vs. climatology
   - Antecedent moisture: **preseason (Oct–Apr) precipitation** — soybean draws on stored profile water
   Justified by APSIM's *drought stress* and *water-table depth* being the most valuable simulated
   features to add to ML `[S]`, and by August/September root-zone soil moisture being a dominant county
   driver `[S]`.
3. **Stress-day counts** (per stage window): days with Tmax > 30/32/35 °C; days with Tmin > 22 °C
   (night-temperature stress — needed for the fatty-acid day × night interaction, §1.6); consecutive dry
   days ≥ 7/14; days with VPD > 2 kPa; **compound hot-dry days** (Tmax > 32 °C AND soil water < 50 %
   ASW) — compound extremes are what drive the tail losses `[S]`.
4. **Radiation and radiation × temperature**: incident solar radiation per stage window; photothermal
   quotient (radiation ÷ mean temperature) over R1–R5, a classic sink-capacity proxy. Radiation added
   ~5 % explanatory power on top of T and P nationally `[S]`.
5. **Excess water**: days with precipitation > 25 mm, waterlogging proxy = consecutive days at field
   capacity, spring saturation index (soybean is planted late enough that this matters less than for
   corn, but replant risk lives here).

### 2.4 Phenology encoding

Do not use calendar dates as a proxy for stage. Use a **photothermal phenology model**:

- **SoySim** (Setiyono et al., Univ. of Nebraska) predicts soybean development as a joint function of
  photoperiod and temperature, developed for relative maturity groups **0.8–4.2** in US temperate
  conditions; the R1–R8 phase is a function of temperature and photoperiod for R1–R7 and of minimum
  temperature and relative humidity for R7–R8. `[S]`
- Photoperiod sensitivity and optimum photoperiod vary systematically with maturity group — **low MGs
  are less photoperiod-sensitive and have a higher optimum photoperiod** than high MGs. `[S]`
- **Accuracy to budget for:** SoyStage predicted R1, R5, R7 with mean differences of 0.15, 1.10 and
  3.7 days and **RMSE of 5.8, 6.8 and 7.2 days** respectively. `[S]`
- CRONOSOJA is a published daily-time-step hierarchical alternative covering Southern Cone MGs. `[S]`

**Design consequence:** with ±6–7 days of stage-timing error, do not build features on windows narrower
than ~14 days. Use 5 wide reproductive windows, and additionally emit a
`phenology_confidence` feature (e.g. |MG − 2.5| out-of-range flag, replant flag) so the model can learn
to discount stage-aligned features when the phenology model is extrapolating.

Encode as features:
- `dap_R1`, `dap_R3`, `dap_R5`, `dap_R7` (days after planting to each stage)
- `duration_R1_R5`, `duration_R5_R7` (seed-fill duration — mechanistically central for oil)
- `photoperiod_at_R1`, `mean_photoperiod_R1_R5`
- `thermal_time_R5_R7`
- `fraction_of_season_complete` (for in-season models, §5)

### 2.5 Soil, location and management

| Feature | Source | Encoding |
|---|---|---|
| Available water capacity (AWC) | gSSURGO (30 m, US) / SoilGrids250m (global) | Weighted mean to 100 cm and 150 cm; also AWC of top 30 cm |
| Sand / silt / clay | same | by 6 depth layers, following the Khaki 11 × 6 template `[P]` |
| Organic matter / SOC | same | 0–30 cm and profile |
| pH, CEC | same | 0–30 cm |
| Bulk density, coarse fragments | same | profile mean |
| Drainage class / hydrologic group | gSSURGO | ordinal |
| Rooting depth restriction | gSSURGO | cm to restrictive layer |
| Slope, aspect, TWI, curvature, elevation | 10 m DEM | field scale only; terrain was a meaningful SHAP contributor in yield-monitor work `[S]` |
| Latitude, longitude | — | **raw lat/lon plus a smooth spatial basis** (thin-plate spline or 2-D RBF features), *not* one-hot county — see §3.3 |
| Planting date | grower record / NASS progress | day of year **and** deviation from the county's 10-year median planting date; Khaki used 14 planting-week indicators `[P]` |
| Maturity group | grower record | numeric relative maturity (continuous, e.g. 2.7), not a category |
| Row spacing, seeding rate, irrigated flag, tillage, previous crop | grower record | as available; **irrigated flag is essential** and is the single largest management interaction with water features |

**Latitude interacts with everything** in this crop (photoperiod → MG choice → seed-fill window →
temperature during seed fill → oil). Provide lat explicitly and provide `latitude × mean_temp_R5_R7`
as an engineered interaction for the linear benchmark; the GBM will find it itself.

### 2.6 Trend / technology terms, and how not to leak them

This is where most published pipelines quietly cheat.

**The problem.** County yields have a strong upward technology trend (~0.3–0.5 bu/ac/yr for soybean).
A model given `year` as a feature, or trained on a target detrended using the *whole* record, has seen
information from the future.

- The trend for later years incorporates information from earlier years, so training on later years and
  testing on earlier years leaks; and if you use a trend feature, plain k-fold CV is invalid because a
  test fold can sit chronologically before its training folds. `[S]`
- Detrend-then-model has *"an incorrigible bias due to circular dependency, and errors in the detrending
  step inevitably leak into subsequent steps."* `[S]`

**Recommended handling (in order of preference):**

1. **Two-stage with strictly causal trend.** For each forecast origin year `T`:
   - Fit the trend + spatial level model on years `≤ T−1` only:
     `y_it = μ_i + β_s·(t − t0) + ε_it` where `i` = county/field, `s` = state (or CRD), fit by a mixed
     model or by per-county robust (Theil-Sen) regression pooled with a state-level slope prior.
   - Target for the ML model = `ε_it` (the yield **anomaly**, in bu/ac).
   - Prediction = `trend_hat_i,T + ML(features)`.
   - Because the trend is refit for every origin year using only prior data, there is no leakage. This
     costs `n_years` trend fits — negligible.
2. **Trend as a causal feature.** Instead of detrending, pass `expected_yield_i,T` (the county's
   trend-projected yield computed from years `≤ T−1`) as a *feature* and predict raw yield. Reported to
   significantly improve accuracy and reduce uncertainty versus no trend processing `[S]`. Equivalent in
   spirit to (1); slightly less interpretable but lets the GBM modulate the trend by soil/weather.
3. **Spatially aware detrending.** If detrending, do it with spatial smoothing rather than
   county-by-county — spatially aware detrending reduced artificial cross-boundary discontinuities by up
   to 45 % `[S]`. Practically: shrink each county's slope toward its CRD/state slope (partial pooling).

**Never:** pass raw `year` as a numeric feature to a tree model. Trees cannot extrapolate; `year` will
be split at the training boundary and the model will silently predict the last training year's level
forever. If you want a trend, supply it as an explicit level (option 2), not as `year`.

**Also never:** include yield-derived features that encode the target year (in-year county average
yield, in-year NASS condition ratings aggregated at a level that includes the target unit, harvest-time
imagery for an "in-season July" model). Build a **feature-availability calendar** (§5.2) and enforce it
in code.

### 2.7 Composition-specific feature engineering

The composition targets need a *different*, smaller feature set than yield. Chiozza et al. found that
**latitude, longitude, planting date, and mean temperature from planting to flowering performed
similarly to much more informed models** under LOYO CV `[S]`. Start there and add only:

- `mean_temp_R5_R7` and `mean_temp_R3_R7` (seed fill) — the mechanistic driver, with a **quadratic
  term** and a maximum near **28 °C** for oil per Piper & Boote `[S]`
- `mean_Tmax_R5_R8` and `mean_Tmin_R5_R8` **separately**, because the day × night interaction reverses
  the oleic/linoleic response `[S]` — do not collapse to a mean
- `n_days_Tmax>33_R5_R8`, `n_days_Tmin>22_R5_R8`
- `water_deficit_R5_R7` and `relative_ET_R5_R7` — water stress during seed fill raises protein
  concentration by suppressing oil and residual accumulation more than protein `[S]`
- `duration_R5_R7` (seed-fill length)
- `maturity_group`, `latitude`, `planting_date_doy`
- `soil_AWC`, `irrigated`
- Optionally soil S and N proxies (S and N supply alter protein, oil and fatty acids `[S]`)

**Compositional closure for fatty acids.** Palmitic + stearic + oleic + linoleic + linolenic ≈ 100 % of
the five major fatty acids. Modeling five percentages independently will produce predictions that do not
sum correctly and will over-report skill for the well-determined ones. Use an **additive log-ratio (ALR)
transform**:

```
z_k = log(p_k / p_ref),  k ∈ {palmitic, stearic, oleic, linolenic},  ref = linoleic
```

Model the four `z_k` (multi-output, or four separate LightGBM models), then invert with a softmax back
onto the simplex. Report accuracy in **percentage points on the original scale**, after inversion —
that is what a crush plant cares about. Propagate uncertainty by sampling in `z` space and inverting
(§4.5).

---

## 3. Validation: how to be honest

### 3.1 Why random k-fold overstates skill for crop yield

Three distinct leakage channels, all of which random k-fold opens:

1. **Temporal.** Weather is spatially correlated over hundreds of kilometres. In a drought year, most
   counties are low together. If 2012 appears in both train and test folds, the model has effectively
   been told "2012 was bad," and it will look like it inferred that from the weather features. Real
   deployment always predicts an *unseen year*.
2. **Spatial.** Neighboring counties share soil, climate, management and even the same reporting
   practices. Random folds put near-duplicates on both sides.
3. **Trend.** As in §2.6, a trend feature or a globally detrended target makes random folds
   chronologically incoherent `[S]`.

Published magnitude of the inflation:

- **Within-country random CV inflates performance by 0.22–0.38 R² units relative to leave-one-country-out
  CV** (leave-one-country-out evaluation in Sub-Saharan Africa, arXiv:2605.08113). `[S]` *Different
  geography and larger domain shift than ours, so treat as an upper bound — but 0.2+ R² units of
  inflation is the right order of magnitude to expect from spatial leakage.*
- Random CV *"exhibited poor error tracking performance in predicting yield beyond the model spatial
  domain, while spatial CV and leave-one-field-out CV approaches provided better expectation on yield
  predictions outside the model's training spatial domain."* `[S]`
- Recommendation from that literature: *"leave-one-country-out, or at minimum a spatial block CV scheme
  that prevents geographic leakage, should be the default evaluation protocol for operational yield
  prediction."* `[S]`
- Nested leave-two-out CV has been proposed specifically for optimal crop yield model selection
  (Geosci. Model Dev. 15:3519, 2022) `[S]` — relevant because we also select hyperparameters, and doing
  that on the same folds we report is a second, subtler inflation.

### 3.2 The protocol we will use

Four evaluations, all reported, always:

**V1 — Forward-chaining by year (the headline; matches deployment).**
```
for T in [Y0+k .. Y_last]:
    train on years <= T-1  (trend refit on <= T-1 as in §2.6)
    test on year T
```
This is what an operational forecaster experiences. Report per-year metrics, not just the pooled number
— pooled metrics hide the fact that we will be worst exactly in the anomalous years that matter
commercially (2012, 2019, 2023).

**V2 — Leave-one-year-out CV (more data-efficient, for model selection).**
Train on all years but `T`, test on `T`. Used by Chiozza et al. for soybean composition `[S]`, and
standard in this literature. Slightly optimistic versus V1 because it uses future years, so **select on
V2, report V1.**

**V3 — Blocked spatial CV (tests spatial transfer).**
Hold out whole **Crop Reporting Districts** (or ~100 km spatial blocks, or whole states for the
pessimistic version). Necessary before we claim we can serve a geography we have no training data in.

**V4 — Leave-one-year-and-region-out (the pessimistic bound, and the number to quote to a partner
entering a new geography).** Hold out year `T` *and* region `R` simultaneously. Expect this to be
materially worse than V1 or V3 alone. If a partner asks "what will it do for me in year one in a
district you've never seen," V4 is the honest answer.

**Nested hyperparameter tuning.** Inside each outer fold, tune on an inner temporal split of the
training years only. Never tune on the outer test year. Budget for the fact that reported skill drops
when you do this correctly.

### 3.3 Two extra guardrails specific to this problem

- **Never one-hot encode county/field ID.** It memorizes the spatial mean and makes V3/V4 impossible to
  interpret, and it will not generalize to a new field. Encode place through *physical* variables
  (soil, lat/lon basis, elevation, climate normals) plus the explicit trend/level term from §2.6.
- **Report on both scales.** Every metric table gets two columns:
  - **raw yield** (what the customer receives), and
  - **anomaly** = raw − (county level + trend) (what the *model* actually contributes).

  The gap between these is where overclaiming happens.

### 3.4 Baselines that must be beaten

A model is not useful because its R² is high. It is useful because it beats what the customer already
has for free.

| ID | Baseline | Why it matters |
|---|---|---|
| **B0** | Global mean of training yields | Sanity floor |
| **B1** | Climatological county mean (last 5 or 10 years) | The free forecast |
| **B2** | **County mean + state/CRD linear trend, fit on years ≤ T−1** | **The real bar. Report skill score vs B2 as the headline.** |
| **B3** | Last year's county yield (persistence) | Weak for yield (weather is not persistent) but a good check |
| **B4** | B2 + a single stress index (e.g. July–Aug KDD_30 and water deficit in an OLS) | Tests whether the ML earns its complexity over 3 features |
| **B5** | **USDA NASS August/September forecast**, where the geography and timing permit | The incumbent operational product. Paudel et al. benchmarked against the MCYFS operational forecast for exactly this reason `[S]` |

For composition:
- **B1c** = grand mean of oil/protein/each FA
- **B2c** = mean by (maturity group × latitude band × year), fit on prior years
- **B3c** = the Piper & Boote quadratic in mean seed-fill temperature alone (adjusted R² 0.239 for oil,
  0.003 for protein `[S]`) — **if our oil model does not clearly beat a single-variable 1999 quadratic,
  we have nothing.**

**Metrics to report:** RMSE, MAE, bias (mean error — check it is ~0 per year and per region), R²,
Nash-Sutcliffe vs B2, **skill score `1 − MSE_model/MSE_B2`**, and, for the probabilistic model,
**empirical PI coverage, mean interval width, CRPS, and pinball loss** (§4.6).

---

## 4. Uncertainty: recommendation and algorithm

### 4.1 The options

| Method | Guarantee | Adaptive width? | Cost | Fit with our stack |
|---|---|---|---|---|
| **Quantile gradient boosting** (LightGBM `objective="quantile"`) | None (miscalibrated in practice) | Yes | 1 model per quantile | Excellent |
| **Split conformal** (absolute-residual score) | Marginal coverage, finite-sample, distribution-free (under exchangeability) | **No** — constant width | 1 extra split | Excellent |
| **CQR** — conformalized quantile regression (Romano, Patterson & Candès 2019) | Same marginal guarantee | **Yes** — inherits quantile model's heteroscedasticity | 2 quantile models + calibration set | Excellent |
| **Jackknife+ / CV+ / Jackknife+-after-Bootstrap** (MAPIE) | Slightly weaker but strong empirical coverage; *"typical coverage levels estimated by jackknife+ follow very closely the target coverage levels"* `[S]` | Limited | n or k model fits | Good; CV+ advised when n is large or fits are expensive `[S]` |
| **NGBoost** (Duan et al., ICML 2020) | None (parametric, well-calibrated in practice); full predictive distribution | Yes | 1 model | Separate library; slower than LightGBM |
| **Deep ensembles / bootstrap ensembles** | None; captures epistemic spread; underestimates aleatoric | Yes | k model fits | Good; we get it free from the V1 fold models |

Published crop-yield evidence that calibration is the whole game: an uncalibrated global crop-yield
model delivered **40.03 % empirical coverage for a nominal 80 % interval**, while conformalized quantile
regression delivered **80.72 %** (HSE-GNN-CP, Information 17:141). `[S]` A 2× coverage error is the
default state of an uncalibrated model. This is why we conformalize.

### 4.2 Recommendation

**Use LightGBM quantile regression conformalized by CQR, with (a) a temporally-blocked calibration set,
(b) Mondrian (group-conditional) calibration by region × forecast-date, and (c) non-exchangeable
(recency-weighted) residual weighting. Add Adaptive Conformal Inference for online recalibration in
production.**

Rationale:

1. **Heteroscedasticity is the dominant feature of this problem.** Yield error is small in benign years
   and enormous in drought years; oil error is larger at extreme seed-fill temperatures. A
   constant-width interval (split conformal) would be simultaneously too wide in normal years and too
   narrow in the years customers care about. CQR is specifically *"adaptive to local variability even
   for highly heteroscedastic data"* `[S]`.
2. **It reuses our primary model class.** No second modeling stack, no distributional assumption. Same
   LightGBM, three objectives.
3. **Distribution-free finite-sample marginal coverage**, which is the only kind of guarantee we can
   defensibly put in a contract.
4. **Group-conditional calibration** matters commercially: marginal 90 % coverage can hide 70 % coverage
   in the Delta and 98 % in Iowa. Group-conditional conformal prediction via quantile-regression
   calibration is established `[S]`. A partner operating in one region needs coverage *there*.

### 4.3 The honest caveat we must state

**Conformal prediction's coverage guarantee assumes exchangeability, and crop-yield data are not
exchangeable.** Years are temporally dependent, the technology trend is a drift, and climate is a slow
distribution shift.

- *"Conformal prediction provides distribution-free coverage guarantees that crucially rely on the
  assumption of exchangeability, but this assumption is fundamentally violated in time series data where
  temporal dependence and distributional shifts are pervasive. As a result, classical split-conformal
  methods may yield prediction intervals that fail to maintain nominal validity."* `[S]`
- *"Although Adaptive Conformal Inference substantially mitigates coverage degradation under
  distribution shift, it does not fully restore the formal exchangeability guarantee required by
  standard conformal prediction when calibration and deployment periods experience fundamentally
  different climate regimes."* `[S]`
- Mitigations in the literature: reweighting calibration data, dynamically updating residual
  distributions, adaptively tuning the target coverage in real time. `[S]`

**Therefore our customer language must be empirical, not theoretical:** *"In leave-one-year-out
backtesting over N years and M counties, our nominal 80 % intervals contained the realized yield in
X % of cases; coverage was Y % in the worst single year (20ZZ)."* Not: *"our intervals have a
statistical guarantee of 80 % coverage."* The guarantee is conditional on an assumption we know to be
false, and the empirically measured coverage in a *held-out year* is the only number that means
anything.

### 4.4 Algorithm (implementable as written)

**CQR with grouped, recency-weighted calibration, per forecast date.**

Inputs: target miscoverage `α` (e.g. 0.2 for 80 % intervals); training years `Y_train`; forecast-origin
date `d`; grouping function `g(x)` returning a region label; recency half-life `h` in years.

```
STEP 1 — Split by TIME, not at random.
  cal_years  = the most recent 2-3 years of Y_train        # e.g. {T-3, T-2, T-1}
  fit_years  = Y_train \ cal_years
  Rationale: a random split would leak the calibration year's weather regime into the fit,
  producing optimistically narrow intervals. Blocking by year is essential.

STEP 2 — Fit two quantile models on fit_years, at the date-d feature set.
  q_lo = LightGBM(objective="quantile", alpha=α/2      )   # e.g. 0.10
  q_hi = LightGBM(objective="quantile", alpha=1 - α/2  )   # e.g. 0.90
  (Also fit q_50 / an L2 model for the point forecast.)

STEP 3 — Conformity scores on the calibration set.
  For each calibration point i:
      E_i = max( q_lo(x_i) - y_i ,  y_i - q_hi(x_i) )
  E_i > 0 means the nominal interval missed; E_i < 0 means it was wider than needed.
  This is the CQR score: it is signed so it both widens AND narrows.

STEP 4 — Weights (non-exchangeability correction).
  w_i = 0.5 ** ( (T - year_i) / h )        # exponential recency, half-life h (start h = 5 yr)
  Optionally multiply by a similarity weight, e.g. exp(-dist(region_i, region_target)/L).
  Normalize weights within each calibration group.

STEP 5 — Group-conditional (Mondrian) quantile of the scores.
  For each group G = g(x) (region × forecast-date bucket):
      Q_G = weighted quantile of {E_i : i in G} at level ceil((n_G+1)(1-α))/n_G
      (weighted: smallest t such that  sum_{E_i <= t} w_i / sum w_i  >= (1-α)(1 + 1/n_G) )
  Guardrails:
      if n_G < 50:  fall back to the pooled Q (record the fallback in the prediction metadata)
      Q_G = clip(Q_G, lower=-0.5 * nominal_width, upper=+3 * nominal_width)   # stop pathologies

STEP 6 — Predict.
  interval(x) = [ q_lo(x) - Q_{g(x)} ,  q_hi(x) + Q_{g(x)} ]
  point(x)    = q_50(x)  (or the L2 model; report which)
  Enforce physical bounds: yield >= 0; oil, protein, each FA in (0, 100); ALR-inverted FAs sum to 100.

STEP 7 — Validate the intervals as a first-class deliverable.
  Under the V1 forward-chaining protocol, for each held-out year compute:
      empirical coverage, mean interval width, CRPS, pinball loss at α/2 and 1-α/2,
      coverage broken out by region, by decile of predicted yield, and by year
  Publish the WORST-YEAR coverage, not just the average.

STEP 8 — Production: Adaptive Conformal Inference (online).
  Maintain α_t. After each observed outcome:
      α_{t+1} = α_t + γ ( α - 1{ y_t not in interval_t } ),   γ ≈ 0.01-0.05
  Clip α_t to [0.01, 0.5]. This tracks drift between annual retrainings.
  NOTE: with one harvest per year, ACI updates once per season. Its practical value here is across
  the *many spatial units* per season, so update on a per-unit basis within a season and treat the
  per-season aggregate as one drift observation.
```

**Library:** `mapie` implements split conformal, CQR (`ConformalizedQuantileRegressor` /
historically `MapieQuantileRegressor`), Jackknife+, CV+, and Jackknife+-after-Bootstrap on any
scikit-learn-compatible estimator `[S]`. MAPIE's own guidance: **jackknife+ when accurate and robust
intervals are required; CV+ when n is large or each fit is expensive; CQR when data are
heteroscedastic** `[S]`. Note that MAPIE's public API changed between 0.x and 1.x (the
`fit`/`conformalize`/`predict_interval` split); **pin the version and read the installed docstrings
rather than trusting blog posts.** For the grouped + weighted variant in §4.4 above, implement Steps
3–6 directly (it is ~40 lines) and use MAPIE as the cross-check that our implementation reproduces its
marginal coverage on the same split.

### 4.5 Fatty acids: uncertainty on the simplex

Sample `S = 1000` draws of the ALR vector using per-component conformal intervals as marginal ranges
with an empirical residual correlation matrix from the calibration set (Gaussian copula on ALR
residuals), invert each draw with softmax, then take per-component empirical quantiles on the
percentage scale. This keeps closure and produces correctly negatively-correlated oleic/linoleic
intervals, which is the physically real behavior.

### 4.6 Reject NGBoost / bootstrap-only as the primary

NGBoost is attractive (one model, full distribution, any distribution family and scoring rule `[S]`)
but adds a second boosting implementation, is slower, and gives no coverage guarantee. **Use it as a
challenger in the model bake-off, scored on CRPS and NLL, not as the default.** Bootstrap/deep
ensembles capture epistemic but not aleatoric uncertainty and will under-cover; keep the V1 fold models
as a free ensemble for *disagreement diagnostics* (a useful drift alarm) rather than as intervals.

---

## 5. In-season prediction with an incomplete season

### 5.1 The architecture: date-stamped models

**Train a separate model per forecast origin date.** Do not train one model and feed it partially
missing features — tree models will route missingness in ways that are not calibrated to that lead time,
and the uncertainty calibration must be lead-time-specific.

Recommended origins (US soybean):
`pre-plant (Apr 1)`, `May 1`, `Jun 1`, `Jul 1`, `Jul 15`, `Aug 1`, `Aug 15`, `Sep 1`, `Oct 1`.

At each origin `d`, features come from three sources:

1. **Observed weather** from planting (or Jan 1) to `d`. Real, complete.
2. **Short-range NWP** for `d` to `d+15`. Note the sobering evidence: *"inclusion of 7 to 14 day
   weather forecast did not improve end-of-season yield prediction accuracy"* for APSIM maize/soybean
   (Togliatti et al.) `[S]`, and CRPS skill for climate variables is *"normally positive for about the
   first 14 days, and after that skill scores become very close to zero"* `[S]`. **Include it; expect
   little.** It is cheap and it helps most for the *near-term* stress that is currently in progress.
3. **The remaining season**, filled by one of:
   - **Climatology ensemble (recommended default).** Take the last 30 years of observed weather for that
     location for the remaining calendar days → 30 "analog completions." Compute features for each,
     predict, and combine. This gives a *distribution* over the remaining season, which is the honest
     representation of what we don't know.
   - **Analog years conditioned on state.** Restrict the 30 analogs by ENSO phase and/or current
     soil-moisture anomaly. Modest expected gain; test it, don't assume it.
   - **Seasonal (S2S/GCM) ensemble** (e.g. CFSv2, ECMWF SEAS5) members as completions. The published
     framing: seasonal forecast systems initialized at different lead times are *compared against
     climatology alone* to examine predictive skill by lead time `[S]`. Treat climatology as the
     benchmark to beat, and only ship seasonal-forecast completion if it beats climatology in V1
     backtesting. Australian wheat work found ENSO-analogue and GCM-derived approaches broadly
     comparable `[S]`.
   - **Two "bracket" scenarios** for customer communication: a favorable (75th percentile) and adverse
     (25th percentile) completion, alongside the central forecast. Customers understand scenarios better
     than intervals.

**Combining ensemble completions with the uncertainty machinery.** For each analog completion `m`,
produce the CQR interval; then the final interval is the union-then-recalibrate:
`lo = quantile_{α/2}( {lo_m} ∪ {point_m} )`, `hi = quantile_{1−α/2}( ... )`, followed by a second
conformal correction fit on historical in-season backtests *at that same origin date*. This composes
weather uncertainty with model uncertainty. Verify coverage empirically; do not assume the composition
is calibrated.

### 5.2 Feature-availability calendar (enforce in code)

Build a machine-readable manifest, and make the feature builder raise on violation:

```yaml
- feature: mean_temp_R5_R7
  available_from: end_of_R7 + 1d          # otherwise must be climatology-completed
- feature: planting_date_doy
  available_from: planting + 1d
- feature: county_yield_prior_year
  available_from: Jan 15 of current year
- feature: nass_condition_rating
  available_from: weekly, lag 3d
  forbidden_at_scale: county              # if the rating is derived from the target unit
```

A single unenforced availability rule is how in-season models get accidentally trained on
harvest-time information and then fail in production. Unit-test this.

### 5.3 Reported skill-vs-date evidence

| Source | Crop / scale | Skill vs date |
|---|---|---|
| County-level CNN-LSTM (Sensors 19:4363) `[S]` | US county soybean | R² rises **rapidly from early April to end of August**; after the pod-setting stage both validation and test R² stay **close to maximum and stable**; "accurate in-season forecasts can be achieved as early as the end of pod-setting." Test RMSE **0.263 t/ha (3.91 bu/ac)** around **30 Aug** |
| County XGBoost framework `[S]` | US county soybean | Six evaluated origins: **Jun 2, Jul 4, Aug 5, Sep 14, Oct 16, Nov 17**; final test **R² 0.82** |
| Schwalbert et al. 2020, Agric. For. Meteorol. 284:107886 `[S]` | Municipality soybean, Rio Grande do Sul, Brazil | **MAE 0.24 Mg/ha (3.6 bu/ac) at DOY 64**; **MAE 0.42 Mg/ha (6.2 bu/ac) ~70 days before harvest**; ensemble RMSE ~0.35 t/ha (5.2 bu/ac). **LSTM beat RF and OLS at every origin except the earliest (DOY 16), where multivariate OLS won** |
| Paudel et al. 2021 `[S]` | 5 crops, EU NUTS2/3 | Optimized ML significantly better nRMSE than baseline **at 60 days before harvest** |
| Seasonal-forecast literature `[S]` | Wheat | Early-season (Apr–Jun) ~**12 % RMSE**, improving from July as lead time shortens |
| "Skillful U.S. Soy Yield Forecasts at Presowing Lead Times," AIES 2:AIES-D-21-0009 `[S]` | US soy | Claims genuine **pre-sowing** skill. Numbers not verified `[X]` — retrieve this paper; it is the most directly relevant published result for our earliest product tier |

**Do not cite** the figures "soybean early-season R² ~0.48 climbing to ~0.72, MAE 1.0–1.4 bu/ac" that
surface in search around this topic — they trace to a **commercial Substack newsletter**, not a
peer-reviewed source, and an MAE of 1.0–1.4 bu/ac for early-season county soybean yield is not credible
(it is below the reporting noise of the target itself). Flagged here specifically because it is the kind
of number that gets copied into a sales deck.

**The two robust generalizations we can rely on:**

1. **Skill rises steeply through pod set (R3–R5) and plateaus after it.** For US soybean that means the
   big jump happens between **1 July and 1 September**, and there is comparatively little to gain after
   ~1 September. This is agronomically expected: seed number is set at R3–R5 and August soil moisture and
   August Tmax are the dominant drivers `[S]`.
2. **The simplest model wins earliest.** At the earliest origins, when almost all features are
   climatology, a linear model beat LSTM `[S]`. Because at long lead the only real information is
   trend + soil + location, and a flexible model just overfits the climatological noise. **Our
   pre-plant and June products should be regularized linear or trend-plus-soil models, and we should
   switch to GBM only from about 1 July.** Encode this as a per-date model-class choice validated by V1,
   not as a fixed architecture.

---

## 6. Interpretability and the credibility package

### 6.1 Methods, and their traps

**SHAP (TreeExplainer)** — the default. Cheap and exact for tree ensembles. Deliver:
- global bar plot (mean |SHAP|) with **features grouped** (all temperature-bin features as one group,
  all water-balance features as one group) — ungrouped importances are meaningless when 40 collinear
  bins split the credit
- beeswarm plot for direction of effect
- SHAP dependence plots for the marquee variables, used as **agronomic acceptance tests** (§6.2)
- local waterfall plots for individual field/county predictions — this is the artifact that wins
  agronomist trust in a sales meeting

Precedent: SHAP was used exactly this way on US maize/soybean yield-monitor data to quantify how
weather, soil and terrain drive yield variability `[S]`.

**Permutation importance** — run it **grouped and on a held-out year**, not on training data. With
correlated weather features, single-feature permutation understates importance (the model recovers the
signal from a correlated twin). Group permutation is the honest version.

**Partial dependence / ALE** — prefer **ALE**, because PDP averages over an implausible joint
distribution (it will evaluate "45 °C with 900 mm rain") and correlated weather features make that
routine. ALE stays within the observed data manifold.

**The published caution, which we should take seriously.** *"Crop yield prediction via explainable AI
and interpretable machine learning: Dangers of black box models for evaluating climate change impacts
on crop yield"* (Agric. For. Meteorol. 2023) `[S]` — an ML model can attain good predictive accuracy
while attributing it to agronomically wrong variables, especially with collinear weather inputs, and
XAI on such a model produces confidently wrong explanations. Interpretability output is a **hypothesis
about the model**, not a discovery about the crop.

### 6.2 Agronomic acceptance tests (make these CI gates)

Before any model ships, it must pass:

| Test | Expected behavior | Source |
|---|---|---|
| Temperature response | Yield SHAP declines above ~**30 °C** for soybean, with a steeper slope above than the incline below | Schlenker & Roberts `[S]` |
| Stage sensitivity | Water deficit at **R3–R5** has larger yield impact than at V-stages | agronomic consensus; APSIM drought-stress dominance `[S]` |
| VPD | High VPD in the ~60–90 DAS window reduces yield | Lobell et al. `[S]` |
| AWC × drought | Yield penalty from water deficit is **larger on low-AWC soils** (interaction present, correct sign) | water balance |
| Irrigation | Water-deficit features have near-zero effect on irrigated units | trivially true; a good leakage detector |
| Oil × temperature | Oil rises with seed-fill mean temperature, peaking near **28 °C** | Piper & Boote `[S]` |
| Fatty acids | Oleic ↑ and linoleic + linolenic ↓ with rising seed-fill day temperature; palmitic and stearic ~flat | Wolf et al.; Gibson & Mullen `[S]` |
| Day/night | Higher **night** temperature at high day temperature moves oleic ↓ / linoleic ↑ | `[S]` |
| Protein × water stress | Water deficit during seed fill raises protein concentration | Rotundo & Westgate `[S]` |
| Protein–oil | Predicted protein and oil are **negatively correlated** | Assefa; Chiozza `[S]` |
| Planting date | Delayed planting reduces oil and yield, strongest at 40–45 °N | Assefa `[S]` |
| Monotonic sanity | No implausible non-monotonicity in AWC → yield | — |

A model that predicts well but fails these is a model that has learned a spatial or trend shortcut.
Where a constraint is unambiguous, **enforce it** with LightGBM `monotone_constraints` (e.g. AWC ↑ →
yield ↑ at fixed weather) rather than hoping for it.

### 6.3 What a commercially credible model card + validation report must contain

Following Mitchell et al. 2019 (*Model Cards for Model Reporting*), whose sections are **Model Details,
Intended Use, Factors, Metrics, Evaluation Data, Training Data, Quantitative Analyses, Ethical
Considerations, Caveats and Recommendations** `[S]`, with the key discipline being **disaggregated
reporting of performance across subgroups** `[S]`.

Our instantiation:

1. **Model details** — version, git SHA, training date, model class, hyperparameters, exact feature
   list with data sources and versions (Daymet vX, gSSURGO release, NASS revision date), training
   window, retraining cadence.
2. **Intended use** — target crop, geography, spatial scale, forecast origins, decision context.
   **Explicit non-uses:** e.g. "not validated for irrigated Delta production," "not for crop insurance
   loss adjudication," "not for individual-field agronomic prescriptions," "not for geographies outside
   the training footprint," "protein predictions are not commercially actionable."
3. **Factors** — the disaggregations we report: state/CRD, irrigated vs rainfed, maturity group band,
   soil AWC tercile, forecast origin date, and **year type** (normal / drought / wet).
4. **Metrics** — RMSE, MAE, bias, R², **skill score vs B2**, PI coverage, mean interval width, CRPS.
   Definitions written out, including which scale (raw vs anomaly).
5. **Evaluation data** — the V1/V2/V3/V4 protocols spelled out, with the exact held-out years and
   regions, sample counts per cell, and target-data provenance and its own error (NASS county yields are
   survey estimates with revision history; yield-monitor data need cleaning rules documented).
6. **Training data** — counts, spatial and temporal footprint, class/geography imbalance, known gaps,
   the cleaning and outlier rules, and **the synthetic-data disclosure of §7 if applicable**.
7. **Quantitative analyses** — per-year and per-region tables (not just pooled), the skill-vs-date
   curve, calibration/reliability diagram, PI coverage by subgroup, error distribution with tails, the
   worst 5 % of predictions characterized, and residual maps to expose spatial structure.
8. **Limitations and failure modes** — extrapolation behavior, response to unprecedented weather, the
   known degradation in V4, the irreducible genotype variance for composition, sensitivity to planting
   date and MG input errors, and behavior when phenology is out of the model's calibrated MG range.
9. **Caveats and recommendations** — plain-language statement of what the intervals do and do not
   guarantee (§4.3), the retraining and monitoring plan, and drift alarms.

**Plus a separate, standing validation report** regenerated automatically each retrain, versioned, and
diffable against the previous release, so a partner can see whether we got better or worse.

---

## 7. Synthetic / simulated bootstrap data — how, and what to tell customers

### 7.1 Legitimate uses

A synthetic dataset is worth building for: pipeline and schema plumbing; unit and property tests of the
feature builder (does the phenology model behave at MG 0 and MG 8? at replant?); **testing whether the
uncertainty machinery achieves nominal coverage when we control the truth**; verifying that the
validation harness detects leakage we deliberately inject; load and cost estimation; and prior
elicitation / design-of-experiments for what real data to buy first.

It is **not** worth building to estimate accuracy, and any accuracy measured on it is meaningless
(§7.4).

### 7.2 Generator design (agronomically faithful)

**Rule 1: never synthesize the inputs.** Use *real* weather (Daymet / gridMET / PRISM), *real* soil
(gSSURGO / SoilGrids), *real* geography. Synthesize only the response. This preserves the true
covariance structure of the predictors — which is the single most important property, because the
correlation between temperature and VPD and radiation and drought is exactly what makes this problem
hard and what makes naive interpretability wrong (§6.1).

**Rule 2: generate y from published response functions, with published effect sizes.**

```
# --- Phenology -------------------------------------------------------------
stages = photothermal_phenology(daily_T, photoperiod(lat, doy), maturity_group)
   # SoySim-style: development rate = f(T) * g(photoperiod), MG-specific photoperiod
   # sensitivity and optimum photoperiod (low MG = less sensitive, higher optimum). [S]
   # Inject the model's own error: add N(0, 6 d) to R1, N(0, 7 d) to R5/R7 to mimic the
   # published RMSE of 5.8 / 6.8 / 7.2 days. [S]  Omitting this makes downstream features
   # unrealistically clean.

# --- Yield ----------------------------------------------------------------
Y_pot   = f(radiation_R1_R5, MG, lat)                     # sink-capacity potential
f_temp  = beta_response(T_mean, T_base=10, T_opt=26, T_max=40)
          # T_opt 26 C for indeterminate, 23 C for determinate  [S]
f_heat  = 1 - k_heat * KDD_30_R3_R5 / scale
          # soybean threshold 30 C, asymmetric steep decline above it  [S]
f_water = 1 - sum_over_stages( Ky_s * (1 - ETa_s/ETc_s) )
          # FAO-33 form; Ky largest at R3-R5, then R5-R7, small at V stages
f_vpd   = 1 - k_vpd * excess_VPD_60_90_DAS                # Lobell et al.  [S]
Y = Y_pot * f_temp * f_heat * f_water * f_vpd
Y = Y * (1 + trend_rate * (year - y0)) + county_offset(soil, lat)

# --- Oil (% dry basis) ----------------------------------------------------
oil = a0 + a1*T_R3_R7 + a2*T_R3_R7^2            # quadratic, max near 28 C  [S]
      + b1*planting_delay_weeks * lat_north_flag  # -0.007 to -0.06 %/week at 40-45 N [S]
      + b2*water_deficit_R5_R7                    # oil accumulation suppressed [S]
      + genotype_effect                           # UNOBSERVED -> becomes irreducible noise
# CALIBRATE the coefficients so that temperature alone yields adjusted R2 ~= 0.24  [S]
#   i.e. deliberately make oil mostly unexplained. This is the point.

# --- Protein (%) ----------------------------------------------------------
protein = c0 - rho * (oil - mean_oil)             # inverse protein-oil relation  [S]
        + d1*water_deficit_R5_R7                  # water stress RAISES protein conc.  [S]
        + d2*N_supply_proxy                       # N supply raises protein  [S]
        + genotype_effect_p
# CALIBRATE so weather+soil explains R2 ~= 0.09.  [S]  Do not make protein predictable.

# --- Fatty acids (5-part composition) ------------------------------------
# Work in ALR space, invert with softmax so the parts close to 100.
z_oleic     +=  e1 * (Tmax_R5_R8 - 25)  - e2 * (Tmin_R5_R8 - 20) * (Tmax_R5_R8 > 30)
z_linolenic += -e3 * (Tmax_R5_R8 - 25)
z_linoleic  += -e4 * (Tmax_R5_R8 - 25)  + e5 * (Tmin_R5_R8 - 20) * (Tmax_R5_R8 > 30)
z_palmitic  +=  ~0 ; z_stearic += ~0              # temperature-insensitive  [S]
# The (Tmax > 30) * Tmin terms reproduce the published day x night reversal:
# at 30 C day, raising night temperature DECREASED oleic and INCREASED linoleic.  [S]
# Add a cultivar x temperature interaction term: the interaction is significant for
# protein, oil, palmitic, oleic and linolenic.  [S]
fa = softmax_inverse_alr(z) * 100
```

**Rule 3: make the noise realistic, which mostly means making it correlated.** iid Gaussian noise is
the classic mistake — it produces a dataset on which any decent model looks superb, because averaging
kills iid noise.

- **Spatially correlated residuals**: Gaussian random field with ~150–300 km range.
- **Year effects**: a shared annual shock, so all units in a year move together (this is what destroys
  apparent skill under leave-one-year-out and is the whole reason V1 exists).
- **Unobserved genotype**: draw a per-(variety × trait) effect and *do not expose it as a feature*. Size
  it from the significant cultivar × temperature interactions `[S]` and from the fact that the observed
  spread in the Uniform Tests is far larger than controlled manipulation explains `[S]`.
- **Unobserved management**: per-field offsets for fertility, pest pressure, stand loss, hail.
- **Measurement error on the target**: oil/protein by NIR carries a standard error of prediction of
  roughly 0.3–0.5 pp (oil) and 0.5+ pp (protein) — verify against our own lab's calibration statistics.
  County yields are survey estimates with revisions. Add both.
- **Missingness and lateness**, mimicking real feeds: missing planting dates, unknown MG, late soil
  data, revised NASS values.

**Rule 4: better than hand-rolling — use CROPGRO-Soybean (DSSAT) or APSIM as the generator.** CROPGRO
already simulates seed oil and protein concentration from a C and N balance with cultivar-specific
targets plus a temperature effect, at hourly temperature/photoperiod resolution `[S]`, with reported
validation of yield R² 0.81 / nRMSE 10.8 % `[S]`. That is a far more defensible synthetic response
surface than any equation set we write, it doubles as the hybrid-feature source for §9.6, and it makes
the assumption set auditable by an agronomist. **Cost: CROPGRO does not simulate the fatty-acid
profile**, so fatty acids still need the published-response-function layer above.

### 7.3 Test the generator before trusting it

Marginal and joint distributions of simulated yield/oil/protein must match published US distributions
(means, SDs, protein–oil correlation ≈ −0.6 to −0.7, latitude gradients in oil, MG × latitude
structure). If your synthetic oil has SD 3 pp when US commercial oil SD is ~1 pp, every downstream
conclusion is void. Write these as assertions.

### 7.4 The honest caveats to state to customers — non-negotiable language

If any model touched by synthetic data is ever shown externally, these statements must accompany it:

1. **"This model was trained on simulated data. It has not been validated against observed
   soybean yields or observed seed composition. No accuracy figure should be inferred from it."**
2. **The circularity, stated plainly:** *"Accuracy measured on simulated data measures how well the
   model recovers the equations we used to create the data. It is a test of our software, not of
   agronomic reality. It cannot indicate real-world accuracy in either direction."*
3. **The optimism direction is known:** a simulator omits everything nobody has parameterized — pest and
   disease pressure, hail, replant, compaction, weed competition, cultivar turnover, management
   heterogeneity, and target measurement error. **Synthetic performance is therefore systematically
   optimistic**, and the gap is not estimable from the synthetic data.
4. **Named provenance for every response function**, so a customer's agronomist can audit the
   assumptions (Schlenker & Roberts 2009 for the temperature threshold; Piper & Boote 1999 for oil ×
   temperature; Rotundo & Westgate 2009 for stress × composition; Wolf et al. 1982 / Gibson & Mullen
   1996 for fatty acid × temperature; SoySim for phenology).
5. **Never present synthetic-derived intervals as calibrated.** Coverage on synthetic data proves the
   conformal code is correct, nothing more.
6. **State the exit criteria:** the minimum real dataset needed before any accuracy claim (§9.5), and
   commit to replacing every synthetic-derived number with a backtested one.
7. **Do not blend silently.** If real and synthetic data are mixed, disclose the ratio, and always report
   metrics on **real held-out data only**.

A useful internal rule: **any number that could appear in a contract must be traceable to a held-out
real year.** If it cannot, it does not leave the building.

---

## 8. Honest limitations

Written to be reused directly in the model card and to constrain the sales narrative.

1. **Weather explains a minority of interannual yield variance in our core market.** Up to ~30 % of
   year-to-year soybean yield variability in MO/IA/IL is explained by temperature plus precipitation
   `[S]`. High reported R² values in the literature are dominated by spatial and trend variance. We can
   deliver real value on top of a trend baseline, but we cannot deliver a precise yield number.
2. **Protein is not predictable from weather and soil.** R² ≈ 0.09 with weather+soil+season `[S]`;
   adjusted R² ≈ 0.003 from seed-fill temperature `[S]`. Report protein as a *directional* indicator or
   not at all. High published protein R² values (e.g. 79 %) come from models that include genotype `[S]`.
3. **Oil is modestly predictable.** R² ≈ 0.39 from weather+soil+season `[S]`; ~0.24 from temperature
   alone `[S]`; ~0.5 with satellite imagery `[S]`. Expect ~1 pp RMSE. For a crush plant this may be
   useful for logistics and blending; it is not a specification guarantee.
4. **Fatty acids have no published field-scale weather-only benchmark.** The mechanistic response is
   well established but the evidence is largely controlled-environment `[S]`, and the cultivar ×
   temperature interaction is significant `[S]`. Palmitic and stearic are essentially
   temperature-invariant `[S]` — we should decline to predict them rather than manufacture skill. Treat
   the whole fatty-acid module as **research-grade until we have backtested it on real data**.
5. **Genotype is the ceiling.** Composition is primarily genetically determined; environment modulates
   it. Without variety identity we cannot cross that ceiling. This is the strongest argument for
   acquiring variety data as a product input.
6. **Field scale is harder than county scale, not easier.** County aggregation averages away the
   idiosyncratic management and sampling noise that dominates single-field outcomes. Beware published
   field-scale R² values that are really within-field spatial R² (§1.9).
7. **Phenology inputs carry ~6–7 days of error** `[S]`, and unknown planting date or maturity group
   degrades this further. Stage-aligned features inherit that error.
8. **New geographies degrade materially.** Expect the largest drop under V4 (unseen year × unseen
   region). Spatial leakage in random CV has been measured at 0.22–0.38 R² units in a
   leave-one-country-out setting `[S]`.
9. **Unprecedented weather is extrapolation.** Tree ensembles cannot extrapolate; they will predict the
   edge of their training range and report a confident interval. Ship an explicit
   out-of-distribution flag (Mahalanobis distance or an isolation-forest score in feature space) and
   widen or refuse.
10. **Conformal guarantees rest on exchangeability, which is violated here** `[S]`. Empirical held-out
    coverage is the only defensible claim (§4.3).
11. **The target itself is noisy.** NASS county yields are survey estimates subject to revision;
    yield-monitor data need substantial cleaning. Model RMSE cannot go below target measurement error,
    and part of our reported RMSE is *their* error.
12. **Detrending is a modeling choice with real consequences** and an inherent circular-dependency bias
    `[S]`. We will report results both with and without trend handling so the effect is visible.
13. **Short-range weather forecasts add little.** 7–14 day forecasts did not improve end-of-season yield
    prediction `[S]`; usable NWP skill largely expires around 14 days `[S]`. Do not promise "forecast-
    powered" precision.

### Claims we must not make

- ❌ "R² of 0.82 for soybean yield" without stating scale (county), protocol (which held-out years),
  and target scale (raw vs detrended).
- ❌ Any accuracy figure derived from synthetic data.
- ❌ "Predicts protein content" as a headline capability.
- ❌ Fatty-acid accuracy figures before we have backtested on real data.
- ❌ "Statistically guaranteed 80 % prediction intervals."
- ❌ Field-level accuracy quoted from county-level validation, or year-ahead accuracy quoted from
  within-field spatial validation.
- ❌ Any number carrying an `[S]` or `[X]` marker in this document until it has been verified.

---

## 9. RECOMMENDED ARCHITECTURE

### 9.1 Targets

| ID | Target | Units | Modeled as | Priority |
|---|---|---|---|---|
| `Y_yield` | Seed yield | bu/ac (store kg/ha) | **Anomaly** vs causal trend+level (§2.6), back-transformed for delivery | P0 |
| `Y_oil` | Seed oil concentration | % dry basis (13 % moisture standard) | Direct, with quadratic temperature prior | P0 |
| `Y_protein` | Seed protein concentration | % | Direct; **shipped as directional/low-confidence only** | P1 |
| `Y_fa` | Palmitic, stearic, oleic, linoleic, linolenic | % of total FA | **ALR-transformed 4-vector**, softmax-inverted to the simplex (§2.7) | P2 (research) |
| `Y_oilyield` | Oil yield per area | kg oil/ha | **Derived** = `Y_yield × Y_oil`, with uncertainty by Monte Carlo over the joint | P0 (often the real commercial target) |

`Y_oilyield` is worth calling out: for a crush partner it is more decision-relevant than either
component, and because yield and oil errors are only weakly correlated, its *relative* uncertainty is
often better than oil concentration's alone. Compute it from joint samples, never by multiplying point
estimates and adding relative errors.

### 9.2 Models (named, per target, per forecast date)

| Role | Model | Config notes |
|---|---|---|
| **Primary, yield, origins ≥ Jul 1** | **LightGBM** `objective="regression_l2"` | ~600–2000 trees, `learning_rate` 0.02–0.05, `num_leaves` 31–127, `min_data_in_leaf` ≥ 50, `feature_fraction` 0.6, `bagging_fraction` 0.8, `lambda_l2` 1–10, `monotone_constraints` on AWC and irrigation. LightGBM over XGBoost for categorical handling and speed; XGBoost as a challenger (a like-for-like study found XGBoost best among 8 learners at county soybean scale `[S]`, so bake off, don't assume) |
| **Primary, yield, origins ≤ Jun 1 (incl. pre-plant)** | **ElasticNet / Ridge on the binned-temperature + water-balance + soil design matrix** | Because at long lead the flexible model overfits climatology; a linear model beat LSTM at the earliest origin in published in-season work `[S]` |
| **Benchmark (always trained, always reported)** | **Ridge on Schlenker-Roberts design**: 3 °C temperature bins × stage window + quadratic precipitation + water deficit + soil + lat/lon spline | The interpretable, extrapolation-safer model. If GBM's V1 advantage over this is <5 % RMSE, ship this |
| **Uncertainty** | **LightGBM `objective="quantile"`** at α/2 and 1−α/2, conformalized by grouped weighted CQR (§4.4) | One pair per target per origin date |
| **Trend / level** | Partially-pooled mixed model: county intercept + CRD-level year slope, refit per origin year on years ≤ T−1 | `statsmodels` MixedLM or a hand-rolled shrinkage estimator |
| **Ensemble** | Ridge stack (non-negative weights, fit on out-of-fold V1 predictions) over {LightGBM, ElasticNet, benchmark Ridge} | Ensembles were the most precise models at RRMSE 9.5 % in published corn work, using blocked sequential out-of-bag predictions `[S]` — use the same blocking |
| **Composition (oil, protein)** | **LightGBM, small** (`num_leaves` 15, `min_data_in_leaf` 100) **and** a **GAM** (`pygam`) with an explicit quadratic in seed-fill temperature | Deliberately low-capacity: Chiozza found 4 predictors matched much richer models under LOYO CV `[S]`. The GAM is also the presentation artifact for agronomists |
| **Fatty acids** | Multi-output LightGBM on the 4 ALR components + Gaussian copula for joint sampling | Research tier |
| **Challengers (bake off, do not default to)** | NGBoost (score on CRPS/NLL); 1D-CNN or GRU on the daily sequence; TabPFN-class models for small-n composition | Deep sequence models only earn their place at county scale with many years of data — CNN-RNN reached 8 % RMSE for soybean `[S]`, but on 13 states × many years. At our likely starting n, GBM wins |

**Explicitly not recommended for v1:** transformers, graph neural networks, and CNNs on satellite
histograms. They are where the published headline numbers are, but they need data volume we will not
have at launch, and they make the interpretability and uncertainty story much harder. Revisit at
> ~20,000 unit-years.

### 9.3 Feature list (concrete)

**Block A — Weather, per stage window** `{VE-R1, R1-R3, R3-R5, R5-R7, R7-R8}` **and per calendar month**
`{May..Oct}`:

```
tmean, tmax_mean, tmin_mean, tmax_p90, tmin_p90
gdd_base10_cap30
kdd_30                       # sum max(0, Tmax - 30)     [soybean threshold]  [S]
tbin_[0-5,5-8,...,38-41,41+] # 3 C bins, time-in-bin from sine interpolation  [S]
n_days_tmax_gt_30, gt_33, gt_35
n_days_tmin_gt_22            # night stress; needed for fatty acids            [S]
precip_total, precip_p95_day, n_days_precip_gt_25mm, max_dry_spell_days
srad_total, srad_mean
photothermal_quotient        # srad / tmean
vpd_mean, vpd_max_mean, vpd_p90, vpd_excess_2kPa
et0_total, etc_total, eta_total
relative_et                  # eta/etc                                          [S]
water_deficit                # etc - eta
n_days_asw_lt_50pct, min_relative_soil_water
n_days_compound_hot_dry      # Tmax>32 AND asw<50%                              [S]
```

**Block B — Season-level weather:**
```
preseason_precip_oct_apr      # stored profile water
season_total_gdd, season_kdd_30
vpd_mean_60_90_das            # Lobell window                                   [S]
aug_tmax_mean, aug_sep_soil_moisture   # dominant county drivers                [S]
anomaly versions of the above vs the unit's 30-yr climate normal
```
Anomaly-vs-normal encoding matters: it lets one model serve Iowa and Mississippi without memorizing
place.

**Block C — Soil** (gSSURGO / SoilGrids, following Khaki's 11 × 6 template `[P]`):
```
awc_0_30, awc_0_100, awc_0_150
sand/silt/clay/soc/ph/cec/bulk_density  x  {0-5,5-15,15-30,30-60,60-100,100-200 cm}
depth_to_restrictive_layer, drainage_class, hydrologic_group
```

**Block D — Location / structure:**
```
latitude, longitude
rbf_spatial_1..k              # k ~ 20 smooth spatial basis functions (NOT county one-hot)
elevation; slope, aspect, twi, curvature   [field scale only]
climate_normals: tmean_jja_30yr, precip_growing_30yr, vpd_jja_30yr, aridity_index
```

**Block E — Phenology / management:**
```
planting_date_doy, planting_date_dev_from_county_median
maturity_group (continuous)
dap_R1, dap_R3, dap_R5, dap_R7
duration_R1_R5, duration_R5_R7
photoperiod_at_R1, mean_photoperiod_R1_R5
irrigated_flag
row_spacing, seeding_rate, tillage, previous_crop      [if available]
phenology_confidence_flag, replant_flag
```

**Block F — Trend / level (causal, from years ≤ T−1 only):**
```
expected_yield_trend         # county trend projection, fit on <= T-1            [S]
county_mean_yield_prior5, prior10
crd_trend_slope
# FORBIDDEN: raw `year` as a numeric tree feature (§2.6)
```

**Block G — In-season bookkeeping:**
```
forecast_origin_doy, fraction_of_season_observed, fraction_features_from_climatology
analog_ensemble_spread       # spread of predictions across the 30 climatology completions
```
`analog_ensemble_spread` is a genuinely useful feature *for the uncertainty model*: it directly measures
how much of the answer is still unknown weather.

**Composition models use only:** `latitude, longitude, planting_date_doy, maturity_group,
tmean_planting_to_R1, tmean_R3_R7 (+ quadratic), tmax_mean_R5_R8, tmin_mean_R5_R8,
n_days_tmax_gt_33_R5_R8, n_days_tmin_gt_22_R5_R8, water_deficit_R5_R7, relative_et_R5_R7,
duration_R5_R7, awc_0_100, irrigated_flag, soil_S_proxy` — ~16 features, per Chiozza's parsimony
finding `[S]`. Grow only if LOYO CV justifies it.

### 9.4 Validation protocol (the contract)

- **Select** hyperparameters and features on **V2 (LOYO)** with a nested inner temporal split.
- **Report** on **V1 (forward-chaining by year)**, per year, never only pooled.
- **Also report V3 (blocked spatial, leave-one-CRD-out)** and **V4 (leave-one-year-and-region-out)**.
- **Always two scales:** raw yield and anomaly (§3.3).
- **Always vs B2** (county mean + causal trend); headline the **skill score vs B2**, and include **B5**
  (NASS forecast) wherever comparable.
- **Composition also vs B3c** (the Piper & Boote quadratic).
- **Interval quality is a first-class metric:** empirical coverage, mean width, CRPS, pinball loss,
  broken out by year and region, with **worst-year coverage published**.
- **Agronomic acceptance tests (§6.2) are CI gates**, not a report section.
- **Minimum data before any external accuracy claim:** ≥ 8 distinct years (so V1 has ≥ 5 test years
  including at least one drought year — 2012-class) × ≥ 200 spatial units, with the target measured
  consistently. Below that, the confidence interval on our RMSE estimate is wider than the differences
  we would be claiming.

**Verification checklist before any `[S]` number is quoted externally** — retrieve full text and confirm:
1. You et al. 2017 AAAI — per-method soybean RMSE table and whether 5.83/5.55 bu/ac are on raw yields.
2. Khaki, Wang & Archontoulis 2020 — absolute soybean RMSE in bu/ac and the denominator average yield.
3. County XGBoost framework 2023 — whether R² 0.82 is on raw or detrended yields.
4. Chiozza et al. 2025 — actual RMSE for oil and protein under LOYO (our closest benchmark).
5. The 235-field / 13-state study — full citation and whether R² 0.56/0.39/0.09 are cross-validated or
   in-sample. **This is the single most important number in the brief and it is currently unverified.**
6. "Skillful U.S. Soy Yield Forecasts at Presowing Lead Times," AIES 2023 — the actual skill-vs-lead
   numbers.
7. Roberts et al. 2017 ERL — out-of-sample R² for statistical vs process vs combined.
8. Whether any published field-scale weather-only fatty-acid model exists at all.

### 9.5 Expected realistic accuracy (what we can honestly quote, once backtested)

These are **pre-registered expectations**, to be replaced by measured numbers. They are deliberately
conservative and are derived by discounting published figures for: our smaller dataset, no satellite
imagery in v1, honest V1/V4 protocols, and no genotype information.

**Soybean yield, county scale, forward-chaining by year:**

| Forecast origin | RMSE (bu/ac) | RMSE (t/ha) | R² raw yield | R² anomaly | Skill vs B2 |
|---|---|---|---|---|---|
| Pre-plant (Apr 1) | 7.5–10 | 0.50–0.67 | 0.55–0.68 | 0.02–0.12 | 0–8 % |
| Jun 1 | 7–9 | 0.47–0.61 | 0.58–0.70 | 0.05–0.18 | 3–14 % |
| Jul 1 | 6–8 | 0.40–0.54 | 0.62–0.74 | 0.12–0.28 | 8–22 % |
| **Aug 1** | **5–7** | **0.34–0.47** | **0.66–0.78** | **0.20–0.35** | **15–30 %** |
| Sep 1 | 4.5–6.5 | 0.30–0.44 | 0.68–0.80 | 0.25–0.40 | 20–35 % |
| End of season (all weather observed) | 4–6 | 0.27–0.40 | 0.70–0.82 | 0.28–0.45 | 25–40 % |

Sanity anchors: CNN-RNN reached 8 % of average yield ≈ 4 bu/ac for soybean at end of season across 13
states `[S]`; county XGBoost reached 0.246 t/ha = 3.66 bu/ac `[S]`; the deep GP LSTM reached
5.55 bu/ac `[S]`; CNN-LSTM reached 0.263 t/ha ≈ 3.9 bu/ac at ~30 Aug `[S]`. Our end-of-season band
(4–6 bu/ac) sits at or slightly worse than the published best, which is the right place to be for a v1
without satellite imagery. The **anomaly R²** column is bounded by the ~30 % of interannual variance
that temperature and precipitation explain in the core Corn Belt `[S]` — we should not project above
~0.45 there, and we should expect the low end in the Corn Belt and the high end in the Southeast, where
climate explains >60 % `[S]`.

**Soybean yield, field scale (weather + soil + terrain + management, no in-season imagery):**
RMSE **8–12 bu/ac**, anomaly R² **0.10–0.30**. Materially worse than county scale, because aggregation
no longer averages away idiosyncratic management and measurement noise. Quote separately and never
substitute county numbers.

**Seed composition, forward-chaining by year:**

| Target | RMSE | R² | Basis |
|---|---|---|---|
| **Oil** (% db) | **0.9–1.3 pp** | **0.30–0.45** | vs 0.39 from weather+soil+season `[S]`, 0.24 from temperature alone `[S]`, ~1.04 pp with imagery `[S]` |
| **Protein** (%) | **1.3–1.9 pp** | **0.05–0.25** | vs 0.09 `[S]`; ~1.80 pp with imagery `[S]`. **Ship as directional only** |
| **Oil yield** (kg/ha) | 8–13 % rRMSE | 0.60–0.78 | derived; benefits from partially independent errors |
| Oleic (%) | 1.5–2.5 pp (base ~22–24 %) | 0.15–0.35 | mechanism strong `[S]`, **no field benchmark** — provisional |
| Linoleic (%) | 1.5–2.5 pp (base ~52–55 %) | 0.15–0.35 | provisional |
| Linolenic (%) | 0.5–0.8 pp (base ~6–8 %) | 0.20–0.40 | most temperature-responsive `[S]`; provisional |
| Palmitic, stearic | — | **< 0.10** | **Do not ship.** Temperature-invariant `[S]` |

**Uncertainty target:** nominal 80 % intervals achieving **75–85 % empirical coverage in every held-out
year** and **70–90 % in every region**, with mean width ≈ 1.6–2.2 × RMSE at county scale. Publish
worst-year coverage.

**How to phrase it externally (template):**

> "Across N held-out years and M counties, backtested with strict forward-chaining (each year predicted
> using only prior years), our 1 August soybean yield forecast achieved an RMSE of X bu/ac, reducing
> error by Y % relative to a county-mean-plus-trend baseline. Our 80 % prediction intervals contained
> the realized yield in Z % of cases (worst single year: W %). Seed oil concentration was predicted with
> an RMSE of V percentage points. We do not currently offer a commercially actionable protein forecast,
> because seed protein is primarily genetically determined and is poorly predicted from weather and soil
> alone."

That paragraph is defensible, specific, and includes the limitation. It will win more technical
partners than a bare R².

### 9.6 Roadmap beyond v1

1. **Satellite imagery** (Sentinel-2 / HLS / PlanetScope VIs by stage window). The published gains are
   large: county soybean at R² 0.79 with Sentinel-1/2 + Daymet `[S]`; composition at R² 0.53 oil /
   0.36 protein from imagery alone `[S]`. Highest-value single addition.
2. **Hybrid crop model features.** Add APSIM- or CROPGRO-simulated drought stress, water-table depth and
   phenology as ML features: **7–20 % RMSE reduction reported**, with soil-moisture-related simulated
   variables the most influential `[S]`. Also validated by the process+statistical combination beating
   either alone `[S]`. CROPGRO additionally simulates oil and protein `[S]`, so this is the single
   highest-leverage move for the *composition* targets specifically.
3. **Variety / genotype identity.** The only route past the composition ceiling (§8.5).
4. **Deep sequence models** once the dataset supports them (>~20k unit-years).
5. **Functional/distributed-lag weather representation** as a research track (§2.1).

### 9.7 Python stack

| Purpose | Library | Notes |
|---|---|---|
| Data | `polars` (or `pandas`), `pyarrow`, `duckdb` | Parquet feature store, partitioned by year × origin_date |
| Gridded weather / soil | `xarray`, `rioxarray`, `rasterio`, `geopandas`, `pyproj`, `exactextract` | Area-weighted zonal stats to county; **mask to cropland (CDL)**, not whole county — Schlenker & Roberts weight to where crops are actually grown `[S]` |
| Reference ET / VPD / phenology | `pyeto` or hand-rolled ASCE/Hargreaves; custom photothermal module | Write the phenology module ourselves against SoySim's published equations and unit-test against its reported stage RMSEs `[S]` |
| Models | `lightgbm`, `xgboost`, `scikit-learn`, `pygam`, `statsmodels` (MixedLM for trend) | Pin versions |
| Uncertainty | `mapie` (**pin the major version**; API changed at 1.0), plus our own grouped/weighted CQR (§4.4); `ngboost` for the challenger; `properscoring` or `scoringrules` for CRPS | Cross-check our CQR against MAPIE's on the same split |
| Interpretability | `shap` (TreeExplainer), `alibi` or `PyALE` for ALE, `sklearn.inspection.permutation_importance` | Group features before reporting importance |
| Tuning | `optuna` with a **temporal-CV objective** | Never a random-CV objective |
| Validation harness | custom `sklearn`-compatible splitters: `ForwardChainingByYear`, `LeaveOneYearOut`, `BlockedSpatialKFold`, `LeaveYearAndRegionOut` | The most important code we will write. Make the splitters the thing everyone must go through |
| Data contracts | `pandera` or `pydantic` schemas + the feature-availability manifest (§5.2) | Enforce leakage rules mechanically |
| Tracking | `mlflow` (or `dvc` + git) | Every metric in §9.4 logged per run; model card auto-generated from the run |
| Testing | `pytest`, `hypothesis` | Property tests on the feature builder; the §6.2 agronomic tests as CI gates |

### 9.8 Training pipeline pseudocode

```python
# ============================================================================
# soyml/pipeline.py  —  training + honest evaluation for one target, one origin
# ============================================================================
from soyml.splitters import ForwardChainingByYear, LeaveOneYearOut
from soyml.features  import build_features, FEATURE_MANIFEST
from soyml.trend     import fit_causal_trend
from soyml.uq        import GroupedWeightedCQR
from soyml.checks    import agronomic_acceptance_tests
import lightgbm as lgb, numpy as np, shap, mlflow

TARGET = "yield_bu_ac"        # or oil_pct / protein_pct / alr_fa_*
ORIGIN = "08-01"              # forecast origin date
ALPHA  = 0.20                 # 80% intervals


def assemble(raw, origin):
    """Features legal at `origin` only; remaining season from climatology analogs."""
    FEATURE_MANIFEST.assert_available(origin)          # HARD FAIL on leakage
    obs   = raw.weather.truncate(to=origin)
    nwp   = raw.nwp.window(origin, origin + 15)        # expect little value  [S]
    analogs = raw.climatology.completions(origin, n=30)  # 30 analog years

    frames = []
    for m, completion in enumerate(analogs):
        wx = concat([obs, nwp, completion])
        ph = photothermal_phenology(wx, raw.lat, raw.maturity_group)  # SoySim-style
        frames.append(build_features(wx, ph, raw.soil, raw.mgmt, member=m))
    X = mean_and_spread(frames)     # member-mean features + analog_ensemble_spread
    return X


def run():
    raw = load_all()
    years = sorted(raw.years)
    oof, records = [], []

    # ---- OUTER: forward chaining. Each year predicted from prior years only ----
    for T in ForwardChainingByYear(years, min_train=6):
        tr_years, te_years = [y for y in years if y < T], [T]

        # (1) Causal trend + county level, fit on <= T-1 ONLY.  No leakage. [S]
        trend = fit_causal_trend(raw.subset(tr_years), unit="county", pool="crd")
        y_tr  = raw.target(tr_years, TARGET) - trend.predict(tr_years)   # anomaly
        y_te  = raw.target(te_years, TARGET)

        X_tr = assemble(raw.subset(tr_years), ORIGIN)
        X_te = assemble(raw.subset(te_years), ORIGIN)

        # (2) INNER: tune on a temporal split of TRAIN ONLY (nested; no peeking)
        params = optuna_tune(
            X_tr, y_tr,
            cv=LeaveOneYearOut(tr_years),
            model=lgb.LGBMRegressor,
            fixed=dict(objective="regression_l2",
                       monotone_constraints=MONOTONE_MAP),  # AWC up -> yield up
        )

        # (3) Point model + linear benchmark + long-lead linear model
        m_gbm   = lgb.LGBMRegressor(**params).fit(X_tr, y_tr)
        m_ridge = ridge_on_temperature_bins(X_tr, y_tr)   # Schlenker-Roberts design [S]
        m_point = m_ridge if ORIGIN <= "06-01" else m_gbm  # linear wins at long lead [S]

        # (4) Uncertainty: grouped, recency-weighted CQR. Calibration split BY YEAR.
        cal_years = tr_years[-3:]
        uq = GroupedWeightedCQR(
                base=lgb.LGBMRegressor, params=params, alpha=ALPHA,
                group_fn=lambda df: df["crd"],            # Mondrian by region
                half_life_years=5, min_group_n=50,
        ).fit(X_tr, y_tr, fit_years=tr_years[:-3], cal_years=cal_years)

        # (5) Predict, add the trend back, clip to physical bounds
        anom_hat = m_point.predict(X_te)
        lo, hi   = uq.predict_interval(X_te)
        base     = trend.predict(te_years)
        yhat, lo, hi = clip_physical(base + anom_hat, base + lo, base + hi, TARGET)

        # (6) Baselines — the actual bar
        b1 = baseline_climatological_mean(raw, tr_years, te_years)
        b2 = base                                          # county mean + causal trend
        b3 = baseline_last_year(raw, T)
        b4 = baseline_trend_plus_stress_ols(raw, tr_years, te_years)
        b5 = baseline_nass_forecast(raw, T, ORIGIN)         # incumbent, where available

        records.append(dict(
            year=T,
            # BOTH scales, always
            rmse_raw=rmse(y_te, yhat), r2_raw=r2(y_te, yhat),
            rmse_anom=rmse(y_te - b2, yhat - b2), r2_anom=r2(y_te - b2, yhat - b2),
            mae=mae(y_te, yhat), bias=np.mean(yhat - y_te),
            skill_vs_b2=1 - mse(y_te, yhat) / mse(y_te, b2),   # HEADLINE
            skill_vs_b5=1 - mse(y_te, yhat) / mse(y_te, b5),
            coverage=np.mean((y_te >= lo) & (y_te <= hi)),
            mean_width=np.mean(hi - lo), crps=crps(y_te, lo, yhat, hi),
            pinball=pinball(y_te, lo, hi, ALPHA),
            # disaggregate: coverage and error by region / AWC tercile / irrigation
            by_group=disaggregate(y_te, yhat, lo, hi, raw.groups(te_years)),
        ))
        oof.append((te_years, yhat, lo, hi))

    # ---- Additional protocols: spatial transfer and the pessimistic bound ----
    records_v3 = evaluate(BlockedSpatialKFold(raw, block="crd"))
    records_v4 = evaluate(LeaveYearAndRegionOut(raw))

    # ---- Final model on ALL years, for production ----
    trend_full = fit_causal_trend(raw, unit="county", pool="crd")
    X_full = assemble(raw, ORIGIN)
    y_full = raw.target(years, TARGET) - trend_full.predict(years)
    final  = lgb.LGBMRegressor(**params).fit(X_full, y_full)
    final_uq = GroupedWeightedCQR(...).fit(X_full, y_full,
                                           fit_years=years[:-3], cal_years=years[-3:])

    # ---- Credibility gates: must pass or the run FAILS ----
    expl = shap.TreeExplainer(final)
    sv   = expl.shap_values(X_full)
    agronomic_acceptance_tests(sv, X_full, target=TARGET).assert_all_pass()
    #   soybean yield declines above ~30 C, steeper above than below   [S]
    #   R3-R5 water deficit > V-stage water deficit
    #   high VPD at 60-90 DAS reduces yield                            [S]
    #   drought penalty larger on low-AWC soils
    #   water features ~inert on irrigated units
    #   oil peaks near 28 C seed-fill temperature                       [S]
    #   oleic up / linoleic+linolenic down with seed-fill day temp      [S]
    assert min(r["coverage"] for r in records) > 0.65, "worst-year coverage too low"
    assert abs(np.mean([r["bias"] for r in records])) < 0.5, "systematic bias"
    assert np.mean([r["skill_vs_b2"] for r in records]) > 0.10, \
        "does not beat county-mean-plus-trend by enough to ship"

    # ---- Ship artifacts ----
    mlflow.log_dict(records, f"validation_v1_{TARGET}_{ORIGIN}.json")
    mlflow.log_dict(records_v3, "validation_v3_spatial.json")
    mlflow.log_dict(records_v4, "validation_v4_year_x_region.json")
    write_model_card(final, records, records_v3, records_v4,
                     grouped_shap(sv, FEATURE_GROUPS),
                     ood_detector=fit_ood_detector(X_full),   # flag extrapolation
                     synthetic_disclosure=raw.synthetic_fraction)  # §7.4
    return final, final_uq
```

Three things in that pseudocode are the load-bearing parts, and are easy to lose under deadline
pressure: **the trend is refit inside the outer loop** (no leakage), **the conformal calibration split
is by year, not random** (no optimistically narrow intervals), and **the agronomic tests and the
skill-vs-B2 threshold are assertions that fail the build** (no shipping a model that memorized the
trend). Everything else is replaceable.

---

## 10. Summary for the person writing customer-facing claims

Four sentences you can rely on:

1. **County-scale soybean yield, from 1 August, at 5–7 bu/ac RMSE, beating a county-mean-plus-trend
   baseline by 15–30 %** is a realistic and defensible product. Say the origin date, the scale, and the
   baseline every time.
2. **Seed oil at ~1 percentage point RMSE (R² ~0.35)** is realistic and genuinely useful for logistics
   and blending. It is not a specification guarantee.
3. **Protein is not predictable from weather and soil** (published R² ≈ 0.09). Do not sell it. Saying so
   plainly will increase, not decrease, technical partners' trust in the rest of the claims.
4. **Fatty acids are a research capability** until we have backtested them on real field data; the
   mechanism is well established but no field-scale weather-only benchmark appears to exist.

And one rule: **every number that leaves the building must trace to a held-out real year under the V1
protocol.** Nothing from synthetic data, nothing from a paper we have not opened, nothing from a
random-k-fold R².
