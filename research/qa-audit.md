# OleoCast QA / Data-Integrity Audit

**Auditor:** adversarial QA pass (independent web verification + code execution)
**Date:** 2026-08-12
**Scope:** research briefs, encoded constants, model metadata, provenance labeling, API/UI surfaces, runtime outputs.
**Method limits:** verification was done via web search (snippets/abstracts); direct fetch of most publisher domains is blocked by egress policy, so "VERIFIED" below means confirmed against independently surfaced abstracts/extension pages, not full-text PDFs. This is the same limitation the briefs themselves disclose — and disclose honestly.

Verdict vocabulary: **VERIFIED** / **PLAUSIBLE-UNVERIFIED** / **MISLABELED** / **CONTRADICTED** / **FABRICATED**.

---

## 1. Scientific claims (spot-checked 14)

| # | Claim (where) | Verdict | Evidence |
|---|---|---|---|
| 1 | Oil–temperature response unimodal, optimum ~28 °C; Piper & Boote 1999: 1,863 obs, 20 cultivars, 10 MGs, 60 locations, 14.6–28.7 °C (brief §1.1; `agronomy.py::_oil_temperature_response`) | **VERIFIED** | Paper exists (JAOCS 76, doi 10.1007/s11746-999-0099-y); design details (1,863 cultivar×location×year, Uniform Tests, temperature from first pod to maturity, 14.6–28.7 °C) independently confirmed. "Approaching a maximum near 28 °C" is the widely quoted result. |
| 2 | Oil–protein inverse relationship, r ≈ −0.4 to −0.6, moderate not near-unity (brief §1.6; `agronomy.py` protein from oil) | **VERIFIED** | Independent sources report r ≈ −0.53 (and −0.55 to −0.9 in specific populations). Direction and "moderate" framing correct. Note: `agronomy.py` docstring says slope ≈ −0.55 pt protein/pt oil but the code uses **−0.85** — internal inconsistency (see §3). |
| 3 | ~−0.4 pt oil/°C above optimum (Dornbos & Mullen 1992: 35 vs 29 °C → −2.6 pp oil, +4.0 pp protein; drought −2.9 pp oil, +4.4 pp protein) | **VERIFIED (literature) / CONTRADICTED (encoding)** | Paper exists (JAOCS 69:228–231); severe-drought numbers (+4.4 protein, −2.9 oil) independently confirmed verbatim. **But the encoded quadratic (−0.022·(T−28)²) yields only −0.18 pt/°C at 32 °C and −0.31 at 35 °C — 2–3× weaker than the −0.43 pt/°C the code's own docstring cites.** |
| 4 | GDD base 10 °C (50 °F) with 86 °F/30 °C cap is the standard soybean method (`phenology.py`, brief §3.2) | **VERIFIED** | Iowa State, Cornell/NRCCA, NDAWN all confirm the 86/50 method for soybean. `gdd_day()` implements it correctly (cap 30 °C, floor 10 °C). |
| 5 | GDD stage targets by MG (`phenology.py::_GDD_TARGETS_MG3`, 10 %/MG scaling) | **PLAUSIBLE-UNVERIFIED, with a real-world timing problem** | NDAWN/Kandel anchor points check out exactly (1,666/1,862/2,030 °F-day to R8 for MG 00.7/0.4/1.0; n=1,816; dev 2007–2012, val 2013–2015). Model MG 0.4 → ~1,073 °C-day to R7 ≈ NDAWN's 1,034 °C-day to R8: consistent. But the brief itself says a stage-by-stage table could NOT be verified, and **runtime stage timing runs late**: Story Co. IA, May-9 planting → R1 at DAP 61 (~Jul 9), R5 at DAP 97 (~Aug 14–30 depending on year), R7 ~Oct 2. Real-world central-Iowa R5 is late July–early Aug (DAP ~75–85). The demo's 2025 Story forecast dates R5 = Aug 30 — ~3 weeks late. |
| 6 | Higher seed-fill temperature → oleic up, linolenic/linoleic down (brief §2.1; `agronomy.py`) | **VERIFIED** | Multiple independent sources (Gibson & Mullen 1996 JAOCS; four-oilseed Ind. Crops & Products study, 10→40 °C) confirm direction. Encoded signs match. |
| 7 | Critical photoperiod decreases with maturity group; soybean short-day (`phenology.py::critical_photoperiod`) | **VERIFIED (direction) / PLAUSIBLE-UNVERIFIED (formula)** | Later MG = more photoperiod-sensitive confirmed (Yang 2019 Crop Science; Setiyono 2007). The specific `14.35 − 0.32·(MG−3)` linear form is not independently verifiable — plausible parameterization, not a published equation. |
| 8 | Frost termination at Tmin ≤ −2 °C after flowering (`phenology.py`) | **PLAUSIBLE-UNVERIFIED** | Standard extension killing-frost threshold for soybean is 28 °F ≈ −2.2 °C; −2.0 °C is a reasonable implementation. Collapsing all remaining stages to the frost date is a simplification (real frost-killed seed is immature, not "mature at frost date") — acceptable for a demo, note it. |
| 9 | Typical oil 18–23 % and protein 33–37 % (brief §0.4; `agronomy.py` comments) | **VERIFIED, with a units landmine** | US Soybean Quality Survey long-term averages: **35 % protein / 19 % oil at 13 % moisture**; commodity oil range 16–23 %. The ranges are right — **but the code labels its outputs "% of seed dry weight" while generating values (mean oil 19.7, protein 35.7) that are 13 %-moisture-basis numbers.** Dry-basis oil should center ~21.8 %. This is exactly the ~2.8 pp basis-mixing artifact the agronomy brief warns about in §0.3, committed by the product's own code. It propagates into the crush math (§3 below). |
| 10 | August precipitation the dominant single precipitation driver of US soybean yield (brief §4.2; `aug_precip_mm` feature, `+4.5·tanh` yield term) | **VERIFIED** | farmdoc daily (2023) crop-weather model confirms August precipitation is the top weather variable, edging July precipitation. The ~2 bu/ac two-thirds-range magnitude claim is consistent with the code's ±4.5 bu saturating term. |
| 11 | Fehr & Caviness staging: R5 = seed 1/8 in (3 mm), R7 = one mature-color pod, R8 = 95 % pods mature (brief §3.1; `phenology.py` docstring) | **VERIFIED** | NC State / Clemson / NDSU extension reproduce these definitions verbatim. Brief's stage table is accurate. |
| 12 | Schlenker & Roberts 2009: soybean yield optimum/threshold 30 °C, steep asymmetric decline (briefs; `*_days_gt30` features) | **VERIFIED** | PNAS 106:15594–15598 confirmed: 29 °C corn / 30 °C soybean / 32 °C cotton, decline steeper than incline. |
| 13 | Warm nights during fill (brief §1.2: warm nights/low DTR **depress** oil) vs `agronomy.py` (`oil += 0.012·fill_night_warm`, "warm nights mildly raise oil") | **CONTRADICTED (code vs own brief)** | The brief, citing Zhang et al. 2016 (763 samples, China — paper real), concludes DTR positive for oil, Tmin **negative** for oil, and prescribes exactly that expected sign. The code encodes the opposite sign. One of them is wrong by the project's own standards. |
| 14 | Drought effect on oleic: brief §2.2 says drought **decreases** oleic (Dornbos & Mullen: drought ↑stearic ↓oleic); `agronomy.py` has `oleic = … + 4.0·drought` (increase) | **CONTRADICTED (code vs own brief)** | Second sign inversion between the response functions and the research brief they claim to encode. `EXPECTED_SIGNS` bakes the inverted sign into the test suite, so the tests enforce the contradiction rather than catching it. |

