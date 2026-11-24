import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(), post: vi.fn(), upload: vi.fn(), download: vi.fn() },
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
const push = vi.fn()
vi.mock('next/navigation', () => ({
  usePathname: () => '/projects/p1/budgets/b1',
  useParams: () => ({ id: 'p1', budgetId: 'b1' }),
  useRouter: () => ({ push }),
}))

import { api, ApiError } from '@/lib/api'
import { formatAmount } from '@/lib/money'
import BudgetPage from '../[id]/budgets/[budgetId]/page'
import type { BudgetCalculated, BudgetSheet } from '@/types'
import { ALL_PERMISSIONS, makeCalculated, makeSheet, seedCompany } from './fixtures'

/**
 * The Budget sheet.
 *
 * **The browser never computes money.** The Crew section's lines are
 * 2 550.00 and 1 650.00; the engine's subtotal for Crew is 3 990.00 (after
 * the section's 5 % discount). The screen must show 3 990.00 — a sheet that
 * added its rows up would show 4 200.00, and that is the mutation this file
 * exists to catch.
 */

function mockApi(sheet: BudgetSheet = makeSheet(), calc: BudgetCalculated | Error = makeCalculated()) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/budgets/b1') return sheet as never
    if (path === '/budgets/b1/calculated') {
      if (calc instanceof Error) throw calc
      return calc as never
    }
    return { items: [], total: 0, limit: 10, offset: 0 } as never
  })
}

function calculatedFetches(): number {
  return vi.mocked(api.get).mock.calls.filter((call) => call[0] === '/budgets/b1/calculated').length
}

function sectionRow(name: string): HTMLElement {
  const row = screen
    .getAllByTestId('section-row')
    .find((element) => element.getAttribute('data-section') === name)
  if (!row) throw new Error(`no section row ${name}`)
  return row
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany(ALL_PERMISSIONS)
  mockApi()
})

describe('the numbers on the sheet', () => {
  it('a section subtotal is the engine’s, not the sum of its rows', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    const crew = sectionRow('s-crew')
    expect(within(crew).getByTestId('section-subtotal').textContent).toBe(formatAmount('3990.00'))
    expect(within(crew).getByTestId('section-subtotal').textContent).not.toBe(formatAmount('4200.00'))
    expect(within(crew).getByTestId('section-cost').textContent).toBe(formatAmount('3060.00'))
  })

  it('each line shows the engine’s price and cost for it', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    const dop = screen
      .getAllByTestId('line-row')
      .find((row) => row.getAttribute('data-line') === 'l-dop')!
    expect(within(dop).getByTestId('line-price').textContent).toBe(formatAmount('2550.00'))
    expect(within(dop).getByTestId('line-cost').textContent).toBe(formatAmount('1800.00'))
  })

  it('a cell that follows a global shows the number charged and the key, not the stored number', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    const dop = screen.getAllByTestId('line-row').find((row) => row.getAttribute('data-line') === 'l-dop')!
    const cell = within(dop).getByLabelText('Period — DoP')
    expect(within(cell).getByTestId('followed-value').textContent).toBe('3')
    expect(cell.textContent).toContain('shoot_days')

    // Focusing it opens the ref for editing.
    fireEvent.focus(cell)
    expect((within(dop).getByLabelText('Period — DoP') as HTMLInputElement).value).toBe('=shoot_days')
  })

  it('marks a line that differs from its catalog item and explains a catalog factor', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    expect(screen.getByTestId('differs-from-catalog').getAttribute('title')).toContain('1250.00')
    const factor = screen.getByTestId('factor-explained')
    expect(factor.textContent).toBe('2.8')
    expect(factor.getAttribute('title')).toContain('the 5-day row')
    expect(factor.getAttribute('title')).toContain('Standard-Mietfaktoren')
  })

  it('a refused calculation is shown as the API’s sentence and the sheet stays', async () => {
    mockApi(makeSheet(), new ApiError(422, 'the period of “DoP” points at the global \'shoot_days\''))
    render(<BudgetPage />)
    expect((await screen.findByTestId('budget-calc-error')).textContent).toContain('shoot_days')
    expect(screen.getByTestId('budget-sheet')).toBeInTheDocument()
  })

  it('the top sheet shows the API’s shares and totals', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByRole('tab', { name: 'Top sheet' }))
    const shares = screen.getAllByTestId('top-sheet-share').map((cell) => cell.textContent)
    expect(shares).toEqual([`${formatAmount('54.29')} %`, `${formatAmount('45.71')} %`])
    expect(screen.getByTestId('top-sheet-net').textContent).toBe(formatAmount('7350.00'))
  })
})

