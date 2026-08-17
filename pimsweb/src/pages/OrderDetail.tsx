import { useEffect, useMemo, useState } from 'react'
import { useApp, useToast } from '../App'
import { api } from '../lib/api'
import type { AuditEntry, Evaluation, Order, QcRecord, QcValidation, Transaction } from '../lib/types'
import {
  Alert, Badge, Card, DataTable, ErrorBox, Field, Loading, Meter, Modal, SpecBadge,
  StatusBadge, Tabs, fmtDate, fmtDateTime, fmtLbs, fmtNumber, today, useAsync,
} from '../components/ui'

type Detail = Order & {
  transactions: Transaction[]
  qc: QcRecord[]
  qa_checklists: any[]
  in_process: any[]
  pending_shipments: any[]
  customer_requirements?: { requirement: string }[]
  vendor_requirements?: { requirement: string }[]
}

export default function OrderDetail({ orderId }: { orderId: number }) {
  const { navigate, can } = useApp()
  const [tab, setTab] = useState('overview')
  const order = useAsync(() => api.get<Detail>(`/api/orders/${orderId}`), [orderId])

  if (order.loading) return <Loading />
  if (order.error) return <ErrorBox error={order.error} />
  const data = order.data!

  return (
    <>
      <div className="page-head">
        <div>
          <div className="row" style={{ gap: 10 }}>
            <h1>Order {data.order_id}</h1>
            <Badge tone="brand">{data.order_type}</Badge>
            <StatusBadge status={data.status} />
            {data.material_one_number && (
              <span className="muted">
                {data.material_one_number} · {data.material_one_description}
              </span>
            )}
          </div>
          <div className="sub">
            {data.plant_code} · due {fmtDate(data.due_date)} ·{' '}
            {data.customer_name || data.vendor_name || 'internal'} · created by {data.added_by}
          </div>
        </div>
        <div className="actions">
          <button onClick={() => navigate('orders')}>← All orders</button>
          <button onClick={() => navigate(`operations?order=${data.order_id}`)}>Post movement</button>
        </div>
      </div>

      {(data.customer_requirements?.length || data.vendor_requirements?.length) ? (
        <div style={{ marginBottom: 16 }}>
          <Alert tone="info" title="Requirements for this order">
            <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
              {[...(data.customer_requirements ?? []), ...(data.vendor_requirements ?? [])].map(
                (requirement, index) => <li key={index}>{requirement.requirement}</li>,
              )}
            </ul>
          </Alert>
        </div>
      ) : null}

      <Tabs
        active={tab}
        onChange={setTab}
        tabs={[
          { key: 'overview', label: 'Overview' },
          { key: 'activity', label: `Activity (${data.transactions.length})` },
          { key: 'qc', label: `Quality control (${data.qc.length})` },
          { key: 'qa', label: `QA checklist (${data.qa_checklists.length})` },
          { key: 'audit', label: 'History' },
        ]}
      />

      {tab === 'overview' && <Overview order={data} onChanged={order.reload} canEdit={can('order.write')} />}
      {tab === 'activity' && <Activity order={data} onChanged={order.reload} canVoid={can('txn.void')} />}
      {tab === 'qc' && <QualityControl order={data} onChanged={order.reload} canWrite={can('qc.write')} />}
      {tab === 'qa' && <QaChecklists order={data} onChanged={order.reload} canWrite={can('qc.write')} />}
      {tab === 'audit' && <History orderId={orderId} />}
    </>
  )
}

/* -------------------------------------------------------------- overview */

