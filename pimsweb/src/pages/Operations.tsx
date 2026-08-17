/* Plant floor: the six transaction screens.
 *
 * The legacy client had a separate window per operation (Receive, Produce,
 * Move, Load Trailer, Ship Trailer, Shrinkage) with the same fields arranged
 * differently in each. They share one form here, driven by a per-operation
 * spec, with the balance panels the originals showed under "Balance of
 * Materials at the selected From/To Location" — those were the useful part,
 * because they tell the operator whether the number they are about to type is
 * possible. */

import { useEffect, useMemo, useState } from 'react'
import { useApp, useToast } from '../App'
import { api, qs } from '../lib/api'
import type { Balance, Order, PendingShipment, Transaction } from '../lib/types'
import {
  Alert, Badge, Card, DataTable, ErrorBox, Field, Loading, Meter, Tabs,
  fmtDateTime, fmtLbs, today, useAsync,
} from '../components/ui'

interface OperationSpec {
  key: string
  label: string
  blurb: string
  from: boolean
  to: boolean
  needsOrder: boolean
  hours?: boolean
  trailer?: boolean
}

const OPERATIONS: OperationSpec[] = [
  { key: 'receive', label: 'Receive', blurb: 'Book incoming product into a tank against a purchase order.', from: false, to: true, needsOrder: true },
  { key: 'produce', label: 'Produce', blurb: 'Consume input from one location and produce output into another.', from: true, to: true, needsOrder: true, hours: true },
  { key: 'move', label: 'Move', blurb: 'Move inventory between locations without an order.', from: true, to: true, needsOrder: false, hours: true },
  { key: 'load', label: 'Load trailer', blurb: 'Load a trailer from a tank and stage it for shipping.', from: true, to: true, needsOrder: true, trailer: true },
  { key: 'ship', label: 'Ship trailer', blurb: 'Complete the shipment for a staged trailer and print the BOL.', from: false, to: false, needsOrder: false },
  { key: 'shrink', label: 'Shrinkage', blurb: 'Record product lost to heels, line loss or spillage.', from: true, to: false, needsOrder: false },
]

export default function Operations({ initialOperation }: { initialOperation?: string }) {
  const [operation, setOperation] = useState(() => {
    const key = (initialOperation || '').split('?')[0]
    return OPERATIONS.some((op) => op.key === key) ? key : 'receive'
  })
  const spec = OPERATIONS.find((op) => op.key === operation)!

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Plant floor</h1>
          <div className="sub">{spec.blurb}</div>
        </div>
      </div>

      <Tabs
        active={operation}
        onChange={setOperation}
        tabs={OPERATIONS.map((op) => ({ key: op.key, label: op.label }))}
      />

      {operation === 'ship' ? <ShipTrailer /> : <TransactionForm spec={spec} />}
    </>
  )
}

