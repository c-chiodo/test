# Public / Free Data Sources for a US Soybean Yield & Seed-Oil Prediction Product

Compiled 2026-08-12. Scope: sources that are free and either no-auth or free-key auth, usable as an
ingestion layer for county- and field-scale soybean yield and seed composition (oil / protein) modeling
in the United States, with global coverage noted where it exists.

---

## READ THIS FIRST — verification status and an important honesty note

**I was not able to execute live requests against any of these APIs.** This session's outbound network
is behind a policy-enforcing egress proxy that denied `CONNECT` to every data host I tried
(`api.open-meteo.com`, `archive-api.open-meteo.com`, `power.larc.nasa.gov`, `quickstats.nass.usda.gov`,
`daymet.ornl.gov`, `rest.isric.org`, `api.weather.gov`). Both `curl` and the fetch tool returned
`403 CONNECT tunnel failed` / `EGRESS_BLOCKED` for those domains. Per the proxy's own runbook, policy
denials must be reported rather than routed around, so I did not attempt to tunnel through a third-party
fetch proxy.

**Consequently there are no pasted live API responses in this document, and there are no invented ones.**
Every endpoint below was instead verified against the strongest available primary evidence I *could*
reach — machine-readable OpenAPI specifications and the source code of well-maintained official client
libraries, both of which encode the real request contract. Each source carries an explicit verification
label:

| Label | Meaning |
|---|---|
| **[SPEC]** | Verified verbatim from the provider's own OpenAPI/Swagger specification. Highest confidence — parameter names and enum values are exact. |
| **[SRC]** | Verified verbatim from the source code of an official or widely-used client library that calls the API in production (ORNL DAAC, rOpenSci, etc.). High confidence on URL + parameter names. |
| **[DOC]** | Read from provider documentation via search-engine page extraction, not fetched directly. Good confidence on substance; treat exact strings as needing a smoke test. |
| **[UNVERIFIED]** | Reported from secondary sources only. Smoke-test before relying on it. |

**Action item for whoever picks this up:** run the "smoke test script" in the final section from an
unrestricted network before writing ingestion code. It exercises every no-auth endpoint here in about
20 requests.

---

## 1. Weather / climate

### Summary table

| Source | Gives us | Auth | Spatial res | Temporal res & range | Rate limit | Commercial use |
|---|---|---|---|---|---|---|
| Open-Meteo Historical (ERA5 archive) | Daily/hourly reanalysis incl. soil moisture & temp by depth, ET0, VPD | None (free tier) | 0.25° ERA5, ~0.1° ERA5-Land, ~9 km IFS | Daily + hourly, 1940-01-01 → ~5 d ago | 600/min, 5 000/h, 10 000/day | **NO — free tier is non-commercial only. Paid plan required.** |
| Open-Meteo Forecast | 16-day forecast, 92 days past | None (free tier) | 1–11 km depending on model | Hourly/daily | Same as above | **NO on free tier** |
| Open-Meteo Climate (CMIP6) | Downscaled climate projections | None (free tier) | ~10 km downscaled | Daily, 1950-01-01 → 2050-12-31 | Same as above | **NO on free tier** |
| NASA POWER | Agroclimatology daily/hourly met + solar | **None** | 0.5° × 0.625° met; 1° solar | Daily 1981→; hourly 2001→ | Soft; abusive repeat polling blocked | **Yes — US Government open data** |
| Daymet V4 (ORNL DAAC) | 1 km daily gridded met, North America | **None** | 1 km | Daily, 1980 → last complete year | Undocumented; be polite | **Yes — NASA Earthdata open sharing** |
| NOAA NCEI Access Data Service | GHCN-Daily station observations | **None** | Station point | Daily, station-dependent (1800s→) | Undocumented | **Yes — US Government** |
| NOAA CDO API v2 | Same data, richer discovery | **Free token** | Station point | Daily+ | 5 req/s, 10 000/day | **Yes — US Government** |
| NWS api.weather.gov | Official US operational forecast | None, but **User-Agent required** | ~2.5 km gridpoints | Hourly forecast, 7 days | Undocumented; 403 on abuse | **Yes — US Government** |
| gridMET | 4 km CONUS daily met incl. VPD, ETo, ETr | None (THREDDS/OPeNDAP) | 4 km (1/24°) | Daily, 1979 → yesterday | N/A (bulk file access) | Verify with Climatology Lab **[UNVERIFIED]** |
| PRISM | 4 km / 800 m CONUS daily & normals | None (web service) | 4 km, 800 m | Daily, 1981→ | 1 grid per request | **NO for 4 km purchased tier — commercial use "strictly prohibited" without prior arrangement.** |

### 1.1 Open-Meteo — Historical Weather API (ERA5 archive) **[SPEC]**

Verified verbatim against `openapi/historical-weather.yml` in the `open-meteo/open-meteo` repository.

- **Servers:** `https://archive-api.open-meteo.com` (free) and `https://customer-archive-api.open-meteo.com` (paid)
- **Path:** `/v1/archive`
- **Required:** `latitude`, `longitude`, `start_date`, `end_date` (both ISO 8601, `YYYY-MM-DD`)
- **Optional:** `hourly`, `daily`, `timezone` (any IANA name, or `auto`), `models`, `cell_selection`
  (`land` | `sea` | `nearest`), `temperature_unit`, `precipitation_unit`, `timeformat`

**`models` enum (exact):** `best_match`, `era5_seamless`, `era5`, `era5_land`, `ecmwf_ifs`, `cerra`,
`era5_ensemble`, `ecmwf_ifs_analysis_long_window`

`era5_seamless` is the one you want for production: it stitches ERA5 / ERA5-Land with ECMWF IFS to cut
the reanalysis latency (raw ERA5 lags ~5 days).

**`daily` enum (exact, complete):**
`weather_code`, `temperature_2m_mean`, `temperature_2m_max`, `temperature_2m_min`,
`apparent_temperature_mean`, `apparent_temperature_max`, `apparent_temperature_min`, `sunrise`, `sunset`,
`daylight_duration`, `sunshine_duration`, `precipitation_sum`, `rain_sum`, `snowfall_sum`,
`precipitation_hours`, `wind_speed_10m_max`, `wind_gusts_10m_max`, `wind_direction_10m_dominant`,
`shortwave_radiation_sum`, `et0_fao_evapotranspiration`, `cloud_cover_mean`, `dew_point_2m_mean`,
`dew_point_2m_max`, `dew_point_2m_min`, `relative_humidity_2m_mean`, `relative_humidity_2m_max`,
`relative_humidity_2m_min`, `pressure_msl_mean`, `wind_speed_10m_mean`, `wet_bulb_temperature_2m_mean`,
`vapour_pressure_deficit_max`, `soil_moisture_0_to_7cm_mean`, `soil_moisture_7_to_28cm_mean`,
`soil_moisture_28_to_100cm_mean`, `soil_moisture_0_to_100cm_mean`, `soil_temperature_0_to_7cm_mean`,
`soil_temperature_7_to_28cm_mean`, `soil_temperature_28_to_100cm_mean`

This single enum covers essentially every predictor the brief asked for — note in particular
`vapour_pressure_deficit_max`, `et0_fao_evapotranspiration`, and the four soil-moisture depth bands,
all available at *daily* aggregation so you do not need to roll up hourly data yourself.

**`hourly` enum (exact, complete):**
`temperature_2m`, `relative_humidity_2m`, `dew_point_2m`, `apparent_temperature`, `precipitation`,
`rain`, `snowfall`, `snow_depth`, `weather_code`, `pressure_msl`, `surface_pressure`, `cloud_cover`,
`cloud_cover_low`, `cloud_cover_mid`, `cloud_cover_high`, `et0_fao_evapotranspiration`,
`vapour_pressure_deficit`, `wind_speed_10m`, `wind_speed_100m`, `wind_direction_10m`,
`wind_direction_100m`, `wind_gusts_10m`, `soil_temperature_0_to_7cm`, `soil_temperature_7_to_28cm`,
`soil_temperature_28_to_100cm`, `soil_temperature_100_to_255cm`, `soil_moisture_0_to_7cm`,
`soil_moisture_7_to_28cm`, `soil_moisture_28_to_100cm`, `soil_moisture_100_to_255cm`,
`soil_moisture_0_to_100cm`, `soil_temperature_0_to_100cm`, `soil_moisture_index_0_to_7cm`,
`soil_moisture_index_7_to_28cm`, `soil_moisture_index_28_to_100cm`, `soil_moisture_index_0_to_100cm`,
`boundary_layer_height`, `wet_bulb_temperature_2m`, `total_column_integrated_water_vapour`, `is_day`,
`sunshine_duration`, `growing_degree_days_base_0_limit_50`, `leaf_wetness_probability`, `wave_height`,
`wave_direction`, `wave_period`, `sea_surface_temperature`, `shortwave_radiation`, `direct_radiation`,
`diffuse_radiation`, `direct_normal_irradiance`, `global_tilted_irradiance`, `terrestrial_radiation`,
`shortwave_radiation_instant`, `direct_radiation_instant`, `diffuse_radiation_instant`,
`direct_normal_irradiance_instant`, `global_tilted_irradiance_instant`, `terrestrial_radiation_instant`

