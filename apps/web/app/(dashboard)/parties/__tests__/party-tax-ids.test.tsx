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
  useParams: () => ({ id: 'p1' }),
  usePathname: () => '/parties/p1',
}))

import { api, ApiError } from '@/lib/api'
import PartyDetailPage from '../[id]/page'
import { useCompanyStore } from '@/stores/company-store'
import type {
  Party,
  PartyTaxId,
  PartyVatCheck,
  ResolvedRecipient,
  TaxProposal,
} from '@/types'

/**
 * The Tax tab as a list of typed identifiers.
 *
 * **The rule every test here protects**: the screen reads the API's
 * `vat_verified` flag and never the chip beside it. Those two legitimately
 * disagree, and the case where they do is the expensive one — a manual
 * confirmation on an EU VAT ID shows a green chip and is NOT verified for
 * tax purposes (D66). A screen that read the chip would put a reverse charge
 * on an invoice with nothing behind it.
 *
 * So the fixtures below deliberately pair a `valid` check with
 * `vat_verified: false`, which no local derivation could produce.
 */

function check(overrides: Partial<PartyVatCheck> = {}): PartyVatCheck {
  return {
    id: 'c1',
    tax_id_id: 't1',
    vat_id: 'DE123456789',
    country: 'DE',
    kind: 'vat',
    provider: 'vies',
    trigger_kind: 'manual',
    qualified: false,
    status: 'valid',
    response: {},
    consultation_ref: null,
    registered_name: null,
    registered_address: null,
    name_match: null,
    address_match: null,
    registration_active: null,
    gst_registered: null,
    manual_note: null,
    evidence_reference: null,
    valid_until: null,
    change_detected: null,
    checked_at: '2026-10-24T10:00:00Z',
    detail: 'the server said so',
    trader_name: null,
    trader_address: null,
    ...overrides,
  }
}

function taxId(overrides: Partial<PartyTaxId> = {}): PartyTaxId {
  return {
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
    next_check_at: '2027-01-22T10:00:00Z',
    unavailable_streak: 0,
    needs_attention: false,
    needs_attention_reason: null,
    latest_check: check(),
    created_at: '2026-10-24T00:00:00Z',
    updated_at: '2026-10-24T00:00:00Z',
    ...overrides,
  }
}

function makeParty(overrides: Partial<Party> = {}): Party {
  return {
    id: 'p1',
    type: 'organisation',
    parent_id: null,
    name: 'Berlin Produktion GmbH',
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
    party_number: 'K-0002',
    language: null,
    default_currency: null,
    payment_terms_days: null,
    payment_terms_text: null,
    po_reference: null,
    notes: null,
    business_status: 'auto',
    tax_country: 'DE',
    tax_status_note: null,
    recheck_interval_days: null,
    tax_override: null,
    einvoice_routing: {},
    is_active: true,
    created_at: '2026-10-24T00:00:00Z',
    updated_at: '2026-10-24T00:00:00Z',
    vat_verified: true,
    latest_vat_check: null,
    buyer_type: 'business',
    buyer_type_reason: 'organisation',
    tax_ids: [taxId()],
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
    own_level: 'organisation',
    ids: { organisation: 'p1' },
    names: { organisation: 'Berlin Produktion GmbH' },
    contact_name: field('Berlin Produktion GmbH', 'organisation'),
    invoice_name: field('Berlin Produktion GmbH', 'organisation'),
    billing_address: field(null, null),
    vat_id: field('DE123456789', 'organisation'),
    vat_verified: field(true, 'organisation'),
    tax_id_kind: field('vat', 'organisation'),
    tax_status_note: field(null, null),
    tax_id_evidence: field(null, null),
    tax_country: field('DE', 'organisation'),
    business_status: field('auto', 'organisation'),
    tax_override: field(null, null),
    language: field(null, null),
    default_currency: field(null, null),
    payment_terms_days: field(null, null),
    payment_terms_text: field(null, null),
    po_reference: field(null, null),
    email: field(null, null),
    phone: field(null, null),
    einvoice_routing: field(null, null),
    buyer_type: 'business',
    buyer_type_reason: 'organisation',
    ...overrides,
  }
}

