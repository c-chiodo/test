/* Reports: the spreadsheets the plants keep, computed from the ledger.
 *
 * DM Yields, caustic per load with the pH, and how often a posting is undone
 * — each with the workbook's definitions on the page — plus exports in the
 * legacy column layouts, so the existing workbooks keep working while they
 * are retired. */

import { useState } from 'react'
import { useApp, useToast } from '../App'
import { api } from '../lib/api'
import {
  Card, DataTable, ErrorBox, Field, Loading, Stat, Tabs, daysFromToday, fmtLbs, fmtNumber, useAsync,
} from '../components/ui'

const TABS = [
  { key: 'acid-yields', label: 'Acid yields' },
  { key: 'caustic', label: 'Caustic per load' },
  { key: 'reversals', label: 'Reversals' },
  { key: 'export', label: 'Spreadsheet exports' },
]

const LAYOUTS = [
  { key: 'yields', label: 'DM Yields', detail: 'PIMS QUERY Export, 28 columns — paste over the "PIMS QUERY Export" sheet of DM_Yields.' },
  { key: 'query', label: 'Operations workbook', detail: 'PIMS QUERY Export, 30 columns — the executive overview and caustic sheets read this.' },
  { key: 'report', label: 'Caustic, MGR & pH', detail: 'PIMS Report Export, 24 columns, with REVERSAL rows and their parents.' },
]

const pct = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? '—' : `${fmtNumber(v, digits)}%`)

