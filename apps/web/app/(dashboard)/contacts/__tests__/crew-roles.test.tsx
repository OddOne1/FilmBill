import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), upload: vi.fn(), download: vi.fn() },
  setActiveCompanyId: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number
    detail: string
    constructor(status: number, detail: string) {
      super(detail)
      this.status = status
      this.detail = detail
    }
  },
}))
vi.mock('next/navigation', () => ({
  usePathname: () => '/contacts',
  useParams: () => ({}),
}))

import { api } from '@/lib/api'
import ContactsPage from '../page'
import { CrewRolesSection } from '../[id]/crew-roles'
import { useCompanyStore } from '@/stores/company-store'
import type { CrewMember, CrewRole, PartyCrewRoles } from '@/types'

/**
 * Roles & rates (R1): the person page's section and the Crew tab's rate
 * filter, sort and column.
 *
 * Every number is the API's, so the fixtures carry values the page could
 * not have worked out (an effective rate that is NOT own-else-typical of the
 * fixture's own fields would still be shown as given): what is under test
 * is what the page SENDS and which of the API's fields it draws — and that
 * without `budgets.view` it draws no rate and offers no rate control.
 */

function seed(permissions: string[]) {
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

const ALL = ['parties.view', 'parties.edit', 'budgets.view', 'catalog.view']
const NO_COST = ['parties.view', 'parties.edit', 'catalog.view']

function role(overrides: Partial<CrewRole> = {}): CrewRole {
  return {
    id: 'r1',
    item_id: 'i-dop',
    name: 'DoP',
    notes: null,
    own_day_rate: '650.00',
    typical_day_rate: '600.00',
    effective_day_rate: '650.00',
    rate_source: 'own',
    difference_to_typical: '50.00',
    ...overrides,
  }
}

function roles(overrides: Partial<PartyCrewRoles> = {}): PartyCrewRoles {
  return {
    party_id: 'p1',
    is_crew: true,
    rates_visible: true,
    roles: [role()],
    tag_suggestions: [{ tag: 'gaffer', item_id: 'i-gaf', item_name: 'Gaffer' }],
    ...overrides,
  }
}

const LABOR = {
  items: [
    { id: 'i-dop', name_de: 'DoP', kind: 'labor', is_active: true },
    { id: 'i-gaf', name_de: 'Gaffer', kind: 'labor', is_active: true },
    { id: 'i-snd', name_de: 'Sound', kind: 'labor', is_active: true },
  ],
  total: 3,
  limit: 500,
  offset: 0,
}

describe('Roles & rates on the person page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows own, typical and the difference as the API gives them', async () => {
    seed(ALL)
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/parties/p1/crew-roles') return roles()
      if (path.startsWith('/catalog/items')) return LABOR
      throw new Error(`unexpected GET ${path}`)
    })
    render(<CrewRolesSection partyId="p1" partyName="Anna Huber" canEdit onChanged={() => {}} />)
    const row = await screen.findByTestId('crew-role')
    expect(within(row).getByTestId('crew-role-effective')).toHaveTextContent('650.00')
    expect(within(row).getByTestId('crew-role-source')).toHaveTextContent(/own, \+.*50\.00 above typical/)
    expect(within(row).getByTestId('crew-role-typical')).toHaveTextContent(/typical: .*600\.00/)
    expect(screen.getByTestId('crew-roles-typical-link')).toHaveAttribute('href', '/catalog?kind=labor')
  })

  it('adds a role from the labor items with an own rate, and offers a matching tag once', async () => {
    seed(ALL)
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/parties/p1/crew-roles') return roles()
      if (path.startsWith('/catalog/items')) return LABOR
      throw new Error(`unexpected GET ${path}`)
    })
    vi.mocked(api.post).mockResolvedValue(roles())
    const changed = vi.fn()
    render(<CrewRolesSection partyId="p1" partyName="Anna Huber" canEdit onChanged={changed} />)
    await screen.findByTestId('crew-role')
    await waitFor(() => expect(within(screen.getByTestId('crew-roles-item')).getByText('Sound')).toBeInTheDocument())
    // DoP is held already, so it is not offered again.
    expect(within(screen.getByTestId('crew-roles-item')).queryByText('DoP')).toBeNull()
    // The search narrows the list.
    fireEvent.change(screen.getByTestId('crew-roles-search'), { target: { value: 'sou' } })
    expect(within(screen.getByTestId('crew-roles-item')).queryByText('Gaffer')).toBeNull()
    fireEvent.change(screen.getByTestId('crew-roles-item'), { target: { value: 'i-snd' } })
    fireEvent.change(screen.getByTestId('crew-roles-own-rate'), { target: { value: '420,50' } })
    fireEvent.click(screen.getByTestId('crew-roles-add'))
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/parties/p1/crew-roles', { item_id: 'i-snd', own_day_rate: '420.50' }),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Use ‘gaffer’ as a role' }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/parties/p1/crew-roles/from-tag', { tag: 'gaffer' }))
    // The party's tags changed, so the page is told to reload them.
    await waitFor(() => expect(changed).toHaveBeenCalled())
  })

  it('clears an own rate with null so the typical rate applies again', async () => {
    seed(ALL)
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/parties/p1/crew-roles') return roles()
      if (path.startsWith('/catalog/items')) return LABOR
      throw new Error(`unexpected GET ${path}`)
    })
    vi.mocked(api.patch).mockResolvedValue(roles())
    render(<CrewRolesSection partyId="p1" partyName="Anna Huber" canEdit onChanged={() => {}} />)
    const input = await screen.findByTestId('crew-role-own-rate')
    fireEvent.change(input, { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/parties/p1/crew-roles/r1', { own_day_rate: null }))
  })

  it('draws no rate and no rate field when the API says rates are not visible', async () => {
    seed(NO_COST)
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/parties/p1/crew-roles')
        return roles({
          rates_visible: false,
          roles: [role({ own_day_rate: null, typical_day_rate: null, effective_day_rate: null, rate_source: null, difference_to_typical: null })],
        })
      if (path.startsWith('/catalog/items')) return LABOR
      throw new Error(`unexpected GET ${path}`)
    })
    render(<CrewRolesSection partyId="p1" partyName="Anna Huber" canEdit onChanged={() => {}} />)
    const row = await screen.findByTestId('crew-role')
    expect(row).toHaveTextContent('DoP')
    expect(screen.getByTestId('crew-roles-no-rates')).toHaveTextContent('Rates are hidden')
    expect(screen.queryByTestId('crew-role-effective')).toBeNull()
    expect(screen.queryByTestId('crew-role-own-rate')).toBeNull()
    expect(screen.queryByTestId('crew-roles-own-rate')).toBeNull()
    expect(document.body.textContent).not.toMatch(/€|EUR|\d+[.,]\d{2}/)
  })
})

