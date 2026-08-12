import { useCallback, useEffect, useMemo, useState } from "react";
import { api, Forecast, History, ProgressionPoint, Region } from "../api";
import HistoryPanel from "../components/HistoryPanel";
import ForecastTiles from "../components/ForecastTiles";
import PhenologyTimeline from "../components/PhenologyTimeline";
import ProgressionChart from "../components/ProgressionChart";
import DriversChart from "../components/DriversChart";
import FattyAcidBar from "../components/FattyAcidBar";
import CrushPanel from "../components/CrushPanel";
import WeatherStrip from "../components/WeatherStrip";

const YEARS = [2022, 2023, 2024, 2025];

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export default function Dashboard() {
  const [regions, setRegions] = useState<Region[]>([]);
  const [regionId, setRegionId] = useState("story-ia");
  const [year, setYear] = useState(2025);
  const [asOf, setAsOf] = useState("2025-08-12");
  const [tempDelta, setTempDelta] = useState(0);
  const [precipDelta, setPrecipDelta] = useState(0);

  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [baseline, setBaseline] = useState<Forecast | null>(null);
  const [progression, setProgression] = useState<ProgressionPoint[]>([]);
  const [history, setHistory] = useState<History | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const scenario = useDebounced({ tempDelta, precipDelta }, 350);
  const isScenario = scenario.tempDelta !== 0 || scenario.precipDelta !== 0;

  useEffect(() => {
    api.regions().then(setRegions).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    // Keep as-of inside the selected year.
    setAsOf((prev) => {
      const d = prev.slice(5);
      return `${year}-${d < "04-01" || d > "11-15" ? "08-12" : d}`;
    });
  }, [year]);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    const base = { region_id: regionId, year, as_of: asOf, include_weather: true };
    Promise.all([
      api.forecast({
        ...base,
        scenario_temp_c: scenario.tempDelta,
        scenario_precip_pct: scenario.precipDelta,
      }),
      isScenario ? api.forecast(base) : Promise.resolve(null),
      api.progression(regionId, year),
      api.history(regionId, year),
    ])
      .then(([fc, bl, prog, hist]) => {
        setForecast(fc);
        setBaseline(bl ?? fc);
        setProgression(prog);
        setHistory(hist);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [regionId, year, asOf, scenario, isScenario]);

  useEffect(load, [load]);

  const region = useMemo(
    () => regions.find((r) => r.region_id === regionId),
    [regions, regionId],
  );

  return (
    <main className="dash container">
      <div className="dash-head">
        <div>
          <h1>Season forecast</h1>
          {forecast && (
            <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
              <span className="badge">
                <span className="dot" style={{ background: "var(--good)" }} />
                Stage: <b>{forecast.current_stage}</b>
              </span>
              <span className="badge">
                Season observed: <b>{Math.round(forecast.season_coverage * 100)}%</b>
              </span>
              <span className="badge">
                Weather: <b>{forecast.weather_source}</b>
              </span>
              <span className="badge">
                Planted: <b>{forecast.planting_date}</b> · MG{" "}
                <b>{forecast.maturity_group}</b>
              </span>
            </div>
          )}
        </div>
        <div className="controls">
          <label className="field">
            Region
            <select value={regionId} onChange={(e) => setRegionId(e.target.value)}>
              {regions.map((r) => (
                <option key={r.region_id} value={r.region_id}>
                  {r.name}, {r.state}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Season
            <select value={year} onChange={(e) => setYear(Number(e.target.value))}>
              {YEARS.map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Forecast as of
            <input
              type="date"
              value={asOf}
              min={`${year}-05-01`}
              max={`${year}-11-15`}
              onChange={(e) => setAsOf(e.target.value)}
            />
          </label>
        </div>
      </div>

      {error && <div className="err">Backend unavailable: {error}</div>}
      {!error && loading && !forecast && (
        <div className="loading">Running models…</div>
      )}

      {forecast && (
        <div style={{ opacity: loading ? 0.55 : 1, transition: "opacity 120ms" }}>
          <ForecastTiles fc={forecast} baseline={isScenario ? baseline : null} />

          <div className="panel">
            <h3>Growth stages · {region?.name}</h3>
            <p className="note">
              Predicted from accumulated growing degree days and photoperiod;
              stages after the as-of date are projected with climate normals.
            </p>
            <PhenologyTimeline fc={forecast} />
          </div>

          <div className="grid2">
            <div className="panel">
              <h3>Forecast evolution through the season</h3>
              <p className="note">
                How the {year} prediction (line) and its 90% interval (band)
                changed as observed weather replaced climatology.
              </p>
              <ProgressionChart points={progression} asOf={forecast.as_of} />
            </div>
            <div className="panel">
              <h3>What's driving this forecast</h3>
              <p className="note">
                SHAP attribution from the gradient-boosted model — bars push
                the prediction up (blue) or down (red) vs. an average season.
              </p>
              <DriversChart fc={forecast} />
            </div>
          </div>

          <div className="grid2">
            <div className="panel">
              <h3>Scenario: rest of season</h3>
              <p className="note">
                Shift the unobserved remainder of the season and watch yield,
                composition, and value respond. Baseline = climate normals.
              </p>
              <div className="slider-row">
                <label htmlFor="temp-slider">Temperature shift</label>
                <input
                  id="temp-slider"
                  type="range"
                  min={-4}
                  max={6}
                  step={0.5}
                  value={tempDelta}
                  onChange={(e) => setTempDelta(Number(e.target.value))}
                />
                <span className="val">
                  {tempDelta > 0 ? "+" : ""}
                  {tempDelta.toFixed(1)} °C
                </span>
              </div>
              <div className="slider-row">
                <label htmlFor="precip-slider">Precipitation shift</label>
                <input
                  id="precip-slider"
                  type="range"
                  min={-60}
                  max={60}
                  step={5}
                  value={precipDelta}
                  onChange={(e) => setPrecipDelta(Number(e.target.value))}
                />
                <span className="val">
                  {precipDelta > 0 ? "+" : ""}
                  {precipDelta}%
                </span>
              </div>
              {(tempDelta !== 0 || precipDelta !== 0) && (
                <button
                  className="btn ghost"
                  style={{ marginTop: 6 }}
                  onClick={() => {
                    setTempDelta(0);
                    setPrecipDelta(0);
                  }}
                >
                  Reset to normals
                </button>
              )}
              <WeatherStrip fc={forecast} />
            </div>
            <div className="panel">
              <h3>Oil quality — fatty acid profile</h3>
              <p className="note">
                Warmer, drier seed fill shifts oil toward oleic and away from
                linolenic — changing oxidative stability and end-use value.
              </p>
              <FattyAcidBar fc={forecast} />
            </div>
          </div>

          {history && <HistoryPanel history={history} fc={forecast} />}

          <CrushPanel fc={forecast} />

          <div className="disclaimer">
            Demo models are trained on synthetic seasons generated from
            published crop-response research, pending ingestion of USDA NASS
            ground truth — treat outputs as illustrative of the product, not
            as trading or agronomic advice. Weather source for this run:{" "}
            {forecast.weather_source}. Validation methodology and current
            metrics: see Science &amp; Validation.
          </div>
        </div>
      )}
    </main>
  );
}
