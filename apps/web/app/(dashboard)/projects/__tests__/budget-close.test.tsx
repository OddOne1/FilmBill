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

import { api, ApiError } from '@/lib/api'
import { formatAmount } from '@/lib/money'
import BudgetPage from '../[id]/budgets/[budgetId]/page'
import type { BudgetSheet, CloseCheck } from '@/types'
import { ALL_PERMISSIONS, makeCalculated, makeLedger, makeSheet, makeTracking, seedCompany } from './fixtures'

/**
 * B1.1 part 3 — closing a budget on the screen. Every amount and the state
 * come from the API's close check; the dialog only words them.
 */

function makeCheck(overrides: Partial<CloseCheck> = {}): CloseCheck {
  return {
    budget_id: 'b1',
    state: 'within_estimate',
    estimate_cost: '1400.00',
    efc: '1300.00',
    variance: '100.00',
    lines_over: [
      {
        line_id: 'l-gaffer',
        section_id: 's-crew',
        description: 'Gaffer',
        estimate_cost: '1260.00',
        actual: '1500.00',
        committed_open: '0.00',
        efc: '1500.00',
        variance: '-240.00',
      },
    ],
    lines_under_not_finished: [],
    finished_with_open_commitment: [],
    open_commitments: [],
    open_commitments_total: '0.00',
    unallocated: [],
    unallocated_total: '0.00',
    digest: 'digest-1',
    ...overrides,
  }
}

function mockApi({ sheet = makeSheet({ locked: true }), check = makeCheck() }: { sheet?: BudgetSheet; check?: CloseCheck } = {}) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/budgets/b1') return sheet as never
    if (path === '/budgets/b1/calculated') return makeCalculated() as never
    if (path === '/budgets/b1/tracking') return makeTracking() as never
    if (path.startsWith('/budgets/b1/ledger')) return makeLedger() as never
    if (path === '/budgets/b1/close-check') return check as never
    if (path.startsWith('/parties')) return { parties: [], total: 0, limit: 200, offset: 0 } as never
    return {} as never
  })
}

async function openDialog() {
  render(<BudgetPage />)
  await screen.findByTestId('budget-sheet')
  fireEvent.click(screen.getByTestId('budget-close'))
  const dialog = await screen.findByTestId('close-dialog')
  await within(dialog).findByTestId('close-state')
  return dialog
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany(ALL_PERMISSIONS)
  mockApi()
  vi.mocked(api.post).mockResolvedValue({} as never)
})

describe('the Close budget button', () => {
  it('is offered on a locked budget only', async () => {
    mockApi({ sheet: makeSheet() })
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    expect(screen.queryByTestId('budget-close')).toBeNull()
  })
})

describe('the close dialog', () => {
  it('lists what does not add up and says whether the total stays within the estimate', async () => {
    const dialog = await openDialog()
    expect(within(dialog).getByTestId('close-state').getAttribute('data-state')).toBe('within_estimate')
    expect(within(dialog).getByTestId('close-state').textContent).toContain('The total stays within the estimate')
    const items = within(dialog).getAllByTestId('close-item')
    expect(items[0].textContent).toContain('Gaffer')
    expect(items[0].textContent).toContain(`${formatAmount('240.00')} over`)
  })

  it('says “over estimate” when the API says so', async () => {
    mockApi({ check: makeCheck({ state: 'over_estimate', efc: '1600.00', variance: '-200.00' }) })
    const dialog = await openDialog()
    expect(within(dialog).getByTestId('close-state').textContent).toContain('The total is over the estimate')
  })

  it('will not close without the approve box and a note, and says why (17c)', async () => {
    const dialog = await openDialog()
    const submit = within(dialog).getByTestId('close-submit')
    expect(submit).toBeDisabled()
    expect(within(dialog).getByTestId('close-missing').textContent).toBe(
      'To close: tick that you approve closing it as it is and write a note of at least one sentence.',
    )
    fireEvent.click(within(dialog).getByTestId('close-approve'))
    expect(submit).toBeDisabled()
    fireEvent.change(within(dialog).getByTestId('close-note'), { target: { value: 'Approved by the client.' } })
    expect(submit).not.toBeDisabled()

    fireEvent.click(submit)
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.post).mock.calls[0]).toEqual([
      '/budgets/b1/close',
      { acknowledge: true, digest: 'digest-1', note: 'Approved by the client.' },
    ])
  })

  it('a clean budget closes with no box and no note', async () => {
    mockApi({ check: makeCheck({ state: 'clean', lines_over: [], efc: '1400.00', variance: '0.00' }) })
    const dialog = await openDialog()
    expect(within(dialog).queryByTestId('close-approve')).toBeNull()
    fireEvent.click(within(dialog).getByTestId('close-submit'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/budgets/b1/close', {}))
  })

  it('a stale list: shows the API’s sentence and loads the list again', async () => {
    vi.mocked(api.post).mockRejectedValueOnce(
      new ApiError(409, 'The budget changed since the list was shown — look at the list again before closing it.'),
    )
    const dialog = await openDialog()
    const before = vi.mocked(api.get).mock.calls.filter((call) => call[0] === '/budgets/b1/close-check').length
    fireEvent.click(within(dialog).getByTestId('close-approve'))
    fireEvent.change(within(dialog).getByTestId('close-note'), { target: { value: 'Approved by the client.' } })
    fireEvent.click(within(dialog).getByTestId('close-submit'))
    expect((await within(dialog).findByTestId('close-error')).textContent).toContain('look at the list again')
    await waitFor(() =>
      expect(vi.mocked(api.get).mock.calls.filter((call) => call[0] === '/budgets/b1/close-check').length).toBe(before + 1),
    )
  })
})

describe('a closed budget', () => {
  it('says so at the top, hides Unlock and Close, and refuses costs on screen with “Reopen it first”', async () => {
    mockApi({ sheet: makeSheet({ closed: true }) })
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    const notice = screen.getByTestId('budget-closed')
    expect(notice.textContent).toContain('This budget is closed since 2026-11-24')
    expect(notice.textContent).toContain('Approved by the producer on the wrap call.')
    expect(screen.queryByTestId('budget-unlock')).toBeNull()
    expect(screen.queryByTestId('budget-close')).toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: 'Costs' }))
    expect((await screen.findByTestId('costs-closed')).textContent).toContain('Reopen it first')
    expect(screen.queryByTestId('add-actual')).toBeNull()
  })

  it('Reopen asks for a reason and sends it', async () => {
    mockApi({ sheet: makeSheet({ closed: true }) })
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByTestId('budget-reopen'))
    expect(screen.getByTestId('reopen-submit')).toBeDisabled()
    expect(screen.getByTestId('budget-closed').textContent).toContain('Reopening needs a reason.')
    fireEvent.change(screen.getByTestId('reopen-reason'), { target: { value: 'Late invoice from the rental house' } })
    fireEvent.click(screen.getByTestId('reopen-submit'))
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/budgets/b1/reopen', { reason: 'Late invoice from the rental house' }),
    )
  })

  it('without budgets.edit there is no Reopen', async () => {
    seedCompany(['projects.view', 'budgets.view'])
    mockApi({ sheet: makeSheet({ closed: true }) })
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    expect(screen.getByTestId('budget-closed')).toBeInTheDocument()
    expect(screen.queryByTestId('budget-reopen')).toBeNull()
  })
})
