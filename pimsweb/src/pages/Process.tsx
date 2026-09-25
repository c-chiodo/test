/* Staged batches — acidulation — on the floor.
 *
 * Blending is one button because a blend is done the moment it is mixed.
 * Acidulation is not. Soap comes off trucks and railcars (or off the spur,
 * where a car was received for the invoice and waited), into a settle tank
 * as Soap in Process; acid and steam go in on top; it cooks and mixes, then
 * sits and settles for hours; then it breaks — oil off the top into the
 * 20's, MGR, and process water off the bottom — each measured, the oil and
 * MGR with a moisture and an S reading. The MGR is reprocessed the same way
 * in its own tanks.
 *
 * So the acid screen is built around the tanks, and each batch is one page
 * read down in the order it happens, with the clock showing how long it has
 * been at its stage:
 *
 *   ① Soap in   ② Acid & steam in   ③ Cook & mix   ④ Settle   ⑤ Break
 *
 * The batch lives on the server. The operator who charges the tank at 5 a.m.
 * and the one who breaks it after lunch open the same page, from any
 * terminal, and see what the other one did. */

import { useEffect, useMemo, useState } from 'react'
import { useApp, useToast } from '../App'
import { api, newKey, qs } from '../lib/api'
import type { Order, ProcessBatch, ProcessGuide, ProcessOutput } from '../lib/types'
import { Alert, Badge, Card, ErrorBox, Loading, fmtDate, fmtDateTime, fmtLbs, useAsync } from '../components/ui'
import { FlowPart } from './LoadAndShip'
import { Qty, Step, TankPicker, useTanks } from './Operations'

/** "3 h 05 min", "25 min", "less than a minute". */
export function clock(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return ''
  if (minutes < 1) return 'less than a minute'
  const h = Math.floor(minutes / 60)
  const m = Math.round(minutes % 60)
  return h ? `${h} h ${String(m).padStart(2, '0')} min` : `${m} min`
}

/** Minutes since an ISO time, ticking twice a minute. */
function useMinutesSince(iso: string | null | undefined): number | null {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000)
    return () => window.clearInterval(timer)
  }, [])
  if (!iso) return null
  return Math.max(0, (now - new Date(iso).getTime()) / 60_000)
}

