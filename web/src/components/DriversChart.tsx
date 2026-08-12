import { useState } from "react";
import {
  Bar,
  BarChart,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { FEATURE_LABELS, Forecast } from "../api";

// Diverging encoding: positive contributions blue, negative red,
// zero line as the neutral midpoint.
export default function DriversChart({ fc }: { fc: Forecast }) {
  const [target, setTarget] = useState<"yield_bu_ac" | "oil_pct">("yield_bu_ac");
  const unit = target === "yield_bu_ac" ? "bu/ac" : "pts oil";
  const drivers = (fc.drivers[target] ?? []).slice(0, 7);
  const data = drivers
    .map((d) => ({
      name: FEATURE_LABELS[d.feature] ?? d.feature,
      shap: d.shap,
      value: d.value,
    }))
    .reverse();

  return (
    <>
      <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
        {(
          [
            ["yield_bu_ac", "Yield"],
            ["oil_pct", "Oil %"],
          ] as const
        ).map(([k, label]) => (
          <button
            key={k}
            className="btn ghost"
            style={{
              padding: "4px 12px",
              fontSize: 13,
              borderColor: target === k ? "var(--ink)" : "var(--baseline)",
              fontWeight: target === k ? 700 : 400,
            }}
            onClick={() => setTarget(k)}
          >
            {label}
          </button>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={290}>
        <BarChart data={data} layout="vertical" margin={{ top: 0, right: 16, left: 40, bottom: 0 }}>
          <XAxis
            type="number"
            tick={{ fontSize: 11, fill: "var(--muted)" }}
            stroke="var(--baseline)"
          />
          <YAxis
            type="category"
            dataKey="name"
            width={150}
            tick={{ fontSize: 12, fill: "var(--ink-2)" }}
            stroke="var(--baseline)"
          />
          <Tooltip
            contentStyle={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              fontSize: 12,
              color: "var(--ink)",
            }}
            formatter={(v: any, _n: string, item: any) => [
              `${Number(v) > 0 ? "+" : ""}${Number(v).toFixed(2)} ${unit} (feature value ${item.payload.value})`,
              "contribution",
            ]}
          />
          <ReferenceLine x={0} stroke="var(--baseline)" />
          <Bar dataKey="shap" radius={[4, 4, 4, 4]} isAnimationActive={false} barSize={16}>
            {data.map((d, i) => (
              <Cell key={i} fill={d.shap >= 0 ? "var(--pos)" : "var(--neg)"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div style={{ fontSize: 12, color: "var(--muted)" }}>
        <span style={{ color: "var(--pos)", fontWeight: 700 }}>■</span> raises
        the forecast&nbsp;&nbsp;
        <span style={{ color: "var(--neg)", fontWeight: 700 }}>■</span> lowers
        it — relative to an average training season.
      </div>
    </>
  );
}
