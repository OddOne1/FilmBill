import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(() => Promise.resolve({})) },
  setActiveCompanyId: vi.fn(),
  ApiError: class extends Error {},
}))

import { CompanySwitcher } from '../company-switcher'
import { useCompanyStore } from '@/stores/company-store'
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

describe('CompanySwitcher', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    seed([], null)
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
