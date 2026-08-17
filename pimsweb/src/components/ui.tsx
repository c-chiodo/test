/* The component vocabulary the screens are built from. */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

export function fmtNumber(value: unknown, digits = 0): string {
  const n = typeof value === 'number' ? value : Number(value)
  if (value === null || value === undefined || value === '' || Number.isNaN(n)) return '—'
  return n.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function fmtLbs(value: unknown): string {
  return fmtNumber(value, 0)
}

export function fmtDate(value: unknown): string {
  if (!value) return '—'
  const text = String(value)
  const date = new Date(text.length <= 10 ? `${text}T00:00:00Z` : text)
  if (Number.isNaN(date.getTime())) return text
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: '2-digit' })
}

export function fmtDateTime(value: unknown): string {
  if (!value) return '—'
  const date = new Date(String(value))
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

export function today(): string {
  return new Date().toISOString().slice(0, 10)
}

export function daysFromToday(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() + days)
  return date.toISOString().slice(0, 10)
}

/* ------------------------------------------------------------------ toasts */

interface Toast { id: number; kind: 'info' | 'success' | 'error'; title: string; body?: string }

const ToastContext = createContext<{
  push: (kind: Toast['kind'], title: string, body?: string) => void
}>({ push: () => undefined })

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const push = useCallback((kind: Toast['kind'], title: string, body?: string) => {
    const id = Date.now() + Math.random()
    setToasts((current) => [...current, { id, kind, title, body }])
    setTimeout(() => setToasts((current) => current.filter((t) => t.id !== id)), 6000)
  }, [])

  const value = useMemo(() => ({ push }), [push])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast ${toast.kind}`} onClick={() =>
            setToasts((current) => current.filter((t) => t.id !== toast.id))
          }>
            <div className="head">{toast.title}</div>
            {toast.body && <div className="small muted">{toast.body}</div>}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  return useContext(ToastContext)
}

/* -------------------------------------------------------------- primitives */

export function Card({
  title, subtitle, actions, children, tight,
}: {
  title?: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  children: ReactNode
  tight?: boolean
}) {
  return (
    <section className="card">
      {(title || actions) && (
        <header>
          <div>
            <h2>{title}</h2>
            {subtitle && <div className="sub">{subtitle}</div>}
          </div>
          <div className="spacer" />
          {actions}
        </header>
      )}
      <div className={`body${tight ? ' tight' : ''}`}>{children}</div>
    </section>
  )
}

export function Stat({
  label, value, foot, tone,
}: { label: string; value: ReactNode; foot?: ReactNode; tone?: 'alert' | 'warn' }) {
  return (
    <div className={`stat${tone ? ` ${tone}` : ''}`}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {foot && <div className="foot">{foot}</div>}
    </div>
  )
}

export function Badge({ children, tone }: { children: ReactNode; tone?: string }) {
  return <span className={`badge${tone ? ` ${tone}` : ''}`}>{children}</span>
}

export function StatusBadge({ status }: { status: string }) {
  const tone =
    status === 'Closed' || status === 'Cancelled' ? '' :
    status === 'Complete' ? 'ok' :
    status === 'In Process' ? 'info' : 'brand'
  return <Badge tone={tone}>{status}</Badge>
}

export function SpecBadge({ status }: { status: string }) {
  if (status === 'out_of_spec') return <Badge tone="danger">Out of spec</Badge>
  if (status === 'incomplete') return <Badge tone="warn">Incomplete</Badge>
  return <Badge tone="ok">In spec</Badge>
}

export function Meter({
  value, max, variant = 'capacity',
}: { value: number; max?: number | null; variant?: 'capacity' | 'progress' }) {
  if (!max) return <span className="muted small">—</span>
  const pct = Math.max(0, Math.min(100, (value / max) * 100))
  // A full tank is a warning; a fully fulfilled order is not.
  const tone = variant === 'progress'
    ? (pct >= 100 ? 'ok' : '')
    : pct > 95 ? 'danger' : pct > 85 ? 'warn' : 'ok'
  return (
    <div className="row" style={{ gap: 8, flexWrap: 'nowrap' }}>
      <div className={`meter ${tone}`}><span style={{ width: `${pct}%` }} /></div>
      <span className="small num muted">{pct.toFixed(0)}%</span>
    </div>
  )
}

export function Field({
  label, hint, error, children, className,
}: {
  label: string
  hint?: ReactNode
  error?: string
  children: ReactNode
  className?: string
}) {
  return (
    <div className={`field${error ? ' invalid' : ''}${className ? ` ${className}` : ''}`}>
      <label>{label}</label>
      {children}
      {error ? <div className="error">{error}</div> : hint ? <div className="hint">{hint}</div> : null}
    </div>
  )
}

export function Alert({
  tone = 'info', title, children,
}: { tone?: 'info' | 'warn' | 'danger' | 'ok'; title?: ReactNode; children?: ReactNode }) {
  const icon = tone === 'danger' ? '⛔' : tone === 'warn' ? '⚠' : tone === 'ok' ? '✓' : 'ⓘ'
  return (
    <div className={`alert ${tone}`}>
      <span className="icon">{icon}</span>
      <div>
        {title && <strong>{title}</strong>}
        {title && children ? <br /> : null}
        {children}
      </div>
    </div>
  )
}

export function Modal({
  title, subtitle, onClose, footer, children, width,
}: {
  title: ReactNode
  subtitle?: ReactNode
  onClose: () => void
  footer?: ReactNode
  children: ReactNode
  width?: number
}) {
  useEffect(() => {
    const handler = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div className="scrim" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div className="modal" style={width ? { width: `min(${width}px, 100%)` } : undefined}>
        <header>
          <div>
            <h2>{title}</h2>
            {subtitle && <div className="sub muted small">{subtitle}</div>}
          </div>
          <div className="spacer" />
          <button className="ghost" onClick={onClose} aria-label="Close">✕</button>
        </header>
        <div className="body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </div>
    </div>
  )
}

export function Tabs({
  tabs, active, onChange,
}: { tabs: { key: string; label: ReactNode }[]; active: string; onChange: (key: string) => void }) {
  return (
    <div className="tabs">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          className={tab.key === active ? 'active' : ''}
          onClick={() => onChange(tab.key)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}

export interface Column<T> {
  key: string
  label: string
  numeric?: boolean
  render?: (row: T) => ReactNode
  width?: number
}

export function DataTable<T extends Record<string, any>>({
  columns, rows, empty = 'No records.', onRowClick, rowKey, selectedKey, maxHeight,
}: {
  columns: Column<T>[]
  rows: T[]
  empty?: ReactNode
  onRowClick?: (row: T) => void
  rowKey?: (row: T, index: number) => string | number
  selectedKey?: string | number
  maxHeight?: string
}) {
  if (!rows.length) return <div className="empty">{empty}</div>
  return (
    <div className="table-wrap" style={maxHeight ? { maxHeight } : undefined}>
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} className={column.numeric ? 'num' : ''} style={column.width ? { width: column.width } : undefined}>
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const key = rowKey ? rowKey(row, index) : index
            return (
              <tr
                key={key}
                className={`${onRowClick ? 'clickable' : ''}${selectedKey === key ? ' selected' : ''}`}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
              >
                {columns.map((column) => (
                  <td key={column.key} className={column.numeric ? 'num' : ''}>
                    {column.render ? column.render(row) : formatCell(row[column.key])}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function formatCell(value: unknown): ReactNode {
  if (value === null || value === undefined || value === '') return <span className="muted">—</span>
  if (typeof value === 'number') return fmtNumber(value, Number.isInteger(value) ? 0 : 2)
  return String(value)
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="empty">
      <span className="spinner" /> <span style={{ marginLeft: 8 }}>{label}</span>
    </div>
  )
}

/* Field keys are database column names. They are the right key for a form to
 * match an error to an input, and the wrong thing to read out to an operator —
 * "from_material_id: Choose the material being taken." names a column nobody
 * on the floor has heard of. These are the labels the forms already use. */
const FIELD_LABELS: Record<string, string> = {
  from_location_id: 'From tank', from_material_id: 'Product', from_qty: 'Quantity',
  from_bol: 'BOL number', to_location_id: 'To location', to_material_id: 'To product',
  to_qty: 'Quantity in', to_bol: 'BOL number', trailer_number: 'Trailer #',
  plant_id: 'Plant', order_id: 'Order', department_id: 'Department',
  user_date: 'Transaction date', remarks: 'Reason', reason: 'Reason',
  tank_hours: 'Tank time', employee_hours: 'Labor', responses: 'Unanswered',
  moisture: 'Moisture', temp: 'Temperature', ph: 'pH', ffa: 'FFA', tfa: 'TFA',
  spintest_fallout: 'Spintest', flash_pf: 'Flash', sample_number: 'Sample #',
  seal_number: 'Seals', material_one_id: 'Product', material_one_quantity: 'Quantity',
  customer_id: 'Customer', vendor_id: 'Vendor', due_date: 'Due date',
  order_date: 'Order date', order_type_id: 'Order type', status_id: 'Status',
}

export function fieldLabel(field: string): string {
  return FIELD_LABELS[field]
    ?? field.replace(/_id$/, '').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
}

export function ErrorBox({ error }: { error: unknown }) {
  const err = error as { message?: string; correlationId?: string; fields?: Record<string, string> }
  if (!err) return null
  const fields = err.fields && Object.keys(err.fields).length ? err.fields : null
  return (
    <Alert tone="danger" title={err.message || 'Something went wrong.'}>
      {fields && (
        <ul style={{ margin: '6px 0 0 16px', padding: 0 }}>
          {Object.entries(fields).map(([field, message]) => (
            <li key={field}><strong>{fieldLabel(field)}</strong>: {message}</li>
          ))}
        </ul>
      )}
      {err.correlationId && (
        <div className="small" style={{ marginTop: 6, opacity: 0.85 }}>
          Reference for support: <span className="mono">{err.correlationId}</span>
        </div>
      )}
    </Alert>
  )
}

/** Data loader with loading / error states, refreshable by the caller. */
export function useAsync<T>(loader: () => Promise<T>, deps: unknown[]) {
  const [state, setState] = useState<{ data: T | null; loading: boolean; error: unknown }>({
    data: null, loading: true, error: null,
  })
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    let cancelled = false
    setState((current) => ({ ...current, loading: true, error: null }))
    loader()
      .then((data) => { if (!cancelled) setState({ data, loading: false, error: null }) })
      .catch((error) => { if (!cancelled) setState({ data: null, loading: false, error }) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  return { ...state, reload: () => setNonce((n) => n + 1) }
}
