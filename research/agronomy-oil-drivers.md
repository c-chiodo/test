# Weather Drivers of Soybean Seed Oil Concentration and Yield

**A citation-backed research brief for ML product development**

Prepared: 2026-08-12
Scope: *Glycine max*, temperate rainfed production (primarily US Corn Belt / Mid-South, with Argentine and Chinese regional studies used for corroboration).

---

## 0. How to read this document (methodology and evidence caveats)

**Read this section before using any number downstream.**

### 0.1 Source access limitation

This brief was assembled under a network egress policy that permitted **search-engine access only**. Direct full-text retrieval (`WebFetch`, `curl`) was blocked for every publisher and extension domain attempted — Wiley/ACSESS (Crop Science, Agronomy Journal), AOCS/JAOCS, ScienceDirect (Field Crops Research, Agricultural and Forest Meteorology), Springer, Frontiers, PMC/NCBI, PNAS, Nature, and land-grant extension hosts (NDSU/NDAWN, SDSU, Iowa State, UNL).

**Consequence:** quantitative values below were extracted from search-surfaced abstract and snippet text, not from verified full text, tables, or figures. Every number is therefore **provisional**. Before any coefficient here is hard-coded into a production model, feature definition, or customer-facing claim, it must be re-verified against the PDF. Section 8 gives a prioritized verification list.

### 0.2 Confidence tags used throughout

| Tag | Meaning |
|---|---|
| **[E]** Established | Consistent direction across multiple independent studies, designs, and regions. Safe to encode as a prior. |
| **[M]** Moderate | Two or more supporting studies; magnitude uncertain or design-dependent. |
| **[S]** Single-study | One source. Direction plausible, magnitude not replicated. Do not treat as a constraint. |
| **[C]** Contested | Credible sources disagree on **sign**. Flagged explicitly; see §1.9. |
| **[?]** Suspect transcription | Number as surfaced is internally implausible (units or magnitude). Do not use until verified. |

### 0.3 Units discipline — a recurring source of error

- Oil and protein are reported here as **% of seed dry matter (dry basis, db)** unless stated. Commercial and USDA-adjacent reporting is frequently on a **13% moisture basis (mb)**; conversion is `%db = %mb / 0.87`. A 19.0% mb oil equals ~21.8% db. **Mixing bases produces a ~2.8 pp artifact — larger than most weather effects in this document.** Normalize all training labels to one basis before modeling.
- "pp" = percentage points (absolute change in oil %). "%" used for relative change.
- Some literature reports g kg⁻¹ (195 g kg⁻¹ = 19.5%).
- GDD appears in both °C-day (base 10 °C) and °F-day (base 50 °F). Multiply °C-day by 1.8 to get °F-day.

### 0.4 Baseline distributions (orientation)

- Typical commercial oil range: **18–23% db**; US mean reported as **195 g kg⁻¹ (19.5%)**. [E]
- Protein and oil are jointly constrained: seed at **18–19% oil should carry 34–35% protein** to yield a 44%-protein meal — the processor's working relationship. [E]
- Interannual US-average soybean yield variability is **~22%, of which ~32% is explainable by temperature and precipitation alone**. [M] This is the honest ceiling for a weather-only yield model at national scale.

---

## 1. Oil concentration drivers

### 1.1 The headline structural finding: the temperature response is unimodal, and this resolves an apparent contradiction

The literature appears to contradict itself on the sign of `∂Oil/∂T`. It does not. **Oil concentration is a unimodal (quadratic) function of mean temperature during seed fill, peaking near 28 °C.** Studies disagree only because they sample different sides of the optimum.

| Study type | Temperature range sampled | Observed sign | Representative source |
|---|---|---|---|
| Multi-environment field regression | mean 14.6–28.7 °C during seed fill | **Positive**, saturating at ~28 °C | Piper & Boote 1999 |
| Controlled environment, heat treatments | mean 25 → 33 °C | **Negative** | Gibson & Mullen 1996; Dornbos & Mullen 1992 |
| In vitro / cultured cotyledons | broad | optimum **25–28 °C** | Thomas et al.; Iyer et al. 2008 |

**Implication for feature engineering:** a single linear mean-temperature term will produce an unstable, region-dependent coefficient and will silently mis-extrapolate under warming. Use a **quadratic term or a hinge/piecewise-linear spline with a breakpoint near 28 °C**, and add separate hot-extreme counters (§1.3).

#### Piper & Boote (1999) — the strongest field-scale evidence [E for shape, M for magnitude]

- Design: **1,863 cultivar × location × year observations**, 20 cultivars spanning 10 maturity groups, **60 locations, 29.4–47.5 °N** (Uniform Soybean Tests).
- Temperature covariate: **average daily mean temperature from predicted first pod to observed maturity**, range **14.6–28.7 °C**.
- Model selection: linear, quadratic, and linear-plateau compared; **quadratic best**.
- **Oil increased with temperature, approaching a maximum at ~28 °C mean.**
- Fit: **adjusted R² = 0.239 for oil**; **adjusted R² = 0.003 for protein** — i.e. temperature explains ~24% of oil variation and **essentially none of protein variation** at this scale.
- `Protein + oil` was **linear** in temperature (adjusted partial R² = 0.183).

Two commercially important reads of this:

1. **Oil is the more weather-predictable of the two traits.** The near-zero protein R² is not a nuisance result — it says a weather-driven protein model will underperform a weather-driven oil model. Prioritize oil.
2. **The sum is more tractable than the parts.** Modeling `protein+oil` (linear in T) and the `oil:protein` split separately may outperform modeling each independently.

> **Derived, not published:** Piper & Boote report R² and shape but no slope in the surfaced text. Across a ~14 °C span explaining ~24% of oil variance, the implied linear-region sensitivity is on the order of **+0.2 to +0.4 pp oil per °C** below the optimum. **This is my arithmetic inference, flagged [S]/derived — extract the actual polynomial coefficients from the paper before use.**

#### Above the optimum: quantified declines [E for sign, M for magnitude]

**Dornbos & Mullen (1992)**, JAOCS 69:228–231 — the most quotable per-degree numbers in the literature:

- Seed from plants at **35 °C during seed fill vs 29 °C**: **+4.0 pp protein and −2.6 pp oil**, averaged across drought levels.
- → **derived ≈ −0.43 pp oil per °C** over 29–35 °C.
- Severe drought (independent of temperature): **+4.4 pp protein, −2.9 pp oil**.
- Both responses were **linear in accumulated stress degree days** at each temperature — a directly usable functional form.

**Gibson & Mullen (1996)**, JAOCS (seed composition) and Crop Science 36 (yield) — the cleanest day/night factorial:

- Treatments: day/night **30/20, 30/30, 35/20, 35/30 °C** (means 25 → 33 °C), cv. Gnome 85.
- **Stage specificity is the key result:** treatments imposed during **R1–R5 had little effect on oil and protein.** Effects appeared when treatments spanned **R5–R8** (and R1–R8).
- Over R5–R8, as mean rose 25 → 33 °C: **oil decreased, protein increased.**

**This is the single most important phenological finding in the brief:** oil concentration is set during **R5–R8**, not during flowering. Aggregating temperature over the whole season, or over R1–R4, dilutes the signal.

**Alsajri et al. (2020)**, Agronomy Journal 112:194–204 [M]:

- Five day/night regimes: **21/13, 25/17, 29/21, 33/25, 37/29 °C**; one indeterminate (AG5332) and one determinate (P5333RY) cultivar.
- **Quadratic** functions best described yield vs temperature; **optimum temperature for yield 26 °C (indeterminate) and 23 °C (determinate)**.
- **Cultivar × temperature interaction significant for oil, protein, palmitic, oleic, and linolenic acid**, plus raffinose and stachyose.

The significant **cultivar × temperature interaction** matters commercially: the temperature response of oil is **genotype-dependent**. A weather-only model implicitly averages over an unobserved genotype distribution. If variety or at least maturity group is obtainable per field, it belongs in the model as an interaction, not just an additive term.

Note also that **optimum temperature for yield (23–26 °C) is lower than optimum for oil concentration (~28 °C)**. Oil% and yield are not maximized by the same weather. A season can deliver high oil and mediocre yield, and vice versa — do not expect the two model heads to share a sign structure.

### 1.2 Night temperature and diurnal temperature range (DTR)

[M] — direction reasonably supported, magnitudes weak.

- Reported that **diurnal temperature difference had a greater effect on soybean protein and oil contents than average daily temperature**, implying night temperature carries independent information beyond the mean.
- **Zhang et al. (2016)**, J. Agric. Food Chem. — 763 samples, China, 2010–2013: **path analysis identified DTR as the main factor directly affecting protein and oil contents.** Protein correlated **positively with accumulated temperature ≥15 °C (AT15) and mean daily temperature (MDT)**, and **negatively with sunshine hours and DTR**; **oil correlations were opposite in sign** — i.e. **oil positively associated with DTR and sunshine hours**.
- Consistent inference: **warm nights (low DTR) depress oil**; **large DTR (warm days, cool nights) favors oil**.
- Gibson & Mullen's fatty-acid results reinforce that night temperature acts differently from day temperature: raising night temperature from 20 → 30 °C at a 30 °C day **decreased oleic and increased linoleic** — the *opposite* direction from raising day temperature (§2).
- High-night-temperature stress is independently damaging to yield: **−42% seed yield under high nighttime temperature at ambient CO₂** in one genotype (Thenveettil et al. 2025, *Scientific Reports*) [S].

**Modeling implication:** include **Tmin and DTR during R5–R6 as features distinct from Tmean.** Do not assume they are redundant with the mean. Expected sign: DTR **positive** for oil, Tmin **negative** for oil.

### 1.3 Heat stress / extreme-day exposure during seed fill

[E] for existence of a sharp threshold; [M] for the specific cut-point applied to oil.

- Reproductive tissue thresholds: **35–38 °C reduces pollen germination, anther dehiscence, and stigma receptivity**; **≥35 °C** causes flowering delay, flower and pod abortion, and reduced 100-seed weight. **35 °C for 10 h/day produced ~27% yield reduction** [S].
- Seed-filling physiology: as mean temperature rises **above 23 °C, seed growth rate, seed size, and seed harvest index decline, reaching zero at ~39 °C** [M].
- Yield-side threshold (§4): **30 °C** for soybean in county-panel analysis (Schlenker & Roberts 2009).
- 2023 field observations: extreme heat **>40 °C** produced **flower abortion 26–80%**, vs **21–53% under <35 °C** conditions in 2024 [S].

**Note the threshold divergence:** the *statistical yield* threshold (~30 °C daily max, from panel regressions) and the *physiological reproductive* threshold (~35 °C) are different quantities and should both be represented. Recommend counting exposure at **three cut-points (30, 32, 35 °C)** and letting regularization select, rather than committing a priori.

### 1.4 Water availability

#### During seed fill (R5–R6) — the dominant water window for oil [E for sign, M for magnitude]

- **Rotundo & Westgate (2009)**, Field Crops Research 110:147–156 — meta-analysis, the best synthesis available: **water stress reduced the per-seed content (mg/seed) of protein, oil, and residual fractions. Protein accumulation was less affected than oil and residual.** Because protein is buffered relative to oil, **final protein concentration rises and oil concentration falls.**
  - Mechanistically this is a **dilution/partitioning result, not a biosynthesis result** — worth understanding, because it means oil% responds to anything that shortens or slows seed fill, not only to water per se.
  - Important caveat the authors themselves raise: **the magnitude of composition response to experimental manipulation in the field was far smaller than the variation observed in the Uniform Soybean Regional Field Tests.** Translation: **observational variation is dominated by genotype, region, and unmeasured factors, not by the treatments we can quantify.** This directly bounds achievable model R² (see §5.4).
- **Dornbos & Mullen (1992)**: severe drought during seed fill **−2.9 pp oil, +4.4 pp protein**; **linear in accumulated stress degree days**.
- Drought during seed filling reported to **decrease oil content by up to 12.4% (relative)** with reduced oleic acid [S].
- **Rotundo & Westgate (2010)**, Crop Science — separates **rate vs duration** of component accumulation under water stress. This rate/duration decomposition is the correct mental model for features: water stress can cut oil by shortening fill duration, by lowering fill rate, or both, and these have different weather signatures.

#### During flowering / pod set (R1–R4) [E for yield, weak for oil]

