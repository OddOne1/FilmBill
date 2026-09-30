import { describe, it, expect, beforeEach, vi } from 'vitest'

vi.mock('../auth', () => ({
  getAccessToken: vi.fn(() => 'a-token'),
  refreshAccessToken: vi.fn(() => Promise.resolve(null)),
}))

import { api, setActiveCompanyId } from '../api'

/**
 * The company header is attached in ONE place (P0b-1 §2), so these assert on
 * `lib/api` itself rather than on any call site. A per-call-site header is a
 * header somebody forgets, and the request that forgets it fails in a way
 * that looks like a server problem.
 */

function okResponse() {
  return {
    ok: true,
    status: 200,
    headers: {
      get: (key: string) => (key === 'content-type' ? 'application/json' : null),
    },
    json: () => Promise.resolve({}),
    text: () => Promise.resolve('{}'),
  }
}

function headersOfLastCall(): Record<string, string> {
  const call = vi.mocked(fetch).mock.calls.at(-1)
  return (call?.[1] as RequestInit).headers as Record<string, string>
}

describe('X-Company-Id', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.unstubAllGlobals()
    setActiveCompanyId(null)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(okResponse()))
  })

  it('is absent before a company has been chosen', async () => {
    // Absent, not empty. The server reads a blank header as a broken client
    // and answers 400, and /auth/me and /companies are both fetched before
    // the app knows which company it is in.
    await api.get('/auth/me')

    expect(headersOfLastCall()).not.toHaveProperty('X-Company-Id')
  })

  it('is attached to every verb once a company is active', async () => {
    setActiveCompanyId('company-a')

    await api.get('/company')
    expect(headersOfLastCall()['X-Company-Id']).toBe('company-a')

    await api.post('/company/members', { email: 'x@example.com' })
    expect(headersOfLastCall()['X-Company-Id']).toBe('company-a')

    await api.patch('/company', { trading_name: 'X' })
    expect(headersOfLastCall()['X-Company-Id']).toBe('company-a')

    await api.delete('/company/members/1')
    expect(headersOfLastCall()['X-Company-Id']).toBe('company-a')
  })

  it('is attached to uploads as well', async () => {
    // An upload lands in a company like anything else. This is the code path
    // most likely to be left behind, because it builds its headers
    // separately in order to leave Content-Type to the browser.
    setActiveCompanyId('company-a')

    await api.upload('/company/logo', new FormData())

    expect(headersOfLastCall()['X-Company-Id']).toBe('company-a')
  })

  it('follows a switch without the caller doing anything', async () => {
    setActiveCompanyId('company-a')
    await api.get('/company')
    setActiveCompanyId('company-b')

    await api.get('/company')

    expect(headersOfLastCall()['X-Company-Id']).toBe('company-b')
  })

  it('survives the 401 refresh retry', async () => {
    // The retry rebuilds the headers from scratch. Losing the company on the
    // second attempt would make every request that happened to race a token
    // refresh answer 400 — intermittently, which is the worst way to find it.
    const { refreshAccessToken } = await import('../auth')
    vi.mocked(refreshAccessToken).mockResolvedValue('fresh-token')
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce({ ...okResponse(), ok: false, status: 401 })
        .mockResolvedValue(okResponse()),
    )
    setActiveCompanyId('company-a')

    await api.get('/company')

    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2)
    expect(headersOfLastCall()['X-Company-Id']).toBe('company-a')
  })
})
