import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

// The mocked `ApiError` carries `status` and `detail`, like the real one. A
// one-line `class extends Error {}` does not, and the screen shows
// `err.detail` — so a refusal built from that stub would arrive with `detail`
// undefined and the page would render its generic fallback instead of the
// server's sentence, which is exactly what several tests below assert.
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
import TaxPage from '../tax/page'
import { useCompanyStore } from '@/stores/company-store'
import type {
  NoTaxCategoryOption,
  TaxClass,
  TaxCode,
  TaxProposal,
  TaxRule,
} from '@/types'

/**
 * Settings → Company → Tax.
 *
 * **What these tests are for.** The rule this screen must not break is that
 * it decides no tax itself (CLAUDE.md rule 2): the treatment, the code, the
 * rate, the warnings and the explanation all come from
 * `POST /company/tax/propose`, which runs the same `propose_tax` that P2's
 * finalize will run. So the proposal the mock returns is deliberately one no
 * client-side evaluator could have produced from the options on screen — a
 * code that is in no list and a rate that matches no class. If the screen
 * ever starts working any of it out locally, it stops matching and these
 * fail.
 *
 * **Readiness gates on fetched values, never on the chrome** (the CI-flake
 * instance of the harness rule). The tab strip and the headings paint on
 * first render, so waiting for them and then reaching for a row is a race the
 * fast machine wins. Every gate below is a `findBy*` on something that exists
 * only after a request has resolved.
 */

// ─── Fixtures: the real API shapes ─────────────────────────────────────────

function makeCode(overrides: Partial<TaxCode> = {}): TaxCode {
  return {
    id: 'code-20',
    code: 'AT-20',
    name: '20 % USt (Normalsteuersatz)',
    rate: '20.0000',
    category: 'S',
    legal_notes: {},
    notes_reviewed: false,
    reporting_mapping: {},
    revenue_account: null,
    is_active: true,
    origin_pack: 'at',
    created_at: '2026-10-22T00:00:00Z',
    updated_at: '2026-10-22T00:00:00Z',
    // Empty on every read; the server fills it after a write (P1a-2).
    notices: [],
    ...overrides,
  }
}

const REVERSE_CHARGE = makeCode({
  id: 'code-ae',
  code: 'EU-AE',
  name: 'Reverse charge',
  rate: '0.0000',
  category: 'AE',
  legal_notes: {
    en: 'Reverse charge: VAT to be accounted for by the recipient.',
    de: 'Steuerschuld geht auf den Leistungsempfänger über.',
  },
  origin_pack: 'eu_generic',
})

/** The generic pack's unset standard rate: 0 %, inactive, category S. */
const UNSET_STANDARD = makeCode({
  id: 'code-std',
  code: 'EU-STD',
  name: 'Standard rate (set your rate)',
  rate: '0.0000',
  category: 'S',
  is_active: false,
  origin_pack: 'eu_generic',
})

const CLASSES: TaxClass[] = [
  {
    id: 'class-standard',
    key: 'standard',
    label: 'Normalsteuersatz (20 %)',
    code_id: 'code-20',
    code: 'AT-20',
    rate: '20.0000',
    category: 'S',
    code_is_active: true,
    is_active: true,
    position: 10,
    origin_pack: 'at',
    is_custom: false,
  },
  {
    id: 'class-reduced',
    key: 'reduced',
    label: 'Ermäßigt (10 %)',
    code_id: 'code-10',
    code: 'AT-10',
    rate: '10.0000',
    category: 'S',
    code_is_active: true,
    is_active: true,
    position: 20,
    origin_pack: 'at',
    is_custom: false,
  },
]

const RULES: TaxRule[] = [
  {
    id: 'rule-30',
    position: 30,
    name: 'EU business, VAT ID verified, services: reverse charge',
    conditions: { buyer_region: 'eu_other' },
    outcome: { treatment: 'eu_reverse_charge', code: { code: 'EU-AE' } },
    origin_pack: 'eu_generic',
    is_active: true,
  },
]

/** A code and a rate the screen could not have worked out from anything on it. */
const SERVER_ONLY_CODE = 'SERVER-SAYS-XX'
const SERVER_ONLY_RATE = '13.7500'

function makeProposal(overrides: Partial<TaxProposal> = {}): TaxProposal {
  return {
    seller_country: 'AT',
    treatment: 'eu_reverse_charge',
    treatment_label: 'Reverse charge (EU)',
    code: SERVER_ONLY_CODE,
    rate: SERVER_ONLY_RATE,
    legal_note: 'Reverse charge: VAT to be accounted for by the recipient.',
    applied_rule: 'EU business, VAT ID verified, services: reverse charge',
    applied_rule_position: 30,
    reasons: [
      'The seller is in AT.',
      'The buyer is a business in DE, another EU country.',
      'Rule 30 “EU business, VAT ID verified, services: reverse charge” applies.',
    ],
    warnings: [],
    blocking: false,
    would_have_applied: null,
    ...overrides,
  }
}

