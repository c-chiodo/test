/* Staged batches — acidulation — on the floor.
 *
 * Blending is one button because a blend is done the moment it is mixed.
 * Acidulation is not: soap comes off trucks and railcars into the reactor,
 * acid goes in on top, it cooks and mixes, and then it sits and settles for
 * hours before the acidulated soapstock can be drawn off. So the acid screen
 * is built around the reactors, and each batch is one page that reads down
 * in the order it happens, with the clock showing how long it has been at
 * its current stage:
 *
 *   ① Soap in   ② Acid in   ③ Mix   ④ Settle   ⑤ Draw off
 *
 * The batch lives on the server. The operator who charges the reactor at
 * 5 a.m. and the one who draws it off after lunch open the same page, from
 * any terminal, and see what the other one did. */

import { useEffect, useMemo, useState } from 'react'
import { useApp, useToast } from '../App'
import { api, newKey, qs } from '../lib/api'
import type { Order, ProcessBatch } from '../lib/types'
import { Alert, Badge, Card, ErrorBox, Loading, fmtDate, fmtDateTime, fmtLbs, useAsync } from '../components/ui'
import { FlowPart } from './LoadAndShip'
import { Qty, Step, TankPicker, useTanks } from './Operations'

/** "3 h 05 min", "25 min", "just now". */
export function clock(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return ''
  if (minutes < 1) return 'less than a minute'
  const h = Math.floor(minutes / 60)
  const m = Math.round(minutes % 60)
  return h ? `${h} h ${String(m).padStart(2, '0')} min` : `${m} min`
}

/** Minutes since an ISO time, ticking once a minute. */
function useMinutesSince(iso: string | null | undefined): number | null {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000)
    return () => window.clearInterval(timer)
  }, [])
  if (!iso) return null
  return Math.max(0, (now - new Date(iso).getTime()) / 60_000)
}

/* -------------------------------------------------------------- overview */

export function ProcessOverview({ departmentId, orderId }: { departmentId: number; orderId?: number }) {
  const { plantId, plantCode, navigate, departments, can, companion } = useApp()
  const toast = useToast()
  const department = departments.find((d) => d.department_id === departmentId)
  const name = department?.description ?? 'Batches'
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<any>(null)
  const canAct = can('txn.post') && !companion

  const vessels = useAsync(
    () => api.get<any[]>(`/api/process/vessels${qs({ plant_id: plantId, department_id: departmentId })}`),
    [plantId, departmentId],
  )
  const work = useAsync(
    () => api.get<{ rows: Order[] }>(`/api/orders${qs({ plant_id: plantId, order_type_id: 2, open_only: true, limit: 200 })}`),
    [plantId],
  )
  const recipes = useAsync(() => api.get<any[]>('/api/blend/recipes'), [])
  const mine = new Set((recipes.data ?? []).filter((r) => r.department_id === departmentId).map((r) => r.material_id))
  const running = new Set((vessels.data ?? []).map((v) => v.batch?.order_id).filter(Boolean))
  const waiting = (work.data?.rows ?? [])
    .filter((o) => mine.has(o.material_one_id) && o.material_one_quantity - o.qty_fulfilled > 0.5 && !running.has(o.order_id))
    .sort((a, b) => a.due_date.localeCompare(b.due_date))
  const free = (vessels.data ?? []).filter((v) => !v.batch)

  async function start(order: Order, vesselId?: number) {
    setBusy(true); setError(null)
    try {
      const batch = await api.post<ProcessBatch>('/api/process/start', { order_id: order.order_id, vessel_id: vesselId })
      toast.push('success', `Batch ${batch.batch_id} started`, `${batch.vessel?.number} · work order ${order.order_id}`)
      navigate(`process/${batch.batch_id}`)
    } catch (err) { setError(err) } finally { setBusy(false) }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{name} — {plantCode}</h1>
          <div className="sub">The reactors, and what is in each. Open a batch to carry on with it, or start one in a free reactor.</div>
        </div>
        <div className="actions"><button onClick={() => navigate('today')}>Back to Today</button></div>
      </div>
      {error && <div style={{ marginBottom: 12 }}><ErrorBox error={error} /></div>}

      <section className="task-group">
        <h2>Reactors</h2>
        {vessels.loading ? <Loading /> : vessels.error ? <ErrorBox error={vessels.error} /> : (vessels.data ?? []).length === 0 ? (
          <Alert tone="info" title="No reactor at this plant">
            Batches run in a location whose type is the recipe's vessel. Ask an administrator to add one.
          </Alert>
        ) : (
          <div className="reactors">
            {(vessels.data ?? []).map((v) => <Reactor key={v.location_id} vessel={v} />)}
          </div>
        )}
      </section>

      <section className="task-group">
        <h2>Work orders waiting</h2>
        {work.loading || recipes.loading ? <Loading /> : waiting.length === 0 ? (
          <div className="lane-empty today-empty">Nothing waiting — every {name.toLowerCase()} work order is running or done.</div>
        ) : (
          <div className="choice-list">
            {waiting.map((o) => (
              <div key={o.order_id} className={`choice${orderId === o.order_id ? ' selected' : ''}`}>
                <strong>{o.material_one_number} · {o.material_one_description}</strong>
                <span className="muted">{fmtLbs(o.material_one_quantity - o.qty_fulfilled)} lbs to make · WO {o.order_id} · due {fmtDate(o.due_date)}</span>
                {canAct && (free.length ? (
                  <div className="row" style={{ gap: 6, marginTop: 8 }}>
                    {free.map((v) => (
                      <button key={v.location_id} className="primary sm" disabled={busy} onClick={() => start(o, v.location_id)}>
                        Start in {v.number}
                      </button>
                    ))}
                  </div>
                ) : (
                  <span className="tank-flag" style={{ marginTop: 6 }}>every reactor is busy — draw one off first</span>
                ))}
              </div>
            ))}
          </div>
        )}
      </section>
    </>
  )
}

