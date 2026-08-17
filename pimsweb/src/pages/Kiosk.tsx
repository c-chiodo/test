/* Kiosk sign-in for a shared plant terminal.
 *
 * A loadout screen is used by whoever is on shift, with gloves on, standing up.
 * Passwords do not survive that: either everyone shares one, or the terminal
 * stays signed in as whoever used it last and the audit trail becomes fiction.
 * So: pick your name, tap a PIN, get a short session. */

import { useEffect, useState } from 'react'
import { api, qs, token as tokenStore } from '../lib/api'
import type { Plant, User } from '../lib/types'
import { ErrorBox, Loading, useAsync } from '../components/ui'

const KIOSK_PLANT_KEY = 'pims.kiosk.plant'

export function kioskPlantId(): number | null {
  const stored = Number(localStorage.getItem(KIOSK_PLANT_KEY))
  return stored > 0 ? stored : null
}

export default function Kiosk({ onSignedIn }: { onSignedIn: (user: User) => void }) {
  const [plantId, setPlantId] = useState<number | null>(kioskPlantId())
  const [person, setPerson] = useState<{ username: string; full_name: string } | null>(null)
  const [pin, setPin] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  const plants = useAsync(() => api.get<Plant[]>('/api/kiosk/plants'), [])
  const people = useAsync(
    () => (plantId
      ? api.get<{ username: string; full_name: string; role: string }[]>(
          `/api/auth/kiosk-users${qs({ plant_id: plantId })}`)
      : Promise.resolve([])),
    [plantId],
  )

  // A scanner or keypad can type the PIN; Enter submits.
  useEffect(() => {
    if (pin.length >= 4 && person) {
      const handle = setTimeout(() => submit(), 120)
      return () => clearTimeout(handle)
    }
    return undefined
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pin])

  async function submit() {
    if (!person || pin.length < 4 || busy) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.post<{ token: string; user: User }>('/api/auth/pin', {
        username: person.username, pin, plant_id: plantId,
      })
      tokenStore.set(result.token)
      onSignedIn(result.user)
    } catch (err) {
      setError(err)
      setPin('')
    } finally {
      setBusy(false)
    }
  }

  if (!plantId) {
    return (
      <div className="login">
        <div className="panel" style={{ gridTemplateColumns: '1fr' }}>
          <form onSubmit={(event) => event.preventDefault()}>
            <h2>Set up this terminal</h2>
            <p className="muted small">
              Choose the plant this screen lives at. It is remembered on this device.
            </p>
            {plants.loading ? <Loading /> : (
              <div className="people">
                {(plants.data ?? []).map((plant) => (
                  <button
                    key={plant.plant_id}
                    onClick={() => {
                      localStorage.setItem(KIOSK_PLANT_KEY, String(plant.plant_id))
                      setPlantId(plant.plant_id)
                    }}
                  >
                    <strong>{plant.code}</strong>
                    <span className="role">{plant.name}</span>
                  </button>
                ))}
              </div>
            )}
          </form>
        </div>
      </div>
    )
  }

  return (
    <div className="login">
      <div className="panel" style={{ gridTemplateColumns: '1fr' }}>
        <form onSubmit={(event) => { event.preventDefault(); submit() }}>
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <h2>{person ? `PIN for ${person.full_name}` : 'Who is loading?'}</h2>
            <button
              type="button"
              className="ghost sm"
              onClick={() => { localStorage.removeItem(KIOSK_PLANT_KEY); setPlantId(null) }}
            >
              Change plant
            </button>
          </div>

          {!person ? (
            people.loading ? <Loading /> : (
              <div className="people">
                {(people.data ?? []).map((option) => (
                  <button key={option.username} type="button" onClick={() => setPerson(option)}>
                    <strong>{option.full_name}</strong>
                    <span className="role">{option.role}</span>
                  </button>
                ))}
                {(people.data ?? []).length === 0 && (
                  <span className="muted small">
                    Nobody at this plant has a PIN yet. Set one in the support console.
                  </span>
                )}
              </div>
            )
          ) : (
            <>
              <div className="pin-display">{'•'.repeat(pin.length)}</div>
              {error ? <ErrorBox error={error} /> : null}
              <div className="pinpad">
                {['1', '2', '3', '4', '5', '6', '7', '8', '9'].map((digit) => (
                  <button key={digit} type="button" onClick={() => setPin((current) => (current + digit).slice(0, 8))}>
                    {digit}
                  </button>
                ))}
                <button type="button" onClick={() => { setPerson(null); setPin('') }}>←</button>
                <button type="button" onClick={() => setPin((current) => (current + '0').slice(0, 8))}>0</button>
                <button type="button" onClick={() => setPin((current) => current.slice(0, -1))}>⌫</button>
              </div>
              {busy && <div className="row" style={{ marginTop: 10 }}><span className="spinner" /> Checking…</div>}
            </>
          )}
        </form>
      </div>
    </div>
  )
}
