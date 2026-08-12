import { Forecast } from "../api";

// Single stacked composition bar with 2px surface gaps. Five fixed
// categorical slots in palette order; every segment is direct-labeled and a
// table view follows, so identity never rides on color alone.
const ACIDS: { key: string; label: string; color: string; note: string }[] = [
  { key: "linoleic_pct", label: "Linoleic", color: "var(--s1)", note: "18:2 — polyunsaturated workhorse" },
  { key: "oleic_pct", label: "Oleic", color: "var(--s2)", note: "18:1 — heat-stable, premium frying oil" },
  { key: "linolenic_pct", label: "Linolenic", color: "var(--s3)", note: "18:3 — oxidation risk; lower is better for food use" },
];

export default function FattyAcidBar({ fc }: { fc: Forecast }) {
  const p = fc.predictions;
  const parts = ACIDS.map((a) => ({ ...a, v: p[a.key]?.value ?? 0, pred: p[a.key] }));
  const other = Math.max(0, 100 - parts.reduce((s, x) => s + x.v, 0));

  return (
    <>
      <div style={{ display: "flex", height: 34, borderRadius: 8, overflow: "hidden" }}>
        {parts.map((a) => (
          <div
            key={a.key}
            title={`${a.label}: ${a.v.toFixed(1)}%`}
            style={{
              width: `${a.v}%`,
              background: a.color,
              marginRight: 2,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#fff",
              fontSize: 12,
              fontWeight: 700,
              minWidth: 0,
              whiteSpace: "nowrap",
            }}
          >
            {a.v > 12 ? `${a.label} ${a.v.toFixed(1)}%` : a.v > 6 ? `${a.v.toFixed(0)}%` : ""}
          </div>
        ))}
        <div
          title={`Saturates & other: ${other.toFixed(1)}%`}
          style={{
            width: `${other}%`,
            background: "var(--baseline)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--ink)",
            fontSize: 12,
          }}
        >
          {other > 10 ? `Sat. ${other.toFixed(0)}%` : ""}
        </div>
      </div>

      <table className="data" style={{ marginTop: 14 }}>
        <thead>
          <tr>
            <th>Fatty acid</th>
            <th>Forecast</th>
            <th>90% interval</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {parts.map((a) => (
            <tr key={a.key}>
              <td>
                <span
                  className="swatch"
                  style={{
                    background: a.color,
                    display: "inline-block",
                    width: 10,
                    height: 10,
                    borderRadius: 3,
                    marginRight: 8,
                  }}
                />
                {a.label}
              </td>
              <td>{a.v.toFixed(1)}%</td>
              <td>
                {a.pred ? `${a.pred.lo90.toFixed(1)} – ${a.pred.hi90.toFixed(1)}%` : "—"}
              </td>
              <td style={{ color: "var(--muted)", fontSize: 12 }}>{a.note}</td>
            </tr>
          ))}
          <tr>
            <td>
              <span
                className="swatch"
                style={{
                  background: "var(--baseline)",
                  display: "inline-block",
                  width: 10,
                  height: 10,
                  borderRadius: 3,
                  marginRight: 8,
                }}
              />
              Saturates &amp; other
            </td>
            <td>{other.toFixed(1)}%</td>
            <td>—</td>
            <td style={{ color: "var(--muted)", fontSize: 12 }}>
              palmitic + stearic — largely weather-invariant
            </td>
          </tr>
        </tbody>
      </table>
    </>
  );
}