function Reactor({ vessel }: { vessel: any }) {
  const { navigate } = useApp()
  const batch: ProcessBatch | null = vessel.batch
  const minutes = useMinutesSince(batch?.stage_since)
  const pct = vessel.max_capacity ? Math.min(100, (vessel.total / vessel.max_capacity) * 100) : 0
  return (
    <button className={`reactor${batch ? ' busy' : ''}`} onClick={() => batch && navigate(`process/${batch.batch_id}`)} disabled={!batch}>
      <span className="mini-gauge tall"><span style={{ height: `${pct}%` }} /></span>
      <span className="reactor-text">
        <strong>{vessel.number}</strong>
        {batch ? (
          <>
            <span className="reactor-stage">{batch.stage_label}{minutes !== null ? ` · ${clock(minutes)}` : ''}</span>
            <span>Batch {batch.batch_id} · {fmtLbs(batch.total_in)} lbs in</span>
            <span className="muted">for WO {batch.order_id} · {fmtLbs(batch.target_lbs)} lbs {batch.product?.number}</span>
            <span className="reactor-open">Open batch →</span>
          </>
        ) : (
          <span className="muted">Empty — free for a batch</span>
        )}
      </span>
    </button>
  )
}

/* ------------------------------------------------------------ one batch */

const ORDER = ['charging', 'acid', 'mixing', 'settling', 'drawn']