/** The six kinds, as `GET /company/tax/no-tax-categories` serves them. */
const CATEGORIES: NoTaxCategoryOption[] = [
  {
    category: 'Z',
    labels: {
      en: 'Zero-rated — taxable, but the rate is 0 %.',
      de: 'Nullsatz — steuerbar, aber mit 0 % besteuert.',
    },
  },
  {
    category: 'E',
    labels: {
      en: 'Exempt — outside the tax for a reason in the law, with no input-tax deduction.',
      de: 'Steuerfrei — gesetzlich befreit, ohne Vorsteuerabzug.',
    },
  },
  {
    category: 'AE',
    labels: {
      en: 'Reverse charge — the recipient accounts for the tax.',
      de: 'Reverse Charge — die Steuerschuld geht auf den Empfänger über.',
    },
  },
  {
    category: 'K',
    labels: {
      en: 'Intra-community supply — goods to a VAT-registered buyer in another EU country.',
      de: 'Innergemeinschaftliche Lieferung.',
    },
  },
  {
    category: 'G',
    labels: {
      en: 'Export — goods leaving the EU.',
      de: 'Ausfuhr — Waren verlassen die EU.',
    },
  },
  {
    category: 'O',
    labels: {
      en: 'Not subject to tax — the place of supply is outside this country.',
      de: 'Nicht steuerbar.',
    },
  },
]

/** A rate the company added itself, with its generated class key. */
const CUSTOM_CODE = makeCode({
  id: 'code-45',
  code: 'AT-4.5',
  name: 'Super-reduced',
  rate: '4.5000',
  category: 'S',
  origin_pack: null,
})

const CUSTOM_CLASS: TaxClass = {
  id: 'class-custom',
  key: 'custom_1',
  label: 'Super-reduced',
  code_id: 'code-45',
  code: 'AT-4.5',
  rate: '4.5000',
  category: 'S',
  code_is_active: true,
  is_active: true,
  position: 50,
  origin_pack: null,
  is_custom: true,
}

/** What `GET /parties/{id}/resolved` says — values the screen could not have
 *  worked out from the party row it never sees. */
const RESOLVED_FROM_PARTY = {
  own_level: 'department',
  ids: { department: 'p-dept', organisation: 'p-org' },
  names: { department: 'Einkauf', organisation: 'Berlin GmbH' },
  contact_name: { value: 'Einkauf', source: 'department' },
  invoice_name: { value: 'Berlin GmbH', source: 'organisation' },
  billing_address: { value: null, source: null },
  vat_id: { value: 'DE123456789', source: 'organisation' },
  vat_verified: { value: true, source: 'organisation' },
  tax_country: { value: 'DE', source: 'organisation' },
  business_status: { value: 'auto', source: 'department' },
  tax_override: { value: null, source: null },
  language: { value: null, source: null },
  default_currency: { value: null, source: null },
  payment_terms_days: { value: null, source: null },
  payment_terms_text: { value: null, source: null },
  po_reference: { value: null, source: null },
  email: { value: null, source: null },
  phone: { value: null, source: null },
  einvoice_routing: { value: null, source: null },
  buyer_type: 'business',
  buyer_type_reason: 'organisation',
}

function seedCompany(permissions = ['company.view', 'company.settings.edit']) {
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
  codes = [makeCode(), REVERSE_CHARGE],
  classes = CLASSES,
  rules = RULES,
  categories = CATEGORIES,
  proposal = makeProposal(),
  parties = [],
  resolvedRecipient = RESOLVED_FROM_PARTY,
}: {
  codes?: TaxCode[]
  classes?: TaxClass[]
  rules?: TaxRule[]
  categories?: NoTaxCategoryOption[]
  proposal?: TaxProposal
  parties?: { id: string; name: string; type: string }[]
  resolvedRecipient?: unknown
} = {}) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/company/tax/codes') return codes as never
    if (path === '/company/tax/classes') return classes as never
    if (path === '/company/tax/rules') return rules as never
    if (path === '/company/tax/no-tax-categories') return categories as never
    if (path.startsWith('/parties?')) return { parties, total: parties.length, limit: 200, offset: 0 } as never
    if (path.startsWith('/parties/')) return resolvedRecipient as never
    throw new Error(`unexpected GET ${path}`)
  })
  vi.mocked(api.post).mockImplementation(async (path: string) => {
    if (path === '/company/tax/propose') return proposal as never
    return makeCode() as never
  })
  vi.mocked(api.patch).mockImplementation(async () => makeCode() as never)
}

async function openTestTab() {
  fireEvent.click(screen.getByRole('tab', { name: 'Test a case' }))
  // Gate on the proposal, not on the tab: the tab's own label is chrome and
  // paints immediately.
  return screen.findByTestId('tax-proposal')
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany()
})

