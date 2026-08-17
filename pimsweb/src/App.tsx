/* Application shell: session, plant context, navigation and routing.
 *
 * The legacy client had a title bar showing "Logged in as: … ( DES MOINES )"
 * and a red banner when you were pointed at the test environment. Both were
 * genuinely useful on a shared plant terminal, so both survive — in the top
 * bar, where they stay visible on every screen.
 *
 * Kiosk mode is the same app with the chrome cut back: a plant terminal signs
 * in with a PIN, shows only the screens a loader needs, and signs itself out
 * when the shift walks away. */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, DEMO, api, setNoticeSink, token as tokenStore } from './lib/api'
import type { Reference, User } from './lib/types'
import { ErrorBox, Loading, ToastProvider, useToast } from './components/ui'
import ScanBox from './components/ScanBox'
import Login from './pages/Login'
import Kiosk, { kioskPlantId } from './pages/Kiosk'
import Dashboard from './pages/Dashboard'
import Orders from './pages/Orders'
import OrderDetail from './pages/OrderDetail'
import LoadAndShip from './pages/LoadAndShip'
import Operations from './pages/Operations'
import Inventory from './pages/Inventory'
import Inquiry from './pages/Inquiry'
import QueryBuilder from './pages/QueryBuilder'
import Specs from './pages/Specs'
import Support from './pages/Support'

interface AppState {
  user: User
  reference: Reference
  plantId: number
  plantCode: string
  setPlantId: (id: number) => void
  environment: string
  navigate: (route: string) => void
  can: (permission: string) => boolean
  reloadReference: () => void
  logout: () => void
  kiosk: boolean
}

const AppContext = createContext<AppState | null>(null)

export function useApp(): AppState {
  const state = useContext(AppContext)
  if (!state) throw new Error('useApp must be used inside the app shell')
  return state
}

export { useToast }

const KIOSK_KEY = 'pims.kiosk'
/** Sign a shared terminal out after this long with nobody touching it. */
const KIOSK_IDLE_SECONDS = 180

interface NavItem { route: string; label: string; icon: string; permission?: string; kiosk?: boolean }

const NAV: { group: string; items: NavItem[] }[] = [
  { group: 'Operations', items: [
    { route: 'dashboard', label: 'Dashboard', icon: '▤' },
    { route: 'load-ship', label: 'Load & ship', icon: '⇢', kiosk: true },
    { route: 'orders', label: 'Orders', icon: '▦', kiosk: true },
    { route: 'operations', label: 'Plant floor', icon: '⚙', kiosk: true },
    { route: 'inventory', label: 'Inventory', icon: '⛁', kiosk: true },
  ]},
  { group: 'Analysis', items: [
    { route: 'inquiry', label: 'Inquiry', icon: '⌕' },
    { route: 'query', label: 'Custom query', icon: '⧉' },
    { route: 'specs', label: 'Products & limits', icon: '✓' },
  ]},
  { group: 'Administration', items: [
    { route: 'support', label: 'Support console', icon: '⛨', permission: 'support.read' },
  ]},
]

