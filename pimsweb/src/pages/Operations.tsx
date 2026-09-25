/* Plant floor: say what happened, and it is recorded.
 *
 * The legacy client had a window per transaction type — Receive, Produce,
 * Move, Load Trailer, Ship Trailer, Shrinkage — each the same grid of From
 * location / From material / From qty / From BOL / To location / To material /
 * To qty / To BOL, plus an order picked from every open order at the plant.
 * The first version of this screen kept that shape, and it was the one
 * operators found hardest: to record "I pumped tank 105 into tank 106" they had
 * to know that is a *Move*, that the tank is a *From location*, which product
 * code is in it, and to leave half the boxes empty.
 *
 * Now the screen asks what happened, in the operator's words, and each job is
 * a few numbered questions on one page:
 *
 *   - tanks are picked from tiles showing what each holds and how much room is
 *     left, not from a list of location codes;
 *   - the product fills itself in from the tank (a tank holds one product);
 *   - only the order types that job uses are offered — purchase orders for a
 *     delivery, nothing at all for a tank-to-tank move;
 *   - dates, BOLs, hours and remarks wait under "More details";
 *   - the button says what will be recorded, in a sentence;
 *   - and the result can be undone on the spot, because a mistake that is cheap
 *     to fix is a mistake people are not afraid to make while they learn.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { batchDepartments, useApp, useToast } from '../App'
import { api, newKey, qs } from '../lib/api'
import type { Order, PendingShipment, TankBoardData, TankTile, Transaction } from '../lib/types'
import {
  Alert, Badge, Card, DataTable, ErrorBox, Field, Loading,
  fmtDate, fmtDateTime, fmtLbs, today, useAsync,
} from '../components/ui'

type Job = 'receive' | 'move' | 'shrink' | 'produce' | 'ship'

interface Task {
  key: string
  title: string
  detail: string
  icon: string
  /** Where the task lives: one of this screen's jobs, or another screen. */
  job?: Job
  route?: string
}

