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
import CompanyGeneralPage from '../page'
import { useCompanyStore } from '@/stores/company-store'
import type { CountryChangePlan } from '@/types'

/**
 * Settings → Company → the Country field and its dialog.
 *
 * **The dialog decides nothing.** Every sentence, warning and count it shows
 * comes from `POST /company/tax/country-change/preview`; Confirm sends the
 * same country and mode to the real endpoint. So the plan these tests feed
 * the mock is deliberately one the screen could not have composed — a code
 * name that is in no pack and a sentence no template here produces. If the
 * dialog ever starts describing the change in its own words, it stops
 * matching and these fail.
 *
 * The Country box itself is read-only on purpose. It used to be an ordinary
 * text field, and that was the bug: typing over it changed the country and
 * installed nothing, so a company that corrected DE to AT kept German rates.
 */

const SERVER_ONLY_CODE = 'XX-SERVER-SAID'
const SERVER_ONLY_SENTENCE = `1 tax code(s) are added: ${SERVER_ONLY_CODE}.`

function makePlan(overrides: Partial<CountryChangePlan> = {}): CountryChangePlan {
  return {
    from_country: 'AT',
    to_country: 'DE',
    pack: 'de',
    mode: 'apply_pack',
    first_set: false,
    codes_added: [SERVER_ONLY_CODE],
    classes_repointed: [['standard', 'AT-20', 'DE-19']],
    classes_without_equivalent: [['reduced_2', 'AT-13']],
    classes_added: [],
    codes_deactivated: ['AT-20'],
    codes_reactivated: [],
    codes_kept_because_referenced: [],
    untouched: [],
    rules_replaced: 0,
    warnings: [],
    sentences: [SERVER_ONLY_SENTENCE],
    counts: {},
    ...overrides,
  }
}

const COMPANY = {
  id: 'c1',
  legal_name: 'YON Studio OG',
  trading_name: null,
  legal_form: 'OG',
  register_number: null,
  register_court: null,
  vat_id: null,
  tax_number: null,
  address_street: null,
  address_zip: null,
  address_city: null,
  address_country: 'AT',
  email: null,
  phone: null,
  website: null,
  default_currency: 'EUR',
  default_language: 'de',
  timezone: 'Europe/Vienna',
  fiscal_year_start_month: 1,
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

function mockApi(plan: CountryChangePlan | null = makePlan()) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/company') return COMPANY as never
    throw new Error(`unexpected GET ${path}`)
  })
  vi.mocked(api.post).mockImplementation(async () => plan as never)
  vi.mocked(api.patch).mockImplementation(async () => COMPANY as never)
}

async function openDialog() {
  // Gates on a fetched value — the country from `/company` — rather than on
  // the button, which paints immediately.
  await waitFor(() =>
    expect(screen.getByLabelText('Country')).toHaveValue('AT'),
  )
  fireEvent.click(screen.getByTestId('company-change-country'))
  return screen.findByTestId('change-country-dialog')
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany()
})

describe('the Country field', () => {
  it('shows the country read-only, with a button rather than a text box', async () => {
    mockApi()
    render(<CompanyGeneralPage />)

    const field = await screen.findByLabelText('Country')
    expect(field).toHaveValue('AT')
    expect(field).toBeDisabled()
    expect(screen.getByTestId('company-change-country')).toHaveTextContent(
      'Change…',
    )
  })

  it('never sends the country in an ordinary save', async () => {
    mockApi()
    render(<CompanyGeneralPage />)
    await waitFor(() =>
      expect(screen.getByLabelText('Country')).toHaveValue('AT'),
    )

    fireEvent.submit(screen.getByRole('button', { name: /Save/ }).closest('form')!)

    await waitFor(() => expect(api.patch).toHaveBeenCalled())
    const [, body] = vi.mocked(api.patch).mock.calls[0]
    // The whole point: `PATCH /company` refuses a country change on a
    // company with tax rows, so this screen must not try.
    expect(body).not.toHaveProperty('address_country')
  })

  it('says “Set…” when there is no country yet', async () => {
    vi.mocked(api.get).mockResolvedValue({
      ...COMPANY,
      address_country: null,
    } as never)
    render(<CompanyGeneralPage />)

    expect(
      await screen.findByTestId('company-change-country'),
    ).toHaveTextContent('Set…')
  })
})

