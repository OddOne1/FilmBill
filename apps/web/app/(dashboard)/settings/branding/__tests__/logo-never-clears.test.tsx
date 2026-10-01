/**
 * A configured logo cannot be taken away.
 *
 * The complaint: "Remove" cleared the stored key, which made every surface
 * fall back to the bundled FilmBill logo. Once an organisation has set its
 * own, that default must never appear again — only a new upload changes
 * what is shown.
 *
 * The backend is what actually enforces this (test_brand_image_never_clears.py
 * — a stale tab, a replayed request or the next redesign all go through the
 * API, not through this page). What is asserted HERE is the other half of
 * the decision: the page no longer offers a control that would ask for it.
 * A Remove button wired to a request the server ignores is worse than no
 * button — it is a button that visibly does nothing.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SWRConfig } from 'swr'

const get = vi.fn()
const patch = vi.fn()
const upload = vi.fn()
vi.mock('@/lib/api', () => ({
  api: {
    get: (path: string) => get(path),
    patch: (path: string, body: unknown) => patch(path, body),
    upload: (path: string, body: unknown) => upload(path, body),
    post: vi.fn(),
    delete: vi.fn(),
  },
}))

vi.mock('@/stores/auth-store', () => ({
  useAuthStore: () => ({ user: { email: 'admin@example.com' }, isSuperAdmin: true }),
}))
vi.mock('@/stores/theme-store', () => ({ useThemeStore: () => ({ resolvedTheme: 'dark' }) }))
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace: vi.fn() }) }))

import BrandingPage from '../page'

const COMMITTED = '/stream/hls/acme-dark.png?token=t'
const REPLACEMENT = '/stream/hls/acme-new.png?token=t'

type Settings = {
  org_name: string
  logo_dark_url: string | null
  logo_light_url: string | null
  logo_login_url: string | null
  favicon_url: string | null
  theme_colors: Record<string, unknown> | null
}

let settings: Settings
/** How many times `/site-settings` has RESOLVED.
 *
 *  The readiness gate below needs it. On a fixture whose fetched values happen
 *  to equal the pre-fetch defaults — a fresh install, `org_name: 'FilmBill'`
 *  and no logo — every rendered check is already true before the fetch lands,
 *  so a gate built only from what is on screen passes immediately and the
 *  `queryByRole(...).toBeNull()` assertions after it pass **vacuously**: they
 *  would hold just as well against a page that never loaded. This is the one
 *  clause `branding-draft.test.tsx` lacks, and it is the one that makes an
 *  absence assertion mean something. */
let fetches = 0

beforeEach(() => {
  settings = {
    org_name: 'Acme Studio',
    logo_dark_url: COMMITTED,
    logo_light_url: null,
    logo_login_url: null,
    favicon_url: null,
    theme_colors: null,
  }
  ;[get, patch, upload].forEach((m) => m.mockReset())
  fetches = 0
  get.mockImplementation(async () => {
    fetches += 1
    return settings
  })
  patch.mockImplementation(async (_p: string, body: Record<string, unknown>) => {
    settings = { ...settings, ...(body as Partial<Settings>) }
    return settings
  })
  upload.mockImplementation(async () => {
    // What the real endpoint does: the key is replaced, never emptied.
    settings = { ...settings, logo_dark_url: REPLACEMENT }
    return settings
  })
  if (!URL.createObjectURL) {
    Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(), writable: true })
    Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), writable: true })
  }
  let blobSeq = 0
  vi.spyOn(URL, 'createObjectURL').mockImplementation(() => `blob:mock-${++blobSeq}`).mockClear()
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {}).mockClear()
})

