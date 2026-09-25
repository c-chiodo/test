/* Today: the whole shift on one screen.
 *
 * An operator used to hop between Orders, Load & ship, Blend and Plant floor
 * to find the next job. Here is every job that is waiting — trucks to load,
 * deliveries to receive, batches to run, trailers to ship — each with one
 * button, the scan box for the paperwork in hand, and the tanks along the
 * bottom. A staged trailer ships from right here; everything else opens
 * straight into its job.
 *
 * A terminal belongs to a department — the kiosk in the acid building is the
 * acid department's — so the department is chosen here once and remembered on
 * the device. Then the lanes hold that department's work only (the orders
 * booked to it), and the tanks are its tanks. "Everything" is always one tap
 * away for a supervisor. */

import { useEffect, useState } from 'react'
import { batchDepartments, useApp, useToast } from '../App'
import { api, qs } from '../lib/api'
import type { Department, Order, PendingShipment, ProcessBatch, TankBoardData } from '../lib/types'
import { Alert, Badge, ErrorBox, Loading, fmtDate, fmtLbs, today, useAsync } from '../components/ui'
import PopOutTanks from '../components/PopOutTanks'
import { readFlow } from './LoadAndShip'
import { clock } from './Process'
import { STATE_LABEL } from './TankBoard'

const left = (row: Order) => row.material_one_quantity - row.qty_fulfilled
const byDue = (a: Order, b: Order) => a.due_date.localeCompare(b.due_date)
const MAX_ROWS = 12

interface LaneSpec {
  key: string
  title: string
  rows: any[]
  late: number
  loading: boolean
  error: unknown
  empty: string
  /** Shown even when empty (the everyday lanes), or only when there is work. */
  always: boolean
  render: (row: any) => React.ReactNode
}

