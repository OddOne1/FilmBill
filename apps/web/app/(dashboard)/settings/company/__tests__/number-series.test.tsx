import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(), post: vi.fn(), delete: vi.fn() },
  setActiveCompanyId: vi.fn(),
  ApiError: class extends Error {},
}))

import { api } from '@/lib/api'
import NumberSeriesPage from '../number-series/page'
import { useCompanyStore } from '@/stores/company-store'
import type { NumberSeries } from '@/types'

/**
 * Settings → Company → Number series.
 *
 * **What these tests are for.** The one rule this screen must not break is
 * that it never builds a number itself (CLAUDE.md rule 2) — the padding, the
 * `{YYYY}` substitution and "the next one is 413" all come from
 * `POST /company/number-series/preview`, which runs the same engine the
 * finalize transaction will. So the preview assertions deliberately feed the
 * mock a number that NO correct client-side formatter would produce from the
 * pattern on screen: if the screen ever starts formatting locally, it stops
 * matching and these fail.
 *
 * **Readiness gates on the fetched value, not on the heading** (CLAUDE.md's
 * harness rule, the CI-flake instance): the heading paints on first render,
 * so waiting for it and then reaching for a series row is a race the fast
 * machine wins. Every gate below is a `findBy*` on something that only exists
 * once `/company/number-series` has resolved.
 */

function makeSeries(overrides: Partial<NumberSeries> = {}): NumberSeries {
  return {
    id: 's1',
    document_type: 'invoice',
    name: 'Ausgangsrechnungen',
    pattern: 'AR-{YYYY}-{SEQ}',
    reset: 'yearly',
    next_value: 1,
    current_period: null,
    padding: 4,
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: null,
    allocated_count: 0,
    latest_number: null,
    ...overrides,
  }
}

/** A number the client could not have produced from the pattern on screen. */
const SERVER_ONLY_NUMBER = 'SERVER-SAYS-0413'

function seedCompany(permissions = ['number_series.manage']) {
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

function mockApi(
  series: NumberSeries[],
  preview: Record<string, unknown> = { number: SERVER_ONLY_NUMBER, value: 413, period: '2026', warnings: [], starts_new_period: false },
) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/company/number-series') return series as never
    throw new Error(`unexpected GET ${path}`)
  })
  vi.mocked(api.post).mockImplementation(async () => preview as never)
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany()
})

describe('the screen shows the series the API sent', () => {
  it('lists each series with its pattern and how many numbers it has issued', async () => {
    mockApi([
      makeSeries(),
      makeSeries({
        id: 's2',
        document_type: 'quote',
        name: 'Angebote',
        pattern: 'AN-{YYYY}-{SEQ}',
        allocated_count: 412,
        latest_number: 'AN-2026-0412',
      }),
    ])

    render(<NumberSeriesPage />)

    expect(await screen.findByText('Ausgangsrechnungen')).toBeTruthy()
    expect(screen.getByText('Angebote')).toBeTruthy()
    expect(screen.getByText('AR-{YYYY}-{SEQ}')).toBeTruthy()
    expect(screen.getByText('412 issued, last AN-2026-0412')).toBeTruthy()
    expect(screen.getByText('none issued yet')).toBeTruthy()
  })

  it('says a number is taken at finalise and never on this screen', async () => {
    mockApi([makeSeries()])
    render(<NumberSeriesPage />)

    await screen.findByText('Ausgangsrechnungen')
    expect(
      screen.getByText(/taken when a document is finalised, and never before/),
    ).toBeTruthy()
  })
})

