/* Load and ship, in one flow.
 *
 * The legacy client made a loadout operator visit four screens for one truck —
 * Plant floor, the order, the QC tab, the QA tab, then Ship — and hand-type the
 * order, trailer, BOL, seals, sample number and weight in the middle of it.
 * This is the same five things in sequence, with every value the system already
 * knows filled in and the reason shown next to it. */

import { useEffect, useMemo, useRef, useState } from 'react'
import { useApp, useToast } from '../App'
import { api, newKey, qs } from '../lib/api'
import type { Balance, Order, QcValidation } from '../lib/types'
import {
  Alert, Badge, Card, DataTable, ErrorBox, Field, Loading, SpecBadge,
  fmtDate, fmtLbs, fmtNumber, today, useAsync,
} from '../components/ui'

interface Prefill {
  values: Record<string, any>
  notes: string[]
  trailer_history: Record<string, any>[]
}

interface ScaleReading {
  reading_id: number
  gross_lbs: number | null
  tare_lbs: number | null
  net_lbs: number | null
  trailer_number: string
  captured_at: string
}

/* The trailer inspection comes before the load, not after it. Four of the
 * checklist questions ask whether an *empty* trailer is clean, dry and
 * compatible with the product; asking them once the product is aboard makes
 * them paperwork rather than a check. */
const STEPS = ['Order', 'Trailer', 'Load', 'Quality', 'Sign-off', 'Ship'] as const
type Step = (typeof STEPS)[number]

/* Where a flow in progress is kept.
 *
 * Every value below used to live in React state alone, so a kiosk idle
 * sign-out at 180 seconds — or a closed laptop, or a reload — threw away the
 * fact that a truck had already been loaded. The load itself was committed:
 * product had left the tank, a BOL number was minted, a stage was waiting. The
 * operator came back to an empty screen and loaded the truck again. */
const FLOW_KEY = 'pims.flow'

interface FlowState {
  orderId: number | null
  step: Step
  loadTxn: any
  qcRecord: any
  preLoadDone: boolean
  checklistDone: boolean
  shipped: any
  startedAt: number
}

const EMPTY_FLOW: FlowState = {
  orderId: null,
  step: 'Order',
  loadTxn: null,
  qcRecord: null,
  preLoadDone: false,
  checklistDone: false,
  shipped: null,
  startedAt: 0,
}

function readFlow(): FlowState | null {
  try {
    const raw = sessionStorage.getItem(FLOW_KEY)
    if (!raw) return null
    const saved = JSON.parse(raw) as FlowState
    // A flow older than a shift is stale; do not offer to resume yesterday.
    if (!saved.orderId || Date.now() - (saved.startedAt || 0) > 12 * 3_600_000) return null
    return saved
  } catch {
    return null
  }
}

