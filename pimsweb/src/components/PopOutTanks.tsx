/* Opens the tank board in its own window — for a second monitor, or a wall
 * screen — or hands over a link for a screen on another computer.
 *
 * The board runs on a display link of its own (see pims/services/display.py),
 * so it keeps running after the operator who opened it signs out. */

import { useRef, useState } from 'react'
import { DEMO, api } from '../lib/api'
import { useApp, useToast } from '../App'
import { Modal } from './ui'

export default function PopOutTanks({ className = 'sm' }: { className?: string }) {
  const { plantId, plantCode } = useApp()
  const toast = useToast()
  const [link, setLink] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const pushTimer = useRef<number | null>(null)

  async function boardUrl(): Promise<string> {
    const minted = await api.post<{ token: string }>('/api/display/token', {
      plant_id: plantId, label: `${plantCode} tank board`,
    })
    const base = window.location.href.split('#')[0]
    return `${base}#/board?t=${encodeURIComponent(minted.token)}&plant=${plantId}`
  }

  async function popOut() {
    setBusy(true)
    try {
      const url = await boardUrl()
      const popup = window.open(url, `pims-tanks-${plantId}`, 'popup,width=1400,height=860')
      if (!popup) {
        // Pop-ups blocked: offer the link instead, which also works for a
        // screen attached to another computer.
        setLink(url)
        return
      }
      if (DEMO) keepPushing(popup)
    } catch (err) {
      toast.push('error', 'Could not open the tank board', (err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // The sandbox has no server, so a new window would start from fresh data.
  // Push this window's live numbers to it instead, until it is closed.
  function keepPushing(popup: Window) {
    if (pushTimer.current) window.clearInterval(pushTimer.current)
    const push = async () => {
      if (popup.closed) {
        if (pushTimer.current) window.clearInterval(pushTimer.current)
        return
      }
      try {
        const data = await api.get(`/api/display/tanks?plant_id=${plantId}`)
        popup.postMessage({ type: 'pims-tanks', data }, '*')
      } catch { /* the board keeps its last numbers and says how old they are */ }
    }
    window.setTimeout(push, 600)
    pushTimer.current = window.setInterval(push, 5000)
  }

  async function showLink() {
    setBusy(true)
    try { setLink(await boardUrl()) }
    catch (err) { toast.push('error', 'Could not make a board link', (err as Error).message) }
    finally { setBusy(false) }
  }

  return (
    <>
      <span className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
        <button className={className} onClick={popOut} disabled={busy} title="Open the tank board in its own window">
          ⧉ Pop out tanks
        </button>
        <button className={`${className} ghost`} onClick={showLink} disabled={busy} title="A link for a screen on another computer">
          Link for another screen
        </button>
      </span>
      {link && (
        <Modal title="Tank board link" subtitle={`${plantCode} — every tank, refreshing on its own`} onClose={() => setLink('')} width={620}>
          <p className="small">
            Open this on the screen that should show the tanks — a second monitor, or a
            wall screen on another computer. It needs no sign-in, shows this plant's tank
            levels and nothing else, keeps running when you sign out, and lasts 90 days.
            A supervisor can switch it off from the support console.
          </p>
          <textarea readOnly value={link} rows={3} style={{ width: '100%', fontFamily: 'var(--mono)', fontSize: 12 }}
            onFocus={(event) => event.currentTarget.select()} />
          <div className="row end" style={{ gap: 8, marginTop: 10 }}>
            <button onClick={() => { navigator.clipboard?.writeText(link).then(() => toast.push('success', 'Link copied')).catch(() => undefined) }}>
              Copy link
            </button>
            <button className="primary" onClick={() => { window.location.href = link }}>Open here instead</button>
          </div>
        </Modal>
      )}
    </>
  )
}