- **R1–R4 water status principally sets seed number, not seed composition.** Gibson & Mullen found day/night temperature treatments during **R1–R5 had little effect on oil and protein**; the same stage-specificity logic applies to water.
- Water stress imposed across reproductive stages showed **a significant effect on protein but not on seed oil** in at least one deficit-irrigation study [S].
- Sensitivity peak for **yield and thousand-seed weight** runs from flowering through seed development, with the **most sensitive window at R4 to mid-R5** [M].
- A well-documented interaction with real agronomic consequence: **irrigation during flowering increases seed number, but subsequent water stress during seed fill then reduces seed size, and the net yield penalty can exceed that of never irrigating at all** [M]. This is a genuine **path-dependency / carry-over effect** — a model with independent per-stage water features cannot represent it. It argues for including an **interaction term between R1–R4 water supply and R5–R6 water deficit**, or a running water-balance state variable rather than stage-independent aggregates.

#### The contested case: Carrera et al. (2009) [C]

**Carrera, Martínez, Dardanelli & Balzarini (2009)**, Crop Science 49:990–998 — multi-environment trials, Argentina, **29–38 °S**:

- **The functions relating oil and protein to mean seed-fill temperature themselves change with water deficit** — i.e. water status is an **effect modifier**, not merely an additive term.
- Reported: when **precipitation − potential evapotranspiration during the reproductive period < 70 mm**, **oil increased linearly with increasing temperature and with rising water deficit**; protein increased linearly with temperature but **decreased** with water deficit.

**This "oil increases with water deficit" result contradicts both Rotundo & Westgate's meta-analysis and Dornbos & Mullen's controlled work, and its protein result (protein falls with water deficit) contradicts the near-universal finding that drought raises protein concentration.**

My assessment: **weight the evidence toward drought reducing oil% and increasing protein%.** Likely explanations for the discrepancy — (a) in observational field data, `P − PET` is strongly collinear with temperature and radiation, so the "water deficit" coefficient partly absorbs a thermal/radiation effect; (b) the ≤70 mm regime is a conditional subset; (c) Argentine germplasm and thermal regimes differ. **Do not encode a positive oil–drought prior.** But do take the paper's *structural* claim seriously — **temperature × water interaction is real**, and it is the single most defensible interaction term to include.

### 1.5 Solar radiation and photothermal quotient (PTQ)

[M] for oil in soybean; [E] for the analogous effect in sunflower/canola; [E] for radiation → seed number in soybean.

- Soybean-specific: variation in oil concentration related to **intercepted PAR during seed filling**; incident radiation on **leaves and pods** both contribute to seed weight and composition (Field Crops Research / European Journal of Agronomy work on incident radiation and pod-level interception).
- **Zhang et al. (2016)** (China, n=763): **oil positively correlated with sunshine hours**; protein negatively correlated. Consistent with radiation → assimilate supply → oil.
- Cross-crop support: in sunflower, reduced radiation and photothermal coefficient during grain filling **lowered both yield and oil content**; in canola, **PTQ cumulative from 100 to 500 °Cd after flowering adequately summarizes combined post-flowering abiotic stress effects on seed productivity** — a directly transferable window definition.
- **PTQ = accumulated incident (or intercepted) radiation ÷ accumulated thermal time** over a defined window. It is attractive precisely because it captures the *joint* condition "bright and cool," which is the assimilate-favorable state for oil.

**Assessment:** PTQ over the seed-fill window is a **high-value, under-exploited feature** for oil. Evidence in soybean specifically is thinner than in sunflower — treat as **[M], promising**. Compute it over **R5–R6** and also over the canola-derived **100–500 °Cd post-flowering** analogue, and let validation choose.

### 1.6 The oil–protein inverse relationship

[E] — one of the most robust facts in the field.

- Reported correlations between seed oil and protein: **r = −0.39 to −0.58 across years** and **r = −0.41 to −0.61 across regions** (US, Frontiers in Plant Science 2019).
- Note these are **moderate, not near-unity** correlations. The trade-off is a real constraint but leaves substantial independent variance — protein is **not** simply `k − oil`.
- Piper & Boote: **`protein + oil` is linear in temperature** (adj. partial R² = 0.183) while protein alone is temperature-insensitive (R² = 0.003) — so temperature acts mainly on the **sum** and on **oil**, not on protein.
- Mechanism: carbon and nitrogen partitioning during seed maturation is coordinated by pleiotropic regulators mediating metabolic trade-offs that limit dual optimization (fast-neutron mutant work, *Plant Physiology* / PMC7022410). Metabolic flux mapping shows temperature shifts flux between protein and oil biosynthesis in developing cotyledons (Iyer et al. 2008, *Plant Cell & Environment*).

**Practical slope:** given r ≈ −0.5 and the field observation that 18–19% oil pairs with 34–35% protein, an operational rule of thumb is **roughly −0.5 to −1.0 pp protein per +1 pp oil**. **I could not verify a published regression slope for this from the accessible sources — treat the slope as unverified [?] and estimate it empirically from your own paired protein/oil data**, which is the more defensible route anyway since the slope is known to vary by region and year.

**Modeling implication:** predict **oil and (protein+oil)** and derive protein, or predict both jointly with a multi-task model and a soft correlation penalty. Do **not** impose a hard `protein = k − oil` constraint; the correlation is only ~−0.5.

### 1.7 Latitude, planting date, and maturity group

[M] overall; individual magnitudes weak.

- **Delayed planting reduces oil.** Reported: **oil concentration decreased 0.007–0.06% per week of delay (R² ≈ 0.70)**, with **yield declining 0.01–0.04 Mg ha⁻¹ per week**, **mainly at northern latitudes (40–45 °N)**.
  - **[?] The oil magnitude as surfaced (0.007–0.06 pp/week) is implausibly small** — at 0.06 pp/week, a six-week planting delay moves oil by 0.36 pp, below assay noise, which is inconsistent with the same literature calling planting date a meaningful oil lever. Suspect a units/transcription issue. **Verify against Frontiers in Plant Science 2022 (PMC9618690) before use.**
- **Mechanism is thermal, and it is the right mechanism to model:** delayed planting and later maturity **push seed fill into cooler autumn conditions**, and the oil reduction was **explicitly attributed to decreased temperature during the seed-fill phase**. This is strong confirmation that **planting date and MG are not independent drivers — they are instruments that relocate the R5–R6 thermal window.**
- Latitude/MG interaction: at **southern latitudes (30–35 °N)**, moving from **MG 3 → 7** reduced oil and increased protein. At northern latitudes, delayed planting harmed protein as well.
- Recommendation from that work: **MG 3–4 when planting is delayed**, across the latitude range, to limit low-meal-protein risk.
- **[?] "Protein declined at 78 mg protein per gram per degree increase in latitude"** — as stated this is **7.8 pp per degree of latitude**, which is physically impossible (it would exhaust the seed within 5 degrees). Almost certainly a unit error in the surfaced text (plausibly 0.78 mg g⁻¹ °lat⁻¹, i.e. ~0.08 pp). **Do not use.** Latitude *does* have a well-documented negative association with protein and positive with oil in the US, but this specific coefficient is unusable.

**Modeling implication:** planting date, MG, and latitude should enter **primarily through the phenology model** that determines *when* R5–R6 occurs, and only secondarily as direct features. This is the strongest argument in the brief for building a phenology layer (§3) rather than using fixed calendar windows.

### 1.8 Nutrient effects

[M] for direction, [S] for magnitudes. All are small relative to weather effects.

- **Sulfur:** meta-analysis of **72 experiments** (Midwest, upper Plains, Mid-South US): **S at planting increased seed protein concentration ~0.3 pp** alongside a yield increase. A two-site Southeast US trial found **no total protein effect, with a protein-concentration increase at one site only** — i.e. environment-dependent. Oil effect not cleanly quantified in accessible text; expect a **small negative** oil response by the protein–oil trade-off.
- **Nitrogen:** **Rotundo & Westgate (2009)**: increased N supply (in vitro and hydroponic) **raised protein concentration and content, and slightly decreased oil concentration** [E for direction]. Field: **N applied just after pod initiation (~R5) increased protein by 6–15 mg g⁻¹ (0.6–1.5 pp) and improved yield in some environments, with a modest oil decrease** [M].
- **Ammonium sulfate increased yield 15.5% over control** in one study, attributed to combined N + S supply [S].
- **Phosphorus:** reported to enhance seed yield, essential amino acids, and unsaturated fatty acids [S] — thin evidence.

**Assessment:** nutrient effects on oil are **an order of magnitude smaller than seed-fill temperature and water effects** (≈0.3 pp vs 2–3 pp). Include S and N application as features **if the data exist at field level**, but do not invest in acquiring them ahead of weather and phenology work.

### 1.9 Summary of contested points

| Question | Positions | My assessment |
|---|---|---|
| Sign of `∂Oil/∂T` during seed fill | Positive (Piper & Boote, field) vs negative (Gibson & Mullen, Dornbos & Mullen, controlled) | **Resolved:** unimodal, peak ~28 °C. Both are right on their own side of the optimum. Use quadratic/hinge. **[E]** |
| Does drought raise or lower oil%? | Lower (Rotundo & Westgate meta-analysis; Dornbos & Mullen) vs **raise** (Carrera 2009, Argentina, P−PET<70 mm) | **Lean strongly to lower.** Carrera's positive coefficient is likely confounded with temperature/radiation collinearity in observational field data. **[C]** |
| Does drought raise or lower protein%? | Raise (near-universal) vs lower (Carrera 2009) | **Raise.** Carrera is the outlier. **[E]** |
| Oil vs MDT in Chinese data | Zhang et al.: positive only when MDT < 19.7 °C, quadratic overall — implying a much lower optimum than Piper & Boote's 28 °C | **Unresolved.** Different germplasm (NE China), cooler regime, different covariate definition (whole-season MDT vs seed-fill mean). Do not transfer the 19.7 °C breakpoint to US data. **[C]** |
| Magnitude of planting-date effect on oil | 0.007–0.06 pp/week | **Suspect transcription. [?]** Verify. |
| Latitude coefficient on protein | 78 mg g⁻¹ per °latitude | **Physically impossible as stated. [?] Discard.** |

---

## 2. Fatty acid profile weather sensitivity

Baseline commodity soybean oil profile (approximate, % of total fatty acids): **palmitic (C16:0) ~10–11, stearic (C18:0) ~4, oleic (C18:1) ~22–24, linoleic (C18:2) ~53–55, linolenic (C18:3) ~7–8.**

### 2.1 Temperature — the best-established composition–weather relationship in the crop

[E] for direction; [M] for magnitude.

**Core finding, replicated across studies, crops, and decades: higher temperature during seed fill increases oleic acid and decreases linoleic and linolenic acids.** Total saturates tend to rise modestly. The net effect is **reduced polyunsaturation → lower iodine value → higher oxidative stability.**

Supporting evidence:

- **Gibson & Mullen (1996):** increased **day** temperature during R5–R8 and R1–R8 **increased oleic, decreased linoleic and linolenic**. Increased **night** temperature at a 30 °C day **decreased oleic and increased linoleic** — night and day temperature act in **opposite directions** on the profile. [M, and important — most models would miss this]
- **Dornbos & Mullen (1992):** high temperature **reduced the polyunsaturated proportion**; drought had **little effect on overall fatty acid composition** but **increased stearic and decreased oleic**.
- Cross-crop quantification (soybean, canola, sunflower, one further oilseed): **temperature 10 → 40 °C increased oleic and decreased linoleic and linolenic**; the authors fit **linear regressions of grain-fill temperature (defined as the 30 days before harvest) against molar % of oleic, linoleic, and linolenic**, and report that these linear regressions **correctly predict molar fatty acid amounts**. [M — a directly reusable functional form and window definition]
- Magnitude anchor [S]: under high air temperature, **linolenic −19% and linoleic −5% (relative), oleic +13% (relative)**.
- Planting date as a temperature proxy, Ames, Iowa (cv. A16): linolenic **1.9% (May 1) → 2.1% (May 15) → 2.2% (May 30) → 2.4% (June 15)** — later planting → cooler fill → **higher linolenic**. A clean, monotone, internally consistent series. [S but mechanistically coherent]
- **Carrera & Dardanelli (2017)**, Crop Science 57:3179–3189: **water deficit modulates the temperature–unsaturated fatty acid relationship** — again an interaction, not additivity.
- **Alsajri et al. (2020):** significant **cultivar × temperature** interaction for **palmitic, oleic, and linolenic** — genotype-dependent response.

