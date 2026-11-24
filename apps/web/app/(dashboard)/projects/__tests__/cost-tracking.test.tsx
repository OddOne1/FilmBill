import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(), post: vi.fn(), put: vi.fn(), upload: vi.fn(), download: vi.fn() },
  setActiveCompanyId: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number
    detail: string
    constructor(status: number, detail: string) {
      super(detail)
      this.status = status
      this.detail = detail
    }
  },
}))
vi.mock('next/navigation', () => ({
  usePathname: () => '/projects/p1/budgets/b1',
  useParams: () => ({ id: 'p1', budgetId: 'b1' }),
  useRouter: () => ({ push: vi.fn() }),
}))

import { api } from '@/lib/api'
import { formatAmount } from '@/lib/money'
import BudgetPage from '../[id]/budgets/[budgetId]/page'
import type { BudgetLedger, BudgetSheet, BudgetTracking, CostEntryDuplicate, LineTracking } from '@/types'
import { ALL_PERMISSIONS, makeCalculated, makeLedger, makeSheet, makeTracking, seedCompany } from './fixtures'

/**
 * Cost planning on the budget screen (slice B1).
 *
 * **The browser never computes money.** The Crew section in
 * `makeTracking()` has an unallocated invoice of 150.00 on it — a cost on the
 * section with no line. So the section's variance is **−390.00**, while its
 * lines' variances add up to only −240.00. A top sheet that added its rows up
 * would print −240.00 under Crew, and that is the mutation this file exists
 * to catch.
 */

function mockApi({
  sheet = makeSheet(),
  tracking = makeTracking(),
  ledger = makeLedger(),
  duplicates = [] as CostEntryDuplicate[],
}: {
  sheet?: BudgetSheet
  tracking?: BudgetTracking
  ledger?: BudgetLedger
  duplicates?: CostEntryDuplicate[]
} = {}) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/budgets/b1') return sheet as never
    if (path === '/budgets/b1/calculated') return makeCalculated() as never
    if (path === '/budgets/b1/tracking') return tracking as never
    if (path.startsWith('/budgets/b1/ledger')) return ledger as never
    if (path.startsWith('/budgets/b1/duplicates')) return duplicates as never
    if (path.startsWith('/parties')) return { parties: [], total: 0, limit: 200, offset: 0 } as never
    return {} as never
  })
}

function trackingFetches(): number {
  return vi.mocked(api.get).mock.calls.filter((call) => call[0] === '/budgets/b1/tracking').length
}

function phaseLine(id: string): HTMLElement {
  const row = screen.getAllByTestId('phase-line-row').find((element) => element.getAttribute('data-line') === id)
  if (!row) throw new Error(`no phase line row ${id}`)
  return row
}

function phaseSection(id: string): HTMLElement {
  const row = screen
    .getAllByTestId('phase-section-row')
    .find((element) => element.getAttribute('data-section') === id)
  if (!row) throw new Error(`no phase section row ${id}`)
  return row
}

function forecastRow(id: string): HTMLElement {
  const row = screen.getAllByTestId('forecast-row').find((element) => element.getAttribute('data-section') === id)
  if (!row) throw new Error(`no forecast row ${id}`)
  return row
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany(ALL_PERMISSIONS)
  mockApi()
})