export default function Operations({ initialOperation, orderId }: { initialOperation?: string; orderId?: number }) {
  const { navigate, departments, department } = useApp()
  const key = (initialOperation || '').split('?')[0]

  // Old links still land somewhere sensible.
  useEffect(() => {
    if (key === 'load') navigate(orderId ? `load-ship/${orderId}` : 'load-ship')
  }, [key, orderId, navigate])

  if (key === 'receive') return <JobPage title="A delivery arrived"><ReceiveJob orderId={orderId} /></JobPage>
  if (key === 'move') return <JobPage title="Product moved to another tank"><MoveJob /></JobPage>
  if (key === 'shrink') return <JobPage title="Product lost or written off"><ShrinkJob /></JobPage>
  if (key === 'produce') return <JobPage title="Made product without a recipe"><ProduceJob orderId={orderId} /></JobPage>
  if (key === 'ship') return <JobPage title="Ship a loaded trailer"><ShipTrailer /></JobPage>

  const own = batchDepartments(departments)
  const groups: { title: string; tasks: Task[] }[] = [
    { title: 'Trucks', tasks: [
      { key: 'receive', job: 'receive', icon: '⇣', title: 'A delivery arrived', detail: 'Soap, acid or an ingredient came in on a truck or railcar and went into a tank.' },
      { key: 'load', route: 'load-ship', icon: '⇡', title: 'Load a truck', detail: 'Check the trailer, load it, test it and send it — one page.' },
      { key: 'ship', job: 'ship', icon: '⇢', title: 'Ship a loaded trailer', detail: 'The trailer is loaded and sealed, and the driver is leaving.' },
    ]},
    { title: 'Making product', tasks: [
      { key: 'blend', route: 'blend', icon: '⚗', title: 'Blend a batch', detail: 'Pick the work order; the recipe and the tanks are already known.' },
      ...own.map((d) => ({
        key: `batch-${d.department_id}`, route: `batches/${d.department_id}`, icon: '⚗',
        title: `Run ${/^[aeiou]/i.test(d.description) ? 'an' : 'a'} ${d.description.toLowerCase()} batch`,
        detail: d.methods?.includes('staged')
          ? 'Soap in, acid in, mix, settle, draw off — the reactor and its clock, stage by stage.'
          : `The ${d.description.toLowerCase()} department's recipe, charge and yield, worked out for you.`,
      })),
      { key: 'produce', job: 'produce', icon: '⚙', title: 'Made product without a recipe', detail: 'Something went in and something else came out, on a work order.' },
    ]},
    { title: 'Tanks', tasks: [
      { key: 'move', job: 'move', icon: '⇄', title: 'Product moved to another tank', detail: 'Pumped from one tank to another. Same product, same pounds.' },
      { key: 'shrink', job: 'shrink', icon: '↘', title: 'Product lost or written off', detail: 'A heel, a line flush, a spill or a sample that will not come back.' },
    ]},
  ]
  // The device's department's own batch job goes to the front of the line.
  if (department) {
    for (const group of groups) {
      group.tasks.sort((a, b) => Number(b.route === `batches/${department.department_id}`) - Number(a.route === `batches/${department.department_id}`))
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>What happened?</h1>
          <div className="sub">Pick the one that matches. Each asks a few questions on one page — nothing else.</div>
        </div>
        <div className="actions"><button onClick={() => navigate('today')}>Back to Today</button></div>
      </div>
      {groups.map((group) => (
        <section key={group.title} className="task-group">
          <h2>{group.title}</h2>
          <div className="task-grid">
            {group.tasks.map((task) => (
              <button
                key={task.key}
                className="task-card"
                onClick={() => navigate(task.job ? `operations/${task.job}` : task.route!)}
              >
                <span className="task-icon" aria-hidden="true">{task.icon}</span>
                <span className="task-text">
                  <strong>{task.title}</strong>
                  <span>{task.detail}</span>
                </span>
              </button>
            ))}
          </div>
        </section>
      ))}
    </>
  )
}

function JobPage({ title, children }: { title: string; children: React.ReactNode }) {
  const { navigate, plantCode } = useApp()
  return (
    <>
      <div className="page-head">
        <div>
          <h1>{title} — {plantCode}</h1>
          <div className="sub">Answer down the page. The button at the bottom says exactly what will be recorded.</div>
        </div>
        <div className="actions">
          <button onClick={() => navigate('operations')}>← Something else happened</button>
          <button onClick={() => navigate('today')}>Back to Today</button>
        </div>
      </div>
      {children}
    </>
  )
}

/* ------------------------------------------------------------ shared parts */

export function Step({ n, title, hint, children }: { n: number; title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="pf-step">
      <header>
        <span className="flow-n" aria-hidden="true">{n}</span>
        <h2>{title}</h2>
        {hint && <span className="pf-hint">{hint}</span>}
      </header>
      <div className="pf-body">{children}</div>
    </section>
  )
}

/** The plant's tanks, with material ids alongside the tile's product numbers. */
export function useTanks() {
  const { plantId, reference } = useApp()
  const board = useAsync(() => api.get<TankBoardData>(`/api/display/tanks?plant_id=${plantId}`), [plantId])
  const ids = useMemo(() => new Map(reference.materials.map((m) => [m.number, m.material_id])), [reference.materials])
  const tiles = board.data?.tanks ?? []
  const holds = (tile: TankTile, materialId: number | null | undefined) =>
    tile.products.find((p) => p.lbs > 0.5 && ids.get(p.number) === materialId)?.lbs ?? 0
  const mainProduct = (tile: TankTile) => {
    const top = tile.products.find((p) => p.lbs > 0.5)
    return top ? { ...top, material_id: ids.get(top.number) ?? null } : null
  }
  return { board, tiles, holds, mainProduct }
}

/* A heel of another product is normal; more than this and mixing is a
 * decision for a person, so the tile says so (the ledger does not forbid it). */
const MIX_WARN_LBS = 2_000

export function TankPicker({
  tiles, value, onChange, mode, materialId, qty, holds, mainProduct, exclude, suggested,
}: {
  tiles: TankTile[]
  value: number | null
  onChange: (locationId: number) => void
  mode: 'from' | 'to'
  materialId?: number | null
  qty?: number
  holds: (tile: TankTile, materialId: number | null | undefined) => number
  mainProduct: (tile: TankTile) => ({ number: string; description: string; lbs: number; material_id: number | null }) | null
  exclude?: number | null
  suggested?: number | null
}) {
  const [showAll, setShowAll] = useState(false)
  let list = tiles.filter((t) => t.location_id !== exclude)
  if (mode === 'from') {
    list = list.filter((t) => (materialId ? holds(t, materialId) > 0.5 : t.total > 0.5))
  } else if (materialId) {
    // Tanks already holding this product first, then empty storage tanks,
    // then the rest. An empty blend tank or acid reactor is a process vessel,
    // not somewhere to store product, so it is not offered up front.
    const rank = (t: TankTile) => (
      holds(t, materialId) > 0.5 ? 0 : t.kind !== 'Tank' ? 3 : t.total <= 0.5 ? 1 : 2
    )
    const ranked = [...list].sort((a, b) => rank(a) - rank(b) || a.number.localeCompare(b.number))
    const likely = ranked.filter((t) => rank(t) < 2 || t.location_id === value)
    // Nothing obvious: show every tank rather than an empty list.
    list = showAll || likely.length === 0 ? ranked : likely
  }
  const hidden = mode === 'to' && materialId && !showAll
    ? tiles.filter((t) => t.location_id !== exclude).length - list.length : 0

  if (!tiles.length) return <Loading />
  if (!list.length) {
    return <div className="muted">{mode === 'from' ? 'No tank at this plant holds that product.' : 'No tank to choose.'}</div>
  }
  return (
    <>
      <div className="tank-pick" role="radiogroup">
        {list.map((tile) => {
          const product = mainProduct(tile)
          const noRoom = mode === 'to' && tile.room !== null && qty !== undefined && qty > tile.room + 0.5
          const otherProduct = mode === 'to' && materialId && product && product.material_id !== materialId
            && product.lbs > MIX_WARN_LBS
          const selected = value === tile.location_id
          return (
            <button
              key={tile.location_id}
              role="radio"
              aria-checked={selected}
              className={`tank-option${selected ? ' selected' : ''}${otherProduct ? ' caution' : ''}`}
              disabled={noRoom}
              onClick={() => onChange(tile.location_id)}
            >
              <span className="mini-gauge"><span style={{ height: `${Math.max(0, Math.min(100, tile.percent_full ?? 0))}%` }} /></span>
              <span className="tank-option-text">
                <strong>{tile.number}{selected ? ' ✓' : ''}</strong>
                <span>{product ? `${product.number} · ${product.description}` : 'Empty'}</span>
                <span className="muted">
                  {mode === 'from' && materialId
                    ? `${fmtLbs(holds(tile, materialId))} lbs on hand`
                    : `${fmtLbs(tile.total)} lbs${tile.room !== null ? ` · room ${fmtLbs(Math.max(tile.room, 0))}` : ''}`}
                </span>
                {noRoom && <span className="tank-flag">not enough room</span>}
                {otherProduct && <span className="tank-flag">holds a different product</span>}
                {suggested === tile.location_id && !selected && <span className="tank-flag ok">suggested</span>}
              </span>
            </button>
          )
        })}
      </div>
      {hidden > 0 && (
        <button className="ghost sm" onClick={() => setShowAll(true)}>
          Show {hidden} tank{hidden === 1 ? '' : 's'} holding other products
        </button>
      )}
    </>
  )
}

export function Qty({
  value, onChange, max, maxLabel, reading, onUseReading,
}: {
  value: string
  onChange: (value: string) => void
  max?: number
  maxLabel?: string
  reading?: { net_lbs: number | null; gross_lbs: number | null; trailer_number: string; captured_at: string } | null
  onUseReading?: () => void
}) {
  const weight = reading ? reading.net_lbs ?? reading.gross_lbs : null
  return (
    <div className="pf-qty">
      <div className="row" style={{ gap: 10, alignItems: 'center' }}>
        <input
          type="number"
          inputMode="decimal"
          aria-label="Pounds"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          style={{ maxWidth: 220, fontSize: 20 }}
        />
        <span className="muted">lbs</span>
        {max !== undefined && max > 0 && (
          <button className="sm" onClick={() => onChange(String(Math.round(max * 100) / 100))}>
            {maxLabel ?? 'All of it'} ({fmtLbs(max)})
          </button>
        )}
      </div>
      {weight && onUseReading && Number(value) !== weight && (
        <div className="small muted" style={{ marginTop: 6 }}>
          Scale: {fmtLbs(weight)} lbs{reading!.trailer_number ? ` · trailer ${reading!.trailer_number}` : ''},
          {' '}weighed {fmtDateTime(reading!.captured_at)}.{' '}
          <button className="sm" onClick={onUseReading}>Use the scale weight</button>
        </div>
      )}
      {max !== undefined && Number(value) > max + 0.01 && (
        <div style={{ marginTop: 8 }}>
          <Alert tone="warn" title="More than is there">
            {fmtLbs(max)} lbs on hand — this will be refused.
          </Alert>
        </div>
      )}
    </div>
  )
}

function MoreDetails({
  form, set, hours = false, bol = false,
}: { form: Record<string, any>; set: (patch: Record<string, any>) => void; hours?: boolean; bol?: boolean }) {
  return (
    <details className="pf-more">
      <summary>More details — date, {bol ? 'BOL, ' : ''}{hours ? 'hours, ' : ''}remarks</summary>
      <div className="form-grid cols-2" style={{ marginTop: 10 }}>
        <Field label="Date it happened" hint="Today unless you are catching up">
          <input type="date" value={form.user_date ?? today()} onChange={(e) => set({ user_date: e.target.value })} />
        </Field>
        {bol && (
          <Field label="BOL" hint="Leave blank and one is assigned">
            <input value={form.to_bol ?? ''} onChange={(e) => set({ to_bol: e.target.value })} />
          </Field>
        )}
        {hours && (
          <>
            <Field label="Tank time (hrs)">
              <input type="number" step="0.1" value={form.tank_hours ?? ''} onChange={(e) => set({ tank_hours: e.target.value })} />
            </Field>
            <Field label="Labour (hrs)">
              <input type="number" step="0.1" value={form.employee_hours ?? ''} onChange={(e) => set({ employee_hours: e.target.value })} />
            </Field>
          </>
        )}
        <Field label="Remarks" className="span-2">
          <input value={form.remarks ?? ''} onChange={(e) => set({ remarks: e.target.value })} />
        </Field>
      </div>
    </details>
  )
}

/** Post a job, show what happened, and offer to undo it. */
function usePost(job: Job) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<any>(null)
  const [posted, setPosted] = useState<Transaction | null>(null)
  const key = useRef(newKey())
  async function post(payload: Record<string, any>) {
    setBusy(true); setError(null)
    try {
      const clean: Record<string, any> = {}
      for (const [k, v] of Object.entries(payload)) clean[k] = v === '' ? null : v
      const txn = await api.post<Transaction>(`/api/transactions/${job}`, { ...clean, idempotency_key: key.current })
      key.current = newKey()
      setPosted(txn)
      toast.push('success', 'Recorded', `Transaction ${txn.transaction_id}`)
    } catch (err) { setError(err) } finally { setBusy(false) }
  }
  return { busy, error, posted, post, reset: () => { setPosted(null); setError(null) } }
}

