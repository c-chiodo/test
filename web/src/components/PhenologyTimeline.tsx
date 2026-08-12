import { Forecast } from "../api";

// Stage bands: planting → each stage date. Sequential single-hue ramp
// (green family for vegetative, brand-oil family for reproductive fill).
const BANDS: { from: string; to: string; label: string; color: string }[] = [
  { from: "planting", to: "VE", label: "Sown", color: "#b7d3f6" },
  { from: "VE", to: "R1", label: "Vegetative", color: "#1e5c33" },
  { from: "R1", to: "R3", label: "Flowering", color: "#1baf7a" },
  { from: "R3", to: "R5", label: "Pod set", color: "#eda100" },
  { from: "R5", to: "R6", label: "Seed fill · oil window", color: "#d9821f" },
  { from: "R6", to: "R7", label: "Maturing", color: "#8a5a2b" },
  { from: "R7", to: "R8", label: "Drydown", color: "#6b6a64" },
];

export default function PhenologyTimeline({ fc }: { fc: Forecast }) {
  const dates: Record<string, number> = { planting: Date.parse(fc.planting_date) };
  for (const [s, d] of Object.entries(fc.stage_dates)) dates[s] = Date.parse(d);

  const bands = BANDS.filter((b) => dates[b.from] && dates[b.to]);
  if (bands.length === 0) return <p className="note">No stages predicted yet.</p>;

  const t0 = dates[bands[0].from];
  const t1 = dates[bands[bands.length - 1].to];
  const span = t1 - t0;
  const asOf = Date.parse(fc.as_of);
  const asOfPct = Math.min(100, Math.max(0, ((asOf - t0) / span) * 100));

  return (
    <>
      <div style={{ position: "relative" }}>
        <div className="pheno">
          {bands.map((b) => {
            const w = ((dates[b.to] - dates[b.from]) / span) * 100;
            return (
              <div
                key={b.label}
                className="seg"
                style={{ width: `${w}%`, background: b.color, marginRight: 2 }}
                title={`${b.label}: ${new Date(dates[b.from]).toLocaleDateString()} → ${new Date(dates[b.to]).toLocaleDateString()}`}
              >
                {w > 9 && <span>{b.label}</span>}
              </div>
            );
          })}
        </div>
        <div
          style={{
            position: "absolute",
            left: `${asOfPct}%`,
            top: -6,
            bottom: -6,
            width: 2,
            background: "var(--ink)",
            borderRadius: 1,
          }}
          title={`Forecast as of ${fc.as_of}`}
        />
      </div>
      <div className="pheno-legend">
        {["VE", "R1", "R5", "R6", "R7"].map(
          (s) =>
            fc.stage_dates[s] && (
              <span key={s}>
                <b>{s}</b> {fc.stage_dates[s].slice(5)}
              </span>
            ),
        )}
      </div>
      <div className="asof-line">
        ▎black marker = forecast date ({fc.as_of}). Stages right of it are
        projections with climate normals.
      </div>
    </>
  );
}