export default function Reports({ initialTab }: { initialTab?: string }) {
  const { plantId, plantCode } = useApp()
  const toast = useToast()
  const [tab, setTab] = useState(TABS.some((t) => t.key === initialTab) ? initialTab! : 'acid-yields')
  const [start, setStart] = useState(daysFromToday(-27))
  const [end, setEnd] = useState(daysFromToday(0))
  const [scope, setScope] = useState<'plant' | 'all'>(tab === 'acid-yields' ? 'plant' : 'all')
  const [tfa, setTfa] = useState('26')
  const [layout, setLayout] = useState('yields')

  const payload = {
    start, end, plant_id: scope === 'plant' ? plantId : 'all',
    ...(tab === 'acid-yields' ? { tfa: Number(tfa) || 26 } : {}),
    ...(tab === 'export' ? { layout } : {}),
  }
  const result = useAsync(() => api.post<any>(`/api/reports/${tab}`, payload), [tab, start, end, scope, plantId, tfa, layout], 250)

  async function download() {
    const name = tab === 'export' ? `PIMS_${layout}_export_${start}_${end}.csv` : `${tab}_${start}_${end}.csv`
    try {
      await api.download(`/api/reports/${tab}/csv`, payload, name)
    } catch (error) {
      toast.push('error', 'Export failed', (error as Error).message)
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Reports</h1>
          <div className="sub">The spreadsheets, from the ledger — same definitions, any plant, any dates, no copy and paste.</div>
        </div>
        <div className="actions">
          <button className="primary" onClick={download} disabled={!result.data}>Download CSV</button>
        </div>
      </div>

      <Tabs tabs={TABS} active={tab} onChange={(key) => {
        // The yields workbook is one plant; the caustic and reversal sheets are all plants.
        setScope(key === 'acid-yields' ? 'plant' : 'all')
        setTab(key)
      }} />

      <Card>
        <div className="form-grid report-filters">
          <Field label="From"><input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
          <Field label="To"><input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
          <Field label="Plants">
            <select value={scope} onChange={(e) => setScope(e.target.value as 'plant' | 'all')}>
              <option value="plant">{plantCode} only</option>
              <option value="all">All my plants</option>
            </select>
          </Field>
          {tab === 'acid-yields' && (
            <Field label="TFA of the soap" hint="The workbook assumes 26%">
              <input type="number" step="0.5" value={tfa} onChange={(e) => setTfa(e.target.value)} />
            </Field>
          )}
          {tab === 'export' && (
            <Field label="Layout">
              <select value={layout} onChange={(e) => setLayout(e.target.value)}>
                {LAYOUTS.map((l) => <option key={l.key} value={l.key}>{l.label}</option>)}
              </select>
            </Field>
          )}
          <div className="quick-ranges">
            {[['Last 7 days', 6], ['4 weeks', 27], ['Quarter', 90]].map(([label, days]) => (
              <button key={label as string} className="ghost sm"
                onClick={() => { setStart(daysFromToday(-(days as number))); setEnd(daysFromToday(0)) }}>{label}</button>
            ))}
          </div>
        </div>
      </Card>

      {result.error ? <ErrorBox error={result.error} />
        : !result.data ? <Loading />
        : result.data.report !== tab ? <Loading />
        : tab === 'acid-yields' ? <Yields data={result.data} />
        : tab === 'caustic' ? <Caustic data={result.data} />
        : tab === 'reversals' ? <Reversals data={result.data} />
        : <Export data={result.data} layout={LAYOUTS.find((l) => l.key === layout)!} />}
    </>
  )
}

function Split({ parts }: { parts: { part: string; lbs: number; pct: number | null }[] }) {
  const label: Record<string, string> = { oil: "Oil to the 20's", mgr: 'MGR', water: 'Water' }
  return (
    <>
      <div className="split-bar">
        {parts.map((p) => <span key={p.part} className={`seg ${p.part}`} style={{ width: `${p.pct ?? 0}%` }} title={`${label[p.part]} ${pct(p.pct)}`} />)}
      </div>
      <table className="kv">
        <tbody>
          {parts.map((p) => (
            <tr key={p.part}><td><span className={`dot ${p.part}`} />{label[p.part]}</td>
              <td className="num">{fmtLbs(p.lbs)} lbs</td><td className="num">{pct(p.pct)}</td></tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

function Line({ rows, series }: { rows: any[]; series: { key: string; label: string; cls: string }[] }) {
  const w = 640, h = 150, pad = 28
  const values = rows.flatMap((r) => series.map((s) => r[s.key])).filter((v) => v !== null && v !== undefined) as number[]
  if (!values.length) return <div className="empty">Nothing processed in these dates.</div>
  const lo = Math.max(0, Math.floor(Math.min(...values) / 10) * 10), hi = Math.ceil(Math.max(...values, lo + 10) / 10) * 10
  const x = (i: number) => pad + (i * (w - pad * 2)) / Math.max(rows.length - 1, 1)
  const y = (v: number) => h - pad + ((lo - v) * (h - pad * 1.6)) / (hi - lo || 1)
  return (
    <svg className="line-chart" viewBox={`0 0 ${w} ${h}`} role="img" aria-label="Seven-day yields by day">
      {[lo, (lo + hi) / 2, hi].map((g) => (
        <g key={g}><line x1={pad} x2={w - pad} y1={y(g)} y2={y(g)} className="grid-line" />
          <text x={4} y={y(g) + 4} className="axis">{g}%</text></g>
      ))}
      {series.map((s) => {
        const pts = rows.map((r, i) => (r[s.key] === null || r[s.key] === undefined ? null : `${x(i)},${y(r[s.key])}`)).filter(Boolean)
        return <polyline key={s.key} points={pts.join(' ')} className={`series ${s.cls}`} />
      })}
      <text x={pad} y={h - 6} className="axis">{rows[0]?.date?.slice(5)}</text>
      <text x={w - pad} y={h - 6} className="axis" textAnchor="end">{rows[rows.length - 1]?.date?.slice(5)}</text>
    </svg>
  )
}

function Definitions({ items }: { items: { name: string; definition: string }[] }) {
  return (
    <details className="definitions">
      <summary>How each number is worked out</summary>
      <dl>{items.map((d) => <div key={d.name}><dt>{d.name}</dt><dd>{d.definition}</dd></div>)}</dl>
    </details>
  )
}

function Yields({ data }: { data: any }) {
  const y = data.yields, i = data.inputs, o = data.oil
  return (
    <>
      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <Stat label="First-pass yield" value={pct(y.fpy)} foot={`settle oil ÷ soap × ${data.tfa}% TFA`} />
        <Stat label="Second-pass yield" value={pct(y.spy)} foot={`${pct(o.bottoms_pct)} of 20's oil back as bottoms`} />
        <Stat label="Overall yield" value={pct(y.oy)} foot={`${pct(y.oy_less_water)} leaving reprocessed water out`} />
        <Stat label="Acid per lb of soap" value={pct(i.acid_pct, 2)} foot={`${fmtLbs(i.acid_used)} lbs acid`} />
      </div>
      <div className="grid cols-3" style={{ marginBottom: 16 }}>
        <Card title="Soap in">
          <table className="kv"><tbody>
            <tr><td>Soap received</td><td className="num">{fmtLbs(i.soap_received)} lbs</td></tr>
            <tr><td>Soap processed</td><td className="num">{fmtLbs(i.soap_processed)} lbs</td></tr>
            <tr><td>Acid used</td><td className="num">{fmtLbs(i.acid_used)} lbs</td></tr>
            <tr><td>Steam (estimated)</td><td className="num">{fmtLbs(i.steam)} lbs</td></tr>
            <tr><td>Reprocessed water</td><td className="num">{fmtLbs(i.reprocessed_water)} lbs</td></tr>
            <tr><td>Theoretical oil</td><td className="num">{fmtLbs(y.theoretical_oil)} lbs</td></tr>
          </tbody></table>
        </Card>
        <Card title="Settle breaks" subtitle={`${fmtLbs(data.settle_total)} lbs drawn off`}>
          <Split parts={data.settle_break} />
        </Card>
        <Card title="MGR breaks" subtitle={`${fmtLbs(data.mgr_processed)} lbs processed · ${fmtLbs(data.mgr_total)} lbs drawn off`}>
          <Split parts={data.mgr_break} />
        </Card>
      </div>
      <div className="grid cols-2" style={{ marginBottom: 16 }}>
        <Card title="20's oil">
          <table className="kv"><tbody>
            <tr><td>Total 20's oil</td><td className="num">{fmtLbs(o.total_20s)} lbs</td></tr>
            <tr><td>from settles</td><td className="num">{fmtLbs(o.settle_oil)} lbs</td></tr>
            <tr><td>from MGR (MGRV oil)</td><td className="num">{fmtLbs(o.mgrv_oil)} lbs</td></tr>
            {o.mgra_oil > 0 && <tr><td>from MGR animal</td><td className="num">{fmtLbs(o.mgra_oil)} lbs</td></tr>}
            <tr><td>20's bottoms back to MGR</td><td className="num">{fmtLbs(o.bottoms_20s)} lbs</td></tr>
            <tr><td><strong>Oil final</strong> (made into product)</td><td className="num"><strong>{fmtLbs(o.oil_final)} lbs</strong></td></tr>
          </tbody></table>
        </Card>
        <Card title="Outbound">
          <table className="kv"><tbody>
            <tr><td>MGRV blended onto trailers</td><td className="num">{fmtLbs(data.outbound.mgrv)} lbs</td></tr>
            <tr><td>MGRA blended onto trailers</td><td className="num">{fmtLbs(data.outbound.mgra)} lbs</td></tr>
            <tr><td>Process water in blends</td><td className="num">{fmtLbs(data.outbound.water)} lbs</td></tr>
            <tr><td>Caustic in blends</td><td className="num">{fmtLbs(data.outbound.caustic)} lbs</td></tr>
            <tr><td>Process water shipped</td><td className="num">{fmtLbs(data.outbound_water.lbs)} lbs · ≈{fmtNumber(data.outbound_water.trailers_est, 1)} trailers</td></tr>
          </tbody></table>
        </Card>
      </div>
      <Card title="Day by day" subtitle="Seven-day rolling yields, as the Summary sheet's 7-day values"
        actions={<span className="legend"><span className="dot fpy" />FPY <span className="dot oy" />OY</span>}>
        <Line rows={data.daily} series={[{ key: 'fpy_7d', label: 'FPY', cls: 'fpy' }, { key: 'oy_7d', label: 'OY', cls: 'oy' }]} />
        <DataTable
          maxHeight="320px"
          rows={[...data.daily].reverse()}
          columns={[
            { key: 'date', label: 'Date' },
            { key: 'soap_processed', label: 'Soap processed', numeric: true },
            { key: 'acid_used', label: 'Acid', numeric: true },
            { key: 'oil_fp', label: 'Settle oil', numeric: true },
            { key: 'oil_final', label: 'Oil final', numeric: true },
            { key: 'fpy_7d', label: 'FPY 7-day', numeric: true, render: (r) => pct(r.fpy_7d) },
            { key: 'oy_7d', label: 'OY 7-day', numeric: true, render: (r) => pct(r.oy_7d) },
          ]}
        />
      </Card>
      <Definitions items={data.definitions} />
    </>
  )
}

function Caustic({ data }: { data: any }) {
  const t = data.total
  const summaryCols = [
    { key: 'loads', label: 'Loads', numeric: true },
    { key: 'gross', label: 'Gross lbs', numeric: true },
    { key: 'reversed', label: 'Reversed lbs', numeric: true },
    { key: 'net', label: 'Net lbs', numeric: true },
    { key: 'reversal_pct', label: 'Reversed %', numeric: true, render: (r: any) => pct(r.reversal_pct) },
    { key: 'avg_per_load', label: 'Per load', numeric: true },
    { key: 'avg_ph', label: 'Avg pH', numeric: true },
    { key: 'not_tested', label: 'Not tested', numeric: true },
  ]
  return (
    <>
      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <Stat label="Net caustic" value={`${fmtLbs(t.net)} lbs`} foot={`${fmtNumber(t.loads)} loads · ${fmtLbs(t.avg_per_load)} lbs a load`} />
        <Stat label="Reversed" value={pct(t.reversal_pct)} foot={`${fmtLbs(t.reversed)} of ${fmtLbs(t.gross)} lbs posted`}
          tone={t.reversal_pct > 10 ? 'warn' : undefined} />
        <Stat label="Average pH" value={t.avg_ph ?? '—'} foot="loads that were tested" />
        <Stat label="Not tested" value={fmtNumber(t.not_tested)} foot="loads with no pH on record" tone={t.not_tested ? 'warn' : undefined} />
      </div>
      <div className="grid cols-2" style={{ marginBottom: 16 }}>
        <Card title="By plant" tight><DataTable rows={data.by_plant} columns={[{ key: 'plant', label: 'Plant' }, ...summaryCols]} /></Card>
        <Card title="By month" tight><DataTable rows={data.by_month} columns={[{ key: 'month', label: 'Month' }, { key: 'plant', label: 'Plant' }, ...summaryCols.slice(0, 1), summaryCols[3], summaryCols[5], summaryCols[6]]} /></Card>
      </div>
      <Card title="By product" tight>
        <DataTable rows={data.by_product} columns={[{ key: 'plant', label: 'Plant' }, { key: 'product', label: 'Product' }, ...summaryCols]} />
      </Card>
      <Card title="Every load" subtitle="One row per order, as the Load Caustic Detail sheet" tight>
        <DataTable
          maxHeight="420px"
          rows={data.loads}
          columns={[
            { key: 'day', label: 'Date' },
            { key: 'plant', label: 'Plant' },
            { key: 'product', label: 'Product' },
            { key: 'order_id', label: 'Order' },
            { key: 'ph_readings', label: 'pH', render: (r: any) => (r.ph_readings.length ? r.ph_readings.join(', ') : <span className="muted">not tested</span>) },
            { key: 'gross', label: 'Gross', numeric: true },
            { key: 'reversed', label: 'Reversed', numeric: true },
            { key: 'net', label: 'Net', numeric: true },
            { key: 'status', label: 'Status', render: (r: any) => (r.status === 'No reversal' ? <span className="muted">{r.status}</span> : <span className="badge warn">{r.status}</span>) },
          ]}
        />
      </Card>
      <Definitions items={data.definitions} />
    </>
  )
}

function Reversals({ data }: { data: any }) {
  const t = data.total
  const cols = [
    { key: 'postings', label: 'Postings', numeric: true },
    { key: 'reversed', label: 'Reversed', numeric: true },
    { key: 'rate_pct', label: 'Rate', numeric: true, render: (r: any) => pct(r.rate_pct) },
    { key: 'lbs_reversed', label: 'Lbs reversed', numeric: true },
  ]
  return (
    <>
      <div className="grid cols-3" style={{ marginBottom: 16 }}>
        <Stat label="Postings reversed" value={pct(t.rate_pct, 2)} foot={`${fmtNumber(t.reversed)} of ${fmtNumber(t.postings)}`} />
        <Stat label="Pounds reversed" value={`${fmtLbs(t.lbs_reversed)} lbs`} />
        <Stat label="Worst" value={data.by_type.filter((r: any) => r.reversed).sort((a: any, b: any) => b.rate_pct - a.rate_pct)[0]?.type ?? '—'}
          foot={(() => { const w = data.by_type.filter((r: any) => r.reversed).sort((a: any, b: any) => b.rate_pct - a.rate_pct)[0]; return w ? `${w.plant} · ${pct(w.rate_pct)}` : 'nothing reversed' })()} />
      </div>
      <div className="grid cols-2" style={{ marginBottom: 16 }}>
        <Card title="By plant and transaction" tight>
          <DataTable rows={data.by_type} columns={[{ key: 'plant', label: 'Plant' }, { key: 'type', label: 'Type' }, ...cols]} />
        </Card>
        <Card title="Week by week" tight>
          <DataTable rows={data.by_week} columns={[{ key: 'week', label: 'Week of' }, ...cols]} />
        </Card>
      </div>
      <Card title="What gets reversed most" tight>
        <DataTable rows={data.top_materials} empty="Nothing was reversed in these dates."
          columns={[{ key: 'plant', label: 'Plant' }, { key: 'type', label: 'Type' }, { key: 'material', label: 'Material' },
            { key: 'reversed', label: 'Times', numeric: true }, { key: 'lbs_reversed', label: 'Lbs', numeric: true }]} />
      </Card>
    </>
  )
}

function Export({ data, layout }: { data: any; layout: { label: string; detail: string } }) {
  return (
    <Card title={`${layout.label} — ${fmtNumber(data.rows.length)} rows`} subtitle={layout.detail} tight>
      <DataTable
        maxHeight="480px"
        rows={data.rows.slice(0, 200)}
        columns={data.columns.map((c: string) => ({ key: c, label: c }))}
        empty="No transactions in these dates."
      />
      {data.rows.length > 200 && <div className="muted small" style={{ padding: 10 }}>Showing the first 200. Download for all {fmtNumber(data.rows.length)}.</div>}
    </Card>
  )
}
