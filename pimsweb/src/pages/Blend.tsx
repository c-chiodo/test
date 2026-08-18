/* Blend: raw materials in, product out, one button.
 *
 * The operator picks the work order (or scans it); the recipe belongs to the
 * product, so they never pick a recipe. The screen shows the batch exactly as
 * it will post — each component in pounds, the tank supplying it, the tank it
 * lands in — and the server re-checks all of it inside one transaction, so
 * what was on the screen is what happens, whole or not at all. */

import { useRef, useState } from 'react'
import { useApp, useToast } from '../App'
import { api, newKey, qs } from '../lib/api'
import type { Order } from '../lib/types'
import {
  Alert, Badge, Card, DataTable, ErrorBox, Field, Loading,
  fmtDate, fmtLbs, useAsync,
} from '../components/ui'

interface PlanComponent {
  material_id: number
  material_number: string
  material_description: string
  percentage: number
  required: number
  from_location_id: number | null
  from_location_number: string | null
  available: number
  short: boolean
}

interface Plan {
  order_id: number | null
  material_id: number
  material_number: string
  material_description: string
  recipe: { recipe_id: number; name: string; notes: string }
  quantity: number
  components: PlanComponent[]
  to_location_id: number | null
  to_location_number: string | null
  destination_headroom: number | null
  short: boolean
  does_not_fit: boolean
  notes: string[]
}

export default function Blend() {
  const { plantCode, navigate } = useApp()
  const [orderId, setOrderId] = useState<number | null>(null)
  const [batch, setBatch] = useState<any>(null)

  function restart() {
    setOrderId(null)
    setBatch(null)
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Blend — {plantCode}</h1>
          <div className="sub">
            {orderId
              ? `Work order ${orderId}`
              : 'Pick the work order; the recipe and the tanks are already known.'}
          </div>
        </div>
        <div className="actions">
          {orderId && <button onClick={() => navigate(`orders/${orderId}`)}>Open order</button>}
          <button onClick={restart}>Start another</button>
        </div>
      </div>

      {!orderId && <PickWorkOrder onPicked={setOrderId} />}
      {orderId && !batch && (
        <BatchStep orderId={orderId} onBlended={setBatch} onBack={restart} />
      )}
      {orderId && batch && <DoneStep batch={batch} orderId={orderId} onRestart={restart} />}
    </>
  )
}

/* ------------------------------------------------------------- 1. order */

function PickWorkOrder({ onPicked }: { onPicked: (orderId: number) => void }) {
  const { plantId } = useApp()
  const [scanCode, setScanCode] = useState('')
  const [scanMessage, setScanMessage] = useState('')
  const result = useAsync(
    () => api.get<{ rows: Order[] }>(
      `/api/orders${qs({ plant_id: plantId, order_type_id: 2, open_only: true, limit: 100 })}`,
    ),
    [plantId],
  )

  async function scan(event: React.FormEvent) {
    event.preventDefault()
    const code = scanCode.trim()
    if (!code) return
    setScanMessage('')
    try {
      const found = await api.get<{ hits: any[] }>(`/api/scan${qs({ code, plant_id: plantId })}`)
      const hit = found.hits.find((h) => h.type === 'order' || h.order_id)
      if (hit) {
        onPicked(Number(hit.type === 'order' ? hit.id : hit.order_id))
        return
      }
      setScanMessage(`Nothing matches "${code}". Pick the work order from the list instead.`)
    } catch (error) {
      setScanMessage((error as Error).message)
    }
  }

  // Which products have a recipe: a work order for anything else is single-
  // input production, done on Plant floor -> Produce, and picking it here
  // would be a dead end. Say so on the row instead of after the click.
  const recipes = useAsync(() => api.get<any[]>('/api/blend/recipes'), [])
  const blendable = new Set((recipes.data ?? []).map((recipe) => recipe.material_id))

  // Orders this screen can actually blend come first, then work left, then the rest.
  const rows = [...(result.data?.rows ?? [])].sort((a, b) => {
    const left = (row: Order) => row.material_one_quantity - row.qty_fulfilled
    const rank = (row: Order) =>
      Number(blendable.has(row.material_one_id)) * 2 + Number(left(row) > 0.5)
    return rank(b) - rank(a)
  })
  const ready = rows.filter(
    (row) => blendable.has(row.material_one_id)
      && row.material_one_quantity - row.qty_fulfilled > 0.5,
  ).length

  return (
    <Card
      title="Work orders"
      subtitle={
        result.data
          ? `${ready} ready to blend · ${rows.length - ready} produced elsewhere or done`
          : 'Open work orders at this plant'
      }
      tight
    >
      <form className="scan-start" onSubmit={scan}>
        <label htmlFor="blend-scan">Scan the paperwork</label>
        <input
          id="blend-scan"
          autoFocus
          placeholder="Work order number"
          value={scanCode}
          onChange={(event) => { setScanCode(event.target.value); setScanMessage('') }}
        />
        <button type="submit" className="primary">Go</button>
        <span className="muted small">or pick from the list below</span>
      </form>
      {scanMessage && <div className="notice" role="status">{scanMessage}</div>}
      {result.loading ? <Loading /> : result.error ? <ErrorBox error={result.error} /> : (
        <DataTable
          rows={rows}
          rowKey={(row) => row.order_id}
          onRowClick={(row) => onPicked(row.order_id)}
          empty="No open work orders at this plant."
          columns={[
            { key: 'order_id', label: 'Order' },
            { key: 'due_date', label: 'Due', render: (row) => fmtDate(row.due_date) },
            {
              key: 'material',
              label: 'Product',
              render: (row) => (
                <span className="row" style={{ gap: 8 }}>
                  {row.material_one_number} · {row.material_one_description}
                  {!blendable.has(row.material_one_id) && (
                    <Badge>no recipe — made on Plant floor</Badge>
                  )}
                </span>
              ),
            },
            {
              key: 'remaining',
              label: 'Left to blend',
              numeric: true,
              render: (row) => {
                const left = row.material_one_quantity - row.qty_fulfilled
                if (left > 0.5) return fmtLbs(left)
                if (left < -0.5) return <Badge tone="warn">{fmtLbs(-left)} over</Badge>
                return <Badge tone="ok">produced</Badge>
              },
            },
            { key: 'blend_serial_number', label: 'Serial' },
            {
              key: 'go',
              label: '',
              render: (row) => (
                blendable.has(row.material_one_id)
                  ? <button className="sm primary">Blend this</button>
                  : null
              ),
            },
          ]}
        />
      )}
    </Card>
  )
}