**Also checked:** Iowa May–Sep GDD₁₀ from the weather generator = 1,344–1,714 (mean 1,456) for Story Co. — inside the plausible 1,300–1,600 band. Yield tech trend 0.45 bu/ac/yr — matches the commonly cited ~0.4–0.5. Commodity fatty-acid baseline (11/4/23/54/8) — matches published commodity profile.

---

## 2. Reference authenticity (13 sampled — none fabricated)

Fabricated citations are the classic failure mode; I found **zero** among the sample, including the two most "suspicious-looking" ones.

| Citation (as given in briefs) | Found? |
|---|---|
| Piper & Boote 1999, JAOCS 76 | **FOUND** — exact title, journal, design details match |
| Dornbos & Mullen 1992, JAOCS 69:228–231 | **FOUND** — exact volume/pages; drought numbers match verbatim |
| Gibson & Mullen 1996, JAOCS (BF02517949) | **FOUND** |
| Fehr & Caviness 1977, ISU Special Report 80 | **FOUND** (canonical) |
| Rotundo & Westgate 2009, Field Crops Res. 110:147–156 | **FOUND** — exact pages; water-stress/protein-buffering findings match |
| Schlenker & Roberts 2009, PNAS 106:15594–15598 | **FOUND** — exact |
| Kandel et al. 2017, Agric. For. Meteorol. (NDAWN GDD model) | **FOUND** — 1,816 data points, 1,666/1,862/2,030 AGDD all match |
| Hamed, Van Loon, Aerts & Coumou 2021, Earth Syst. Dynam. 12:1371–1391 | **FOUND** — exact vol/pages; "90 % rainfed", ">1/3 of traded soybean" match |
| Chiozza et al. 2025, Crop Science, doi 10.1002/csc2.70142 | **FOUND** — "22 US states, 24 years", LOYO, parsimony finding all match |
| "Harvesting insights", Sci. Reports 16:8994 (2026) | **FOUND** — 134 crop-site-years, R²>0.87, RMSE<1.13 Mg/ha all match |
| Habibi et al. 2023, Comput. Electron. Agric. 212:108096 | **FOUND** — 47 fields KS+IA 2019–2021, RMSE 1.04 oil / 1.80 protein, XGBoost best — all match |
| Alsajri et al. 2020, Agronomy J. 112:194–204 (agj2.20034) | **FOUND** |
| Setiyono et al. 2007, Field Crops Res. (photothermal phenology) | **FOUND** |

