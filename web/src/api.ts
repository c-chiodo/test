// Typed API client for the OleoCast backend.

export interface Region {
  region_id: string;
  name: string;
  state: string;
  fips: string;
  lat: number;
  lon: number;
  maturity_group: number;
  typical_planting_doy: number;
  soil_awc_mm: number;
}

export interface Prediction {
  target: string;
  value: number;
  lo80: number;
  hi80: number;
  lo90: number;
  hi90: number;
}

export interface Driver {
  feature: string;
  value: number;
  shap: number;
}

export interface Crush {
  oil_lb_per_bu: number;
  meal_lb_per_bu: number;
  meal_protein_pct: number;
  oil_value_usd_bu: number;
  meal_value_usd_bu: number;
  epv_usd_bu: number;
  gross_margin_usd_bu: number;
  oil_quality_adj_usd_lb: number;
  oil_lb_per_ac: number;
  epv_usd_ac: number;
}

export interface Forecast {
  region_id: string;
  year: number;
  as_of: string;
  planting_date: string;
  maturity_group: number;
  current_stage: string;
  season_coverage: number;
  weather_source: string;
  stage_dates: Record<string, string>;
  predictions: Record<string, Prediction>;
  oil_yield_lb_ac: number;
  drivers: Record<string, Driver[]>;
  crush: Crush;
  weather_daily: {
    date: string;
    tmax_c: number;
    tmin_c: number;
    precip_mm: number;
    observed: boolean;
  }[];
}

export interface ProgressionPoint {
  as_of: string;
  stage: string;
  coverage: number;
  yield: Prediction;
  oil: Prediction;
  oil_yield_lb_ac: number;
}

export interface ValidationReport {
  target: string;
  loyo_rmse: number;
  loyo_mae: number;
  loyo_r2: number;
  baseline_mean_rmse: number;
  baseline_trend_rmse: number;
  skill_vs_trend: number;
  conformal_q90: number;
  coverage_check: number;
}

export interface ModelMeta {
  feature_names: string[];
  targets: string[];
  n_train: number;
  years: [number, number];
  regions: string[];
  conformal: Record<string, { q80: number; q90: number }>;
  validation: Record<string, ValidationReport>;
  training_data: string;
}

export interface HistoryYear {
  year: number;
  yield_bu_ac: number;
  oil_pct: number;
  protein_pct: number;
  oil_yield_lb_ac: number;
  epv_usd_ac: number;
  fill_start: string | null;
  planting: string;
}

export interface HistoryStats {
  mean: number;
  min: number;
  max: number;
  std: number;
}

export interface History {
  region_id: string;
  years: HistoryYear[];
  stats: Record<string, HistoryStats>;
}

export interface DataSource {
  name: string;
  kind: string;
  auth: string;
  license: string;
  status: string;
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json() as Promise<T>;
}

export const api = {
  regions: () => get<Region[]>("/api/regions"),
  modelMeta: () => get<ModelMeta>("/api/model/meta"),
  dataSources: () => get<DataSource[]>("/api/datasources"),
  forecast: (p: {
    region_id: string;
    year: number;
    as_of?: string;
    scenario_temp_c?: number;
    scenario_precip_pct?: number;
    include_weather?: boolean;
  }) => {
    const q = new URLSearchParams({
      region_id: p.region_id,
      year: String(p.year),
      ...(p.as_of ? { as_of: p.as_of } : {}),
      ...(p.scenario_temp_c ? { scenario_temp_c: String(p.scenario_temp_c) } : {}),
      ...(p.scenario_precip_pct
        ? { scenario_precip_pct: String(p.scenario_precip_pct) }
        : {}),
      ...(p.include_weather ? { include_weather: "true" } : {}),
    });
    return get<Forecast>(`/api/forecast?${q}`);
  },
  progression: (region_id: string, year: number, step_days = 14) =>
    get<ProgressionPoint[]>(
      `/api/progression?region_id=${region_id}&year=${year}&step_days=${step_days}`,
    ),
  history: (region_id: string, year: number, n_years = 10) =>
    get<History>(
      `/api/history?region_id=${region_id}&year=${year}&n_years=${n_years}`,
    ),
};

// Friendly labels for feature names (driver chart).
export const FEATURE_LABELS: Record<string, string> = {
  lat: "Latitude",
  maturity_group: "Maturity group",
  soil_awc_mm: "Soil water capacity",
  som_pct: "Soil organic matter",
  planting_doy: "Planting date",
  veg_gdd: "Vegetative GDD",
  veg_precip_mm: "Vegetative rain",
  veg_tmean_c: "Vegetative temp",
  veg_stress_water: "Vegetative water stress",
  flower_tmean_c: "Flowering temp (R1–R5)",
  flower_tmax_mean_c: "Flowering max temp",
  flower_precip_mm: "Flowering rain",
  flower_srad_mj: "Flowering sunlight",
  flower_vpd_kpa: "Flowering VPD",
  flower_days_gt30: "Flowering days >30°C",
  flower_days_gt35: "Flowering days >35°C",
  flower_stress_water: "Flowering water stress",
  flower_ptq: "Flowering photothermal",
  fill_tmean_c: "Seed-fill temp (R5–R6)",
  fill_tmin_mean_c: "Seed-fill night temp",
  fill_tmax_mean_c: "Seed-fill max temp",
  fill_night_warm: "Warm nights in fill",
  fill_precip_mm: "Seed-fill rain",
  fill_srad_mj: "Seed-fill sunlight",
  fill_vpd_kpa: "Seed-fill VPD",
  fill_days_gt30: "Seed-fill days >30°C",
  fill_days_gt35: "Seed-fill days >35°C",
  fill_stress_water: "Seed-fill water stress",
  fill_ptq: "Seed-fill photothermal",
  fill_gdd: "Seed-fill GDD",
  fill_length_days: "Seed-fill length",
  late_tmean_c: "Late-season temp",
  late_precip_mm: "Late-season rain",
  late_stress_water: "Late water stress",
  season_precip_mm: "Season rain",
  season_gdd: "Season GDD",
  aug_precip_mm: "August rain",
};
