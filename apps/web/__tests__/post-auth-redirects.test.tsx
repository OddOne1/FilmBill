/**
 * Every redirect out of an auth flow lands on a route this app actually has.
 *
 * The bug: after the port, finishing a login still sent people to
 * FreeFrame's `/projects`. That route left with the media features, so the
 * first thing a brand-new admin saw after creating their account was a 404.
 * Four call sites had it (three in the login form, one in the login page),
 * and every one of them was only ever read by eye.
 *
 * Two halves, and both are needed:
 *
 *  1. The targets are OBSERVED, not grepped. Each flow is driven the way a
 *     user drives it — fill the form, submit, resolve the API call — and the
 *     assertion is made against what actually reached `router.replace` /
 *     `router.push` / `NextResponse.redirect`. A grep over these files would
 *     have passed on the comment you are reading (CLAUDE.md rule 11).
 *  2. The allowed set is built from the FILESYSTEM, the same way
 *     `test_openapi_surface.py` builds its allowlist from the live schema
 *     rather than from a hand-kept list. So the day someone deletes
 *     `app/(dashboard)/notifications/page.tsx`, this fails instead of the
 *     user.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { readdirSync } from 'fs'
import path from 'path'
import { render, screen, waitFor, renderHook, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { NextRequest } from 'next/server'

// ── the allowed set, read off the App Router tree ───────────────────────────

const APP_DIR = path.resolve(__dirname, '..', 'app')

/**
 * Every route the app serves, as a list of segment arrays. `/` is `[]`.
 *
 * Route groups (`(auth)`) contribute no segment; dynamic segments
 * (`[token]`) match anything; `_private` folders are not routes.
 */
function routePatterns(dir: string, prefix: string[] = []): string[][] {
  const entries = readdirSync(dir, { withFileTypes: true })
  const routes: string[][] = []

  if (entries.some((e) => e.isFile() && /^page\.(tsx|ts|jsx|js)$/.test(e.name))) {
    routes.push(prefix)
  }
  for (const entry of entries) {
    if (!entry.isDirectory()) continue
    if (entry.name.startsWith('_') || entry.name.startsWith('@')) continue
    const isGroup = entry.name.startsWith('(') && entry.name.endsWith(')')
    routes.push(
      ...routePatterns(
        path.join(dir, entry.name),
        isGroup ? prefix : [...prefix, entry.name],
      ),
    )
  }
  return routes
}

const PATTERNS = routePatterns(APP_DIR)

function isDynamic(segment: string): boolean {
  return segment.startsWith('[') && segment.endsWith(']')
}

/** Does `target` — a path, possibly with a query string — resolve to a page? */
function resolvesToARoute(target: string): boolean {
  const pathname = target.split('?')[0].split('#')[0]
  if (!pathname.startsWith('/')) return false
  const parts = pathname.split('/').filter(Boolean)
  return PATTERNS.some(
    (pattern) =>
      pattern.length === parts.length &&
      pattern.every((segment, i) => isDynamic(segment) || segment === parts[i]),
  )
}

/** Fails with the target spelled out, which is the whole point when it breaks. */
function expectRoute(target: string | undefined) {
  expect(target, 'no redirect happened').toBeDefined()
  expect(
    resolvesToARoute(target as string),
    `redirect target ${target} is not a route this app has. Routes: ` +
      PATTERNS.map((p) => '/' + p.join('/')).sort().join(', '),
  ).toBe(true)
}

describe('the route list this test checks against', () => {
  it('is read off the filesystem and is not empty', () => {
    // A builder that silently returned [] would make every check below pass.
    expect(PATTERNS.length).toBeGreaterThan(5)
    expect(resolvesToARoute('/')).toBe(true)
    expect(resolvesToARoute('/settings/profile')).toBe(true)
    expect(resolvesToARoute('/invite/any-long-token')).toBe(true) // [token]
  })

  it('does not resolve routes that left with the media features', () => {
    expect(resolvesToARoute('/projects')).toBe(false)
    expect(resolvesToARoute('/share/abc')).toBe(false)
  })
})

// ── mocks shared by the client flows ────────────────────────────────────────

const replace = vi.fn()
const push = vi.fn()
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push, refresh: vi.fn() }),
}))

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  }
})

/** Set per test: the login PAGE only redirects when a token is already held. */
let storedAccessToken: string | null = null
vi.mock('@/lib/auth', () => ({
  setTokens: vi.fn(),
  getAccessToken: () => storedAccessToken,
}))

