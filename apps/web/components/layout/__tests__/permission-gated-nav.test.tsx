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

describe('the Parties entry (P1b)', () => {
  it('is shown to a producer, who holds parties.view', () => {
    // A producer sends the invoice, so a producer sees who it goes to.
    seed(['company.view', 'parties.view', 'documents.finalize'], 'producer')
    render(<Sidebar collapsed={false} onToggle={() => {}} />)

    expect(screen.getByText('Parties')).toBeInTheDocument()
  })

  it('is hidden from a tax advisor, whose access is the archive', () => {
    // D23/D43: the advisor reads finalized documents, where the recipient is
    // already snapshotted on the document in front of them. The live
    // customer list is wider than what they were handed the archive to do.
    seed(['company.view', 'archive.view'], 'tax_advisor')
    render(<Sidebar collapsed={false} onToggle={() => {}} />)

    expect(screen.queryByText('Parties')).not.toBeInTheDocument()
    expect(screen.getByText('Archive')).toBeInTheDocument()
  })

  it('is hidden from staff, who hold neither key', () => {
    seed(['company.view'], 'staff')
    render(<Sidebar collapsed={false} onToggle={() => {}} />)

    expect(screen.queryByText('Parties')).not.toBeInTheDocument()
  })

  it('sits between the dashboard and the archive', () => {
    seed(['company.view', 'parties.view', 'archive.view'], 'owner')
    render(<Sidebar collapsed={false} onToggle={() => {}} />)

    const labels = screen
      .getAllByRole('link')
      .map((link) => link.textContent?.trim())
      .filter(Boolean) as string[]
    expect(labels.indexOf('Parties')).toBeGreaterThan(labels.indexOf('Dashboard'))
    expect(labels.indexOf('Parties')).toBeLessThan(labels.indexOf('Archive'))
  })
})
