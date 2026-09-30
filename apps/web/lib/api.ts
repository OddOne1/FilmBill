import { getAccessToken, refreshAccessToken } from './auth'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

/**
 * The company every request acts in, attached here and nowhere else.
 *
 * A module-level value with a setter rather than a read of the company store,
 * deliberately: the store imports this module, so reading it back would be a
 * cycle, and this file has no business knowing about zustand. The store calls
 * `setActiveCompanyId` whenever the active company changes — see
 * stores/company-store.ts, which is the single writer.
 *
 * ONE call site for the header is the whole point (P0b-1 §2). A per-call-site
 * header is a header somebody forgets, and the request that forgets it is not
 * a visible bug — it is a 400, or worse, an endpoint that later grows a
 * fallback and starts answering for the wrong company.
 */
let activeCompanyId: string | null = null

export function setActiveCompanyId(id: string | null): void {
  activeCompanyId = id
}

export function getActiveCompanyId(): string | null {
  return activeCompanyId
}

export class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

interface RequestOptions {
  headers?: Record<string, string>
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  options?: RequestOptions,
): Promise<T> {
  const buildHeaders = (token: string | null): Record<string, string> => {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...options?.headers,
    }
    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }
    // Omitted rather than sent empty when no company is active yet: the
    // server reads a blank header as a broken client and answers 400, while
    // an absent one is simply a request with no company context — which is
    // exactly right for /auth/me and /companies, the two things the app
    // fetches before it knows which company it is in.
    if (activeCompanyId) {
      headers['X-Company-Id'] = activeCompanyId
    }
    return headers
  }

  const execute = async (token: string | null): Promise<Response> => {
    return fetch(`${API_URL}${path}`, {
    method,
    cache: 'no-store',
    headers: buildHeaders(token),
    body: body !== undefined ? JSON.stringify(body) : undefined,
    })
      }

        let token = getAccessToken()
          let response = await execute(token)

            // On 401, attempt a token refresh and retry once
              if (response.status === 401) {
                  const newToken = await refreshAccessToken()    
                  if (newToken) {
      response = await execute(newToken)
    }
  }

  if (!response.ok) {
    let detail = response.statusText
    try {
      const errorBody = await response.json()
      if (errorBody?.detail) {
        if (typeof errorBody.detail === 'string') {
          detail = errorBody.detail
        } else if (Array.isArray(errorBody.detail)) {
          // FastAPI validation errors: [{loc: [...], msg: "...", type: "..."}]
          detail = errorBody.detail
            .map((e: { msg?: string; loc?: string[] }) => e.msg || 'Validation error')
            .join('; ')
        } else {
          detail = JSON.stringify(errorBody.detail)
        }
      }
    } catch {
      // ignore parse errors; use statusText as fallback
    }
    throw new ApiError(response.status, detail)
  }

  // Handle empty responses (e.g. 204 No Content, or empty body)
  if (response.status === 204 || response.headers.get('content-length') === '0') {
    return undefined as unknown as T
  }

  const contentType = response.headers.get('content-type')
  if (!contentType || !contentType.includes('application/json')) {
    return undefined as unknown as T
  }

  const text = await response.text()
  if (!text) {
    return undefined as unknown as T
  }

  return JSON.parse(text) as T
}

async function uploadRequest<T>(path: string, formData: FormData): Promise<T> {
  const buildHeaders = (token: string | null): Record<string, string> => {
    const headers: Record<string, string> = {}
    if (token) headers['Authorization'] = `Bearer ${token}`
    // The same header as `request` above. An upload is a request like any
    // other and lands in a company like any other; leaving it out here is
    // how one code path ends up unscoped while its neighbour is fine.
    if (activeCompanyId) headers['X-Company-Id'] = activeCompanyId
    return headers
  }

  const execute = async (token: string | null): Promise<Response> => {
    return fetch(`${API_URL}${path}`, {
      method: 'POST',
      headers: buildHeaders(token),
      body: formData,
    })
  }

  let token = getAccessToken()
  let response = await execute(token)

  if (response.status === 401) {
    const newToken = await refreshAccessToken()
    if (newToken) response = await execute(newToken)
  }

  if (!response.ok) {
    let detail = response.statusText
    try {
      const errorBody = await response.json()
      if (errorBody?.detail) detail = typeof errorBody.detail === 'string' ? errorBody.detail : JSON.stringify(errorBody.detail)
    } catch {}
    throw new ApiError(response.status, detail)
  }

  if (response.status === 204) return undefined as unknown as T
  const text = await response.text()
  return text ? (JSON.parse(text) as T) : (undefined as unknown as T)
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) =>
    request<T>('GET', path, undefined, options),

  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>('POST', path, body, options),

  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>('PATCH', path, body, options),

  put: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>('PUT', path, body, options),

  delete: <T>(path: string, options?: RequestOptions) =>
    request<T>('DELETE', path, undefined, options),

  upload: <T>(path: string, formData: FormData) =>
    uploadRequest<T>(path, formData),
}

export type { ApiError as ApiErrorType }
