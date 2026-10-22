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
import type { TaxClass, TaxCode, TaxProposal, TaxRule } from '@/types'

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
    position: 10,
    origin_pack: 'at',
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
    position: 20,
    origin_pack: 'at',
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
  proposal = makeProposal(),
}: {
  codes?: TaxCode[]
  classes?: TaxClass[]
  rules?: TaxRule[]
  proposal?: TaxProposal
} = {}) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/company/tax/codes') return codes as never
    if (path === '/company/tax/classes') return classes as never
    if (path === '/company/tax/rules') return rules as never
    throw new Error(`unexpected GET ${path}`)
  })
  vi.mocked(api.post).mockImplementation(async () => proposal as never)
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

// ─── Codes ──────────────────────────────────────────────────────────────────

describe('the Codes tab shows what the API sent', () => {
  it('lists each code with its rate, category and note state', async () => {
    mockApi()
    render(<TaxPage />)

    // Gates on a fetched value — the rate string, which no part of the
    // chrome contains.
    expect(await screen.findByText('20.0000 %')).toBeInTheDocument()
    expect(screen.getByText('AT-20')).toBeInTheDocument()
    expect(screen.getByText('EU-AE')).toBeInTheDocument()
    // `AT-20` ships without a legal note, `EU-AE` with an unreviewed one.
    expect(screen.getByText('No legal note')).toBeInTheDocument()
    expect(screen.getByText('Note not reviewed')).toBeInTheDocument()
  })

  it('shows a reviewed note as reviewed', async () => {
    mockApi({ codes: [{ ...REVERSE_CHARGE, notes_reviewed: true }] })
    render(<TaxPage />)

    expect(await screen.findByText('Note reviewed')).toBeInTheDocument()
    expect(screen.queryByText('Note not reviewed')).not.toBeInTheDocument()
  })

  it('tells the user to set a rate on the generic pack’s inactive standard code', async () => {
    mockApi({ codes: [UNSET_STANDARD] })
    render(<TaxPage />)

    const hint = await screen.findByTestId('tax-code-needs-rate-EU-STD')
    expect(hint).toHaveTextContent('Set your rate')
    expect(hint).toHaveTextContent('nothing can be proposed from it')
  })

  it('does not show that hint on an active code', async () => {
    mockApi({ codes: [makeCode()] })
    render(<TaxPage />)

    await screen.findByText('20.0000 %')
    expect(
      screen.queryByTestId('tax-code-needs-rate-AT-20'),
    ).not.toBeInTheDocument()
  })

  it('says what to do when the company has no tax codes at all', async () => {
    mockApi({ codes: [] })
    render(<TaxPage />)

    const empty = await screen.findByTestId('tax-codes-empty')
    expect(empty).toHaveTextContent('it has no country')
    expect(empty).toHaveTextContent('Company')
  })
})

describe('editing a code', () => {
  it('sends only the editable fields, and never the code or the category', async () => {
    mockApi()
    render(<TaxPage />)
    fireEvent.click(await screen.findByText('AT-20'))

    const rate = await screen.findByLabelText(/Rate \(%\)/)
    fireEvent.change(rate, { target: { value: '21' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(api.patch).toHaveBeenCalled())
    const [path, body] = vi.mocked(api.patch).mock.calls[0]
    expect(path).toBe('/company/tax/codes/code-20')
    expect(body).toMatchObject({ rate: '21' })
    // Immutable on the server; not even offered here. A screen that sent
    // them would get a 422 naming the field.
    expect(body).not.toHaveProperty('code')
    expect(body).not.toHaveProperty('category')
  })

  it('says the code and the category cannot be changed, and why', async () => {
    mockApi()
    render(<TaxPage />)
    fireEvent.click(await screen.findByText('AT-20'))

    const drawer = await screen.findByLabelText(/Rate \(%\)/)
    expect(drawer).toBeInTheDocument()
    expect(
      screen.getByText(/cannot\s+be changed/),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/renaming it would restate invoices/),
    ).toBeInTheDocument()
  })

  it('offers a note field per language and drops a language that is emptied', async () => {
    mockApi({ codes: [REVERSE_CHARGE] })
    render(<TaxPage />)
    fireEvent.click(await screen.findByText('EU-AE'))

    const german = await screen.findByLabelText('DE')
    expect(german).toHaveValue(
      'Steuerschuld geht auf den Leistungsempfänger über.',
    )
    fireEvent.change(german, { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(api.patch).toHaveBeenCalled())
    const [, body] = vi.mocked(api.patch).mock.calls[0]
    expect((body as { legal_notes: Record<string, string> }).legal_notes).toEqual({
      en: 'Reverse charge: VAT to be accounted for by the recipient.',
    })
  })

  it('shows the server’s own sentence when a save is refused', async () => {
    mockApi()
    vi.mocked(api.patch).mockRejectedValue(
      new ApiError(
        422,
        'Tax code \'EU-AE\': category \'AE\' is a zero-rate category, so its rate must be 0 and not 20.',
      ),
    )
    render(<TaxPage />)
    fireEvent.click(await screen.findByText('AT-20'))
    fireEvent.change(await screen.findByLabelText(/Rate \(%\)/), {
      target: { value: '20' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    // Verbatim. The server owns this explanation, and a generic "could not
    // save" would hide the only sentence that says what to do.
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'zero-rate category',
    )
  })

  it('says “No changes” rather than being inert when nothing was edited', async () => {
    mockApi()
    render(<TaxPage />)
    fireEvent.click(await screen.findByText('AT-20'))

    // Rule 17c: a disabled control must say why, and this one says it in its
    // own label rather than being dead and silent.
    const button = await screen.findByRole('button', { name: 'No changes' })
    expect(button).toBeDisabled()
  })
})

describe('a member who may read but not edit', () => {
  it('sees the rows, a sentence saying why, and no edit drawer', async () => {
    seedCompany(['company.view'])
    mockApi()
    render(<TaxPage />)

    expect(await screen.findByText('20.0000 %')).toBeInTheDocument()
    expect(
      screen.getByText(/read these but not change them/),
    ).toBeInTheDocument()

    fireEvent.click(screen.getByText('AT-20'))
    // No drawer, and — rule 17c — no dead Save button either.
    expect(screen.queryByLabelText(/Rate \(%\)/)).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /^(Save|No changes)$/ }),
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
