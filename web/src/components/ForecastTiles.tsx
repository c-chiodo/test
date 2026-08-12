import { Forecast } from "../api";

function delta(cur: number, base: number, digits = 1) {
  const d = cur - base;
  if (Math.abs(d) < 0.5 / 10 ** digits) return null;
  return d;
}

function Tile({
  label,
  swatch,
  value,
  unit,
  lo,
  hi,
  base,
  digits = 1,
}: {
  label: string;
  swatch?: string;
  value: number;
  unit: string;
  lo?: number;
  hi?: number;
  base?: number | null;
  digits?: number;
}) {
  const d = base != null ? delta(value, base, digits) : null;
  return (
    <div className="tile">
      <div className="label">
        {swatch && <span className="swatch" style={{ background: swatch }} />}
        {label}
      </div>
      <div className="value">
        {value.toFixed(digits)} <span className="unit">{unit}</span>
      </div>
      {lo != null && hi != null && (
        <div className="range">
          90%: {lo.toFixed(digits)} – {hi.toFixed(digits)}
        </div>
      )}
      {d != null && (
        <div
          className="range"
          style={{ color: d > 0 ? "var(--good-text)" : "var(--critical)", fontWeight: 600 }}
        >
          {d > 0 ? "▲" : "▼"} {Math.abs(d).toFixed(digits)} vs normals
        </div>
      )}
    </div>
  );
}

export default function ForecastTiles({
  fc,
  baseline,
}: {
  fc: Forecast;
  baseline: Forecast | null;
}) {
  const p = fc.predictions;
  const b = baseline?.predictions;
  return (
    <div className="tiles">
      <Tile
        label="Yield"
        swatch="var(--s1)"
        value={p.yield_bu_ac.value}
        unit="bu/ac"
        lo={p.yield_bu_ac.lo90}
        hi={p.yield_bu_ac.hi90}
        base={b?.yield_bu_ac.value}
      />
      <Tile
        label="Seed oil"
        swatch="var(--s2)"
        value={p.oil_pct.value}
        unit="%"
        lo={p.oil_pct.lo90}
        hi={p.oil_pct.hi90}
        base={b?.oil_pct.value}
      />
      <Tile
        label="Protein"
        swatch="var(--s3)"
        value={p.protein_pct.value}
        unit="%"
        lo={p.protein_pct.lo90}
        hi={p.protein_pct.hi90}
        base={b?.protein_pct.value}
      />
      <Tile
        label="Oil yield"
        value={fc.oil_yield_lb_ac}
        unit="lb/ac"
        base={baseline ? baseline.oil_yield_lb_ac : null}
        digits={0}
      />
      <Tile
        label="Est. processing value"
        value={fc.crush.epv_usd_ac}
        unit="$/ac"
        base={baseline ? baseline.crush.epv_usd_ac : null}
        digits={0}
      />
    </div>
  );
}