Two hourly variables are quietly very valuable for this product: `growing_degree_days_base_0_limit_50`
(server-side GDD accumulation) and `leaf_wetness_probability` (disease-pressure proxy, which drives
both yield loss and seed quality). The `soil_moisture_index_*` variables are normalized between wilting
point and field capacity, which makes them far more transferable across soil types than raw volumetric
moisture.

**Example request (soybean pixel near Ames, Iowa, 2000–2024 daily):**

```
https://archive-api.open-meteo.com/v1/archive?latitude=42.03&longitude=-93.62&start_date=2000-01-01&end_date=2024-12-31&daily=temperature_2m_max,temperature_2m_min,temperature_2m_mean,precipitation_sum,shortwave_radiation_sum,et0_fao_evapotranspiration,vapour_pressure_deficit_max,relative_humidity_2m_mean,dew_point_2m_mean,soil_moisture_0_to_7cm_mean,soil_moisture_7_to_28cm_mean,soil_moisture_28_to_100cm_mean,soil_moisture_0_to_100cm_mean,soil_temperature_0_to_7cm_mean&models=era5_seamless&timezone=America%2FChicago
```

**Coverage / limits / license [DOC]:** ERA5 from 1940 at 0.25°. Free tier: **< 600 calls/minute,
< 5 000/hour, < 10 000/day**, no API key, no signup. Underlying data is **CC-BY-4.0** (confirmed
verbatim from the `open-meteo/open-data` repository README, which also disclaims any warranty of
accuracy or uptime).

> ### ⚠️ COMMERCIAL-USE FLAG — the single most important licensing finding in this document
> The Open-Meteo **data** is CC-BY-4.0 and freely reusable commercially, **but the free API *service*
> is licensed for non-commercial use only.** Open-Meteo's terms restrict the free endpoints
> (`api.open-meteo.com`, `archive-api.open-meteo.com`) to non-commercial purposes; commercial use
> requires a paid subscription served from `customer-api.open-meteo.com` /
> `customer-archive-api.open-meteo.com` with an `apikey` parameter.
>
> Because Open-Meteo is otherwise the single best-fit source for this product, budget for the paid
> tier from day one. Practically this is cheap relative to the engineering value: the paid endpoints
> are drop-in URL swaps with an added `&apikey=`, so build the client with a configurable base URL and
> you can flip commercial-compliant without touching feature code. **Do not ship a commercial product
> on the free endpoints.**
>
> The fully self-hosted escape hatch also exists and is genuinely viable: Open-Meteo publishes its
> server as open source with Docker images and an AWS Open Data S3 bucket of the underlying `.om`
> files, so you can run your own instance over CC-BY-4.0 data and owe nobody a service fee. That is
> the right answer if call volume gets large.

### 1.2 Open-Meteo — Forecast API **[SPEC]**

- **Servers:** `https://api.open-meteo.com` / `https://customer-api.open-meteo.com`
- **Path:** `/v1/forecast`
- **`forecast_days`:** default 7, min 0, **max 16**
- **`past_days`:** default 0, min 0, **max 92** — lets one call return a continuous 92-days-back to
  16-days-forward window, which is exactly the shape of an in-season prediction feature vector.
- **`daily` enum:** `weather_code`, `temperature_2m_max`, `temperature_2m_min`,
  `apparent_temperature_max`, `apparent_temperature_min`, `sunrise`, `sunset`, `daylight_duration`,
  `sunshine_duration`, `uv_index_max`, `uv_index_clear_sky_max`, `rain_sum`, `showers_sum`,
  `snowfall_sum`, `precipitation_sum`, `precipitation_hours`, `precipitation_probability_max`,
  `wind_speed_10m_max`, `wind_gusts_10m_max`, `wind_direction_10m_dominant`, `shortwave_radiation_sum`,
  `et0_fao_evapotranspiration`
- **US-relevant `models`:** `best_match`, `ncep_gfs_seamless`, `ncep_gfs_global`, `ncep_hrrr_conus`,
  `ncep_nbm_conus`, `ncep_nam_conus`, `ncep_gfs_graphcast025`, `ncep_aigfs025`,
  `ncep_hgefs025_ensemble_mean`, `ecmwf_ifs025`, `ecmwf_aifs025_single` (full enum has ~50 models
  including ICON, JMA, KMA, UKMO, MeteoFrance, DMI, KNMI, MeteoSwiss)

`ncep_hrrr_conus` at ~3 km is the highest-resolution free US option; `ncep_nbm_conus` (National Blend
of Models) is usually the best-calibrated single choice for CONUS point forecasts.

**Example:**
```
https://api.open-meteo.com/v1/forecast?latitude=42.03&longitude=-93.62&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,shortwave_radiation_sum,et0_fao_evapotranspiration&past_days=92&forecast_days=16&models=best_match&timezone=America%2FChicago
```

### 1.3 Open-Meteo — Climate API (CMIP6) **[SPEC]**

- **Servers:** `https://climate-api.open-meteo.com` / `https://customer-climate-api.open-meteo.com`
- **Path:** `/v1/climate`
- **Date bounds (exact):** `start_date` min **1950-01-01**, `end_date` max **2050-12-31**
- **`models` enum (exact):** `CMCC_CM2_VHR4`, `FGOALS_f3_H`, `HiRAM_SIT_HR`, `MRI_AGCM3_2_S`,
  `EC_Earth3P_HR`, `MPI_ESM1_2_XR`, `NICAM16_8S`
- **Daily variables include:** `temperature_2m_max/min/mean`, `precipitation_sum`, `cloud_cover_mean`,
  `wind_speed_10m_mean`, `soil_moisture_0_to_10cm_mean`, `soil_temperature_0_to_7cm_mean`,
  `shortwave_radiation_sum`, `et0_fao_evapotranspiration_sum`, `growing_degree_days_base_0_limit_50`,
  plus ~18 more

Use this for scenario/long-horizon product features, not for in-season prediction.

### 1.4 Open-Meteo — Historical Forecast API **[DOC]**

- **Base URL:** `https://historical-forecast-api.open-meteo.com/v1/forecast`
- Archives the **first hours of every model run** into a continuous series, from **2021–2022 onward**
  depending on model, at the native high resolution (1–2 km over North America).

This is a subtle but important one for model training integrity. If your production system will make
predictions from *forecasts*, training on ERA5 reanalysis introduces a train/serve skew — reanalysis is
more accurate than any forecast you will actually have at inference time. Training on the historical
*forecast* archive instead removes that skew. Recommend using ERA5 for the long history and the
historical forecast archive for 2021+ to calibrate the skew term.

### 1.5 NASA POWER **[SRC]**

Verified from the rOpenSci `nasapower` package source (`R/get_power.R`), which is the reference client.

- **URL pattern:** `https://power.larc.nasa.gov/api/temporal/{temporal_api}/{spatial_identifier}`
  - `{temporal_api}` ∈ `hourly` | `daily` | `monthly` | `climatology`
  - `{spatial_identifier}` ∈ `point` | `regional` | `global`
- **Parameters:** `community` (`ag` | `re` | `sb` — use **`ag`** for agroclimatology), `parameters`
  (comma-separated; **max 20** for daily/monthly/climatology, **max 15** for hourly), `start` and `end`
  (**`YYYYMMDD`**), `latitude`, `longitude` (point) or `latitude-min/latitude-max/longitude-min/longitude-max`
  (regional), `format` (`csv` | `json` | `netcdf` | `ascii`), `time-standard` (`LST` | `UTC`),
  `site-elevation`, `wind-elevation`, `wind-surface`, `user`
- **Coverage (verified verbatim from source):** daily and monthly earliest **1981-01-01**; hourly
  earliest **2001-01-01**. Global land coverage.
- **Spatial resolution [DOC]:** 0.5° × 0.625° for meteorology, 1° × 1° for solar. This is coarse —
  roughly 50 km — so POWER is appropriate for county-scale aggregates and as a redundancy/fallback
  source, but it cannot resolve field-scale variation.
- **Auth:** none. **Rate limits [DOC]:** not formally published; NASA warns that applications
  repeatedly hammering the same location may be blocked. Cache aggressively.
- **License:** US Government work, freely available including commercially. **No commercial restriction.**

**Key parameter codes for soybeans:** `T2M`, `T2M_MAX`, `T2M_MIN`, `T2MDEW`, `T2MWET`, `PRECTOTCORR`,
`ALLSKY_SFC_SW_DWN`, `CLRSKY_SFC_SW_DWN`, `ALLSKY_SFC_PAR_TOT`, `RH2M`, `WS2M`, `PS`, `GWETTOP`,
`GWETROOT`, `GWETPROF`, `EVPTRNS`, `T2M_RANGE`.