function Done({ txn, sentence, onAnother }: { txn: Transaction; sentence: string; onAnother: () => void }) {
  const { navigate } = useApp()
  const toast = useToast()
  const [undone, setUndone] = useState(false)
  const [busy, setBusy] = useState(false)
  async function undo() {
    setBusy(true)
    try {
      await api.post(`/api/transactions/${txn.transaction_id}/void`, { reason: 'Undone straight after recording it' })
      setUndone(true)
      toast.push('info', 'Undone', `Transaction ${txn.transaction_id} reversed`)
    } catch (err) {
      toast.push('error', 'Could not undo', (err as Error).message)
    } finally { setBusy(false) }
  }
  return (
    <Card title={undone ? 'Undone' : 'Recorded'}>
      <Alert tone={undone ? 'info' : 'ok'} title={sentence}>
        {undone
          ? 'The ledger now shows it and its reversal, so the balances are back as they were.'
          : `Transaction ${txn.transaction_id}${txn.to_bol ? ` · BOL ${txn.to_bol}` : ''}. Wrong? Undo it now — nothing is ever deleted, a reversal is written.`}
      </Alert>
      <div className="step-actions">
        {!undone && <button disabled={busy} onClick={undo}>Undo this</button>}
        <button onClick={onAnother}>Record another</button>
        <button className="primary" onClick={() => navigate('today')}>Back to Today</button>
      </div>
    </Card>
  )
}