**Approximate magnitudes for a warm vs cool seed fill (use as priors, not as calibrated coefficients):**

| Fatty acid | Sign vs seed-fill temperature | Rough magnitude | Confidence |
|---|---|---|---|
| Oleic C18:1 | **+** | +10 to +15% relative over a warm-vs-cool contrast | [E] direction, [M] size |
| Linoleic C18:2 | **−** | −5% relative | [E] direction, [S] size |
| Linolenic C18:3 | **−** (most temperature-sensitive) | −15 to −20% relative; ~0.5 pp absolute across planting dates | [E] direction, [M] size |
| Palmitic C16:0 | **+** (weak) | small | [M] |
| Stearic C18:0 | **+** (weak; also **+ under drought**) | small | [M] |

### 2.2 Drought

[M] — weaker and less consistent than temperature.

- **Drought has little effect on overall fatty acid composition** relative to temperature (Dornbos & Mullen), but specifically **increases stearic and decreases oleic**.
- Drought during seed filling **reduced oleic acid** alongside reduced total oil [S].
- Note the **inconsistency**: drought decreases oleic, while heat increases oleic. Since heat and drought co-occur, **their fatty-acid effects partially cancel on oleic** — a real reason compound hot-dry events are hard to characterize for oil *quality*, even though they are unambiguously bad for yield. Flag this to product stakeholders: **hot-dry does not simply mean "high-oleic oil."**
- Combined stress: heat during grain development **reduces lipid unsaturation**, lowering essential fatty acid content.

### 2.3 Why this matters commercially

Profile shifts are **modest in absolute terms (typically <1–2 pp per fatty acid)** but affect:
- **Oxidative stability** (lower C18:3 → longer shelf life, less hydrogenation demand) — favorable.
- **Iodine value**, a traded specification and a biodiesel/renewable-diesel parameter (IV constrains cold-flow and stability trade-offs).
- **Nutritional/essential fatty acid claims** (lower C18:2 and C18:3 is unfavorable for food-nutrition positioning) — the same shift is good for stability and bad for nutrition.

**Realistic scope:** predicting fatty acid profile from weather is a **second-tier product feature**. Direction is reliable; magnitudes are small relative to genotype effects and to assay noise, and genotype (including specialty high-oleic and low-linolenic varieties, which swamp weather effects entirely) dominates. Do not promise profile-level accuracy without genotype data.

---

## 3. Phenology

### 3.1 Growth stage definitions (Fehr & Caviness)

The standard scale is **Fehr & Caviness (1977/1980)**, universally adopted by US extension. Vegetative stages are counted by nodes with **fully developed leaves**; reproductive staging is judged **on the four uppermost nodes with a fully developed leaf**.

**Vegetative**

| Stage | Definition |
|---|---|
| **VE** | Emergence — cotyledons above the soil surface |
| **VC** | Cotyledon — unifoliolate leaves unrolled sufficiently that leaf edges are not touching |
| **V1** | First node — fully developed leaves at unifoliolate nodes |
| **V2** | Second node — one fully developed trifoliolate leaf |
| **V(n)** | n nodes with fully developed leaves beginning with the unifoliolate node |

**Reproductive**

| Stage | Name | Definition |
|---|---|---|
| **R1** | Beginning bloom | One open flower at any node on the main stem |
| **R2** | Full bloom | Open flower at one of the two uppermost nodes with a fully developed leaf |
| **R3** | Beginning pod | Pod **3/16 in (5 mm)** long at one of the four uppermost nodes |
| **R4** | Full pod | Pod **3/4 in (2 cm)** long at one of the four uppermost nodes |
| **R5** | Beginning seed | Seed **1/8 in (3 mm)** long in a pod at one of the four uppermost nodes |
| **R6** | Full seed | Pod containing a green seed that **fills the pod cavity** at one of the four uppermost nodes |
| **R7** | Beginning maturity | **One** normal pod on the main stem has reached mature (brown/tan) color |
| **R8** | Full maturity | **95%** of pods have reached mature color |

Notes for the product: **maximum pod size is reached at ~R5; seed size increases from R5 until the cavity is filled at R6.** **Seed fill (R5–R7) lasts ~30–40 days** and accumulates 30–40% of final yield. **R5–R6 is the oil-determining window** (§1.1).

### 3.2 GDD / thermal time — what is actually established

**Base temperature: 10 °C (50 °F) is the accepted standard for soybean.** [E]

**The standard GDU form with cap and floor (Pioneer, and general US practice):**

```
GDU/day (°F) = ((min(Tmax, 86) + max(Tmin, 50)) / 2) − 50
```

i.e. **Tmax capped at 86 °F (30 °C), Tmin floored at 50 °F (10 °C)**, result floored at 0. The 86 °F cap is important and often omitted in naive implementations — it means GDD **saturates** above 30 °C, so **GDD alone cannot represent heat stress.** Extreme heat must be carried by separate features (§1.3, §4.3). **[E]**

**Verified accumulation values:**

- **NDSU / NDAWN model (Kandel et al.)**, developed 2007–2012 in northern/central/southern North Dakota, validated 2013–2015, **n = 1,816 data points**: accumulated GDD (base 50 °F) **from planting to maturity (R8)**:

| Maturity group | AGDD to R8 (°F-day, base 50 °F) | ≈ °C-day (base 10 °C) |
|---|---|---|
| **00.7** | **1,666** | ~926 |
| **0.4** | **1,862** | ~1,034 |
| **1.0** | **2,030** | ~1,128 |

  → **implies roughly +190 °F-day (~105 °C-day) per 0.5 MG increment** in this early-MG range. A usable extrapolation basis, but **fitted on MG 0–1 in North Dakota; do not extrapolate to MG 3–5 Corn Belt or MG 5–7 Mid-South material without recalibration.** [M within range, unsupported outside it]

- **Planting-date effect on GDU accumulation to R1:** variation among planting dates was **greater for later-maturity varieties (219 GDU) than earlier-maturity varieties (102 GDU)** (Pioneer). And critically: **GDDs to R1, and from R1 to R6, both *decrease* as planting is delayed.** [M]

**This last point is a serious warning for the phenology layer: soybean thermal-time requirements are not constant — they shrink with later planting, because of photoperiod.** A pure GDD model will systematically mis-time R5 for late plantings. Photoperiod must be in the model.

**Gaps I could not close [flagged honestly]:** I was **unable to verify a stage-by-stage GDD table (planting→R1, R1→R5, R5→R7) by maturity group** from an authoritative source. Values circulating informally (e.g. "~700 GDU base 50 to R1, ~1,400 to R5") appeared in **none** of the accessible sources and **should not be used**. The correct sources to consult with full-text access:
- **NDAWN soybean GDD documentation** (ndawn.ndsu.nodak.edu) — full table.
- **Kandel et al. (2017)**, *Agricultural and Forest Meteorology* — "Developing a growing degree day model for North Dakota and Northern Minnesota soybean."
- **Setiyono et al. (2007)**, *Field Crops Research* — the SoySim phenology basis.
- **Santos et al. (2019)**, *Agricultural & Environmental Letters* — "Soybean Phenology Prediction Tool for the US Midsouth."
- **SoyStage** (soystage.uark.edu) — predicts **R1, R5, R7 for MG 3–6 in half-MG increments**; the best-matched operational tool for Mid-South/Corn Belt and worth benchmarking the phenology layer against.

### 3.3 Photoperiod sensitivity

[E] qualitatively; parameter values [M].

- Soybean is a **quantitative short-day plant**: flowering requires days shorter than a critical value; **long days delay flowering.**
- **Maturity groups are defined by photoperiod response** — later MG = greater photoperiod sensitivity.
- **Critical daylength ranges from ~13 h** for tropical-adapted genotypes **to effectively photoperiod-insensitive** for high-latitude material.
- **Juvenile insensitivity:** plants are **insensitive to daylength for ~9 days after emergence**.
- **Induction requirement:** photoperiods shorter than the critical daylength are needed for **7 to 26 days** to complete floral induction.
- **Photoperiod sensitivity persists after flowering** and affects seed number determination (Field Crops Research work on post-flowering photoperiod sensitivity) — so photoperiod is not only a pre-R1 concern.

**Recommended phenology approach:** a **photothermal** model, not pure GDD — daily development rate as the product of a temperature function (cardinal temperatures **Tbase ≈ 10 °C, Topt ≈ 30 °C, Tmax ≈ 40 °C**; note these specific cardinal values were **not verifiable** in accessible sources and are the standard values used in CROPGRO/SoySim-family models — verify against Setiyono et al. 2007 [?]) and a photoperiod function parameterized by MG. This is exactly the structure of **CROPGRO-Soybean** and **SoySim** (§5), both of which are validated and available — **strongly prefer adopting one of these over building a bespoke GDD model.**

---

## 4. Yield drivers

### 4.1 Critical period for yield determination

[E] for R3–R6 seed number; [M] for the recent R4–R7 redefinition.

- **Seed number per unit area is the yield component most closely correlated with yield variation** (Egli 1998). [E]
- **Classical critical period: R3 (beginning pod) → R6 (full seed)** for seed number determination. [E]
- **Recent refinement** ("Redefining soybean critical period for yield determination," *Field Crops Research* 2024): **the critical period for overall yield extends R4 → R7 with peak sensitivity around R5**, while **grain-number sensitivity spans R3 → R6**. [M — recent, single-team]
- **Accumulated dry matter during the critical period is a better predictor of seed number than crop growth rate**, because it accounts for differences in critical-period **duration** driven by weather and management. [M] — favors **cumulative** rather than **rate-based** features.
- Within the critical period, **accumulated solar radiation during pod setting is closely associated with seed number** when water and nutrients are non-limiting. [E]
- Seed number relates to **R3–R6 duration** (especially when temperature-corrected) **and to the integral of solar radiation over R3–R6**. [E]

**Two-window structure for the model:** **R3–R6 → seed number → yield**; **R5–R6(–R8) → seed composition → oil%.** These windows overlap but are not identical, and features should be aggregated separately for the yield head and the oil head.

### 4.2 Precipitation, especially August

[E] that August precipitation is the dominant single precipitation predictor in the US Corn Belt.

- August precipitation is the **most dominant weather parameter for soybean yield, followed by July maximum temperature** (ML feature-importance analysis).
- Soybean yields most affected by **technology and June–August precipitation magnitude, especially August** (Illinois farmdoc / Univ. of Illinois analysis).
- **USDA WAOB operational model** uses, for seven states (IA, IL, MN, NE, IN, OH, MO): **June precipitation shortfall, July precipitation, August precipitation, July temperature, August temperature**, plus a yield trend. This is the closest thing to an **official benchmark specification** and its variable list is a defensible starting feature set.
- Magnitude: **about two-thirds of the time, the impact of either August or July precipitation on US average soybean yield falls within a range of slightly more than 2 bu/ac** (farmdoc daily, 2023).
- A reported **temperature coefficient of −0.514** for July–August temperature appears in the farmdoc analysis. **[?] Units not established** in the surfaced text (bu/ac per °F is the plausible reading). Verify before use.
- **Why August:** August in the Corn Belt is precisely when **R5–R6 seed fill** occurs for typical MG 2–3 plantings. **The "August precipitation effect" is not a calendar fact — it is the seed-fill water-supply effect observed through a fixed calendar window.** For a model with a phenology layer, **replace calendar-August aggregates with R5–R6 aggregates**; this should strictly dominate, and it is the main mechanism by which your product can beat the WAOB-style benchmark.

### 4.3 Heat stress thresholds

[E] for the 30 °C statistical threshold; [E] for asymmetry.

- **Schlenker & Roberts (2009)**, *PNAS* 106:15594–15598 — the canonical reference. County-level yield panel with fine-scale within-day temperature distributions:
  - **Yields increase with temperature up to 29 °C (maize), 30 °C (soybean), 32 °C (cotton); above these thresholds temperatures are very harmful.**
  - **The slope of decline above the optimum is significantly steeper than the incline below it** — strong asymmetry. Encode with **separate below-/above-threshold terms (GDD and "extreme degree days"/EDD), never a single symmetric quadratic.**
  - Projected losses of **30–82%** by end of century depending on scenario.