function Overview({ order, onChanged, canEdit }: { order: Detail; onChanged: () => void; canEdit: boolean }) {
  const [editing, setEditing] = useState(false)

  return (
    <>
      <div className="grid cols-3">
        <Card title="Progress">
          <div className="stack">
            <div>
              <div className="muted small">Ordered</div>
              <div style={{ fontSize: 22, fontWeight: 650 }} className="num">
                {fmtLbs(order.material_one_quantity)} lbs
              </div>
            </div>
            <Meter value={order.qty_fulfilled} max={order.material_one_quantity} variant="progress" />
            <dl className="kv">
              <dt>Fulfilled</dt><dd className="num">{fmtLbs(order.qty_fulfilled)} lbs</dd>
              <dt>Shipped</dt><dd className="num">{fmtLbs(order.qty_shipped)} lbs</dd>
              <dt>Complete</dt><dd className="num">{fmtNumber(order.percent_complete, 1)}%</dd>
              <dt>Staged</dt><dd>{order.pending_shipments.length} trailer(s) awaiting ship</dd>
            </dl>
          </div>
        </Card>

        <Card title="Order" actions={canEdit && !order.is_terminal ? (
          <button className="sm" onClick={() => setEditing(true)}>Edit</button>
        ) : undefined}>
          <dl className="kv">
            <dt>Type</dt><dd>{order.order_type} — {order.order_type_description}</dd>
            <dt>Plant</dt><dd>{order.plant_code} {order.plant_name}</dd>
            <dt>Department</dt><dd>{order.department_code || '—'}</dd>
            <dt>Order date</dt><dd>{fmtDate(order.order_date)}</dd>
            <dt>Due date</dt><dd>{fmtDate(order.due_date)}</dd>
            <dt>Reference</dt><dd>{order.order_reference || '—'}</dd>
            <dt>Blend SN</dt><dd>{order.blend_serial_number || '—'}</dd>
            <dt>Ship method</dt><dd>{order.ship_method || '—'}</dd>
            <dt>Trailer #</dt><dd>{order.trailer_number || '—'}</dd>
            <dt>Comments</dt><dd>{order.comments || '—'}</dd>
          </dl>
        </Card>

        <Card title="Quality summary">
          {order.qc.length === 0 ? (
            <div className="muted">No QC recorded yet.</div>
          ) : (
            <div className="stack">
              {order.qc.slice(0, 3).map((record) => (
                <div key={record.qc_id} className="row" style={{ justifyContent: 'space-between' }}>
                  <div>
                    <div className="small mono">{record.sample_number || 'no sample #'}</div>
                    <div className="small muted">{fmtDate(record.test_date)} · {record.performed_by}</div>
                  </div>
                  <SpecBadge status={record.spec_summary.status} />
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {editing && (
        <EditOrder order={order} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); onChanged() }} />
      )}
    </>
  )
}

function EditOrder({ order, onClose, onSaved }: { order: Detail; onClose: () => void; onSaved: () => void }) {
  const { reference } = useApp()
  const toast = useToast()
  const [error, setError] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({
    due_date: order.due_date,
    department_id: order.department_id ?? '',
    material_one_quantity: order.material_one_quantity,
    order_reference: order.order_reference,
    blend_serial_number: order.blend_serial_number,
    ship_method: order.ship_method,
    trailer_number: order.trailer_number,
    comments: order.comments,
    status_id: order.status_id,
  })

  const set = (patch: Record<string, any>) => setForm((current) => ({ ...current, ...patch }))

  async function save() {
    setBusy(true); setError(null)
    try {
      await api.patch(`/api/orders/${order.order_id}`, form)
      toast.push('success', `Order ${order.order_id} updated`)
      onSaved()
    } catch (err) { setError(err) } finally { setBusy(false) }
  }

  return (
    <Modal
      title={`Edit order ${order.order_id}`}
      onClose={onClose}
      footer={<>
        <button onClick={onClose}>Cancel</button>
        <button className="primary" onClick={save} disabled={busy}>Save changes</button>
      </>}
    >
      {error && <div style={{ marginBottom: 12 }}><ErrorBox error={error} /></div>}
      <div className="form-grid">
        <Field label="Due date" error={error?.fields?.due_date}>
          <input type="date" value={form.due_date} onChange={(e) => set({ due_date: e.target.value })} />
        </Field>
        <Field label="Quantity (lbs)" error={error?.fields?.material_one_quantity}>
          <input type="number" value={form.material_one_quantity}
            onChange={(e) => set({ material_one_quantity: Number(e.target.value) })} />
        </Field>
        <Field label="Status">
          <select value={form.status_id} onChange={(e) => set({ status_id: Number(e.target.value) })}>
            {reference.statuses.map((status) => (
              <option key={status.status_id} value={status.status_id}>{status.name}</option>
            ))}
          </select>
        </Field>
        <Field label="Department">
          <select value={form.department_id} onChange={(e) => set({ department_id: e.target.value })}>
            <option value="">—</option>
            {reference.departments.map((dept) => (
              <option key={dept.department_id} value={dept.department_id}>{dept.code}</option>
            ))}
          </select>
        </Field>
        <Field label="Reference">
          <input value={form.order_reference} onChange={(e) => set({ order_reference: e.target.value })} />
        </Field>
        <Field label="Blend SN">
          <input value={form.blend_serial_number} onChange={(e) => set({ blend_serial_number: e.target.value })} />
        </Field>
        <Field label="Ship method">
          <input value={form.ship_method} onChange={(e) => set({ ship_method: e.target.value })} />
        </Field>
        <Field label="Trailer #">
          <input value={form.trailer_number} onChange={(e) => set({ trailer_number: e.target.value })} />
        </Field>
        <Field label="Comments" className="span-3">
          <input value={form.comments} onChange={(e) => set({ comments: e.target.value })} />
        </Field>
      </div>
    </Modal>
  )
}

/* -------------------------------------------------------------- activity */

function Activity({ order, onChanged, canVoid }: { order: Detail; onChanged: () => void; canVoid: boolean }) {
  const toast = useToast()
  const [voiding, setVoiding] = useState<Transaction | null>(null)
  const [reason, setReason] = useState('')

  async function submitVoid() {
    if (!voiding) return
    try {
      await api.post(`/api/transactions/${voiding.transaction_id}/void`, { reason })
      toast.push('success', `Transaction ${voiding.transaction_id} reversed`)
      setVoiding(null); setReason(''); onChanged()
    } catch (error) {
      toast.push('error', 'Void failed', (error as Error).message)
    }
  }

  return (
    <>
      <Card title="Inventory activity" subtitle="Every movement posted against this order" tight>
        <DataTable
          rows={order.transactions}
          rowKey={(row) => row.transaction_id}
          empty="Nothing posted against this order yet."
          columns={[
            { key: 'transaction_id', label: 'Trans id' },
            { key: 'transaction_type', label: 'Type', render: (row) => <Badge>{row.transaction_type}</Badge> },
            { key: 'user_date', label: 'Trans date', render: (row) => fmtDate(row.user_date) },
            { key: 'username', label: 'User' },
            {
              key: 'from', label: 'From',
              render: (row) => row.from_location_number
                ? `${row.from_location_number} · ${row.from_material_number ?? ''}`
                : '—',
            },
            { key: 'from_qty', label: 'Qty out', numeric: true, render: (row) => row.from_qty ? fmtLbs(row.from_qty) : '—' },
            {
              key: 'to', label: 'To',
              render: (row) => row.to_location_number
                ? `${row.to_location_number} · ${row.to_material_number ?? ''}`
                : '—',
            },
            { key: 'to_qty', label: 'Qty in', numeric: true, render: (row) => row.to_qty ? fmtLbs(row.to_qty) : '—' },
            { key: 'trailer_number', label: 'Trailer' },
            { key: 'remarks', label: 'Remarks' },
            {
              key: 'actions', label: '',
              // The server decides: a supervisor may reverse anything, and an
              // operator may reverse their own recent unshipped posting. When
              // they may not, say so — rendering nothing left an operator
              // looking at their own mistake with no button and no reason.
              render: (row) => (row.can_void ?? canVoid) ? (
                <button className="ghost sm" onClick={(event) => { event.stopPropagation(); setVoiding(row) }}>
                  Void
                </button>
              ) : row.void_blocked ? (
                <div className="stack" style={{ gap: 2 }}>
                  <button className="ghost sm" disabled>Void</button>
                  <span className="small muted">{row.void_blocked}</span>
                </div>
              ) : null,
            },
          ]}
        />
      </Card>

      {voiding && (
        <Modal
          title={`Void transaction ${voiding.transaction_id}`}
          subtitle="A reversing entry is posted; the original stays in the ledger."
          onClose={() => setVoiding(null)}
          width={520}
          footer={<>
            <button onClick={() => setVoiding(null)}>Cancel</button>
            <button className="danger" onClick={submitVoid} disabled={!reason.trim()}>Post reversal</button>
          </>}
        >
          <Field label="Reason" hint="Recorded in the audit trail and on the reversing entry.">
            <textarea value={reason} onChange={(event) => setReason(event.target.value)} />
          </Field>
        </Modal>
      )}
    </>
  )
}

/* -------------------------------------------------------- quality control */

const QC_FIELDS: { key: string; analyte: string; label: string; unit: string }[] = [
  { key: 'moisture', analyte: 'moisture', label: 'Moisture', unit: '%' },
  { key: 'temp', analyte: 'temp', label: 'Temperature', unit: '°F' },
  { key: 'ph', analyte: 'ph', label: 'pH', unit: '' },
  { key: 'ffa', analyte: 'ffa', label: 'FFA', unit: '%' },
  { key: 'tfa', analyte: 'tfa', label: 'TFA', unit: '%' },
  { key: 'spintest_fallout', analyte: 'spintest', label: 'Spintest fallout', unit: 'mils' },
]

function QualityControl({ order, onChanged, canWrite }: { order: Detail; onChanged: () => void; canWrite: boolean }) {
  const [entering, setEntering] = useState(false)

  return (
    <>
      <Card
        title="QC records"
        subtitle="Results are checked against this product's published limits"
        actions={canWrite ? <button className="primary sm" onClick={() => setEntering(true)}>Record QC</button> : undefined}
        tight
      >
        <DataTable
          rows={order.qc}
          rowKey={(row) => row.qc_id}
          empty="No QC recorded for this order."
          columns={[
            { key: 'test_date', label: 'Test date', render: (row) => fmtDate(row.test_date) },
            { key: 'sample_number', label: 'Sample #' },
            { key: 'bol_number', label: 'BOL #' },
            ...QC_FIELDS.map((field) => ({
              key: field.key,
              label: field.label,
              numeric: true,
              render: (row: QcRecord) => {
                const evaluation = row.evaluations.find((e) => e.analyte === field.analyte)
                const value = row[field.key]
                if (value === null || value === undefined) return <span className="muted">—</span>
                const bad = evaluation?.verdict === 'out_of_spec'
                return (
                  <span style={bad ? { color: 'var(--danger)', fontWeight: 650 } : undefined}>
                    {fmtNumber(value, 2)}
                  </span>
                )
              },
            })),
            { key: 'spec', label: 'Spec', render: (row) => <SpecBadge status={row.spec_summary.status} /> },
            { key: 'performed_by', label: 'By' },
          ]}
        />
      </Card>

      <Card title="In-process testing" subtitle="Readings taken at a test point during the run" tight>
        <DataTable
          rows={order.in_process}
          rowKey={(row) => row.reading_id}
          empty="No in-process readings."
          columns={[
            { key: 'reading_time', label: 'Time', render: (row) => fmtDateTime(row.reading_time) },
            { key: 'test_point_name', label: 'Test point' },
            { key: 'analyte', label: 'Analyte' },
            { key: 'value', label: 'Value', numeric: true },
            { key: 'comments', label: 'Comments' },
            { key: 'added_by', label: 'By' },
          ]}
        />
      </Card>

      {entering && (
        <QcEntry order={order} onClose={() => setEntering(false)} onSaved={() => { setEntering(false); onChanged() }} />
      )}
    </>
  )
}

function QcEntry({ order, onClose, onSaved }: { order: Detail; onClose: () => void; onSaved: () => void }) {
  const toast = useToast()
  const [form, setForm] = useState<Record<string, any>>({
    test_date: today(),
    bol_number: '',
    sample_number: '',
    seal_number: '',
    last_material_hauled: '',
    flash_pf: '',
    steam_on: false,
    comments: '',
    moisture: '', temp: '', ph: '', ffa: '', tfa: '', spintest_fallout: '',
  })
  const [check, setCheck] = useState<QcValidation | null>(null)
  const [error, setError] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [acknowledged, setAcknowledged] = useState(false)

  const set = (patch: Record<string, any>) => setForm((current) => ({ ...current, ...patch }))

  // Live validation: the server decides what this product is tested for.
  useEffect(() => {
    const handle = setTimeout(() => {
      api.post<QcValidation>(`/api/orders/${order.order_id}/qc/validate`, form)
        .then(setCheck)
        .catch(() => undefined)
    }, 250)
    return () => clearTimeout(handle)
  }, [order.order_id, JSON.stringify(form)])

  const required = useMemo(() => new Set(check?.required_tests ?? []), [check])
  const warnings = check?.warnings ?? []

  async function save() {
    setBusy(true); setError(null)
    try {
      await api.post(`/api/orders/${order.order_id}/qc`, {
        ...form,
        acknowledge_warnings: acknowledged,
      })
      toast.push('success', 'QC recorded',
        warnings.length ? `${warnings.length} warning(s) acknowledged` : undefined)
      onSaved()
    } catch (err) { setError(err) } finally { setBusy(false) }
  }

  const blocked = warnings.length > 0 && !acknowledged

  return (
    <Modal
      title="Record quality control"
      subtitle={check ? `${check.material.number} · ${check.material.description} (${check.material.family})` : `Order ${order.order_id}`}
      onClose={onClose}
      footer={<>
        <button onClick={onClose}>Cancel</button>
        <button className="primary" onClick={save} disabled={busy || blocked}>
          {busy ? <span className="spinner" /> : null} Save
        </button>
      </>}
    >
      {error && <div style={{ marginBottom: 12 }}><ErrorBox error={error} /></div>}

      {check && (
        <div style={{ marginBottom: 14 }}>
          <Alert tone="info" title={`Tests run for this product: ${check.required_tests.map((t) => t.toUpperCase()).join(', ') || 'none'}`}>
            {check.not_tested.length > 0 && (
              <>Not tested for this product: {check.not_tested.join(', ')} — those fields stay optional.</>
            )}
          </Alert>
        </div>
      )}

      <div className="form-grid">
        <Field label="Test date"><input type="date" value={form.test_date} onChange={(e) => set({ test_date: e.target.value })} /></Field>
        <Field label="BOL number"><input value={form.bol_number} onChange={(e) => set({ bol_number: e.target.value })} /></Field>
        <Field label="Sample #" hint="Matches this record to the LIMS result">
          <input value={form.sample_number} onChange={(e) => set({ sample_number: e.target.value })} />
        </Field>

        {QC_FIELDS.map((field) => {
          const evaluation = check?.evaluations.find((e) => e.analyte === field.analyte)
          const isRequired = required.has(field.analyte)
          const warning = warnings.find((w) => w.field === field.key)
          return (
            <Field
              key={field.key}
              label={`${field.label}${field.unit ? ` (${field.unit})` : ''}${isRequired ? ' *' : ''}`}
              error={warning?.severity === 'out_of_spec' ? warning.message : undefined}
              hint={<SpecHint evaluation={evaluation} required={isRequired} />}
            >
              <input
                type="number"
                step="0.01"
                value={form[field.key]}
                disabled={!isRequired && !form[field.key]}
                onFocus={() => undefined}
                onChange={(event) => set({ [field.key]: event.target.value })}
                style={!isRequired ? { background: 'var(--surface-2)' } : undefined}
              />
            </Field>
          )
        })}

        <Field label="Flash (P/F)">
          <select value={form.flash_pf} onChange={(e) => set({ flash_pf: e.target.value })}>
            <option value="">—</option>
            <option value="P">Pass</option>
            <option value="F">Fail</option>
          </select>
        </Field>
        <Field label="In/out seals"><input value={form.seal_number} onChange={(e) => set({ seal_number: e.target.value })} /></Field>
        <Field label="Last material hauled">
          <input value={form.last_material_hauled} onChange={(e) => set({ last_material_hauled: e.target.value })} />
        </Field>
        <div className="field">
          <label className="row small" style={{ gap: 6, marginTop: 22 }}>
            <input type="checkbox" checked={form.steam_on} onChange={(e) => set({ steam_on: e.target.checked })} />
            Steam on
          </label>
        </div>
        <Field label="Comments" className="span-2">
          <input value={form.comments} onChange={(e) => set({ comments: e.target.value })} />
        </Field>
      </div>

      {warnings.length > 0 && (
        <div style={{ marginTop: 14 }} className="stack">
          <Alert tone="warn" title={`${warnings.length} thing(s) to check before saving`}>
            <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
              {warnings.map((warning, index) => <li key={index}>{warning.message}</li>)}
            </ul>
          </Alert>
          <label className="row small" style={{ gap: 8 }}>
            <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
            I have reviewed these and want to save anyway (recorded in the audit trail).
          </label>
        </div>
      )}
    </Modal>
  )
}

function SpecHint({ evaluation, required }: { evaluation?: Evaluation; required: boolean }) {
  if (!evaluation || (evaluation.min_value === null && evaluation.max_value === null)) {
    return <>{required ? 'Required for this product' : 'Not run for this product'}</>
  }
  const parts: string[] = []
  if (evaluation.min_value !== null) parts.push(`min ${evaluation.min_value}`)
  if (evaluation.max_value !== null) parts.push(`max ${evaluation.max_value}`)
  return (
    <>
      Limit: {parts.join(' · ')}
      {evaluation.needs_review ? ' ⚠ pending confirmation' : ''}
    </>
  )
}

/* ------------------------------------------------------------ QA checklist */

function QaChecklists({ order, onChanged, canWrite }: { order: Detail; onChanged: () => void; canWrite: boolean }) {
  const { reference } = useApp()
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [responses, setResponses] = useState<Record<number, string>>({})
  const [trailer, setTrailer] = useState('')
  const [error, setError] = useState<any>(null)

  async function save() {
    setError(null)
    try {
      await api.post(`/api/orders/${order.order_id}/qa-checklist`, {
        responses, trailer_number: trailer, trailer_load_time: new Date().toISOString(),
      })
      toast.push('success', 'QA checklist saved')
      setOpen(false); setResponses({}); onChanged()
    } catch (err) { setError(err) }
  }

  return (
    <>
      <Card
        title="QA checklists"
        subtitle="Trailer and load checks completed before shipping"
        actions={canWrite ? <button className="primary sm" onClick={() => setOpen(true)}>New checklist</button> : undefined}
      >
        {order.qa_checklists.length === 0 ? (
          <div className="muted">No checklist completed for this order.</div>
        ) : (
          <div className="stack">
            {order.qa_checklists.map((header) => (
              <div key={header.header_id} className="card" style={{ boxShadow: 'none' }}>
                <div className="body">
                  <div className="row" style={{ justifyContent: 'space-between' }}>
                    <div>
                      <strong>Trailer {header.trailer_number || '—'}</strong>
                      <div className="small muted">
                        {fmtDateTime(header.date_added)} · {header.added_by}
                      </div>
                    </div>
                    {header.exceptions.length
                      ? <Badge tone="danger">{header.exceptions.length} exception(s)</Badge>
                      : <Badge tone="ok">All pass</Badge>}
                  </div>
                  <div className="divider" />
                  <dl className="kv">
                    {header.responses.map((response: any) => (
                      <div key={response.response_id} style={{ display: 'contents' }}>
                        <dt>{response.question}</dt>
                        <dd style={response.response === 'No' ? { color: 'var(--danger)', fontWeight: 650 } : undefined}>
                          {response.response}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {open && (
        <Modal
          title="QA checklist"
          subtitle={`Order ${order.order_id}`}
          onClose={() => setOpen(false)}
          width={640}
          footer={<>
            <button onClick={() => setOpen(false)}>Cancel</button>
            <button className="primary" onClick={save}>Save checklist</button>
          </>}
        >
          {error && <div style={{ marginBottom: 12 }}><ErrorBox error={error} /></div>}
          <Field label="Trailer #"><input value={trailer} onChange={(e) => setTrailer(e.target.value)} /></Field>
          <div className="divider" />
          <div className="stack">
            {reference.qa_questions.map((question) => (
              <div key={question.question_id} className="row" style={{ justifyContent: 'space-between', gap: 16 }}>
                <span>{question.question}</span>
                {question.answer_type === 'yesno' ? (
                  <div className="row" style={{ gap: 6 }}>
                    {['Yes', 'No', 'N/A'].map((option) => (
                      <button
                        key={option}
                        className={responses[question.question_id] === option ? 'primary sm' : 'sm'}
                        onClick={() => setResponses((current) => ({ ...current, [question.question_id]: option }))}
                      >
                        {option}
                      </button>
                    ))}
                  </div>
                ) : (
                  <input
                    style={{ maxWidth: 200 }}
                    type={question.answer_type === 'number' ? 'number' : 'text'}
                    value={responses[question.question_id] ?? ''}
                    onChange={(event) => setResponses((current) => ({
                      ...current, [question.question_id]: event.target.value,
                    }))}
                  />
                )}
              </div>
            ))}
          </div>
        </Modal>
      )}
    </>
  )
}

/* ---------------------------------------------------------------- history */

function History({ orderId }: { orderId: number }) {
  const trail = useAsync(
    () => api.get<AuditEntry[]>(`/api/orders/${orderId}/audit`), [orderId],
  )
  if (trail.loading) return <Loading />
  if (trail.error) return <ErrorBox error={trail.error} />

  return (
    <Card title="Change history" subtitle="Every change to this order, with before and after values" tight>
      <DataTable
        rows={trail.data ?? []}
        rowKey={(row) => row.audit_id}
        empty="No history recorded."
        columns={[
          { key: 'occurred_at', label: 'When', render: (row) => fmtDateTime(row.occurred_at) },
          { key: 'username', label: 'Who' },
          { key: 'action', label: 'Action', render: (row) => <Badge>{row.action}</Badge> },
          { key: 'summary', label: 'Summary' },
          {
            key: 'changes',
            label: 'Detail',
            render: (row) => {
              const changes = row.detail?.changes as Record<string, { from: unknown; to: unknown }> | undefined
              if (!changes || !Object.keys(changes).length) return <span className="muted">—</span>
              return (
                <div className="small">
                  {Object.entries(changes).map(([field, change]) => (
                    <div key={field}>
                      <span className="mono">{field}</span>: {String(change.from ?? '—')} → <strong>{String(change.to ?? '—')}</strong>
                    </div>
                  ))}
                </div>
              )
            },
          },
        ]}
      />
    </Card>
  )
}