function useScale(trailer = '') {
  const { plantId } = useApp()
  const [reading, setReading] = useState<any>(null)
  useEffect(() => {
    let cancelled = false
    api.get<{ reading: any }>(`/api/scale/latest${qs({ plant_id: plantId, trailer_number: trailer })}`)
      .then((r) => { if (!cancelled) setReading(r.reading) })
      .catch(() => undefined)
    return () => { cancelled = true }
  }, [plantId, trailer])
  return reading
}

const materialLabel = (reference: any, id: number | null | undefined) => {
  const m = reference.materials.find((row: any) => row.material_id === id)
  return m ? `${m.number} ${m.description}` : 'product'
}
const tankNumber = (tiles: TankTile[], id: number | null) => tiles.find((t) => t.location_id === id)?.number ?? '—'

/* ------------------------------------------------------------ a delivery */

function ReceiveJob({ orderId }: { orderId?: number }) {
  const { plantId, reference } = useApp()
  const { tiles, holds, mainProduct } = useTanks()
  const orders = useAsync(
    () => api.get<{ rows: Order[] }>(`/api/orders${qs({ plant_id: plantId, order_type_id: 3, open_only: true, limit: 200 })}`),
    [plantId],
  )
  const [picked, setPicked] = useState<number | null>(orderId ?? null)
  const [tank, setTank] = useState<number | null>(null)
  const [qty, setQty] = useState('')
  // Soap, acid and blend ingredients come in on trucks and on railcars; which,
  // and its number, goes on the receipt — the car number is how a quality
  // problem is traced back to the supplier.
  const [conveyance, setConveyance] = useState<'Truck' | 'Railcar'>('Truck')
  const [vehicle, setVehicle] = useState('')
  const [form, setForm] = useState<Record<string, any>>({ user_date: today() })
  const set = (patch: Record<string, any>) => setForm((current) => ({ ...current, ...patch }))
  const { busy, error, posted, post, reset } = usePost('receive')
  const reading = useScale()

  const open = (orders.data?.rows ?? []).filter((o) => o.material_one_quantity - o.qty_fulfilled > 0.5)
    .sort((a, b) => a.due_date.localeCompare(b.due_date))
  const order = (orders.data?.rows ?? []).find((o) => o.order_id === picked) ?? null
  const materialId = order?.material_one_id ?? null
  const remaining = order ? Math.max(order.material_one_quantity - order.qty_fulfilled, 0) : 0

  // The tank already holding this product, with the most room, is the
  // obvious one; failing that, an empty tank. Suggested, never forced.
  const need = Number(qty || 0)
  const suggested = useMemo(() => {
    if (!materialId) return null
    // Only a tank that can take the whole delivery is suggested.
    const fits = (t: TankTile) => t.room === null || t.room + 0.5 >= need
    const same = tiles.filter((t) => holds(t, materialId) > 0.5 && fits(t)).sort((a, b) => (b.room ?? 0) - (a.room ?? 0))
    const empty = tiles.filter((t) => t.total <= 0.5 && t.kind === 'Tank' && fits(t))
    return (same[0] ?? empty[0])?.location_id ?? null
  }, [tiles, materialId, need])
  useEffect(() => {
    if (!order) return
    setQty(String(Math.round(remaining * 100) / 100))
    setTank(null)
    setConveyance(/rail/i.test(order.ship_method || '') ? 'Railcar' : 'Truck')
  }, [picked, orders.data])
  // One truck expected: that is the one that arrived.
  useEffect(() => { if (picked === null && open.length === 1) setPicked(open[0].order_id) }, [orders.data])
  useEffect(() => {
    const current = tiles.find((t) => t.location_id === tank)
    // Follow the suggestion until the operator picks, and drop a tank that
    // can no longer take the pounds typed.
    if (tank === null || (current && current.room !== null && current.room + 0.5 < need)) setTank(suggested)
  }, [suggested])

  if (posted) {
    return <Done txn={posted}
      sentence={`${fmtLbs(posted.to_qty)} lbs of ${posted.to_material_number} received into ${posted.to_location_number}`
        + ` off ${conveyance.toLowerCase()}${posted.trailer_number ? ` ${posted.trailer_number}` : ''}`}
      onAnother={() => { reset(); setPicked(null); setTank(null); setQty(''); setVehicle('') }} />
  }

  const n = Number(qty || 0)
  return (
    <Card>
      {error && <div style={{ marginBottom: 12 }}><ErrorBox error={error} /></div>}
      <Step n={1} title="Which delivery?" hint="The purchase orders still expecting product">
        {orders.loading ? <Loading /> : open.length === 0 ? (
          <Alert tone="info" title="No deliveries are expected at this plant">
            A delivery is received against its purchase order. Ask the office to raise one — it
            then appears here.
          </Alert>
        ) : (
          <div className="choice-list" role="radiogroup">
            {open.map((o) => (
              <button key={o.order_id} role="radio" aria-checked={picked === o.order_id}
                className={`choice${picked === o.order_id ? ' selected' : ''}`}
                onClick={() => setPicked(o.order_id)}>
                <strong>{o.vendor_name ?? 'Vendor'}{picked === o.order_id ? ' ✓' : ''}</strong>
                <span>{o.material_one_number} · {o.material_one_description}</span>
                <span className="muted">
                  {fmtLbs(o.material_one_quantity - o.qty_fulfilled)} lbs expected
                  {/rail/i.test(o.ship_method || '') ? ' by rail' : /truck/i.test(o.ship_method || '') ? ' by truck' : ''}
                  {' '}· PO {o.order_id} · due {fmtDate(o.due_date)}
                </span>
              </button>
            ))}
          </div>
        )}
      </Step>
      {order && (
        <>
          <Step n={2} title="Truck or railcar?">
            <div className="row" style={{ gap: 8 }}>
              {(['Truck', 'Railcar'] as const).map((c) => (
                <button key={c} className={conveyance === c ? 'primary' : ''} aria-pressed={conveyance === c}
                  onClick={() => setConveyance(c)}>{c}</button>
              ))}
              <input
                aria-label={`${conveyance} number`}
                placeholder={conveyance === 'Railcar' ? 'Railcar number, e.g. UTLX 204417' : 'Trailer number'}
                value={vehicle}
                onChange={(e) => setVehicle(e.target.value)}
                style={{ maxWidth: 280 }}
              />
            </div>
          </Step>
          <Step n={3} title="Into which tank?" hint={`Tanks holding ${order.material_one_number} come first`}>
            <TankPicker tiles={tiles} value={tank} onChange={setTank} mode="to" materialId={materialId}
              qty={n} holds={holds} mainProduct={mainProduct} suggested={suggested} />
            {n > 0 && !suggested && tiles.length > 0 && (
              <div className="small muted" style={{ marginTop: 8 }}>
                No storage tank has room for all {fmtLbs(n)} lbs. Receive part of it into one tank now
                and the rest into another — or, for soap, put it straight into a reactor from the batch on the acid screen.
              </div>
            )}
          </Step>
          <Step n={4} title="How much came in?">
            <Qty value={qty} onChange={setQty} reading={reading}
              onUseReading={() => setQty(String(reading.net_lbs ?? reading.gross_lbs))} />
          </Step>
          <MoreDetails form={form} set={set} bol />
          <div className="pf-go">
            <button className="primary big" disabled={busy || !tank || n <= 0}
              onClick={() => post({
                order_id: order.order_id, plant_id: plantId, to_location_id: tank, to_material_id: materialId,
                to_qty: n, ...form,
                trailer_number: vehicle.trim(),
                remarks: [`${conveyance}${vehicle.trim() ? ` ${vehicle.trim()}` : ''}`, form.remarks].filter(Boolean).join(' — '),
                scale_reading_id: reading && n === (reading.net_lbs ?? reading.gross_lbs) ? reading.reading_id : null,
              })}>
              {busy ? <span className="spinner" /> : null}
              Receive {fmtLbs(n)} lbs of {materialLabel(reference, materialId)} off the {conveyance.toLowerCase()} into {tankNumber(tiles, tank)}
            </button>
          </div>
        </>
      )}
    </Card>
  )
}

