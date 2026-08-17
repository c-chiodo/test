/* API client.
 *
 * One place that knows about the token, the correlation id and how the server
 * reports errors, so every screen fails the same way: a readable message, the
 * field-level detail when there is one, and the id support needs to find the
 * request in the log. */

export interface ApiErrorBody {
  code: string
  message: string
  detail?: Record<string, unknown>
  correlation_id?: string
}

export class ApiError extends Error {
  code: string
  detail: Record<string, unknown>
  correlationId?: string
  status: number

  constructor(status: number, body: ApiErrorBody) {
    super(body.message || 'Request failed')
    this.status = status
    this.code = body.code || 'error'
    this.detail = body.detail || {}
    this.correlationId = body.correlation_id
  }

  /** Field-level messages, when the server sent them. */
  get fields(): Record<string, string> {
    const fields = this.detail?.fields
    return fields && typeof fields === 'object' ? (fields as Record<string, string>) : {}
  }
}

/* The sandbox build has no server: `src/demo/api.ts` answers the same routes
 * in the page. Vite drops that module from the normal build. */
export const DEMO = import.meta.env.VITE_PIMS_DEMO === '1'

type DemoHandler = (method: string, path: string, body?: unknown) => Promise<any>
let demoHandler: DemoHandler | null = null

async function demo(): Promise<DemoHandler> {
  if (!demoHandler) {
    const module = await import('../demo/api')
    demoHandler = module.handle as DemoHandler
  }
  return demoHandler
}

/** Notices the sandbox needs to show the user (downloads are blocked there). */
let noticeSink: ((title: string, body?: string) => void) | null = null
export function setNoticeSink(sink: (title: string, body?: string) => void): void {
  noticeSink = sink
}

/** A key identifying one attempt at one write.
 *
 * Held by the form until the write succeeds, so a retry after a dropped
 * connection carries the same key and the server returns the row it already
 * wrote instead of writing a second one. */
export function newKey(): string {
  const cryptoApi = globalThis.crypto as Crypto | undefined
  if (cryptoApi?.randomUUID) return cryptoApi.randomUUID()
  return `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

const TOKEN_KEY = 'pims.token'

export const token = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (value: string) => localStorage.setItem(TOKEN_KEY, value),
  clear: () => localStorage.removeItem(TOKEN_KEY),
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (DEMO) {
    const handle = await demo()
    try {
      return (await handle(method, path, (body ?? {}) as Record<string, unknown>)) as T
    } catch (error: any) {
      throw new ApiError(error?.status ?? 500, error as ApiErrorBody)
    }
  }

  const headers: Record<string, string> = { Accept: 'application/json' }
  const current = token.get()
  if (current) headers.Authorization = `Bearer ${current}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  let response: Response
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    // A transport failure is a raw TypeError with the message "Failed to
    // fetch", which tells an operator nothing and — worse — says nothing
    // about whether the write landed. On the plant Wi-Fi it usually did.
    throw new ApiError(0, {
      code: 'network',
      message:
        method === 'GET'
          ? 'PIMS could not be reached. Check the network and try again.'
          : 'The network dropped before PIMS answered, so this may or may not ' +
            'have gone through. Check before doing it again — pressing the ' +
            'button a second time is safe, it will not post twice.',
      detail: { method, path },
    })
  }

  if (response.status === 204) return undefined as T

  const contentType = response.headers.get('content-type') || ''
  if (!contentType.includes('application/json')) {
    const text = await response.text()
    if (!response.ok) {
      throw new ApiError(response.status, { code: 'http_error', message: text.slice(0, 300) })
    }
    return text as unknown as T
  }

  const payload = await response.json()
  if (!response.ok) {
    if (response.status === 401) token.clear()
    throw new ApiError(response.status, payload)
  }
  return payload as T
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {}),
  patch: <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
  put: <T>(path: string, body: unknown) => request<T>('PUT', path, body),
  del: <T>(path: string) => request<T>('DELETE', path),

  /** POST returning a file the browser should download. */
  async download(path: string, body: unknown, filename: string): Promise<void> {
    if (DEMO) {
      // The sandbox runs inside an iframe that blocks page-initiated
      // downloads, so hand the export over through the clipboard instead of
      // firing a link that would silently do nothing.
      const handle = await demo()
      const csv = (await handle('POST', path, body as Record<string, unknown>)) as string
      try {
        await navigator.clipboard.writeText(csv)
        noticeSink?.(
          'CSV copied to your clipboard',
          `${csv.trim().split('\n').length - 1} row(s). File downloads are blocked in the sandbox; paste it into a spreadsheet.`,
        )
      } catch {
        noticeSink?.(
          'Export ready, but the clipboard is blocked',
          'Downloads and clipboard access are both restricted in the sandbox — run PIMS locally to export files.',
        )
      }
      return
    }

    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    const current = token.get()
    if (current) headers.Authorization = `Bearer ${current}`
    const response = await fetch(path, { method: 'POST', headers, body: JSON.stringify(body) })
    if (!response.ok) {
      const payload = await response.json().catch(() => ({ message: 'Export failed' }))
      throw new ApiError(response.status, payload)
    }
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    anchor.click()
    URL.revokeObjectURL(url)
  },
}

export function qs(params: Record<string, unknown>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}
