import { getAccessToken } from '@/lib/auth/session'

const DEFAULT_API_BASE_URL = 'http://localhost:8080'

export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE_URL
).replace(/\/$/, '')

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

function mergeHeaders(init?: RequestInit): Headers {
  const headers = new Headers(init?.headers)

  if (!headers.has('Content-Type') && init?.body) {
    headers.set('Content-Type', 'application/json')
  }

  const token = getAccessToken()
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  return headers
}

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`
}

export function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  return fetch(apiUrl(input), {
    ...init,
    headers: mergeHeaders(init),
  })
}

export function apiFetchWithSignal(
  input: string,
  init: RequestInit,
  signal: AbortSignal
): Promise<Response> {
  return fetch(apiUrl(input), {
    ...init,
    headers: mergeHeaders(init),
    signal,
  })
}

export async function readApiError(response: Response): Promise<ApiError> {
  let message = response.statusText || `HTTP ${response.status}`

  try {
    const data = (await response.json()) as { error?: string; message?: string }
    message = data.error || data.message || message
  } catch {
    // Keep the HTTP status fallback.
  }

  return new ApiError(message, response.status)
}

export async function apiJson<T>(input: string, init: RequestInit = {}): Promise<T> {
  const response = await apiFetch(input, init)
  if (!response.ok) {
    throw await readApiError(response)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}
