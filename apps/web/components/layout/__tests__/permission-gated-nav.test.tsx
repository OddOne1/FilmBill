import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(() => Promise.resolve({})) },
  setActiveCompanyId: vi.fn(),
  ApiError: class extends Error {},
}))
vi.mock('next/navigation', () => ({ usePathname: () => '/' }))
// The Archive switch (UI-1 part 5). The module that DEFINES it is replaced,
// with a getter so each test can flip it: the gate tests below are about
// the permission check that comes back with P5, so they run with it on, and
// 'Archive hidden until P5' runs with it off and proves the shipped value.
let archiveNav = true
vi.mock('@/lib/nav-flags', () => ({
  get ARCHIVE_NAV_ENABLED() {
    return archiveNav
  },
}))
vi.mock('@/hooks/use-site-settings', () => ({
  useSiteSettings: () => ({ orgName: 'FilmBill', logoDarkUrl: null, logoLightUrl: null }),
}))

import { Sidebar } from '../sidebar'
import { findPartyWords } from '@/test/user-words'
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
    archiveNav = true
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

    expect(screen.getByText('Contacts')).toBeInTheDocument()
    // P1b-4: the entry is Contacts and goes to /contacts.
    expect(screen.getByText('Contacts').closest('a')).toHaveAttribute('href', '/contacts')
    expect(findPartyWords(document.body)).toEqual([])
  })

  it('is hidden from a tax advisor, whose access is the archive', () => {
    // D23/D43: the advisor reads finalized documents, where the recipient is
    // already snapshotted on the document in front of them. The live
    // customer list is wider than what they were handed the archive to do.
    seed(['company.view', 'archive.view'], 'tax_advisor')
    render(<Sidebar collapsed={false} onToggle={() => {}} />)

    expect(screen.queryByText('Contacts')).not.toBeInTheDocument()
    expect(screen.getByText('Archive')).toBeInTheDocument()
  })

  it('is hidden from staff, who hold neither key', () => {
    seed(['company.view'], 'staff')
    render(<Sidebar collapsed={false} onToggle={() => {}} />)

    expect(screen.queryByText('Contacts')).not.toBeInTheDocument()
  })

  it('sits between the dashboard and the archive', () => {
    seed(['company.view', 'parties.view', 'archive.view'], 'owner')
    render(<Sidebar collapsed={false} onToggle={() => {}} />)

    const labels = screen
      .getAllByRole('link')
      .map((link) => link.textContent?.trim())
      .filter(Boolean) as string[]
    expect(labels.indexOf('Contacts')).toBeGreaterThan(labels.indexOf('Dashboard'))
    expect(labels.indexOf('Contacts')).toBeLessThan(labels.indexOf('Archive'))
  })
})

// 'the Crew entry (B2b)' went with UI-1: crew live in Contacts → Crew and
// there is no menu item for them. See 'no Crew entry (UI-1)' below.

describe('the Gear entry (B2b)', () => {
  it('is shown to staff, who hold devices.view', () => {
    seed(['company.view', 'projects.view', 'devices.view'], 'staff')
    render(<Sidebar collapsed={false} onToggle={() => {}} />)

    expect(screen.getByText('Gear')).toBeInTheDocument()
  })

  it('is hidden from a tax advisor', () => {
    seed(['company.view', 'archive.view'], 'tax_advisor')
    render(<Sidebar collapsed={false} onToggle={() => {}} />)

    expect(screen.queryByText('Gear')).not.toBeInTheDocument()
  })
})

describe('no Crew entry (UI-1)', () => {
  beforeEach(() => {
    useNotificationStore.setState({ unreadCount: 0, fetchNotifications: vi.fn() } as never)
  })

  it('crew live in Contacts → Crew, so the sidebar has Contacts and no Crew', () => {
    seed(['company.view', 'parties.view', 'parties.edit', 'projects.view', 'devices.view'], 'owner')

    render(<Sidebar collapsed={false} onToggle={() => {}} />)

    expect(screen.getByText('Contacts').closest('a')).toHaveAttribute('href', '/contacts')
    expect(screen.queryByText('Crew')).toBeNull()
    expect(document.querySelector('a[href="/crew"]')).toBeNull()
  })
})

describe('Archive hidden until P5 (UI-1)', () => {
  beforeEach(() => {
    useNotificationStore.setState({ unreadCount: 0, fetchNotifications: vi.fn() } as never)
  })

  it('ships switched off', async () => {
    const actual = await vi.importActual<typeof import('@/lib/nav-flags')>('@/lib/nav-flags')
    expect(actual.ARCHIVE_NAV_ENABLED).toBe(false)
  })

  it('is absent while the switch is off, even for a tax advisor', () => {
    archiveNav = false
    seed(['company.view', 'archive.view', 'archive.download'], 'tax_advisor')
    render(<Sidebar collapsed={false} onToggle={() => {}} />)

    // Positive marker: the sidebar drew its ungated entry.
    expect(screen.getByText('Dashboard')).toBeInTheDocument()
    expect(screen.queryByText('Archive')).toBeNull()
    expect(document.querySelector('a[href="/archive"]')).toBeNull()
  })

  it('comes back when the switch is flipped, still behind archive.view', () => {
    archiveNav = true
    seed(['company.view', 'archive.view'], 'tax_advisor')
    const { unmount } = render(<Sidebar collapsed={false} onToggle={() => {}} />)
    expect(screen.getByText('Archive').closest('a')).toHaveAttribute('href', '/archive')
    unmount()

    seed(['company.view'], 'staff')
    render(<Sidebar collapsed={false} onToggle={() => {}} />)
    expect(screen.getByText('Dashboard')).toBeInTheDocument()
    expect(screen.queryByText('Archive')).toBeNull()
  })
})