/* ------------------------------------------------------------- 2. batch */

function BatchStep({
  orderId, onBlended, onBack,
}: { orderId: number; onBlended: (batch: any) => void; onBack: () => void }) {
  const { plantId, reference } = useApp()
  const toast = useToast()
  const [quantity, setQuantity] = useState<string>('')
  const [tanks, setTanks] = useState<Record<number, string>>({})
  const [error, setError] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const idempotencyKey = useRef(newKey())

  // Re-planned as the quantity changes, debounced. The plan writes nothing,
  // so asking on every pause is free — and the operator always sees the batch
  // the button will post, never a stale version of it.
  const plan = useAsync(
    () => api.get<Plan>(`/api/blend/plan${qs({ order_id: orderId, quantity: quantity || undefined })}`),
    [orderId, quantity],
    250,
  )
  const data = plan.data

  const locations = reference.locations.filter((location) => location.plant_id === plantId)
  const chosenTank = (component: PlanComponent) =>
    tanks[component.material_id] ?? String(component.from_location_id ?? '')

  async function blendIt() {
    if (!data) return
    setBusy(true); setError(null)
    try {
      const result = await api.post<any>('/api/blend/execute', {
        order_id: orderId,
        material_id: data.material_id,
        quantity: data.quantity,
        to_location_id: data.to_location_id,
        idempotency_key: idempotencyKey.current,
        components: data.components.map((component) => ({
          material_id: component.material_id,
          from_location_id: Number(chosenTank(component)),
          quantity: component.required,
        })),
      })
      toast.push('success', `Blended ${fmtLbs(result.quantity)} lbs`, `Batch ${result.batch_id}`)
      onBlended(result)
    } catch (err) { setError(err) } finally { setBusy(false) }
  }

  if (plan.loading && !data) return <Loading />
  if (plan.error && !data) {
    return (
      <Card title="This order cannot be blended here">
        <ErrorBox error={plan.error} />
        <div className="step-actions">
          <button className="primary" onClick={onBack}>Pick another order</button>
        </div>
      </Card>
    )
  }
  if (!data) return null

  const blocked = data.components.some((c) => {
    const tank = chosenTank(c)
    return !tank || (String(c.from_location_id ?? '') === tank && c.short)
  }) || data.does_not_fit || data.quantity <= 0

  return (
    <Card
      title={`${data.material_number} · ${data.material_description}`}
      subtitle={`Recipe: ${data.recipe.name}`}
    >
      {error && <div style={{ marginBottom: 12 }}><ErrorBox error={error} /></div>}
      {data.recipe.notes && (
        <div style={{ marginBottom: 12 }}>
          <Alert tone="info" title="About this recipe">{data.recipe.notes}</Alert>
        </div>
      )}

      <div className="form-grid cols-2" style={{ maxWidth: 560 }}>
        <Field label="Batch size (lbs)" hint="Pre-filled with what the order still needs">
          <input
            type="number"
            inputMode="decimal"
            value={quantity === '' ? data.quantity : quantity}
            onChange={(event) => setQuantity(event.target.value)}
          />
        </Field>
        <Field
          label="Into tank"
          hint={
            data.destination_headroom !== null
              ? `Room for ${fmtLbs(Math.max(data.destination_headroom, 0))} lbs`
              : undefined
          }
        >
          <input value={data.to_location_number ?? '—'} disabled />
        </Field>
      </div>

      <DataTable
        rows={data.components}
        rowKey={(row) => row.material_id}
        empty="The recipe has no components."
        columns={[
          {
            key: 'material_number',
            label: 'Component',
            render: (row) => `${row.material_number} · ${row.material_description}`,
          },
          { key: 'percentage', label: '%', numeric: true },
          { key: 'required', label: 'Lbs', numeric: true, render: (row) => fmtLbs(row.required) },
          {
            key: 'from',
            label: 'From tank',
            render: (row) => (
              <select
                value={chosenTank(row)}
                onChange={(event) => setTanks((current) => ({
                  ...current, [row.material_id]: event.target.value,
                }))}
              >
                <option value="">Select…</option>
                {locations.map((location) => (
                  <option key={location.location_id} value={location.location_id}>
                    {location.number}
                  </option>
                ))}
              </select>
            ),
          },
          {
            key: 'available',
            label: 'On hand',
            numeric: true,
            render: (row) => (
              String(row.from_location_id ?? '') === chosenTank(row) && row.short
                ? <Badge tone="danger">{fmtLbs(row.available)} — short</Badge>
                : fmtLbs(row.available)
            ),
          },
        ]}
      />

      {data.short && (
        <div style={{ marginTop: 12 }}>
          <Alert tone="warn" title="Not enough of a component">
            A smaller batch may fit — lower the batch size, or receive more of the
            short component first.
          </Alert>
        </div>
      )}
      {data.does_not_fit && (
        <div style={{ marginTop: 12 }}>
          <Alert tone="warn" title={`${data.to_location_number} cannot hold this batch`}>
            Room for {fmtLbs(Math.max(data.destination_headroom ?? 0, 0))} lbs. Lower the
            batch size, or move product out of the blend tank first.
          </Alert>
        </div>
      )}

      {data.notes.length > 0 && (
        <div className="why">
          {data.notes.map((note, i) => <span key={i}>{note}</span>)}
        </div>
      )}

      <div className="row end" style={{ marginTop: 16 }}>
        <button className="primary" onClick={blendIt} disabled={busy || blocked}>
          {busy ? <span className="spinner" /> : null} Blend {fmtLbs(data.quantity)} lbs
        </button>
      </div>
    </Card>
  )
}