/* ----------------------------------------------------------- tank to tank */

function MoveJob() {
  const { plantId, reference } = useApp()
  const { tiles, holds, mainProduct, board } = useTanks()
  const [from, setFrom] = useState<number | null>(null)
  const [materialId, setMaterialId] = useState<number | null>(null)
  const [to, setTo] = useState<number | null>(null)
  const [qty, setQty] = useState('')
  const [form, setForm] = useState<Record<string, any>>({ user_date: today() })
  const set = (patch: Record<string, any>) => setForm((current) => ({ ...current, ...patch }))
  const { busy, error, posted, post, reset } = usePost('move')

  const source = tiles.find((t) => t.location_id === from)
  const inSource = (source?.products ?? []).filter((p) => p.lbs > 0.5)
  // A tank holds one product; when it holds more, the operator says which.
  useEffect(() => {
    if (!source) return
    const top = mainProduct(source)
    setMaterialId(top?.material_id ?? null)
    setTo(null)
  }, [from])
  const available = source && materialId ? holds(source, materialId) : 0
  const n = Number(qty || 0)

  if (posted) {
    return <Done txn={posted}
      sentence={`${fmtLbs(posted.from_qty)} lbs of ${posted.from_material_number} moved from ${posted.from_location_number} to ${posted.to_location_number}`}
      onAnother={() => { reset(); setFrom(null); setTo(null); setQty(''); board.reload() }} />
  }
  return (
    <Card>
      {error && <div style={{ marginBottom: 12 }}><ErrorBox error={error} /></div>}
      <Step n={1} title="From which tank?">
        <TankPicker tiles={tiles} value={from} onChange={setFrom} mode="from" holds={holds} mainProduct={mainProduct} />
      </Step>
      {source && inSource.length > 1 && (
        <Step n={2} title="Which product?" hint={`${source.number} holds more than one`}>
          <div className="row" style={{ gap: 8 }}>
            {inSource.map((p) => {
              const id = reference.materials.find((m) => m.number === p.number)?.material_id ?? null
              return (
                <button key={p.number} className={materialId === id ? 'primary' : ''} onClick={() => setMaterialId(id)}>
                  {p.number} · {fmtLbs(p.lbs)} lbs
                </button>
              )
            })}
          </div>
        </Step>
      )}
      {source && materialId && (
        <>
          <Step n={inSource.length > 1 ? 3 : 2} title="To which tank?" hint={`Tanks holding ${materialLabel(reference, materialId)} come first`}>
            <TankPicker tiles={tiles} value={to} onChange={setTo} mode="to" materialId={materialId} qty={n}
              holds={holds} mainProduct={mainProduct} exclude={from} />
          </Step>
          <Step n={inSource.length > 1 ? 4 : 3} title="How much moved?">
            <Qty value={qty} onChange={setQty} max={available} />
          </Step>
          <MoreDetails form={form} set={set} hours />
          <div className="pf-go">
            <button className="primary big" disabled={busy || !to || n <= 0 || n > available + 0.01}
              onClick={() => post({
                plant_id: plantId, from_location_id: from, from_material_id: materialId, from_qty: n,
                to_location_id: to, to_material_id: materialId, to_qty: n, ...form,
              })}>
              {busy ? <span className="spinner" /> : null}
              Move {fmtLbs(n)} lbs of {materialLabel(reference, materialId)} from {source.number} to {tankNumber(tiles, to)}
            </button>
          </div>
        </>
      )}
    </Card>
  )
}