One nuance: the brief attributes "oil R²=0.53 / protein R²=0.36" to the PlanetScope/GRU work and "abs error 1.04/1.80" to Habibi — the same error numbers appear attached to both papers in the ML brief (§1.6), which smells like one study's numbers echoed onto the other. Not fabrication, but tighten before quoting.

**Verdict: reference base is genuine.** The briefs' self-flagged suspect numbers ([?] items like "78 mg/g per °latitude", "0.007–0.06 pp/week") are correctly quarantined and never encoded in the product — good discipline.

---

## 3. Internal consistency

### 3.1 MODEL_CARD.md vs models/meta.json — **VERIFIED**
Every cell of the model-card metrics table matches `meta.json` to rounding (yield 6.02/0.76/11.13/46 %/9.77/0.90; oil 0.50/0.85/1.07/53 %/0.83; protein 0.65/0.72/1.08/39 %/1.08; oleic 1.37/0.89/3.37/59 %/2.18; linoleic 1.34/0.78/2.44/45 %/2.22; linolenic 0.55/0.84/1.15/52 %/0.93). "37 features" = `len(FEATURE_NAMES)` ✓. "1,056 field-seasons, 8 regions × 2004–2025 × ~6" = `n_train` 1056 ✓. Training-data tag matches ✓.