export function ProcessBatchPage({ batchId }: { batchId: string }) {
  const { navigate, plantCode, can, companion } = useApp()
  const toast = useToast()
  const [batch, setBatch] = useState<ProcessBatch | null>(null)
  const [error, setError] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const load = () => api.get<ProcessBatch>(`/api/process/${batchId}`).then(setBatch).catch(setError)
  useEffect(() => { load() }, [batchId])
  // Someone else may be working the same batch from another terminal.
  useEffect(() => {
    const timer = window.setInterval(load, 60_000)
    return () => window.clearInterval(timer)
  }, [batchId])
  const minutes = useMinutesSince(batch?.stage_since)
  const canAct = can('txn.post') && !companion

  async function advance(to: string, message: string) {
    setBusy(true); setError(null)
    try {
      setBatch(await api.post<ProcessBatch>(`/api/process/${batchId}/advance`, { to }))
      toast.push('success', message, `Batch ${batchId}`)
      window.setTimeout(() => document.querySelector('.flow-part:not(.done):not(.waiting)')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80)
    } catch (err) { setError(err) } finally { setBusy(false) }
  }

  if (!batch) return error ? <ErrorBox error={error} /> : <Loading />
  const at = ORDER.indexOf(batch.status)
  const lead = batch.guide[0]
  const rest = batch.guide.slice(1)
  const done = (stage: string) => at > ORDER.indexOf(stage)
  const waiting = (stage: string, why: string) => (at < ORDER.indexOf(stage) ? why : false)

  return (
    <div className="flow">
      <div className="page-head">
        <div>
          <h1>Batch {batch.batch_id} — {plantCode}</h1>
          <div className="sub">
            {batch.product?.number} {batch.product?.description} in {batch.vessel?.number} · work order {batch.order_id} for {fmtLbs(batch.target_lbs)} lbs
          </div>
        </div>
        <div className="actions">
          <button onClick={() => navigate(`batches/${batch.department_id}`)}>All reactors</button>
          <button onClick={() => navigate('today')}>Back to Today</button>
        </div>
      </div>

      <div className="stage-strip" aria-label="Stages">
        {batch.timeline.map((t, i) => (
          <span key={t.stage} className={`stage-pill${i < at ? ' done' : i === at ? ' now' : ''}`}>
            <strong>{i < at || batch.status === 'drawn' ? '✓ ' : ''}{t.label}</strong>
            <span>{t.at ? fmtDateTime(t.at) : i === at ? 'now' : ''}</span>
          </span>
        ))}
        {batch.status !== 'drawn' && batch.status !== 'cancelled' && (
          <span className="stage-clock" role="status">{batch.stage_label} for {clock(minutes)}</span>
        )}
      </div>

      {error && <div style={{ marginBottom: 12 }}><ErrorBox error={error} /></div>}
      {batch.recipe?.notes && at < 4 && (
        <div style={{ marginBottom: 12 }}><Alert tone="info" title="About this recipe">{batch.recipe.notes}</Alert></div>
      )}

      <FlowPart n={1} title={`Soap in${lead ? ` — ${lead.material_number} ${lead.material_description}` : ''}`}
        done={done('charging')}
        summary={<>{fmtLbs(lead?.charged_lbs)} lbs of {lead?.material_number} charged</>}>
        {lead && (
          <div className="guide-line">
            About <strong>{fmtLbs(lead.guide_lbs)} lbs</strong> {lead.basis}.
            {lead.charged_lbs > 0 && <> In so far: <strong>{fmtLbs(lead.charged_lbs)} lbs</strong>.</>}
          </div>
        )}
        <Charges batch={batch} materialId={lead?.material_id} />
        {canAct && lead && (
          <AddCharge batch={batch} materialId={lead.material_id} suggested={lead.still_to_add} allowDelivery onDone={setBatch} />
        )}
        {canAct && (
          <div className="pf-go">
            <button className="primary big" disabled={busy || !(lead?.charged_lbs > 0)}
              onClick={() => advance('acid', 'Soap is in')}>
              Soap is in — add the acid →
            </button>
          </div>
        )}
      </FlowPart>

      <FlowPart n={2} title="Acid in" done={done('acid')} waiting={waiting('acid', 'Once the soap is in.')}
        summary={<>{rest.map((g) => `${fmtLbs(g.charged_lbs)} lbs ${g.material_number}`).join(' · ')}</>}>
        {rest.map((g) => (
          <div key={g.material_id} className="acid-part">
            <div className="guide-line">
              <strong>{g.material_number} {g.material_description}</strong> — about {fmtLbs(g.guide_lbs)} lbs {g.basis}
              {g.charged_lbs > 0 && <> · in so far {fmtLbs(g.charged_lbs)} lbs</>}
              {g.still_to_add <= 0.5 && g.charged_lbs > 0 && <Badge tone="ok">enough</Badge>}
            </div>
            <Charges batch={batch} materialId={g.material_id} />
            {canAct && g.still_to_add > 0.5 && (
              <AddCharge batch={batch} materialId={g.material_id} suggested={g.still_to_add} onDone={setBatch} />
            )}
          </div>
        ))}
        {canAct && (
          <>
            {rest.some((g) => g.charged_lbs < g.guide_lbs * 0.9) && (
              <Alert tone="warn" title="Less than the recipe calls for">
                {rest.filter((g) => g.charged_lbs < g.guide_lbs * 0.9).map((g) => `${g.material_number}: ${fmtLbs(g.charged_lbs)} of about ${fmtLbs(g.guide_lbs)} lbs`).join(' · ')}.
                {' '}Carry on if that is what the batch needs.
              </Alert>
            )}
            <div className="pf-go">
              <button className="primary big" disabled={busy} onClick={() => advance('mixing', 'Mixing')}>
                Acid is in — start mixing →
              </button>
            </div>
          </>
        )}
      </FlowPart>

      <FlowPart n={3} title="Cook & mix" done={done('mixing')} waiting={waiting('mixing', 'Once the acid is in.')}
        summary={<>mixed {batch.timeline[2].at && batch.timeline[3].at ? clock((new Date(batch.timeline[3].at).getTime() - new Date(batch.timeline[2].at).getTime()) / 60000) : ''}</>}>
        <div className="big-clock">Mixing for {clock(minutes)}</div>
        {canAct && (
          <div className="pf-go">
            <button className="primary big" disabled={busy} onClick={() => advance('settling', 'Settling')}>
              Mixing done — let it settle →
            </button>
          </div>
        )}
      </FlowPart>

      <FlowPart n={4} title="Settle" done={done('settling')} waiting={waiting('settling', 'Once it has mixed.')}
        summary={<>settled {batch.timeline[3].at && batch.timeline[4].at ? clock((new Date(batch.timeline[4].at).getTime() - new Date(batch.timeline[3].at).getTime()) / 60000) : ''}</>}
        keepOpen={batch.status === 'settling'}>
        <div className="big-clock">Settling for {clock(minutes)}</div>
        <div className="muted">Draw it off below once the layers have separated.</div>
      </FlowPart>

      <FlowPart n={5} title="Draw off" done={batch.status === 'drawn'} waiting={waiting('settling', 'Once it has settled.')}
        summary={<>{fmtLbs(batch.drawn_lbs)} of {fmtLbs(batch.total_in)} lbs drawn off</>} keepOpen>
        {batch.status === 'drawn'
          ? <DrawnSummary batch={batch} />
          : canAct ? <DrawOff batch={batch} onDone={setBatch} /> : <div className="muted">Drawn off by the acid department.</div>}
      </FlowPart>
    </div>
  )
}

