import { useEffect, useState } from "react";
import { api, DataSource } from "../api";

const STATUS_COLOR: Record<string, string> = {
  "connector-ready": "var(--good)",
  "connector-planned": "var(--warning)",
  "etl-planned": "var(--warning)",
};

export default function DataSources() {
  const [sources, setSources] = useState<DataSource[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.dataSources().then(setSources).catch((e) => setError(String(e)));
  }, []);

  return (
    <main className="dash container">
      <div className="dash-head">
        <h1>Data provenance</h1>
      </div>
      <div className="panel">
        <h3>Every input is public and auditable</h3>
        <p className="note">
          The platform is built exclusively on public data so partners can
          verify any number independently. Licensing posture is tracked per
          source — commercial restrictions are flagged, not buried.
        </p>
        {error && <div className="err">Backend unavailable: {error}</div>}
        <table className="data">
          <thead>
            <tr>
              <th>Source</th>
              <th>Provides</th>
              <th>Auth</th>
              <th>License / commercial posture</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => (
              <tr key={s.name}>
                <td>
                  <b>{s.name}</b>
                </td>
                <td>{s.kind}</td>
                <td>{s.auth}</td>
                <td style={{ maxWidth: 340 }}>{s.license}</td>
                <td>
                  <span className="badge">
                    <span
                      className="dot"
                      style={{ background: STATUS_COLOR[s.status] ?? "var(--s1)" }}
                    />
                    {s.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h3>Why this matters commercially</h3>
        <div className="cards">
          <div className="card">
            <h3>No data lock-in</h3>
            <p>
              Nothing proprietary sits between a partner and the forecast —
              the moat is the modeling, phenology alignment, and validation
              discipline, not a data monopoly.
            </p>
          </div>
          <div className="card">
            <h3>Provenance on every forecast</h3>
            <p>
              Each API response is stamped with the weather source used —
              live archive, live forecast, or climatology simulation — so
              downstream decisions can weight it appropriately.
            </p>
          </div>
          <div className="card">
            <h3>Graceful degradation</h3>
            <p>
              When a live source is unreachable, the platform falls back to a
              climatology-anchored simulation and says so — it never silently
              fabricates observed weather.
            </p>
          </div>
        </div>
      </div>
    </main>
  );
}
