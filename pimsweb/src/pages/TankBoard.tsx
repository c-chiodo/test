/* The tank board: every tank at a plant, readable from across the room.
 *
 * Opened in its own window — on a second monitor beside PIMS, or a wall
 * screen at the loadout — with no navigation, no sign-in and nothing to
 * click. It runs on a display link that can read one plant's tank levels
 * and nothing else, so it keeps going when the operator who opened it signs
 * out. It refreshes itself, and if it cannot, it says how old its numbers
 * are: a frozen board that looks live is worse than no board.
 *
 * Colour follows the control-room convention (ISA-101): a normal tank is
 * plain grey; only an abnormal one — nearly full, over capacity, nearly
 * empty — is coloured, and always with a word as well as a colour. */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { DEMO, api, qs } from '../lib/api'
import { clock } from './Process'
import type { TankBoardData, TankTile } from '../lib/types'
import { fmtLbs } from '../components/ui'

const REFRESH_SECONDS = 30
/** Past this, the board says its numbers are old. */
const STALE_SECONDS = 90

export const STATE_LABEL: Record<string, string> = {
  over: 'OVER CAPACITY',
  high: 'NEARLY FULL',
  warn: 'FILLING',
  low: 'NEARLY EMPTY',
  empty: 'EMPTY',
  negative: 'CHECK — NEGATIVE',
}

function boardParams(): { token: string; plantId: number | null; departmentId: number | null } {
  const query = window.location.hash.split('?')[1] ?? ''
  const params = new URLSearchParams(query)
  return {
    token: params.get('t') ?? '',
    plantId: params.get('plant') ? Number(params.get('plant')) : null,
    departmentId: params.get('dept') ? Number(params.get('dept')) : null,
  }
}

function ago(iso: string | null, now: number): string {
  if (!iso) return 'no movements'
  const seconds = Math.max(0, (now - new Date(iso).getTime()) / 1000)
  if (seconds < 90) return 'moved just now'
  const minutes = seconds / 60
  if (minutes < 90) return `moved ${Math.round(minutes)} min ago`
  const hours = minutes / 60
  if (hours < 36) return `moved ${Math.round(hours)} h ago`
  return `moved ${Math.round(hours / 24)} days ago`
}