function Charges({ batch, materialId }: { batch: ProcessBatch; materialId?: number }) {
  const rows = batch.transactions.filter((t) => t.to_location_id === batch.vessel_id && t.to_material_id === materialId
    && !t.voided && !t.is_reversal)
  if (!rows.length) return null
  return (
    <ul className="charges">
      {rows.map((t) => (
        <li key={t.transaction_id}>
          <strong>{fmtLbs(t.to_qty)} lbs</strong>{' '}
          {t.transaction_type === 'RECEIVE'
            ? <>off {t.remarks?.toLowerCase().includes('railcar') ? 'railcar' : 'truck'} {t.trailer_number || ''}</>
            : <>from {t.from_location_number}</>}
          <span className="muted"> · {fmtDateTime(t.transaction_date)} · {t.username}</span>
        </li>
      ))}
    </ul>
  )
}

/** Put one ingredient in: from a tank, or (soap) straight off a delivery. */
function AddCharge({
  batch, materialId, suggested, allowDelivery = false, onDone,
}: {
  batch: ProcessBatch
  materialId: number
  suggested: number
  allowDelivery?: boolean
  onDone: (batch: ProcessBatch) => void
}) {
  const { plantId, reference } = useApp()
  const toast = useToast()
  const { tiles, holds, mainProduct, board } = useTanks()
  const [source, setSource] = useState<'tank' | 'delivery'>('tank')
  const [tank, setTank] = useState<number | null>(null)
  const [po, setPo] = useState<number | null>(null)
  const [conveyance, setConveyance] = useState<'Truck' | 'Railcar'>('Truck')
  const [vehicle, setVehicle] = useState('')
  const [qty, setQty] = useState(suggested > 0 ? String(Math.round(suggested)) : '')
  const [error, setError] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const key = useMemo(() => newKey(), [batch.transactions.length])
  const material = reference.materials.find((m) => m.material_id === materialId)

  const deliveries = useAsync(
    () => (allowDelivery
      ? api.get<{ rows: Order[] }>(`/api/orders${qs({ plant_id: plantId, order_type_id: 3, open_only: true, limit: 200 })}`)
      : Promise.resolve({ rows: [] as Order[] })),
    [plantId, allowDelivery],
  )
  const expected = (deliveries.data?.rows ?? [])
    .filter((o) => o.material_one_id === materialId && o.material_one_quantity - o.qty_fulfilled > 0.5)
  // The tank holding the most of it is the obvious source.
  useEffect(() => {
    if (tank !== null) return
    const best = tiles.filter((t) => holds(t, materialId) > 0.5 && t.location_id !== batch.vessel_id)
      .sort((a, b) => holds(b, materialId) - holds(a, materialId))[0]
    if (best) setTank(best.location_id)
  }, [tiles])
  useEffect(() => { setQty(suggested > 0 ? String(Math.round(suggested)) : '') }, [suggested])
  const chosenPo = expected.find((o) => o.order_id === po)
  useEffect(() => { if (chosenPo) setConveyance(/rail/i.test(chosenPo.ship_method) ? 'Railcar' : 'Truck') }, [po])

  const n = Number(qty || 0)
  const available = tank ? holds(tiles.find((t) => t.location_id === tank)!, materialId) : 0
  const ready = n > 0 && (source === 'tank' ? Boolean(tank) && n <= available + 0.01 : Boolean(po))
  const from = source === 'tank'
    ? tiles.find((t) => t.location_id === tank)?.number ?? '—'
    : `${conveyance.toLowerCase()}${vehicle ? ` ${vehicle}` : ''}`

  async function put() {
    setBusy(true); setError(null)
    try {
      const updated = await api.post<ProcessBatch>(`/api/process/${batch.batch_id}/charge`, {
        material_id: materialId, quantity: n, idempotency_key: key,
        ...(source === 'tank'
          ? { from_location_id: tank }
          : { order_id: po, conveyance, vehicle }),
      })
      toast.push('success', `${fmtLbs(n)} lbs of ${material?.number} in`, batch.vessel?.number ?? '')
      board.reload()
      deliveries.reload()
      onDone(updated)
    } catch (err) { setError(err) } finally { setBusy(false) }
  }

  return (
    <div className="add-charge">
      {error && <div style={{ marginBottom: 10 }}><ErrorBox error={error} /></div>}
      {allowDelivery && (
        <div className="row" style={{ gap: 8, marginBottom: 10 }} role="group" aria-label="Where it comes from">
          <button className={source === 'tank' ? 'primary' : ''} aria-pressed={source === 'tank'} onClick={() => setSource('tank')}>From a tank</button>
          <button className={source === 'delivery' ? 'primary' : ''} aria-pressed={source === 'delivery'} onClick={() => setSource('delivery')}>
            Straight off a truck or railcar
          </button>
        </div>
      )}
      {source === 'tank' ? (
        <TankPicker tiles={tiles.filter((t) => t.location_id !== batch.vessel_id)} value={tank} onChange={setTank}
          mode="from" materialId={materialId} holds={holds} mainProduct={mainProduct} />
      ) : deliveries.loading ? <Loading /> : expected.length === 0 ? (
        <Alert tone="info" title={`No ${material?.number} deliveries are expected`}>
          A delivery is received against its purchase order; ask the office to raise one.
        </Alert>
      ) : (
        <>
          <div className="choice-list" role="radiogroup">
            {expected.map((o) => (
              <button key={o.order_id} role="radio" aria-checked={po === o.order_id}
                className={`choice${po === o.order_id ? ' selected' : ''}`} onClick={() => setPo(o.order_id)}>
                <strong>{o.vendor_name ?? 'Vendor'}{po === o.order_id ? ' ✓' : ''}</strong>
                <span className="muted">{fmtLbs(o.material_one_quantity - o.qty_fulfilled)} lbs expected · {/rail/i.test(o.ship_method) ? 'railcar' : 'truck'} · PO {o.order_id}</span>
              </button>
            ))}
          </div>
          {chosenPo && (
            <div className="row" style={{ gap: 8, marginTop: 10 }}>
              {(['Truck', 'Railcar'] as const).map((c) => (
                <button key={c} className={conveyance === c ? 'primary' : ''} aria-pressed={conveyance === c} onClick={() => setConveyance(c)}>{c}</button>
              ))}
              <input placeholder={conveyance === 'Railcar' ? 'Railcar number, e.g. UTLX 204417' : 'Trailer number'}
                value={vehicle} onChange={(e) => setVehicle(e.target.value)} style={{ maxWidth: 260 }} aria-label={`${conveyance} number`} />
            </div>
          )}
        </>
      )}
      <div style={{ marginTop: 10 }}>
        <Qty value={qty} onChange={setQty} max={source === 'tank' && tank ? available : undefined} maxLabel="All of it" />
      </div>
      <div className="pf-go" style={{ marginTop: 10 }}>
        <button className="primary" disabled={busy || !ready} onClick={put}>
          {busy ? <span className="spinner" /> : null} Put {fmtLbs(n)} lbs of {material?.number} in {batch.vessel?.number} from {from}
        </button>
      </div>
    </div>
  )
}