describe('the phase switch', () => {
  it('Estimate is today’s sheet, unchanged', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    expect(screen.getByTestId('phase-estimate').getAttribute('aria-checked')).toBe('true')
    expect(screen.queryByTestId('phase-sheet')).toBeNull()
    // A2's own cells are still there and still editable.
    expect(screen.getByLabelText('Unit price — Gaffer')).toBeInTheDocument()
  })

  it('Actual shows the API’s actual per line and per section — the section includes its unallocated cost', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByTestId('phase-actual'))

    const table = await screen.findByTestId('phase-sheet')
    expect(table.getAttribute('data-phase')).toBe('actual')
    expect(screen.queryByTestId('budget-sheet')).toBeNull()
    expect(within(phaseLine('l-gaffer')).getByTestId('phase-line-value').textContent).toBe(formatAmount('1500.00'))
    expect(within(phaseLine('l-dop')).getByTestId('phase-line-value').textContent).toBe(formatAmount('0.00'))
    // 1 500.00 on the Gaffer line plus 150.00 booked on the section alone.
    // The API says 1 650.00; a sheet that summed its rows would say 1 500.00.
    expect(within(phaseSection('s-crew')).getByTestId('phase-section-value').textContent).toBe(formatAmount('1650.00'))
    expect(within(phaseSection('s-crew')).getByTestId('phase-unallocated').textContent).toContain(formatAmount('150.00'))
  })

  it('Ordered shows what is still owed on open orders, the forecast final cost the forecast, under/over the API’s variance in words', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')

    fireEvent.click(screen.getByTestId('phase-committed'))
    expect(within(phaseLine('l-alexa')).getByTestId('phase-line-value').textContent).toBe(formatAmount('1000.00'))

    fireEvent.click(screen.getByTestId('phase-efc'))
    expect(within(phaseLine('l-dop')).getByTestId('phase-line-value').textContent).toBe(formatAmount('1800.00'))
    expect(screen.getByTestId('phase-total').textContent).toBe(formatAmount('4450.00'))

    fireEvent.click(screen.getByTestId('phase-variance'))
    expect(within(phaseSection('s-crew')).getByTestId('phase-section-value').textContent).toBe(`${formatAmount('390.00')} over`)
    expect(screen.getByTestId('phase-total').textContent).toBe(`${formatAmount('1390.00')} over`)
  })

  it('marks the lines the API says are over budget, and only those', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByTestId('phase-efc'))
    expect(within(phaseLine('l-gaffer')).getByTestId('over-budget')).toBeInTheDocument()
    expect(within(phaseLine('l-alexa')).getByTestId('over-budget')).toBeInTheDocument()
    expect(within(phaseLine('l-dop')).queryByTestId('over-budget')).toBeNull()
  })

  it('a line whose still-to-spend was set by hand shows it', async () => {
    const tracking = makeTracking()
    tracking.lines!['l-dop'] = { ...tracking.lines!['l-dop'], remaining_rule: 'etc', etc: '500.00' } as LineTracking
    mockApi({ tracking })
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByTestId('phase-efc'))
    expect(within(phaseLine('l-dop')).getByTestId('line-has-etc').textContent).toContain(formatAmount('500.00'))
    expect(within(phaseLine('l-gaffer')).queryByTestId('line-has-etc')).toBeNull()
  })
})

