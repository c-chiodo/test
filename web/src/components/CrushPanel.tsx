import { Forecast } from "../api";

export default function CrushPanel({ fc }: { fc: Forecast }) {
  const c = fc.crush;
  return (
    <div className="panel">
      <h3>Crush economics</h3>
      <p className="note">
        Standard crush arithmetic applied to the forecast composition —
        default price deck (oil $0.47/lb · meal $340/ton · beans $10.60/bu),
        configurable via the API for your own marks.
      </p>
      <div className="tiles" style={{ marginBottom: 0 }}>
        <div className="tile">
          <div className="label">Oil recovered</div>
          <div className="value">
            {c.oil_lb_per_bu.toFixed(1)} <span className="unit">lb/bu</span>
          </div>
          <div className="range">{c.oil_lb_per_ac.toFixed(0)} lb/ac</div>
        </div>
        <div className="tile">
          <div className="label">Meal produced</div>
          <div className="value">
            {c.meal_lb_per_bu.toFixed(1)} <span className="unit">lb/bu</span>
          </div>
          <div className="range">{c.meal_protein_pct.toFixed(1)}% protein basis</div>
        </div>
        <div className="tile">
          <div className="label">Oil value</div>
          <div className="value">
            ${c.oil_value_usd_bu.toFixed(2)} <span className="unit">/bu</span>
          </div>
          <div className="range">
            quality adj {c.oil_quality_adj_usd_lb >= 0 ? "+" : ""}
            {c.oil_quality_adj_usd_lb.toFixed(3)} $/lb
          </div>
        </div>
        <div className="tile">
          <div className="label">Est. processing value</div>
          <div className="value">
            ${c.epv_usd_bu.toFixed(2)} <span className="unit">/bu</span>
          </div>
          <div className="range">${c.epv_usd_ac.toFixed(0)} /ac</div>
        </div>
        <div className="tile">
          <div className="label">Gross crush margin</div>
          <div
            className="value"
            style={{
              color: c.gross_margin_usd_bu >= 0 ? "var(--good-text)" : "var(--critical)",
            }}
          >
            ${c.gross_margin_usd_bu.toFixed(2)} <span className="unit">/bu</span>
          </div>
          <div className="range">vs bean cost $10.60/bu</div>
        </div>
      </div>
    </div>
  );
}