describe('the preview comes from the API', () => {
  it('shows the number the server returned, not one built from the pattern', async () => {
    mockApi([makeSeries()])
    render(<NumberSeriesPage />)

    fireEvent.click(await screen.findByText('Ausgangsrechnungen'))

    // The value asserted is deliberately not derivable from `AR-{YYYY}-{SEQ}`
    // with padding 4. A screen that formatted locally would render
    // `AR-2026-0001` here and this would fail — which is the point.
    expect(await screen.findByText(SERVER_ONLY_NUMBER)).toBeTruthy()
    expect(vi.mocked(api.post)).toHaveBeenCalledWith(
      '/company/number-series/preview',
      expect.objectContaining({
        series_id: 's1',
        pattern: 'AR-{YYYY}-{SEQ}',
        reset: 'yearly',
        padding: 4,
      }),
    )
  })

  it('asks again when the pattern is edited', async () => {
    mockApi([makeSeries()])
    render(<NumberSeriesPage />)

    fireEvent.click(await screen.findByText('Ausgangsrechnungen'))
    await screen.findByText(SERVER_ONLY_NUMBER)

    const pattern = screen.getByDisplayValue('AR-{YYYY}-{SEQ}')
    fireEvent.change(pattern, { target: { value: 'RE-{YY}/{SEQ}' } })

    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        '/company/number-series/preview',
        expect.objectContaining({ pattern: 'RE-{YY}/{SEQ}' }),
      ),
    )
  })

  it('sends a preview date only once it is a complete date', async () => {
    mockApi([makeSeries()])
    render(<NumberSeriesPage />)

    fireEvent.click(await screen.findByText('Ausgangsrechnungen'))
    await screen.findByText(SERVER_ONLY_NUMBER)
    vi.mocked(api.post).mockClear()

    const dateField = screen.getByLabelText(/Preview for the date/)
    fireEvent.change(dateField, { target: { value: '2025-12-31' } })

    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        '/company/number-series/preview',
        expect.objectContaining({ document_date: '2025-12-31' }),
      ),
    )
  })

  it('shows the out-of-order warning the server sent, in the server’s words', async () => {
    const message =
      'This document is dated 2025-12-31, which is before 2026-06-01 — the date of AR-2026-0001, the most recent document in this series.'
    mockApi([makeSeries()], {
      number: 'AR-2025-0001',
      value: 1,
      period: '2025',
      starts_new_period: false,
      warnings: [
        {
          code: 'document_date_before_latest',
          message,
          latest_document_date: '2026-06-01',
          latest_number: 'AR-2026-0001',
        },
      ],
    })

    render(<NumberSeriesPage />)
    fireEvent.click(await screen.findByText('Ausgangsrechnungen'))

    expect(await screen.findByText(message)).toBeTruthy()
  })

  it('shows the validation message instead of a number when the pattern cannot work', async () => {
    mockApi([makeSeries()])
    const { ApiError } = await import('@/lib/api')
    const refusal = new ApiError('x') as Error & { detail: string }
    refusal.detail =
      'A series that restarts every year needs {YYYY} or {YY} in its pattern.'
    vi.mocked(api.post).mockRejectedValue(refusal)

    render(<NumberSeriesPage />)
    fireEvent.click(await screen.findByText('Ausgangsrechnungen'))

    expect(
      await screen.findByText(/needs \{YYYY\} or \{YY\} in its pattern/),
    ).toBeTruthy()
  })
})