function makePreview(): TaxProposal {
  return {
    seller_country: 'AT',
    treatment: 'eu_reverse_charge',
    treatment_label: 'Reverse charge (EU)',
    code: 'EU-AE',
    rate: '0.0000',
    legal_note: null,
    applied_rule: 'Rule 30',
    applied_rule_position: 30,
    reasons: [],
    warnings: [],
    blocking: false,
    would_have_applied: null,
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
  history = [check()],
}: { party?: Party; resolved?: ResolvedRecipient; history?: PartyVatCheck[] } = {}) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/parties/p1') return party as never
    if (path === '/parties/p1/resolved') return resolved as never
    if (path.includes('/checks')) return history as never
    throw new Error(`unexpected GET ${path}`)
  })
  vi.mocked(api.post).mockImplementation(async (path: string) => {
    if (path.endsWith('/tax-preview')) return makePreview() as never
    return {} as never
  })
  vi.mocked(api.patch).mockImplementation(async () => party as never)
}

async function openTax() {
  await screen.findByTestId('party-resolved-panel')
  fireEvent.click(screen.getByRole('tab', { name: 'Tax' }))
  return screen.findByTestId('party-resolved-tax-id')
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany()
})

describe('every chip state', () => {
  const states: [string, string][] = [
    ['valid', 'Confirmed'],
    ['invalid', 'Not valid'],
    ['unavailable', 'No answer'],
    ['not_configured', 'Not set up'],
    ['not_checkable', 'Not checkable'],
  ]

  it.each(states)('shows %s as “%s”', async (status, label) => {
    mockApi({
      party: makeParty({
        tax_ids: [taxId({ latest_check: check({ status: status as never }) })],
      }),
    })
    render(<PartyDetailPage />)
    await openTax()

    expect(
      await screen.findByTestId(`tax-id-status-${status}-t1`),
    ).toHaveTextContent(label)
  })

  it('explains the three states that are not findings, in plain words', async () => {
    for (const [status, phrase] of [
      ['unavailable', 'not a finding about the number'],
      ['not_configured', 'Nothing about the number is in doubt'],
      ['not_checkable', 'Confirm it by hand instead'],
    ] as [string, string][]) {
      vi.clearAllMocks()
      mockApi({
        party: makeParty({
          tax_ids: [
            taxId({
              verifiable: status !== 'not_checkable',
              latest_check: check({ status: status as never }),
            }),
          ],
        }),
      })
      const view = render(<PartyDetailPage />)
      await openTax()

      expect(
        await screen.findByTestId(`tax-id-plain-${status}-t1`),
      ).toHaveTextContent(phrase)
      view.unmount()
    }
  })

  it('shows a manual confirmation as its own chip, never the registry’s', async () => {
    // A green "Confirmed" that turned out to be a colleague's note would be
    // the most expensive mistake available on this screen.
    mockApi({
      party: makeParty({
        tax_ids: [
          taxId({
            kind: 'ein',
            value: '123456789',
            verifiable: false,
            latest_check: check({
              provider: 'manual',
              kind: 'ein',
              status: 'valid',
              manual_note: 'W-9 on file.',
              evidence_reference: '2026/114',
            }),
          }),
        ],
      }),
    })
    render(<PartyDetailPage />)
    await openTax()

    expect(
      await screen.findByTestId('tax-id-status-manual-t1'),
    ).toHaveTextContent('Confirmed by hand')
    expect(screen.queryByTestId('tax-id-status-valid-t1')).not.toBeInTheDocument()
    expect(screen.getByText(/W-9 on file/)).toBeInTheDocument()
    expect(screen.getByText(/2026\/114/)).toBeInTheDocument()
  })
})

