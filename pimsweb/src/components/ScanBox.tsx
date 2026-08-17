/* One box for anything with a barcode on it.
 *
 * A scanner types the code and presses Enter, so the same control serves the
 * scanner and the keyboard. The server works out what was scanned; when more
 * than one thing matches, the operator picks. */

import { useEffect, useRef, useState } from 'react'
import { api, qs } from '../lib/api'

interface Hit {
  type: string
  id: string | number
  label: string
  sublabel: string
  route: string
  order_id?: number
}

export default function ScanBox({
  plantId, onNavigate, autoFocus = false,
}: { plantId: number; onNavigate: (route: string) => void; autoFocus?: boolean }) {
  const [code, setCode] = useState('')
  const [hits, setHits] = useState<Hit[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const input = useRef<HTMLInputElement>(null)

  // Ctrl/Cmd+K puts the cursor here without reaching for the mouse — the
  // shared terminal's equivalent of picking up the scanner.
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        input.current?.focus()
        input.current?.select()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    const query = code.trim()
    if (!query) return
    setBusy(true)
    setMessage('')
    try {
      const result = await api.get<{ hits: Hit[] }>(`/api/scan${qs({ code: query, plant_id: plantId })}`)
      if (result.hits.length === 1) {
        go(result.hits[0])
      } else if (result.hits.length === 0) {
        setHits([])
        setMessage(`Nothing in PIMS matches "${query}".`)
      } else {
        setHits(result.hits)
      }
    } catch (error) {
      setHits([])
      setMessage((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  function go(hit: Hit) {
    setHits(null)
    setCode('')
    onNavigate(hit.route)
  }

  return (
    <div style={{ position: 'relative' }}>
      <form className="scan" onSubmit={submit}>
        <span aria-hidden>⌗</span>
        <input
          ref={input}
          value={code}
          autoFocus={autoFocus}
          placeholder="Scan or type an order, sample, BOL, tank…"
          onChange={(event) => setCode(event.target.value)}
          onBlur={() => setTimeout(() => setHits(null), 200)}
          aria-label="Scan a code"
        />
        {busy ? <span className="spinner" /> : <span className="hintkey">⌘K</span>}
      </form>

      {hits !== null && (
        <div className="scan-results">
          {hits.length === 0 ? (
            <div className="empty small">{message || 'No match.'}</div>
          ) : (
            hits.map((hit) => (
              <button key={`${hit.type}-${hit.id}`} onMouseDown={() => go(hit)}>
                <div className="kind">{hit.type}</div>
                <div>{hit.label}</div>
                <div className="small muted">{hit.sublabel}</div>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  )
}
