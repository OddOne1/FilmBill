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
vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'p-person' }),
  usePathname: () => '/parties/p-person',
}))

import { api, ApiError } from '@/lib/api'
import PartyDetailPage from '../[id]/page'
import { useCompanyStore } from '@/stores/company-store'
import type { Party, ResolvedRecipient, TaxProposal } from '@/types'

/**
 * The party detail screen.
 *
 * **Two rules this screen must not break**, and every test here is about one
 * of them.
 *
 * It does not derive inheritance. `GET /parties/{id}/resolved` says which
 * level each value came from, and the screen shows that — so the fixtures
 * below name a source the screen could not have worked out (`organisation`
 * for a field the person's own row also has a value for) and assert the
 * screen says `organisation`.
 *
 * It does not decide a VAT status or a tax. The chip shows the status the
 * server recorded and the tax panel shows what the engine proposed, so the
 * fixtures feed a code and a rate no local evaluator could produce.
 */

//: Values the screen could not have composed.
const SERVER_ONLY_CODE = 'XX-SERVER'
const SERVER_ONLY_RATE = '13.7500'

function makeParty(overrides: Partial<Party> = {}): Party {
  return {
    id: 'p-person',
    type: 'person',
    parent_id: 'p-dept',
    name: 'Maria Huber',
    trading_name: null,
    legal_form: null,
    register_number: null,
    register_court: null,
    title: null,
    position: 'Redakteurin',
    is_customer: false,
    is_supplier: false,
    is_partner: false,
    tags: [],
    party_number: null,
    language: null,
    default_currency: null,
    payment_terms_days: null,
    payment_terms_text: null,
    po_reference: null,
    notes: null,
    business_status: 'auto',
    tax_country: null,
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
    tax_ids: [],
    addresses: [],
    contact_points: [],
    bank_accounts: [],
    children: [],
    duplicates: [],
    ...overrides,
  }
}

function field(value: unknown, source: string | null) {
  return { value, source }
}

function makeResolved(overrides: Partial<ResolvedRecipient> = {}): ResolvedRecipient {
  return {
    own_level: 'person',
    ids: { person: 'p-person', department: 'p-dept', organisation: 'p-org' },
    names: {
      person: 'Maria Huber',
      department: 'Redaktion Kultur',
      organisation: 'ORF',
    },
    contact_name: field('Maria Huber', 'person'),
    invoice_name: field('ORF', 'organisation'),
    billing_address: field(
      {
        street: 'Würzburggasse 30',
        street2: null,
        zip: '1136',
        city: 'Wien',
        country: 'AT',
      },
      'organisation',
    ),
    vat_id: field('ATU16360407', 'organisation'),
    vat_verified: field(true, 'organisation'),
    tax_id_kind: field('vat', 'organisation'),
    tax_status_note: field(null, null),
    tax_id_evidence: field(null, null),
    tax_country: field('AT', 'organisation'),
    business_status: field('auto', 'person'),
    tax_override: field(null, null),
    language: field('de', 'organisation'),
    default_currency: field(null, null),
    payment_terms_days: field(30, 'organisation'),
    payment_terms_text: field(null, null),
    po_reference: field(null, null),
    email: field('office@orf.at', 'organisation'),
    phone: field(null, null),
    einvoice_routing: field(null, null),
    buyer_type: 'business',
    buyer_type_reason: 'organisation',
    ...overrides,
  }
}

function makePreview(overrides: Partial<TaxProposal> = {}): TaxProposal {
  return {
    seller_country: 'AT',
    treatment: 'eu_reverse_charge',
    treatment_label: 'Reverse charge (EU)',
    code: SERVER_ONLY_CODE,
    rate: SERVER_ONLY_RATE,
    legal_note: null,
    applied_rule: 'Rule 30',
    applied_rule_position: 30,
    reasons: [],
    warnings: [],
    blocking: false,
    would_have_applied: null,
    ...overrides,
  }
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

function mockApi({
  party = makeParty(),
  resolved = makeResolved(),
  preview = makePreview(),
}: {
  party?: Party
  resolved?: ResolvedRecipient
  preview?: TaxProposal
} = {}) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/parties/p-person') return party as never
    if (path === '/parties/p-person/resolved') return resolved as never
    throw new Error(`unexpected GET ${path}`)
  })
  vi.mocked(api.post).mockImplementation(async (path: string) => {
    if (path.endsWith('/tax-preview')) return preview as never
    return party as never
  })
  vi.mocked(api.patch).mockImplementation(async () => party as never)
}