describe('the name comparison', () => {
  it('shows both names side by side', async () => {
    mockApi({
      party: makeParty({
        tax_ids: [
          taxId({
            latest_check: check({
              registered_name: 'Berlin Produktion Gesellschaft m.b.H.',
              name_match: 'partial',
            }),
          }),
        ],
      }),
    })
    render(<PartyDetailPage />)
    await openTax()

    expect(await screen.findByTestId('tax-id-name-partial-t1')).toHaveTextContent(
      'Name is close',
    )
    expect(screen.getByTestId('tax-id-registry-name-t1')).toHaveTextContent(
      'registry says: Berlin Produktion Gesellschaft m.b.H.',
    )
    expect(screen.getByTestId('tax-id-party-name-t1')).toHaveTextContent(
      'party says: Berlin Produktion GmbH',
    )
  })

  it.each([
    ['match', 'Name agrees'],
    ['partial', 'Name is close'],
    ['mismatch', 'Name differs'],
    ['unknown', 'No name given'],
  ])('shows %s as “%s”', async (result, label) => {
    mockApi({
      party: makeParty({
        tax_ids: [
          taxId({ latest_check: check({ name_match: result as never }) }),
        ],
      }),
    })
    render(<PartyDetailPage />)
    await openTax()

    expect(await screen.findByTestId(`tax-id-name-${result}-t1`)).toHaveTextContent(
      label,
    )
  })

  it('never writes the registry’s name onto the party', async () => {
    mockApi({
      party: makeParty({
        tax_ids: [
          taxId({
            latest_check: check({
              registered_name: 'Somebody Else AG',
              name_match: 'mismatch',
            }),
          }),
        ],
      }),
    })
    render(<PartyDetailPage />)
    await openTax()

    await screen.findByTestId('tax-id-name-mismatch-t1')
    // The heading still says the party's own name, and nothing was PATCHed.
    expect(
      screen.getByRole('heading', { name: 'Berlin Produktion GmbH' }),
    ).toBeInTheDocument()
    expect(vi.mocked(api.patch)).not.toHaveBeenCalled()
  })
})

describe('verified comes from the API, not from the chip', () => {
  it('says not verified although the chip is green', async () => {
    // **The case that makes this rule necessary**: a manual confirmation on
    // an EU VAT ID. The registry is reachable, so a human's word does not
    // count (D66) — and no local derivation from the chip could produce
    // this pair.
    mockApi({
      party: makeParty({
        vat_verified: false,
        tax_ids: [
          taxId({
            latest_check: check({ provider: 'manual', status: 'valid' }),
          }),
        ],
      }),
    })
    render(<PartyDetailPage />)
    await openTax()

    expect(await screen.findByTestId('party-verified-flag')).toHaveTextContent(
      'Not verified for tax purposes',
    )
  })

  it('says verified when the API says so', async () => {
    mockApi({ party: makeParty({ vat_verified: true }) })
    render(<PartyDetailPage />)
    await openTax()

    expect(await screen.findByTestId('party-verified-flag')).toHaveTextContent(
      'a reverse charge can be proposed',
    )
  })

  it('says not verified although a valid check is on the row', async () => {
    // The other direction: a `valid` check that has gone stale past
    // `max_check_age_days`. The row still says valid; the flag says no.
    mockApi({
      party: makeParty({
        vat_verified: false,
        tax_ids: [taxId({ latest_check: check({ status: 'valid' }) })],
      }),
    })
    render(<PartyDetailPage />)
    await openTax()

    expect(await screen.findByTestId('tax-id-status-valid-t1')).toBeInTheDocument()
    expect(screen.getByTestId('party-verified-flag')).toHaveTextContent(
      'Not verified',
    )
  })
})

describe('dates and scheduling', () => {
  it('shows when it was checked and when it is next due', async () => {
    mockApi()
    render(<PartyDetailPage />)
    await openTax()

    const row = await screen.findByTestId('tax-id-t1')
    expect(row).toHaveTextContent('Checked')
    expect(row).toHaveTextContent('next check')
  })

  it('says there is no register rather than leaving the date blank', async () => {
    mockApi({
      party: makeParty({
        tax_ids: [
          taxId({
            kind: 'uid_ch',
            value: 'CHE109322551',
            verifiable: false,
            next_check_at: null,
            latest_check: null,
          }),
        ],
      }),
    })
    render(<PartyDetailPage />)
    await openTax()

    const row = await screen.findByTestId('tax-id-t1')
    expect(row).toHaveTextContent('Never checked')
    expect(row).toHaveTextContent('no register to check it against')
  })

  it('marks a qualified consultation and shows its reference', async () => {
    mockApi({
      party: makeParty({
        tax_ids: [
          taxId({
            latest_check: check({
              qualified: true,
              consultation_ref: 'WAPIAAAA1',
            }),
          }),
        ],
      }),
    })
    render(<PartyDetailPage />)
    await openTax()

    const row = await screen.findByTestId('tax-id-t1')
    expect(row).toHaveTextContent('qualified consultation')
    expect(row).toHaveTextContent('Consultation reference WAPIAAAA1')
  })
})