export default function LoadAndShip({ initialOrderId }: { initialOrderId?: number }) {
  const { plantCode, navigate, can } = useApp()
  const resumed = useMemo(() => (initialOrderId ? null : readFlow()), [initialOrderId])
  const [flow, setFlow] = useState<FlowState>(
    () => resumed ?? {
      ...EMPTY_FLOW,
      orderId: initialOrderId ?? null,
      step: initialOrderId ? 'Trailer' : 'Order',
      startedAt: Date.now(),
    },
  )
  const [resumeNotice, setResumeNotice] = useState(Boolean(resumed))
  const { orderId, step, loadTxn, qcRecord, preLoadDone, checklistDone, shipped } = flow

  const patch = (values: Partial<FlowState>) =>
    setFlow((current) => ({ ...current, ...values }))
  const setStep = (next: Step) => patch({ step: next })

  // Written on every change rather than on unmount: a kiosk sign-out unmounts
  // the whole tree without warning, and an unload handler does not always run.
  useEffect(() => {
    try {
      if (flow.orderId) sessionStorage.setItem(FLOW_KEY, JSON.stringify(flow))
      else sessionStorage.removeItem(FLOW_KEY)
    } catch {
      /* a full or disabled sessionStorage must not break the flow */
    }
  }, [flow])

  const order = useAsync(
    () => (orderId ? api.get<Order>(`/api/orders/${orderId}`) : Promise.resolve(null as any)),
    [orderId, loadTxn?.transaction_id, qcRecord?.qc_id, shipped?.transaction_id],
  )

  function restart() {
    try { sessionStorage.removeItem(FLOW_KEY) } catch { /* nothing to clear */ }
    setResumeNotice(false)
    setFlow({ ...EMPTY_FLOW, startedAt: Date.now() })
  }

  const done: Record<Step, boolean> = {
    Order: Boolean(orderId),
    Trailer: preLoadDone,
    Load: Boolean(loadTxn),
    Quality: Boolean(qcRecord),
    'Sign-off': checklistDone,
    Ship: Boolean(shipped),
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Load &amp; ship — {plantCode}</h1>
          <div className="sub">
            {order.data
              ? `Order ${order.data.order_id} · ${order.data.material_one_number} ${order.data.material_one_description} · ${order.data.customer_name ?? ''}`
              : 'Pick the order going out, and work down the strip.'}
          </div>
        </div>
        <div className="actions">
          {orderId && <button onClick={() => navigate(`orders/${orderId}`)}>Open order</button>}
          <button onClick={restart}>Start another</button>
        </div>
      </div>

      {resumeNotice && (
        <div style={{ marginBottom: 14 }}>
          <Alert
            tone={loadTxn ? 'warn' : 'info'}
            title={loadTxn ? 'You were part way through loading a truck' : 'Picking up where you left off'}
          >
            {loadTxn ? (
              <>
                Trailer <strong>{loadTxn.trailer_number || '—'}</strong> was loaded with{' '}
                {fmtLbs(loadTxn.from_qty)} lbs on BOL <strong>{loadTxn.to_bol}</strong>
                {shipped ? ' and has shipped.' : ' and has not shipped yet.'}{' '}
                {shipped ? '' : 'Carry on from here rather than loading it again.'}
              </>
            ) : (
              <>Order {orderId} was already selected.</>
            )}
            <div className="row" style={{ gap: 8, marginTop: 10 }}>
              <button className="sm" onClick={() => setResumeNotice(false)}>Carry on</button>
              <button className="sm" onClick={restart}>Start a different truck</button>
            </div>
          </Alert>
        </div>
      )}

      <div className="steps">
        {STEPS.map((name, i) => (
          <button
            key={name}
            className={`step${name === step ? ' active' : ''}${done[name] ? ' done' : ''}`}
            disabled={!orderId && name !== 'Order'}
            aria-current={name === step ? 'step' : undefined}
            onClick={() => setStep(name)}
          >
            <span className="n" aria-hidden="true">{done[name] ? '✓' : i + 1}</span>
            {name}
            <span className="sr-only">{done[name] ? ' — done' : ''}</span>
          </button>
        ))}
      </div>

      {step === 'Order' && (
        <PickOrder onPicked={(id) => patch({ orderId: id, step: 'Trailer', startedAt: Date.now() })} />
      )}
      {step === 'Trailer' && orderId && (
        <ChecklistStep
          key="pre"
          stage="pre_load"
          orderId={orderId}
          trailerNumber={order.data?.trailer_number ?? ''}
          done={preLoadDone}
          onDone={() => patch({ preLoadDone: true, step: 'Load' })}
          onSkip={() => setStep('Load')}
        />
      )}
      {step === 'Load' && orderId && (
        <LoadStep
          orderId={orderId}
          posted={loadTxn}
          preLoadDone={preLoadDone}
          onCheckTrailer={() => setStep('Trailer')}
          onPosted={(txn) => patch({ loadTxn: txn, step: 'Quality' })}
        />
      )}
      {step === 'Quality' && orderId && (
        <QualityStep
          orderId={orderId}
          saved={qcRecord}
          canWrite={can('qc.write')}
          onSaved={(record) => patch({ qcRecord: record, step: 'Sign-off' })}
          onSkip={() => setStep('Sign-off')}
        />
      )}
      {step === 'Sign-off' && orderId && (
        <ChecklistStep
          key="post"
          stage="post_load"
          orderId={orderId}
          trailerNumber={loadTxn?.trailer_number ?? order.data?.trailer_number ?? ''}
          done={checklistDone}
          onDone={() => patch({ checklistDone: true, step: 'Ship' })}
          onSkip={() => setStep('Ship')}
        />
      )}
      {step === 'Ship' && orderId && (
        <ShipStep
          orderId={orderId}
          loadTxn={loadTxn}
          shipped={shipped}
          onShipped={(txn) => patch({ shipped: txn })}
          onRestart={restart}
        />
      )}
    </>
  )
}

/* ------------------------------------------------------------ 1. order */

