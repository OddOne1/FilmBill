import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(), post: vi.fn(), delete: vi.fn() },
  setActiveCompanyId: vi.fn(),
  ApiError: class extends Error {},
}))

import { api } from '@/lib/api'
import SecurityPage from '../security/page'
import { useCompanyStore } from '@/stores/company-store'
import type { Company, CompanyMember } from '@/types'

/**
 * P0b-2 §3: the Security screen must state the consequence **before** the
 * save, not after.
 *
 * Adding a role to `require_2fa_roles` forces every member holding it into
 * two-factor enrolment at their next login, and P0b-1's
 * `two_factor_required_for` then refuses to let them turn it off. For someone
 * whose mail is broken and who has no authenticator, that is a lockout only a
 * superadmin can undo. The person ticking the box is the only one who can
 * judge whether those people can receive mail — so they have to be told who
 * they are, while they can still untick it.
 *
 * These assert on the rendered warning rather than on the handler, because
 * "the user was told" is a fact about the screen. A test of the state would
 * pass for a warning that renders off-screen or after the request.
 */

function makeCompany(overrides: Partial<Company> = {}): Company {
  return {
    id: 'c1',
    legal_name: 'YON Studio OG',
    trading_name: null,
    legal_form: null,
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
    fiscal_year_start_month: 1,
    timezone: 'Europe/Vienna',
    logo_s3_key: null,
    require_2fa_roles: [],
    tax_advisor_reports: false,
    bookkeeping_mode: 'ear',
    vat_timing: 'soll',
    kleinunternehmer: false,
    chart_of_accounts_template: null,
    export_format: null,
    archive_date_basis: 'invoice_date',
    month_approval_enabled: false,
    created_at: '2026-01-01T00:00:00Z',
    archived_at: null,
    ...overrides,
  }
}

function member(
  name: string,
  role: CompanyMember['role'],
  twoFactor: boolean,
): CompanyMember {
  return {
    id: `m-${name}`,
    user_id: `u-${name}`,
    email: `${name}@example.com`,
    name,
    role,
    status: 'active',
    granted_at: '2026-01-01T00:00:00Z',
    expires_at: null,
    revoked_at: null,
    is_active: true,
    two_factor_enabled: twoFactor,
  }
}

function seedStore(permissions: string[]) {
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

const FULL = ['company.view', 'company.settings.edit', 'company.members.manage']

function respondWith(company: Company, members: CompanyMember[]) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/company') return company as never
    if (path === '/company/members') return members as never
    throw new Error(`unexpected GET ${path}`)
  })
}

