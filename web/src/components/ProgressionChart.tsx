import {
  Area,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ProgressionPoint } from "../api";

// One measure per chart (no dual axes): yield on top, oil below —
// small multiples sharing the x axis.
function Panel({
  data,
  yKey,
  color,
  unit,
  label,
  asOf,
  domain,
}: {
  data: any[];
  yKey: string;
  color: string;
  unit: string;
  label: string;
  asOf: string;
  domain: [number | "auto", number | "auto"];
}) {
  return (
    <div>
      <div style={{ fontSize: 12, color: "var(--ink-2)", marginBottom: 2 }}>
        <span
          style={{
            display: "inline-block",
            width: 10,
            height: 10,
            borderRadius: 3,
            background: color,
            marginRight: 6,
          }}
        />
        {label}
      </div>
      <ResponsiveContainer width="100%" height={150}>
        <ComposedChart data={data} margin={{ top: 4, right: 8, left: -14, bottom: 0 }}>
          <XAxis
            dataKey="as_of"
            tick={{ fontSize: 11, fill: "var(--muted)" }}
            tickFormatter={(d: string) => d.slice(5)}
            stroke="var(--baseline)"
          />
          <YAxis
            domain={domain}
            tick={{ fontSize: 11, fill: "var(--muted)" }}
            stroke="var(--baseline)"
            width={54}
          />
          <Tooltip
            contentStyle={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              fontSize: 12,
              color: "var(--ink)",
            }}
            formatter={(v: any, name: string) => {
              if (name === "band") {
                const [lo, hi] = v as [number, number];
                return [`${lo.toFixed(1)} – ${hi.toFixed(1)} ${unit}`, "90% interval"];
              }
              return [`${Number(v).toFixed(1)} ${unit}`, label];
            }}
            labelFormatter={(d: string) => `Forecast as of ${d}`}
          />
          <Area
            dataKey="band"
            name="band"
            stroke="none"
            fill={color}
            fillOpacity={0.14}
            isAnimationActive={false}
          />
          <Line
            dataKey={yKey}
            stroke={color}
            strokeWidth={2}
            dot={{ r: 3, fill: color, strokeWidth: 0 }}
            isAnimationActive={false}
          />
          <ReferenceLine x={asOf} stroke="var(--ink)" strokeDasharray="4 3" />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function ProgressionChart({
  points,
  asOf,
}: {
  points: ProgressionPoint[];
  asOf: string;
}) {
  if (!points.length) return <p className="note">No progression available.</p>;
  const data = points.map((p) => ({
    as_of: p.as_of,
    stage: p.stage,
    y: p.yield.value,
    yband: [p.yield.lo90, p.yield.hi90],
    o: p.oil.value,
    oband: [p.oil.lo90, p.oil.hi90],
  }));
  return (
    <>
      <Panel
        data={data.map((d) => ({ ...d, band: d.yband }))}
        yKey="y"
        color="var(--s1)"
        unit="bu/ac"
        label="Yield forecast (bu/ac)"
        asOf={asOf}
        domain={["auto", "auto"]}
      />
      <Panel
        data={data.map((d) => ({ ...d, band: d.oband }))}
        yKey="o"
        color="var(--s2)"
        unit="%"
        label="Seed oil forecast (%)"
        asOf={asOf}
        domain={["auto", "auto"]}
      />
    </>
  );
}