/* ------------------------------------------------------------- write-off */

const LOSS_REASONS = ['Tank heel', 'Line flush', 'Spill', 'Sample', 'Other']

function ShrinkJob() {
  const { plantId, reference } = useApp()
  const { tiles, holds, mainProduct, board } = useTanks()
  const [from, setFrom] = useState<number | null>(null)
  const [materialId, setMaterialId] = useState<number | null>(null)
  const [qty, setQty] = useState('')
  const [reason, setReason] = useState('')
  const [form, setForm] = useState<Record<string, any>>({ user_date: today() })
  const set = (patch: Record<string, any>) => setForm((current) => ({ ...current, ...patch }))
  const { busy, error, posted, post, reset } = usePost('shrink')

  const source = tiles.find((t) => t.location_id === from)
  useEffect(() => { if (source) setMaterialId(mainProduct(source)?.material_id ?? null) }, [from])
  const available = source && materialId ? holds(source, materialId) : 0
  const n = Number(qty || 0)

  if (posted) {
    return <Done txn={posted}
      sentence={`${fmtLbs(posted.from_qty)} lbs of ${posted.from_material_number} written off from ${posted.from_location_number}`}
      onAnother={() => { reset(); setFrom(null); setQty(''); setReason(''); board.reload() }} />
  }
  return (
    <Card>
      {error && <div style={{ marginBottom: 12 }}><ErrorBox error={error} /></div>}
      <Step n={1} title="Which tank?">
        <TankPicker tiles={tiles} value={from} onChange={setFrom} mode="from" holds={holds} mainProduct={mainProduct} />
      </Step>
      {source && materialId && (
        <>
          <Step n={2} title="How much was lost?" hint={`${materialLabel(reference, materialId)} in ${source.number}`}>
            <Qty value={qty} onChange={setQty} max={available} maxLabel="The whole heel" />
          </Step>
          <Step n={3} title="Why?">
            <div className="row" style={{ gap: 8 }}>
              {LOSS_REASONS.map((r) => (
                <button key={r} className={reason === r ? 'primary' : ''} aria-pressed={reason === r} onClick={() => setReason(r)}>{r}</button>
              ))}
            </div>
          </Step>
          <MoreDetails form={form} set={set} />
          <div className="pf-go">
            <button className="primary big" disabled={busy || n <= 0 || n > available + 0.01 || !reason}
              onClick={() => post({
                plant_id: plantId, from_location_id: from, from_material_id: materialId, from_qty: n,
                ...form, remarks: [reason, form.remarks].filter(Boolean).join(' — '),
              })}>
              {busy ? <span className="spinner" /> : null}
              Write off {fmtLbs(n)} lbs of {materialLabel(reference, materialId)} from {source.number}
            </button>
          </div>
        </>
      )}
    </Card>
  )
}

