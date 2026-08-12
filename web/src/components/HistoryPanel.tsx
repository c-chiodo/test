import { useState } from "react";
import {
  Bar,
  ComposedChart,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Forecast, History } from "../api";

// One measure per chart. Historical years in the series hue; the current
// forecast is a dashed reference line; the 10-yr mean a solid gray line.
const METRICS = [
  { key: "yield_bu_ac", label: "Yield", unit: "bu/ac", color: "var(--s1)", digits: 1 },
  { key: "oil_pct", label: "Seed oil", unit: "%", color: "var(--s2)", digits: 2 },
  { key: "oil_yield_lb_ac", label: "Oil yield", unit: "lb/ac", color: "var(--s2)", digits: 0 },
  { key: "epv_usd_ac", label: "Processing value", unit: "$/ac", color: "var(--s3)", digits: 0 },
] as const;

type MetricKey = (typeof METRICS)[number]["key"];

const CURRENT: Record<MetricKey, (fc: Forecast) => number> = {
  yield_bu_ac: (fc) => fc.predictions.yield_bu_ac.value,
  oil_pct: (fc) => fc.predictions.oil_pct.value,
  oil_yield_lb_ac: (fc) => fc.oil_yield_lb_ac,
  epv_usd_ac: (fc) => fc.crush.epv_usd_ac,
};

export default function HistoryPanel({
  history,
  fc,
}: {
  history: History;
  fc: Forecast;
}) {
  const [metricKey, setMetricKey] = useState<MetricKey>("yield_bu_ac");
  const metric = METRICS.find((m) => m.key === metricKey)!;
  const stats = history.stats[metric.key];
  const current = CURRENT[metric.key](fc);
  const delta = current - stats.mean;
  const deltaPct = (delta / stats.mean) * 100;

  const data = history.years.map((y) => ({
    year: String(y.year),
    v: y[metric.key] as number,
  }));

  // Bars encode length — the axis must start at zero or differences lie.
  const hi = Math.max(stats.max, current);
  const pad = hi * 0.08 || 1;

  return (
    <div className="panel">
      <h3>This season vs. history</h3>
      <p className="note">
        Retrospective model outcomes for the last {history.years.length}{" "}
        seasons in this region, against the current {fc.year} forecast.
      </p>

      <div style={{ display: "flex", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
        {METRICS.map((m) => (
          <button
            key={m.key}
            className="btn ghost"
            style={{
              padding: "4px 12px",
              fontSize: 13,
              borderColor: metricKey === m.key ? "var(--ink)" : "var(--baseline)",
              fontWeight: metricKey === m.key ? 700 : 400,
            }}
            onClick={() => setMetricKey(m.key)}
          >
            {m.label}
          </button>
        ))}
      </div>

      <div className="tiles" style={{ marginBottom: 8 }}>
        <div className="tile">
          <div className="label">{fc.year} forecast</div>
          <div className="value">
            {current.toFixed(metric.digits)} <span className="unit">{metric.unit}</span>
          </div>
        </div>
        <div className="tile">
          <div className="label">{history.years.length}-yr average</div>
          <div className="value">
            {stats.mean.toFixed(metric.digits)} <span className="unit">{metric.unit}</span>
          </div>
          <div className="range">
            range {stats.min.toFixed(metric.digits)} – {stats.max.toFixed(metric.digits)}
          </div>
        </div>
        <div className="tile">
          <div className="label">vs. average</div>
          <div
            className="value"
            style={{ color: delta >= 0 ? "var(--good-text)" : "var(--critical)" }}
          >
            {delta >= 0 ? "+" : ""}
            {delta.toFixed(metric.digits)}{" "}
            <span className="unit">
              {metric.unit} ({deltaPct >= 0 ? "+" : ""}
              {deltaPct.toFixed(1)}%)
            </span>
          </div>
        </div>
      </div>

      <ResponsiveContainer width="100%" height={210}>
        <ComposedChart data={data} margin={{ top: 8, right: 8, left: -10, bottom: 0 }}>
          <XAxis
            dataKey="year"
            tick={{ fontSize: 11, fill: "var(--muted)" }}
            stroke="var(--baseline)"
          />
          <YAxis
            domain={[0, Math.ceil(hi + pad)]}
            tick={{ fontSize: 11, fill: "var(--muted)" }}
            stroke="var(--baseline)"
            width={52}
          />
          <Tooltip
            contentStyle={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              fontSize: 12,
              color: "var(--ink)",
            }}
            formatter={(v: any) => [
              `${Number(v).toFixed(metric.digits)} ${metric.unit}`,
              metric.label,
            ]}
          />
          <Bar dataKey="v" radius={[4, 4, 0, 0]} isAnimationActive={false} barSize={26}>
            {data.map((d, i) => (
              <Cell key={i} fill={metric.color} fillOpacity={0.85} />
            ))}
          </Bar>
          <ReferenceLine
            y={stats.mean}
            stroke="var(--muted)"
            strokeWidth={1.5}
            label={{
              value: "avg",
              position: "insideTopRight",
              fontSize: 11,
              fill: "var(--muted)",
            }}
          />
          <ReferenceLine
            y={current}
            stroke="var(--ink)"
            strokeDasharray="5 4"
            strokeWidth={1.5}
            label={{
              value: `${fc.year} forecast`,
              position: "insideBottomRight",
              fontSize: 11,
              fill: "var(--ink)",
            }}
          />
        </ComposedChart>
      </ResponsiveContainer>
      <div style={{ fontSize: 12, color: "var(--muted)" }}>
        Bars = completed seasons (retrospective model estimate) · dashed line ={" "}
        {fc.year} forecast · gray line = historical average.
      </div>
    </div>
  );
}