// ─── Codes: two sections ────────────────────────────────────────────────────

describe('the Codes tab separates rates from no-tax cases', () => {
  it('lists each rate from its class, with the code and rate the API sent', async () => {
    mockApi({ codes: [makeCode(), REVERSE_CHARGE, CUSTOM_CODE], classes: [...CLASSES, CUSTOM_CLASS] })
    render(<TaxPage />)

    // Gates on a fetched value — a rate string, which no chrome contains.
    expect(await screen.findByText('20.0000 %')).toBeInTheDocument()
    expect(screen.getByText('AT-20')).toBeInTheDocument()
    expect(screen.getByText('4.5000 %')).toBeInTheDocument()
    // A rate the company added says so, because "FilmBill put this here" and
    // "we added this" are different things to somebody reviewing a tax table.
    expect(screen.getByText('added here')).toBeInTheDocument()
  })

  it('puts a code no class points at in the no-tax section, with its kind', async () => {
    mockApi()
    render(<TaxPage />)

    await screen.findByText('20.0000 %')
    // `EU-AE` has no class, so it is a no-tax case — and its kind is the
    // sentence the API served, not a code.
    expect(screen.getByText('EU-AE')).toBeInTheDocument()
    expect(
      screen.getByText('Reverse charge — the recipient accounts for the tax.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Note not reviewed')).toBeInTheDocument()
  })

  it('shows a reviewed note as reviewed', async () => {
    mockApi({ codes: [makeCode(), { ...REVERSE_CHARGE, notes_reviewed: true }] })
    render(<TaxPage />)

    expect(await screen.findByText('Note reviewed')).toBeInTheDocument()
    expect(screen.queryByText('Note not reviewed')).not.toBeInTheDocument()
  })

  it('says “set your rate” on an unset rate, whatever its code is called', async () => {
    // The generalised condition: category S, 0 %, inactive. The generic pack
    // now ships two of these, and the hint must not depend on the name.
    mockApi({
      codes: [UNSET_STANDARD, { ...UNSET_STANDARD, id: 'code-red', code: 'EU-RED', name: 'Reduced rate (set your rate)' }],
      classes: [
        { ...CLASSES[0], code_id: 'code-std', code: 'EU-STD', rate: '0.0000', code_is_active: false },
        { ...CLASSES[1], code_id: 'code-red', code: 'EU-RED', rate: '0.0000', code_is_active: false },
      ],
    })
    render(<TaxPage />)

    for (const code of ['EU-STD', 'EU-RED']) {
      const hint = await screen.findByTestId(`tax-code-needs-rate-${code}`)
      expect(hint).toHaveTextContent('Set your rate')
      expect(hint).toHaveTextContent('nothing can be proposed')
    }
  })

  it('does not show that hint on a rate that has a number in it', async () => {
    // Austria's 4.9 % foodstuffs rate is inactive WITH a real rate, so the
    // hint would send somebody looking for a field already filled in.
    mockApi({
      codes: [{ ...CUSTOM_CODE, is_active: false }],
      classes: [{ ...CUSTOM_CLASS, is_active: false, code_is_active: false }],
    })
    render(<TaxPage />)

    await screen.findByText('4.5000 %')
    expect(
      screen.queryByTestId('tax-code-needs-rate-AT-4.5'),
    ).not.toBeInTheDocument()
  })

  it('says what to do when the company has no tax codes at all', async () => {
    mockApi({ codes: [], classes: [] })
    render(<TaxPage />)

    const empty = await screen.findByTestId('tax-codes-empty')
    expect(empty).toHaveTextContent('it has no country')
    expect(empty).toHaveTextContent('Company')
  })
})