/** A fresh SWR cache per render — see branding-draft.test.tsx for why.
 *
 *  **It waits for the FETCHED settings, never for static chrome.** This is the
 *  CI flake, and it is worth spelling out because the obvious simplification
 *  brings it straight back:
 *
 *  `'Workspace name'` is an `<h2>`. It is on screen on the first render,
 *  before any fetch resolves. Half this page, though, is rendered from the
 *  fetched settings — `page.tsx` only renders the Reset button when
 *  `hasResettableBranding` is true, and that is derived from the fetched org
 *  name, which is `'FilmBill'` until `/site-settings` lands. So a gate on the
 *  heading waited for something that does not depend on the data, and the
 *  `getByRole` after it — synchronous, no retry — needed something that does.
 *
 *  On a fast machine the fetch settled inside the same flush and it passed. On
 *  a Linux container and on GitHub's runners it did not, which is why CI was
 *  intermittently red from #13 and why the suite growing (338 → 361 → 391
 *  tests) kept moving the odds. Nothing was ever wrong with the component: a
 *  conditional that flips when data arrives is ordinary SWR behaviour.
 *
 *  Reproduced by giving `get` a 20ms delay, which turns it from intermittent
 *  into certain — and which surfaced a SECOND racy test in this file that CI
 *  had never shown (`darkSlotSrc()` read a slot that still had no `<img>`).
 *  Run `pnpm ci:web` to check the whole pipeline the way CI does.
 *
 *  Do not replace any of the three clauses below with a heading, a timeout or
 *  a `retry:` in the vitest config. The first hides this bug again; the other
 *  two hide the entire class of it. */
async function renderPage() {
  const r = render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <BrandingPage />
    </SWRConfig>,
  )
  await waitFor(() => {
    // 1. The fetch has actually resolved. Carries the fixtures whose values
    //    match the defaults — see `fetches`.
    expect(fetches).toBeGreaterThan(0)
    // 2. …and a render consumed it: the committed name is in its own field.
    expect(nameField().value).toBe(settings.org_name)
    // 3. …including the logo, for the fixtures that have one. The name alone
    //    is not enough: a fixture that customises only a logo leaves the name
    //    at the default, so clause 2 is already true mid-flight.
    if (settings.logo_dark_url) {
      expect(darkSlotSrc()).toContain(settings.logo_dark_url)
    }
  })
  return r
}

const nameField = () => screen.getByPlaceholderText('e.g. Acme Studio') as HTMLInputElement

/** The slot's own preview image, which is what an admin is looking at.
 *
 *  Compared by substring: the hook runs every URL through
 *  `resolveApiMediaUrl`, which prefixes the API origin, and pinning the
 *  exact prefix here would be asserting that helper's behaviour rather than
 *  which image is on screen. */
function darkSlotSrc(): string {
  const slot = screen.getByText('Dark theme logo').closest('div')!.parentElement!
  // `?.` and not `!`: this is called from inside `renderPage`'s `waitFor`,
  // where the slot legitimately has no image yet, and a TypeError thrown
  // there is not a retryable assertion failure — it is the gate crashing.
  // Returning '' lets the gate poll again, and an empty string is also the
  // honest answer for the fresh-install fixtures, which show "No logo".
  return slot.querySelector('img')?.getAttribute('src') ?? ''
}

const fileInputs = () =>
  Array.from(document.querySelectorAll('input[type="file"]')) as HTMLInputElement[]