- **Schauberger et al. (2017)**, *Nature Communications* 8:13931 — "Consistent negative response of US crops to high temperatures in observations and crop models": **each day >30 °C diminishes maize and soybean yields by up to 6% under rainfed conditions.** [M — verify whether the ~6% figure is soybean-specific or maize-led]
- Reproductive physiology thresholds: **≥35 °C** for flower/pod abortion and pollen function; **35–38 °C** for pollen germination, anther dehiscence, stigma receptivity. **35 °C for 10 h/day → ~27% yield loss** [S].
- Warming sensitivity estimates, whole-season: reported values range from **~2.4% yield loss per °C** to **~7.7% per °C (4.3% per °F)**. **Treat the spread as genuine methodological disagreement, not measurement error** — estimates differ in region, period, and whether genetic gain is controlled. **[C on magnitude, E on sign]**
- Regional heterogeneity is large: **state-level responses ranged −22% to +9%** (Mourtzinis et al. 2014/2015, *Nature Plants*) and varied by **month in which warming occurred.** [E] **This is a strong argument for region-specific models or region interactions rather than one national model.**

### 4.4 Vapor pressure deficit

[M] rising toward [E]; increasingly regarded as more informative than temperature or precipitation alone.

- **VPD during 61–90 days after planting (DAP) was the most important predictor of soybean yield** in US Midwest analysis — the analogue of Lobell et al. (2014), who found VPD at 60–90 DAP the most important driver of maize yields (1995–2012) in a dataset also containing temperature and precipitation. [M]
- **Maize and soybean yields in Iowa, Illinois, and Indiana have become increasingly sensitive to high VPD** over time, partly offset by agronomic advances (Lobell and colleagues). [M]
- **Projected long-term climate trends reveal the critical role of VPD for soybean yields in the US Midwest** (*Science of the Total Environment*, 2023): **multivariate adaptive regression splines (MARS)** used to generate **yield response curves for VPD** — a **nonlinear** response, so include splines/tree models rather than a linear VPD term. [M]
- Mechanism: increased **extreme degree days (hourly temperatures >30 °C)** co-occurring with **increased VPD** drives water deficit and crop loss.

**Note that 61–90 DAP corresponds approximately to the R3–R5 window** for typical Corn Belt plantings — again a calendar proxy for a phenological window, and again an opportunity for a phenology-aware model to improve on the literature.

### 4.5 Compound hot-dry extremes

[E] for super-additivity.

- **Hamed, Van Loon, Aerts & Coumou (2021)**, *Earth System Dynamics* 12:1371–1391 — county-scale US soybean, **1982–2016**:
  - **Compound August hot–dry conditions produce the largest yield impacts, beyond the additive effects of each stressor separately.**
  - Soybean yields **most negatively influenced by the combination of high temperature and low soil moisture during the summer reproductive period.**
  - Context: **~90% of US soybean is rainfed**, and the US supplies **>1/3 of globally traded soybean.**

**Modeling implication — this is not optional:** an additive model of heat and drought will **underestimate** losses in exactly the years that matter most commercially. **Include explicit heat × water-deficit interaction terms** (or use tree ensembles / GBMs that capture interactions natively) and validate specifically on compound-extreme years (1988, 2012, and recent flash-drought years).

---

## 5. Existing models — what to benchmark against

### 5.1 Process-based crop models

| Model | Basis | Simulates composition? | Notes |
|---|---|---|---|
| **CSM-CROPGRO-Soybean** (DSSAT) | Daily C/N balance, photothermal phenology, cultivar coefficients | **Yes** | Simulates **seed oil and protein concentration from a carbon and nitrogen balance with cultivar-specific target protein and oil concentrations and an explicit temperature effect.** Validation reports it **predicted yield and oil concentration efficiently across new environments.** **The single most relevant existing model for this product.** |
| **SoySim** (Univ. of Nebraska; Setiyono et al. 2010) | Photothermal phenology + growth; **minimal cultivar-specific input** | Not primarily | Developed/validated for **relative MG 0.8–4.2** in US temperate conditions; later extended to higher MGs. Predicts development as a **joint function of photoperiod and temperature**. Best-in-class phenology reference. |
| **APSIM-Soybean** | Daily C balance, water/N modules | Limited | Widely used for yield and climate studies; composition support weaker than CROPGRO. |
| **GLYCIM** | Detailed soil–water–plant, process-rich | No | Compared against SoySim and CROPGRO in Mississippi Delta model-comparison work. |
| **EPIC** | Generic crop/erosion model | No | Appears in soybean phenology/yield comparisons. |

Model-comparison studies exist for the **US Mississippi Delta** (*European Journal of Agronomy* 2022) and near-optimal growth conditions (*Field Crops Research* 2010). **CROPGRO and SoySim have both been "satisfactorily evaluated" for reproducing measured yields in well-managed experiments** — but note that phrase: *well-managed experiments*, not commercial fields. Expect degradation on farm data.

**Recommendation:** do not rebuild phenology or composition physiology from scratch. Use **CROPGRO-Soybean (via DSSAT) or SoySim as the phenology engine and as a source of derived state variables** (simulated seed growth rate, water stress factor, N stress factor, fill duration), then feed those as engineered features into the ML layer. This **hybrid crop-model + ML** design is well-supported in the literature (see "Coupling Machine Learning and Crop Modeling Improves Crop Yield Prediction," arXiv 2008.04060) and typically beats either alone, because the crop model supplies mechanistically correct nonlinearities and stage timing that the ML layer would otherwise have to learn from limited data.

### 5.2 Published ML yield-prediction accuracy — benchmark table

| Study | Method | Data | Reported accuracy |
|---|---|---|---|
| **Khaki, Wang & Archontoulis (2020)**, *Front. Plant Sci.* — CNN-RNN | CNN-RNN, weather + soil + management | US county-level, ~13 states | **RRMSE 8% for soybean** (9% maize) |
| **Khaki & Wang (2019)**, *Front. Plant Sci.* — DNN | Deep NN vs Lasso, shallow NN, regression tree | Syngenta hybrid trial data | **RMSE 12.81 kg/1000 m², r = 0.81** (**maize hybrids** — do not cite as soybean) |
| **Sun et al. (2019)** — county-level deep CNN-LSTM | CNN-LSTM; crop-growth + weather + MODIS LST + MODIS surface reflectance | US county | End-of-season **and in-season** soybean yield |
| Weather-driven RF (2024, *Comput. Electron. Agric.*) | Random Forest | Regional | **R² = 0.64, RMSE = 0.21 Mg/ha** for soybean |
| **"Harvesting insights" (2026)**, *Sci. Reports* 16:8994 | ML + **SHAP** interpretation; weather, soil, terrain | **Yield-monitor data, 134 crop-site-years**, US commercial fields | **R² > 0.87, RMSE < 1.13 Mg/ha** |
| **Shook et al. (2021)** | **LSTM-RNN**, genotype × weather | **13 years US/Canada Uniform Soybean Test** | Predicts **yield, oil, protein, maturity, plant height, seed size**; isolates key weather events and G×E in unseen environments |

**Read the 0.87 with care.** That study predicts **within-field, sub-field yield variability** from yield-monitor data where **soil and terrain** carry much of the signal — it is not comparable to predicting a county or field mean from weather. **For county/field-mean soybean yield from weather, the honest state of the art is R² ≈ 0.6–0.75 / RRMSE ≈ 8–10%.** Target that band, and be explicit with stakeholders about which task the metric describes. Conflating these two numbers is the most likely way to set an unachievable internal accuracy target.

### 5.3 Published ML accuracy for seed **composition** — the directly relevant benchmark

| Study | Method | Target | Reported accuracy |
|---|---|---|---|
| **"On-farm soybean seed protein and oil prediction using satellite data" (2023)**, *Comput. Electron. Agric.* 212:108096 | **XGBoost** (best), GRU, others; satellite + weather | Oil, protein | **XGBoost absolute error 1.04% for oil, 1.80% for protein**; **GRU: oil R² = 0.53, NRMSE 4.78%; protein R² = 0.36, NRMSE 3.62%** |
| **"Soybean seed composition prediction from standing crops using PlanetScope satellite imagery and ML" (2023)**, *ISPRS J. Photogramm. Remote Sens.* | ML on high-cadence satellite | Composition | Standing-crop, pre-harvest prediction |
| **Shook et al. (2021)** | LSTM-RNN, genotype + weather | Oil, protein, yield | Genotypic response prediction in unseen environments |
| **Van der Laan et al. (2025)**, *The Plant Genome* | Genomic + phenomic prediction | Yield, protein, oil | Genomic-prediction framing |
| **Naeve/Ray et al.** — "A method to estimate soybean seed protein and oil concentration before harvest," *JAOCS* (2004) | Pre-harvest estimation | Oil, protein | Pre-ML precedent |

**This is the number that should anchor product expectations:**

> **Best published on-farm oil-concentration prediction: R² ≈ 0.53, absolute error ≈ 1.0 pp oil.** Protein is harder (**R² ≈ 0.36**).

Given oil ranges ~18–23% (a ~5 pp spread), **~1 pp absolute error is roughly 20% of the useful range.** That is genuinely useful for blending, procurement, and crush-margin forecasting — but it is **not** precision analytics, and it is consistent with the physiological constraints: Piper & Boote found temperature explains **R² = 0.24** of oil variation, and Rotundo & Westgate explicitly warned that **field-manipulable effects are far smaller than the observational variation**, most of which is genotype and unmeasured management.

**Beating R² ≈ 0.53 will most likely require non-weather information — genotype/variety, and remote sensing — not better weather features alone.** Plan the data roadmap accordingly.

### 5.4 Realistic accuracy expectations — summary

| Target | Weather-only realistic | With genotype/MG | With remote sensing added |
|---|---|---|---|
| Field/county yield | R² 0.5–0.7 | +0.05–0.1 | R² 0.7–0.8 |
| Oil % | **R² 0.3–0.45** | 0.45–0.55 | **0.5–0.6** (published SOTA ≈ 0.53) |
| Protein % | R² 0.2–0.3 | 0.3–0.4 | ~0.36 (published) |
| Fatty acid profile | Low; direction only | Genotype dominates | Marginal |

---

## 6. Processing relevance

### 6.1 The reference crush and why oil% matters

**Standard 60-lb bushel yields: ~11 lb soybean oil, ~44 lb of 48%-protein meal (alternatively quoted as ~47.5 lb of 44%-protein meal), ~4 lb hulls, ~1 lb waste.** Per ton: **~190 kg oil and ~780 kg meal.** [E]

**CME board crush:** `Crush Spread = (Meal price × 0.022) + (Oil price × 11) − Soybean price`, embedding the 44 lb meal / 11 lb oil convention. [E]

**Extraction rates have risen materially and are demand-responsive:** historical **17.9% → 19.8%**; modern facilities **~18.5%** on some accountings; **July 2025 actual 20.0% vs trendline 19.6%**, a **+0.4 pp "crush composition effect"** attributed to renewable-diesel-driven oil demand, worth **+43.9 million lb of oil in that month alone.** [M]

**Key economic point:** at ~4.4 billion bushels of US crush-scale supply, **1 pp of oil concentration ≈ 0.6 lb of oil per bushel** — at typical soybean-oil prices this is a **first-order margin item**, which is precisely why an oil-concentration forecast has commercial value. **Estimated Processed Value (EPV)** is the formal framework: EPV = summed values of oil, protein/meal, and hulls, computed via **NOPA** or **HY+Q** methods, both of which multiply **yield, oil %, and protein %** by market prices. A weather-driven oil/protein forecast feeds directly into a forward EPV and crush-margin forecast — **that is the cleanest commercial articulation of this product.**

### 6.2 Extraction yield and residual oil

- Hexane solvent extraction leaves **<1% residual oil**; efficient modern plants target **residual oil ≤0.5% in meal**. [E]
- **Because extraction is near-complete, oil recovered per bushel is nearly linear in seed oil concentration.** There is no meaningful "extractability" nonlinearity to model for solvent plants — **seed oil% is the operative variable.** (Mechanical/expeller plants differ, retaining far more residual oil, and serve premium organic/non-GMO markets.)
- Meal protein is the complementary constraint: **seed at 18–19% oil must carry 34–35% protein to make a 44%-protein meal.** Low-protein seed forces meal downgrades or blending, so **the protein forecast has independent commercial value even though it is harder to predict.** [E]

### 6.3 Free fatty acids (FFA) — the main weather-and-handling-mediated quality risk

