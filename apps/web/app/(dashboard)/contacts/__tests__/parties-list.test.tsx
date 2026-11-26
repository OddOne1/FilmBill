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
import { CHIP_CLASS, DROP, MAX_CHIPS } from '../contacts-table'
import { findPartyWords } from '@/test/user-words'
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
    is_crew: false,
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
    name_not_split: false,
    is_sole_proprietor: false,
    needs_attention_reasons: [],
    restricted_withheld: false,
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
    tax_ids: [],
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
      '/contacts/p1',
    )
  })

  it('asks the server to search, rather than filtering here', async () => {
    mockApi()
    render(<PartiesPage />)
    await screen.findByText('ORF')

    fireEvent.change(screen.getByLabelText('Search contacts'), {
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
      'No contacts yet',
    )

    fireEvent.change(screen.getByLabelText('Search contacts'), {
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
        name_addition: null,
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
        'A department cannot belong to a person — Maria Huber is a person. Choose an organisation instead.',
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

describe('names (P1b-4)', () => {
  it('a new person is entered as first, middle and last name, never as one name', async () => {
    mockApi()
    render(<PartiesPage />)
    await screen.findByText('ORF')
    fireEvent.click(screen.getByTestId('parties-new'))
    fireEvent.change(await screen.findByLabelText('What kind'), {
      target: { value: 'person' },
    })
    // The organisation-only fields are gone, the person's are there.
    expect(screen.queryByLabelText('Additional line')).not.toBeInTheDocument()
    const submit = screen.getByTestId('parties-new-submit')
    expect(submit).toBeDisabled()
    expect(submit).toHaveTextContent('Enter a first or last name first')

    fireEvent.change(screen.getByTestId('parties-new-first'), { target: { value: 'Philipp' } })
    fireEvent.change(screen.getByTestId('parties-new-middle'), { target: { value: 'Maximilian' } })
    fireEvent.change(screen.getByTestId('parties-new-last'), { target: { value: 'Reisinger' } })
    fireEvent.change(screen.getByLabelText('Position'), { target: { value: 'DoP' } })
    fireEvent.click(submit)

    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/parties', {
        type: 'person',
        first_name: 'Philipp',
        middle_name: 'Maximilian',
        last_name: 'Reisinger',
        title: null,
        position: 'DoP',
        parent_id: null,
      }),
    )
  })

  it('an organisation gets its additional line, and the list shows it beneath the name', async () => {
    mockApi([makeParty({ name_addition: 'Studio Vienna' })])
    render(<PartiesPage />)
    expect(await screen.findByTestId('party-name-addition-p1')).toHaveTextContent('Studio Vienna')

    fireEvent.click(screen.getByTestId('parties-new'))
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Neue GmbH' } })
    fireEvent.change(screen.getByLabelText('Additional line'), { target: { value: 'c/o Studio' } })
    fireEvent.click(screen.getByTestId('parties-new-submit'))
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/parties', {
        type: 'organisation',
        name: 'Neue GmbH',
        name_addition: 'c/o Studio',
        parent_id: null,
      }),
    )
  })

  it('a person whose name is not split carries the reason as its chip', async () => {
    mockApi([
      makeParty({
        id: 'p9',
        type: 'person',
        name: 'Reisinger Philipp Maximilian',
        name_not_split: true,
        needs_attention_reasons: ['name_not_split'],
      }),
    ])
    render(<PartiesPage />)
    expect(await screen.findByTestId('party-chip-attention-p9')).toHaveTextContent(
      'Name not split',
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


// ─── The needs-attention worklist (P1b-2) ──────────────────────────────────

describe('needs attention', () => {
  it('asks the server for flagged parties, rather than filtering here', async () => {
    mockApi()
    render(<PartiesPage />)
    await screen.findByText('ORF')

    fireEvent.click(screen.getByRole('tab', { name: 'Needs attention' }))

    await waitFor(() => expect(lastPath()).toContain('needs_attention=true'))
  })

  it('shows a chip on any tab, not only the worklist', async () => {
    // Somebody opening a customer for an unrelated reason should see that a
    // registry answer about them changed.
    mockApi([
      makeParty({
        // What the API sends with a flagged identifier (P1b-4 served field).
        needs_attention_reasons: ['tax_id_changed'],
        tax_ids: [
          {
            id: 't1',
            kind: 'vat',
            country: 'DE',
            value: 'DE123456789',
            value_as_entered: 'DE123456789',
            is_primary: true,
            is_active: true,
            notes: null,
            replaces_id: null,
            verifiable: true,
            initial_checks_done: 1,
            next_check_at: null,
            unavailable_streak: 0,
            needs_attention: true,
            needs_attention_reason: 'the registry’s answer changed',
            latest_check: null,
            created_at: '2026-10-24T00:00:00Z',
            updated_at: '2026-10-24T00:00:00Z',
          },
        ],
      }),
    ])
    render(<PartiesPage />)

    expect(
      await screen.findByTestId('party-chip-attention-p1'),
    ).toHaveTextContent('Needs attention')
  })

  it('an empty worklist says nothing changed, not “no parties”', async () => {
    // "No parties yet. Add the first customer" on a worklist would read as
    // though the customer list were empty.
    mockApi([])
    render(<PartiesPage />)
    await screen.findByTestId('parties-empty')

    fireEvent.click(screen.getByRole('tab', { name: 'Needs attention' }))

    await waitFor(() =>
      expect(screen.getByTestId('parties-empty')).toHaveTextContent(
        'Nothing needs attention',
      ),
    )
  })
})


describe('the layout that uses the window (P1b-4 part 4)', () => {
  const everyHat = makeParty({
    id: 'p4',
    name: 'Studio Licht',
    is_customer: true,
    is_supplier: true,
    is_partner: true,
    is_crew: true,
  })

  it('is full width: no narrow centred container', async () => {
    mockApi()
    const { container } = render(<PartiesPage />)
    await screen.findByText('ORF')
    const outer = container.firstElementChild as HTMLElement
    expect(outer.className).not.toMatch(/max-w-/)
    expect(outer.className).toContain('w-full')
  })

  it.each([480, 1024, 1920])(
    'role chips are one fixed-height line at %ipx, with the rest as +N',
    async (width) => {
      window.innerWidth = width
      window.dispatchEvent(new Event('resize'))
      mockApi([everyHat])
      render(<PartiesPage />)
      const roles = await screen.findByTestId('party-roles-p4')
      // The container never wraps; every chip has a fixed height, no wrap.
      expect(roles.className).toContain('flex-nowrap')
      const chips = Array.from(roles.children) as HTMLElement[]
      expect(chips).toHaveLength(MAX_CHIPS + 1)
      for (const chip of chips) {
        for (const cls of ['h-5', 'whitespace-nowrap', 'shrink-0']) {
          expect(chip.className.split(' ')).toContain(cls)
        }
      }
      expect(CHIP_CLASS).toContain('whitespace-nowrap')
    },
  )

  it('overflow becomes +N with every role in the tooltip', async () => {
    mockApi([everyHat])
    render(<PartiesPage />)
    const more = await screen.findByTestId('party-roles-more-p4')
    expect(more).toHaveTextContent('+2')
    expect(more).toHaveAttribute('title', 'Customer, Supplier, Partner, Crew')
  })

  it('columns drop in order as the window narrows; name, type and roles never do', async () => {
    mockApi()
    render(<PartiesPage />)
    await screen.findByText('ORF')
    const cls = (key: string) => screen.getByTestId(`contacts-th-${key}`).className
    for (const key of ['name', 'type', 'roles']) expect(cls(key)).not.toContain('hidden')
    // Tags and city first (below 2xl), then phone (below xl), then email (below lg).
    expect(cls('tags')).toContain('hidden 2xl:table-cell')
    expect(cls('city')).toContain('hidden 2xl:table-cell')
    expect(cls('phone')).toContain('hidden xl:table-cell')
    expect(cls('email')).toContain('hidden lg:table-cell')
    // The same class on the body cells, so a column drops as a whole.
    expect(screen.getByTestId('contacts-td-phone-p1').className).toContain(DROP.phone)
  })

  it('shows the contact columns from the row the API served', async () => {
    mockApi([
      makeParty({
        country: 'AT',
        city: 'Wien',
        primary_email: 'office@orf.at',
        primary_phone: '+43 1 870',
        tags: ['Broadcaster'],
        name_addition: 'Küniglberg',
      }),
    ])
    render(<PartiesPage />)
    await screen.findByText('ORF')
    expect(screen.getByTestId('contacts-td-country-p1')).toHaveTextContent('Austria (AUT)')
    expect(screen.getByTestId('contacts-td-city-p1')).toHaveTextContent('Wien')
    expect(screen.getByTestId('contacts-td-email-p1')).toHaveTextContent('office@orf.at')
    expect(screen.getByTestId('contacts-td-phone-p1')).toHaveTextContent('+43 1 870')
    expect(screen.getByTestId('contacts-td-tags-p1')).toHaveTextContent('Broadcaster')
    expect(screen.getByTestId('contacts-td-name-p1')).toHaveTextContent('Küniglberg')
  })

  it('the picker hides a column and the browser remembers it', async () => {
    window.localStorage.clear()
    mockApi()
    const first = render(<PartiesPage />)
    await screen.findByText('ORF')
    fireEvent.click(screen.getByTestId('contacts-columns'))
    // Name, type and roles cannot be hidden.
    expect(screen.queryByTestId('contacts-column-name')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('contacts-column-email'))
    expect(screen.queryByTestId('contacts-th-email')).not.toBeInTheDocument()
    first.unmount()

    render(<PartiesPage />)
    await screen.findByText('ORF')
    expect(screen.queryByTestId('contacts-th-email')).not.toBeInTheDocument()
    expect(screen.getByTestId('contacts-th-phone')).toBeInTheDocument()
    window.localStorage.clear()
  })

  it('storage that refuses to work never breaks the list', async () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError')
    })
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    mockApi()
    render(<PartiesPage />)
    await screen.findByText('ORF')
    fireEvent.click(screen.getByTestId('contacts-columns'))
    fireEvent.click(screen.getByTestId('contacts-column-email'))
    // Applied for this visit even though it could not be remembered.
    expect(screen.queryByTestId('contacts-th-email')).not.toBeInTheDocument()
    getItem.mockRestore()
    setItem.mockRestore()
  })

  it('the internal columns are there for somebody who may edit', async () => {
    mockApi([makeParty({ vat_verified: true, payment_terms_days: 30, last_check_at: '2026-11-20T10:00:00Z' })])
    render(<PartiesPage />)
    await screen.findByText('ORF')
    for (const key of ['vat', 'payment', 'tax_status', 'last_check']) {
      expect(screen.getByTestId(`contacts-th-${key}`)).toBeInTheDocument()
    }
    expect(screen.getByTestId('contacts-td-payment-p1')).toHaveTextContent('30 days')
    expect(screen.getByTestId('contacts-td-tax_status-p1')).toHaveTextContent('Business')
  })

  it('the internal columns are absent, header, cells and picker, without parties.edit', async () => {
    seedCompany(['parties.view'])
    mockApi([makeParty({ restricted_withheld: true, buyer_type: null })])
    render(<PartiesPage />)
    await screen.findByText('ORF')
    for (const key of ['vat', 'payment', 'tax_status', 'last_check']) {
      expect(screen.queryByTestId(`contacts-th-${key}`)).not.toBeInTheDocument()
      expect(screen.queryByTestId(`contacts-td-${key}-p1`)).not.toBeInTheDocument()
    }
    fireEvent.click(screen.getByTestId('contacts-columns'))
    expect(screen.queryByTestId('contacts-column-vat')).not.toBeInTheDocument()
    // The contact columns are still there.
    expect(screen.getByTestId('contacts-th-email')).toBeInTheDocument()
  })
})

describe('the word on the screen is Contacts (P1b-4 part 4)', () => {
  it('no state of the list shows “party” or “parties” to a person', async () => {
    mockApi([makeParty({ name_not_split: true, needs_attention_reasons: ['name_not_split'] })])
    const { container, unmount } = render(<PartiesPage />)
    await screen.findByText('ORF')
    fireEvent.click(screen.getByTestId('contacts-columns'))
    fireEvent.click(screen.getByTestId('parties-new'))
    for (const kind of ['organisation', 'department', 'person']) {
      fireEvent.change(screen.getByLabelText('What kind'), { target: { value: kind } })
      expect(findPartyWords(container)).toEqual([])
    }
    unmount()

    for (const tab of ['Needs attention', 'All']) {
      mockApi([])
      const empty = render(<PartiesPage />)
      fireEvent.click(await screen.findByRole('tab', { name: tab }))
      await screen.findByTestId('parties-empty')
      expect(findPartyWords(empty.container)).toEqual([])
      empty.unmount()
    }

    seedCompany(['parties.view'])
    mockApi()
    const reader = render(<PartiesPage />)
    await screen.findByText('ORF')
    expect(findPartyWords(reader.container)).toEqual([])
    reader.unmount()

    seedCompany([])
    const none = render(<PartiesPage />)
    expect(findPartyWords(none.container)).toEqual([])
  })
})


describe('Add people now (P1b-4 part 5)', () => {
  it('sends the people with the organisation, in the one create', async () => {
    mockApi()
    render(<PartiesPage />)
    await screen.findByText('ORF')
    fireEvent.click(screen.getByTestId('parties-new'))
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Kino GmbH' } })
    fireEvent.click(screen.getByTestId('parties-new-add-person'))
    fireEvent.click(screen.getByTestId('parties-new-add-person'))
    fireEvent.change(screen.getByLabelText('Person 1 first name'), { target: { value: 'Anna' } })
    fireEvent.change(screen.getByLabelText('Person 1 last name'), { target: { value: 'Adler' } })
    fireEvent.change(screen.getByLabelText('Person 1 email'), { target: { value: 'anna@kino.at' } })
    fireEvent.change(screen.getByLabelText('Person 2 last name'), { target: { value: 'Ohr' } })
    fireEvent.change(screen.getByLabelText('Person 2 position'), { target: { value: 'Ton' } })
    fireEvent.click(screen.getByTestId('parties-new-submit'))
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/parties', {
        type: 'organisation',
        name: 'Kino GmbH',
        name_addition: null,
        parent_id: null,
        people: [
          { first_name: 'Anna', last_name: 'Adler', email: 'anna@kino.at', phone: null, position: null },
          { first_name: null, last_name: 'Ohr', email: null, phone: null, position: 'Ton' },
        ],
      }),
    )
  })

  it('offers it only for an organisation, and a removed row is not sent', async () => {
    mockApi()
    render(<PartiesPage />)
    await screen.findByText('ORF')
    fireEvent.click(screen.getByTestId('parties-new'))
    fireEvent.click(await screen.findByTestId('parties-new-add-person'))
    fireEvent.click(screen.getByLabelText('Remove person 1'))
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Solo GmbH' } })
    fireEvent.click(screen.getByTestId('parties-new-submit'))
    await waitFor(() => expect(vi.mocked(api.post)).toHaveBeenCalled())
    expect(vi.mocked(api.post).mock.calls[0][1]).not.toHaveProperty('people')

    fireEvent.click(screen.getByTestId('parties-new'))
    fireEvent.change(await screen.findByLabelText('What kind'), { target: { value: 'person' } })
    expect(screen.queryByTestId('parties-new-add-person')).not.toBeInTheDocument()
  })
})
