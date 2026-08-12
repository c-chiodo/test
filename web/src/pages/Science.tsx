import { useEffect, useState } from "react";
import { api, ModelMeta } from "../api";

const TARGET_LABELS: Record<string, [string, string]> = {
  yield_bu_ac: ["Yield", "bu/ac"],
  oil_pct: ["Seed oil", "pts"],
  protein_pct: ["Protein", "pts"],
  oleic_pct: ["Oleic acid", "pts"],
  linoleic_pct: ["Linoleic acid", "pts"],
  linolenic_pct: ["Linolenic acid", "pts"],
};

export default function Science() {
  const [meta, setMeta] = useState<ModelMeta | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.modelMeta().then(setMeta).catch((e) => setError(String(e)));
  }, []);

  return (
    <main className="dash container">
      <div className="dash-head">
        <h1>Science &amp; validation</h1>
      </div>

      <div className="panel">
        <h3>The agronomy we encode</h3>
        <p className="note">
          Feature engineering follows the peer-reviewed record, aligned to
          growth stage rather than calendar (full briefs with 250+ citations
          live in the repository under <code>research/</code>).
        </p>
        <div className="cards">
          <div className="card">
            <h3>Oil is made in seed fill</h3>
            <p>
              Seed oil concentration responds to R5–R6 weather, not flowering
              weather. The temperature response is dome-shaped with an optimum
              near 28&nbsp;°C — field warming below the optimum raises oil,
              heat above it costs roughly 0.4 points per degree. Drought in
              fill lowers oil and raises protein.
            </p>
          </div>
          <div className="card">
            <h3>Yield is set by water and heat</h3>
            <p>
              The critical window runs from flowering through seed fill:
              water-balance stress, days above 30/35&nbsp;°C, vapor-pressure
              deficit, August rainfall, and radiation capture carry most of
              the weather signal.
            </p>
          </div>
          <div className="card">
            <h3>Quality follows temperature</h3>
            <p>
              Warmer seed fill shifts the fatty-acid profile toward oleic and
              away from linolenic — better oxidative stability and a different
              end-use value. We forecast the profile alongside oil quantity.
            </p>
          </div>
        </div>
      </div>

      <div className="panel">
        <h3>Validation protocol</h3>
        <p className="note">
          Metrics below are computed live from the deployed model bundle —
          the same numbers the model card publishes.
        </p>
        <div className="cards" style={{ marginBottom: 18 }}>
          <div className="card">
            <h3>Leave-one-year-out</h3>
            <p>
              Every metric comes from predicting a fully held-out year —
              random splits leak same-season weather and flatter the model, so
              we don't use them.
            </p>
          </div>
          <div className="card">
            <h3>Baselines must be beaten</h3>
            <p>
              We report skill relative to a region-mean-plus-trend baseline.
              If a model can't beat "this county, usual year, plus trend," it
              has no business being sold.
            </p>
          </div>
          <div className="card">
            <h3>Conformal intervals</h3>
            <p>
              80/90% intervals are calibrated on held-out-year residuals and
              widen with unobserved season remaining. Measured coverage is
              shown next to the nominal level.
            </p>
          </div>
        </div>

        {error && <div className="err">Backend unavailable: {error}</div>}
        {meta && (
          <>
            <table className="data">
              <thead>
                <tr>
                  <th>Target</th>
                  <th>RMSE (LOYO)</th>
                  <th>R² (LOYO)</th>
                  <th>Baseline RMSE (trend)</th>
                  <th>Skill vs baseline</th>
                  <th>90% interval ±</th>
                  <th>Measured coverage</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(meta.validation).map(([t, v]) => {
                  const [label, unit] = TARGET_LABELS[t] ?? [t, ""];
                  return (
                    <tr key={t}>
                      <td>
                        <b>{label}</b>
                      </td>
                      <td>
                        {v.loyo_rmse.toFixed(2)} {unit}
                      </td>
                      <td>{v.loyo_r2.toFixed(2)}</td>
                      <td>
                        {v.baseline_trend_rmse.toFixed(2)} {unit}
                      </td>
                      <td
                        style={{
                          color:
                            v.skill_vs_trend > 0 ? "var(--good-text)" : "var(--critical)",
                          fontWeight: 600,
                        }}
                      >
                        {(v.skill_vs_trend * 100).toFixed(0)}%
                      </td>
                      <td>
                        {v.conformal_q90.toFixed(2)} {unit}
                      </td>
                      <td>{(v.coverage_check * 100).toFixed(0)}%</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="note" style={{ marginTop: 10 }}>
              Trained on {meta.n_train.toLocaleString()} field-seasons,{" "}
              {meta.years[0]}–{meta.years[1]}, {meta.regions.length} regions ·
              training data: <code>{meta.training_data}</code>
            </p>
          </>
        )}
      </div>

      <div className="panel">
        <h3>Current limitations — read before relying on this</h3>
        <p className="note">Stated plainly, because partners will find out anyway.</p>
        <ul style={{ margin: 0, paddingLeft: 20, color: "var(--ink-2)", fontSize: 14 }}>
          <li>
            <b>Synthetic training data.</b> The demo models are trained on
            simulated seasons whose yield/composition responses are generated
            from published research. They demonstrate the pipeline and learn
            the literature's structure; they have not yet been fit to USDA
            NASS county yields or USB composition surveys. Metrics above
            measure recovery of that structure, not real-world accuracy —
            production skill will be lower and will be re-published when real
            ground truth is ingested.
          </li>
          <li>
            <b>Composition ground truth is coarse.</b> Public oil/protein data
            exists at state level (US Soybean Quality Survey), not county
            level — production composition models will carry wider intervals
            than yield models.
          </li>
          <li>
            <b>No genotype yet.</b> Published weather-only oil prediction
            tops out near R² ≈ 0.4–0.5; variety information is the next
            biggest lever and slots into the same feature set.
          </li>
          <li>
            <b>Weather licensing.</b> Open-Meteo's free tier is non-commercial;
            production deployments use their commercial tier, self-hosted
            ERA5, or NASA POWER (public domain).
          </li>
        </ul>
      </div>
    </main>
  );
}
