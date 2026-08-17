import { useState } from 'react'
import { DEMO, api, token as tokenStore } from '../lib/api'
import type { User } from '../lib/types'
import { ErrorBox, Field } from '../components/ui'

export default function Login({
  onSignedIn, error: initialError,
}: { onSignedIn: (user: User) => void; error?: unknown }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(initialError ?? null)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const result = await api.post<{ token: string; user: User }>('/api/auth/login', {
        username, password,
      })
      tokenStore.set(result.token)
      onSignedIn(result.user)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login">
      <div className="panel">
        <div className="pitch">
          <h1>PIMS</h1>
          <p style={{ marginTop: 6, opacity: 0.85 }}>
            Production Inventory Management System — Feed Energy
          </p>
          <ul>
            <li>Orders, receiving, production, movement and shipping</li>
            <li>Quality control with live product limits</li>
            <li>Inquiry, custom query and export</li>
            <li>Support console with health and audit</li>
          </ul>
          <p className="small" style={{ marginTop: 24, opacity: 0.7 }}>
            {DEMO
              ? 'Sandbox build: the seeded dataset and the rules run inside this page. Post movements, record QC, run queries — everything reacts. Nothing is saved; reload to start over.'
              : 'Replaces the Windows client. Same workflows, browser-based, with the business rules documented and tested.'}
          </p>
        </div>
        <form onSubmit={submit}>
          <h2>Sign in</h2>
          <Field label="Username">
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoFocus
              autoComplete="username"
            />
          </Field>
          <Field label="Password">
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
            />
          </Field>
          {error ? <ErrorBox error={error} /> : null}
          <button className="primary" disabled={busy || !username || !password}>
            {busy ? <span className="spinner" /> : null}
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
          <div className="small muted">
            Sign in as <span className="mono">cchiodo</span> (admin),{' '}
            <span className="mono">jmartin</span> (supervisor),{' '}
            <span className="mono">rprice</span> (QC) or{' '}
            <span className="mono">toperator</span> (operator) — password{' '}
            <span className="mono">pims-demo</span>
            {DEMO ? ' (any password works in the sandbox).' : '.'}
          </div>
        </form>
      </div>
    </div>
  )
}