describe('adding a rate', () => {
  it('offers the button to an editor and posts what was typed', async () => {
    mockApi()
    render(<TaxPage />)

    fireEvent.click(await screen.findByTestId('tax-add-rate'))
    fireEvent.change(screen.getByLabelText(/^Name/), {
      target: { value: 'Super-reduced' },
    })
    fireEvent.change(screen.getByLabelText(/Rate \(%\)/), {
      target: { value: '4.5' },
    })
    fireEvent.click(screen.getByTestId('tax-add-rate-submit'))

    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/company/tax/rates', {
        label: 'Super-reduced',
        rate: '4.5',
        // Empty means "you name it": the server builds it from the country
        // and the rate.
        code: null,
      }),
    )
  })

  it('sends a code the user typed, upper-cased', async () => {
    mockApi()
    render(<TaxPage />)

    fireEvent.click(await screen.findByTestId('tax-add-rate'))
    fireEvent.change(screen.getByLabelText(/^Name/), {
      target: { value: 'House' },
    })
    fireEvent.change(screen.getByLabelText(/Rate \(%\)/), {
      target: { value: '7' },
    })
    fireEvent.change(screen.getByLabelText(/Code \(optional\)/), {
      target: { value: 'haus-7' },
    })
    fireEvent.click(screen.getByTestId('tax-add-rate-submit'))

    await waitFor(() =>
      expect(vi.mocked(api.post).mock.calls.at(-1)?.[1]).toMatchObject({
        code: 'HAUS-7',
      }),
    )
  })

  it('cannot be submitted without a name and a rate', async () => {
    mockApi()
    render(<TaxPage />)

    fireEvent.click(await screen.findByTestId('tax-add-rate'))
    // Rule 17c: disabled, and the two fields it is waiting for are the two
    // visibly empty ones rather than a hidden condition.
    expect(screen.getByTestId('tax-add-rate-submit')).toBeDisabled()

    fireEvent.change(screen.getByLabelText(/^Name/), {
      target: { value: 'Only a name' },
    })
    expect(screen.getByTestId('tax-add-rate-submit')).toBeDisabled()

    fireEvent.change(screen.getByLabelText(/Rate \(%\)/), {
      target: { value: '4.5' },
    })
    expect(screen.getByTestId('tax-add-rate-submit')).toBeEnabled()
  })

  it('shows the server’s own sentence when the rate is refused', async () => {
    mockApi()
    vi.mocked(api.post).mockRejectedValue(
      new ApiError(
        422,
        'A rate of 140 % is not usable. Give a percentage above 0 and below 100 — 20 means 20 %.',
      ),
    )
    render(<TaxPage />)

    fireEvent.click(await screen.findByTestId('tax-add-rate'))
    fireEvent.change(screen.getByLabelText(/^Name/), {
      target: { value: 'Nonsense' },
    })
    fireEvent.change(screen.getByLabelText(/Rate \(%\)/), {
      target: { value: '140' },
    })
    fireEvent.click(screen.getByTestId('tax-add-rate-submit'))

    expect(await screen.findByTestId('tax-add-rate-error')).toHaveTextContent(
      'above 0 and below 100',
    )
  })
})

describe('adding a no-tax case', () => {
  it('offers the six kinds the API served, with their own sentences', async () => {
    mockApi()
    render(<TaxPage />)

    fireEvent.click(await screen.findByTestId('tax-add-no-tax-case'))
    const kinds = screen.getByLabelText('Kind')
    // The list comes from the API, so the dialog cannot offer a seventh.
    expect(kinds).toHaveTextContent('Zero-rated')
    expect(kinds).toHaveTextContent('Exempt')
    expect(kinds).toHaveTextContent('Reverse charge')
    expect(kinds).not.toHaveTextContent('Standard')
  })

  it('shows the chosen kind’s explanation in both languages', async () => {
    mockApi()
    render(<TaxPage />)

    fireEvent.click(await screen.findByTestId('tax-add-no-tax-case'))
    fireEvent.change(screen.getByLabelText('Kind'), { target: { value: 'E' } })

    const help = await screen.findByTestId('tax-no-tax-kind-help')
    expect(help).toHaveTextContent('outside the tax for a reason in the law')
    expect(help).toHaveTextContent('gesetzlich befreit')
  })

  it('says the wording is the user’s responsibility', async () => {
    mockApi()
    render(<TaxPage />)

    fireEvent.click(await screen.findByTestId('tax-add-no-tax-case'))

    const warning = await screen.findByTestId('tax-no-tax-responsibility')
    expect(warning).toHaveTextContent('yours to be right about')
    expect(warning).toHaveTextContent('tax advisor')
  })

  it('posts the kind, the name and the notes per language', async () => {
    mockApi()
    render(<TaxPage />)

    fireEvent.click(await screen.findByTestId('tax-add-no-tax-case'))
    fireEvent.change(screen.getByLabelText('Kind'), { target: { value: 'E' } })
    fireEvent.change(screen.getByLabelText(/^Name/), {
      target: { value: 'Exempt – training' },
    })
    fireEvent.change(screen.getByLabelText('DE'), {
      target: { value: 'Steuerfrei: Bildungsleistung.' },
    })
    fireEvent.click(screen.getByTestId('tax-add-no-tax-submit'))

    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        '/company/tax/no-tax-cases',
        {
          label: 'Exempt – training',
          category: 'E',
          code: null,
          legal_notes: { de: 'Steuerfrei: Bildungsleistung.' },
        },
      ),
    )
  })

  it('cannot be submitted without a kind', async () => {
    mockApi()
    render(<TaxPage />)

    fireEvent.click(await screen.findByTestId('tax-add-no-tax-case'))
    fireEvent.change(screen.getByLabelText(/^Name/), {
      target: { value: 'Nameless kind' },
    })
    expect(screen.getByTestId('tax-add-no-tax-submit')).toBeDisabled()

    fireEvent.change(screen.getByLabelText('Kind'), { target: { value: 'G' } })
    expect(screen.getByTestId('tax-add-no-tax-submit')).toBeEnabled()
  })
})