// Callable AND with `getState`: the forms reach for
// `useAuthStore.getState().fetchUser()`, useAuth() calls the hook itself.
vi.mock('@/stores/auth-store', () => {
  const state = {
    user: null,
    isAuthenticated: false,
    isSuperAdmin: false,
    isLoading: false,
    setUser: vi.fn(),
    logout: vi.fn(),
    fetchUser: vi.fn(),
  }
  return { useAuthStore: Object.assign(() => state, { getState: () => state }) }
})

vi.mock('@/hooks/use-site-settings', () => ({
  SITE_SETTINGS_KEY: '/site-settings',
  useSiteSettings: () => ({ requireTwoFactor: false, isLoading: false, orgName: 'FilmBill' }),
}))

import { api } from '@/lib/api'
import { LoginForm } from '@/components/auth/login-form'
import { SetupWizard } from '@/components/auth/setup-wizard'
import { InviteAccept } from '@/components/auth/invite-accept'
import LoginPage from '@/app/(auth)/login/page'
import { useAuth } from '@/hooks/use-auth'
import { middleware } from '../middleware'

const TOKENS = {
  access_token: 'access-1',
  refresh_token: 'refresh-1',
  token_type: 'bearer',
  needs_password: false,
  requires_2fa: false as const,
}

beforeEach(() => {
  vi.clearAllMocks()
  storedAccessToken = null
  window.history.replaceState({}, '', '/login')
})

async function typeCode(user: ReturnType<typeof userEvent.setup>, code: string) {
  const boxes = screen.getAllByLabelText(/^digit /i)
  for (let i = 0; i < code.length; i++) {
    await user.type(boxes[i], code[i])
  }
}

// ── the login form: three ways out, all of them used to say /projects ───────