describe('a configured logo', () => {
  it('offers no way to remove it', async () => {
    await renderPage()

    expect(darkSlotSrc()).toContain('acme-dark.png')
    // Not "the button does nothing" — the button is not there.
    expect(screen.queryByRole('button', { name: /remove/i })).toBeNull()
  })

  it('says so, rather than leaving the absence to be discovered', async () => {
    await renderPage()

    // `findBy`, not `getBy`: this sentence only renders for a slot that HAS a
    // committed logo, so it is fetched-dependent. The gate above already
    // guarantees it — this is the second lock on the same door.
    expect(
      await screen.findByText(/can be replaced, but not removed/i),
    ).toBeInTheDocument()
  })

  it('is never cleared by anything this page can send', async () => {
    await renderPage()

    // Every control on the page, exercised: nothing produces a null for a
    // brand-image key. This is the assertion that would have caught the
    // original bug from the UI side.
    for (const call of patch.mock.calls) {
      const body = call[1] as Record<string, unknown>
      for (const key of ['logo_dark_s3_key', 'logo_light_s3_key', 'logo_login_s3_key', 'favicon_s3_key']) {
        expect(body[key]).toBeUndefined()
      }
    }
  })

  it('changes only when a new file is actually uploaded', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.upload(fileInputs()[0], new File(['x'], 'new.png', { type: 'image/png' }))
    // Staged, not saved: still local, nothing sent.
    expect(darkSlotSrc()).toBe('blob:mock-1')
    expect(upload).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => expect(upload).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(darkSlotSrc()).toContain('acme-new.png'))
  })

  it('backs out of a staged file to the committed logo, not to empty', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.upload(fileInputs()[0], new File(['x'], 'new.png', { type: 'image/png' }))
    expect(darkSlotSrc()).toBe('blob:mock-1')

    // Undo is the only destructive-looking control left, and it is not
    // destructive: it reverts an unsaved pick.
    await user.click(screen.getAllByRole('button', { name: /undo/i })[0])

    expect(darkSlotSrc()).toContain('acme-dark.png')
    expect(patch).not.toHaveBeenCalled()
    expect(upload).not.toHaveBeenCalled()
  })

  it('offers Undo only while something is staged', async () => {
    const user = userEvent.setup()
    await renderPage()

    expect(screen.queryByRole('button', { name: /undo/i })).toBeNull()
    await user.upload(fileInputs()[0], new File(['x'], 'new.png', { type: 'image/png' }))
    expect(screen.getAllByRole('button', { name: /undo/i }).length).toBe(1)
  })
})

describe('"reset" no longer reaches the logos', () => {
  it('resets the name and colours and leaves the logo alone', async () => {
    const user = userEvent.setup()
    await renderPage()

    // `findBy` for the same reason: this button is the one the flake was
    // about, and it exists only once the fetched name differs from the
    // default.
    await user.click(
      await screen.findByRole('button', { name: /reset name and colors/i }),
    )
    await user.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => expect(patch).toHaveBeenCalled())
    const body = patch.mock.calls[0][1] as Record<string, unknown>
    expect(body.org_name).toBe('FilmBill')
    expect(body.theme_colors).toBeNull()
    expect('logo_dark_s3_key' in body).toBe(false)
    expect('favicon_s3_key' in body).toBe(false)
    // And the logo is still on screen afterwards.
    await waitFor(() => expect(darkSlotSrc()).toContain('acme-dark.png'))
  })

  it('is not offered to an org whose only customisation is its logo', async () => {
    /* The button can only reset a name and a palette now. Offering it where
       neither is customised promises something it cannot deliver. */
    settings = { ...settings, org_name: 'FilmBill', theme_colors: null }
    await renderPage()

    expect(screen.queryByRole('button', { name: /reset name and colors/i })).toBeNull()
  })
})

describe('a fresh install', () => {
  it('shows an empty slot and still has no Remove to offer', async () => {
    settings = { ...settings, org_name: 'FilmBill', logo_dark_url: null }
    await renderPage()

    expect(screen.getAllByText('No logo').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: /remove/i })).toBeNull()
  })

  it('can still set its first logo', async () => {
    settings = { ...settings, org_name: 'FilmBill', logo_dark_url: null }
    upload.mockImplementation(async () => {
      settings = { ...settings, logo_dark_url: COMMITTED }
      return settings
    })
    const user = userEvent.setup()
    await renderPage()

    await user.upload(fileInputs()[0], new File(['x'], 'first.png', { type: 'image/png' }))
    await user.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => expect(upload).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(darkSlotSrc()).toContain('acme-dark.png'))
  })
})