function PickOrder({ onPicked }: { onPicked: (orderId: number) => void }) {
  const { plantId } = useApp()
  const result = useAsync(
    () => api.get<{ rows: Order[] }>(
      `/api/orders${qs({ plant_id: plantId, order_type_id: 1, open_only: true, limit: 100 })}`,
    ),
    [plantId],
  )

  // Orders with product still to load come first. An order that is already
  // loaded to its full quantity is not work, and burying the real jobs under
  // a screenful of finished ones is how the wrong order gets picked.
  const rows = [...(result.data?.rows ?? [])].sort((a, b) => {
    const left = (row: Order) => row.material_one_quantity - row.qty_fulfilled
    return Number(left(b) > 0.5) - Number(left(a) > 0.5)
  })
  const ready = rows.filter((row) => row.material_one_quantity - row.qty_fulfilled > 0.5).length

  return (
    <Card
      title="Sales orders ready to load"
      subtitle={
        result.data
          ? `${ready} with product still to load · ${rows.length - ready} already loaded`
          : 'Open orders at this plant'
      }
      tight
    >
      {result.loading ? <Loading /> : result.error ? <ErrorBox error={result.error} /> : (
        <DataTable
          rows={rows}
          rowKey={(row) => row.order_id}
          onRowClick={(row) => onPicked(row.order_id)}
          empty="No open sales orders at this plant."
          columns={[
            { key: 'order_id', label: 'Order' },
            { key: 'due_date', label: 'Due', render: (row) => fmtDate(row.due_date) },
            { key: 'customer_name', label: 'Customer' },
            {
              key: 'material',
              label: 'Product',
              render: (row) => `${row.material_one_number} · ${row.material_one_description}`,
            },
            {
              key: 'remaining',
              label: 'Left to load',
              numeric: true,
              // Signed. Clamping at zero hid exactly the row that needed
              // attention: an order already loaded past what was ordered,
              // sitting in the list looking like ordinary work.
              render: (row) => {
                const left = row.material_one_quantity - row.qty_fulfilled
                if (left > 0.5) return fmtLbs(left)
                if (left < -0.5) return <Badge tone="warn">{fmtLbs(-left)} over</Badge>
                return <Badge tone="ok">fully loaded</Badge>
              },
            },
            { key: 'trailer_number', label: 'Trailer' },
            { key: 'go', label: '', render: () => <button className="sm primary">Load this</button> },
          ]}
        />
      )}
    </Card>
  )
}

/* ------------------------------------------------------------- 2. load */