`GWETROOT` (root-zone soil wetness) and `GWETPROF` (profile soil wetness) deserve specific attention —
they are free, global, gap-free root-zone moisture proxies from MERRA-2 land, and root-zone water status
during pod fill is among the strongest physiological drivers of both yield and the oil/protein tradeoff.

**Example request:**
```
https://power.larc.nasa.gov/api/temporal/daily/point?parameters=T2M_MAX,T2M_MIN,T2M,PRECTOTCORR,ALLSKY_SFC_SW_DWN,RH2M,T2MDEW,WS2M,GWETROOT,GWETPROF&community=ag&latitude=42.03&longitude=-93.62&start=20000101&end=20241231&format=json&time-standard=LST
```

### 1.6 Daymet V4 — ORNL DAAC single-pixel REST API **[SRC]**

Base URL verified verbatim from the `daymetr` package (`R/zzz.r`); parameter names cross-checked against
both `daymetr` and ORNL DAAC's own `daymet-single-pixel-batch` Python example.

- **Base URL:** `https://daymet.ornl.gov/single-pixel/api/data`
- **Parameters:** `lat`, `lon`, `vars` (comma-separated), `year` (comma-separated list) **or**
  `start` / `end` (`YYYY-MM-DD`), `format`
- ⚠️ **Naming discrepancy to smoke-test:** `daymetr` sends the variable list as **`vars`**, while
  ORNL DAAC's own Python batch example sends it as **`measuredParams`**. Both appear to be accepted by
  the service. Test both and pin whichever responds; do not assume.
- **Variables:** `tmax`, `tmin`, `prcp`, `srad`, `vp`, `swe`, `dayl`
  (°C, °C, mm/day, W/m², Pa, kg/m², s)
- **Coverage:** **1980 → last complete calendar year**, 1 km grid, continental North America, Hawaii,
  Puerto Rico. Latitude bounds roughly 14.5°N–52.0°N. `daymetr` hard-stops on `start < 1980` and
  defaults the max year to *current year − 1* (overridable with `force = TRUE`), which reflects the
  annual release cadence — Daymet is **not** suitable for in-season use.
- **Important modeling caveat:** Daymet uses a **365-day year and drops December 31 in leap years.**
  Any day-of-year join against other sources must handle this or your late-season features will be
  silently off by one day in leap years.
- **Auth:** none. **License:** openly shared under NASA Earthdata Data Use Guidance — **commercial use
  permitted**, citation expected.
- **Related endpoints:** gridded subsets via THREDDS NCSS at
  `https://thredds.daac.ornl.gov/thredds/ncss/ornldaac/{2129|2130|2131}` (daily/annual/monthly) and
  tiles at `https://thredds.daac.ornl.gov/thredds/fileServer/ornldaac/2129/tiles`.

**Example:**
```
https://daymet.ornl.gov/single-pixel/api/data?lat=42.03&lon=-93.62&vars=tmax,tmin,prcp,srad,vp,dayl&start=2000-01-01&end=2024-12-31
```

Daymet's `vp` (water vapor pressure, Pa) combined with `tmax` lets you compute true VPD at 1 km, which
is finer than anything else free in this list.

### 1.7 NOAA

**(a) NCEI Access Data Service — GHCN-Daily, no token [DOC]**
- **Base URL:** `https://www.ncei.noaa.gov/access/services/data/v1`
- **Parameters:** `dataset` (e.g. `global-summary-of-the-day`, `daily-summaries` for GHCN-Daily),
  `stations` (comma-separated IDs), `startDate` / `endDate` (ISO 8601 date or datetime),
  `dataTypes` (e.g. `TMAX,TMIN,PRCP,TAVG`), `format` (`csv` | `ssv` | `json` | `pdf` | `netcdf`),
  `units` (`standard` | `metric`), `boundingBox`, `includeAttributes`, `includeStationName`,
  `includeStationLocation`
- **Auth: none.** This is the practical way to get GHCN-Daily programmatically and is the one I'd
  reach for over CDO v2.
- **Example:**
  ```
  https://www.ncei.noaa.gov/access/services/data/v1?dataset=daily-summaries&stations=USW00014933&startDate=2000-01-01&endDate=2024-12-31&dataTypes=TMAX,TMIN,PRCP&format=csv&units=metric
  ```

**(b) Climate Data Online (CDO) API v2 — token required [DOC]**
- **Base URL:** `https://www.ncei.noaa.gov/cdo-web/api/v2/` (endpoints: `datasets`, `datacategories`,
  `datatypes`, `locationcategories`, `locations`, `stations`, `data`)
- **Auth:** free token, requested at the CDO web services page, passed in the **`token` HTTP header**
  (not a query parameter).
- **Rate limits:** **5 requests/second and 10 000 requests/day.** Also caps 1 000 records per response
  (`limit`/`offset` pagination).
- Better station/metadata discovery than the Access service; slower and rate-limited for bulk pulls.
  Use CDO for discovery, Access Data Service for volume.

**(c) NWS api.weather.gov — operational US forecast [DOC]**
- **Base URL:** `https://api.weather.gov`
- **Flow:** `GET /points/{lat},{lon}` → response `properties.forecastGridData` →
  `GET /gridpoints/{office}/{gridX},{gridY}` (raw ~2.5 km grid) or
  `GET /gridpoints/{office}/{gridX},{gridY}/forecast`
- **Auth: no API key, but a descriptive `User-Agent` header identifying your app and a contact address
  is REQUIRED** — requests without one are rejected. This trips up most first integrations.
- **Rate limits:** not formally documented; abuse yields HTTP 403 with a reference ID.
- The `/points` mapping is stable, so cache lat/lon → gridpoint permanently.
- **License:** US Government, no commercial restriction.
- **Example:** `https://api.weather.gov/points/42.03,-93.62`

### 1.8 gridMET (University of Idaho / Climatology Lab) **[DOC]**

- **Access:** THREDDS/OPeNDAP, e.g. `http://thredds.northwestknowledge.net/thredds/dodsC/MET/{var}/{var}_{year}.nc`
- **Resolution / coverage:** **4 km (1/24°) CONUS, daily, 1979 → yesterday.** Near-real-time, unlike
  Daymet.
- **Variables:** `tmmx`, `tmmn`, `pr`, `srad`, `rmax`, `rmin`, `vs`, **`vpd`**, **`eto`**, **`etr`**, `erc`, `bi`, `fm100`, `fm1000`, `pet`
- **Why it matters here:** gridMET is the sweet spot for CONUS agronomic modeling — it has
  *native, ready-made* VPD and reference ET (both grass `eto` and alfalfa `etr`) at 4 km with
  next-day latency. VPD during seed fill is a well-established driver of the soybean oil/protein
  tradeoff, so this is a first-class feature source, not a nice-to-have.
- **Constraint:** it is NetCDF-over-OPeNDAP, not a point JSON API. You need `xarray` + a
  subsetting/caching layer. That is real engineering work versus Open-Meteo's one-URL convenience.
- **License [UNVERIFIED]:** I could not confirm explicit commercial terms. gridMET is widely used in
  commercial ag analytics and is generally treated as open with citation required, but **confirm with
  the Climatology Lab in writing before commercial launch.**

### 1.9 PRISM (Oregon State) **[DOC]** — ⚠️ commercial restriction

- **Web service:** `https://services.nacse.org/prism/data/public/4km/{element}/{YYYYMMDD}`
  (also `.../800m/...`; add `?format=grib2` etc.)
- **Elements:** `ppt`, `tmin`, `tmax`, `tmean`, `tdmean`, `vpdmin`, `vpdmax`, `solslope`, `soltotal`,
  `solclear`, `soltrans`
- **Resolution:** 4 km and 800 m; daily, monthly, annual, 30-year normals; 1981 → present.
  As of March 2025 the 800 m data is free to the public via FTP and web services.
- **One grid per request** — bulk pulls require scripted iteration.
- > ⚠️ **COMMERCIAL-USE FLAG:** PRISM terms state that purchased 4 km data may **not** be duplicated or
  > redistributed, and that **commercial use is strictly prohibited absent advance special
  > arrangements.** Treat PRISM as **unusable for a commercial product** until you have written
  > permission from the PRISM Climate Group. gridMET is the practical substitute — it is explicitly
  > built to inherit PRISM's spatial fidelity via climatically-aided interpolation while adding
  > better temporal attributes.

### 1.10 Also genuinely useful

- **US Drought Monitor REST services [DOC]** — `https://usdmdataservices.unl.edu/api/{area}/{statisticsType}`
  with `aoi` (5-digit county FIPS or 2-letter state), `startdate`, `enddate`, `statisticsType`.
  Types include `GetDroughtSeverityStatisticsByArea`, `GetDroughtSeverityStatisticsByPercent`, and
  **`GetDSCI`** (Drought Severity and Coverage Index — a single 0–500 scalar per county per week).
  Weekly, 2000 → present, JSON. Free, no auth. DSCI is an unusually high-signal, low-dimensional
  drought feature and is cheap to ingest — strong recommend.