/* -------------------------------------------------------------- 3. done */

function DoneStep({
  batch, orderId, onRestart,
}: { batch: any; orderId: number; onRestart: () => void }) {
  const { navigate } = useApp()
  return (
    <Card title={`Batch ${batch.batch_id}`}>
      <Alert tone="ok" title={`${fmtLbs(batch.quantity)} lbs of ${batch.product_number} into ${batch.to_location_number}`}>
        Every component and the product are in the ledger under this batch. QC on the
        blend is recorded against the order.
      </Alert>
      <DataTable
        rows={batch.transactions}
        rowKey={(row) => row.transaction_id}
        columns={[
          { key: 'transaction_id', label: 'Txn' },
          {
            key: 'from',
            label: 'Consumed',
            render: (row) => `${fmtLbs(row.from_qty)} lbs ${row.from_material_number} from ${row.from_location_number}`,
          },
          {
            key: 'to',
            label: 'Produced',
            render: (row) => `${fmtLbs(row.to_qty)} lbs ${row.to_material_number} into ${row.to_location_number}`,
          },
        ]}
      />
      <div className="step-actions">
        <button onClick={() => navigate(`orders/${orderId}`)}>Record QC on the order</button>
        <button className="primary" onClick={onRestart}>Next batch</button>
      </div>
    </Card>
  )
}
