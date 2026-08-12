import {
  Bar,
  ComposedChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useMemo } from "react";
import { Forecast } from "../api";

// Weekly precipitation for the season: observed weeks solid, projected weeks
// hatched-light (single sequential hue — magnitude, one measure, one axis).
export default function WeatherStrip({ fc }: { fc: Forecast }) {
  const weekly = useMemo(() => {
    const out: { week: string; precip: number; observed: boolean }[] = [];
    let acc = 0;
    let obs = true;
    let label = "";
    fc.weather_daily.forEach((d, i) => {
      if (i % 7 === 0) {
        if (label) out.push({ week: label, precip: +acc.toFixed(1), observed: obs });
        acc = 0;
        obs = true;
        label = d.date.slice(5);
      }
      acc += d.precip_mm;
      obs = obs && d.observed;
    });
    if (label) out.push({ week: label, precip: +acc.toFixed(1), observed: obs });
    return out;
  }, [fc.weather_daily]);

  if (!weekly.length) return null;
  const firstProjected = weekly.find((w) => !w.observed)?.week;

  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ fontSize: 12, color: "var(--ink-2)", marginBottom: 2 }}>
        Weekly rainfall (mm) — solid = observed, faded = scenario/normals
      </div>
      <ResponsiveContainer width="100%" height={120}>
        <ComposedChart data={weekly} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
          <XAxis
            dataKey="week"
            tick={{ fontSize: 10, fill: "var(--muted)" }}
            interval={2}
            stroke="var(--baseline)"
          />
          <YAxis tick={{ fontSize: 10, fill: "var(--muted)" }} stroke="var(--baseline)" />
          <Tooltip
            contentStyle={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              fontSize: 12,
              color: "var(--ink)",
            }}
            formatter={(v: any, _n, item: any) => [
              `${v} mm ${item.payload.observed ? "(observed)" : "(projected)"}`,
              "rain",
            ]}
            labelFormatter={(w: string) => `Week of ${w}`}
          />
          <Bar
            dataKey="precip"
            radius={[3, 3, 0, 0]}
            isAnimationActive={false}
            fill="var(--s1)"
            // per-cell opacity via fillOpacity keyed on observed
            shape={(props: any) => {
              const { x, y, width, height, payload } = props;
              return (
                <rect
                  x={x}
                  y={y}
                  width={width}
                  height={height}
                  rx={3}
                  fill="var(--s1)"
                  fillOpacity={payload.observed ? 0.95 : 0.35}
                />
              );
            }}
          />
          {firstProjected && (
            <ReferenceLine x={firstProjected} stroke="var(--ink)" strokeDasharray="4 3" />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