- **Copernicus Climate Data Store (ERA5/ERA5-Land native) [UNVERIFIED]** — free account + `cdsapi`
  key. Full global ERA5 at source, no intermediary rate limits, but request-queue latency measured in
  minutes-to-hours and NetCDF/GRIB output. Right choice for a one-time global historical backfill;
  wrong choice for per-request serving.
- **NOAA CPC** — free gridded US precipitation/temperature and seasonal outlooks via FTP/HTTP.

---

## 2. Crop / yield / ground truth

### 2.1 USDA NASS Quick Stats API **[SRC]** — the backbone of the label set

Base URL and endpoints verified verbatim from the rOpenSci `rnassqs` source (`R/request.R`); the full
parameter vector verified verbatim from `R/params.R`.

- **Base URL:** `https://quickstats.nass.usda.gov/api/`
- **Endpoints:**
  - `api_GET` — data retrieval
  - `get_param_values` — enumerate legal values for a parameter (invaluable for discovery; call this
    first rather than guessing enum strings)
  - `get_counts` — return the row count a query *would* return, without fetching it. **Always call
    this before `api_GET`** to stay under the record cap.
- **Auth:** **free API key**, requested at `https://quickstats.nass.usda.gov/api/` (email signup,
  issued immediately). Passed as the **`key=` query parameter**.
- **Formats:** `JSON`, `CSV`, `XML` (`format=` parameter). JSON responses carry ~39 fields per row.
- **Hard limit:** **50 000 records per request** — exceeding it returns **HTTP 413**. Partition by
  state and/or year.
- **Rate limiting:** HTTP **429** on over-request. `rnassqs` handles this by starting at ~5 requests/
  second and backing off to 1 request every 3 seconds after a 429 — a sensible client policy to copy.

**Complete valid parameter list (verified verbatim):**
`agg_level_desc`, `asd_code`, `asd_desc`, `begin_code`, `class_desc`, `commodity_desc`,
`congr_district_code`, `country_code`, `country_name`, `county_ansi`, `county_code`, `county_name`,
`domaincat_desc`, `domain_desc`, `end_code`, `freq_desc`, `group_desc`, `load_time`, `location_desc`,
`prodn_practice_desc`, `reference_period_desc`, `region_desc`, `sector_desc`, `short_desc`,
`state_alpha`, `state_ansi`, `state_name`, `state_fips_code`, `statisticcat_desc`, `source_desc`,
`unit_desc`, `util_practice_desc`, `watershed_code`, `watershed_desc`, `week_ending`, `year`, `zip_5`

**Comparison operators** append to any parameter name: `__LE`, `__LT`, `__GT`, `__GE`, `__LIKE`,
`__NOT_LIKE`, `__NE`.

#### Literal example: soybean YIELD by county in Iowa, 2000–2024

```
https://quickstats.nass.usda.gov/api/api_GET/?key=YOUR_KEY&source_desc=SURVEY&sector_desc=CROPS&group_desc=FIELD%20CROPS&commodity_desc=SOYBEANS&statisticcat_desc=YIELD&unit_desc=BU%20%2F%20ACRE&agg_level_desc=COUNTY&state_alpha=IA&year__GE=2000&year__LE=2024&format=JSON
```

Tighter and less ambiguous, pinning the exact series with `short_desc`:

```
https://quickstats.nass.usda.gov/api/api_GET/?key=YOUR_KEY&source_desc=SURVEY&short_desc=SOYBEANS%20-%20YIELD%2C%20MEASURED%20IN%20BU%20%2F%20ACRE&agg_level_desc=COUNTY&state_alpha=IA&year__GE=2000&year__LE=2024&format=JSON
```

Count-check first:
```
https://quickstats.nass.usda.gov/api/get_counts/?key=YOUR_KEY&commodity_desc=SOYBEANS&statisticcat_desc=YIELD&agg_level_desc=COUNTY&state_alpha=IA&year__GE=2000&year__LE=2024
```

#### Production and acreage (same pattern, different `statisticcat_desc`)

```
https://quickstats.nass.usda.gov/api/api_GET/?key=YOUR_KEY&source_desc=SURVEY&commodity_desc=SOYBEANS&statisticcat_desc=AREA%20HARVESTED&agg_level_desc=COUNTY&state_alpha=IA&year__GE=2000&year__LE=2024&format=JSON
```
`statisticcat_desc` values of interest: `YIELD`, `PRODUCTION`, `AREA PLANTED`, `AREA HARVESTED`,
`PRICE RECEIVED`, `PROGRESS`, `CONDITION`.

#### Weekly Crop Progress & Condition (state level)

```
https://quickstats.nass.usda.gov/api/api_GET/?key=YOUR_KEY&source_desc=SURVEY&commodity_desc=SOYBEANS&statisticcat_desc=PROGRESS&freq_desc=WEEKLY&agg_level_desc=STATE&state_alpha=IA&year__GE=2000&format=JSON
```
```
https://quickstats.nass.usda.gov/api/api_GET/?key=YOUR_KEY&source_desc=SURVEY&commodity_desc=SOYBEANS&statisticcat_desc=CONDITION&freq_desc=WEEKLY&agg_level_desc=STATE&state_alpha=IA&year__GE=2000&format=JSON
```
Relevant `short_desc` series: `SOYBEANS - PROGRESS, MEASURED IN PCT PLANTED`, `... PCT EMERGED`,
`... PCT BLOOMING`, `... PCT SETTING PODS`, `... PCT DROPPING LEAVES`, `... PCT HARVESTED`; and
`SOYBEANS - CONDITION, MEASURED IN PCT {VERY POOR|POOR|FAIR|GOOD|EXCELLENT}`. Join on `week_ending`.
Crop progress is **state-level only** — there is no county-level progress in Quick Stats. (NASS does
publish separate experimental *Crop Progress Gridded Layers* if you need spatial disaggregation.)

**Two data-quality caveats that will bite you:**
1. **Disclosure suppression.** County-level values are withheld where they would disclose individual
   operations, so county yield panels have structural holes concentrated in low-acreage counties.
   Your training set is therefore biased toward high-production counties. Plan for it explicitly
   rather than dropping NAs silently.
2. **`source_desc=SURVEY` vs `CENSUS`.** Always filter. SURVEY is annual; CENSUS is every 5 years
   (2002, 2007, 2012, 2017, 2022) with different methodology. Mixing them creates phantom duplicate
   rows for census years.

- **License:** US Government, public domain. **No commercial restriction.**

### 2.2 USDA-NASS Cropland Data Layer (CDL) **[DOC]**

- **CropScape web services:** `https://nassgeodata.gmu.edu/axis2/services/CDLService/{operation}`
  (WSDL at `?wsdl`). Operations: `GetCDLFile`, `GetCDLStat`, `GetCDLImage`, `GetCDLComp`,
  `GetCDLValue`, `ExtractCDLByValues`, `GetCDLPDF`. HTTP GET/POST with KVP or SOAP.
- **Example — county-level crop area statistics (Story County, Iowa, FIPS 19015):**
  ```
  https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLStat?year=2010&fips=19015&format=csv
  ```