describe('the change-country dialog', () => {
  it('shows the plan the server computed, verbatim', async () => {
    mockApi()
    render(<CompanyGeneralPage />)
    await openDialog()

    fireEvent.change(screen.getByLabelText('New country'), {
      target: { value: 'de' },
    })

    const shown = await screen.findByTestId('change-country-plan')
    // A sentence and a code the screen could not have composed.
    expect(shown).toHaveTextContent(SERVER_ONLY_SENTENCE)
    await waitFor(() =>
      expect(vi.mocked(api.post).mock.calls.at(-1)).toEqual([
        '/company/tax/country-change/preview',
        { country: 'DE', mode: 'apply_pack' },
      ]),
    )
  })

  it('re-asks the server when the mode changes', async () => {
    mockApi()
    render(<CompanyGeneralPage />)
    await openDialog()
    fireEvent.change(screen.getByLabelText('New country'), {
      target: { value: 'DE' },
    })
    await screen.findByTestId('change-country-plan')

    fireEvent.click(
      screen.getByRole('radio', { name: /Change the country only/ }),
    )

    // `country_only` and `apply_pack` are genuinely different plans, and
    // showing one while the other radio is selected is the worst thing this
    // dialog could do.
    await waitFor(() =>
      expect(vi.mocked(api.post).mock.calls.at(-1)?.[1]).toMatchObject({
        mode: 'country_only',
      }),
    )
  })

  it('shows the server’s warnings as warnings', async () => {
    mockApi(
      makePlan({
        warnings: [
          'The standard rate for FR is 0 % and not usable yet: proposals will be blocked until you set your rate on the Tax screen.',
        ],
      }),
    )
    render(<CompanyGeneralPage />)
    await openDialog()
    fireEvent.change(screen.getByLabelText('New country'), {
      target: { value: 'FR' },
    })

    const warning = await screen.findByTestId('change-country-warning')
    expect(warning).toHaveTextContent('proposals will be blocked')
    expect(warning).toHaveTextContent('set your rate')
  })

  it('says that finalised documents are unaffected', async () => {
    mockApi()
    render(<CompanyGeneralPage />)
    await openDialog()

    expect(
      screen.getByText(/already finalised are unaffected/),
    ).toBeInTheDocument()
  })

  it('cannot be confirmed before a plan has come back', async () => {
    mockApi()
    render(<CompanyGeneralPage />)
    await openDialog()

    // Rule 17c: disabled, and the label says what it is waiting for rather
    // than leaving a dead button.
    const confirm = screen.getByTestId('change-country-confirm')
    expect(confirm).toBeDisabled()
    expect(confirm).toHaveTextContent('Enter a country')

    fireEvent.change(screen.getByLabelText('New country'), {
      target: { value: 'DE' },
    })
    await screen.findByTestId('change-country-plan')
    expect(screen.getByTestId('change-country-confirm')).toBeEnabled()
  })

  it('sends the same country and mode it previewed', async () => {
    mockApi()
    render(<CompanyGeneralPage />)
    await openDialog()
    fireEvent.change(screen.getByLabelText('New country'), {
      target: { value: 'DE' },
    })
    await screen.findByTestId('change-country-plan')

    fireEvent.click(screen.getByTestId('change-country-confirm'))

    await waitFor(() => {
      const applied = vi
        .mocked(api.post)
        .mock.calls.filter(
          ([path]) => path === '/company/tax/country-change',
        )
      expect(applied).toHaveLength(1)
      expect(applied[0][1]).toEqual({ country: 'DE', mode: 'apply_pack' })
    })
  })

  it('shows the server’s sentence when the change is refused', async () => {
    mockApi()
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path.endsWith('/preview')) return makePlan() as never
      throw new ApiError(
        422,
        "'ZZ' is not a country. Use a two-letter ISO code, for example AT or DE.",
      )
    })
    render(<CompanyGeneralPage />)
    await openDialog()
    fireEvent.change(screen.getByLabelText('New country'), {
      target: { value: 'ZZ' },
    })
    await screen.findByTestId('change-country-plan')
    fireEvent.click(screen.getByTestId('change-country-confirm'))

    expect(await screen.findByTestId('change-country-error')).toHaveTextContent(
      'two-letter ISO code',
    )
  })

  it('shows the new country in the field afterwards', async () => {
    mockApi()
    render(<CompanyGeneralPage />)
    await openDialog()
    fireEvent.change(screen.getByLabelText('New country'), {
      target: { value: 'DE' },
    })
    await screen.findByTestId('change-country-plan')
    fireEvent.click(screen.getByTestId('change-country-confirm'))

    await waitFor(() =>
      expect(screen.getByLabelText('Country')).toHaveValue('DE'),
    )
    expect(
      screen.queryByTestId('change-country-dialog'),
    ).not.toBeInTheDocument()
  })
})