/* ------------------------------------------------- production, no recipe */

function ProduceJob({ orderId }: { orderId?: number }) {
  const { plantId, reference } = useApp()
  const { tiles, holds, mainProduct, board } = useTanks()
  const orders = useAsync(
    () => api.get<{ rows: Order[] }>(`/api/orders${qs({ plant_id: plantId, order_type_id: 2, open_only: true, limit: 200 })}`),
    [plantId],
  )
  const recipes = useAsync(() => api.get<any[]>('/api/blend/recipes'), [])
  const withRecipe = new Set((recipes.data ?? []).map((r) => r.material_id))
  const [picked, setPicked] = useState<number | null>(orderId ?? null)
  const [from, setFrom] = useState<number | null>(null)
  const [to, setTo] = useState<number | null>(null)
  const [used, setUsed] = useState('')
  const [made, setMade] = useState('')
  const [form, setForm] = useState<Record<string, any>>({ user_date: today() })
  const set = (patch: Record<string, any>) => setForm((current) => ({ ...current, ...patch }))
  const { busy, error, posted, post, reset } = usePost('produce')

  // Orders with a recipe run on their batch screen; this is for the rest.
  const open = (orders.data?.rows ?? [])
    .filter((o) => o.material_one_quantity - o.qty_fulfilled > 0.5 && !withRecipe.has(o.material_one_id))
  const order = (orders.data?.rows ?? []).find((o) => o.order_id === picked) ?? null
  const source = tiles.find((t) => t.location_id === from)
  const input = source ? mainProduct(source) : null
  const available = source && input ? holds(source, input.material_id) : 0
  const nUsed = Number(used || 0)
  const nMade = Number(made || used || 0)

  if (posted) {
    return <Done txn={posted}
      sentence={`${fmtLbs(posted.to_qty)} lbs of ${posted.to_material_number} made into ${posted.to_location_number} from ${fmtLbs(posted.from_qty)} lbs of ${posted.from_material_number}`}
      onAnother={() => { reset(); setPicked(null); setFrom(null); setTo(null); setUsed(''); setMade(''); board.reload() }} />
  }
  return (
    <Card>
      {error && <div style={{ marginBottom: 12 }}><ErrorBox error={error} /></div>}
      <Step n={1} title="Which work order?" hint="Orders with a recipe are run from their batch screen instead">
        {orders.loading ? <Loading /> : open.length === 0 ? (
          <div className="muted">No open work orders without a recipe at this plant.</div>
        ) : (
          <div className="choice-list" role="radiogroup">
            {open.map((o) => (
              <button key={o.order_id} role="radio" aria-checked={picked === o.order_id}
                className={`choice${picked === o.order_id ? ' selected' : ''}`} onClick={() => setPicked(o.order_id)}>
                <strong>{o.material_one_number} · {o.material_one_description}{picked === o.order_id ? ' ✓' : ''}</strong>
                <span className="muted">{fmtLbs(o.material_one_quantity - o.qty_fulfilled)} lbs to make · WO {o.order_id} · due {fmtDate(o.due_date)}</span>
              </button>
            ))}
          </div>
        )}
      </Step>
      {order && (
        <>
          <Step n={2} title="What went in — from which tank?">
            <TankPicker tiles={tiles} value={from} onChange={setFrom} mode="from" holds={holds} mainProduct={mainProduct} />
          </Step>
          {source && input && (
            <>
              <Step n={3} title="How much went in?" hint={`${input.number} ${input.description}`}>
                <Qty value={used} onChange={setUsed} max={available} />
              </Step>
              <Step n={4} title={`Where did the ${order.material_one_number} go?`}>
                <TankPicker tiles={tiles} value={to} onChange={setTo} mode="to" materialId={order.material_one_id}
                  qty={nMade} holds={holds} mainProduct={mainProduct} exclude={from} />
              </Step>
              <Step n={5} title="How much came out?" hint="Leave it if nothing was lost">
                <Qty value={made || used} onChange={setMade} />
              </Step>
              <MoreDetails form={form} set={set} hours />
              <div className="pf-go">
                <button className="primary big" disabled={busy || !to || nUsed <= 0 || nMade <= 0 || nUsed > available + 0.01}
                  onClick={() => post({
                    order_id: order.order_id, plant_id: plantId,
                    from_location_id: from, from_material_id: input.material_id, from_qty: nUsed,
                    to_location_id: to, to_material_id: order.material_one_id, to_qty: nMade, ...form,
                  })}>
                  {busy ? <span className="spinner" /> : null}
                  Make {fmtLbs(nMade)} lbs of {materialLabel(reference, order.material_one_id)} into {tankNumber(tiles, to)}
                </button>
              </div>
            </>
          )}
        </>
      )}
    </Card>
  )
}

/* ------------------------------------------------------------ ship trailer */

