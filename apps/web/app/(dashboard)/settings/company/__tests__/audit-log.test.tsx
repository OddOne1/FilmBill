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

import { api } from '@/lib/api'
import AuditLogPage from '../audit-log/page'
import { useCompanyStore } from '@/stores/company-store'
import type { AuditEvent, AuditEventPage } from '@/types'

/**
 * Settings → Company → Audit log.
 *
 * **What these tests are for.** Three things that are easy to get wrong in a
 * read-only screen and expensive if they are:
 *
 *   1. values render VERBATIM. `[redacted]` is a marker the API put there and
 *      the masked IBAN is the API's masking — the screen must not tidy either
 *      away, and must certainly not try to reconstruct them. The masking is
 *      what stops `audit.view` being a way round the `company.settings.edit`
 *      gate on bank accounts, and the tax advisor holds `audit.view`.
 *   2. the filters reach the SERVER. The log is paged, so a filter applied in
 *      the browser would filter one page of fifty and quietly lie about the
 *      rest.
 *   3. the screen says it is append-only, because a log with no buttons
 *      otherwise reads as unfinished rather than as deliberate.
 *
 * Readiness gates on the fetched rows, never on the heading — the heading
 * paints on first render, so waiting for it and then reaching for a row is a
 * race the fast machine wins (CLAUDE.md's harness rule, the CI-flake
 * instance). Green with the fetch delayed 20, 50 and 120 ms.
 */

function makeEvent(overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    id: 'e1',
    entity_type: 'companies',
    entity_id: 'c1',
    action: 'company.updated',
    changes: { legal_name: ['YON Studio OG', 'YON Studio OG (Wien)'] },
    reason: null,
    user_id: 'u1',
    user_name: 'Owner Person',
    user_email: 'owner@example.com',
    ip: '192.0.2.7',
    created_at: '2026-10-20T09:00:00Z',
    ...overrides,
  }
}

function makePage(
  events: AuditEvent[],
  overrides: Partial<AuditEventPage> = {},
): AuditEventPage {
  return {
    events,
    total: events.length,
    limit: 50,
    offset: 0,
    entity_types: ['companies', 'company_bank_accounts'],
    actions: ['bank_account.created', 'company.updated'],
    ...overrides,
  }
}

function seedCompany(permissions = ['audit.view']) {
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

/** Resolve the next `api.get` with `page`.
 *
 * A helper rather than `mockResolvedValue(makePage(...) as never)` at each
 * call site: the `as never` the mocked module's signature needs pushes
 * `never` into the object literal's contextual type, and tsc then rejects
 * `entity_types: ['companies']`. One cast, in one place, away from the
 * literals.
 */
function respondWith(page: AuditEventPage) {
  vi.mocked(api.get).mockResolvedValue(page as never)
}

/** The one value that appears ONLY in a fetched row.
 *
 * Not the action and not the email: this screen builds its Action and User
 * filters from the same response, so both of those match an `<option>` as
 * well as the row and `findByText` throws on multiple matches. Nine of these
 * tests failed at once that way — every one of them reporting the gate
 * rather than the thing it was about, which is the readiness-gate trap in
 * CLAUDE.md's harness rule wearing a different hat.
 *
 * The IP is in no filter, so it is unambiguous, and it exists only after the
 * fetch resolved — which is what a readiness gate has to be.
 */
const LOADED_ROW = '192.0.2.7'

/** The last query string the screen asked for. */
function lastQuery(): URLSearchParams {
  const calls = vi.mocked(api.get).mock.calls
  const path = String(calls[calls.length - 1][0])
  return new URLSearchParams(path.split('?')[1] ?? '')
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany()
})