### 3.2 README vs code — **VERIFIED, with two caveats**
- LOYO validation: real (`train.py::loyo_validate`, per-year folds) ✓.
- Conformal intervals: real (split-conformal on out-of-fold residuals; widths in meta; inflated in-season by `_season_uncertainty_factor`) ✓.
- No `year` leakage: `FEATURE_NAMES` contains no year/trend feature ✓; year is used only for baselines and fold splitting ✓.
- "29 tests incl. model credibility gates": 29 pass, and `test_models_beat_baselines` / `test_shap_directions_match_agronomy` exist ✓.
- **Caveat A — circular coverage check.** `coverage_check` is computed on the *same* residual set whose 0.90-quantile defines the interval, so 0.90 is guaranteed by construction (all six targets report the identical 0.8996 = 950/1056). Presenting it in the card/UI as "measured coverage" overstates what it demonstrates. It is out-of-fold, so it isn't dishonest — but it can never fail, which makes it decoration, not a check.
- **Caveat B — Science page "250+ citations".** Agronomy brief has 129 numbered refs; data-sources ~25 sources; the ML brief has no numbered reference list (inline mentions only). "250+" is not obviously supportable; say "129-reference agronomy brief" or count properly.

### 3.3 Crush constants (`processing.py`) — **CONTRADICTED (industry arithmetic)**
Industry standard (CME/farmdoc, independently verified): a 60-lb bushel → **11 lb oil + 44 lb of 48 %-protein meal + 4 lb hulls + 1 lb waste**; survey-based oil yield ~10.9 lb/bu.

What the code produces:
- **Oil: 9.47 lb/bu at 19 % oil** (`60 × 0.87 × 0.19 × 0.955`) vs the module docstring's own claim of "commonly quoted 11 lb per bushel at 19 % oil". The formula is correct **only if** `oil_pct` is dry-basis (21.8 % db → 10.9 lb ✓), but the models emit ~19–20 (a 13 %-moisture-basis magnitude mislabeled as dry basis, §1 item 9). Net effect: **oil value understated ~13 %** across the whole product.
- **Meal: 47.5–48.3 lb/bu at 48 % protein.** That pairs the 44 %-protein meal *quantity* with the 48 %-protein meal *grade*. Protein mass balance fails: 48.3 lb × 48 % = 23.2 lb protein out of a bushel containing only ~18–19 lb of protein (35 % of 52.2 lb dm). Physically impossible; **meal value overstated ~10 %**.
- `meal_protein = 48·(protein/35)` produces meal >50 % protein for high-protein seed (53.3 % in the Cass ND demo) — beyond anything solvent crush produces.
- The two errors partially cancel in EPV ($12.7–13.1/bu, margin ~$2.1–2.4/bu — plausible-looking), which is why nothing caught it. Plausible total, wrong components.

### 3.4 Climate normals in `geography.py` — **VERIFIED (3 spot-checks)**
- Story Co./Ames IA: code July 28.6/16.8 °C, annual 894 mm. Published: July high 84 °F (28.9), annual ~38 in (965 mm). Match within ~1 °C / 8 %.
- Cass Co./Fargo ND: code July 27.4/14.9 °C, annual 585 mm. Published: July high 82 °F (27.8), annual ~25 in (635 mm). Match.
- Champaign Co. IL: code July 29.6/18.6 °C, annual 1,011 mm. Published: July high 85 °F (29.4), annual ~40–42 in. Match.
The "PRISM/NOAA-consistent" claim holds for the counties checked.

---

## 4. Provenance honesty