- **Example — raster subset by bounding box (coordinates in CDL's Albers projection, EPSG:5070):**
  ```
  https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLFile?year=2009&bbox=130783,2203171,153923,2217961
  ```
  Note the `bbox` must be in **projected Albers meters, not lat/lon** — a very common integration error.
- **Bulk:** national GeoTIFFs from `https://www.nass.usda.gov/Research_and_Science/Cropland/Release/`
  and NRCS Data Gateway. 2025 releases: 10 m CDL ~9.8 GB, 30 m CDL ~1.9 GB.
- **Resolution / range:** 30 m (2008→present nationally; 10 m products in recent years), annual,
  CONUS. **Soybean class value = 5.**
- **Accuracy:** >90% for corn and soybeans in Corn Belt states; user's/producer's accuracy against FSA
  labels generally >95% for corn and soy 2008–2018. Weaker for minor crops and outside core regions.
- **Latency:** released the following winter — a **post-season mask only**, never available in-season.
  For in-season masking use the prior year's CDL, which is highly serviceable given corn/soy rotation
  is only partially predictable.
- **License:** **public domain, no copyright restrictions.** Fully usable commercially; NASS
  acknowledgment appreciated.

### 2.3 Soybean OIL and PROTEIN composition — the hard problem

The brief flagged this as central to the product, and I searched hard. **Here is the honest finding,
because it should shape the product design rather than be discovered later:**

> **There is no free, machine-readable, county-level dataset of soybean oil content by year in the
> United States. It does not exist publicly.** The best public composition data is **state- and
> region-level, published annually as PDF reports.**

This is the single biggest data gap in the proposed product and the most important thing on this page
after the Open-Meteo licensing flag.

**What actually exists:**

| Source | Granularity | Format | Range | Auth | Notes |
|---|---|---|---|---|---|
| **US Soybean Quality Annual Report** (Univ. of Minnesota / United Soybean Board / USSEC) | **State and region** (Western Corn Belt, Eastern Corn Belt, Midsouth, Southeast, East Coast) | **PDF only** | Survey series **1986 → present**; historical protein/oil table spans 1986–2019+ in recent editions | None | The single best public composition source. Farmer-submitted samples weighted by each state's soybean acreage; analyzed for protein, oil, amino acids, and soluble carbohydrates by **NIRS**. Recent nationwide values: **2024 = 34.0% protein / 19.9% oil; 2025 = 34.6% protein / 19.8% oil** (as-is basis). Hosted at `soyquality.com` (e.g. `https://soyquality.com/wp-content/uploads/2024/11/2024-Quality-Report.pdf`). |
| **USDA AMS Soybean Export Assessment** | Export elevator / port region | PDF + some tabular | ~2008 → present | None | ~400 export-inspected samples Sept–Jan, each graded with **protein, oil**, foreign material, moisture, test weight. Deliberately designed to be directly comparable to the farm-gate quality assessment. `https://www.ams.usda.gov/reports/soybean-export-assessment` |
| **USDA ERS Oil Crops Yearbook** | National | **XLSX + CSV** | Long historical series | None | The only public compilation of historical oilseed supply/demand/price statistics. Machine-readable. `https://www.ers.usda.gov/data-products/oil-crops-yearbook` |
| **SoyStats** (American Soybean Association) | National, some state | PDF | Annual | None | Reference figures; useful for sanity checks, not modeling. `https://soystats.com` |
| **USDA NASS Quick Stats** | — | — | — | Free key | **Does NOT contain seed oil or protein composition.** Yield/production/acreage/price only. Do not plan around finding it here. |
| **NOPA Monthly Crush Report** | National | Subscription | Monthly | **Paid** | ⚠️ **Not free — ~US$1 200/year, distributed exclusively via Refinitiv/LSEG.** Covers ~98% of US crush from 20 member companies. Excluded from any free-tier stack. |

**Practical implications for the product:**

1. **Your composition label is state-year, not county-year.** Plan the model accordingly: predict
   oil% at the state-year level, or build a county-level model with state-year labels using a
   hierarchical/mixed-effects structure that treats state-year as the observed aggregate. Do not
   pretend to county-level ground truth you do not have.
2. **The PDFs must be parsed.** Budget for a one-time ETL that extracts the annual state tables from
   ~40 years of quality-report PDFs into a tidy `state × year × {protein, oil}` table. This is
   tractable (the tables are consistently structured within report eras) and it is a genuine
   proprietary asset once built — it is precisely the work competitors skip.
3. **The agronomic signal is real and well-established**, which is what makes the modeling viable
   despite coarse labels: oil and protein trade off inversely, and the tradeoff is driven strongly by
   temperature and water status during seed fill (R5–R6). This is why the VPD, root-zone soil moisture,
   and growing-degree-day variables called out in the weather section matter more for the oil product
   than they do for the yield product.
4. **Field-level composition data is commercial.** Elevator and processor NIR data exists but is
   private. If the product needs field-level oil truth, that is a partnership/BD problem, not a
   public-data problem.

### 2.4 USDA FAS PSD — global production context **[DOC]**

- **Endpoints:** `https://apps.fas.usda.gov/PSDOnlineDataServices/api/CommodityData/GetAllData` and
  the FAS Open Data portal `https://apps.fas.usda.gov/opendatawebv2/`
- **Auth:** **free API key**, in request headers.
- **Coverage:** Production/Supply/Distribution by **country × commodity × market year**, monthly WASDE-
  aligned updates. Covers soybeans (`0813100` area), soybean oil (`4232000`), soybean meal.
- **Global coverage:** yes — this is the primary free source for non-US soybean supply/demand.
- **License:** US Government, no commercial restriction.

---

## 3. Soil

| Source | Gives us | Base URL | Auth | Resolution | Commercial use |
|---|---|---|---|---|---|
| **USDA Soil Data Access (SSURGO)** | Authoritative US soil: AWC, texture, OM, drainage class, by map unit/component/horizon | `https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest` | **None** | Map-unit polygons (~1:12k–1:24k) | **Yes — public domain** |
| **SoilGrids (ISRIC)** | Global gridded soil properties with uncertainty | `https://rest.isric.org/soilgrids/v2.0/properties/query` | **None** | 250 m | **Yes — CC-BY 4.0** |
| **POLARIS** | 30 m probabilistic CONUS soil properties | `http://hydrology.cee.duke.edu/POLARIS/` | None (bulk files) | **30 m** | Research dataset — verify **[UNVERIFIED]** |

### 3.1 USDA Soil Data Access (SDA) / SSURGO **[DOC]**

- **REST/POST endpoint:** `https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest`
- Accepts **raw SQL** in a POST body (`{"query": "...", "format": "JSON+COLUMNNAME"}`) against the Soil
  Data Mart. Returns denormalized map unit / component / horizon rows with typed properties. There is
  also a spatial service for geometry.
- **Key columns for this product:** `mapunit.mukey`, `muaggatt.aws0100wta` /
  `aws0150wta` (available water storage 0–100 cm / 0–150 cm, mm — **the single most useful soil
  variable for soybean yield**), `muaggatt.drclassdcd` (drainage class), `component.slope_r`,
  `chorizon.awc_r` (available water capacity, cm/cm), `chorizon.claytotal_r`, `chorizon.sandtotal_r`,
  `chorizon.silttotal_r`, `chorizon.om_r` (organic matter %), `chorizon.ph1to1h2o_r`,
  `chorizon.hzdept_r` / `hzdepb_r` (horizon depth top/bottom), plus `component.comppct_r` for
  area-weighting components within a map unit.
- **Modeling note:** SSURGO is hierarchical (map unit → component → horizon) with percentage weights.
  You must depth-weight horizons and area-weight components to get a single value per location.
  `muaggatt.aws*` gives you pre-aggregated available water storage and saves most of that work — start
  there. The `soilDB` R package's aggregation logic is a good reference implementation.
- **Auth: none.** **License: public domain**, commercial use fine. Authoritative for the US and should
  outrank SoilGrids wherever both cover.

### 3.2 SoilGrids (ISRIC) **[DOC]**

- **Endpoint:** `https://rest.isric.org/soilgrids/v2.0/properties/query`
- **Parameters:** `lon` (−180…180), `lat` (−90…90), `property` (repeatable), `depth` (repeatable),
  `value` (repeatable)
- **Properties:** `bdod` (bulk density), `cec`, `cfvo` (coarse fragments), `clay`, `nitrogen`,
  `phh2o`, `sand`, `silt`, `soc` (soil organic carbon), `ocd` (organic carbon density),
  **`wv0010`, `wv0033`, `wv1500`** (volumetric water content at 10, 33, and 1500 kPa — i.e. field
  capacity and permanent wilting point, from which **plant-available water = `wv0033` − `wv1500`**)
- **Depths:** `0-5cm`, `5-15cm`, `15-30cm`, `30-60cm`, `60-100cm`, `100-200cm`
- **Values:** `mean`, `Q0.05`, `Q0.5`, `Q0.95` (the quantiles give you genuine per-pixel uncertainty,
  which SSURGO does not provide and which is useful for uncertainty-aware yield models)
- **Resolution:** 250 m, **global**
- **⚠️ Rate limit:** fair use is **5 API calls per 1-minute period** — extremely tight. For anything
  beyond spot lookups, download the bulk GeoTIFF/VRT coverages from ISRIC rather than hitting the API.
  Do not build a per-request ingestion path on this endpoint.
- **Status:** REST v2.0 is still described as beta with no uptime or support guarantee.
- **License:** **CC-BY 4.0 — commercial use permitted with attribution.**
- **Example:**
  ```
  https://rest.isric.org/soilgrids/v2.0/properties/query?lon=-93.62&lat=42.03&property=clay&property=sand&property=soc&property=wv0033&property=wv1500&depth=0-5cm&depth=5-15cm&depth=15-30cm&value=mean&value=Q0.05&value=Q0.95
  ```

### 3.3 POLARIS **[DOC]**

- **Download:** `http://hydrology.cee.duke.edu/POLARIS/`
- **30 m probabilistic soil property maps for CONUS** (Chaney et al., 2019, *Water Resources Research*).
  Built by machine-learning disaggregation (DSMART-HPC) of SSURGO, which fixes SSURGO's polygon
  discretization, harmonizes across survey areas, and fills spatial gaps. Provides 100-bin histograms
  per cell per layer plus summary statistics at 30/300/3000 m.
- **Properties:** sand, silt, clay, organic matter, bulk density, pH, saturated hydraulic conductivity,
  and van Genuchten water-retention parameters, over 6 depth layers to 200 cm.
- **Access:** bulk GeoTIFF tiles over HTTP; no API. The `XPolaris` R package wraps retrieval.
- **Where it wins:** highest-resolution free US soil data and gap-free, so it is the right choice for
  **field-scale** modeling where SSURGO polygon edges create artifacts. Use SSURGO for county-scale
  aggregates (authoritative), POLARIS for within-field variation.
- **License [UNVERIFIED]:** academic dataset; Duke's tech-transfer office lists POLARIS as a
  technology, so **confirm commercial terms before shipping.** SSURGO + SoilGrids together cover the
  same needs with unambiguous permissions.

---

## 4. Remote sensing vegetation indices

Ranked by *practicality without credentials*, which is what the brief asked for.

| Source | Practical without credentials? | Base URL | Resolution | Range |
|---|---|---|---|---|
| **ORNL MODIS/VIIRS Subsets REST** | ✅ **Yes — best no-auth option** | `https://modis.ornl.gov/rst/api/v1/` | 250 m–1 km | 2000 → present |
| **Element 84 Earth Search (Sentinel-2 on AWS)** | ✅ **Yes — no credentials** | `https://earth-search.aws.element84.com/v1` | 10 m | 2015 → present |
| **Microsoft Planetary Computer STAC** | ✅ Mostly (anonymous search; SAS token for some assets) | `https://planetarycomputer.microsoft.com/api/stac/v1` | 10–30 m | Varies |
| **USGS LandsatLook STAC** | ✅ Search yes; some asset downloads may need auth | `https://landsatlook.usgs.gov/stac-server` | 30 m | 1982 → present |
| **NASA AppEEARS** | ❌ Earthdata Login token required | `https://appeears.earthdatacloud.nasa.gov/api/` | 250 m–1 km | 2000 → present |
| **NASA HLS (L30/S30)** | ❌ Earthdata Login required | LP DAAC / CMR STAC | 30 m, ~2–3 day revisit | 2013 → present |
| **Copernicus Data Space** | ❌ OAuth2 registration | `https://catalogue.dataspace.copernicus.eu` | 10 m | 2015 → present |
| **Google Earth Engine** | ❌ Google account + project; **commercial use requires paid license** | — | Everything | Everything |

### 4.1 ORNL MODIS/VIIRS Subsets REST Web Service **[SRC]** — start here

Base URL verified verbatim from `MODISTools` (`R/zzz.R`); endpoint shape and parameters from
`R/mt_subset.R`.

- **Base URL:** `https://modis.ornl.gov/rst/api/v1/`
- **Endpoints:** `/products`, `/{product}/bands`, `/{product}/dates`, `/{product}/subset`,
  `/{product}/{site_id}/subset`. Interactive OpenAPI UI at `https://modis.ornl.gov/rst/ui/`.
- **Subset parameters (verified):** `latitude`, `longitude`, `band`, `startDate`, `endDate`,
  `kmAboveBelow`, `kmLeftRight`
- **Products for this product:** `MOD13Q1` / `MYD13Q1` (250 m, 16-day NDVI **and EVI**),
  `MOD13A1` (500 m), `VNP13A1` (VIIRS 500 m, continuity after MODIS end-of-life),
  `MCD15A3H` (LAI/FPAR, 4-day), `MOD11A2` (land surface temperature — useful as a canopy-stress proxy).
- **Bands:** e.g. `250m_16_days_NDVI`, `250m_16_days_EVI`, `250m_16_days_pixel_reliability`
  (always ingest the reliability/QA band and filter on it — unfiltered MODIS VI series are badly
  contaminated by cloud).
- **⚠️ Practical limit:** the service accepts a limited number of dates per request; `MODISTools`
  chunks requests into groups of **10 dates**. Long time series therefore require many sequential
  calls. Plan for a queued, resumable backfill, not a single request.
- **Auth: none.** This is the only vegetation-index source here that needs no credentials at all and
  returns tidy point time series, which makes it the pragmatic choice for a county- or field-centroid
  feature pipeline.
- **Example:**
  ```
  https://modis.ornl.gov/rst/api/v1/MOD13Q1/subset?latitude=42.03&longitude=-93.62&band=250m_16_days_NDVI&startDate=A2024001&endDate=A2024193&kmAboveBelow=1&kmLeftRight=1
  ```
  Note MODIS date format `A{YYYY}{DDD}`; use `/MOD13Q1/dates` to list valid composite dates first.

### 4.2 Element 84 Earth Search — Sentinel-2 at 10 m, no credentials **[DOC]**

- **STAC API:** `https://earth-search.aws.element84.com/v1`
- **Collections:** `sentinel-2-c1-l2a` (Collection-1 L2A COGs), `sentinel-2-l2a`, `landsat-c2-l2`
- **Works without any credentials** — no registration, no auth. Standard STAC `POST /search` with
  `collections`, `bbox`/`intersects`, `datetime`, `query` (e.g. cloud cover filter). Assets are
  public COGs on S3, readable directly with GDAL/rasterio/`stackstac`.
- **⚠️ Known gotcha:** band value **offsets differ across Sentinel-2 processing baselines** within the
  collection. Sentinel-2 introduced a radiometric offset at baseline 04.00; if you ignore it, NDVI
  values silently shift mid-timeseries. Read `earthsearch:boa_offset_applied` / the product baseline
  from item properties and normalize before computing indices. This is the most common source of
  spurious trend in Sentinel-2 NDVI pipelines.
- **License:** Copernicus Sentinel data — free, full, open, **commercial use permitted** with
  attribution.
- **Example:**
  ```
  POST https://earth-search.aws.element84.com/v1/search
  {"collections":["sentinel-2-c1-l2a"],"bbox":[-93.7,41.9,-93.5,42.1],
   "datetime":"2024-05-01T00:00:00Z/2024-10-01T00:00:00Z",
   "query":{"eo:cloud_cover":{"lt":20}},"limit":100}
  ```

### 4.3 The credentialed options, and whether they're worth it

- **NASA AppEEARS** — requires an Earthdata Login account, then `POST /login` for a bearer token
  attached to every subsequent call. Genuinely excellent: submits point or area sample jobs across
  MODIS/VIIRS/Landsat/HLS/SMAP, applies QA filtering, returns tidy CSV. **Earthdata registration is
  free and takes two minutes** — I would not let "easy auth" disqualify it. It is asynchronous
  (submit → poll → download), so it suits scheduled backfills rather than request-time serving.
- **NASA HLS (HLSL30 / HLSS30)** — Harmonized Landsat + Sentinel-2, **30 m with ~2–3 day revisit**,
  cloud-optimized in NASA Earthdata Cloud, Earthdata Login required. This is the best free
  spatiotemporal compromise for field-scale in-season monitoring and is worth the auth burden once the
  product needs sub-field resolution.
- **Copernicus Data Space Ecosystem** — OAuth2 client credentials; the authoritative Sentinel source.
  Prefer Earth Search for the no-auth path unless you need Copernicus-only products.
- **Google Earth Engine** — ⚠️ **Flag: commercial use requires a paid Google Earth Engine commercial
  license.** The free tier is restricted to research, education, and nonprofit use. GEE is far and
  away the fastest way to *prototype* zonal NDVI over thousands of counties, so use it for research
  and feasibility work, but do not architect the production pipeline on it without budgeting the
  commercial license.
- **NASA Harvest** — a research program and partnership network rather than an API. Publishes valuable
  crop-type maps, labeled training datasets, and methods papers; treat it as a source of ancillary
  datasets and validation labels, not an ingestion endpoint.

---

## 5. Market / price context

| Source | Gives us | Base URL | Auth | License |
|---|---|---|---|---|
| **USDA AMS Market News (MARS) API** | Cash/spot soybean, soybean oil and meal prices from official market reports | `https://marsapi.ams.usda.gov/services/v1.2/reports` | **Free key** (HTTP Basic, key as username) | US Gov — commercial OK |
| **USDA ERS Oil Crops Yearbook** | Long historical soybean oil/meal price + supply/demand, XLSX/CSV | `https://www.ers.usda.gov/data-products/oil-crops-yearbook` | None | US Gov — commercial OK |
| **USDA FAS PSD** | Global soybean/oil/meal S&D | `https://apps.fas.usda.gov/PSDOnlineDataServices/` | Free key | US Gov — commercial OK |
| **NOPA Crush Report** | Monthly US crush (~98% coverage) | via Refinitiv/LSEG | ⚠️ **Paid ~$1 200/yr** | Excluded from free stack |
| **CME Group** | Futures settlements for ZS / ZL / ZM → board crush | `cmegroup.com` | None for delayed/historical | Check redistribution terms |

**USDA AMS MARS API [DOC]:** register free at `https://mymarketnews.ams.usda.gov`, retrieve your key
from My Profile. REST, JSON (and XLSX) responses, including errors. Endpoints follow
`/services/v1.2/reports/{slug_id}` with `q` filters and `?format=json`. Relevant reports include the
**National Grain and Oilseed Processor Feedstuff Report** (report 3511) and the grain/oilseed cash
market series. This is the correct free source for cash-market soybean oil and meal prices.

**Crush spread:** compute it yourself rather than sourcing it. The CME board crush is
`(meal price × 0.022) + (oil price × 11) − soybean price`, reflecting standard yields of 44 lb meal
(0.022 short tons) and 11 lb oil per bushel. Since your product predicts *oil content*, note the
interesting product angle: crush margin is sensitive to actual oil yield per bushel, so a credible
oil-content forecast has direct economic value to crushers — that is the commercial thesis, and it
argues for making predicted oil% per bushel a first-class output alongside yield.

---

## 6. Commercial-use restrictions — consolidated flag list

Read this section before writing a single line of ingestion code.

| Source | Status | Detail |
|---|---|---|
| **Open-Meteo free API** | 🔴 **BLOCKED for commercial** | Free endpoints are non-commercial only. Use the paid `customer-*` endpoints, or self-host from the CC-BY-4.0 AWS Open Data bucket. Data itself is CC-BY-4.0. |
| **PRISM (4 km purchased tier)** | 🔴 **BLOCKED for commercial** | "Commercial use is strictly prohibited" absent advance arrangement; no duplication or redistribution. Substitute gridMET. |
| **NOPA Crush Report** | 🔴 Paid only | ~$1 200/yr via Refinitiv/LSEG. |
| **Google Earth Engine** | 🔴 Paid for commercial | Free tier limited to research/education/nonprofit. Fine for prototyping. |
| **gridMET** | 🟡 Verify | Widely used commercially; explicit terms not confirmed. Get written confirmation from Climatology Lab. |
| **POLARIS** | 🟡 Verify | Academic dataset, listed by Duke tech transfer. Confirm before shipping. |
| **SoilGrids** | 🟢 OK | CC-BY 4.0. Attribution required. Beta service, 5 calls/min — use bulk downloads. |
| **NASA POWER, Daymet, NOAA (all), NASS Quick Stats, CDL, SSURGO, AMS, ERS, FAS PSD, USDM** | 🟢 OK | US Government / NASA open data. Public domain or open licence, commercial use permitted, citation appreciated. |
| **Sentinel-2 / Copernicus** | 🟢 OK | Free, full and open policy; commercial use permitted with attribution. |
| **Landsat / HLS** | 🟢 OK | USGS/NASA public domain. |
| **US Soybean Quality Annual Report** | 🟡 Verify | Published by USB/USSEC/Univ. of Minnesota. Free to read; confirm terms for redistributing extracted tables in a commercial product. Deriving aggregate model parameters is a much safer posture than republishing the tables. |

---

## 7. Recommended stack — minimum viable ingestion layer

Five sources. This is the smallest set that supports a credible US soybean yield **and** oil-content
product, ordered by build priority.

### 1. USDA NASS Quick Stats — the label set (free key)
`https://quickstats.nass.usda.gov/api/api_GET/`

**Pull:** county-year soybean `YIELD` (BU/ACRE), `PRODUCTION`, `AREA PLANTED`, `AREA HARVESTED` for all
soybean states, `source_desc=SURVEY`, 1990→present; plus state-week `PROGRESS` (planted, blooming,
setting pods, dropping leaves, harvested) and `CONDITION` (pct very poor→excellent).
**Why first:** it is the ground truth. Nothing else can be validated until this exists. Call
`get_counts` before every `api_GET`, partition by state-year to stay under 50 000 records, and handle
disclosure-suppressed counties explicitly rather than dropping them.

### 2. Open-Meteo Historical (ERA5) + Forecast — the weather features (**paid tier required**)
`https://archive-api.open-meteo.com/v1/archive` → `https://customer-archive-api.open-meteo.com/v1/archive`
`https://api.open-meteo.com/v1/forecast` → `https://customer-api.open-meteo.com/v1/forecast`

**Pull, daily, per county centroid or soybean-mask centroid, `models=era5_seamless`:**
`temperature_2m_max`, `temperature_2m_min`, `temperature_2m_mean`, `precipitation_sum`,
`shortwave_radiation_sum`, `et0_fao_evapotranspiration`, `vapour_pressure_deficit_max`,
`relative_humidity_2m_mean`, `dew_point_2m_mean`, `soil_moisture_0_to_7cm_mean`,
`soil_moisture_7_to_28cm_mean`, `soil_moisture_28_to_100cm_mean`, `soil_moisture_0_to_100cm_mean`,
`soil_temperature_0_to_7cm_mean`.
Plus hourly `growing_degree_days_base_0_limit_50` and `leaf_wetness_probability` where you want
finer-grained accumulations. Forecast side: same daily variables with `past_days=92&forecast_days=16`.
**Why:** one API, one parameter vocabulary, 1940→present, covers every weather predictor the model
needs including the depth-resolved soil moisture that most free sources lack — and it is **global**, so
the same client extends to Brazil and Argentina with no new integration.
**Non-negotiable:** build the client with a configurable base URL + `apikey` and buy the commercial
plan before launch. Consider self-hosting from the AWS Open Data bucket once volume justifies it.

### 3. USDA Soil Data Access (SSURGO) — the static soil layer (no auth)
`https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest`

**Pull:** per county (or per field polygon), area-weighted `muaggatt.aws0100wta` and `aws0150wta`
(available water storage, mm), `drclassdcd` (drainage class), `component.slope_r`, and depth-weighted
`chorizon.awc_r`, `claytotal_r`, `sandtotal_r`, `silttotal_r`, `om_r`, `ph1to1h2o_r`.
**Why:** soil available water capacity is the dominant static control on soybean yield stability and
strongly conditions how weather stress translates into yield and oil loss. It is authoritative, public
domain, free, and — being static — needs pulling exactly once. Highest value-per-unit-effort on this
list. Add SoilGrids (CC-BY, bulk download) only when you extend outside the US.

### 4. ORNL MODIS/VIIRS Subsets — the in-season vegetation signal (no auth)
`https://modis.ornl.gov/rst/api/v1/{product}/subset` (e.g. `.../MOD13Q1/subset`)

**Pull:** `MOD13Q1` bands `250m_16_days_NDVI`, `250m_16_days_EVI`, and
`250m_16_days_pixel_reliability` (filter on it), 2000→present, plus `VNP13A1` for VIIRS continuity.
Derive per-county seasonal integrals and peak/timing features over a CDL soybean mask.
**Why:** NDVI/EVI integrals are the strongest single in-season yield predictor available for free, and
this is the only vegetation-index source requiring no credentials whatsoever. Chunk requests in groups
of ~10 dates and make the backfill resumable.
**Companion:** the annual CDL (`GetCDLStat` / national GeoTIFF, soybean class **5**, public domain) as
the soybean acreage mask. Use the prior year's CDL in-season since the current year won't publish
until winter.

### 5. US Soybean Quality Annual Report — the oil/protein label (PDF ETL)
`https://soyquality.com` (+ USDA AMS Soybean Export Assessment, ERS Oil Crops Yearbook for
machine-readable national series)

**Pull:** state × year × {protein %, oil %} from every annual report edition, 1986→present, via a
one-time PDF table-extraction pipeline.
**Why fifth but essential:** this *is* the seed-oil target variable, and no free alternative exists.
Accept up front that it is **state-year, not county-year** — architect the oil model hierarchically
with state-year aggregate labels rather than inventing county-level truth. Building this parsed table
is the highest-leverage proprietary asset in the whole stack, precisely because the data is trapped in
PDFs and everyone else skips it.

### Strong optional adds (cheap, high signal)
- **US Drought Monitor `GetDSCI`** — one 0–500 scalar per county per week, free, no auth, 2000→present.
  Trivial to ingest, meaningful lift. `https://usdmdataservices.unl.edu/api/`
- **gridMET** — 4 km native VPD and reference ET with next-day latency, better resolved than
  Open-Meteo's ERA5 over CONUS. Add once the NetCDF/OPeNDAP tooling is justified, and pin down the
  commercial terms first.
- **NASA POWER** — free, no-auth, no commercial restriction. Keep it wired as a **fallback/redundancy
  path** so an Open-Meteo outage or contract lapse cannot take the product down.
- **USDA AMS MARS API** — free key, cash soybean oil and meal prices for the crush-margin framing that
  turns an oil-content forecast into a dollar number.

### Global extension
Open-Meteo (global, 1940→), NASA POWER (global), SoilGrids (global), MODIS/VIIRS (global) and USDA FAS
PSD (all countries) already give worldwide coverage with the *same* client code. What does **not**
extend is the US ground truth: NASS county yields, CDL, SSURGO, and the US quality survey are all
US-only. For Brazil/Argentina you would need CONAB and the Buenos Aires Grain Exchange respectively
as substitute label sources.

---

## 8. Smoke-test script — run this from an unrestricted network first

Because no endpoint below could be exercised from this session, validate before building. This
exercises every no-auth source in the recommended stack.

```bash
#!/usr/bin/env bash
# Verify the no-auth endpoints in the recommended stack. Expect HTTP 200 from each.
set -u
LAT=42.03; LON=-93.62

check () { printf '%-22s %s\n' "$1" "$(curl -sS -m 30 -o /dev/null -w '%{http_code}' "$2")"; }

check "open-meteo/archive"  "https://archive-api.open-meteo.com/v1/archive?latitude=$LAT&longitude=$LON&start_date=2024-07-01&end_date=2024-07-03&daily=temperature_2m_max,precipitation_sum,et0_fao_evapotranspiration,vapour_pressure_deficit_max,soil_moisture_0_to_100cm_mean&models=era5_seamless&timezone=UTC"
check "open-meteo/forecast" "https://api.open-meteo.com/v1/forecast?latitude=$LAT&longitude=$LON&daily=temperature_2m_max,precipitation_sum&past_days=7&forecast_days=16&timezone=UTC"
check "nasa-power"          "https://power.larc.nasa.gov/api/temporal/daily/point?parameters=T2M_MAX,T2M_MIN,PRECTOTCORR,ALLSKY_SFC_SW_DWN,RH2M,GWETROOT&community=ag&latitude=$LAT&longitude=$LON&start=20240701&end=20240703&format=json"
check "daymet(vars)"        "https://daymet.ornl.gov/single-pixel/api/data?lat=$LAT&lon=$LON&vars=tmax,tmin,prcp,srad,vp&start=2023-07-01&end=2023-07-03"
check "daymet(measured)"    "https://daymet.ornl.gov/single-pixel/api/data?lat=$LAT&lon=$LON&measuredParams=tmax,tmin,prcp,srad,vp&year=2023"
check "ncei-access"         "https://www.ncei.noaa.gov/access/services/data/v1?dataset=daily-summaries&stations=USW00014933&startDate=2024-07-01&endDate=2024-07-03&dataTypes=TMAX,TMIN,PRCP&format=json&units=metric"
check "soilgrids"           "https://rest.isric.org/soilgrids/v2.0/properties/query?lon=$LON&lat=$LAT&property=clay&property=wv0033&property=wv1500&depth=0-5cm&value=mean"
check "modis-ornl/dates"    "https://modis.ornl.gov/rst/api/v1/MOD13Q1/dates?latitude=$LAT&longitude=$LON"
check "cropscape/stat"      "https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLStat?year=2020&fips=19015&format=csv"
check "usdm/dsci"           "https://usdmdataservices.unl.edu/api/CountyStatistics/GetDSCI?aoi=19015&startdate=1/1/2024&enddate=12/31/2024&statisticsType=1"
check "earth-search"        "https://earth-search.aws.element84.com/v1/collections"
check "landsatlook"         "https://landsatlook.usgs.gov/stac-server/collections"

# NWS requires a descriptive User-Agent or it rejects the request:
printf '%-22s %s\n' "nws/points" "$(curl -sS -m 30 -o /dev/null -w '%{http_code}' \
  -H 'User-Agent: soy-yield-research (cchiodo@feedenergy.com)' \
  "https://api.weather.gov/points/$LAT,$LON")"

# SSURGO / Soil Data Access is POST + SQL:
curl -sS -m 60 -X POST "https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest" \
  -H 'Content-Type: application/json' \
  -d '{"format":"JSON+COLUMNNAME","query":"SELECT TOP 5 mukey, aws0100wta, drclassdcd FROM muaggatt"}' | head -c 400; echo

# Quick Stats needs your free key:
# curl -sS "https://quickstats.nass.usda.gov/api/get_counts/?key=$NASS_KEY&commodity_desc=SOYBEANS&statisticcat_desc=YIELD&agg_level_desc=COUNTY&state_alpha=IA&year__GE=2000&year__LE=2024"
```

Note the deliberate duplicate Daymet check — it resolves the `vars` vs `measuredParams` discrepancy
flagged in §1.6.

---

## Sources

- [Open-Meteo OpenAPI specs (historical-weather.yml, forecast.yml, climate.yml)](https://github.com/open-meteo/open-meteo/tree/main/openapi)
- [open-meteo/open-data README (CC-BY-4.0, data sources)](https://github.com/open-meteo/open-data/blob/main/README.md)
- [Open-Meteo Historical Weather API docs](https://open-meteo.com/en/docs/historical-weather-api) · [Pricing](https://open-meteo.com/en/pricing) · [Terms](https://open-meteo.com/en/terms) · [Historical Forecast API](https://open-meteo.com/en/docs/historical-forecast-api)
- [rOpenSci nasapower source (get_power.R)](https://github.com/ropensci/nasapower) · [NASA POWER Temporal API docs](https://power.larc.nasa.gov/docs/services/api/temporal/) · [Daily API](https://power.larc.nasa.gov/docs/services/api/temporal/daily/)
- [daymetr source (zzz.r, download_daymet.r)](https://github.com/bluegreen-labs/daymetr) · [Daymet Single Pixel Extraction Tool guide](https://daac.ornl.gov/DAYMET/guides/Daymet_SPET.html) · [ORNL daymet-single-pixel-batch](https://github.com/ornldaac/daymet-single-pixel-batch)
- [rOpenSci rnassqs source (request.R, params.R)](https://github.com/ropensci/rnassqs) · [NASS Quick Stats API](https://quickstats.nass.usda.gov/api) · [NASS Developers](https://www.nass.usda.gov/developer/index.php)
- [NCEI Access Data Service documentation](https://www.ncei.noaa.gov/support/access-data-service-api-user-documentation) · [NOAA CDO Web Services v2](https://www.ncdc.noaa.gov/cdo-web/webservices/v2)
- [api.weather.gov general FAQs](https://weather-gov.github.io/api/general-faqs) · [Gridpoint FAQs](https://weather-gov.github.io/api/gridpoints)
- [gridMET — Climatology Lab](https://www.climatologylab.org/gridmet.html) · [PRISM web service docs](https://prism.oregonstate.edu/documents/PRISM_downloads_web_service_v1.pdf) · [PRISM ordering/terms](https://prism.oregonstate.edu/orders/)
- [Soil Data Access web service help](https://sdmdataaccess.nrcs.usda.gov/webservicehelp.aspx) · [SSURGO overview](https://www.nrcs.usda.gov/resources/data-and-reports/soil-survey-geographic-database-ssurgo)
- [ISRIC SoilGrids REST](https://rest.isric.org/) · [SoilGrids](https://isric.org/explore/soilgrids) · [soilDB fetchSoilGrids](https://ncss-tech.github.io/soilDB/reference/fetchSoilGrids.html)
- [POLARIS (Chaney et al. 2019, WRR)](https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2018WR022797) · [XPolaris](https://pmc.ncbi.nlm.nih.gov/articles/PMC8390218/)
- [CropScape web service dev help](https://nassgeodata.gmu.edu/CropScape/devhelp/cropscapews.html) · [CDL 2024 metadata](https://data.nass.usda.gov/Research_and_Science/Cropland/metadata/metadata_Cropland-Data-Layer-2024.htm) · [CDL releases](https://www.nass.usda.gov/Research_and_Science/Cropland/Release/) · [CDL FAQs](https://www.nass.usda.gov/Research_and_Science/Cropland/sarsfaqs2.php)
- [MODISTools source (zzz.R, mt_subset.R)](https://github.com/bluegreen-labs/MODISTools) · [ORNL MODIS/VIIRS web service guide](https://daac.ornl.gov/LAND_VAL/guides/MODIS_Web_Service_C6_V2.html) · [ornldaac/modis-viirs-rest-api-python](https://github.com/ornldaac/modis-viirs-rest-api-python)
- [Element 84 Earth Search](https://element84.com/earth-search/) · [Earth Search v1 announcement](https://element84.com/geospatial/introducing-earth-search-v1-new-datasets-now-available/) · [LandsatLook STAC](https://landsatlook.usgs.gov/stac-server/api.html) · [AppEEARS API](https://appeears.earthdatacloud.nasa.gov/api/) · [nasa/HLS-Data-Resources](https://github.com/nasa/HLS-Data-Resources)
- [US Soybean Quality Annual Report 2024](https://soyquality.com/wp-content/uploads/2024/11/2024-Quality-Report.pdf) · [USB Quality Surveys (Iowa State)](https://crops.extension.iastate.edu/united-soybean-board-quality-surveys) · [USDA AMS Soybean Export Assessment](https://www.ams.usda.gov/reports/soybean-export-assessment) · [Assessing Variation in US Soybean Seed Composition](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6421286/)
- [USDA ERS Oil Crops Yearbook](https://www.ers.usda.gov/data-products/oil-crops-yearbook) · [NOPA Monthly Crush Report](https://www.nopa.org/resources/nopa-monthly-crush-report/) · [CME Soybean Crush Reference Guide](https://www.cmegroup.com/content/dam/cmegroup/education/files/soybean-crush-reference-guide.pdf)
- [USDA AMS MARS API getting started](https://mymarketnews.ams.usda.gov/mars-api/getting-started) · [AMS APIs & Open Data](https://www.ams.usda.gov/resources/apis-open-data) · [USDA FAS Databases & Applications](https://www.fas.usda.gov/data/databases-applications)
- [US Drought Monitor web service info](https://droughtmonitor.unl.edu/DmData/DataDownload/WebServiceInfo.aspx)
