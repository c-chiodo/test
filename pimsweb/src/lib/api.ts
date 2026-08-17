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

const TOKEN_KEY = 'pims.token'

export const token = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (value: string) => localStorage.setItem(TOKEN_KEY, value),
  clear: () => localStorage.removeItem(TOKEN_KEY),
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  const current = token.get()
  if (current) headers.Authorization = `Bearer ${current}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })

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