const READING_LABEL: Record<string, string> = { moisture: 'Moisture %', spintest: 'S' }

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
  const spur = useAsync(() => api.get<any[]>(`/api/process/spur?plant_id=${plantId}`), [plantId])
  const recipes = useAsync(() => api.get<any[]>('/api/blend/recipes'), [])
  const staged = (recipes.data ?? []).filter((r) => r.department_id === departmentId && r.method === 'staged')
  const recipeOf = (o: Order) => staged.find((r) => r.recipe_id === o.recipe_id)
    ?? staged.find((r) => r.material_id === o.material_one_id)
  const running = new Set((vessels.data ?? []).map((v) => v.batch?.order_id).filter(Boolean))
  const waiting = (work.data?.rows ?? [])
    .filter((o) => recipeOf(o) && o.material_one_quantity - o.qty_fulfilled > 0.5 && !running.has(o.order_id))
    .sort((a, b) => a.due_date.localeCompare(b.due_date))
  // In the order a batch goes through them.
  const kinds = [...new Set((vessels.data ?? []).map((v) => v.vessel_type))]
    .sort((a, b) => ['Acid', 'Settle', 'MGR'].indexOf(a) - ['Acid', 'Settle', 'MGR'].indexOf(b))

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
          <div className="sub">The reactors, settle and MGR tanks and what is in each, what is waiting on the spur, and the work to start.</div>
        </div>
        <div className="actions"><button onClick={() => navigate('today')}>Back to Today</button></div>
      </div>
      {error && <div style={{ marginBottom: 12 }}><ErrorBox error={error} /></div>}

      {vessels.loading ? <Loading /> : vessels.error ? <ErrorBox error={vessels.error} /> : kinds.length === 0 ? (
        <Alert tone="info" title="No settle tanks at this plant">
          A batch runs in a location whose type is the recipe's tank type. Ask an administrator to add one.
        </Alert>
      ) : kinds.map((kind) => (
        <section key={kind} className="task-group">
          <h2>{({ Acid: 'Reactors — soap and acid go in here', Settle: 'Settle tanks — moved here to settle and break', MGR: 'MGR tanks' } as Record<string, string>)[kind] ?? `${kind} tanks`}</h2>
          <div className="reactors">
            {(vessels.data ?? []).filter((v) => v.vessel_type === kind).map((v) => <Reactor key={v.location_id} vessel={v} />)}
          </div>
        </section>
      ))}

      {(spur.data ?? []).length > 0 && (
        <section className="task-group">
          <h2>Waiting on the spur</h2>
          <div className="choice-list">
            {(spur.data ?? []).map((row) => (
              <div key={`${row.location_id}-${row.material_id}`} className="choice">
                <strong>{fmtLbs(row.balance)} lbs {row.material_number} {row.material_description}</strong>
                <span className="muted">
                  on {row.location_number} · {row.cars.map((c: any) => c.trailer_number || '—').join(', ')}
                  {row.oldest_minutes ? ` · received ${clock(row.oldest_minutes)} ago` : ''}
                </span>
                <span className="small muted">Unload it into a settle tank from the batch's “Soap in”.</span>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="task-group">
        <h2>Work orders waiting</h2>
        {work.loading || recipes.loading ? <Loading /> : waiting.length === 0 ? (
          <div className="lane-empty today-empty">Nothing waiting — every {name.toLowerCase()} work order is running or done.</div>
        ) : (
          <div className="choice-list">
            {waiting.map((o) => {
              const recipe = recipeOf(o)
              const free = (vessels.data ?? []).filter((v) => !v.batch && v.vessel_type === recipe?.vessel_type)
              return (
                <div key={o.order_id} className={`choice${orderId === o.order_id ? ' selected' : ''}`}>
                  <strong>{recipe?.name} · {o.material_one_number} {o.material_one_description}</strong>
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
                    <span className="tank-flag" style={{ marginTop: 6 }}>every {String(recipe?.vessel_type).toLowerCase()} tank is busy — break one first</span>
                  ))}
                </div>
              )
            })}
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
            <span className="muted">WO {batch.order_id} · {fmtLbs(batch.target_lbs)} lbs {batch.product?.number}</span>
            <span className="reactor-open">Open batch →</span>
          </>
        ) : (
          <span className="muted">{vessel.total > 0.5 ? `${fmtLbs(vessel.total)} lbs in, no batch` : 'Empty — free for a batch'}</span>
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
  const into = batch.process_material ? ` as ${batch.process_material.number} ${batch.process_material.description}` : ''

  return (
    <div className="flow">
      <div className="page-head">
        <div>
          <h1>Batch {batch.batch_id} — {plantCode}</h1>
          <div className="sub">
            {batch.recipe?.name} in {batch.vessel?.number}
            {batch.moves?.length ? <> (via {[batch.moves[0].from, ...batch.moves.slice(0, -1).map((m) => m.to)].join(' → ')})</> : null}
            {' '}· work order {batch.order_id} for {fmtLbs(batch.target_lbs)} lbs {batch.product?.number} {batch.product?.description}
          </div>
        </div>
        <div className="actions">
          <button onClick={() => navigate(`batches/${batch.department_id}`)}>All tanks</button>
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

      <FlowPart n={1} title={`${lead?.label ?? 'Soap'} in`} done={done('charging')}
        summary={<>{fmtLbs(lead?.charged_lbs)} lbs of {lead?.label.toLowerCase()} into {batch.moves?.[0]?.from ?? batch.vessel?.number}{into}</>}>
        {lead && (
          <div className="guide-line">
            About <strong>{fmtLbs(lead.guide_lbs)} lbs</strong> {lead.basis}.
            {lead.charged_lbs > 0 && <> In so far: <strong>{fmtLbs(lead.charged_lbs)} lbs</strong>.</>}
          </div>
        )}
        <Charges batch={batch} group={lead} />
        {canAct && lead && <AddCharge batch={batch} group={lead} suggested={lead.still_to_add} lead onDone={setBatch} />}
        {canAct && (
          <div className="pf-go">
            <button className="primary big" disabled={busy || !(lead?.charged_lbs > 0)} onClick={() => advance('acid', 'Soap is in')}>
              {lead?.label ?? 'Soap'} is in — add the acid →
            </button>
          </div>
        )}
      </FlowPart>

      <FlowPart n={2} title={rest.length ? `${rest.map((g) => g.label).join(' & ')} in` : 'Additions'}
        done={done('acid')} waiting={waiting('acid', 'Once the soap is in.')}
        summary={<>{rest.map((g) => `${fmtLbs(g.charged_lbs)} lbs ${g.label.toLowerCase()}`).join(' · ') || 'nothing added'}</>}>
        {rest.length === 0 && <div className="muted">This recipe adds nothing on top.</div>}
        {rest.map((g) => (
          <div key={g.key} className="acid-part">
            <div className="guide-line">
              <strong>{g.label}</strong> — about {fmtLbs(g.guide_lbs)} lbs ({g.percentage} per 100 lbs of {lead?.label.toLowerCase()}) {g.basis}
              {g.charged_lbs > 0 && <> · in so far {fmtLbs(g.charged_lbs)} lbs</>}
              {g.still_to_add <= 0.5 && g.charged_lbs > 0 && <Badge tone="ok">enough</Badge>}
            </div>
            <Charges batch={batch} group={g} />
            {canAct && g.still_to_add > 0.5 && <AddCharge batch={batch} group={g} suggested={g.still_to_add} onDone={setBatch} />}
          </div>
        ))}
        {canAct && (
          <>
            {rest.some((g) => g.charged_lbs < g.guide_lbs * 0.9) && (
              <Alert tone="warn" title="Less than usual">
                {rest.filter((g) => g.charged_lbs < g.guide_lbs * 0.9).map((g) => `${g.label}: ${fmtLbs(g.charged_lbs)} of about ${fmtLbs(g.guide_lbs)} lbs`).join(' · ')}.
                {' '}Carry on if that is what this soap needs.
              </Alert>
            )}
            <div className="pf-go">
              <button className="primary big" disabled={busy} onClick={() => advance('mixing', 'Cooking & mixing')}>
                Acid is in — start the cook →
              </button>
            </div>
          </>
        )}
      </FlowPart>

      <FlowPart n={3} title="Cook & mix" done={done('mixing')} waiting={waiting('mixing', 'Once the acid is in.')}
        summary={<>cooked {spanOf(batch, 2, 3)}</>}>
        <div className="big-clock">Cooking for {clock(minutes)}</div>
        {canAct && batch.can_move && <MoveBatch batch={batch} prefer="Settle" onDone={setBatch} />}
        {canAct && (
          <div className="pf-go">
            <button className="primary big" disabled={busy} onClick={() => advance('settling', 'Settling')}>
              Cook done — let it settle →
            </button>
          </div>
        )}
      </FlowPart>

      <FlowPart n={4} title="Settle" done={done('settling')} waiting={waiting('settling', 'Once it has cooked.')}
        summary={<>settled {spanOf(batch, 3, 4)}</>} keepOpen={batch.status === 'settling'}>
        <div className="big-clock">Settling for {clock(minutes)}</div>
        <div className="muted">Break it below once the layers have separated.</div>
        {canAct && batch.can_move && batch.status === 'settling' && <MoveBatch batch={batch} prefer="Settle" onDone={setBatch} />}
      </FlowPart>

      <FlowPart n={5} title="Break" done={batch.status === 'drawn'} waiting={waiting('settling', 'Once it has settled.')}
        summary={<>{fmtLbs(batch.metrics.measured_out)} of {fmtLbs(batch.total_in)} lbs measured off</>} keepOpen>
        {batch.status === 'drawn'
          ? <BrokenSummary batch={batch} />
          : canAct ? (batch.outputs.length ? <Break batch={batch} onDone={setBatch} /> : <SingleDraw batch={batch} onDone={setBatch} />)
            : <div className="muted">Broken by the acid department.</div>}
      </FlowPart>
    </div>
  )
}

const VESSEL_WORD: Record<string, string> = { Acid: 'reactor', Settle: 'settle tank', MGR: 'MGR tank' }

/** Pump the whole batch on to another tank — out of the reactor into a settle
 *  tank, say — as each plant does before the break. */
function MoveBatch({ batch, prefer, onDone }: { batch: ProcessBatch; prefer: string; onDone: (batch: ProcessBatch) => void }) {
  const { plantId } = useApp()
  const toast = useToast()
  const vessels = useAsync(
    () => api.get<any[]>(`/api/process/vessels${qs({ plant_id: plantId, department_id: batch.department_id })}`),
    [plantId, batch.vessel_id],
  )
  const [open, setOpen] = useState(false)
  const [to, setTo] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<any>(null)
  const rank = (type: string) => (type === prefer ? 0 : type === 'Acid' ? 1 : 2)
  const free = (vessels.data ?? [])
    .filter((v) => !v.batch && v.location_id !== batch.vessel_id && v.total <= 0.5)
    .sort((a, b) => rank(a.vessel_type) - rank(b.vessel_type))
  const target = free.find((v) => v.location_id === to)

  async function go() {
    setBusy(true); setError(null)
    try {
      const moved = await api.post<ProcessBatch>(`/api/process/${batch.batch_id}/move`, { to_location_id: to })
      toast.push('success', `Moved to ${moved.vessel?.number}`, `${fmtLbs(moved.total_in)} lbs · batch ${batch.batch_id}`)
      setOpen(false); setTo(null)
      onDone(moved)
    } catch (err) { setError(err) } finally { setBusy(false) }
  }

  if (!open) {
    return (
      <div className="move-batch">
        <button onClick={() => setOpen(true)}>Move it to another tank…</button>
        <span className="muted small"> The whole batch goes, and it breaks from wherever it ends up.</span>
      </div>
    )
  }
  return (
    <div className="move-batch open">
      <div className="guide-line"><strong>Move {fmtLbs(batch.total_in)} lbs out of {batch.vessel?.number} into:</strong></div>
      {error && <div style={{ marginBottom: 10 }}><ErrorBox error={error} /></div>}
      {vessels.loading ? <Loading /> : free.length === 0
        ? <div className="muted">Every other tank has something in it.</div>
        : (
          <div className="choice-list">
            {free.map((v) => (
              <button key={v.location_id} className={`choice${to === v.location_id ? ' selected' : ''}`} onClick={() => setTo(v.location_id)}>
                <strong>{v.number}{to === v.location_id ? ' ✓' : ''}</strong>
                <span>{VESSEL_WORD[v.vessel_type] ?? v.vessel_type} · empty{v.max_capacity ? `, holds ${fmtLbs(v.max_capacity)} lbs` : ''}</span>
              </button>
            ))}
          </div>
        )}
      <div className="pf-go" style={{ marginTop: 12, gap: 8 }}>
        <button className="ghost" onClick={() => { setOpen(false); setTo(null) }}>Not now</button>
        <button className="primary big" disabled={busy || !target || (target.max_capacity && target.max_capacity < batch.total_in)} onClick={go}>
          {busy ? <span className="spinner" /> : null}
          Move {fmtLbs(batch.total_in)} lbs from {batch.vessel?.number} to {target?.number ?? '—'}
        </button>
      </div>
    </div>
  )
}

function spanOf(batch: ProcessBatch, from: number, to: number): string {
  const a = batch.timeline[from]?.at
  const b = batch.timeline[to]?.at
  return a && b ? clock((new Date(b).getTime() - new Date(a).getTime()) / 60000) : ''
}

function Charges({ batch, group }: { batch: ProcessBatch; group?: ProcessGuide }) {
  const ids = new Set((group?.materials ?? []).map((m) => m.material_id))
  // What went in, wherever the batch has moved since: the rows that made the
  // process material (a move out of one tank into the next does not count).
  const into = (t: Record<string, any>) => (batch.process_material
    ? t.to_material_id === batch.process_material.material_id : t.to_location_id === batch.vessel_id)
  const rows = batch.transactions.filter((t) => into(t) && !t.voided && !t.is_reversal
    && ids.has(t.from_material_id ?? t.to_material_id))
  if (!rows.length) return null
  return (
    <ul className="charges">
      {rows.map((t) => (
        <li key={t.transaction_id}>
          <strong>{fmtLbs(t.to_qty)} lbs</strong> {t.from_material_number}{' '}
          {/RECV/.test(t.from_location_number ?? '')
            ? <>off {/RAIL/.test(t.from_location_number) ? 'railcar' : 'truck'} {t.trailer_number || ''}</>
            : <>from {t.from_location_number}</>}
          <span className="muted"> · {fmtDateTime(t.transaction_date)} · {t.username}</span>
        </li>
      ))}
    </ul>
  )
}

type Source = 'spur' | 'delivery' | 'tank' | 'utility'

/** Put one ingredient in: off the spur, straight off a delivery, from a
 *  tank, or (steam) from the plant's utility. */
function AddCharge({
  batch, group, suggested, lead = false, onDone,
}: {
  batch: ProcessBatch
  group: ProcessGuide
  suggested: number
  lead?: boolean
  onDone: (batch: ProcessBatch) => void
}) {
  const { plantId, reference } = useApp()
  const toast = useToast()
  const { tiles, holds, mainProduct, board } = useTanks()
  const ids = useMemo(() => new Set(group.materials.map((m) => m.material_id)), [group])
  const utility = reference.locations.find((l) => l.plant_id === plantId && l.location_type === 'Utility')
  const isUtility = group.materials.some((m) => /steam/i.test(m.description)) && Boolean(utility)

  const spur = useAsync(() => (lead ? api.get<any[]>(`/api/process/spur?plant_id=${plantId}`) : Promise.resolve([])), [plantId, lead])
  const deliveries = useAsync(
    () => (lead
      ? api.get<{ rows: Order[] }>(`/api/orders${qs({ plant_id: plantId, order_type_id: 3, open_only: true, limit: 200 })}`)
      : Promise.resolve({ rows: [] as Order[] })),
    [plantId, lead],
  )
  const onSpur = (spur.data ?? []).filter((r) => ids.has(r.material_id))
  const expected = (deliveries.data?.rows ?? []).filter((o) => ids.has(o.material_one_id ?? -1) && o.material_one_quantity - o.qty_fulfilled > 0.5)
  const sources: Source[] = isUtility ? ['utility'] : lead ? ['spur', 'delivery', 'tank'] : ['tank']
  const [source, setSource] = useState<Source>(sources[0])
  useEffect(() => { if (lead && spur.data && !onSpur.length && source === 'spur') setSource(expected.length ? 'delivery' : 'tank') }, [spur.data, deliveries.data])

  const [pick, setPick] = useState<any>(null)            // spur row, PO, or tank id
  const [conveyance, setConveyance] = useState<'Truck' | 'Railcar'>('Truck')
  const [vehicle, setVehicle] = useState('')
  const [qty, setQty] = useState(suggested > 0 ? String(Math.round(suggested)) : '')
  const [error, setError] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const key = useMemo(() => newKey(), [batch.transactions.length])
  useEffect(() => { setQty(suggested > 0 ? String(Math.round(suggested)) : '') }, [suggested])
  useEffect(() => { setPick(null) }, [source])
  useEffect(() => {
    if (source === 'delivery' && pick) setConveyance(/^RL|rail/i.test(pick.ship_method || '') ? 'Railcar' : 'Truck')
  }, [pick])
  // The tank holding the most of it is the obvious source.
  useEffect(() => {
    if (source !== 'tank' || pick !== null) return
    const best = tiles.filter((t) => t.location_id !== batch.vessel_id && [...ids].some((m) => holds(t, m) > 0.5))
      .sort((a, b) => Math.max(...[...ids].map((m) => holds(b, m))) - Math.max(...[...ids].map((m) => holds(a, m))))[0]
    if (best) setPick(best.location_id)
  }, [tiles, source])

  // Which material is going in follows from where it comes from.
  const tank = source === 'tank' ? tiles.find((t) => t.location_id === pick) : undefined
  const tankMaterial = tank ? [...ids].sort((a, b) => holds(tank, b) - holds(tank, a))[0] : null
  const materialId = source === 'spur' ? pick?.material_id
    : source === 'delivery' ? pick?.material_one_id
      : source === 'utility' ? group.materials[0].material_id
        : tankMaterial
  const material = reference.materials.find((m) => m.material_id === materialId)
  const available = source === 'tank' && tank && tankMaterial ? holds(tank, tankMaterial)
    : source === 'spur' && pick ? pick.balance : undefined
  const n = Number(qty || 0)
  const ready = n > 0 && Boolean(materialId) && (available === undefined || n <= available + 0.01)
  const from = source === 'spur' ? `the spur (${pick?.cars?.[0]?.trailer_number || pick?.location_number || ''})`
    : source === 'delivery' ? `${conveyance.toLowerCase()}${vehicle ? ` ${vehicle}` : ''}`
      : source === 'utility' ? utility?.number : tank?.number ?? '—'

  async function put() {
    setBusy(true); setError(null)
    try {
      const updated = await api.post<ProcessBatch>(`/api/process/${batch.batch_id}/charge`, {
        material_id: materialId, quantity: n, idempotency_key: key,
        ...(source === 'delivery' ? { order_id: pick.order_id, conveyance, vehicle }
          : source === 'spur' ? { from_location_id: pick.location_id }
            : source === 'utility' ? { from_location_id: utility!.location_id }
              : { from_location_id: pick }),
      })
      toast.push('success', `${fmtLbs(n)} lbs of ${material?.number} in`, batch.vessel?.number ?? '')
      board.reload(); spur.reload(); deliveries.reload()
      setPick(null)
      onDone(updated)
    } catch (err) { setError(err) } finally { setBusy(false) }
  }

  const SOURCE_LABEL: Record<Source, string> = {
    spur: `Waiting on the spur${onSpur.length ? ` (${onSpur.length})` : ''}`,
    delivery: 'Straight off a truck or railcar',
    tank: 'From a tank',
    utility: 'Steam',
  }

  return (
    <div className="add-charge">
      {error && <div style={{ marginBottom: 10 }}><ErrorBox error={error} /></div>}
      {sources.length > 1 && (
        <div className="row" style={{ gap: 8, marginBottom: 10 }} role="group" aria-label="Where it comes from">
          {sources.map((s) => (
            <button key={s} className={source === s ? 'primary' : ''} aria-pressed={source === s} onClick={() => setSource(s)}>
              {SOURCE_LABEL[s]}
            </button>
          ))}
        </div>
      )}
      {source === 'utility' && (
        <div className="muted" style={{ marginBottom: 8 }}>
          From {utility?.number} {utility?.description} — an estimate; there is no tank to run dry.
        </div>
      )}
      {source === 'spur' && (spur.loading ? <Loading /> : onSpur.length === 0 ? (
        <div className="muted">Nothing of this is waiting on the spur.</div>
      ) : (
        <div className="choice-list" role="radiogroup">
          {onSpur.map((row) => (
            <button key={`${row.location_id}-${row.material_id}`} role="radio" aria-checked={pick === row}
              className={`choice${pick === row ? ' selected' : ''}`} onClick={() => setPick(row)}>
              <strong>{fmtLbs(row.balance)} lbs {row.material_description}{pick === row ? ' ✓' : ''}</strong>
              <span className="muted">{row.location_number} · {row.cars.map((c: any) => c.trailer_number || '—').join(', ')}</span>
            </button>
          ))}
        </div>
      ))}
      {source === 'delivery' && (deliveries.loading ? <Loading /> : expected.length === 0 ? (
        <Alert tone="info" title="No soap deliveries are expected">
          A delivery is received against its purchase order; ask the office to raise one.
        </Alert>
      ) : (
        <>
          <div className="choice-list" role="radiogroup">
            {expected.map((o) => (
              <button key={o.order_id} role="radio" aria-checked={pick === o}
                className={`choice${pick === o ? ' selected' : ''}`} onClick={() => setPick(o)}>
                <strong>{o.vendor_name ?? 'Vendor'}{pick === o ? ' ✓' : ''}</strong>
                <span>{o.material_one_description}</span>
                <span className="muted">{fmtLbs(o.material_one_quantity - o.qty_fulfilled)} lbs expected · {/^RL|rail/i.test(o.ship_method) ? 'rail' : 'truck'} · PO {o.order_id}</span>
              </button>
            ))}
          </div>
          {pick && (
            <div className="row" style={{ gap: 8, marginTop: 10 }}>
              {(['Truck', 'Railcar'] as const).map((c) => (
                <button key={c} className={conveyance === c ? 'primary' : ''} aria-pressed={conveyance === c} onClick={() => setConveyance(c)}>{c}</button>
              ))}
              <input placeholder={conveyance === 'Railcar' ? 'Railcar number, e.g. UTLX 667576' : 'Trailer number'}
                value={vehicle} onChange={(e) => setVehicle(e.target.value)} style={{ maxWidth: 260 }} aria-label={`${conveyance} number`} />
            </div>
          )}
        </>
      ))}
      {source === 'tank' && (
        <TankPicker tiles={tiles.filter((t) => t.location_id !== batch.vessel_id && [...ids].some((m) => holds(t, m) > 0.5))}
          value={pick} onChange={setPick} mode="from" holds={holds} mainProduct={mainProduct} />
      )}
      <div style={{ marginTop: 10 }}>
        <Qty value={qty} onChange={setQty} max={available} maxLabel="All of it" />
      </div>
      <div className="pf-go" style={{ marginTop: 10 }}>
        <button className="primary" disabled={busy || !ready} onClick={put}>
          {busy ? <span className="spinner" /> : null} Put {fmtLbs(n)} lbs of {material?.number ?? group.label.toLowerCase()} in {batch.vessel?.number} from {from}
        </button>
      </div>
    </div>
  )
}

function readingsOf(output: ProcessOutput): string[] {
  return String(output.readings || '').split(',').map((r) => r.trim()).filter(Boolean)
}

/** The break: each layer into its own tank, measured, with its readings. */
function Break({ batch, onDone }: { batch: ProcessBatch; onDone: (batch: ProcessBatch) => void }) {
  const toast = useToast()
  const { tiles, holds, mainProduct } = useTanks()
  const [layers, setLayers] = useState<Record<number, { to: number | null; qty: string; readings: Record<string, string> }>>(() =>
    Object.fromEntries(batch.outputs.map((o) => [o.material_id, { to: o.suggested_tanks[0] ?? null, qty: '', readings: {} }])))
  const [error, setError] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const set = (id: number, patch: Partial<{ to: number | null; qty: string; readings: Record<string, string> }>) =>
    setLayers((current) => ({ ...current, [id]: { ...current[id], ...patch } }))
  const measured = batch.outputs.reduce((a, o) => a + Number(layers[o.material_id]?.qty || 0), 0)
  const oil = batch.outputs.find((o) => o.role === 'oil')
  const oilLbs = oil ? Number(layers[oil.material_id]?.qty || 0) : 0
  const theoretical = batch.metrics.theoretical_oil
  const fpy = theoretical && oilLbs ? Math.round((oilLbs / theoretical) * 1000) / 10 : null
  const gap = batch.total_in - measured
  const unbalanced = error?.detail?.rule === 'unbalanced_break'
  const ready = batch.outputs.every((o) => Number(layers[o.material_id]?.qty || 0) > 0 && layers[o.material_id]?.to)

  async function breakIt(acknowledge = false) {
    setBusy(true); setError(null)
    try {
      const updated = await api.post<ProcessBatch>(`/api/process/${batch.batch_id}/draw`, {
        acknowledge_balance: acknowledge,
        outputs: batch.outputs.map((o) => ({
          material_id: o.material_id, to_location_id: layers[o.material_id].to, quantity: Number(layers[o.material_id].qty),
          readings: Object.fromEntries(Object.entries(layers[o.material_id].readings).filter(([, v]) => v !== '').map(([k, v]) => [k, Number(v)])),
        })),
      })
      toast.push('success', `Broke ${batch.batch_id}`, `${fmtLbs(measured)} lbs measured off`)
      onDone(updated)
    } catch (err) { setError(err) } finally { setBusy(false) }
  }

  return (
    <>
      {error && !unbalanced && <div style={{ marginBottom: 12 }}><ErrorBox error={error} /></div>}
      {batch.outputs.map((o, i) => (
        <Step key={o.material_id} n={i + 1} title={`${o.label} — ${o.material_number} ${o.material_description}`}
          hint={o.role === 'water' ? 'Off the bottom' : o.role === 'oil' ? 'Off the top' : undefined}>
          <TankPicker tiles={tiles.filter((t) => t.location_id !== batch.vessel_id)} value={layers[o.material_id]?.to ?? null}
            onChange={(id) => set(o.material_id, { to: id })} mode="to" materialId={o.material_id}
            qty={Number(layers[o.material_id]?.qty || 0)} holds={holds} mainProduct={mainProduct} suggested={o.suggested_tanks[0]} />
          <div className="row" style={{ gap: 14, marginTop: 10, alignItems: 'flex-end' }}>
            <div>
              <div className="small muted">Pounds off (gauge or meter)</div>
              <Qty value={layers[o.material_id]?.qty ?? ''} onChange={(v) => set(o.material_id, { qty: v })} />
            </div>
            {readingsOf(o).map((r) => (
              <label key={r} className="field" style={{ maxWidth: 120 }}>
                <span className="small muted">{READING_LABEL[r] ?? r}</span>
                <input type="number" inputMode="decimal" step="0.01" aria-label={`${o.label} ${READING_LABEL[r] ?? r}`}
                  value={layers[o.material_id]?.readings[r] ?? ''}
                  onChange={(e) => set(o.material_id, { readings: { ...layers[o.material_id].readings, [r]: e.target.value } })} />
              </label>
            ))}
          </div>
        </Step>
      ))}
      <div className="yield-line">
        {fmtLbs(measured)} of {fmtLbs(batch.total_in)} lbs in measured off
        {Math.abs(gap) > 0.5 && measured > 0 && <> · {fmtLbs(Math.abs(gap))} lbs {gap > 0 ? 'unaccounted for' : 'over'}</>}
        {fpy !== null && <> · first-pass yield <strong>{fpy}%</strong> of what the {batch.guide[0]?.label.toLowerCase()} could give (usually about {batch.expected_yield}%)</>}
      </div>
      {unbalanced && (
        <Alert tone="warn" title={error.message}>
          <div className="row" style={{ gap: 8, marginTop: 10 }}>
            <button className="primary sm" disabled={busy} onClick={() => breakIt(true)}>Yes, the gauges are right</button>
          </div>
        </Alert>
      )}
      <div className="pf-go">
        <button className="primary big" disabled={busy || !ready} onClick={() => breakIt()}>
          {busy ? <span className="spinner" /> : null}
          Break {batch.batch_id}: {batch.outputs.map((o) => `${fmtLbs(Number(layers[o.material_id]?.qty || 0))} ${o.label.toLowerCase()}`).join(' · ')}
        </button>
      </div>
    </>
  )
}

/** A recipe with one product and no layers: draw it off, measured. */
function SingleDraw({ batch, onDone }: { batch: ProcessBatch; onDone: (batch: ProcessBatch) => void }) {
  const toast = useToast()
  const { tiles, holds, mainProduct } = useTanks()
  const [to, setTo] = useState<number | null>(null)
  const [qty, setQty] = useState(String(Math.round(batch.expected_out ?? 0)))
  const [error, setError] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const n = Number(qty || 0)
  const unexpected = error?.detail?.rule === 'unexpected_yield'
  async function draw(acknowledge = false) {
    setBusy(true); setError(null)
    try {
      const updated = await api.post<ProcessBatch>(`/api/process/${batch.batch_id}/draw`, { to_location_id: to, quantity: n, acknowledge_yield: acknowledge })
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
      <Step n={2} title="How much came off?"><Qty value={qty} onChange={setQty} /></Step>
      {unexpected && (
        <Alert tone="warn" title={error.message}>
          <button className="primary sm" disabled={busy} onClick={() => draw(true)}>Yes, {fmtLbs(n)} lbs is right</button>
        </Alert>
      )}
      <div className="pf-go">
        <button className="primary big" disabled={busy || !to || n <= 0} onClick={() => draw()}>Draw off {fmtLbs(n)} lbs</button>
      </div>
    </>
  )
}

function BrokenSummary({ batch }: { batch: ProcessBatch }) {
  const { navigate } = useApp()
  const m = batch.metrics
  return (
    <Card>
      <Alert tone="ok" title={`Broken: ${batch.outputs.map((o) => `${fmtLbs(o.lbs)} lbs ${o.label.toLowerCase()} into ${o.into.join(', ')}`
        + (Object.keys(o.measured ?? {}).length ? ` (${Object.entries(o.measured).map(([k, v]) => `${k === 'moisture' ? 'M' : 'S'} ${v}`).join(', ')})` : '')).join(' · ')}`}>
        {fmtLbs(batch.total_in)} lbs in, {fmtLbs(m.measured_out)} lbs measured off
        {m.unaccounted ? ` (${fmtLbs(Math.abs(m.unaccounted))} lbs ${m.unaccounted > 0 ? 'unaccounted for' : 'over'})` : ''}.
        {m.fpy !== undefined && <> First-pass yield {m.fpy}% of what the soap could give at {m.tfa}% TFA.</>}
        {m.split && <> Split: {Object.entries(m.split).map(([role, pct]) => `${role} ${pct}%`).join(', ')}.</>}
        {' '}The tank is free for the next batch.
      </Alert>
      <div className="step-actions">
        <button onClick={() => navigate(`orders/${batch.order_id}`)}>Open the work order</button>
        <button className="primary" onClick={() => navigate(`batches/${batch.department_id}`)}>Next batch</button>
      </div>
    </Card>
  )
}