describe('the actions', () => {
  it('checks now, and posts to the identifier’s own endpoint', async () => {
    mockApi()
    render(<PartyDetailPage />)
    await openTax()

    fireEvent.click(await screen.findByTestId('tax-id-check-t1'))

    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        '/parties/p1/tax-ids/t1/check',
        {},
      ),
    )
  })

  it('cannot check a kind with no register, and the button says why', async () => {
    // Rule 17c: disabled with the reason in its own label, and the manual
    // route offered beside it rather than left to be discovered.
    mockApi({
      party: makeParty({
        tax_ids: [taxId({ kind: 'ein', value: '123456789', verifiable: false })],
      }),
    })
    render(<PartyDetailPage />)
    await openTax()

    const button = await screen.findByTestId('tax-id-check-t1')
    expect(button).toBeDisabled()
    expect(button).toHaveTextContent('No register to check')
    expect(screen.getByTestId('tax-id-confirm-t1')).toBeEnabled()
  })

  it('offers no manual confirmation where a register exists', async () => {
    // Because for a `vat` it would not count anyway, and offering it would
    // invite somebody to use it instead of Check now.
    mockApi()
    render(<PartyDetailPage />)
    await openTax()

    await screen.findByTestId('tax-id-check-t1')
    expect(screen.queryByTestId('tax-id-confirm-t1')).not.toBeInTheDocument()
  })

  it('records a manual confirmation with its note and reference', async () => {
    mockApi({
      party: makeParty({
        tax_ids: [taxId({ kind: 'ein', value: '123456789', verifiable: false })],
      }),
    })
    render(<PartyDetailPage />)
    await openTax()

    fireEvent.click(await screen.findByTestId('tax-id-confirm-t1'))
    fireEvent.change(screen.getByLabelText('Confirmation note'), {
      target: { value: 'W-9 on file.' },
    })
    fireEvent.change(screen.getByLabelText('Evidence reference'), {
      target: { value: '2026/114' },
    })
    fireEvent.click(screen.getByTestId('tax-id-confirm-submit-t1'))

    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        '/parties/p1/tax-ids/t1/manual-confirmation',
        { note: 'W-9 on file.', evidence_reference: '2026/114' },
      ),
    )
  })

  it('will not submit a confirmation without a note, and says so', async () => {
    mockApi({
      party: makeParty({
        tax_ids: [taxId({ kind: 'ein', value: '123456789', verifiable: false })],
      }),
    })
    render(<PartyDetailPage />)
    await openTax()
    fireEvent.click(await screen.findByTestId('tax-id-confirm-t1'))

    const submit = screen.getByTestId('tax-id-confirm-submit-t1')
    expect(submit).toBeDisabled()
    expect(submit).toHaveTextContent('Say how you confirmed it')
  })

  it('retires an identifier and offers to use it again', async () => {
    mockApi()
    render(<PartyDetailPage />)
    await openTax()

    fireEvent.click(await screen.findByTestId('tax-id-retire-t1'))
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        '/parties/p1/tax-ids/t1/retire',
        {},
      ),
    )
  })

  it('shows a retired identifier greyed, with Use again', async () => {
    mockApi({
      party: makeParty({
        tax_ids: [taxId({ is_active: false, is_primary: false })],
      }),
    })
    render(<PartyDetailPage />)
    await openTax()

    expect(await screen.findByTestId('tax-id-retired-t1')).toHaveTextContent(
      'Retired',
    )
    expect(screen.getByTestId('tax-id-retire-t1')).toHaveTextContent('Use again')
  })

  it('shows the server’s sentence when a check is refused', async () => {
    mockApi()
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path.endsWith('/tax-preview')) return makePreview() as never
      throw new ApiError(503, 'The registry could not be reached.')
    })
    render(<PartyDetailPage />)
    await openTax()

    fireEvent.click(await screen.findByTestId('tax-id-check-t1'))

    expect(await screen.findByTestId('tax-id-error-t1')).toHaveTextContent(
      'could not be reached',
    )
  })
})