describe('Security screen — the consequence of requiring 2FA', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    seedStore(FULL)
  })

  it('says nothing until a role is actually added', async () => {
    respondWith(makeCompany(), [member('Ada', 'producer', false)])

    render(<SecurityPage />)
    await screen.findByText('Producer')

    expect(screen.queryByTestId('require-2fa-consequence')).not.toBeInTheDocument()
  })

  it('warns the moment the box is ticked, before any save', async () => {
    respondWith(makeCompany(), [member('Ada', 'producer', false)])

    render(<SecurityPage />)
    fireEvent.click(await screen.findByLabelText('Producer'))

    const warning = screen.getByTestId('require-2fa-consequence')
    expect(warning).toHaveTextContent(/force two-factor setup/i)
    expect(warning).toHaveTextContent(/will not be able to turn it off/i)
    // Nothing has been sent. The point is that the warning precedes the save.
    expect(api.patch).not.toHaveBeenCalled()
  })

  it('names the people who would be forced to enrol', async () => {
    // A count would not be enough: the person saving has to RECOGNISE them to
    // judge whether they can receive mail.
    respondWith(makeCompany(), [
      member('Ada', 'producer', false),
      member('Bo', 'producer', true),
      member('Cy', 'staff', false),
    ])

    render(<SecurityPage />)
    fireEvent.click(await screen.findByLabelText('Producer'))

    const warning = screen.getByTestId('require-2fa-consequence')
    expect(warning).toHaveTextContent('Ada')
    // Bo already has 2FA, Cy holds a different role — neither is affected.
    expect(warning).not.toHaveTextContent('Bo')
    expect(warning).not.toHaveTextContent('Cy')
    expect(warning).toHaveTextContent(/locked out/i)
  })

  it('says plainly when nobody would be locked out', async () => {
    // Still a warning — it applies to anyone added later — but it must not
    // claim a risk that is not there, or the warning stops being read.
    respondWith(makeCompany(), [member('Bo', 'producer', true)])

    render(<SecurityPage />)
    fireEvent.click(await screen.findByLabelText('Producer'))

    const warning = screen.getByTestId('require-2fa-consequence')
    expect(warning).toHaveTextContent(/already has two-factor/i)
    expect(warning).toHaveTextContent(/nobody is locked out today/i)
    // The alarming claim specifically. "nobody is locked out today" contains
    // the words and is the opposite statement — asserting on the bare phrase
    // would have failed on correct copy.
    expect(warning).not.toHaveTextContent(/will be locked out/i)
  })

  it('withdraws the warning when the box is unticked again', async () => {
    respondWith(makeCompany(), [member('Ada', 'producer', false)])

    render(<SecurityPage />)
    const box = await screen.findByLabelText('Producer')
    fireEvent.click(box)
    expect(screen.getByTestId('require-2fa-consequence')).toBeInTheDocument()

    fireEvent.click(box)

    expect(screen.queryByTestId('require-2fa-consequence')).not.toBeInTheDocument()
  })

  it('does not warn about a role that is already required', async () => {
    // Re-saving an unchanged setting forces nobody into anything, and a
    // warning there would be noise that teaches people to ignore it.
    respondWith(makeCompany({ require_2fa_roles: ['producer'] }), [
      member('Ada', 'producer', false),
    ])

    render(<SecurityPage />)
    await screen.findByText('Producer')

    expect(screen.queryByTestId('require-2fa-consequence')).not.toBeInTheDocument()
  })

  it('says on the button itself what the save is about to do', async () => {
    respondWith(makeCompany(), [member('Ada', 'producer', false)])

    render(<SecurityPage />)
    expect(
      await screen.findByRole('button', { name: /save changes/i }),
    ).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('Producer'))

    expect(
      screen.getByRole('button', { name: /save and require two-factor/i }),
    ).toBeInTheDocument()
  })

  it('sends the roles on save', async () => {
    respondWith(makeCompany(), [member('Ada', 'producer', false)])
    vi.mocked(api.patch).mockResolvedValue(
      makeCompany({ require_2fa_roles: ['producer'] }) as never,
    )

    render(<SecurityPage />)
    fireEvent.click(await screen.findByLabelText('Producer'))
    fireEvent.click(screen.getByRole('button', { name: /save and require/i }))

    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith('/company', {
        require_2fa_roles: ['producer'],
        tax_advisor_reports: false,
      }),
    )
  })

  it('shows tax advisor as permanently required and not changeable', async () => {
    // That role is held by someone outside the company, so it requires 2FA on
    // every install and is not a per-company decision (P0b-1 §4).
    respondWith(makeCompany(), [])

    render(<SecurityPage />)
    const box = (await screen.findByLabelText(/Tax advisor/)) as HTMLInputElement

    expect(box.checked).toBe(true)
    expect(box.disabled).toBe(true)
  })

  it('refuses the whole screen to a role that cannot edit settings', async () => {
    // Stated, not blank, and not a redirect (rule 17c).
    seedStore(['company.view'])

    render(<SecurityPage />)

    expect(
      await screen.findByText(/does not include this/i),
    ).toBeInTheDocument()
    expect(api.get).not.toHaveBeenCalled()
  })
})