function TransactionForm({ spec }: { spec: OperationSpec }) {
  const { plantId, plantCode, reference, navigate } = useApp()
  const toast = useToast()
  const [form, setForm] = useState<Record<string, any>>({
    order_id: '',
    user_date: today(),
    from_location_id: '', from_material_id: '', from_qty: '', from_bol: '',
    to_location_id: '', to_material_id: '', to_qty: '', to_bol: '',
    trailer_number: '', tank_hours: '', employee_hours: '', remarks: '',
  })
  const [error, setError] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [posted, setPosted] = useState<Transaction | null>(null)

  useEffect(() => {
    setForm((current) => ({
      ...current,
      from_location_id: '', to_location_id: '', from_material_id: '', to_material_id: '',
      from_qty: '', to_qty: '',
    }))
    setPosted(null)
    setError(null)
  }, [spec.key, plantId])

  const set = (patch: Record<string, any>) => setForm((current) => ({ ...current, ...patch }))

  const openOrders = useAsync(
    () => api.get<{ rows: Order[] }>(`/api/orders${qs({ plant_id: plantId, open_only: true, limit: 300 })}`),
    [plantId],
  )
  const balances = useAsync(
    () => api.get<Balance[]>(`/api/balances?plant_id=${plantId}`), [plantId, posted?.transaction_id],
  )

  const locations = reference.locations.filter((location) => location.plant_id === plantId)
  const fromBalances = (balances.data ?? []).filter(
    (row) => String(row.location_id) === String(form.from_location_id),
  )
  const toBalances = (balances.data ?? []).filter(
    (row) => String(row.location_id) === String(form.to_location_id),
  )
  const available = fromBalances.find(
    (row) => String(row.material_id) === String(form.from_material_id),
  )?.balance ?? 0

  const order = useMemo(
    () => (openOrders.data?.rows ?? []).find((row) => String(row.order_id) === String(form.order_id)),
    [openOrders.data, form.order_id],
  )

  // Selecting an order fills in the product it is for, the way the legacy
  // screens pre-filled Material 1 from the order header.
  useEffect(() => {
    if (!order?.material_one_id) return
    setForm((current) => ({
      ...current,
      from_material_id: spec.from ? (current.from_material_id || order.material_one_id) : current.from_material_id,
      to_material_id: spec.to ? (current.to_material_id || order.material_one_id) : current.to_material_id,
    }))
  }, [order?.order_id])

  async function post() {
    setBusy(true); setError(null)
    try {
      const payload: Record<string, any> = { plant_id: plantId, ...form }
      for (const key of Object.keys(payload)) if (payload[key] === '') payload[key] = null
      if (spec.key === 'produce' && !payload.to_qty) payload.to_qty = payload.from_qty
      const result = await api.post<Transaction>(`/api/transactions/${spec.key}`, payload)
      setPosted(result)
      toast.push('success', `${spec.label} posted`, `Transaction ${result.transaction_id}`)
      setForm((current) => ({
        ...current, from_qty: '', to_qty: '', remarks: '', from_bol: '', to_bol: '',
      }))
      balances.reload()
    } catch (err) { setError(err) } finally { setBusy(false) }
  }

  const fieldError = (name: string) => error?.fields?.[name]

  return (
    <>
      <div className="grid cols-2">
        <Card title={`${spec.label} — ${plantCode}`}>
          {error && <div style={{ marginBottom: 12 }}><ErrorBox error={error} /></div>}
          {posted && (
            <div style={{ marginBottom: 12 }}>
              <Alert tone="ok" title={`Transaction ${posted.transaction_id} posted`}>
                {posted.from_location_number ? `${fmtLbs(posted.from_qty)} lbs out of ${posted.from_location_number}. ` : ''}
                {posted.to_location_number ? `${fmtLbs(posted.to_qty)} lbs into ${posted.to_location_number}.` : ''}
              </Alert>
            </div>
          )}

          <div className="form-grid cols-2">
            {spec.needsOrder ? (
              <Field label="Order" error={fieldError('order_id')} className="span-2">
                <select value={form.order_id} onChange={(event) => set({ order_id: event.target.value })}>
                  <option value="">Select an open order…</option>
                  {(openOrders.data?.rows ?? []).map((row) => (
                    <option key={row.order_id} value={row.order_id}>
                      {row.order_id} · {row.order_type} · {row.material_one_number} {row.material_one_description} · {fmtLbs(row.material_one_quantity)} lbs
                    </option>
                  ))}
                </select>
              </Field>
            ) : (
              <Field label="Order (optional)" className="span-2">
                <select value={form.order_id} onChange={(event) => set({ order_id: event.target.value })}>
                  <option value="">No order</option>
                  {(openOrders.data?.rows ?? []).map((row) => (
                    <option key={row.order_id} value={row.order_id}>
                      {row.order_id} · {row.material_one_number}
                    </option>
                  ))}
                </select>
              </Field>
            )}

            <Field label="Transaction date">
              <input type="date" value={form.user_date} onChange={(event) => set({ user_date: event.target.value })} />
            </Field>
            {spec.trailer && (
              <Field label="Trailer #" error={fieldError('trailer_number')}>
                <input value={form.trailer_number} onChange={(event) => set({ trailer_number: event.target.value })} />
              </Field>
            )}

            {spec.from && (
              <fieldset className="span-2">
                <legend>From (using)</legend>
                <div className="form-grid cols-2">
                  <Field label="From location" error={fieldError('from_location_id')}>
                    <select value={form.from_location_id} onChange={(event) => set({ from_location_id: event.target.value })}>
                      <option value="">Select…</option>
                      {locations.map((location) => (
                        <option key={location.location_id} value={location.location_id}>
                          {location.number} — {location.description}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="From material" error={fieldError('from_material_id')}>
                    <select value={form.from_material_id} onChange={(event) => set({ from_material_id: event.target.value })}>
                      <option value="">Select…</option>
                      {(fromBalances.length ? fromBalances.map((b) => ({
                        material_id: b.material_id, number: b.material_number, description: b.material_description,
                      })) : reference.materials).map((material: any) => (
                        <option key={material.material_id} value={material.material_id}>
                          {material.number} — {material.description}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field
                    label="Quantity (lbs)"
                    error={fieldError('from_qty')}
                    hint={form.from_material_id ? `On hand: ${fmtLbs(available)} lbs` : undefined}
                  >
                    <input
                      type="number"
                      value={form.from_qty}
                      onChange={(event) => set({
                        from_qty: event.target.value,
                        to_qty: spec.to && spec.key !== 'produce' ? event.target.value : form.to_qty,
                      })}
                    />
                  </Field>
                  <Field label="BOL">
                    <input value={form.from_bol} onChange={(event) => set({ from_bol: event.target.value })} />
                  </Field>
                </div>
                {form.from_qty && Number(form.from_qty) > available && (
                  <div style={{ marginTop: 10 }}>
                    <Alert tone="warn" title="More than the location holds">
                      {fmtLbs(available)} lbs on hand — this will be rejected when posted.
                    </Alert>
                  </div>
                )}
              </fieldset>
            )}

            {spec.to && (
              <fieldset className="span-2">
                <legend>To (produced / put away)</legend>
                <div className="form-grid cols-2">
                  <Field label="To location" error={fieldError('to_location_id')}>
                    <select value={form.to_location_id} onChange={(event) => set({ to_location_id: event.target.value })}>
                      <option value="">Select…</option>
                      {locations.map((location) => (
                        <option key={location.location_id} value={location.location_id}>
                          {location.number} — {location.description}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="To material" error={fieldError('to_material_id')}>
                    <select value={form.to_material_id} onChange={(event) => set({ to_material_id: event.target.value })}>
                      <option value="">Select…</option>
                      {reference.materials.map((material) => (
                        <option key={material.material_id} value={material.material_id}>
                          {material.number} — {material.description}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field
                    label="Quantity (lbs)"
                    error={fieldError('to_qty')}
                    hint={spec.key === 'produce' ? 'Yield may differ from the input quantity.' : undefined}
                  >
                    <input type="number" value={form.to_qty} onChange={(event) => set({ to_qty: event.target.value })} />
                  </Field>
                  <Field label="BOL" error={fieldError('to_bol')}>
                    <input value={form.to_bol} onChange={(event) => set({ to_bol: event.target.value })} />
                  </Field>
                </div>
              </fieldset>
            )}

            {spec.hours && (
              <>
                <Field label="Tank time (hrs)">
                  <input type="number" step="0.1" value={form.tank_hours} onChange={(event) => set({ tank_hours: event.target.value })} />
                </Field>
                <Field label="Labor (hrs)">
                  <input type="number" step="0.1" value={form.employee_hours} onChange={(event) => set({ employee_hours: event.target.value })} />
                </Field>
              </>
            )}

            <Field label="Remarks" className="span-2">
              <input value={form.remarks} onChange={(event) => set({ remarks: event.target.value })} />
            </Field>
          </div>

          <div className="row end" style={{ marginTop: 16 }}>
            {posted?.order_id && (
              <button onClick={() => navigate(`orders/${posted.order_id}`)}>Open order {posted.order_id}</button>
            )}
            <button className="primary" onClick={post} disabled={busy}>
              {busy ? <span className="spinner" /> : null} Post {spec.label.toLowerCase()}
            </button>
          </div>
        </Card>

        <div className="stack">
          {spec.from && (
            <Card title="Balance at the from location" tight>
              <BalancePanel
                rows={fromBalances}
                loading={balances.loading}
                selected={Boolean(form.from_location_id)}
              />
            </Card>
          )}
          {spec.to && (
            <Card title="Balance at the to location" tight>
              <BalancePanel
                rows={toBalances}
                loading={balances.loading}
                selected={Boolean(form.to_location_id)}
              />
            </Card>
          )}
          {order && (
            <Card title={`Order ${order.order_id}`} subtitle={`${order.order_type} · ${order.status}`}>
              <dl className="kv">
                <dt>Material</dt><dd>{order.material_one_number} {order.material_one_description}</dd>
                <dt>Ordered</dt><dd className="num">{fmtLbs(order.material_one_quantity)} lbs</dd>
                <dt>Fulfilled</dt><dd className="num">{fmtLbs(order.qty_fulfilled)} lbs</dd>
                <dt>Party</dt><dd>{order.customer_name || order.vendor_name || '—'}</dd>
              </dl>
              <div style={{ marginTop: 10 }}>
                <Meter value={order.qty_fulfilled} max={order.material_one_quantity} variant="progress" />
              </div>
            </Card>
          )}
        </div>
      </div>
    </>
  )
}

function BalancePanel({
  rows, loading, selected,
}: { rows: Balance[]; loading: boolean; selected: boolean }) {
  if (loading) return <Loading />
  return (
    <DataTable
      rows={rows}
      rowKey={(row) => `${row.location_id}-${row.material_id}`}
      empty={selected
        ? 'This location is empty — nothing on hand.'
        : 'Select a location to see what it holds.'}
      maxHeight="240px"
      columns={[
        { key: 'material_number', label: 'Material' },
        { key: 'material_description', label: 'Description' },
        { key: 'balance', label: 'Lbs', numeric: true, render: (row) => fmtLbs(row.balance) },
        { key: 'full', label: 'Full', render: (row) => <Meter value={row.balance} max={row.max_capacity} /> },
      ]}
    />
  )
}

/* ------------------------------------------------------------ ship trailer */

function ShipTrailer() {
  const { plantId, navigate } = useApp()
  const toast = useToast()
  const [busy, setBusy] = useState<number | null>(null)
  const [bol, setBol] = useState<any>(null)

  const staged = useAsync(
    () => api.get<PendingShipment[]>(`/api/shipments/pending?plant_id=${plantId}`), [plantId],
  )

  async function ship(stage: PendingShipment) {
    setBusy(stage.stage_id)
    try {
      await api.post(`/api/shipments/${stage.stage_id}/ship`, {})
      toast.push('success', `Trailer ${stage.trailer_number} shipped`, `Order ${stage.order_id}`)
      staged.reload()
    } catch (error) {
      toast.push('error', 'Ship failed', (error as Error).message)
    } finally { setBusy(null) }
  }

  async function preview(stage: PendingShipment) {
    try { setBol(await api.get(`/api/orders/${stage.order_id}/bol`)) }
    catch (error) { toast.push('error', 'Could not build the BOL', (error as Error).message) }
  }

  return (
    <div className="grid cols-2">
      <Card title="Staged trailers" subtitle="Loaded and waiting to ship" tight>
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
              { key: 'quantity', label: 'Lbs', numeric: true, render: (row) => fmtLbs(row.quantity) },
              { key: 'loaded_at', label: 'Loaded', render: (row) => fmtDateTime(row.loaded_at) },
              {
                key: 'actions',
                label: '',
                render: (row) => (
                  <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
                    <button className="sm" onClick={() => preview(row)}>BOL</button>
                    <button className="primary sm" disabled={busy === row.stage_id} onClick={() => ship(row)}>
                      Ship
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