Per the **USSEC Soybean Oil Quality fact sheets** (FFA, Neutral Oil Loss, Refining):

- **FFA concentration in crude degummed soybean oil (CDSBO) is a function of whole-soybean quality — damage, splits, and moisture — plus storage conditions and post-harvest handling.** [E]
- **FFA affects odor, flavor, rancidity, and shelf life of RBD soybean oil.** [E]
- **Higher FFA → higher alkali dosage → higher neutral oil loss** (i.e. **direct refining yield loss**), plus increased steam and heat consumption, longer deodorization, and greater splash losses. [E]
- **Field- and storage-damaged soybeans** show, with increasing damage severity, **increasing FFA, Lovibond color, and 270 nm-absorbing oxidative deterioration products**; processing such beans causes **substantial refining losses and inferior finished oil** (*JAOCS*, chemical evaluation of field- and storage-damaged soybeans). [E]

**Weather linkage — and its limits:** FFA is driven by **seed damage and moisture**, which are functions of **late-season and harvest-period weather** (rain and humidity at R7–R8 and during harvest delay, field weathering, alternating wet/dry cycles, heat during storage) rather than of seed-fill temperature. **This is a distinct third prediction target with a distinct weather window (R7–R8 and post-maturity/harvest), and it is largely orthogonal to the oil-concentration problem.** I did **not** find a published quantitative weather→FFA model; treat FFA prediction as **exploratory** and note that USSEC's specific numeric FFA specification limits could not be retrieved (blocked PDFs) — obtain the fact sheets directly.

### 6.4 Moisture

- Moisture is both a **quality driver** (higher moisture → mold, heating, FFA development in storage) and a **units issue** (§0.3). Commercial settlement is at **13% moisture**; oil and protein specifications must state their basis.
- Moisture at harvest is weather-driven (late-season rain, drydown rate) and is a legitimate secondary model target with direct economic value (drying cost, shrink, discount schedules).

### 6.5 Fatty acid profile in processing

- Lower **linolenic (C18:3)** → better oxidative stability, less hydrogenation/interesterification demand → **favorable for food oil and shelf life**.
- **Iodine value** (a direct function of unsaturation) is a traded specification and a **biodiesel/renewable-diesel parameter**, constraining cold-flow vs oxidative-stability trade-offs.
- Warm, dry seed fill therefore tends to produce **more stable but less nutritionally "essential-fatty-acid-rich"** oil — a trade-off, not a straightforward quality gain (§2.3). Note again the heat/drought cancellation on oleic (§2.2).

---

## 7. Modeling implications — candidate feature specification

### 7.1 Design principles (in priority order)

1. **Build a phenology layer first.** Almost every important relationship in this brief is defined on a **stage window (R5–R6, R3–R6, R7–R8)**, and the canonical calendar findings ("August precipitation," "VPD at 61–90 DAP") are *calendar proxies for those windows*. A phenology layer converts a proxy into the actual causal variable. **This is the highest-leverage engineering decision and the clearest route to beating published benchmarks.** Use CROPGRO-Soybean or SoySim; validate against SoyStage (MG 3–6).
2. **Never use raw GDD to represent heat stress.** The standard GDU form caps Tmax at 30 °C, so GDD **saturates** exactly where damage begins. Heat must be carried by separate above-threshold features.
3. **Encode asymmetry and nonlinearity explicitly.** Below-optimum and above-optimum temperature effects have different slopes (Schlenker & Roberts). Use hinges/splines or tree ensembles, never a single symmetric quadratic for the yield head.
4. **Encode heat × water interaction.** Compound hot-dry is super-additive (Hamed et al. 2021). Additive models fail in the highest-impact years.
5. **Separate the model heads.** Yield (R3–R6, seed number) and oil% (R5–R6/R8, composition) have different windows, different optima (yield optimum 23–26 °C; oil optimum ~28 °C), and different achievable accuracy. Do not share a feature aggregation.
6. **Model `protein + oil` and the split, not protein and oil independently.** The sum is linear in temperature; protein alone is nearly temperature-insensitive (R² = 0.003).
7. **Include genotype/MG if at all obtainable.** Cultivar × temperature interactions are significant for oil and for fatty acids, and genotype dominates the composition variance that weather cannot explain.
8. **Region-specific models or region interactions.** State-level warming responses span −22% to +9%.

### 7.2 Candidate features for the OIL % head

| # | Feature | Window | Aggregation | Expected sign on oil% | Confidence | Source basis |
|---|---|---|---|---|---|---|
| O1 | **Mean daily temperature** | **R5–R6** (extend R5–R8 as variant) | mean, **plus quadratic term or hinge at 28 °C** | **+ below 28 °C, − above** | **[E]** shape | Piper & Boote 1999; Gibson & Mullen 1996 |
| O2 | Mean **Tmin** (night temperature) | R5–R6 | mean | **−** | [M] | Gibson & Mullen 1996; DTR studies |
| O3 | **Diurnal temperature range (DTR)** | R5–R6 | mean | **+** | [M] | Zhang et al. 2016 (path analysis: DTR = main direct factor) |
| O4 | **Days with Tmax > 30 / 32 / 35 °C** | R5–R6 | counts (3 separate features) | **−** | [M] | Dornbos & Mullen 1992; heat-threshold literature |
| O5 | **Heat degree-days above 30 °C** (EDD) | R5–R6 | Σ(Tmax − 30)⁺ | **−** | [M] | Schlenker & Roberts 2009 form |
| O6 | **Stress degree days** (Dornbos & Mullen form) | R5–R6 | accumulated | **−**, approx. **linear** | [M] | Dornbos & Mullen 1992 (explicitly linear) |
| O7 | **Precipitation** | **R5–R6** | total | **+** | [E] | Rotundo & Westgate 2009 |
| O8 | **P − PET** (or P − ET₀) water balance | R5–R6 and whole reproductive period | sum; **also a <70 mm indicator** | **+** (deficit → lower oil) | **[C]** — Carrera 2009 reports opposite | Rotundo & Westgate 2009 vs Carrera 2009 |
| O9 | **Soil moisture / plant-available water** | R5–R6 | mean, and minimum | **+** | [M] | Hamed et al. 2021; Rotundo & Westgate 2010 |
| O10 | **VPD** | R5–R6 | mean and max | **−** | [M] | STOTEN 2023; Lobell et al. 2014 |
| O11 | **Incident solar radiation** | R5–R6 | total | **+** | [M] | Zhang et al. 2016 (sunshine hours); radiation-interception work |
| O12 | **Photothermal quotient (PTQ)** | R5–R6 **and** 100–500 °Cd post-flowering | Σ radiation ÷ Σ thermal time | **+** | [M] — promising, under-tested in soybean | canola/sunflower PTQ literature |
| O13 | **Seed-fill duration** (R5→R7, days and °Cd) | derived from phenology layer | days; °Cd | **+** (longer fill → more oil) | [M] | Rotundo & Westgate 2010 (rate/duration); Egli |
| O14 | **Temperature × water-deficit interaction** | R5–R6 | product or tree interaction | interaction, **negative** in hot-dry | **[E]** that interaction exists | Carrera 2009; Hamed et al. 2021 |
| O15 | **Latitude** | static | — | **+** (higher lat → higher oil, lower protein) | [M]; specific published coefficient is **[?]** | US composition surveys |
| O16 | **Planting date** and **maturity group** | static | — | later planting → **−** oil | [M]; magnitude **[?]** | Frontiers 2022 planting-date/MG analysis |
| O17 | **Calendar-date of R5** (derived) | derived | day-of-year | later R5 → **−** (cooler fill) | [M] — the mechanism behind O16 | same |
| O18 | **Genotype / variety / MG** | static | categorical + interaction with O1 | genotype-dependent | **[E]** interaction is significant | Alsajri et al. 2020 |
| O19 | **S and N fertilization** (rate, timing) | management | — | **−** small (protein ↑, oil ↓) | [M], effect ~0.3 pp — low priority | S meta-analysis (72 expts); Rotundo & Westgate 2009 |
| O20 | Mean temperature during **R1–R4** | R1–R4 | mean | **≈ 0 (near-null control)** | **[E]** that it is small | Gibson & Mullen 1996 (R1–R5 little effect) |

**O20 is deliberately included as a negative control.** Gibson & Mullen showed R1–R5 temperature has little effect on composition. If your fitted model assigns O20 a large coefficient, that is strong evidence of **leakage or collinearity with O1**, not a discovery. Use it as a diagnostic.

### 7.3 Candidate features for the YIELD head

| # | Feature | Window | Aggregation | Expected sign | Confidence | Source basis |
|---|---|---|---|---|---|---|
| Y1 | **Accumulated solar radiation** | **R3–R6** | total | **+** | **[E]** | critical-period seed-number literature |
| Y2 | **Critical-period duration** | R3–R6 (and R4–R7) | days; °Cd | **+** | [E] | Field Crops Res. 2020, 2024 |
| Y3 | **Growing degree days, base 10 °C, capped at 30 °C** | planting→R8, and per stage | Σ | **+** (to optimum) | [E] | standard GDU form |
| Y4 | **Extreme degree days above 30 °C** | R1–R6, and whole season | Σ(T − 30)⁺ | **−**, **steeper than the positive GDD slope** | **[E]** | Schlenker & Roberts 2009 |
| Y5 | **Days Tmax > 35 °C** | **R1–R4** (flower/pod abortion) | count | **−** | [E] | pollen/abortion literature |
| Y6 | **Days Tmax > 30 °C** | R5–R6 | count | **−** (~up to 6%/day, rainfed) | [M] | Schauberger et al. 2017 |
| Y7 | **August precipitation** (or better, **R5–R6 precipitation**) | Aug / R5–R6 | total | **+** | **[E]** | WAOB model; farmdoc; ML feature importance |
| Y8 | **July precipitation**, **June precipitation shortfall** | calendar | total / deficit | **+** / **−** | [E] | USDA WAOB specification |
| Y9 | **July and August mean temperature** | calendar | mean | **−** in warm regions | [E] | WAOB; farmdoc (coefficient −0.514, units **[?]**) |
| Y10 | **VPD** | **61–90 DAP** and R3–R5 | mean, max | **−**, **nonlinear** | [M] | STOTEN 2023 (MARS response curves) |
| Y11 | **Soil moisture** | R3–R6 | mean, min, days below threshold | **+** | [E] | Hamed et al. 2021 |
| Y12 | **Compound hot-dry indicator** | **August / R5–R6** | joint (hot AND dry) indicator or interaction | **−, super-additive** | **[E]** | Hamed et al. 2021 |
| Y13 | **Early-season excessive precipitation** | planting→V-stages | total; days above threshold | **−** (establishment, root, disease, leaching) | [M] | Mourtzinis et al. |
| Y14 | **Late-season minimum temperature** | R6–R8 | mean | driver of variability; sign region-dependent | [M] | Mourtzinis et al. |
| Y15 | **Mean temperature during seed fill** | R5–R7 | mean; quadratic | optimum **23–26 °C**; **−** above; seed growth → 0 at 39 °C | [M] | Alsajri et al. 2020; seed-growth-rate work |
| Y16 | **Water stress × heat interaction** | R3–R6 | interaction | **−** | [E] | Hamed et al. 2021 |
| Y17 | **Technology/year trend** | — | linear or spline in year | **+** | **[E] — mandatory** | all yield-panel literature |
| Y18 | **Soil AWC, texture, terrain/topography** | static | — | + / context | **[E]**, and dominant at sub-field scale | Sci. Reports 2026 (SHAP) |
| Y19 | **Irrigated flag** | static | — | **+**, and **modifies all water features** | [E] | ~90% US soybean rainfed |

**Y17 is not optional.** Every credible yield-panel study includes a technology trend; omitting it will load secular genetic gain onto whichever weather variable happens to trend, producing a confidently wrong model.

### 7.4 Candidate features for the FATTY ACID head (secondary priority)