| Surface | Labeled? | Verdict |
|---|---|---|
| `weather.py` generator | `attrs["source"]="simulated-climatology"`; live fetches labeled by source name | **VERIFIED** |
| `/api/forecast` | `weather_source` field in every response | **VERIFIED** |
| `/api/model/meta` | `training_data: synthetic-from-published-response-functions-v1` | **VERIFIED** |
| `/api/datasources` | Lists the simulator as "active (used when live sources unreachable)"; flags Open-Meteo non-commercial tier | **VERIFIED** |
| Dashboard disclaimer | Explicit: synthetic training data, weather source named, "illustrative, not trading/agronomic advice" | **VERIFIED** |
| Science page limitations | Synthetic data first bullet; metrics framed as structure-recovery, not field accuracy | **VERIFIED** |
| MODEL_CARD.md | "Training data — read this first" section; explicit "do not quote these numbers to customers" | **VERIFIED** — genuinely good |
| **`/api/history` payload** | **NO.** Returns `{region_id, years[], stats}` with yield/oil/EPV numbers and **no `weather_source`, no model-estimate flag, no synthetic tag.** The FastAPI docstring says "retrospectives," but a JSON consumer sees bare numbers indistinguishable from USDA records. | **MISLABELED (by omission)** |
| HistoryPanel (UI) | Partially: note says "Retrospective model outcomes", footer says "retrospective model estimate". It does **not** say the underlying weather is simulated — that fact lives only in the page-bottom disclaimer. Acceptable for the dashboard; the API gap above is the real issue. | **PLAUSIBLE / needs one phrase** |
| `/api/progression` payload | Same omission as history: no source field per response. | **MISLABELED (by omission)** |
| Landing page | "**100 % public data inputs** — NASA POWER, Open-Meteo/ERA5, USDA NASS, SSURGO" — but NASS and SSURGO are `connector-planned`, nothing live is wired in this build, and the demo runs entirely on simulation. Marketing copy describes the roadmap as if it were the present. Also "conformal intervals … coverage you can check" — see circular coverage, §3.2. | **MISLABELED (overstated)** |

**Direct answer to the specific question:** No — neither `/api/history` nor the HistoryPanel states that historical "outcomes" are retrospective model estimates computed on **simulated weather**. The UI says "model estimate" but not "simulated weather, not USDA records"; the API payload says nothing at all.

---

## 5. Numbers smell test (code executed)

Ran `soyoil.train` artifacts, generator, phenology, and `soyoil.predict.forecast` for all 8 regions (2025, as-of Aug 12, `prefer_live=False`).

**Passes:**
- Training-table yields: mean 44.7, IQR 40–53, 2020–25 regional means 38–55 bu/ac — inside the 35–65 band (Story 54.9, Champaign 51.3).
- Oil 16.0–22.6 % (mean 19.7), protein 32.5–40 — within stated commodity ranges (on the mislabeled basis, §1 item 9).
- Story Co. May–Sep GDD₁₀: 1,344–1,714, mean 1,456 ✓.
- 22-year LOYO conformal machinery reproducible; 29/29 tests pass.
- Demo EPV $12.7–13.1/bu, crush margin ~$2/bu — plausible totals.

**Failures / outliers:**
1. **Cass Co. ND 2025 demo forecast: 13.7 bu/ac.** ND actual county averages run ~30–40. This is a 1988-class disaster presented as a routine demo year, driven by late phenology (R5 Aug 25 in Fargo) pushing fill into September cold in the simulated year. First thing a soybean-literate customer will see.
2. **Phenology runs 1.5–3 weeks late** (R5 DAP 97 vs real ~80 for central IA; demo Story R5 = Aug 30). Consequences cascade: `fill_tmean_c` mean 20.7 °C (real IA Aug ~23–24 °C), minimum 4.0 °C (a "seed-fill" mean of 4 °C is nonsense — frost-terminated tails), depressed oil for northern regions (Story demo oil 18.7 vs IA survey ~19.5), inflated linolenic (10.3 % vs typical 7–9).
3. **Fatty-acid outliers and closure break:** noise is added after normalizing to 100, and never renormalized — training rows include oleic 1.2 % (physically absurd), linolenic 14.1 %; demo oleic 15.9 % (below the ~17–30 commodity range). The ML brief's own §2.7 prescribes compositional closure (ALR); the code ignores its own brief.
4. **Precip tails too fat:** 32 training seasons >1,000 mm season precip, max 1,723 mm (Corn Belt Mar–Dec extreme is ~1,200); Aug precip max 633 mm.
5. Saline Co. NE mean 36–38 bu/ac vs real SE-Nebraska ~55+ — regionally miscalibrated low.

---

## 6. Prioritized fix list

1. **Fix the oil/protein basis inconsistency and crush arithmetic** (customer-money-facing).
   - Decide the basis: either emit true dry-basis oil (~21.8 centered) or relabel outputs as 13 %-moisture basis and drop the `×(1−0.13)` from `crush_value`. Target: ~11 lb oil/bu at standard composition.
   - Meal: 44 lb @ 48 % protein (or 47.5 @ 44 %), not 47.5 @ 48 %. Cap `meal_protein_pct` ≤ ~50. Add a protein mass-balance assertion to tests.