export default function TankBoard() {
  const { token, plantId, departmentId } = useMemo(boardParams, [])
  const [data, setData] = useState<TankBoardData | null>(null)
  const [fetchedAt, setFetchedAt] = useState<number>(0)
  const [error, setError] = useState<string>('')
  const [now, setNow] = useState(Date.now())
  const [pushed, setPushed] = useState(false)

  useEffect(() => {
    document.body.classList.add('board')
    document.title = 'PIMS · Tanks'
    return () => document.body.classList.remove('board')
  }, [])

  const load = useCallback(async () => {
    try {
      const result = await api.get<TankBoardData>(
        `/api/display/tanks${qs({
          token, plant_id: token ? undefined : plantId ?? undefined, department_id: departmentId ?? undefined,
        })}`,
      )
      setData(result)
      setFetchedAt(Date.now())
      setError('')
    } catch (err) {
      setError((err as Error).message)
    }
  }, [token, plantId, departmentId])

  // In the sandbox there is no server: the window that opened this board
  // pushes its live numbers across, so a load posted there shows here.
  useEffect(() => {
    if (!DEMO) return undefined
    const onMessage = (event: MessageEvent) => {
      if (event.data?.type === 'pims-tanks' && event.data.data) {
        setData(event.data.data)
        setFetchedAt(Date.now())
        setPushed(true)
        setError('')
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])

  useEffect(() => {
    const first = window.setTimeout(() => { if (!pushed) load() }, DEMO ? 1500 : 0)
    const timer = window.setInterval(() => { if (!pushed) load() }, REFRESH_SECONDS * 1000)
    const clock = window.setInterval(() => setNow(Date.now()), 1000)
    return () => { window.clearTimeout(first); window.clearInterval(timer); window.clearInterval(clock) }
  }, [load, pushed])

  const age = fetchedAt ? Math.round((now - fetchedAt) / 1000) : null
  const stale = age !== null && age > STALE_SECONDS

  function fullScreen() {
    const el = document.documentElement
    if (document.fullscreenElement) document.exitFullscreen().catch(() => undefined)
    else el.requestFullscreen?.().catch(() => undefined)
  }

  if (!data) {
    return (
      <div className="board-empty">
        {error ? <><h1>Tank board can't load</h1><p>{error}</p></> : <p>Loading tanks…</p>}
      </div>
    )
  }

  const abnormal = data.tanks.filter((t) => ['over', 'high', 'negative'].includes(t.state))

  return (
    <div className="board-page">
      <header className="board-head">
        <div className="board-title">
          <strong>{data.plant.code}</strong> {data.plant.name} · {data.department ? `${data.department.description} tanks` : 'Tanks'}
        </div>
        {abnormal.length > 0 && (
          <div className="board-alarm" role="status">
            {abnormal.length} tank{abnormal.length === 1 ? '' : 's'} need attention:{' '}
            {abnormal.map((t) => t.number).join(', ')}
          </div>
        )}
        <div className="board-spacer" />
        <div className={`board-fresh${stale || error ? ' stale' : ''}`} role="status">
          {error || stale
            ? `Not updating — numbers are ${age !== null ? Math.round(age / 60) || 1 : '?'} min old`
            : `Live · updated ${age ?? 0}s ago`}
        </div>
        <div className="board-clock">
          {new Date(now).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </div>
        <button className="board-button" onClick={fullScreen} title="Full screen (Esc to leave)">
          ⛶ Full screen
        </button>
      </header>

      <main className={`board-grid${stale || error ? ' is-stale' : ''}`}>
        {data.tanks.map((tank) => <Tile key={tank.location_id} tank={tank} now={now} />)}
      </main>

      {DEMO && (
        <footer className="board-foot">
          Sandbox — {pushed ? 'showing the live numbers from the window that opened this board.' : 'showing the starting data.'}
        </footer>
      )}
    </div>
  )
}

function Tile({ tank, now }: { tank: TankTile; now: number }) {
  const pct = tank.percent_full
  const fill = pct === null ? (tank.total > 0 ? 100 : 0) : Math.max(0, Math.min(100, pct))
  const main = tank.products[0]
  const label = STATE_LABEL[tank.state]
  return (
    <section className={`tile state-${tank.state}`} aria-label={`${tank.number}: ${label ?? 'normal'}`}>
      <div className="tile-top">
        <span className="tile-number">{tank.number}</span>
        {label && <span className="tile-state">{label}</span>}
      </div>
      <div className="tile-body">
        <div className="gauge" aria-hidden="true">
          <div className="gauge-fill" style={{ height: `${fill}%` }} />
          <div className="gauge-mark m95" />
          <div className="gauge-mark m85" />
        </div>
        <div className="tile-figures">
          <div className="tile-pct">{pct === null ? '—' : `${Math.round(pct)}%`}</div>
          <div className="tile-lbs">{fmtLbs(tank.total)} lbs</div>
          {tank.capacity ? (
            <div className="tile-room">
              {tank.room !== null && tank.room >= 0
                ? `room for ${fmtLbs(tank.room)}`
                : `${fmtLbs(-(tank.room ?? 0))} over`}
            </div>
          ) : <div className="tile-room">no stated capacity</div>}
        </div>
      </div>
      <div className="tile-product">
        {main && main.lbs > 0.5 ? (
          <>
            <strong>{main.number}</strong> {main.description}
            {tank.mixed && <span className="tile-mixed"> + {tank.products.filter((p) => p.lbs > 0.5).length - 1} more</span>}
          </>
        ) : <span className="muted-on-dark">—</span>}
      </div>
      {tank.batch && (
        <div className="tile-batch">
          Batch {tank.batch.batch_id} · <strong>{tank.batch.label}</strong>
          {tank.batch.minutes !== null ? ` · ${clock(tank.batch.minutes)}` : ''}
        </div>
      )}
      <div className="tile-foot">{ago(tank.last_moved, now)}</div>
    </section>
  )
}
