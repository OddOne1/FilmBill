/**
 * Settings nav order, grouping and dividers.
 *
 * The order is a P0a decision that outlives P0a (see the comment on
 * settingsNavGroups): Profile · Appearance · Notifications | Branding ·
 * Design | Admin, with Design present as a placeholder from day one so the
 * navigation never reshuffles under people once P4 fills it in.
 *
 * The interesting rule is the divider: the admin-only groups are
 * conditionally visible, so a separator placed by index would leave a stray
 * line whenever the group beside it is hidden. That is what these assert,
 * rather than the order alone.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

let isSuperAdmin = false

vi.mock('@/stores/auth-store', () => ({
  useAuthStore: () => ({ user: { name: 'Tester', email: 't@example.com' }, isSuperAdmin }),
}))
vi.mock('next/navigation', () => ({ usePathname: () => '/settings/profile' }))

import SettingsLayout from '../layout'
import { useCompanyStore } from '@/stores/company-store'

beforeEach(() => {
  isSuperAdmin = false
  // Not loaded: the company group is hidden rather than shown and then
  // withdrawn, which is what the tests below about dividers depend on.
  useCompanyStore.setState({
    companies: [],
    activeCompanyId: null,
    loaded: false,
    error: null,
  })
})

/** One company, with exactly `permissions`, active. */
function seedCompany(permissions: string[]) {
  useCompanyStore.setState({
    companies: [
      {
        id: 'c1',
        legal_name: 'YON Studio OG',
        trading_name: null,
        display_name: 'YON Studio OG',
        default_currency: 'EUR',
        role: 'owner',
        permissions,
        expires_at: null,
      },
    ],
    activeCompanyId: 'c1',
    loaded: true,
    error: null,
  })
}

function renderNav() {
  return render(<SettingsLayout>{null}</SettingsLayout>)
}

/** Nav links in render order. */
function order() {
  return screen
    .getAllByRole('link')
    .map((a) => a.textContent?.trim())
}

function groups() {
  return screen.getAllByTestId('settings-nav-group')
}

/** A divider is the top border on every group after the first. */
function dividerCount() {
  return groups().filter((g) => g.className.includes('border-t')).length
}

describe('settings nav order', () => {
  it('puts Branding and Design between Notifications and Admin, for a superadmin', () => {
    isSuperAdmin = true
    renderNav()

    expect(order()).toEqual([
      'Profile',
      'Appearance',
      'Notifications',
      'Branding',
      'Design',
      'Admin',
    ])
    // Two dividers: before Branding, and before Admin.
    expect(groups()).toHaveLength(3)
    expect(dividerCount()).toBe(2)
  })

  it('offers Design even though its page is a P4 placeholder', () => {
    isSuperAdmin = true
    renderNav()

    const design = screen.getByRole('link', { name: 'Design' })
    expect(design.getAttribute('href')).toBe('/settings/design')
  })
})

describe('dividers follow visibility', () => {
  it('draws no stray line where the hidden admin groups would have been', () => {
    isSuperAdmin = false
    renderNav()

    expect(order()).toEqual(['Profile', 'Appearance', 'Notifications'])
    // Both admin groups collapse away entirely rather than leaving a line
    // with nothing under it.
    expect(groups()).toHaveLength(1)
    expect(dividerCount()).toBe(0)
  })

  it('hides Branding, Design and Admin together from a non-superadmin', () => {
    isSuperAdmin = false
    renderNav()
    expect(order()).not.toContain('Admin')
    expect(order()).not.toContain('Branding')
    expect(order()).not.toContain('Design')
  })
})


describe('the company group follows company permissions', () => {
  it('shows Number series only with number_series.manage', () => {
    // `company.settings.edit` is NOT enough: re-pointing a number series is a
    // structural act (rule 5's gap-free promise is what is at stake) and the
    // endpoints enforce the narrower key. A nav entry gated on the wrong one
    // is a list of 404s.
    seedCompany(['company.view', 'company.settings.edit'])
    renderNav()
    expect(order()).toContain('Bank accounts')
    expect(order()).not.toContain('Number series')
  })

  it('shows the Audit log only with audit.view', () => {
    // The tax advisor's one extra entry here (Mathias, 2026-10-20). Gated on
    // `audit.view` and not on `company.settings.edit`, which the advisor does
    // not hold — an entry gated on the wrong key is a 404 with a menu item.
    seedCompany(['company.view', 'company.settings.edit'])
    renderNav()
    expect(order()).not.toContain('Audit log')
  })

  it('shows the Audit log for a tax advisor, who holds nothing else here', () => {
    seedCompany(['company.view', 'audit.view', 'archive.view'])
    renderNav()

    const entries = order()
    expect(entries).toContain('Audit log')
    expect(entries).not.toContain('Bank accounts')
    expect(entries).not.toContain('Members')
    expect(entries).not.toContain('Number series')
  })

  it('shows it when the permission is held, between Accounting and Security', () => {
    seedCompany([
      'company.view',
      'company.settings.edit',
      'number_series.manage',
    ])
    renderNav()

    const entries = order()
    expect(entries).toContain('Number series')
    expect(entries.indexOf('Number series')).toBeGreaterThan(
      entries.indexOf('Accounting'),
    )
    expect(entries.indexOf('Number series')).toBeLessThan(
      entries.indexOf('Security'),
    )
  })

  it('shows Tax to any member, including one who cannot edit settings', () => {
    // P1a. Gated on `company.view` and not on `company.settings.edit`: a rate
    // is printed on every document the company sends, and an accountant or
    // tax advisor who cannot see the tax table cannot check an invoice
    // against it. The screen gates its own EDITING and says so, rather than
    // offering a Save button that 404s (rule 17c).
    seedCompany(['company.view'])
    renderNav()

    const entries = order()
    expect(entries).toContain('Tax')
    // And nothing that needs the settings permission came with it.
    expect(entries).not.toContain('Bank accounts')
    expect(entries).not.toContain('Accounting')
  })

  it('puts Tax between Number series and Security', () => {
    seedCompany([
      'company.view',
      'company.settings.edit',
      'number_series.manage',
    ])
    renderNav()

    const entries = order()
    expect(entries.indexOf('Tax')).toBeGreaterThan(
      entries.indexOf('Number series'),
    )
    expect(entries.indexOf('Tax')).toBeLessThan(entries.indexOf('Security'))
  })

  it('shows Tax to a tax advisor, whose only other company entry is the log', () => {
    seedCompany(['company.view', 'audit.view', 'archive.view'])
    renderNav()

    const entries = order()
    expect(entries).toContain('Tax')
    expect(entries).toContain('Audit log')
    expect(entries).not.toContain('Members')
  })

  it('hides the whole company group while the company list has not answered', () => {
    // Hidden rather than shown-and-withdrawn, and asserted against a state
    // where `can()` would say yes if it were consulted — so this cannot pass
    // merely because the permissions were absent.
    useCompanyStore.setState({
      companies: [
        {
          id: 'c1',
          legal_name: 'YON Studio OG',
          trading_name: null,
          display_name: 'YON Studio OG',
          default_currency: 'EUR',
          role: 'owner',
          permissions: ['number_series.manage', 'company.settings.edit'],
          expires_at: null,
        },
      ],
      activeCompanyId: 'c1',
      loaded: false,
      error: null,
    })
    renderNav()

    expect(order()).not.toContain('Number series')
    expect(order()).not.toContain('Bank accounts')
  })
})