describe('editing', () => {
  it('an edited cell is one PATCH in the API’s decimal form, then the numbers are fetched again', async () => {
    vi.mocked(api.patch).mockResolvedValue({} as never)
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    const before = calculatedFetches()

    const price = screen.getByLabelText('Unit price — Gaffer')
    fireEvent.change(price, { target: { value: '1.234,56' } })
    fireEvent.blur(price)

    await waitFor(() => expect(api.patch).toHaveBeenCalled())
    expect(vi.mocked(api.patch).mock.calls[0]).toEqual([
      '/budgets/b1/lines/l-gaffer',
      { unit_price: '1234.56' },
    ])
    await waitFor(() => expect(calculatedFetches()).toBe(before + 1))
  })

  it('typing a number over a ref clears the ref', async () => {
    vi.mocked(api.patch).mockResolvedValue({} as never)
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.focus(screen.getByLabelText('Period — DoP'))
    const period = screen.getByLabelText('Period — DoP')
    expect(period.tagName).toBe('INPUT')
    fireEvent.change(period, { target: { value: '4' } })
    fireEvent.blur(period)
    await waitFor(() => expect(api.patch).toHaveBeenCalled())
    expect(vi.mocked(api.patch).mock.calls[0][1]).toEqual({ period_value: '4', clear: ['period_ref'] })
  })

  it('Enter moves to the same column in the next row', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    const dopQty = screen.getByLabelText('Quantity — DoP')
    dopQty.focus()
    fireEvent.keyDown(dopQty, { key: 'Enter' })
    expect(document.activeElement).toBe(screen.getByLabelText('Quantity — Gaffer'))
  })

  it('a global that lines use cannot be removed, and says by how many', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    expect(screen.getByTestId('global-in-use').textContent).toBe('used by 1 line')
    expect(screen.queryByLabelText('Remove shoot_days')).toBeNull()
  })

  it('imports by preview, then applies with the preview’s digest', async () => {
    vi.mocked(api.upload)
      .mockResolvedValueOnce({
        rows: [{ line_number: 2, action: 'update', message: 'updates DoP', section_code: '1100', description: 'DoP' }],
        creates: 0,
        updates: 1,
        errors: 0,
        digest: 'abc123',
        delimiter: ';',
        ignored_columns: [],
      } as never)
      .mockResolvedValueOnce({
        rows: [],
        creates: 0,
        updates: 1,
        errors: 0,
        digest: 'abc123',
        delimiter: ';',
        ignored_columns: [],
        applied: true,
      } as never)
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByText('Import CSV'))
    const file = new File(['section_code;description\n1100;DoP\n'], 'lines.csv', { type: 'text/csv' })
    fireEvent.change(screen.getByTestId('budget-import-file'), { target: { files: [file] } })

    expect((await screen.findByTestId('budget-import-summary')).textContent).toContain('1 updated')
    fireEvent.click(screen.getByTestId('budget-import-apply'))
    await waitFor(() => expect(api.upload).toHaveBeenCalledTimes(2))
    expect(vi.mocked(api.upload).mock.calls[0][0]).toBe('/budgets/b1/import/preview')
    expect(vi.mocked(api.upload).mock.calls[1][0]).toBe('/budgets/b1/import?expected_digest=abc123')
  })
})

describe('who may do what', () => {
  it('a locked budget has no inputs, says it is locked, and offers Unlock', async () => {
    mockApi(makeSheet({ locked: true }))
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    expect(screen.getByTestId('budget-locked').textContent).toContain('Unlock it to edit')
    expect(screen.queryByLabelText('Unit price — Gaffer')).toBeNull()
    expect(screen.getByTestId('budget-unlock')).toBeInTheDocument()
  })

  it('budgets.view without edit: no inputs, no Lock, and a sentence', async () => {
    seedCompany(['projects.view', 'budgets.view'])
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    expect(screen.getByTestId('budgets-read-only')).toBeInTheDocument()
    expect(screen.queryByLabelText('Unit price — Gaffer')).toBeNull()
    expect(screen.queryByTestId('budget-lock')).toBeNull()
  })

  it('without budgets.view nothing is fetched and the screen says why', async () => {
    seedCompany(['projects.view'])
    render(<BudgetPage />)
    expect(screen.getByTestId('budgets-no-access')).toBeInTheDocument()
    expect(api.get).not.toHaveBeenCalled()
  })
})