describe('the line drawer', () => {
  it('opens on a line with its forecast, the rule in use, and its entries', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByTestId('phase-efc'))
    fireEvent.click(within(phaseLine('l-gaffer')).getByText('Gaffer'))

    const drawer = await screen.findByTestId('line-drawer')
    expect(within(drawer).getByTestId('drawer-efc').textContent).toBe(formatAmount('1500.00'))
    expect(within(drawer).getByTestId('drawer-variance').textContent).toBe(
      `${formatAmount('240.00')} over (${formatAmount('19.05')} %)`,
    )
    expect(within(drawer).getByTestId('drawer-rule').textContent).toContain('worked out for you')
    await waitFor(() =>
      expect(vi.mocked(api.get).mock.calls.some((call) => call[0] === '/budgets/b1/ledger?line_id=l-gaffer')).toBe(true),
    )
    expect((await within(drawer).findAllByTestId('ledger-row')).length).toBe(1)
  })

  it('setting still-to-spend is one PUT in the API’s decimal form, then the forecast is fetched again', async () => {
    vi.mocked(api.put).mockResolvedValue({} as never)
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByTestId('phase-efc'))
    fireEvent.click(within(phaseLine('l-gaffer')).getByText('Gaffer'))
    const drawer = await screen.findByTestId('line-drawer')
    const before = trackingFetches()

    fireEvent.change(within(drawer).getByLabelText('Still to spend — Gaffer'), { target: { value: '1.234,56' } })
    fireEvent.click(within(drawer).getByTestId('etc-save'))

    await waitFor(() => expect(api.put).toHaveBeenCalled())
    expect(vi.mocked(api.put).mock.calls[0]).toEqual(['/budgets/b1/lines/l-gaffer/etc', { etc: '1234.56' }])
    await waitFor(() => expect(trackingFetches()).toBe(before + 1))
  })

  it('Clear says why it is unavailable when nothing is set by hand', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByTestId('phase-efc'))
    fireEvent.click(within(phaseLine('l-gaffer')).getByText('Gaffer'))
    const drawer = await screen.findByTestId('line-drawer')
    const clear = within(drawer).getByTestId('etc-clear')
    expect(clear).toBeDisabled()
    expect(drawer.textContent).toContain('Clear has nothing to clear')
  })

  it('a hand-set still-to-spend can be cleared back to the worked-out one', async () => {
    vi.mocked(api.put).mockResolvedValue({} as never)
    const tracking = makeTracking()
    tracking.lines!['l-dop'] = { ...tracking.lines!['l-dop'], remaining_rule: 'etc', etc: '500.00' } as LineTracking
    mockApi({ tracking })
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByTestId('phase-efc'))
    fireEvent.click(within(phaseLine('l-dop')).getByText('DoP'))
    const drawer = await screen.findByTestId('line-drawer')
    fireEvent.click(within(drawer).getByTestId('etc-clear'))
    await waitFor(() => expect(api.put).toHaveBeenCalled())
    expect(vi.mocked(api.put).mock.calls[0]).toEqual(['/budgets/b1/lines/l-dop/etc', { etc: null }])
  })
})

describe('the top sheet', () => {
  it('prints the API’s variance per section — not the sum of the section’s lines', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByRole('tab', { name: 'Top sheet' }))
    await screen.findByTestId('cost-forecast')

    const crew = forecastRow('s-crew')
    expect(within(crew).getByTestId('forecast-variance').textContent).toBe(`${formatAmount('390.00')} over`)
    expect(within(crew).getByTestId('forecast-variance').textContent).not.toBe(`${formatAmount('240.00')} over`)
    expect(within(crew).getByTestId('forecast-efc').textContent).toBe(formatAmount('3450.00'))
    expect(within(crew).getByTestId('forecast-estimate_cost').textContent).toBe(formatAmount('3060.00'))
    expect(within(forecastRow('s-eq')).getByTestId('forecast-committed_open').textContent).toBe(formatAmount('1000.00'))
    expect(screen.getByTestId('forecast-total-variance').textContent).toBe(`${formatAmount('1390.00')} over`)
  })

  it('has a “not on a line” row with the API’s unallocated amount', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByRole('tab', { name: 'Top sheet' }))
    const row = await screen.findByTestId('forecast-unallocated')
    expect(within(row).getByTestId('forecast-unallocated-total').textContent).toBe(formatAmount('150.00'))
  })

  it('shows the forecast profit against the agreed price, with the planned profit beside it', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByRole('tab', { name: 'Top sheet' }))
    await screen.findByTestId('cost-forecast')
    expect(screen.getByTestId('forecast-margin').textContent).toBe(
      `${formatAmount('2900.00')} (${formatAmount('39.46')} %)`,
    )
    expect(screen.getByTestId('estimate-margin').textContent).toBe(
      `${formatAmount('4290.00')} (${formatAmount('58.37')} %)`,
    )
    expect(screen.getAllByTestId('spend-bar').length).toBeGreaterThan(0)
    // A2's half of the top sheet is still there.
    expect(screen.getByTestId('top-sheet-net').textContent).toBe(formatAmount('7350.00'))
  })
})