| # | Feature | Window | Expected signs | Confidence |
|---|---|---|---|---|
| F1 | Mean temperature | **R5–R8** (and the literature's "30 days before harvest" variant) | **oleic +; linoleic −; linolenic − (most sensitive); palmitic + weak; stearic + weak** | [E] direction |
| F2 | **Tmin / night temperature** | R5–R8 | **oleic −; linoleic +** (opposite to day temperature) | [M] — distinctive, worth testing |
| F3 | Water deficit | R5–R6 | **stearic +; oleic −** | [M] |
| F4 | Temperature × water interaction | R5–R6 | modifies the unsaturated profile | [M] | Carrera & Dardanelli 2017 |
| F5 | Planting date / date of R5 | derived | later (cooler) fill → **linolenic +** | [S], mechanistically coherent |
| F6 | Genotype | static | **dominant** — specialty high-oleic / low-linolenic lines swamp all weather effects | [E] |

### 7.5 Candidate features for the FFA / QUALITY head (exploratory)

| # | Feature | Window | Expected sign on FFA | Confidence |
|---|---|---|---|---|
| Q1 | Precipitation, rain days, humidity | **R7–R8 and post-maturity / pre-harvest** | **+** | [M] mechanism, no published model |
| Q2 | Harvest delay (days R8 → harvest) | post-R8 | **+** | [M] |
| Q3 | Wet/dry cycling, field weathering index | post-R8 | **+** | [S] |
| Q4 | Seed moisture at harvest | harvest | **+** | [E] mechanism |
| Q5 | Mechanical damage / splits proxy | harvest | **+** | [E] mechanism |
| Q6 | Storage temperature and duration | post-harvest | **+** | [E] mechanism |

**Flag clearly:** no published quantitative weather→FFA model was located. Q1–Q6 are **mechanistically motivated hypotheses**, not literature-calibrated features.

### 7.6 Validation protocol recommendations

1. **Spatially and temporally blocked cross-validation** — leave-one-year-out **and** leave-one-region-out. Random CV will leak badly given spatial autocorrelation in weather and will overstate accuracy substantially.
2. **Hold out compound-extreme years explicitly** (1988, 2012, and recent flash-drought years) and report performance on them separately. A model that is accurate on average and wrong in 2012 is commercially worthless for risk management.
3. **Benchmark against the USDA WAOB variable set** (June precip shortfall, July/Aug precip, July/Aug temp, trend, 7 states) as a **minimum-viable baseline**. If the phenology-aware model does not beat it, the phenology layer is not earning its complexity.
4. **Report against published SOTA explicitly:** yield RRMSE ~8% (CNN-RNN), oil R² ~0.53 / ~1.0 pp absolute error (satellite XGBoost).
5. **Normalize all composition labels to a single moisture basis before training** (§0.3) and record the basis in the schema. This is the most likely source of a silent, large, systematic error.
6. **Test the O20 negative control** (R1–R4 temperature should be near-null for oil).
7. **Check O8's sign empirically** rather than assuming — it is the one genuinely contested sign in the oil feature set.

---

## 8. Evidence assessment and verification priorities

### 8.1 Well-established (safe to build on)

- Oil–protein inverse relationship (r ≈ −0.4 to −0.6). [E]
- Oil concentration is determined during **R5–R8**, not during flowering. [E]
- Temperature–oil response is **unimodal, optimum ~25–28 °C**. [E]
- Drought during seed fill **raises protein % and lowers oil %** (a partitioning/dilution effect). [E]
- Higher seed-fill temperature → **oleic ↑, linoleic ↓, linolenic ↓**. [E]
- Soybean GDD **base 10 °C (50 °F)**, standard form caps Tmax at 30 °C (86 °F). [E]
- Soybean is a **quantitative short-day plant**; MG is defined by photoperiod response. [E]
- Critical period for seed number **R3–R6**; seed number is the yield component best correlated with yield. [E]
- **Yield threshold ~30 °C** with a **steeper above-threshold slope** (Schlenker & Roberts). [E]
- **August precipitation** is the dominant single precipitation predictor of US soybean yield. [E]
- **Compound hot-dry is super-additive.** [E]
- Solvent extraction is near-complete (residual ≤0.5–1%), so **recovered oil ≈ linear in seed oil %**. [E]
- FFA is driven by **seed damage, splits, moisture, storage/handling**, and directly increases refining loss. [E]

### 8.2 Moderately supported (use, but validate)

- Night temperature / DTR carry information independent of the mean (DTR **positive** for oil).
- PTQ during seed fill as an oil predictor (strong in sunflower/canola, thinner in soybean).
- VPD at 61–90 DAP as the leading soybean yield predictor.
- NDSU AGDD-to-R8 values (1,666 / 1,862 / 2,030 °F-day for MG 00.7 / 0.4 / 1.0) — **valid only for MG 0–1 in ND/northern MN**.
- Thermal-time requirements **shrink with delayed planting** (photoperiod effect).
- Sulfur ≈ +0.3 pp protein; N at R5 ≈ +0.6–1.5 pp protein with modest oil decrease.
- Yield optimum temperature 23–26 °C, and its dependence on determinacy.
- Delayed planting reduces oil **via a cooler seed-fill window** (mechanism [M]; magnitude [?]).

### 8.3 Single-study or weak — do not treat as constraints

- 35 °C for 10 h/day → ~27% yield loss.
- Drought during seed fill → oil −12.4% relative.
- High night temperature → −42% yield (one genotype).
- 2023/2024 flower-abortion percentages.
- Specific relative fatty-acid magnitudes (linolenic −19%, linoleic −5%, oleic +13%).
- Ammonium sulfate +15.5% yield.
- Phosphorus effects on unsaturated fatty acids.

### 8.4 Contested — resolve before encoding

- **Sign of drought effect on oil %** (Rotundo & Westgate / Dornbos & Mullen **negative** vs Carrera 2009 **positive**). Lean negative.
- **Sign of drought effect on protein %** (Carrera 2009 is the outlier). Lean positive.
- **Location of the oil–temperature optimum** (Piper & Boote ~28 °C vs Zhang et al.'s implied ~19.7 °C breakpoint in Chinese data).
- **Whole-season warming sensitivity magnitude** (2.4% to 7.7% per °C).

### 8.5 Suspect numbers — do not use until verified [?]

1. **"Protein declines 78 mg g⁻¹ per degree latitude"** — implies 7.8 pp/°lat; physically impossible. **Discard.**
2. **"Oil decreased 0.007–0.06% per week of delayed planting"** — implausibly small relative to the paper's own conclusions. Verify.
3. **farmdoc temperature coefficient "−0.514"** — units unstated.
4. **Cardinal temperatures 10 / 30 / 40 °C** for the photothermal model — standard in CROPGRO/SoySim-family models but **not verified** in an accessible source.
5. **"~6% yield loss per day >30 °C"** — confirm whether soybean-specific or maize-led in Schauberger et al. 2017.
6. **USSEC numeric FFA specification limits** — not retrievable; obtain the fact sheets.

### 8.6 Prioritized full-text verification list

Retrieve these in order; each unlocks a coefficient the model needs:

1. **Piper & Boote (1999)**, *JAOCS* 76:1233–1241 — **the actual quadratic coefficients** for oil vs seed-fill temperature. *Highest value single item in this list.*
2. **Rotundo & Westgate (2009)**, *Field Crops Res.* 110:147–156 — **meta-analysis effect sizes** for water, temperature, N on oil and protein.
3. **Carrera et al. (2009)**, *Crop Sci.* 49:990–998 — the **temperature × water-deficit functional forms** and the sign dispute.
4. **Gibson & Mullen (1996)**, *JAOCS* — full **day/night × stage** composition and fatty-acid tables.
5. **Dornbos & Mullen (1992)**, *JAOCS* 69:228–231 — the **stress-degree-day linear regressions**.
6. **Kandel et al. (2017)**, *Agric. For. Meteorol.* + **NDAWN** docs — **stage-by-stage GDD table**.
7. **Setiyono et al. (2007)**, *Field Crops Res.* — **SoySim photothermal parameters and cardinal temperatures**.
8. **Alsajri et al. (2020)**, *Agron. J.* 112:194–204 — **full quadratic response functions** for yield, oil, protein, fatty acids.
9. **"On-farm soybean seed protein and oil prediction using satellite data" (2023)**, *Comput. Electron. Agric.* 212:108096 — **the benchmark to beat**; extract its full feature list.
10. **Frontiers in Plant Science (2022)**, PMC9618690 — planting date × MG × latitude oil coefficients (resolve item 8.5.2).
11. **Schlenker & Roberts (2009)**, *PNAS* — the **GDD/EDD piecewise specification** to replicate.
12. **Hamed et al. (2021)**, *ESD* 12:1371–1391 — **compound-extreme interaction magnitudes**.
13. **Zhang et al. (2016)**, *J. Agric. Food Chem.* — **DTR path coefficients** and the MDT quadratic.
14. **USSEC** FFA / Neutral Oil Loss / Refining fact sheets — processing specification limits.

---

## 9. References

*URLs are the landing pages surfaced by search. Full text was not retrievable in this session (§0.1); DOIs/volume-page details are as reported in search metadata and should be confirmed on retrieval.*

### Temperature and seed composition

1. Piper, E.L. & Boote, K.J. (1999). Temperature and cultivar effects on soybean seed oil and protein concentrations. *Journal of the American Oil Chemists' Society* 76. https://link.springer.com/article/10.1007/s11746-999-0099-y | https://aocs.onlinelibrary.wiley.com/doi/10.1007/s11746-999-0099-y
2. Gibson, L.R. & Mullen, R.E. (1996). Soybean seed composition under high day and night growth temperatures. *JAOCS* 73. https://link.springer.com/article/10.1007/BF02517949
3. Gibson, L.R. & Mullen, R.E. (1996). Influence of day and night temperature on soybean seed yield. *Crop Science* 36. https://acsess.onlinelibrary.wiley.com/doi/10.2135/cropsci1996.0011183X003600010018x
4. Dornbos, D.L. & Mullen, R.E. (1992). Soybean seed protein and oil contents and fatty acid composition adjustments by drought and temperature. *JAOCS* 69:228–231. https://link.springer.com/article/10.1007/BF02635891
5. Alsajri, F.A., Wijewardana, C., Irby, J.T., Bellaloui, N., Krutz, L.J., Golden, B.R., Gao, W. & Reddy, K.R. (2020). Developing functional relationships between temperature and soybean yield and seed quality. *Agronomy Journal* 112:194–204. https://acsess.onlinelibrary.wiley.com/doi/10.1002/agj2.20034 | https://www.semanticscholar.org/paper/35f89a63204a4f775efa7af3e498dee0b47adc65
6. Wolf, R.B. et al. (1982). Effect of temperature on soybean seed constituents: oil, protein, moisture, fatty acids, amino acids and sugars. *JAOCS* 59. https://link.springer.com/article/10.1007/BF02582182
7. Iyer, V.V. et al. (2008). Metabolic flux maps comparing the effect of temperature on protein and oil biosynthesis in developing soybean cotyledons. *Plant, Cell & Environment* 31. https://onlinelibrary.wiley.com/doi/10.1111/j.1365-3040.2008.01781.x
8. Thenveettil, N. et al. (2025). Drought and high nighttime temperature impact on soybean seed yield and quality under ambient and elevated CO₂ environments. *Scientific Reports*. https://www.nature.com/articles/s41598-025-20392-0
9. Temperature and elevated CO₂ alter soybean seed yield and quality, exhibiting transgenerational effects on seedling emergence and vigor (2024). *Frontiers in Plant Science*. https://www.frontiersin.org/journals/plant-science/articles/10.3389/fpls.2024.1427086/full
10. Effects of high night temperature on soybean yield and compositions. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9987466/
11. Morpho-physiological, yield, and transgenerational seed germination responses of soybean to temperature (2022). *Frontiers in Plant Science*. https://www.frontiersin.org/journals/plant-science/articles/10.3389/fpls.2022.839270/full

### Water availability and seed composition

12. Rotundo, J.L. & Westgate, M.E. (2009). Meta-analysis of environmental effects on soybean seed composition. *Field Crops Research* 110:147–156. https://www.sciencedirect.com/science/article/abs/pii/S0378429008001652
13. Rotundo, J.L. & Westgate, M.E. (2010). Rate and duration of seed component accumulation in water-stressed soybean. *Crop Science* 50. https://acsess.onlinelibrary.wiley.com/doi/10.2135/cropsci2009.05.0240
14. Carrera, C., Martínez, M.J., Dardanelli, J. & Balzarini, M. (2009). Water deficit effect on the relationship between temperature during the seed fill period and soybean seed oil and protein concentrations. *Crop Science* 49:990–998. https://acsess.onlinelibrary.wiley.com/doi/10.2135/cropsci2008.06.0361
15. Carrera, C.S. & Dardanelli, J.L. (2017). Water deficit modulates the relationship between temperature and unsaturated fatty acid profile in soybean seed oil. *Crop Science* 57:3179–3189. https://acsess.onlinelibrary.wiley.com/doi/10.2135/cropsci2017.04.0214
16. Carrera, C. et al. (2011). Environmental variation and correlation of seed components in nontransgenic soybeans: protein, oil, unsaturated fatty acids, tocopherols, and isoflavones. *Crop Science* 51. https://acsess.onlinelibrary.wiley.com/doi/abs/10.2135/cropsci2010.06.0314
17. Soybean seed physiology, quality, and chemical composition under soil moisture stress. *Food Chemistry*. https://www.sciencedirect.com/science/article/abs/pii/S0308814618319745
18. Drought stress during soybean seed filling affects storage compounds through regulation of lipid and protein metabolism. *Acta Physiologiae Plantarum* 40. https://link.springer.com/article/10.1007/s11738-018-2683-y
19. Irrigation increases on-farm soybean yields in water-limited environments without a trade-off in seed protein concentration. *Field Crops Research*. https://www.sciencedirect.com/science/article/pii/S0378429023003568
20. Drought or/and heat-stress effects on seed filling in food crops: impacts on functional biochemistry, seed yields, and nutritional quality (2018). *Frontiers in Plant Science*. https://www.frontiersin.org/journals/plant-science/articles/10.3389/fpls.2018.01705/full
21. Resilience of soybean cultivars to drought stress during flowering and early-seed setting stages. *Scientific Reports*. https://www.nature.com/articles/s41598-023-28354-0
22. Soybean water use and irrigation timing. Bayer Crop Science. https://www.cropscience.bayer.us/articles/bayer/soybean-water-use-and-irrigation-timing
23. Drought effect on pod fill in soybeans. Bayer Crop Science. https://www.cropscience.bayer.us/articles/bayer/soybean-pod-fill-drought-stress

### Radiation, PTQ, and seed composition

24. Contribution of incident solar radiation on leaves and pods to soybean seed weight and composition. *European Journal of Agronomy*. https://www.sciencedirect.com/science/article/abs/pii/S1161030116300478
25. Oil quality of maize and soybean genotypes with increased oleic acid percentage as affected by intercepted solar radiation and temperature. *Field Crops Research*. https://www.sciencedirect.com/science/article/abs/pii/S0378429011003868
26. Photothermal quotient describes the combined effects of heat and shade stresses on canola seed productivity. https://www.mdpi.com/2674-1024/2/1/12
27. Dosio, G.A.A. et al. (2000). Solar radiation intercepted during seed filling and oil production in two sunflower hybrids. *Crop Science* 40:1637. https://acsess.onlinelibrary.wiley.com/doi/abs/10.2135/cropsci2000.4061637x
28. Radiation and photothermal coefficient as major determinants of grain yield and oil content in sunflower under different sowing dates. *International Journal of Plant Production*. https://link.springer.com/article/10.1007/s42106-024-00321-3

### Composition surveys, oil–protein relationship, latitude and management

29. Assessing variation in US soybean seed composition (protein and oil) (2019). *Frontiers in Plant Science* 10:298. https://www.frontiersin.org/journals/plant-science/articles/10.3389/fpls.2019.00298/full | https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6421286/
30. Regional analysis of planting date and cultivar maturity recommendations that improve soybean oil yield and meal protein concentration (2022). *Frontiers in Plant Science*. https://www.frontiersin.org/journals/plant-science/articles/10.3389/fpls.2022.954111/full | https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9618690/
31. Planting date and maturity group impact on soybean seed quality in the southeastern United States. *Agronomy Journal*. https://acsess.onlinelibrary.wiley.com/doi/full/10.1002/agj2.20913
32. US soybean seed protein concentrations — current status, challenges, and some potential crop management solutions. *Agronomy Journal*. https://acsess.onlinelibrary.wiley.com/doi/10.1002/agj2.21731
33. Regional and temporal variation in soybean seed protein and oil across the United States. https://www.researchgate.net/publication/283489628
34. Zhang, J. et al. (2016). Analyzing the effects of climate factors on soybean protein, oil contents, and composition by extensive and high-density sampling in China. *Journal of Agricultural and Food Chemistry*. https://pubs.acs.org/doi/abs/10.1021/acs.jafc.6b00008
35. On the inverse correlation of protein and oil: examining the effects of altered central carbon metabolism on seed composition using soybean fast neutron mutants. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7022410/
36. Balancing act: progress and prospects in breeding soybean varieties with high oil and seed protein content (2025). *Frontiers in Plant Science*. https://www.frontiersin.org/journals/plant-science/articles/10.3389/fpls.2025.1560845/full
37. Genetic regulations of the oil and protein contents in soybean seeds and strategies for improvement. https://www.sciencedirect.com/science/chapter/bookseries/abs/pii/S0065229622000350
38. Soybean maturity groups, environments, and their interaction define mega-environments for seed composition in Argentina. https://www.researchgate.net/publication/237204869
39. A method to estimate soybean seed protein and oil concentration before harvest. *JAOCS* (2004). https://link.springer.com/article/10.1007/s11746-004-1016-2
40. Protein and oil patterns in U.S. and world soybean markets. Iowa Grain Quality Initiative, Iowa State University Extension. https://www.extension.iastate.edu/grain/topics/USandWorldSoybeanMarkets.htm

### Fatty acid composition and weather

41. Increased growing temperature reduces content of polyunsaturated fatty acids in four oilseed crops. *Industrial Crops and Products*. https://www.sciencedirect.com/science/article/abs/pii/S0926669013004950
42. Climate-based variability in the essential fatty acid composition of soybean oil. *American Journal of Clinical Nutrition*. https://ajcn.nutrition.org/article/S0002-9165(23)66122-2/fulltext
43. Bellaloui, N. et al. (2015). Drought and heat stress effects on soybean fatty acid composition and oil stability. USDA-ARS. https://www.ars.usda.gov/ARSUserFiles/60663500/Publications/Reddy/2015/Bellaloui%20et%20al_2015_Preddy_Chapter45.pdf
44. Temperature effects upon the expression of a high oleic acid trait in soybean. *JAOCS*. https://link.springer.com/article/10.1007/BF02546044
45. Enhancing soybean heat stress tolerance: effects of sowing date on seed yield, oil content, and fatty acid composition in hot climate conditions. https://pmc.ncbi.nlm.nih.gov/articles/PMC11717034/
46. Profiling of seed fatty acid composition in 1025 Chinese soybean accessions from diverse ecoregions. *The Crop Journal*. https://www.sciencedirect.com/science/article/pii/S2214514119301412
47. Fatty acid composition: quality of Canadian soybean, oilseed-type 2020. Canadian Grain Commission. https://www.grainscanada.gc.ca/en/grain-research/export-quality/oilseeds/soybean-oil/2020/05-fatty-acid-composition.html

### Phenology, growth stages, GDD, photoperiod

48. Fehr, W.R. & Caviness, C.E. (1977). *Stages of soybean development*. Iowa State University Cooperative Extension Special Report 80. (Standard reference; stage tables reproduced in refs 49–53.)
49. Soybean growth stages. Kansas State University Research and Extension, MF3339. https://bookstore.ksre.ksu.edu/download/soybean-growth-and-development-poster_MF3339
50. Chapter 3: Soybean growth stages. SDSU Extension. https://extension.sdstate.edu/sites/default/files/2020-03/S-0004-03-Soybean.pdf
51. Visual guide to soybean growth stages. Clemson University Land-Grant Press. https://lgpress.clemson.edu/publication/visual-guide-to-soybean-growth-stages/
52. The soybean plant. North Carolina Soybean Production Guide, NC State Extension. https://content.ces.ncsu.edu/north-carolina-soybean-production-guide/the-soybean-plant
53. North Dakota soybean production field guide, A1172 (rev. 2023). NDSU Extension. https://www.ndsu.edu/agriculture/sites/default/files/2023-01/a1172.pdf
54. Growing degree day model for North Dakota soybean. NDAWN, North Dakota State University. https://ndawn.ndsu.nodak.edu/help-soybean-growing-degree-days.html
55. Kandel, H. et al. (2017). Developing a growing degree day model for North Dakota and northern Minnesota soybean. *Agricultural and Forest Meteorology*. https://www.sciencedirect.com/science/article/abs/pii/S0168192317300709
56. NDSU develops soybean growing degree day model. NDSU Extension and Ag Research News. https://www.ag.ndsu.edu/news/newsreleases/2016/june-13-2016/ndsu-develops-soybean-growing-degree-day-model
57. Planting date effect on soybean reproductive duration. Pioneer Seeds Agronomy. https://www.pioneer.com/us/agronomy/planting-date-soybean-stages-r1-r6.html
58. Setiyono, T.D. et al. (2007). Understanding and modeling the effect of temperature and daylength on soybean phenology under high-yield conditions. *Field Crops Research*. https://www.researchgate.net/publication/222530234
59. Santos, C. et al. (2019). Soybean phenology prediction tool for the US Midsouth. *Agricultural & Environmental Letters*. https://acsess.onlinelibrary.wiley.com/doi/10.2134/ael2019.09.0036
60. SoyStage — soybean growth stage prediction tool (MG 3–6). University of Arkansas. https://soystage.uark.edu/
61. Photoperiod affects node appearance rate and flowering in early maturing soybean. *Plants* 11:871. https://doi.org/10.3390/plants11070871
62. Photoperiod sensitivity after flowering and seed number determination in indeterminate soybean cultivars. *Field Crops Research*. https://sciencedirect.com/science/article/abs/pii/S037842900100168X
63. Phenology and seed yield performance of determinate soybean cultivars grown at elevated temperatures in a temperate region. *PLOS ONE*. https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0165977
64. Understanding soybean seed filling: contribution to yield. Kansas State University Agronomy eUpdate. https://eupdate.agronomy.ksu.edu/article_new/understanding-soybean-seed-filling-contribution-to-yield-404
65. Egli, D.B. (1994). Cultivar maturity and reproductive growth duration in soybean. *Journal of Agronomy and Crop Science*. https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1439-037X.1994.tb00561.x

### Yield determination and weather–yield relationships

66. Egli, D.B. (1998). *Seed Biology and the Yield of Grain Crops*. CAB International. (Foundational reference for seed number as the primary yield component.)
67. Critical period for seed number determination in soybean as determined by crop growth rate, duration, and dry matter accumulation. *Field Crops Research* (2020). https://www.sciencedirect.com/science/article/abs/pii/S0378429020313009
68. Redefining soybean critical period for yield determination. *Field Crops Research* (2024). https://www.sciencedirect.com/science/article/abs/pii/S0378429024004155
69. Soybean yield formation physiology — a foundation for precision breeding based improvement. https://pmc.ncbi.nlm.nih.gov/articles/PMC8634342/
70. Soybean yield: when and how it's determined in the field. Science for Success (land-grant collaborative). https://soybeanscienceforsuccess.org/2025/10/21/soybean-yield-determined-in-field/
71. Schlenker, W. & Roberts, M.J. (2009). Nonlinear temperature effects indicate severe damages to U.S. crop yields under climate change. *PNAS* 106:15594–15598. https://www.pnas.org/doi/abs/10.1073/pnas.0906865106
72. Schauberger, B. et al. (2017). Consistent negative response of US crops to high temperatures in observations and crop models. *Nature Communications* 8:13931. https://www.nature.com/articles/ncomms13931 | https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5253679/
73. Mourtzinis, S. et al. (2015). Climate-induced reduction in US-wide soybean yields underpinned by region- and in-season-specific responses. *Nature Plants*. https://www.nature.com/articles/nplants201426
74. Hamed, R., Van Loon, A.F., Aerts, J. & Coumou, D. (2021). Impacts of compound hot–dry extremes on US soybean yields. *Earth System Dynamics* 12:1371–1391. https://esd.copernicus.org/articles/12/1371/2021/
75. Projected long-term climate trends reveal the critical role of vapor pressure deficit for soybean yields in the US Midwest. *Science of the Total Environment* (2023). https://www.sciencedirect.com/science/article/abs/pii/S0048969723015784 | https://pubmed.ncbi.nlm.nih.gov/36958552/
76. Systemic effects of rising atmospheric vapor pressure deficit on plant physiology and productivity. *Global Change Biology*. https://onlinelibrary.wiley.com/doi/10.1111/gcb.15548
77. A high resolution, gridded product for vapor pressure deficit using Daymet. *Scientific Data* (2025). https://www.nature.com/articles/s41597-025-04544-5
78. In-season weather data provide reliable yield estimates of maize and soybean in the US central Corn Belt. *International Journal of Biometeorology*. https://link.springer.com/article/10.1007/s00484-020-02039-z | https://pmc.ncbi.nlm.nih.gov/articles/PMC7985103/
79. Weather effects on expected corn and soybean yields. USDA Economic Research Service, FDS-13g-01. https://www.ers.usda.gov/media/9594/fds-13g-01.pdf
80. Weather, technology, and corn and soybean yields in the U.S. Corn Belt. University of Illinois farmdoc, MOBR 08-01. https://farmdoc.illinois.edu/assets/marketing/morr/morr_08-01.pdf
81. The relative impact of crop weather variables on the U.S. average yield of soybeans (2023). farmdoc daily, University of Illinois. https://farmdocdaily.illinois.edu/2023/10/the-relative-impact-of-crop-weather-variables-on-the-u-s-average-yield-of-soybeans.html
82. Impact of growing-season weather on corn and soybean yield in Illinois. *Agricultural and Forest Meteorology*. https://www.sciencedirect.com/science/article/pii/S0168192326000729
83. The role of climate covariability on crop yields in the conterminous United States. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5018813/
84. Impact of combined stress of high temperature and water deficit on growth and seed yield of soybean. https://pmc.ncbi.nlm.nih.gov/articles/PMC5787112/
85. Soybean pollen anatomy, viability and pod set under high temperature stress. https://www.researchgate.net/publication/234036963
86. Plasticity in flower number and abortion shape soybean yield under different environmental stress. *Journal of Agronomy and Crop Science* (2026). https://onlinelibrary.wiley.com/doi/10.1111/jac.70151

### Crop simulation models

87. CSM-CROPGRO-Soybean. DSSAT. https://dssat.net/csm-cropgro-soybean/
88. Simulation of soybean growth and yield in near-optimal growth conditions. *Field Crops Research* (2010). https://www.sciencedirect.com/science/article/abs/pii/S0378429010001760
89. Evaluation of models for simulating soybean growth and climate sensitivity in the U.S. Mississippi Delta (GLYCIM, SoySim, CSM-CROPGRO-Soybean). *European Journal of Agronomy* (2022). https://www.sciencedirect.com/science/article/abs/pii/S1161030122001587
90. Extending the evaluation of the SoySim model to soybean cultivars with high maturation groups. https://www.researchgate.net/publication/311098453
91. CROPGRO-Soybean model calibration and assessment of soybean yield responses to climate change. https://www.researchgate.net/publication/344431652
92. Simulation of genotype-by-environment interactions on irrigated soybean yields in the U.S. Midsouth. *Agricultural Systems*. https://www.sciencedirect.com/science/article/abs/pii/S0308521X16303274
93. Soybean yield simulation and sustainability assessment based on the DSSAT-CROPGRO-Soybean model. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11397127/

### Machine learning and statistical models

94. Khaki, S., Wang, L. & Archontoulis, S.V. (2020). A CNN-RNN framework for crop yield prediction. *Frontiers in Plant Science* 10:1750. https://www.frontiersin.org/journals/plant-science/articles/10.3389/fpls.2019.01750/full | https://arxiv.org/pdf/1911.09045
95. Khaki, S. & Wang, L. (2019). Crop yield prediction using deep neural networks. *Frontiers in Plant Science* 10:621. https://www.frontiersin.org/journals/plant-science/articles/10.3389/fpls.2019.00621/full
96. Sun, J. et al. (2019). County-level soybean yield prediction using deep CNN-LSTM model. *Sensors*. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6832950/
97. Harvesting insights: interpretable machine learning to understand environmental drivers of U.S. maize and soybean yield (2026). *Scientific Reports* 16:8994. https://www.nature.com/articles/s41598-026-38724-z | https://pmc.ncbi.nlm.nih.gov/articles/PMC12992539/
98. Utilizing machine learning framework to evaluate the effect of climate change on maize and soybean yield. *Computers and Electronics in Agriculture* (2024). https://www.sciencedirect.com/science/article/pii/S0168169924003739
99. On-farm soybean seed protein and oil prediction using satellite data (2023). *Computers and Electronics in Agriculture* 212:108096. https://www.sciencedirect.com/science/article/pii/S0168169923004842 | https://dl.acm.org/doi/10.1016/j.compag.2023.108096
100. Soybean seed composition prediction from standing crops using PlanetScope satellite imagery and machine learning (2023). *ISPRS Journal of Photogrammetry and Remote Sensing*. https://www.sciencedirect.com/science/article/pii/S0924271623002514
101. Shook, J. et al. (2021). Integrating genotype and weather variables for soybean yield prediction using deep learning. *bioRxiv* / *PLOS ONE*. https://www.biorxiv.org/content/10.1101/331561v1
102. Van der Laan, L. et al. (2025). Genomic and phenomic prediction for soybean seed yield, protein, and oil. *The Plant Genome*. https://acsess.onlinelibrary.wiley.com/doi/full/10.1002/tpg2.70002 | https://pmc.ncbi.nlm.nih.gov/articles/PMC11839941/
103. Coupling machine learning and crop modeling improves crop yield prediction. https://arxiv.org/pdf/2008.04060
104. Maize and soybean yield prediction using machine learning methods: a systematic literature review. *Discover Agriculture*. https://link.springer.com/article/10.1007/s44279-025-00215-6
105. The response of maize, sorghum, and soybean yield to growing-phase climate revealed with machine learning. https://www.academia.edu/82139959/
106. Soybean yield prediction using machine learning algorithms under a cover crop management system. https://www.sciencedirect.com/science/article/pii/S2772375524000479
107. Using partial least squares and regression to interpret temperature and precipitation effects on maize and soybean genetic variance expression. *Agronomy* 13:2752. https://doi.org/10.3390/agronomy13112752

### Processing, crush, and oil quality

108. Understanding soybean crush. CME Group. https://www.cmegroup.com/education/courses/introduction-to-agriculture/grains-oilseeds/understanding-soybean-crush
109. What is oilshare? CME Group. https://www.cmegroup.com/articles/whitepapers/what-is-oil-share.html
110. The soybean industry response to the renewable diesel boom, Part 1: the long-run evolution of oilseed crushing (2025). farmdoc daily, University of Illinois. https://farmdocdaily.illinois.edu/2025/08/the-soybean-industry-response-to-the-renewable-diesel-boom-part-1-the-long-run-evolution-of-oilseed-crushing.html
111. The value of soybean oil in the soybean crush: further evidence on the impact of the U.S. biodiesel boom (2017). farmdoc daily. https://farmdocdaily.illinois.edu/2017/09/the-value-of-soybean-oil-in-the-soybean-crush.html
112. Current soybean oil contribution to combined soybean crush value (2025). DTN/Progressive Farmer. https://www.dtnpf.com/agriculture/web/ag/blogs/fundamentally-speaking/blog-post/2025/07/02/current-soybean-oil-contribution
113. Soybean value concepts: the true drivers of soybean value (Estimated Processed Value; NOPA and HY+Q methods). United Soybean Board Market View Insight. https://marketviewdb.unitedsoybean.org/uploads/briefs/MVI%20(18-5)%20Value%20Concepts-%20EPV.pdf
114. Overview of the soybean process in the crushing industry. *OCL — Oilseeds and Fats, Crops and Lipids*. https://www.ocl-journal.org/articles/ocl/full_html/2020/01/ocl200047s/ocl200047s.html
115. Soybean oil quality fact sheet — free fatty acids. U.S. Soybean Export Council (USSEC). https://ussec.org/wp-content/uploads/2025/08/Free-Fatty-Acids-Fact-Sheet-2025.pdf
116. Soybean oil quality fact sheet — neutral oil loss. USSEC. https://ussec.org/wp-content/uploads/2025/08/Neutral-Oil-Loss-Fact-Sheet-2025.pdf
117. Soybean oil quality fact sheet — refining. USSEC. https://ussec.org/wp-content/uploads/2025/08/Soybean-Oil-Quality-Refining-2025.pdf
118. Soybean oil quality fact sheet — glossary. USSEC. https://ussec.org/wp-content/uploads/2025/08/Soybean-Oil-Quality-Glossary-2025.pdf
119. Soybean oil value calculator. USSEC. https://ussec.org/soybean-oil-value-calculator/
120. Chemical evaluation of oil from field- and storage-damaged soybeans. *JAOCS*. https://link.springer.com/article/10.1007/BF02639850
121. The value of U.S. soybean oil: beyond price and protein. U.S. Soy. https://ussoy.org/the-value-of-u-s-soybean-oil-beyond-price-and-protein/
122. Profitability analysis of soybean oil processes. *Bioengineering* 4:83. https://doi.org/10.3390/bioengineering4040083
123. GIAC standardizing protein moisture basis certification. USDA Agricultural Marketing Service. https://www.ams.usda.gov/about-ams/giac-may-2024-meeting/moisture

### Nutrient management and composition

124. Sulfur fertilization alters soybean seed protein in one of two test environments in the southeast U.S. *Journal of Plant Nutrition*. https://www.tandfonline.com/doi/full/10.1080/01904167.2025.2522253
125. Assessing the effects of fertilisation on the yield, protein, and oil content of soybean seeds: a metadata analysis. *bioRxiv* (2025). https://www.biorxiv.org/content/10.1101/2025.09.16.676653v1.full
126. Soybean yield response to nitrogen and sulfur fertilization in the United States: contribution of soil N and N fixation processes. *European Journal of Agronomy*. https://www.sciencedirect.com/science/article/abs/pii/S116103012300059X
127. Nitrogen source and sulfur contribution effects on yield, protein, and oil content in soybean. *Agronomy Journal*. https://acsess.onlinelibrary.wiley.com/doi/10.1002/agj2.70336
128. Enhancing soybean yield: the synergy of sulfur and rhizobia inoculation. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10675423/
129. Enhanced seed yield, essential amino acids and unsaturated fatty acids in soybean seeds with phosphorus fertilizer supply. https://www.sciencedirect.com/science/article/abs/pii/S0889157523006877

---

## 10. Executive summary for product decisions

1. **Oil concentration is set during R5–R8; flowering-window weather is nearly irrelevant to it.** Build features on phenological windows, not calendar months. This alone differentiates the product from WAOB-style calendar models.
2. **The temperature–oil relationship is unimodal with a peak near 28 °C.** Encode it as a quadratic or hinge. A linear term will produce a coefficient whose sign flips by region and which fails under warming.
3. **Drought during seed fill lowers oil % and raises protein %** — a partitioning effect, not a biosynthesis effect. One credible study (Carrera 2009) reports the opposite sign; treat that as unresolved and test it against your own data.
4. **Include DTR and Tmin separately from Tmean.** Night temperature and diurnal range carry independent, mechanistically supported signal for both oil and fatty acid profile.
5. **GDD cannot represent heat stress** (the standard form caps at 30 °C). Add separate above-threshold counters at 30, 32, and 35 °C.
6. **Compound hot-dry effects are super-additive.** Include heat × water interactions and validate on 1988/2012-class years specifically.
7. **Calibrate expectations to published SOTA: oil % R² ≈ 0.53 and ~1.0 pp absolute error; yield RRMSE ≈ 8%.** The 0.87 R² figure in the recent literature is for sub-field yield variability from yield-monitor plus soil/terrain data and is not the comparable benchmark.
8. **Weather alone will not beat R² ≈ 0.5 for oil.** Temperature explains ~24% of oil variation (Piper & Boote), and the meta-analytic literature explicitly notes that manipulable field effects are far smaller than observational variation. **Genotype/maturity group and remote sensing are the necessary next data acquisitions** — the cultivar × temperature interaction is significant, so genotype is not merely an additive offset.
9. **Adopt CROPGRO-Soybean or SoySim as the phenology and state-variable engine** rather than building bespoke GDD logic; CROPGRO already simulates seed oil and protein via C/N balance with an explicit temperature effect and has been validated for oil across new environments.
10. **Every number in this brief is provisional** because full-text retrieval was blocked (§0.1). Work through the §8.6 verification list — items 1–5 in particular — before any coefficient reaches production.
