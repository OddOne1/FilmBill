import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(() => Promise.resolve({})) },
  setActiveCompanyId: vi.fn(),
  ApiError: class extends Error {},
}))

import { CompanySwitcher } from '../company-switcher'
import { useCompanyStore } from '@/stores/company-store'
import { useAuthStore } from '@/stores/auth-store'
import type { CompanySummary } from '@/types'

function company(id: string, name: string, role: CompanySummary['role'] = 'owner'): CompanySummary {
  return {
    id,
    legal_name: name,
    trading_name: null,
    display_name: name,
    default_currency: 'EUR',
    role,
    permissions: ['company.view'],
    expires_at: null,
  }
}

function seed(companies: CompanySummary[], activeCompanyId: string | null) {
  useCompanyStore.setState({ companies, activeCompanyId, loaded: true, error: null })
}

/** Whether this viewer runs the INSTALLATION. The only thing that puts "New
 *  company…" in the menu, and therefore the only thing that makes the control
 *  appear for someone with a single company. */
function asSuperAdmin(value: boolean) {
  useAuthStore.setState({ isSuperAdmin: value })
}

describe('CompanySwitcher', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    seed([], null)
    asSuperAdmin(false)
  })

  it('renders nothing for a user with one company', () => {
    // §2 — most installations have exactly one, and a control with one
    // option only ever costs a click and a moment of doubt.
    seed([company('a', 'YON Studio OG')], 'a')

    const { container } = render(<CompanySwitcher />)

    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing while the list has not loaded', () => {
    const { container } = render(<CompanySwitcher />)

    expect(container).toBeEmptyDOMElement()
  })

  it('shows the active company when there is more than one', () => {
    seed([company('a', 'YON Studio OG'), company('b', 'Test GmbH')], 'a')

    render(<CompanySwitcher />)

    expect(screen.getByTestId('company-switcher')).toHaveTextContent('YON Studio OG')
  })

  it('switches the active company, which is what moves every later request', () => {
    seed([company('a', 'YON Studio OG'), company('b', 'Test GmbH')], 'a')
    render(<CompanySwitcher />)

    fireEvent.click(screen.getByTestId('company-switcher'))
    fireEvent.click(screen.getByRole('option', { name: /Test GmbH/ }))

    expect(useCompanyStore.getState().activeCompanyId).toBe('b')
  })

  it('names the role held in each company', () => {
    // Someone who owns one company and advises another needs to know which
    // hat they are putting on before they put it on.
    seed(
      [company('a', 'YON Studio OG', 'owner'), company('b', 'Klientin OG', 'tax_advisor')],
      'a',
    )
    render(<CompanySwitcher />)

    fireEvent.click(screen.getByTestId('company-switcher'))

    expect(screen.getByRole('option', { name: /Klientin OG/ })).toHaveTextContent(
      'tax advisor',
    )
  })
})

describe('CompanySwitcher — creating a company', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    seed([], null)
    asSuperAdmin(false)
  })

  it('offers "New company…" to an installation administrator', () => {
    // P0b-2 §3: P0b-1 shipped POST /companies with nothing that reaches it, so
    // its own acceptance had to go through Swagger. This entry is the fix, and
    // the menu is where it belongs — the alternative is a URL you have to know.
    asSuperAdmin(true)
    seed([company('a', 'YON Studio OG')], 'a')

    render(<CompanySwitcher />)
    fireEvent.click(screen.getByTestId('company-switcher'))

    expect(screen.getByTestId('company-switcher-new')).toBeInTheDocument()
  })

  it('shows the control to a superadmin with only ONE company', () => {
    // The deliberate exception to "hidden for one company": without it, the
    // second company cannot be created from the interface at all.
    asSuperAdmin(true)
    seed([company('a', 'YON Studio OG')], 'a')

    render(<CompanySwitcher />)

    expect(screen.getByTestId('company-switcher')).toBeInTheDocument()
  })

  it('still hides the control from a non-admin with one company', () => {
    // The rule P0b-1 set, unchanged for everyone who cannot create one.
    seed([company('a', 'YON Studio OG')], 'a')

    const { container } = render(<CompanySwitcher />)

    expect(container).toBeEmptyDOMElement()
  })

  it('offers it on a fresh install whose company list is empty', () => {
    // The one state where the create entry is the ONLY useful thing in the
    // menu — and the state where a hidden control would leave the app with
    // nothing to do.
    asSuperAdmin(true)
    seed([], null)

    render(<CompanySwitcher />)
    fireEvent.click(screen.getByTestId('company-switcher'))

    expect(screen.getByTestId('company-switcher-new')).toBeInTheDocument()
  })

  it('does not offer it to a non-admin whose list is empty', () => {
    const { container } = render(<CompanySwitcher />)

    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing at all until the list has answered', () => {
    asSuperAdmin(true)
    useCompanyStore.setState({ companies: [], activeCompanyId: null, loaded: false, error: null })

    const { container } = render(<CompanySwitcher />)

    expect(container).toBeEmptyDOMElement()
  })
})
