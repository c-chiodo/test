/* Order Selection / Order Edit, merged.
 *
 * The legacy client had two nearly identical grids behind different menu
 * buttons; the only real difference was which actions the buttons ran. One
 * grid with a selection and an action bar does the same job with half the
 * navigation. */

import { useMemo, useState } from 'react'
import { useApp, useToast } from '../App'
import { api, qs } from '../lib/api'
import type { Order } from '../lib/types'
import {
  Card, DataTable, ErrorBox, Field, Loading, Meter, Modal, StatusBadge,
  daysFromToday, fmtDate, fmtLbs, today, useAsync,
} from '../components/ui'

interface Filters {
  order_type_id?: number | ''
  department_id?: number | ''
  status_id?: number | ''
  material_id?: number | ''
  customer_id?: number | ''
  vendor_id?: number | ''
  due_from: string
  due_to: string
  text: string
  open_only: boolean
}

const EMPTY: Filters = {
  order_type_id: '', department_id: '', status_id: '', material_id: '',
  customer_id: '', vendor_id: '',
  due_from: daysFromToday(-60), due_to: daysFromToday(30), text: '', open_only: false,
}

export default function Orders() {
  const { plantId, reference, navigate, can } = useApp()
  const toast = useToast()
  const [filters, setFilters] = useState<Filters>(EMPTY)
  const [selected, setSelected] = useState<number[]>([])
  const [creating, setCreating] = useState(false)

  const result = useAsync(
    () => api.get<{ total: number; rows: Order[] }>(
      `/api/orders${qs({ plant_id: plantId, ...filters, limit: 500 })}`,
    ),
    [plantId, JSON.stringify(filters)],
  )

  const set = (patch: Partial<Filters>) => setFilters((current) => ({ ...current, ...patch }))

  async function closeSelected(force = false) {
    try {
      const outcome = await api.post<{ closed: number[]; skipped: { order_id: number; reason: string }[] }>(
        '/api/orders/close', { order_ids: selected, force },
      )
      if (outcome.closed.length) {
        toast.push('success', `Closed ${outcome.closed.length} order(s)`)
      }
      if (outcome.skipped.length) {
        toast.push(
          'error',
          `${outcome.skipped.length} order(s) not closed`,
          outcome.skipped.map((s) => `${s.order_id}: ${s.reason}`).join('; '),
        )
      }
      setSelected([])
      result.reload()
    } catch (error) {
      toast.push('error', 'Close failed', (error as Error).message)
    }
  }

  const rows = result.data?.rows ?? []

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Orders</h1>
          <div className="sub">
            {result.data ? `${result.data.total.toLocaleString()} matching order(s)` : 'Loading…'}
          </div>
        </div>
        <div className="actions">
          {selected.length > 0 && can('order.close') && (
            <>
              <button onClick={() => closeSelected(false)}>Close {selected.length} selected</button>
              <button className="ghost" onClick={() => setSelected([])}>Clear</button>
            </>
          )}
          {can('order.write') && (
            <button className="primary" onClick={() => setCreating(true)}>Create order</button>
          )}
        </div>
      </div>

      <Card title="Filter">
        <div className="form-grid">
          <Field label="Search">
            <input
              value={filters.text}
              placeholder="Order id, reference, blend SN, customer, material…"
              onChange={(event) => set({ text: event.target.value })}
            />
          </Field>
          <Field label="Type">
            <select value={filters.order_type_id} onChange={(e) => set({ order_type_id: e.target.value ? Number(e.target.value) : '' })}>
              <option value="">All types</option>
              {reference.order_types.map((type) => (
                <option key={type.order_type_id} value={type.order_type_id}>
                  {type.code} — {type.description}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Status">
            <select value={filters.status_id} onChange={(e) => set({ status_id: e.target.value ? Number(e.target.value) : '' })}>
              <option value="">All statuses</option>
              {reference.statuses.map((status) => (
                <option key={status.status_id} value={status.status_id}>{status.name}</option>
              ))}
            </select>
          </Field>
          <Field label="Department">
            <select value={filters.department_id} onChange={(e) => set({ department_id: e.target.value ? Number(e.target.value) : '' })}>
              <option value="">All departments</option>
              {reference.departments.map((dept) => (
                <option key={dept.department_id} value={dept.department_id}>
                  {dept.code} — {dept.description}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Material">
            <select value={filters.material_id} onChange={(e) => set({ material_id: e.target.value ? Number(e.target.value) : '' })}>
              <option value="">All materials</option>
              {reference.materials.map((material) => (
                <option key={material.material_id} value={material.material_id}>
                  {material.number} — {material.description}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Customer">
            <select value={filters.customer_id} onChange={(e) => set({ customer_id: e.target.value ? Number(e.target.value) : '' })}>
              <option value="">All customers</option>
              {reference.customers.map((customer) => (
                <option key={customer.customer_id} value={customer.customer_id}>{customer.name}</option>
              ))}
            </select>
          </Field>
          <Field label="Due from">
            <input type="date" value={filters.due_from} onChange={(e) => set({ due_from: e.target.value })} />
          </Field>
          <Field label="Due to">
            <input type="date" value={filters.due_to} onChange={(e) => set({ due_to: e.target.value })} />
          </Field>
          <div className="field" style={{ justifyContent: 'flex-end' }}>
            <label className="row small" style={{ gap: 6 }}>
              <input
                type="checkbox"
                checked={filters.open_only}
                onChange={(event) => set({ open_only: event.target.checked })}
              />
              Open orders only
            </label>
            <button className="ghost sm" onClick={() => setFilters(EMPTY)}>Reset filters</button>
          </div>
        </div>
      </Card>

      <Card title="Results" tight>
        {result.loading ? <Loading /> : result.error ? <ErrorBox error={result.error} /> : (
          <DataTable
            rows={rows}
            rowKey={(row) => row.order_id}
            onRowClick={(row) => navigate(`orders/${row.order_id}`)}
            empty="No orders match these filters."
            maxHeight="60vh"
            columns={[
              {
                key: 'select',
                label: '',
                width: 34,
                render: (row) => (
                  <input
                    type="checkbox"
                    checked={selected.includes(row.order_id)}
                    onClick={(event) => event.stopPropagation()}
                    onChange={(event) => setSelected((current) =>
                      event.target.checked
                        ? [...current, row.order_id]
                        : current.filter((id) => id !== row.order_id),
                    )}
                  />
                ),
              },
              { key: 'order_id', label: 'Order id' },
              { key: 'order_type', label: 'Type' },
              { key: 'due_date', label: 'Due', render: (row) => fmtDate(row.due_date) },
              { key: 'department_code', label: 'Dept' },
              {
                key: 'material',
                label: 'Material',
                render: (row) => row.material_one_number
                  ? `${row.material_one_number} · ${row.material_one_description}`
                  : '—',
              },
              {
                key: 'party',
                label: 'Customer / vendor',
                render: (row) => row.customer_name || row.vendor_name || '—',
              },
              { key: 'material_one_quantity', label: 'Ordered', numeric: true, render: (row) => fmtLbs(row.material_one_quantity) },
              { key: 'qty_fulfilled', label: 'Complete', numeric: true, render: (row) => fmtLbs(row.qty_fulfilled) },
              {
                key: 'percent_complete',
                label: 'Progress',
                width: 150,
                render: (row) => <Meter value={row.qty_fulfilled} max={row.material_one_quantity} variant="progress" />,
              },
              { key: 'status', label: 'Status', render: (row) => <StatusBadge status={row.status} /> },
            ]}
          />
        )}
      </Card>

      {creating && (
        <CreateOrder
          onClose={() => setCreating(false)}
          onCreated={(orderId) => { setCreating(false); result.reload(); navigate(`orders/${orderId}`) }}
        />
      )}
    </>
  )
}

function CreateOrder({ onClose, onCreated }: { onClose: () => void; onCreated: (orderId: number) => void }) {
  const { reference, plantId } = useApp()
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<any>(null)
  const [form, setForm] = useState<Record<string, any>>({
    order_type_id: 2,
    plant_id: plantId,
    company_id: reference.companies[0]?.company_id ?? 1,
    department_id: '',
    order_date: today(),
    due_date: today(),
    material_one_id: '',
    material_one_quantity: '',
    material_two_id: '',
    material_three_id: '',
    material_four_id: '',
    customer_id: '',
    vendor_id: '',
    order_reference: '',
    blend_serial_number: '',
    ship_method: '',
    trailer_number: '',
    comments: '',
    status_id: 1,
    count: 1,
  })

  const set = (patch: Record<string, any>) => setForm((current) => ({ ...current, ...patch }))
  const fieldError = (name: string) => error?.fields?.[name]

  const typeCode = useMemo(
    () => reference.order_types.find((t) => t.order_type_id === Number(form.order_type_id))?.code,
    [reference.order_types, form.order_type_id],
  )

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      const payload: Record<string, any> = { ...form }
      for (const key of Object.keys(payload)) {
        if (payload[key] === '') payload[key] = null
      }
      payload.count = Number(form.count) || 1
      const created = await api.post<{ created: Order[] }>('/api/orders', payload)
      toast.push('success', `Created order ${created.created[0].order_id}`,
        created.created.length > 1 ? `${created.created.length} orders created` : undefined)
      onCreated(created.created[0].order_id)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title="Create order"
      subtitle="Work, sales, purchase or transfer order"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" onClick={submit} disabled={busy}>
            {busy ? <span className="spinner" /> : null} Save
          </button>
        </>
      }
    >
      {error && <div style={{ marginBottom: 14 }}><ErrorBox error={error} /></div>}
      <div className="form-grid">
        <Field label="Order type" error={fieldError('order_type_id')}>
          <select value={form.order_type_id} onChange={(e) => set({ order_type_id: Number(e.target.value) })}>
            {reference.order_types.map((type) => (
              <option key={type.order_type_id} value={type.order_type_id}>
                {type.code} — {type.description}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Company" error={fieldError('company_id')}>
          <select value={form.company_id} onChange={(e) => set({ company_id: Number(e.target.value) })}>
            {reference.companies.map((company) => (
              <option key={company.company_id} value={company.company_id}>{company.name}</option>
            ))}
          </select>
        </Field>
        <Field label="Plant" error={fieldError('plant_id')}>
          <select value={form.plant_id} onChange={(e) => set({ plant_id: Number(e.target.value) })}>
            {reference.plants.map((plant) => (
              <option key={plant.plant_id} value={plant.plant_id}>{plant.code} — {plant.name}</option>
            ))}
          </select>
        </Field>

        <Field label="Order date">
          <input type="date" value={form.order_date} onChange={(e) => set({ order_date: e.target.value })} />
        </Field>
        <Field label="Due date" error={fieldError('due_date')}>
          <input type="date" value={form.due_date} onChange={(e) => set({ due_date: e.target.value })} />
        </Field>
        <Field label="Department">
          <select value={form.department_id} onChange={(e) => set({ department_id: e.target.value })}>
            <option value="">Select…</option>
            {reference.departments.map((dept) => (
              <option key={dept.department_id} value={dept.department_id}>
                {dept.code} — {dept.description}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Primary product" error={fieldError('material_one_id')} className="span-2">
          <select value={form.material_one_id} onChange={(e) => set({ material_one_id: e.target.value })}>
            <option value="">Select…</option>
            {reference.materials.map((material) => (
              <option key={material.material_id} value={material.material_id}>
                {material.number} — {material.description}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Quantity (lbs)" error={fieldError('material_one_quantity')}>
          <input
            type="number"
            value={form.material_one_quantity}
            onChange={(e) => set({ material_one_quantity: e.target.value })}
          />
        </Field>

        {typeCode === 'SO' && (
          <Field label="Customer" error={fieldError('customer_id')} className="span-2">
            <select value={form.customer_id} onChange={(e) => set({ customer_id: e.target.value })}>
              <option value="">Select…</option>
              {reference.customers.map((customer) => (
                <option key={customer.customer_id} value={customer.customer_id}>
                  {customer.name} ({customer.gp_custnmbr})
                </option>
              ))}
            </select>
          </Field>
        )}
        {typeCode === 'PO' && (
          <Field label="Vendor" error={fieldError('vendor_id')} className="span-2">
            <select value={form.vendor_id} onChange={(e) => set({ vendor_id: e.target.value })}>
              <option value="">Select…</option>
              {reference.vendors.map((vendor) => (
                <option key={vendor.vendor_id} value={vendor.vendor_id}>
                  {vendor.name} ({vendor.gp_vendorid})
                </option>
              ))}
            </select>
          </Field>
        )}
        {typeCode === 'WO' && (
          <Field label="Blend serial number">
            <input value={form.blend_serial_number} onChange={(e) => set({ blend_serial_number: e.target.value })} />
          </Field>
        )}

        <fieldset className="span-3">
          <legend>By-products to produce (optional)</legend>
          <div className="form-grid">
            {['material_two_id', 'material_three_id', 'material_four_id'].map((key, index) => (
              <Field key={key} label={`Material ${index + 2}`}>
                <select value={form[key]} onChange={(e) => set({ [key]: e.target.value })}>
                  <option value="">Not required</option>
                  {reference.materials.map((material) => (
                    <option key={material.material_id} value={material.material_id}>
                      {material.number} — {material.description}
                    </option>
                  ))}
                </select>
              </Field>
            ))}
          </div>
        </fieldset>

        <Field label="Order reference">
          <input value={form.order_reference} onChange={(e) => set({ order_reference: e.target.value })} />
        </Field>
        <Field label="Ship method">
          <input value={form.ship_method} onChange={(e) => set({ ship_method: e.target.value })} />
        </Field>
        <Field label="Trailer #">
          <input value={form.trailer_number} onChange={(e) => set({ trailer_number: e.target.value })} />
        </Field>

        <Field label="Comments" className="span-2">
          <input value={form.comments} onChange={(e) => set({ comments: e.target.value })} />
        </Field>
        <Field label="# of orders to create" hint="Creates identical orders, as the legacy dialog did.">
          <input
            type="number"
            min={1}
            max={50}
            value={form.count}
            onChange={(e) => set({ count: e.target.value })}
          />
        </Field>
      </div>
    </Modal>
  )
}