function member(overrides: Partial<CrewMember> = {}): CrewMember {
  return {
    id: 'p1',
    name: 'Anna Huber',
    roles: ['Drone pilot'],
    crew_roles: [role()],
    rates_visible: true,
    rate_low: '650.00',
    rate_high: '650.00',
    rate_source: 'own',
    email: null,
    phone: null,
    notes: null,
    is_active: true,
    is_customer: false,
    is_supplier: false,
    party_number: null,
    created_at: '2026-11-26T10:00:00Z',
    ...overrides,
  }
}

function routeCrew(members: CrewMember[]) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/crew/roles')
      return { used: ['Drone pilot'], catalog: [], labor_items: [{ item_id: 'i-dop', name: 'DoP' }, { item_id: 'i-gaf', name: 'Gaffer' }] }
    if (path.startsWith('/crew')) return { members, total: members.length }
    if (path.startsWith('/parties')) return { parties: [], total: 0 }
    throw new Error(`unexpected GET ${path}`)
  })
}

describe('Contacts → Crew: role and rate', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.history.replaceState(null, '', '/contacts?tab=crew')
  })

  it('filters by a labor item and a rate range, sorts by rate, and shows own/typical', async () => {
    seed(ALL)
    routeCrew([member()])
    render(<ContactsPage />)
    await screen.findAllByTestId('crew-row')
    await waitFor(() => expect(within(screen.getByTestId('crew-role-filter')).getByText('DoP')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('crew-role-filter'), { target: { value: 'item:i-dop' } })
    fireEvent.change(screen.getByTestId('crew-rate-min'), { target: { value: '500' } })
    fireEvent.change(screen.getByTestId('crew-rate-max'), { target: { value: '700,50' } })
    fireEvent.change(screen.getByTestId('crew-sort'), { target: { value: 'rate' } })
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith('/crew?role_item_id=i-dop&min_rate=500&max_rate=700.50&sort=rate'),
    )
    const cell = screen.getByTestId('crew-row-rate')
    expect(cell).toHaveTextContent('650.00')
    expect(within(cell).getByTestId('crew-rate-source')).toHaveTextContent('own')
    // The catalog role and the remaining tag are both shown.
    expect(screen.getByTestId('crew-row-role')).toHaveTextContent('DoP')
    expect(screen.getByTestId('crew-typical-rates-link')).toHaveAttribute('href', '/catalog?kind=labor')
  })

  it('shows lowest to highest when no role is selected', async () => {
    seed(ALL)
    routeCrew([member({ rate_low: '450.00', rate_high: '520.00', rate_source: null })])
    render(<ContactsPage />)
    const cell = await screen.findByTestId('crew-row-rate')
    expect(cell.textContent).toMatch(/450\.00.*–.*520\.00/)
  })

  it('has no rate filter, no rate sort and no rate column without budgets.view', async () => {
    seed(NO_COST)
    routeCrew([member({ rates_visible: false, rate_low: null, rate_high: null, rate_source: null, crew_roles: [role({ own_day_rate: null, typical_day_rate: null, effective_day_rate: null, rate_source: null, difference_to_typical: null })] })])
    render(<ContactsPage />)
    await screen.findAllByTestId('crew-row')
    expect(screen.queryByTestId('crew-rate-min')).toBeNull()
    expect(screen.queryByTestId('crew-rate-max')).toBeNull()
    expect(screen.queryByTestId('crew-row-rate')).toBeNull()
    expect(within(screen.getByTestId('crew-sort')).queryByText(/Day rate/)).toBeNull()
    expect(screen.getByTestId('crew-row-role')).toHaveTextContent('DoP')
  })
})