describe('the acknowledge flow', () => {
  const flagged = () =>
    makeParty({
      tax_ids: [
        taxId({
          needs_attention: true,
          needs_attention_reason:
            'Since the last check, the registry’s answer changed from “valid” to “invalid”. Acknowledge this with a note once you have looked at it.',
          latest_check: check({ status: 'invalid' }),
        }),
      ],
    })

  it('shows the reason the server wrote, verbatim', async () => {
    mockApi({ party: flagged() })
    render(<PartyDetailPage />)
    await openTax()

    expect(await screen.findByTestId('tax-id-attention-t1')).toHaveTextContent(
      'changed from “valid” to “invalid”',
    )
  })

  it('acknowledges with a note', async () => {
    mockApi({ party: flagged() })
    render(<PartyDetailPage />)
    await openTax()

    fireEvent.change(
      await screen.findByLabelText('Acknowledgement note'),
      { target: { value: 'They deregistered. Switching to domestic.' } },
    )
    fireEvent.click(screen.getByTestId('tax-id-acknowledge-t1'))

    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        '/parties/p1/tax-ids/t1/checks/c1/acknowledge',
        { note: 'They deregistered. Switching to domestic.' },
      ),
    )
  })

  it('will not acknowledge without a note, and the button says why', async () => {
    mockApi({ party: flagged() })
    render(<PartyDetailPage />)
    await openTax()

    const button = await screen.findByTestId('tax-id-acknowledge-t1')
    expect(button).toBeDisabled()
    expect(button).toHaveTextContent('Add a note first')
  })

  it('a reader sees the flag but not the acknowledge form', async () => {
    seedCompany(['parties.view'])
    mockApi({ party: flagged() })
    render(<PartyDetailPage />)
    await openTax()

    expect(await screen.findByTestId('tax-id-attention-t1')).toBeInTheDocument()
    expect(
      screen.queryByTestId('tax-id-acknowledge-t1'),
    ).not.toBeInTheDocument()
  })
})

describe('the history drawer', () => {
  it('lists every check with what came back', async () => {
    mockApi({
      history: [
        check({ id: 'c2', status: 'invalid', trigger_kind: 'scheduled' }),
        check({
          id: 'c1',
          status: 'valid',
          trigger_kind: 'initial',
          qualified: true,
          consultation_ref: 'WAPIAAAA1',
          registered_name: 'Berlin Produktion GmbH',
        }),
      ],
    })
    render(<PartyDetailPage />)
    await openTax()

    fireEvent.click(await screen.findByTestId('tax-id-history-t1'))

    const list = await screen.findByTestId('tax-id-history-list-t1')
    expect(list).toHaveTextContent('Not valid')
    expect(list).toHaveTextContent('scheduled')
    expect(list).toHaveTextContent('Confirmed')
    expect(list).toHaveTextContent('qualified')
    expect(list).toHaveTextContent('WAPIAAAA1')
    expect(list).toHaveTextContent('registry: Berlin Produktion GmbH')
  })

  it('says so when there is no history yet', async () => {
    mockApi({ history: [] })
    render(<PartyDetailPage />)
    await openTax()

    fireEvent.click(await screen.findByTestId('tax-id-history-t1'))

    expect(
      await screen.findByTestId('tax-id-history-empty-t1'),
    ).toHaveTextContent('Nothing has been checked or confirmed yet')
  })

  it('shows a manual row as confirmed by hand, not as a status', async () => {
    mockApi({
      history: [check({ provider: 'manual', status: 'valid' })],
    })
    render(<PartyDetailPage />)
    await openTax()
    fireEvent.click(await screen.findByTestId('tax-id-history-t1'))

    const list = await screen.findByTestId('tax-id-history-list-t1')
    expect(list).toHaveTextContent('confirmed by hand')
  })
})

