/* Today: the whole shift on one screen.
 *
 * An operator used to hop between Orders, Load & ship, Blend and Plant floor
 * to find the next job. Here is every job that is waiting — trucks to load,
 * batches to blend, trailers to ship — each with one button, the scan box
 * for the paperwork in hand, and the tanks along the bottom. A staged trailer
 * ships from right here; everything else opens straight into its job. */

import { useEffect, useState } from 'react'
import { useApp, useToast } from '../App'
import { api, qs } from '../lib/api'
import type { Order, PendingShipment, TankBoardData } from '../lib/types'
import { Alert, Badge, ErrorBox, Loading, fmtDate, fmtLbs, today, useAsync } from '../components/ui'
import PopOutTanks from '../components/PopOutTanks'
import { readFlow } from './LoadAndShip'
import { STATE_LABEL } from './TankBoard'

const left = (row: Order) => row.material_one_quantity - row.qty_fulfilled

export default function Today() {
  const { plantId, plantCode, navigate, can, companion, user } = useApp()
  const toast = useToast()
  const [scanCode, setScanCode] = useState('')
  const [scanMessage, setScanMessage] = useState('')
  const [confirming, setConfirming] = useState<PendingShipment | null>(null)
  const [busy, setBusy] = useState(false)
  const [resume, setResume] = useState(() => readFlow())
  const canAct = can('txn.post') && !companion

  const sales = useAsync(
    () => api.get<{ rows: Order[] }>(`/api/orders${qs({ plant_id: plantId, order_type_id: 1, open_only: true, limit: 200 })}`),
    [plantId],
  )
  const work = useAsync(
    () => api.get<{ rows: Order[] }>(`/api/orders${qs({ plant_id: plantId, order_type_id: 2, open_only: true, limit: 200 })}`),
    [plantId],
  )
  const recipes = useAsync(() => api.get<any[]>('/api/blend/recipes'), [])
  const staged = useAsync(() => api.get<PendingShipment[]>(`/api/shipments/pending?plant_id=${plantId}`), [plantId])
  const tanks = useAsync(() => api.get<TankBoardData>(`/api/display/tanks?plant_id=${plantId}`), [plantId])

  // The shift's lists go stale as other people work; refresh them quietly.
  useEffect(() => {
    const timer = window.setInterval(() => { staged.reload(); tanks.reload() }, 60_000)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plantId])

  const blendable = new Set((recipes.data ?? []).map((r) => r.material_id))
  const toLoad = (sales.data?.rows ?? []).filter((r) => left(r) > 0.5)
    .sort((a, b) => a.due_date.localeCompare(b.due_date))
  const toBlend = (work.data?.rows ?? []).filter((r) => left(r) > 0.5 && blendable.has(r.material_one_id))
    .sort((a, b) => a.due_date.localeCompare(b.due_date))
  const toShip = staged.data ?? []
  const lateCount = (rows: Order[]) => rows.filter((r) => r.due_date < today()).length

  async function scan(event: React.FormEvent) {
    event.preventDefault()
    const code = scanCode.trim()
    if (!code) return
    setScanMessage('')
    try {
      const found = await api.get<{ hits: any[] }>(`/api/scan${qs({ code, plant_id: plantId })}`)
      const stage = toShip.find((s) => s.trailer_number === code || s.bol_number === code)
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
      const all = [...(sales.data?.rows ?? []), ...(work.data?.rows ?? [])]
      const order = all.find((o) => o.order_id === orderId)
      if (!canAct || !order) navigate(`orders/${orderId}`)
      else if (order.order_type_id === 1) navigate(`load-ship/${orderId}`)
      else if (order.order_type_id === 2 && blendable.has(order.material_one_id)) navigate(`blend/${orderId}`)
      else navigate(`orders/${orderId}`)
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

  return (
    <div className="today">
      <div className="page-head">
        <div>
          <h1>Today — {plantCode}</h1>
          <div className="sub">
            {companion
              ? 'What is waiting at this plant. Record the work in PIMS; it appears here at the next sync.'
              : `Every job waiting at this plant, ${user.full_name.split(' ')[0]}. Scan the paperwork, or tap a job.`}
          </div>
        </div>
      </div>

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

      <div className="lanes">
        <Lane
          title="Trucks to load"
          count={toLoad.length}
          late={lateCount(toLoad)}
          loading={sales.loading}
          error={sales.error}
          empty="Nothing waiting to load."
          rows={toLoad.slice(0, 12)}
          more={toLoad.length - 12}
          render={(row) => (
            <>
              <div className="job-main">
                <strong>{row.customer_name ?? '—'}</strong>
                <span>{row.material_one_number} · {row.material_one_description}</span>
              </div>
              <div className="job-side">
                <span className="job-qty">{fmtLbs(left(row))} lbs</span>
                <span className={row.due_date < today() ? 'job-late' : 'job-due'}>
                  {row.due_date < today() ? 'late · ' : 'due '}{fmtDate(row.due_date)}
                </span>
              </div>
              {canAct
                ? <button className="primary job-go" onClick={() => navigate(`load-ship/${row.order_id}`)}>Load</button>
                : <button className="job-go" onClick={() => navigate(`orders/${row.order_id}`)}>Open</button>}
            </>
          )}
        />
        <Lane
          title="Batches to blend"
          count={toBlend.length}
          late={lateCount(toBlend)}
          loading={work.loading || recipes.loading}
          error={work.error}
          empty="Nothing waiting to blend."
          rows={toBlend.slice(0, 12)}
          more={toBlend.length - 12}
          render={(row) => (
            <>
              <div className="job-main">
                <strong>{row.material_one_number} · {row.material_one_description}</strong>
                <span>Work order {row.order_id}{row.blend_serial_number ? ` · serial ${row.blend_serial_number}` : ''}</span>
              </div>
              <div className="job-side">
                <span className="job-qty">{fmtLbs(left(row))} lbs</span>
                <span className={row.due_date < today() ? 'job-late' : 'job-due'}>
                  {row.due_date < today() ? 'late · ' : 'due '}{fmtDate(row.due_date)}
                </span>
              </div>
              {canAct
                ? <button className="primary job-go" onClick={() => navigate(`blend/${row.order_id}`)}>Blend</button>
                : <button className="job-go" onClick={() => navigate(`orders/${row.order_id}`)}>Open</button>}
            </>
          )}
        />
        <Lane
          title="Trailers to ship"
          count={toShip.length}
          late={0}
          loading={staged.loading}
          error={staged.error}
          empty="No loaded trailers waiting."
          rows={toShip.slice(0, 12)}
          more={toShip.length - 12}
          render={(row: PendingShipment) => (
            <>
              <div className="job-main">
                <strong>Trailer {row.trailer_number || '—'}</strong>
                <span>{row.customer_name ?? '—'} · {row.material_number}</span>
              </div>
              <div className="job-side">
                <span className="job-qty">{fmtLbs(row.quantity)} lbs</span>
                <span className="job-due">BOL {row.bol_number}</span>
              </div>
              {canAct
                ? <button className="primary job-go" onClick={() => setConfirming(row)}>Ship</button>
                : <button className="job-go" onClick={() => navigate(`orders/${row.order_id}`)}>Open</button>}
            </>
          )}
        />
      </div>

      <section className="tank-strip">
        <div className="tank-strip-head">
          <strong>Tanks</strong>
          {tanks.data && tanks.data.abnormal > 0 && <Badge tone="danger">{tanks.data.abnormal} need attention</Badge>}
          <div className="spacer" />
          <PopOutTanks />
        </div>
        {tanks.loading && !tanks.data ? <Loading /> : tanks.error ? <ErrorBox error={tanks.error} /> : (
          <div className="mini-tanks">
            {(tanks.data?.tanks ?? []).map((tank) => (
              <div key={tank.location_id} className={`mini-tank state-${tank.state}`} title={tank.products[0]?.description ?? ''}>
                <div className="mini-gauge"><div style={{ height: `${Math.max(0, Math.min(100, tank.percent_full ?? 0))}%` }} /></div>
                <div className="mini-text">
                  <strong>{tank.number}</strong>
                  <span>{tank.percent_full === null ? '—' : `${Math.round(tank.percent_full)}%`}</span>
                  <span className="mini-product">{tank.products[0]?.lbs > 0.5 ? tank.products[0].number : 'empty'}</span>
                  {tank.state !== 'normal' && tank.state !== 'empty' && <span className="mini-state">{STATE_LABEL[tank.state]}</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

function Lane<T extends { order_id: number }>({
  title, count, late, loading, error, empty, rows, more, render,
}: {
  title: string
  count: number
  late: number
  loading: boolean
  error: unknown
  empty: string
  rows: T[]
  more: number
  render: (row: T) => React.ReactNode
}) {
  return (
    <section className="lane">
      <header className="lane-head">
        <h2>{title}</h2>
        <span className="lane-count">{count}</span>
        {late > 0 && <Badge tone="warn">{late} late</Badge>}
      </header>
      {loading && !rows.length ? <Loading /> : error ? <ErrorBox error={error} /> : rows.length === 0 ? (
        <div className="lane-empty">{empty}</div>
      ) : (
        <ul className="jobs">
          {rows.map((row, i) => <li key={(row as any).stage_id ?? row.order_id ?? i} className="job">{render(row)}</li>)}
          {more > 0 && <li className="job-more">and {more} more — scan the paperwork to go straight to one</li>}
        </ul>
      )}
    </section>
  )
}