describe('the log renders what the API sent', () => {
  it('shows each event with who, what and the before/after pair', async () => {
    respondWith(makePage([makeEvent()]))

    render(<AuditLogPage />)

    expect(await screen.findByText(LOADED_ROW)).toBeTruthy()
    // `getAllByText` for these two: the screen builds its Action and User
    // filters from the same response, so each also appears as an `<option>`.
    expect(screen.getAllByText('company.updated').length).toBeGreaterThan(0)
    expect(screen.getAllByText('owner@example.com').length).toBeGreaterThan(0)
    // `getAllByText` again: the old value is also the active company's name
    // in this screen's own header, which `CompanySection` renders.
    expect(screen.getAllByText('YON Studio OG').length).toBeGreaterThan(0)
    expect(screen.getByText('YON Studio OG (Wien)')).toBeTruthy()
  })

  it('says the log is append-only and that IBANs are masked', async () => {
    respondWith(makePage([makeEvent()]))
    render(<AuditLogPage />)

    await screen.findByText(LOADED_ROW)
    expect(
      screen.getByText(/cannot be edited or removed/),
    ).toBeTruthy()
    expect(screen.getByText(/shown masked/)).toBeTruthy()
  })

  it('shows a masked IBAN exactly as the API sent it', async () => {
    // `AT…3201` is the server's masking (country code + last four). The
    // screen renders it verbatim: it must not expand it, and it has no way
    // to — which is the point of masking server-side.
    respondWith(makePage([
        makeEvent({
          entity_type: 'company_bank_accounts',
          action: 'bank_account.created',
          changes: { iban: [null, 'AT…3201'], label: [null, 'Hauptkonto'] },
        }),
      ]))

    render(<AuditLogPage />)

    expect(await screen.findByText('AT…3201')).toBeTruthy()
    expect(screen.getByText('Hauptkonto')).toBeTruthy()
  })

  it('shows the redaction marker as itself, not as a blank', async () => {
    // A screen that rendered `[redacted]` as an empty cell would make a
    // password change look like a change to nothing.
    respondWith(makePage([
        makeEvent({
          entity_type: 'users',
          action: 'two_factor.enabled',
          changes: { totp_secret_encrypted: ['[redacted]', '[redacted]'] },
        }),
      ]))

    render(<AuditLogPage />)

    const markers = await screen.findAllByText('[redacted]')
    expect(markers.length).toBe(2)
  })

  it('renders a system event with no actor as “system”', async () => {
    respondWith(
      makePage([
        makeEvent({ user_id: null, user_name: null, user_email: null, ip: null }),
      ]),
    )

    render(<AuditLogPage />)

    expect(await screen.findByText('system')).toBeTruthy()
  })

  it('shows the reason when the event has one', async () => {
    respondWith(makePage([
        makeEvent({
          action: 'number.out_of_order',
          entity_type: 'number_allocations',
          changes: { number: ['AR-2026-0001', 'AR-2025-0013'] },
          reason:
            'This document is dated 2025-11-30, which is before 2026-06-01.',
        }),
      ]))

    render(<AuditLogPage />)

    expect(
      await screen.findByText(/dated 2025-11-30, which is before 2026-06-01/),
    ).toBeTruthy()
  })

  it('says so rather than showing an empty table when there is nothing', async () => {
    respondWith(makePage([]))

    render(<AuditLogPage />)

    expect(await screen.findByTestId('audit-empty')).toBeTruthy()
  })

  it('shows the server’s refusal rather than an empty log', async () => {
    const { ApiError } = await import('@/lib/api')
    vi.mocked(api.get).mockRejectedValue(new ApiError(404, 'Not found'))

    render(<AuditLogPage />)

    expect(await screen.findByRole('alert')).toBeTruthy()
  })
})