/** Gate on a fetched value, never on the tab strip, which paints at once. */
async function ready() {
  return screen.findByTestId('party-resolved-panel')
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany()
})

describe('the resolved panel answers “what will the invoice say”', () => {
  it('shows each value with the level it came from', async () => {
    mockApi()
    render(<PartyDetailPage />)
    const panel = await ready()

    // The organisation's name, from the API's `invoice_name` — a field the
    // screen has no way to derive, since the party row says "Maria Huber".
    expect(panel).toHaveTextContent('ORF')
    expect(panel).toHaveTextContent('Maria Huber')
    expect(panel).toHaveTextContent('Würzburggasse 30')
    expect(panel).toHaveTextContent('ATU16360407')
  })

  it('names the source of an inherited value', async () => {
    mockApi()
    render(<PartyDetailPage />)
    await ready()

    // "from ORF — organisation": the difference between a user understanding
    // why the address is what it is and editing the wrong record.
    const marks = screen.getAllByTestId('inherited-from-organisation')
    expect(marks.length).toBeGreaterThan(0)
    expect(marks[0]).toHaveTextContent('from ORF — organisation')
  })

  it('does not mark the party’s own value as inherited', async () => {
    mockApi()
    render(<PartyDetailPage />)
    await ready()

    // `contact_name` has source `person`, which IS this party's level.
    expect(screen.queryByTestId('inherited-from-person')).not.toBeInTheDocument()
  })

  it('follows the API when a department’s own address wins', async () => {
    mockApi({
      resolved: makeResolved({
        billing_address: field(
          {
            street: 'Argentinierstraße 30a',
            street2: null,
            zip: '1040',
            city: 'Wien',
            country: 'AT',
          },
          'department',
        ),
      }),
    })
    render(<PartyDetailPage />)
    const panel = await ready()

    expect(panel).toHaveTextContent('Argentinierstraße 30a')
    expect(
      screen.getAllByTestId('inherited-from-department')[0],
    ).toHaveTextContent('from Redaktion Kultur — department')
  })

  it('says so when nothing in the hierarchy has an address', async () => {
    mockApi({
      resolved: makeResolved({ billing_address: field(null, null) }),
    })
    render(<PartyDetailPage />)
    await ready()

    fireEvent.click(screen.getByRole('tab', { name: 'Addresses' }))
    expect(
      await screen.findByTestId('party-billing-resolved'),
    ).toHaveTextContent('None anywhere in the hierarchy')
  })
})

describe('the Tax tab shows what the server decided', () => {
  it('shows the derived buyer type with its reason in words', async () => {
    mockApi()
    render(<PartyDetailPage />)
    await ready()

    fireEvent.click(screen.getByRole('tab', { name: 'Tax' }))

    const line = await screen.findByTestId('party-buyer-type')
    expect(line).toHaveTextContent('business')
    expect(line).toHaveTextContent('an organisation, or somebody inside one')
  })

  it('shows a sole trader’s reason', async () => {
    mockApi({
      party: makeParty({
        buyer_type: 'business',
        buyer_type_reason: 'person_with_vat_id',
      }),
    })
    render(<PartyDetailPage />)
    await ready()
    fireEvent.click(screen.getByRole('tab', { name: 'Tax' }))

    expect(await screen.findByTestId('party-buyer-type')).toHaveTextContent(
      'a person with a VAT ID — a sole trader',
    )
  })

  it('shows the engine’s own code and rate, not one of its own', async () => {
    mockApi()
    render(<PartyDetailPage />)
    await ready()
    fireEvent.click(screen.getByRole('tab', { name: 'Tax' }))

    const panel = await screen.findByTestId('party-tax-preview')
    expect(panel).toHaveTextContent(SERVER_ONLY_CODE)
    expect(panel).toHaveTextContent(`${SERVER_ONLY_RATE} %`)
    expect(panel).toHaveTextContent('Reverse charge (EU)')
  })

  it('says plainly when nothing can be proposed', async () => {
    mockApi({
      preview: makePreview({
        blocking: true,
        code: null,
        rate: null,
        treatment: null,
        treatment_label: null,
        warnings: [
          {
            code: 'code_inactive',
            text: 'The tax code this resolves to is inactive, so nothing can be proposed.',
          },
        ],
      }),
    })
    render(<PartyDetailPage />)
    await ready()
    fireEvent.click(screen.getByRole('tab', { name: 'Tax' }))

    const panel = await screen.findByTestId('party-tax-preview')
    expect(panel).toHaveTextContent('Nothing can be proposed yet')
    expect(
      screen.getByTestId('party-tax-warning-code_inactive'),
    ).toHaveTextContent('inactive')
  })

  // `shows each VIES status as its own chip`, `marks a name VIES holds
  // that differs from the party's own` and `says what this check is not`
  // lived here until P1b-2 part 5. The Tax tab is a LIST of typed
  // identifiers now, and each chip belongs to one identifier's latest
  // check rather than to the party — so those tests moved to
  // `party-tax-ids.test.tsx`, where they cover six states instead of
  // four and the manual-confirmation case that the party-level version
  // could not express at all.

})