export default function Today() {
  const { plantId, plantCode, navigate, can, companion, user, departments, department, setDepartment } = useApp()
  const toast = useToast()
  const [scanCode, setScanCode] = useState('')
  const [scanMessage, setScanMessage] = useState('')
  const [confirming, setConfirming] = useState<PendingShipment | null>(null)
  const [busy, setBusy] = useState(false)
  const [resume, setResume] = useState(() => readFlow())
  const canAct = can('txn.post') && !companion
  const deptId = department?.department_id ?? null

  const openOrders = (type: number) => () =>
    api.get<{ rows: Order[] }>(`/api/orders${qs({ plant_id: plantId, order_type_id: type, open_only: true, limit: 300 })}`)
  const sales = useAsync(openOrders(1), [plantId])
  const work = useAsync(openOrders(2), [plantId])
  const purchases = useAsync(openOrders(3), [plantId])
  const recipes = useAsync(() => api.get<any[]>('/api/blend/recipes'), [])
  const staged = useAsync(() => api.get<PendingShipment[]>(`/api/shipments/pending?plant_id=${plantId}`), [plantId])
  // Batches under way in a reactor — they span shifts, so whoever is on now
  // sees them first.
  const running = useAsync(() => api.get<ProcessBatch[]>(`/api/process/batches?plant_id=${plantId}`), [plantId])
  const tanks = useAsync(
    () => api.get<TankBoardData>(`/api/display/tanks${qs({ plant_id: plantId, department_id: deptId ?? undefined })}`),
    [plantId, deptId],
  )

  // The shift's lists go stale as other people work; refresh them quietly.
  useEffect(() => {
    const timer = window.setInterval(() => { staged.reload(); tanks.reload(); running.reload() }, 60_000)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plantId, deptId])

  // A department sees the orders booked to it; "Everything" sees them all.
  const inDept = (row: { department_id?: number | null }) => !deptId || row.department_id === deptId
  const waiting = (rows: Order[] | undefined) => (rows ?? []).filter((r) => left(r) > 0.5 && inDept(r)).sort(byDue)
  const lateCount = (rows: Order[]) => rows.filter((r) => r.due_date < today()).length

  const toLoad = waiting(sales.data?.rows)
  const toReceive = waiting(purchases.data?.rows)
  const orderDept = new Map((sales.data?.rows ?? []).map((o) => [o.order_id, o.department_id]))
  const toShip = (staged.data ?? []).filter((s) => !deptId || orderDept.get(s.order_id) === deptId)

  // Batches: the blend room's, and one lane per department that runs its own
  // (Acid, in its reactor). Which lane a work order sits in is decided by its
  // product's recipe, so nothing here names a department.
  const ownScreens = batchDepartments(departments)
  const recipeFor = new Map((recipes.data ?? []).map((r) => [r.material_id, r]))
  const workWaiting = waiting(work.data?.rows).filter((r) => recipeFor.has(r.material_one_id))
  const batchLanes = [
    { dept: null as Department | null, title: 'Batches to blend' },
    ...ownScreens.map((d) => ({ dept: d as Department | null, title: `${d.description} batches` })),
  ].map(({ dept, title }) => {
    const staged = Boolean(dept?.methods?.includes('staged'))
    const underway = staged ? (running.data ?? []).filter((b) => b.department_id === dept!.department_id) : []
    const busyOrders = new Set(underway.map((b) => b.order_id))
    const rows: any[] = [
      ...underway,
      ...workWaiting.filter((r) => {
        const recipe = recipeFor.get(r.material_one_id)
        const mine = dept ? recipe.department_id === dept.department_id : (recipe.vessel_type ?? 'Blend') === 'Blend'
        return mine && !busyOrders.has(r.order_id)
      }),
    ]
    return { dept, title, rows, staged }
  })

  const lanes: LaneSpec[] = [
    {
      key: 'load', title: 'Trucks to load', rows: toLoad, late: lateCount(toLoad),
      loading: sales.loading, error: sales.error, empty: 'Nothing waiting to load.', always: true,
      render: (row: Order) => (
        <>
          <div className="job-main">
            <strong>{row.customer_name ?? '—'}</strong>
            <span>{row.material_one_number} · {row.material_one_description}</span>
          </div>
          <JobSide qty={left(row)} due={row.due_date} />
          <Go label="Load" act={canAct} onAct={() => navigate(`load-ship/${row.order_id}`)} onOpen={() => navigate(`orders/${row.order_id}`)} />
        </>
      ),
    },
    {
      key: 'receive', title: 'Deliveries to receive', rows: toReceive, late: lateCount(toReceive),
      loading: purchases.loading, error: purchases.error, empty: 'No deliveries expected.', always: false,
      render: (row: Order) => (
        <>
          <div className="job-main">
            <strong>{row.vendor_name ?? '—'}</strong>
            <span>{row.material_one_number} · {row.material_one_description}</span>
          </div>
          <JobSide qty={left(row)} due={row.due_date} />
          <Go label="Receive" act={canAct} onAct={() => navigate(`operations/receive/${row.order_id}`)} onOpen={() => navigate(`orders/${row.order_id}`)} />
        </>
      ),
    },
    ...batchLanes.map((lane): LaneSpec => ({
      key: `batch-${lane.dept?.department_id ?? 'blend'}`, title: lane.title, rows: lane.rows, late: lateCount(lane.rows),
      loading: work.loading || recipes.loading, error: work.error,
      empty: lane.dept ? `No ${lane.dept.description.toLowerCase()} batches waiting.` : 'Nothing waiting to blend.',
      always: !lane.dept,
      render: (row: any) => row.batch_id ? (
        // A batch in a reactor: its stage and how long it has been there.
        <>
          <div className="job-main">
            <strong>{row.vessel?.number} · {row.stage_label}</strong>
            <span>Batch {row.batch_id} · {fmtLbs(row.total_in)} lbs in · WO {row.order_id}</span>
          </div>
          <div className="job-side">
            <span className="job-qty">{clock(row.stage_minutes)}</span>
            <span className="job-due">{row.status === 'settling' ? 'ready to draw off when settled' : 'under way'}</span>
          </div>
          <button className={`${canAct ? 'primary ' : ''}job-go`} onClick={() => navigate(`process/${row.batch_id}`)}>Open</button>
        </>
      ) : (
        <>
          <div className="job-main">
            <strong>{row.material_one_number} · {row.material_one_description}</strong>
            <span>Work order {row.order_id}{row.blend_serial_number ? ` · serial ${row.blend_serial_number}` : ''}</span>
          </div>
          <JobSide qty={left(row)} due={row.due_date} />
          <Go
            label={lane.staged ? 'Start' : lane.dept ? 'Run' : 'Blend'}
            act={canAct}
            onAct={() => navigate(lane.dept ? `batches/${lane.dept.department_id}/${row.order_id}` : `blend/${row.order_id}`)}
            onOpen={() => navigate(`orders/${row.order_id}`)}
          />
        </>
      ),
    })),
    {
      key: 'ship', title: 'Trailers to ship', rows: toShip, late: 0,
      loading: staged.loading, error: staged.error, empty: 'No loaded trailers waiting.', always: true,
      render: (row: PendingShipment) => (
        <>
          <div className="job-main">
            <strong>Trailer {row.trailer_number || '—'}</strong>
            <span>{row.customer_name ?? '—'} · {row.material_number}</span>
          </div>
          <div className="job-side">
            <span className="job-qty">{fmtLbs(row.quantity)} lbs</span>
            <span className="job-due">BOL {row.bol_number}</span>
          </div>
          <Go label="Ship" act={canAct} onAct={() => setConfirming(row)} onOpen={() => navigate(`orders/${row.order_id}`)} />
        </>
      ),
    },
  ]
  // With a department chosen, only the lanes that hold its work; with
  // everything, the everyday lanes always and the others when they have rows.
  const settled = !sales.loading && !work.loading && !purchases.loading && !staged.loading && !recipes.loading && !running.loading
  const shown = lanes.filter((lane) => (deptId ? lane.rows.length > 0 : lane.always || lane.rows.length > 0))

  async function scan(event: React.FormEvent) {
    event.preventDefault()
    const code = scanCode.trim()
    if (!code) return
    setScanMessage('')
    try {
      const found = await api.get<{ hits: any[] }>(`/api/scan${qs({ code, plant_id: plantId })}`)
      const stage = (staged.data ?? []).find((s) => s.trailer_number === code || s.bol_number === code)
      if (stage && canAct) { setConfirming(stage); setScanCode(''); return }
      const hit = found.hits.find((h) => h.type === 'order' || h.order_id)
      if (!hit) {
        // A tank or a product: open where it lives rather than say nothing.
        if (found.hits[0]?.route) { setScanCode(''); navigate(found.hits[0].route); return }
        setScanMessage(`Nothing in PIMS matches "${code}".`)
        return
      }
      const orderId = Number(hit.type === 'order' ? hit.id : hit.order_id)
      setScanCode('')
      // Straight to the job that order needs, not to the order.
      const all = [...(sales.data?.rows ?? []), ...(work.data?.rows ?? []), ...(purchases.data?.rows ?? [])]
      const order = all.find((o) => o.order_id === orderId)
      const recipe = order ? recipeFor.get(order.material_one_id) : null
      if (!canAct || !order) navigate(`orders/${orderId}`)
      else if (order.order_type_id === 1) navigate(`load-ship/${orderId}`)
      else if (order.order_type_id === 3) navigate(`operations/receive/${orderId}`)
      else if (order.order_type_id === 2 && recipe) {
        const own = ownScreens.find((d) => d.department_id === recipe.department_id)
        navigate(own ? `batches/${own.department_id}/${orderId}` : `blend/${orderId}`)
      } else navigate(`orders/${orderId}`)
    } catch (error) {
      setScanMessage((error as Error).message)
    }
  }

  async function ship(stage: PendingShipment) {
    setBusy(true)
    try {
      await api.post(`/api/shipments/${stage.stage_id}/ship`, {})
      toast.push('success', `Trailer ${stage.trailer_number} shipped`, `${fmtLbs(stage.quantity)} lbs · order ${stage.order_id}`)
      setConfirming(null)
      staged.reload()
      tanks.reload()
      sales.reload()
    } catch (error) {
      toast.push('error', 'Not shipped', (error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const unshipped = resume && resume.loadTxn && !resume.shipped
  // Only departments that have work to show: a department with no orders and
  // no batches of its own would be a button that always opens an empty page.
  const pickable = departments.filter((d) => d.open_orders > 0 || d.runs_batches || d.department_id === deptId)

  return (
    <div className="today">
      <div className="page-head">
        <div>
          <h1>{department ? `${department.description} — ${plantCode}` : `Today — ${plantCode}`}</h1>
          <div className="sub">
            {companion
              ? 'What is waiting at this plant. Record the work in PIMS; it appears here at the next sync.'
              : `Every job waiting${department ? ` for ${department.description}` : ' at this plant'}, ${user.full_name.split(' ')[0]}. Scan the paperwork, or tap a job.`}
          </div>
        </div>
      </div>

      {pickable.length > 1 && (
        <div className="dept-picker" role="group" aria-label="Department">
          <span className="dept-label">This screen is for</span>
          <button
            className={!deptId ? 'primary' : ''}
            aria-pressed={!deptId}
            onClick={() => setDepartment(null)}
          >
            Everything
          </button>
          {pickable.map((d) => (
            <button
              key={d.department_id}
              className={deptId === d.department_id ? 'primary' : ''}
              aria-pressed={deptId === d.department_id}
              onClick={() => setDepartment(d.department_id)}
            >
              {d.description}
            </button>
          ))}
        </div>
      )}

      {unshipped && canAct && (
        <div style={{ marginBottom: 14 }}>
          <Alert tone="warn" title="You were part way through loading a truck">
            Trailer <strong>{resume!.loadTxn.trailer_number || '—'}</strong> was loaded with{' '}
            {fmtLbs(resume!.loadTxn.from_qty)} lbs and has not shipped yet.
            <div className="row" style={{ gap: 8, marginTop: 10 }}>
              <button className="primary" onClick={() => navigate('load-ship')}>Carry on with it</button>
              <button onClick={() => { try { sessionStorage.removeItem('pims.flow') } catch { /* */ } setResume(null) }}>
                Not now
              </button>
            </div>
          </Alert>
        </div>
      )}

      <form className="scan-start today-scan" onSubmit={scan}>
        <label htmlFor="today-scan">Scan the paperwork</label>
        <input
          id="today-scan"
          autoFocus
          placeholder="Order, BOL or trailer number"
          value={scanCode}
          onChange={(event) => { setScanCode(event.target.value); setScanMessage('') }}
        />
        <button type="submit" className="primary">Go</button>
      </form>
      {scanMessage && <div className="notice" role="status">{scanMessage}</div>}

      {confirming && (
        <div style={{ marginBottom: 14 }}>
          <Alert tone="warn" title={`Ship trailer ${confirming.trailer_number || '—'}?`}>
            {fmtLbs(confirming.quantity)} lbs of {confirming.material_number} {confirming.material_description}
            {' '}on BOL {confirming.bol_number}, order {confirming.order_id} for {confirming.customer_name ?? '—'}.
            <div className="row" style={{ gap: 8, marginTop: 10 }}>
              <button className="primary" disabled={busy} onClick={() => ship(confirming)}>
                {busy ? <span className="spinner" /> : null} Yes, ship trailer {confirming.trailer_number}
              </button>
              <button onClick={() => setConfirming(null)}>Cancel</button>
            </div>
          </Alert>
        </div>
      )}

      {shown.length === 0 && settled ? (
        <div className="lane-empty today-empty">
          Nothing waiting for {department?.description ?? 'this plant'} right now.
        </div>
      ) : (
        <div className="lanes">
          {shown.map((lane) => <Lane key={lane.key} spec={lane} />)}
        </div>
      )}

      <section className="tank-strip">
        <div className="tank-strip-head">
          <strong>{department ? `${department.description} tanks` : 'Tanks'}</strong>
          {tanks.data && tanks.data.abnormal > 0 && <Badge tone="danger">{tanks.data.abnormal} need attention</Badge>}
          <div className="spacer" />
          <PopOutTanks department={department} />
        </div>
        {tanks.loading && !tanks.data ? <Loading /> : tanks.error ? <ErrorBox error={tanks.error} /> : (
          <div className="mini-tanks">
            {(tanks.data?.tanks ?? []).length === 0 && <div className="muted small">No tanks hold this department's products.</div>}
            {(tanks.data?.tanks ?? []).map((tank) => (
              <div key={tank.location_id} className={`mini-tank state-${tank.state}`} title={tank.products[0]?.description ?? ''}>
                <div className="mini-gauge"><div style={{ height: `${Math.max(0, Math.min(100, tank.percent_full ?? 0))}%` }} /></div>
                <div className="mini-text">
                  <strong>{tank.number}</strong>
                  <span>{tank.percent_full === null ? '—' : `${Math.round(tank.percent_full)}%`}</span>
                  <span className="mini-product">{tank.products[0]?.lbs > 0.5 ? tank.products[0].number : 'empty'}</span>
                  {tank.state !== 'normal' && tank.state !== 'empty' && <span className="mini-state">{STATE_LABEL[tank.state]}</span>}
                  {tank.batch && <span className="mini-batch">{tank.batch.label} · {clock(tank.batch.minutes)}</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

function JobSide({ qty, due }: { qty: number; due: string }) {
  const late = due < today()
  return (
    <div className="job-side">
      <span className="job-qty">{fmtLbs(qty)} lbs</span>
      <span className={late ? 'job-late' : 'job-due'}>{late ? 'late · ' : 'due '}{fmtDate(due)}</span>
    </div>
  )
}

function Go({ label, act, onAct, onOpen }: { label: string; act: boolean; onAct: () => void; onOpen: () => void }) {
  return act
    ? <button className="primary job-go" onClick={onAct}>{label}</button>
    : <button className="job-go" onClick={onOpen}>Open</button>
}

function Lane({ spec }: { spec: LaneSpec }) {
  const { title, rows, late, loading, error, empty, render } = spec
  return (
    <section className="lane">
      <header className="lane-head">
        <h2>{title}</h2>
        <span className="lane-count">{rows.length}</span>
        {late > 0 && <Badge tone="warn">{late} late</Badge>}
      </header>
      {loading && !rows.length ? <Loading /> : error ? <ErrorBox error={error} /> : rows.length === 0 ? (
        <div className="lane-empty">{empty}</div>
      ) : (
        <ul className="jobs">
          {rows.slice(0, MAX_ROWS).map((row, i) => (
            <li key={row.batch_id ?? row.stage_id ?? row.order_id ?? i} className="job">{render(row)}</li>
          ))}
          {rows.length > MAX_ROWS && (
            <li className="job-more">and {rows.length - MAX_ROWS} more — scan the paperwork to go straight to one</li>
          )}
        </ul>
      )}
    </section>
  )
}