describe('adding an identifier', () => {
  it('posts the kind, value and country', async () => {
    mockApi({ party: makeParty({ tax_ids: [] }) })
    render(<PartyDetailPage />)
    await openTax()

    fireEvent.click(await screen.findByTestId('tax-id-add'))
    fireEvent.change(screen.getByLabelText('Kind of identifier'), {
      target: { value: 'uid_ch' },
    })
    fireEvent.change(screen.getByLabelText('Identifier'), {
      target: { value: 'CHE-109.322.551' },
    })
    fireEvent.change(screen.getByLabelText('Identifier country'), {
      target: { value: 'ch' },
    })
    fireEvent.click(screen.getByTestId('tax-id-new-submit'))

    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/parties/p1/tax-ids', {
        kind: 'uid_ch',
        value: 'CHE-109.322.551',
        country: 'CH',
      }),
    )
  })

  it('shows the server’s refusal, which names the rule', async () => {
    mockApi({ party: makeParty({ tax_ids: [] }) })
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path.endsWith('/tax-preview')) return makePreview() as never
      throw new ApiError(
        422,
        "'CHE109322552' has the right shape but its check digit does not add up. The rule is the modulo-11 calculation in eCH-0097 section 2.4.2.",
      )
    })
    render(<PartyDetailPage />)
    await openTax()

    fireEvent.click(await screen.findByTestId('tax-id-add'))
    fireEvent.change(screen.getByLabelText('Identifier'), {
      target: { value: 'CHE109322552' },
    })
    fireEvent.click(screen.getByTestId('tax-id-new-submit'))

    expect(await screen.findByTestId('tax-id-new-error')).toHaveTextContent(
      'eCH-0097',
    )
  })

  it('says an empty list is not necessarily wrong', async () => {
    mockApi({ party: makeParty({ tax_ids: [] }) })
    render(<PartyDetailPage />)
    await openTax()

    expect(await screen.findByTestId('tax-ids-empty')).toHaveTextContent(
      'below the small-business threshold holds none',
    )
  })
})

describe('the Kleinunternehmer case', () => {
  it('explains the empty field rather than leaving it blank', async () => {
    mockApi({
      party: makeParty({
        type: 'person',
        name: 'Franz Klein e.U.',
        business_status: 'business',
        buyer_type: 'business',
        buyer_type_reason: 'explicit_without_tax_id',
        tax_status_note: 'Kleinunternehmer nach §6 Abs 1 Z 27 UStG, keine UID',
        tax_country: 'AT',
        vat_verified: false,
        tax_ids: [],
      }),
      resolved: makeResolved({
        vat_id: field(null, null),
        tax_id_kind: field(null, null),
        tax_status_note: field(
          'Kleinunternehmer nach §6 Abs 1 Z 27 UStG, keine UID',
          'person',
        ),
        buyer_type_reason: 'explicit_without_tax_id',
      }),
    })
    render(<PartyDetailPage />)
    await openTax()

    expect(screen.getByTestId('party-buyer-type')).toHaveTextContent(
      'set explicitly as a business, with no tax identifier on file',
    )
    expect(screen.getByLabelText('Tax status note')).toHaveValue(
      'Kleinunternehmer nach §6 Abs 1 Z 27 UStG, keine UID',
    )
    expect(screen.getByTestId('party-resolved-tax-id')).toHaveTextContent(
      'None anywhere in the hierarchy',
    )
  })
})

describe('a reader who cannot edit', () => {
  it('sees everything and can change nothing', async () => {
    seedCompany(['parties.view'])
    mockApi()
    render(<PartyDetailPage />)
    await openTax()

    expect(screen.getByTestId('tax-id-status-valid-t1')).toBeInTheDocument()
    expect(screen.queryByTestId('tax-id-add')).not.toBeInTheDocument()
    expect(screen.queryByTestId('tax-id-check-t1')).not.toBeInTheDocument()
    expect(screen.queryByTestId('tax-id-retire-t1')).not.toBeInTheDocument()
    // The history is readable: a reader answering a question about an
    // invoice needs it.
    expect(screen.getByTestId('tax-id-history-t1')).toBeEnabled()
  })
})
