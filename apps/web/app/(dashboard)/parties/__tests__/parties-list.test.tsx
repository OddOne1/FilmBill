import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(), post: vi.fn(), delete: vi.fn() },
  setActiveCompanyId: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number
    detail: string
    constructor(status: number, detail: string) {
      super(detail)
      this.name = 'ApiError'
      this.status = status
      this.detail = detail
    }
  },
}))

import { api, ApiError } from '@/lib/api'
import PartiesPage from '../page'
import { useCompanyStore } from '@/stores/company-store'
import type { Party, PartyPage } from '@/types'

/**
 * The parties list.
 *
 * **The search is the server's.** A client-side filter would only ever
 * search the page it had already loaded, so every test here asserts what
 * went out in the query string rather than what got filtered in the browser.
 */

function makeParty(overrides: Partial<Party> = {}): Party {
  return {
    id: 'p1',
    type: 'organisation',
    parent_id: null,
    name: 'ORF',
    trading_name: null,
    legal_form: null,
    register_number: null,
    register_court: null,
    title: null,
    position: null,
    is_customer: true,
    is_supplier: false,
    is_partner: false,
    tags: [],
    party_number: 'K-0001',
    language: null,
    default_currency: null,
    payment_terms_days: null,
    payment_terms_text: null,
    po_reference: null,
    notes: null,
    business_status: 'auto',
    tax_country: 'AT',
    tax_status_note: null,
    recheck_interval_days: null,
    tax_override: null,
    einvoice_routing: {},
    is_active: true,
    created_at: '2026-10-23T00:00:00Z',
    updated_at: '2026-10-23T00:00:00Z',
    vat_verified: false,
    latest_vat_check: null,
    buyer_type: 'business',
    buyer_type_reason: 'organisation',
    addresses: [],
    contact_points: [],
    bank_accounts: [],
    children: [],
    duplicates: [],
    ...overrides,
  }
}

function page(parties: Party[]): PartyPage {
  return { parties, total: parties.length, limit: 50, offset: 0 }
}

function seedCompany(permissions = ['parties.view', 'parties.edit']) {
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

function mockApi(parties: Party[] = [makeParty()]) {
  vi.mocked(api.get).mockImplementation(async () => page(parties) as never)
  vi.mocked(api.post).mockImplementation(async () => makeParty() as never)
}

/** The last path `api.get` was asked for. */
function lastPath(): string {
  return String(vi.mocked(api.get).mock.calls.at(-1)?.[0] ?? '')
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany()
})

describe('the list', () => {
  it('shows each party with its number, type and role chips', async () => {
    mockApi([
      makeParty({ vat_verified: true }),
      makeParty({
        id: 'p2',
        name: 'Kamera GmbH',
        is_customer: false,
        is_supplier: true,
        party_number: 'L-0001',
        is_active: false,
      }),
    ])
    render(<PartiesPage />)

    // Gates on a fetched value, not on the heading.
    expect(await screen.findByText('ORF')).toBeInTheDocument()
    expect(screen.getByText('K-0001')).toBeInTheDocument()
    expect(screen.getByTestId('party-chip-verified-p1')).toHaveTextContent(
      'VAT checked',
    )
    expect(screen.getByTestId('party-chip-archived-p2')).toHaveTextContent(
      'Archived',
    )
  })

  it('links each row to its detail page', async () => {
    mockApi()
    render(<PartiesPage />)

    expect(await screen.findByTestId('party-row-p1')).toHaveAttribute(
      'href',
      '/parties/p1',
    )
  })

  it('asks the server to search, rather than filtering here', async () => {
    mockApi()
    render(<PartiesPage />)
    await screen.findByText('ORF')

    fireEvent.change(screen.getByLabelText('Search parties'), {
      target: { value: 'rundfunk' },
    })

    await waitFor(() => expect(lastPath()).toContain('search=rundfunk'))
  })

  it('sends the role filter the tab stands for', async () => {
    mockApi()
    render(<PartiesPage />)
    await screen.findByText('ORF')

    for (const [label, expected] of [
      ['Customers', 'role=customer'],
      ['Suppliers', 'role=supplier'],
      ['Partners', 'role=partner'],
      ['People', 'type=person'],
    ] as [string, string][]) {
      fireEvent.click(screen.getByRole('tab', { name: label }))
      await waitFor(() => expect(lastPath()).toContain(expected))
    }
  })

  it('asks for archived parties only when told to', async () => {
    mockApi()
    render(<PartiesPage />)
    await screen.findByText('ORF')
    expect(lastPath()).not.toContain('include_inactive')

    fireEvent.click(screen.getByLabelText(/Show archived/))
    await waitFor(() => expect(lastPath()).toContain('include_inactive=true'))
  })

  it('says what is empty, and distinguishes “none” from “none matching”', async () => {
    mockApi([])
    render(<PartiesPage />)

    expect(await screen.findByTestId('parties-empty')).toHaveTextContent(
      'No parties yet',
    )

    fireEvent.change(screen.getByLabelText('Search parties'), {
      target: { value: 'nobody' },
    })
    await waitFor(() =>
      expect(screen.getByTestId('parties-empty')).toHaveTextContent(
        'Nothing matches “nobody”',
      ),
    )
  })

  it('shows the server’s message when the list fails', async () => {
    vi.mocked(api.get).mockRejectedValue(
      new ApiError(503, 'The database is not reachable.'),
    )
    render(<PartiesPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'not reachable',
    )
  })
})