describe('the Costs tab', () => {
  async function openAddActual() {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByRole('tab', { name: 'Costs' }))
    fireEvent.click(await screen.findByTestId('add-actual'))
    const form = await screen.findByTestId('entry-form-actual')
    fireEvent.change(within(form).getByLabelText('Amount (net)'), { target: { value: '250,00' } })
    fireEvent.change(within(form).getByLabelText('Payee (free text)'), { target: { value: 'Kamera GmbH' } })
    fireEvent.change(within(form).getByLabelText('Reference'), { target: { value: 'RE-1' } })
    return form
  }

  it('shows a duplicate warning BEFORE saving, and saves only on “Save anyway”', async () => {
    mockApi({
      duplicates: [
        { kind: 'actual', id: 'a-old', amount: '250.00', date: '2026-06-02', payee: 'Kamera GmbH', reference: 'RE-1', description: null },
      ],
    })
    vi.mocked(api.post).mockResolvedValue({ actual: {}, duplicates: [] } as never)
    const form = await openAddActual()

    fireEvent.click(within(form).getByTestId('save-actual'))
    const warning = await within(form).findByTestId('duplicate-warning')
    expect(warning.textContent).toContain('RE-1')
    expect(api.post).not.toHaveBeenCalled()

    const check = vi.mocked(api.get).mock.calls.find((call) => String(call[0]).startsWith('/budgets/b1/duplicates'))
    expect(check?.[0]).toBe('/budgets/b1/duplicates?amount=250.00&payee_text=Kamera+GmbH&reference=RE-1')

    fireEvent.click(within(form).getByTestId('save-anyway'))
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1))
    const [path, body] = vi.mocked(api.post).mock.calls[0] as [string, Record<string, unknown>]
    expect(path).toBe('/budgets/b1/actuals')
    expect(body.amount).toBe('250.00')
    expect(body.payee_text).toBe('Kamera GmbH')
    expect(body.party_id).toBeNull()
  })

  it('with no duplicate, saves straight away', async () => {
    vi.mocked(api.post).mockResolvedValue({ actual: {}, duplicates: [] } as never)
    const form = await openAddActual()
    fireEvent.click(within(form).getByTestId('save-actual'))
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1))
    expect(screen.queryByTestId('duplicate-warning')).toBeNull()
  })

  it('Save says what is missing instead of going dead (17c)', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByRole('tab', { name: 'Costs' }))
    fireEvent.click(await screen.findByTestId('add-commitment'))
    const form = await screen.findByTestId('entry-form-commitment')
    expect(within(form).getByTestId('save-commitment')).toBeDisabled()
    expect(within(form).getByTestId('entry-missing').textContent).toContain('an amount')
  })

  it('a cost can go on a section with no line', async () => {
    vi.mocked(api.post).mockResolvedValue({ commitment: {}, duplicates: [] } as never)
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByRole('tab', { name: 'Costs' }))
    fireEvent.click(await screen.findByTestId('add-commitment'))
    const form = await screen.findByTestId('entry-form-commitment')
    fireEvent.change(within(form).getByLabelText('Where it belongs'), { target: { value: 's-eq|' } })
    fireEvent.change(within(form).getByLabelText('Amount (net)'), { target: { value: '1000' } })
    fireEvent.click(within(form).getByTestId('save-commitment'))
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    const body = vi.mocked(api.post).mock.calls[0][1] as Record<string, unknown>
    expect(body.section_id).toBe('s-eq')
    expect(body.line_id).toBeNull()
  })
})

describe('who may do what', () => {
  it('without budgets.edit there are no Add buttons and a sentence says why', async () => {
    seedCompany(['projects.view', 'budgets.view'])
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByRole('tab', { name: 'Costs' }))
    expect(await screen.findByTestId('costs-read-only')).toBeInTheDocument()
    expect(screen.queryByTestId('add-actual')).toBeNull()
    expect(screen.queryByTestId('add-commitment')).toBeNull()
    expect(screen.queryByTestId('import-actuals')).toBeNull()
  })

  it('a locked budget says estimate edits are closed while costs can still be entered — and they can', async () => {
    mockApi({ sheet: makeSheet({ locked: true }) })
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    const notice = screen.getByTestId('budget-locked').textContent ?? ''
    expect(notice).toContain('cannot be changed')
    expect(notice).toContain('Costs are still open')
    fireEvent.click(screen.getByRole('tab', { name: 'Costs' }))
    expect(await screen.findByTestId('add-actual')).toBeInTheDocument()
  })
})
