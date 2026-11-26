/**
 * UI-1 part 1: the sidebar starts EXPANDED for anybody who has not chosen,
 * and a browser's own choice is remembered under a versioned key.
 *
 * The real DashboardShell and the real Sidebar are rendered, and real
 * jsdom `localStorage` holds the choice — the thing under test is what the
 * shell reads from storage and what the sidebar then draws. "Expanded" is
 * asserted by what a person sees: the item labels are on screen and the
 * toggle offers to collapse. (A collapsed sidebar draws icons only.)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(() => new Promise(() => {})), patch: vi.fn(), post: vi.fn() },
  setActiveCompanyId: vi.fn(),
  ApiError: class extends Error {},
}))
vi.mock('next/navigation', () => ({ usePathname: () => '/' }))
vi.mock('@/hooks/use-site-settings', () => ({
  useSiteSettings: () => ({ orgName: 'FilmBill', logoDarkUrl: null, logoLightUrl: null }),
}))
vi.mock('@/stores/auth-store', () => ({
  useAuthStore: () => ({
    user: { id: 'u1', name: 'Tester', email: 't@example.com', avatar_url: null },
    fetchUser: vi.fn(),
  }),
}))
vi.mock('@/components/layout/header', () => ({ Header: () => null }))
vi.mock('@/components/layout/command-palette', () => ({ CommandPalette: () => null }))

import { DashboardShell } from '../dashboard-shell'
import {
  RETIRED_SIDEBAR_KEYS,
  SIDEBAR_COLLAPSED_KEY,
} from '@/lib/sidebar-preference'
import { useNotificationStore } from '@/stores/notification-store'

function renderShell() {
  return render(
    <DashboardShell>
      <p>page</p>
    </DashboardShell>,
  )
}

/** Expanded: the label is drawn and the toggle says Collapse. */
async function expectExpanded() {
  await waitFor(() => expect(screen.getByTitle('Collapse sidebar')).toBeInTheDocument())
  expect(screen.getByText('Dashboard')).toBeInTheDocument()
}

async function expectCollapsed() {
  await waitFor(() => expect(screen.getByTitle('Expand sidebar')).toBeInTheDocument())
  expect(screen.queryByText('Dashboard')).not.toBeInTheDocument()
}

beforeEach(() => {
  window.localStorage.clear()
  useNotificationStore.setState({ unreadCount: 0, notifications: [], fetchNotifications: vi.fn() } as never)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('the sidebar on first load', () => {
  it('is expanded when this browser has never chosen', async () => {
    renderShell()
    await expectExpanded()
  })

  it('honours a stored "collapsed" under the new key', async () => {
    window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, '1')
    renderShell()
    await expectCollapsed()
  })

  it('ignores a "collapsed" stored under an older name', async () => {
    for (const key of RETIRED_SIDEBAR_KEYS) window.localStorage.setItem(key, '1')
    for (const key of RETIRED_SIDEBAR_KEYS) window.localStorage.setItem(key, 'true')
    renderShell()
    await expectExpanded()
  })

  it('is expanded when storage throws', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError: storage is disabled')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    renderShell()
    await expectExpanded()
    // And the toggle still works for this page, though nothing is saved.
    await userEvent.click(screen.getByTitle('Collapse sidebar'))
    await expectCollapsed()
  })

  it('remembers the toggle under the new key, both ways', async () => {
    const { unmount } = renderShell()
    await expectExpanded()
    await userEvent.click(screen.getByTitle('Collapse sidebar'))
    await expectCollapsed()
    expect(window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY)).toBe('1')
    unmount()

    renderShell()
    await expectCollapsed()
    await userEvent.click(screen.getByTitle('Expand sidebar'))
    await expectExpanded()
    expect(window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY)).toBe('0')
  })
})