function DrawOff({ batch, onDone }: { batch: ProcessBatch; onDone: (batch: ProcessBatch) => void }) {
  const toast = useToast()
  const { tiles, holds, mainProduct } = useTanks()
  const [to, setTo] = useState<number | null>(null)
  const [qty, setQty] = useState(String(Math.round(batch.expected_out)))
  const [error, setError] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const n = Number(qty || 0)
  const actual = batch.total_in ? Math.round((n / batch.total_in) * 1000) / 10 : 0
  const loss = Math.max(batch.total_in - n, 0)
  const unexpected = error?.detail?.rule === 'unexpected_yield'
  useEffect(() => {
    if (to !== null || !batch.product) return
    const best = tiles.filter((t) => holds(t, batch.material_id) > 0.5 && t.kind === 'Tank')
      .sort((a, b) => (b.room ?? 0) - (a.room ?? 0))[0]
    if (best) setTo(best.location_id)
  }, [tiles])

  async function draw(acknowledge = false) {
    setBusy(true); setError(null)
    try {
      const updated = await api.post<ProcessBatch>(`/api/process/${batch.batch_id}/draw`, {
        to_location_id: to, quantity: n, acknowledge_yield: acknowledge,
      })
      toast.push('success', `Drew off ${fmtLbs(n)} lbs`, `Batch ${batch.batch_id} closed`)
      onDone(updated)
    } catch (err) { setError(err) } finally { setBusy(false) }
  }

  return (
    <>
      {error && !unexpected && <div style={{ marginBottom: 12 }}><ErrorBox error={error} /></div>}
      <Step n={1} title={`Into which tank does the ${batch.product?.number} go?`}>
        <TankPicker tiles={tiles.filter((t) => t.location_id !== batch.vessel_id)} value={to} onChange={setTo}
          mode="to" materialId={batch.material_id} qty={n} holds={holds} mainProduct={mainProduct} />
      </Step>
      <Step n={2} title="How much came off?" hint="From the gauge or the meter — the measured pounds, not the expected">
        <Qty value={qty} onChange={setQty} />
        <div className="yield-line">
          {fmtLbs(n)} of {fmtLbs(batch.total_in)} lbs charged is a <strong>{actual}%</strong> yield
          {' '}(usually about {batch.expected_yield}%). {fmtLbs(loss)} lbs leave as acid water and loss.
        </div>
      </Step>
      {unexpected && (
        <Alert tone="warn" title={error.message}>
          <div className="row" style={{ gap: 8, marginTop: 10 }}>
            <button className="primary sm" disabled={busy} onClick={() => draw(true)}>Yes, {fmtLbs(n)} lbs is right</button>
          </div>
        </Alert>
      )}
      <div className="pf-go">
        <button className="primary big" disabled={busy || !to || n <= 0} onClick={() => draw()}>
          {busy ? <span className="spinner" /> : null}
          Draw off {fmtLbs(n)} lbs of {batch.product?.number} into {tiles.find((t) => t.location_id === to)?.number ?? '—'} and close {batch.batch_id}
        </button>
      </div>
    </>
  )
}

function DrawnSummary({ batch }: { batch: ProcessBatch }) {
  const { navigate } = useApp()
  const produce = batch.transactions.filter((t) => t.transaction_type === 'PRODUCE' && !t.voided)
  const into = produce[0]?.to_location_number
  return (
    <Card>
      <Alert tone="ok" title={`${fmtLbs(batch.drawn_lbs)} lbs of ${batch.product?.number} into ${into}`}>
        {fmtLbs(batch.total_in)} lbs charged, {Math.round(((batch.drawn_lbs ?? 0) / (batch.total_in || 1)) * 1000) / 10}% yield,
        {' '}{fmtLbs(batch.total_in - (batch.drawn_lbs ?? 0))} lbs acid water and loss. The reactor is free for the next batch.
      </Alert>
      <div className="step-actions">
        <button onClick={() => navigate(`orders/${batch.order_id}`)}>Record QC on the order</button>
        <button className="primary" onClick={() => navigate(`batches/${batch.department_id}`)}>Next batch</button>
      </div>
    </Card>
  )
}
