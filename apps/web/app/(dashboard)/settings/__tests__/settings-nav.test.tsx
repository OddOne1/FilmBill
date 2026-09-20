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

beforeEach(() => {
  isSuperAdmin = false
})

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