describe('switching modes says when it takes effect', () => {
  it('tells the truth about “Continue without restart”: immediately, no gap', async () => {
    mockApi([makeSeries({ current_period: '2026', next_value: 413 })])
    render(<NumberSeriesPage />)

    fireEvent.click(await screen.findByText('Ausgangsrechnungen'))
    fireEvent.click(
      await screen.findByRole('radio', { name: /Continue without restart/ }),
    )

    expect(
      await screen.findByText(/keeps the counter exactly where it is/),
    ).toBeTruthy()
  })

  it('tells the truth about “Restart every year”: at the NEXT turn of the year', async () => {
    mockApi([
      makeSeries({ reset: 'never', current_period: '2026', next_value: 413 }),
    ])
    render(<NumberSeriesPage />)

    fireEvent.click(await screen.findByText('Ausgangsrechnungen'))
    fireEvent.click(
      await screen.findByRole('radio', { name: /Restart every year/ }),
    )

    const note = await screen.findByText(/takes effect at the next turn of the year/)
    expect(note.textContent).toContain('2026 carries on from 413')
  })

  it('says nothing about a switch while the mode is unchanged', async () => {
    mockApi([makeSeries({ current_period: '2026', next_value: 413 })])
    render(<NumberSeriesPage />)

    fireEvent.click(await screen.findByText('Ausgangsrechnungen'))
    // Gate on the preview, which only exists after the fetch resolved — so
    // the two `queryBy` nulls below cannot pass vacuously against a screen
    // that never loaded.
    await screen.findByText(SERVER_ONLY_NUMBER)

    expect(screen.queryByText(/keeps the counter exactly where it is/)).toBeNull()
    expect(
      screen.queryByText(/takes effect at the next turn of the year/),
    ).toBeNull()
  })
})

describe('the save control is never dead and silent', () => {
  it('says “No changes” rather than being inert for no stated reason', async () => {
    mockApi([makeSeries()])
    render(<NumberSeriesPage />)

    fireEvent.click(await screen.findByText('Ausgangsrechnungen'))
    const button = await screen.findByRole('button', { name: 'No changes' })
    expect((button as HTMLButtonElement).disabled).toBe(true)
  })

  it('is enabled as soon as something is edited', async () => {
    mockApi([makeSeries()])
    render(<NumberSeriesPage />)

    fireEvent.click(await screen.findByText('Ausgangsrechnungen'))
    await screen.findByText(SERVER_ONLY_NUMBER)

    fireEvent.change(screen.getByDisplayValue('Ausgangsrechnungen'), {
      target: { value: 'Rechnungen' },
    })

    const button = await screen.findByRole('button', { name: 'Save changes' })
    expect((button as HTMLButtonElement).disabled).toBe(false)
  })

  it('stays enabled when the PREVIEW failed — the server validates the save', async () => {
    mockApi([makeSeries()])
    vi.mocked(api.post).mockRejectedValue(new Error('preview is down'))
    render(<NumberSeriesPage />)

    fireEvent.click(await screen.findByText('Ausgangsrechnungen'))
    fireEvent.change(screen.getByDisplayValue('AR-{YYYY}-{SEQ}'), {
      target: { value: 'RE-{YYYY}-{SEQ}' },
    })

    const button = await screen.findByRole('button', { name: 'Save changes' })
    expect((button as HTMLButtonElement).disabled).toBe(false)
  })

  it('shows the server’s refusal verbatim when a save is rejected', async () => {
    mockApi([makeSeries({ allocated_count: 5, latest_number: 'AR-2026-0005' })])
    const { ApiError } = await import('@/lib/api')
    const refusal = new ApiError('x') as Error & { detail: string }
    refusal.detail =
      'This series has already issued AR-2026-0003. Setting the next number to 3 would issue it again.'
    vi.mocked(api.patch).mockRejectedValue(refusal)

    render(<NumberSeriesPage />)
    fireEvent.click(await screen.findByText('Ausgangsrechnungen'))
    await screen.findByText(SERVER_ONLY_NUMBER)

    fireEvent.change(screen.getByDisplayValue('Ausgangsrechnungen'), {
      target: { value: 'Rechnungen' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    expect(
      await screen.findByText(/already issued AR-2026-0003/),
    ).toBeTruthy()
  })
})

describe('the permission gate', () => {
  it('refuses in a sentence, not a blank page, without number_series.manage', async () => {
    seedCompany(['company.view', 'company.settings.edit'])
    mockApi([makeSeries()])

    render(<NumberSeriesPage />)

    expect(
      await screen.findByText(/does not include this/),
    ).toBeTruthy()
    expect(screen.queryByText('Ausgangsrechnungen')).toBeNull()
  })
})
