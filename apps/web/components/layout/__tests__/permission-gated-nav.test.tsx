import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(() => Promise.resolve({})) },
  setActiveCompanyId: vi.fn(),
  ApiError: class extends Error {},
}))
vi.mock('next/navigation', () => ({ usePathname: () => '/' }))
vi.mock('@/hooks/use-site-settings', () => ({
  useSiteSettings: () => ({ orgName: 'FilmBill', logoDarkUrl: null, logoLightUrl: null }),
}))

import { Sidebar } from '../sidebar'
import { useCompanyStore } from '@/stores/company-store'
import { useNotificationStore } from '@/stores/notification-store'
import type { CompanySummary } from '@/types'

/**
 * Navigation renders only what the person holds (§3), and the case that
 * matters is the tax advisor: `archive.view` is their only permission, so the
 * Archive entry is the entire difference between an account that is correctly
 * restricted and one that is broken.
 */

function seed(permissions: string[], role: CompanySummary['role']) {
  useCompanyStore.setState({
    companies: [
      {
        id: 'a',
        legal_name: 'Klientin OG',
        trading_name: null,
        display_name: 'Klientin OG',
        default_currency: 'EUR',
        role,
        permissions,
        expires_at: null,
      },
    ],
    activeCompanyId: 'a',
    loaded: true,
    error: null,
  })
}

describe('sidebar navigation gating', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useNotificationStore.setState({ unreadCount: 0, fetchNotifications: vi.fn() } as never)
    useCompanyStore.setState({ companies: [], activeCompanyId: null, loaded: false, error: null })
  })

  it('shows Archive to a tax advisor', () => {
    seed(['company.view', 'archive.view', 'archive.download'], 'tax_advisor')

    render(<Sidebar collapsed={false} onToggle={() => {}} />)

    expect(screen.getByText('Archive')).toBeInTheDocument()
  })

  it('hides Archive from a role that does not hold archive.view', () => {
    seed(['company.view'], 'staff')

    render(<Sidebar collapsed={false} onToggle={() => {}} />)

    expect(screen.queryByText('Archive')).not.toBeInTheDocument()
  })

  it('hides it while the company list has not answered yet', () => {
    // Shown-then-withdrawn reads as a bug, and for a tax advisor the flicker
    // would be their whole sidebar.
    render(<Sidebar collapsed={false} onToggle={() => {}} />)

    expect(screen.queryByText('Archive')).not.toBeInTheDocument()
  })

  it('still shows the ungated entries to everyone', () => {
    seed(['company.view'], 'staff')

    render(<Sidebar collapsed={false} onToggle={() => {}} />)

    expect(screen.getByText('Dashboard')).toBeInTheDocument()
  })
})