describe('editing and retiring from the row', () => {
  it('renames through the class and changes the rate through the code', async () => {
    mockApi({ codes: [CUSTOM_CODE], classes: [CUSTOM_CLASS] })
    render(<TaxPage />)

    await screen.findByText('4.5000 %')
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    fireEvent.change(screen.getByLabelText(/^Name/), {
      target: { value: 'Super-reduced (checked)' },
    })
    fireEvent.change(screen.getByLabelText(/Rate \(%\)/), {
      target: { value: '4.9' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(api.patch).toHaveBeenCalledTimes(2))
    const calls = vi.mocked(api.patch).mock.calls
    // The label belongs to the class, the percentage to the code. The class
    // goes first, so a refused rate does not lose the rename too.
    expect(calls[0]).toEqual([
      '/company/tax/classes/class-custom',
      { label: 'Super-reduced (checked)' },
    ])
    expect(calls[1]).toEqual(['/company/tax/codes/code-45', { rate: '4.9' }])
  })

  it('shows the server’s notice about what a rate change means', async () => {
    const notice =
      'This rate applies to documents created from now on. Documents that have already been issued keep the rate they were issued with.'
    mockApi({ codes: [CUSTOM_CODE], classes: [CUSTOM_CLASS] })
    vi.mocked(api.patch).mockResolvedValue({
      ...CUSTOM_CODE,
      rate: '4.9000',
      notices: [notice],
    } as never)
    render(<TaxPage />)

    await screen.findByText('4.5000 %')
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    fireEvent.change(screen.getByLabelText(/Rate \(%\)/), {
      target: { value: '4.9' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    // Verbatim, from the response — the screen does not own this wording.
    expect(
      await screen.findByTestId('tax-rate-notice-AT-4.5'),
    ).toHaveTextContent('already been issued')
  })

  it('retires a rate through the class endpoint', async () => {
    mockApi({ codes: [CUSTOM_CODE], classes: [CUSTOM_CLASS] })
    render(<TaxPage />)

    await screen.findByText('4.5000 %')
    fireEvent.click(screen.getByTestId('tax-rate-toggle-AT-4.5'))

    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith(
        '/company/tax/classes/class-custom',
        { is_active: false },
      ),
    )
  })

  it('shows the 409 sentence naming the referrer when a retire is refused', async () => {
    mockApi()
    vi.mocked(api.patch).mockRejectedValue(
      new ApiError(
        409,
        'EU-AE cannot be deactivated: it is used by rule “EU business, VAT ID verified, services: reverse charge”. Change or deactivate that first.',
      ),
    )
    render(<TaxPage />)

    await screen.findByText('EU-AE')
    fireEvent.click(screen.getByTestId('tax-case-toggle-EU-AE'))

    // The referrer's NAME, not a generic failure — the whole point of the
    // 409 is that it says where to go.
    const error = await screen.findByTestId('tax-case-error-EU-AE')
    expect(error).toHaveTextContent('used by rule')
    expect(error).toHaveTextContent('reverse charge')
  })

  it('reactivates an inactive case', async () => {
    mockApi({
      codes: [makeCode(), { ...REVERSE_CHARGE, is_active: false }],
    })
    render(<TaxPage />)

    await screen.findByText('EU-AE')
    fireEvent.click(screen.getByTestId('tax-case-toggle-EU-AE'))

    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith('/company/tax/codes/code-ae', {
        is_active: true,
      }),
    )
  })
})

describe('a member who may read but not edit', () => {
  it('sees the rows and no way to change anything, and is told why', async () => {
    seedCompany(['company.view'])
    mockApi({ codes: [makeCode(), REVERSE_CHARGE, CUSTOM_CODE], classes: [...CLASSES, CUSTOM_CLASS] })
    render(<TaxPage />)

    expect(await screen.findByText('20.0000 %')).toBeInTheDocument()
    expect(
      screen.getByText(/read these but not change them/),
    ).toBeInTheDocument()

    // No plus buttons, no Edit, no Deactivate — and rule 17c: absent rather
    // than present and dead, with a sentence saying why.
    expect(screen.queryByTestId('tax-add-rate')).not.toBeInTheDocument()
    expect(screen.queryByTestId('tax-add-no-tax-case')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Edit' }),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /Deactivate|Activate/ }),
    ).not.toBeInTheDocument()
  })
})

// ─── Test a case ────────────────────────────────────────────────────────────

describe('the Test a case tab asks the server for everything', () => {
  it('shows the code, rate, treatment and explanation the API returned', async () => {
    mockApi()
    render(<TaxPage />)
    await openTestTab()

    // A code and a rate no local evaluator could have produced from the
    // options on screen. If the screen ever computes either, this fails.
    expect(await screen.findByText(SERVER_ONLY_CODE)).toBeInTheDocument()
    expect(screen.getByText(`${SERVER_ONLY_RATE} %`)).toBeInTheDocument()
    expect(screen.getByText('Reverse charge (EU)')).toBeInTheDocument()
    expect(screen.getByText('The seller is in AT.')).toBeInTheDocument()
    expect(
      screen.getByText(/Decided by rule/),
    ).toBeInTheDocument()
  })

  it('shows the seller country read-only, from the proposal', async () => {
    mockApi()
    render(<TaxPage />)
    await openTestTab()

    const seller = screen.getByLabelText(/Seller country/)
    expect(seller).toHaveValue('AT')
    expect(seller).toBeDisabled()
  })

  it('posts the case to the propose endpoint rather than evaluating it', async () => {
    mockApi()
    render(<TaxPage />)
    await openTestTab()

    fireEvent.change(screen.getByLabelText(/Buyer country/), {
      target: { value: 'de' },
    })
    fireEvent.click(screen.getByLabelText(/VAT ID verified/))

    await waitFor(() => {
      const last = vi.mocked(api.post).mock.calls.at(-1)
      expect(last?.[0]).toBe('/company/tax/propose')
      expect(last?.[1]).toMatchObject({
        buyer_country: 'DE',
        buyer_vat_verified: true,
        buyer_type: 'business',
        supply_kind: 'services',
        item_class: 'standard',
      })
    })
  })

  it('offers each tax class with its code and rate, from the API', async () => {
    mockApi()
    render(<TaxPage />)
    await openTestTab()

    const select = screen.getByLabelText('Item tax class')
    expect(select).toHaveTextContent('Normalsteuersatz (20 %) — AT-20, 20.0000 %')
    expect(select).toHaveTextContent('Ermäßigt (10 %) — AT-10, 10.0000 %')
  })

  it('renders each warning with the server’s sentence, not a code', async () => {
    mockApi({
      proposal: makeProposal({
        warnings: [
          {
            code: 'buyer_vat_id_not_verified',
            text: 'The buyer’s VAT ID has not been verified, so a reverse charge is not proposed. Verify it to propose a reverse charge.',
          },
        ],
      }),
    })
    render(<TaxPage />)
    await openTestTab()

    const warning = await screen.findByTestId(
      'tax-warning-buyer_vat_id_not_verified',
    )
    expect(warning).toHaveTextContent('has not been verified')
    expect(warning).toHaveTextContent('Verify it')
    // The code is for branching, never for display.
    expect(warning).not.toHaveTextContent('buyer_vat_id_not_verified')
  })

  it('marks an unreviewed legal note as not reviewed', async () => {
    mockApi({
      proposal: makeProposal({
        warnings: [
          {
            code: 'legal_note_not_reviewed',
            text: 'The legal note on this code is the generic wording FilmBill ships and has not been reviewed by anybody in this company.',
          },
        ],
      }),
    })
    render(<TaxPage />)
    await openTestTab()

    await screen.findByText(SERVER_ONLY_CODE)
    expect(screen.getByText('Not reviewed')).toBeInTheDocument()
  })

  it('shows what the rules would have done when an override changed the answer', async () => {
    mockApi({
      proposal: makeProposal({
        treatment: 'domestic',
        treatment_label: 'Domestic',
        code: 'AT-20',
        rate: '20.0000',
        applied_rule: 'override:item',
        applied_rule_position: null,
        would_have_applied: {
          treatment: 'eu_reverse_charge',
          treatment_label: 'Reverse charge (EU)',
          code: 'EU-AE',
          rate: '0.0000',
          applied_rule:
            'EU business, VAT ID verified, services: reverse charge',
          applied_rule_position: 30,
          legal_note: 'Reverse charge: VAT to be accounted for by the recipient.',
        },
      }),
    })
    render(<TaxPage />)
    await openTestTab()

    const panel = await screen.findByTestId('tax-would-have-applied')
    expect(panel).toHaveTextContent('EU-AE')
    expect(panel).toHaveTextContent('0.0000 %')
    expect(panel).toHaveTextContent('Reverse charge (EU)')
    expect(panel).toHaveTextContent('Rule 30')
    expect(screen.getByText(/Decided by the/)).toHaveTextContent('item')
  })

  it('shows nothing of the kind when no override was in play', async () => {
    mockApi()
    render(<TaxPage />)
    await openTestTab()

    await screen.findByText(SERVER_ONLY_CODE)
    expect(
      screen.queryByTestId('tax-would-have-applied'),
    ).not.toBeInTheDocument()
  })

  it('sends the override the user picked, with its code', async () => {
    mockApi()
    render(<TaxPage />)
    await openTestTab()

    fireEvent.change(screen.getByLabelText('Item override'), {
      target: { value: 'code' },
    })
    fireEvent.change(await screen.findByLabelText('Item override — tax code'), {
      target: { value: 'EU-AE' },
    })

    await waitFor(() => {
      expect(vi.mocked(api.post).mock.calls.at(-1)?.[1]).toMatchObject({
        item_override: { kind: 'code', code: 'EU-AE' },
      })
    })
  })

  it('sends no code for the domestic-for-foreign-business override', async () => {
    mockApi()
    render(<TaxPage />)
    await openTestTab()

    fireEvent.change(screen.getByLabelText('Party override'), {
      target: { value: 'domestic_for_foreign_business' },
    })

    await waitFor(() => {
      expect(vi.mocked(api.post).mock.calls.at(-1)?.[1]).toMatchObject({
        party_override: { kind: 'domestic_for_foreign_business' },
      })
    })
    expect(
      screen.queryByLabelText('Party override — tax code'),
    ).not.toBeInTheDocument()
  })

  it('explains that the VAT-ID toggle is meaningless for a private buyer', async () => {
    mockApi()
    render(<TaxPage />)
    await openTestTab()

    fireEvent.change(screen.getByLabelText('Buyer'), {
      target: { value: 'private' },
    })

    // Rule 17c: the control is disabled and the screen says why.
    const toggle = screen.getByLabelText(/VAT ID verified/)
    expect(toggle).toBeDisabled()
    expect(
      screen.getByText('A private buyer has no VAT ID to verify.'),
    ).toBeInTheDocument()
  })

  it('says plainly that nothing can be proposed, and shows the reason, when blocked', async () => {
    mockApi({
      proposal: makeProposal({
        seller_country: null,
        treatment: null,
        treatment_label: null,
        code: null,
        rate: null,
        legal_note: null,
        applied_rule: null,
        applied_rule_position: null,
        blocking: true,
        reasons: ['This company has no country, so no rule could be evaluated.'],
        warnings: [
          {
            code: 'home_country_missing',
            text: 'This company has no country, so no tax rule can be evaluated. Set the country in Settings → Company.',
          },
        ],
      }),
    })
    render(<TaxPage />)
    await openTestTab()

    expect(
      await screen.findByTestId('tax-proposal-blocked'),
    ).toHaveTextContent('Nothing can be proposed')
    expect(
      screen.getByTestId('tax-warning-home_country_missing'),
    ).toHaveTextContent('Set the country in Settings → Company')
    // No invented rate anywhere on the panel. Asserted on the panel's whole
    // text rather than on a query, because "a rate appeared" is a statement
    // about the panel and not about one element.
    expect(screen.getByTestId('tax-proposal').textContent).not.toMatch(/\d\s*%/)
  })

  it('shows the server’s message when the proposal request fails', async () => {
    mockApi()
    vi.mocked(api.post).mockRejectedValue(
      new ApiError(422, 'item_override.kind: unexpected value'),
    )
    render(<TaxPage />)
    fireEvent.click(screen.getByRole('tab', { name: 'Test a case' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'unexpected value',
    )
  })
})

// ─── Test a case: the selectors follow what is usable ───────────────────────

describe('the Test-a-case selectors offer only what can be proposed', () => {
  it('lists a class the company added, alongside the pack’s own', async () => {
    mockApi({
      codes: [makeCode(), REVERSE_CHARGE, CUSTOM_CODE],
      classes: [...CLASSES, CUSTOM_CLASS],
    })
    render(<TaxPage />)
    await openTestTab()

    const select = screen.getByLabelText('Item tax class')
    expect(select).toHaveTextContent('Normalsteuersatz (20 %) — AT-20, 20.0000 %')
    expect(select).toHaveTextContent('Super-reduced — AT-4.5, 4.5000 %')
  })

  it('omits a retired rate, because its only possible answer is “blocked”', async () => {
    mockApi({
      codes: [makeCode(), { ...CUSTOM_CODE, is_active: false }],
      classes: [
        CLASSES[0],
        { ...CUSTOM_CLASS, is_active: false, code_is_active: false },
      ],
    })
    render(<TaxPage />)
    await openTestTab()

    const select = screen.getByLabelText('Item tax class')
    expect(select).toHaveTextContent('Normalsteuersatz')
    expect(select).not.toHaveTextContent('Super-reduced')
  })

  it('offers an active custom no-tax case as an override', async () => {
    const exempt = makeCode({
      id: 'code-exempt',
      code: 'AT-E',
      name: 'Exempt – training',
      rate: '0.0000',
      category: 'E',
      origin_pack: null,
    })
    mockApi({ codes: [makeCode(), exempt] })
    render(<TaxPage />)
    await openTestTab()

    fireEvent.change(screen.getByLabelText('Item override'), {
      target: { value: 'code' },
    })
    const picker = await screen.findByLabelText('Item override — tax code')
    expect(picker).toHaveTextContent('AT-E — Exempt – training')
  })

  it('omits a retired no-tax case from the override picker', async () => {
    const exempt = makeCode({
      id: 'code-exempt',
      code: 'AT-E',
      name: 'Exempt – training',
      rate: '0.0000',
      category: 'E',
      is_active: false,
      origin_pack: null,
    })
    mockApi({ codes: [makeCode(), exempt] })
    render(<TaxPage />)
    await openTestTab()

    fireEvent.change(screen.getByLabelText('Party override'), {
      target: { value: 'code' },
    })
    const picker = await screen.findByLabelText('Party override — tax code')
    expect(picker).toHaveTextContent('AT-20')
    expect(picker).not.toHaveTextContent('AT-E')
  })
})

// ─── Taking the buyer from a party (P1b) ───────────────────────────────────

describe('taking the buyer from a party', () => {
  const PARTIES = [
    { id: 'p-dept', name: 'Einkauf', type: 'department' },
    { id: 'p-org', name: 'Berlin GmbH', type: 'organisation' },
  ]

  it('is not offered when there are no parties, so the fields stay typeable', async () => {
    mockApi({ parties: [] })
    render(<TaxPage />)
    await openTestTab()

    expect(
      screen.queryByLabelText('Take buyer from a party'),
    ).not.toBeInTheDocument()
    expect(screen.getByLabelText(/Buyer country/)).toBeEnabled()
  })

  it('fills the buyer fields from the API’s RESOLVED recipient', async () => {
    mockApi({ parties: PARTIES })
    render(<TaxPage />)
    await openTestTab()

    fireEvent.change(
      await screen.findByLabelText('Take buyer from a party'),
      { target: { value: 'p-dept' } },
    )

    // `DE` and `verified` come from the organisation two levels up. The
    // screen sees no party row at all — only the resolver's answer — so
    // these values are ones it could not have worked out.
    await waitFor(() =>
      expect(screen.getByLabelText('Buyer country')).toHaveValue('DE'),
    )
    await waitFor(() =>
      expect(vi.mocked(api.post).mock.calls.at(-1)?.[1]).toMatchObject({
        buyer_country: 'DE',
        buyer_type: 'business',
        buyer_vat_verified: true,
      }),
    )
  })

  it('shows where each value came from', async () => {
    mockApi({ parties: PARTIES })
    render(<TaxPage />)
    await openTestTab()
    fireEvent.change(
      await screen.findByLabelText('Take buyer from a party'),
      { target: { value: 'p-dept' } },
    )

    const note = await screen.findByTestId('tax-buyer-from-party')
    expect(note).toHaveTextContent('Berlin GmbH')
    expect(note).toHaveTextContent('country DE (from organisation)')
    expect(note).toHaveTextContent('VAT ID DE123456789 (from organisation)')
    expect(note).toHaveTextContent('verified')
    expect(note).toHaveTextContent('treated as business')
  })

  it('makes the buyer fields read-only while a party is chosen', async () => {
    mockApi({ parties: PARTIES })
    render(<TaxPage />)
    await openTestTab()
    fireEvent.change(
      await screen.findByLabelText('Take buyer from a party'),
      { target: { value: 'p-dept' } },
    )

    await waitFor(() =>
      expect(screen.getByLabelText('Buyer country')).toBeDisabled(),
    )
    expect(screen.getByLabelText('Buyer')).toBeDisabled()
    expect(screen.getByLabelText(/VAT ID verified/)).toBeDisabled()
  })

  it('hands the fields back when the party is deselected', async () => {
    mockApi({ parties: PARTIES })
    render(<TaxPage />)
    await openTestTab()
    const selector = await screen.findByLabelText('Take buyer from a party')
    fireEvent.change(selector, { target: { value: 'p-dept' } })
    await waitFor(() =>
      expect(screen.getByLabelText('Buyer country')).toBeDisabled(),
    )

    fireEvent.change(selector, { target: { value: '' } })

    await waitFor(() =>
      expect(screen.getByLabelText('Buyer country')).toBeEnabled(),
    )
    expect(
      screen.queryByTestId('tax-buyer-from-party'),
    ).not.toBeInTheDocument()
  })

  it('shows the result panel unchanged', async () => {
    mockApi({ parties: PARTIES })
    render(<TaxPage />)
    await openTestTab()
    fireEvent.change(
      await screen.findByLabelText('Take buyer from a party'),
      { target: { value: 'p-dept' } },
    )

    // Still the server's code and rate, still from `POST .../propose`.
    expect(await screen.findByText(SERVER_ONLY_CODE)).toBeInTheDocument()
    expect(screen.getByText(`${SERVER_ONLY_RATE} %`)).toBeInTheDocument()
  })
})
