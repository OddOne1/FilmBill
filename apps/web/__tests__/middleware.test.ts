/**
 * The route gate actually gates.
 *
 * Two bugs live here, both found by walking the route list after the port:
 *
 *  1. The setup check used to answer `NextResponse.next()` on success and
 *     return — so on every request that arrived WITHOUT the `fb_setup_done`
 *     cookie (the first one after a deploy, a cookie clear, or its 24h
 *     expiry) the token check below it never ran at all. The API still
 *     refused the data, so nothing leaked; but a gate that lets people
 *     through on their first knock is not a gate.
 *  2. `PUBLIC_PREFIXES` still listed `/share/`, a FreeFrame route that does
 *     not exist here. An unauthenticated prefix matching nothing is how a
 *     hole gets re-opened by accident, the day someone adds a page there.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { NextRequest } from 'next/server'

import { middleware } from '../middleware'

function request(path: string, cookies: Record<string, string> = {}) {
  const req = new NextRequest(new URL(`http://localhost:3100${path}`))
  for (const [name, value] of Object.entries(cookies)) {
    req.cookies.set(name, value)
  }
  return req
}

/** Where the middleware sent this request, or null if it let it through. */
function redirectedTo(res: { headers: Headers }): string | null {
  const location = res.headers.get('location')
  return location ? new URL(location).pathname : null
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ needs_setup: false }),
    }),
  )
})

describe('public routes', () => {
  it.each(['/login', '/setup'])('lets %s through unauthenticated', async (path) => {
    expect(redirectedTo(await middleware(request(path)))).toBeNull()
  })

  it('lets an invite link through — the recipient has no account yet', async () => {
    expect(redirectedTo(await middleware(request('/invite/some-long-token')))).toBeNull()
  })

  it('does NOT treat /share/ as public any more', async () => {
    // The route is gone; the exemption must not outlive it.
    expect(redirectedTo(await middleware(request('/share/anything')))).toBe('/login')
  })
})

describe('the auth gate', () => {
  it('redirects an unauthenticated request to login', async () => {
    const res = await middleware(request('/settings/design', { fb_setup_done: '1' }))
    expect(redirectedTo(res)).toBe('/login')
  })

  it('remembers where the user was going', async () => {
    const res = await middleware(request('/settings/design', { fb_setup_done: '1' }))
    const location = new URL(res.headers.get('location') as string)
    expect(location.searchParams.get('from')).toBe('/settings/design')
  })

  it('STILL gates when the setup cookie is absent', async () => {
    // Bug 1. This is the first request after a deploy, and it used to sail
    // straight past the token check.
    expect(redirectedTo(await middleware(request('/settings/design')))).toBe('/login')
  })

  it('still gates when the API cannot be reached', async () => {
    // Failing to confirm that setup is done is not a reason to stop
    // checking for a token.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('down')))
    expect(redirectedTo(await middleware(request('/settings/design')))).toBe('/login')
  })

  it('lets an authenticated request through', async () => {
    const res = await middleware(
      request('/settings/design', { fb_setup_done: '1', fb_access_token: 'tok' }),
    )
    expect(redirectedTo(res)).toBeNull()
  })

  it('accepts a refresh token alone — the access token expires first', async () => {
    const res = await middleware(
      request('/settings/design', { fb_setup_done: '1', fb_refresh_token: 'tok' }),
    )
    expect(redirectedTo(res)).toBeNull()
  })

  it("does not accept FreeFrame's old cookie names", async () => {
    const res = await middleware(
      request('/settings/design', { ff_setup_done: '1', ff_access_token: 'tok' }),
    )
    expect(redirectedTo(res)).toBe('/login')
  })
})

describe('first-run setup', () => {
  it('sends everyone to /setup while no superadmin exists', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ needs_setup: true }),
      }),
    )
    expect(redirectedTo(await middleware(request('/settings/design')))).toBe('/setup')
  })

  it('caches the answer, and only on a request it let through', async () => {
    const res = await middleware(request('/settings/design', { fb_access_token: 'tok' }))
    expect(redirectedTo(res)).toBeNull()
    expect(res.cookies.get('fb_setup_done')?.value).toBe('1')
  })
})