describe('adding a party', () => {
  it('explains each kind and posts what was chosen', async () => {
    mockApi()
    render(<PartiesPage />)
    await screen.findByText('ORF')

    fireEvent.click(screen.getByTestId('parties-new'))
    const panel = await screen.findByTestId('parties-new-panel')
    // The help line for the chosen kind, so "department" is not a guess.
    expect(panel).toHaveTextContent('The top of the tree')

    fireEvent.change(screen.getByLabelText('Name'), {
      target: { value: 'Neue GmbH' },
    })
    fireEvent.click(screen.getByTestId('parties-new-submit'))

    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/parties', {
        type: 'organisation',
        name: 'Neue GmbH',
        parent_id: null,
      }),
    )
  })

  it('will not submit a department without an organisation, and says why', async () => {
    mockApi()
    render(<PartiesPage />)
    await screen.findByText('ORF')
    fireEvent.click(screen.getByTestId('parties-new'))

    fireEvent.change(await screen.findByLabelText('What kind'), {
      target: { value: 'department' },
    })
    fireEvent.change(screen.getByLabelText('Name'), {
      target: { value: 'Redaktion' },
    })

    // Rule 17c: disabled, with the reason in the button's own label rather
    // than left for somebody to work out.
    const submit = screen.getByTestId('parties-new-submit')
    expect(submit).toBeDisabled()
    expect(submit).toHaveTextContent('Choose an organisation first')
  })

  it('shows the server’s sentence when a create is refused', async () => {
    mockApi()
    vi.mocked(api.post).mockRejectedValue(
      new ApiError(
        409,
        'A department cannot belong to a person. Allowed: organisation.',
      ),
    )
    render(<PartiesPage />)
    await screen.findByText('ORF')
    fireEvent.click(screen.getByTestId('parties-new'))
    fireEvent.change(screen.getByLabelText('Name'), {
      target: { value: 'Doomed' },
    })
    fireEvent.click(screen.getByTestId('parties-new-submit'))

    expect(await screen.findByTestId('parties-new-error')).toHaveTextContent(
      'cannot belong to a person',
    )
  })
})

describe('permissions', () => {
  it('a reader sees the list, no New button, and a sentence saying why', async () => {
    seedCompany(['parties.view'])
    mockApi()
    render(<PartiesPage />)

    expect(await screen.findByText('ORF')).toBeInTheDocument()
    expect(screen.queryByTestId('parties-new')).not.toBeInTheDocument()
    expect(
      screen.getByText(/read these but not change them/),
    ).toBeInTheDocument()
  })

  it('somebody without parties.view gets a sentence, not a blank page', async () => {
    seedCompany([])
    mockApi()
    render(<PartiesPage />)

    // Rule 17c: a redirect would make a permission problem look like a
    // broken link.
    expect(
      await screen.findByText(/do not have access to this company/),
    ).toBeInTheDocument()
    expect(screen.queryByTestId('parties-new')).not.toBeInTheDocument()
  })
})