function LoadStep({
  orderId, posted, preLoadDone, onCheckTrailer, onPosted,
}: {
  orderId: number
  posted: any
  preLoadDone: boolean
  onCheckTrailer: () => void
  onPosted: (txn: any) => void
}) {
  const { plantId, reference } = useApp()
  const toast = useToast()
  const [form, setForm] = useState<Record<string, any>>({})
  const [error, setError] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [reading, setReading] = useState<ScaleReading | null>(null)
  // Minted once for this form and kept until the post succeeds, so pressing
  // the button again after a dropped connection returns the transaction that
  // already exists instead of loading the truck twice.
  const idempotencyKey = useRef(newKey())

  const prefill = useAsync(
    () => api.get<Prefill>(`/api/prefill/load${qs({ order_id: orderId, plant_id: plantId })}`),
    [orderId, plantId],
  )
  const balances = useAsync(
    () => api.get<Balance[]>(`/api/balances?plant_id=${plantId}`), [plantId, posted?.transaction_id],
  )

  // What the chosen tank actually holds of the chosen product — so an
  // over-draw is caught here rather than by the server after a full form.
  const available = (balances.data ?? []).find(
    (row) => String(row.location_id) === String(form.from_location_id)
      && String(row.material_id) === String(form.from_material_id),
  )?.balance ?? 0
  const overdrawn = Number(form.from_qty || 0) > available

  useEffect(() => {
    if (prefill.data) {
      setForm((current) => ({ user_date: today(), ...prefill.data!.values, ...current }))
    }
  }, [prefill.data])

  // Offer the most recent unused weigh-out for this trailer.
  useEffect(() => {
    let cancelled = false
    api.get<{ reading: ScaleReading | null }>(
      `/api/scale/latest${qs({ plant_id: plantId, trailer_number: form.trailer_number })}`,
    )
      .then((result) => { if (!cancelled) setReading(result.reading) })
      .catch(() => undefined)
    return () => { cancelled = true }
  }, [plantId, form.trailer_number, posted?.transaction_id])

  const set = (patch: Record<string, any>) => setForm((current) => ({ ...current, ...patch }))
  const locations = reference.locations.filter((location) => location.plant_id === plantId)

  async function post(acknowledgeOverLoad = false) {
    setBusy(true); setError(null)
    try {
      const payload: Record<string, any> = { order_id: orderId, plant_id: plantId, ...form }
      // A load relocates product: the same product, the same weight. The form
      // shows one Product and one Quantity for exactly that reason.
      payload.to_material_id = payload.from_material_id
      payload.to_qty = payload.from_qty
      payload.idempotency_key = idempotencyKey.current
      if (acknowledgeOverLoad) payload.acknowledge_over_load = true
      if (reading) payload.scale_reading_id = reading.reading_id
      delete payload.bol_preview
      const txn = await api.post<any>('/api/transactions/load', payload)
      toast.push('success', `Loaded ${fmtLbs(txn.from_qty)} lbs`, `BOL ${txn.to_bol}`)
      onPosted(txn)
    } catch (err) { setError(err) } finally { setBusy(false) }
  }

  // The server refuses a load that would take the order past what was ordered
  // and says by how much. Split loads are normal, so this is a confirmation
  // rather than a wall.
  const overLoad = error?.detail?.rule === 'over_fulfilment' ? error.detail : null

  if (prefill.loading) return <Loading />

  if (posted) {
    return (
      <Card title="Loaded">
        <Alert tone="ok" title={`Transaction ${posted.transaction_id}`}>
          {fmtLbs(posted.from_qty)} lbs of {posted.from_material_number} out of{' '}
          {posted.from_location_number} onto trailer {posted.trailer_number}. BOL{' '}
          <strong>{posted.to_bol}</strong> — generated, not typed.
        </Alert>
      </Card>
    )
  }

  return (
    <div className="grid cols-2">
      <Card title="Load the trailer">
        {error && <div style={{ marginBottom: 12 }}><ErrorBox error={error} /></div>}
        <div className="form-grid cols-2">
          <Field label="From tank" error={error?.fields?.from_location_id}>
            <select value={form.from_location_id ?? ''} onChange={(e) => set({ from_location_id: e.target.value })}>
              <option value="">Select…</option>
              {locations.map((location) => (
                <option key={location.location_id} value={location.location_id}>
                  {location.number} — {location.description}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Product" error={error?.fields?.from_material_id}>
            <select value={form.from_material_id ?? ''} onChange={(e) => set({ from_material_id: e.target.value })}>
              <option value="">Select…</option>
              {reference.materials.map((material) => (
                <option key={material.material_id} value={material.material_id}>
                  {material.number} — {material.description}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Trailer #" error={error?.fields?.trailer_number}>
            <input value={form.trailer_number ?? ''} onChange={(e) => set({ trailer_number: e.target.value })} />
          </Field>
          <Field label="Transaction date">
            <input type="date" value={form.user_date ?? today()} onChange={(e) => set({ user_date: e.target.value })} />
          </Field>
          <Field
            label="Quantity (lbs)"
            error={error?.fields?.from_qty}
            className="span-2"
            hint={
              form.from_location_id && form.from_material_id
                ? `On hand: ${fmtLbs(available)} lbs${form.bol_preview ? ` · BOL ${form.bol_preview} on post` : ''}`
                : form.bol_preview ? `BOL ${form.bol_preview} will be assigned on post` : undefined
            }
          >
            <input
              type="number"
              inputMode="decimal"
              value={form.from_qty ?? ''}
              onChange={(e) => set({ from_qty: e.target.value })}
            />
          </Field>
        </div>

        {overdrawn && (
          <div style={{ marginTop: 12 }}>
            <Alert tone="warn" title="More than that tank holds">
              {fmtLbs(available)} lbs of this product on hand. Pick another tank, or load less —
              this will be refused when posted.
            </Alert>
          </div>
        )}

        {overLoad && (
          <div style={{ marginTop: 12 }}>
            <Alert tone="warn" title={`This puts the order ${fmtLbs(overLoad.over_by)} lbs over`}>
              {fmtLbs(overLoad.already_loaded)} lbs of {fmtLbs(overLoad.ordered)} lbs are already
              loaded. Split loads are normal — confirm if this one is meant to go out.
              <div className="row" style={{ gap: 8, marginTop: 10 }}>
                <button className="sm primary" disabled={busy} onClick={() => post(true)}>
                  Yes, post it anyway
                </button>
              </div>
            </Alert>
          </div>
        )}

        {!preLoadDone && (
          <div style={{ marginTop: 12 }}>
            <Alert tone="warn" title="The trailer has not been checked">
              The clean, dry and previous-load questions are about an empty trailer, so they
              are asked before the product goes in.
              <div className="row" style={{ gap: 8, marginTop: 10 }}>
                <button className="sm primary" onClick={onCheckTrailer}>Check the trailer</button>
              </div>
            </Alert>
          </div>
        )}

        {prefill.data?.notes.length ? (
          <div className="why">
            {prefill.data.notes.map((note, i) => <span key={i}>{note}</span>)}
          </div>
        ) : null}

        <div className="row end" style={{ marginTop: 16 }}>
          <button
            className="primary"
            onClick={() => post()}
            disabled={busy || overdrawn || !form.from_qty || !preLoadDone}
          >
            {busy ? <span className="spinner" /> : null} Post load
          </button>
        </div>
      </Card>

      <div className="stack">
        <Card title="Truck scale">
          {reading ? (
            <div className="stack">
              <div>
                <div className="readout">
                  {fmtLbs(reading.net_lbs ?? reading.gross_lbs)}<span className="unit">lbs net</span>
                </div>
                <div className="small muted">
                  Gross {fmtLbs(reading.gross_lbs)} · tare {fmtLbs(reading.tare_lbs)} ·{' '}
                  {reading.trailer_number ? `trailer ${reading.trailer_number} · ` : ''}
                  captured {new Date(reading.captured_at).toLocaleTimeString()}
                </div>
              </div>
              <div className="row">
                <button
                  className="primary"
                  onClick={() => set({ from_qty: reading.net_lbs ?? reading.gross_lbs })}
                >
                  Use this weight
                </button>
              </div>
            </div>
          ) : (
            <div className="muted small">
              No unused weigh-out from this plant's scale in the last two hours.
              Type the quantity, or start the scale agent.
            </div>
          )}
        </Card>

        <Card title="This trailer's last loads" tight>
          <DataTable
            rows={prefill.data?.trailer_history ?? []}
            rowKey={(row, i) => row.transaction_id ?? i}
            empty="No history for this trailer."
            columns={[
              { key: 'user_date', label: 'Date', render: (row) => fmtDate(row.user_date) },
              { key: 'operation', label: 'Op' },
              {
                key: 'material',
                label: 'Product',
                render: (row) => `${row.material_number ?? ''} ${row.material_description ?? ''}`,
              },
              { key: 'customer_name', label: 'Customer' },
            ]}
          />
        </Card>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------- 3. quality */

const QC_FIELDS = [
  { key: 'moisture', analyte: 'moisture', label: 'Moisture', unit: '%' },
  { key: 'temp', analyte: 'temp', label: 'Temperature', unit: '°F' },
  { key: 'ph', analyte: 'ph', label: 'pH', unit: '' },
  { key: 'ffa', analyte: 'ffa', label: 'FFA', unit: '%' },
  { key: 'tfa', analyte: 'tfa', label: 'TFA', unit: '%' },
  { key: 'spintest_fallout', analyte: 'spintest', label: 'Spintest', unit: 'mils' },
]

function QualityStep({
  orderId, saved, canWrite, onSaved, onSkip,
}: {
  orderId: number
  saved: any
  canWrite: boolean
  onSaved: (record: any) => void
  onSkip: () => void
}) {
  const toast = useToast()
  const [form, setForm] = useState<Record<string, any>>({})
  const [check, setCheck] = useState<QcValidation | null>(null)
  const [error, setError] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [acknowledged, setAcknowledged] = useState(false)

  const prefill = useAsync(() => api.get<any>(`/api/prefill/qc/${orderId}`), [orderId])

  useEffect(() => {
    if (prefill.data) {
      setForm((current) => ({ test_date: today(), ...prefill.data.values, ...current }))
      setCheck(prefill.data.validation)
    }
  }, [prefill.data])

  useEffect(() => {
    const handle = setTimeout(() => {
      api.post<QcValidation>(`/api/orders/${orderId}/qc/validate`, form)
        .then(setCheck)
        .catch(() => undefined)
    }, 250)
    return () => clearTimeout(handle)
  }, [orderId, JSON.stringify(form)])

  const required = useMemo(() => new Set(check?.required_tests ?? []), [check])
  const warnings = check?.warnings ?? []
  // An out-of-spec number is a decision and needs a signature; a reading not
  // typed in yet is just an unfinished record and must not block the save.
  const outOfSpec = warnings.filter((warning) => warning.severity === 'out_of_spec')
  const advisories = warnings.filter((warning) => warning.severity !== 'out_of_spec')
  // The banner used to name every analyte the product is tested for, including
  // the ones this form has no box for — so it promised seven tests and offered
  // six. The ones that arrive from LIMS are now listed as such.
  const formAnalytes = new Set(QC_FIELDS.map((field) => field.analyte))
  const onThisForm = (check?.required_tests ?? []).filter((a) => formAnalytes.has(a))
  const fromLims = (check?.required_tests ?? []).filter((a) => !formAnalytes.has(a))
  const set = (patch: Record<string, any>) => setForm((current) => ({ ...current, ...patch }))

  async function generateSample() {
    try {
      const result = await api.post<{ sample_number: string }>('/api/numbering/sample', { order_id: orderId })
      set({ sample_number: result.sample_number })
      toast.push('info', 'Sample number generated', result.sample_number)
    } catch (err) { toast.push('error', 'Could not generate a sample number', (err as Error).message) }
  }

  async function save() {
    setBusy(true); setError(null)
    try {
      const record = await api.post<any>(`/api/orders/${orderId}/qc`, {
        ...form, acknowledge_warnings: acknowledged,
      })
      toast.push('success', 'QC recorded', record.spec_summary.status.replace('_', ' '))
      onSaved(record)
    } catch (err) { setError(err) } finally { setBusy(false) }
  }

  if (prefill.loading) return <Loading />
  if (saved) {
    return (
      <Card title="Quality recorded">
        <div className="row" style={{ gap: 10 }}>
          <SpecBadge status={saved.spec_summary.status} />
          <span className="mono">{saved.sample_number || 'no sample number'}</span>
        </div>
      </Card>
    )
  }
  if (!canWrite) {
    return (
      <Card title="Quality control">
        <Alert tone="info" title="Your role cannot record QC">
          Carry on to the checklist; QC staff will enter results against this order.
        </Alert>
        <div className="row end" style={{ marginTop: 12 }}>
          <button onClick={onSkip}>Skip to checklist</button>
        </div>
      </Card>
    )
  }

  return (
    <Card
      title="Quality control"
      subtitle={check ? `${check.material.number} · ${check.material.description}` : undefined}
    >
      {error && <div style={{ marginBottom: 12 }}><ErrorBox error={error} /></div>}
      {check && (
        <div style={{ marginBottom: 14 }}>
          <Alert
            tone="info"
            title={`Enter here: ${onThisForm.join(', ').toUpperCase() || 'nothing'}`}
          >
            {fromLims.length > 0 && (
              <div>
                Also tested for {fromLims.join(', ')} — those come back from the lab and are
                not typed on this screen.
              </div>
            )}
            {check.not_tested.length > 0 && (
              <div>Not run for this product: {check.not_tested.join(', ')}.</div>
            )}
          </Alert>
        </div>
      )}

      <div className="form-grid">
        <Field label="BOL number" hint="From the load you just posted">
          <input value={form.bol_number ?? ''} onChange={(e) => set({ bol_number: e.target.value })} />
        </Field>
        <Field label="Sample #" className="span-2">
          <div className="row" style={{ gap: 8, flexWrap: 'nowrap' }}>
            <input value={form.sample_number ?? ''} onChange={(e) => set({ sample_number: e.target.value })} />
            <button className="sm nowrap" onClick={generateSample}>Generate</button>
          </div>
        </Field>

        {QC_FIELDS.map((field) => {
          const isRequired = required.has(field.analyte)
          const evaluation = check?.evaluations.find((e) => e.analyte === field.analyte)
          const bad = evaluation?.verdict === 'out_of_spec'
          return (
            <Field
              key={field.key}
              label={`${field.label}${field.unit ? ` (${field.unit})` : ''}${isRequired ? ' *' : ''}`}
              error={bad ? `Outside spec for this product` : undefined}
              hint={
                evaluation && (evaluation.min_value !== null || evaluation.max_value !== null)
                  ? `Limit ${evaluation.min_value ?? '—'} to ${evaluation.max_value ?? '—'}`
                  : isRequired ? 'Required for this product' : 'Not run for this product'
              }
            >
              <input
                type="number"
                step="0.01"
                inputMode="decimal"
                disabled={!isRequired && !form[field.key]}
                value={form[field.key] ?? ''}
                onChange={(e) => set({ [field.key]: e.target.value })}
              />
            </Field>
          )
        })}

        <Field label="In/out seals">
          <input value={form.seal_number ?? ''} onChange={(e) => set({ seal_number: e.target.value })} />
        </Field>
        <Field label="Last material hauled" className="span-2" hint="From this trailer's previous load">
          <input
            value={form.last_material_hauled ?? ''}
            onChange={(e) => set({ last_material_hauled: e.target.value })}
          />
        </Field>
      </div>

      {advisories.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <Alert tone="info" title={`${advisories.length} still to enter`}>
            <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
              {advisories.map((warning, i) => <li key={i}>{warning.message}</li>)}
            </ul>
            A partial record saves fine — come back and finish it.
          </Alert>
        </div>
      )}

      {outOfSpec.length > 0 && (
        <div className="stack" style={{ marginTop: 14 }}>
          <Alert tone="warn" title={`${outOfSpec.length} result(s) outside spec`}>
            <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
              {outOfSpec.map((warning, i) => <li key={i}>{warning.message}</li>)}
            </ul>
          </Alert>
          <label className="row small check" style={{ gap: 10 }}>
            <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
            Reviewed — save anyway. Your name and these numbers go on the record.
          </label>
        </div>
      )}

      <div className="step-actions">
        <button onClick={onSkip}>Skip for now</button>
        <button
          className="primary"
          onClick={save}
          disabled={busy || (outOfSpec.length > 0 && !acknowledged)}
        >
          {busy ? <span className="spinner" /> : null} Save QC
        </button>
      </div>
    </Card>
  )
}

/* -------------------------------------------------------- 4. checklist */

const CHECKLIST_COPY = {
  pre_load: {
    title: 'Check the trailer',
    lead: 'Before any product goes in.',
    doneTitle: 'Trailer checked',
    save: 'Trailer is good — continue',
    skip: 'Skip the trailer check',
  },
  post_load: {
    title: 'Sign off the load',
    lead: 'Seals and load temperature, now the truck is full.',
    doneTitle: 'Signed off',
    save: 'Save sign-off',
    skip: 'Skip for now',
  },
} as const

function ChecklistStep({
  stage, orderId, trailerNumber, done, onDone, onSkip,
}: {
  stage: 'pre_load' | 'post_load'
  orderId: number
  trailerNumber: string
  done: boolean
  onDone: () => void
  onSkip: () => void
}) {
  const { reference } = useApp()
  const toast = useToast()
  const [responses, setResponses] = useState<Record<number, string>>({})
  const [error, setError] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const copy = CHECKLIST_COPY[stage]

  const questions = reference.qa_questions.filter((q) => (q.stage ?? 'post_load') === stage)
  const answered = questions.filter((q) => String(responses[q.question_id] ?? '').trim()).length

  async function save() {
    setBusy(true); setError(null)
    try {
      await api.post(`/api/orders/${orderId}/qa-checklist?stage=${stage}`, {
        responses,
        trailer_number: trailerNumber,
        trailer_load_time: new Date().toISOString(),
      })
      toast.push('success', `${copy.title} saved`)
      onDone()
    } catch (err) { setError(err) } finally { setBusy(false) }
  }

  if (done) {
    return <Card title={copy.doneTitle}><Badge tone="ok">All questions answered</Badge></Card>
  }
  if (questions.length === 0) {
    return (
      <Card title={copy.title}>
        <Alert tone="info" title="Nothing to answer">No questions are set up for this step.</Alert>
        <div className="step-actions"><button className="primary" onClick={onDone}>Continue</button></div>
      </Card>
    )
  }

  return (
    <Card
      title={copy.title}
      subtitle={`${copy.lead} Trailer ${trailerNumber || '—'} · ${answered}/${questions.length} answered`}
    >
      {error && <div style={{ marginBottom: 12 }}><ErrorBox error={error} /></div>}
      <div className="stack">
        {questions.map((question) => {
          const answer = responses[question.question_id] ?? ''
          const setAnswer = (value: string) =>
            setResponses((current) => ({ ...current, [question.question_id]: value }))
          return (
            <div key={question.question_id} className="qa-question">
              <span id={`q-${question.question_id}`}>{question.question}</span>
              {question.answer_type === 'yesno' ? (
                <div className="qa-answers" role="group" aria-labelledby={`q-${question.question_id}`}>
                  {['Yes', 'No', 'N/A'].map((option) => (
                    <button
                      key={option}
                      className={answer === option ? 'primary' : ''}
                      aria-pressed={answer === option}
                      onClick={() => setAnswer(option)}
                    >
                      {option}
                    </button>
                  ))}
                </div>
              ) : (
                // A written or numeric answer needs an N/A too. Without one the
                // only way past a question that does not apply — a tank wagon
                // with no wash ticket — was to invent a value.
                <div className="qa-answers">
                  <input
                    aria-labelledby={`q-${question.question_id}`}
                    style={{ maxWidth: 200 }}
                    inputMode={question.answer_type === 'number' ? 'decimal' : 'text'}
                    disabled={answer === 'N/A'}
                    value={answer === 'N/A' ? '' : answer}
                    onChange={(event) => setAnswer(event.target.value)}
                  />
                  <button
                    className={answer === 'N/A' ? 'primary' : ''}
                    aria-pressed={answer === 'N/A'}
                    onClick={() => setAnswer(answer === 'N/A' ? '' : 'N/A')}
                  >
                    N/A
                  </button>
                </div>
              )}
            </div>
          )
        })}
      </div>
      <div className="step-actions">
        <button onClick={onSkip}>{copy.skip}</button>
        <button className="primary" onClick={save} disabled={busy || answered < questions.length}>
          {copy.save}
        </button>
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------- 5. ship */

function ShipStep({
  orderId, loadTxn, shipped, onShipped, onRestart,
}: {
  orderId: number
  loadTxn: any
  shipped: any
  onShipped: (txn: any) => void
  onRestart: () => void
}) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [confirming, setConfirming] = useState<any>(null)
  const staged = useAsync(
    () => api.get<any[]>(`/api/shipments/pending${qs({ order_id: orderId })}`),
    [orderId, shipped?.transaction_id],
  )
  // The document that leaves with the driver covers the truck in front of
  // them. Asking for the order-wide BOL printed every load on the order and
  // totalled them, which on a split order is somebody else's freight.
  const bol = useAsync(
    () => api.get<any>(
      `/api/orders/${orderId}/bol${qs({ transaction_id: loadTxn?.transaction_id })}`,
    ),
    [orderId, loadTxn?.transaction_id, shipped?.transaction_id],
  )

  // Which staged row is the truck this flow just loaded. Without this the
  // screen offered a column of identical Ship buttons and the operator — or an
  // automated click — took the first one, which is not necessarily theirs.
  const isThisTruck = (row: any) =>
    loadTxn && String(row.transaction_id) === String(loadTxn.transaction_id)
  const rows = [...(staged.data ?? [])].sort(
    (a, b) => Number(isThisTruck(b)) - Number(isThisTruck(a)),
  )
  const others = rows.filter((row) => !isThisTruck(row)).length

  async function ship(stageId: number) {
    setBusy(true)
    setConfirming(null)
    try {
      const txn = await api.post<any>(`/api/shipments/${stageId}/ship`, {})
      toast.push('success', 'Shipped', `Transaction ${txn.transaction_id}`)
      onShipped(txn)
      staged.reload()
      bol.reload()
    } catch (error) {
      toast.push('error', 'Not shipped', (error as Error).message)
    } finally { setBusy(false) }
  }

  return (
    <div className="grid cols-2">
      <Card
        title="Ship"
        subtitle={
          loadTxn
            ? `Trailer ${loadTxn.trailer_number || '—'} — the one you just loaded`
            : 'Staged trailers on this order'
        }
        tight
      >
        {loadTxn && others > 0 && (
          <div style={{ margin: '0 0 12px' }}>
            <Alert tone="info" title={`${others} other trailer${others === 1 ? '' : 's'} staged on this order`}>
              Yours is at the top, marked <strong>this truck</strong>. Shipping one of the
              others asks first.
            </Alert>
          </div>
        )}
        {confirming && (
          <div style={{ margin: '0 0 12px' }}>
            <Alert tone="warn" title={`Ship trailer ${confirming.trailer_number || '—'}?`}>
              That is not the trailer you loaded in this flow. It carries{' '}
              {fmtLbs(confirming.quantity)} lbs on BOL {confirming.bol_number}, loaded by{' '}
              {confirming.loaded_by || 'someone else'}.
              <div className="row" style={{ gap: 8, marginTop: 10 }}>
                <button className="sm primary" disabled={busy} onClick={() => ship(confirming.stage_id)}>
                  Yes, ship trailer {confirming.trailer_number || '—'}
                </button>
                <button className="sm" onClick={() => setConfirming(null)}>Cancel</button>
              </div>
            </Alert>
          </div>
        )}
        {staged.loading ? <Loading /> : (
          <DataTable
            rows={rows}
            rowKey={(row) => row.stage_id}
            empty={shipped ? 'Shipped — nothing left staged.' : 'Nothing staged for this order.'}
            columns={[
              {
                key: 'trailer_number',
                label: 'Trailer',
                render: (row) => (
                  <span className="row" style={{ gap: 8 }}>
                    {row.trailer_number}
                    {isThisTruck(row) && <Badge tone="ok">this truck</Badge>}
                  </span>
                ),
              },
              { key: 'bol_number', label: 'BOL' },
              {
                key: 'material_number',
                label: 'Product',
                render: (row) => (
                  <span>
                    {row.material_number} {row.material_description}
                    {row.order_material_number && row.order_material_number !== row.material_number && (
                      <Badge tone="warn">not the ordered product</Badge>
                    )}
                  </span>
                ),
              },
              { key: 'quantity', label: 'Lbs', numeric: true, render: (row) => fmtLbs(row.quantity) },
              {
                key: 'go',
                label: '',
                render: (row) => (
                  <button
                    className={isThisTruck(row) || !loadTxn ? 'primary' : ''}
                    disabled={busy}
                    onClick={() => (isThisTruck(row) || !loadTxn
                      ? ship(row.stage_id)
                      : setConfirming(row))}
                  >
                    Ship {row.trailer_number || ''}
                  </button>
                ),
              },
            ]}
          />
        )}
      </Card>

      <Card
        title="Bill of lading"
        subtitle={bol.data?.single_load ? 'This trailer' : 'Every load on this order'}
        actions={<button className="sm" onClick={() => window.print()}>Print</button>}
      >
        {bol.loading ? <Loading /> : bol.error ? <ErrorBox error={bol.error} /> : (
          <div className="stack printable" id="bol-print">
            <div>
              <strong>{bol.data.shipper.name}</strong>
              <div className="small muted">{bol.data.shipper.address}</div>
            </div>
            {bol.data.material_mismatch && (
              <Alert tone="warn" title="This load is not the product on the order">
                The order is for {bol.data.order.material_one_number}{' '}
                {bol.data.order.material_one_description}. What is on the trailer is printed
                below. Check before the driver leaves.
              </Alert>
            )}
            <dl className="kv">
              <dt>Consigned to</dt><dd>{bol.data.order.customer_name ?? '—'}</dd>
              <dt>Order</dt><dd>{bol.data.order.order_id}</dd>
              <dt>BOL</dt>
              <dd className="mono">{bol.data.loads.map((l: any) => l.bol_number).join(', ') || '—'}</dd>
              <dt>Trailer</dt>
              <dd>{[...new Set(bol.data.loads.map((l: any) => l.trailer_number))].join(', ') || '—'}</dd>
              {/* The product on the paperwork is the product on the truck. It
                  used to be read off the order header, which is the same thing
                  right up until the day it is not. */}
              <dt>Product</dt>
              <dd>
                {bol.data.products.length
                  ? bol.data.products.map((p: any) => `${p.number} ${p.description}`).join(', ')
                  : '—'}
              </dd>
              <dt>Quantity</dt><dd className="num">{fmtLbs(bol.data.total_quantity)} lbs</dd>
              <dt>Complete</dt><dd className="num">{fmtNumber(bol.data.order.percent_complete, 1)}%</dd>
            </dl>
            {bol.data.qc[0] && (
              <div className="small muted">
                Sample {bol.data.qc[0].sample_number || '—'} · seals {bol.data.qc[0].seal_number || '—'}
              </div>
            )}
            {shipped && (
              <Alert tone="ok" title="Shipped">
                Transaction {shipped.transaction_id}. The order closes on its own once the
                overnight job sees it fulfilled, shipped and QC'd.
              </Alert>
            )}
            <div className="row end">
              <button className="primary" onClick={onRestart}>Next truck</button>
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}