describe('hierarchy and archiving', () => {
  it('lists the children', async () => {
    mockApi({
      party: makeParty({
        id: 'p-person',
        children: [
          {
            id: 'p-child',
            type: 'person',
            name: 'Maria Huber',
            position: null,
            party_number: null,
            is_active: true,
          },
        ],
      }),
    })
    render(<PartyDetailPage />)
    await ready()

    fireEvent.click(screen.getByRole('tab', { name: 'Hierarchy' }))
    expect(await screen.findByTestId('party-child-p-child')).toHaveTextContent(
      'Maria Huber',
    )
  })

  it('says so when there is nothing below', async () => {
    mockApi()
    render(<PartyDetailPage />)
    await ready()

    fireEvent.click(screen.getByRole('tab', { name: 'Hierarchy' }))
    expect(await screen.findByTestId('party-no-children')).toHaveTextContent(
      'Nothing below this one',
    )
  })

  it('shows the 409 naming the children when archiving is refused', async () => {
    mockApi()
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path.endsWith('/tax-preview')) return makePreview() as never
      throw new ApiError(
        409,
        'ORF still has active entries below it: Redaktion Kultur (department). Archive those first — archiving never deletes.',
      )
    })
    render(<PartyDetailPage />)
    await ready()

    fireEvent.click(screen.getByTestId('party-archive'))

    const error = await screen.findByTestId('party-archive-error')
    expect(error).toHaveTextContent('Redaktion Kultur')
    expect(error).toHaveTextContent('never deletes')
  })

  it('shows duplicate warnings with a link to the other one', async () => {
    mockApi({
      party: makeParty({
        duplicates: [
          {
            party_id: 'p-other',
            name: 'ORF',
            reason: 'vat_id',
            detail: 'ORF already has the VAT ID ATU16360407.',
          },
        ],
      }),
    })
    render(<PartyDetailPage />)
    await ready()

    const warning = await screen.findByTestId('party-duplicate-vat_id')
    expect(warning).toHaveTextContent('already has the VAT ID')
    expect(warning.querySelector('a')).toHaveAttribute('href', '/parties/p-other')
  })
})

describe('a member who may read but not edit', () => {
  it('sees the values and no way to change them, and is told why', async () => {
    seedCompany(['parties.view'])
    mockApi()
    render(<PartyDetailPage />)
    await ready()

    expect(
      screen.getByText(/read this party but not change it/),
    ).toBeInTheDocument()
    expect(screen.queryByTestId('party-archive')).not.toBeInTheDocument()
    expect(screen.queryByTestId('party-basics-save')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: 'Tax' }))
    // The values are all readable — a reader who cannot see the tax position
    // cannot answer a question about an invoice.
    expect(await screen.findByTestId('party-buyer-type')).toBeInTheDocument()
    expect(screen.getByTestId('party-resolved-tax-id')).toHaveTextContent(
      'ATU16360407',
    )
    // And nothing is editable.
    expect(screen.queryByTestId('party-tax-save')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Country for tax')).toBeDisabled()
    expect(screen.getByLabelText('Tax status note')).toBeDisabled()
  })
})