function ShipTrailer() {
  const { plantId, navigate } = useApp()
  const toast = useToast()
  const [busy, setBusy] = useState<number | null>(null)
  const [bol, setBol] = useState<any>(null)
  // Every row here is a different truck and the buttons are identical, so the
  // one that is about to leave is named before it does.
  const [confirming, setConfirming] = useState<PendingShipment | null>(null)

  const staged = useAsync(
    () => api.get<PendingShipment[]>(`/api/shipments/pending?plant_id=${plantId}`), [plantId],
  )

  async function ship(stage: PendingShipment) {
    setBusy(stage.stage_id)
    setConfirming(null)
    try {
      await api.post(`/api/shipments/${stage.stage_id}/ship`, {})
      toast.push('success', `Trailer ${stage.trailer_number} shipped`, `Order ${stage.order_id}`)
      staged.reload()
    } catch (error) {
      toast.push('error', 'Not shipped', (error as Error).message)
    } finally { setBusy(null) }
  }

  async function preview(stage: PendingShipment) {
    // The BOL for this trailer, not for every load on the order.
    try { setBol(await api.get(`/api/orders/${stage.order_id}/bol${qs({ transaction_id: stage.transaction_id })}`)) }
    catch (error) { toast.push('error', 'Could not build the BOL', (error as Error).message) }
  }

  return (
    <div className="grid cols-2">
      <Card title="Staged trailers" subtitle="Loaded and waiting to ship" tight>
        {confirming && (
          <div style={{ margin: '0 0 12px' }}>
            <Alert tone="warn" title={`Ship trailer ${confirming.trailer_number || '—'}?`}>
              {fmtLbs(confirming.quantity)} lbs of {confirming.material_number}{' '}
              {confirming.material_description} on BOL {confirming.bol_number}, order{' '}
              {confirming.order_id} for {confirming.customer_name ?? '—'}.
              <div className="row" style={{ gap: 8, marginTop: 10 }}>
                <button
                  className="sm primary"
                  disabled={busy === confirming.stage_id}
                  onClick={() => ship(confirming)}
                >
                  Yes, ship trailer {confirming.trailer_number || '—'}
                </button>
                <button className="sm" onClick={() => setConfirming(null)}>Cancel</button>
              </div>
            </Alert>
          </div>
        )}
        {staged.loading ? <Loading /> : (
          <DataTable
            rows={staged.data ?? []}
            rowKey={(row) => row.stage_id}
            empty="Nothing staged at this plant."
            columns={[
              { key: 'order_id', label: 'Order' },
              { key: 'customer_name', label: 'Customer' },
              { key: 'bol_number', label: 'BOL #' },
              { key: 'trailer_number', label: 'Trailer' },
              {
                key: 'material_number',
                label: 'Product',
                render: (row) => (
                  <span>
                    {row.material_number}
                    {row.order_material_number && row.order_material_number !== row.material_number && (
                      <Badge tone="warn">not the ordered product</Badge>
                    )}
                  </span>
                ),
              },
              { key: 'quantity', label: 'Lbs', numeric: true, render: (row) => fmtLbs(row.quantity) },
              { key: 'loaded_at', label: 'Loaded', render: (row) => fmtDateTime(row.loaded_at) },
              {
                key: 'actions',
                label: '',
                render: (row) => (
                  <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
                    <button className="sm" onClick={() => preview(row)}>BOL</button>
                    <button
                      className="primary sm"
                      disabled={busy === row.stage_id}
                      onClick={() => setConfirming(row)}
                    >
                      Ship {row.trailer_number || ''}
                    </button>
                  </div>
                ),
              },
            ]}
          />
        )}
      </Card>

      <Card title="Bill of lading" subtitle={bol ? `Order ${bol.order.order_id}` : 'Select a trailer to preview'}>
        {!bol ? (
          <div className="muted">
            The BOL carries the shipper details, the loads on the trailer, and the
            sample and seal numbers from QC.
          </div>
        ) : (
          <div className="stack">
            <div>
              <strong>{bol.shipper.name}</strong>
              <div className="small muted">{bol.shipper.address}</div>
            </div>
            <dl className="kv">
              <dt>Consigned to</dt><dd>{bol.order.customer_name || '—'}</dd>
              <dt>Order</dt><dd>{bol.order.order_id}</dd>
              <dt>Material</dt><dd>{bol.order.material_one_number} {bol.order.material_one_description}</dd>
              <dt>Total quantity</dt><dd className="num">{fmtLbs(bol.total_quantity)} lbs</dd>
            </dl>
            <div className="divider" />
            <DataTable
              rows={bol.loads}
              rowKey={(row: any) => row.transaction_id}
              empty="No loads recorded."
              columns={[
                { key: 'bol_number', label: 'BOL #' },
                { key: 'trailer_number', label: 'Trailer' },
                { key: 'quantity', label: 'Lbs', numeric: true, render: (row: any) => fmtLbs(row.quantity) },
                { key: 'loaded_by', label: 'Loaded by' },
              ]}
            />
            {bol.qc.length > 0 && (
              <div className="small muted">
                Sample #{bol.qc[0].sample_number || '—'} · Seals {bol.qc[0].seal_number || '—'}
              </div>
            )}
            <div className="row end">
              <Badge tone="info">Print via the browser</Badge>
              <button onClick={() => navigate(`orders/${bol.order.order_id}`)}>Open order</button>
              <button className="primary" onClick={() => window.print()}>Print</button>
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}