2. **Label `/api/history` and `/api/progression` payloads**: add `weather_source` and `provenance: "retrospective model estimate on simulated weather — not USDA records"` per response; add one phrase to the HistoryPanel note. This is the one place a user can see simulated numbers with no path to knowing.
3. **Recalibrate phenology timing** (~10–20 days late): retune `_GDD_TARGETS_MG3` / photoperiod penalty against SoyStage or NDAWN stage dates; add a test asserting R5 for MG 2.6 @ 42 °N, May-10 planting lands DAP 75–90. This alone fixes most of the northern-region output distortion (Cass 13.7 bu/ac, oil lows, linolenic highs).
4. **Reconcile `agronomy.py` with the agronomy brief:** night-warm sign (brief: warm nights depress oil; code: raise), oleic–drought sign (brief: drought lowers oleic; code: raises), above-optimum slope (docstring −0.4 pt/°C; encoded −0.18 at 32 °C), protein slope (docstring −0.55; code −0.85). Fix code or docstrings — currently the test suite enforces the contradictions.
5. **Fix the circular coverage check:** calibrate the conformal quantile on one subset of years and measure coverage on a disjoint held-out set (or report leave-one-year-out coverage per calibration-excluded year). Until then, rename "measured coverage" to "in-sample consistency check" in the card, Science page, and Landing copy.
6. **Enforce fatty-acid closure and physical bounds** after residual noise (renormalize to 100; clip oleic ≥ ~15, linolenic ≤ ~13), per the ML brief's own §2.7.
7. **Soften the Landing page:** "100 % public data inputs" → "built on public data sources" with a "demo runs on simulated weather" footnote; NASS/SSURGO are planned, not wired.
8. Minor: replace "250+ citations" with an accurate count; note that frost-termination stamps immature seasons as mature; tame generator precip tails; recheck the duplicated 1.04/1.80 error numbers attached to two different papers in the ML brief.

---

## 7. Executive summary

**What is real:** The code, pipeline, and validation machinery are real and run: LightGBM per target, genuine leave-one-year-out CV, real baselines that models must beat, conformal interval plumbing, SHAP drivers, 29 passing tests. The scientific foundation is real: **all 13 sampled citations exist with matching details — no fabricated references**, and the headline constants (28 °C oil optimum, 86/50 GDD, Fehr & Caviness staging, Schlenker-Roberts 30 °C, August-precipitation dominance, oleic-up/linolenic-down, NDAWN GDD table, county climate normals) all check out against independent sources. Model card ↔ meta.json is an exact match.

**What is synthetic and labeled:** Weather, training data, and all metrics. Labeling is unusually good where it exists — the model card, Science page, dashboard disclaimer, `/api/datasources`, and every forecast's `weather_source` stamp are honest and prominent. The briefs' own confidence-tagging and self-flagged suspect numbers are best-practice.

**What needs fixing before showing a customer:**
1. **Crush math is wrong** against the industry's own 11-lb-oil / 44-lb-48 %-meal arithmetic (oil understated ~13 %, meal protein mass-balance impossible) — rooted in a dry-basis/13 %-moisture mislabeling that the agronomy brief explicitly warned about.
2. **`/api/history` serves simulated retrospectives as bare numbers** with no provenance field — the one genuine labeling hole.
3. **Phenology runs 2–3 weeks late**, producing a 13.7 bu/ac North Dakota demo forecast and depressed northern oil values that any agronomist will spot in the first minute.
4. **Two response-function signs contradict the project's own research brief** (night temperature, oleic-drought), and the "measured coverage = 90 %" claim is circular.

Nothing here is fabricated. The failures are arithmetic, calibration, and labeling gaps — all fixable, and mostly localized to `processing.py`, `phenology.py` targets, `agronomy.py` signs, and one API payload.
