/* Load and ship, in one flow.
 *
 * The legacy client made a loadout operator visit four screens for one truck —
 * Plant floor, the order, the QC tab, the QA tab, then Ship — and hand-type the
 * order, trailer, BOL, seals, sample number and weight in the middle of it.
 * This is the same five things in sequence, with every value the system already
 * knows filled in and the reason shown next to it. */

import { useEffect, useMemo, useState } from 'react'
import { useApp, useToast } from '../App'
import { api, qs } from '../lib/api'
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

const STEPS = ['Order', 'Load', 'Quality', 'Checklist', 'Ship'] as const
type Step = (typeof STEPS)[number]

export default function LoadAndShip({ initialOrderId }: { initialOrderId?: number }) {
  const { plantCode, navigate, can } = useApp()
  const [step, setStep] = useState<Step>(initialOrderId ? 'Load' : 'Order')
  const [orderId, setOrderId] = useState<number | null>(initialOrderId ?? null)
  const [loadTxn, setLoadTxn] = useState<any>(null)
  const [qcRecord, setQcRecord] = useState<any>(null)
  const [checklistDone, setChecklistDone] = useState(false)
  const [shipped, setShipped] = useState<any>(null)

  const order = useAsync(
    () => (orderId ? api.get<Order>(`/api/orders/${orderId}`) : Promise.resolve(null as any)),
    [orderId, loadTxn?.transaction_id, qcRecord?.qc_id, shipped?.transaction_id],
  )

  function restart() {
    setOrderId(null)
    setLoadTxn(null)
    setQcRecord(null)
    setChecklistDone(false)
    setShipped(null)
    setStep('Order')
  }

  const done: Record<Step, boolean> = {
    Order: Boolean(orderId),
    Load: Boolean(loadTxn),
    Quality: Boolean(qcRecord),
    Checklist: checklistDone,
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

      <div className="steps">
        {STEPS.map((name, i) => (
          <button
            key={name}
            className={`step${name === step ? ' active' : ''}${done[name] ? ' done' : ''}`}
            disabled={!orderId && name !== 'Order'}
            onClick={() => setStep(name)}
          >
            <span className="n">{done[name] ? '✓' : i + 1}</span>
            {name}
          </button>
        ))}
      </div>

      {step === 'Order' && <PickOrder onPicked={(id) => { setOrderId(id); setStep('Load') }} />}
      {step === 'Load' && orderId && (
        <LoadStep
          orderId={orderId}
          posted={loadTxn}
          onPosted={(txn) => { setLoadTxn(txn); setStep('Quality') }}
        />
      )}
      {step === 'Quality' && orderId && (
        <QualityStep
          orderId={orderId}
          saved={qcRecord}
          canWrite={can('qc.write')}
          onSaved={(record) => { setQcRecord(record); setStep('Checklist') }}
          onSkip={() => setStep('Checklist')}
        />
      )}
      {step === 'Checklist' && orderId && (
        <ChecklistStep
          orderId={orderId}
          trailerNumber={loadTxn?.trailer_number ?? order.data?.trailer_number ?? ''}
          done={checklistDone}
          onDone={() => { setChecklistDone(true); setStep('Ship') }}
          onSkip={() => setStep('Ship')}
        />
      )}
      {step === 'Ship' && orderId && (
        <ShipStep orderId={orderId} shipped={shipped} onShipped={setShipped} onRestart={restart} />
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

  return (
    <Card title="Sales orders ready to load" subtitle="Open orders at this plant" tight>
      {result.loading ? <Loading /> : result.error ? <ErrorBox error={result.error} /> : (
        <DataTable
          rows={result.data?.rows ?? []}
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
              render: (row) => fmtLbs(Math.max(row.material_one_quantity - row.qty_fulfilled, 0)),
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
  orderId, posted, onPosted,
}: { orderId: number; posted: any; onPosted: (txn: any) => void }) {
  const { plantId, reference } = useApp()
  const toast = useToast()
  const [form, setForm] = useState<Record<string, any>>({})
  const [error, setError] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [reading, setReading] = useState<ScaleReading | null>(null)

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

  async function post() {
    setBusy(true); setError(null)
    try {
      const payload: Record<string, any> = { order_id: orderId, plant_id: plantId, ...form }
      payload.to_qty = payload.from_qty
      if (reading) payload.scale_reading_id = reading.reading_id
      delete payload.bol_preview
      const txn = await api.post<any>('/api/transactions/load', payload)
      toast.push('success', `Loaded ${fmtLbs(txn.from_qty)} lbs`, `BOL ${txn.to_bol}`)
      onPosted(txn)
    } catch (err) { setError(err) } finally { setBusy(false) }
  }

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

        {prefill.data?.notes.length ? (
          <div className="why">
            {prefill.data.notes.map((note, i) => <span key={i}>{note}</span>)}
          </div>
        ) : null}

        <div className="row end" style={{ marginTop: 16 }}>
          <button className="primary" onClick={post} disabled={busy || overdrawn || !form.from_qty}>
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
          <Alert tone="info" title={`Tested for: ${check.required_tests.join(', ').toUpperCase() || 'nothing'}`}>
            {check.not_tested.length > 0 && `Not run for this product: ${check.not_tested.join(', ')}.`}
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

      {warnings.length > 0 && (
        <div className="stack" style={{ marginTop: 14 }}>
          <Alert tone="warn" title={`${warnings.length} thing(s) to check`}>
            <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
              {warnings.map((warning, i) => <li key={i}>{warning.message}</li>)}
            </ul>
          </Alert>
          <label className="row small" style={{ gap: 8 }}>
            <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
            Reviewed — save anyway (recorded in the audit trail).
          </label>
        </div>
      )}

      <div className="step-actions">
        <button onClick={onSkip}>Skip for now</button>
        <button className="primary" onClick={save} disabled={busy || (warnings.length > 0 && !acknowledged)}>
          {busy ? <span className="spinner" /> : null} Save QC
        </button>
      </div>
    </Card>
  )
}

/* -------------------------------------------------------- 4. checklist */

function ChecklistStep({
  orderId, trailerNumber, done, onDone, onSkip,
}: {
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

  const questions = reference.qa_questions
  const answered = questions.filter((q) => responses[q.question_id]).length

  async function save() {
    setBusy(true); setError(null)
    try {
      await api.post(`/api/orders/${orderId}/qa-checklist`, {
        responses,
        trailer_number: trailerNumber,
        trailer_load_time: new Date().toISOString(),
      })
      toast.push('success', 'Checklist saved')
      onDone()
    } catch (err) { setError(err) } finally { setBusy(false) }
  }

  if (done) {
    return <Card title="Checklist complete"><Badge tone="ok">All questions answered</Badge></Card>
  }

  return (
    <Card title="QA checklist" subtitle={`Trailer ${trailerNumber || '—'} · ${answered}/${questions.length} answered`}>
      {error && <div style={{ marginBottom: 12 }}><ErrorBox error={error} /></div>}
      <div className="stack">
        {questions.map((question) => (
          <div key={question.question_id} className="row" style={{ justifyContent: 'space-between', gap: 16 }}>
            <span>{question.question}</span>
            {question.answer_type === 'yesno' ? (
              <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
                {['Yes', 'No', 'N/A'].map((option) => (
                  <button
                    key={option}
                    className={responses[question.question_id] === option ? 'primary' : ''}
                    onClick={() => setResponses((current) => ({ ...current, [question.question_id]: option }))}
                  >
                    {option}
                  </button>
                ))}
              </div>
            ) : (
              <input
                style={{ maxWidth: 220 }}
                inputMode={question.answer_type === 'number' ? 'decimal' : 'text'}
                value={responses[question.question_id] ?? ''}
                onChange={(event) => setResponses((current) => ({
                  ...current, [question.question_id]: event.target.value,
                }))}
              />
            )}
          </div>
        ))}
      </div>
      <div className="step-actions">
        <button onClick={onSkip}>Skip for now</button>
        <button className="primary" onClick={save} disabled={busy || answered < questions.length}>
          Save checklist
        </button>
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------- 5. ship */

function ShipStep({
  orderId, shipped, onShipped, onRestart,
}: {
  orderId: number
  shipped: any
  onShipped: (txn: any) => void
  onRestart: () => void
}) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const staged = useAsync(
    () => api.get<any[]>(`/api/shipments/pending${qs({ order_id: orderId })}`),
    [orderId, shipped?.transaction_id],
  )
  const bol = useAsync(() => api.get<any>(`/api/orders/${orderId}/bol`), [orderId, shipped?.transaction_id])

  async function ship(stageId: number) {
    setBusy(true)
    try {
      const txn = await api.post<any>(`/api/shipments/${stageId}/ship`, {})
      toast.push('success', 'Shipped', `Transaction ${txn.transaction_id}`)
      onShipped(txn)
      staged.reload()
      bol.reload()
    } catch (error) {
      toast.push('error', 'Ship failed', (error as Error).message)
    } finally { setBusy(false) }
  }

  return (
    <div className="grid cols-2">
      <Card title="Ship" subtitle="Complete the shipment for this order" tight>
        {staged.loading ? <Loading /> : (
          <DataTable
            rows={staged.data ?? []}
            rowKey={(row) => row.stage_id}
            empty={shipped ? 'Shipped — nothing left staged.' : 'Nothing staged for this order.'}
            columns={[
              { key: 'trailer_number', label: 'Trailer' },
              { key: 'bol_number', label: 'BOL' },
              { key: 'quantity', label: 'Lbs', numeric: true, render: (row) => fmtLbs(row.quantity) },
              {
                key: 'go',
                label: '',
                render: (row) => (
                  <button className="primary" disabled={busy} onClick={() => ship(row.stage_id)}>
                    Ship
                  </button>
                ),
              },
            ]}
          />
        )}
      </Card>

      <Card
        title="Bill of lading"
        actions={<button className="sm" onClick={() => window.print()}>Print</button>}
      >
        {bol.loading ? <Loading /> : bol.error ? <ErrorBox error={bol.error} /> : (
          <div className="stack">
            <div>
              <strong>{bol.data.shipper.name}</strong>
              <div className="small muted">{bol.data.shipper.address}</div>
            </div>
            <dl className="kv">
              <dt>Consigned to</dt><dd>{bol.data.order.customer_name ?? '—'}</dd>
              <dt>Order</dt><dd>{bol.data.order.order_id}</dd>
              <dt>Product</dt>
              <dd>{bol.data.order.material_one_number} {bol.data.order.material_one_description}</dd>
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