describe('the filters are applied by the server', () => {
  it('sends the entity, action and user filters as query parameters', async () => {
    respondWith(makePage([makeEvent()]))
    render(<AuditLogPage />)
    await screen.findByText(LOADED_ROW)

    fireEvent.change(screen.getByLabelText('Entity'), {
      target: { value: 'company_bank_accounts' },
    })
    await waitFor(() =>
      expect(lastQuery().get('entity_type')).toBe('company_bank_accounts'),
    )

    fireEvent.change(screen.getByLabelText('Action'), {
      target: { value: 'bank_account.created' },
    })
    await waitFor(() =>
      expect(lastQuery().get('action')).toBe('bank_account.created'),
    )

    fireEvent.change(screen.getByLabelText('User'), {
      target: { value: 'u1' },
    })
    await waitFor(() => expect(lastQuery().get('user_id')).toBe('u1'))
  })

  it('offers only the entity types and actions this log actually holds', async () => {
    respondWith(makePage([makeEvent()], {
        entity_types: ['companies'],
        actions: ['company.updated'],
      }))

    render(<AuditLogPage />)
    await screen.findByText(LOADED_ROW)

    const entity = screen.getByLabelText('Entity') as HTMLSelectElement
    expect(
      Array.from(entity.options).map((option) => option.value),
    ).toEqual(['', 'companies'])
  })

  it('sends a date only once it is complete', async () => {
    respondWith(makePage([makeEvent()]))
    render(<AuditLogPage />)
    await screen.findByText(LOADED_ROW)

    fireEvent.change(screen.getByLabelText('From'), {
      target: { value: '2026-1' },
    })
    await waitFor(() => expect(lastQuery().has('from')).toBe(false))

    fireEvent.change(screen.getByLabelText('From'), {
      target: { value: '2026-10-01' },
    })
    await waitFor(() => expect(lastQuery().get('from')).toBe('2026-10-01'))
  })

  it('resets to the first page when a filter changes', async () => {
    // Otherwise "page 3 of the unfiltered log" becomes "page 3 of a filtered
    // log that has one page", which answers "nothing found" for a filter that
    // matches plenty.
    respondWith(makePage([makeEvent()], { total: 120 }))
    render(<AuditLogPage />)
    await screen.findByText(LOADED_ROW)

    fireEvent.click(screen.getByRole('button', { name: 'Older' }))
    await waitFor(() => expect(lastQuery().get('offset')).toBe('50'))

    fireEvent.change(screen.getByLabelText('Entity'), {
      target: { value: 'companies' },
    })
    await waitFor(() => expect(lastQuery().get('offset')).toBe('0'))
  })

  it('clears every filter at once', async () => {
    respondWith(makePage([makeEvent()]))
    render(<AuditLogPage />)
    await screen.findByText(LOADED_ROW)

    fireEvent.change(screen.getByLabelText('Entity'), {
      target: { value: 'companies' },
    })
    await waitFor(() => expect(lastQuery().get('entity_type')).toBe('companies'))

    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    await waitFor(() => expect(lastQuery().has('entity_type')).toBe(false))
  })
})

describe('paging', () => {
  it('hides the controls when everything fits on one page', async () => {
    respondWith(makePage([makeEvent()]))
    render(<AuditLogPage />)
    // Gated on the fetched row, so the two nulls below cannot pass vacuously
    // against a screen that never loaded.
    await screen.findByText(LOADED_ROW)

    expect(screen.queryByRole('button', { name: 'Older' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Newer' })).toBeNull()
  })

  it('says which rows are on screen, and Newer is dead only on page one', async () => {
    respondWith(makePage([makeEvent()], { total: 120 }))
    render(<AuditLogPage />)
    await screen.findByText(LOADED_ROW)

    expect(screen.getByText('1–50 of 120')).toBeTruthy()
    expect(
      (screen.getByRole('button', { name: 'Newer' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true)
    expect(
      (screen.getByRole('button', { name: 'Older' }) as HTMLButtonElement)
        .disabled,
    ).toBe(false)
  })
})

describe('the permission gate', () => {
  it('refuses in a sentence, not a blank page, without audit.view', async () => {
    seedCompany(['company.view', 'company.settings.edit'])
    respondWith(makePage([makeEvent()]))

    render(<AuditLogPage />)

    expect(await screen.findByText(/does not include this/)).toBeTruthy()
    expect(screen.queryByText(LOADED_ROW)).toBeNull()
  })
})