function useHashRoute(): [string[], (route: string) => void] {
  const [hash, setHash] = useState(() => window.location.hash.replace(/^#\/?/, '') || 'dashboard')

  useEffect(() => {
    const handler = () => setHash(window.location.hash.replace(/^#\/?/, '') || 'dashboard')
    window.addEventListener('hashchange', handler)
    return () => window.removeEventListener('hashchange', handler)
  }, [])

  const navigate = useCallback((route: string) => {
    window.location.hash = `#/${route}`
  }, [])

  return [hash.split('/').filter(Boolean), navigate]
}

export default function App() {
  return (
    <ToastProvider>
      <Session />
    </ToastProvider>
  )
}

/** Lets the API layer surface sandbox-only notices as toasts. */
function useNoticeSink(): void {
  const toast = useToast()
  useEffect(() => {
    setNoticeSink((title, body) => toast.push('info', title, body))
  }, [toast])
}

function Session() {
  useNoticeSink()
  const [user, setUser] = useState<User | null>(null)
  const [checking, setChecking] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [kiosk, setKiosk] = useState(
    () => localStorage.getItem(KIOSK_KEY) === '1'
      || window.location.hash.replace(/^#\/?/, '').startsWith('kiosk'),
  )

  useEffect(() => {
    if (window.location.hash.replace(/^#\/?/, '').startsWith('kiosk')) {
      localStorage.setItem(KIOSK_KEY, '1')
      setKiosk(true)
      window.location.hash = '#/load-ship'
    }
  }, [])

  useEffect(() => {
    if (!tokenStore.get()) { setChecking(false); return }
    api.get<User>('/api/auth/me')
      .then(setUser)
      .catch((err) => { if (!(err instanceof ApiError && err.status === 401)) setError(err) })
      .finally(() => setChecking(false))
  }, [])

  const signOut = useCallback(() => { tokenStore.clear(); setUser(null) }, [])

  if (checking) return <div className="login"><Loading label="Signing in…" /></div>
  if (!user) {
    return kiosk
      ? <Kiosk onSignedIn={setUser} />
      : <Login onSignedIn={setUser} error={error} />
  }
  return (
    <Shell
      user={user}
      kiosk={kiosk}
      onSignOut={signOut}
      onExitKiosk={() => { localStorage.removeItem(KIOSK_KEY); setKiosk(false) }}
      onEnterKiosk={() => { localStorage.setItem(KIOSK_KEY, '1'); setKiosk(true) }}
    />
  )
}

/** Sign out after a spell with no keyboard, mouse or touch. */
function useIdleSignOut(enabled: boolean, seconds: number, onIdle: () => void): number {
  const [remaining, setRemaining] = useState(seconds)
  const last = useRef(Date.now())

  useEffect(() => {
    if (!enabled) return undefined
    const touch = () => { last.current = Date.now() }
    const events = ['mousedown', 'keydown', 'touchstart', 'wheel']
    events.forEach((event) => window.addEventListener(event, touch, { passive: true }))
    const timer = setInterval(() => {
      const left = seconds - Math.floor((Date.now() - last.current) / 1000)
      setRemaining(Math.max(left, 0))
      if (left <= 0) onIdle()
    }, 1000)
    return () => {
      events.forEach((event) => window.removeEventListener(event, touch))
      clearInterval(timer)
    }
  }, [enabled, seconds, onIdle])

  return remaining
}

function Shell({
  user, kiosk, onSignOut, onExitKiosk, onEnterKiosk,
}: {
  user: User
  kiosk: boolean
  onSignOut: () => void
  onExitKiosk: () => void
  onEnterKiosk: () => void
}) {
  const [route, navigate] = useHashRoute()
  const [plantId, setPlantIdState] = useState<number>(() => {
    const terminal = kioskPlantId()
    if (terminal && user.plants.some((p) => p.plant_id === terminal)) return terminal
    const saved = Number(localStorage.getItem('pims.plant'))
    if (saved && user.plants.some((p) => p.plant_id === saved)) return saved
    return user.plants[0]?.plant_id ?? 1
  })
  const [reference, setReference] = useState<Reference | null>(null)
  const [referenceError, setReferenceError] = useState<unknown>(null)
  const [nonce, setNonce] = useState(0)
  const [health, setHealth] = useState<{ environment: string } | null>(null)

  useEffect(() => {
    document.body.classList.toggle('kiosk', kiosk)
    return () => document.body.classList.remove('kiosk')
  }, [kiosk])

  useEffect(() => {
    api.get<Reference>(`/api/reference?plant_id=${plantId}`)
      .then((data) => { setReference(data); setReferenceError(null) })
      .catch(setReferenceError)
  }, [plantId, nonce])

  useEffect(() => { api.get<{ environment: string }>('/api/health').then(setHealth).catch(() => undefined) }, [])

  const setPlantId = useCallback((id: number) => {
    localStorage.setItem('pims.plant', String(id))
    setPlantIdState(id)
  }, [])

  const logout = useCallback(() => {
    api.post('/api/auth/logout').catch(() => undefined).finally(onSignOut)
  }, [onSignOut])

  const idleRemaining = useIdleSignOut(kiosk, KIOSK_IDLE_SECONDS, logout)

  const can = useCallback(
    (permission: string) =>
      user.permissions.includes('*') || user.permissions.includes(permission),
    [user],
  )

  const plantCode = useMemo(
    () => user.plants.find((p) => p.plant_id === plantId)?.code ?? '—',
    [user.plants, plantId],
  )

  const state: AppState | null = useMemo(
    () => reference ? {
      user, reference, plantId, plantCode, setPlantId,
      environment: health?.environment ?? '',
      navigate, can, kiosk,
      reloadReference: () => setNonce((n) => n + 1),
      logout,
    } : null,
    [user, reference, plantId, plantCode, setPlantId, health, navigate, can, logout, kiosk],
  )

  const page = route[0] || (kiosk ? 'load-ship' : 'dashboard')
  const isProduction = (health?.environment || '').toUpperCase() === 'PRODUCTION'
  const kioskItems = NAV.flatMap((section) => section.items).filter((item) => item.kiosk)

  return (
    <div className="app">
      {!kiosk && (
        <aside className="sidebar">
          <div className="brand">
            <div className="mark">PI</div>
            <div>
              <div className="name">PIMS</div>
              <div className="sub">Feed Energy</div>
            </div>
          </div>
          <nav className="nav">
            {NAV.map((section) => {
              const items = section.items.filter((item) => !item.permission || can(item.permission))
              if (!items.length) return null
              return (
                <div key={section.group}>
                  <div className="group">{section.group}</div>
                  {items.map((item) => (
                    <a
                      key={item.route}
                      href={`#/${item.route}`}
                      className={page === item.route ? 'active' : ''}
                    >
                      <span className="icon">{item.icon}</span>
                      {item.label}
                    </a>
                  ))}
                </div>
              )
            })}
          </nav>
        </aside>
      )}

      <div className="main">
        {kiosk ? (
          <header className="kiosk-bar">
            <span className="who">{user.full_name}</span>
            <span className="muted">{plantCode}</span>
            {kioskItems.map((item) => (
              <button
                key={item.route}
                className={page === item.route ? 'primary sm' : 'sm'}
                onClick={() => navigate(item.route)}
              >
                {item.label}
              </button>
            ))}
            {state && <ScanBox plantId={plantId} onNavigate={navigate} />}
            <span className={`timer${idleRemaining <= 30 ? ' soon' : ''}`}>
              signs out in {Math.floor(idleRemaining / 60)}:{String(idleRemaining % 60).padStart(2, '0')}
            </span>
            <button className="sm" onClick={logout}>Sign out</button>
            <button className="ghost sm" onClick={onExitKiosk} title="Return to the full application">
              Exit kiosk
            </button>
          </header>
        ) : (
          <header className="topbar">
            <span className="title">{titleFor(page)}</span>
            {health && (
              <span className={`env-banner${isProduction ? ' production' : ''}`}>
                {health.environment || 'unknown'}
              </span>
            )}
            <div className="spacer" />
            {state && <ScanBox plantId={plantId} onNavigate={navigate} />}
            <label className="row small" style={{ gap: 6 }}>
              <span className="muted">Plant</span>
              <select
                value={plantId}
                onChange={(event) => setPlantId(Number(event.target.value))}
                style={{ width: 'auto' }}
              >
                {user.plants.map((plant) => (
                  <option key={plant.plant_id} value={plant.plant_id}>
                    {plant.code} · {plant.name}
                  </option>
                ))}
              </select>
            </label>
            <span className="small muted nowrap">
              {user.full_name} · {user.role}
            </span>
            {DEMO && (
              <span className="small muted nowrap" title="Changes live in this browser tab only; reload to reset.">
                in-browser demo
              </span>
            )}
            <button className="ghost sm" onClick={onEnterKiosk} title="Shared plant terminal mode">
              Kiosk
            </button>
            <button className="ghost sm" onClick={logout}>Sign out</button>
          </header>
        )}

        <main className="content">
          {referenceError ? <ErrorBox error={referenceError} /> : null}
          {!state ? <Loading /> : (
            <AppContext.Provider value={state}>
              <Route path={route.length ? route : [page]} />
            </AppContext.Provider>
          )}
        </main>
      </div>
    </div>
  )
}

function Route({ path }: { path: string[] }) {
  const [page, param] = path
  switch (page) {
    case 'orders':
      return param ? <OrderDetail orderId={Number(param)} /> : <Orders />
    case 'load-ship':
      return <LoadAndShip initialOrderId={param ? Number(param) : undefined} />
    case 'operations':
      return <Operations initialOperation={param} />
    case 'inventory':
      return <Inventory />
    case 'inquiry':
      return <Inquiry initialTab={param} />
    case 'query':
      return <QueryBuilder />
    case 'specs':
      return <Specs />
    case 'support':
      return <Support />
    default:
      return <Dashboard />
  }
}

function titleFor(page: string): string {
  return {
    dashboard: 'Dashboard',
    orders: 'Orders',
    'load-ship': 'Load & ship',
    operations: 'Plant floor',
    inventory: 'Inventory',
    inquiry: 'Inquiry',
    query: 'Custom query',
    specs: 'Products & limits',
    support: 'Support console',
  }[page] ?? 'PIMS'
}