describe('the login form', () => {
  it('sends a finished password login to a real route', async () => {
    const user = userEvent.setup()
    vi.mocked(api.post).mockResolvedValueOnce(TOKENS)
    render(<LoginForm />)

    await user.click(screen.getByRole('button', { name: /sign in with password/i }))
    await user.type(screen.getByLabelText(/email address/i), 'u@example.com')
    await user.type(screen.getByLabelText(/^password$/i), 'hunter2hunter2')
    await user.click(screen.getByRole('button', { name: /^sign in$/i }))

    await waitFor(() => expect(replace).toHaveBeenCalled())
    expectRoute(replace.mock.calls[0][0])
  })

  it('sends a user who just set their first password to a real route', async () => {
    const user = userEvent.setup()
    vi.mocked(api.post)
      .mockResolvedValueOnce({ message: 'sent' })
      .mockResolvedValueOnce({ ...TOKENS, needs_password: true })
      .mockResolvedValueOnce(TOKENS)
    render(<LoginForm />)

    await user.type(screen.getByLabelText(/email address/i), 'u@example.com')
    await user.click(screen.getByRole('button', { name: /send magic code/i }))
    await typeCode(user, '123456')
    await screen.findByText(/create your password/i)

    await user.type(screen.getByLabelText(/^password$/i), 'hunter2hunter2')
    await user.type(screen.getByLabelText(/confirm password/i), 'hunter2hunter2')
    await user.click(screen.getByRole('button', { name: /set password/i }))

    await waitFor(() => expect(replace).toHaveBeenCalled())
    expectRoute(replace.mock.calls[0][0])
  })

  it('sends a user who just enrolled in 2FA to a real route', async () => {
    // The backup-codes screen: the last redirect in the form, and the one
    // least likely to be walked through by hand.
    const user = userEvent.setup()
    vi.mocked(api.post)
      .mockResolvedValueOnce({
        requires_2fa: true as const,
        setup_required: true,
        pending_token: 'pending-1',
        method: null,
        email_code_sent: false,
      })
      .mockResolvedValueOnce({ method: 'email', email_code_sent: true })
      .mockResolvedValueOnce({
        backup_codes: ['aaaa-1111', 'bbbb-2222'],
        method: 'email',
        tokens: TOKENS,
      })
    render(<LoginForm />)

    await user.click(screen.getByRole('button', { name: /sign in with password/i }))
    await user.type(screen.getByLabelText(/email address/i), 'u@example.com')
    await user.type(screen.getByLabelText(/^password$/i), 'hunter2hunter2')
    await user.click(screen.getByRole('button', { name: /^sign in$/i }))

    await user.click(await screen.findByRole('button', { name: /^email/i }))
    await typeCode(user, '333444')
    await screen.findByText(/save your backup codes/i)
    await user.click(screen.getByRole('button', { name: /i've saved these codes/i }))

    await waitFor(() => expect(replace).toHaveBeenCalled())
    expectRoute(replace.mock.calls[0][0])
  })
})

// ── the login page: the `from` hand-off and its fallback ────────────────────

describe('the login page', () => {
  it('sends an already-signed-in visitor to a real route', async () => {
    storedAccessToken = 'access-1'
    vi.mocked(api.get).mockResolvedValue({ needs_setup: false })
    render(<LoginPage />)

    await waitFor(() => expect(replace).toHaveBeenCalled())
    expectRoute(replace.mock.calls[0][0])
  })

  it('still honours where the middleware was sending them', async () => {
    // `from` is set by middleware.ts from a path it already gated, so it is
    // taken as given — but it must still be USED, or the deep link is lost.
    storedAccessToken = 'access-1'
    window.history.replaceState({}, '', '/login?from=/settings/branding')
    vi.mocked(api.get).mockResolvedValue({ needs_setup: false })
    render(<LoginPage />)

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/settings/branding'))
    expectRoute(replace.mock.calls[0][0])
  })

  it('sends an un-set-up instance to a real route', async () => {
    vi.mocked(api.get).mockResolvedValue({ needs_setup: true })
    render(<LoginPage />)

    await waitFor(() => expect(replace).toHaveBeenCalled())
    expectRoute(replace.mock.calls[0][0])
  })
})

// ── first-run setup and invite acceptance ───────────────────────────────────

describe('first-run setup', () => {
  it('sends the new admin to a real route once the account exists', async () => {
    const user = userEvent.setup()
    vi.mocked(api.post).mockResolvedValueOnce({ message: 'created' })
    render(<SetupWizard />)

    await user.type(screen.getByLabelText(/last name/i), 'Sonnleitner')
    await user.type(screen.getByLabelText(/email address/i), 'a@example.com')
    await user.type(screen.getByLabelText(/^password$/i), 'hunter2hunter2')
    await user.type(screen.getByLabelText(/confirm password/i), 'hunter2hunter2')
    await user.click(screen.getByRole('button', { name: /create admin account/i }))

    // The wizard shows its success panel for 1.8s before moving.
    await waitFor(() => expect(push).toHaveBeenCalled(), { timeout: 4000 })
    expectRoute(push.mock.calls[0][0])
  })
})

describe('invite acceptance', () => {
  it('sends the new member to a real route', async () => {
    const user = userEvent.setup()
    vi.mocked(api.get).mockResolvedValue({
      email: 'new@example.com',
      org_name: 'YON Studio',
      inviter_name: 'Mathias',
    })
    vi.mocked(api.post).mockResolvedValueOnce(TOKENS)
    render(<InviteAccept token="invite-token-1" />)

    await user.type(await screen.findByLabelText(/full name/i), 'New Person')
    await user.type(screen.getByLabelText(/^password$/i), 'hunter2hunter2')
    await user.type(screen.getByLabelText(/confirm password/i), 'hunter2hunter2')
    await user.click(screen.getByRole('button', { name: /create account & join/i }))

    await waitFor(() => expect(replace).toHaveBeenCalled())
    expectRoute(replace.mock.calls[0][0])
  })
})

// ── useAuth(), which has no caller yet and so no one to notice ─────────────

describe('the useAuth hook', () => {
  it('sends a finished login to a real route', async () => {
    vi.mocked(api.post).mockResolvedValueOnce(TOKENS)
    vi.mocked(api.get).mockResolvedValueOnce({ id: 'u1', email: 'u@example.com' })
    const { result } = renderHook(() => useAuth())

    await act(async () => {
      await result.current.login('u@example.com', 'hunter2hunter2')
    })

    expectRoute(push.mock.calls[0][0])
  })

  it('sends a logout to a real route', () => {
    const { result } = renderHook(() => useAuth())

    act(() => result.current.logout())

    expectRoute(push.mock.calls[0][0])
  })
})

// ── the middleware, which decides where an ungated request goes ─────────────

describe('the route middleware', () => {
  function request(path: string, cookies: Record<string, string> = {}) {
    const req = new NextRequest(new URL(`http://localhost:3100${path}`))
    for (const [name, value] of Object.entries(cookies)) req.cookies.set(name, value)
    return req
  }

  function target(res: { headers: Headers }): string | undefined {
    const location = res.headers.get('location')
    if (!location) return undefined
    const url = new URL(location)
    return url.pathname + url.search
  }

  it('sends an unauthenticated request to a real route', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ needs_setup: false }) }),
    )
    expectRoute(target(await middleware(request('/settings/design', { fb_setup_done: '1' }))))
  })

  it('sends a fresh install to a real route', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ needs_setup: true }) }),
    )
    expectRoute(target(await middleware(request('/settings/design'))))
  })
})
